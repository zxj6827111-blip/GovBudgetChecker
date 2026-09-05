"""说明文本归因基础设施（P1 说明归因重构的公共层）。

解决 docs/SYSTEM_ISSUES_HANDOFF_2026-09-05.md §3.2 的根因：
- PDF 软换行把「决算」拆成「决+换行+算」、把金额与主体拆行，导致
  `因公出国.*?决算.*?数字+万元` 之类的跨行正则错配（V33-244×2 误报根因）；
- `near_number` 用 `[^0-9]*?` 跨章节抓数字，把「2025年度」的年份当成金额
  （V33-106 误报根因）、跨句配对预算/决算数（V33-110 误报根因）。

提供四层能力（规则统一从这里取用，禁止规则内再写跨段 `.*?` 数字正则）：
1. 软换行恢复：``merge_soft_wrapped_lines`` 把 PDF 逐行文本还原成逻辑段落；
2. 章节切分：``split_numbered_sections`` 按中文序号标题切分说明章节；
3. 句子/分句切分：``split_clauses``；
4. 金额抽取：``extract_amounts`` 只认「数字+万元/亿元/元」组合，
   自动排除年份 token；``extract_three_public_facts`` 产出
   {subject, metric, period, amount, unit, source_span} 结构。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# 行尾若为句末标点，说明句子已完整，下一行是新句/新段
_SENTENCE_ENDINGS = "。；：！？!?:;"

# 新逻辑段特征：中文序号、括号序号、阿拉伯序号、表题。
# 注意括号序号必须要求括号成对，不能写成 \(?[一二三四五六七八九十]+\)?——
# 那会匹配任何以中文数字开头的行（如「五五”规划》…」）。
_NEW_PARAGRAPH_RE = re.compile(
    r"^(?:[一二三四五六七八九十]+[、.．]"
    r"|[(（][一二三四五六七八九十]+[)）]"
    r"|\(?\d{1,3}\)?[、.．]"
    r"|\d+\.\d+"
    r"|\d+、)"
)

# 金额：数字（含千分位）+ 可选小数 + 单位；要求单位紧随其后，
# 从根上排除「2024年度」这类无单位数字
_AMOUNT_RE = re.compile(
    r"(?<![\d.])(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?\s*(亿元|万元|元)"
)

# 年份 token：出现在 20xx 年/年度 上下文中的数字一律不作金额
_YEAR_CONTEXT_RE = re.compile(r"(20\d{2})\s*(?:年度|年)")


def merge_soft_wrapped_lines(page_text: str) -> List[str]:
    """把 PDF 软换行合并成逻辑段落。

    合并规则：行尾不是句末标点、且下一行不是新逻辑段起点时，视为软换行
    拼接（决算说明是两端对齐正文，断行位置与语义无关）。
    """
    paragraphs: List[str] = []
    buffer = ""
    for raw_line in str(page_text or "").splitlines():
        line = raw_line.strip()
        if not line:
            if buffer:
                paragraphs.append(buffer)
                buffer = ""
            continue
        if buffer and (
            buffer[-1] in _SENTENCE_ENDINGS
            or _NEW_PARAGRAPH_RE.match(line)
        ):
            paragraphs.append(buffer)
            buffer = line
        else:
            buffer = buffer + line if buffer else line
    if buffer:
        paragraphs.append(buffer)
    return paragraphs


def merge_page_texts(page_texts: List[str]) -> str:
    """恢复软换行后的全文（按页合并段落，段落间保留换行）。

    跨页断句不做暴力拼接：页与页之间一律视为新段落边界，
    避免把上一页末句和下一页首句黏成一句造成跨页错配。
    """
    merged: List[str] = []
    for page_text in page_texts or []:
        merged.extend(merge_soft_wrapped_lines(page_text))
    return "\n".join(merged)


def split_clauses(text: str) -> List[str]:
    """把段落切成最小对齐单元。

    分隔符：句末标点（。；?!）、顿号/逗号（，、）与换行。
    说明句式「年初预算为21.00万元，支出决算为16.95万元，其中：因公出国
    （境）费决算为0.00万元」里每个金额分句以逗号分隔——不在逗号处切开
    就无法做主体-金额一一配对。
    """
    return [c.strip() for c in re.split(r"[。；;！!？?\n，、]", str(text or "")) if c.strip()]


_SECTION_TITLE_RE = re.compile(
    r"(?:^|\n)\s*(?:[（(][一二三四五六七八九十]+[)）]|[一二三四五六七八九十]+、|\d{1,2}、)"
    r"[^。；;！!？?\n]{0,60}(?:说明|情况)[^。；;！!？?\n]{0,10}"
)


def split_numbered_sections(text: str) -> List[Tuple[str, str, int]]:
    """按中文序号标题切分说明章节。

    返回 [(section_title, section_body, char_offset)]；offset 是标题在
    原文中的位置，供页码/定位回溯。
    """
    text = str(text or "")
    matches = list(_SECTION_TITLE_RE.finditer(text))
    sections: List[Tuple[str, str, int]] = []
    for i, match in enumerate(matches):
        start = match.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body_start = match.end()
        sections.append(
            (match.group(0).strip(), text[body_start:end], start)
        )
    return sections


def find_section(text: str, keywords: List[str]) -> Optional[Tuple[str, str]]:
    """按关键词找章节，返回 (标题, 正文)。找不到返回 None。"""
    for title, body, _offset in split_numbered_sections(text):
        if all(kw in title for kw in keywords):
            return title, body
    return None


def extract_amounts(clause: str) -> List[Tuple[float, str]]:
    """从分句中抽取金额，返回 [(amount_in_wan, unit)]。

    - 只认「数字+单位」组合，单位缺省时不要（裸数字多为年份/编号/百分比）；
    - 「20xx年度/年」上下文中的数字不算金额；
    - 单位换算统一到万元（元=0.0001，亿元=10000）。
    """
    clause = str(clause or "")
    amounts: List[Tuple[float, str]] = []
    for match in _AMOUNT_RE.finditer(clause):
        # 排除年份上下文：数字紧邻 20xx 年/年度 时跳过
        prefix = clause[max(0, match.start() - 6): match.start()]
        if _YEAR_CONTEXT_RE.search(prefix + match.group(0)):
            continue
        value = float(match.group(1).replace(",", ""))
        decimals = match.group(2)
        if decimals:
            value += float(decimals)
        unit = match.group(3)
        if unit == "亿元":
            value *= 10000
        elif unit == "元":
            value *= 0.0001
        amounts.append((round(value, 6), unit))
    return amounts


@dataclass
class NarrationFact:
    """说明文本归因出的结构化事实。"""

    subject: str                    # 主体：三公经费合计/因公出国/公务用车/公务接待/…
    metric: str                     # 口径：预算/决算/增加额/…
    period: str = ""                # 期间：本年/2024（同比语境）等
    amount: Optional[float] = None  # 单位：万元
    unit: str = "万元"
    source_span: Tuple[int, int] = (0, 0)   # 在合并后文本中的位置
    page: Optional[int] = None
    raw: str = ""
    tags: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, object]:
        return {
            "subject": self.subject,
            "metric": self.metric,
            "period": self.period,
            "amount": self.amount,
            "unit": self.unit,
            "source_span": list(self.source_span),
            "page": self.page,
            "raw": self.raw[:120],
        }


# 三公主体识别词（顺序即优先级，先长后短避免「公务用车购置」误判）
_THREE_PUBLIC_SUBJECTS: List[Tuple[str, str]] = [
    ("三公", "三公经费合计"),
    ("因公出国", "因公出国（境）费"),
    ("公务用车购置及运行维护", "公务用车购置及运行维护费"),
    ("公务用车运行维护", "公务用车运行维护费"),
    ("公务用车购置", "公务用车购置费"),
    ("公务用车", "公务用车购置及运行维护费"),
    ("公务接待", "公务接待费"),
]

_DIRECTION_UP = ("增加", "增长", "高于", "超出", "超过")
_DIRECTION_DOWN = ("减少", "下降", "低于", "少于")
_DIRECTION_FLAT = ("持平", "与上年一致", "与上年同期一致")


def extract_three_public_facts(
    section_text: str,
    page: Optional[int] = None,
    offset: int = 0,
) -> List[NarrationFact]:
    """从三公经费说明段落抽取 {subject, metric, period, amount} 事实。

    关键机制：
    - **主体沿分句向下继承**：「支出决算为16.95万元」这类无主体分句继承
      上一分句的主体（三公合计），否则合计值会错配给下一个分项；
    - **基数句与变化句分开**：含「增加/减少」的分句是同比变化句（如
      「决算数比2024年度增加5.07万元」），其金额不是决算基数，
      metric 记为 决算变化/预算变化，表文对比时不得使用（V33-244
      把同比增加额 5.07 错配给公务接待的误报根因）。
    """
    facts: List[NarrationFact] = []
    current_subject = ""
    for para in merge_soft_wrapped_lines(section_text or ""):
        current_subject = ""
        for clause in split_clauses(para):
            subject = current_subject
            subject_hit = False
            for token, name in _THREE_PUBLIC_SUBJECTS:
                if token in clause:
                    subject = name
                    subject_hit = True
                    break
            if subject_hit:
                current_subject = subject
            elif _NEW_PARAGRAPH_RE.match(clause):
                current_subject = ""

            if not subject:
                continue

            # 口径判定：决算 > 预算 > 支出（决算/执行语境）
            if "决算" in clause:
                base_metric = "决算"
            elif "预算" in clause:
                base_metric = "预算"
            elif "支出" in clause:
                base_metric = "决算"
            else:
                base_metric = ""

            # 变化句判定：同比方向词（「无增长/零增长」等否定语境除外）
            direction = clause_direction(clause)
            if direction and re.search(r"[无未零]\s*(增|减|增减|增长|减少)", clause):
                direction = None

            amounts = extract_amounts(clause)
            if not amounts or not base_metric:
                continue

            metric = f"{base_metric}变化" if direction else base_metric
            value, unit = amounts[0]
            facts.append(
                NarrationFact(
                    subject=subject,
                    metric=metric,
                    period="同比" if direction else "本年",
                    amount=value,
                    unit=unit,
                    source_span=(offset, offset + len(clause)),
                    page=page,
                    raw=clause,
                )
            )
    return facts


def clause_direction(clause: str) -> Optional[str]:
    """判断分句的方向表述：up / down / flat / None。"""
    if any(word in clause for word in _DIRECTION_FLAT):
        return "flat"
    if any(word in clause for word in _DIRECTION_UP):
        return "up"
    if any(word in clause for word in _DIRECTION_DOWN):
        return "down"
    return None


def amount_in_section(
    section_body: str,
    anchor_keywords: List[str],
    *,
    exclude_keywords: List[str] = None,
) -> Optional[float]:
    """在章节正文中按口径锚点取金额（年份安全、句子内配对）。

    只在包含锚点关键词的分句（及其紧邻分句）里取「数字+单位」，
    并可用 exclude_keywords 排除预算/同比分句——禁止跨句抓数的
    `near_number` 语义由此替代（V33-106 年份误配、V33-110 跨句
    配对的根因都是无边界抓取）。
    """
    exclude_keywords = exclude_keywords or []
    clauses = split_clauses(section_body)
    for i, clause in enumerate(clauses):
        if not any(kw in clause for kw in anchor_keywords):
            continue
        if any(kw in clause for kw in exclude_keywords):
            continue
        amounts = extract_amounts(clause)
        if amounts:
            return amounts[0][0]
        # 锚点分句自身无金额时，看紧邻的下一分句（说明句式常见
        # 「总体情况说明」标题行后紧跟金额句）
        if i + 1 < len(clauses):
            nxt = clauses[i + 1]
            if not any(kw in nxt for kw in exclude_keywords):
                amounts = extract_amounts(nxt)
                if amounts:
                    return amounts[0][0]
    return None
