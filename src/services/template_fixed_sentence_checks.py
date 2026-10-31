"""试运行能力：未进正式规则清单/义务账本。

模板固定语句试运行检查（2025 决算模板对照最小试运行闭环，专用模块）。

本模块只消费试运行导出包 config 里的 ``trial_fixed_sentences`` 候选条目：
这些条目来自 uploads/.templates 两份 2025 决算模板 extracted.txt，状态为
"候选（待人工确认）"，不冒充已发布标准，也不写入义务账本或正式规则清单。
判定口径全部为试运行判据：命中结果以"改动候选/缺失候选"呈现，供人工复核。

输入约定（与生产锚点检查同一取数路径）：

- ``doc.document_profile.writing_scope``：material_writing.load 的统一裁决，
  其中 ``snapshot.available`` 须为真，``snapshot.content.config.trial_fixed_sentences``
  为候选条目列表；
- ``doc.writing_page_texts``（优先）或 ``doc.page_texts``：材料逐页原生文本。

每条候选固定语句的判定顺序（与试运行方案一致）：

1. 资格：模板侧记录（来源哈希/行号/原文/核对核心）齐全，否则"未检查"；
2. 层级：``writing_scope`` 裁决与条目 scope 不匹配 → "不适用"；
   裁决未确认且 scope='all' 仍可运行（固定语句与写法无关）；
3. 上级章节（如"第三部分"）在正文（跳目录页）未定位 → "未检查"并写明原因；
4. 正文精确出现核对尾串（仅允许机构名槽位差异）→ "正常"；
5. 找到近似行（核对核心在页内但尾串被改，或"无"与核心关键词近距离同现
   而与原文不一致）→ "命中/改动候选"（规划 CMM-028，low）；
6. 完全找不到但条件语境存在（三公/国资章节可定位）→ 结合实际支出叙述
   判"命中/缺失候选"（规划 CMM-029，medium）或信号混叠时"待复核"；
   条件语境不存在（材料有实际支出叙述或章节缺失）→ "不适用"。
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from src.engine.checklist_support import is_toc_page

# ---------------------------------------------------------------------------
# 中文正则集中区（试运行固定算法；候选条目不能注入任意正则，只能引用此处
# 已审核的守卫模式，与 annual_templates 固定算法同一纪律）。
# ---------------------------------------------------------------------------

#: 近似行守卫：'无'与核对核心关键词近距离同现（间隔内不得出现句读）。
#: 三公固定语句：无……"三公"经费；国资固定语句：无……国有资本。
_PROXIMITY_GUARDS: Dict[str, re.Pattern[str]] = {
    "sangong": re.compile(r"无[^，。；：！？\n]{0,8}“?三公”?经?费"),
    "state_capital": re.compile(r"无[^，。；：！？\n]{0,8}国有资本"),
}

#: 实际支出叙述提示：金额陈述（区分"空表说明语境"与真实支出）。
_AMOUNT_STATEMENT_RE = re.compile(r"\d+(?:\.\d+)?\s*万元")

#: 上下文章节标题前缀（决算第三部分：决算情况说明）。
_PART_HEADING_PREFIX = "第三部分"

#: 候选条目允许的层级取值（与锚点 scope 同表）。
_ALLOWED_SCOPES = ("all", "unit", "department")


def _flat(text: str) -> str:
    """比较用归一：去全部空白。引号、全半角差异按原文保留，避免过度归一。"""
    return re.sub(r"\s+", "", str(text or ""))


def _iter_body(pages: List[str]):
    """逐页产出 (1 起始页码, 原文, 去空白视图)；目录页跳过（与 iter_pages 同纪律）。"""
    for index, text in enumerate(pages, start=1):
        if not str(text or "").strip() or is_toc_page(text):
            continue
        yield index, str(text), _flat(text)


def _snippet(flat_page: str, start: int, end: int, radius: int = 16) -> str:
    """在去空白视图上截取命中片段及少量上下文，供报告阅读定位。"""
    left = max(0, start - radius)
    right = min(len(flat_page), end + radius)
    return flat_page[left:right]


def _entry_qualified(entry: Any) -> Tuple[bool, str]:
    """资格检查：模板侧记录（哈希/行号/原文/核对核心）齐全且内部一致。"""
    if not isinstance(entry, dict):
        return False, "候选条目不是对象"
    template_side = entry.get("template_side")
    if not isinstance(template_side, dict):
        return False, "缺少模板侧记录（template_side）"
    for field in ("source_id", "line", "source_sha256", "extracted_sha256"):
        if not template_side.get(field):
            return False, "模板侧记录缺少字段：" + field
    if not isinstance(template_side.get("line"), int) or template_side["line"] < 1:
        return False, "模板侧行号无效"
    for field in ("source_sha256", "extracted_sha256"):
        value = str(template_side.get(field) or "")
        if len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
            return False, "模板侧哈希格式无效：" + field
    for field in ("original", "check_core", "tail"):
        if not str(entry.get(field) or "").strip():
            return False, "缺少原文/核对核心/核对尾串：" + field
    if entry.get("guard") not in _PROXIMITY_GUARDS:
        return False, "近似守卫模式不在固定算法清单内"
    if entry.get("scope") not in _ALLOWED_SCOPES:
        return False, "适用层级无效"
    tail = _flat(entry.get("tail"))
    if _flat(entry.get("check_core")) not in tail:
        return False, "核对核心不在核对尾串内，条目内部不一致"
    return True, ""


def _find_exact(pages: List[str], tail: str) -> Optional[Dict[str, Any]]:
    """正文精确出现核对尾串（仅机构名槽位差异）→ 正常。"""
    for page, _raw, page_flat in _iter_body(pages):
        start = page_flat.find(tail)
        if start >= 0:
            return {"page": page, "text": _snippet(page_flat, start, start + len(tail))}
    return None


def _find_near_miss(pages: List[str], core: str, tail: str, guard: re.Pattern[str]) -> List[Dict[str, Any]]:
    """近似行两种形态（试运行判据，注明重叠范围）：

    a) 核对核心整段在去空白页内、但核对尾串不再成立（核心片段外围被改，
       如"……财政拨款支出安排。"）；
    b) "无"与核心关键词近距离同现的行，去空白后与原文尾串不一致
       （核心片段内部被改，如"和"改"及"）。
    """
    hits: List[Dict[str, Any]] = []
    for page, raw, page_flat in _iter_body(pages):
        if tail in page_flat:
            continue
        start = page_flat.find(core)
        if start >= 0:
            hits.append({"page": page, "text": _snippet(page_flat, start, start + len(core)),
                         "form": "核心片段外围被改"})
            continue
        for line in raw.splitlines():
            flat_line = _flat(line)
            if not flat_line or flat_line == _flat(tail):
                continue
            if guard.search(flat_line):
                hits.append({"page": page, "text": _snippet(flat_line, 0, min(len(flat_line), 48)),
                             "form": "无与核心关键词近距离同现"})
                break
    return hits


def _last_page_with(pages: List[str], needle: str) -> Optional[int]:
    """取（跳目录页后）包含归一文本的最后一个正文页。

    目录续页（样本第 3 页）不被 is_toc_page 识别，标题先出现在目录里；
    正文位置总在目录之后，故取最后一次出现作为条件语境页（试运行判据）。
    """
    found: Optional[int] = None
    for page, _raw, page_flat in _iter_body(pages):
        if needle in page_flat:
            found = page
    return found


def _locate_part(pages: List[str], prefix: str) -> Optional[int]:
    """定位上级部分标题（跳目录页）；找不到返回 None。

    标题可能折行或与正文同行，去空白视图上包含前缀即视为定位到（试运行
    判据；目录页已跳过，正文引用场景极少，人工复核兜底）。
    """
    return _last_page_with(pages, _flat(prefix))


def _locate_section(pages: List[str], heading: str) -> Optional[Dict[str, Any]]:
    """按标题原文（去空白）定位条件语境章节页（取最后一次出现，见 _last_page_with）。"""
    needle = _flat(heading)
    page = _last_page_with(pages, needle)
    if page is None:
        return None
    page_flat = _flat(pages[page - 1])
    start = page_flat.find(needle)
    return {"page": page, "text": _snippet(page_flat, start, start + len(needle))}


def _spending_signals(pages: List[str], anchor_page: int, context_token: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """在语境章节页及邻近页（±1）内分检"实际支出叙述"与"无字样"信号。

    返回 (支出信号, 无字样信号)。两者同时存在时判据混叠，按试运行纪律
    只能标"待复核"，不冒充确定结论。
    """
    spending: List[Dict[str, Any]] = []
    negatives: List[Dict[str, Any]] = []
    wanted = {anchor_page - 1, anchor_page, anchor_page + 1}
    for page, raw, _page_flat in _iter_body(pages):
        if page not in wanted:
            continue
        for line in raw.splitlines():
            flat_line = _flat(line)
            if context_token not in flat_line:
                continue
            if _AMOUNT_STATEMENT_RE.search(flat_line) and "无" not in flat_line:
                spending.append({"page": page, "text": _snippet(flat_line, 0, min(len(flat_line), 48))})
            elif "无" in flat_line:
                negatives.append({"page": page, "text": _snippet(flat_line, 0, min(len(flat_line), 48))})
    return spending, negatives


def trial_fixed_sentence_findings(doc: Any) -> List[Dict[str, Any]]:
    """对候选固定语句逐条给出试运行判定（结构化条目，不产出正式 Issue）。"""
    resolution = getattr(getattr(doc, "document_profile", None), "writing_scope", {}) or {}
    snapshot = resolution.get("snapshot") or {}
    config = snapshot.get("effective_config") or (snapshot.get("content") or {}).get("config") or {}
    pages = list(getattr(doc, "writing_page_texts", None) or getattr(doc, "page_texts", None) or [])
    entries = config.get("trial_fixed_sentences") or []

    findings: List[Dict[str, Any]] = []
    for position, entry in enumerate(entries, start=1):
        identifier = str(entry.get("id") or "F#" + str(position)) if isinstance(entry, dict) else "F#" + str(position)
        template_side = entry.get("template_side") if isinstance(entry, dict) else None
        base: Dict[str, Any] = {
            "id": identifier,
            "rule_scope": "CMM-028(规划)/CMM-029(规划)",
            "trial_only": True,
            "condition": entry.get("condition") if isinstance(entry, dict) else None,
            "template_side": {
                "source_id": (template_side or {}).get("source_id"),
                "material_type": (template_side or {}).get("material_type"),
                "line": (template_side or {}).get("line"),
                "context_line": (template_side or {}).get("context_line"),
                "original": entry.get("original") if isinstance(entry, dict) else None,
                "source_sha256": (template_side or {}).get("source_sha256"),
                "extracted_sha256": (template_side or {}).get("extracted_sha256"),
            } if isinstance(entry, dict) else None,
            "material_side": None,
            "verdict": None,
            "reason": "",
        }

        qualified, quality_reason = _entry_qualified(entry)
        if not qualified:
            base["verdict"] = "未检查"
            base["reason"] = "模板侧记录不完整，" + quality_reason
            findings.append(base)
            continue

        if not snapshot.get("available"):
            base["verdict"] = "未检查"
            base["reason"] = "年度模板快照不可用：" + str(snapshot.get("reason") or "")
            findings.append(base)
            continue

        scope = entry.get("scope")
        status = resolution.get("status")
        value = resolution.get("value")
        if scope != "all":
            if status != "confirmed":
                base["verdict"] = "未检查"
                base["reason"] = "写法层级未确认（scope=" + str(scope) + "），固定语句层级适用性无法裁决"
                findings.append(base)
                continue
            if value != scope:
                base["verdict"] = "不适用"
                base["reason"] = "材料写法层级为" + str(value) + "，候选语句适用层级为" + str(scope)
                findings.append(base)
                continue

        core = _flat(entry["check_core"])
        tail = _flat(entry["tail"])
        guard = _PROXIMITY_GUARDS[entry["guard"]]

        parent_page = _locate_part(pages, str(entry.get("parent_section") or _PART_HEADING_PREFIX))
        if parent_page is None:
            base["verdict"] = "未检查"
            base["reason"] = "上级章节“" + str(entry.get("parent_section") or _PART_HEADING_PREFIX) + "”未在正文（跳目录页）定位，固定语句无法归位"
            findings.append(base)
            continue

        exact = _find_exact(pages, tail)
        if exact is not None:
            base["verdict"] = "正常"
            base["material_side"] = exact
            base["reason"] = "核对尾串在正文精确出现（仅机构名槽位与模板不同）；模板侧第" + str(template_side["line"]) + "行"
            findings.append(base)
            continue

        near = _find_near_miss(pages, core, tail, guard)
        if near:
            first = near[0]
            base["verdict"] = "命中"
            base["candidate_type"] = "改动候选"
            base["rule_scope"] = "CMM-028(规划)"
            base["severity_suggestion"] = "low"
            base["material_side"] = {"page": first["page"], "text": first["text"]}
            base["overlaps"] = [{"page": item["page"], "text": item["text"], "form": item["form"]} for item in near]
            base["reason"] = ("正文出现与模板固定语句近似但不一致的写法（" + first["form"] +
                              "）；模板侧第" + str(template_side["line"]) + "行原文为：" + str(entry["original"]) +
                              "。改动候选为试运行判据，与缺失候选存在重叠范围，待人工复核")
            findings.append(base)
            continue

        section = _locate_section(pages, str(entry.get("context_heading") or ""))
        if section is None:
            base["verdict"] = "不适用"
            base["reason"] = "条件语境不存在：条件章节“" + str(entry.get("context_heading") or "") + "”未在正文定位"
            findings.append(base)
            continue

        context_token = str(entry.get("context_token") or "")
        spending, negatives = _spending_signals(pages, section["page"], context_token)
        if spending and negatives:
            base["verdict"] = "待复核"
            base["material_side"] = {"page": section["page"], "text": section["text"]}
            base["reason"] = ("条件语境页内同时存在实际支出叙述与“无”字样说明，试运行判据无法区分；"
                              "固定语句未找到。语境章节见第" + str(section["page"]) + "页，待人工复核")
            findings.append(base)
            continue
        if spending:
            base["verdict"] = "不适用"
            base["material_side"] = {"page": section["page"], "text": section["text"]}
            base["reason"] = ("材料在“" + str(entry.get("context_heading") or "") + "”章节（第" + str(section["page"]) +
                              "页起）存在实际支出叙述，不属于空表说明语境；固定语句缺席符合模板条件")
            findings.append(base)
            continue

        base["verdict"] = "命中"
        base["candidate_type"] = "缺失候选"
        base["rule_scope"] = "CMM-029(规划)"
        base["severity_suggestion"] = "medium"
        base["material_side"] = {"page": section["page"], "text": section["text"]}
        base["reason"] = ("条件语境存在（" + str(entry.get("context_heading") or "") + "，第" + str(section["page"]) +
                          "页起）但正文未找到模板固定语句；模板侧第" + str(template_side["line"]) + "行原文为：" +
                          str(entry["original"]) + "。缺失候选为试运行判据，与改动候选存在重叠范围，待人工复核")
        findings.append(base)
    return findings


__all__ = ["trial_fixed_sentence_findings"]
