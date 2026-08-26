"""Authenticated remediation workflow endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from api.auth_utils import require_login
from src.services.audit_log import append_audit_event
from src.services import issue_workflow_store


router = APIRouter()


@router.get("/api/workflow")
async def get_workflow(request: Request):
    _, _, user = require_login(request)
    return issue_workflow_store.get_visible_state(user)


@router.post("/api/workflow")
async def mutate_workflow(request: Request):
    _, _, user = require_login(request)
    try:
        body = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="invalid JSON body") from exc
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="JSON object required")

    action = str(body.get("action") or "").strip()
    actor = str(user.get("username") or "")
    if action == "update_issue":
        state = await issue_workflow_store.update_issue(
            user,
            job_id=str(body.get("job_id") or ""),
            issue_id=str(body.get("issue_id") or ""),
            status=str(body.get("status") or ""),
            note=body.get("note") if isinstance(body.get("note"), str) else None,
        )
        append_audit_event(
            action="workflow.issue.update",
            actor=actor,
            result="success",
            resource_type="issue_workflow",
            resource_id=f"{body.get('job_id') or ''}::{body.get('issue_id') or ''}",
            details={"status": body.get("status")},
        )
        return state

    if action == "create_package":
        result = await issue_workflow_store.create_package(
            user,
            name=body.get("name") if isinstance(body.get("name"), str) else None,
            job_ids=body.get("job_ids"),
            issue_keys=body.get("issue_keys"),
        )
        append_audit_event(
            action="workflow.package.create",
            actor=actor,
            result="success",
            resource_type="remediation_package",
            resource_id=str(result["package"]["id"]),
            resource_name=str(result["package"]["name"]),
            details={"job_count": len(result["package"]["job_ids"]), "issue_count": len(result["package"]["issue_keys"])},
        )
        return result

    raise HTTPException(status_code=400, detail="unsupported action")
