#!/usr/bin/env python3
"""误报整改第一批的独立评估入口（不覆盖 r2 输出、不改匹配/真值/指标公式）。

r2 的 ``scripts/gold_baseline_match.py`` 固定读写 ``outputs/gold_baseline_20261102``，
且人工裁决按旧条目 ID（``doc|raw_id``）关联；整改后条目会自动重编号。本入口：

1. 用同一份 ``gold_baseline_match.py`` 的 ``load_gold``/``load_system``/``match_item``
   /``match_o`` 与 ``metrics_main`` 计算（匹配、真值、公式、容差一行未改）；
2. 新旧条目按“规则 + 页码 + 原文/引文”对齐，不按展示序号继承裁决；
3. 只把旧裁决通过唯一对齐带到新条目；无法唯一对齐的差异单独列出；
4. 全部写入新的输出目录，r2 的 ``rev2/`` 与 ``run1/`` 只读。

用法：
    .venv/bin/python scripts/gold_baseline_eval_fp_batch1.py \
        --run outputs/fp_batch1_20261103/after/run1 \
        --baseline-analysis outputs/gold_baseline_20261102/rev2/analysis \
        --targets outputs/gold_baseline_20261102/rev2/fp_batch1_targets.csv \
        --out outputs/fp_batch1_20261103/after/eval
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import importlib.util
import json
import re
import sys
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

HERE = Path(__file__).resolve().parent
DEFAULT_MATCH = HERE / "gold_baseline_match.py"


def read_gz(path: Path) -> Any:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return json.load(handle)


def load_match_module(path: Path):
    spec = importlib.util.spec_from_file_location("gold_baseline_match", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"无法加载匹配脚本：{path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["gold_baseline_match"] = module
    spec.loader.exec_module(module)
    return module


def norm(text: str) -> str:
    text = re.sub(r"\s+", "", text or "")
    return re.sub(r"^[一二三四五六七八九十百]+、", "", text)


def similarity(a: Dict[str, Any], b: Dict[str, Any]) -> float:
    if a["doc"] != b["doc"] or a["tier"] != b["tier"] or a["rule"] != b["rule"]:
        return 0.0
    if a["pages"] != b["pages"]:
        return 0.0
    message = SequenceMatcher(None, norm(a["message"]), norm(b["message"])).ratio()
    quote_a, quote_b = norm("".join(a.get("quotes") or [])), norm("".join(b.get("quotes") or []))
    quote = SequenceMatcher(None, quote_a, quote_b).ratio() if (quote_a or quote_b) else 1.0
    if message == 1.0 and quote == 1.0:
        return 1.0
    return 0.85 * message + 0.15 * quote


def identity_key(item: Dict[str, Any]):
    """完整业务身份：文档、层级、规则、页码、标题、去序号正文与原文引文。"""
    return (item["doc"], item["tier"], item["rule"], tuple(item["pages"]), item.get("sev"),
            _ws(item.get("title")), norm(item.get("message")),
            tuple(_ws(q) for q in item.get("quotes") or []))


def align(old: List[Dict[str, Any]], new: List[Dict[str, Any]], decision_of=None):
    """一对一配对；同指纹重复组只有组内裁决完全相同时才按组配对，其余一律 REVIEW。"""
    pool = {item["id"]: item for item in new}
    pairs: List[Tuple[Dict[str, Any], Dict[str, Any], str]] = []
    old_only: List[Dict[str, Any]] = []
    pending: List[Dict[str, Any]] = []
    review: List[Dict[str, Any]] = []
    resolved: List[Dict[str, Any]] = []

    groups: Dict[Any, List[Dict[str, Any]]] = {}
    for item in old:
        groups.setdefault(identity_key(item), []).append(item)
    handled: set = set()
    for o in old:
        key = identity_key(o)
        if key in handled:
            continue
        old_group = groups[key]
        candidates = [n for n in pool.values() if identity_key(n) == key]
        if len(old_group) == 1 and len(candidates) == 1:
            pairs.append((o, candidates[0], "exact"))
            pool.pop(candidates[0]["id"])
            handled.add(key)
            continue
        if len(old_group) == len(candidates) and candidates:
            requires_decision = bool({m["tier"] for m in old_group} & {"F", "P", "O", "R"})
            signatures = [decision_of(m) for m in old_group] if decision_of else []
            if (not requires_decision) or (
                    signatures and all(s is not None for s in signatures)
                    and len(set(signatures)) == 1):
                for old_item, new_item in zip(old_group, candidates, strict=True):
                    pairs.append((old_item, new_item, "duplicate-group"))
                for candidate in candidates:
                    pool.pop(candidate["id"])
                resolved.append({"old_ids": [m["id"] for m in old_group],
                                 "new_ids": [n["id"] for n in candidates],
                                 "decision": signatures[0] if signatures else None,
                                 "reason": "同指纹重复组；组内完整裁决签名一致（或该层无裁决），按组配对"})
                handled.add(key)
                continue
            reason = ("同指纹重复组内旧裁决缺失，无法按组继承"
                      if signatures and any(s is None for s in signatures)
                      else "同指纹重复组内完整裁决签名不一致，无法按组继承")
            review.append({"old_ids": [m["id"] for m in old_group],
                           "candidates": [n["id"] for n in candidates],
                           "signatures": [list(s) if isinstance(s, tuple) else s for s in signatures],
                           "reason": reason})
            for candidate in candidates:
                pool.pop(candidate["id"])
            handled.add(key)
            continue
        if candidates:
            review.append({"old_ids": [m["id"] for m in old_group],
                           "candidates": [n["id"] for n in candidates],
                           "reason": "旧同指纹组与候选数量不一致，无法一一对应"})
            for candidate in candidates:
                pool.pop(candidate["id"])
            handled.add(key)
            continue
        pending.append(o)

    for o in pending:
        scored = sorted(((similarity(o, n), n) for n in pool.values()), key=lambda x: -x[0])
        scored = [(s, n) for s, n in scored if s >= 0.90]
        if not scored:
            old_only.append(o)
            continue
        best_score, best = scored[0]
        second_score = scored[1][0] if len(scored) > 1 else -1.0
        if best_score - second_score < 0.02:
            review.append({"old_id": o["id"], "candidates": [n["id"] for _, n in scored[:3]],
                           "scores": [round(s, 4) for s, _ in scored[:3]],
                           "reason": "最高分不唯一（并列或差 <0.02），无法唯一对应"})
            for _, candidate in scored:
                pool.pop(candidate["id"], None)
            continue
        pairs.append((o, best, f"similar:{best_score:.3f}"))
        pool.pop(best["id"])
    return pairs, old_only, list(pool.values()), review, resolved


def _ws(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def message_equal(left: str, right: str) -> bool:
    """去展示序号后相等；仅当两侧都触发 600 字截断时允许 1–2 字前缀差。"""
    a, b = norm(left), norm(right)
    if a == b:
        return True
    if len(left or "") >= 600 and len(right or "") >= 600:
        short, long = sorted((a, b), key=len)
        return len(long) - len(short) <= 2 and long.startswith(short)
    return False


def _text_key(item: Dict[str, Any]) -> str:
    """text = 标题 + 空格 + 正文/原文；只归一规则自动加的展示序号，不归一原文。"""
    text = item.get("text") or ""
    title = item.get("title") or ""
    prefix = len(title) + 1 if title and text.startswith(title + " ") else 0
    head, rest = text[:prefix], text[prefix:]
    rest = re.sub(r"^[一二三四五六七八九十百]+、", "", rest)
    return _ws(head + rest)


def fingerprint_diffs(old: Dict[str, Any], new: Dict[str, Any]) -> List[str]:
    """完整实质指纹：严重度、原文、证据位置、期望/实际值（在 text 中）等。"""
    diffs = [field for field in ("tier", "rule", "pages", "sev", "title", "bucket",
                                 "checklist_item", "obligation_ids")
             if old.get(field) != new.get(field)]
    if not message_equal(old.get("message"), new.get("message")):
        diffs.append("message")
    if [_ws(q) for q in old.get("quotes") or []] != [_ws(q) for q in new.get("quotes") or []]:
        diffs.append("quotes")
    if _text_key(old) != _text_key(new):
        diffs.append("text")
    return diffs


# ---------------------------------------------------------------------------
# 原始 status 指纹：直接对照基线 run，覆盖 bbox/location/证据结构/结构化期望值
# ---------------------------------------------------------------------------

#: 每次运行都会变的运行标识，不参与内容比较（不是业务字段）。
RAW_RUN_KEYS = {"id", "analysis_revision", "created_at", "model_version", "prompt_version"}


def _pick_raw(entries: Optional[List[Dict[str, Any]]], message: Optional[str] = None):
    if not entries:
        return None
    if len(entries) == 1:
        return entries[0]
    if message:
        matched = [entry for entry in entries if message_equal(entry.get("message"), message)]
        if len(matched) == 1:
            return matched[0]
    return None


def load_raw_index(run: Path) -> Dict[Tuple[str, str, str], List[Dict[str, Any]]]:
    """只读 status，按 (文档, 类型, 身份) 建索引；不依赖 system_items 的粗粒度行。"""
    index: Dict[Tuple[str, str, str], List[Dict[str, Any]]] = {}
    for path in sorted((run / "status").glob("*.status.json.gz")):
        sid = path.name.split(".")[0]
        status = read_gz(path)
        report = status.get("review_report") or {}
        for bucket in ("issues", "tips"):
            for item in report.get(bucket) or []:
                if item.get("id"):
                    index.setdefault((sid, "finding", item["id"]), []).append(item)
        for record in report.get("manual_review") or []:
            finding = record.get("finding") or {}
            if finding.get("id"):
                index.setdefault((sid, "finding", finding["id"]), []).append(finding)
            code = record.get("code")
            if code and record.get("record_type") != "finding_review_candidate":
                index.setdefault((sid, "notice", code), []).append(record)
        coverage = status.get("check_coverage") or {}
        for instance in coverage.get("instances") or []:
            oid = instance.get("obligation_id")
            if oid:
                index.setdefault((sid, "obligation", oid), []).append(instance)
    return index


def raw_item(index: Dict[Tuple[str, str, str], List[Dict[str, Any]]], item: Dict[str, Any]):
    doc, tier = item["doc"], item["tier"]
    if tier == "O":
        return _pick_raw(index.get((doc, "obligation", item["rule"])))
    if tier == "N":
        return _pick_raw(index.get((doc, "notice", item["rule"])), item.get("message"))
    raw_id = item.get("raw_id")
    if not raw_id:
        return None
    return _pick_raw(index.get((doc, "finding", raw_id)))


def _round_floats(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 3)
    if isinstance(value, dict):
        return {key: _round_floats(val) for key, val in value.items()}
    if isinstance(value, list):
        return [_round_floats(val) for val in value]
    return value


def canonical_raw(raw: Dict[str, Any]) -> Dict[str, Any]:
    data = {key: value for key, value in raw.items() if key not in RAW_RUN_KEYS}
    data = _round_floats(data)
    if "message" in data:
        data["message"] = norm(data["message"])
    if "title" in data:
        data["title"] = _ws(data["title"])
    return data


#: 引用字段：只接受冻结例外清单里的精确旧/新值；不做运行时推导。
_REF_KEY_NAMES = {"pre_group_issue_id", "member_issue_ids", "merged_issue_ids"}
_ID_RE = re.compile(r"^([A-Za-z][A-Za-z0-9-]*)-(\d+)$")


def is_reference_path(path: str) -> bool:
    key = path.rstrip("/").split("/")[-1].split("[")[0]
    return key in _REF_KEY_NAMES


def get_path(value: Any, path: str, default: Any = None) -> Any:
    current = value
    try:
        for key, index in re.findall(r"/([^/\[]+)|\[(\d+)\]", path):
            current = current[key] if key else current[int(index)]
    except (KeyError, IndexError, TypeError):
        return default
    return current


def reference_view(raw: Dict[str, Any]) -> Dict[str, Any]:
    """抽取一个条目的全部引用字段，用于跨表示一致性比较。"""
    view: Dict[str, Any] = {}
    for key in _REF_KEY_NAMES:
        if key in raw:
            view[key] = raw[key]
    location = raw.get("location") if isinstance(raw.get("location"), dict) else {}
    for key in _REF_KEY_NAMES:
        if key in location:
            view[f"location.{key}"] = location[key]
    related = [ref.get("location", {}).get("pre_group_issue_id")
               for ref in location.get("related_findings") or []
               if isinstance(ref, dict)]
    if any(related):
        view["related_findings_pre"] = related
    return view


def membership_consistent(raw: Dict[str, Any]) -> bool:
    """merged/member 列表必须与 related_findings 中的成员引用一致。"""
    location = raw.get("location") if isinstance(raw.get("location"), dict) else {}
    members: List[str] = []
    for source in (raw, location):
        for key in ("member_issue_ids", "merged_issue_ids"):
            value = source.get(key)
            if isinstance(value, list):
                members.extend(str(item) for item in value)
    related = [str(ref.get("location", {}).get("pre_group_issue_id"))
               for ref in location.get("related_findings") or []
               if isinstance(ref, dict) and ref.get("location", {}).get("pre_group_issue_id")]
    if members and related:
        return sorted(set(members)) == sorted(set(related))
    return True


def load_entity_index(run: Path) -> Dict[Tuple[str, str], Dict[str, Any]]:
    """result.issues.* 的原始实体表（跨表示一致性用），按 id 去重。"""
    index: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for path in sorted((run / "status").glob("*.status.json.gz")):
        sid = path.name.split(".")[0]
        status = read_gz(path)
        buckets = ((status.get("result") or {}).get("issues") or {})
        for bucket in buckets.values():
            if not isinstance(bucket, list):
                continue
            for item in bucket:
                if item.get("id"):
                    index.setdefault((sid, item["id"]), item)
    return index


def collect_id_maxima(run: Path) -> Dict[Tuple[str, str], int]:
    """基线 run 中每个文档、每个 ID 前缀的最大编号（重编号只能变小）。"""
    maxima: Dict[Tuple[str, str], int] = {}

    def visit(sid: str, value: Any) -> None:
        if isinstance(value, str):
            match = _ID_RE.fullmatch(value)
            if match:
                key = (sid, match.group(1))
                maxima[key] = max(maxima.get(key, 0), int(match.group(2)))
        elif isinstance(value, dict):
            for item in value.values():
                visit(sid, item)
        elif isinstance(value, list):
            for item in value:
                visit(sid, item)

    for path in sorted((run / "status").glob("*.status.json.gz")):
        sid = path.name.split(".")[0]
        visit(sid, read_gz(path))
    return maxima


def reference_id_allowed(value: Any, doc: str, maxima: Dict[Tuple[str, str], int]) -> bool:
    if not isinstance(value, str):
        return False
    match = _ID_RE.fullmatch(value)
    if not match:
        return False
    suffix = int(match.group(2))
    return 1 <= suffix <= maxima.get((doc, match.group(1)), 0)


def load_reference_exceptions(path: Path):
    payload = json.loads(path.read_text(encoding="utf-8"))
    refs = {}
    for entry in payload.get("entries") or []:
        key = (entry["doc"], entry["old_id"], entry["new_id"], entry["path"],
               json.dumps(entry["old_value"], ensure_ascii=False, sort_keys=True),
               json.dumps(entry["new_value"], ensure_ascii=False, sort_keys=True))
        refs[key] = entry
    counts = {(entry["doc"], entry["old_id"], entry["new_id"], entry["path"]): entry
              for entry in payload.get("count_exceptions") or []}
    return refs, counts, payload


def reference_change_allowed(doc: str, old_id: str, new_id: str, path: str,
                             old_value: Any, new_value: Any,
                             exceptions: Dict[Any, Dict[str, Any]],
                             maxima: Dict[Tuple[str, str], int]):
    """引用变化只有在冻结例外清单精确命中且新 ID 未超出基线范围时放行。"""
    key = (doc, old_id, new_id, path,
           json.dumps(old_value, ensure_ascii=False, sort_keys=True),
           json.dumps(new_value, ensure_ascii=False, sort_keys=True))
    entry = exceptions.get(key)
    if entry is None:
        return False, "引用变化不在冻结例外清单", None
    if not reference_id_allowed(new_value, doc, maxima):
        return False, f"新引用 {new_value} 超出基线 ID 范围", None
    return True, "", entry


def count_change_allowed(doc: str, old_id: str, new_id: str, path: str,
                         old_count: Any, new_count: Any, rule_id: Optional[str],
                         expected_removed: int, exceptions: Dict[Any, Dict[str, Any]]):
    """计数变化只允许冻结条目；旧/新值、规则与移除数量必须精确一致。"""
    entry = exceptions.get((doc, old_id, new_id, path))
    if entry is None:
        return False, "计数变化不在冻结例外清单", None
    expected_new = entry.get("new_count")
    new_ok = new_count in (None, 0) if expected_new is None else new_count == expected_new
    if entry.get("old_count") != old_count or not new_ok:
        return False, f"计数 {old_count}->{new_count} 与冻结例外值不符", None
    if entry.get("rule_id") != rule_id or entry.get("removed_count") != expected_removed:
        return False, "规则或目标移除数量与冻结例外不符", None
    if (old_count or 0) - (new_count or 0) != expected_removed:
        return False, "计数差与目标移除数量不符", None
    return True, "", entry


def exception_bidirectional_conflicts(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    groups: Dict[Tuple[str, str], List[Tuple[str, str]]] = {}
    for entry in entries:
        family = "pre" if entry["path"].endswith("pre_group_issue_id") else "members"
        pairs = groups.setdefault((entry["doc"], family), [])
        pair = (str(entry["new_value"]), str(entry["old_value"]))
        if pair not in pairs:
            pairs.append(pair)
    conflicts = []
    for (doc, family), pairs in groups.items():
        news = [new for new, _ in pairs]
        olds = [old for _, old in pairs]
        if len(news) != len(set(news)) or len(olds) != len(set(olds)):
            conflicts.append({"doc": doc, "family": family,
                              "pairs": [{"new": n, "old": o} for n, o in pairs]})
    return conflicts


def diff_paths(left: Any, right: Any, path: str = "") -> List[str]:
    if type(left) is not type(right):
        return [path or "$"]
    if isinstance(left, dict):
        paths: List[str] = []
        for key in sorted(set(left) | set(right)):
            if key not in left or key not in right:
                paths.append(f"{path}/{key}")
            else:
                paths.extend(diff_paths(left[key], right[key], f"{path}/{key}"))
        return paths
    if isinstance(left, list):
        if len(left) != len(right):
            return [f"{path}[len {len(left)}≠{len(right)}]"]
        paths = []
        for index, (a, b) in enumerate(zip(left, right, strict=True)):
            paths.extend(diff_paths(a, b, f"{path}[{index}]"))
        return paths
    return [] if left == right else [path or "$"]


def decision_signature(row: Optional[Dict[str, Any]]):
    """基线 final_rows 的完整裁决签名；缺行返回 None（重复组必须 REVIEW）。"""
    if row is None:
        return None
    links = tuple(sorted(str(link) for link in (row.get("links") or [])))
    return (row.get("final"), links, row.get("cause") or "", bool(row.get("amend")))


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run", required=True, help="整改后重放的 run1 目录")
    parser.add_argument("--baseline-run", required=True, help="原基线 run（冻结采集，只读）")
    parser.add_argument("--baseline-analysis", required=True, help="r2 的 analysis 目录（只读）")
    parser.add_argument("--targets", required=True, help="目标行 CSV（判断移除是否符合预期）")
    parser.add_argument("--out", required=True, help="独立评估输出目录（不得已存在非空）")
    parser.add_argument("--match-script", default=str(DEFAULT_MATCH), help="gold_baseline_match.py 路径")
    parser.add_argument("--reference-exceptions", default=str(HERE / "fp_batch1_reference_exceptions.json"),
                        help="冻结的引用/计数精确例外清单")
    args = parser.parse_args(argv)

    run = Path(args.run).resolve()
    baseline_run = Path(args.baseline_run).resolve()
    baseline = Path(args.baseline_analysis).resolve()
    targets_path = Path(args.targets).resolve()
    out = Path(args.out).resolve()
    if out.exists() and any(out.iterdir()):
        raise SystemExit(f"输出目录已存在且非空：{out}")
    analysis = out / "analysis"
    analysis.mkdir(parents=True, exist_ok=True)

    gm = load_match_module(Path(args.match_script).resolve())
    sys.path.insert(0, str(Path(args.match_script).resolve().parent))
    import gold_baseline_decisions_r2 as dec  # noqa: E402

    docs = gm.load_gold()
    items, aux = gm.load_system(run, docs)
    old_items = json.loads((baseline / "system_items.json").read_text(encoding="utf-8"))
    baseline_rows = {f"{row['doc']}|{row.get('raw_id')}": row for row in
                     json.loads((baseline / "final_rows.json").read_text(encoding="utf-8"))}

    targets: Dict[Tuple[str, str], Dict[str, str]] = {}
    with targets_path.open(encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            targets[(row["sample_id"], row["system_item_id"])] = row

    def decision_of(item: Dict[str, Any]):
        """完整裁决签名：final + 排序 links + cause + amend，来自基线 final_rows（自动与人工）。"""
        key = f"{item['doc']}|{item.get('raw_id') or item['rule']}"
        return decision_signature(baseline_rows.get(key))

    pairs, old_only, new_only, review, resolved_duplicates = align(old_items, items, decision_of)
    new_to_old = {n["id"]: o for o, n, _ in pairs}
    fingerprint_drift = []
    for old, new, how in pairs:
        diffs = fingerprint_diffs(old, new)
        if diffs:
            fingerprint_drift.append({"old_id": old["id"], "new_id": new["id"], "how": how,
                                      "fields": diffs,
                                      "old": {f: old.get(f) for f in diffs},
                                      "new": {f: new.get(f) for f in diffs}})

    # F5：直接对照基线 run 的原始 status（bbox/location/证据结构/结构化期望值）。
    old_raw_index = load_raw_index(baseline_run)
    new_raw_index = load_raw_index(run)
    old_entity_index = load_entity_index(baseline_run)
    new_entity_index = load_entity_index(run)
    id_maxima = collect_id_maxima(baseline_run)
    reference_exceptions_path = Path(args.reference_exceptions).resolve()
    ref_exceptions, count_exceptions, exceptions_payload = load_reference_exceptions(
        reference_exceptions_path)
    reference_exceptions_sha256 = hashlib.sha256(reference_exceptions_path.read_bytes()).hexdigest()
    exception_conflicts = exception_bidirectional_conflicts(list(ref_exceptions.values()))
    target_removal_counts = Counter()
    for (sid, _), row in targets.items():
        if row["expect_after"] == "移除":
            target_removal_counts[(sid, row["rule"])] += 1

    raw_fingerprint_drift, raw_missing = [], []
    reference_exceptions_applied, count_exceptions_applied = [], []
    used_reference_keys = set()
    for old, new, how in pairs:
        raw_old = raw_item(old_raw_index, old)
        raw_new = raw_item(new_raw_index, new)
        if raw_old is None or raw_new is None:
            raw_missing.append({"old_id": old["id"], "new_id": new["id"],
                                "old_found": raw_old is not None, "new_found": raw_new is not None})
            continue
        reasons, drift_paths = [], []

        # 1) 跨表示一致性：review_report 的引用必须与 result.issues 同一实体一致。
        for label, raw, entity_index, item in (("old", raw_old, old_entity_index, old),
                                                ("new", raw_new, new_entity_index, new)):
            view = reference_view(raw)
            if not view:
                continue
            entity_key = (item["doc"], item.get("raw_id"))
            entity = entity_index.get(entity_key) if entity_key[1] else None
            if entity is None:
                reasons.append(f"{label} 引用在 result.issues 中没有对应实体")
                drift_paths.append("/__cross_representation__")
            elif reference_view(entity) != view:
                reasons.append(f"{label} review_report 与 result.issues 的引用不一致")
                drift_paths.append("/__cross_representation__")

        # 2) 成员归属：member/merged 列表必须与 related_findings 的成员引用一致。
        for label, raw in (("old", raw_old), ("new", raw_new)):
            if not membership_consistent(raw):
                reasons.append(f"{label} member/merged 与 related_findings 引用不一致")
                drift_paths.append("/__membership__")

        # 3) 逐字段：引用只认冻结例外清单；计数值按精确旧/新值与移除数量核验。
        left = canonical_raw(raw_old)
        right = canonical_raw(raw_new)
        for path in diff_paths(left, right):
            if is_reference_path(path):
                try:
                    old_value, new_value = get_path(left, path), get_path(right, path)
                except (KeyError, IndexError, TypeError):
                    reasons.append(f"{path} 引用无法读取")
                    drift_paths.append(path)
                    continue
                allowed, reason, entry = reference_change_allowed(
                    old["doc"], old["id"], new["id"], path, old_value, new_value,
                    ref_exceptions, id_maxima)
                if not allowed:
                    reasons.append(f"{path} {reason}")
                    drift_paths.append(path)
                else:
                    exception_key = (old["doc"], old["id"], new["id"], path,
                                     json.dumps(old_value, ensure_ascii=False, sort_keys=True),
                                     json.dumps(new_value, ensure_ascii=False, sort_keys=True))
                    used_reference_keys.add(exception_key)
                    reference_exceptions_applied.append(entry)
                continue
            count_match = re.fullmatch(r"/unresolved_rules\[(\d+)\]/partial_findings_count", path)
            if count_match and old["tier"] == "N":
                old_value, new_value = get_path(left, path), get_path(right, path)
                old_rules = raw_old.get("unresolved_rules") or []
                index = int(count_match.group(1))
                rule_id = (old_rules[index] or {}).get("rule_id") if index < len(old_rules) else None
                expected_removed = target_removal_counts.get((old["doc"], rule_id), 0)
                allowed, reason, entry = count_change_allowed(
                    old["doc"], old["id"], new["id"], path, old_value, new_value,
                    rule_id, expected_removed, count_exceptions)
                if not allowed:
                    reasons.append(f"{path} {reason}")
                else:
                    count_exceptions_applied.append(entry)
                continue
            reasons.append("非引用/非冻结计数字段漂移")
            drift_paths.append(path)
        if reasons:
            raw_fingerprint_drift.append({"old_id": old["id"], "new_id": new["id"], "how": how,
                                          "paths": sorted(set(drift_paths))[:20],
                                          "reasons": list(dict.fromkeys(reasons))[:10]})

    unused_reference_exceptions = [entry for key, entry in ref_exceptions.items()
                                   if key not in used_reference_keys]

    # 旧裁决只能通过唯一对齐继承；移除条目必须全部是目标 CSV 的“移除”行。
    unused, unexpected_unused, missing_decision = [], [], []
    used = set()
    for item in items:
        if item["tier"] not in {"F", "P"}:
            continue
        old = new_to_old.get(item["id"])
        key = f"{item['doc']}|{old.get('raw_id')}" if old else None
        if key and key in dec.D:
            used.add(key)
        elif old is None:
            missing_decision.append({"new_id": item["id"], "old_id": None, "tier": item["tier"]})
    for key in dec.D:
        if key in used:
            continue
        doc, raw = key.split("|", 1)
        target = targets.get((doc, raw))
        if target and target["expect_after"] == "移除":
            unused.append(key)
        else:
            unexpected_unused.append({"key": key, "target": target})

    removed_rows = []
    for o in old_only:
        target = targets.get((o["doc"], o.get("raw_id")))
        removed_rows.append({"sample_id": o["doc"], "tier": o["tier"], "rule": o["rule"],
                             "raw_id": o.get("raw_id"), "pages": o["pages"], "message": o.get("message"),
                             "in_targets": bool(target), "target_class": (target or {}).get("class"),
                             "expect_after": (target or {}).get("expect_after")})
    target_removed = [r for r in removed_rows if r["in_targets"] and r["expect_after"] == "移除"]
    target_missing = [f"{sid}|{raw}" for (sid, raw), row in targets.items()
                      if row["expect_after"] == "移除"
                      and not any(r["sample_id"] == sid and r["raw_id"] == raw for r in removed_rows)]
    unexpected_removed = [r for r in removed_rows
                          if not (r["in_targets"] and r["expect_after"] == "移除")]
    added_rows = [{"sample_id": n["doc"], "tier": n["tier"], "rule": n["rule"], "raw_id": n.get("raw_id"),
                   "pages": n["pages"], "message": n.get("message")} for n in new_only]
    added_formal = [r for r in added_rows if r["tier"] in {"F", "P", "O", "R", "D"}]

    # 与 r2 相同的 finalize 逻辑，只是新条目通过对齐后的旧 raw_id 取裁决。
    by_id = {item["id"]: item for item in items}

    def finalize_mapped(rows_items, auto):
        rows = []
        for r in auto:
            item = by_id[r["id"]]
            old = new_to_old.get(item["id"])
            key = f"{item['doc']}|{old.get('raw_id')}" if old else ""
            row = {"id": r["id"], "doc": item["doc"], "tier": item["tier"], "rule": item["rule"],
                   "raw_id": item.get("raw_id") or item["rule"], "sev": item.get("sev"),
                   "ct": r["ct"], "pages": item["pages"], "title": item["title"], "message": item["message"],
                   "auto_status": r["status"], "auto_links": [link["unit"] for link in r["links"]],
                   "final": "", "links": [], "cause": "", "why": "", "evidence": "", "amend": False,
                   "decided": "auto"}
            d = dec.D.get(key) if item["tier"] in {"F", "P"} else None
            if d:
                row.update(final=d["final"], cause=d["cause"], why=d["why"], evidence=d["evidence"],
                           amend=d["amend"], decided="manual")
                row["links"] = list(d["links"]) if d["final"] in {"TP", "HIT"} and d["links"] is not None \
                    else list(d["links"] or [])
            else:
                status = r["status"]
                if item["tier"] == "F":
                    if status != "TP":
                        raise SystemExit(f"F 条目缺少可继承的旧裁决：{item['id']} 自动状态 {status}")
                    row.update(final="TP", links=list(row["auto_links"]))
                elif item["tier"] == "P":
                    if status == "TP":
                        row.update(final="HIT", links=list(row["auto_links"]))
                    elif status == "NOGOLD":
                        row.update(final="OUT_OF_SCOPE",
                                   why="D-011 同科目收入表/支出表金额差异：口径决议⑤不判错，无金标对应")
                    else:
                        raise SystemExit(f"P 条目缺少可继承的旧裁决：{item['id']} 自动状态 {status}")
                elif item["tier"] == "O":
                    row.update(final=status, links=list(row["auto_links"]))
                else:
                    row.update(final="REF_HIT" if status == "TP" else "REF_OTHER",
                               links=list(row["auto_links"]) if status == "TP" else [])
            rows.append(row)
        return rows

    auto = []
    for item in items:
        if item["tier"] in {"N", "D"}:
            continue
        auto.append(gm.match_o(item, docs[item["doc"]]) if item["tier"] == "O"
                    else gm.match_item(item, docs[item["doc"]]))

    def write_reports(summary: Dict[str, Any]) -> int:
        (out / "eval_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=1, default=dict) + "\n", encoding="utf-8")
        with (out / "item_alignment.csv").open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["kind", "sample_id", "tier", "rule", "old_raw_id", "new_raw_id", "pages",
                             "how_or_status", "in_targets", "target_class", "message"])
            for old, new, how in sorted(pairs, key=lambda p: (p[0]["doc"], str(p[0].get("raw_id")))):
                writer.writerow(["matched", old["doc"], old["tier"], old["rule"], old.get("raw_id"),
                                 new.get("raw_id"), " ".join(map(str, old["pages"])), how, "", "",
                                 new.get("message")])
            for row in removed_rows:
                writer.writerow(["removed", row["sample_id"], row["tier"], row["rule"], row["raw_id"], "",
                                 " ".join(map(str, row["pages"])), "", "Y" if row["in_targets"] else "",
                                 row["target_class"] or "", row["message"]])
            for row in added_rows:
                writer.writerow(["added", row["sample_id"], row["tier"], row["rule"], "", row["raw_id"],
                                 " ".join(map(str, row["pages"])), "", "", "", row["message"]])
        print(json.dumps({k: summary[k] for k in ("items_old", "items_new", "tiers_old", "tiers_new", "pairs",
                                                 "target_removed", "target_missing", "unexpected_removed",
                                                 "added_formal", "decisions_unused_unexpected", "missing_decision",
                                                 "review_required", "resolved_duplicates", "fingerprint_drift",
                                                 "decision_drift", "raw_fingerprint_drift", "raw_missing",
                                                 "reference_exceptions_applied", "reference_exceptions_unused",
                                                 "reference_exception_conflicts", "count_exceptions_applied",
                                                 "headline", "ok")},
                         ensure_ascii=False, indent=1, default=dict)[:6000])
        return 0 if summary["ok"] else 1

    summary: Dict[str, Any] = {
        "run": str(run), "baseline_run": str(baseline_run), "baseline_analysis": str(baseline),
        "items_old": len(old_items), "items_new": len(items),
        "tiers_old": dict(Counter(i["tier"] for i in old_items)),
        "tiers_new": dict(Counter(i["tier"] for i in items)),
        "pairs": len(pairs), "removed": len(removed_rows), "added": len(added_rows),
        "target_removed": len(target_removed), "target_missing": target_missing,
        "unexpected_removed": unexpected_removed, "added_formal": added_formal,
        "decisions_inherited": len(used), "decisions_unused_expected": unused,
        "decisions_unused_unexpected": unexpected_unused, "missing_decision": missing_decision,
        "review_required": review, "resolved_duplicates": resolved_duplicates,
        "fingerprint_drift": fingerprint_drift, "decision_drift": [],
        "raw_fingerprint_drift": raw_fingerprint_drift, "raw_missing": raw_missing,
        "reference_exceptions_applied": reference_exceptions_applied,
        "reference_exceptions_unused": unused_reference_exceptions,
        "reference_exception_conflicts": exception_conflicts,
        "count_exceptions_applied": count_exceptions_applied,
        "reference_exceptions_file": str(reference_exceptions_path),
        "reference_exceptions_sha256": reference_exceptions_sha256,
        "headline": None,
    }
    if review or missing_decision:
        summary["ok"] = False
        return write_reports(summary)

    # r2 的 metrics_main 固定读 <RUN_ROOT>/run1/todo；用输出目录内的别名兼容任意 run 目录名。
    run_alias = out / "_run_alias"
    run_alias.mkdir(exist_ok=True)
    alias_link = run_alias / "run1"
    if alias_link.is_symlink() or alias_link.exists():
        alias_link.unlink()
    alias_link.symlink_to(run, target_is_directory=True)

    # 复用 r2 的指标实现：只换数据目录与“新条目→旧裁决”的取用方式。
    (analysis / "gold_docs.json").write_text(json.dumps(docs, ensure_ascii=False, indent=1), encoding="utf-8")
    (analysis / "system_items.json").write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    (analysis / "auto_match.json").write_text(json.dumps(auto, ensure_ascii=False, indent=1), encoding="utf-8")
    (analysis / "ledgers.json").write_text(json.dumps(aux["ledgers"], ensure_ascii=False), encoding="utf-8")
    gm.REV = "fp_batch1"
    gm.RUN_ROOT = run_alias
    gm.OUT = out
    gm.ANALYSIS = analysis
    gm.finalize = finalize_mapped
    gm.metrics_main(SimpleNamespace())

    metrics = json.loads((analysis / "metrics.json").read_text(encoding="utf-8"))
    old_rows = {f"{row['doc']}|{row.get('raw_id')}": row for row in
                json.loads((baseline / "final_rows.json").read_text(encoding="utf-8"))}
    new_rows = {row["id"]: row for row in
                json.loads((analysis / "final_rows.json").read_text(encoding="utf-8"))}
    decision_drift = []
    for old, new, _ in pairs:
        old_row = old_rows.get(f"{old['doc']}|{old.get('raw_id')}")
        new_row = new_rows.get(new["id"])
        if old_row is None or new_row is None:
            continue
        for field in ("final", "links", "cause", "amend"):
            left, right = old_row.get(field), new_row.get(field)
            if field == "links" and isinstance(left, list) and isinstance(right, list):
                left, right = sorted(left), sorted(right)
            if left != right:
                decision_drift.append({"old_id": old["id"], "new_id": new["id"], "field": field,
                                       "old": left, "new": right})
    summary.update({
        "decision_drift": decision_drift,
        "headline": {
            "F_total": metrics["F_total"], "F_final": metrics["F_final"],
            "FPR_headline": metrics["FPR_headline"], "FPR_strict_gold": metrics["FPR_strict_gold"],
            "MR_overall": metrics["MR_overall"], "MR_CU": metrics["MR_CU"],
            "MR_FU_exhaustive": metrics["MR_FU_exhaustive"],
            "PendingHit": metrics["PendingHit"], "coverage": metrics["coverage"],
            "workload": metrics["workload"], "tiers": metrics["tiers"],
        },
    })
    summary["ok"] = (not added_formal and not unexpected_removed and not target_missing
                     and not unexpected_unused and not missing_decision and not review
                     and not fingerprint_drift and not decision_drift
                     and not raw_fingerprint_drift and not raw_missing
                     and not exception_conflicts)
    return write_reports(summary)


if __name__ == "__main__":
    raise SystemExit(main())
