#!/usr/bin/env python3
"""API latency baseline and optional threshold assertions."""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("TESTING", "true")
os.environ.setdefault("GOVBUDGET_AUTH_ENABLED", "false")
os.environ.setdefault("GOVBUDGET_API_KEY", "dev")

from fastapi.testclient import TestClient

from api import runtime
from api.main import app


@dataclass(frozen=True)
class EndpointCase:
    name: str
    path: str
    note: str = ""


def _percentile(values: List[float], p: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    ordered = sorted(values)
    k = (len(ordered) - 1) * (p / 100.0)
    lo = int(k)
    hi = min(lo + 1, len(ordered) - 1)
    if lo == hi:
        return ordered[lo]
    frac = k - lo
    return ordered[lo] * (1 - frac) + ordered[hi] * frac


def _resolve_heaviest_department_id(client: TestClient) -> str | None:
    try:
        storage = runtime.require_org_storage()
        departments = storage.get_departments()
        if not departments:
            return None
        heaviest = max(
            departments,
            key=lambda dept: len(storage.get_org_jobs(dept.id, include_children=True)),
        )
        return heaviest.id
    except Exception:
        resp = client.get("/api/departments")
        if resp.status_code != 200:
            return None
        payload = resp.json() if isinstance(resp.json(), dict) else {}
        departments = payload.get("departments") if isinstance(payload, dict) else None
        if not isinstance(departments, list) or not departments:
            return None
        dept = departments[0]
        if isinstance(dept, dict):
            return str(dept.get("id") or "")
        return None


def _resolve_heaviest_org_id(client: TestClient) -> str | None:
    try:
        storage = runtime.require_org_storage()
        orgs = storage.get_all()
        if not orgs:
            return None
        heaviest = max(
            orgs,
            key=lambda org: len(storage.get_org_jobs(org.id, include_children=False)),
        )
        return heaviest.id
    except Exception:
        resp = client.get("/api/organizations/list")
        if resp.status_code != 200:
            return None
        payload = resp.json() if isinstance(resp.json(), dict) else {}
        orgs = payload.get("organizations") if isinstance(payload, dict) else None
        if not isinstance(orgs, list) or not orgs:
            return None
        org = orgs[0]
        if isinstance(org, dict):
            return str(org.get("id") or "")
        return None


def _build_cases(client: TestClient) -> tuple[List[EndpointCase], List[str]]:
    cases = [
        EndpointCase("jobs_list_50", "/api/jobs?limit=50&offset=0"),
        EndpointCase("jobs_list_200", "/api/jobs?limit=200&offset=0"),
    ]
    skipped: List[str] = []

    dept_id = _resolve_heaviest_department_id(client)
    if dept_id:
        cases.append(
            EndpointCase(
                "department_stats",
                f"/api/departments/{dept_id}/stats",
                note=f"dept_id={dept_id}",
            )
        )
    else:
        skipped.append("department_stats (no department found)")

    org_id = _resolve_heaviest_org_id(client)
    if org_id:
        cases.append(
            EndpointCase(
                "organization_jobs_50",
                f"/api/organizations/{org_id}/jobs?limit=50&offset=0",
                note=f"org_id={org_id}",
            )
        )
    else:
        skipped.append("organization_jobs_50 (no organization found)")

    return cases, skipped


def _run_case(
    client: TestClient,
    case: EndpointCase,
    warmup: int,
    iterations: int,
) -> Dict[str, Any]:
    latencies_ms: List[float] = []
    payload_sizes: List[int] = []
    status_counts: Counter[int] = Counter()
    errors: List[str] = []

    total_runs = warmup + iterations
    for idx in range(total_runs):
        started = time.perf_counter()
        response = client.get(case.path)
        elapsed_ms = (time.perf_counter() - started) * 1000

        if idx < warmup:
            continue

        latencies_ms.append(elapsed_ms)
        payload_sizes.append(len(response.content or b""))
        status_counts[response.status_code] += 1
        if response.status_code >= 400:
            errors.append(f"status={response.status_code}")

    avg_ms = statistics.mean(latencies_ms) if latencies_ms else 0.0
    return {
        "name": case.name,
        "path": case.path,
        "note": case.note,
        "samples": len(latencies_ms),
        "avg_ms": round(avg_ms, 2),
        "p95_ms": round(_percentile(latencies_ms, 95), 2),
        "min_ms": round(min(latencies_ms), 2) if latencies_ms else 0.0,
        "max_ms": round(max(latencies_ms), 2) if latencies_ms else 0.0,
        "payload_avg_bytes": int(statistics.mean(payload_sizes)) if payload_sizes else 0,
        "payload_max_bytes": max(payload_sizes) if payload_sizes else 0,
        "status_codes": {str(code): count for code, count in sorted(status_counts.items())},
        "error_count": len(errors),
    }


def _load_thresholds(path: Path) -> Dict[str, Dict[str, float]]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return {}
    metrics = payload.get("metrics", payload)
    if not isinstance(metrics, dict):
        return {}
    parsed: Dict[str, Dict[str, float]] = {}
    for name, limits in metrics.items():
        if not isinstance(name, str) or not isinstance(limits, dict):
            continue
        metric_limits: Dict[str, float] = {}
        for metric_name, limit in limits.items():
            try:
                metric_limits[str(metric_name)] = float(limit)
            except Exception:
                continue
        if metric_limits:
            parsed[name] = metric_limits
    return parsed


def _assert_thresholds(
    results: List[Dict[str, Any]],
    thresholds: Dict[str, Dict[str, float]],
) -> List[str]:
    failures: List[str] = []
    by_name = {item.get("name"): item for item in results}
    for case_name, limits in thresholds.items():
        result = by_name.get(case_name)
        if not isinstance(result, dict):
            continue
        for metric_name, max_value in limits.items():
            value = result.get(metric_name)
            if value is None:
                continue
            try:
                numeric = float(value)
            except Exception:
                continue
            if numeric > max_value:
                failures.append(
                    f"{case_name}.{metric_name}: actual={numeric:.2f}ms, limit={max_value:.2f}ms"
                )
    return failures


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run API performance baseline checks.")
    parser.add_argument("--warmup", type=int, default=3, help="Warmup requests per endpoint.")
    parser.add_argument(
        "--iterations",
        type=int,
        default=10,
        help="Measured requests per endpoint after warmup.",
    )
    parser.add_argument(
        "--threshold-file",
        type=Path,
        default=REPO_ROOT / "scripts" / "perf_thresholds.json",
        help="Threshold JSON path used by --assert-thresholds.",
    )
    parser.add_argument(
        "--assert-thresholds",
        action="store_true",
        help="Fail with exit code 1 when any threshold is exceeded.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional path to save the generated report JSON.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if args.warmup < 0 or args.iterations <= 0:
        print("warmup must be >= 0 and iterations must be > 0", file=sys.stderr)
        return 2

    with TestClient(app) as client:
        cases, skipped = _build_cases(client)
        results = [_run_case(client, case, args.warmup, args.iterations) for case in cases]

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "warmup": args.warmup,
        "iterations": args.iterations,
        "results": results,
        "skipped": skipped,
    }

    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    print(json.dumps(report, ensure_ascii=False, indent=2))

    if not args.assert_thresholds:
        return 0

    thresholds = _load_thresholds(args.threshold_file)
    failures = _assert_thresholds(results, thresholds)
    if failures:
        print("threshold assertion failed:", file=sys.stderr)
        for line in failures:
            print(f"- {line}", file=sys.stderr)
        return 1

    print(f"threshold assertion passed ({args.threshold_file})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
