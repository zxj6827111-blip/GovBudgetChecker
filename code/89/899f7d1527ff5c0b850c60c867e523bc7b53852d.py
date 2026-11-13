#!/usr/bin/env python3
"""待核治理第一阶段验收工具（验收包 v1-regression）。

方案：``docs/待核治理实施方案_20261101.md``（R2）；验收包说明：
``docs/待核治理验收包_第一阶段_20261101.md``。本工具由验收方编写并随验收包冻结，
执行方不得修改“比较与判定”部分；入口函数改名时只能改“适配层”，并在整改记录中写明。

子命令
------
``manifest``  验收方编制样本清单与冻结输入（一次性；执行方不得运行）。
``seal``      为验收包生成 ``SHA256SUMS``（验收方使用）。
``snapshot``  把代码根精确复制到独立目录，逐文件登记哈希（U-1 的快照方式）。
``collect``   在指定代码根上按冻结运行条件重放样本并采集结果。
``compare``   比较两次采集：第一阶段不变量与 DH-02/DH-03 覆盖目标。
``boundary``  运行冻结的同源判定边界用例。
``selftest``  工具自测：确定性、敏感性与拒绝条件。

红线（违反即拒绝运行，不跳过后继续）
------------------------------------
- 验收包 ``SHA256SUMS`` 与文件不符；
- 任一样本 sha256 出现在金标排除清单中，或与清单登记不符；
- 输出目录已存在；
- 代码根位于验收包或输出目录之内。
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import gzip
import hashlib
import json
import os
import platform
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

KIT_VERSION = "v1-regression"
REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_KIT = REPO_ROOT / "outputs" / "pending_review_20261101" / "acceptance_kit" / KIT_VERSION
CATALOG_V12 = "obligations-v12-checklist-global-20261022"
FIXTURE_FILENAMES = {"FORMAL_fixture.pdf"}
UNRESOLVED_RECEIPT = {"insufficient_data", "parse_error", "execution_error"}
UNRESOLVED_OBLIGATION = {
    "not_implemented", "not_executed", "insufficient_data", "parse_ambiguity", "execution_error",
    "profile_unresolved", "kind_conflict", "ai_not_run", "ai_failed",
}
#: 快照与代码指纹覆盖的目录/文件（后端管线与测试实际加载的范围）。
#: ocr_service 在 OCR 关闭时也会被管线导入（2026-11-01 自测发现，漏掉即 ModuleNotFoundError）。
CODE_DIRS = ("src", "api", "config", "rules", "gbc_yaml", "scripts", "tests", "ocr_service")
CODE_ROOT_FILES = ("pyproject.toml", "pytest.ini", "requirements.txt", "requirements-dev.txt", "uv.lock",
                   "ai_extractor_service.py", "AGENTS.md")
CODE_SUFFIXES = {".py", ".yaml", ".yml", ".json", ".toml", ".ini", ".txt", ".md", ".csv", ".sql", ".cfg"}
#: 快照不复制的内容：密钥与运行产物。
SNAPSHOT_EXCLUDE_NAMES = {".env", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "node_modules"}


# ---------------------------------------------------------------------------
# 通用工具
# ---------------------------------------------------------------------------


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n",
                    encoding="utf-8")


def write_json_gz(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str))


def read_json_gz(path: Path) -> Any:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return json.loads(handle.read())


def refuse(message: str) -> None:
    print(f"拒绝运行：{message}", file=sys.stderr)
    raise SystemExit(2)


def ensure_new_dir(path: Path) -> Path:
    path = path.expanduser().resolve()
    if path.exists():
        refuse(f"输出目录已存在，为避免覆盖已有证据而拒绝：{path}")
    path.mkdir(parents=True)
    return path


def is_within(child: Path, parent: Path) -> bool:
    child, parent = child.resolve(), parent.resolve()
    return child == parent or parent in child.parents


# ---------------------------------------------------------------------------
# 验收包：校验与读取
# ---------------------------------------------------------------------------


def kit_files(kit: Path) -> List[Path]:
    return sorted(p for p in kit.rglob("*") if p.is_file() and p.name != "SHA256SUMS")


def seal_kit(kit: Path) -> str:
    lines = [f"{sha256_file(p)}  {p.relative_to(kit).as_posix()}" for p in kit_files(kit)]
    harness = Path(__file__).resolve()
    lines.append(f"{sha256_file(harness)}  @harness/{harness.name}")
    (kit / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return sha256_file(kit / "SHA256SUMS")


def verify_kit(kit: Path) -> str:
    """校验验收包完整性；返回 SHA256SUMS 自身的哈希（验收包身份）。"""
    sums = kit / "SHA256SUMS"
    if not sums.exists():
        refuse(f"验收包缺少 SHA256SUMS：{kit}")
    expected: Dict[str, str] = {}
    for line in sums.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        digest, name = line.split("  ", 1)
        expected[name] = digest
    actual = {p.relative_to(kit).as_posix(): p for p in kit_files(kit)}
    unlisted = sorted(set(actual) - set(expected))
    missing = sorted(name for name in expected if not name.startswith("@harness/") and name not in actual)
    if unlisted or missing:
        refuse(f"验收包文件集合与 SHA256SUMS 不一致：多出 {unlisted[:5]}，缺少 {missing[:5]}")
    for name, path in actual.items():
        if sha256_file(path) != expected[name]:
            refuse(f"验收包文件哈希不符：{name}")
    harness_key = f"@harness/{Path(__file__).name}"
    if expected.get(harness_key) != sha256_file(Path(__file__).resolve()):
        refuse("验收工具本身与验收包登记的哈希不符（工具被修改，或用错了版本）")
    return sha256_file(sums)


def load_samples(kit: Path, sets: Sequence[str], only: Optional[Set[str]] = None) -> List[Dict[str, Any]]:
    gold = set(read_json(kit / "gold_exclusion.json")["sha256"])
    samples: List[Dict[str, Any]] = []
    for name in sets:
        manifest = read_json(kit / f"manifest_{name}.json")
        for sample in manifest["samples"]:
            if only and sample["sample_id"] not in only:
                continue
            pdf = kit / sample["pdf"]
            if sample["sha256"] in gold:
                refuse(f"样本 {sample['sample_id']} 的 sha256 在金标排除清单中")
            if sha256_file(pdf) != sample["sha256"]:
                refuse(f"样本 {sample['sample_id']} 的 PDF 与清单登记的 sha256 不符")
            samples.append(sample)
    if only:
        absent = sorted(only - {s["sample_id"] for s in samples})
        if absent:
            refuse(f"--only 中的样本不在清单内：{absent}")
    ids = [s["sample_id"] for s in samples]
    if len(ids) != len(set(ids)):
        refuse("样本编号重复")
    return samples


# ---------------------------------------------------------------------------
# 运行条件
# ---------------------------------------------------------------------------

_ENV_NAME_RE = re.compile(
    r"""(?:os\.getenv|os\.environ\.get|environ\.get|getenv)\(\s*['"]([A-Z][A-Z0-9_]+)['"]"""
    r"""|os\.environ\[\s*['"]([A-Z][A-Z0-9_]+)['"]\s*\]"""
)


def code_env_names(code_root: Path) -> List[str]:
    names: Set[str] = set()
    for directory in ("src", "api", "config"):
        for path in (code_root / directory).rglob("*.py"):
            for match in _ENV_NAME_RE.finditer(path.read_text(encoding="utf-8", errors="ignore")):
                names.add(match.group(1) or match.group(2))
    return sorted(names)


def dotenv_names(code_root: Path) -> List[str]:
    """只读取 .env 的键名（不读值），用于证明它没有进入运行环境。"""
    path = code_root / ".env"
    if not path.exists():
        return []
    names = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        match = re.match(r"\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
        if match:
            names.append(match.group(1))
    return sorted(set(names))


#: 每次运行由工具指定的路径变量（不进冻结 profile，取值随运行目录变化）。
#: 后三项的默认值指向代码根下的 data/：重放时一律改到运行目录，既不读真实配置，也不写真实数据。
RUNTIME_SET_ENV = {"PATH", "HOME", "TMPDIR", "ORG_DATA_DIR", "UPLOAD_DIR",
                   "AUDIT_LOG_PATH", "AI_MODEL_SETTINGS_PATH", "DISCLOSURE_CALENDAR_PATH"}


def worker_env(profile: Dict[str, str], *, home: Path, tmp: Path, org_dir: Path, upload_dir: Path) -> Dict[str, str]:
    """从零构造子进程环境：不继承调用者的 shell 变量，.env 由 PYTHON_DOTENV_DISABLED 关闭。"""
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(home),
        "TMPDIR": str(tmp),
    }
    env.update(profile)
    env.update(ORG_DATA_DIR=str(org_dir), UPLOAD_DIR=str(upload_dir),
               AUDIT_LOG_PATH=str(tmp / "audit.jsonl"),
               AI_MODEL_SETTINGS_PATH=str(home / "absent-ai-model-settings.json"),
               DISCLOSURE_CALENDAR_PATH=str(home / "absent-disclosure-calendar.json"))
    return env


def tree_state(code_root: Path) -> Dict[str, Tuple[int, int]]:
    """代码根内文件的 (大小, 修改时间)，用于发现重放是否写入了代码根。"""
    state: Dict[str, Tuple[int, int]] = {}
    for directory in (*CODE_DIRS, "data", "logs", "models"):
        base = code_root / directory
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if path.is_file() and not any(part in SNAPSHOT_EXCLUDE_NAMES for part in path.parts):
                stat = path.stat()
                state[path.relative_to(code_root).as_posix()] = (stat.st_size, stat.st_mtime_ns)
    return state


def code_fingerprint(code_root: Path) -> Dict[str, Any]:
    files: Dict[str, str] = {}
    for directory in CODE_DIRS:
        base = code_root / directory
        if not base.exists():
            continue
        for path in sorted(base.rglob("*")):
            if (path.is_file() and path.suffix in CODE_SUFFIXES
                    and not any(part in SNAPSHOT_EXCLUDE_NAMES for part in path.parts)):
                files[path.relative_to(code_root).as_posix()] = sha256_file(path)
    for name in CODE_ROOT_FILES:
        if (code_root / name).exists():
            files[name] = sha256_file(code_root / name)
    info: Dict[str, Any] = {"file_count": len(files), "digest": sha256_text(canonical(files)), "files": files}
    snapshot_meta = code_root / "SNAPSHOT.json"
    if snapshot_meta.exists():
        info["snapshot"] = read_json(snapshot_meta).get("source")
    if (code_root / ".git").exists():
        def git(*args: str) -> str:
            return subprocess.run(["git", *args], cwd=code_root, capture_output=True, text=True).stdout
        info["git_head"] = git("rev-parse", "HEAD").strip()
        porcelain = git("status", "--porcelain")
        info["git_status_entries"] = len([l for l in porcelain.splitlines() if l.strip()])
        info["git_status_sha256"] = sha256_text(porcelain)
    return info


def runtime_fingerprint(python: str) -> Dict[str, Any]:
    # .venv 由 uv 管理、不带 pip，依赖清单用 importlib.metadata 读取。
    probe = ("import sys,platform,importlib.metadata as m;print(sys.version.replace(chr(10),' '));"
             "print(platform.platform());"
             "print('\\n'.join(sorted({f\"{d.metadata['Name']}=={d.version}\" for d in m.distributions()})))")
    lines = subprocess.run([python, "-c", probe], capture_output=True, text=True,
                           env={"PATH": os.environ.get("PATH", "")}).stdout.splitlines()
    packages = lines[2:]
    return {
        "python": python,
        "python_version": lines[0] if lines else None,
        "platform": lines[1] if len(lines) > 1 else platform.platform(),
        "packages_sha256": sha256_text("\n".join(packages)),
        "package_count": len(packages),
        "packages": packages,
    }


# ---------------------------------------------------------------------------
# 适配层：只在这里接触生产入口（在子进程内、代码根上执行）
# ---------------------------------------------------------------------------


def adapter_run_pipeline(job_dir: Path) -> None:
    import asyncio

    from api import main as api_main

    asyncio.run(api_main._run_pipeline(job_dir))


def adapter_todo(job_id: str, status: Dict[str, Any]) -> Dict[str, Any]:
    """与 ``api.runtime._blocking_obligation_total`` 同一组函数，逐条给出待办与同源判定。"""
    from src.schemas.review_report import review_queue_counts
    from src.services.material_detail_query_service import build_coverage
    from src.services.obligation_finding_coverage import obligation_covered_by_finding
    from src.services.review_lifecycle_service import blocking_coverage_obligations
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
        if item.blocks_gate and item.status in UNRESOLVED_OBLIGATION
    }
    report = status.get("review_report") if isinstance(status.get("review_report"), dict) else {}
    return {
        "coverage_available": bool(coverage.available),
        "coverage_items": [item.model_dump() for item in items],
        "blocking_ids": sorted(item.obligation_id for item in blocking),
        "covered_by_finding": covered,
        "queue_counts": review_queue_counts(report, ()),
        "problems": [
            {"issue_id": p.issue_id, "source": p.source, "severity": p.severity,
             "member_issue_ids": list(getattr(p, "member_issue_ids", ()) or ())}
            for p in problems
        ],
    }


