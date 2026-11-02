"""年度公开时限的读写端点。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import ValidationError

from api.auth_utils import require_admin, require_login
from src.services import disclosure_calendar as calendar
from src.services.audit_log import append_audit_event

router = APIRouter()


@router.get("/api/disclosure-calendar")
async def get_calendar(request: Request):
    require_login(request)
    return {
        "rows": calendar.load_rows(),
        "by_year": calendar.by_year(),
        "kinds": list(calendar.KINDS),
        "levels": list(calendar.LEVELS),
    }


@router.post("/api/admin/disclosure-calendar")
async def put_calendar(request: Request):
    _, _, user = require_admin(request)
    payload = await request.json()
    entries = payload.get("entries") if isinstance(payload, dict) else None
    if not isinstance(entries, list) or not 1 <= len(entries) <= 8:
        raise HTTPException(422, "entries 需为 1-8 条记录")
    # 先全部校验再落盘：半个年度写进去会让"是否逾期"按混合日期判定，比不写更糟。
    validated = []
    errors = []
    for item in entries:
        try:
            validated.append(calendar.CalendarEntry.model_validate(item))
        except ValidationError as exc:
            errors.append(f"{item.get('year')}{item.get('kind')}{item.get('level')}: "
                          + "；".join(str(e.get("msg")) for e in exc.errors(include_input=False)))
    if errors:
        raise HTTPException(422, "；".join(errors))
    saved = [calendar.upsert(entry) for entry in validated]
    append_audit_event(action="disclosure_calendar.update", actor=str(user.get("username") or ""),
                       result="success", resource_type="system_setting",
                       details={"entries": [{"year": r["year"], "kind": r["kind"], "level": r["level"],
                                             "deadline": r["deadline"]} for r in saved]})
    return {"saved": saved, "by_year": calendar.by_year()}
