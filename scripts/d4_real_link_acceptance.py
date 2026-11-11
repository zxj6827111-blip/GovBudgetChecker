#!/usr/bin/env python
"""D4 真实链路验收（IR-08 重做版，2026-11-10）。

一次性隔离栈：docker 一次性 PostgreSQL + 真实 uvicorn API + 真实 Worker
（与 scripts/run_isolated_service_validation.py 同一启动配方，不使用生产
.env / 生产数据 / 生产数据库）。

与旧版（被 Codex IR-08 判为证明不足）的区别：

1. **逐条**核对产品实际交付的全部证据（不再 ``evidence_ids[:20]``）：
   每条 evidence 经 ``GET /api/jobs/{id}/evidence/{eid}`` 读回，要求
   页码为文档内正整数（``page=None`` 不算定位成功）、引文非空，并在
   **原件 PDF 的该页文本层**逐字命中（只折叠空白，与证据门同一归一）。
   findings 的 ``table_refs`` 每一侧都要在自己的页上命中；跨页/双侧
   关系要求每一侧的页码都出现在该 finding 的交付证据里。
2. confirm-report 的**失败与成功两条真实路径**都验：先验证系统未完成时
   409 拒绝且不落记录；口径确认触发重分析后，验证写侧成功签认、刷新
   读回、重复请求幂等、并发同 key 不产生重复记录、持久化失败返回 500
   且不留下半状态、旧版本页面确认被 409 REVISION_CHANGED 拒绝。
3. 不再把全部问题自动设为 confirmed、全部义务写 verified_ok。复核决定
   只对"本脚本已逐条核对原件（证据页码+原文命中）"的正式问题作出，并
   在回执中逐条记录核对依据；任何一条无法独立核对就保持未决、由完成
   门禁如实阻塞（回执同时报告）。脚本以"复核人"身份操作，仅用于验证
   写侧机制，不计为程序自动完成，也不作为人工工作量下降的证据。
4. 完成/重开/导出都要求**成功**路径：开始幂等、完成后重开 200 并读回、
   重开后可再次完成、导出四格式非空且重复下载一致、导出问题成员与
   当前分析一致、导出绑定当前版本。
5. 回执固定：git HEAD、关键源文件 sha256、原件 sha256、页数、分析
   revision、配置开关；任何一步失败即非零退出。

用法:
    .venv/bin/python scripts/d4_real_link_acceptance.py \
        --output <目录> [--pdf <PDF>] [--timeout-seconds 1800]
"""
from __future__ import annotations

import argparse
import asyncio
import concurrent.futures
import hashlib
import json
import re
import secrets
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.run_isolated_service_validation import (  # noqa: E402
    docker,
    get_report_with_backoff,
    port,
    prepare_db,
    request_with_backoff,
    service_lifetime,
)

DEFAULT_PDF = (
    ROOT
    / "uploads/6000a11c0737d5149975516b44fd8682"
    / "上海市普陀区人民政府宜川路街道办事处2025年度部门决算.pdf"
)


class Steps:
    def __init__(self) -> None:
        self.records: list[dict] = []

    def step(self, name: str, ok: bool, evidence: dict) -> None:
        self.records.append({"step": name, "ok": bool(ok), "evidence": evidence})
        print(f"[{'PASS' if ok else 'FAIL'}] {name}", flush=True)
        if not ok:
            raise AssertionError(
                f"D4 验收步骤失败: {name}: {json.dumps(evidence, ensure_ascii=False)[:1200]}")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _normalise(text: str) -> str:
    """与证据门同一归一：只折叠空白，不改数字/单位/百分号。"""
    return "".join(str(text or "").split())


def _page_text(page_texts: list[str], page: int) -> str:
    if type(page) is int and 1 <= page <= len(page_texts):
        return _normalise(page_texts[page - 1])
    return ""


def _quote_on_page(quote: str, page: int, page_texts: list[str]) -> bool:
    needle = _normalise(quote)
    return bool(needle) and needle in _page_text(page_texts, page)


def _positive_int(value) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _json_result(payload: dict) -> dict:
    result = payload.get("result")
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except ValueError:
            result = {}
    return result if isinstance(result, dict) else {}


def _iter_findings(result: dict):
    from src.services.evidence_guard import iter_findings

    return list(iter_findings(result))


def _dedupe_findings(findings: list[dict]) -> list[dict]:
    """同一 finding 可能同时出现在 rule_findings 与 legacy 分桶：按 id 去重。"""
    seen: set = set()
    deduped: list[dict] = []
    for finding in findings:
        key = str(finding.get("id") or "")
        if not key:
            key = "|".join(str(finding.get(k) or "") for k in
                           ("rule_id", "rule", "page", "message"))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(finding)
    return deduped


def _finding_evidence_status(finding: dict) -> str:
    verification = finding.get("evidence_verification")
    if isinstance(verification, dict):
        return str(verification.get("status") or "unverified")
    return "unverified"


def _evidence_items(finding: dict) -> list[dict]:
    return [item for item in (finding.get("evidence") or []) if isinstance(item, dict)]


def _is_document_level(finding: dict) -> bool:
    from src.services.evidence_guard import is_document_level_finding

    return is_document_level_finding(finding)


def _decimal_or_none(value):
    from decimal import Decimal, InvalidOperation

    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _cell_numeric(cell) -> "object":
    from decimal import Decimal, InvalidOperation

    if getattr(cell, "numeric_value", None) is not None:
        return Decimal(str(cell.numeric_value))
    text = "".join(str(getattr(cell, "raw_text", "") or "").split()).replace(",", "")
    if not text:
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def verify_relation_operands(
    findings: list[dict],
    cells: dict,
    page_texts: list[str],
    page_count: int,
) -> dict:
    """R2-01：逐侧核对金额关系操作数 → 当前原件原格/字符/值/单位。

    每个带 ``location.relation_operands`` 的关系必须给出 basic/three_public
    两侧；金额侧要有 source_cell_id 且解析到原格、值一致、进入交付证据；
    空白零侧要有原格空白 + 授权约定（U-5 或说明零依据），并进入
    native_blank_calculation_refs；零声明侧要能回原件页命中。表名/标题
    不能替代任一侧操作数。
    """
    total = ok_count = 0
    failures: list[dict] = []
    for finding in findings:
        location = finding.get("location") or {}
        operands = location.get("relation_operands")
        if not operands:
            if location.get("san_gong_item"):
                failures.append({"finding": finding.get("id"),
                                 "reason": "cross_relation_without_operands"})
            continue
        total += 1
        finding_ok = True
        failure_start = len(failures)
        sides = {}
        for operand in operands:
            if isinstance(operand, dict) and operand.get("side"):
                sides[operand["side"]] = operand
        if set(sides) != {"basic", "three_public"}:
            failures.append({"finding": finding.get("id"),
                             "reason": "operand_sides_incomplete",
                             "sides": sorted(sides)})
            finding_ok = False
        evidence_cells = {item.get("source_cell_id")
                          for item in finding.get("evidence") or []
                          if isinstance(item, dict) and item.get("source_cell_id")}
        blank_cells = {item.get("source_cell_id")
                       for item in location.get("native_blank_calculation_refs") or []
                       if isinstance(item, dict) and item.get("source_cell_id")}
        for side in ("basic", "three_public"):
            operand = sides.get(side) or {}
            kind = str(operand.get("kind") or "")
            cell_id = str(operand.get("source_cell_id") or "")
            page = operand.get("page")
            if kind == "amount":
                cell = cells.get(cell_id)
                if cell is None:
                    failures.append({"finding": finding.get("id"), "side": side,
                                     "reason": "amount_cell_missing", "cell": cell_id})
                    finding_ok = False
                    continue
                if not isinstance(page, int) or page != getattr(cell, "page_number", None):
                    failures.append({"finding": finding.get("id"), "side": side,
                                     "reason": "amount_cell_page_mismatch",
                                     "operand_page": page,
                                     "cell_page": getattr(cell, "page_number", None)})
                    finding_ok = False
                operand_value = _decimal_or_none(operand.get("value"))
                cell_value = _cell_numeric(cell)
                if operand_value is None or cell_value is None or operand_value != cell_value:
                    failures.append({"finding": finding.get("id"), "side": side,
                                     "reason": "amount_cell_value_mismatch",
                                     "operand_value": str(operand.get("value")),
                                     "cell_text": getattr(cell, "raw_text", None),
                                     "cell_value": str(cell_value)})
                    finding_ok = False
                if str(operand.get("unit") or "") != "万元":
                    failures.append({"finding": finding.get("id"), "side": side,
                                     "reason": "amount_unit_not_wan",
                                     "unit": operand.get("unit")})
                    finding_ok = False
                if cell_id not in evidence_cells:
                    failures.append({"finding": finding.get("id"), "side": side,
                                     "reason": "amount_cell_not_in_delivered_evidence",
                                     "cell": cell_id})
                    finding_ok = False
            elif kind == "blank_zero":
                cell = cells.get(cell_id)
                if cell is None or str(getattr(cell, "raw_text", "") or "").strip():
                    failures.append({"finding": finding.get("id"), "side": side,
                                     "reason": "blank_zero_cell_not_blank",
                                     "cell": cell_id})
                    finding_ok = False
                    continue
                if not isinstance(page, int) or page != getattr(cell, "page_number", None):
                    failures.append({"finding": finding.get("id"), "side": side,
                                     "reason": "blank_zero_page_mismatch",
                                     "operand_page": page,
                                     "cell_page": getattr(cell, "page_number", None)})
                    finding_ok = False
                convention = operand.get("convention") or {}
                authorized = bool(
                    isinstance(convention, dict)
                    and convention.get("id")
                    and (convention.get("approved") is True
                         or str(convention.get("source_kind") or "") == "user_confirmed")
                ) or bool(operand.get("narrative_sources"))
                if not authorized:
                    failures.append({"finding": finding.get("id"), "side": side,
                                     "reason": "blank_zero_without_authorized_basis",
                                     "convention": convention})
                    finding_ok = False
                if cell_id not in blank_cells:
                    failures.append({"finding": finding.get("id"), "side": side,
                                     "reason": "blank_zero_not_in_blank_evidence",
                                     "cell": cell_id})
                    finding_ok = False
            elif kind == "zero_statement":
                refs = [item for item in operand.get("refs") or [] if isinstance(item, dict)]
                if not refs:
                    failures.append({"finding": finding.get("id"), "side": side,
                                     "reason": "zero_statement_without_refs"})
                    finding_ok = False
                for ref in refs:
                    ref_page = ref.get("page")
                    text = str(ref.get("text") or ref.get("quote") or "")
                    if (not isinstance(ref_page, int) or not 1 <= ref_page <= page_count
                            or not _quote_on_page(text, ref_page, page_texts)):
                        failures.append({"finding": finding.get("id"), "side": side,
                                         "reason": "zero_statement_ref_not_grounded",
                                         "page": ref_page, "text": text[:60]})
                        finding_ok = False
            else:
                failures.append({"finding": finding.get("id"), "side": side,
                                 "reason": "operand_kind_missing",
                                 "kind": kind})
                finding_ok = False
        if finding_ok and len(failures) == failure_start:
            ok_count += 1
    return {
        "ok": not failures,
        "relations_total": total,
        "relations_ok": ok_count,
        "failures": failures[:40],
        "failure_count": len(failures),
    }