def adapter_upload_inputs(pdf: Path, job_dir: Path) -> Dict[str, Any]:
    """按上传链路（未手选组织）推导冻结输入：预检 → 元数据 → 自动归属。"""
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


def adapter_coverage_decision(ledger: Dict[str, Any], findings: List[Dict[str, Any]],
                              visible: List[str]) -> Dict[str, Any]:
    from src.services.material_detail_query_service import build_coverage
    from src.services.obligation_finding_coverage import obligation_covered_by_finding
    from src.services.review_lifecycle_service import blocking_coverage_obligations

    coverage = build_coverage(ledger)
    result = {"rule_findings": findings}
    items = list(coverage.items)
    blocking = {item.obligation_id for item in blocking_coverage_obligations(items, result, visible_issue_ids=set(visible))}
    return {
        item.obligation_id: {
            "covered": bool(obligation_covered_by_finding(item, result, visible_issue_ids=set(visible))),
            "in_todo": item.obligation_id in blocking,
        }
        for item in items
    }


# ---------------------------------------------------------------------------
# 子进程入口
# ---------------------------------------------------------------------------


def _worker_prepare(code_root: Path) -> None:
    os.chdir(code_root)
    if str(code_root) not in sys.path:
        sys.path.insert(0, str(code_root))
    import logging

    logging.disable(logging.CRITICAL)


def worker_main(args: argparse.Namespace) -> int:
    code_root = Path(args.code_root).resolve()
    _worker_prepare(code_root)
    task = read_json(Path(args.task))
    out: Dict[str, Any] = {"task": task.get("kind")}
    leaked = sorted(name for name in task.get("dotenv_names", [])
                    if name in os.environ and name not in task.get("pinned_names", []))
    try:
        if task["kind"] == "pipeline":
            job_dir = Path(task["job_dir"])
            started = time.time()
            adapter_run_pipeline(job_dir)
            elapsed = time.time() - started
            status = read_json(job_dir / "status.json")
            todo = adapter_todo(job_dir.name, status)
            leaked = sorted(set(leaked) | {name for name in task.get("dotenv_names", [])
                                           if name in os.environ and name not in task.get("pinned_names", [])})
            out.update(ok=True, elapsed_s=round(elapsed, 2), todo=todo)
        elif task["kind"] == "upload_inputs":
            out.update(ok=True, inputs=adapter_upload_inputs(Path(task["pdf"]), Path(task["job_dir"])))
        elif task["kind"] == "coverage_decision":
            out.update(ok=True, decision=adapter_coverage_decision(task["ledger"], task["findings"], task["visible"]))
        else:
            out.update(ok=False, error=f"unknown task {task['kind']}")
    except BaseException as exc:  # noqa: BLE001 - 单份失败须留痕
        import traceback

        out.update(ok=False, error=f"{type(exc).__name__}: {exc}", traceback=traceback.format_exc()[-4000:])
    out["dotenv_leaked"] = leaked
    out["env_seen"] = {name: os.environ.get(name) for name in task.get("pinned_names", [])}
    write_json(Path(args.result), out)
    return 0 if out.get("ok") else 1


def run_worker(task: Dict[str, Any], *, code_root: Path, python: str, env: Dict[str, str], work: Path,
               timeout: int, log_path: Path) -> Dict[str, Any]:
    task_path = work / "task.json"
    result_path = work / "worker_result.json"
    write_json(task_path, task)
    # 不用 -I：它会忽略冻结的 PYTHONHASHSEED。代码根由 _worker_prepare 插到 sys.path 首位。
    cmd = [python, "-s", str(Path(__file__).resolve()), "_worker", "--code-root", str(code_root),
           "--task", str(task_path), "--result", str(result_path)]
    try:
        proc = subprocess.run(cmd, cwd=code_root, env=env, capture_output=True, text=True, timeout=timeout)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(f"$ {' '.join(cmd)}\n--- stdout ---\n{proc.stdout[-20000:]}\n"
                            f"--- stderr ---\n{proc.stderr[-20000:]}\n", encoding="utf-8")
        if not result_path.exists():
            return {"ok": False, "error": f"worker exited {proc.returncode} without result"}
        return read_json(result_path)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"timeout after {timeout}s"}


# ---------------------------------------------------------------------------
# 记录提取（在父进程内，只读 status.json）
# ---------------------------------------------------------------------------


