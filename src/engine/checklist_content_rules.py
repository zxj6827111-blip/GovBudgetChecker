"""检查点清单：预决算通用的内容与表格规范规则（全系统默认检查指标，2026-10-22）。

| 规则 | 清单条目 | 内容 |
|------|----------|------|
| CMM-020 | 预算 C-08、决算 A-10/C-16 | 并列列示的各项占比合计应为 100% |
| CMM-021 | E-06 | 表名与规范表名一致（部门/单位前缀、不得多字少字） |
| CMM-022 | B-04 | 科目编码格式（前导 0）、经济分类编码与名称匹配、同表编码重复、编码类别错位 |
| CMM-023 | 预算 A-01/A-03/A-04/A-08/A-11/A-12，决算 A-01/A-03/A-04/A-11/A-12，A-05 目录缺失 | 章节模板（全系统默认） |
| CMM-024 | 预算 C-10、决算 C-11/C-12 前置 | 说明中“总额，其中：分项”按已知构成项加总 |
| CMM-025 | 决算 B-09（预算同口径） | 绩效项目数为 0 时涉及金额应为 0 |
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .checklist_support import (
    CJK,
    compact,
    display_envelope,
    display_half_unit,
    flat_span,
    is_toc_page,
    issue_at,
    iter_pages,
    page_flat,
    page_has_table,
    report_kind,
    subject_scope,
)
from .checklist_identity_rules import body_scope
from .rule_outcome import RuleNotApplicable
from src.utils.narration import normalize_amount_punctuation
from .rules_v33 import Document, Issue, Rule

_AMOUNT = r"(\d[\d,]*(?:\.\d+)?)"


def _dec(text: str) -> Optional[Decimal]:
    try:
        return Decimal(str(text).replace(",", ""))
    except (InvalidOperation, ValueError):
        return None


def _envelope(count: int) -> Decimal:
    """n 项各自两位小数四舍五入的显示包络 + 0.01 万元政策容差。"""
    return Decimal("0.005") * (count + 1) + Decimal("0.01")


# ---------------------------------------------------------------------------
# CMM-020 占比合计
# ---------------------------------------------------------------------------

_LIST_INTRO_RE = re.compile(r"(?:主要用于以下方面|主要用于以下几个方面|结构情况|构成情况|其中|包括)[:：]")
_SHARE_RE = re.compile(r"占[^，,；;。%％\d]{0,14}?(\d+(?:\.\d+)?)\s*[%％]")
_SHARE_NO_PERCENT_RE = re.compile(r"占[^，,；;。%％\d]{0,14}?\d+(?:\.\d+)?(?![\d.]|\s*[%％])")


class CMM020_ShareSum(Rule):
    code, severity = "CMM-020", "warn"
    desc = "并列列示的各项占比合计应为100%（预/决算通用，清单 C-08 / A-10 / C-16）"

    def apply(self, doc: Document) -> List[Issue]:
        kind = report_kind(doc)
        issues: List[Issue] = []
        for page, text in iter_pages(doc):
            flat, positions = page_flat(doc, page, text)
            intros = list(_LIST_INTRO_RE.finditer(flat))
            for index, intro in enumerate(intros):
                stop_candidates = [flat.find("。", intro.end())]
                if index + 1 < len(intros):
                    stop_candidates.append(intros[index + 1].start())
                heading = re.search(rf"[{'一二三四五六七八九十'}]、|[（(][一二三四五六七八九十][)）]", flat[intro.end():])
                if heading:
                    stop_candidates.append(intro.end() + heading.start())
                stop = min(c for c in stop_candidates if c >= 0) if any(c >= 0 for c in stop_candidates) else len(flat)
                segment = flat[intro.end():stop]
                if _SHARE_NO_PERCENT_RE.search(segment):
                    continue  # 有占比漏写百分号，由 V33-DISCLOSURE-PERCENT-UNIT 报告，不再连带报合计
                raw_shares = [m.group(1) for m in _SHARE_RE.finditer(segment)]
                shares = [s for s in (_dec(r) for r in raw_shares) if s is not None]
                if len(shares) < 2 or all(s == 0 for s in shares):
                    continue
                if kind == "final" and "三公" in flat[max(0, intro.start() - 60):stop]:
                    continue  # 决算三公占比由 V33-224 按表复算
                total = sum(shares, Decimal("0"))
                tolerance = Decimal("0.1") + sum((display_half_unit(r) for r in raw_shares), Decimal(0))
                if abs(total - Decimal("100")) <= tolerance:
                    continue
                start, end = flat_span(positions, max(0, intro.start() - 20), min(len(flat), intro.end() + 30))
                issues.append(issue_at(
                    self, f"并列列示的各项占比合计为{total.normalize():f}%，应为100%"
                    f"（{'列示不全' if total < 100 else '占比计算有误'}，共{len(shares)}项）。",
                    page, text, start, end, severity="warn", checklist_item="C-08",
                    share_total=str(total), share_count=len(shares),
                ))
        return issues


# ---------------------------------------------------------------------------
# CMM-021 表名规范
# ---------------------------------------------------------------------------

_BUDGET_TABLE_NAMES = (
    "{p}财务收支预算总表", "{p}收入预算总表", "{p}支出预算总表", "{p}财政拨款收支预算总表",
    "{p}一般公共预算支出功能分类预算表", "{p}政府性基金预算支出功能分类预算表",
    "{p}国有资本经营预算支出功能分类预算表", "{p}一般公共预算基本支出部门预算经济分类预算表",
    "{p}三公经费和机关运行经费预算表",
)
_FINAL_TABLE_NAMES = (
    "收入支出决算总表", "收入决算表", "支出决算表", "财政拨款收入支出决算总表",
    "一般公共预算财政拨款支出决算表", "一般公共预算财政拨款基本支出决算表",
    "财政拨款三公经费支出决算表", "政府性基金预算财政拨款收入支出决算表",
    "国有资本经营预算财政拨款收入支出决算表",
)
#: 每张规范表的“核心词”，用来把正文表题对应到规范表（再逐字比对）。
_BUDGET_CORES = ("财务收支预算总表", "收入预算总表", "支出预算总表", "财政拨款收支预算总表",
                 "一般公共预算支出功能分类", "政府性基金预算支出功能分类", "国有资本经营预算支出功能分类",
                 "基本支出部门预算经济分类", "三公经费和机关运行经费")
_FINAL_CORES = ("收入支出决算总表", "收入决算表", "支出决算表", "财政拨款收入支出决算总表",
                "财政拨款支出决算表", "财政拨款基本支出决算表", "三公经费支出决算表",
                "政府性基金预算财政拨款", "国有资本经营预算财政拨款")


def _title_norm(text: str) -> str:
    return re.sub(r"[\s“”\"'‘’]", "", str(text or ""))


def table_title_lines(doc: Document) -> List[Tuple[int, str, int, int, str]]:
    """表格页顶部的表题行：(页码, 页文本, 起, 止, 规范化表题)。"""
    found: List[Tuple[int, str, int, int, str]] = []
    for page, text in iter_pages(doc):
        if not page_has_table(doc, page - 1):
            continue
        offset = 0
        lines = text.split("\n")
        for line in lines[:6]:
            start, offset = offset, offset + len(line) + 1
            norm = _title_norm(line)
            if norm.endswith("表") and re.search(r"(?:预算|决算)", norm) and len(norm) <= 48 \
                    and not norm.startswith(("注", "第")):
                found.append((page, text, start, start + len(line), norm))
                break
    return found


class CMM021_TableTitleNormative(Rule):
    code, severity = "CMM-021", "info"
    desc = "表名与规范表名一致（预/决算通用，清单 E-06）"

    def apply(self, doc: Document) -> List[Issue]:
        kind = report_kind(doc)
        if kind not in {"budget", "final"}:
            raise RuleNotApplicable(self.code, detail="文种未识别，规范表名无法确定")
        titles = table_title_lines(doc)
        if not titles:
            raise RuleNotApplicable(self.code, detail="未识别到表格页表题")
        if kind == "budget":
            scope = body_scope(doc) or subject_scope(doc)
            if scope not in {"department", "unit"}:
                raise RuleNotApplicable(self.code, detail="部门/单位口径未识别，预算表名前缀无法核对")
            prefix = "部门" if scope == "department" else "单位"
            names = [n.format(p=prefix) for n in _BUDGET_TABLE_NAMES]
            cores = _BUDGET_CORES
        else:
            names, cores = list(_FINAL_TABLE_NAMES), _FINAL_CORES
        mismatches: List[Tuple[int, str, int, int, str, str]] = []
        for page, text, start, end, norm in titles:
            body = re.sub(r"^20\d{2}年度?", "", norm)
            index = next((i for i, core in reversed(list(enumerate(cores))) if core in _title_norm(body)), None)
            if index is None:
                continue
            expected = names[index]
            if kind == "final" and index == 8 and body in {"国有资本经营预算财政拨款支出决算表"}:
                continue  # 清单 A-06 写法，视为规范
            if body != expected:
                mismatches.append((page, text, start, end, body, expected))
        if not mismatches:
            return []
        page, text, start, end, body, expected = mismatches[0]
        detail = "；".join(f"“{b}”应为“{e}”" for _, _, _, _, b, e in mismatches[:4])
        more = f"等 {len(mismatches)} 张" if len(mismatches) > 4 else ""
        return [issue_at(
            self, f"表名与规范表名不一致：{detail}{more}。",
            page, text, start, end, severity="info", checklist_item="E-06", occurrences=len(mismatches),
        )]


# ---------------------------------------------------------------------------
# CMM-022 科目编码
# ---------------------------------------------------------------------------

#: 部门预算支出经济分类科目：编码 → 名称。各地、各年度款级编号有差异（如上海市级
#: 决算表 30203 为“手续费”），所以只用类级（前三位：301 工资福利 / 302 商品和服务 /
#: 303 对个人和家庭的补助 / 310 资本性支出）反查“名称所属类别与编码类别是否一致”，
#: 款级编号不判。
ECONOMIC_CODES: Dict[str, str] = {
    "30101": "基本工资", "30102": "津贴补贴", "30103": "奖金", "30106": "伙食补助费", "30107": "绩效工资",
    "30108": "机关事业单位基本养老保险缴费", "30109": "职业年金缴费", "30110": "职工基本医疗保险缴费",
    "30111": "公务员医疗补助缴费", "30112": "其他社会保障缴费", "30113": "住房公积金", "30114": "医疗费",
    "30199": "其他工资福利支出", "30201": "办公费", "30202": "印刷费", "30203": "咨询费", "30204": "手续费",
    "30205": "水费", "30206": "电费", "30207": "邮电费", "30208": "取暖费", "30209": "物业管理费",
    "30211": "差旅费", "30212": "因公出国（境）费用", "30213": "维修（护）费", "30214": "租赁费",
    "30215": "会议费", "30216": "培训费", "30217": "公务接待费", "30218": "专用材料费", "30224": "被装购置费",
    "30225": "专用燃料费", "30226": "劳务费", "30227": "委托业务费", "30228": "工会经费", "30229": "福利费",
    "30231": "公务用车运行维护费", "30239": "其他交通费用", "30240": "税金及附加费用",
    "30299": "其他商品和服务支出", "30301": "离休费", "30302": "退休费", "30303": "退职（役）费",
    "30304": "抚恤金", "30305": "生活补助", "30306": "救济费", "30307": "医疗费补助", "30308": "助学金",
    "30309": "奖励金", "30310": "个人农业生产补贴", "30311": "代缴社会保险费", "30399": "其他对个人和家庭的补助",
    "31001": "房屋建筑物购建", "31002": "办公设备购置", "31003": "专用设备购置", "31005": "基础设施建设",
    "31006": "大型修缮", "31007": "信息网络及软件购置更新", "31008": "物资储备", "31009": "土地补偿",
    "31010": "安置补助", "31011": "地上附着物和青苗补偿", "31012": "拆迁补偿", "31013": "公务用车购置",
    "31019": "其他交通工具购置", "31021": "文物和陈列品购置", "31022": "无形资产购置",
    "31099": "其他资本性支出",
}
_ECONOMIC_BY_NAME = {re.sub(r"[（）()]", "", name): code for code, name in ECONOMIC_CODES.items()}


def _cell(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or ""))


def table_code_rows(row: Sequence[Any]) -> List[Tuple[str, List[str], str]]:
    """从一行单元格中解析 (完整编码, 编码分段, 科目名称)，兼容分列“类/款/项”与双侧表。"""
    cells = [_cell(c) for c in row]
    out: List[Tuple[str, List[str], str]] = []
    index = 0
    while index < len(cells):
        cell = cells[index]
        if re.fullmatch(r"\d{3}", cell):
            segments = [cell]
            probe = index + 1
            while probe < len(cells) and len(segments) < 3 and re.fullmatch(r"\d{1,2}", cells[probe]):
                segments.append(cells[probe])
                probe += 1
            while probe < len(cells) and cells[probe] == "":
                probe += 1
            name = cells[probe] if probe < len(cells) else ""
            if name and re.search(f"[{CJK}]", name) and not re.fullmatch(r"[\d,.\-]+", name):
                out.append(("".join(segments), segments, name))
            index = probe + 1
            continue
        if re.fullmatch(r"\d{5}|\d{7}", cell):
            probe = index + 1
            while probe < len(cells) and cells[probe] == "":
                probe += 1
            name = cells[probe] if probe < len(cells) else ""
            if name and re.search(f"[{CJK}]", name):
                out.append((cell, [cell[:3], *re.findall(r"\d{2}", cell[3:])], name))
            index = probe + 1
            continue
        index += 1
    return out


class CMM022_SubjectCodeFormat(Rule):
    code, severity = "CMM-022", "warn"
    desc = "科目编码格式与名称匹配、表内编码不重复（预/决算通用，清单 B-04）"

    def apply(self, doc: Document) -> List[Issue]:
        issues: List[Issue] = []
        texts = list(doc.page_texts or [])
        for page_index, tables in enumerate(getattr(doc, "page_tables", None) or []):
            text = str(texts[page_index] if page_index < len(texts) else "")
            for table in tables or []:
                seen: Dict[str, str] = {}
                for row in table or []:
                    for code, segments, name in table_code_rows(row):
                        quote_start = self._locate(text, segments, name)
                        if quote_start is None:
                            continue
                        start, end = quote_start
                        if len(segments) > 1 and any(len(seg) == 1 for seg in segments[1:]):
                            fixed = " ".join([segments[0]] + [seg.zfill(2) for seg in segments[1:]])
                            issues.append(issue_at(
                                self, f"科目编码缺前导0：“{' '.join(segments)} {name}”应为“{fixed} {name}”。",
                                page_index + 1, text, start, end, severity="warn", checklist_item="B-04",
                            ))
                            continue
                        plain = re.sub(r"[（）()]", "", name)
                        expected = _ECONOMIC_BY_NAME.get(plain)
                        if expected and len(code) == 5 and code.startswith("3") and code[:3] != expected[:3]:
                            issues.append(issue_at(
                                self, f"经济分类科目编码类别与名称不匹配：“{' '.join(segments)} {name}”属于"
                                f"“{expected[:3]}”类（如 {expected[:3]} {expected[3:]}），编码类别写成了“{code[:3]}”。",
                                page_index + 1, text, start, end, severity="warn", checklist_item="B-04",
                            ))
                        previous = seen.get(code)
                        if previous is not None and previous != name:
                            issues.append(issue_at(
                                self, f"同一张表内科目编码重复：“{code}”同时对应“{previous}”与“{name}”。",
                                page_index + 1, text, start, end, severity="warn", checklist_item="B-04",
                            ))
                        seen.setdefault(code, name)
        return issues

    @staticmethod
    def _locate(text: str, segments: Sequence[str], name: str) -> Optional[Tuple[int, int]]:
        """原页文本中“编码…名称”的位置（名称可能被单元格折行，只取前 4 个字）。"""
        pattern = r"\s*".join(re.escape(seg) for seg in segments) + r"\s*" + r"\s*".join(map(re.escape, name[:4]))
        match = re.search(pattern, text)
        return (match.start(), match.end()) if match else None


# ---------------------------------------------------------------------------
# CMM-023 章节模板
# ---------------------------------------------------------------------------

#: (清单条目, 章节名, 关键词正则, 适用口径)。关键词兼容各地模板常见写法（“职能/职责”“机构设置/
#: 单位构成”“国有资产占有/占用”），只在整份材料里一处都找不到时才报缺失。
_BUDGET_SECTIONS: Sequence[Tuple[str, str, str, Optional[str]]] = (
    ("A-03", "主要职能", r"主要职能|单位职责|部门职责|职能职责|主要职责", None),
    ("A-04", "机构设置", r"机构设置|单位构成|机构情况", None),
    ("A-01", "名词解释", r"名词解释", None),
    ("A-11", "政府采购预算情况", r"政府采购", None),
    ("A-12", "绩效目标设置情况", r"绩效目标|绩效管理", None),
    ("A-08", "国有资产占有使用情况", r"国有资产占有使用|国有资产占用|国有资产使用", None),
    ("A-01", "项目经费情况说明", r"项目经费情况说明", "department"),
)
_FINAL_SECTIONS: Sequence[Tuple[str, str, str, Optional[str]]] = (
    ("A-03", "主要职能", r"主要职能|单位职责|部门职责|主要职责", None),
    ("A-04", "机构设置/部门决算单位构成", r"机构设置|单位构成", None),
    ("A-01", "名词解释", r"名词解释", None),
    ("A-11", "国有资产占有使用情况", r"国有资产占有使用|国有资产占用|国有资产使用", None),
    ("A-12", "政府采购支出情况", r"政府采购", None),
)
_FINAL_PARTS: Sequence[Tuple[str, str]] = (
    ("第一部分", r"概况"), ("第二部分", r"决算表"), ("第三部分", r"决算情况说明|情况说明"), ("第四部分", r"名词解释"),
)


class CMM023_SectionTemplate(Rule):
    code, severity = "CMM-023", "warn"
    desc = "报告结构与章节模板齐全（预/决算通用，清单 A-01/A-03/A-04/A-08/A-11/A-12）"

    def apply(self, doc: Document) -> List[Issue]:
        kind = report_kind(doc)
        if kind not in {"budget", "final"}:
            raise RuleNotApplicable(self.code, detail="文种未识别，章节模板无法确定")
        texts = list(doc.page_texts or [])
        if len(texts) < 4 or not any(str(t or "").strip() for t in texts):
            raise RuleNotApplicable(self.code, detail="材料页数过少或无文本层，章节结构由 BUD-001/V33-003 判定")
        body = compact("\n".join(str(t or "") for t in texts[1:]))
        scope = body_scope(doc) or subject_scope(doc)
        cover = str(texts[0] or "")
        first = next((ln for ln in cover.split("\n") if ln.strip()), "")
        issues: List[Issue] = []

        def _missing(item: str, message: str) -> None:
            issues.append(issue_at(self, message, 1, cover, 0, len(first), severity="warn",
                                   checklist_item=item, document_level=True))

        if len(texts) >= 8 and not any(is_toc_page(t) for t in texts[:6]):
            _missing("A-05", "报告缺少目录（目录缺失或空白）。")
        sections = _BUDGET_SECTIONS if kind == "budget" else _FINAL_SECTIONS
        for item, title, pattern, only_scope in sections:
            if only_scope and scope != only_scope:
                continue
            if not re.search(pattern, body):
                _missing(item, f"报告缺少“{title}”章节（标题及内容均未找到）。")
        if kind == "budget" and scope == "unit" and re.search(r"项目经费情况说明", body):
            page = next((i for i, t in enumerate(texts, 1) if "项目经费情况说明" in compact(t) and i > 1), None)
            if page:
                text = texts[page - 1]
                raw = text.find("项目经费")
                issues.append(issue_at(
                    self, "单位预算不应包含“项目经费情况说明”（该部分为部门预算专有内容）。",
                    page, text, max(raw, 0), max(raw, 0) + 8, severity="warn", checklist_item="A-01",
                ))
        if kind == "final":
            for part, pattern in _FINAL_PARTS:
                if not re.search(rf"{part}[^。；]{{0,60}}?(?:{pattern})", body):
                    _missing("A-01", f"报告缺少“{part}”（标题或“{part}”字样缺失）。")
        return issues


# ---------------------------------------------------------------------------
# CMM-024 说明中的构成项加总
# ---------------------------------------------------------------------------

#: (总额正则, 构成项正则, 名称)。构成项只认同一维度的已知项目，避免把增减额、
#: 占比或“主要包括”的部分列举当作全部分项。
_COMPOSITIONS: Sequence[Tuple[re.Pattern[str], re.Pattern[str], str]] = (
    (
        re.compile(r"(?:本年收入合计|收入预算|收入总预算)(?:为|共|合计)?" + _AMOUNT + r"万元[，,]?(?:其中|包括)[:：]"),
        re.compile(r"(?P<label>财政拨款收入|一般公共预算(?:财政)?拨款收入|一般公共预算收入|政府性基金(?:预算)?(?:财政)?拨款收入"
                   r"|政府性基金预算收入|国有资本经营预算(?:财政)?(?:拨款)?收入|上级补助收入|事业收入|事业单位经营收入|经营收入"
                   r"|附属单位上缴收入|其他收入|上年结转(?:收入)?|使用非财政拨款结余)(?:为|共)?" + _AMOUNT + r"万元"),
        "收入构成",
    ),
    (
        re.compile(r"(?:本年支出合计|支出预算|支出总预算)(?:为|共|合计)?" + _AMOUNT + r"万元[，,]?(?:其中|包括)[:：]"),
        re.compile(r"(?P<label>基本支出|项目支出|上缴上级支出|经营支出|对附属单位补助支出|结转下年)(?:为|共)?" + _AMOUNT + r"万元"),
        "支出构成",
    ),
    (
        re.compile(r"财政拨款支出预算(?:为|共)?" + _AMOUNT + r"万元[^。]{0,60}?。?财政拨款支出预算中[，,]"),
        re.compile(r"(?P<label>一般公共预算拨款支出预算|政府性基金拨款支出预算|政府性基金预算拨款支出预算"
                   r"|国有资本经营预算拨款支出预算)(?:为|共)?" + _AMOUNT + r"万元"),
        "财政拨款支出构成",
    ),
)


def _composition_items(segment: str, item_re: "re.Pattern[str]") -> List[Tuple[str, Decimal, str]]:
    items: List[Tuple[str, Decimal, str]] = []
    labels: set = set()
    for match in item_re.finditer(segment):
        before = segment[max(0, match.start() - 4):match.start()]
        if re.search(r"比|较|增加|减少|占", before):
            continue
        label = match.group("label")
        if label in labels:
            continue
        value = _dec(match.group(2))
        if value is not None:
            labels.add(label)
            items.append((label, value, match.group(2)))
    return items


class CMM024_CompositionSum(Rule):
    code, severity = "CMM-024", "warn"
    desc = "说明中总额与所列构成项加总一致（预/决算通用，清单 C-10）"

    def apply(self, doc: Document) -> List[Issue]:
        issues: List[Issue] = []
        for page, text in iter_pages(doc):
            flat, positions = page_flat(doc, page, text)
            # 全角千分位逗号等格式错误由 CMM-010 报告；这里逐字替换（长度不变）后再加总，避免连带误报
            flat = normalize_amount_punctuation(flat)
            for total_re, item_re, label in _COMPOSITIONS:
                for head in total_re.finditer(flat):
                    total = _dec(head.group(1))
                    if total is None:
                        continue
                    stop = flat.find("。", head.end())
                    if label == "财政拨款支出构成":
                        stop = flat.find("。", head.end())
                        nxt = re.search(r"财政拨款收入支出|主要内容", flat[head.end():])
                        if nxt:
                            stop = head.end() + nxt.start()
                    segment = flat[head.end(): stop if stop > 0 else len(flat)]
                    items = _composition_items(segment, item_re)
                    if len(items) < 2:
                        continue
                    subtotal = sum((v for _, v, _ in items), Decimal("0"))
                    if abs(subtotal - total) <= display_envelope([head.group(1), *(r for _, _, r in items)]):
                        continue
                    start, end = flat_span(positions, head.start(), head.end())
                    listing = "、".join(f"{n}{v}万元" for n, v, _ in items[:6])
                    issues.append(issue_at(
                        self, f"{label}加总不一致：总额{total}万元，所列分项（{listing}）合计{subtotal}万元，"
                        f"相差{abs(subtotal - total)}万元。",
                        page, text, start, end, severity="warn", checklist_item="C-10",
                        expected=str(total), actual=str(subtotal),
                    ))
        return issues


# ---------------------------------------------------------------------------
# CMM-025 绩效项目数为 0 时金额应为 0
# ---------------------------------------------------------------------------

_PERF_ZERO_RE = re.compile(
    r"(?P<what>(?:编报绩效目标|绩效跟踪评价|绩效自评|绩效评价)[^，,；;。]{0,12}?)项目\s*0\s*个[，,、]?\s*"
    r"(?:共计|合计)?涉及(?:项目)?(?:预算)?(?:金额|资金)\s*" + _AMOUNT + r"\s*万元"
)


class CMM025_PerformanceZeroCount(Rule):
    code, severity = "CMM-025", "warn"
    desc = "绩效项目数为0时涉及金额应为0（预/决算通用，清单决算 B-09）"

    def apply(self, doc: Document) -> List[Issue]:
        issues: List[Issue] = []
        for page, text in iter_pages(doc):
            flat, positions = page_flat(doc, page, text)
            for match in _PERF_ZERO_RE.finditer(flat):
                amount = _dec(match.group(2))
                if amount is None or amount == 0:
                    continue
                start, end = flat_span(positions, match.start(), match.end())
                issues.append(issue_at(
                    self, f"绩效数量与金额逻辑矛盾：{match.group('what')}项目为0个，涉及金额却为{amount}万元。",
                    page, text, start, end, severity="warn", checklist_item="B-09",
                ))
        return issues


__all__ = [
    "CMM020_ShareSum",
    "CMM021_TableTitleNormative",
    "CMM022_SubjectCodeFormat",
    "CMM023_SectionTemplate",
    "CMM024_CompositionSum",
    "CMM025_PerformanceZeroCount",
    "ECONOMIC_CODES",
    "table_code_rows",
]
