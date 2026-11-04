#!/usr/bin/env python3
"""第二批 33 份金标阶段比较器（阶段契约版，独立于第一批评估器）。

用途：
- ``--mode self``：同一代码两遍采集自比，要求零差异（冻结组合基线 / 每段回归的第一步）。
- ``--mode baseline``：组合基线对第一批已验收结果，要求零业务差异（集成只许组合，不许夹带）。
- ``--mode stage``：本段结果对冻结组合基线，只允许本段目标与契约列明字段变化。

与第一批评估器的关系：
- 复用其对齐、完整裁决签名、原始 status 指纹、引用/计数精确例外与歧义 REVIEW 逻辑；
- 目标语义改为第二批 ``target_matrix.csv``：FP 目标=指定条目消失/转回执，MISS 目标=按检查键+页码+原文出现新正式条目；
- 第一批 45 条引用例外**不继承**；本工具默认无例外，只有显式 ``--reference-exceptions`` 才读取，
  且必须是第二批自己冻结的清单。

用法：
    .venv/bin/python scripts/batch2_gold_compare.py \
        --mode stage --stage S2 \
        --run outputs/batch2/S2/run1 \
        --baseline-run outputs/batch2/baseline/run1 \
        --baseline-analysis outputs/batch2/baseline/analysis \
        --targets docs/joint_execution_20261103/target_matrix.csv \
        --contract docs/batch2_r1_20261103/batch2_stage_contract.json \
        --out outputs/batch2/S2/compare
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
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

HERE = Path(__file__).resolve().parent
BATCH1_EVAL = HERE / "gold_baseline_eval_fp_batch1.py"
#: r2 冻结匹配脚本在运行仓库 scripts/（未入 git），固定只读路径；REPO 由脚本自身位置推导。
DEFAULT_MATCH = Path(
    "/Volumes/workSSD/software development/GovBudgetChecker/scripts/gold_baseline_match.py")
FORMAL_TIERS = {"F", "P", "O", "R", "D"}


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"无法加载模块：{path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def load_targets(path: Path, stage: str) -> Dict[str, Any]:
    """读取 target_matrix，按阶段切分 FP 移除目标与 MISS 新增目标。"""
    fp_rows, miss_rows = [], []
    with path.open(encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            if row.get("owner_stage") != stage:
                continue
            if row["target_type"] == "FP":
                fp_rows.append({
                    "target_id": row["target_id"], "sample_id": row["sample_id"],
                    "system_item_id": row["source_id"], "rule": row.get("rule_or_check"),
                    "pages": [int(p) for p in (row.get("evidence_pages") or "").split() if p.isdigit()],
                })
            elif row["target_type"] == "MISS":
                miss_rows.append({
                    "target_id": row["target_id"], "sample_id": row["sample_id"],
                    "check_key": row.get("rule_or_check"), "gold_unit": row.get("gold_units"),
                    "pages": [int(p) for p in (row.get("evidence_pages") or "").split() if p.isdigit()],
                })
    return {"fp_removals": fp_rows, "miss_additions": miss_rows}


def load_contract(path: Optional[Path]) -> Dict[str, Any]:
    if path is None:
        return {"check_rules": {}, "allowed_field_changes": [], "resolved_earlier": []}
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.setdefault("check_rules", {})
    payload.setdefault("allowed_field_changes", [])
    payload.setdefault("resolved_earlier", [])
    return payload


def item_key(item: Dict[str, Any]) -> Tuple[str, str]:
    return item["doc"], str(item.get("raw_id") or item["rule"])


def _pages(item: Dict[str, Any]) -> List[int]:
    values = item.get("_all_pages") or item.get("pages") or []
    return [int(p) for p in values if str(p).isdigit()]


def _rule_matches(item: Dict[str, Any], rules: Sequence[str]) -> bool:
    if not rules:
        return False
    rule = str(item.get("rule") or "")
    for expected in rules:
        token = str(expected).strip()
        if not token:
            continue
        if rule == token or rule.startswith(token + "-"):
            return True
    return False


def _quote_matches(item: Dict[str, Any], quotes: Sequence[str], eval_lib) -> bool:
    if not quotes:
        return True
    haystack = "".join(eval_lib.norm(q) for q in item.get("quotes") or [])
    return any(eval_lib.norm(q) and eval_lib.norm(q) in haystack for q in quotes)


def _find_unit(docs, sample_id: str, unit_id: str):
    if not docs or sample_id not in docs:
        return None
    for unit in docs[sample_id].get("units") or []:
        if str(unit.get("unit_id")) == str(unit_id):
            return unit
    return None


def _defect_anchors(unit: Dict[str, Any], needles: Sequence[str] = ()) -> List[str]:
    """从金标单位取“错误出现点”原文；有契约缺陷词时只保留命中项。"""
    quotes = [str(value.get("quote") or "") for value in unit.get("values") or []
              if isinstance(value, dict)]
    anchors = []
    for quote in quotes:
        compact = re.sub(r"\s+", "", quote)
        if not compact or re.fullmatch(r"[\d,.;%％]+", compact):
            continue
        chinese = len(re.findall(r"[\u4e00-\u9fff]", compact))
        if chinese < 3:
            continue
        if re.search(r"完成年初预算的|完成预算的", compact) and not re.search(r"支出|收入|费|金", compact):
            continue
        anchors.append(quote)
    if needles:
        compact_needles = [re.sub(r"\s+", "", str(item)) for item in needles if str(item)]
        narrowed = [quote for quote in anchors
                    if any(item and item in re.sub(r"\s+", "", quote) for item in compact_needles)]
        if narrowed:
            return narrowed
    return anchors or quotes


_AMOUNT_TOKEN_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")
_UNIT_TOKENS = ("万元", "亿元", "元", "%", "％")


def _amount_tokens(text: str) -> List[str]:
    normalized = re.sub(r"\s+", "", str(text or ""))
    return [token.replace(",", "").replace("，", "")
            for token in _AMOUNT_TOKEN_RE.findall(normalized)]


def _text_segments(needle: str) -> List[str]:
    """去掉金额、单位和括注后的中文片段；不按名称前缀/后缀互相吞并。"""
    segments = re.split(r"[（(][^）)]*[）)]|\d[\d,.]*\s*(?:万元|亿元|元|%|％)?", needle)
    return [segment for segment in segments
            if len(re.findall(r"[\u4e00-\u9fff]", segment)) >= 4]


#: 金额 token + 紧跟其后的精确单位。顺序保证“万元/亿元”先于“元”，
#: 避免把“100.00万元”解析成单位“元”。
_AMOUNT_UNIT_RE = re.compile(r"(?<![\d.,，])(\d[\d,]*(?:\.\d+)?)\s*(万元|亿元|元|%|％)?")


def _amount_bindings(text: str) -> List[tuple]:
    """[(数值, 精确单位)]；单位只认紧跟在数值后的那一个。

    数值做 canonical 化（去千分位、去前导零），"06万元" 与 "6万元" 是同一
    金额；这不改变 9.99 与 1,659.14 的区分。
    """
    bindings = []
    for match in _AMOUNT_UNIT_RE.finditer(text or ""):
        unit = match.group(2) or ""
        if unit == "％":
            unit = "%"
        raw = match.group(1).replace(",", "").replace("，", "")
        integer, dot, fraction = raw.partition(".")
        integer = integer.lstrip("0") or "0"
        canonical = f"{integer}.{fraction}" if dot else integer
        bindings.append((canonical, unit))
    return bindings


def _item_occurrences(item: Dict[str, Any]) -> List[tuple]:
    """[(page, text)]：同一引用内部的文本只与它自己的 page 绑定。

    R2-02：不再把不同页的 text/quotes/refs 汇总成一个语料池——页 6 的引文
    不能借 finding.pages 覆盖页 5。只有证据引用、位置引用或单页条目才带页；
    多页条目的顶层正文无法确定归属时页为 None（fail-closed）。
    """
    occurrences: List[tuple] = []
    location = item.get("location") if isinstance(item.get("location"), dict) else {}
    for key in ("locations", "table_refs", "fact_source_refs", "occurrences"):
        entries = location.get(key)
        if not isinstance(entries, (list, tuple)):
            continue
        for ref in entries:
            if not isinstance(ref, dict):
                continue
            value = (ref.get("text") or ref.get("span") or ref.get("quote")
                     or ref.get("text_snippet"))
            if not value:
                continue
            raw_page = ref.get("page")
            page = int(raw_page) if isinstance(raw_page, int) and raw_page > 0 else None
            occurrences.append((page, str(value)))
    for evidence in item.get("evidence") or []:
        if not isinstance(evidence, dict):
            continue
        value = (evidence.get("quote") or evidence.get("text")
                 or evidence.get("text_snippet"))
        if not value:
            continue
        raw_page = evidence.get("page")
        page = int(raw_page) if isinstance(raw_page, int) and raw_page > 0 else None
        occurrences.append((page, str(value)))
    pages = {int(p) for p in item.get("pages") or [] if isinstance(p, int)}
    primary = location.get("page")
    single = next(iter(pages)) if len(pages) == 1 else None
    if single is None and isinstance(primary, int) and primary > 0:
        single = primary
    for value in [str(item.get("text") or "")] + [str(q) for q in item.get("quotes") or [] if q]:
        if value:
            occurrences.append((single, value))
    return occurrences


def _anchor_covered(item: Dict[str, Any], anchor: str, eval_lib,
                    page: Optional[int] = None, amount_required: bool = True) -> bool:
    """出现点覆盖：页、局部引文、金额、精确单位必须属于同一出现点。

    R2-02 收紧（独立复核 20261104）：
    - 传入 ``page`` 时必须存在绑定到该页的引用；缺页码/只有其他页的引文
      不能证明带页码锚点。
    - 金额锚点的每个数值必须在同一段引用内逐字命中，且紧跟的精确单位
      一致；“元”不能被“万元”的子串关系满足。
    - 金额不符后不再用“同页 + 名称片段”放行。名称/口径类契约必须由调用方
      显式传 ``amount_required=False``，并且仍要求同页同段名称命中。
    """
    needle = eval_lib.norm(anchor)
    if not needle:
        return False
    occurrences = [(p, eval_lib.norm(t)) for p, t in _item_occurrences(item) if t]
    if page is not None:
        occurrences = [(p, t) for p, t in occurrences if p == page]
    if not occurrences:
        return False
    anchor_bindings = _amount_bindings(needle)
    segments = _text_segments(needle)
    if not anchor_bindings:
        # 纯文本锚点：逐出现点匹配，不跨段拼接。
        for _p, text in occurrences:
            if needle in text:
                return True
        for _p, text in occurrences:
            shorter, longer = sorted((len(text), len(needle)))
            if text and (text in needle or needle in text) and longer and shorter / longer >= 0.8:
                return True
            for segment in segments:
                if len(segment) >= max(4, int(len(needle) * 0.6)) and segment in text:
                    return True
        if not amount_required and page is not None:
            for _p, text in occurrences:
                for segment in segments:
                    for start in range(0, max(1, len(segment) - 7)):
                        window = segment[start:start + 8]
                        if len(re.findall(r"[\u4e00-\u9fff]", window)) >= 6 and window in text:
                            return True
        return False
    # 金额锚点：同一出现段内数值、精确单位、名称三项都要命中。
    # 百分比是另一维度，作为金额缺陷的上下文时不强制复述；纯百分比锚点
    # （如占比检查）仍要求全部百分比命中。
    material = [item for item in anchor_bindings if item[1] != "%"]
    if not material:
        material = list(anchor_bindings)
    for _p, text in occurrences:
        units_by_number: Dict[str, set] = {}
        for number, unit in _amount_bindings(text):
            units_by_number.setdefault(number, set()).add(unit)
        amount_ok = True
        for number, unit in material:
            if number not in units_by_number:
                amount_ok = False
                break
            # 锚点带单位时必须精确一致；锚点未写单位时只绑定数值本身。
            if unit and unit not in units_by_number[number]:
                amount_ok = False
                break
        if amount_ok and (not segments or any(segment in text for segment in segments)):
            return True
        # 名称/口径类显式契约：候选不必复述金额，但必须同页同段命中名称。
        # 这是唯一允许忽略金额的分支，且不会被金额/格式类调用方启用。
        if not amount_required and page is not None and segments:
            if any(segment in text for segment in segments):
                return True
            for segment in segments:
                for start in range(0, max(1, len(segment) - 7)):
                    window = segment[start:start + 8]
                    if len(re.findall(r"[\u4e00-\u9fff]", window)) >= 6 and window in text:
                        return True
    return False


def _defect_anchor_entries(unit: Dict[str, Any], needles: Sequence[str] = ()) -> List[Dict[str, Any]]:
    """带页码的出现点：用于把覆盖绑定到原页，而不是只看正文。"""
    entries = []
    for value in unit.get("values") or []:
        if not isinstance(value, dict):
            continue
        quote = str(value.get("quote") or "")
        compact = re.sub(r"\s+", "", quote)
        if not compact or re.fullmatch(r"[\d,.;%％]+", compact):
            continue
        if len(re.findall(r"[\u4e00-\u9fff]", compact)) < 3:
            continue
        if re.search(r"完成年初预算的|完成预算的", compact) and not re.search(r"支出|收入|费|金", compact):
            continue
        entries.append({"quote": quote, "page": value.get("page")})
    if needles:
        compact_needles = [re.sub(r"\s+", "", str(item)) for item in needles if str(item)]
        narrowed = [entry for entry in entries
                    if any(item and item in re.sub(r"\s+", "", entry["quote"])
                           for item in compact_needles)]
        if narrowed:
            return narrowed
    return entries or [{"quote": str(value.get("quote") or ""), "page": value.get("page")}
                        for value in unit.get("values") or [] if isinstance(value, dict)]


def _item_covers_anchor(item: Dict[str, Any], target: Dict[str, Any], docs, eval_lib) -> bool:
    unit = _find_unit(docs, target["sample_id"], target.get("gold_unit") or "")
    if unit is None:
        return True  # 无金标单位数据时退回页码/原文匹配
    return any(_anchor_covered(item, entry["quote"], eval_lib, entry.get("page"))
               for entry in _defect_anchor_entries(unit))


def _evidence_qualified(item: Dict[str, Any]) -> bool:
    """正式命中必须显式证明证据合格，不能因 tier=F 或存在 quote 就默认完整。"""
    status = str(item.get("evidence_status") or "").strip()
    if status != "complete":
        return False
    raw = item.get("evidence_verification") or {}
    if isinstance(raw, dict) and str(raw.get("status") or "") == "failed":
        return False
    return True


def _worse_evidence(previous, current):
    """同一实体在 review_report 与 result 两处表示中取更差证据状态（fail-closed）。"""
    if previous is None:
        return current

    def rank(pair):
        return (pair[0] == "complete", pair[1] == "verified")

    return previous if rank(previous) <= rank(current) else current


def match_addition(item: Dict[str, Any], targets: Sequence[Dict[str, Any]],
                   contract: Dict[str, Any], eval_lib, gm=None, docs=None) -> List[Dict[str, Any]]:
    """把新增正式条目匹配到 MISS 目标。

    首选金标匹配器的 unit 链接（与 r2 命中口径一致）；契约提供 ``check_rules`` 时
    作为补充路径，仍要求样本+页码+原文同时满足。
    """
    linked = set()
    if gm is not None and docs is not None and item["doc"] in docs:
        try:
            match = gm.match_item(item, docs[item["doc"]])
        except Exception:  # noqa: BLE001 - 匹配器不认识的结构退回契约路径
            match = {}
        linked = {str(link.get("unit")) for link in match.get("links") or [] if isinstance(link, dict)}
    hits = []
    message_needles = contract.get("target_message_contains") or {}
    for target in targets:
        if item["doc"] != target["sample_id"]:
            continue
        # 页/规则相同的多个 MISS 目标（同文档同检查的不同原子点）用条目正文中的
        # 缺陷词消歧；只在契约显式给出时生效，未给出时不改变原有匹配。
        needles = message_needles.get(target["target_id"]) or []
        if needles and not any(str(needle) in str(item.get("message") or "") for needle in needles):
            continue
        if target.get("gold_unit") and target["gold_unit"] in linked:
            # unit 链接只证明“同一金标单位”，还要绑定目标检查规则与页码，
            # 否则同文档任意 F 条目都能借 unit 链接冒充该出现点。
            rules = contract["check_rules"].get(target["check_key"] or "", [])
            if rules and not _rule_matches(item, rules):
                continue
            if target["pages"] and not (set(_pages(item)) & set(target["pages"])):
                continue
            if _item_covers_anchor(item, target, docs, eval_lib):
                hits.append(target)
            continue
        if target["pages"] and not (set(_pages(item)) & set(target["pages"])):
            continue
        rules = contract["check_rules"].get(target["check_key"] or "", [])
        if not _rule_matches(item, rules):
            continue
        if not _quote_matches(item, contract.get("target_quotes", {}).get(target["target_id"], []), eval_lib):
            continue
        if not _item_covers_anchor(item, target, docs, eval_lib):
            continue
        hits.append(target)
    return hits


def run(args: argparse.Namespace) -> int:
    run_dir = Path(args.run).resolve()
    baseline_run = Path(args.baseline_run).resolve()
    baseline_analysis = Path(args.baseline_analysis).resolve()
    targets_path = Path(args.targets).resolve()
    out = Path(args.out).resolve()
    if out.exists() and any(out.iterdir()):
        raise SystemExit(f"输出目录已存在且非空：{out}")
    out.mkdir(parents=True, exist_ok=True)

    eval_lib = load_module("batch1_eval_lib", BATCH1_EVAL)
    gm = load_module("gold_baseline_match", Path(args.match_script).resolve())
    sys.path.insert(0, str(Path(args.match_script).resolve().parent))
    import gold_baseline_decisions_r2 as dec  # noqa: E402

    docs = gm.load_gold()
    items, _aux = gm.load_system(run_dir, docs)
    # 冻结匹配器构造的条目行不带证据字段；从原始 status 实体补齐，供
    # “正式命中必须证据合格”和证据回退检查使用（不改变匹配语义）。
    new_raw_index = eval_lib.load_raw_index(run_dir)
    for item in items:
        raw = eval_lib.raw_item(new_raw_index, item) or {}
        if isinstance(raw, dict):
            if not str(item.get("evidence_status") or "").strip():
                item["evidence_status"] = raw.get("evidence_status")
            if not item.get("evidence_verification"):
                item["evidence_verification"] = raw.get("evidence_verification")
    old_items = json.loads((baseline_analysis / "system_items.json").read_text(encoding="utf-8"))
    baseline_rows = {f"{row['doc']}|{row.get('raw_id')}": row for row in
                     json.loads((baseline_analysis / "final_rows.json").read_text(encoding="utf-8"))}

    targets = load_targets(targets_path, args.stage) if args.mode == "stage" else {"fp_removals": [], "miss_additions": []}
    contract = load_contract(Path(args.contract).resolve() if args.contract else None)

    def decision_of(item: Dict[str, Any]):
        return eval_lib.decision_signature(baseline_rows.get(f"{item['doc']}|{item.get('raw_id') or item['rule']}"))

    # 组合期 O/N 条目的 message 承载义务 detail；DH-03b 会改写 detail。
    # 身份按 (文档, 层级, 规则, 页码, 严重度) 对齐，文本漂移单独记录并须命中允许字段。
    ignore_text_tiers = set(contract.get("identity_ignore_text_tiers") or ())
    ignore_tier_rules = set(contract.get("identity_ignore_tier_rules") or ())
    ignore_pages_rules = set(contract.get("identity_ignore_pages_rules") or ())
    if ignore_text_tiers or ignore_tier_rules or ignore_pages_rules:
        original_identity = eval_lib.identity_key

        def _identity(item, _original=original_identity, _tiers=ignore_text_tiers,
                      _rules=ignore_tier_rules, _page_rules=ignore_pages_rules):
            if item.get("rule") in _page_rules:
                # 证据/页码增强不改变同一名称差异的身份；裁决继续继承。
                return (item["doc"], item["rule"], eval_lib.norm(item.get("message")))
            if item.get("tier") in _tiers:
                return (item["doc"], item["tier"], item["rule"], tuple(item["pages"]), item.get("sev"))
            if item.get("rule") in _rules:
                return (item["doc"], item["rule"], tuple(item["pages"]),
                        eval_lib.norm(item.get("message")))
            return _original(item)

        eval_lib.identity_key = _identity

    pairs, old_only, new_only, review, resolved_duplicates = eval_lib.align(old_items, items, decision_of)
    new_to_old = {new["id"]: old for old, new, _ in pairs}

    # ---- 目标判定 ----
    fp_targets = {(t["sample_id"], str(t["system_item_id"])): t for t in targets["fp_removals"]}
    miss_targets = targets["miss_additions"]
    removed_keys = {item_key(o) for o in old_only}
    def _target_for_removed(old):
        direct = fp_targets.get(item_key(old))
        if direct is not None:
            return direct
        # 序号会随前序小批整体位移：按 样本+规则+页码 兜底匹配冻结目标。
        pages = set(_pages(old))
        for target in fp_targets.values():
            if (target["sample_id"] == old["doc"] and target.get("rule") == old["rule"]
                    and (not target["pages"] or pages & set(target["pages"]))):
                return target
        return None

    removed_target_hits = []
    removed_unexpected = []
    removed_notice = []
    allow_notice_changes = bool(contract.get("allowed_notice_changes"))
    for old in old_only:
        target = _target_for_removed(old)
        if target:
            removed_target_hits.append({
                "sample_id": old["doc"], "raw_id": old.get("raw_id"), "rule": old["rule"],
                "pages": _pages(old), "target_id": target["target_id"],
            })
            continue
        if old.get("rule") in set(contract.get("allowed_removed_rules") or []):
            removed_notice.append({
                "sample_id": old["doc"], "raw_id": old.get("raw_id"), "rule": old["rule"],
            })
            continue
        if allow_notice_changes and old.get("tier") not in {"F", "P"}:
            removed_notice.append({
                "sample_id": old["doc"], "raw_id": old.get("raw_id"), "rule": old["rule"],
            })
            continue
        removed_unexpected.append({
            "sample_id": old["doc"], "raw_id": old.get("raw_id"), "rule": old["rule"],
            "pages": _pages(old), "target_id": (target or {}).get("target_id"),
        })
    missing_removals = [t for key, t in fp_targets.items()
                        if key not in removed_keys
                        and not any(h["target_id"] == t["target_id"] for h in removed_target_hits)]

    # 严格修复只认 F 且证据合格；P 只计候选，不得把候选当正式修复。
    formal_additions = [n for n in new_only if n["tier"] == "F" and _evidence_qualified(n)]
    candidate_additions = [n for n in new_only if n["tier"] == "P"]
    # 目标覆盖允许使用当前全部 F 层条目（含既有 F）；新增统计仍只看 new_only。
    coverage_additions = [item for item in items
                          if item.get("tier") == "F" and _evidence_qualified(item)]
    new_only_ids = {item["id"] for item in new_only}
    for old, new, _how in pairs:
        if new.get("tier") == "P" and old.get("tier") != "P":
            candidate_additions.append(new)
    def _enrich_pages(item):
        pages = set(_pages(item))
        raw = eval_lib.raw_item(new_raw_index, item) or {}
        location = raw.get("location") if isinstance(raw.get("location"), dict) else {}
        if location:
            item["location"] = location
        # 证据引用各自带 page：出现点绑定要按引用页，而不是汇总成全文。
        evidence = raw.get("evidence") if isinstance(raw.get("evidence"), list) else []
        if evidence and not item.get("evidence"):
            item["evidence"] = evidence
            for ref in evidence:
                if isinstance(ref, dict) and isinstance(ref.get("page"), int) and ref["page"] > 0:
                    pages.add(ref["page"])
        for key in ("locations", "table_refs", "occurrences", "pages"):
            entries = location.get(key)
            if isinstance(entries, list):
                for entry in entries:
                    page = entry.get("page") if isinstance(entry, dict) else None
                    if isinstance(page, int) and page > 0:
                        pages.add(page)
            elif isinstance(entries, int) and entries > 0:
                pages.add(entries)
        item["_all_pages"] = sorted(pages)
        return item

    satisfied: Dict[str, List[str]] = defaultdict(list)
    partial_targets: Dict[str, Dict[str, Any]] = {}
    unexpected_additions = []
    expected_additions = []
    added_notice = []
    for item in new_only:
        if allow_notice_changes and item.get("tier") not in {"F", "P"}:
            added_notice.append({"sample_id": item["doc"], "raw_id": item.get("raw_id"), "rule": item["rule"]})
    target_anchors: Dict[str, List[Dict[str, Any]]] = {}
    for target in miss_targets:
        unit = _find_unit(docs, target["sample_id"], target.get("gold_unit") or "")
        needles = (contract.get("target_message_contains") or {}).get(target["target_id"]) or []
        target_anchors[target["target_id"]] = _defect_anchor_entries(unit, needles) if unit else []
    target_covered: Dict[str, set] = defaultdict(set)
    target_items: Dict[str, List[str]] = defaultdict(list)
    for item in coverage_additions:
        _enrich_pages(item)
        hits = match_addition(item, miss_targets, contract, eval_lib, gm=gm, docs=docs)
        if len(hits) > 1:
            review.append({"new_id": item["id"], "reason": "新增条目同时匹配多个 MISS 目标",
                           "targets": [h["target_id"] for h in hits]})
        if not hits:
            if item["id"] not in new_only_ids:
                # 既有 F 条目只是参与出现点覆盖，不是本段新增。
                continue
            allowed_rules = {str(rule) for rule in contract.get("allowed_added_rules") or []}
            exact = next((entry for entry in contract.get("expected_additions") or []
                          if isinstance(entry, dict) and entry.get("sample_id") == item["doc"]
                          and str(entry.get("rule") or "") == str(item["rule"])
                          and (not entry.get("raw_id") or str(entry.get("raw_id")) == str(item.get("raw_id")))
                          and all(str(needle) in str(item.get("message") or "")
                                  for needle in entry.get("message_contains") or [])), None)
            if item["rule"] in allowed_rules or exact is not None:
                expected_additions.append({"sample_id": item["doc"], "raw_id": item.get("raw_id"),
                                           "rule": item["rule"], "pages": _pages(item),
                                           "basis": (exact or {}).get("basis") or "契约允许的本段变更"})
            else:
                unexpected_additions.append({"sample_id": item["doc"], "raw_id": item.get("raw_id"),
                                             "rule": item["rule"], "pages": _pages(item)})
            continue
        for target in hits:
            anchors = target_anchors.get(target["target_id"]) or []
            name_only = (target.get("check_key") or "") in {
                "GS-N05", "GS-S01", "GS-S03", "GS-S04", "free:R21",
                "free:R22", "free:R24", "free:R28", "free:R9-半角冒号",
            }
            covered = {index for index, anchor in enumerate(anchors)
                       if _anchor_covered(item, anchor["quote"], eval_lib, anchor.get("page"),
                                          amount_required=not name_only)}
            target_covered[target["target_id"]] |= covered
            target_items[target["target_id"]].append(item["id"])
    missing_additions = []
    occurrence_coverage = []
    for target in miss_targets:
        anchors = target_anchors.get(target["target_id"]) or []
        covered = sorted(target_covered.get(target["target_id"]) or set())
        full = bool(anchors) and len(covered) == len(anchors)
        occurrence_coverage.append({
            "target_id": target["target_id"], "sample_id": target["sample_id"],
            "anchors": len(anchors), "covered": len(covered),
            "missing": [anchors[index]["quote"] for index in range(len(anchors)) if index not in covered],
            "items": target_items.get(target["target_id"]) or [],
        })
        if full:
            satisfied[target["target_id"]] = target_items.get(target["target_id"]) or []
        else:
            missing_additions.append(target["target_id"])
            partial_targets[target["target_id"]] = occurrence_coverage[-1]

    # ---- 非目标条目漂移 ----
    target_fp_keys = set(fp_targets)
    allowed_fields_by_tier = contract.get("allowed_fingerprint_fields_by_tier") or {}
    fingerprint_drift = []
    expected_fingerprint_drift = []
    decision_drift = []
    for old, new, how in pairs:
        if item_key(old) in target_fp_keys:
            continue
        diffs = eval_lib.fingerprint_diffs(old, new)
        if diffs:
            allowed_fields = set(allowed_fields_by_tier.get(new.get("tier"), []))
            allowed_fields |= set((contract.get("allowed_fingerprint_fields_by_rule") or {}).get(new.get("rule"), []))
            unexpected = [field for field in diffs if field not in allowed_fields]
            record = {"sample_id": old["doc"], "old_raw_id": old.get("raw_id"),
                      "new_raw_id": new.get("raw_id"), "how": how, "fields": diffs,
                      "old": {f: old.get(f) for f in diffs},
                      "new": {f: new.get(f) for f in diffs}}
            if unexpected:
                record["unexpected_fields"] = unexpected
                fingerprint_drift.append(record)
            else:
                expected_fingerprint_drift.append(record)
        # 裁决继承按对齐关系进行；这里只核对基线条目确有最终态可继承，
        # 不按展示序号（raw_id）比较——序号会随目标增删整体位移。
        if new.get("tier") in {"F", "P"}:
            old_row = baseline_rows.get(f"{old['doc']}|{old.get('raw_id')}")
            if old_row is None or not str(old_row.get("final") or "").strip():
                decision_drift.append({"sample_id": old["doc"], "raw_id": old.get("raw_id"),
                                       "reason": "基线缺少可继承裁决"})

    # ---- 引用/计数/原始指纹 ----
    old_raw_index = eval_lib.load_raw_index(baseline_run)
    old_entity = eval_lib.load_entity_index(baseline_run)
    new_entity = eval_lib.load_entity_index(run_dir)
    id_maxima = eval_lib.collect_id_maxima(baseline_run)
    ref_exceptions, count_exceptions = {}, {}
    exceptions_sha = None
    if args.reference_exceptions:
        ref_path = Path(args.reference_exceptions).resolve()
        ref_exceptions, count_exceptions, _payload = eval_lib.load_reference_exceptions(ref_path)
        exceptions_sha = eval_lib.hashlib.sha256(ref_path.read_bytes()).hexdigest()
    exception_conflicts = eval_lib.exception_bidirectional_conflicts(list(ref_exceptions.values()))

    raw_drift, raw_expected, raw_missing = [], [], []
    allow_prefixes = tuple(contract.get("raw_path_allowlist") or ())
    reference_keys = {"pre_group_issue_id", "member_issue_ids", "merged_issue_ids"}

    class _ReferenceIndex:
        """从冻结 status 建“问题/分组/成员”索引，引用必须能落到真实实体。

        成员分三档，避免“任意写一个 ID 就自动变成合法虚拟成员”：
        - ``real``：本次输出里真实存在的问题；
        - ``evidenced``：合并后未单独输出、但有 ``member_evidence`` 证明的成员；
        - ``virtual``：只在成员列表里出现、没有独立证据的编号——只能在
          基线原样保留时容忍，不能作为新增引用。
        """

        def __init__(self, run_dir: Path):
            self.issues: Dict[Tuple[str, str], str] = {}
            self.groups: Dict[Tuple[str, str], set] = defaultdict(set)
            self.real: set = set()
            self.evidenced: set = set()
            self.virtual: set = set()
            self.group_of: Dict[Tuple[str, str], str] = {}
            self.evidence: Dict[Tuple[str, str], Tuple[str, str]] = {}
            for path in sorted((run_dir / "status").glob("*.status.json.gz")):
                doc = path.name.split(".")[0]
                try:
                    payload = json.loads(gzip.open(path, "rt", encoding="utf-8").read())
                except Exception:
                    continue
                report = payload.get("review_report") or {}

                def add(obj):
                    if not isinstance(obj, dict) or not obj.get("id"):
                        return
                    issue_id = str(obj["id"])
                    rule = str(obj.get("rule_id") or obj.get("code") or "")
                    self.issues[(doc, issue_id)] = rule
                    self.real.add((doc, issue_id))
                    self.evidence[(doc, issue_id)] = _worse_evidence(
                        self.evidence.get((doc, issue_id)),
                        (
                            str(obj.get("evidence_status") or ""),
                            str((obj.get("evidence_verification") or {}).get("status") or "")
                            if isinstance(obj.get("evidence_verification"), dict) else "",
                        ),
                    )
                    location = obj.get("location")
                    if isinstance(location, dict) and location.get("pre_group_issue_id"):
                        group = str(location["pre_group_issue_id"])
                        self.groups[(doc, group)].add(issue_id)
                        self.group_of[(doc, issue_id)] = group
                    if isinstance(location, dict):
                        # 合并后未单独输出的成员只存在于 member_issue_ids /
                        # member_evidence；只有带成员证据的才登记为可新增的虚拟成员。
                        member_ids = location.get("member_issue_ids") or []
                        member_evidence = location.get("member_evidence") or []
                        for index, member in enumerate(member_ids):
                            if not member or (doc, str(member)) in self.real:
                                continue
                            evidence = member_evidence[index] if index < len(member_evidence) else {}
                            backed = isinstance(evidence, dict) and bool(
                                evidence.get("rule_id") or evidence.get("message") or evidence.get("evidence"))
                            if backed:
                                self.evidenced.add((doc, str(member)))
                                self.issues[(doc, str(member))] = str(evidence.get("rule_id") or "")
                            else:
                                self.virtual.add((doc, str(member)))
                                self.issues.setdefault((doc, str(member)), "")
                        for member in location.get("merged_issue_ids") or []:
                            key = (doc, str(member))
                            if key in self.real or key in self.evidenced:
                                continue
                            self.virtual.add(key)
                            self.issues.setdefault(key, "")

                for key in ("issues", "tips"):
                    for obj in report.get(key) or []:
                        add(obj)
                for obj in report.get("manual_review") or []:
                    add(obj)
                    add(obj.get("finding") if isinstance(obj, dict) else None)
                for bucket in (payload.get("result", {}).get("issues") or {}).values():
                    if isinstance(bucket, list):
                        for obj in bucket:
                            add(obj)

        def valid(self, doc: str, ref: str) -> bool:
            return (doc, ref) in self.issues or bool(self.groups.get((doc, ref)))

        def member_verifiable(self, doc: str, ref: str) -> bool:
            """成员是否有独立来源：真实问题或带成员证据的合并成员。"""
            return (doc, ref) in self.real or (doc, ref) in self.evidenced

        def rule(self, doc: str, ref: str):
            if (doc, ref) in self.issues:
                return self.issues[(doc, ref)] or None
            rules = {self.issues.get((doc, member)) for member in self.groups.get((doc, ref), set())}
            return next(iter(rules)) if len(rules) == 1 else None



    old_ref_index = _ReferenceIndex(baseline_run)
    new_ref_index = _ReferenceIndex(run_dir)

    def _ref_fingerprints(index: _ReferenceIndex, raw_index, doc: str, ref: str) -> list:
        members = index.groups.get((doc, ref)) or ({ref} if (doc, ref) in index.issues else set())
        return sorted(fp for fp in (_entity_fingerprint(raw_index, doc, member)
                                    for member in members) if fp)

    _group_pair_exceptions = {
        (str(item.get("sample_id")), str(item.get("rule")), str(item.get("old")),
         str(item.get("new")))
        for item in contract.get("reference_group_renumber_allowlist") or []
        if isinstance(item, dict)
    }
    _renumber_exceptions = {
        (str(item.get("sample_id")), str(item.get("rule")), str(item.get("old")),
         str(item.get("new")), tuple(item.get("member_fingerprints_old") or []),
         tuple(item.get("member_fingerprints_new") or []))
        for item in contract.get("reference_renumber_exceptions") or []
        if isinstance(item, dict)
    }

    def _reference_change_ok(doc: str, key: str, old_value: Any, new_value: Any, rule: str,
                             old_item_id: str = "", new_item_id: str = "",
                             self_group: str = "") -> bool:
        old_refs = _reference_values(old_value)
        new_refs = _reference_values(new_value)
        if key == "pre_group_issue_id":
            if len(old_refs) != 1 or len(new_refs) != 1:
                return False
            old_ref, new_ref = old_refs[0], new_refs[0]
            if not old_ref_index.valid(doc, old_ref) or not new_ref_index.valid(doc, new_ref):
                return False
            if old_ref_index.rule(doc, old_ref) not in (None, rule):
                return False
            if new_ref_index.rule(doc, new_ref) not in (None, rule):
                return False
            old_members = tuple(sorted(old_ref_index.groups.get((doc, old_ref)) or {old_ref}))
            new_members = tuple(sorted(new_ref_index.groups.get((doc, new_ref)) or {new_ref}))
            if (old_item_id and new_item_id and old_members == (old_item_id,)
                    and new_members == (new_item_id,)
                    and (doc, rule, old_ref, new_ref) in _group_pair_exceptions):
                return True
            old_fps = tuple(_ref_fingerprints(old_ref_index, old_raw_index, doc, old_ref))
            new_fps = tuple(_ref_fingerprints(new_ref_index, new_raw_index, doc, new_ref))
            if not old_fps or old_fps != new_fps:
                return False
            if old_ref_index.valid(doc, new_ref):
                return True
            # 新分组号未在基线出现：只接受冻结的“旧号→新号+成员实体指纹”精确例外。
            return (doc, rule, old_ref, new_ref, old_fps, new_fps) in _renumber_exceptions
        if key in {"member_issue_ids", "merged_issue_ids"}:
            if any(not old_ref_index.valid(doc, ref) for ref in old_refs):
                return False
            if any(not new_ref_index.valid(doc, ref) for ref in new_refs):
                return False
            # 新增成员必须有独立来源：真实问题或带 member_evidence 的合并成员。
            # 只在成员列表里写一个编号不能自动成为合法虚拟成员（J-05）。
            for ref in new_refs:
                if ref in old_refs:
                    continue
                if not new_ref_index.member_verifiable(doc, ref):
                    return False
                # 新增成员若本身归在另一分组，不能同时冒充本组成员。
                if key == "member_issue_ids" and self_group:
                    other = new_ref_index.group_of.get((doc, ref))
                    if other and str(other) != str(self_group):
                        return False
            # merged/member 组可跨规则合并（如 V33-005 与 V33-120 同差额），
            # 成员规则不强制等价；存在性与新增来源已逐条核验。
            return True
        if key == "related_findings":
            old_list = old_value if isinstance(old_value, list) else []
            new_list = new_value if isinstance(new_value, list) else []
            old_ids = sorted(str(item.get("id")) for item in old_list if isinstance(item, dict) and item.get("id"))
            new_ids = sorted(str(item.get("id")) for item in new_list if isinstance(item, dict) and item.get("id"))
            return (all(old_ref_index.valid(doc, ref) for ref in old_ids)
                    and all(new_ref_index.valid(doc, ref) for ref in new_ids))
        return True

    def _reference_values(value: Any) -> List[str]:
        if isinstance(value, str):
            return [value]
        if isinstance(value, list):
            return [str(item) for item in value if str(item)]
        return []

    _REF_STRIP = {"pre_group_issue_id", "member_issue_ids", "merged_issue_ids", "related_findings",
                  "id", "analysis_revision", "created_at", "updated_at", "severity"}

    def _strip_references(value: Any) -> Any:
        if isinstance(value, dict):
            cleaned = {}
            for key, item in value.items():
                if key in _REF_STRIP:
                    continue
                if key in {"message", "title"} and isinstance(item, str):
                    cleaned[key] = eval_lib.norm(item) if key == "message" else eval_lib._ws(item)
                else:
                    cleaned[key] = _strip_references(item)
            return cleaned
        if isinstance(value, list):
            return [_strip_references(v) for v in value]
        return value

    def _entity_fingerprint(index, sid: str, ident: str) -> Optional[str]:
        entries = index.get((sid, "finding", ident)) or []
        entity = entries[0] if entries else {}
        if not entity:
            entity = index.get((sid, ident)) or {}
        if not entity:
            return None
        return json.dumps(_strip_references(eval_lib._round_floats(entity)), ensure_ascii=False,
                          sort_keys=True, separators=(",", ":"), default=str)

    if getattr(args, "emit_reference_exceptions", None):
        entries = []
        for old, new, _how in pairs:
            raw_old = eval_lib.raw_item(old_raw_index, old)
            raw_new = eval_lib.raw_item(new_raw_index, new)
            if raw_old is None or raw_new is None:
                continue
            old_view = eval_lib.reference_view(raw_old)
            new_view = eval_lib.reference_view(raw_new)
            for path, value in new_view.items():
                key = (path.rsplit(".", 1)[-1] if "." in path
                       else path.rstrip("/").split("/")[-1].split("[")[0])
                if key != "pre_group_issue_id":
                    continue
                old_value = old_view.get(path)
                old_refs, new_refs = _reference_values(old_value), _reference_values(value)
                if len(old_refs) != 1 or len(new_refs) != 1:
                    continue
                old_ref, new_ref = old_refs[0], new_refs[0]
                if old_ref == new_ref:
                    continue
                if not old_ref_index.valid(old["doc"], old_ref) or not new_ref_index.valid(old["doc"], new_ref):
                    continue
                old_fps = tuple(_ref_fingerprints(old_ref_index, old_raw_index, old["doc"], old_ref))
                new_fps = tuple(_ref_fingerprints(new_ref_index, new_raw_index, old["doc"], new_ref))
                if not old_fps or old_fps != new_fps:
                    continue
                if old_ref_index.valid(old["doc"], new_ref):
                    continue
                entries.append({"sample_id": old["doc"], "rule": str(old.get("rule") or ""),
                                "old": old_ref, "new": new_ref,
                                "member_fingerprints_old": list(old_fps),
                                "member_fingerprints_new": list(new_fps)})
        group_entries = []
        for old, new, _how in pairs:
            raw_old = eval_lib.raw_item(old_raw_index, old)
            raw_new = eval_lib.raw_item(new_raw_index, new)
            if raw_old is None or raw_new is None:
                continue
            old_pre = (raw_old.get("location") or {}).get("pre_group_issue_id")
            new_pre = (raw_new.get("location") or {}).get("pre_group_issue_id")
            if not old_pre or not new_pre or str(old_pre) == str(new_pre):
                continue
            old_members = old_ref_index.groups.get((old["doc"], str(old_pre))) or set()
            new_members = new_ref_index.groups.get((old["doc"], str(new_pre))) or set()
            if old_members == {str(old.get("raw_id"))} and new_members == {str(new.get("raw_id"))}:
                group_entries.append({"sample_id": old["doc"], "rule": str(old.get("rule") or ""),
                                      "old": str(old_pre), "new": str(new_pre)})
        Path(args.emit_reference_exceptions).write_text(
            json.dumps({"contract_id": "reference-renumber-exceptions", "entries": entries,
                        "group_entries": group_entries},
                       ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(json.dumps({"emitted": len(entries), "out": args.emit_reference_exceptions},
                         ensure_ascii=False))
        return 0

    for old, new, how in pairs:
        if item_key(old) in target_fp_keys:
            continue
        raw_old = eval_lib.raw_item(old_raw_index, old)
        raw_new = eval_lib.raw_item(new_raw_index, new)
        if raw_old is None or raw_new is None:
            raw_missing.append({"sample_id": old["doc"], "old_raw_id": old.get("raw_id"),
                                "new_raw_id": new.get("raw_id"),
                                "old_found": raw_old is not None, "new_found": raw_new is not None})
            continue
        a, b = eval_lib.canonical_raw(raw_old), eval_lib.canonical_raw(raw_new)
        if a != b:
            paths = sorted(set(eval_lib.diff_paths(a, b)))
            record = {"sample_id": old["doc"], "old_raw_id": old.get("raw_id"),
                      "new_raw_id": new.get("raw_id"), "paths": paths[:30]}
            unexplained, renumbered = [], []
            for path in paths:
                key = path.rstrip("/").split("/")[-1].split("[")[0]
                rule_paths = (contract.get("raw_path_allowlist_by_rule") or {}).get(
                    str((raw_new or {}).get("rule_id") or "")) or []
                if (key == "pre_group_issue_id" and rule_paths
                        and path.startswith(tuple(rule_paths))
                        and eval_lib.get_path(a, path) and not eval_lib.get_path(b, path)):
                    # 独立问题不再写分组展示号：仅允许“旧有→新无”的删除，
                    # 不允许新增/改写分组引用。
                    continue
                if key in reference_keys:
                    # 引用必须独立核验真实来源/成员归属/实体等价；不接受同前缀顺移，
                    # 也不允许 raw_path_allowlist 整字段放行。
                    if not _reference_change_ok(old["doc"], key, eval_lib.get_path(a, path),
                                                eval_lib.get_path(b, path), str(old.get("rule") or ""),
                                                str(old.get("raw_id") or ""), str(new.get("raw_id") or ""),
                                                str((raw_new.get("location") or {}).get("pre_group_issue_id") or "")):
                        unexplained.append(path)
                    else:
                        renumbered.append(path)
                    continue
                if allow_prefixes and path.startswith(allow_prefixes):
                    continue
                rule_paths = (contract.get("raw_path_allowlist_by_rule") or {}).get(
                    str((raw_new or {}).get("rule_id") or "")) or []
                if rule_paths and path.startswith(tuple(rule_paths)):
                    continue
                if allow_prefixes and path.startswith(allow_prefixes):
                    continue
                allowed_rule_fields = set((contract.get("allowed_fingerprint_fields_by_rule") or {}).get(
                    str((raw_new or {}).get("rule_id") or ""), []))
                if path == "/severity" and "sev" in allowed_rule_fields:
                    continue
                unexplained.append(path)
            record["renumbered_paths"] = renumbered
            record["unexplained_paths"] = unexplained
            if unexplained:
                raw_drift.append(record)
            else:
                raw_expected.append(record)

    # ---- TP 保持 ----
    def finals(rows_items):
        result = Counter()
        for row in rows_items:
            if row["tier"] in {"F", "P", "O", "R"}:
                result[row.get("final")] += 1
        return result
    baseline_finals = Counter(row.get("final") for row in baseline_rows.values())
    # 直接用基线 final_rows 与当前重算结果对比（当前裁决按对齐继承）
    current_finals = Counter()
    for item in items:
        if item["tier"] in {"F", "P"}:
            old = new_to_old.get(item["id"])
            key = f"{item['doc']}|{old.get('raw_id')}" if old else None
            row = baseline_rows.get(key) if key else None
            current_finals[(row or {}).get("final")] += 1
        elif item["tier"] in {"O", "R"}:
            if item["tier"] == "O":
                current_finals[gm.match_o(item, docs[item["doc"]]).get("status")] += 1
            else:
                # R 层按参考层裁决：TP -> REF_HIT，其余 -> REF_OTHER（与 r2 finalize 一致）。
                status = gm.match_item(item, docs[item["doc"]]).get("status")
                current_finals["REF_HIT" if status == "TP" else "REF_OTHER"] += 1
    tp_before = sum(v for k, v in baseline_finals.items() if k in {"TP", "HIT"})
    tp_after = sum(v for k, v in current_finals.items() if k in {"TP", "HIT"})

    # 严格指标：F 层正式 TP/FP、P 候选、ADJ_TRUE 分栏；173 这类 TP+HIT
    # 只能作保持计数，不能当正式 F 层 TP。
    strict_formal_tp = 0
    strict_formal_fp = 0
    strict_p_candidates = 0
    for item in items:
        if item.get("tier") == "P":
            strict_p_candidates += 1
        if item.get("tier") != "F":
            continue
        old = new_to_old.get(item["id"])
        if old:
            row = baseline_rows.get(f"{item['doc']}|{old.get('raw_id')}")
            if row and row.get("final") == "TP":
                strict_formal_tp += 1
            elif row and row.get("final") in {"FP_GOLD", "ADJ_FALSE"}:
                strict_formal_fp += 1
        else:
            status = gm.match_item(item, docs[item["doc"]]).get("status")
            if status == "TP" and _evidence_qualified(item):
                strict_formal_tp += 1
            elif status in {"FP_G", "FP"}:
                strict_formal_fp += 1
    strict_tp_before = sum(1 for row in baseline_rows.values()
                           if row.get("tier") == "F" and row.get("final") == "TP")
    strict_metrics = {
        "formal_tp": strict_formal_tp,
        "formal_tp_before": strict_tp_before,
        "formal_fp": strict_formal_fp,
        "p_candidates": strict_p_candidates,
        "tp_plus_hit_keep": tp_after,
        "adj_true": sum(1 for row in baseline_rows.values() if row.get("final") == "ADJ_TRUE"),
        "strict_fn": missing_additions,
        "partial_targets": list(partial_targets.values()),
    }

    checks = []

    def add(check_id, title, ok, details=()):
        checks.append({"id": check_id, "title": title,
                       "verdict": "PASS" if ok else "FAIL",
                       "count": len(details), "details": list(details)[:50]})

    add("B1", "两次采集样本集合一致", set(i["doc"] for i in old_items) == set(i["doc"] for i in items))
    add("B2", "条目对齐无歧义/重复签名冲突", not review, [str(r)[:300] for r in review])
    add("B3", "FP 目标条目全部移除", not missing_removals,
        [f"{m['sample_id']}|{m['system_item_id']}" for m in missing_removals])
    add("B4", "无目标外条目移除", not removed_unexpected,
        [f"{r['sample_id']}|{r['raw_id']}" for r in removed_unexpected])
    add("B5", "MISS 目标全部出现 F 层且证据合格的出现点覆盖", not missing_additions, missing_additions)
    add("B6", "无目标外新增正式条目", not unexpected_additions,
        [f"{r['sample_id']}|{r['raw_id']}|{r['rule']}" for r in unexpected_additions])
    add("B7", "非目标条目全文指纹无漂移", not fingerprint_drift,
        [f"{r['sample_id']}|{r['old_raw_id']}:{','.join(r['fields'])}" for r in fingerprint_drift])
    add("B8", "非目标条目原始 status 指纹无漂移", not raw_drift,
        [f"{r['sample_id']}|{r['old_raw_id']}" for r in raw_drift])
    add("B9", "原始 status 均可定位", not raw_missing, [str(r)[:200] for r in raw_missing])
    add("B10", "正式 F 层 TP 数量不回退（P/HIT 单列）",
        strict_formal_tp >= strict_tp_before,
        [f"strict {strict_tp_before}->{strict_formal_tp}; mixed keep {tp_before}->{tp_after}"])
    add("B11", "引用/计数例外无双向冲突", not exception_conflicts, exception_conflicts)
    add("B12", "裁决继承无漂移", not decision_drift, [str(r) for r in decision_drift[:10]])

    # ---- todo/队列计数完整指纹（计数值、方向、移除数被篡改都要暴露）----
    todo_allow = tuple(contract.get("allowed_todo_paths") or ())
    todo_drift, todo_expected = [], []
    for sid in sorted({i["doc"] for i in old_items}):
        old_todo = eval_lib.read_gz(baseline_run / "todo" / f"{sid}.todo.json.gz")
        new_todo = eval_lib.read_gz(run_dir / "todo" / f"{sid}.todo.json.gz")
        if eval_lib.canonical_raw(old_todo) == eval_lib.canonical_raw(new_todo):
            continue
        paths = sorted(set(eval_lib.diff_paths(old_todo, new_todo)))
        if todo_allow and all(path.startswith(todo_allow) for path in paths):
            todo_expected.append({"sample_id": sid, "paths": paths})
        else:
            todo_drift.append(sid)
    add("B13", "待办/队列计数指纹无漂移", not todo_drift, todo_drift)

    # ---- 跨表示一致性与引用 ID 边界（F5 口径，第二批不继承任何引用例外）----
    entity_conflicts = []
    for old, new, _how in pairs:
        raw_new = eval_lib.raw_item(new_raw_index, new)
        if raw_new is None:
            continue
        raw_old = eval_lib.raw_item(old_raw_index, old)
        if not eval_lib.membership_consistent(raw_new):
            entity_conflicts.append(f"{old['doc']}|{new.get('raw_id')}:成员引用与 related_findings 不一致")
        new_view = eval_lib.reference_view(raw_new)
        old_view = eval_lib.reference_view(raw_old) if raw_old is not None else {}
        self_group = str((raw_new.get("location") or {}).get("pre_group_issue_id") or "")
        for path, value in new_view.items():
            key = (path.rsplit(".", 1)[-1] if "." in path
                   else path.rstrip("/").split("/")[-1].split("[")[0])
            if key not in reference_keys:
                continue
            if isinstance(value, list):
                # 成员集合按集合核验，不能按展示顺序逐位比较。
                if not _reference_change_ok(old["doc"], key, old_view.get(path), value,
                                            str(old.get("rule") or ""),
                                            str(old.get("raw_id") or ""), str(new.get("raw_id") or ""),
                                            self_group):
                    entity_conflicts.append(
                        f"{old['doc']}|{new.get('raw_id')}:{path} 成员集合无法证明同源")
                continue
            old_value = old_view.get(path)
            # 引用（含原样保留）都必须落到真实实体，且成员集合等价；
            # 未知 ID、跨规则、成员冲突、同前缀顺移一律拒收。
            if not _reference_change_ok(old["doc"], key, old_value, value, str(old.get("rule") or ""),
                                        str(old.get("raw_id") or ""), str(new.get("raw_id") or ""),
                                        self_group):
                entity_conflicts.append(
                    f"{old['doc']}|{new.get('raw_id')}:{path}={value} 引用无法证明同源")
    add("B14", "跨表示引用一致且新引用不越界", not entity_conflicts, entity_conflicts[:50])

    # ---- 证据状态不回退（防止把正式条目的证据降级藏在允许字段里）----
    evidence_conflicts = []
    for old, new, _how in pairs:
        old_state = old_ref_index.evidence.get((old["doc"], str(old.get("raw_id") or "")))
        new_state = new_ref_index.evidence.get((old["doc"], str(new.get("raw_id") or "")))
        if not old_state or not new_state:
            continue
        if old_state[0] == "complete" and new_state[0] != "complete":
            evidence_conflicts.append(
                f"{old['doc']}|{new.get('raw_id')}:证据状态 {old_state[0]}->{new_state[0] or '缺失'}")
        elif old_state[1] == "verified" and new_state[1] == "failed":
            evidence_conflicts.append(
                f"{old['doc']}|{new.get('raw_id')}:原文核验 verified->failed")
    add("B15", "非目标条目证据状态不回退", not evidence_conflicts, evidence_conflicts[:50])

    overall = "FAIL" if any(c["verdict"] == "FAIL" for c in checks) else "PASS"
    summary = {
        "mode": args.mode, "stage": args.stage, "run": str(run_dir),
        "baseline_run": str(baseline_run), "baseline_analysis": str(baseline_analysis),
        "targets": {k: len(v) for k, v in targets.items()},
        "items_old": len(old_items), "items_new": len(items),
        "pairs": len(pairs), "removed": len(old_only), "added": len(new_only),
        "target_removed": len(removed_target_hits), "target_missing": missing_removals,
        "unexpected_removed": removed_unexpected,
        "satisfied_miss_targets": {k: v for k, v in satisfied.items()},
        "partial_miss_targets": partial_targets,
        "occurrence_coverage": occurrence_coverage,
        "strict_metrics": strict_metrics,
        "missing_additions": missing_additions, "unexpected_additions": unexpected_additions,
        "expected_additions": expected_additions,
        "fingerprint_drift": fingerprint_drift,
        "fingerprint_expected": expected_fingerprint_drift,
        "todo_expected": todo_expected,
        "raw_fingerprint_drift": raw_drift,
        "raw_fingerprint_expected": raw_expected,
        "raw_missing": raw_missing, "decision_drift": decision_drift,
        "review_required": review, "resolved_duplicates": resolved_duplicates,
        "tp_before": tp_before, "tp_after": tp_after,
        "reference_exceptions_sha256": exceptions_sha,
        "exception_conflicts": exception_conflicts,
        "evidence_conflicts": evidence_conflicts,
        "checks": checks, "overall": overall,
    }
    (out / "batch2_gold_compare.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=1, default=dict) + "\n", encoding="utf-8")
    with (out / "targets.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["kind", "target_id", "sample_id", "outcome", "evidence"])
        for target in targets["fp_removals"]:
            hit = any(r["sample_id"] == target["sample_id"] and r["raw_id"] == target["system_item_id"]
                      for r in removed_target_hits)
            writer.writerow(["fp_removal", target["target_id"], target["sample_id"],
                             "removed" if hit else "still_present", target["system_item_id"]])
        for target in miss_targets:
            ids = satisfied.get(target["target_id"], [])
            writer.writerow(["miss_addition", target["target_id"], target["sample_id"],
                             "satisfied" if ids else "missing", ";".join(ids)])
    print(json.dumps({"overall": overall, "stage": args.stage, "mode": args.mode,
                      "checks": {c["id"]: c["verdict"] for c in checks},
                      "target_removed": len(removed_target_hits),
                      "target_missing": len(missing_removals),
                      "missing_additions": len(missing_additions),
                      "unexpected_additions": len(unexpected_additions),
                      "unexpected_removed": len(removed_unexpected),
                      "tp": [tp_before, tp_after]}, ensure_ascii=False, indent=1))
    return 0 if overall == "PASS" else 1


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mode", choices=["self", "baseline", "stage"], required=True)
    parser.add_argument("--stage", default="S1")
    parser.add_argument("--run", required=True)
    parser.add_argument("--baseline-run", required=True)
    parser.add_argument("--baseline-analysis", required=True)
    parser.add_argument("--targets", required=True)
    parser.add_argument("--contract", default=None)
    parser.add_argument("--reference-exceptions", default=None,
                        help="第二批自己的冻结例外清单；默认不继承第一批")
    parser.add_argument("--match-script", default=str(DEFAULT_MATCH))
    parser.add_argument("--out", required=True)
    parser.add_argument("--emit-reference-exceptions", default=None,
                        help="仅生成“旧组→新组+成员集合”精确例外清单，供冻结后写入契约")
    return run(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
