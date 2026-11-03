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


#: 只对明确的问题引用字段做“字面量相等 或 已验证新旧映射后相等”的归一；
#: 其他 location 字段原样比较，不做整体豁免。
_REF_FIELD_RE = re.compile(r"(?:^|_)(?:issue_id|issue_ids)$")


def _ref_equal(left: Any, right: Any, mapping: Dict[str, str]) -> bool:
    if left == right:
        return True
    if isinstance(left, str) and isinstance(right, str):
        return mapping.get(right) == left
    if isinstance(left, list) and isinstance(right, list):
        mapped = [mapping.get(value, value) if isinstance(value, str) else value for value in right]
        return sorted(map(str, left)) == sorted(map(str, mapped))
    return False


def _collect_ref_votes(left: Any, right: Any, votes: Dict[Tuple[str, str], set],
                       doc: str, path: str = "") -> None:
    """同结构递归收集“文档内 新引用 → 旧引用”的共现票；只对引用字段收集。"""
    if isinstance(left, dict) and isinstance(right, dict):
        for key in sorted(set(left) & set(right)):
            if _REF_FIELD_RE.search(key):
                old_val, new_val = left[key], right[key]
                if isinstance(old_val, str) and isinstance(new_val, str) and old_val != new_val:
                    votes.setdefault((doc, new_val), set()).add(old_val)
                elif isinstance(old_val, list) and isinstance(new_val, list) \
                        and len(old_val) == len(new_val):
                    for old_item, new_item in zip(old_val, new_val, strict=True):
                        if isinstance(old_item, str) and isinstance(new_item, str) and old_item != new_item:
                            votes.setdefault((doc, new_item), set()).add(old_item)
            _collect_ref_votes(left[key], right[key], votes, doc, f"{path}/{key}")
    elif isinstance(left, list) and isinstance(right, list) and len(left) == len(right):
        for index, (old_item, new_item) in enumerate(zip(left, right, strict=True)):
            _collect_ref_votes(old_item, new_item, votes, doc, f"{path}[{index}]")


