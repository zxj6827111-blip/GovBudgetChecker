#!/usr/bin/env python3
"""第二批金标比较器的拒收探针（合成变异，不触真实数据）。

每个用例复制 r2 冻结 run1 的 status/todo，只改一个业务事实，验证比较器会拒绝：
- 严重度/bbox 漂移
- 非目标条目移除
- 重复组歧义
- 引用越界/成员不一致
- 队列计数篡改
"""
from __future__ import annotations

import gzip
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path("/Volumes/workSSD/software development/GovBudgetChecker")
TOOL = Path(__file__).resolve().parents[1] / "scripts" / "batch2_gold_compare.py"
PYTHON = REPO / ".venv/bin/python"
RUN = REPO / "outputs/gold_baseline_20261102/run1"
ANALYSIS = REPO / "outputs/gold_baseline_20261102/rev2/analysis"
TARGETS = REPO / "docs/joint_execution_20261103/target_matrix.csv"


def read_gz(path: Path):
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return json.load(handle)


def write_gz(path: Path, payload) -> None:
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False)


def first_formal(status):
    for bucket in ("issues", "tips"):
        for item in status["review_report"].get(bucket) or []:
            if str(item.get("severity") or "").lower() in {"warn", "low", "medium", "high", "critical"}:
                return bucket, item
    raise AssertionError("没有可用的正式条目")