def operand_adversarial_control(
    findings: list[dict],
    cells: dict,
    page_texts: list[str],
    page_count: int,
) -> dict:
    """R2-01 负控：把跨表关系的金额证据换成正确页上的标题，验收必须失败。"""
    import copy

    cross = next((item for item in findings
                  if (item.get("location") or {}).get("relation_operands")), None)
    if cross is None:
        return {"ok": False, "reason": "no_cross_relation_to_mutate"}
    mutated = copy.deepcopy(cross)
    location = mutated.get("location") or {}
    location.pop("relation_operands", None)
    location.pop("operand_refs", None)
    location.pop("native_blank_calculation_refs", None)
    location["table_refs"] = [
        {"page": 1, "span": "政府性基金表", "role": "表格"},
        {"page": min(2, page_count), "span": "情况说明", "role": "说明"},
    ]
    mutated["evidence"] = [
        {"evidence_id": "title-control-1", "page": 1, "quote": "政府性基金表"},
        {"evidence_id": "title-control-2", "page": min(2, page_count),
         "quote": "情况说明"},
    ]
    control = verify_relation_operands([mutated], cells, page_texts, page_count)
    return {
        "ok": (not control["ok"]) and bool(control["failure_count"]),
        "expected": "title_only_operands_must_fail",
        "control_failures": control["failures"][:5],
    }


def _export_plain_text(suffix: str, content: bytes) -> str:
    import html
    import io
    import re
    import zipfile

    if suffix == "docx":
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            xml = archive.read("word/document.xml").decode("utf-8", "replace")
        return html.unescape(re.sub(r"<[^>]+>", " ", xml))
    if suffix == "pdf":
        import pymupdf as fitz

        document = fitz.open(stream=content, filetype="pdf")
        try:
            return "\n".join(page.get_text() for page in document)
        finally:
            document.close()
    return content.decode("utf-8-sig", errors="replace")


_ISSUE_MARKER_RE = re.compile(r"ISSUE_ID\|([^|]*)\|")
_EXPORT_BLOCK_BOUNDARY_RE = re.compile(
    r"ISSUE_ID\|[^|]*\||TIP_ID\|[^|]*\||DECISION_ID\|[^|]*\||MANUAL_ID\|[^|]*\||CHECK_ID\|[^|]*\|"
    r"|待人工复核（|检查回执（|检查覆盖台账|未完成检查事项")


def _export_expected_members(issues: list[dict]) -> list[dict]:
    """导出应出现的每个问题成员：身份、页码、逐侧证据（不按长度过滤）。"""
    members: list[dict] = []
    for issue in issues:
        location = issue.get("location") if isinstance(issue.get("location"), dict) else {}
        page = location.get("page")
        if page in (None, ""):
            page = issue.get("page_number")
        excerpts: list[dict] = []
        for item in issue.get("evidence") or []:
            if not isinstance(item, dict):
                continue
            quote = str(item.get("quote") or item.get("text")
                        or item.get("text_snippet") or "").strip()
            if not quote:
                continue
            item_page = item.get("page")
            if item_page in (None, ""):
                item_page = item.get("physical_page")
            excerpts.append({
                "page": item_page if item_page not in (None, "") else "",
                "quote": quote,
            })
        members.append({
            "id": str(issue.get("id") or "").strip(),
            "page": page if page not in (None, "") else "",
            "evidence": excerpts,
            "label": _export_evidence_label(issue),
        })
    return members


def _export_evidence_label(issue: dict) -> str:
    """与导出实现一致的证据标签（verified 才是“原文摘录”）。"""
    verification = issue.get("evidence_verification")
    if (
        issue.get("evidence_status") in {"complete", "verified"}
        and isinstance(verification, dict)
        and verification.get("status") == "verified"
        and verification.get("source_identity_complete") is True
    ):
        return "原文摘录"
    return "候选证据/计算说明（原文绑定未完成）"


def _export_text_blocks(text: str) -> list[tuple[str, str]]:
    """按 ISSUE_ID 标记切出问题块，并在台账/决定/回执边界处截断。

    附录或台账中对同一 ID 的引用没有 ISSUE_ID 块，不能补偿正文漏成员；
    若出现重复块，按成员重复计数拒绝。
    """
    blocks: list[tuple[str, str]] = []
    matches = list(_ISSUE_MARKER_RE.finditer(text))
    for index, match in enumerate(matches):
        start = match.start()
        next_start = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        boundary = _EXPORT_BLOCK_BOUNDARY_RE.search(text, match.end(), next_start)
        end = boundary.start() if boundary else next_start
        blocks.append((match.group(1).strip(), text[start:end]))
    return blocks


def _export_norm(value: object) -> str:
    return "".join(str(value or "").split())


def _export_norm_loose(value: object) -> str:
    """去空白与冒号：DOCX 表格单元格与 PDF 文本行的分隔差异不影响内容核对。"""
    return _export_norm(value).replace("：", "").replace(":", "")


def _verify_text_issue_block(
    block: str,
    member: dict,
    revision: str,
) -> dict:
    loose = _export_norm_loose(block)
    failures: list[str] = []
    page = member.get("page")
    if page not in (None, ""):
        if f"证据页码{page}" not in loose and f"页码{page}" not in loose:
            failures.append("page_not_bound")
    if revision and _export_norm(revision) not in _export_norm(block):
        failures.append("revision_not_bound")
    missing_quotes: list[str] = []
    for excerpt in member.get("evidence") or []:
        quote = _export_norm_loose(excerpt.get("quote"))
        if not quote:
            continue
        page_label = (f"（第{excerpt['page']}页）"
                      if excerpt.get("page") not in (None, "") else "")
        # 证据必须出现在本成员的证据行内，而不是只出现在问题说明/台账引用中。
        bound = f"{_export_norm_loose(member.get('label'))}{page_label}{quote}"
        if bound not in loose:
            missing_quotes.append(str(excerpt.get("quote"))[:40])
    if missing_quotes:
        failures.append("evidence_not_bound")
    return {
        "ok": not failures,
        "failures": failures,
        "missing_evidence": missing_quotes[:10],
    }


def _verify_csv_members(content: bytes, members: list[dict], revision: str) -> dict:
    import csv
    import io
    import json as _json

    reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig", errors="replace")))
    rows = [row for row in reader if (row.get("record_type") or "") == "issue"]
    by_id: dict[str, list[dict]] = {}
    for row in rows:
        by_id.setdefault(str(row.get("issue_id") or ""), []).append(row)
    expected_ids = [member["id"] for member in members]
    missing_members = [issue_id for issue_id in expected_ids
                       if issue_id not in by_id]
    duplicate_members = [issue_id for issue_id, items in by_id.items() if len(items) > 1]
    extra_members = [issue_id for issue_id in by_id if issue_id not in set(expected_ids)]
    failures: list[dict] = []
    if len(rows) != len(members):
        failures.append({"kind": "member_count_mismatch",
                         "expected": len(members), "actual": len(rows)})
    for member in members:
        items = by_id.get(member["id"]) or []
        if not items:
            continue
        row = items[0]
        member_failures: list[str] = []
        page = member.get("page")
        if page not in (None, "") and str(row.get("页码") or "").strip() != str(page):
            member_failures.append("page_not_bound")
        if revision and str(row.get("analysis_revision") or "").strip() != str(revision):
            member_failures.append("revision_not_bound")
        try:
            delivered = _json.loads(row.get("evidence_quotes") or "[]")
        except Exception:  # noqa: BLE001
            delivered = []
        delivered_keys = {
            (_export_norm(item.get("page")), _export_norm(item.get("quote")))
            for item in delivered if isinstance(item, dict)
        }
        missing_quotes = [
            excerpt["quote"] for excerpt in member.get("evidence") or []
            if (_export_norm(excerpt.get("page")), _export_norm(excerpt.get("quote")))
            not in delivered_keys
        ]
        if missing_quotes:
            member_failures.append("evidence_not_bound")
        if member_failures:
            failures.append({"issue_id": member["id"], "failures": member_failures,
                             "missing_evidence": [quote[:40] for quote in missing_quotes[:10]]})
    return {
        "ok": not (missing_members or duplicate_members or extra_members or failures),
        "member_count": len(rows),
        "missing_members": missing_members[:10],
        "duplicate_members": duplicate_members[:10],
        "extra_members": extra_members[:10],
        "failed_members": failures[:20],
        "revision_present": bool(revision) and all(
            str(row.get("analysis_revision") or "").strip() == str(revision) for row in rows),
    }


