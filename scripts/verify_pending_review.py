#!/usr/bin/env python3
"""待核治理第一阶段验收工具（验收包 v1-regression）。

方案：``docs/待核治理实施方案_20261101.md``（R2.1）；验收包说明：
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
import ast
import contextlib
import copy
import gzip
import hashlib
import importlib.util
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
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

KIT_VERSION = "v1-regression-r2"
META_SCHEMA_VERSION = 3
REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_KIT = REPO_ROOT / "outputs" / "pending_review_20261101" / "acceptance_kit" / KIT_VERSION
if (REPO_ROOT / "rule_identity_dimensions.json").is_file() and (REPO_ROOT / "manifest_RA.json").is_file():
    # 封包内的 tools/ 副本自包含定位验收包，避免在线工作区改写工具影响长时验证。
    DEFAULT_KIT = REPO_ROOT
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
    if not (kit / "rule_identity_dimensions.json").is_file():
        refuse("封包前必须导出规则—必需身份维度对照表")
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
    if not sets or len(sets) != len(set(sets)) or set(sets) - {"RA", "RB"}:
        refuse("样本集必须是非空且不重复的 RA/RB 列表")
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
    if not samples:
        refuse("样本集合为空，零样本不能用于采集或验收")
    return samples


def validate_pipeline_status(status: Any) -> List[str]:
    """函数正常返回不等于分析成功；失败终态和空台账不能作为等价基线。"""
    if not isinstance(status, dict):
        return ["status.json 不是对象"]
    errors = []
    if status.get("status") not in {"done", "review_required"}:
        errors.append(f"无效分析终态：{status.get('status')!r}")
    if status.get("analysis_conclusion") not in {"findings_detected", "no_findings", "incomplete"}:
        errors.append(f"无效分析结论：{status.get('analysis_conclusion')!r}")
    if status.get("error") not in (None, ""):
        errors.append("管线仍带失败原因")
    ledger = status.get("check_coverage")
    result = status.get("result")
    report = status.get("review_report")
    if not isinstance(ledger, dict) or ledger.get("catalog_version") != CATALOG_V12:
        errors.append("缺少指定版本的检查台账")
    for label, items, key in (
        ("检查台账", ledger.get("instances") if isinstance(ledger, dict) else None, "obligation_id"),
        ("关系级回执", report.get("check_receipts") if isinstance(report, dict) else None, None),
    ):
        if not isinstance(items, list) or not items or any(not isinstance(x, dict) for x in items):
            errors.append(f"{label}缺失、为空或结构无效")
        elif key:
            ids = [x.get(key) for x in items]
            if any(not isinstance(x, str) or not x for x in ids) or len(ids) != len(set(ids)):
                errors.append(f"{label}身份缺失或重复")
    meta = result.get("meta") if isinstance(result, dict) else None
    projection = meta.get("obligation_coverage") if isinstance(meta, dict) else None
    if not isinstance(projection, dict) or not projection.get("instances"):
        errors.append("缺少 result.meta.obligation_coverage")
    return errors


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


#: 验收工具自身由验收包 SHA256SUMS 单独校验，不计入被测代码指纹（否则工具修订会被误当成代码变化）。
CODE_FINGERPRINT_EXCLUDE = {"scripts/verify_pending_review.py"}


def code_fingerprint(code_root: Path) -> Dict[str, Any]:
    files: Dict[str, str] = {}
    for directory in CODE_DIRS:
        base = code_root / directory
        if not base.exists():
            continue
        for path in sorted(base.rglob("*")):
            rel = path.relative_to(code_root).as_posix()
            if (path.is_file() and path.suffix in CODE_SUFFIXES and rel not in CODE_FINGERPRINT_EXCLUDE
                    and not any(part in SNAPSHOT_EXCLUDE_NAMES for part in path.parts)):
                files[rel] = sha256_file(path)
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
        info["git_status_entries"] = len([line for line in porcelain.splitlines() if line.strip()])
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
    # 工具自己设置的变量（冻结 profile + 按运行目录指定的路径）即使与 .env 同名也不算泄漏。
    harness_set = set(task.get("pinned_names", [])) | RUNTIME_SET_ENV
    leaked = sorted(name for name in task.get("dotenv_names", []) if name in os.environ and name not in harness_set)
    try:
        if task["kind"] == "pipeline":
            job_dir = Path(task["job_dir"])
            started = time.time()
            adapter_run_pipeline(job_dir)
            elapsed = time.time() - started
            status = read_json(job_dir / "status.json")
            invalid = validate_pipeline_status(status)
            if invalid:
                raise ValueError("采集结果无效：" + "；".join(invalid))
            todo = adapter_todo(job_dir.name, status)
            leaked = sorted(set(leaked) | {name for name in task.get("dotenv_names", [])
                                           if name in os.environ and name not in harness_set})
            out.update(ok=True, elapsed_s=round(elapsed, 2), todo=todo)
        elif task["kind"] == "upload_inputs":
            out.update(ok=True, inputs=adapter_upload_inputs(Path(task["pdf"]), Path(task["job_dir"])))
        elif task["kind"] == "coverage_decision":
            out.update(ok=True, decision=adapter_coverage_decision(task["ledger"], task["findings"], task["visible"]))
        elif task["kind"] == "terms":
            from src.services.obligation_finding_coverage import distinctive_terms

            out.update(ok=True, terms=[sorted(distinctive_terms(text)) for text in task["texts"]])
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
        result = read_json(result_path)
        if proc.returncode != 0:
            result.update(ok=False, error=result.get("error") or f"worker exited {proc.returncode}")
        return result
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


def _identity_context(status, meta):
    profile = status.get("document_profile") or meta.get("document_profile") or {}
    scope = profile.get("writing_scope") or {}
    return {"kind": profile.get("kind") if profile.get("kind") in {"final", "budget"} else "unknown",
            "domain": scope.get("domain") if scope.get("domain") in {"final", "budget"} else "unknown"}


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
        "identity_context": _identity_context(status, meta),
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
    contract = _identity_contract(kit)
    if sha256_file(code_root / contract["source_file"]) != contract["source_sha256"]:
        refuse("规则身份维度声明与冻结对照表不符，须重新导出、封包并采集基线")
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
        "schema_version": META_SCHEMA_VERSION,
        "kit_version": KIT_VERSION, "kit_digest": kit_digest, "label": args.label,
        "sample_scope": "full" if set(sets) == {"RA", "RB"} and not only else "partial",
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
#: reason_codes 在台账实例上已存在（按可填充字段处理），在覆盖块条目上是新字段，故也登记为可新增。
STAGE_NEW_FIELDS = {
    "self": set(),
    "DH-02": {"reason_codes", "blocked_by", "missing_operands", "candidates", "next_action", "root_cause_key"},
    "DH-03a": {"reason_codes", "blocked_by", "missing_operands", "candidates", "next_action", "root_cause_key",
               "legacy_coverage_terms"},
    "DH-03b": {"reason_codes", "blocked_by", "missing_operands", "candidates", "next_action", "root_cause_key",
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


def _contains_all(base: Any, cur: Any) -> bool:
    """可填充字段的“只增”判定：列表按多重集包含，字典按键值包含。"""
    if isinstance(base, list) and isinstance(cur, list):
        remaining = [canonical(v) for v in cur]
        for item in base:
            key = canonical(item)
            if key not in remaining:
                return False
            remaining.remove(key)
        return True
    if isinstance(base, dict) and isinstance(cur, dict):
        return all(k in cur and canonical(v) == canonical(cur[k]) for k, v in base.items())
    return False


def _compare_dict(base: Dict[str, Any], cur: Dict[str, Any], stage: str, where: str,
                  problems: List[str], additions: List[str]) -> None:
    allow_new = STAGE_NEW_FIELDS[stage] if stage != "self" else set()
    for key, value in base.items():
        if key in COMPAT_TEXT_FIELDS and stage == "DH-03b":
            continue
        if key not in cur:
            problems.append(f"{where}: 字段 {key} 消失")
            continue
        if key in FILLABLE_FIELDS and stage != "self":
            if _empty(value):
                continue
            if _contains_all(value, cur[key]):
                continue  # 只增：基线已有的元素全部保留，允许追加
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


def _frozen_samples(kit: Path) -> Dict[str, str]:
    expected: Dict[str, str] = {}
    gold = set(read_json(kit / "gold_exclusion.json")["sha256"])
    for name in ("RA", "RB"):
        manifest = read_json(kit / f"manifest_{name}.json")
        samples = manifest["samples"]
        if not samples or manifest["count"] != len(samples):
            raise ValueError(f"冻结清单 {name} 为空或计数不符")
        for sample in samples:
            sid, sha = sample["sample_id"], sample["sha256"]
            if sid in expected or not isinstance(sid, str) or not sid:
                raise ValueError("冻结清单样本身份重复或无效")
            if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{64}", sha) or sha in gold:
                raise ValueError(f"冻结清单 {sid} 哈希无效或命中金标")
            expected[sid] = sha
    if len(set(expected.values())) != len(expected):
        raise ValueError("冻结清单包含重复 PDF")
    return expected


def _run_records(root: Path, meta: Dict[str, Any], expected: Dict[str, str], allow_partial: bool):
    """先验证采集范围与记录有效性，避免空集合、错文件和重复失败被当成一致。"""
    identity_errors, validity_errors = [], []
    declared: Dict[str, str] = {}
    samples = meta.get("samples")
    if not isinstance(samples, list) or not samples:
        identity_errors.append("样本集合为空或无效")
    else:
        for item in samples:
            if not isinstance(item, dict) or not isinstance(item.get("sample_id"), str):
                identity_errors.append("样本身份结构无效")
                continue
            sid, sha = item["sample_id"], item.get("sha256")
            if sid in declared or sid not in expected or sha != expected[sid]:
                identity_errors.append(f"{sid}: 样本重复或与冻结清单身份不符")
            declared[sid] = sha
    if meta.get("schema_version") != META_SCHEMA_VERSION:
        identity_errors.append("采集元数据版本不符，旧产物不得冒充新包采集")
    if not allow_partial and (declared != expected or meta.get("sample_scope") != "full"):
        identity_errors.append(f"正式验收须使用冻结全量样本：实际 {len(declared)}，应有 {len(expected)}")
    if meta.get("sample_scope") not in {"full", "partial"}:
        identity_errors.append("缺少明确的样本范围标记")
    entries = meta.get("entries") if isinstance(meta.get("entries"), list) else []
    entry_ids = [x.get("sample_id") for x in entries if isinstance(x, dict)]
    if any(not _nonblank(x) for x in entry_ids) or len(entry_ids) != len(declared) \
            or len(entry_ids) != len(set(entry_ids)) or set(entry_ids) != set(declared):
        validity_errors.append("采集回执与样本集合不符")
    for item in entries or []:
        if not isinstance(item, dict) or item.get("ok") is not True or item.get("dotenv_leaked") \
                or item.get("env_seen_matches_profile") is not True:
            validity_errors.append("存在失败采集回执或环境隔离失败")
    paths = {p.name[:-8]: p for p in (root / "records").glob("*.json.gz")}
    if set(paths) != set(declared):
        identity_errors.append("记录文件集合与样本集合不符")
    records = {}
    for sid, path in paths.items():
        try:
            row = read_json_gz(path)
            if row.get("sample_id") != sid or row.get("sha256") != declared.get(sid):
                identity_errors.append(f"{sid}: 记录内部 sample_id/sha256 不符")
            for field in ("run", "obligation_summary", "obligations", "coverage_items", "todo", "finding_bodies"):
                if not isinstance(row.get(field), dict):
                    raise ValueError(f"{field} 不是对象")
            for field in ("receipts", "findings", "reading_items"):
                if not isinstance(row.get(field), list):
                    raise ValueError(f"{field} 不是列表")
            context = row.get("identity_context")
            if not isinstance(context, dict) or any(context.get(k) not in {"final", "budget", "unknown"} for k in ("kind", "domain")):
                raise ValueError("缺少有效的规则身份上下文")
            if row["run"].get("status") not in {"done", "review_required"} \
                    or row["run"].get("analysis_conclusion") not in {"findings_detected", "no_findings", "incomplete"} \
                    or not _empty(row["run"].get("error")):
                raise ValueError("管线终态或分析结论无效")
            if row["obligation_summary"].get("catalog_version") != CATALOG_V12 \
                    or not row["obligations"] or not row["coverage_items"] or not row["receipts"] \
                    or row["todo"].get("coverage_available") is not True:
                raise ValueError("缺少指定版本的有效台账、回执或覆盖块")
            for field in ("obligations", "coverage_items"):
                if any(not isinstance(v, dict) or v.get("obligation_id") != k for k, v in row[field].items()):
                    raise ValueError(f"{field} 条目身份无效")
            if any(not isinstance(v, dict) for v in row["receipts"]):
                raise ValueError("关系级回执结构无效")
            records[sid] = row
        except (ValueError, TypeError, KeyError, AttributeError, OSError, EOFError) as exc:
            validity_errors.append(f"{sid}: {exc}")
    return declared, records, identity_errors, validity_errors


def _nonblank(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def build_identity_contract(code_root: Path) -> Dict[str, Any]:
    """从规则声明导出，不复制通用维度清单；同时执行实际上下文条件分支。"""
    source = code_root / "src/engine/rule_identity.py"
    spec = importlib.util.spec_from_file_location("pending_frozen_identity", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    ids = set(module.INTERNAL_RELATION_RULES) | set(module.LEGACY_TABLE_INPUTS)
    for path in (code_root / "src/engine").glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                    and re.fullmatch(r"(?:V33|CMM|BUD)-[A-Z0-9-]+", node.value):
                ids.add(node.value)
    rules = {}
    for rid in sorted(ids):
        variants = {}
        for kind in ("final", "budget", "unknown"):
            for domain in ("final", "budget", "unknown"):
                doc = SimpleNamespace(document_profile=SimpleNamespace(kind=kind, writing_scope={"domain": domain}),
                                      fact_context=None, parse_artifact=None)
                with module.rule_identity_requirements(doc, rid):
                    variants[f"{kind}|{domain}"] = list(module.ACTIVE_IDENTITY.get())
        tables = module.LEGACY_TABLE_INPUTS.get(rid)
        rules[rid] = {"required_dimensions": list(module.required_identity_dimensions(rid)), "contexts": variants,
                      "legacy_input_declared": rid in module.LEGACY_TABLE_INPUTS,
                      "input_tables": sorted(tables) if tables is not None else None}
    upstream = {
        "subject_unconfirmed": ["organization"], "subject_conflict": ["organization"],
        "scope_unconfirmed": ["org_scope", "fund_scope", "budget_stage", "year"],
        "table_missing": ["instance_uid"], "table_mapping_incomplete": ["instance_uid"],
        "row_code_missing": ["row"], "row_identity_duplicate": ["row"], "row_level_unresolved": ["row"],
        "column_identity_unconfirmed": ["column"], "column_width_drift": ["column"],
        "cell_blank_semantics_unknown": ["cell"], "cell_low_confidence": ["cell"], "cell_unbound": ["cell"],
    }
    return {"version": 1, "source_file": "src/engine/rule_identity.py", "source_sha256": sha256_file(source),
            "rules": rules, "structural_dimensions": ["row", "column", "cell"],
            "upstream_reason_dimensions": upstream,
            "root_priority": {"organization": 0, "year": 1, "fund_scope": 1, "budget_stage": 1,
                              "org_scope": 1, "instance_uid": 2, "row": 3, "column": 4, "cell": 5}}


def _identity_contract(kit: Path) -> Dict[str, Any]:
    path = kit / "rule_identity_dimensions.json"
    if not path.is_file():
        refuse("验收包缺少冻结的规则—必需身份维度对照表")
    return read_json(path)


def _required_dimensions(contract: Dict[str, Any], rule_id: str, context: Dict[str, Any]) -> Set[str]:
    if not _nonblank(rule_id):
        raise ValueError("回执缺少规则编号")
    rule = contract["rules"].get(rule_id)
    if rule is None:
        raise ValueError(f"规则 {rule_id!r} 不在冻结维度表中")
    key = f"{context.get('kind', 'unknown')}|{context.get('domain', 'unknown')}"
    return set(rule["contexts"].get(key, rule["required_dimensions"])) | set(contract["structural_dimensions"])


def _root_signature(item: Dict[str, Any], contract: Dict[str, Any]) -> str:
    blockers = [b for b in item.get("blocked_by", []) if isinstance(b, dict)] if isinstance(item.get("blocked_by"), list) else []
    if blockers:
        # 同层多阻断时采用冻结的字典序选主根因，其他阻断仍完整保留。
        points = [{k: b.get(k) for k in ("dimension", "ref", "reason_code")} for b in blockers]
        point = min(points, key=lambda b: (contract["root_priority"].get(str(b["dimension"]), 99), canonical(b)))
        return canonical({"upstream": point})
    missing = item.get("missing_operands")
    if isinstance(missing, list) and missing and all(isinstance(v, dict) and type(v.get("page")) is int
                                                   and v["page"] > 0 and v.get("source") in {"table", "text"}
                                                   for v in missing):
        return canonical({"missing_locations": sorted(missing, key=canonical)})
    return canonical({"rule": item.get("rule_id"), "sub_check": item.get("sub_check_id"),
                      "obligation": item.get("obligation_id"), "reasons": sorted(item.get("reason_codes") or []),
                      "missing": item.get("missing_operands"),
                      "candidate_locations": [{k: c.get(k) for k in ("source", "table_code", "section", "page", "cell_id", "span")}
                                              for c in item.get("candidates", []) if isinstance(c, dict)]
                          if isinstance(item.get("candidates", []), list) else []})


def diagnostic_root_key(item: Dict[str, Any], contract: Dict[str, Any]) -> str:
    return "rc:" + sha256_text(_root_signature(item, contract))


def _upstream_rank(item: Dict[str, Any], contract: Dict[str, Any]) -> int:
    blockers = item.get("blocked_by") if isinstance(item.get("blocked_by"), list) else []
    return min((contract["root_priority"].get(str(b.get("dimension")), 99)
                for b in blockers if isinstance(b, dict)), default=99)


def _operand_shape_errors(value: Any, *, candidates: bool = False) -> List[str]:
    if not isinstance(value, list) or len(value) < (2 if candidates else 1):
        return ["候选必须是至少两项的列表" if candidates else "missing_operands 必须是非空列表"]
    errors = []
    for item in value:
        if not isinstance(item, dict):
            errors.append("元素必须是对象")
            continue
        source = item.get("source")
        if not isinstance(source, str) or source not in {"table", "text"}:
            errors.append("source 必须明确为 table 或 text")
        keys = {"source", "page", "value"} if candidates else {
            "source", "role", "page", "row_key", "column", "fund_scope", "stage", "year"}
        keys |= ({"table_code", "cell_id"} if candidates else {"table_code"}) if source == "table" else {"section"}
        if keys - set(item):
            errors.append(f"缺少必需键 {sorted(keys - set(item))}")
        required = (["role"] if not candidates else []) + (["table_code"] + (["cell_id"] if candidates else [])
                    if source == "table" else ["section"])
        for key in required:
            if not _nonblank(item.get(key)):
                errors.append(f"{key} 必须是非空字符串")
        page = item.get("page")
        if page is not None and (type(page) is not int or page < 1):
            errors.append("page 必须是正整数或明确的 null")
        if candidates:
            if source == "text":
                span = item.get("span")
                if not (isinstance(span, list) and len(span) == 2 and all(type(x) is int for x in span)
                        and 0 <= span[0] < span[1]):
                    errors.append("文字候选须给出页内 span=[起点,终点]，不得伪造单元格编号")
                if page is None:
                    errors.append("文字候选须有已确认页码")
            if isinstance(item.get("value"), (dict, list, bool)):
                errors.append("候选 value 必须是原文标量或 null")
        else:
            for key in ("column", "fund_scope", "stage"):
                if item.get(key) is not None and not _nonblank(item[key]):
                    errors.append(f"{key} 必须是非空字符串或 null")
            if item.get("year") is not None and (type(item["year"]) is not int or item["year"] < 1900):
                errors.append("year 必须是有效年度或 null")
            row = item.get("row_key")
            if row is not None and not (_nonblank(row) or isinstance(row, dict) and any(_nonblank(v) for v in row.values())):
                errors.append("row_key 必须是名称/编码、有效身份对象或 null")
    if candidates and not errors:
        locations = {canonical({k: item.get(k) for k in ("source", "table_code", "section", "page", "cell_id", "span")})
                     for item in value}
        if len(locations) < 2:
            errors.append("候选必须至少包含两个不同位置，不能重复同一个候选")
    return errors


def _diagnostic_errors(item: Dict[str, Any], vocabulary: Set[str], contract: Dict[str, Any],
                       dimensions: Set[str]) -> List[str]:
    errors = []
    codes = item.get("reason_codes")
    if not isinstance(codes, list) or not codes or any(not _nonblank(c) for c in codes):
        errors.append("reason_codes 必须是非空字符串列表")
    elif set(codes) - vocabulary:
        errors.append("reason_codes 含未冻结的代码")
    if not _nonblank(item.get("next_action")) or item["next_action"] not in NEXT_ACTIONS or not _nonblank(item.get("root_cause_key")):
        errors.append("next_action 或 root_cause_key 无效")
    blockers = item.get("blocked_by")
    if not isinstance(blockers, list):
        errors.append("blocked_by 必须是列表")
    else:
        for blocker in blockers:
            if not isinstance(blocker, dict) or not _nonblank(blocker.get("dimension")) \
                    or blocker["dimension"] not in dimensions or not _nonblank(blocker.get("ref")) \
                    or not _nonblank(blocker.get("reason_code")) or blocker["reason_code"] not in vocabulary:
                errors.append("blocked_by 元素缺少有效的 dimension/ref/reason_code")
            elif blocker["reason_code"] not in (codes or []):
                errors.append("阻断项原因码不在本条诊断的原因码中")
            elif blocker["reason_code"] in contract["upstream_reason_dimensions"] \
                    and blocker["dimension"] not in contract["upstream_reason_dimensions"][blocker["reason_code"]]:
                errors.append("阻断项的维度与原因码不符")
        if isinstance(codes, list):
            for code in codes:
                if isinstance(code, str) and code in contract["upstream_reason_dimensions"] \
                        and not any(isinstance(b, dict) and b.get("reason_code") == code for b in blockers):
                    errors.append(f"上游原因 {code} 缺少对应阻断项")
    return errors


def _receipt_key(receipt: Dict[str, Any]) -> Tuple[Any, Any, Any]:
    return tuple(receipt.get(k) for k in ("obligation_id", "rule_id", "sub_check_id"))


def diagnostic_receipt_keys(row: Dict[str, Any]) -> Set[Tuple[Any, Any, Any]]:
    # 旧聚合器可能用 fail 保留正式问题，同时仍记录未完成原因、保留父义务未完成。
    # 此范围按基线冻结；不得靠改写新文案选择或排除需要诊断的回执。
    return {_receipt_key(r) for r in row["receipts"]
            if r.get("status") in UNRESOLVED_RECEIPT or
            row["obligations"].get(r.get("obligation_id"), {}).get("status") in DH02_OBLIGATION_SCOPE
            and _nonblank(r.get("incomplete_reason"))}


def _diagnostic_consistency(row: Dict[str, Any], vocabulary: Set[str], contract: Dict[str, Any],
                            eligible: Set[Tuple[Any, Any, Any]], baseline: Optional[Dict[str, Any]] = None):
    shape, rollup, roots = [], [], []
    point_to_key, key_to_point = {}, {}
    context = row.get("identity_context") or {}
    children: Dict[str, List[Dict[str, Any]]] = {}
    dimensions_by_oid: Dict[str, Set[str]] = {}
    for receipt in row["receipts"]:
        if _receipt_key(receipt) not in eligible:
            continue
        oid, rid = receipt.get("obligation_id"), receipt.get("rule_id")
        where = f"{oid}|{rid}|{receipt.get('sub_check_id')}"
        children.setdefault(oid, []).append(receipt)
        try:
            dimensions = _required_dimensions(contract, rid, context)
        except ValueError as exc:
            shape.append(f"{where}: {exc}")
            dimensions = set(contract["structural_dimensions"])
        dimensions_by_oid.setdefault(oid, set()).update(dimensions)
        shape.extend(f"{where}: {e}" for e in _diagnostic_errors(receipt, vocabulary, contract, dimensions))
        if oid is not None and oid not in row["obligations"]:
            rollup.append(f"{where}: 未完成回执没有所属义务")

    def register(item, where):
        if not _nonblank(item.get("root_cause_key")) or not isinstance(item.get("reason_codes"), list) \
                or not all(isinstance(c, str) for c in item["reason_codes"]):
            return
        point, key = _root_signature(item, contract), item["root_cause_key"]
        if point in point_to_key and point_to_key[point] != key:
            roots.append(f"{where}: 同一主阻断点使用不同根因键")
        if key in key_to_point and key_to_point[key] != point:
            roots.append(f"{where}: 不同主阻断点共用同一根因键")
        point_to_key[point], key_to_point[key] = key, point

    for rs in children.values():
        for receipt in rs:
            register(receipt, f"receipt {receipt.get('obligation_id')}|{receipt.get('sub_check_id')}")
    for oid, item in row["obligations"].items():
        if item.get("status") not in DH02_OBLIGATION_SCOPE:
            continue
        rs = children.get(oid, [])
        dimensions = dimensions_by_oid.get(oid, set(contract["structural_dimensions"]))
        shape.extend(f"{oid}: {e}" for e in _diagnostic_errors(item, vocabulary, contract, dimensions))
        if not rs:
            # AI 未执行等模式义务没有规则回执，但仍保留自身诊断；不虚构汇总来源。
            if item.get("status") not in {"ai_not_run", "ai_failed"}:
                rollup.append(f"{oid}: 范围内义务没有未完成回执可供汇总")
            register(item, oid)
            continue
        codes = {c for r in rs for c in (r.get("reason_codes") if isinstance(r.get("reason_codes"), list) else []) if isinstance(c, str)}
        parent_codes = item.get("reason_codes") if isinstance(item.get("reason_codes"), list) else []
        if not codes.issubset(set(c for c in parent_codes if isinstance(c, str))):
            rollup.append(f"{oid}: 义务原因码未包含全部未完成回执原因码")
        legacy = ((baseline or {}).get("obligations", {}).get(oid, {}).get("reason_codes") or [])
        if set(c for c in parent_codes if isinstance(c, str)) - codes - set(legacy):
            rollup.append(f"{oid}: 新增义务原因码没有回执依据")
        best_rank = min(_upstream_rank(r, contract) for r in rs)
        selected = [r for r in rs if r.get("root_cause_key") == item.get("root_cause_key")
                    and _upstream_rank(r, contract) == best_rank]
        if not selected or item.get("next_action") not in [r.get("next_action") for r in selected]:
            rollup.append(f"{oid}: 主根因键/下一步动作不来自同一最上游未完成回执")
        for field in ("blocked_by", "missing_operands", "candidates"):
            expected = {canonical(v) for r in rs for v in (r.get(field) if isinstance(r.get(field), list) else [])}
            actual = {canonical(v) for v in (item.get(field) if isinstance(item.get(field), list) else [])}
            if expected != actual:
                rollup.append(f"{oid}: 义务 {field} 与回执汇总不符")
    return shape, rollup, roots


def compare_runs(base_dir: Path, cur_dir: Path, stage: str, kit: Path, *, allow_partial: bool = False) -> Dict[str, Any]:
    checks: List[Dict[str, Any]] = []

    def add(check_id: str, title: str, ok: Optional[bool], details: Sequence[str] = (), level: str = "FAIL") -> None:
        verdict = "PASS" if ok else ("PENDING" if ok is None else level)
        checks.append({"id": check_id, "title": title, "verdict": verdict, "count": len(details),
                       "details": list(details)})

    bm, cm = read_json(base_dir / "meta.json"), read_json(cur_dir / "meta.json")
    vocabulary = set(read_json(kit / "reason_codes_v1.json")["codes"])
    contract = _identity_contract(kit)
    expected_digest = sha256_file(kit / "SHA256SUMS")
    frozen_profile = read_json(kit / "runtime_profile.json")["env"]
    expected_samples = _frozen_samples(kit)
    bs, brs, bid, bvalid = _run_records(base_dir, bm, expected_samples, allow_partial)
    cs, crs, cid, cvalid = _run_records(cur_dir, cm, expected_samples, allow_partial)

    # M：比较前提
    add("M1", "两次采集均属于当前冻结验收包", bm.get("kit_digest") == cm.get("kit_digest") == expected_digest)
    identity_errors = [f"基线: {e}" for e in bid] + [f"当前: {e}" for e in cid]
    add("M2", "样本范围、记录身份与冻结清单一致", bs == cs and not identity_errors, identity_errors)
    profile_ok = bm.get("profile") == cm.get("profile") == frozen_profile \
        and not bm.get("profile_overrides") and not cm.get("profile_overrides")
    add("M3", "两次运行均使用冻结变量，配置覆盖实验不进入验收", profile_ok)
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
    invalid_results = [f"基线: {e}" for e in bvalid] + [f"当前: {e}" for e in cvalid]
    add("M6", "采集与分析结果有效、无失败样本且环境隔离成功",
        not bm["failed"] and not cm["failed"] and bm["dotenv_isolation_ok"] and cm["dotenv_isolation_ok"]
        and not invalid_results, invalid_results)
    changed_env = sorted(name for name in set(bm["code_env_names"]) | set(cm["code_env_names"])
                         if bm["code_env_names"].get(name, "<unset>") != cm["code_env_names"].get(name, "<unset>")
                         and (name in bm["profile"] or name in cm["profile"]))
    add("M7", "代码读取的运行变量取值一致", not changed_env, changed_env)
    drift = [f"{label}: 采集期间代码指纹变化" for label, m in (("基线", bm), ("当前", cm))
             if m["code"]["digest"] != m.get("code_after")]
    add("M8", "采集期间代码未被改动", not drift, drift)
    writes = [f"{label}: {name}" for label, m in (("基线", bm), ("当前", cm)) for name in m.get("code_root_writes", [])]
    add("M9", "采集未写入代码根（含 data/、logs/）", not writes, writes, level="REVIEW")
    # 新代码读取的新环境变量：若用它做功能开关且默认关闭，重放就没有执行到新代码。
    new_env = sorted(set(cm["code_env_names"]) - set(bm["code_env_names"]))
    add("M10", "当前代码新读取的环境变量（须说明默认值与生产取值一致）", not new_env, new_env, level="REVIEW")
    add("M11", "规则身份维度声明与冻结对照表一致", all(
        m["code"].get("files", {}).get(contract["source_file"]) == contract["source_sha256"] for m in (bm, cm)))

    run_d, obl_d, add_d, rec_d, fnd_d, todo_d, cov_d, read_d, sum_d, led_d = ([] for _ in range(10))
    t_codes, t_noreason, t_missing, t_ambig, t_vocab, t_inst, t_terms, t_terms_eq, t_phrase, t_dump = (
        [] for _ in range(10))
    out_of_scope: List[str] = []
    shape_d, transport_d = [], []
    rollup_d, root_d = [], []
    unresolved_receipts = 0
    no_reason = 0
    for sid in sorted(bs):
        if sid not in brs or sid not in crs:
            run_d.append(f"{sid}: 缺少有效采集记录")
            continue
        b, c = brs[sid], crs[sid]
        diagnostic_keys = diagnostic_receipt_keys(b)
        if b["identity_context"] != c["identity_context"]:
            run_d.append(f"{sid}: 规则身份上下文改变")
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
        if set(b["coverage_items"]) != set(c["coverage_items"]):
            cov_d.append(f"{sid}: 覆盖块条目集合改变")
        if set(c["coverage_items"]) != set(c["obligations"]):
            cov_d.append(f"{sid}: 覆盖块条目与台账义务集合不符")
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
            for index, (br, cr) in enumerate(zip(blist, cg.get(key, []), strict=False)):
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
        if stage != "self":
            ds, dr, dk = _diagnostic_consistency(c, vocabulary, contract, diagnostic_keys, b)
            shape_d.extend(f"{sid}: {e}" for e in ds)
            rollup_d.extend(f"{sid}: {e}" for e in dr)
            root_d.extend(f"{sid}: {e}" for e in dk)
        for r in c["receipts"]:
            if _receipt_key(r) not in diagnostic_keys:
                continue
            unresolved_receipts += 1
            where = f"{sid} {rkey(r)}"
            raw_codes = r.get("reason_codes")
            codes = raw_codes if isinstance(raw_codes, list) and all(isinstance(v, str) for v in raw_codes) else []
            if not codes:
                t_codes.append(where)
            if "rule_no_reason" in codes:
                no_reason += 1
            if "operand_missing" in codes:
                t_missing.extend(f"{where}: {e}" for e in _operand_shape_errors(r.get("missing_operands")))
            if "operand_ambiguous" in codes:
                t_ambig.extend(f"{where}: {e}" for e in _operand_shape_errors(r.get("candidates"), candidates=True))
            unknown = sorted(set(codes) - vocabulary)
            if unknown:
                t_vocab.append(f"{where}: {unknown}")
            for field in ("incomplete_reason",):
                if not _nonblank(r.get(field)):
                    t_phrase.append(f"{where}: 缺少非空 {field}")
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
            else:
                if stage != "self":
                    dimensions = set(contract["structural_dimensions"])
                    for receipt in c["receipts"]:
                        if receipt.get("obligation_id") == oid and receipt.get("rule_id") in contract["rules"]:
                            dimensions.update(_required_dimensions(contract, receipt["rule_id"], c.get("identity_context") or {}))
                    t_inst.extend(f"{where}: {e}" for e in _diagnostic_errors(ci, vocabulary, contract, dimensions))
                for field in STAGE_NEW_FIELDS.get(stage, set()):
                    if field in ci and canonical(ci[field]) != canonical(c["coverage_items"].get(oid, {}).get(field, "<absent>")):
                        transport_d.append(f"{where}: 覆盖块丢失或改写 {field}")
                codes = ci.get("reason_codes") if isinstance(ci.get("reason_codes"), list) else []
                if "operand_missing" in codes:
                    shape_d.extend(f"{where}: {e}" for e in _operand_shape_errors(ci.get("missing_operands")))
                if "operand_ambiguous" in codes:
                    shape_d.extend(f"{where}: {e}" for e in _operand_shape_errors(ci.get("candidates"), candidates=True))
            if not _nonblank(ci.get("detail")):
                t_phrase.append(f"{where}: 缺少非空 detail")
            if not _nonblank(c["coverage_items"].get(oid, {}).get("detail")):
                t_phrase.append(f"{where}: 覆盖块缺少非空 detail")
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
        add("T11", "关系级与义务级卡点字段结构有效", not shape_d, shape_d)
        add("T12", "全部新增卡点字段从台账贯通到接口覆盖块", not transport_d, transport_d)
        add("T13", "义务诊断来自其未完成回执且汇总完整", not rollup_d, rollup_d)
        add("T14", "同份材料的主阻断点与根因键一一对应", not root_d, root_d)
        checks.append({"id": "T6i", "title": "范围外的未完成义务（方案 DH-02 第 1 条未要求，仅报告）",
                       "verdict": "INFO", "count": len(out_of_scope), "details": out_of_scope})
    if stage in {"DH-03a", "DH-03b"}:
        add("T7", "取数不足义务均带列表形式的 legacy_coverage_terms", not t_terms, t_terms)
        add("T8", "legacy_coverage_terms 等于旧算法从基线 detail 取出的短语", not t_terms_eq, t_terms_eq)
        add("T9", "legacy_coverage_terms 经覆盖块（接口层）后不丢失", not t_dump, t_dump)
    if stage == "DH-03b":
        add("T10", "新原因文案存在且不再出现“缺失或不唯一”", not t_phrase, t_phrase)

    verdicts = [c["verdict"] for c in checks]
    overall = ("FAIL" if "FAIL" in verdicts else "REVIEW" if "REVIEW" in verdicts
               else "PENDING" if "PENDING" in verdicts else "PASS")
    partial = bs != expected_samples or cs != expected_samples or bm.get("sample_scope") != "full" or cm.get("sample_scope") != "full"
    if overall == "PASS" and partial:
        overall = "PARTIAL_PASS"
    return {"stage": stage, "baseline": str(base_dir), "current": str(cur_dir), "overall": overall,
            "formal_acceptance_eligible": overall == "PASS" and not partial,
            "sample_count": len(bs), "expected_sample_count": len(expected_samples), "checks": checks}


def compare_main(args: argparse.Namespace) -> int:
    kit = Path(args.kit).resolve()
    verify_kit(kit)
    report = compare_runs(Path(args.baseline).resolve(), Path(args.current).resolve(), args.stage, kit,
                          allow_partial=args.allow_partial)
    if args.out:
        write_json(Path(args.out), report)
    for check in report["checks"]:
        print(f"[{check['verdict']:7}] {check['id']:4} {check['title']}" + (f"（{check['count']}）" if check["count"] else ""))
        for line in check["details"][:5]:
            print(f"           - {line}")
    print(f"总体：{report['overall']}")
    return 0 if report["overall"] == "PASS" or args.allow_partial and report["overall"] == "PARTIAL_PASS" else 1


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
    for sample, res in zip(rb, derived, strict=False):
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


def identity_main(args: argparse.Namespace) -> int:
    path = Path(args.out).resolve()
    if path.exists():
        refuse("身份维度对照表输出已存在，禁止覆盖冻结表")
    result = build_identity_contract(Path(args.code_root).resolve())
    write_json(path, result)
    print(json.dumps({"rules": len(result["rules"]), "source_sha256": result["source_sha256"], "out": str(path)}))
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
                       "git_status_entries": len([line for line in porcelain.splitlines() if line.strip()]),
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


def _selftest_probe(run: Path) -> str:
    # 并非每份材料都有未完成关系回执；负例必须选中实际具备前置条件的材料。
    for path in sorted((run / "records").glob("*.json.gz")):
        row = read_json_gz(path)
        if any(r.get("status") in UNRESOLVED_RECEIPT for r in row["receipts"]) \
                and any(i.get("status") == "insufficient_data" for i in row["obligations"].values()):
            return row["sample_id"]
    refuse("自测样本缺少取数不足义务及未完成关系回执，无法运行整改正反例")


def _synthetic_diagnostics(record, stage, contract):
    """仅构造字段契约测试数据；不把合成卡点当作对真实原文的诊断。"""
    eligible = diagnostic_receipt_keys(record)
    by_oid = {}
    for receipt in record["receipts"]:
        if _receipt_key(receipt) not in eligible:
            continue
        receipt["reason_codes"] = list(dict.fromkeys(list(receipt.get("reason_codes") or []) + ["operand_missing"]))
        receipt["missing_operands"] = [{"source":"table", "role":"total", "table_code":"FIN_03_expenditure",
            "page":None, "row_key":None, "column":None, "fund_scope":None, "stage":None, "year":None}]
        receipt.update(blocked_by=[], next_action="tech_fix")
        receipt["root_cause_key"] = diagnostic_root_key(receipt, contract)
        if stage == "DH-03b":
            receipt["incomplete_reason"] = "结构化生成的回执原因"
        by_oid.setdefault(receipt.get("obligation_id"), []).append(receipt)
    for oid, item in record["obligations"].items():
        old_terms = _old_coverage_terms(item.get("detail", ""))
        if item.get("status") in DH02_OBLIGATION_SCOPE:
            rs = by_oid.get(oid, [])
            if rs:
                item["reason_codes"] = list(dict.fromkeys(list(item.get("reason_codes") or []) +
                    [c for r in rs for c in r["reason_codes"]]))
                for field in ("blocked_by", "missing_operands", "candidates"):
                    values = {canonical(v):v for r in rs for v in r.get(field, [])}
                    if values or field == "blocked_by":
                        item[field] = [values[k] for k in sorted(values)]
                primary = min(rs, key=lambda r: (_upstream_rank(r, contract), r["root_cause_key"]))
                item.update(next_action=primary["next_action"], root_cause_key=primary["root_cause_key"])
            else:
                code = "ai_not_requested" if item.get("status") == "ai_not_run" else "ai_partial_invalid_output"
                item.update(reason_codes=list(dict.fromkeys(list(item.get("reason_codes") or [])+[code])),
                            blocked_by=[], next_action="rerun_ai")
                item["root_cause_key"] = diagnostic_root_key(item, contract)
            if oid in record["coverage_items"]:
                record["coverage_items"][oid].update({k:copy.deepcopy(item[k]) for k in STAGE_NEW_FIELDS[stage] if k in item})
        if stage in {"DH-03a", "DH-03b"} and item.get("status") == "insufficient_data":
            item["legacy_coverage_terms"] = list(old_terms)
            record["coverage_items"][oid]["legacy_coverage_terms"] = list(old_terms)
        if stage == "DH-03b" and item.get("status") in UNRESOLVED_OBLIGATION:
            item["detail"] = record["coverage_items"][oid]["detail"] = f"结构化生成的卡点说明（{oid}）"


def selftest_main(args: argparse.Namespace) -> int:
    kit = Path(args.kit).resolve()
    contract = _identity_contract(kit)
    out = ensure_new_dir(Path(args.out))
    me = [sys.executable, str(Path(__file__).resolve())]
    results: List[Dict[str, Any]] = []

    def expect(name: str, cmd: List[str], want_code: Optional[int], want_text: str = "") -> subprocess.CompletedProcess:
        if "compare" in cmd and "--allow-partial" not in cmd:
            cmd = [*cmd, "--allow-partial"]
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
            "--out", str(out / "S1c.json")], 0, "总体：PARTIAL_PASS")
    first = _selftest_probe(run1)

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
        report = compare_runs(run1, mutated, "self", kit, allow_partial=True)
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
    report = compare_runs(run1, mutated, "self", kit, allow_partial=True)
    m3 = next(c for c in report["checks"] if c["id"] == "M3")
    results.append({"name": "S2h 运行变量不同 → M3 判 FAIL", "ok": m3["verdict"] == "FAIL", "detail": m3})
    print(f"[{'PASS' if m3['verdict'] == 'FAIL' else 'FAIL'}] S2h 运行变量不同 → M3 判 FAIL", flush=True)

    # 阶段判定的正反例：用 run2 构造“合格的 / 不合格的整改输出”，检验 T 类与阶段字段规则本身
    def synth(name: str, stage: str, tweak=None, base_tweak=None) -> Dict[str, Any]:
        base = run1
        if base_tweak:
            # 构造“基线中本就存在”的情形（如非空原因码）：基线与当前同时带上，再由 tweak 改当前。
            base = out / f"synbase_{name}"
            shutil.copytree(run1, base)
        dst = out / f"syn_{name}"
        shutil.copytree(run2, dst)
        meta = read_json(dst / "meta.json")
        meta["code"]["digest"] = meta["code_after"] = f"synthetic-{name}"
        write_json(dst / "meta.json", meta)
        if base_tweak:
            for root in (base, dst):
                for path in sorted((root / "records").glob("*.json.gz")):
                    record = read_json_gz(path)
                    base_tweak(record)
                    write_json_gz(path, record)
        for path in sorted((dst / "records").glob("*.json.gz")):
            record = read_json_gz(path)
            _synthetic_diagnostics(record, stage, contract)
            if tweak:
                tweak(record)
            write_json_gz(path, record)
        report = compare_runs(base, dst, stage, kit, allow_partial=True)
        if base_tweak:
            shutil.rmtree(base, ignore_errors=True)
        return report

    def first_record_fn(fn):
        def apply(record: Dict[str, Any]) -> None:
            if record["sample_id"] == first:
                fn(record)
        return apply

    def unresolved_receipt(record: Dict[str, Any]) -> Dict[str, Any]:
        return next(r for r in record["receipts"] if r.get("status") in UNRESOLVED_RECEIPT)

    def insufficient(record: Dict[str, Any]) -> str:
        return next(oid for oid, i in record["obligations"].items() if i.get("status") == "insufficient_data")

    def drop_projection(record):
        for item in record["coverage_items"].values():
            for field in STAGE_NEW_FIELDS["DH-02"]:
                item.pop(field, None)

    def remove_text(record):
        for collection in (record["obligations"], record["coverage_items"]):
            for item in collection.values():
                if item.get("status") in UNRESOLVED_OBLIGATION:
                    item.pop("detail", None)
        for receipt in record["receipts"]:
            if receipt.get("status") in UNRESOLVED_RECEIPT:
                receipt.pop("incomplete_reason", None)

    def seed_existing_codes(record):
        oid = insufficient(record)
        record["obligations"][oid]["reason_codes"] = ["stated_total_missing"]
        record["coverage_items"][oid]["reason_codes"] = ["stated_total_missing"]

    def wrong_rollup(record):
        oid = insufficient(record)
        for collection in (record["obligations"], record["coverage_items"]):
            collection[oid].update(reason_codes=["ai_timeout"], root_cause_key="wrong-ai-root", next_action="rerun_ai")

    def text_positive(record):
        receipt = unresolved_receipt(record)
        receipt["missing_operands"] = [{"source":"text", "role":"basic_expenditure", "section":"三、支出决算情况说明",
            "page":20, "row_key":"基本支出", "column":None, "fund_scope":None, "stage":None, "year":None}]
        receipt["root_cause_key"] = diagnostic_root_key(receipt, contract)
        oid = receipt["obligation_id"]
        rs = [r for r in record["receipts"] if r.get("obligation_id") == oid and _receipt_key(r) in diagnostic_receipt_keys(record)]
        primary = min(rs, key=lambda r: r["root_cause_key"])
        for collection in (record["obligations"], record["coverage_items"]):
            target = collection[oid]
            for field in ("blocked_by", "missing_operands", "candidates"):
                values = {canonical(v):v for r in rs for v in r.get(field, [])}
                target[field] = [values[k] for k in sorted(values)]
            target.update(root_cause_key=primary["root_cause_key"], next_action=primary["next_action"])

    synthetic = [
        ("S7a DH-02 合格局部输出 → PARTIAL_PASS", "a", "DH-02", None, None, "PARTIAL_PASS"),
        ("S7b DH-02 有回执缺原因码 → T1 FAIL", "b", "DH-02",
         first_record_fn(lambda r: unresolved_receipt(r).update(reason_codes=[])), "T1", "FAIL"),
        ("S7c DH-02 改了 detail → I2 FAIL", "c", "DH-02",
         first_record_fn(lambda r: r["obligations"][first_unresolved(r)].update(detail="改写")), "I2", "FAIL"),
        ("S7d DH-02 operand_ambiguous 无 candidates → T4 FAIL", "d", "DH-02",
         first_record_fn(lambda r: unresolved_receipt(r)["reason_codes"].append("operand_ambiguous")), "T4", "FAIL"),
        ("S7e DH-02 词表外原因码 → T5 FAIL", "e", "DH-02",
         first_record_fn(lambda r: unresolved_receipt(r)["reason_codes"].append("made_up_code")), "T5", "FAIL"),
        ("S7f DH-02 未完成义务缺 next_action → T6 FAIL", "f", "DH-02",
         first_record_fn(lambda r: r["obligations"][insufficient(r)].pop("next_action")), "T6", "FAIL"),
        ("S7g DH-02 回执状态改变 → I3 FAIL", "g", "DH-02",
         first_record_fn(lambda r: unresolved_receipt(r).update(status="pass")), "I3", "FAIL"),
        ("S7h DH-03a 合格局部输出 → PARTIAL_PASS", "h", "DH-03a", None, None, "PARTIAL_PASS"),
        ("S7i DH-03a 短语与旧算法不符 → T8 FAIL", "i", "DH-03a",
         first_record_fn(lambda r: r["obligations"][insufficient(r)].update(legacy_coverage_terms=["随便一个短语"])),
         "T8", "FAIL"),
        ("S7j DH-03a 覆盖块丢了字段 → T9 FAIL", "j", "DH-03a",
         first_record_fn(lambda r: r["coverage_items"][insufficient(r)].pop("legacy_coverage_terms")), "T9", "FAIL"),
        ("S7k DH-03a 义务缺字段 → T7 FAIL", "k", "DH-03a",
         first_record_fn(lambda r: r["obligations"][insufficient(r)].pop("legacy_coverage_terms")), "T7", "FAIL"),
        ("S7l DH-03b 合格局部输出（detail 已改写）→ PARTIAL_PASS", "l", "DH-03b", None, None, "PARTIAL_PASS"),
        ("S7m DH-03b 新 detail 仍含“缺失或不唯一” → T10 FAIL", "m", "DH-03b",
         first_record_fn(lambda r: r["obligations"][insufficient(r)].update(detail="X的Y/决算操作数缺失或不唯一")),
         "T10", "FAIL"),
        ("S7n DH-02 基线已有的原因码被删 → I2 FAIL", "n", "DH-02",
         first_record_fn(lambda r: r["obligations"][insufficient(r)].update(reason_codes=[])), "I2", "FAIL",
         first_record_fn(seed_existing_codes)),
        ("S7o DH-02 在基线已有的原因码后追加 → I2 PASS", "o", "DH-02", None, "I2", "PASS",
         first_record_fn(seed_existing_codes)),
        ("S7p DH-02 接口丢掉全部新字段 → T12 FAIL", "p", "DH-02",
         first_record_fn(drop_projection), "T12", "FAIL"),
        ("S7q missing_operands 为数字 → T3 FAIL", "q", "DH-02",
         first_record_fn(lambda r: unresolved_receipt(r).update(missing_operands=123)), "T3", "FAIL"),
        ("S7r candidates 为字符串 → T4 FAIL", "r", "DH-02",
         first_record_fn(lambda r: unresolved_receipt(r).update(
             reason_codes=["operand_missing", "operand_ambiguous"], candidates="不是列表")), "T4", "FAIL"),
        ("S7s blocked_by 只有空壳 → T11 FAIL", "s", "DH-02",
         first_record_fn(lambda r: unresolved_receipt(r).update(blocked_by=[{}])), "T11", "FAIL"),
        ("S7t 覆盖块多出条目 → I6 FAIL", "t", "DH-02",
         first_record_fn(lambda r: r["coverage_items"].update({"EXTRA": {"obligation_id": "EXTRA"}})), "I6", "FAIL"),
        ("S7u 删除全部原因文案 → T10 FAIL", "u", "DH-03b",
         first_record_fn(remove_text), "T10", "FAIL"),
        ("S7v 上游原因却没有阻断项 → T11 FAIL", "v", "DH-02",
         first_record_fn(lambda r: unresolved_receipt(r).update(reason_codes=["table_mapping_incomplete"], blocked_by=[])), "T11", "FAIL"),
        ("S7w 义务诊断与回执无关 → T13 FAIL", "w", "DH-02", first_record_fn(wrong_rollup), "T13", "FAIL"),
        ("S7x 说明文字操作数无表号 → PARTIAL_PASS", "x", "DH-02", first_record_fn(text_positive), None, "PARTIAL_PASS"),
    ]
    for entry in synthetic:
        title, name, stage, tweak, check_id, want = entry[:6]
        base_tweak = entry[6] if len(entry) > 6 else None
        report = synth(name, stage, tweak, base_tweak)
        if check_id is None:
            ok = report["overall"] == want
            detail: Any = [c for c in report["checks"] if c["verdict"] not in {"PASS", "INFO"}]
        else:
            hit = next((c for c in report["checks"] if c["id"] == check_id), None)
            ok = bool(hit and hit["verdict"] == want)
            if want == "PASS":
                ok = ok and report["overall"] in {"PASS", "PARTIAL_PASS"}
            detail = hit
        results.append({"name": title, "ok": ok, "detail": detail})
        print(f"[{'PASS' if ok else 'FAIL'}] {title}", flush=True)
        shutil.rmtree(out / f"syn_{name}", ignore_errors=True)

    # 辅助判定的单元检查，以及冻结旧算法与代码根中 distinctive_terms 的等价性
    unit = [
        _contains_all(["a"], ["a", "b"]), not _contains_all(["a", "b"], ["a"]),
        _contains_all({"k": 1}, {"k": 1, "j": 2}), not _contains_all({"k": 1}, {"k": 2}),
        not _contains_all(["a", "a"], ["a"]),
    ]
    results.append({"name": "S8a 只增判定的单元检查", "ok": all(unit), "detail": unit})
    print(f"[{'PASS' if all(unit) else 'FAIL'}] S8a 只增判定的单元检查", flush=True)
    texts = sorted({str(i.get("detail") or "") for path in sorted((run1 / "records").glob("*.json.gz"))
                    for i in read_json_gz(path)["obligations"].values()} | {
        o["ledger"]["instances"][0]["detail"] for o in read_json(kit / "boundary_cases.json")["cases"]})
    case_dir = out / "terms_work"
    for sub in ("home", "tmp", "org", "uploads"):
        (case_dir / sub).mkdir(parents=True)
    profile = read_json(kit / "runtime_profile.json")["env"]
    res = run_worker({"kind": "terms", "texts": texts, "dotenv_names": [], "pinned_names": sorted(profile)},
                     code_root=Path(args.code_root).resolve(), python=args.python,
                     env=worker_env(profile, home=case_dir / "home", tmp=case_dir / "tmp", org_dir=case_dir / "org",
                                    upload_dir=case_dir / "uploads"), work=case_dir, timeout=300,
                     log_path=case_dir / "worker.log")
    mismatch = [t for t, live in zip(texts, res.get("terms") or [], strict=False) if _old_coverage_terms(t) != live]
    ok = bool(res.get("ok")) and not mismatch and len(res.get("terms") or []) == len(texts)
    results.append({"name": f"S8b 冻结旧算法与代码根 distinctive_terms 等价（{len(texts)} 条 detail）", "ok": ok,
                    "detail": mismatch[:5] or res.get("error")})
    print(f"[{'PASS' if ok else 'FAIL'}] S8b 冻结旧算法与代码根 distinctive_terms 等价（{len(texts)} 条 detail）", flush=True)

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

    expect("S9a 零样本集 → 拒绝", [*me, "collect", "--kit", str(kit), "--code-root", args.code_root,
           "--sets", "", "--out", str(out / "never-empty")], 2, "非空")
    full_guard = compare_runs(run1, run2, "self", kit)
    results.append({"name": "S9b 局部样本不得用于正式验收", "ok": full_guard["overall"] == "FAIL"
                    and next(c for c in full_guard["checks"] if c["id"] == "M2")["verdict"] == "FAIL"})
    for name, field, value, check_id in (
        ("S9c 两次均登记其他验收包", "kit_digest", "other-kit", "M1"),
        ("S9d 两次均使用未冻结配置", "profile", {"OCR_ENABLED": "true"}, "M3"),
    ):
        roots = []
        for index, src in enumerate((run1, run2)):
            dst = out / f"guard-{field}-{index}"
            shutil.copytree(src, dst)
            data = read_json(dst / "meta.json")
            data[field] = value
            write_json(dst / "meta.json", data)
            roots.append(dst)
        report = compare_runs(*roots, "self", kit, allow_partial=True)
        ok = next(c for c in report["checks"] if c["id"] == check_id)["verdict"] == "FAIL"
        results.append({"name": name + " → " + check_id + " FAIL", "ok": ok})
        for dst in roots:
            shutil.rmtree(dst)

    # 模板开关实验：无数据库时 GBC_TEMPLATE_SCOPE=1 是否改变结果（只报告，不判 PASS/FAIL）
    run_scope = out / "run_scope1"
    subprocess.run([*me, "collect", *common, "--out", str(run_scope), "--label", "selftest-scope1",
                    "--profile-override", "GBC_TEMPLATE_SCOPE=1"], capture_output=True, text=True)
    scope_report = compare_runs(run1, run_scope, "self", kit, allow_partial=True) if (run_scope / "meta.json").exists() else None
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
        if (run_eq / "meta.json").exists():
            # 原仓库可能正被其他会话修改（与基线无关的文件），所以 M5 只报告差异文件；
            # 快照完整性看结果：除 M5、M9 外的全部判定项都必须 PASS。
            report = compare_runs(run1, run_eq, "self", kit, allow_partial=True)
            write_json(out / "S6b.json", report)
            others = [c for c in report["checks"] if c["id"] not in {"M5", "M9"} and c["verdict"] != "PASS"]
            snap_files = read_json(run1 / "meta.json")["code"]["files"]
            live_files = read_json(run_eq / "meta.json")["code"]["files"]
            differ = sorted(n for n in set(snap_files) | set(live_files) if snap_files.get(n) != live_files.get(n))
            results.append({"name": "S6b 原仓库与快照的检查结果逐项一致（M5 只报告差异文件）", "ok": not others,
                            "detail": {"non_pass": others, "code_files_differing": differ}})
            print(f"[{'PASS' if not others else 'FAIL'}] S6b 原仓库与快照的检查结果逐项一致；代码差异文件 {differ}",
                  flush=True)
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

    identity = sub.add_parser("identity-matrix")
    identity.add_argument("--code-root", required=True)
    identity.add_argument("--out", required=True)

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
    k.add_argument("--allow-partial", action="store_true", help="只验证非空局部样本；成功仅标 PARTIAL_PASS，不得用于正式验收")

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
    handlers = {"_worker": worker_main, "manifest": manifest_main, "seal": seal_main, "identity-matrix": identity_main, "snapshot": snapshot_main,
                "collect": collect_main, "compare": compare_main, "boundary": boundary_main,
                "selftest": selftest_main}
    return handlers[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
