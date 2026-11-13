"""交叉复核实验：把 A 的问题清单交给 B 逐条判定，看能否拦住误报/保留真问题。"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FIXTURE = ROOT / "tests" / "fixtures" / "sample_page_data.json"


def page_of(issue: dict) -> int | None:
    loc = issue.get("location") or {}
    pairs = loc.get("evidence_pairs") or []
    if pairs and isinstance(pairs[0], dict) and pairs[0].get("page"):
        return int(pairs[0]["page"])
    if issue.get("page_number"):
        return int(issue["page_number"])
    m = re.search(r"第\s*(\d+)\s*页", str(issue.get("message") or ""))
    return int(m.group(1)) if m else None


def evidence_text(issue: dict) -> str:
    ev = issue.get("evidence")
    if isinstance(ev, list) and ev:
        return str((ev[0] or {}).get("text") or "")
    if isinstance(ev, dict):
        return str(ev.get("text") or "")
    return str(ev or "")


def build_prompt(issues: list[dict], page_texts: list[str]) -> str:
    pages = sorted({p for p in (page_of(i) for i in issues) if p})
    doc = "\n\n".join(f"【第{p}页】\n{page_texts[p-1][:1200]}" for p in pages)
    rows = []
    for idx, issue in enumerate(issues, 1):
        rows.append({
            "id": idx,
            "page": page_of(issue),
            "title": issue.get("title"),
            "message": str(issue.get("message") or "")[:300],
            "evidence": evidence_text(issue)[:200],
        })
    return (
        "你是独立复核员。下面给出材料原文片段和另一位审查员的问题清单。\n"
        "请逐条判断该问题是否成立：\n"
        "- valid：原文支持，且确实违反预决算公开规范；\n"
        "- invalid：原文不支持、引用错误，或不属于问题；\n"
        "- uncertain：证据不足，需要人工核对。\n"
        "严格只输出 JSON 数组，元素为 {\"id\": 编号, \"verdict\": \"valid|invalid|uncertain\", \"reason\": \"简短理由\"}，"
        "不要新增问题，不要输出其它文字。\n\n"
        f"=== 问题清单 ===\n{json.dumps(rows, ensure_ascii=False, indent=1)}\n\n"
        f"=== 材料原文片段 ===\n{doc}\n"
    )


async def run(findings_path: Path, verifier_id: str, out_path: Path) -> None:
    from src.services.ai_model_settings import resolve_runtime, create_client

    runtime = resolve_runtime(verifier_id)
    runtime["max_tokens"] = 16000
    runtime["timeout_s"] = 300
    issues = json.loads(findings_path.read_text(encoding="utf-8"))["issues"]
    page_texts = json.loads(FIXTURE.read_text(encoding="utf-8"))["page_texts"]
    prompt = build_prompt(issues, page_texts)

    client = create_client(runtime, retries=0)
    response = await client.chat(
        messages=[{"role": "system", "content": "你是严格的 JSON 输出助手。"},
                  {"role": "user", "content": prompt}],
        preferred_provider="managed", model=runtime["model"], temperature=0,
        max_tokens=runtime["max_tokens"], timeout=runtime["timeout_s"],
    )
    content = str(response.get("content") or "")
    match = re.search(r"\[.*\]", content, re.S)
    verdicts = json.loads(match.group(0)) if match else []
    payload = {
        "verifier": {k: runtime.get(k) for k in ("id", "name", "model")},
        "target": str(findings_path),
        "usage": response.get("tokens"),
        "finish_reason": response.get("finish_reason"),
        "verdicts": verdicts,
        "raw_tail": content[-500:] if not verdicts else "",
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    counts: dict[str, int] = {}
    for item in verdicts:
        counts[str(item.get("verdict"))] = counts.get(str(item.get("verdict")), 0) + 1
    print(json.dumps({"verifier": runtime["model"], "target": findings_path.name,
                      "verdicts": len(verdicts), "counts": counts}, ensure_ascii=False), flush=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--findings", required=True)
    parser.add_argument("--verifier-profile-id", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    asyncio.run(run(Path(args.findings), args.verifier_profile_id, Path(args.out)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
