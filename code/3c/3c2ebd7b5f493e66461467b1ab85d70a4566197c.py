#!/usr/bin/env python3
"""第二批比较器独立变异负控（B2）。

在冻结采集的隔离副本上逐项变异，验证比较器会按对应原因拒收，并保留
正常对照必须 PASS，避免"所有输入都失败"的假通过。

变异：
1. existing_wrong_pre_group  引用改成同文档同规则下真实存在但不同实体的组号
2. cross_document_member     成员引用另一个文档的真实条目
3. bidirectional_conflict    新增成员本身归在另一个分组
4. evidence_degraded         目标条目证据降级，不得再算正式命中
5. partial_anchor            只覆盖双出现点目标中的一个
6. same_page_unrelated       同页但正文无关的条目不得满足目标
7. virtual_member_added      只在成员列表里写一个不存在的编号

用法：
    python3 tools/comparator_independent_probes.py --out <dir>
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import shutil
import subprocess
from pathlib import Path

REPO = Path("/Volumes/workSSD/software development/GovBudgetChecker")
EVIDENCE = Path("/Volumes/workSSD/software development/gbc-worktrees/joint-execution-20261103-evidence")
TOOL = Path("/Volumes/workSSD/software development/gbc-worktrees/"
            "joint-exp-batch2-tools-20261103/scripts/batch2_gold_compare.py")
PYTHON = REPO / ".venv/bin/python"
BASE = EVIDENCE / "s4b/gold/final"
BASELINE_RUN = EVIDENCE / "s5b/gold/run2"
BASELINE_ANALYSIS = EVIDENCE / "s5b/gold/run2_analysis"
TARGETS = EVIDENCE / "s4b/targets_residual.csv"
CONTRACT = EVIDENCE / "s4b/contract_residual.json"
TARGET_SAMPLE = "GS-2022-04"
TARGET_ID = "V33-227-9"
PARTIAL_SAMPLE = "GS-2024-02"
PARTIAL_ID = "V33-227-15"


def read_gz(path: Path):
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return json.load(handle)


def write_gz(path: Path, payload) -> None:
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False)


def all_items(status):
    report = status.get("review_report") or {}
    for bucket in ("issues", "tips"):
        for item in report.get(bucket) or []:
            if isinstance(item, dict) and item.get("id"):
                yield bucket, item
    for record in report.get("manual_review") or []:
        finding = record.get("finding") if isinstance(record, dict) else None
        if isinstance(finding, dict) and finding.get("id"):
            yield "manual_review", finding


def find_item(status, ident):
    for _bucket, item in all_items(status):
        if str(item.get("id")) == ident:
            return item
    raise AssertionError(f"找不到条目 {ident}")


def item_pages(item):
    location = item.get("location") or {}
    pages = set()
    if isinstance(location.get("page"), int):
        pages.add(location["page"])
    for page in location.get("pages") or []:
        if isinstance(page, int):
            pages.add(page)
    return pages


class Mutant:
    def __init__(self) -> None:
        self.root = Path(
            subprocess.run(
                ["mktemp", "-d", "-t", "cmp-probe"],
                capture_output=True, text=True, check=True,
            ).stdout.strip()
        )
        self.run = self.root / "run"
        self.out = self.root / "out"
        shutil.copytree(BASE / "status", self.run / "status")
        shutil.copytree(BASE / "todo", self.run / "todo")

    def path(self, sid: str) -> Path:
        return self.run / "status" / f"{sid}.status.json.gz"

    def compare(self, tag: str, targets: Path = TARGETS, contract: Path = CONTRACT):
        proc = subprocess.run(
            [str(PYTHON), str(TOOL), "--mode", "stage", "--stage", "S4",
             "--run", str(self.run), "--baseline-run", str(BASELINE_RUN),
             "--baseline-analysis", str(BASELINE_ANALYSIS),
             "--targets", str(targets), "--contract", str(contract),
             "--out", str(self.out)],
            capture_output=True, text=True,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        report = json.loads((self.out / "batch2_gold_compare.json").read_text(encoding="utf-8"))
        (self.out / "stdout.log").write_text(proc.stdout, encoding="utf-8")
        (self.out / "stderr.log").write_text(proc.stderr, encoding="utf-8")
        return proc.returncode, report

    def cleanup(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)


def _first_pre_group(status):
    for _bucket, item in all_items(status):
        location = item.get("location") or {}
        if location.get("pre_group_issue_id"):
            return item, str(location["pre_group_issue_id"])
    return None, None


def _all_group_ids(status):
    values = set()
    for _bucket, item in all_items(status):
        location = item.get("location") or {}
        if location.get("pre_group_issue_id"):
            values.add(str(location["pre_group_issue_id"]))
    return values


def mutate_existing_wrong_pre_group(m: Mutant) -> dict:
    path = m.path("GS-2022-01")
    status = read_gz(path)
    item, current = _first_pre_group(status)
    assert item is not None, "样本缺少分组引用"
    candidates = sorted(gid for gid in _all_group_ids(status)
                        if gid != current and gid.split("-")[0] == current.split("-")[0])
    assert candidates, "样本缺少同规则其他分组"
    item["location"]["pre_group_issue_id"] = candidates[0]
    write_gz(path, status)
    return {"expected_check": "B14", "expected_reason": "引用", "forbid_satisfied": TARGET_ID}


def mutate_cross_document_member(m: Mutant) -> dict:
    path = m.path("GS-2022-01")
    status = read_gz(path)
    other = read_gz(m.path("GS-2022-02"))
    foreign = next(str(i.get("id")) for _b, i in all_items(other) if i.get("id"))
    target = next(i for _b, i in all_items(status)
                  if isinstance(i.get("location"), dict) and i["location"].get("pre_group_issue_id"))
    target["location"].setdefault("member_issue_ids", []).append(foreign)
    write_gz(path, status)
    return {"expected_check": "B14", "expected_reason": "成员", "forbid_satisfied": TARGET_ID}


def mutate_bidirectional_conflict(m: Mutant) -> dict:
    path = m.path("GS-2022-01")
    status = read_gz(path)
    host, host_group = _first_pre_group(status)
    victim = None
    for _bucket, item in all_items(status):
        location = item.get("location") or {}
        group = location.get("pre_group_issue_id")
        if group and str(group) != host_group and item is not host:
            victim = item
            break
    assert victim is not None, "样本缺少第二分组"
    host["location"].setdefault("member_issue_ids", []).append(str(victim["id"]))
    write_gz(path, status)
    return {"expected_check": "B14", "expected_reason": "成员集合", "forbid_satisfied": TARGET_ID}


def mutate_evidence_degraded(m: Mutant) -> dict:
    path = m.path(TARGET_SAMPLE)
    status = read_gz(path)
    item = find_item(status, TARGET_ID)
    item["evidence_status"] = "incomplete_rule_warning"
    write_gz(path, status)
    return {"expected_check": "B5", "expected_reason": "MISS", "forbid_satisfied": TARGET_ID}


def mutate_partial_anchor(m: Mutant) -> dict:
    """四出现点目标只保留两个 F 出现点：一个条目降为候选，不得当整项已修复。"""
    path = m.path("GS-2022-08")
    status = read_gz(path)
    item = find_item(status, "V33-227-14")
    item["severity"] = "manual_review"
    write_gz(path, status)
    return {
        "expected_check": "B5",
        "expected_reason": "MISS",
        "forbid_satisfied": "MISS|GS-2022-08|C|GS-N05|2",
        "targets": EVIDENCE / "s4b/targets_n05_all.csv",
        "contract": EVIDENCE / "s4b/contract_n05_all.json",
    }


def mutate_same_page_unrelated(m: Mutant) -> dict:
    path = m.path(TARGET_SAMPLE)
    status = read_gz(path)
    item = find_item(status, TARGET_ID)
    item["message"] = "九、说明5款级科目名称与T5不一致（210-99）：同页其他说明文字。"
    item["evidence"] = []
    item["quotes"] = []
    write_gz(path, status)
    return {"expected_check": "B5", "expected_reason": "MISS", "forbid_satisfied": TARGET_ID}


def mutate_virtual_member_added(m: Mutant) -> dict:
    path = m.path("GS-2022-01")
    status = read_gz(path)
    item = next(i for _b, i in all_items(status)
                if isinstance(i.get("location"), dict) and i["location"].get("pre_group_issue_id"))
    item["location"].setdefault("member_issue_ids", []).append("V33-999-888")
    write_gz(path, status)
    return {"expected_check": "B14", "expected_reason": "成员", "forbid_satisfied": TARGET_ID}


MUTANTS = [
    ("existing_wrong_pre_group", mutate_existing_wrong_pre_group),
    ("cross_document_member", mutate_cross_document_member),
    ("bidirectional_conflict", mutate_bidirectional_conflict),
    ("evidence_degraded", mutate_evidence_degraded),
    ("partial_anchor", mutate_partial_anchor),
    ("same_page_unrelated", mutate_same_page_unrelated),
    ("virtual_member_added", mutate_virtual_member_added),
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)

    results = []
    # 正常对照：同一冻结副本必须全部 PASS，证明上面的拒收不是“全部失败”。
    control = Mutant()
    try:
        code, report = control.compare("normal")
        verdicts = {c["id"]: c["verdict"] for c in report["checks"]}
        results.append({"probe": "normal_control", "exit": code, "overall": report["overall"],
                        "checks": verdicts, "details": {}, "ok": code == 0 and report["overall"] == "PASS"})
    finally:
        control.cleanup()

    for name, mutate in MUTANTS:
        probe = Mutant()
        try:
            spec = mutate(probe)
            code, report = probe.compare(name, spec.get("targets", TARGETS),
                                         spec.get("contract", CONTRACT))
            verdicts = {c["id"]: c["verdict"] for c in report["checks"]}
            failed = [cid for cid, verdict in verdicts.items() if verdict == "FAIL"]
            detail_text = " ".join(str(detail) for check in report["checks"]
                                   for detail in (check.get("details") or []))
            satisfied = set(report.get("satisfied_miss_targets") or {})
            expected = spec["expected_check"]
            reason_ok = str(spec["expected_reason"]) in detail_text or expected == "B5"
            ok = (
                code == 1
                and report["overall"] == "FAIL"
                and expected in failed
                and spec["forbid_satisfied"] not in satisfied
                and reason_ok
            )
            results.append({
                "probe": name, "exit": code, "overall": report["overall"],
                "checks": verdicts, "failed": failed,
                "satisfied": sorted(satisfied),
                "expected": expected, "reason_ok": reason_ok,
                "partial_targets": sorted((report.get("partial_miss_targets") or {})),
                "ok": ok,
            })
        except AssertionError as exc:
            results.append({"probe": name, "ok": False, "error": f"变异未成立：{exc}"})
        except Exception as exc:  # noqa: BLE001 - 探针自身异常也要如实记录
            results.append({"probe": name, "ok": False, "error": f"{type(exc).__name__}: {exc}"})
        finally:
            probe.cleanup()

    (args.out / "summary.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False, indent=1))
    return 0 if all(row.get("ok") for row in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
