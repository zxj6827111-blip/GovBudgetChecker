# Performance Baseline

Use the local TestClient baseline to detect API latency regressions for key list endpoints.

## Commands

```bash
make perf-baseline
```

Run with threshold assertions:

```bash
make perf-check
```

## Endpoints covered

- `/api/jobs?limit=50&offset=0`
- `/api/jobs?limit=200&offset=0`
- `/api/departments/{dept_id}/stats` (auto-selects the heaviest department)
- `/api/organizations/{org_id}/jobs?limit=50&offset=0` (auto-selects the heaviest organization)

## Threshold file

Default threshold config: `scripts/perf_thresholds.json`

You can override it:

```bash
python scripts/perf_baseline.py --assert-thresholds --threshold-file path/to/custom.json
```