def verify_export_formats(
    contents: dict,
    issues: list[dict],
    revision: str,
) -> dict:
    """R3-02：CSV/DOCX/PDF 逐成员、逐侧必要证据、逐成员版本核对。

    与 R2-07 的差别：
    1. 按问题成员身份（ISSUE_ID/CSV issue 行）计数并比对集合，不以
       rule_id 在全篇出现替代；同一 rule_id 的多个关系不能互相顶替。
    2. 每条证据（含 13.24/19.56 等短金额）都必须绑定到本成员的证据行，
       不能用问题说明或附录/台账中的同值补偿。
    3. 页码与 analysis_revision 绑定到成员本身（CSV 行字段、DOCX/PDF 块内）。
    """
    members = _export_expected_members(issues)
    expected_ids = [member["id"] for member in members]
    details: dict = {}
    ok = True
    for suffix in ("csv", "docx", "pdf"):
        content = contents.get(suffix)
        if not content:
            details[suffix] = {"ok": False, "reason": "empty"}
            ok = False
            continue
        if suffix == "csv":
            result = _verify_csv_members(content, members, revision)
        else:
            text = _export_plain_text(suffix, content)
            blocks = _export_text_blocks(text)
            block_ids = [issue_id for issue_id, _block in blocks]
            missing_members = [issue_id for issue_id in expected_ids
                               if issue_id not in block_ids]
            duplicate_members = sorted({
                issue_id for issue_id in block_ids if block_ids.count(issue_id) > 1})
            extra_members = [issue_id for issue_id in block_ids
                             if issue_id not in set(expected_ids)]
            failed_members = []
            for issue_id, block in blocks:
                if issue_id not in set(expected_ids):
                    continue
                member = next(item for item in members if item["id"] == issue_id)
                check = _verify_text_issue_block(block, member, revision)
                if not check["ok"]:
                    failed_members.append({
                        "issue_id": issue_id,
                        "failures": check["failures"],
                        "missing_evidence": check["missing_evidence"],
                    })
            count_mismatch = len(blocks) != len(members)
            result = {
                "ok": not (count_mismatch or missing_members or duplicate_members
                           or extra_members or failed_members),
                "member_count": len(blocks),
                "missing_members": missing_members[:10],
                "duplicate_members": duplicate_members[:10],
                "extra_members": extra_members[:10],
                "failed_members": failed_members[:20],
                "revision_present": bool(revision) and all(
                    _export_norm(revision) in _export_norm(block) for _id, block in blocks),
            }
        details[suffix] = result
        ok = ok and result["ok"]
    return {"ok": ok, "issues": len(members), "formats": details}


_SPAN_UNIT_AMOUNT_RE = re.compile(
    r"(?<![\d.])(\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?=\s*(?:万元|亿元|元))")
_SPAN_DECIMAL_RE = re.compile(r"(?<![\d.])\d+(?:,\d{3})*\.\d+")


def _span_amounts(span: str) -> list[str]:
    """引文里的"金额身份"：带单位的数字或带小数的数字。

    页码（第22页）、年度（2025年度）、科目编码（30212）都不带金额单位也
    不带小数，不参与该检查；金额必须能在交付证据里找到同一写法。
    """
    text = str(span or "").replace("，", ",")
    amounts = [match.group(0) for match in _SPAN_UNIT_AMOUNT_RE.finditer(text)]
    for match in _SPAN_DECIMAL_RE.finditer(text):
        if match.group(0) not in amounts:
            amounts.append(match.group(0))
    return amounts


def verify_all_evidence(
    client: httpx.Client,
    job_id: str,
    result: dict,
    page_texts: list[str],
    original_hash: str,
    page_count: int,
    cells: dict | None = None,
) -> dict:
    """逐条核对全部交付证据 + 每一侧 table_refs + page=None 判定 + 操作数原格。

    返回可 JSON 化的核对结果；``ok`` 为 False 时携带前若干失败明细。
    """
    findings = _dedupe_findings(_iter_findings(result))
    evidence_by_id: dict[str, dict] = {}
    failures: list[dict] = []
    verified_findings = 0
    evidence_total = 0
    evidence_grounded = 0
    refs_total = 0
    refs_grounded = 0
    multi_side_total = 0
    multi_side_grounded = 0
    documents_without_page: list[str] = []

    for finding in findings:
        finding_id = str(finding.get("id") or finding.get("rule_id") or "")
        items = _evidence_items(finding)
        finding_ok = True
        evidence_pages: set[int] = set()
        evidence_texts_by_page: dict[int, list[str]] = {}
        if not items and not _is_document_level(finding):
            failures.append({"finding": finding_id, "reason": "no_evidence_items"})
            finding_ok = False
        for item in items:
            evidence_total += 1
            eid = str(item.get("evidence_id") or "").strip()
            item_page = _positive_int(item.get("page")) or _positive_int(
                item.get("physical_page"))
            item_text = str(item.get("quote") or item.get("text") or "").strip()
            if not eid:
                failures.append({"finding": finding_id, "reason": "evidence_without_id",
                                 "page": item_page})
                finding_ok = False
                continue
            answer = client.get(f"/api/jobs/{job_id}/evidence/{eid}")
            if answer.status_code != 200:
                failures.append({"finding": finding_id, "evidence_id": eid,
                                 "reason": f"evidence_http_{answer.status_code}"})
                finding_ok = False
                continue
            body = answer.json()
            evidence_by_id[eid] = body
            page = _positive_int(body.get("page")) or _positive_int(
                body.get("physical_page"))
            if page is None:
                # IR-08：page=None 不算定位成功（旧验收器把 None 计通过）
                failures.append({"finding": finding_id, "evidence_id": eid,
                                 "reason": "page_missing"})
                finding_ok = False
                continue
            if page > page_count:
                failures.append({"finding": finding_id, "evidence_id": eid,
                                 "reason": "page_out_of_range",
                                 "page": page, "page_count": page_count})
                finding_ok = False
                continue
            if item_page is not None and item_page != page:
                failures.append({"finding": finding_id, "evidence_id": eid,
                                 "reason": "payload_page_mismatch",
                                 "payload_page": item_page, "endpoint_page": page})
                finding_ok = False
            quote = str(body.get("quote") or body.get("text")
                        or body.get("text_snippet") or item_text or "").strip()
            if not quote:
                failures.append({"finding": finding_id, "evidence_id": eid,
                                 "reason": "quote_missing", "page": page})
                finding_ok = False
            elif not _quote_on_page(quote, page, page_texts):
                failures.append({"finding": finding_id, "evidence_id": eid,
                                 "reason": "quote_not_on_original_page",
                                 "page": page, "quote": quote[:80]})
                finding_ok = False
            else:
                evidence_grounded += 1
            body_hash = str(body.get("file_hash") or "").strip()
            if body_hash and body_hash != original_hash:
                failures.append({"finding": finding_id, "evidence_id": eid,
                                 "reason": "file_hash_mismatch",
                                 "file_hash": body_hash[:16]})
                finding_ok = False
            evidence_pages.add(page)
            if quote:
                evidence_texts_by_page.setdefault(page, []).append(quote)

        # 每一侧 table_refs：span 必须在自己声明的页上逐字命中；
        # 跨页关系要求每一侧的页都出现在该 finding 的交付证据里。
        location = finding.get("location") or {}
        refs = [ref for ref in (location.get("table_refs") or [])
                if isinstance(ref, dict) and str(ref.get("span") or "").strip()]
        ref_pages: set[int] = set()
        for ref in refs:
            refs_total += 1
            ref_page = _positive_int(ref.get("page"))
            if ref_page is None:
                failures.append({"finding": finding_id, "reason": "table_ref_page_missing",
                                 "span": str(ref.get("span"))[:40]})
                finding_ok = False
                continue
            ref_pages.add(ref_page)
            span = str(ref.get("span"))
            if _quote_on_page(span, ref_page, page_texts):
                refs_grounded += 1
            else:
                failures.append({"finding": finding_id, "reason": "table_ref_span_not_on_page",
                                 "page": ref_page, "span": span[:80]})
                finding_ok = False
            # R2-01：table_ref 声明了金额（带小数的数字）时，该页交付证据
            # 必须包含同一金额。仅把正确页上的表名/标题当证据不成立。
            span_amounts = _span_amounts(span)
            if span_amounts:
                delivered = _normalise(" ".join(
                    evidence_texts_by_page.get(ref_page, []))).replace(",", "")
                missing_amounts = [amount for amount in span_amounts
                                   if _normalise(amount).replace(",", "") not in delivered]
                if missing_amounts:
                    failures.append({
                        "finding": finding_id,
                        "reason": "table_ref_amount_not_in_delivered_evidence",
                        "page": ref_page, "amounts": missing_amounts,
                        "span": span[:80],
                        "delivered_texts": [text[:40] for text in
                                            evidence_texts_by_page.get(ref_page, [])][:4],
                    })
                    finding_ok = False
        if len(ref_pages) >= 2:
            multi_side_total += 1
            missing_side = ref_pages - evidence_pages
            if missing_side:
                failures.append({"finding": finding_id,
                                 "reason": "multi_side_evidence_missing",
                                 "ref_pages": sorted(ref_pages),
                                 "evidence_pages": sorted(evidence_pages),
                                 "missing": sorted(missing_side)})
                finding_ok = False
            else:
                multi_side_grounded += 1

        # 交付页：正式问题必须有可定位页；page=None 不算定位成功。
        severity = str(finding.get("severity") or "").strip().lower()
        page = _positive_int((location.get("page")) or finding.get("page"))
        if page is None or page > page_count:
            if severity in {"error", "warn", "high", "medium", "critical", "low"}:
                documents_without_page.append(finding_id)
                finding_ok = False
                failures.append({"finding": finding_id, "reason": "finding_page_missing",
                                 "severity": severity})
        if _finding_evidence_status(finding) != "verified":
            failures.append({"finding": finding_id,
                             "reason": f"evidence_status_{_finding_evidence_status(finding)}"})
            finding_ok = False
        if finding_ok:
            verified_findings += 1

    operand_check = verify_relation_operands(
        findings, cells or {}, page_texts, page_count)
    return {
        "ok": (not failures) and operand_check["ok"],
        "findings_total": len(findings),
        "findings_grounded": verified_findings,
        "evidence_total": evidence_total,
        "evidence_grounded": evidence_grounded,
        "table_refs_total": refs_total,
        "table_refs_grounded": refs_grounded,
        "multi_side_total": multi_side_total,
        "multi_side_grounded": multi_side_grounded,
        "failures": failures[:40],
        "failure_count": len(failures),
        "documents_without_page": documents_without_page,
        "operand_relations_total": operand_check["relations_total"],
        "operand_relations_ok": operand_check["relations_ok"],
        "operand_failures": operand_check["failures"],
        "operand_failure_count": operand_check["failure_count"],
    }


