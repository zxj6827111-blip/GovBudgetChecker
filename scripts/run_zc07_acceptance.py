#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ZC-07 冻结真值与真实端到端发布门禁。

默认只做环境/语料盘点，不执行 PDF 回放，不连接业务数据库，不启动容器。
提供完整冻结数据集、逐文档回放结果及真实端到端证据后，才计算可计算指标。
缺少真值或端到端证据时仍保存 JSON 报告并返回非零，绝不以空样本报 100%。

数据集清单格式见 ``docs/ZC07_真实验收数据格式.md``。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.schemas.review_report import normalize_manual_review
from src.services.truth_grouping import canonical_truth_group

SPLITS = {"development", "blind"}
REPORT_KINDS = {"budget", "final"}
ORGANIZATION_TYPES = {"department", "unit"}
SOURCE_TYPES = {"native", "scanned"}
REQUIRED_E2E_SCENARIOS = {
    "native_budget",
    "native_final_known_issue",
    "scanned_budget",
    "mixed_final_manual_correction",
}
E2E_SCENARIO_SHAPES = {
    "native_budget": {"document_type": "budget", "source_type": "native", "mixed": False},
    "native_final_known_issue": {"document_type": "final", "source_type": "native", "mixed": False},
    "scanned_budget": {"document_type": "budget", "source_type": "scanned", "mixed": False},
    "mixed_final_manual_correction": {"document_type": "final", "mixed": True},
}
STRATA = {
    (kind, organization, source)
    for kind in REPORT_KINDS
    for organization in ORGANIZATION_TYPES
    for source in SOURCE_TYPES
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON 顶层必须是对象：{path}")
    return value


def _repo_relative(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def _inventory(root: Path) -> dict[str, Any]:
    """只统计现有登记/冻结材料，不把登记材料当成验收集。"""
    manifest_path = root / "corpus" / "manifest.csv"
    registered = 0
    if manifest_path.is_file():
        import csv

        with manifest_path.open(encoding="utf-8-sig", newline="") as handle:
            registered = sum(1 for row in csv.DictReader(handle) if row.get("doc_id"))

    frozen = 0
    human_reviewed = 0
    source_pdf_verified = 0
    real_defect_groups = 0
    corpus = root / "corpus"
    if corpus.is_dir():
        for golden_path in sorted(corpus.glob("*/golden.json")):
            try:
                golden = _read_json(golden_path)
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            labels = golden.get("labels") if isinstance(golden.get("labels"), list) else []
            if golden.get("truth_frozen_at"):
                frozen += 1
            if golden.get("truth_frozen_at") and golden.get("reviewed_by"):
                human_reviewed += 1
                defect_ids = {
                    canonical_truth_group(label)
                    for label in labels
                    if isinstance(label, dict) and label.get("label") == "defect"
                }
                real_defect_groups += len({item for item in defect_ids if item})
                expected_sha = str(golden.get("sha256") or "").strip().lower()
                matching_pdf = [
                    path
                    for path in golden_path.parent.glob("*.pdf")
                    if expected_sha and sha256_file(path) == expected_sha
                ]
                if len(matching_pdf) == 1:
                    source_pdf_verified += 1

    return {
        "registered_materials": registered,
        "golden_files": len(list(corpus.glob("*/golden.json"))) if corpus.is_dir() else 0,
        "frozen_truth_files": frozen,
        "human_reviewed_frozen_truth_files": human_reviewed,
        "human_reviewed_source_pdf_hash_verified": source_pdf_verified,
        "defect_groups_in_human_reviewed_frozen_truth": real_defect_groups,
        "note": "语料登记数和单样本真值不代表 ZC-07 的开发集/盲测集达标。",
    }


def _environment(root: Path) -> dict[str, Any]:
    docker_bin = shutil.which("docker")
    daemon_reachable = False
    docker_error = "docker CLI 未安装"
    if docker_bin:
        try:
            result = subprocess.run(
                [docker_bin, "info", "--format", "{{.ServerVersion}}"],
                capture_output=True,
                text=True,
                timeout=4,
                check=False,
            )
            daemon_reachable = result.returncode == 0 and bool(result.stdout.strip())
            docker_error = "" if daemon_reachable else "Docker daemon 不可用或未启动"
        except (OSError, subprocess.TimeoutExpired):
            docker_error = "Docker daemon 检查失败或超时"

    test_database_configured = bool(
        str(os.environ.get("GOVBUDGET_TEST_DATABASE_URL") or "").strip()
    )
    ocr_manifest = root / "models" / "MANIFEST.sha256"
    ocr_service_configured = ocr_manifest.is_file()
    return {
        "test_database": {
            "status": "configured" if test_database_configured else "missing",
            "variable": "GOVBUDGET_TEST_DATABASE_URL",
            "value_recorded": False,
        },
        "docker": {
            "cli_available": bool(docker_bin),
            "daemon_reachable": daemon_reachable,
            "status": "ready" if daemon_reachable else "missing",
            "detail": docker_error,
        },
        "ocr": {
            "model_manifest_exists": ocr_service_configured,
            "manifest_path": _repo_relative(ocr_manifest, root),
            "status": "manifest_present" if ocr_service_configured else "missing",
            "runtime_verified": False,
            "note": "本预检只盘点清单；真实启动、预热和逐格推理由独立实跑回执证明",
        },
    }


def validate_dataset_manifest(path: Path, root: Path, mode: str = "legacy") -> dict[str, Any]:
    errors: list[str] = []
    documents: list[dict[str, Any]] = []
    by_split: Counter[str] = Counter()
    by_stratum: Counter[tuple[str, str, str, str]] = Counter()
    pdf_hash_owner: dict[str, str] = {}
    replay_missing: list[str] = []
    defect_groups_by_split: Counter[str] = Counter()
    input_hashes: list[dict[str, str]] = []

    try:
        manifest = _read_json(path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {
            "status": "invalid",
            "path": _repo_relative(path, root),
            "sha256": sha256_file(path) if path.is_file() else None,
            "errors": [f"无法读取数据集清单：{exc}"],
            "documents": [],
            "sample_counts": {},
            "strata_counts": {},
            "defect_groups_by_split": {},
            "input_hashes": [],
            "metrics_ready": False,
        }

    if manifest.get("schema_version") != "zc07.dataset.v1":
        errors.append("schema_version 必须为 zc07.dataset.v1")
    rows = manifest.get("documents")
    if not isinstance(rows, list):
        rows = []
        errors.append("documents 必须为数组")

    seen_doc_ids: set[str] = set()
    for index, row in enumerate(rows):
        prefix = f"documents[{index}]"
        if not isinstance(row, dict):
            errors.append(f"{prefix} 必须为对象")
            continue
        doc_id = str(row.get("doc_id") or "").strip()
        split = str(row.get("split") or "").strip()
        kind = str(row.get("document_type") or "").strip()
        organization = str(row.get("organization_type") or "").strip()
        source = str(row.get("source_type") or "").strip()
        mixed = bool(row.get("mixed", False))
        if not doc_id:
            errors.append(f"{prefix}.doc_id 缺失")
            continue
        if (
            doc_id in {".", ".."}
            or Path(doc_id).name != doc_id
            or "/" in doc_id
            or "\\" in doc_id
            or "\x00" in doc_id
        ):
            errors.append(f"{prefix}.doc_id 不能包含路径分隔或目录跳转")
            continue
        if doc_id in seen_doc_ids:
            errors.append(f"DOC-ID 重复：{doc_id}")
        seen_doc_ids.add(doc_id)
        if split not in SPLITS:
            errors.append(f"{doc_id}: split 必须为 development 或 blind")
        if kind not in REPORT_KINDS:
            errors.append(f"{doc_id}: document_type 必须为 budget 或 final")
        if organization not in ORGANIZATION_TYPES:
            errors.append(f"{doc_id}: organization_type 必须为 department 或 unit")
        if source not in SOURCE_TYPES:
            errors.append(f"{doc_id}: source_type 必须为 native 或 scanned")
        if split in SPLITS:
            by_split[split] += 1
        if split in SPLITS and kind in REPORT_KINDS and organization in ORGANIZATION_TYPES and source in SOURCE_TYPES:
            by_stratum[(split, kind, organization, source)] += 1

        doc_dir = root / "corpus" / doc_id
        golden_path = doc_dir / "golden.json"
        if not golden_path.is_file():
            errors.append(f"{doc_id}: 缺少 corpus/{doc_id}/golden.json")
            continue
        try:
            golden = _read_json(golden_path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            errors.append(f"{doc_id}: golden.json 无效：{exc}")
            continue
        golden_hash = str(golden.get("sha256") or "").strip().lower()
        if golden.get("doc_id") != doc_id:
            errors.append(f"{doc_id}: golden doc_id 不匹配")
        golden_kind = str(golden.get("report_kind") or "").strip().lower()
        if golden_kind and golden_kind != kind:
            errors.append(f"{doc_id}: manifest 文种 {kind} 与 golden report_kind {golden_kind} 不一致")
        if not golden.get("truth_frozen_at"):
            errors.append(f"{doc_id}: 真值未冻结")
        if not isinstance(golden.get("reviewed_by"), list) or not golden.get("reviewed_by"):
            errors.append(f"{doc_id}: 缺少人工复核记录 reviewed_by")
        labels = golden.get("labels")
        if not isinstance(labels, list) or not labels:
            errors.append(f"{doc_id}: labels 必须包含完整人工标注，不能留空")
        else:
            for label_index, label in enumerate(labels):
                if not isinstance(label, dict):
                    errors.append(f"{doc_id}: labels[{label_index}] 必须为对象")
                    continue
                label_kind = str(label.get("label") or "")
                if label_kind not in {"defect", "rounding_hint", "manual_review", "acceptable"}:
                    errors.append(f"{doc_id}: labels[{label_index}] 的 label 类型无效")
                if not str(label.get("annotation_id") or "").strip():
                    errors.append(f"{doc_id}: labels[{label_index}] 缺少 annotation_id")
                if label_kind in {"defect", "rounding_hint", "manual_review"} and not str(
                    label.get("truth_id") or ""
                ).strip():
                    errors.append(f"{doc_id}: labels[{label_index}] 缺少 truth_id")
        if not golden_hash or len(golden_hash) != 64:
            errors.append(f"{doc_id}: golden SHA-256 缺失或格式错误")

        pdfs = sorted(doc_dir.glob("*.pdf"))
        matching_pdfs = [pdf for pdf in pdfs if golden_hash and sha256_file(pdf) == golden_hash]
        if len(matching_pdfs) != 1:
            errors.append(f"{doc_id}: 真实原件 PDF 与 golden SHA-256 唯一匹配数为 {len(matching_pdfs)}")
        else:
            prior = pdf_hash_owner.get(golden_hash)
            if prior:
                errors.append(f"{doc_id}: 与 {prior} 重复使用同一原件 SHA-256")
            pdf_hash_owner[golden_hash] = doc_id
            input_hashes.append({"doc_id": doc_id, "pdf_sha256": golden_hash})

        if split in SPLITS:
            labels = golden.get("labels") if isinstance(golden.get("labels"), list) else []
            groups = {
                canonical_truth_group(label)
                for label in labels
                if isinstance(label, dict) and label.get("label") == "defect"
            }
            defect_groups_by_split[split] += len({group for group in groups if group})

        replay_value = str(row.get("replay_path") or "").strip()
        if replay_value:
            replay_path = Path(replay_value)
            if not replay_path.is_absolute():
                replay_path = root / replay_path
            if not replay_path.is_file():
                replay_missing.append(doc_id)
            else:
                try:
                    replay_payload = _read_json(replay_path)
                except (OSError, ValueError, json.JSONDecodeError) as exc:
                    errors.append(f"{doc_id}: replay JSON 无效：{exc}")
                else:
                    if replay_payload.get("doc_id") != doc_id:
                        errors.append(f"{doc_id}: replay doc_id 不匹配")
                    if replay_payload.get("sha256") != golden_hash:
                        errors.append(f"{doc_id}: replay SHA-256 与冻结原件不匹配")
                    if not isinstance(replay_payload.get(mode), dict):
                        errors.append(f"{doc_id}: replay 缺少 {mode} 生产链结果")
                    input_hashes.append({"doc_id": f"{doc_id}:replay", "sha256": sha256_file(replay_path)})
        else:
            replay_missing.append(doc_id)

        documents.append(
            {
                "doc_id": doc_id,
                "split": split,
                "document_type": kind,
                "organization_type": organization,
                "source_type": source,
                "mixed": mixed,
                "golden_sha256": golden_hash or None,
            }
        )

    for split in sorted(SPLITS):
        if by_split[split] < 32:
            errors.append(f"{split}: 材料数 {by_split[split]}/32")
        for kind, organization, source in sorted(STRATA):
            count = by_stratum[(split, kind, organization, source)]
            if count < 4:
                errors.append(
                    f"{split} 分层 {kind}/{organization}/{source}: {count}/4"
                )
    if defect_groups_by_split["blind"] < 100:
        errors.append(f"盲测真实缺陷组 {defect_groups_by_split['blind']}/100")

    return {
        "status": "ready" if not errors and not replay_missing else "incomplete",
        "path": _repo_relative(path, root),
        "sha256": sha256_file(path),
        "errors": errors,
        "replay_missing_doc_ids": replay_missing,
        "documents": documents,
        "sample_counts": dict(by_split),
        "strata_counts": {
            "/".join(key): count for key, count in sorted(by_stratum.items())
        },
        "defect_groups_by_split": dict(defect_groups_by_split),
        "input_hashes": input_hashes,
        "metrics_ready": not errors and not replay_missing and bool(documents),
    }


def validate_e2e_evidence(path: Path | None, root: Path) -> dict[str, Any]:
    if path is None:
        return {"status": "missing", "errors": ["未提供真实 API/Worker/PostgreSQL/OCR E2E 证据文件"], "scenarios": {}}
    try:
        payload = _read_json(path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {"status": "invalid", "path": _repo_relative(path, root), "errors": [str(exc)], "scenarios": {}}
    errors: list[str] = []
    if payload.get("schema_version") != "zc07.e2e-evidence.v1":
        errors.append("schema_version 必须为 zc07.e2e-evidence.v1")
    scenarios = payload.get("scenarios") if isinstance(payload.get("scenarios"), dict) else {}
    missing = REQUIRED_E2E_SCENARIOS - set(scenarios)
    errors.extend(f"缺少真实 E2E 场景：{name}" for name in sorted(missing))
    verified: dict[str, Any] = {}
    for name in sorted(REQUIRED_E2E_SCENARIOS & set(scenarios)):
        item = scenarios[name]
        if not isinstance(item, dict):
            errors.append(f"{name}: 场景记录必须为对象")
            continue
        required_flags = ["api", "worker_consumed", "postgres", "original_evidence", "downloads_succeeded"]
        if name == "scanned_budget":
            required_flags.append("ocr")
        if name == "native_final_known_issue":
            required_flags.append("known_issue_detected")
        if name == "mixed_final_manual_correction":
            required_flags.extend(["ocr", "manual_correction", "reanalyzed_after_correction"])
        failed_flags = [flag for flag in required_flags if item.get(flag) is not True]
        if item.get("status") != "passed":
            failed_flags.append("status=passed")
        for field, expected in E2E_SCENARIO_SHAPES[name].items():
            actual = item.get(field)
            if type(actual) is not type(expected) or actual != expected:
                failed_flags.append(f"{field}={expected}")
        if name == "mixed_final_manual_correction" and item.get("source_type") not in SOURCE_TYPES:
            failed_flags.append("source_type=native|scanned")
        identity_fields = [
            "run_id",
            "doc_id",
            "job_id",
            "api_build_revision",
            "worker_build_revision",
            "postgres_identity",
            "source_pdf_sha256",
            "export_sha256",
        ]
        for field in identity_fields:
            value = str(item.get(field) or "").strip()
            if not value:
                failed_flags.append(field)
            if field in {"source_pdf_sha256", "export_sha256"} and value and (
                len(value) != 64 or any(char not in "0123456789abcdefABCDEF" for char in value)
            ):
                failed_flags.append(f"{field} 格式错误")
        # AC-14：自报布尔不再单独作数——必须核对真实文件与版本一致性。
        api_revision = str(item.get("api_build_revision") or "").strip()
        worker_revision = str(item.get("worker_build_revision") or "").strip()
        if api_revision and worker_revision and api_revision != worker_revision:
            failed_flags.append("api/Worker 版本不一致")

        def _verify_hashed_file(
            scenario: dict[str, Any],
            path_field: str,
            sha_field: str,
            expected_sha: str,
            label: str,
            errors: list[str],
        ) -> None:
            raw_path = str(scenario.get(path_field) or "").strip()
            if not raw_path:
                errors.append(f"{path_field} 缺失")
                return
            file_path = Path(raw_path)
            if not file_path.is_absolute():
                file_path = root / file_path
            if not file_path.is_file():
                errors.append(f"{label} 文件不存在: {raw_path}")
                return
            actual_sha = sha256_file(file_path)
            if actual_sha != expected_sha:
                errors.append(f"{label} 哈希与 {sha_field} 不符")

        source_sha = str(item.get("source_pdf_sha256") or "").strip().lower()
        if source_sha:
            _verify_hashed_file(item, "source_pdf_path", "source_pdf_sha256", source_sha, "源 PDF", failed_flags)
        export_sha = str(item.get("export_sha256") or "").strip().lower()
        if export_sha:
            _verify_hashed_file(item, "export_path", "export_sha256", export_sha, "导出文件", failed_flags)

        trace_raw = str(item.get("trace_path") or "").strip()
        if not trace_raw:
            failed_flags.append("trace_path 缺失")
        else:
            trace_path = Path(trace_raw)
            if not trace_path.is_absolute():
                trace_path = root / trace_path
            if not trace_path.is_file():
                failed_flags.append(f"trace 文件不存在: {trace_raw}")
            else:
                try:
                    trace_payload = _read_json(trace_path)
                except (OSError, ValueError, json.JSONDecodeError) as exc:
                    trace_payload = None
                    failed_flags.append(f"trace 文件不可解析: {exc}")
                if isinstance(trace_payload, dict):
                    if not trace_payload:
                        failed_flags.append("trace 内容为空对象")
                    trace_job = str(trace_payload.get("job_id") or "").strip()
                    scenario_job = str(item.get("job_id") or "").strip()
                    if scenario_job and trace_job and trace_job != scenario_job:
                        failed_flags.append("trace.job_id 与场景 job_id 不一致")
                    stages = trace_payload.get("stages") or trace_payload.get("events")
                    if isinstance(stages, list) and not stages:
                        failed_flags.append("trace 阶段记录为空")
                elif trace_payload is not None:
                    failed_flags.append("trace 必须为 JSON 对象")
        if not isinstance(item.get("reviewed_by"), list) or not item.get("reviewed_by"):
            failed_flags.append("reviewed_by")
        if name == "native_final_known_issue" and not str(item.get("known_issue_truth_id") or "").strip():
            failed_flags.append("known_issue_truth_id")
        if name in {"scanned_budget", "mixed_final_manual_correction"}:
            ocr_sha = str(item.get("ocr_model_sha256") or "").strip()
            if len(ocr_sha) != 64 or any(char not in "0123456789abcdefABCDEF" for char in ocr_sha):
                failed_flags.append("ocr_model_sha256")
        if name == "mixed_final_manual_correction":
            correction_id = str(item.get("correction_id") or "").strip()
            revision_before = str(item.get("analysis_revision_before") or "").strip()
            revision_after = str(item.get("analysis_revision_after") or "").strip()
            if not correction_id:
                failed_flags.append("correction_id")
            if not revision_before or not revision_after or revision_before == revision_after:
                failed_flags.append("校正前后 analysis_revision 必须齐备且不同")
            if item.get("reanalysis_status") != "completed":
                failed_flags.append("reanalysis_status=completed")
        artifact = Path(str(item.get("artifact_path") or ""))
        if not artifact.is_absolute():
            artifact = root / artifact
        expected_artifact_sha = str(item.get("artifact_sha256") or "").strip().lower()
        if not artifact.is_file() or not expected_artifact_sha:
            failed_flags.append("artifact_path/artifact_sha256")
            artifact_sha = None
        else:
            artifact_sha = sha256_file(artifact)
            if artifact_sha != expected_artifact_sha:
                failed_flags.append("artifact SHA-256 mismatch")
        if failed_flags:
            errors.append(f"{name}: 未满足 {', '.join(failed_flags)}")
        verified[name] = {
            "status": "passed" if not failed_flags else "failed",
            "artifact_path": _repo_relative(artifact, root) if artifact.exists() else str(artifact),
            "artifact_sha256": artifact_sha,
            "checks": {key: item.get(key) is True for key in required_flags},
        }
    return {
        "status": "ready" if not errors else "incomplete",
        "path": _repo_relative(path, root),
        "sha256": sha256_file(path),
        "errors": errors,
        "scenarios": verified,
    }


def _wilson_interval(successes: int, trials: int, z: float = 1.959963984540054) -> list[float] | None:
    """95% Wilson interval for a descriptive binomial proportion."""
    if trials <= 0:
        return None
    proportion = successes / trials
    denominator = 1 + z * z / trials
    center = (proportion + z * z / (2 * trials)) / denominator
    radius = z * ((proportion * (1 - proportion) / trials + z * z / (4 * trials * trials)) ** 0.5) / denominator
    return [round(max(0.0, center - radius), 4), round(min(1.0, center + radius), 4)]


def _aggregate_evaluations(rows: list[dict[str, Any]]) -> dict[str, Any]:
    tp = sum(int(row["report"].get("tp") or 0) for row in rows)
    fp = sum(int(row["report"].get("fp") or 0) for row in rows)
    fn = sum(int(row["report"].get("fn") or 0) for row in rows)
    precision_denominator = tp + fp
    recall_denominator = tp + fn
    precision = tp / precision_denominator if precision_denominator else None
    recall = tp / recall_denominator if recall_denominator else None
    high_misses = sum(int(row.get("high_or_critical_missed_groups") or 0) for row in rows)
    location_pairs = [
        (float(row["report"]["page_accuracy"]), int(row["report"].get("tp") or 0))
        for row in rows
        if row["report"].get("page_accuracy") is not None
        and int(row["report"].get("tp") or 0) > 0
    ]
    weighted_page_accuracy = (
        sum(value * weight for value, weight in location_pairs) / sum(weight for _, weight in location_pairs)
        if location_pairs
        else None
    )
    return {
        "document_count": len(rows),
        "defect_groups": tp + fn,
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": round(precision, 4) if precision is not None else None,
        "precision_wilson_95_ci": _wilson_interval(tp, precision_denominator),
        "recall": round(recall, 4) if recall is not None else None,
        "recall_wilson_95_ci": _wilson_interval(tp, recall_denominator),
        "high_or_critical_missed_groups": high_misses,
        "page_accuracy_tp_weighted": round(weighted_page_accuracy, 4) if weighted_page_accuracy is not None else None,
    }


def _iter_run_issues(run: dict[str, Any]) -> list[dict[str, Any]]:
    """按版本化输入契约读取正式问题（RC-13：合法回放不得被当空）。

    接受三种形态：
    - ``issues``：list 或 {"all": list}（新统一报告）；
    - ``findings`` / ``rule_findings``：list（正式回放/规则主链产物）。
    除此之外的结构（如 {"items": ...}）一律不猜——返回空并在调用方
    以 not_computable/缺记录如实呈现。
    """
    for key in ("issues", "findings", "rule_findings"):
        issues = run.get(key)
        if isinstance(issues, dict):
            merged = issues.get("all")
            if isinstance(merged, list):
                return [item for item in merged if isinstance(item, dict)]
            continue
        if isinstance(issues, list):
            return [item for item in issues if isinstance(item, dict)]
    return []


def _authenticated_reference(item: Any, source_sha: str, pdf_path: Optional[Path],
                             scan_manifest: Optional[dict] = None) -> bool:
    if not isinstance(item, dict):
        return False
    file_hash = str(item.get("file_hash") or "").strip().lower()
    physical_page = item.get("physical_page")
    source_id = str(item.get("source_cell_id") or item.get("source_block_id") or "").strip()
    quote = str(item.get("quote") or "").strip()
    evidence_id = str(item.get("evidence_id") or "").strip().lower()
    if (
        not re.fullmatch(r"[0-9a-f]{64}", file_hash)
        or file_hash != source_sha
        or not isinstance(physical_page, int)
        or isinstance(physical_page, bool)
        or physical_page < 1
        or not source_id
        or not quote
        or not re.fullmatch(r"[0-9a-f]{16}", evidence_id)
        or str(item.get("verification_status") or "") != "verified"
    ):
        return False
    expected_id = hashlib.sha1(
        "|".join((file_hash, str(physical_page), source_id, quote)).encode("utf-8")
    ).hexdigest()[:16]
    if evidence_id != expected_id or pdf_path is None or not pdf_path.is_file():
        return False
    if item.get("source_method") == "ocr":
        from src.services.scan_evidence_certification import verify_scan_reference
        return verify_scan_reference(item,pdf_path,scan_manifest)
    try:
        import fitz

        with fitz.open(pdf_path) as document:
            if physical_page > document.page_count:
                return False
            page = document.load_page(physical_page - 1)
            page_text = "".join((page.get_text() or "").split())
            needle = "".join(quote.split())
            if not needle or needle not in page_text:
                return False
            if item.get("location_level") in {"exact_cell", "exact_span"}:
                bbox = item.get("bbox")
                if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
                    return False
                x0, y0, x1, y1 = (float(value) for value in bbox)
                rect = page.rect
                if x0 < rect.x0 or y0 < rect.y0 or x1 > rect.x1 or y1 > rect.y1:
                    return False
                words = page.get_text("words") or []
                region = "".join(
                    "".join(str(word[4] or "") for word in words
                            if not (word[2] < x0 or word[0] > x1 or word[3] < y0 or word[1] > y1)).split()
                )
                if needle not in region:
                    return False
        return True
    except (ImportError, OSError, TypeError, ValueError):
        return False


def _frozen_scan_manifest(golden: dict, root: Path, doc_id: str) -> Optional[dict]:
    from src.services.scan_evidence_certification import load_attestations
    name = str(golden.get("scan_attestations_file") or "")
    digest = str(golden.get("scan_attestations_sha256") or "")
    if not name or Path(name).is_absolute() or not re.fullmatch(r"[a-f0-9]{64}", digest):
        return None
    corpus_root = (root / "corpus").resolve()
    directory = (corpus_root / doc_id).resolve()
    if corpus_root not in directory.parents:
        return None
    path = (directory / name).resolve()
    if directory not in path.parents:
        return None
    return load_attestations(path,digest)


def _evaluate_evidence_authenticity(evaluated: list[dict[str, Any]], root: Path) -> dict[str, Any]:
    """AC-14：证据真实性率——逐条 evidence_verification 三态聚合。

    RC-13：分母是**全部正式问题**——缺核验记录的问题必须计入
    ``missing_record``（不算真实），否则"只给 1 条问题配记录"就能
    把真实性率刷成 1.0。verified+结构完整才算真实；failed（伪造引文/
    页码越界）与缺记录计入违规。
    """
    total = authentic = failed = unverified = missing_record = source_identity_failures = 0
    for row in evaluated:
        golden = row.get("golden") if isinstance(row.get("golden"), dict) else {}
        source_sha = str(golden.get("sha256") or "").strip().lower()
        doc_id = str(row.get("doc_id") or row.get("report", {}).get("doc_id") or "")
        source_candidates = sorted((root / "corpus" / doc_id).glob("*.pdf")) if doc_id else []
        source_pdfs = [path for path in source_candidates if path.is_file() and source_sha and sha256_file(path) == source_sha]
        source_pdf = source_pdfs[0] if len(source_pdfs) == 1 else None
        scan_manifest = _frozen_scan_manifest(golden,root,doc_id)
        for finding in _iter_run_issues(row.get("run") or {}):
            total += 1
            verification = finding.get("evidence_verification")
            if not isinstance(verification, dict):
                missing_record += 1
                continue
            status = str(verification.get("status") or "")
            evidence = finding.get("evidence")
            if not isinstance(evidence, list):
                evidence = []
            identity_complete = bool(evidence) and all(
                _authenticated_reference(item, source_sha, source_pdf,scan_manifest) for item in evidence
            )
            if status == "verified" and identity_complete:
                authentic += 1
            elif status == "verified":
                unverified += 1
                source_identity_failures += 1
            elif status == "failed":
                failed += 1
            else:
                unverified += 1
    if total == 0:
        return {"status": "not_computable", "reason": "回放结果未含正式问题（无证据真实性样本）"}
    return {
        "status": "computed",
        "total": total,
        "authentic": authentic,
        "failed": failed,
        "unverified": unverified,
        "missing_record": missing_record,
        "source_identity_failures": source_identity_failures,
        "authenticity_rate": round(authentic / total, 4),
        "violations": failed + missing_record,
    }


def _evaluate_receipts(evaluated: list[dict[str, Any]]) -> dict[str, Any]:
    """AC-14：回执覆盖率与必查子项完成率——关系级回执聚合。

    RC-13：分母优先用冻结真值清单里的 ``applicable_sub_checks``（应检
    关系全集）；缺回执/重复回执按 (rule_id, sub_check_id) 去重后计入
    未完成——"只给 1 条 pass 回执"不能再把完成率刷成 1.0。
    """
    receipt_total = 0
    with_operands = 0
    completed = 0
    uncompleted_reasons: dict[str, int] = {}
    expected_total = 0
    expected_completed = 0
    expected_receipt_covered = 0
    expected_with_operands = 0
    denominator_errors: list[str] = []
    conflicting_receipts: list[str] = []
    for row in evaluated:
        doc_id = str(row.get("doc_id") or row.get("report", {}).get("doc_id") or "unknown")
        meta = ((row.get("run") or {}).get("meta") or {})
        receipts = meta.get("check_receipts")
        receipts_by_key: dict[tuple[str, str], list[dict[str, Any]]] = {}
        if isinstance(receipts, list):
            for receipt in receipts:
                if not isinstance(receipt, dict) or not str(receipt.get("sub_check_id") or ""):
                    continue
                key = (str(receipt.get("rule_id") or ""), str(receipt.get("sub_check_id") or ""))
                receipts_by_key.setdefault(key, []).append(receipt)
        row_receipts: list[dict[str, Any]] = []
        for key, duplicates in receipts_by_key.items():
            signatures = {
                json.dumps(receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
                for receipt in duplicates
            }
            if len(signatures) > 1:
                conflicting_receipts.append(f"{doc_id}:{key[0]}:{key[1]}")
                continue
            row_receipts.append(duplicates[0])
        for receipt in row_receipts:
            receipt_total += 1
            if receipt.get("operand_fact_ids") and receipt.get("formula"):
                with_operands += 1
            status = str(receipt.get("status") or "")
            if status in {"pass", "fail", "not_applicable"}:
                completed += 1
            else:
                reason = str(receipt.get("incomplete_reason") or status or "unknown")
                uncompleted_reasons[reason] = uncompleted_reasons.get(reason, 0) + 1
        golden = row.get("golden") if isinstance(row.get("golden"), dict) else {}
        expected_ids = golden.get("applicable_sub_checks")
        if not isinstance(expected_ids, list) or not expected_ids:
            denominator_errors.append(f"{doc_id}: 缺少非空冻结 applicable_sub_checks 分母")
            continue
        if isinstance(expected_ids, list):
            expected_keys: set[tuple[str, str]] = set()
            for index, item in enumerate(expected_ids):
                if isinstance(item, dict):
                    rule_id = item["rule_id"].strip() if isinstance(item.get("rule_id"), str) else ""
                    sub_id = item["sub_check_id"].strip() if isinstance(item.get("sub_check_id"), str) else ""
                else:
                    raw_id = item.strip() if isinstance(item, str) else ""
                    separator = "::" if "::" in raw_id else ":"
                    rule_id, _, sub_id = raw_id.partition(separator)
                    rule_id, sub_id = rule_id.strip(), sub_id.strip()
                if not rule_id or not sub_id:
                    denominator_errors.append(f"{doc_id}: applicable_sub_checks[{index}] 缺少 rule_id/sub_check_id")
                    continue
                key = (rule_id, sub_id)
                if key in expected_keys:
                    denominator_errors.append(f"{doc_id}: 冻结适用关系重复 {rule_id}:{sub_id}")
                expected_keys.add(key)
            expected_total += len(expected_keys)
            covered_statuses = {
                (str(r.get("rule_id") or ""), str(r.get("sub_check_id") or "")): str(r.get("status") or "")
                for r in row_receipts
            }
            for key in expected_keys:
                matching_receipt = next(
                    (receipt for receipt in row_receipts
                     if (str(receipt.get("rule_id") or ""), str(receipt.get("sub_check_id") or "")) == key),
                    None,
                )
                status = covered_statuses.get(key, "")
                incomplete_reason = str((matching_receipt or {}).get("incomplete_reason") or "").strip()
                status_is_valid = status in {
                    "pass", "fail", "not_applicable", "insufficient_data", "parse_error", "execution_error"
                }
                reason_is_complete = status in {"pass", "fail", "not_applicable"} or bool(incomplete_reason)
                if matching_receipt and status_is_valid and reason_is_complete:
                    expected_receipt_covered += 1
                if status in {"pass", "fail", "not_applicable"}:
                    expected_completed += 1
                if matching_receipt and matching_receipt.get("operand_fact_ids") and matching_receipt.get("formula"):
                    expected_with_operands += 1
    if denominator_errors or conflicting_receipts:
        details = [*denominator_errors, *(
            f"{item}: 同一关系存在冲突回执" for item in conflicting_receipts
        )]
        return {
            "status": "not_computable",
            "reason": "；".join(details[:12]),
            "denominator_errors": denominator_errors,
            "conflicting_receipts": conflicting_receipts,
            "receipt_total": receipt_total,
        }
    if expected_total:
        return {
            "status": "computed",
            "denominator": "frozen_applicable_sub_checks",
            "expected_total": expected_total,
            "expected_completed": expected_completed,
            "expected_receipt_covered": expected_receipt_covered,
            "completion_rate": round(expected_completed / expected_total, 4),
            "receipt_coverage_rate": round(expected_receipt_covered / expected_total, 4),
            "receipt_total": receipt_total,
            "operand_formula_coverage": round(expected_with_operands / expected_total, 4),
            "uncompleted_reasons": uncompleted_reasons,
        }
    return {
        "status": "not_computable",
        "reason": "冻结 applicable_sub_checks 总数为 0；不能用实际回执数量推定完成率",
        "receipt_total": receipt_total,
    }


def _bbox_overlaps(candidate: Any, truth: Any) -> bool:
    """候选 bbox 与真值 bbox 的相交面积占比（相对真值）≥ 0.5 视为命中。"""
    if not isinstance(candidate, (list, tuple)) or not isinstance(truth, (list, tuple)):
        return False
    if len(candidate) != 4 or len(truth) != 4:
        return False
    try:
        cx0, cy0, cx1, cy1 = (float(v) for v in candidate)
        tx0, ty0, tx1, ty1 = (float(v) for v in truth)
    except (TypeError, ValueError):
        return False
    ix0, iy0 = max(cx0, tx0), max(cy0, ty0)
    ix1, iy1 = min(cx1, tx1), min(cy1, ty1)
    if ix1 <= ix0 or iy1 <= iy0:
        return False
    truth_area = max((tx1 - tx0) * (ty1 - ty0), 1e-6)
    return ((ix1 - ix0) * (iy1 - iy0)) / truth_area >= 0.5


def _evaluate_exact_location(evaluated: list[dict[str, Any]], root: Path) -> dict[str, Any]:
    """AC-14：精确定位率——finding 的页与区域必须落入冻结真值区域。

    RC-13：不得只看自报 exact/verified/bbox 标签——必须与
    ``truth_region``（page + bbox）比对：页相同且候选 bbox 与真值
    bbox 相交面积占真值 ≥ 0.5 才算命中；页错或区域远离即计失败。
    """
    truth_total = 0
    exact_hits = 0
    missing_truth_regions: list[str] = []
    for row in evaluated:
        golden = row.get("golden") or {}
        source_sha = str(golden.get("sha256") or "").strip().lower()
        doc_id = str(row.get("doc_id") or row.get("report", {}).get("doc_id") or "")
        source_candidates = sorted((root / "corpus" / doc_id).glob("*.pdf")) if doc_id else []
        source_pdfs = [
            path for path in source_candidates
            if path.is_file() and source_sha and sha256_file(path) == source_sha
        ]
        source_pdf = source_pdfs[0] if len(source_pdfs) == 1 else None
        annotations = golden.get("annotations")
        if isinstance(golden.get("labels"), list):
            target_annotations = [
                item for item in golden["labels"]
                if isinstance(item, dict) and item.get("label") == "defect"
            ]
        elif isinstance(annotations, list):
            target_annotations = [item for item in annotations if isinstance(item, dict)]
        else:
            continue
        if not target_annotations:
            continue
        regions: dict[str, dict[str, Any]] = {}
        truth_total += len(target_annotations)
        for item in target_annotations:
            annotation_id = str(item.get("annotation_id") or "").strip()
            region = item.get("truth_region")
            if not annotation_id or not isinstance(region, dict):
                missing_truth_regions.append(f"{doc_id}:{annotation_id or '(缺 annotation_id)'}")
                continue
            regions[annotation_id] = region
        matched_by_annotation: dict[str, list[dict[str, Any]]] = {}
        for item in row["report"].get("matched", []):
            if isinstance(item, dict):
                matched_by_annotation.setdefault(str(item.get("annotation_id") or ""), []).append(item)
        for annotation_id, region in regions.items():
            matches = matched_by_annotation.get(annotation_id, [])
            if len(matches) != 1:
                continue
            finding = matches[0].get("finding") if isinstance(matches[0].get("finding"), dict) else {}
            location = finding.get("location") if isinstance(finding.get("location"), dict) else {}
            candidate_bbox = finding.get("bbox") or location.get("bbox")
            finding_page = finding.get("page_number") or finding.get("page") or location.get("page")
            truth_page = region.get("page")
            try:
                page_hit = finding_page is not None and truth_page is not None and int(finding_page) == int(truth_page)
            except (TypeError, ValueError):
                page_hit = False
            location_level = str(finding.get("location_level") or "")
            verification_status = str(finding.get("verification_status") or "")
            evidence = finding.get("evidence")
            authenticated = (
                isinstance(evidence, list)
                and bool(evidence)
                and all(_authenticated_reference(item, source_sha, source_pdf,
                        _frozen_scan_manifest(golden,root,doc_id)) for item in evidence)
            )
            candidate_box = None
            try:
                candidate_box = tuple(round(float(value), 4) for value in candidate_bbox)
            except (TypeError, ValueError):
                pass
            exact_source_match = False
            if candidate_box and isinstance(evidence, list):
                for evidence_item in evidence:
                    if not isinstance(evidence_item, dict):
                        continue
                    if (
                        str(evidence_item.get("location_level") or "") not in {"exact_cell", "exact_span"}
                        or str(evidence_item.get("verification_status") or "") != "verified"
                        or evidence_item.get("physical_page") != finding_page
                    ):
                        continue
                    try:
                        evidence_box = tuple(round(float(value), 4) for value in evidence_item.get("bbox", []))
                    except (TypeError, ValueError):
                        continue
                    if len(evidence_box) == 4 and evidence_box == candidate_box:
                        exact_source_match = True
                        break
            precise = (
                location_level in {"exact_cell", "exact_span"}
                and verification_status == "verified"
                and authenticated
                and exact_source_match
            )
            bounded = False
            try:
                cx0, cy0, cx1, cy1 = (float(v) for v in candidate_bbox)
                tx0, ty0, tx1, ty1 = (float(v) for v in region.get("bbox"))
                candidate_area = max(0.0, cx1 - cx0) * max(0.0, cy1 - cy0)
                truth_area = max(0.0, tx1 - tx0) * max(0.0, ty1 - ty0)
                bounded = truth_area > 0 and candidate_area <= truth_area * 4
            except (TypeError, ValueError):
                bounded = False
            if precise and bounded and page_hit and _bbox_overlaps(candidate_bbox, region.get("bbox")):
                exact_hits += 1
    if missing_truth_regions:
        return {
            "status": "not_computable",
            "truth_total": truth_total,
            "exact_hits": exact_hits,
            "missing_truth_regions": missing_truth_regions,
            "reason": "冻结缺陷真值未全部提供 truth_region（页+bbox）；不能缩小定位分母",
        }
    if truth_total == 0:
        return {
            "status": "not_computable",
            "reason": "语料标注未提供 truth_region（页+bbox）级定位真值；页码命中率不构成精确区域定位率",
        }
    return {
        "status": "computed",
        "truth_total": truth_total,
        "exact_hits": exact_hits,
        "exact_location_rate": round(exact_hits / truth_total, 4),
    }


def _evaluate_repeatability(manifest: dict[str, Any], root: Path, mode: str) -> dict[str, Any]:
    """AC-14：重复运行一致性——同原件/配置/版本的独立重复回放逐项比对。

    两轮必须提供相同的原件 SHA、配置指纹和版本指纹。比较完整问题、事实/
    证据身份、精确位置、关系回执和完成状态；缺基线时不能判 repeatable。
    """
    pairs = [
        row
        for row in manifest.get("documents", [])
        if isinstance(row, dict) and str(row.get("repeat_replay_path") or "").strip()
    ]
    document_count = sum(1 for row in manifest.get("documents", []) if isinstance(row, dict))
    if not pairs or len(pairs) != document_count:
        return {
            "status": "not_computable",
            "reason": (
                f"盲测文档仅 {len(pairs)}/{document_count} 份提供 repeat_replay_path；"
                "每份均须提供同原件/配置/版本的独立重复回放"
            ),
        }
    comparable = 0
    consistent = 0
    unavailable: list[str] = []

    def _basis(payload: dict[str, Any], run: dict[str, Any]) -> dict[str, str]:
        value = payload.get("repeatability_basis")
        if not isinstance(value, dict):
            meta = run.get("meta") if isinstance(run.get("meta"), dict) else {}
            value = meta.get("repeatability_basis")
        if not isinstance(value, dict):
            return {}
        return {
            key: str(value.get(key) or "").strip()
            for key in ("source_sha256", "configuration_fingerprint", "version_fingerprint")
        }

    def _signature(run: dict[str, Any]) -> str:
        issue_rows: list[dict[str, Any]] = []
        for finding in _iter_run_issues(run):
            location = finding.get("location") if isinstance(finding.get("location"), dict) else {}
            issue_rows.append({
                "id": str(finding.get("id") or ""),
                "rule_id": str(finding.get("rule_id") or finding.get("rule") or ""),
                "severity": str(finding.get("severity") or ""),
                "title": str(finding.get("title") or ""),
                "message": str(finding.get("message") or ""),
                "page": finding.get("page_number") or finding.get("page") or location.get("page"),
                "bbox": finding.get("bbox") or location.get("bbox"),
                "location_level": finding.get("location_level") or location.get("location_level"),
                "verification_status": finding.get("verification_status") or location.get("verification_status"),
                "check_relation_id": finding.get("check_relation_id"),
                "fact_ids": sorted(str(value) for value in finding.get("fact_ids", []) if str(value)),
                "evidence_ids": sorted(str(value) for value in finding.get("evidence_ids", []) if str(value)),
                "evidence_status": finding.get("evidence_status"),
                "evidence_verification": finding.get("evidence_verification"),
                "evidence": finding.get("evidence"),
                "evidence_refs": finding.get("evidence_refs"),
                "metrics": finding.get("metrics"),
                "human_decision": finding.get("human_decision"),
            })
        issue_rows.sort(key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True, default=str))
        meta = run.get("meta") if isinstance(run.get("meta"), dict) else {}
        receipts = meta.get("check_receipts")
        if not isinstance(receipts, list):
            receipts = []
        normalized_receipts = [
            {
                "rule_id": item.get("rule_id"),
                "sub_check_id": item.get("sub_check_id"),
                "status": item.get("status"),
                "incomplete_reason": item.get("incomplete_reason"),
                "formula": item.get("formula"),
                "required_fact_ids": sorted(str(value) for value in item.get("required_fact_ids", []) if str(value)),
                "operand_fact_ids": sorted(str(value) for value in item.get("operand_fact_ids", []) if str(value)),
                "issue_refs": sorted(str(value) for value in item.get("issue_refs", []) if str(value)),
                "tolerance": item.get("tolerance"),
                "evaluation_basis": item.get("evaluation_basis"),
                "fact_migration_status": item.get("fact_migration_status"),
            }
            for item in receipts if isinstance(item, dict)
        ]
        normalized_receipts.sort(key=lambda item: (str(item.get("rule_id")), str(item.get("sub_check_id"))))
        context = meta.get("fact_context")
        stable_context = {
            key: context.get(key)
            for key in ("context_version", "file_hash", "version_token", "fact_count", "recognition_statuses", "operand_count", "non_operand_count", "overlay_count")
        } if isinstance(context, dict) else None
        return json.dumps({
            "conclusion": str(run.get("analysis_conclusion") or run.get("status") or ""),
            "issue_rows": issue_rows,
            "check_receipts": normalized_receipts,
            "required_checks": run.get("required_checks"),
            # parse_run_uid may fall back to a fresh UUID; the value token is
            # derived from fact identity/value/status and excludes that run ID.
            "fact_context": stable_context,
            "fact_overlay_applied": meta.get("fact_overlay_applied"),
        }, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)

    for row in pairs:
        first_path = Path(row["replay_path"])
        repeat_path = Path(row["repeat_replay_path"])
        if not first_path.is_absolute():
            first_path = root / first_path
        if not repeat_path.is_absolute():
            repeat_path = root / repeat_path
        if not first_path.is_file() or not repeat_path.is_file():
            unavailable.append(f"{row.get('doc_id')}: 双轮回放文件缺失")
            continue
        if first_path.samefile(repeat_path):
            unavailable.append(f"{row.get('doc_id')}: 同一回放文件不能作为第二次独立运行")
            continue
        first = _read_json(first_path)
        repeat = _read_json(repeat_path)
        first_run_id = str(first.get("run_id") or "").strip()
        repeat_run_id = str(repeat.get("run_id") or "").strip()
        if not first_run_id or not repeat_run_id or first_run_id == repeat_run_id:
            unavailable.append(f"{row.get('doc_id')}: 必须提供两个不同的实际 run_id，复制同一次运行不能算重跑")
            continue
        try:
            first_time = datetime.fromisoformat(str(first.get("executed_at") or "").replace("Z", "+00:00"))
            repeat_time = datetime.fromisoformat(str(repeat.get("executed_at") or "").replace("Z", "+00:00"))
            if first_time.tzinfo is None or repeat_time.tzinfo is None or first_time == repeat_time:
                raise ValueError("distinct timezone-aware run timestamps required")
        except ValueError:
            unavailable.append(f"{row.get('doc_id')}: 缺少两次独立运行的带时区 executed_at")
            continue
        first_run = first.get(mode) if isinstance(first.get(mode), dict) else None
        repeat_run = repeat.get(mode) if isinstance(repeat.get(mode), dict) else None
        if first_run is None or repeat_run is None:
            unavailable.append(f"{row.get('doc_id')}: 缺少指定模式 {mode} 的双轮回放")
            continue
        first_basis = _basis(first, first_run)
        repeat_basis = _basis(repeat, repeat_run)
        required_basis = ("source_sha256", "configuration_fingerprint", "version_fingerprint")
        expected_source_sha = str(row.get("golden_sha256") or "").strip().lower()
        if not expected_source_sha:
            try:
                golden_path = root / "corpus" / str(row.get("doc_id") or "") / "golden.json"
                expected_source_sha = str(_read_json(golden_path).get("sha256") or "").strip().lower()
            except (OSError, ValueError, json.JSONDecodeError):
                expected_source_sha = ""
        if (
            any(not first_basis.get(key) or not repeat_basis.get(key) for key in required_basis)
            or first_basis != repeat_basis
            or (expected_source_sha and first_basis.get("source_sha256", "").lower() != expected_source_sha)
        ):
            unavailable.append(f"{row.get('doc_id')}: 缺少或不匹配原件/配置/版本基线")
            continue
        comparable += 1
        if _signature(first_run) == _signature(repeat_run):
            consistent += 1
    if comparable == 0:
        return {"status": "not_computable", "reason": "；".join(unavailable) or "repeat_replay_path 指向的重复回放文件缺失或模式不符"}
    if unavailable:
        return {
            "status": "not_computable",
            "reason": "部分重复回放缺少可信的同源同配置同版本基线：" + "；".join(unavailable[:8]),
            "comparable_documents": comparable,
            "consistent_documents": consistent,
        }
    return {
        "status": "computed",
        "comparable_documents": comparable,
        "consistent_documents": consistent,
        "repeatable": consistent == comparable,
    }


def _evaluate_export_agreement(manifest: dict[str, Any], root: Path) -> dict[str, Any]:
    """比较 canonical JSON/CSV 语义，并校验 PDF/DOCX 指向同一报告版本。"""
    rows_with_exports = [
        row
        for row in manifest.get("documents", [])
        if isinstance(row, dict)
    ]
    if not rows_with_exports:
        return {
            "status": "not_computable",
            "reason": "盲测清单没有文档，无法核对完整导出集合",
        }
    comparable = 0
    consistent = 0
    errors: list[str] = []

    def _list_field(value: Any, label: str) -> list[Any]:
        if not isinstance(value, list):
            raise ValueError(f"{label} 必须为数组")
        return value

    def _canonical_issue(item: dict[str, Any]) -> tuple[Any, ...]:
        location = item.get("location") if isinstance(item.get("location"), dict) else {}
        bbox = item.get("bbox") or location.get("bbox")
        if isinstance(bbox, str):
            bbox = json.loads(bbox) if bbox else None
        bbox_key = tuple(round(float(value), 2) for value in bbox) if isinstance(bbox, (list, tuple)) and len(bbox) == 4 else None
        facts = item.get("fact_ids", [])
        evidence = item.get("evidence_ids", [])
        if not isinstance(facts, list) or not isinstance(evidence, list):
            raise ValueError("fact_ids/evidence_ids 必须为数组")
        decision = item.get("human_decision") if isinstance(item.get("human_decision"), dict) else {}
        return (
            str(item.get("id") or item.get("issue_id") or ""),
            str(item.get("rule_id") or item.get("rule") or ""),
            str(item.get("title") or ""),
            str(item.get("severity") or ""),
            str(item.get("message") or ""),
            str(item.get("page_number") or item.get("page") or location.get("page") or ""),
            bbox_key,
            str(item.get("location_level") or location.get("location_level") or ""),
            str(item.get("verification_status") or location.get("verification_status") or ""),
            str(item.get("check_relation_id") or ""),
            tuple(sorted(str(value) for value in facts if str(value))),
            tuple(sorted(str(value) for value in evidence if str(value))),
            str(decision.get("status") or ""),
            str(decision.get("note") or ""),
            str(decision.get("decision_revision") or ""),
            str(decision.get("actor") or ""),
            str(decision.get("inherited", "")).lower(),
            str(decision.get("identity_fingerprint") or ""),
        )

    def _canonical_decision(item: dict[str, Any]) -> tuple[Any, ...]:
        facts, evidence = item.get("fact_ids"), item.get("evidence_ids")
        if not isinstance(facts, list) or not isinstance(evidence, list):
            raise ValueError("人工决定 fact_ids/evidence_ids 必须为数组")
        return (
            str(item.get("issue_id") or ""), str(item.get("status") or ""),
            str(item.get("note") or ""), str(item.get("analysis_revision") or ""),
            str(item.get("decision_revision") or ""), str(item.get("actor") or ""),
            str(item.get("inherited", "")).lower(), str(item.get("check_relation_id") or ""),
            tuple(sorted(str(value) for value in facts if str(value))),
            tuple(sorted(str(value) for value in evidence if str(value))),
            str(item.get("identity_fingerprint") or ""),
        )

    def _csv_json_list(value: Any, label: str) -> list[Any]:
        raw = str(value or "").strip()
        if not raw:
            return []
        parsed = json.loads(raw)
        if not isinstance(parsed, list):
            raise ValueError(f"CSV {label} 不是 JSON 数组")
        return parsed

    def _report_digest(report: dict[str, Any]) -> str:
        canonical = json.dumps(
            report,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _embedded_digest(path: Path) -> str:
        try:
            if path.suffix.lower() == ".docx":
                import zipfile

                with zipfile.ZipFile(path) as archive:
                    document_xml = archive.read("word/document.xml").decode("utf-8")
                match = re.search(r"REVIEW_REPORT_SHA256:\s*([a-fA-F0-9]{64})", document_xml)
                if not match:
                    raise ValueError("DOCX 缺少 canonical ReviewReport 指纹")
                return match.group(1).lower()
            if path.suffix.lower() == ".pdf":
                try:
                    import fitz
                except Exception as exc:
                    raise ValueError("当前环境没有 PDF 文本读取器，无法认证 PDF 导出指纹") from exc
                with fitz.open(str(path)) as document:
                    text = "\n".join(page.get_text() for page in document)
                normalized = re.sub(r"\s+", "", text)
                match = re.search(r"REVIEW_REPORT_SHA256:([a-fA-F0-9]{64})", normalized)
                if not match:
                    raise ValueError("PDF 缺少 canonical ReviewReport 指纹")
                return match.group(1).lower()
            raise ValueError(f"不支持认证的格式：{path.suffix}")
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError(f"{path.suffix.upper()} 文件损坏或不可读取：{type(exc).__name__}") from exc

    def _validate_visible_export(path: Path, report: dict[str, Any]) -> None:
        """Check that DOCX/PDF contains the canonical rows, not only its digest."""
        suffix = path.suffix.lower()
        try:
            if suffix == ".docx":
                import xml.etree.ElementTree as _ET
                with zipfile.ZipFile(path) as archive:
                    xml_bytes = archive.read("word/document.xml")
                root_element = _ET.fromstring(xml_bytes)
                visible_text = "".join(root_element.itertext())
            elif suffix == ".pdf":
                import fitz
                with fitz.open(str(path)) as document:
                    visible_text = "\n".join(page.get_text() for page in document)
            else:
                return
        except Exception as exc:
            raise ValueError(f"无法读取{suffix.upper()}正文进行语义核对：{type(exc).__name__}") from exc

        compact = re.sub(r"\s+", "", visible_text)
        if suffix == ".pdf":
            # The generated appendix repeats this exact header between wrapped
            # lines. Removing it restores long canonical reasons that cross pages.
            compact = compact.replace("系统生成附录·审校清单", "")
        collections = {
            "ISSUE_ID": [
                *(
                    report.get("issues", [])
                    if isinstance(report.get("issues"), list)
                    else []
                ),
                *(
                    report.get("tips", [])
                    if isinstance(report.get("tips"), list)
                    else []
                ),
            ],
            "DECISION_ID": report.get("human_decisions", []),
            "MANUAL_ID": report.get("manual_review", []),
            "CHECK_ID": report.get("check_receipts", []),
        }
        for marker, items in collections.items():
            if not isinstance(items, list):
                raise ValueError(f"ReviewReport.{marker} 对应字段必须为数组")
            values: list[str] = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                if marker == "ISSUE_ID":
                    value = str(item.get("id") or "")
                elif marker == "DECISION_ID":
                    value = str(item.get("issue_id") or "")
                elif marker == "MANUAL_ID":
                    value = str(item.get("id") or item.get("obligation_id") or item.get("code") or "")
                else:
                    value = f"{item.get('rule_id') or ''}:{item.get('sub_check_id') or ''}"
                if value:
                    values.append(value)
            actual_values = sorted(re.findall(rf"{marker}\|([^|]*)\|", compact))
            if actual_values != sorted(values):
                raise ValueError(f"{suffix.upper()} {marker} 数量或身份与 ReviewReport 不一致")

        def _token(value: Any) -> str:
            return re.sub(r"\s+", "", str(value or ""))

        expected_tokens: list[str] = []
        for marker in ("issues", "tips"):
            items = report.get(marker, [])
            if not isinstance(items, list):
                raise ValueError(f"ReviewReport.{marker} 必须为数组")
            for item in items:
                if not isinstance(item, dict):
                    continue
                location = item.get("location") if isinstance(item.get("location"), dict) else {}
                evidence = item.get("evidence") if isinstance(item.get("evidence"), list) else []
                snippet = next((ref.get("text_snippet") or ref.get("text") for ref in evidence if isinstance(ref, dict) and (ref.get("text_snippet") or ref.get("text"))), "")
                expected_tokens.extend((
                    item.get("title") or item.get("rule_id") or item.get("message"),
                    item.get("rule_id"),
                    item.get("message"),
                    item.get("suggestion"),
                    item.get("page_number") or item.get("page") or location.get("page"),
                    item.get("bbox") or location.get("bbox"),
                    item.get("severity"),
                    item.get("location_level") or location.get("location_level"),
                    item.get("verification_status") or location.get("verification_status"),
                    item.get("check_relation_id"),
                    snippet,
                ))
                for key in ("fact_ids", "evidence_ids"):
                    if isinstance(item.get(key), list):
                        expected_tokens.extend(item[key])
        for item in report.get("human_decisions", []):
            if isinstance(item, dict):
                expected_tokens.extend(item.get(key) for key in (
                    "status", "note", "analysis_revision", "decision_revision", "actor", "identity_fingerprint"
                ))
                expected_tokens.extend(item.get("fact_ids", []) if isinstance(item.get("fact_ids"), list) else [])
                expected_tokens.extend(item.get("evidence_ids", []) if isinstance(item.get("evidence_ids"), list) else [])
        for item in report.get("manual_review", []):
            if isinstance(item, dict):
                title = normalize_manual_review(item)["display_title"]
                if suffix == ".docx":
                    detail = item.get("message") or item.get("detail") or item.get("reason")
                else:
                    detail = item.get("detail") or item.get("reason") or item.get("message")
                expected_tokens.extend((item.get("record_type"), title, item.get("status"), detail))
        for item in report.get("check_receipts", []):
            if isinstance(item, dict):
                expected_tokens.extend((
                    item.get("rule_id"), item.get("sub_check_id"), item.get("status"),
                    item.get("formula") if suffix == ".pdf" else item.get("formula") or item.get("evaluation_basis"),
                    item.get("incomplete_reason"),
                    item.get("tolerance"),
                ))
                for key in ("operand_fact_ids", "required_fact_ids", "issue_refs"):
                    if isinstance(item.get(key), list):
                        expected_tokens.extend(item[key])
        missing_tokens = [
            token for value in expected_tokens
            if (token := _token(value)) and token not in compact
        ]
        if missing_tokens:
            raise ValueError(f"{suffix.upper()} 缺少 {len(missing_tokens)} 项 canonical 可见语义字段")

        # Global token presence is not enough: it lets two issue rows exchange
        # their messages/amounts while the document still contains every token.
        markers = list(re.finditer(r"(?:ISSUE_ID|DECISION_ID|MANUAL_ID|CHECK_ID)\|[^|]*\|", compact))
        issues = [
            item
            for key in ("issues", "tips")
            for item in (report.get(key) if isinstance(report.get(key), list) else [])
            if isinstance(item, dict)
        ]
        for item in issues:
            issue_id = str(item.get("id") or "")
            marker_text = f"ISSUE_ID|{issue_id}|"
            starts = [match.start() for match in markers if match.group(0) == marker_text]
            if len(starts) != 1:
                raise ValueError(f"{suffix.upper()} 问题 {issue_id} 的 ID 标记不唯一")
            start = starts[0]
            following = [match.start() for match in markers if match.start() > start]
            segment = compact[start:min(following) if following else len(compact)]
            location = item.get("location") if isinstance(item.get("location"), dict) else {}
            evidence = item.get("evidence") if isinstance(item.get("evidence"), list) else []
            snippet = next(
                (
                    ref.get("text_snippet") or ref.get("text")
                    for ref in evidence
                    if isinstance(ref, dict) and (ref.get("text_snippet") or ref.get("text"))
                ),
                "",
            )
            row_values = [
                item.get("rule_id") or item.get("rule"),
                item.get("title") or item.get("message"),
                item.get("message"),
                item.get("suggestion"),
                item.get("severity"),
                item.get("check_relation_id"),
                item.get("page_number") or item.get("page") or location.get("page"),
                snippet,
                *(_list_field(item.get("fact_ids", []), "issue.fact_ids")),
                *(_list_field(item.get("evidence_ids", []), "issue.evidence_ids")),
            ]
            decision = item.get("human_decision") if isinstance(item.get("human_decision"), dict) else {}
            row_values.extend((decision.get("status"), decision.get("note")))
            missing_row_tokens = [
                token for value in row_values
                if (token := _token(value)) and token not in segment
            ]
            if missing_row_tokens:
                raise ValueError(f"{suffix.upper()} 问题 {issue_id} 的行内容与 canonical 问题不一致")

        for decision in report.get("human_decisions", []):
            if not isinstance(decision, dict):
                continue
            issue_id = str(decision.get("issue_id") or "")
            marker_text = f"DECISION_ID|{issue_id}|"
            starts = [match.start() for match in markers if match.group(0) == marker_text]
            if len(starts) != 1:
                raise ValueError(f"{suffix.upper()} 人工决定 {issue_id} 的 ID 标记不唯一")
            start = starts[0]
            following = [match.start() for match in markers if match.start() > start]
            segment = compact[start:min(following) if following else len(compact)]
            values = [decision.get(key) for key in (
                "status", "note", "analysis_revision", "decision_revision", "actor", "identity_fingerprint",
            )]
            for key in ("fact_ids", "evidence_ids"):
                values.extend(_list_field(decision.get(key, []), f"decision.{key}"))
            if any((token := _token(value)) and token not in segment for value in values):
                raise ValueError(f"{suffix.upper()} 人工决定 {issue_id} 的行内容与 canonical 决定不一致")

    for row in rows_with_exports:
        document_error_start = len(errors)
        export_paths = row.get("export_paths")
        if not isinstance(export_paths, list):
            errors.append(f"{row.get('doc_id')}: 缺少 export_paths；每份盲测文档都必须提供 JSON/CSV/DOCX/PDF")
            continue
        found: dict[str, list[dict[str, Any]]] = {".json": [], ".csv": []}
        format_digests: dict[str, list[str]] = {".json": [], ".csv": [], ".docx": [], ".pdf": []}
        canonical_reports: list[dict[str, Any]] = []
        semantic_paths: list[Path] = []
        for raw_path in export_paths:
            if isinstance(raw_path, dict):
                raw_path = raw_path.get("path") or ""
            path = Path(str(raw_path))
            if not path.is_absolute():
                path = root / path
            if not path.is_file():
                errors.append(f"{row.get('doc_id')}: 导出文件缺失 {raw_path}")
                continue
            try:
                suffix = path.suffix.lower()
                if suffix == ".json":
                    payload = _read_json(path)
                    report = payload.get("review_report")
                    if not isinstance(report, dict) or report.get("schema_version") != 2:
                        raise ValueError("canonical review_report/schema_version=2 缺失")
                    revision = str(report.get("analysis_revision") or "")
                    if not revision or str(payload.get("analysis_revision") or "") != revision:
                        raise ValueError("analysis_revision 缺失或与 canonical report 不一致")
                    issues = _list_field(report.get("issues"), "review_report.issues")
                    tips = _list_field(report.get("tips"), "review_report.tips")
                    decisions = _list_field(report.get("human_decisions"), "review_report.human_decisions")
                    manual_review = _list_field(report.get("manual_review"), "review_report.manual_review")
                    receipts = _list_field(report.get("check_receipts"), "review_report.check_receipts")
                    if not isinstance(report.get("conclusion"), dict):
                        raise ValueError("review_report.conclusion 缺失")
                    report_digest = _report_digest(report)
                    if str(payload.get("review_report_sha256") or "").lower() != report_digest:
                        raise ValueError("JSON review_report_sha256 与 canonical ReviewReport 不符")
                    canonical_reports.append(report)
                    format_digests[".json"].append(report_digest)
                    found[".json"].append({
                        "revision": revision,
                        "issues": sorted(
                            (_canonical_issue(item) for item in issues + tips if isinstance(item, dict)),
                            key=lambda value: json.dumps(value, ensure_ascii=False, sort_keys=True, default=str),
                        ),
                        "decisions": sorted(_canonical_decision(item) for item in decisions if isinstance(item, dict)),
                        "manual": sorted((
                            str(item.get("record_type") or "manual_review"),
                            str(item.get("id") or ""), str(item.get("obligation_id") or ""),
                            str(item.get("code") or ""), normalize_manual_review(item)["title"],
                            str(item.get("status") or ""),
                            str(item.get("message") or item.get("detail") or item.get("reason") or ""),
                        ) for item in manual_review if isinstance(item, dict)),
                        "receipts": sorted((
                            str(item.get("rule_id") or ""), str(item.get("sub_check_id") or ""),
                            str(item.get("status") or ""), str(item.get("incomplete_reason") or ""),
                            str(item.get("formula") or ""),
                            tuple(sorted(str(value) for value in item.get("operand_fact_ids", []) if str(value))),
                            tuple(sorted(str(value) for value in item.get("issue_refs", []) if str(value))),
                        ) for item in receipts if isinstance(item, dict)),
                        "conclusion": json.dumps(report["conclusion"], ensure_ascii=False, sort_keys=True, default=str),
                    })
                elif suffix == ".csv":
                    import csv as _csv

                    with path.open("r", encoding="utf-8-sig", newline="") as handle:
                        reader = _csv.DictReader(handle)
                        required_columns = {
                            "record_type", "issue_id", "rule_id", "title", "severity", "page", "bbox",
                            "location_level", "verification_status", "check_relation_id", "fact_ids",
                            "evidence_ids", "human_decision", "human_decision_note", "decision_revision",
                            "decision_actor", "decision_inherited", "analysis_revision", "analysis_conclusion",
                            "decision_identity_fingerprint",
                            "review_report_sha256",
                            "quality_status", "conclusion_scope", "manual_review_type", "check_id",
                            "check_status", "incomplete_reason", "formula", "operand_fact_ids", "issue_refs",
                            "conclusion_json",
                        }
                        if not reader.fieldnames or not required_columns.issubset(set(reader.fieldnames)):
                            raise ValueError("CSV schema 缺少完整 canonical 字段")
                        csv_rows = list(reader)
                    summaries = [item for item in csv_rows if item.get("record_type") == "summary"]
                    if len(summaries) != 1:
                        raise ValueError("CSV 必须且只能包含一条 summary 行")
                    revision = str(summaries[0].get("analysis_revision") or "")
                    if not revision:
                        raise ValueError("CSV summary 缺少 analysis_revision")
                    report_digest = str(summaries[0].get("review_report_sha256") or "").lower()
                    if not re.fullmatch(r"[a-f0-9]{64}", report_digest):
                        raise ValueError("CSV summary 缺少有效 ReviewReport 指纹")
                    format_digests[".csv"].append(report_digest)
                    issue_rows = [item for item in csv_rows if item.get("record_type") in {"issue", "tip"}]
                    csv_issues = []
                    for item in issue_rows:
                        csv_issues.append((
                            str(item.get("issue_id") or ""), str(item.get("rule_id") or ""),
                            str(item.get("title") or ""), str(item.get("severity") or ""),
                            str(item.get("message") or ""), str(item.get("page") or ""),
                            tuple(round(float(value), 2) for value in _csv_json_list(item.get("bbox"), "bbox")) if item.get("bbox") else None,
                            str(item.get("location_level") or ""), str(item.get("verification_status") or ""),
                            str(item.get("check_relation_id") or ""),
                            tuple(sorted(str(value) for value in _csv_json_list(item.get("fact_ids"), "fact_ids"))),
                            tuple(sorted(str(value) for value in _csv_json_list(item.get("evidence_ids"), "evidence_ids"))),
                            str(item.get("human_decision") or ""), str(item.get("human_decision_note") or ""),
                            str(item.get("decision_revision") or ""), str(item.get("decision_actor") or ""),
                            str(item.get("decision_inherited") or "").lower(),
                            str(item.get("decision_identity_fingerprint") or ""),
                        ))
                    csv_decisions = []
                    for item in csv_rows:
                        if item.get("record_type") != "human_decision":
                            continue
                        csv_decisions.append((
                            str(item.get("issue_refs") or ""), str(item.get("human_decision") or ""),
                            str(item.get("human_decision_note") or ""), str(item.get("analysis_revision") or ""),
                            str(item.get("decision_revision") or ""), str(item.get("decision_actor") or ""),
                            str(item.get("decision_inherited") or "").lower(), str(item.get("check_relation_id") or ""),
                            tuple(sorted(str(value) for value in _csv_json_list(item.get("fact_ids"), "fact_ids"))),
                            tuple(sorted(str(value) for value in _csv_json_list(item.get("evidence_ids"), "evidence_ids"))),
                            str(item.get("decision_identity_fingerprint") or ""),
                        ))
                    csv_manual = [item for item in csv_rows if item.get("record_type") == "manual_review"]
                    csv_receipts = [item for item in csv_rows if item.get("record_type") == "check"]
                    found[".csv"].append({
                        "revision": revision,
                        "issues": sorted(
                            csv_issues,
                            key=lambda value: json.dumps(value, ensure_ascii=False, sort_keys=True, default=str),
                        ),
                        "decisions": sorted(csv_decisions),
                        "manual": sorted((
                            str(item.get("manual_review_type") or "manual_review"),
                            str(item.get("issue_id") or ""),
                            str(item.get("obligation_ids") or ""), str(item.get("规则编号") or ""),
                            str(item.get("title") or ""), str(item.get("check_status") or ""),
                            str(item.get("incomplete_reason") or item.get("摘要") or ""),
                        ) for item in csv_manual),
                        "receipts": sorted((
                            str(item.get("rule_id") or ""), str(item.get("check_id") or ""),
                            str(item.get("check_status") or ""), str(item.get("incomplete_reason") or ""),
                            str(item.get("formula") or ""),
                            tuple(sorted(str(value) for value in _csv_json_list(item.get("operand_fact_ids"), "operand_fact_ids"))),
                            tuple(sorted(str(value) for value in _csv_json_list(item.get("issue_refs"), "issue_refs"))),
                        ) for item in csv_receipts),
                        "conclusion": json.dumps(
                            json.loads(summaries[0].get("conclusion_json") or "{}"),
                            ensure_ascii=False, sort_keys=True, default=str,
                        ),
                    })
                elif suffix in {".docx", ".pdf"}:
                    format_digests[suffix].append(_embedded_digest(path))
                    semantic_paths.append(path)
                else:
                    raise ValueError(f"不支持的交付格式：{suffix or '(无扩展名)'}")
            except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
                errors.append(f"{row.get('doc_id')}: {path.name}: {exc}")
        if len(canonical_reports) == 1:
            for semantic_path in semantic_paths:
                try:
                    _validate_visible_export(semantic_path, canonical_reports[0])
                except (OSError, ValueError, TypeError) as exc:
                    errors.append(f"{row.get('doc_id')}: {semantic_path.name}: {exc}")
        elif canonical_reports:
            errors.append(f"{row.get('doc_id')}: 必须且只能提供一个 canonical JSON 报告用于 DOCX/PDF 语义核对")
        missing_formats = [suffix for suffix, values in format_digests.items() if len(values) != 1]
        if missing_formats:
            errors.append(
                f"{row.get('doc_id')}: export_paths 必须各提供一个 JSON/CSV/DOCX/PDF；缺少或重复：{','.join(missing_formats)}"
            )
        if not found[".json"] or not found[".csv"]:
            errors.append(f"{row.get('doc_id')}: 必须同时提供 canonical JSON 和 CSV")
            continue
        json_revisions = {str(item.get("revision") or "") for item in found[".json"]}
        csv_revisions = {str(item.get("revision") or "") for item in found[".csv"]}
        if len(json_revisions) != 1 or len(csv_revisions) != 1 or json_revisions != csv_revisions:
            errors.append(f"{row.get('doc_id')}: JSON/CSV 的 analysis_revision 不一致或重复导出版本不唯一")
        signatures = found[".json"] + found[".csv"]
        comparable += 1
        all_digests = [values[0] for values in format_digests.values() if len(values) == 1]
        if (
            len(errors) == document_error_start
            and all(signature == signatures[0] for signature in signatures)
            and len(all_digests) == 4
            and all(value == all_digests[0] for value in all_digests)
        ):
            consistent += 1
    if comparable == 0:
        return {"status": "not_computable", "reason": "；".join(errors[:8]) or "export_paths 指向的导出文件缺失或格式不可比"}
    if errors:
        return {
            "status": "not_computable",
            "reason": "导出结构不完整或不一致：" + "；".join(errors[:8]),
            "comparable_documents": comparable,
            "consistent_documents": consistent,
        }
    return {
        "status": "computed",
        "comparable_documents": comparable,
        "consistent_documents": consistent,
        "export_agreement_rate": round(consistent / comparable, 4),
    }


def _evaluate_replays(dataset: dict[str, Any], manifest_path: Path, root: Path, mode: str) -> dict[str, Any]:
    if not dataset.get("metrics_ready"):
        return {"status": "not_computable", "reason": "数据集、冻结真值或逐文档回放不完整"}
    from scripts.evaluate_golden_corpus import _truth_group, evaluate

    manifest = _read_json(manifest_path)
    evaluated: list[dict[str, Any]] = []
    for row in manifest["documents"]:
        replay_path = Path(row["replay_path"])
        if not replay_path.is_absolute():
            replay_path = root / replay_path
        report = evaluate(str(row["doc_id"]), replay_path, mode=mode)
        golden = _read_json(root / "corpus" / str(row["doc_id"]) / "golden.json")
        replay_payload = _read_json(replay_path)
        run = (
            replay_payload.get(mode)
            if isinstance(replay_payload.get(mode), dict)
            else replay_payload.get("legacy") or {}
        )
        high_groups = {
            _truth_group(label)
            for label in golden.get("labels", [])
            if isinstance(label, dict)
            and label.get("label") == "defect"
            and str(label.get("expected_severity") or "").lower() in {"high", "critical", "error"}
        }
        missed_groups = {str(item.get("truth_group") or "") for item in report.get("missed", [])}
        evaluated.append(
            {
                "split": row["split"],
                "document_type": row["document_type"],
                "organization_type": row["organization_type"],
                "source_type": row["source_type"],
                "mixed": bool(row.get("mixed", False)),
                "high_or_critical_missed_groups": len(high_groups & missed_groups),
                "report": report,
                "run": run if isinstance(run, dict) else {},
                "golden": golden if isinstance(golden, dict) else {},
            }
        )

    def subgroup_metrics(split_rows: list[dict[str, Any]], field: str) -> dict[str, Any]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in split_rows:
            key = str(row[field]).lower() if field == "mixed" else str(row[field])
            grouped.setdefault(key, []).append(row)
        return {key: _aggregate_evaluations(group) for key, group in sorted(grouped.items())}

    split_metrics = {
        split: _aggregate_evaluations([row for row in evaluated if row["split"] == split])
        for split in sorted(SPLITS)
    }
    blind_rows = [row for row in evaluated if row["split"] == "blind"]
    blind_metrics = _aggregate_evaluations(blind_rows)
    blind_subgroups = {
        field: subgroup_metrics(blind_rows, field)
        for field in ("document_type", "organization_type", "source_type", "mixed")
    }
    # AC-14：补齐此前固定 not_computable 的评测——输入齐备即计算，
    # 输入缺失如实保留 not_computable 并说明缺什么，不允许假装通过。
    blind_manifest = {
        **manifest,
        "documents": [item for item in manifest.get("documents", []) if isinstance(item, dict) and item.get("split") == "blind"],
    }
    blind_evaluated = [row for row in evaluated if row["split"] == "blind"]
    development_evaluated = [row for row in evaluated if row["split"] == "development"]
    evidence_authenticity = _evaluate_evidence_authenticity(blind_evaluated, root)
    receipts = _evaluate_receipts(blind_evaluated)
    exact_location = _evaluate_exact_location(blind_evaluated, root)
    repeatability = _evaluate_repeatability(blind_manifest, root, mode)
    export_agreement = _evaluate_export_agreement(blind_manifest, root)
    development_diagnostics = {
        "evidence_authenticity": _evaluate_evidence_authenticity(development_evaluated, root),
        "receipts": _evaluate_receipts(development_evaluated),
        "exact_location": _evaluate_exact_location(development_evaluated, root),
        "split": "development",
        "does_not_contribute_to_release_gates": True,
    }

    return {
        "status": "computed",
        "acceptance_split": "blind",
        **blind_metrics,
        "split_metrics": split_metrics,
        "blind_subgroup_metrics": blind_subgroups,
        "evidence_authenticity": evidence_authenticity,
        "receipts": receipts,
        "exact_location": exact_location,
        "repeatability": repeatability,
        "export_agreement": export_agreement,
        "development_diagnostics": development_diagnostics,
        "review_candidate_rate": None,
        "duplicate_alert_rate": None,
        "noncomputable_metrics": [
            "人工复核候选率：replay 产物未含经审核的全部候选项真值",
            "重复报警率：评测器没有跨 finding 的重复关系标准答案",
        ],
        "evaluator_note": "page_accuracy 仅是页码代理；bbox/原件区域精确定位率另见 exact_location 字段。",
        "per_document": [
            {
                "doc_id": row["report"]["doc_id"],
                "split": row["split"],
                "document_type": row["document_type"],
                "organization_type": row["organization_type"],
                "source_type": row["source_type"],
                "mixed": row["mixed"],
                "tp": row["report"]["tp"],
                "fp": row["report"]["fp"],
                "fn": row["report"]["fn"],
                "precision": row["report"].get("precision"),
                "recall": row["report"].get("recall"),
                "page_accuracy": row["report"].get("page_accuracy"),
                "locatable_evidence_rate": row["report"].get("locatable_evidence_rate"),
            }
            for row in evaluated
        ],
    }


def _gate(status: str, detail: str) -> dict[str, str]:
    return {"status": status, "detail": detail}


def build_report(
    root: Path,
    manifest_path: Path | None,
    e2e_path: Path | None,
    mode: str,
) -> dict[str, Any]:
    inventory = _inventory(root)
    environment = _environment(root)
    if manifest_path is None:
        dataset = {
            "status": "missing",
            "path": None,
            "sha256": None,
            "errors": ["未提供冻结的开发集/盲测集清单"],
            "documents": [],
            "sample_counts": {},
            "strata_counts": {},
            "defect_groups_by_split": {},
            "input_hashes": [],
            "metrics_ready": False,
        }
    else:
        dataset = validate_dataset_manifest(manifest_path, root, mode=mode)
    e2e = validate_e2e_evidence(e2e_path, root)
    metrics = (
        _evaluate_replays(dataset, manifest_path, root, mode)
        if manifest_path is not None
        else {"status": "not_computable", "reason": "没有冻结真值清单与回放材料"}
    )

    gates: dict[str, dict[str, str]] = {}
    if metrics.get("status") == "computed":
        precision = metrics.get("precision")
        recall = metrics.get("recall")
        gates["formal_issue_precision"] = _gate(
            "pass" if precision is not None and precision >= 0.98 else "fail",
            f"盲测 {precision}，门槛 0.98" if precision is not None else "盲测无正式命中/问题，指标不可计算",
        )
        gates["real_issue_recall"] = _gate(
            "pass" if recall is not None and recall >= 0.95 else "fail",
            f"盲测 {recall}，门槛 0.95" if recall is not None else "盲测无真实正例命中分母，指标不可计算",
        )
        major = int(metrics.get("high_or_critical_missed_groups") or 0)
        gates["no_major_misses"] = _gate(
            "pass" if major == 0 else "fail", f"高/重大缺陷漏报组数 {major}，门槛 0"
        )
    else:
        for name in ("formal_issue_precision", "real_issue_recall", "no_major_misses"):
            gates[name] = _gate("not_computable", str(metrics.get("reason")))
    if metrics.get("status") == "computed":
        exact_location = metrics.get("exact_location") or {}
        if exact_location.get("status") == "computed":
            rate = float(exact_location.get("exact_location_rate") or 0.0)
            gates["exact_location"] = _gate(
                "pass" if rate >= 0.98 else "fail",
                f"区域真值命中 {rate}，门槛 0.98",
            )
        else:
            gates["exact_location"] = _gate("not_computable", str(exact_location.get("reason")))

        authenticity = metrics.get("evidence_authenticity") or {}
        if authenticity.get("status") == "computed":
            authentic_rate = float(authenticity.get("authenticity_rate") or 0.0)
            violations = int(
                authenticity.get("failed", 0)
                + authenticity.get("unverified", 0)
                + authenticity.get("missing_record", 0)
            )
            gates["evidence_authenticity"] = _gate(
                "pass" if violations == 0 and authentic_rate >= 1.0 else "fail",
                f"证据真实率 {authentic_rate}，未认证/伪造/缺记录 {violations} 条（门槛：全部正式引用均可认证）",
            )
        else:
            gates["evidence_authenticity"] = _gate("not_computable", str(authenticity.get("reason")))

        receipts = metrics.get("receipts") or {}
        if receipts.get("status") == "computed":
            completion = float(receipts.get("completion_rate") or 0.0)
            coverage = float(receipts.get("receipt_coverage_rate") or 0.0)
            gates["required_check_completion"] = _gate(
                "pass" if completion >= 0.95 else "fail",
                f"关系级完成率 {completion}，门槛 0.95",
            )
            gates["receipt_coverage"] = _gate(
                "pass" if coverage >= 1.0 else "fail",
                f"冻结适用关系的状态/未完成原因覆盖率 {coverage}，门槛 1.0；操作数+公式诊断值 {receipts.get('operand_formula_coverage')}",
            )
        else:
            gates["required_check_completion"] = _gate("not_computable", str(receipts.get("reason")))
            gates["receipt_coverage"] = _gate("not_computable", str(receipts.get("reason")))
    else:
        gates["exact_location"] = _gate(
            "not_computable", "未提供冻结真值清单与回放，无法计算。"
        )
        gates["evidence_authenticity"] = _gate(
            "not_computable", "未提供冻结真值清单与回放，无法计算。"
        )
        gates["required_check_completion"] = _gate(
            "not_computable", "未提供冻结真值清单与回放，无法计算。"
        )
        gates["receipt_coverage"] = _gate(
            "not_computable", "未提供冻结真值清单与回放，无法计算。"
        )
    runtime_ready = (
        e2e.get("status") == "ready"
        and environment["docker"]["daemon_reachable"]
        and environment["test_database"]["status"] == "configured"
        and environment["ocr"]["model_manifest_exists"]
    )
    runtime_blockers = list(e2e.get("errors", []))
    if not environment["docker"]["daemon_reachable"]:
        runtime_blockers.append("Docker daemon 不可用")
    if environment["test_database"]["status"] != "configured":
        runtime_blockers.append("缺少 GOVBUDGET_TEST_DATABASE_URL")
    if not environment["ocr"]["model_manifest_exists"]:
        runtime_blockers.append("缺少 OCR 模型清单")
    gates["real_api_worker_postgres_ocr_e2e"] = _gate(
        "pass" if runtime_ready else "fail",
        "四条真实服务场景证据齐备" if runtime_ready else "; ".join(runtime_blockers),
    )
    if metrics.get("status") == "computed":
        export_agreement = metrics.get("export_agreement") or {}
        if export_agreement.get("status") == "computed":
            rate = float(export_agreement.get("export_agreement_rate") or 0.0)
            gates["export_agreement"] = _gate(
                "pass" if rate >= 1.0 else "fail",
                f"导出一致率 {rate}，门槛 1.0",
            )
        else:
            gates["export_agreement"] = _gate("not_computable", str(export_agreement.get("reason")))
        repeatability = metrics.get("repeatability") or {}
        if repeatability.get("status") == "computed":
            gates["repeatability"] = _gate(
                "pass" if repeatability.get("repeatable") else "fail",
                f"重复运行一致 {repeatability.get('consistent_documents')}/{repeatability.get('comparable_documents')} 份",
            )
        else:
            gates["repeatability"] = _gate("not_computable", str(repeatability.get("reason")))
    else:
        gates["export_agreement"] = _gate("not_computable", "未提供冻结真值清单与回放，无法计算。")
        gates["repeatability"] = _gate("not_computable", "未提供冻结真值清单与回放，无法计算。")
    #: RC-13：诊断项与正式门项分离——缺真值的统计（候选报警复核率、
    # 重复报警率）是诊断指标，记录现状但**不参与** all_pass：既不永久
    # 阻断验收，也不许把缺指标直接当成 pass。
    diagnostic_metrics = {
        "review_candidate_rate": "评测回放没有经人工复核的完整候选项基线。",
        "duplicate_alert_rate": "没有重复报警真值关系及独立复核统计。",
    }
    for name, reason in diagnostic_metrics.items():
        gates[name] = _gate("diagnostic", reason)

    formal_gates = {
        name: gate
        for name, gate in gates.items()
        if name not in diagnostic_metrics
    }
    all_pass = all(gate["status"] == "pass" for gate in formal_gates.values())
    try:
        git_result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        revision = git_result.stdout.strip() if git_result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        revision = None
    config = {"parse_mode": mode, "manifest_sha256": dataset.get("sha256"), "e2e_sha256": e2e.get("sha256")}
    runner_path = root / "scripts" / "run_zc07_acceptance.py"
    config["runner_sha256"] = sha256_file(runner_path) if runner_path.is_file() else None
    try:
        dirty_result = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        source_tree_dirty = dirty_result.returncode != 0 or bool(dirty_result.stdout.strip())
    except (OSError, subprocess.TimeoutExpired):
        source_tree_dirty = None
    config_sha = hashlib.sha256(
        json.dumps(config, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    return {
        "schema_version": "govbudgetchecker.zc07-acceptance-report.v1",
        "run_id": f"zc07-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "revision": revision,
        "source_tree_dirty": source_tree_dirty,
        "execution_chain": "scripts/run_zc07_acceptance.py; replay files evaluated with scripts/evaluate_golden_corpus.py",
        "config": {**config, "sha256": config_sha},
        "inventory": inventory,
        "environment": environment,
        "dataset": dataset,
        "e2e": e2e,
        "metrics": metrics,
        "gates": gates,
        "business_acceptance": "passed" if all_pass else "not_passed",
        "exit_code": 0 if all_pass else 2,
    }


def _write_report(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    target = path
    if target.exists():
        target = target.with_name(f"{target.stem}-{uuid.uuid4().hex[:8]}{target.suffix}")
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, target)
        return target
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, help="冻结的开发集/盲测集清单 JSON")
    parser.add_argument("--e2e-evidence", type=Path, help="四条真实 API/Worker/DB/OCR E2E 证据 JSON")
    parser.add_argument("--mode", choices=["legacy", "structured"], default="legacy")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("outputs/zcode_remediation/ZC-07/acceptance.json"),
    )
    args = parser.parse_args(argv)
    manifest = args.manifest.resolve() if args.manifest else None
    e2e = args.e2e_evidence.resolve() if args.e2e_evidence else None
    output = args.output if args.output.is_absolute() else ROOT / args.output
    report = build_report(ROOT, manifest, e2e, args.mode)
    actual_output = _write_report(output, report)
    print(f"ZC-07 business_acceptance={report['business_acceptance']} exit_code={report['exit_code']}")
    print(f"frozen_dataset={report['dataset']['status']} real_e2e={report['e2e']['status']}")
    print(f"report -> {_repo_relative(actual_output, ROOT)}")
    for name, gate in report["gates"].items():
        if gate["status"] != "pass":
            print(f"GATE-{gate['status'].upper()}: {name}: {gate['detail']}")
    return int(report["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
