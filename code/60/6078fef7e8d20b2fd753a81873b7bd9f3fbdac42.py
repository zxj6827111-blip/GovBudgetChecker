"""同一材料、同一生产提示词，比较两个模型配置的 AI 审查产出。

用法：
    UPLOAD_DIR=scratch/llm_compare/uploads .venv/bin/python -I scratch/llm_compare/run_one.py \
        --profile-id model-mv23o9rd-a24n0j9u --out scratch/llm_compare/gpt61sol.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FIXTURE = ROOT / "tests" / "fixtures" / "sample_page_data.json"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile-id", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--timeout-s", type=int, default=480)
    parser.add_argument("--max-tokens", type=int, default=32000)
    args = parser.parse_args()

    # 与生产一致：窗口/并发沿用环境默认；仅统一模型的超时与输出上限，避免口径不公。
    from src.services.ai_model_settings import resolve_runtime
    from src.schemas.issues import AnalysisConfig, JobContext
    from src.services.ai_findings import AIFindingsService

    runtime = resolve_runtime(args.profile_id)
    runtime["timeout_s"] = args.timeout_s
    runtime["max_tokens"] = args.max_tokens

    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    page_texts = fixture["page_texts"]
    context = JobContext(
        job_id=f"llm-compare-{runtime['id']}"[:40],
        pdf_path=str(FIXTURE),
        ocr_text="\n".join(page_texts),
        page_texts=page_texts,
        page_tables=fixture.get("page_tables") or [],
        pages=int(fixture.get("page_count") or len(page_texts)),
        filesize=int(fixture.get("source_pdf_bytes") or 0),
    )
    config = AnalysisConfig(
        ai_enabled=True,
        enable_ai_analysis=True,
        rule_enabled=False,
        ai_timeout=runtime["timeout_s"],
        ai_temperature=0,
        ai_profile_id=runtime["id"],
    )
    service = AIFindingsService(config, runtime_config=runtime)
    started = time.time()
    issues = asyncio.run(service.analyze(context))
    elapsed = time.time() - started

    payload = {
        "profile": {k: runtime.get(k) for k in ("id", "name", "model", "base_url", "timeout_s", "max_tokens", "reasoning_effort", "thinking")},
        "elapsed_s": round(elapsed, 1),
        "window_meta": getattr(service, "_windows_meta", []),
        "coverage": getattr(service, "ai_coverage", {}),
        "diagnostics": getattr(service, "audit_diagnostics", {}),
        "errors": getattr(service, "ai_errors", []),
        "issues": [issue.model_dump() for issue in issues],
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"profile": payload["profile"], "elapsed_s": elapsed, "issue_count": len(issues),
                      "errors": len(payload["errors"]), "out": str(out)}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    os.chdir(ROOT)
    raise SystemExit(main())
