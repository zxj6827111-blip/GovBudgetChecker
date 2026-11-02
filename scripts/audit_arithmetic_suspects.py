"""勾稽反查：用原件自带的恒等式当免费探测器，把"可能放错格子"的表排成短名单。

原理：合计=分项之和这类式子在原件里天然成立。系统把某个金额放错格子，式子就会算不平。
所以不需要人工真值，也能把 100 多万个金额格压成一张按嫌疑排序的短名单。

分工写死在输出里：
- already_flagged：不平 + 有独立结构证据（表头缺失/几何竞争等）→ 系统已经把它降级为待复核；
- blind_spot：不平但没有结构证据 → 这些格子照常进了规则。它要么是原件真错，要么是系统静默读错，
  本脚本分不开，只负责点名（已核实的 4 份原件真错材料会落在这里，属预期）。

盲区（必须如实说明）：错得刚好把式子带平、或某数字只出现一次没有分项可对，本方法查不出。
"""
from __future__ import annotations

import argparse
import gzip
import json
import re
import sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DEFAULT_CORPUS = Path.home() / "Desktop/决算测试上海市"
DEFAULT_INTAKE = ROOT / "outputs/intake_remediation_20261024/batch2_final/intake.jsonl.gz"
DEFAULT_OUT = ROOT / "outputs/intake_remediation_20261024/arithmetic_suspects"

# 第一批已渲染核实为“原件确有系统性勾稽错误”的材料，用作点名反例。
KNOWN_ORIGINAL_ERRORS = (
    "华东政法大学2025年度决算",
    "龙华烈士陵园2025",
    "航宇科普中心2023",
    "上海应用技术大学本级2022",
)


def filename_org(path: Path) -> str:
    name = re.sub(r"\.pdf$", "", path.name, flags=re.I)
    name = re.sub(r"^\d{4}年度?", "", name)
    return re.split(r"年度决算|年度预算|决算|预算", name)[0].strip() or name


def load_materials(intake: Path, corpus: Path) -> list[dict[str, Any]]:
    seen: set[str] = set()
    tasks = []
    with gzip.open(intake, "rt", encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            sha = str(row.get("sha256") or "")
            if not sha or sha in seen:
                continue
            seen.add(sha)
            path = corpus / str(row.get("file") or "")
            if not path.exists():
                continue
            tasks.append({
                "path": str(path), "rel": str(row.get("file")), "sha256": sha,
                "year": row.get("report_year"), "report_kind": row.get("report_kind") or "final",
                "doc_type": row.get("doc_type") or "dept_final",
            })
    return tasks


def _instance_facts(facts: list) -> dict[str, list]:
    groups: dict[str, list] = defaultdict(list)
    for fact in facts:
        if getattr(fact, "instance_uid", None):
            groups[str(fact.instance_uid)].append(fact)
    return groups


def _swap_like_signatures(facts: list, anomalies: list[dict]) -> list[dict]:
    """差额恰好等于同一行另一列的值 → 更像列错位而不是原件笔误。"""
    rows: dict[Any, dict[str, Decimal]] = defaultdict(dict)
    for fact in facts:
        value = getattr(fact, "normalized_value", None)
        if value is not None:
            rows[(fact.row_order, fact.budget_stage, fact.period)][str(fact.indicator)] = value
    hits = []
    for anomaly in anomalies:
        indicators = set(anomaly.get("indicators") or [])
        for example in anomaly.get("examples") or []:
            key = (example.get("row_order"), example.get("budget_stage"), None)
            row_values = {k: v for k, v in rows.get(key, {}).items() if k not in indicators}
            try:
                diff = abs(Decimal(str(example.get("difference_wan"))))
            except Exception:
                continue
            for other, value in row_values.items():
                if abs(abs(value) - diff) <= Decimal("0.01"):
                    hits.append({"identity": anomaly.get("identity"), "row_order": example.get("row_order"),
                                 "difference_wan": str(diff), "matches_other_column": other})
                    break
    return hits


def audit_one(task: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"file": task["rel"], "sha256": task["sha256"], "year": task["year"]}
    try:
        from src.engine.fact_context import build_fact_context_from_artifact
        from src.services.fact_column_arithmetic import (
            arithmetic_anomalies, independent_structure_evidence)
        from src.services.pdf_parser import PDFParser

        artifact = PDFParser(None).build_parse_artifact(task["path"])
        org = filename_org(Path(task["path"]))
        context = build_fact_context_from_artifact(
            artifact, org_name=org, fiscal_year=task["year"], report_kind=task["report_kind"],
            doc_type=task["doc_type"], require_complete_identity=True)
        facts = list(context.facts) if context is not None else []
        # FiscalFact 不带页码，页号从逻辑表实例取，避免凭空造位置。
        instance_pages = {str(item.instance_uid): int(item.page_start)
                          for item in getattr(artifact, "logical_tables", []) or []}
        anomalies = arithmetic_anomalies(facts)
        out["facts"] = len(facts)
        out["admitted"] = sum(1 for f in facts if f.recognition_status not in {"ambiguous", "low_confidence", "missing"})
        instances = _instance_facts(facts)
        tables = []
        for uid, group in instances.items():
            found = anomalies.get(uid)
            if not found:
                continue
            structure = independent_structure_evidence(group)
            failed = sum(int(item.get("failed_rows") or 0) for item in found)
            checked = sum(int(item.get("checked_rows") or 0) for item in found)
            differences = [value for item in found for example in item.get("examples") or []
                           if (value := _decimal(example.get("difference_wan"))) is not None]
            tables.append({
                "instance_uid": uid,
                "table_code": group[0].table_code,
                "page": instance_pages.get(uid),
                "identities": [item.get("identity") for item in found],
                "checked_rows": checked, "failed_rows": failed,
                "max_difference_wan": str(max(differences)) if differences else None,
                "bucket": "already_flagged" if structure else "blind_spot",
                "structure_reasons": sorted({item["reason"] for item in structure}),
                "swap_like": _swap_like_signatures(group, found)[:5],
            })
        out["tables"] = tables
    except Exception as exc:  # noqa: BLE001 - 探针脚本要如实记录失败而不是静默跳过
        out["error"] = repr(exc)[:200]
    return out


def _decimal(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value)) if value is not None else None
    except Exception:
        return None


