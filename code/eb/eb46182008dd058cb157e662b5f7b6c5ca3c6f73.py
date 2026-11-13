#!/usr/bin/env python3
"""阶段A：建立统一接续身份清单。

只读取既有证据，不修改任何历史文件。输出 takeover_manifest.json：
代码提交、工具、契约、输入、标注与运行画像的哈希，全部用绝对路径记录，
便于独立复核方逐条重算。

用法：
    python3 tools/build_takeover_manifest.py [--root <run_dir>]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

MAIN = Path("/Volumes/workSSD/software development/GovBudgetChecker")
WORKTREES = Path("/Volumes/workSSD/software development/gbc-worktrees")
EVIDENCE_ROOT = WORKTREES / "joint-execution-20261103-evidence"
B1_ROOT = MAIN / "outputs/gold_b1_20261103/run_20261103T150928"
REVIEW_ROOT = MAIN / "outputs/joint_execution_review_codex_20261103"
TAKEOVER_ROOT = MAIN / "outputs/unified_takeover_20261103"
CONTRACT_ROOT = MAIN / "docs/joint_execution_20261103"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_entry(path: Path, role: str) -> dict:
    if not path.exists():
        return {"role": role, "path": str(path), "exists": False}
    return {
        "role": role,
        "path": str(path),
        "exists": True,
        "bytes": path.stat().st_size,
        "sha256": sha256(path),
    }


def dir_entries(root: Path, role: str, pattern: str = "*") -> list:
    if not root.exists():
        return []
    return [
        file_entry(path, role)
        for path in sorted(root.glob(pattern))
        if path.is_file()
    ]


def repo_identity(path: Path) -> dict:
    def git(*args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(path), *args], capture_output=True, text=True, check=False
        ).stdout.strip()

    status = git("status", "--porcelain")
    return {
        "path": str(path),
        "head": git("rev-parse", "HEAD"),
        "branch": git("rev-parse", "--abbrev-ref", "HEAD"),
        "clean": not status,
        "dirty_entries": len(status.splitlines()) if status else 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=str(MAIN / "outputs/unified_closeout_20261103"))
    args = parser.parse_args()
    root = Path(args.root)
    root.mkdir(parents=True, exist_ok=True)

    repos = {
        "main_workspace": MAIN,
        "joint_exp_integration": WORKTREES / "joint-exp-integration-20261103",
        "joint_exp_rework": WORKTREES / "joint-exp-rework-20261103",
        "joint_exp_batch2_tools": WORKTREES / "joint-exp-batch2-tools-20261103",
        "gold_b1": WORKTREES / "gold-b1-20261103",
        "unified_closeout": WORKTREES / "unified-closeout-20261103",
    }

    manifest = {
        "manifest_kind": "unified_closeout_takeover_manifest",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "timezone_local": "America/Los_Angeles",
        "supersedes": {
            "description": "旧 FINAL_HANDOFF.json / STATUS_20261103.md / S6_SUMMARY.md 基于 f76f741b 与 109 fixed 口径，只读保留，不再作为当前事实。",
            "old_handoff_dir": str(EVIDENCE_ROOT),
            "old_business_head": "f76f741bfa1f9121e5d3f1bb7044b5b7c0ede4c2",
        },
        "code_identity": {name: repo_identity(path) for name, path in repos.items()},
        "contracts": [
            file_entry(CONTRACT_ROOT / "master_plan.json", "joint_master_plan"),
            file_entry(CONTRACT_ROOT / "target_matrix.csv", "joint_target_matrix_114"),
            file_entry(CONTRACT_ROOT / "input_manifest.json", "joint_input_manifest"),
        ],
        "independent_review": dir_entries(REVIEW_ROOT, "independent_review_20261103"),
        "takeover_planning": dir_entries(TAKEOVER_ROOT, "takeover_planning_20261103"),
        "evidence_core": [
            file_entry(EVIDENCE_ROOT / "target_status.csv", "impl_ledger_114"),
            file_entry(EVIDENCE_ROOT / "rework/compare_final/batch2_gold_compare.json", "last_stage_compare_S4"),
            file_entry(EVIDENCE_ROOT / "rework/compare_final/target_status_strict.csv", "strict_status_s4"),
            file_entry(EVIDENCE_ROOT / "rework/strict_metrics_frozen.json", "frozen_metrics_by_stage"),
            file_entry(EVIDENCE_ROOT / "rework/comparator_probe_summary.json", "comparator_probe_summary"),
            file_entry(EVIDENCE_ROOT / "rework/pytest_final.log", "pytest_full_log"),
            file_entry(EVIDENCE_ROOT / "rework/d02_http_ui/README.md", "d02_http_ui_readme"),
            *dir_entries(EVIDENCE_ROOT / "rework/d02_http_ui", "d02_http_ui_file"),
            *dir_entries(EVIDENCE_ROOT / "rework/regression", "rework_regression_file"),
        ],
        "b1": {
            "run_dir": str(B1_ROOT),
            "files": [
                file_entry(B1_ROOT / name, "b1_primary")
                for name in (
                    "source_manifest.json",
                    "execution_contract.json",
                    "B1_report.md",
                    "review_response.md",
                    "decimal_checks.csv",
                    "candidate_screen_log.csv",
                    "budget_ledger.csv",
                )
                if (B1_ROOT / name).is_file()
            ],
            "dirs": {
                sub: dir_entries(B1_ROOT / sub, f"b1_{sub}", "*")
                for sub in ("annotations", "blind_annotation_context", "review_package")
                if (B1_ROOT / sub).is_dir()
            },
        },
        "notes": [
            "本清单不写自身哈希；外层索引由 validation_index.json 记录。",
            "所有路径为绝对路径，便于独立复核方逐条重算 sha256。",
        ],
    }

    out = root / "takeover_manifest.json"
    out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
