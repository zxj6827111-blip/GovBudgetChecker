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
import shutil
import subprocess
import sys
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

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
                    str(label.get("truth_id") or label.get("annotation_id") or "")
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
            "status": "ready" if ocr_service_configured else "missing",
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
                str(label.get("truth_id") or label.get("annotation_id") or "")
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
    return {
        "status": "computed",
        "acceptance_split": "blind",
        **blind_metrics,
        "split_metrics": split_metrics,
        "blind_subgroup_metrics": blind_subgroups,
        "review_candidate_rate": None,
        "duplicate_alert_rate": None,
        "noncomputable_metrics": [
            "人工复核候选率：replay 产物未含经审核的全部候选项真值",
            "重复报警率：评测器没有跨 finding 的重复关系标准答案",
        ],
        "evaluator_note": "page_accuracy 仅是页码代理；不等同于 bbox/原件区域精确定位率。",
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
    gates["exact_location"] = _gate(
        "not_computable", "现有评测仅含页码代理，不具备基于原件 bbox/区域的精确定位真值。"
    )
    gates["evidence_authenticity"] = _gate(
        "not_computable", "没有冻结的原件证据区域审核结果与页码边界复核记录。"
    )
    gates["required_check_completion"] = _gate(
        "not_computable", "未提供冻结的适用检查范围及逐子项完成回执。"
    )
    gates["receipt_coverage"] = _gate(
        "not_computable", "未提供逐适用子项回执及原因覆盖证据。"
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
    gates["export_agreement"] = _gate("not_computable", "未提供页面/导出逐项一致性核验记录。")
    gates["repeatability"] = _gate("not_computable", "未提供相同原件、配置、版本的独立重复运行记录。")
    gates["review_candidate_rate"] = _gate(
        "not_computable", "评测回放没有经人工审核的完整候选项基线。"
    )
    gates["duplicate_alert_rate"] = _gate(
        "not_computable", "没有重复报警真值关系及独立复核统计。"
    )

    all_pass = all(gate["status"] == "pass" for gate in gates.values())
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