def gate_truth_check(gate_body: dict, result: dict) -> dict:
    """用产品实际结果结构独立复算三分类，核对门禁没有漏读/错分类。"""
    findings = _dedupe_findings(_iter_findings(result))
    expected_confirmed: list[str] = []
    seen: set[str] = set()
    manual_review_ids: set[str] = set()
    failed_evidence_ids: set[str] = set()
    for finding in findings:
        issue_id = str(finding.get("id") or "").strip()
        severity = str(finding.get("severity") or "").strip().lower()
        status = _finding_evidence_status(finding)
        if severity == "manual_review":
            manual_review_ids.add(issue_id)
        if status == "failed":
            failed_evidence_ids.add(issue_id)
        if not issue_id or issue_id in seen:
            continue
        if status == "verified" and severity != "manual_review":
            seen.add(issue_id)
            expected_confirmed.append(issue_id)
    categories = gate_body.get("categories") or {}
    actual_confirmed = [
        str(item.get("id")) for item in (categories.get("confirmed_material_issues") or [])
        if item.get("id")
    ]
    obligations = ((result.get("meta") or {}).get("obligation_coverage") or {}).get(
        "instances") or []
    expected_blocking_obligations = [
        str(item.get("obligation_id")) for item in obligations
        if isinstance(item, dict)
        and str(item.get("status") or "").strip() not in {"completed", "not_applicable"}
        and item.get("blocks_gate") is not False
    ]
    system_ids = {
        str(item.get("rule_id") or "") for item in (categories.get("system_incomplete") or [])
    }
    problems: list[str] = []
    if set(actual_confirmed) != set(expected_confirmed):
        problems.append(
            f"confirmed_set_mismatch expected={len(expected_confirmed)} actual={len(actual_confirmed)}")
    leaked_manual = manual_review_ids & set(actual_confirmed)
    if leaked_manual:
        problems.append(f"manual_review_in_confirmed={sorted(leaked_manual)}")
    leaked_failed = failed_evidence_ids & set(actual_confirmed)
    if leaked_failed:
        problems.append(f"failed_evidence_in_confirmed={sorted(leaked_failed)}")
    unrepresented = [
        oid for oid in expected_blocking_obligations
        if oid and oid not in system_ids
    ]
    if unrepresented:
        problems.append(f"blocking_obligations_missing_from_system_incomplete={unrepresented}")
    return {
        "ok": not problems,
        "problems": problems,
        "expected_confirmed": len(expected_confirmed),
        "actual_confirmed": len(actual_confirmed),
        "manual_review_ids": len(manual_review_ids),
        "failed_evidence_ids": len(failed_evidence_ids),
        "expected_blocking_obligations": expected_blocking_obligations,
        "gate": gate_body.get("gate"),
        "counts": gate_body.get("counts"),
    }


