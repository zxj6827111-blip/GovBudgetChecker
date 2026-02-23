"""
Minimal AI extractor service used by backend integration tests and docker deployment.

This service keeps a stable contract for:
- GET /health
- POST /ai/extract/v1
"""

from __future__ import annotations

import re
import time
from typing import Any, Dict, List, Optional

from fastapi import FastAPI
from pydantic import BaseModel, Field


class ExtractRequest(BaseModel):
    task: str = Field(default="R33110_pairs_v1")
    section_text: str
    language: str = Field(default="zh")
    doc_hash: Optional[str] = None
    max_windows: int = Field(default=3, ge=1, le=10)


app = FastAPI(title="GovBudgetChecker AI Extractor")


def _simple_pair_extract(text: str) -> List[Dict[str, Any]]:
    """
    Lightweight fallback extractor.
    It intentionally keeps low recall but returns data in the expected schema.
    """
    hits: List[Dict[str, Any]] = []

    # Capture very common pattern snippets in Chinese budget reports.
    pattern = re.compile(
        r"(预算|年初预算|一般公共预算)[^\d]{0,12}(\d+(?:\.\d+)?)"
        r".{0,40}?"
        r"(决算|本年支出|支出合计)[^\d]{0,12}(\d+(?:\.\d+)?)",
        re.S,
    )

    for match in pattern.finditer(text):
        budget_text = match.group(2)
        final_text = match.group(4)
        stmt_text = "文本比较语句"

        budget_start = match.start(2)
        final_start = match.start(4)
        stmt_start = match.start()

        clip_start = max(0, match.start() - 30)
        clip_end = min(len(text), match.end() + 30)
        clip = text[clip_start:clip_end]

        hits.append(
            {
                "budget_text": budget_text,
                "budget_span": [budget_start, budget_start + len(budget_text)],
                "final_text": final_text,
                "final_span": [final_start, final_start + len(final_text)],
                "stmt_text": stmt_text,
                "stmt_span": [stmt_start, stmt_start + len(stmt_text)],
                "reason_text": None,
                "reason_span": None,
                "item_title": None,
                "clip": clip,
            }
        )

        if len(hits) >= 20:
            break

    return hits


@app.get("/health")
async def health() -> Dict[str, Any]:
    return {"status": "ok", "service": "ai-extractor", "ts": time.time()}


@app.post("/ai/extract/v1")
async def extract(req: ExtractRequest) -> Dict[str, Any]:
    if req.task == "semantic_audit_v1":
        return {"hits": [], "meta": {"task": req.task, "engine": "fallback"}}

    hits = _simple_pair_extract(req.section_text or "")
    return {
        "hits": hits,
        "meta": {
            "task": req.task,
            "cached": False,
            "engine": "fallback",
            "count": len(hits),
        },
    }