_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def _volatile(kit: Path) -> Dict[str, Any]:
    return read_json(kit / "volatile_fields.json")


def strip_volatile(value: Any, keys: Set[str]) -> Any:
    if isinstance(value, dict):
        return {k: strip_volatile(v, keys) for k, v in value.items() if k not in keys}
    if isinstance(value, list):
        return [strip_volatile(v, keys) for v in value]
    return value


def finding_fingerprint(finding: Dict[str, Any], keys: Set[str]) -> str:
    return sha256_text(canonical(strip_volatile(finding, keys)))


def extract_record(sample: Dict[str, Any], status: Dict[str, Any], todo: Dict[str, Any],
                   volatile: Dict[str, Any]) -> Dict[str, Any]:
    keys = set(volatile["finding_keys"])
    result = status.get("result") if isinstance(status.get("result"), dict) else {}
    meta = result.get("meta") if isinstance(result.get("meta"), dict) else {}
    findings = [f for f in (result.get("rule_findings") or []) if isinstance(f, dict)]
    id_to_fp = {str(f.get("id")): finding_fingerprint(f, keys) for f in findings if f.get("id")}
    report = status.get("review_report") if isinstance(status.get("review_report"), dict) else {}
    receipts = []
    for receipt in report.get("check_receipts") or []:
        if isinstance(receipt, dict):
            item = dict(receipt)
            item["issue_refs"] = sorted(id_to_fp.get(str(ref), f"unmapped:{ref}") for ref in item.get("issue_refs") or [])
            receipts.append(item)
    ledger = status.get("check_coverage") if isinstance(status.get("check_coverage"), dict) else {}
    meta_ledger = meta.get("obligation_coverage") if isinstance(meta.get("obligation_coverage"), dict) else {}
    covered = todo.get("covered_by_finding") or {}

    def problem_identity(issue_id: str) -> str:
        # 规则问题的 id 是随机 UUID，换成问题指纹；结构化读取等确定性 id 原样保留。
        if issue_id in id_to_fp:
            return id_to_fp[issue_id]
        return "uuid:unmapped" if _UUID_RE.fullmatch(issue_id) else f"id:{issue_id}"

    problems = [
        {**{k: v for k, v in p.items() if k not in {"issue_id", "member_issue_ids"}},
         "identity": problem_identity(str(p.get("issue_id"))),
         "members": sorted(problem_identity(str(m)) for m in p.get("member_issue_ids") or [])}
        for p in todo.get("problems") or []
    ]
    reading = []
    for item in (status.get("structured_ingest") or {}).get("review_items") or []:
        if isinstance(item, dict):
            reading.append({k: item.get(k) for k in ("id", "type", "table_code", "page_numbers", "reason", "severity")})
    return {
        "sample_id": sample["sample_id"],
        "sha256": sample["sha256"],
        "run": {
            "status": status.get("status"),
            "error": status.get("error"),
            "quality_status": status.get("quality_status"),
            "analysis_conclusion": status.get("analysis_conclusion"),
            "review_reason_codes": sorted(str(r.get("code")) for r in status.get("review_reasons") or []
                                          if isinstance(r, dict)),
        },
        "obligation_summary": {k: ledger.get(k) for k in (
            "catalog_version", "applicable_total", "completed_total", "not_applicable_total",
            "unresolved_total", "blocking_total", "by_reason", "blocking_obligation_ids")},
        "ledger_sources_equal": canonical(ledger.get("instances")) == canonical(meta_ledger.get("instances")),
        "obligations": {str(i.get("obligation_id")): i for i in ledger.get("instances") or [] if isinstance(i, dict)},
        "coverage_items": {str(i.get("obligation_id")): i for i in todo.get("coverage_items") or []},
        "receipts": receipts,
        "findings": sorted(id_to_fp[i] for i in id_to_fp) + sorted(
            finding_fingerprint(f, keys) for f in findings if not f.get("id")),
        "finding_bodies": {fp: strip_volatile(f, keys) for f in findings
                           for fp in [finding_fingerprint(f, keys)]},
        "todo": {
            "coverage_available": todo.get("coverage_available"),
            "blocking_ids": todo.get("blocking_ids"),
            "covered_by_finding": dict(sorted(covered.items())),
            "queue_counts": todo.get("queue_counts"),
            "problems": sorted(problems, key=canonical),
        },
        "reading_items": sorted(reading, key=canonical),
    }


# ---------------------------------------------------------------------------
# collect
# ---------------------------------------------------------------------------


def _prepare_job(sample: Dict[str, Any], kit: Path, run_root: Path) -> Tuple[Path, Path, Path]:
    base = run_root / "work" / sample["sample_id"]
    org = base / "org"
    org.mkdir(parents=True)
    for name in ("organizations.json", "job_org_links.json"):
        source = kit / "org" / name
        if source.exists():
            shutil.copy(source, org / name)
    uploads = base / "uploads"
    job = uploads / sample["sample_id"]
    job.mkdir(parents=True)
    pdf = kit / sample["pdf"]
    shutil.copy(pdf, job / pdf.name)
    status = dict(sample["input_status"])
    status.update(job_id=sample["sample_id"], status="queued", checksum=sample["sha256"],
                  use_local_rules=True, use_ai_assist=False, mode="legacy")
    write_json(job / "status.json", status)
    (base / "home").mkdir()
    (base / "tmp").mkdir()
    return base, org, uploads


def collect_main(args: argparse.Namespace) -> int:
    kit = Path(args.kit).resolve()
    kit_digest = verify_kit(kit)
    code_root = Path(args.code_root).resolve()
    out = Path(args.out).expanduser().resolve()
    if is_within(code_root, kit) or is_within(out, kit):
        refuse("代码根或输出目录不得位于验收包内")
    if is_within(code_root, out):
        refuse("代码根不得位于输出目录内")
    sets = [s.strip() for s in args.sets.split(",") if s.strip()]
    only = set(args.only.split(",")) if args.only else None
    samples = load_samples(kit, sets, only)
    out = ensure_new_dir(out)
    profile = read_json(kit / "runtime_profile.json")["env"]
    overrides = dict(item.split("=", 1) for item in args.profile_override or [])
    profile = {**profile, **overrides}
    volatile = _volatile(kit)
    env_names = code_env_names(code_root)
    dotenv = dotenv_names(code_root)
    meta: Dict[str, Any] = {
        "kit_version": KIT_VERSION, "kit_digest": kit_digest, "label": args.label,
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S %z"), "sets": sets,
        "samples": [{"sample_id": s["sample_id"], "sha256": s["sha256"]} for s in samples],
        "code_root": str(code_root), "code": code_fingerprint(code_root),
        "runtime": runtime_fingerprint(args.python), "profile": profile, "profile_overrides": overrides,
        "code_env_names": {name: (profile[name] if name in profile else
                                  "<per-run>" if name in RUNTIME_SET_ENV else "<unset>") for name in env_names},
        "dotenv_key_names": dotenv, "jobs": args.jobs, "timeout_s": args.timeout,
    }

    def one(sample: Dict[str, Any]) -> Dict[str, Any]:
        base, org, uploads = _prepare_job(sample, kit, out)
        env = worker_env(profile, home=base / "home", tmp=base / "tmp", org_dir=org, upload_dir=uploads)
        task = {"kind": "pipeline", "job_dir": str(uploads / sample["sample_id"]), "dotenv_names": dotenv,
                "pinned_names": sorted(profile)}
        res = run_worker(task, code_root=code_root, python=args.python, env=env, work=base,
                         timeout=args.timeout, log_path=out / "logs" / f"{sample['sample_id']}.log")
        entry = {"sample_id": sample["sample_id"], "ok": bool(res.get("ok")), "error": res.get("error"),
                 "elapsed_s": res.get("elapsed_s"), "dotenv_leaked": res.get("dotenv_leaked", []),
                 "env_seen_matches_profile": res.get("env_seen") == {k: profile[k] for k in sorted(profile)}}
        status_path = uploads / sample["sample_id"] / "status.json"
        if status_path.exists():
            status = read_json(status_path)
            write_json_gz(out / "status" / f"{sample['sample_id']}.status.json.gz", status)
            if res.get("ok"):
                write_json_gz(out / "records" / f"{sample['sample_id']}.json.gz",
                              extract_record(sample, status, res["todo"], volatile))
        if not args.keep_work:
            shutil.rmtree(base, ignore_errors=True)
        print(f"{sample['sample_id']}: {'ok' if entry['ok'] else 'FAILED ' + str(entry['error'])}"
              f" ({entry['elapsed_s']}s)", flush=True)
        return entry

    before = tree_state(code_root)
    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        entries = list(pool.map(one, samples))
    after = tree_state(code_root)
    meta["code_root_writes"] = sorted(name for name in set(before) | set(after) if before.get(name) != after.get(name))
    meta["code_after"] = code_fingerprint(code_root)["digest"]
    meta["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S %z")
    meta["entries"] = sorted(entries, key=lambda e: e["sample_id"])
    meta["failed"] = [e["sample_id"] for e in entries if not e["ok"]]
    meta["dotenv_isolation_ok"] = all(not e["dotenv_leaked"] and e["env_seen_matches_profile"] for e in entries)
    write_json(out / "meta.json", meta)
    print(json.dumps({"out": str(out), "samples": len(samples), "failed": meta["failed"],
                      "dotenv_isolation_ok": meta["dotenv_isolation_ok"]}, ensure_ascii=False))
    return 0 if not meta["failed"] and meta["dotenv_isolation_ok"] else 1


