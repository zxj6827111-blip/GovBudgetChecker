"""把两个模型的产出按 golden.json 真值分组，输出可对照清单。"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

TRUTHS = [
    ("T1", "defect", 2, [r"202\s*年度"]),
    ("T5", "defect", 26, [r"接待费.*0\.00.*持平", r"持平.*接待费", r"减少.*0\.00.*持平"]),
    ("T6", "defect", 27, [r"(国内|公务)接待.*(未|缺失|未披露).*(批次|人次)", r"(批次|人次).*(未|缺失|未披露)"]),
    ("T2", "rounding", 10, [r"1,?367\.7[56]"]),
    ("T3", "rounding", 10, [r"1,?367\.7[56]"]),
    ("T4", "rounding", 15, [r"14\.4[34]", r"416\.3[67]"]),
]


def page_of(issue: dict) -> int | None:
    loc = issue.get("location") or {}
    pairs = loc.get("evidence_pairs") or []
    if pairs and isinstance(pairs[0], dict) and pairs[0].get("page"):
        return int(pairs[0]["page"])
    if issue.get("page_number"):
        return int(issue["page_number"])
    metrics = issue.get("metrics") or {}
    if metrics.get("ai_reported_page"):
        return int(metrics["ai_reported_page"])
    m = re.search(r"第\s*(\d+)\s*页", str(issue.get("message") or ""))
    return int(m.group(1)) if m else None


def haystack(issue: dict) -> str:
    parts = [str(issue.get(k) or "") for k in ("title", "message", "check", "table_or_section", "suggestion")]
    ev = issue.get("evidence")
    if isinstance(ev, list):
        for item in ev:
            if isinstance(item, dict):
                parts.append(str(item.get("text") or item.get("quote") or ""))
            else:
                parts.append(str(item))
    elif isinstance(ev, dict):
        parts.append(str(ev.get("text") or ""))
    else:
        parts.append(str(ev or ""))
    return " ".join(parts)


def classify(issue: dict) -> tuple[str, str]:
    page = page_of(issue)
    text = haystack(issue)
    best = ("UNMATCHED", "")
    for tid, kind, tpage, patterns in TRUTHS:
        if page is None:
            continue
        # 目录/年度问题允许 ±0 页；三公类允许 ±1 页；跨表类允许 ±1 页。
        tol = 0 if tid == "T1" else 1
        if abs(page - tpage) > tol:
            continue
        if any(re.search(p, text) for p in patterns):
            return tid, kind
    return best


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    results = {
        "gpt-6.1-sol": load(ROOT / "scratch" / "llm_compare" / "gpt61sol.json"),
        "deepseek-v4.1-flash": load(ROOT / "scratch" / "llm_compare" / "deepseek_v41_flash.json"),
    }
    for name, data in results.items():
        print("=" * 90)
        print(f"{name}  model={data['profile']['model']}  elapsed={data['elapsed_s']}s  issues={len(data['issues'])}  errors={len(data['errors'])}")
        matched: dict[str, list[int]] = {}
        unmatched: list[tuple[int, str, str]] = []
        p28: list[str] = []
        for idx, issue in enumerate(data["issues"]):
            tid, kind = classify(issue)
            page = page_of(issue)
            if tid != "UNMATCHED":
                matched.setdefault(tid, []).append(idx)
            else:
                unmatched.append((idx, page, str(issue.get("title") or "")))
            if page == 28:
                p28.append(str(issue.get("title") or ""))
        print("命中真值:", {k: len(v) for k, v in sorted(matched.items())},
              "| 漏掉:", [t for t, _, _, _ in TRUTHS if t not in matched])
        print("P28 绩效负例违规:", p28 or "无")
        print(f"未命中真值的条目 {len(unmatched)} 条：")
        for idx, page, title in unmatched:
            issue = data["issues"][idx]
            print(f"  [{idx}] p{page} sev={issue.get('severity')} {title} :: {str(issue.get('message') or '')[:110]}")
    print("=" * 90)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
