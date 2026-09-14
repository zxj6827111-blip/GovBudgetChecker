#!/usr/bin/env python3
"""在隔离副本中重跑上传任务并生成可审计的新回放报告。

这个命令与 ``replay_analysis.py`` 故意分开：后者只读历史 status，不能把
缺失的页面覆盖率或证据字段凭空补回来；本命令复制 PDF/status 到新的输出目录，
使用当前生产管线重新解析，再对输出副本做同一套结构性门禁。

安全约束：

* 必须显式指定 ``--output-dir``，且输出目录不能是输入目录或其子目录；
* 默认不连接 PostgreSQL、不调用 AI、不写原始 uploads；
* 组织/年度/材料类型只在副本内按当前 preflight 复核，原始人工确认字段不被覆盖；
* 报告保留 source/output checksum、失败任务和局限，不能被误读成 793 任务历史基线。

示例::

    python scripts/reprocess_uploads.py \
      --uploads-dir uploads \
      --output-dir outputs/reprocess-20260909

若只处理一份已经冻结的清单，清单可以是 ``["job-id", ...]``，或
``{"job_ids": ["job-id", ...]}``。
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.services.pdf_selection import select_canonical_pdf  # noqa: E402


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _basename(value: Any) -> str:
    """跨平台取存储元数据中的文件名。"""
    text = str(value or "").strip()
    return re.split(r"[\\/]", text)[-1] if text else ""


def _job_dirs(root: Path) -> Iterable[Path]:
    if not root.is_dir():
        return []
    return sorted(
        (
            child
            for child in root.iterdir()
            if child.is_dir() and not child.name.startswith(".")
        ),
        key=lambda item: item.name,
    )


def _load_job_ids(path: Optional[str]) -> Optional[set[str]]:
    if not path:
        return None
    payload = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    values = payload.get("job_ids") if isinstance(payload, dict) else payload
    if not isinstance(values, list) or any(not str(value).strip() for value in values):
        raise ValueError("manifest 必须是 job_id 字符串数组，或包含 job_ids 数组的对象")
    return {str(value).strip() for value in values}


def _resolve_output_dir(source: Path, raw_output: str) -> Path:
    output = Path(raw_output).expanduser().resolve()
    source = source.resolve()
    if output == source or source in output.parents:
        raise ValueError("--output-dir 不能是 --uploads-dir 或其子目录")
    if output.exists():
        raise ValueError(f"输出目录已存在，为避免覆盖已有证据而拒绝：{output}")
    return output


def _prepare_copy(
    source_dir: Path,
    output_dir: Path,
    *,
    source_root: Path,
    correct_metadata: bool,
) -> Dict[str, Any]:
    destination = output_dir / source_dir.name
    pdfs = sorted(
        (
            child
            for child in source_dir.iterdir()
            if child.is_file() and child.suffix.lower() == ".pdf"
        ),
        key=lambda item: item.name,
    )
    source_status = _read_json(source_dir / "status.json")
    if not source_status:
        raise ValueError("缺少或无法读取 status.json")
    try:
        source_pdf = select_canonical_pdf(source_dir)
    except FileNotFoundError as exc:
        raise ValueError("缺少 PDF") from exc
    selected_by = "only_pdf" if len(pdfs) == 1 else "status_filename"

    shutil.copytree(source_dir, destination)
    ignored_pdf_names: List[str] = []
    for copied_pdf in sorted(
        (
            child
            for child in destination.iterdir()
            if child.is_file() and child.suffix.lower() == ".pdf"
        ),
        key=lambda item: item.name,
    ):
        if copied_pdf.name == source_pdf.name:
            continue
        # 仅删除新建隔离副本中的派生 PDF；输入 uploads 永不修改。
        copied_pdf.unlink()
        ignored_pdf_names.append(copied_pdf.name)
    status_path = destination / "status.json"
    status = dict(source_status)
    metadata_correction: Optional[Dict[str, Any]] = None
    if correct_metadata:
        from scripts.migrate_historical_uploads import _derive_metadata_correction

        metadata_correction = _derive_metadata_correction(status, source_pdf)
        if metadata_correction:
            status.update(metadata_correction["patch"])
            status["historical_metadata_correction"] = metadata_correction["audit"]

    # 隔离回放只执行本地规则；保留原始字段在 manifest 中，避免把当前
    # 运行环境误认为历史 AI/dual 运行结果。
    status["use_local_rules"] = True
    status["use_ai_assist"] = False
    status["mode"] = "legacy"
    status["reprocess_source_job_id"] = source_dir.name
    _write_json(status_path, status)

    return {
        "job_id": source_dir.name,
        "source_dir": source_dir.relative_to(source_root).as_posix(),
        "source_pdf": source_pdf.name,
        "source_pdf_sha256": _sha256(source_pdf),
        "canonical_pdf_selection": selected_by,
        "ignored_pdf_names": ignored_pdf_names,
        "source_status": str(source_status.get("status") or ""),
        "metadata_corrected": metadata_correction is not None,
        "output_dir": destination.as_posix(),
    }


async def _run_jobs(output_dir: Path, jobs: Sequence[Dict[str, Any]]) -> None:
    # 必须在导入 api.main 前清空连接配置；同时在导入后保留空值，防止
    # dotenv 或测试环境把本机数据库意外带入隔离回放。
    os.environ["DATABASE_URL"] = ""
    os.environ["AI_ASSIST_ENABLED"] = "false"
    os.environ["PDF_PARSE_ISOLATION_ENABLED"] = "false"
    os.environ["UPLOAD_DIR"] = str(output_dir)
    from api import main as api_main

    os.environ["DATABASE_URL"] = ""
    for item in jobs:
        await api_main._run_pipeline(Path(str(item["output_dir"])))


def _build_replay_report(output_dir: Path) -> Dict[str, Any]:
    from scripts.replay_analysis import build_report

    return build_report(output_dir, include_jobs=True)


def _evaluate_gate(report: Dict[str, Any]) -> List[Dict[str, Any]]:
    from scripts.check_replay_thresholds import evaluate

    checks = [item.to_dict() for item in evaluate(report)]
    if report.get("mode") != "offline_reprocess":
        return checks

    reprocess = report.get("reprocess")
    if not isinstance(reprocess, dict):
        checks.append(
            {
                "name": "reprocess_execution",
                "passed": False,
                "detail": "缺少 reprocess 审计元数据（fail-closed）",
            }
        )
        checks.append(
            {
                "name": "database_identity_revalidation",
                "passed": False,
                "detail": "缺少数据库身份复核结果（fail-closed）",
            }
        )
        return checks

    preparation_failures = reprocess.get("preparation_failures")
    run_failures = reprocess.get("run_failures")
    candidate_count = reprocess.get("candidate_count")
    prepared_count = reprocess.get("prepared_count")
    execution_ok = (
        isinstance(preparation_failures, list)
        and isinstance(run_failures, list)
        and not preparation_failures
        and not run_failures
        and isinstance(candidate_count, int)
        and not isinstance(candidate_count, bool)
        and candidate_count >= 0
        and isinstance(prepared_count, int)
        and not isinstance(prepared_count, bool)
        and prepared_count >= 0
        and candidate_count == prepared_count
    )
    checks.append(
        {
            "name": "reprocess_execution",
            "passed": execution_ok,
            "detail": (
                "隔离副本内所有候选任务均完成准备和管线执行"
                if execution_ok
                else "存在准备/管线失败或候选数与准备数不一致，不能判定 GO"
            ),
        }
    )

    identity_ok = reprocess.get("database_identity_revalidated") is True
    checks.append(
        {
            "name": "database_identity_revalidation",
            "passed": identity_ok,
            "detail": (
                "report_id 已在生产数据库复核"
                if identity_ok
                else "隔离重跑未连接生产数据库，report_id 尚未复核"
            ),
        }
    )
    return checks


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uploads-dir", default="uploads")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--manifest", help="显式 job_id 清单 JSON（可选）")
    parser.add_argument("--limit", type=int, default=0, help="最多处理多少任务，0 表示不限")
    parser.add_argument(
        "--no-metadata-correction",
        action="store_true",
        help="不在副本内复核封面年度/类型/组织元数据",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    source_root = Path(args.uploads_dir).expanduser().resolve()
    if not source_root.is_dir():
        print(f"输入目录不存在：{source_root}", file=sys.stderr)
        return 2
    if args.limit < 0:
        print("--limit 不能为负数", file=sys.stderr)
        return 2
    try:
        output_dir = _resolve_output_dir(source_root, args.output_dir)
        selected_ids = _load_job_ids(args.manifest)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"参数/清单错误：{exc}", file=sys.stderr)
        return 2

    candidates = [
        item
        for item in _job_dirs(source_root)
        if selected_ids is None or item.name in selected_ids
    ]
    if args.limit:
        candidates = candidates[: args.limit]
    if selected_ids is not None:
        found = {item.name for item in candidates}
        missing = sorted(selected_ids - found)
        if missing:
            print(f"manifest 中的 job_id 不存在：{missing[:10]}", file=sys.stderr)
            return 2
    if not candidates:
        print("没有可处理的任务目录", file=sys.stderr)
        return 2

    output_dir.mkdir(parents=True, exist_ok=False)
    prepared: List[Dict[str, Any]] = []
    preparation_failures: List[Dict[str, str]] = []
    for source_dir in candidates:
        try:
            prepared.append(
                _prepare_copy(
                    source_dir,
                    output_dir,
                    source_root=source_root,
                    correct_metadata=not args.no_metadata_correction,
                )
            )
        except Exception as exc:  # noqa: BLE001 - 单任务失败须留痕并继续审计
            preparation_failures.append(
                {
                    "job_id": source_dir.name,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )

    asyncio.run(_run_jobs(output_dir, prepared))
    replay = _build_replay_report(output_dir)
    output_jobs = replay.get("jobs") if isinstance(replay.get("jobs"), list) else []
    run_failures = [
        {
            "job_id": str(job.get("job_id") or ""),
            "error": str(job.get("error") or "pipeline_error"),
        }
        for job in output_jobs
        if str(job.get("status") or "") == "error"
    ]
    report = {
        **replay,
        "mode": "offline_reprocess",
        "limitations": [
            *replay.get("limitations", []),
            "输出来自当前代码在隔离副本中的重跑，不等同于历史结果的无损回填",
            "未连接 PostgreSQL，不能证明 report_id 已在生产数据库重新分配/去冲突",
            "未调用 AI；AI 相关结论不在本报告范围内",
        ],
        "reprocess": {
            "source_root": source_root.as_posix(),
            "output_root": output_dir.as_posix(),
            "candidate_count": len(candidates),
            "prepared_count": len(prepared),
            "preparation_failures": preparation_failures,
            "run_failures": run_failures,
            "metadata_corrected_count": sum(
                1 for item in prepared if item["metadata_corrected"]
            ),
            "database_identity_revalidated": False,
            "jobs": prepared,
        },
    }
    gate_checks = _evaluate_gate(report)
    report["gate"] = {
        "passed": all(item["passed"] for item in gate_checks),
        "checks": gate_checks,
        "note": (
            "这是隔离重跑报告；即使结构性检查全绿，也必须另行在生产数据库"
            "复核 report_id，不能替代 793 任务基线或 Golden Corpus。"
        ),
    }
    report_path = output_dir / "reprocess-report.json"
    _write_json(report_path, report)
    print(
        json.dumps(
            {
                "candidate_count": len(candidates),
                "prepared_count": len(prepared),
                "preparation_failures": len(preparation_failures),
                "run_failures": len(run_failures),
                "job_total": report["summary"].get("job_total"),
                "report": report_path.as_posix(),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if report["gate"]["passed"] and not preparation_failures and not run_failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