# ---------------------------------------------------------------------------
# 比较与判定（冻结部分：执行方不得修改）
# ---------------------------------------------------------------------------

#: 各阶段允许新增的字段（只增不改）。其余字段必须逐字一致。
STAGE_NEW_FIELDS = {
    "self": set(),
    "DH-02": {"blocked_by", "missing_operands", "candidates", "next_action", "root_cause_key"},
    "DH-03a": {"blocked_by", "missing_operands", "candidates", "next_action", "root_cause_key",
               "legacy_coverage_terms"},
    "DH-03b": {"blocked_by", "missing_operands", "candidates", "next_action", "root_cause_key",
               "legacy_coverage_terms"},
}
#: 基线中已存在、允许由空变为非空的字段（非空基线值必须一致）。
FILLABLE_FIELDS = {"reason_codes", "required_operand_roles", "operand_sources", "cell_observations",
                   "disclosure_sources"}
#: 兼容文本：DH-02、DH-03a 必须逐字一致；DH-03b 允许变化。
COMPAT_TEXT_FIELDS = {"detail", "incomplete_reason", "unresolved_reasons"}
NEXT_ACTIONS = {"system_recheck", "tech_fix", "human_confirm", "rerun_ai"}
#: 方案 DH-02 第 1 条：由未完成回执汇总的义务，以及词表中有语义模型原因码的 AI 义务。
DH02_OBLIGATION_SCOPE = {"insufficient_data", "parse_ambiguity", "execution_error", "ai_not_run", "ai_failed"}


def _empty(value: Any) -> bool:
    return value in (None, "", [], {})


def _compare_dict(base: Dict[str, Any], cur: Dict[str, Any], stage: str, where: str,
                  problems: List[str], additions: List[str]) -> None:
    allow_new = STAGE_NEW_FIELDS[stage] if stage != "self" else set()
    for key, value in base.items():
        if key in COMPAT_TEXT_FIELDS and stage == "DH-03b":
            continue
        if key not in cur:
            problems.append(f"{where}: 字段 {key} 消失")
            continue
        if key in FILLABLE_FIELDS and stage != "self" and _empty(value):
            continue
        if canonical(value) != canonical(cur[key]):
            problems.append(f"{where}: 字段 {key} 改变：{canonical(value)[:160]} → {canonical(cur[key])[:160]}")
    for key in cur:
        if key not in base and key not in allow_new:
            additions.append(f"{where}: 新增未登记字段 {key}")


def _old_coverage_terms(detail: str) -> List[str]:
    """冻结的旧算法（与 2026-11-01 的 obligation_finding_coverage.distinctive_terms 一致）。"""
    generic = {"一般公共", "一般公共预算", "财政拨款", "年初预算", "决算数", "预算数", "基本支出", "项目支出",
               "合计", "本年", "上年", "三公经费", "机关运行经费", "支出决算", "收入决算"}
    terms = {m.group(1).strip() for m in re.finditer(r"[“「]([^”」]{2,40})[”」]", str(detail or ""))}
    return sorted(t for t in terms if len(t) >= 4 and t not in generic)


