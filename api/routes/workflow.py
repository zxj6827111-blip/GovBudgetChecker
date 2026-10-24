"""Authenticated remediation workflow endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from starlette.concurrency import run_in_threadpool

from api import runtime
from api.auth_utils import require_login
from src.services.audit_log import append_audit_event
from src.services import issue_workflow_store


router = APIRouter()


@router.get("/api/workflow")
async def get_workflow(request: Request, job_id: str | None = Query(default=None, min_length=1)):
    _, _, user = require_login(request)
    return await run_in_threadpool(issue_workflow_store.get_visible_state, user, job_id=job_id)


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
        # Keep the live canonical report in step with the just-persisted decision.
        # Historical analysis snapshots remain immutable; report downloads project
        # the same decision only when its revision/identity still matches.
        normalized_job_id = str(body.get("job_id") or "").strip()
        status_payload = runtime.get_job_status_payload(normalized_job_id, apply_issue_filters=False)
        status_payload["human_decisions"] = issue_workflow_store.project_job_issue_decisions(
            normalized_job_id, status_payload
        )
        from src.schemas.review_report import build_review_report

        status_payload["review_report"] = build_review_report(status_payload)
        runtime.merge_job_status(
            runtime.UPLOAD_ROOT / normalized_job_id,
            {
                "human_decisions": status_payload["human_decisions"],
                "review_report": status_payload["review_report"],
            },
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
