#!/usr/bin/env python3
"""金标准基线重放：把 33 份冻结金标 PDF 交给固定提交的系统（git archive 导出），采集两遍并自比。

只做采集，不做任何指标计算。运行方式（见 docs/GOLD_BASELINE_PROTOCOL_*.md §1）：

    .venv/bin/python scripts/gold_baseline_run.py prepare   --out outputs/gold_baseline_<date> ...
    .venv/bin/python scripts/gold_baseline_run.py collect   --out outputs/gold_baseline_<date>/run1 ...
    .venv/bin/python scripts/gold_baseline_run.py selfcompare --a .../run1 --b .../run2

运行条件沿用 pending-review 验收包 v1-regression-r4 的 runtime_profile.json
（AI 关、OCR 关、无数据库、关闭 .env、固定哈希种子、时区、模板范围关闭），
子进程环境从零构造，路径类变量指向本次运行目录。本脚本不调用 scripts/verify_pending_review.py
（它拒绝金标 sha256），只借用其子进程 worker 的做法：规则在 spawn 子进程里运行，
所以 worker 入口必须有 ``if __name__ == "__main__"`` 保护；不用 ``python -I``（会丢 PYTHONHASHSEED）。
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

REPO_ROOT = Path(__file__).resolve().parents[1]
CORPUS_ROOT = Path("~/Desktop/预决算信息收集/决算测试上海市").expanduser()
DEFAULT_KIT = REPO_ROOT / "outputs/pending_review_20261101/acceptance_kit/v1-regression-r4"
SAMPLE_CSV = REPO_ROOT / "docs/GOLD_STANDARD_SAMPLE_20261101.csv"
FROZEN_CSV = REPO_ROOT / "gold/FROZEN.csv"
#: 工具自己设置的路径类变量（不进冻结 profile）
RUNTIME_SET_ENV = {"PATH", "HOME", "TMPDIR", "ORG_DATA_DIR", "UPLOAD_DIR", "AUDIT_LOG_PATH",
                   "AI_MODEL_SETTINGS_PATH", "DISCLOSURE_CALENDAR_PATH"}
FIXED_PATH = "/usr/bin:/bin"
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


# ---------------------------------------------------------------------------
# 通用
# ---------------------------------------------------------------------------


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_json_gz(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, sort_keys=True)


def read_json_gz(path: Path) -> Any:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return json.load(handle)


def is_within(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def load_samples() -> List[Dict[str, str]]:
    with SAMPLE_CSV.open(encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def worker_env(profile: Dict[str, str], *, home: Path, tmp: Path, org_dir: Path, upload_dir: Path) -> Dict[str, str]:
    """从零构造子进程环境：不继承调用者的 shell 变量。"""
    env = {"PATH": FIXED_PATH, "HOME": str(home), "TMPDIR": str(tmp)}
    env.update(profile)
    env.update(ORG_DATA_DIR=str(org_dir), UPLOAD_DIR=str(upload_dir),
               AUDIT_LOG_PATH=str(tmp / "audit.jsonl"),
               AI_MODEL_SETTINGS_PATH=str(home / "absent-ai-model-settings.json"),
               DISCLOSURE_CALENDAR_PATH=str(home / "absent-disclosure-calendar.json"))
    return env


def run_worker(task: Dict[str, Any], *, code_root: Path, python: str, env: Dict[str, str], work: Path,
               timeout: int, log_path: Path) -> Dict[str, Any]:
    work.mkdir(parents=True, exist_ok=True)
    task_path = work / "task.json"
    result_path = work / "worker_result.json"
    if result_path.exists():
        result_path.unlink()
    write_json(task_path, task)
    cmd = [python, "-s", str(Path(__file__).resolve()), "_worker", "--code-root", str(code_root),
           "--task", str(task_path), "--result", str(result_path)]
    try:
        proc = subprocess.run(cmd, cwd=code_root, env=env, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"timeout after {timeout}s"}
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(f"$ {' '.join(cmd)}\n--- stdout ---\n{proc.stdout[-20000:]}\n"
                        f"--- stderr ---\n{proc.stderr[-20000:]}\n", encoding="utf-8")
    if not result_path.exists():
        return {"ok": False, "error": f"worker exited {proc.returncode} without result"}
    result = read_json(result_path)
    if proc.returncode != 0:
        result.update(ok=False, error=result.get("error") or f"worker exited {proc.returncode}")
    return result


# ---------------------------------------------------------------------------
# 适配层：只在子进程里、在导出的代码根上接触生产入口
# ---------------------------------------------------------------------------


def adapter_run_pipeline(job_dir: Path) -> None:
    import asyncio

    from api import main as api_main

    asyncio.run(api_main._run_pipeline(job_dir))


def adapter_upload_inputs(pdf: Path, job_dir: Path) -> Dict[str, Any]:
    """按上传链路（未手选组织）推导输入：预检 → 元数据 → 自动归属组织。"""
    from unittest import mock

    from api import runtime
    from api.routes import upload

    preflight = upload._inspect_document_preflight(filename=pdf.name, pdf_path=pdf)
    fiscal_year, doc_type = upload._resolve_upload_metadata(fiscal_year=None, doc_type=None, preflight=preflight)
    bound: Dict[str, Any] = {}

    def record(job_id, org_id, *, match_type="auto", confidence=1.0, **_):
        org = runtime.require_org_storage().get_by_id(org_id)
        bound.update(organization_id=org_id, organization_name=getattr(org, "name", None),
                     organization_match_type=match_type, organization_match_confidence=confidence)
        return dict(bound)

    with mock.patch.object(runtime, "find_first_pdf", lambda _job_dir: pdf), \
            mock.patch.object(runtime, "set_job_organization", record):
        upload._auto_match_organization(job_dir.name, pdf.name, None, preflight=None)
    return {
        "organization_id": bound.get("organization_id"),
        "organization_name": bound.get("organization_name"),
        "organization_match_type": bound.get("organization_match_type"),
        "organization_match_confidence": bound.get("organization_match_confidence"),
        "fiscal_year": fiscal_year,
        "doc_type": doc_type,
        "report_year": runtime.parse_report_year(fiscal_year),
        "report_year_source": "preflight_cover" if preflight.get("report_year") else None,
        "report_kind": str(preflight.get("report_kind") or "unknown"),
        "filename": pdf.name,
    }


def adapter_todo(job_id: str, status: Dict[str, Any]) -> Dict[str, Any]:
    """与 ``api.runtime._blocking_obligation_total`` 同一组函数：列表/审核台的必须处理与参考。"""
    from src.schemas.review_report import review_queue_counts
    from src.services.material_detail_query_service import build_coverage
    from src.services.obligation_finding_coverage import obligation_covered_by_finding
    from src.services.review_lifecycle_service import UNRESOLVED_OBLIGATION_STATUSES, blocking_coverage_obligations
    from src.services.review_problem_set import collect_review_problems

    result = status.get("result") if isinstance(status.get("result"), dict) else {}
    meta = result.get("meta") if isinstance(result.get("meta"), dict) else {}
    coverage = build_coverage(meta.get("obligation_coverage"))
    problems = collect_review_problems(
        job_uuid=job_id, ai_findings=result.get("ai_findings"), rule_findings=result.get("rule_findings"),
        merged_result=result.get("merged"), structured_ingest=status.get("structured_ingest"),
        result=result, ignored_issue_ids=[],
    )
    visible = {problem.issue_id for problem in problems}
    items = list(coverage.items) if coverage.available else []
    blocking = blocking_coverage_obligations(items, result, visible_issue_ids=visible)
    covered = {
        item.obligation_id: bool(obligation_covered_by_finding(item, result, visible_issue_ids=visible))
        for item in items
        if item.blocks_gate and item.status in UNRESOLVED_OBLIGATION_STATUSES
    }
    report = status.get("review_report") if isinstance(status.get("review_report"), dict) else {}
    counts = review_queue_counts(report, ())
    counts["queue_obligation_total"] = len(blocking)
    counts["queue_required_total_with_obligations"] = counts["queue_required_total"] + len(blocking)
    return {
        "coverage_available": bool(coverage.available),
        "blocking_ids": sorted(item.obligation_id for item in blocking),
        "covered_by_finding": covered,
        "queue_counts": counts,
        "problems": [
            {"issue_id": p.issue_id, "source": p.source, "severity": p.severity, "is_formal": p.is_formal,
             "title": p.title, "member_issue_ids": list(getattr(p, "member_issue_ids", ()) or ())}
            for p in problems
        ],
    }


# ---------------------------------------------------------------------------
# 子进程入口
# ---------------------------------------------------------------------------


def worker_main(args: argparse.Namespace) -> int:
    code_root = Path(args.code_root).resolve()
    os.chdir(code_root)
    if str(code_root) not in sys.path:
        sys.path.insert(0, str(code_root))
    import logging

    logging.disable(logging.CRITICAL)
    task = read_json(Path(args.task))
    out: Dict[str, Any] = {"task": task.get("kind")}
    harness_set = set(task.get("pinned_names", [])) | RUNTIME_SET_ENV
    try:
        if task["kind"] == "pipeline":
            job_dir = Path(task["job_dir"])
            started = time.time()
            adapter_run_pipeline(job_dir)
            elapsed = time.time() - started
            status = read_json(job_dir / "status.json")
            todo = adapter_todo(job_dir.name, status)
            import api
            import src

            out.update(ok=True, elapsed_s=round(elapsed, 2), todo=todo,
                       module_files={"api": str(getattr(api, "__file__", None)),
                                     "src": str(getattr(src, "__file__", None))})
        elif task["kind"] == "upload_inputs":
            out.update(ok=True, inputs=adapter_upload_inputs(Path(task["pdf"]), Path(task["job_dir"])))
        else:
            out.update(ok=False, error=f"unknown task {task['kind']}")
    except BaseException as exc:  # noqa: BLE001 - 单份失败须留痕
        import traceback

        out.update(ok=False, error=f"{type(exc).__name__}: {exc}", traceback=traceback.format_exc()[-4000:])
    out["dotenv_leaked"] = sorted(name for name in task.get("dotenv_names", [])
                                  if name in os.environ and name not in harness_set)
    out["env_seen"] = {name: os.environ.get(name) for name in task.get("pinned_names", [])}
    write_json(Path(args.result), out)
    return 0 if out.get("ok") else 1


# ---------------------------------------------------------------------------
# prepare：核验冻结、复制 PDF 与组织快照、按上传链路推导输入
# ---------------------------------------------------------------------------


def verify_frozen(samples: List[Dict[str, str]]) -> Dict[str, Dict[str, str]]:
    with FROZEN_CSV.open(encoding="utf-8-sig") as handle:
        frozen = {row["sample_id"]: row for row in csv.DictReader(handle)}
    problems = []
    for sample in samples:
        sid = sample["sample_id"]
        annotation = REPO_ROOT / "gold" / sid / "annotation.json"
        if sid not in frozen:
            problems.append(f"{sid}: 不在 FROZEN.csv")
        elif sha256_file(annotation) != frozen[sid]["annotation_sha256"]:
            problems.append(f"{sid}: annotation.json sha256 与 FROZEN.csv 不符")
        pdf = CORPUS_ROOT / sample["path"]
        if not pdf.exists():
            problems.append(f"{sid}: PDF 不存在 {pdf}")
        elif sha256_file(pdf) != sample["sha256"]:
            problems.append(f"{sid}: PDF sha256 与样本清单不符")
    if problems:
        raise SystemExit("金标/样本核验失败，停止：\n" + "\n".join(problems))
    return frozen


def prepare_main(args: argparse.Namespace) -> int:
    out = Path(args.out).resolve()
    code_root = Path(args.code_root).resolve()
    kit = Path(args.kit).resolve()
    if is_within(code_root, out / "run1") or is_within(code_root, out / "run2"):
        raise SystemExit("代码根不得位于运行结果目录内")
    samples = load_samples()
    frozen = verify_frozen(samples)
    inputs = out / "inputs"
    if (inputs / "inputs.json").exists():
        raise SystemExit("inputs 已存在，拒绝覆盖")
    (inputs / "org").mkdir(parents=True, exist_ok=True)
    org_hashes = {}
    for name in ("organizations.json", "job_org_links.json"):
        shutil.copy(kit / "org" / name, inputs / "org" / name)
        org_hashes[name] = sha256_file(inputs / "org" / name)
    profile = read_json(kit / "runtime_profile.json")["env"]
    entries = []

    def derive(sample: Dict[str, str]) -> Dict[str, Any]:
        sid = sample["sample_id"]
        pdf_src = CORPUS_ROOT / sample["path"]
        pdf_dst = inputs / "pdfs" / sid / pdf_src.name
        pdf_dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(pdf_src, pdf_dst)
        if sha256_file(pdf_dst) != sample["sha256"]:
            raise SystemExit(f"{sid}: 复制后 sha256 不符")
        case = out / "inputs_derivation" / sid
        for sub in ("home", "tmp", "org", "uploads"):
            (case / sub).mkdir(parents=True, exist_ok=True)
        for name in ("organizations.json", "job_org_links.json"):
            shutil.copy(inputs / "org" / name, case / "org" / name)
        job_dir = case / "uploads" / sid
        job_dir.mkdir(exist_ok=True)
        env = worker_env(profile, home=case / "home", tmp=case / "tmp", org_dir=case / "org", upload_dir=case / "uploads")
        res = run_worker({"kind": "upload_inputs", "pdf": str(pdf_dst), "job_dir": str(job_dir),
                          "dotenv_names": [], "pinned_names": sorted(profile)},
                         code_root=code_root, python=args.python, env=env, work=case, timeout=600,
                         log_path=case / "worker.log")
        if not res.get("ok"):
            raise SystemExit(f"{sid} 输入推导失败：{res.get('error')}")
        return {"sample_id": sid, "sha256": sample["sha256"], "pdf": f"pdfs/{sid}/{pdf_src.name}",
                "input_status": res["inputs"], "annotation_sha256": frozen[sid]["annotation_sha256"]}

    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        entries = list(pool.map(derive, samples))
    shutil.rmtree(out / "inputs_derivation", ignore_errors=True)
    write_json(inputs / "inputs.json", {
        "note": "33 份冻结金标 PDF；input_status 按上传链路（未手选组织）推导一次后冻结，两遍采集共用。",
        "kit": str(kit), "runtime_profile": profile, "org_snapshot_sha256": org_hashes,
        "samples": entries})
    print(json.dumps({"prepared": len(entries), "org_snapshot": org_hashes}, ensure_ascii=False))
    return 0


# ---------------------------------------------------------------------------
# collect
# ---------------------------------------------------------------------------


def collect_main(args: argparse.Namespace) -> int:
    out = Path(args.out).resolve()
    code_root = Path(args.code_root).resolve()
    inputs_dir = Path(args.inputs).resolve()
    if is_within(code_root, out):
        raise SystemExit("代码根不得位于输出目录内")
    if out.exists() and any(out.iterdir()):
        raise SystemExit(f"输出目录已存在且非空：{out}")
    out.mkdir(parents=True, exist_ok=True)
    manifest = read_json(inputs_dir / "inputs.json")
    profile = manifest["runtime_profile"]
    only = set(args.only.split(",")) if args.only else None
    samples = [s for s in manifest["samples"] if not only or s["sample_id"] in only]
    meta = {"started_at": time.strftime("%Y-%m-%d %H:%M:%S %z"), "code_root": str(code_root),
            "python": args.python, "profile": profile, "jobs": args.jobs, "timeout_s": args.timeout,
            "head": args.head, "samples": [s["sample_id"] for s in samples]}

    def one(sample: Dict[str, Any]) -> Dict[str, Any]:
        sid = sample["sample_id"]
        base = out / "work" / sid
        org = base / "org"
        org.mkdir(parents=True)
        for name in ("organizations.json", "job_org_links.json"):
            shutil.copy(inputs_dir / "org" / name, org / name)
        uploads = base / "uploads"
        job = uploads / sid
        job.mkdir(parents=True)
        pdf = inputs_dir / sample["pdf"]
        shutil.copy(pdf, job / pdf.name)
        status = dict(sample["input_status"])
        status.update(job_id=sid, status="queued", checksum=sample["sha256"],
                      use_local_rules=True, use_ai_assist=False, mode="legacy")
        write_json(job / "status.json", status)
        (base / "home").mkdir()
        (base / "tmp").mkdir()
        env = worker_env(profile, home=base / "home", tmp=base / "tmp", org_dir=org, upload_dir=uploads)
        task = {"kind": "pipeline", "job_dir": str(job), "dotenv_names": [], "pinned_names": sorted(profile)}
        res = run_worker(task, code_root=code_root, python=args.python, env=env, work=base,
                         timeout=args.timeout, log_path=out / "logs" / f"{sid}.log")
        entry = {"sample_id": sid, "ok": bool(res.get("ok")), "error": res.get("error"),
                 "elapsed_s": res.get("elapsed_s"), "dotenv_leaked": res.get("dotenv_leaked", []),
                 "env_seen_matches_profile": res.get("env_seen") == {k: profile[k] for k in sorted(profile)},
                 "module_files": res.get("module_files")}
        status_path = job / "status.json"
        if status_path.exists():
            final = read_json(status_path)
            write_json_gz(out / "status" / f"{sid}.status.json.gz", final)
            entry["status"] = final.get("status")
            entry["error_in_status"] = final.get("error")
            if res.get("ok"):
                write_json_gz(out / "todo" / f"{sid}.todo.json.gz", res["todo"])
        shutil.rmtree(base, ignore_errors=True)
        print(f"{sid}: {'ok' if entry['ok'] else 'FAILED ' + str(entry['error'])} ({entry['elapsed_s']}s)", flush=True)
        return entry

    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        entries = list(pool.map(one, samples))
    shutil.rmtree(out / "work", ignore_errors=True)
    meta["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S %z")
    meta["entries"] = sorted(entries, key=lambda e: e["sample_id"])
    meta["failed"] = [e["sample_id"] for e in entries if not e["ok"]]
    meta["code_root_is_export"] = all(
        (e.get("module_files") or {}).get("api", "").startswith(str(code_root)) and
        (e.get("module_files") or {}).get("src", "").startswith(str(code_root)) for e in entries if e["ok"])
    meta["dotenv_isolation_ok"] = all(not e["dotenv_leaked"] and e["env_seen_matches_profile"] for e in entries)
    write_json(out / "meta.json", meta)
    print(json.dumps({"out": str(out), "samples": len(samples), "failed": meta["failed"],
                      "code_root_is_export": meta["code_root_is_export"],
                      "env_ok": meta["dotenv_isolation_ok"]}, ensure_ascii=False))
    return 0 if not meta["failed"] else 1


# ---------------------------------------------------------------------------
# selfcompare：两遍采集逐字段比较
# ---------------------------------------------------------------------------

#: 冻结的易变字段（pending-review 验收包 volatile_fields.json）
FROZEN_VOLATILE = {"id", "created_at"}
#: 本次自比中额外发现、只记录挂钟时间/耗时/运行标识的字段（不在冻结清单内，单独列出，不当作内容差异）。
#: analysis_revision 由 api/main.py 生成：``rev-<UTC 时间戳>-<uuid4 前 8 位>``，是每次运行的随机标识，整体抹掉。
CLOCK_PATHS = {"/ts", "/pipeline_budget/active_elapsed_seconds", "/pipeline_budget/elapsed_seconds",
               "/pipeline_budget/started_at", "/result/meta/started_at", "/result/meta/finished_at"}
REVISION_RE = re.compile(r"rev-\d{8}T\d{6}-[0-9a-f]{8}")


def normalize_uuids(value: Any, table: Dict[str, str]) -> Any:
    """把字符串里出现的 UUID 按首次出现顺序换成 u<序号>。两遍确定性相同则序号序列相同。"""
    if isinstance(value, dict):
        return {k: normalize_uuids(value[k], table) for k in sorted(value)}
    if isinstance(value, list):
        return [normalize_uuids(v, table) for v in value]
    if isinstance(value, str) and UUID_RE.search(value):
        def sub(match: "re.Match[str]") -> str:
            return table.setdefault(match.group(0), f"u{len(table)}")

        return UUID_RE.sub(sub, value)
    return value


def strip_keys(value: Any, keys: Set[str]) -> Any:
    if isinstance(value, dict):
        return {k: strip_keys(v, keys) for k, v in value.items() if k not in keys}
    if isinstance(value, list):
        return [strip_keys(v, keys) for v in value]
    return value


def strip_clock(value: Any, path: str = "") -> Any:
    if isinstance(value, dict):
        return {k: strip_clock(v, f"{path}/{k}") for k, v in value.items() if f"{path}/{k}" not in CLOCK_PATHS}
    if isinstance(value, list):
        return [strip_clock(v, path) for v in value]
    if isinstance(value, str) and REVISION_RE.search(value):
        return REVISION_RE.sub("rev-<run-id>", value)
    return value


def diff_paths(a: Any, b: Any, path: str = "") -> List[str]:
    if type(a) is not type(b):
        return [path or "$"]
    if isinstance(a, dict):
        paths: List[str] = []
        for key in sorted(set(a) | set(b)):
            if key not in a or key not in b:
                paths.append(f"{path}/{key}")
            else:
                paths.extend(diff_paths(a[key], b[key], f"{path}/{key}"))
        return paths
    if isinstance(a, list):
        if len(a) != len(b):
            return [f"{path}[len {len(a)}≠{len(b)}]"]
        paths = []
        for i, (x, y) in enumerate(zip(a, b)):
            paths.extend(diff_paths(x, y, f"{path}[{i}]"))
        return paths
    return [] if a == b else [path or "$"]


def selfcompare_main(args: argparse.Namespace) -> int:
    run_a, run_b = Path(args.a).resolve(), Path(args.b).resolve()
    names_a = sorted(p.name for p in (run_a / "status").glob("*.status.json.gz"))
    names_b = sorted(p.name for p in (run_b / "status").glob("*.status.json.gz"))
    report: Dict[str, Any] = {"a": str(run_a), "b": str(run_b), "samples_a": len(names_a), "samples_b": len(names_b),
                              "frozen_volatile_fields": sorted(FROZEN_VOLATILE), "per_sample": {}}
    if names_a != names_b:
        report["sample_set_equal"] = False
    totals = {"identical_after_frozen_volatile": 0, "identical_after_frozen_volatile_and_clock": 0, "differing": 0}
    clock_patterns: Dict[str, int] = {}
    other_paths: Dict[str, int] = {}
    for name in names_a:
        sid = name.split(".")[0]
        sa, sb = read_json_gz(run_a / "status" / name), read_json_gz(run_b / "status" / name)
        # 1) 原始差异（仅作记录）
        raw = diff_paths(sa, sb)
        # 2) 冻结易变字段去掉后，UUID 引用按首次出现顺序归一
        na = normalize_uuids(strip_keys(sa, FROZEN_VOLATILE), {})
        nb = normalize_uuids(strip_keys(sb, FROZEN_VOLATILE), {})
        after_frozen = diff_paths(na, nb)
        # 3) 再抹掉挂钟时间字段后仍有差异的，才是内容差异
        left = diff_paths(strip_clock(na), strip_clock(nb))
        todo_a, todo_b = read_json_gz(run_a / "todo" / f"{sid}.todo.json.gz"), read_json_gz(run_b / "todo" / f"{sid}.todo.json.gz")
        todo_left = diff_paths(normalize_uuids(todo_a, {}), normalize_uuids(todo_b, {}))
        report["per_sample"][sid] = {"raw_diff_count": len(raw),
                                     "diff_paths_after_frozen_volatile": sorted(set(re.sub(r"\[\d+\]", "[]", p) for p in after_frozen)),
                                     "content_diff_after_clock_strip": left, "todo_diff": todo_left}
        if not after_frozen and not todo_left:
            totals["identical_after_frozen_volatile"] += 1
        for p in after_frozen:
            key = re.sub(r"\[\d+\]", "[]", p)
            clock_patterns[key] = clock_patterns.get(key, 0) + 1
        if left or todo_left:
            totals["differing"] += 1
            for p in left + todo_left:
                key = re.sub(r"\[\d+\]", "[]", p)
                other_paths[key] = other_paths.get(key, 0) + 1
        else:
            totals["identical_after_frozen_volatile_and_clock"] += 1
    report["totals"] = totals
    report["paths_differing_after_frozen_volatile"] = dict(sorted(clock_patterns.items(), key=lambda kv: -kv[1]))
    report["unexplained_path_patterns"] = dict(sorted(other_paths.items(), key=lambda kv: -kv[1]))
    write_json(Path(args.out).resolve(), report)
    print(json.dumps({"totals": totals, "patterns": report["unexplained_path_patterns"]}, ensure_ascii=False, indent=2))
    return 0 if not totals["differing"] else 1


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("--out", required=True)
    p.add_argument("--code-root", required=True)
    p.add_argument("--kit", default=str(DEFAULT_KIT))
    p.add_argument("--python", default=sys.executable)
    p.add_argument("--jobs", type=int, default=4)
    p = sub.add_parser("collect")
    p.add_argument("--out", required=True)
    p.add_argument("--inputs", required=True)
    p.add_argument("--code-root", required=True)
    p.add_argument("--python", default=sys.executable)
    p.add_argument("--head", default="")
    p.add_argument("--jobs", type=int, default=4)
    p.add_argument("--timeout", type=int, default=1800)
    p.add_argument("--only", default="")
    p = sub.add_parser("selfcompare")
    p.add_argument("--a", required=True)
    p.add_argument("--b", required=True)
    p.add_argument("--out", required=True)
    p = sub.add_parser("_worker")
    p.add_argument("--code-root", required=True)
    p.add_argument("--task", required=True)
    p.add_argument("--result", required=True)
    args = parser.parse_args(argv)
    return {"prepare": prepare_main, "collect": collect_main, "selfcompare": selfcompare_main,
            "_worker": worker_main}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