def compare_runs(base_dir: Path, cur_dir: Path, stage: str, kit: Path) -> Dict[str, Any]:
    checks: List[Dict[str, Any]] = []

    def add(check_id: str, title: str, ok: Optional[bool], details: Sequence[str] = (), level: str = "FAIL") -> None:
        verdict = "PASS" if ok else ("PENDING" if ok is None else level)
        checks.append({"id": check_id, "title": title, "verdict": verdict, "count": len(details),
                       "details": list(details)[:60]})

    bm, cm = read_json(base_dir / "meta.json"), read_json(cur_dir / "meta.json")
    vocabulary = set(read_json(kit / "reason_codes_v1.json")["codes"])

    # M：比较前提
    add("M1", "两次采集使用同一验收包", bm["kit_digest"] == cm["kit_digest"],
        [] if bm["kit_digest"] == cm["kit_digest"] else [f"{bm['kit_digest']} ≠ {cm['kit_digest']}"])
    add("M2", "样本集合与 sha256 一致", canonical(bm["samples"]) == canonical(cm["samples"]))
    add("M3", "冻结运行变量一致（含覆盖值）", canonical(bm["profile"]) == canonical(cm["profile"]),
        [f"{bm['profile']} ≠ {cm['profile']}"] if bm["profile"] != cm["profile"] else [])
    add("M4", "Python 版本与依赖清单一致",
        bm["runtime"]["python_version"] == cm["runtime"]["python_version"]
        and bm["runtime"]["packages_sha256"] == cm["runtime"]["packages_sha256"]
        and bm["runtime"]["package_count"] > 0)
    same_code = bm["code"]["digest"] == cm["code"]["digest"]
    if stage == "self":
        add("M5", "自比较：代码指纹相同", same_code)
    else:
        add("M5", "整改比较：代码指纹已变化", not same_code,
            ["代码指纹与基线相同，不能作为整改交付比较"] if same_code else [], level="REVIEW")
    add("M6", "两次采集均无失败样本、.env 均未进入运行环境",
        not bm["failed"] and not cm["failed"] and bm["dotenv_isolation_ok"] and cm["dotenv_isolation_ok"],
        [f"基线失败 {bm['failed']}", f"当前失败 {cm['failed']}"] if (bm["failed"] or cm["failed"]) else [])
    changed_env = sorted(name for name in set(bm["code_env_names"]) | set(cm["code_env_names"])
                         if bm["code_env_names"].get(name, "<unset>") != cm["code_env_names"].get(name, "<unset>")
                         and (name in bm["profile"] or name in cm["profile"]))
    add("M7", "代码读取的运行变量取值一致", not changed_env, changed_env)
    drift = [f"{label}: 采集期间代码指纹变化" for label, m in (("基线", bm), ("当前", cm))
             if m["code"]["digest"] != m.get("code_after")]
    add("M8", "采集期间代码未被改动", not drift, drift)
    writes = [f"{label}: {name}" for label, m in (("基线", bm), ("当前", cm)) for name in m.get("code_root_writes", [])]
    add("M9", "采集未写入代码根（含 data/、logs/）", not writes, writes, level="REVIEW")

    run_d, obl_d, add_d, rec_d, fnd_d, todo_d, cov_d, read_d, sum_d, led_d = ([] for _ in range(10))
    t_codes, t_noreason, t_missing, t_ambig, t_vocab, t_inst, t_terms, t_terms_eq, t_phrase, t_dump = (
        [] for _ in range(10))
    out_of_scope: List[str] = []
    unresolved_receipts = 0
    no_reason = 0
    for sample in bm["samples"]:
        sid = sample["sample_id"]
        bp, cp = base_dir / "records" / f"{sid}.json.gz", cur_dir / "records" / f"{sid}.json.gz"
        if not bp.exists() or not cp.exists():
            run_d.append(f"{sid}: 缺少采集记录")
            continue
        b, c = read_json_gz(bp), read_json_gz(cp)
        if canonical(b["run"]) != canonical(c["run"]):
            run_d.append(f"{sid}: {canonical(b['run'])[:200]} → {canonical(c['run'])[:200]}")
        if canonical(b["obligation_summary"]) != canonical(c["obligation_summary"]):
            sum_d.append(f"{sid}: 汇总改变")
        if not c["ledger_sources_equal"]:
            led_d.append(f"{sid}: check_coverage 与 result.meta.obligation_coverage 不一致")
        if set(b["obligations"]) != set(c["obligations"]):
            obl_d.append(f"{sid}: 义务集合改变 {sorted(set(b['obligations']) ^ set(c['obligations']))}")
        for oid, bi in b["obligations"].items():
            ci = c["obligations"].get(oid)
            if ci is None:
                continue
            if bi.get("status") != ci.get("status"):
                obl_d.append(f"{sid} {oid}: status {bi.get('status')} → {ci.get('status')}")
            _compare_dict(bi, ci, stage, f"{sid} {oid}", obl_d, add_d)
        for oid, bi in b["coverage_items"].items():
            ci = c["coverage_items"].get(oid)
            if ci is None:
                cov_d.append(f"{sid} {oid}: 覆盖块条目消失")
                continue
            _compare_dict(bi, ci, stage, f"{sid} coverage_item {oid}", cov_d, add_d)

        def rkey(r: Dict[str, Any]) -> str:
            return f"{r.get('obligation_id')}|{r.get('rule_id')}|{r.get('sub_check_id')}"

        def group(rs: Iterable[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
            out: Dict[str, List[Dict[str, Any]]] = {}
            for r in rs:
                out.setdefault(rkey(r), []).append(r)
            for key in out:
                out[key].sort(key=lambda r: canonical({k: r.get(k) for k in ("status", "formula", "operand_fact_ids")}))
            return out

        bg, cg = group(b["receipts"]), group(c["receipts"])
        if set(bg) != set(cg) or any(len(bg[k]) != len(cg.get(k, [])) for k in bg):
            rec_d.append(f"{sid}: 回执键集合或数量改变 {sorted(set(bg) ^ set(cg))[:6]}")
        for key, blist in bg.items():
            for index, (br, cr) in enumerate(zip(blist, cg.get(key, []))):
                if br.get("status") != cr.get("status"):
                    rec_d.append(f"{sid} {key}#{index}: status {br.get('status')} → {cr.get('status')}")
                _compare_dict(br, cr, stage, f"{sid} receipt {key}#{index}", rec_d, add_d)
        if sorted(b["findings"]) != sorted(c["findings"]):
            gone = sorted(set(b["findings"]) - set(c["findings"]))
            new = sorted(set(c["findings"]) - set(b["findings"]))
            fnd_d.append(f"{sid}: 正式问题指纹改变，消失 {len(gone)}、新增 {len(new)}；"
                         + "；".join(canonical(b['finding_bodies'].get(fp, {}))[:200] for fp in gone[:2]))
        for part in ("blocking_ids", "covered_by_finding", "queue_counts", "problems", "coverage_available"):
            if canonical(b["todo"].get(part)) != canonical(c["todo"].get(part)):
                todo_d.append(f"{sid}: 待办 {part} 改变：{canonical(b['todo'].get(part))[:200]} → "
                              f"{canonical(c['todo'].get(part))[:200]}")
        if canonical(b["reading_items"]) != canonical(c["reading_items"]):
            read_d.append(f"{sid}: 读取缺口改变")

        # 覆盖目标（只看当前采集）
        for r in c["receipts"]:
            if r.get("status") not in UNRESOLVED_RECEIPT:
                continue
            unresolved_receipts += 1
            codes = list(r.get("reason_codes") or [])
            where = f"{sid} {rkey(r)}"
            if not codes:
                t_codes.append(where)
            if "rule_no_reason" in codes:
                no_reason += 1
            if "operand_missing" in codes and _empty(r.get("missing_operands")):
                t_missing.append(where)
            if "operand_ambiguous" in codes and _empty(r.get("candidates")):
                t_ambig.append(where)
            unknown = sorted(set(codes) - vocabulary)
            if unknown:
                t_vocab.append(f"{where}: {unknown}")
            for field in ("incomplete_reason",):
                if "缺失或不唯一" in str(r.get(field) or ""):
                    t_phrase.append(f"{where} {field}")
            for text in r.get("unresolved_reasons") or []:
                if "缺失或不唯一" in str(text):
                    t_phrase.append(f"{where} unresolved_reasons")
        for oid, ci in c["obligations"].items():
            if ci.get("status") not in UNRESOLVED_OBLIGATION:
                continue
            where = f"{sid} {oid}"
            if ci.get("status") not in DH02_OBLIGATION_SCOPE:
                out_of_scope.append(f"{where}: {ci.get('status')}")
            elif ci.get("next_action") not in NEXT_ACTIONS or _empty(ci.get("root_cause_key")) \
                    or not isinstance(ci.get("blocked_by"), list) or _empty(ci.get("reason_codes")):
                t_inst.append(where)
            if "缺失或不唯一" in str(ci.get("detail") or ""):
                t_phrase.append(f"{where} detail")
            if ci.get("status") == "insufficient_data":
                terms = ci.get("legacy_coverage_terms", "<absent>")
                if not isinstance(terms, list):
                    t_terms.append(f"{where}: legacy_coverage_terms={terms!r}")
                else:
                    expected = _old_coverage_terms(b["obligations"].get(oid, {}).get("detail", ""))
                    if sorted(terms) != expected:
                        t_terms_eq.append(f"{where}: {sorted(terms)} ≠ 旧算法 {expected}")
                    dumped = c["coverage_items"].get(oid, {})
                    if dumped.get("legacy_coverage_terms") != terms:
                        t_dump.append(f"{where}: 覆盖块（接口层）未保留 legacy_coverage_terms")

    add("I1", "运行结论一致（状态、质量门、结论、复核原因码）", not run_d, run_d)
    add("I2", "义务集合与 status 一致；兼容文本与其余字段按阶段规则一致", not obl_d, obl_d)
    add("I3", "关系级回执键、数量、status 与字段按阶段规则一致", not rec_d, rec_d)
    add("I4", "正式问题完整指纹一致（去除冻结的易变字段）", not fnd_d, fnd_d)
    add("I5", "待办清单、同源覆盖判定、队列计数、问题集合一致", not todo_d, todo_d)
    add("I6", "覆盖块（接口层）条目字段一致", not cov_d, cov_d)
    add("I7", "义务汇总计数一致", not sum_d, sum_d)
    add("I8", "读取缺口一致", not read_d, read_d)
    add("I9", "两处台账一致（check_coverage 与 result.meta.obligation_coverage）", not led_d, led_d)
    add("I10", "未登记的新增字段", not add_d, add_d, level="REVIEW")

    if stage in {"DH-02", "DH-03a", "DH-03b"}:
        share = (no_reason / unresolved_receipts) if unresolved_receipts else 0.0
        add("T1", "未完成回执 100% 至少有一个原因码", not t_codes, t_codes)
        add("T2", f"rule_no_reason 占比 ≤ 10%（实际 {share:.1%}，{no_reason}/{unresolved_receipts}）", share <= 0.10)
        add("T3", "operand_missing 均带 missing_operands", not t_missing, t_missing)
        add("T4", "operand_ambiguous 均带 candidates", not t_ambig, t_ambig)
        add("T5", "原因码均在冻结词表 v1 内", not t_vocab, t_vocab)
        add("T6", "DH-02 范围内的未完成义务均有 reason_codes、blocked_by、next_action、root_cause_key",
            not t_inst, t_inst)
        checks.append({"id": "T6i", "title": "范围外的未完成义务（方案 DH-02 第 1 条未要求，仅报告）",
                       "verdict": "INFO", "count": len(out_of_scope), "details": out_of_scope[:60]})
    if stage in {"DH-03a", "DH-03b"}:
        add("T7", "取数不足义务均带列表形式的 legacy_coverage_terms", not t_terms, t_terms)
        add("T8", "legacy_coverage_terms 等于旧算法从基线 detail 取出的短语", not t_terms_eq, t_terms_eq)
        add("T9", "legacy_coverage_terms 经覆盖块（接口层）后不丢失", not t_dump, t_dump)
    if stage == "DH-03b":
        add("T10", "新输出不再出现“缺失或不唯一”", not t_phrase, t_phrase)

    verdicts = [c["verdict"] for c in checks]
    overall = "FAIL" if "FAIL" in verdicts else ("REVIEW" if "REVIEW" in verdicts else "PASS")
    return {"stage": stage, "baseline": str(base_dir), "current": str(cur_dir), "overall": overall, "checks": checks}


def compare_main(args: argparse.Namespace) -> int:
    kit = Path(args.kit).resolve()
    verify_kit(kit)
    report = compare_runs(Path(args.baseline).resolve(), Path(args.current).resolve(), args.stage, kit)
    if args.out:
        write_json(Path(args.out), report)
    for check in report["checks"]:
        print(f"[{check['verdict']:7}] {check['id']:4} {check['title']}" + (f"（{check['count']}）" if check["count"] else ""))
        for line in check["details"][:5]:
            print(f"           - {line}")
    print(f"总体：{report['overall']}")
    return 0 if report["overall"] == "PASS" else 1


# ---------------------------------------------------------------------------
# boundary
# ---------------------------------------------------------------------------

BOUNDARY_STAGES = ("baseline", "DH-02", "DH-03a", "DH-03b", "U-7")


def boundary_main(args: argparse.Namespace) -> int:
    kit = Path(args.kit).resolve()
    verify_kit(kit)
    code_root = Path(args.code_root).resolve()
    cases = read_json(kit / "boundary_cases.json")
    profile = read_json(kit / "runtime_profile.json")["env"]
    work = Path(tempfile.mkdtemp(prefix="dh-boundary-", dir=args.work_dir))
    results = []
    for case in cases["cases"]:
        case_dir = work / case["id"]
        for sub in ("home", "tmp", "org", "uploads"):
            (case_dir / sub).mkdir(parents=True)
        env = worker_env(profile, home=case_dir / "home", tmp=case_dir / "tmp", org_dir=case_dir / "org",
                         upload_dir=case_dir / "uploads")
        res = run_worker({"kind": "coverage_decision", "ledger": case["ledger"], "findings": case["findings"],
                          "visible": case["visible"], "dotenv_names": [], "pinned_names": sorted(profile)},
                         code_root=code_root, python=args.python, env=env, work=case_dir, timeout=300,
                         log_path=case_dir / "worker.log")
        target = case["target"]
        actual = (res.get("decision") or {}).get(target) if res.get("ok") else None
        expected = case["expect"].get(args.stage)
        if not res.get("ok") or actual is None:
            verdict = "ERROR"
        elif expected is None:
            verdict = "NOT_REQUIRED_YET"
        else:
            verdict = "PASS" if actual == expected else "FAIL"
        results.append({"id": case["id"], "title": case["title"], "stage": args.stage, "actual": actual,
                        "expected": expected, "verdict": verdict, "error": res.get("error")})
        print(f"[{verdict:16}] {case['id']} {case['title']}；实际 {actual}，本阶段期望 {expected}")
    if args.out:
        write_json(Path(args.out), {"stage": args.stage, "code_root": str(code_root), "results": results})
    if not args.keep_work:
        shutil.rmtree(work, ignore_errors=True)
    bad = [r for r in results if r["verdict"] in {"FAIL", "ERROR"}]
    return 1 if bad else 0


# ---------------------------------------------------------------------------
# manifest / seal / snapshot（验收方使用）
# ---------------------------------------------------------------------------

_YEAR_DIRS = ("2022", "2023", "2024", "2025")


def is_department_name(pdf: Path, year_dir: Path) -> bool:
    """分层用的部门层判定（只看名称，不代表材料口径）：文件名含“部门决算”，
    或文件名与所在主管部门文件夹同名（语料按主管部门分文件夹，如“上海市交通委员会/上海市交通委员会.pdf”）。
    “部门名+年度决算”多为本级单位决算，不计入部门层。"""
    stem = re.sub(r"\s+", "", pdf.stem)
    if "部门决算" in stem:
        return True
    parts = pdf.relative_to(year_dir).parts
    return len(parts) >= 2 and stem == re.sub(r"\s+", "", parts[0])


def manifest_main(args: argparse.Namespace) -> int:
    kit = ensure_new_dir(Path(args.out))
    uploads = Path(args.uploads).resolve()
    corpus = Path(args.corpus).expanduser().resolve()
    gold_csv = Path(args.gold_csv).resolve()
    import csv

    with gold_csv.open(encoding="utf-8-sig") as handle:
        gold = sorted({row["sha256"] for row in csv.DictReader(handle)})
    write_json(kit / "gold_exclusion.json", {"source": str(gold_csv), "source_sha256": sha256_file(gold_csv),
                                             "count": len(gold), "sha256": gold})
    (kit / "org").mkdir()
    for name in ("organizations.json", "job_org_links.json"):
        shutil.copy(Path(args.org_data_dir) / name, kit / "org" / name)
    org_ids = {str(o.get("id")) for o in read_json(kit / "org" / "organizations.json").get("organizations", [])}

    # R-A：当前清单版本的真实上传，剔除测试夹具与金标；同一 PDF 取最近一次任务的上传输入。
    by_sha: Dict[str, List[Tuple[float, Path, Dict[str, Any]]]] = {}
    for status_path in sorted(uploads.glob("*/status.json")):
        try:
            status = read_json(status_path)
        except Exception:  # noqa: BLE001
            continue
        if (status.get("check_coverage") or {}).get("catalog_version") != CATALOG_V12:
            continue
        if status.get("filename") in FIXTURE_FILENAMES or status.get("checksum") in gold:
            continue
        by_sha.setdefault(str(status.get("checksum")), []).append((float(status.get("ts") or 0), status_path.parent, status))
    ra: List[Dict[str, Any]] = []
    for index, sha in enumerate(sorted(by_sha, key=lambda s: sorted(str(p) for _, p, _ in by_sha[s])[0]), start=1):
        jobs = sorted(by_sha[sha], key=lambda item: (item[0], item[1].name))
        _, job_dir, status = jobs[-1]
        job_meta = read_json(job_dir / "job_meta.json") if (job_dir / "job_meta.json").exists() else {}
        filename = str(job_meta.get("filename") or status.get("filename"))
        pdf = job_dir / Path(str(status.get("saved_path") or filename)).name
        if not pdf.exists():
            pdf = job_dir / filename
        if sha256_file(pdf) != sha:
            refuse(f"R-A {job_dir.name} 的 PDF 与 status.checksum 不符")
        sid = f"RA-{index:03d}"
        (kit / "pdfs" / sid).mkdir(parents=True)
        shutil.copy(pdf, kit / "pdfs" / sid / filename)
        inputs = {key: job_meta.get(key, status.get(key)) for key in (
            "organization_id", "organization_name", "fiscal_year", "doc_type", "report_year", "report_kind", "filename")}
        inputs["organization_match_type"] = status.get("organization_match_type")
        inputs["organization_match_confidence"] = status.get("organization_match_confidence")
        inputs["report_year_source"] = status.get("report_year_source")
        ra.append({"sample_id": sid, "sha256": sha, "pdf": f"pdfs/{sid}/{filename}", "input_status": inputs,
                   "source": {"job_ids": [p.name for _, p, _ in jobs], "chosen_job_id": job_dir.name},
                   "org_id_in_snapshot": inputs.get("organization_id") in org_ids if inputs.get("organization_id") else None})
    write_json(kit / "manifest_RA.json", {"set": "RA", "rule": "uploads 中清单版本 v12 的任务，剔除测试夹具与金标；"
                                          "同一 PDF 取最近一次任务；输入取 job_meta.json 的上传字段",
                                          "count": len(ra), "samples": ra})

    # R-B：分层抽样（每年部门 dept_n + 单位 unit_n，固定种子），输入按上传链路推导一次后冻结。
    ra_sha = {s["sha256"] for s in ra}
    gold_set = set(gold)
    rb: List[Dict[str, Any]] = []
    profile = read_json(Path(args.profile))["env"]
    shutil.copy(Path(args.profile), kit / "runtime_profile.json")
    derivation_root = Path(tempfile.mkdtemp(prefix="dh-manifest-", dir=args.work_dir))
    stats: Dict[str, Any] = {}
    for year in _YEAR_DIRS:
        seen: Dict[str, Path] = {}
        for pdf in sorted((corpus / year).rglob("*")):
            if pdf.is_file() and pdf.suffix.lower() == ".pdf":
                seen.setdefault(sha256_file(pdf), pdf)
        pools: Dict[str, List[Tuple[str, Path]]] = {"dept": [], "unit": []}
        excluded = {"gold": 0, "in_ra": 0}
        for sha, pdf in sorted(seen.items(), key=lambda item: item[1].relative_to(corpus).as_posix()):
            if sha in gold_set:
                excluded["gold"] += 1
                continue
            if sha in ra_sha:
                excluded["in_ra"] += 1
                continue
            pools["dept" if is_department_name(pdf, corpus / year) else "unit"].append((sha, pdf))
        stats[year] = {"distinct_pdfs": len(seen), "excluded": excluded,
                       "pool": {k: len(v) for k, v in pools.items()}}
        for kind, wanted in (("dept", args.dept_n), ("unit", args.unit_n)):
            rng = random.Random(f"{args.seed}-{year}-{kind}")
            picked = rng.sample(pools[kind], min(wanted, len(pools[kind])))
            ordered = sorted(picked, key=lambda item: item[1].relative_to(corpus).as_posix())
            for number, (sha, pdf) in enumerate(ordered, start=1):
                sid = f"RB-{year}-{'D' if kind == 'dept' else 'U'}{number:02d}"
                (kit / "pdfs" / sid).mkdir(parents=True)
                shutil.copy(pdf, kit / "pdfs" / sid / pdf.name)
                rb.append({"sample_id": sid, "sha256": sha, "pdf": f"pdfs/{sid}/{pdf.name}",
                           "stratum": {"year": year, "layer_by_name": kind},
                           "source": {"corpus_path": pdf.relative_to(corpus).as_posix()}})

    def derive(sample: Dict[str, Any]) -> Dict[str, Any]:
        sid = sample["sample_id"]
        case = derivation_root / sid
        for sub in ("home", "tmp", "org", "uploads"):
            (case / sub).mkdir(parents=True)
        for name in ("organizations.json", "job_org_links.json"):
            shutil.copy(kit / "org" / name, case / "org" / name)
        job_dir = case / "uploads" / sid
        job_dir.mkdir()
        env = worker_env(profile, home=case / "home", tmp=case / "tmp", org_dir=case / "org",
                         upload_dir=case / "uploads")
        return run_worker({"kind": "upload_inputs", "pdf": str(kit / sample["pdf"]), "job_dir": str(job_dir),
                           "dotenv_names": [], "pinned_names": sorted(profile)},
                          code_root=REPO_ROOT, python=args.python, env=env, work=case, timeout=600,
                          log_path=case / "worker.log")

    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        derived = list(pool.map(derive, rb))
    for sample, res in zip(rb, derived):
        if not res.get("ok"):
            refuse(f"{sample['sample_id']} 输入推导失败：{res.get('error')}")
        sample["input_status"] = res["inputs"]
        org_id = res["inputs"].get("organization_id")
        sample["org_id_in_snapshot"] = (org_id in org_ids) if org_id else None
    shutil.rmtree(derivation_root, ignore_errors=True)
    write_json(kit / "manifest_RB.json", {
        "set": "RB", "corpus": str(corpus), "seed": args.seed, "per_year": {"dept": args.dept_n, "unit": args.unit_n},
        "rule": "按 sha256 去重；剔除金标与 R-A 已有的 PDF；按 is_department_name 分部门层与单位层，"
                "仅用于分层抽样，材料口径以封面为准；输入按上传链路（未手选组织）推导一次后冻结；"
                "自动归属失败的样本保留（代表上传未绑定组织的情形）",
        "derivation_code": code_fingerprint(REPO_ROOT)["digest"], "stats": stats, "count": len(rb), "samples": rb})
    print(json.dumps({"kit": str(kit), "RA": len(ra), "RB": len(rb), "stats": stats}, ensure_ascii=False))
    return 0


def seal_main(args: argparse.Namespace) -> int:
    digest = seal_kit(Path(args.kit).resolve())
    print(f"SHA256SUMS 已生成；验收包身份 = {digest}")
    return 0


def snapshot_main(args: argparse.Namespace) -> int:
    """把代码根中被跟踪与未被忽略的文件精确复制到新目录（不含 .env）。复制前后各取一次指纹，变化即失败。"""
    source = Path(args.source).resolve()
    dest = ensure_new_dir(Path(args.out))

    def listing() -> List[str]:
        out = subprocess.run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                             cwd=source, capture_output=True).stdout.decode("utf-8").split("\0")
        keep = []
        for name in out:
            if not name:
                continue
            top = name.split("/", 1)[0]
            if (top in CODE_DIRS or name in CODE_ROOT_FILES) and not any(
                    part in SNAPSHOT_EXCLUDE_NAMES for part in Path(name).parts) and (source / name).is_file():
                keep.append(name)
        return sorted(keep)

    before = {name: sha256_file(source / name) for name in listing()}
    for name in before:
        target = dest / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / name, target)
    after = {name: sha256_file(source / name) for name in listing()}
    copied = {name: sha256_file(dest / name) for name in before}
    drift = sorted(set(before) ^ set(after)) + sorted(n for n in before if n in after and before[n] != after[n])
    mismatch = sorted(n for n in before if copied[n] != before[n])
    git = lambda *a: subprocess.run(["git", *a], cwd=source, capture_output=True, text=True).stdout  # noqa: E731
    porcelain = git("status", "--porcelain")
    meta = {"source": {"path": str(source), "git_head": git("rev-parse", "HEAD").strip(),
                       "git_status_entries": len([l for l in porcelain.splitlines() if l.strip()]),
                       "git_status_sha256": sha256_text(porcelain), "taken_at": time.strftime("%Y-%m-%d %H:%M:%S %z")},
            "file_count": len(before), "files": before, "drift_during_copy": drift, "copy_mismatch": mismatch}
    write_json(dest / "SNAPSHOT.json", meta)
    (dest / "SNAPSHOT.sha256").write_text("".join(f"{h}  {n}\n" for n, h in sorted(before.items())), encoding="utf-8")
    print(json.dumps({"snapshot": str(dest), "files": len(before), "drift": drift[:10], "mismatch": mismatch[:10]},
                     ensure_ascii=False))
    return 0 if not drift and not mismatch else 1