class ProbeCase:
    def __init__(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="batch2-probe-"))
        self.run = self.root / "run"
        self.out = self.root / "out"
        shutil.copytree(RUN / "status", self.run / "status")
        shutil.copytree(RUN / "todo", self.run / "todo")

    def cleanup(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def status_path(self, sid: str) -> Path:
        return self.run / "status" / f"{sid}.status.json.gz"

    def compare(self, stage: str = "S2", mode: str = "self"):
        proc = subprocess.run(
            [str(PYTHON), "-I", str(TOOL), "--mode", mode,
             "--stage", stage, "--run", str(self.run), "--baseline-run", str(RUN),
             "--baseline-analysis", str(ANALYSIS), "--targets", str(TARGETS),
             "--out", str(self.out)],
            capture_output=True, text=True)
        report = json.loads((self.out / "batch2_gold_compare.json").read_text(encoding="utf-8"))
        return proc.returncode, report

    def reset_out(self) -> None:
        shutil.rmtree(self.out, ignore_errors=True)


CASES = []


def case(name):
    def decorate(func):
        CASES.append((name, func))
        return func
    return decorate


@case("clean_copy_passes")
def clean_copy(c: ProbeCase):
    code, report = c.compare()
    assert code == 0 and report["overall"] == "PASS", report["checks"]


@case("severity_drift_rejected")
def severity_drift(c: ProbeCase):
    path = c.status_path("GS-2022-01")
    status = read_gz(path)
    _bucket, item = first_formal(status)
    item["severity"] = "low" if str(item.get("severity")).lower() != "low" else "warn"
    write_gz(path, status)
    code, report = c.compare()
    verdicts = {ch["id"]: ch["verdict"] for ch in report["checks"]}
    assert code == 1 and verdicts["B7"] == "FAIL", (code, verdicts)


@case("bbox_drift_rejected")
def bbox_drift(c: ProbeCase):
    path = c.status_path("GS-2022-01")
    status = read_gz(path)
    _bucket, item = first_formal(status)
    location = item.setdefault("location", {})
    location["bbox"] = [0, 0, 1, 1]
    write_gz(path, status)
    code, report = c.compare()
    verdicts = {ch["id"]: ch["verdict"] for ch in report["checks"]}
    assert code == 1 and verdicts["B8"] == "FAIL", (code, verdicts)


@case("unexpected_removal_rejected")
def unexpected_removal(c: ProbeCase):
    path = c.status_path("GS-2022-01")
    status = read_gz(path)
    bucket, item = first_formal(status)
    status["review_report"][bucket].remove(item)
    write_gz(path, status)
    code, report = c.compare()
    verdicts = {ch["id"]: ch["verdict"] for ch in report["checks"]}
    assert code == 1 and verdicts["B4"] == "FAIL", (code, verdicts)


@case("duplicate_ambiguity_rejected")
def duplicate_ambiguity(c: ProbeCase):
    path = c.status_path("GS-2022-01")
    status = read_gz(path)
    bucket, item = first_formal(status)
    clone = json.loads(json.dumps(item))
    clone["id"] = "PROBE-DUP-1"
    status["review_report"][bucket].append(clone)
    write_gz(path, status)
    code, report = c.compare()
    verdicts = {ch["id"]: ch["verdict"] for ch in report["checks"]}
    assert code == 1 and verdicts["B2"] == "FAIL", (code, verdicts)


@case("reference_out_of_range_rejected")
def reference_out_of_range(c: ProbeCase):
    path = c.status_path("GS-2022-01")
    status = read_gz(path)
    _bucket, item = first_formal(status)
    location = item.setdefault("location", {})
    location["member_issue_ids"] = ["V33-999-999"]
    location["related_findings"] = [{"location": {"pre_group_issue_id": "V33-999-999"}}]
    write_gz(path, status)
    code, report = c.compare()
    verdicts = {ch["id"]: ch["verdict"] for ch in report["checks"]}
    assert code == 1 and verdicts["B14"] == "FAIL", (code, verdicts)


@case("todo_count_tamper_rejected")
def todo_count_tamper(c: ProbeCase):
    path = c.run / "todo" / "GS-2022-01.todo.json.gz"
    todo = read_gz(path)
    counts = todo.setdefault("queue_counts", {})
    counts["queue_required_total"] = int(counts.get("queue_required_total") or 0) + 1
    write_gz(path, todo)
    code, report = c.compare()
    verdicts = {ch["id"]: ch["verdict"] for ch in report["checks"]}
    assert code == 1 and verdicts["B13"] == "FAIL", (code, verdicts)


@case("evidence_status_regression_rejected")
def evidence_status_regression(c: ProbeCase):
    """已有正式条目的证据从 complete 降级，不得靠允许字段静默通过。"""
    path = c.status_path("GS-2022-01")
    status = read_gz(path)
    _bucket, item = first_formal(status)
    item["evidence_status"] = "incomplete_rule_warning"
    write_gz(path, status)
    code, report = c.compare()
    verdicts = {ch["id"]: ch["verdict"] for ch in report["checks"]}
    assert code == 1 and verdicts["B15"] == "FAIL", (code, verdicts)


@case("member_belongs_to_other_group_rejected")
def member_belongs_to_other_group(c: ProbeCase):
    """新增成员若本身归在另一个分组，不能同时冒充本组成员。"""
    path = c.status_path("GS-2022-01")
    status = read_gz(path)
    items = [item for bucket in ("issues", "tips")
             for item in status["review_report"].get(bucket) or []
             if isinstance(item.get("location"), dict) and item["location"].get("pre_group_issue_id")]
    if len(items) < 2:
        return
    host, victim = items[0], items[1]
    host["location"].setdefault("member_issue_ids", []).append(str(victim["id"]))
    write_gz(path, status)
    code, report = c.compare()
    verdicts = {ch["id"]: ch["verdict"] for ch in report["checks"]}
    assert code == 1 and verdicts["B14"] == "FAIL", (code, verdicts)


def main() -> int:
    failures = []
    for name, func in CASES:
        probe = ProbeCase()
        try:
            func(probe)
            print(f"PASS {name}")
        except AssertionError as exc:
            failures.append(name)
            print(f"FAIL {name}: {exc}")
        except Exception as exc:  # noqa: BLE001 - 探针自身异常也要显示
            failures.append(name)
            print(f"ERROR {name}: {type(exc).__name__}: {exc}")
        finally:
            probe.cleanup()
    print(json.dumps({"cases": len(CASES), "failures": failures}, ensure_ascii=False))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