def self_check_detector() -> dict[str, Any]:
    """灵敏度自检：平的表必须 0 命中；改一个数字必须点名；整列错位必须逐行点名。"""
    from src.schemas.parse_artifact import FiscalFact
    from src.services.fact_column_arithmetic import arithmetic_anomalies

    def fact(row: int, indicator: str, value: str) -> FiscalFact:
        return FiscalFact(fact_uid=f"t:{row}:{indicator}", indicator=indicator, raw_value=value, unit="万元",
                          normalized_value=Decimal(value), table_code="FIN_02_income", instance_uid="t1",
                          row_order=row, budget_stage="决算", period="本年")

    balanced = [fact(0, "total_actual", "100.00"), fact(0, "fiscal_allocation", "60.00"),
                fact(0, "business_income", "30.00"), fact(0, "other_income", "10.00"),
                fact(0, "superior_subsidy_income", "0.00"), fact(0, "operational_income", "0.00"),
                fact(0, "subsidiary_income", "0.00")]
    clean = not arithmetic_anomalies(balanced)

    # 一格改 1 万元：远超舍入包络（7 个操作数 × 0.005 = 0.035），必须点名且只点名这一行。
    nudged = [replace(item, normalized_value=Decimal("11.00"), raw_value="11.00")
              if item.indicator == "other_income" else item for item in balanced]
    one_off = arithmetic_anomalies(nudged)
    catches_single = bool(one_off) and one_off["t1"][0]["failed_rows"] == 1

    # 单列搬走 20 万元且无补偿：合计 100 ≠ 分项 120，必须点名。
    shifted = [replace(item, normalized_value=Decimal("80.00"), raw_value="80.00")
               if item.indicator == "fiscal_allocation" else item for item in balanced]
    catches_shift = bool(arithmetic_anomalies(shifted))

    # 已测盲区（写进报告，不粉饰）：两列互相搬、合计仍等于分项之和时，行内恒等式看不见。
    compensating = [replace(item, normalized_value=Decimal("90.00"), raw_value="90.00")
                    if item.indicator == "fiscal_allocation" else
                    replace(item, normalized_value=Decimal("0.00"), raw_value="0.00")
                    if item.indicator == "business_income" else item for item in balanced]
    blind_spot_confirmed = not arithmetic_anomalies(compensating)

    # 同样的错位出现在两行 → 系统性错位，failed_rows 应为 2。
    stacked = shifted + [replace(item, fact_uid=f"r1:{item.indicator}", row_order=1) for item in shifted]
    multi = arithmetic_anomalies(stacked)
    catches_multirow = bool(multi) and multi["t1"][0]["failed_rows"] == 2
    return {"balanced_table_clean": clean, "catches_single_cell_change": catches_single,
            "catches_uncompensated_shift": catches_shift, "compensating_swap_is_invisible": blind_spot_confirmed,
            "flags_every_unbalanced_row": catches_multirow,
            "all_passed": all([clean, catches_single, catches_shift, blind_spot_confirmed, catches_multirow])}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--intake", type=Path, default=DEFAULT_INTAKE)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--limit", type=int, default=0, help="0 = 全量；否则只跑前 N 份")
    parser.add_argument("--jobs", type=int, default=6)
    parser.add_argument("--every", type=int, default=1, help="按顺序每 N 份取 1 份（分层抽样用）")
    args = parser.parse_args()

    checks = self_check_detector()
    print(json.dumps({"detector_self_check": checks}, ensure_ascii=False))
    if not checks["all_passed"]:
        raise SystemExit("detector self-check failed; refusing to report suspects")

    tasks = load_materials(args.intake, args.corpus)
    if args.every > 1:
        tasks = tasks[::args.every]
    if args.limit:
        tasks = tasks[:args.limit]
    args.out.mkdir(parents=True, exist_ok=True)

    records = []
    with ProcessPoolExecutor(max_workers=args.jobs) as pool:
        for index, record in enumerate(pool.map(audit_one, tasks, chunksize=4), start=1):
            records.append(record)
            if index % 25 == 0:
                print(json.dumps({"done": index, "of": len(tasks)}, ensure_ascii=False), flush=True)

    flagged = [table for r in records for table in r.get("tables", []) if table["bucket"] == "already_flagged"]
    blind = [table for r in records for table in r.get("tables", []) if table["bucket"] == "blind_spot"]
    known_hits = sorted({r["file"] for r in records
                         if any(t["bucket"] == "blind_spot" for t in r.get("tables", []))
                         and any(key in r["file"] for key in KNOWN_ORIGINAL_ERRORS)})
    by_identity = Counter(identity for table in blind for identity in table["identities"])
    by_table_code = Counter(table["table_code"] for table in blind)
    swap_like = [table for table in blind if table["swap_like"]]
    summary = {
        "files_scanned": len(records),
        "files_failed": sum(1 for r in records if r.get("error")),
        "files_with_anomaly": sum(1 for r in records if r.get("tables")),
        "tables_already_flagged": len(flagged),
        "tables_blind_spot": len(blind),
        "blind_spot_with_swap_signature": len(swap_like),
        "known_original_error_files_named": known_hits,
        "blind_spot_by_identity": dict(by_identity.most_common()),
        "blind_spot_by_table_code": dict(by_table_code.most_common()),
        "detector_self_check": checks,
        "limits": [
            "点名不等于判错：blind_spot 里既有原件真错，也可能是系统静默读错，本脚本分不开。",
            "错得刚好把恒等式带平、或该数字没有可加的分项时，本方法查不出（自检里 compensating_swap_is_invisible 就是这条盲区的实测断言）。",
            "只覆盖 ROW_IDENTITIES 登记的行内恒等式；跨表勾稽与文字-表格一致性不在本探针范围。",
            "行内恒等式要求该行合计与全部分项都有值才参与核对，缺格的行不计入 checked_rows。",
        ],
    }
    (args.out / "results.json").write_text(
        json.dumps({"records": records, "summary": summary}, ensure_ascii=False, indent=2), encoding="utf-8")
    ranked = sorted(blind, key=lambda t: (-len(t["swap_like"]), -t["failed_rows"], -(float(t["max_difference_wan"] or 0))))
    with (args.out / "suspects.csv").open("w", encoding="utf-8") as handle:
        handle.write("file,table_code,instance_uid,checked_rows,failed_rows,max_difference_wan,swap_like,identities\n")
        for table in ranked:
            owner = next(r["file"] for r in records if any(t is table for t in r.get("tables", [])))
            handle.write(f"\"{owner}\",{table['table_code']},{table['instance_uid']},{table['checked_rows']},"
                         f"{table['failed_rows']},{table['max_difference_wan']},{len(table['swap_like'])},"
                         f"\"{';'.join(table['identities'])}\"\n")
    (args.out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in summary.items() if key != "blind_spot_by_identity"},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