# ---------------------------------------------------------------------------
# selftest
# ---------------------------------------------------------------------------


def _mutate(src: Path, dst: Path, sample_id: str, fn) -> None:
    shutil.copytree(src, dst)
    path = dst / "records" / f"{sample_id}.json.gz"
    record = read_json_gz(path)
    fn(record)
    write_json_gz(path, record)


def selftest_main(args: argparse.Namespace) -> int:
    kit = Path(args.kit).resolve()
    out = ensure_new_dir(Path(args.out))
    me = [sys.executable, str(Path(__file__).resolve())]
    results: List[Dict[str, Any]] = []

    def expect(name: str, cmd: List[str], want_code: Optional[int], want_text: str = "") -> subprocess.CompletedProcess:
        proc = subprocess.run(cmd, capture_output=True, text=True)
        text = proc.stdout + proc.stderr
        ok = (want_code is None or proc.returncode == want_code) and (want_text in text)
        results.append({"name": name, "ok": ok, "returncode": proc.returncode, "want": want_code,
                        "want_text": want_text, "tail": text[-1500:]})
        print(f"[{'PASS' if ok else 'FAIL'}] {name}", flush=True)
        return proc

    common = ["--kit", str(kit), "--code-root", args.code_root, "--sets", args.sets, "--only", args.only,
              "--jobs", str(args.jobs), "--python", args.python]
    run1, run2 = out / "run1", out / "run2"
    expect("S1a 首次采集成功", [*me, "collect", *common, "--out", str(run1), "--label", "selftest-1"], 0)
    expect("S1b 同代码再次采集成功", [*me, "collect", *common, "--out", str(run2), "--label", "selftest-2"], 0)
    expect("S1c 同代码两次采集逐项一致（确定性）",
           [*me, "compare", "--kit", str(kit), "--baseline", str(run1), "--current", str(run2), "--stage", "self",
            "--out", str(out / "S1c.json")], 0, "总体：PASS")
    first = args.only.split(",")[0]

    def first_unresolved(record: Dict[str, Any]) -> str:
        return next(oid for oid, item in record["obligations"].items() if item.get("status") in UNRESOLVED_OBLIGATION)

    mutations = {
        "S2a 义务 status 改变": ("I2", lambda r: r["obligations"][first_unresolved(r)].update(status="completed")),
        "S2b 兼容文本改一个字": ("I2", lambda r: r["obligations"][first_unresolved(r)].update(
            detail=str(r["obligations"][first_unresolved(r)].get("detail")) + "。")),
        "S2c 正式问题指纹改变": ("I4", lambda r: r["findings"].append("0" * 64)),
        "S2d 待办清单少一条": ("I5", lambda r: r["todo"]["blocking_ids"].pop() if r["todo"]["blocking_ids"]
                              else r["todo"]["blocking_ids"].append("OBL-FAKE")),
        "S2e 同源判定翻转": ("I5", lambda r: r["todo"]["covered_by_finding"].update(
            {k: (not v) for k, v in list(r["todo"]["covered_by_finding"].items())[:1]} or {"OBL-FAKE": True})),
        "S2f 回执 status 改变": ("I3", lambda r: next(x for x in r["receipts"] if x.get("status") in UNRESOLVED_RECEIPT)
                               .update(status="pass")),
        "S2g 新增未登记字段": ("I10", lambda r: r["obligations"][first_unresolved(r)].update(unexpected_field=1)),
    }
    for index, (name, (check_id, fn)) in enumerate(mutations.items()):
        mutated = out / f"mut{index}"
        _mutate(run2, mutated, first, fn)
        verdict = "REVIEW" if check_id == "I10" else "FAIL"
        expect(f"{name} → 总体不通过", [*me, "compare", "--kit", str(kit), "--baseline", str(run1),
                                     "--current", str(mutated), "--stage", "self"], 1, f"总体：{verdict}")
        report = compare_runs(run1, mutated, "self", kit)
        hit = next((c for c in report["checks"] if c["id"] == check_id), None)
        ok = bool(hit and hit["verdict"] == verdict)
        results.append({"name": f"{name}：命中 {check_id}", "ok": ok, "detail": hit})
        print(f"[{'PASS' if ok else 'FAIL'}] {name}：命中 {check_id}", flush=True)
    # 运行条件变化：直接改 meta
    mutated = out / "mut_profile"
    shutil.copytree(run2, mutated)
    meta = read_json(mutated / "meta.json")
    meta["profile"]["OCR_ENABLED"] = "true"
    write_json(mutated / "meta.json", meta)
    report = compare_runs(run1, mutated, "self", kit)
    m3 = next(c for c in report["checks"] if c["id"] == "M3")
    results.append({"name": "S2h 运行变量不同 → M3 判 FAIL", "ok": m3["verdict"] == "FAIL", "detail": m3})
    print(f"[{'PASS' if m3['verdict'] == 'FAIL' else 'FAIL'}] S2h 运行变量不同 → M3 判 FAIL", flush=True)

    # 拒绝条件
    expect("S3a 输出目录已存在 → 拒绝", [*me, "collect", *common, "--out", str(run1)], 2, "输出目录已存在")
    tampered = out / "kit_tampered"
    shutil.copytree(kit, tampered)
    with (tampered / "runtime_profile.json").open("a", encoding="utf-8") as handle:
        handle.write(" ")
    expect("S3b 验收包被改动 → 拒绝", [*me, "collect", *[a if a != str(kit) else str(tampered) for a in common],
                                    "--out", str(out / "never1")], 2, "验收包文件哈希不符")
    goldkit = out / "kit_gold"
    shutil.copytree(kit, goldkit)
    gold = read_json(goldkit / "gold_exclusion.json")
    sample_sha = next(s["sha256"] for s in read_json(goldkit / "manifest_RA.json")["samples"] if s["sample_id"] == first) \
        if first.startswith("RA-") else next(s["sha256"] for s in read_json(goldkit / "manifest_RB.json")["samples"]
                                             if s["sample_id"] == first)
    gold["sha256"].append(sample_sha)
    write_json(goldkit / "gold_exclusion.json", gold)
    seal_kit(goldkit)
    expect("S3c 样本命中金标排除清单 → 拒绝", [*me, "collect", *[a if a != str(kit) else str(goldkit) for a in common],
                                          "--out", str(out / "never2")], 2, "金标排除清单")
    badpdf = out / "kit_badpdf"
    shutil.copytree(kit, badpdf)
    manifest_name = "manifest_RA.json" if first.startswith("RA-") else "manifest_RB.json"
    target = next(s for s in read_json(badpdf / manifest_name)["samples"] if s["sample_id"] == first)
    with (badpdf / target["pdf"]).open("ab") as handle:
        handle.write(b"\n%tamper\n")
    seal_kit(badpdf)
    expect("S3d PDF 与登记哈希不符 → 拒绝", [*me, "collect", *[a if a != str(kit) else str(badpdf) for a in common],
                                         "--out", str(out / "never3")], 2, "sha256 不符")
    for path in (tampered, goldkit, badpdf):
        shutil.rmtree(path, ignore_errors=True)

    # 模板开关实验：无数据库时 GBC_TEMPLATE_SCOPE=1 是否改变结果（只报告，不判 PASS/FAIL）
    run_scope = out / "run_scope1"
    subprocess.run([*me, "collect", *common, "--out", str(run_scope), "--label", "selftest-scope1",
                    "--profile-override", "GBC_TEMPLATE_SCOPE=1"], capture_output=True, text=True)
    scope_report = compare_runs(run1, run_scope, "self", kit) if (run_scope / "meta.json").exists() else None
    if scope_report:
        differing = [c for c in scope_report["checks"] if c["verdict"] != "PASS" and c["id"] != "M3"]
        results.append({"name": "S4 模板开关实验（报告项）", "ok": None,
                        "detail": {"non_pass_except_M3": differing}})
        print(f"[INFO] S4 模板开关实验：除 M3 外不一致的检查 {[c['id'] for c in differing]}", flush=True)

    if args.equivalence_root:
        # 快照完整性：同一份代码在原仓库与快照上运行，结果必须逐项一致（证明快照没有漏掉依赖）。
        run_eq = out / "run_equivalence"
        eq_common = [a if a != args.code_root else args.equivalence_root for a in common]
        expect("S6a 在原仓库上采集成功", [*me, "collect", *eq_common, "--out", str(run_eq), "--label", "selftest-eq"], 0)
        expect("S6b 原仓库与快照结果逐项一致", [*me, "compare", "--kit", str(kit), "--baseline", str(run1),
                                          "--current", str(run_eq), "--stage", "self",
                                          "--out", str(out / "S6b.json")], 0, "总体：PASS")
    expect("S5a 边界用例：当前代码满足 baseline 期望", [*me, "boundary", "--kit", str(kit), "--code-root",
                                                  args.code_root, "--stage", "baseline", "--python", args.python,
                                                  "--out", str(out / "boundary_baseline.json")], 0)
    for stage in ("DH-03a", "U-7"):
        # 当前代码尚未实施 DH-03a / U-7，这两组只记录实际结果，证明用例能区分新旧行为。
        proc = subprocess.run([*me, "boundary", "--kit", str(kit), "--code-root", args.code_root, "--stage", stage,
                               "--python", args.python, "--out", str(out / f"boundary_{stage}.json")],
                              capture_output=True, text=True)
        results.append({"name": f"S5 边界用例（{stage} 期望，报告项）", "ok": None, "tail": proc.stdout[-2000:]})
        print(f"[INFO] S5 边界用例（{stage} 期望）\n{proc.stdout}", flush=True)
    write_json(out / "selftest.json", results)
    hard = [r for r in results if r["ok"] is False]
    print(f"自测：{len(results)} 项，失败 {len(hard)} 项")
    return 0 if not hard else 1


