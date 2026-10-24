"""同步系统生成的 AI 页码文案；引文和原分析留痕保持原样。"""
import re


def synchronize_ai_page_display(finding):
    if finding.get("source") != "ai":
        return
    page = finding.get("page_number")
    location = finding.get("location") or {}
    verified = (finding.get("verification_status") == "verified"
                and finding.get("source_binding_status") == "bound")
    message = finding.get("message")
    if finding.get("source_binding_status") == "unresolved":
        if isinstance(message, str) and re.match(r"^(?:PDF\s*)?第\s*\d+\s*页", message):
            finding.setdefault("location_original_message", message)
            finding["message"] = re.sub(r"^(?:PDF\s*)?第\s*\d+\s*页", "页码待核实的原文", message, count=1)
        if finding.get("display") is not None:
            from src.utils.issue_display import build_issue_display
            finding["display"] = build_issue_display(finding)
        return
    if not verified or not isinstance(page, int) or not isinstance(message, str):
        return
    # 仅处理解释器生成的句首范围；“命中文本”里的页码属于原文，不能替换。
    match = re.match(r"^(?:PDF\s*)?第\s*(\d+)\s*页", message)
    if match and int(match.group(1)) != page:
        finding.setdefault("location_original_message", message)
        finding["message"] = f"PDF 第 {page} 页" + message[match.end():]
    location["page_basis"] = "pdf_physical_page"
    finding["location"] = location
    # 旧快照的 display.detail_lines / page_text 不能继续缓存旧的句首页码。
    if finding.get("display") is not None:
        from src.utils.issue_display import build_issue_display
        finding["display"] = build_issue_display(finding)