def review_problem_set(job_id: str, result: dict, structured_ingest=None) -> list:
    """与复核完成门禁同源的正式问题集合（唯一口径在服务端模块）。"""
    from src.services import issue_workflow_store
    from src.services.review_problem_set import collect_review_problems

    try:
        snapshot = issue_workflow_store.get_job_problem_snapshot(job_id)
    except Exception:  # noqa: BLE001 - 读不到按空快照，与门禁 fail-closed 一致
        snapshot = {"ignored_issue_ids": []}
    return collect_review_problems(
        job_uuid=job_id,
        ai_findings=result.get("ai_findings"),
        rule_findings=result.get("rule_findings"),
        merged_result=result.get("merged"),
        structured_ingest=structured_ingest,
        result=result,
        ignored_issue_ids=snapshot.get("ignored_issue_ids") or [],
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--pdf", type=Path, default=DEFAULT_PDF)
    parser.add_argument("--timeout-seconds", type=int, default=1800)
    parser.add_argument("--expect-session-complete-http", type=int, default=200,
                        help="完成后重开的完成请求期望状态（默认要求成功 200）")
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    pdf_path = args.pdf.resolve()
    if not pdf_path.exists():
        raise SystemExit(f"PDF 不存在: {pdf_path}")
    original_hash = _sha(pdf_path)

    # 原件文本层：证据核对必须回到原件，而不是产品返回的页码/引文。
    import pdfplumber

    from src.services.pdf_page_extract import extract_visible_text_from_page

    with pdfplumber.open(str(pdf_path)) as doc_pdf:
        page_texts = [extract_visible_text_from_page(page) for page in doc_pdf.pages]
    page_count = len(page_texts)
    # R2-01：金额操作数逐侧回"当前原件原格"核对，需要原始 ParseArtifact
    # 的 source_cell_id → 单元格目录；标题/引文命中不能替代原格身份。
    from src.services.pdf_parser import PDFParser
    from src.services.rule_source_binding import _current_cell_catalog

    artifact = PDFParser(None).build_parse_artifact(str(pdf_path))
    cell_catalog, _cell_conflicts = _current_cell_catalog(artifact)

    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                          capture_output=True, text=True).stdout.strip()
    frozen = {
        "git_head": head,
        "key_source_sha256": {
            str(rel): _sha(ROOT / rel) for rel in (
                "src/services/report_confirmation_gate.py",
                "src/services/evidence_guard.py",
                "src/services/rule_source_binding.py",
                "src/engine/pipeline.py",
                "src/engine/issue_evidence.py",
                "api/routes/jobs.py",
            )
        },
        "original_sha256": original_hash,
        "page_count": page_count,
    }

    steps = Steps()
    run_id = f"d4link-{secrets.token_hex(6)}"
    name = f"govbudget-{run_id}"
    processes, handles = [], []
    receipt: dict = {"run_id": run_id, "pdf": str(pdf_path), "steps": steps.records,
                     "frozen": frozen}
    started = time.monotonic()

    def _get_status(client, job_id):
        answer = client.get(f"/api/jobs/{job_id}/status")
        if answer.status_code == 429:
            time.sleep(max(1, float(answer.headers.get("Retry-After", "2"))))
            return None
        answer.raise_for_status()
        return answer.json()

    def _wait_analysis(client, job_id, previous_revision=None):
        for _ in range(args.timeout_seconds):
            payload = _get_status(client, job_id)
            if payload is None:
                continue
            if payload.get("status") not in {"queued", "processing", "running", "pending"}:
                revision = payload.get("analysis_revision")
                if previous_revision is None or revision != previous_revision:
                    return payload
            time.sleep(1)
        raise TimeoutError("分析未在时限内完成/版本未更新")

    try:
        import tempfile

        with (
            tempfile.TemporaryDirectory(prefix="govbudget-d4link-") as temporary,
            service_lifetime(processes, handles),
        ):
            work = Path(temporary)
            env_file = work / "postgres.env"
            password = secrets.token_hex(24)
            env_file.write_text(
                f"POSTGRES_PASSWORD={password}\nPOSTGRES_USER=validator\nPOSTGRES_DB=validation\n")
            env_file.chmod(0o600)
            docker("run", "-d", "--rm", "--name", name, "--env-file", str(env_file),
                   "-p", "127.0.0.1::5432", "postgres:16")
            mapping = docker("port", name, "5432/tcp")
            pg_port = int(mapping.rsplit(":", 1)[1])
            dsn = f"postgresql://validator:{password}@127.0.0.1:{pg_port}/validation"
            for _ in range(90):
                if docker_exec_ready(name):
                    break
                time.sleep(0.5)
            asyncio.run(prepare_db(dsn))

            import os

            env = os.environ.copy()
            for key in list(env):
                if key.startswith(("AI_", "OPENAI_", "GEMINI_", "ZHIPU_", "DEEPSEEK_", "CODEX_API_")):
                    env.pop(key)
            admin_password, api_key, session_secret = (secrets.token_hex(24) for _ in range(3))
            env.update(
                PYTHON_DOTENV_DISABLED="1",
                TESTING="false",
                DATABASE_URL=dsn,
                GOVBUDGET_AUTH_ENABLED="true",
                GOVBUDGET_API_KEY=api_key,
                USER_SESSION_SECRET=session_secret,
                DEFAULT_ADMIN_PASSWORD=admin_password,
                USER_DATA_DIR=str(work / "users"),
                ORG_DATA_DIR=str(work / "orgs"),
                UPLOAD_DIR=str(work / "uploads"),
                AUDIT_LOG_PATH=str(out / "audit.jsonl"),
                AI_ASSIST_ENABLED="false",
                AI_ASSIST_REQUIRED="false",
                AI_ENABLED="false",
                OCR_ENABLED="false",
                JOB_QUEUE_ENABLED="true",
                JOB_QUEUE_INLINE_FALLBACK="false",
                JOB_QUEUE_WORKERS="1",
                JOB_QUEUE_POLL_INTERVAL_SECONDS="0.2",
                READY_REQUIRE_DATABASE="true",
                READY_REQUIRE_AI_EXTRACTOR="false",
                # 整份确认写侧在本隔离实例开启（默认关闭路径由单测覆盖）
                GOVBUDGET_CONFIRM_REPORT_ENABLED="1",
            )
            api_port = port()
            api_log = (out / "api.log").open("w")
            worker_log = (out / "worker.log").open("w")
            handles.extend((api_log, worker_log))
            import subprocess as _sp

            api_process = _sp.Popen(
                [sys.executable, "-m", "uvicorn", "api.main:app", "--host", "127.0.0.1",
                 "--port", str(api_port)],
                cwd=ROOT, env={**env, "JOB_QUEUE_ROLE": "api"},
                stdout=api_log, stderr=_sp.STDOUT,
            )
            processes.append(api_process)
            worker = _sp.Popen(
                [sys.executable, "-m", "api.worker"],
                cwd=ROOT, env={**env, "JOB_QUEUE_ROLE": "worker"},
                stdout=worker_log, stderr=_sp.STDOUT,
            )
            processes.append(worker)

            # trust_env=False：绕过系统代理（本机 shell 带 http_proxy 时，
            # 对 127.0.0.1 的请求会被转发到代理端口导致永久阻塞）。
            with httpx.Client(base_url=f"http://127.0.0.1:{api_port}", timeout=300,
                              headers={"X-API-Key": api_key}, trust_env=False) as client:
                for _ in range(120):
                    if api_process.poll() is not None:
                        raise RuntimeError("API exited before startup; see api.log")
                    try:
                        if client.get("/health").status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    time.sleep(0.5)
                response = request_with_backoff(client, "POST", "/api/auth/login",
                                                json={"username": "admin", "password": admin_password})
                response.raise_for_status()
                client.headers["X-Session-Token"] = response.json()["token"]
                changed = secrets.token_hex(24)
                request_with_backoff(client, "POST", "/api/auth/change-password",
                                     json={"old_password": admin_password,
                                           "new_password": changed}).raise_for_status()
                response = request_with_backoff(client, "POST", "/api/auth/login",
                                                json={"username": "admin", "password": changed})
                response.raise_for_status()
                client.headers["X-Session-Token"] = response.json()["token"]
                client.get("/ready").raise_for_status()

                # ---- S0 组织目录就绪（真实产品流：上传前目录在位）----
                org_create = client.post("/api/organizations", json={
                    "name": "上海市普陀区人民政府宜川路街道办事处", "level": "department"})
                steps.step("S0_org_catalog_ready",
                           org_create.status_code in (200, 201, 422),
                           {"org_http": org_create.status_code})

                # ---- S1 真实上传 + 分析 ----
                with pdf_path.open("rb") as file:
                    response = request_with_backoff(
                        client, "POST", "/api/documents/upload",
                        files={"file": (pdf_path.name, file, "application/pdf")})
                response.raise_for_status()
                job_id = response.json()["job_id"]
                receipt["job_id"] = job_id
                request_with_backoff(
                    client, "POST", f"/api/analyze/{job_id}",
                    json={"mode": "legacy", "use_ai_assist": False,
                          "use_local_rules": True}).raise_for_status()
                payload = _wait_analysis(client, job_id)
                result = _json_result(payload)
                findings = _dedupe_findings(_iter_findings(result))
                revision_r1 = payload.get("analysis_revision")
                steps.step("S1_upload_analyze",
                           payload.get("status") in {"done", "review_required"}
                           and bool(revision_r1) and bool(findings),
                           {"status": payload.get("status"), "job_id": job_id,
                            "revision": revision_r1,
                            "findings": len(findings),
                            "result_keys": sorted(result.keys())[:24]})

                # ---- S2 持久化/读回（事实快照分页读回 + 哈希一致）----
                from src.services.analysis_fact_snapshot import (
                    canonical_facts,
                    validate_snapshot,
                )

                frozen_path = Path(env["UPLOAD_DIR"]) / job_id / "fact_snapshots" / f"{revision_r1}.json"
                snapshot = validate_snapshot(json.loads(frozen_path.read_text()))
                hash_ok = True
                for view in ("raw", "effective"):
                    retrieved = []
                    total = max(1, len(snapshot[f"{view}_context"].get("facts", [])))
                    for offset in range(0, total, 1000):
                        answer = client.get(f"/api/jobs/{job_id}/fact-snapshot",
                                            params={"view": view, "offset": offset, "limit": 1000})
                        answer.raise_for_status()
                        if answer.json()["snapshot_hash"] != snapshot["snapshot_hash"]:
                            hash_ok = False
                        retrieved.extend(answer.json()["facts"])
                    if canonical_facts(retrieved) != canonical_facts(
                            snapshot[f"{view}_context"].get("facts", [])):
                        hash_ok = False
                versions = client.get(f"/api/jobs/{job_id}/analysis-versions")
                versions.raise_for_status()
                steps.step("S2_persist_readback", hash_ok and versions.status_code == 200,
                           {"snapshot_hash": snapshot["snapshot_hash"],
                            "raw": len(snapshot["raw_context"].get("facts", [])),
                            "effective": len(snapshot["effective_context"].get("facts", [])),
                            "versions_http": versions.status_code})

                # ---- S3 全量证据核对（第 1 版；口径确认后还会再核对一次）----
                check_r1 = verify_all_evidence(client, job_id, result, page_texts,
                                               original_hash, page_count, cell_catalog)
                steps.step("S3_all_evidence_grounded", check_r1["ok"],
                           {k: v for k, v in check_r1.items() if k != "ok"})

                # ---- S4 门禁三分类与真实结果结构对账（此时口径未确认，
                #      阻塞性义务应在 system_incomplete 中且确认被 409 拒绝）----
                gate = client.get(f"/api/jobs/{job_id}/report-confirmation")
                gate.raise_for_status()
                gate_body = gate.json()
                truth = gate_truth_check(gate_body, result)
                gate_gate = gate_body.get("gate") or {}
                blocking_count = int(gate_gate.get("blocking_system_incomplete") or 0)
                # 若当前结果确有阻塞性未完成项 → 必须 409 且不落记录；
                # 若门禁已打开（本材料修复后无阻塞项）→ 不伪造阻塞，改为
                # 断言"尚未有任何确认记录"（不能自动替用户确认）。
                blocked_post = None
                post_detail = None
                if blocking_count:
                    blocked_post = client.post(
                        f"/api/jobs/{job_id}/confirm-report",
                        json={"expected_analysis_revision": revision_r1,
                              "idempotency_key": f"blocked-{secrets.token_hex(4)}"})
                    post_detail = blocked_post.json().get("detail")
                gate_after = client.get(f"/api/jobs/{job_id}/report-confirmation")
                gate_after.raise_for_status()
                records_after_block = gate_after.json().get("confirm_records") or []
                blocked_ok = (
                    blocked_post is not None
                    and blocked_post.status_code == 409
                    and post_detail == "SYSTEM_INCOMPLETE"
                ) if blocking_count else (blocked_post is None)
                steps.step("S4_gate_truth_and_state",
                           truth["ok"] and blocked_ok and not records_after_block,
                           {"truth": truth,
                            "blocking_system_incomplete": blocking_count,
                            "blocked_branch_exercised": bool(blocking_count),
                            "post_http": blocked_post.status_code if blocked_post else None,
                            "post_detail": post_detail,
                            "records_after": len(records_after_block)})

                # ---- S5a 材料口径确认（真实产品流；确认会派发完整重分析）----
                rematch = client.post("/api/jobs/rematch-organizations", json={})
                dims_get = client.get(f"/api/jobs/{job_id}/material-dimensions")
                dims_get.raise_for_status()
                dims_body = dims_get.json()
                document_version_id = int(dims_body.get("document_version_id") or 0) or None
                file_hash = str(dims_body.get("file_hash") or "")
                basis_from_dims = str(dims_body.get("analysis_basis_token") or "")
                expected_revision = 0
                for dim in (dims_body.get("dimensions") or []):
                    if isinstance(dim, dict) and dim.get("expected_revision") is not None:
                        expected_revision = int(dim.get("expected_revision"))
                        break
                import uuid as _uuid

                def _dims_payload(revision):
                    return {
                        "request_id": str(_uuid.uuid4()),
                        "document_version_id": document_version_id,
                        "file_hash": file_hash,
                        "analysis_basis_token": basis_from_dims,
                        "writing": {"value": "department", "expected_revision": revision,
                                    "note": "D4 验收：封面为部门决算，写法=部门",
                                    "evidence_reference": "材料口径读取接口",
                                    "acknowledge_conflicts": True, "cover_independent": True},
                        "amount": {"value": "department_summary", "expected_revision": revision,
                                   "note": "D4 验收：部门决算汇总本级及所属单位",
                                   "evidence_reference": "材料口径读取接口",
                                   "acknowledge_conflicts": True, "cover_independent": True},
                    }

                dims_ok = bool(document_version_id and len(file_hash) == 64 and basis_from_dims)
                dims_post = None
                if dims_ok:
                    dims_post = client.post(f"/api/jobs/{job_id}/material-dimensions",
                                            json=_dims_payload(expected_revision))
                    attempts = 1
                    while dims_post.status_code == 409 and attempts < 3:
                        refreshed = client.get(f"/api/jobs/{job_id}/material-dimensions")
                        refreshed.raise_for_status()
                        for dim in (refreshed.json().get("dimensions") or []):
                            if isinstance(dim, dict) and dim.get("expected_revision") is not None:
                                expected_revision = int(dim.get("expected_revision"))
                        dims_post = client.post(f"/api/jobs/{job_id}/material-dimensions",
                                                json=_dims_payload(expected_revision))
                        attempts += 1
                payload_r2 = _wait_analysis(client, job_id, previous_revision=revision_r1)
                result_r2 = _json_result(payload_r2)
                revision_r2 = payload_r2.get("analysis_revision")
                steps.step("S5a_identity_and_scope",
                           rematch.status_code == 200 and dims_ok
                           and dims_post is not None
                           and dims_post.status_code in (200, 202)
                           and bool(revision_r2) and revision_r2 != revision_r1,
                           {"rematch_http": rematch.status_code,
                            "dims_get": dims_get.status_code,
                            "dims_post": dims_post.status_code if dims_post else None,
                            "revision_r1": revision_r1, "revision_r2": revision_r2,
                            "dims_detail": str(dims_post.json())[:200]
                            if dims_post is not None and dims_post.status_code not in (200, 202)
                            else None})

                # ---- S3b 新版本全量证据复核 ----
                check_r2 = verify_all_evidence(client, job_id, result_r2, page_texts,
                                               original_hash, page_count, cell_catalog)
                steps.step("S3b_all_evidence_grounded_after_scope",
                           check_r2["ok"],
                           {k: v for k, v in check_r2.items() if k != "ok"})

                # R2-01 负控：金额证据换成正确页上的标题必须失败。
                operand_control = operand_adversarial_control(
                    _dedupe_findings(_iter_findings(result_r2)), cell_catalog,
                    page_texts, page_count)
                steps.step("S3c_operand_identity_adversarial_control",
                           operand_control["ok"], operand_control)

                # ---- S4b 持久化失败（事务失败）：目录只读时写侧必须 500，
                #      且读取接口看不到任何半状态记录 ----
                job_dir = Path(env["UPLOAD_DIR"]) / job_id
                gate_r2 = client.get(f"/api/jobs/{job_id}/report-confirmation")
                gate_r2.raise_for_status()
                gate_body_r2 = gate_r2.json()
                truth_r2 = gate_truth_check(gate_body_r2, result_r2)
                can_confirm = bool((gate_body_r2.get("gate") or {}).get(
                    "can_confirm_whole_report"))
                fail_key = f"txfail-{secrets.token_hex(4)}"
                records_before = list(gate_body_r2.get("confirm_records") or [])
                import stat as _stat
                original_mode = _stat.S_IMODE(job_dir.stat().st_mode)
                persist_failed = None
                try:
                    job_dir.chmod(0o500)
                    persist = client.post(
                        f"/api/jobs/{job_id}/confirm-report",
                        json={"expected_analysis_revision": revision_r2,
                              "idempotency_key": fail_key})
                    persist_failed = persist.status_code
                    persist_detail = persist.json().get("detail") if persist.status_code != 200 else None
                finally:
                    job_dir.chmod(original_mode)
                after_fail = client.get(f"/api/jobs/{job_id}/report-confirmation")
                after_fail.raise_for_status()
                records_after_fail = list(after_fail.json().get("confirm_records") or [])
                fail_key_records = [r for r in records_after_fail
                                    if r.get("idempotency_key") == fail_key]
                # 门禁在重分析后必须已打开（阻塞性义务由程序完成，非人工代算）
                steps.step("S4b_persist_failure_is_transactional",
                           truth_r2["ok"] and can_confirm
                           and persist_failed == 500
                           and not fail_key_records
                           and len(records_after_fail) == len(records_before),
                           {"truth": truth_r2, "can_confirm": can_confirm,
                            "persist_http": persist_failed,
                            "persist_detail": persist_detail,
                            "records_before": len(records_before),
                            "records_after": len(records_after_fail),
                            "failed_key_records": len(fail_key_records)})

                # ---- S4c 成功签认：版本绑定 + 稳定问题 ID + 幂等 + 并发 ----
                confirm_key = f"ok-{secrets.token_hex(4)}"
                body = {"expected_analysis_revision": revision_r2,
                        "idempotency_key": confirm_key}
                first = client.post(f"/api/jobs/{job_id}/confirm-report", json=body)
                repeat = client.post(f"/api/jobs/{job_id}/confirm-report", json=body)
                with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
                    concurrent_results = list(pool.map(
                        lambda _: client.post(f"/api/jobs/{job_id}/confirm-report",
                                              json={"expected_analysis_revision": revision_r2,
                                                    "idempotency_key": confirm_key}),
                        range(3)))
                readback = client.get(f"/api/jobs/{job_id}/report-confirmation")
                readback.raise_for_status()
                readback_body = readback.json()
                records = readback_body.get("confirm_records") or []
                key_records = [r for r in records if r.get("idempotency_key") == confirm_key]
                record = first.json().get("record") or {} if first.status_code == 200 else {}
                expected_confirmed_ids = [
                    str(item.get("id")) for item in
                    (gate_body_r2.get("categories", {}).get("confirmed_material_issues") or [])
                    if item.get("id")
                ]
                steps.step("S4c_confirm_success_idempotent_concurrent",
                           first.status_code == 200
                           and first.json().get("idempotent") is False
                           and record.get("analysis_revision") == revision_r2
                           and record.get("confirmed_issue_ids") == expected_confirmed_ids
                           and repeat.status_code == 200
                           and repeat.json().get("idempotent") is True
                           and repeat.json().get("record") == record
                           and all(r.status_code == 200 for r in concurrent_results)
                           and len(key_records) == 1
                           and readback_body.get("latest_confirm_record") == record,
                           {"first_http": first.status_code,
                            "repeat_http": repeat.status_code,
                            "concurrent": [r.status_code for r in concurrent_results],
                            "record_revision": record.get("analysis_revision"),
                            "confirmed_count": record.get("confirmed_count"),
                            "expected_confirmed_count": len(expected_confirmed_ids),
                            "records_for_key": len(key_records),
                            "latest_matches": readback_body.get("latest_confirm_record") == record,
                            "excluded": record.get("excluded")})

                # ---- S4e 人工决定变化 + 全新 key 并发首写（R2-03） ----
                # 先确认，再对一条 info 问题作"忽略"决定：读回必须标记
                # 当前确认的决定指纹已变化；新 key 再确认时该条必须被排除。
                info_finding = next(
                    (item for item in _dedupe_findings(_iter_findings(result_r2))
                     if str(item.get("severity") or "").lower() == "info"
                     and item.get("id")), None)
                decision_ok = False
                decision_detail = {}
                if info_finding is not None:
                    ignore = client.post("/api/workflow", json={
                        "action": "update_issue", "job_id": job_id,
                        "issue_id": info_finding["id"], "status": "needs_review",
                        "note": "D4 R2-03：人工标记需复核，验证决定变化后的签认范围"})
                    changed_gate = client.get(
                        f"/api/jobs/{job_id}/report-confirmation")
                    changed_gate.raise_for_status()
                    changed_body = changed_gate.json()
                    new_key = f"after-decision-{secrets.token_hex(4)}"
                    second_confirm = client.post(
                        f"/api/jobs/{job_id}/confirm-report",
                        json={"expected_analysis_revision": revision_r2,
                              "idempotency_key": new_key})
                    second_record = (second_confirm.json().get("record") or {}
                                     if second_confirm.status_code == 200 else {})
                    decision_ok = (
                        ignore.status_code == 200
                        and changed_body.get("current_confirm_decision_changed") is True
                        and changed_body.get("latest_confirm_record_is_current") is True
                        and second_confirm.status_code == 200
                        and info_finding["id"] not in (
                            second_record.get("confirmed_issue_ids") or [])
                        and second_record.get("confirmed_count")
                        == record.get("confirmed_count", 0) - 1
                    )
                    decision_detail = {
                        "ignored_id": info_finding["id"],
                        "ignore_http": ignore.status_code,
                        "decision_changed": changed_body.get(
                            "current_confirm_decision_changed"),
                        "second_confirm_http": second_confirm.status_code,
                        "second_count": second_record.get("confirmed_count"),
                        "first_count": record.get("confirmed_count")}
                    if second_confirm.status_code == 200:
                        record = second_record
                # 全新幂等 key 并发首写：两个都成功，各留一条记录。
                fresh_keys = [f"fresh-{secrets.token_hex(4)}" for _ in range(2)]
                with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                    fresh_results = list(pool.map(
                        lambda key: client.post(
                            f"/api/jobs/{job_id}/confirm-report",
                            json={"expected_analysis_revision": revision_r2,
                                  "idempotency_key": key}),
                        fresh_keys))
                fresh_records = {}
                for key, answer in zip(fresh_keys, fresh_results):
                    if answer.status_code == 200:
                        fresh_records[key] = answer.json().get("record") or {}
                final_readback = client.get(f"/api/jobs/{job_id}/report-confirmation")
                final_readback.raise_for_status()
                final_body = final_readback.json()
                fresh_ok = (
                    all(answer.status_code == 200 for answer in fresh_results)
                    and all(not answer.json().get("idempotent")
                            for answer in fresh_results)
                    and len(fresh_records) == 2
                    and final_body.get("latest_confirm_record_is_current") is True
                    and final_body.get("current_confirm_decision_changed") is False
                )
                steps.step("S4e_decision_change_and_fresh_key_concurrency",
                           decision_ok and fresh_ok,
                           {"decision": decision_detail,
                            "fresh_http": [a.status_code for a in fresh_results],
                            "fresh_records": len(fresh_records),
                            "latest_is_current": final_body.get(
                                "latest_confirm_record_is_current"),
                            "decision_changed_after": final_body.get(
                                "current_confirm_decision_changed")})

                # ---- S4d 版本变化：旧页面（旧 revision）确认必须 409 ----
                stale = client.post(
                    f"/api/jobs/{job_id}/confirm-report",
                    json={"expected_analysis_revision": revision_r1,
                          "idempotency_key": f"stale-{secrets.token_hex(4)}"})
                stale_detail = (stale.json().get("detail")
                                if stale.status_code != 200 else None)
                stale_records = client.get(f"/api/jobs/{job_id}/report-confirmation")
                stale_records.raise_for_status()
                records_on_r1 = [
                    r for r in (stale_records.json().get("confirm_records") or [])
                    if r.get("analysis_revision") == revision_r1]
                steps.step("S4d_stale_revision_rejected",
                           revision_r1 != revision_r2
                           and stale.status_code == 409
                           and isinstance(stale_detail, dict)
                           and stale_detail.get("error") == "REVISION_CHANGED"
                           and stale_detail.get("expected") == revision_r1
                           and stale_detail.get("current") == revision_r2
                           and not records_on_r1,
                           {"revision_old": revision_r1,
                            "revision_current": revision_r2,
                            "stale_http": stale.status_code,
                            "stale_detail": stale_detail,
                            "records_on_old_revision": len(records_on_r1)})

                # ---- S5 复核生命周期：开始幂等 → 完成门禁先如实阻塞 →
                #      逐条核对后才作决定 → 完成 200 → 重开 200 → 再完成 200 ----
                review_by_job = client.get("/api/reviews", params={"job_uuid": job_id})
                review_by_job.raise_for_status()
                review_data = review_by_job.json().get("data") or {}
                context = review_data.get("review_context") or {}
                slot_id = (review_data.get("review") or {}).get("slot_id")
                if not context.get("available") or not slot_id:
                    raise AssertionError(
                        f"复核上下文不可用: {json.dumps(context, ensure_ascii=False)[:400]}")
                start_a = client.post(f"/api/reviews/{slot_id}/start", json={"job_uuid": job_id})
                start_b = client.post(f"/api/reviews/{slot_id}/start", json={"job_uuid": job_id})
                with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                    starts = list(pool.map(
                        lambda _: client.post(f"/api/reviews/{slot_id}/start",
                                              json={"job_uuid": job_id}), range(2)))

                def _sid(resp):
                    return (((resp.json().get("data") or {}).get("session") or {})
                            .get("review_session_id"))

                same_session = (start_a.status_code == 200 and start_b.status_code == 200
                                and _sid(start_a) and _sid(start_a) == _sid(start_b)
                                and all(s.status_code == 200 for s in starts))
                lifecycle = client.get(f"/api/reviews/{slot_id}", params={"job_uuid": job_id})
                lifecycle.raise_for_status()
                lifecycle_data = lifecycle.json().get("data") or {}
                basis_token = ((lifecycle_data.get("current_analysis") or {})
                               .get("analysis_basis_token")) or ""
                session = (lifecycle_data.get("current_session") or {})
                session_token = session.get("analysis_basis_token") or basis_token
                steps.step("S5d_start_idempotent", same_session,
                           {"start_a": start_a.status_code, "start_b": start_b.status_code,
                            "starts": sorted(s.status_code for s in starts),
                            "session_present": bool(_sid(start_a))})

                # 完成门禁在任何决定之前必须如实阻塞（不允许"零决定也完成"）
                pre_complete = client.post(f"/api/reviews/{slot_id}/complete", json={})
                pre_blockers = []
                if pre_complete.status_code == 409:
                    detail = pre_complete.json().get("detail")
                    pre_blockers = (detail or {}).get("blockers") if isinstance(detail, dict) else []
                # 与门禁同源：正式问题集合（无决定 → 必须全部待处理）。
                # review_problem_set 复用服务端模块（含 issue_workflow_store），
                # 导入 api.runtime 需要同一套安全/目录配置；只在本验收进程
                # 设置与隔离栈一致的环境，不改服务端状态。
                os.environ.update(
                    GOVBUDGET_AUTH_ENABLED="true",
                    GOVBUDGET_API_KEY=api_key,
                    USER_SESSION_SECRET=session_secret,
                    UPLOAD_DIR=env["UPLOAD_DIR"],
                    USER_DATA_DIR=env["USER_DATA_DIR"],
                    ORG_DATA_DIR=env["ORG_DATA_DIR"],
                    DATABASE_URL=dsn,
                    TESTING="false",
                )
                ingest = client.get(f"/api/jobs/{job_id}/structured-ingest")
                ingest.raise_for_status()
                structured_ingest = ingest.json() or {}
                problems = review_problem_set(job_id, result_r2, structured_ingest)
                formal_problems = [p for p in problems if p.is_blocking_candidate]
                formal_from_rule_findings = [
                    p for p in formal_problems if p.source == "finding"]
                structured_formal = [
                    p for p in formal_problems if p.source != "finding"]

                # 逐条决定：只有在本脚本已核对原件（且正式问题对应 finding
                # 的证据 verified、页码可定位）之后才作 confirmed；否则保持
                # 未决。结构化待复核项不在确定性证据覆盖内，一律未决。
                grounded_ids = {
                    str(finding.get("id")) for finding
                    in _dedupe_findings(_iter_findings(result_r2))
                    if str(finding.get("id") or "").strip()
                    and _finding_evidence_status(finding) == "verified"
                    and str(finding.get("severity") or "").strip().lower() != "manual_review"
                    and _positive_int(((finding.get("location") or {}).get("page"))
                                      or finding.get("page")) is not None
                }
                decisions, decision_log, undecided = [], [], []
                for problem in structured_formal:
                    undecided.append({"issue_id": problem.issue_id,
                                      "reason": "structured_review_requires_user",
                                      "severity": problem.severity})
                for problem in formal_from_rule_findings:
                    candidates = {problem.issue_id, *problem.member_issue_ids}
                    if not (candidates & grounded_ids):
                        undecided.append({"issue_id": problem.issue_id,
                                          "reason": "not_grounded_by_original_evidence",
                                          "severity": problem.severity})
                        continue
                    answer = client.post("/api/workflow", json={
                        "action": "update_issue", "job_id": job_id,
                        "issue_id": problem.issue_id, "status": "confirmed",
                        "note": ("D4 验收脚本以复核人身份核对原件后确认：证据页码与原文命中"
                                 "见 S3b 覆盖清单；规则=" + str(problem.severity))})
                    decision_log.append({"issue_id": problem.issue_id,
                                         "http": answer.status_code,
                                         "severity": problem.severity})
                    if answer.status_code == 200:
                        decisions.append(problem.issue_id)
                # 结构化待复核项（若有）不在 S3b 证据覆盖内：如实记未决，
                # 不能由脚本代替用户判定。
                remaining = [p.issue_id for p in formal_problems
                             if p.issue_id not in set(decisions)]
                complete_after = client.post(f"/api/reviews/{slot_id}/complete", json={})
                complete_blockers_after = []
                if complete_after.status_code == 409:
                    detail = complete_after.json().get("detail")
                    complete_blockers_after = ((detail or {}).get("blockers")
                                               if isinstance(detail, dict) else [])

                reopen_status, re_complete_status = None, None
                refreshed_session = {}
                if complete_after.status_code == 200:
                    reopened = client.post(f"/api/reviews/{slot_id}/reopen", json={})
                    reopen_status = reopened.status_code
                    refreshed = client.get(f"/api/reviews/{slot_id}",
                                           params={"job_uuid": job_id})
                    refreshed.raise_for_status()
                    refreshed_session = ((refreshed.json().get("data") or {})
                                         .get("current_session") or {})
                    # 重开后再完成：本版本上已有全部决定，必须成功 200
                    # （旧验收器允许 409 也算通过，判据被 Codex 判为不足）
                    re_complete = client.post(f"/api/reviews/{slot_id}/complete", json={})
                    re_complete_status = re_complete.status_code
                steps.step("S5_review_lifecycle_decisions",
                           pre_complete.status_code == 409
                           and bool(pre_blockers)
                           and not undecided
                           and complete_after.status_code == 200
                           and reopen_status == 200
                           and refreshed_session.get("status") in {"in_progress", None}
                           and re_complete_status == 200,
                           {"pre_complete_http": pre_complete.status_code,
                            "pre_blockers": str(pre_blockers)[:300],
                            "formal_problems": len(formal_problems),
                            "decided": len(decisions), "undecided": undecided,
                            "decisions_sample": decision_log[:5],
                            "complete_after_http": complete_after.status_code,
                            "complete_after_blockers": str(complete_blockers_after)[:300],
                            "reopen_http": reopen_status,
                            "recomplete_http": re_complete_status,
                            "refreshed_session_status": refreshed_session.get("status")})

                # ---- S6 导出：四格式非空、重复一致、成员与当前分析一致、
                #      绑定当前版本 ----
                export_sizes, export_ok, repeat_identical = {}, True, True
                export_contents = {}
                exported_json = None
                for suffix in ("json", "csv", "docx", "pdf"):
                    first_export = get_report_with_backoff(
                        client, params={"job_id": job_id, "format": suffix})
                    first_export.raise_for_status()
                    again = get_report_with_backoff(
                        client, params={"job_id": job_id, "format": suffix})
                    again.raise_for_status()
                    if first_export.content != again.content:
                        repeat_identical = False
                    export_sizes[suffix] = len(first_export.content)
                    export_contents[suffix] = first_export.content
                    if not first_export.content:
                        export_ok = False
                    if suffix == "json":
                        exported_json = json.loads(first_export.content)
                current_status = client.get(f"/api/jobs/{job_id}/status")
                current_status.raise_for_status()
                current_payload = current_status.json()
                current_revision = current_payload.get("analysis_revision")
                current_result = _json_result(current_payload)
                review_report = (exported_json or {}).get("review_report") or {}
                exported_issues = [item for item in (review_report.get("issues") or [])
                                   if isinstance(item, dict)]
                from src.schemas.review_report import _is_confirmed_material_finding

                expected_export_ids = {
                    str(finding.get("id"))
                    for finding in _dedupe_findings(_iter_findings(current_result))
                    if finding.get("id") and _is_confirmed_material_finding(finding)
                }
                exported_ids = {str(item.get("id")) for item in exported_issues
                                if item.get("id")}
                membership_ok = exported_ids == expected_export_ids
                # 导出中的每条问题都必须带可定位页（page=None 不算）
                export_pages_ok = all(
                    _positive_int(((item.get("location") or {}).get("page"))
                                  or item.get("page")) is not None
                    for item in exported_issues)
                revision_binding = bool(exported_json) and (
                    exported_json.get("analysis_revision") == current_revision)
                format_check = verify_export_formats(
                    export_contents, exported_issues, current_revision)
                steps.step("S6_export_membership",
                           export_ok and repeat_identical and membership_ok
                           and export_pages_ok and revision_binding
                           and format_check["ok"],
                           {"sizes": export_sizes,
                            "repeat_identical": repeat_identical,
                            "exported_issues": len(exported_issues),
                            "expected_export_issues": len(expected_export_ids),
                            "membership_ok": membership_ok,
                            "export_pages_ok": export_pages_ok,
                            "revision_binding": revision_binding,
                            "format_content": format_check,
                            "missing_from_export": sorted(expected_export_ids - exported_ids)[:10],
                            "extra_in_export": sorted(exported_ids - expected_export_ids)[:10]})

                # ---- S7 阻塞分支（真实配置负控）：AI 被配置为必需但不可用时，
                #      AI 义务必须成为阻塞项，整份确认必须 409 且不落记录。
                #      做法：停掉当前 worker，换一个 AI_ASSIST_REQUIRED=true 的
                #      worker 处理第二个真实任务；不伪造任何结果结构。 ----
                worker.terminate()
                try:
                    worker.wait(timeout=30)
                except Exception:  # noqa: BLE001
                    worker.kill()
                worker_strict = _sp.Popen(
                    [sys.executable, "-m", "api.worker"],
                    cwd=ROOT, env={**env, "JOB_QUEUE_ROLE": "worker",
                                   "AI_ASSIST_REQUIRED": "true"},
                    stdout=(out / "worker_ai_required.log").open("w"),
                    stderr=_sp.STDOUT,
                )
                processes.append(worker_strict)
                time.sleep(2)
                # 上传带校验和去重：同一原件二次上传会被 409 拒绝（产品行为）。
                # 为验证"必需 AI 未执行"的阻塞分支，这里用**同内容字节副本**
                # （仅在文件尾追加 PDF 注释、不改页面/文本）绕过去重；原件不变，
                # 解析结果与首份一致。回执中如实记录该副本的存在与用途。
                control_pdf = work / f"ai-required-control-{pdf_path.name}"
                control_pdf.write_bytes(
                    pdf_path.read_bytes() + b"\n% D4 AI-required control copy\n")
                with control_pdf.open("rb") as file:
                    strict_upload = request_with_backoff(
                        client, "POST", "/api/documents/upload",
                        files={"file": (pdf_path.name, file, "application/pdf")})
                strict_upload.raise_for_status()
                strict_job = strict_upload.json()["job_id"]
                request_with_backoff(
                    client, "POST", f"/api/analyze/{strict_job}",
                    json={"mode": "legacy", "use_ai_assist": False,
                          "use_local_rules": True}).raise_for_status()
                strict_payload = _wait_analysis(client, strict_job)
                strict_result = _json_result(strict_payload)
                strict_revision = strict_payload.get("analysis_revision")
                strict_gate = client.get(f"/api/jobs/{strict_job}/report-confirmation")
                strict_gate.raise_for_status()
                strict_body = strict_gate.json()
                strict_truth = gate_truth_check(strict_body, strict_result)
                strict_post = client.post(
                    f"/api/jobs/{strict_job}/confirm-report",
                    json={"expected_analysis_revision": strict_revision,
                          "idempotency_key": f"strict-{secrets.token_hex(4)}"})
                strict_after = client.get(f"/api/jobs/{strict_job}/report-confirmation")
                strict_after.raise_for_status()
                strict_records = strict_after.json().get("confirm_records") or []
                strict_instances = ((strict_result.get("meta") or {}).get(
                    "obligation_coverage") or {}).get("instances") or []
                ai_instance = next(
                    (item for item in strict_instances
                     if item.get("obligation_id") == "OBL-AI-SEMANTIC-REVIEW"), {})
                steps.step("S7_required_ai_not_run_blocks_confirm",
                           bool(strict_revision)
                           and (strict_body.get("gate") or {}).get(
                               "can_confirm_whole_report") is False
                           and int((strict_body.get("gate") or {}).get(
                               "blocking_system_incomplete") or 0) >= 1
                           and ai_instance.get("status") == "ai_not_run"
                           and ai_instance.get("blocks_gate") is True
                           and strict_truth["ok"]
                           and strict_post.status_code == 409
                           and strict_post.json().get("detail") == "SYSTEM_INCOMPLETE"
                           and not strict_records,
                           {"job_id": strict_job,
                            "revision": strict_revision,
                            "control_input": "same-content byte copy (PDF comment appended)",
                            "gate": strict_body.get("gate"),
                            "counts": strict_body.get("counts"),
                            "ai_instance": {"status": ai_instance.get("status"),
                                            "blocks_gate": ai_instance.get("blocks_gate")},
                            "truth_ok": strict_truth["ok"],
                            "truth_problems": strict_truth["problems"],
                            "post_http": strict_post.status_code,
                            "post_detail": strict_post.json().get("detail"),
                            "records": len(strict_records)})

                receipt["elapsed_seconds"] = round(time.monotonic() - started, 1)
                receipt["conclusion"] = {
                    "steps_passed": len(steps.records),
                    "confirmed_issue_ids": record.get("confirmed_issue_ids"),
                    "excluded": record.get("excluded"),
                    "scripted_reviewer_decisions": decisions,
                    "note": (
                        "本回执验证链路机制：证据逐条回原件核对、门禁如实阻塞/放行、"
                        "确认写侧版本绑定/幂等/事务失败、刷新读回、复核完成/重开、"
                        "导出成员与版本一致。复核决定由脚本以复核人身份按原件核对后"
                        "作出（逐条依据见 S3b 覆盖与 decisions），不计为程序自动完成，"
                        "也不构成人工工作量下降的证据。"
                    ),
                }
                print("D4 真实链路验收：全部步骤 PASS", flush=True)
    except AssertionError as exc:
        receipt["failure"] = str(exc)
        raise
    finally:
        # R2-07：不论成功/失败都清理本次创建的容器（只按本次 name，不碰他人容器），
        # 并把清理结果写进回执。
        cleanup = {"container": name, "attempted": False, "removed": False}
        try:
            cleanup["attempted"] = True
            docker("rm", "-f", name)
            cleanup["rm_ok"] = True
        except Exception as exc:  # noqa: BLE001 - 容器未创建/已退出也要留痕
            cleanup["rm_ok"] = False
            cleanup["rm_error"] = f"{type(exc).__name__}: {exc}"
        try:
            remaining = docker("ps", "-q", "-f", f"name={name}").strip()
            cleanup["still_running"] = bool(remaining)
            cleanup["removed"] = not cleanup["still_running"] and (
                cleanup.get("rm_ok") or "No such container" in str(cleanup.get("rm_error", "")))
        except Exception as exc:  # noqa: BLE001
            cleanup["verify_error"] = f"{type(exc).__name__}: {exc}"
        receipt["cleanup"] = cleanup
        receipt["steps"] = steps.records
        (out / "d4_receipt.json").write_text(
            json.dumps(receipt, ensure_ascii=False, indent=1, default=str), encoding="utf-8")


def docker_exec_ready(name: str) -> bool:
    return subprocess.run(
        ["docker", "exec", name, "pg_isready", "-U", "validator", "-d", "validation"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    ).returncode == 0


if __name__ == "__main__":
    main()