# ---------------------------------------------------------------------------


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    w = sub.add_parser("_worker")
    w.add_argument("--code-root", required=True)
    w.add_argument("--task", required=True)
    w.add_argument("--result", required=True)

    m = sub.add_parser("manifest")
    m.add_argument("--out", default=str(DEFAULT_KIT))
    m.add_argument("--uploads", default=str(REPO_ROOT / "uploads"))
    m.add_argument("--corpus", default="~/Desktop/预决算信息收集/决算测试上海市")
    m.add_argument("--gold-csv", default=str(REPO_ROOT / "docs" / "GOLD_STANDARD_SAMPLE_20261101.csv"))
    m.add_argument("--org-data-dir", default=str(REPO_ROOT / "data"))
    m.add_argument("--profile", required=True, help="待冻结的 runtime_profile.json")
    m.add_argument("--seed", default="20261101")
    m.add_argument("--dept-n", type=int, default=6)
    m.add_argument("--unit-n", type=int, default=24)
    m.add_argument("--python", default=sys.executable)
    m.add_argument("--jobs", type=int, default=4)
    m.add_argument("--work-dir", default=None)

    s = sub.add_parser("seal")
    s.add_argument("--kit", default=str(DEFAULT_KIT))

    sn = sub.add_parser("snapshot")
    sn.add_argument("--source", default=str(REPO_ROOT))
    sn.add_argument("--out", required=True)

    c = sub.add_parser("collect")
    c.add_argument("--kit", default=str(DEFAULT_KIT))
    c.add_argument("--code-root", required=True)
    c.add_argument("--out", required=True)
    c.add_argument("--sets", default="RA,RB")
    c.add_argument("--only", default="")
    c.add_argument("--jobs", type=int, default=4)
    c.add_argument("--timeout", type=int, default=900)
    c.add_argument("--python", default=sys.executable)
    c.add_argument("--label", default="")
    c.add_argument("--keep-work", action="store_true")
    c.add_argument("--profile-override", action="append", help="仅供自测实验；会写入 meta 并使 M3 判 FAIL")

    k = sub.add_parser("compare")
    k.add_argument("--kit", default=str(DEFAULT_KIT))
    k.add_argument("--baseline", required=True)
    k.add_argument("--current", required=True)
    k.add_argument("--stage", choices=sorted(STAGE_NEW_FIELDS), required=True)
    k.add_argument("--out", default="")

    b = sub.add_parser("boundary")
    b.add_argument("--kit", default=str(DEFAULT_KIT))
    b.add_argument("--code-root", required=True)
    b.add_argument("--stage", choices=BOUNDARY_STAGES, required=True)
    b.add_argument("--python", default=sys.executable)
    b.add_argument("--out", default="")
    b.add_argument("--work-dir", default=None)
    b.add_argument("--keep-work", action="store_true")

    t = sub.add_parser("selftest")
    t.add_argument("--kit", default=str(DEFAULT_KIT))
    t.add_argument("--code-root", required=True)
    t.add_argument("--out", required=True)
    t.add_argument("--sets", default="RA,RB")
    t.add_argument("--only", required=True)
    t.add_argument("--jobs", type=int, default=3)
    t.add_argument("--python", default=sys.executable)
    t.add_argument("--equivalence-root", default="", help="快照的来源仓库；给出时加测 S6 快照完整性")

    args = parser.parse_args(argv)
    handlers = {"_worker": worker_main, "manifest": manifest_main, "seal": seal_main, "snapshot": snapshot_main,
                "collect": collect_main, "compare": compare_main, "boundary": boundary_main,
                "selftest": selftest_main}
    return handlers[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