def reconcile_references(left: Any, right: Any, item_map: Dict[str, str],
                         pre_group_map: Dict[str, str], path: str = ""):
    """把“字面量相等或已验证映射相等”的引用字段替换为占位符，其余原样保留。

    ``pre_group_issue_id`` 是合并前的问题组代表 ID，可能与当前条目 ID 不同；
    它只用同规则组内按顺序验证过的 pre-group 映射，不用条目 ID 映射。
    """
    if isinstance(left, dict) and isinstance(right, dict):
        out_left, out_right = {}, {}
        for key in sorted(set(left) | set(right)):
            mapping = pre_group_map if key == "pre_group_issue_id" else item_map
            if key in left and key in right and _REF_FIELD_RE.search(key) \
                    and _ref_equal(left[key], right[key], mapping):
                out_left[key] = out_right[key] = "<verified-reference>"
            else:
                out_left[key], out_right[key] = reconcile_references(
                    left.get(key), right.get(key), item_map, pre_group_map, f"{path}/{key}")
        return out_left, out_right
    if isinstance(left, list) and isinstance(right, list) and len(left) == len(right):
        pairs = [reconcile_references(a, b, item_map, pre_group_map, f"{path}[{index}]")
                 for index, (a, b) in enumerate(zip(left, right, strict=True))]
        return [pair[0] for pair in pairs], [pair[1] for pair in pairs]
    return left, right


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
    new_to_old_raw: Dict[str, Dict[str, str]] = {}
    for old, new, _ in pairs:
        if old.get("raw_id") and new.get("raw_id"):
            new_to_old_raw.setdefault(old["doc"], {})[new["raw_id"]] = old["raw_id"]
    # pre_group_issue_id 是合并前的问题组代表 ID，可能与当前条目 ID 不同；按同文档
    # 同规则组内顺序验证后建立 new_pre → old_pre 映射，不套用条目 ID 映射。
    pre_groups: Dict[Tuple[str, str], List[Tuple[str, str, str, str]]] = defaultdict(list)
    for old, new, _ in pairs:
        raw_old, raw_new = raw_item(old_raw_index, old), raw_item(new_raw_index, new)
        if raw_old is None or raw_new is None:
            continue
        loc_old = raw_old.get("location") if isinstance(raw_old.get("location"), dict) else {}
        loc_new = raw_new.get("location") if isinstance(raw_new.get("location"), dict) else {}
        old_pre, new_pre = loc_old.get("pre_group_issue_id"), loc_new.get("pre_group_issue_id")
        if old_pre and new_pre:
            pre_groups[(old["doc"], old["rule"])].append((old_pre, new_pre, old["id"], new["id"]))
    pre_group_map: Dict[Tuple[str, str], str] = {}
    pre_group_details = []
    for (doc, rule), entries in pre_groups.items():
        olds = [entry[0] for entry in entries]
        news = [entry[1] for entry in entries]
        verified = len(olds) == len(set(olds)) and len(news) == len(set(news))
        if verified:
            for old_pre, new_pre, _, _ in entries:
                pre_group_map[(doc, new_pre)] = old_pre
        pairs_detail = [{"old_id": entry[2], "new_id": entry[3],
                         "old_pre": entry[0], "new_pre": entry[1]} for entry in entries]
        pre_group_details.append({
            "doc": doc, "rule": rule, "count": len(entries), "verified": verified,
            "changed_count": sum(1 for item in pairs_detail if item["old_pre"] != item["new_pre"]),
            "pairs": pairs_detail})
    # 合并成员 ID（如 member_issue_ids/merged_issue_ids 指向已合并、未单独输出的问题）
    # 没有条目级配对；按同结构同位置的共现票建立映射，只有票数唯一才采用。
    ref_votes: Dict[Tuple[str, str], set] = {}
    for old, new, _ in pairs:
        raw_old, raw_new = raw_item(old_raw_index, old), raw_item(new_raw_index, new)
        if raw_old is not None and raw_new is not None:
            _collect_ref_votes(raw_old, raw_new, ref_votes, old["doc"])
    vote_map = {key: next(iter(olds)) for key, olds in ref_votes.items() if len(olds) == 1}
    vote_conflicts = {f"{key[0]}|{key[1]}": sorted(olds)
                      for key, olds in ref_votes.items() if len(olds) > 1}
    raw_fingerprint_drift, raw_missing, reference_mappings = [], [], []
    approved_ledger_detail = []
    approved_notice_counts = []
    target_rules_by_doc: Dict[str, set] = defaultdict(set)
    for (sid, _), row in targets.items():
        if row["expect_after"] == "移除":
            target_rules_by_doc[sid].add(row["rule"])
    for old, new, how in pairs:
        raw_old = raw_item(old_raw_index, old)
        raw_new = raw_item(new_raw_index, new)
        if raw_old is None or raw_new is None:
            raw_missing.append({"old_id": old["id"], "new_id": new["id"],
                                "old_found": raw_old is not None, "new_found": raw_new is not None})
            continue
        mapping = new_to_old_raw.get(old["doc"], {})
        pre_map = {new_pre: old_pre for (doc, new_pre), old_pre in pre_group_map.items()
                   if doc == old["doc"]}
        doc_votes = {new_ref: old_ref for (doc, new_ref), old_ref in vote_map.items()
                     if doc == old["doc"]}
        item_effective = {**doc_votes, **mapping}
        pre_effective = {**doc_votes, **pre_map}
        left = canonical_raw(raw_old)
        right = canonical_raw(raw_new)
        left_base, right_base = reconcile_references(left, right, {}, {})
        left, right = reconcile_references(left, right, item_effective, pre_effective)
        if (left_base, right_base) != (left, right):
            reference_mappings.append({"old_id": old["id"], "new_id": new["id"],
                                       "mapped_reference_ids": True})
        paths = diff_paths(left, right)
        if not paths:
            continue
        # 已批准的附带变化：O 层义务账本 detail 的规则 fail→pass，且没有其他字段变化。
        if (old["tier"] == "O" and all(path == "/detail" for path in paths)
                and re.search(r"=fail\b", str(raw_old.get("detail") or ""))
                and re.search(r"=pass\b", str(raw_new.get("detail") or ""))):
            approved_ledger_detail.append({"old_id": old["id"], "new_id": new["id"],
                                           "old_detail": raw_old.get("detail"),
                                           "new_detail": raw_new.get("detail")})
            continue
        # 已批准的附带变化：N 层“规则取数不足”提示里的 partial_findings_count 随目标
        # 正式问题移除而变化；只在该计数所属规则确有目标移除时放行，并单独记账。
        if (old["tier"] == "N" and paths
                and all(re.fullmatch(r"/unresolved_rules\[\d+\]/partial_findings_count", path)
                        for path in paths)):
            old_rules = raw_old.get("unresolved_rules") or []
            entries, approved = [], True
            for path in paths:
                index = int(re.search(r"\[(\d+)\]", path).group(1))
                rule_id = (old_rules[index] or {}).get("rule_id") if index < len(old_rules) else None
                if rule_id not in target_rules_by_doc.get(old["doc"], set()):
                    approved = False
                    break
                entries.append({"rule_id": rule_id, "path": path})
            if approved:
                approved_notice_counts.append({"old_id": old["id"], "new_id": new["id"],
                                               "changes": entries})
                continue
        raw_fingerprint_drift.append({"old_id": old["id"], "new_id": new["id"], "how": how,
                                      "paths": paths[:20]})

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
                                                 "raw_reference_mappings", "approved_ledger_detail_changes",
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
        "raw_reference_mappings": reference_mappings,
        "pre_group_reference_mappings": pre_group_details,
        "reference_vote_conflicts": vote_conflicts,
        "approved_ledger_detail_changes": approved_ledger_detail,
        "approved_notice_count_changes": approved_notice_counts,
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
                     and not vote_conflicts)
    return write_reports(summary)


if __name__ == "__main__":
    raise SystemExit(main())
