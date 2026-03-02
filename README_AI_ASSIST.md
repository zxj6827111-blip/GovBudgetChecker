# AI Assist Guide

## Overview
This project uses an AI extractor microservice to assist rule-based analysis.

Current implementation is a lightweight local service (`ai_extractor_service.py`) with a stable contract:
- `GET /health`
- `POST /ai/extract/v1`

It is intended for integration stability and can be replaced by a stronger model service later, as long as the API contract remains compatible.

## Integration Architecture
- Backend switch: `AI_ASSIST_ENABLED`
- Backend target: `AI_EXTRACTOR_URL`
- Backend calls extractor endpoint when AI assist is enabled.

## Quick Start

### 1) Start AI extractor
```bash
uvicorn ai_extractor_service:app --host 0.0.0.0 --port 9009
```

### 2) Start backend
```bash
GOVBUDGET_AUTH_ENABLED=true GOVBUDGET_API_KEY=dev-local-key \
AI_ASSIST_ENABLED=true AI_EXTRACTOR_URL=http://127.0.0.1:9009/ai/extract/v1 \
python -m uvicorn api.main:app --reload --port 8000
```

Or use Makefile default:
```bash
make backend
```

### 3) Health checks
```bash
curl http://127.0.0.1:9009/health
curl http://127.0.0.1:8000/api/health
curl http://127.0.0.1:8000/api/ready
```

## Extractor API Contract

### Request
`POST /ai/extract/v1`

```json
{
  "task": "R33110_pairs_v1",
  "section_text": "...",
  "language": "zh",
  "doc_hash": "optional",
  "max_windows": 3
}
```

### Response
```json
{
  "hits": [
    {
      "budget_text": "100",
      "budget_span": [12, 15],
      "final_text": "95",
      "final_span": [40, 42],
      "stmt_text": "文本比较语句",
      "stmt_span": [0, 8],
      "reason_text": null,
      "reason_span": null,
      "item_title": null,
      "clip": "..."
    }
  ],
  "meta": {
    "task": "R33110_pairs_v1",
    "cached": false,
    "engine": "fallback",
    "count": 1
  }
}
```

## Effective Environment Variables

| Variable | Default | Description |
|---|---|---|
| `AI_ASSIST_ENABLED` | `true` | Enable/disable AI assist in backend |
| `AI_EXTRACTOR_URL` | `http://127.0.0.1:9009/ai/extract/v1` | Extractor endpoint |
| `GOVBUDGET_AUTH_ENABLED` | `true` | Backend auth switch |
| `GOVBUDGET_API_KEY` | empty | Required when auth is enabled |

## Testing

Run current test suite (authoritative commands):

```bash
python -m pytest
npm --prefix app run test:e2e
```

Recommended focused checks around AI-assisted routing:

```bash
python -m pytest tests/test_budget_rule_routing.py -q
python -m pytest tests/test_api_contract.py -q
```

## Troubleshooting

### `/api/ready` reports `ai_extractor_reachable=false`
- Check `AI_EXTRACTOR_URL`.
- Confirm extractor process is running.
- Check network and port reachability.

### Need to temporarily disable AI assist
```bash
export AI_ASSIST_ENABLED=false
```
(Windows PowerShell)
```powershell
$env:AI_ASSIST_ENABLED='false'
```

## Notes
- This guide intentionally reflects current code behavior only.
- If extractor implementation changes, keep API contract backward compatible.
