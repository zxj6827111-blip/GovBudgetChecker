"""统一证据列表构造（IR-03，2026-11-10）。

此前只有 engine_rule_runner（双模式）从 ``location.table_refs`` 构造
多侧原文证据；legacy 序列化 ``pipeline._issue_to_dict`` 只落
``evidence_text`` 单条、挂在 finding 页上——同一条规则在两个正式入口
的证据形态不同，跨表关系的另一侧原文在 legacy 路径直接丢失
（Codex 独立核验 IR-03：宜川 CROSS/TXT-FUND 双侧原文 evidence failed）。

本模块是两条入口的**唯一**构造器：
- 只携带真实原文引用，绝不用问题说明冒充原文（ZC-00/SYS-07）；
- 同一问题命中多处原文（table_refs，含 span+page）时逐处给出真实引用，
  证据门逐条按各自页码核验（AC-09）；
- 无 refs 时退回 evidence_text 单条（finding 页）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


def build_issue_evidence_list(
    issue: Any,
    page_number: Optional[int],
) -> List[Dict[str, Any]]:
    """构造证据列表：只携带真实原文引用，绝不用问题说明冒充原文。

    ZC-00（SYS-07）：text 缺失时保持证据文本为空，由证据门按"缺原文"
    记录；页码未知时保持 None，不默认第 1 页。
    """
    text = str(getattr(issue, "evidence_text", None) or "").strip()
    evidence_item: Dict[str, Any] = {"page": page_number}
    if text:
        evidence_item["text"] = text
        evidence_item["text_snippet"] = text
    location = getattr(issue, "location", {}) or {}
    # 同一问题命中多处原文时（如"本表反映单位"共 6 处），逐处给出真实引用，
    # 不只用第一处冒充全部出现点；证据门逐条按各自页码核验。
    refs = [
        ref for ref in (location.get("table_refs") or [])
        if isinstance(ref, dict) and str(ref.get("span") or "").strip()
        and type(ref.get("page")) is int and ref["page"] > 0
    ]
    if len(refs) >= 2:
        items: List[Dict[str, Any]] = []
        seen: set = set()
        for ref in refs:
            key = (ref["page"], str(ref["span"]))
            if key in seen:
                continue
            seen.add(key)
            item = {
                "page": ref["page"],
                "text": str(ref["span"]),
                "text_snippet": str(ref["span"]),
                "location_level": "exact_span",
                "verification_status": "unverified",
            }
            if ref.get("role"):
                item["role"] = ref["role"]
            items.append(item)
        if len(items) >= 2:
            return items
    location_level = str(getattr(issue, "location_level", None) or "unresolved")
    if location_level not in {"exact_cell", "exact_span"}:
        location_level = str(location.get("location_level") or location_level)
    verification_status = str(getattr(issue, "verification_status", None) or "unverified")
    if verification_status != "verified":
        verification_status = str(location.get("verification_status") or verification_status)
    bbox = getattr(issue, "bbox", None) or location.get("bbox")
    evidence_item["location_level"] = location_level
    evidence_item["verification_status"] = verification_status
    if (
        bbox
        and location_level in {"exact_cell", "exact_span"}
        and verification_status == "verified"
    ):
        evidence_item["bbox"] = bbox
    return [evidence_item]
