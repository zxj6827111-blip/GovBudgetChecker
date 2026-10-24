"""真实链路 A/B 回归：上传 → 触发分析 → 采集 check_coverage。

用法:
    .venv/bin/python scripts/ab_coverage.py --tag guard_on  <pdf> [<pdf> ...]
    .venv/bin/python scripts/ab_coverage.py --tag guard_off <pdf> [<pdf> ...]

前置：后端与 worker 已启动（TESTING=1 JOB_QUEUE_ENABLED=true）。
结果写入 outputs/ab_coverage/<tag>.json，不覆盖历史。
"""
from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path

import requests

BASE = "http://127.0.0.1:8001"
ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "outputs" / "ab_coverage"


def api_key() -> str:
    for line in (ROOT / ".env").read_text().splitlines():
        m = re.match(r"^GOVBUDGET_API_KEY=(.*)$", line)
        if m:
            return m.group(1).strip().strip('"').strip("'")
    return ""


def _retry(fn, *args, attempts: int = 3, wait: int = 5, **kwargs):
    """后端在长批量中偶发重启；单次 502/连接错误不应让整份样本作废。"""
    last = None
    for i in range(attempts):
        try:
            return fn(*args, **kwargs)
        except Exception as e:  # noqa: BLE001
            last = e
            if i < attempts - 1:
                time.sleep(wait)
    raise last  # type: ignore[misc]


def upload(pdf: Path, key: str, year: str = "") -> str:
    def _do():
        with pdf.open("rb") as fh:
            resp = requests.post(
                f"{BASE}/api/documents/upload",
                headers={"X-API-Key": key},
                files={"file": (pdf.name, fh, "application/pdf")},
                data={"fiscal_year": year, "doc_type": "dept_final"},
                timeout=300,
            )
        resp.raise_for_status()
        return resp.json()["job_id"]
    return _retry(_do)


def analyze(job_id: str, key: str) -> None:
    def _do():
        resp = requests.post(
            f"{BASE}/api/analyze/{job_id}",
            headers={"X-API-Key": key, "Content-Type": "application/json"},
            json={"mode": "legacy", "use_local_rules": True, "use_ai_assist": False},
            timeout=120,
        )
        resp.raise_for_status()
    _retry(_do)


def wait_done(job_id: str, limit: int = 240) -> dict:
    status_file = ROOT / "uploads" / job_id / "status.json"
    deadline = time.time() + limit
    while time.time() < deadline:
        try:
            data = json.loads(status_file.read_text())
        except Exception:  # noqa: BLE001
            time.sleep(3)
            continue
        if data.get("check_coverage") or data.get("status") in {"failed", "review_required", "done"}:
            return data
        time.sleep(3)
    return {}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True)
    ap.add_argument("--dir", help="递归扫描该目录下的 PDF（与位置参数二选一）")
    ap.add_argument("pdfs", nargs="*")
    args = ap.parse_args()

    targets = [Path(p).expanduser() for p in args.pdfs]
    if args.dir:
        targets.extend(sorted(Path(args.dir).expanduser().rglob("*.pdf")))
    if not targets:
        raise SystemExit("没有待分析的 PDF")

    key = api_key()
    if not key:
        raise SystemExit("缺少 GOVBUDGET_API_KEY")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    records = []
    for pdf in targets:
        t0 = time.time()
        try:
            job_id = upload(pdf, key)
            analyze(job_id, key)
            data = wait_done(job_id)
            cc = data.get("check_coverage") or {}
            unresolved = []
            for reason in data.get("review_reasons") or []:
                if reason.get("code") == "rules_insufficient_data":
                    unresolved = [u.get("rule_id") for u in reason.get("unresolved_rules") or []]
            rec = {
                "file": pdf.name,
                "job_id": job_id,
                "status": data.get("status"),
                "applicable": cc.get("applicable_total"),
                "completed": cc.get("completed_total"),
                "unresolved": cc.get("unresolved_total"),
                "rate": cc.get("coverage_rate"),
                "by_reason": cc.get("by_reason"),
                "unresolved_rules": unresolved,
                "seconds": round(time.time() - t0, 1),
            }
        except Exception as e:  # noqa: BLE001
            rec = {"file": pdf.name, "error": f"{type(e).__name__}: {e}"}
        records.append(rec)
        print(f"  {rec['file'][:30]:<32} rate={rec.get('rate')} unresolved={rec.get('unresolved')} "
              f"规则={rec.get('unresolved_rules')} {rec.get('seconds')}s", flush=True)

    out = OUT_DIR / f"{args.tag}.json"
    out.write_text(json.dumps(records, ensure_ascii=False, indent=1))
    rates = [r["rate"] for r in records if r.get("rate") is not None]
    if rates:
        print(f"\n[{args.tag}] 平均覆盖率 {sum(rates)/len(rates):.2%}  n={len(rates)}")
    print(f"结果已写入 {out}")


if __name__ == "__main__":
    main()
