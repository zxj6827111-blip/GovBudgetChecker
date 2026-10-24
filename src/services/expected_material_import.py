"""应收 CSV：严格预览、同一槽位台账、整批原子导入，不覆盖复核状态。"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from fastapi import HTTPException

from src.db.transaction import transaction_scope
from src.services.material_slot_resolver import decide_slot_allocation
from src.services.material_slot_service import MaterialSlotService
from src.services.material_slot_status import derive_slot_status

COLUMNS = (
    "subject_org_id",
    "subject_kind",
    "fiscal_year",
    "report_kind",
    "material_scope",
    "caliber",
    "due_at",
    "expected_source_url",
)
MAX_BYTES = 1_000_000
MAX_ROWS = 5000


def preview_csv(text: str, org_records: list[dict], visible_org_ids: list[str] | None, *, allow_unknown_due: bool = False) -> dict:
    if len(text.encode("utf-8")) > MAX_BYTES:
        raise HTTPException(413, "CSV 超过 1 MB")
    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff"), newline=""), strict=True)
    if reader.fieldnames != list(COLUMNS):
        raise HTTPException(422, {"error": "invalid_csv_columns", "expected": list(COLUMNS)})
    try:
        sources = list(reader)
    except csv.Error as exc:
        raise HTTPException(422, "CSV 引号或分隔格式不合法") from exc
    rows, errors, seen = [], [], set()
    for line, source in enumerate(sources, 2):
        if line > MAX_ROWS + 1:
            raise HTTPException(413, "CSV 超过 5000 行")
        try:
            if None in source or any(value is None for value in source.values()):
                raise ValueError("列数与表头不一致")
            row = {key: value.strip() for key, value in source.items()}
            org_id = row["subject_org_id"]
            if visible_org_ids is not None and org_id not in visible_org_ids:
                # Do not reveal the existence or name of unauthorized organizations.
                raise HTTPException(403, "CSV 含未授权主体，整批未导入")
            if not re.fullmatch(r"20\d{2}", row["fiscal_year"]):
                raise ValueError("财政年度须为 2000—2099 的四位年份")
            if row["report_kind"] not in ("budget", "final"):
                raise ValueError("文种须为 budget 或 final")
            if row["caliber"] not in ("summary", "self"):
                raise ValueError("口径须明确为 summary 或 self")
            decision = decide_slot_allocation(
                metadata={
                    "organization_id": org_id,
                    "report_year": int(row["fiscal_year"]),
                    "report_kind": row["report_kind"],
                },
                org_records=org_records,
            )
            if not decision.ok or decision.identity is None:
                raise ValueError("主体 ID 不在组织目录中或组织层级未确认")
            identity = decision.identity
            if (
                row["subject_kind"] != identity.subject_kind
                or row["material_scope"] != identity.material_scope
            ):
                raise ValueError("主体层级/材料范围与组织目录冲突")
            if (row["caliber"] == "self") != (row["material_scope"] == "unit_self"):
                raise ValueError("口径与材料范围冲突")
            if allow_unknown_due and not row["due_at"]:
                row["due_at"] = None
            else:
                due = datetime.fromisoformat(row["due_at"].replace("Z", "+00:00"))
                if due.tzinfo is None:
                    raise ValueError("截止时间必须包含时区，例如 2026-10-31T23:59:59+08:00")
                row["due_at"] = due.astimezone(timezone.utc).isoformat()
            url = row["expected_source_url"]
            if url and (
                urlparse(url).scheme not in ("http", "https")
                or not urlparse(url).netloc
                or urlparse(url).username
                or urlparse(url).password
            ):
                raise ValueError("来源链接须为无凭据的 HTTP/HTTPS 地址")
            if identity.slot_key in seen:
                raise ValueError("CSV 内重复槽位身份")
            seen.add(identity.slot_key)
            rows.append({"line": line, "slot_key": identity.slot_key, **row})
        except ValueError as exc:
            errors.append({"line": line, "reason": str(exc)})
    if not rows and not errors:
        errors.append({"line": 2, "reason": "CSV 没有材料记录"})
    canonical = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {
        "valid": not errors,
        "rows": rows,
        "errors": errors,
        "row_count": len(rows),
        "preview_sha256": hashlib.sha256(canonical.encode()).hexdigest(),
        "baseline_scope": "imported_rows_only",
    }


async def import_csv(
    conn: Any,
    *,
    text: str,
    preview_sha256: str,
    org_records: list[dict],
    visible_org_ids: list[str] | None,
    actor: str,
) -> dict:
    preview = preview_csv(text, org_records, visible_org_ids)
    if not preview["valid"]:
        raise HTTPException(422, preview)
    if preview["preview_sha256"] != preview_sha256:
        raise HTTPException(409, "CSV 或组织目录已变更，请重新预览")
    digest = preview["preview_sha256"]
    source_sha = hashlib.sha256(text.encode()).hexdigest()
    service = MaterialSlotService(conn)
    async with transaction_scope(conn):
        # Global order: batch identity, all slot advisory keys, then slot rows.
        await conn.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))", "expected-import:" + digest
        )
        prior = await conn.fetchrow(
            "SELECT receipt FROM expected_material_imports WHERE content_sha256=$1", digest
        )
        if prior:
            receipt = prior["receipt"]
            if isinstance(receipt, str):
                receipt = json.loads(receipt)
            return {**receipt, "replayed": True}
        rows = sorted(preview["rows"], key=lambda row: row["slot_key"])
        for row in rows:
            await service._lock_slot_identity(row["slot_key"])
        receipts = []
        for row in rows:
            decision = decide_slot_allocation(
                metadata={
                    "organization_id": row["subject_org_id"],
                    "report_year": int(row["fiscal_year"]),
                    "report_kind": row["report_kind"],
                },
                org_records=org_records,
            )
            due = datetime.fromisoformat(row["due_at"])
            await service._ensure_slot_row(
                identity=decision.identity,
                decision=decision,
                caliber=row["caliber"],
                due_at=due,
                expected_source_url=row["expected_source_url"] or None,
            )
            current = dict(
                await conn.fetchrow(
                    "SELECT * FROM material_slots WHERE slot_key=$1 FOR UPDATE", row["slot_key"]
                )
            )
            if (
                current["caliber"] not in ("unknown", row["caliber"])
                or current["caliber_conflict_candidate"]
                or (current["due_at"] is not None and current["due_at"] != due)
                or (
                    current["expected_source_url"]
                    and row["expected_source_url"]
                    and current["expected_source_url"] != row["expected_source_url"]
                )
            ):
                raise HTTPException(
                    409,
                    {
                        "error": "existing_slot_conflict",
                        "line": row["line"],
                        "slot_id": str(current["id"]),
                        "message": "既有截止时间、来源或口径不同，整批回滚",
                    },
                )
            if current["applicability_status"] != "applicable":
                raise HTTPException(
                    409, {"error": "existing_applicability_decision", "line": row["line"]}
                )
            # Imports cannot reset a processed document or completed review.
            status, reason = current["status"], current["status_reason"]
            if current["current_document_version_id"] is None:
                derived = derive_slot_status(
                    applicability_status="applicable",
                    due_at=due,
                    identity_resolved=True,
                    has_current_document=False,
                )
                status, reason = derived.status, derived.reason
            await conn.execute(
                "UPDATE material_slots SET caliber=$2, due_at=COALESCE(due_at,$3), expected_source_url=COALESCE(expected_source_url,$4), status=$5, status_reason=$6, updated_at=NOW() WHERE id=$1",
                current["id"],
                row["caliber"],
                due,
                row["expected_source_url"] or None,
                status,
                reason,
            )
            receipts.append(
                {
                    "line": row["line"],
                    "slot_id": str(current["id"]),
                    "slot_key": row["slot_key"],
                    "status": status,
                    "status_reason": reason,
                }
            )
        receipt = {
            "content_sha256": digest,
            "source_sha256": source_sha,
            "actor": actor,
            "imported_at": datetime.now(timezone.utc).isoformat(),
            "row_count": len(receipts),
            "slots": receipts,
            "baseline_scope": "imported_rows_only",
            "replayed": False,
        }
        await conn.execute(
            "INSERT INTO expected_material_imports(content_sha256, source_sha256, actor, receipt) VALUES($1,$2,$3,$4::jsonb)",
            digest,
            source_sha,
            actor,
            json.dumps(receipt, ensure_ascii=False),
        )
        return receipt
