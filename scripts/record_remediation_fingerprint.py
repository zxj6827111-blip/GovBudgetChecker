#!/usr/bin/env python
"""ZCode 整改基线指纹：记录运行链路版本与非敏感配置指纹（ZC-00 交付 1）。

输出只含非敏感信息：git 版本、Python/关键依赖版本、AI 与双模式开关、
容差配置、规则注册表规模、生产入口调用链的模块级锚点。不读取业务数据、
不包含密钥或连接串。

用法：
    python scripts/record_remediation_fingerprint.py --output outputs/zcode_remediation/ZC-00/fingerprint.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _git(*args: str) -> str:
    try:
        return subprocess.run(
            ["git", *args], cwd=_REPO_ROOT, capture_output=True, text=True, check=True
        ).stdout.strip()
    except Exception as exc:
        return f"unavailable: {exc}"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def _entry_chain_anchors() -> Dict[str, List[str]]:
    """真实生产链路锚点：函数级入口到落库/导出的关键跳转（行号不写入）。"""
    return {
        "upload_and_pipeline": [
            "api/main.py::_run_pipeline",
            "api/main.py::_run_pipeline_inner",
            "api/main.py::_run_pipeline_body",
        ],
        "analysis": [
            "src/services/analyze_dual.py::DualModeAnalyzer.analyze",
            "src/services/engine_rule_runner.py::EngineRuleRunner.run_rules",
            "src/services/ai_findings.py::AIFindingsService.analyze（默认关闭）",
        ],
        "evidence_gate": [
            "src/services/evidence_guard.py::apply_evidence_completeness",
            "src/services/evidence_guard.py::evaluate_finding_evidence",
        ],
        "structured_sidecar": [
            "api/main.py::run_structured_ingest",
            "src/services/structured_ingest_runner.py::run_structured_ingest",
        ],
        "review_and_export": [
            "api/routes/reviews.py",
            "api/routes/reports.py::download_report",
            "api/worker.py::_run_pipeline（与 API 共链路）",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    fingerprint: Dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "repo": {
            "head_commit": _git("rev-parse", "HEAD"),
            "branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
            "dirty": bool(_git("status", "--porcelain")),
        },
        "runtime": {
            "python": platform.python_version(),
            "platform": platform.platform(),
        },
        "entry_chain": _entry_chain_anchors(),
        "config_fingerprint": {},
        "rules_registry": {},
        "dependency_versions": {},
    }

    # ---- 非敏感配置指纹 ----
    try:
        import yaml

        app_config = yaml.safe_load((_REPO_ROOT / "config" / "app.yaml").read_text(encoding="utf-8"))
        fingerprint["config_fingerprint"] = {
            "app_yaml_sha256": _sha256_file(_REPO_ROOT / "config" / "app.yaml"),
            "dual_mode": app_config.get("dual_mode", {}),
            "ai_provider_model": {
                key: app_config.get("ai", {}).get(key)
                for key in ("provider", "model")
            },
            "tolerances": app_config.get("tolerances", app_config.get("check", {})),
        }
    except Exception as exc:
        fingerprint["config_fingerprint"] = {"error": str(exc)}

    try:
        from api.config import AI_ASSIST_ENABLED

        fingerprint["config_fingerprint"]["env_ai_assist_enabled_default"] = AI_ASSIST_ENABLED
    except Exception as exc:
        fingerprint["config_fingerprint"]["env_ai_assist_enabled_default"] = f"unavailable: {exc}"

    # ---- 规则注册表规模 ----
    try:
        from src.engine.rules_v33 import ALL_RULES as FINAL_ALL_RULES
        from src.engine.budget_rules import ALL_BUDGET_RULES
        from src.engine.common_rules import ALL_COMMON_RULES

        fingerprint["rules_registry"] = {
            "final_rules": len(FINAL_ALL_RULES),
            "budget_rules": len(ALL_BUDGET_RULES),
            "common_rules": len(ALL_COMMON_RULES),
        }
    except Exception as exc:
        fingerprint["rules_registry"] = {"error": str(exc)}

    # ---- 关键依赖版本 ----
    for module_name in ("pdfplumber", "fitz", "pydantic", "fastapi"):
        try:
            module = __import__(module_name)
            fingerprint["dependency_versions"][module_name] = getattr(
                module, "__version__", "unknown"
            )
        except Exception as exc:
            fingerprint["dependency_versions"][module_name] = f"unavailable: {exc}"

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(fingerprint, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"fingerprint written: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
