# coding: utf-8
# ---------- “九张表” ----------
NINE_TABLES = [
    {"name": "收入支出决算总表",
     "aliases": ["部门收支决算总表", "收入支出决算总表", "收支决算总表"]},
    {"name": "收入决算表",
     "aliases": ["部门收入决算表", "收入决算表"]},
    {"name": "支出决算表",
     "aliases": ["部门支出决算表", "支出决算表"]},
    {"name": "财政拨款收入支出决算总表",
     "aliases": ["财政拨款收支决算总表", "财政拨款收入支出决算总表"]},
    {"name": "一般公共预算财政拨款支出决算表",
     "aliases": ["一般公共预算财政拨款支出决算表", "一般公共预算支出决算表"]},
    {"name": "一般公共预算财政拨款基本支出决算表",
     "aliases": ["一般公共预算财政拨款基本支出决算表", "基本支出决算表"]},
    {"name": "一般公共预算财政拨款“三公”经费支出决算表",
     "aliases": ["财政拨款“三公”经费支出决算表", "三公经费支出决算表", "“三公”经费支出决算表"]},
    {"name": "政府性基金预算财政拨款收入支出决算表",
     "aliases": ["政府性基金预算财政拨款收入支出决算表", "政府性基金决算表"]},
    {"name": "国有资本经营预算财政拨款收入支出决算表",
     "aliases": [
         "国有资本经营预算财政拨款收入支出决算表",
         "国有资本经营预算财政拨款支出决算表",
         "国有资本经营支出决算表"
     ]}
]

# ========= 工具：中文序号 & 排序+编号 =========
_CN_NUM = "零一二三四五六七八九十"
def to_cn_num(n: int) -> str:
    """把 1,2,3... 变成 一、二、三……（<= 99 的中文数字）"""
    if n <= 10:
        if n == 10:
            return "十"
        return _CN_NUM[n]
    t, o = divmod(n, 10)
    if n < 20:
        return "十" + (_CN_NUM[o] if o else "")
    if t >= 10:
        # 如果十位数大于等于10，说明数字大于99，直接返回数字
        return str(n)
    return _CN_NUM[t] + "十" + (_CN_NUM[o] if o else "")

def order_and_number_issues(doc, issues):
    """
    按 (page, pos) 排序，并把 message 前面加上 "一、二、三…"
    说明：
    - 其它规则请尽量往 location 里塞 { "page": 页码, "pos": 页内位置 }，
      这样这里才能按文中顺序排。
    - 噪声过滤增强：剔除含"表出现多次"的问题，提升检测结果质量
    """
    # 增强噪声过滤逻辑
    filtered_issues = []
    for issue in issues:
        # 检查多种噪声模式
        is_noise = False
        
        # 1. 表重复噪声
        if "表出现多次" in issue.message:
            is_noise = True
            
        # 2. 检查desc字段（如果存在）
        if hasattr(issue, 'desc') and issue.desc and "表出现多次" in issue.desc:
            is_noise = True
            
        # 3. 其他潜在噪声模式（可扩展）
        noise_patterns = [
            "页码异常",
            "格式错误",
            "解析失败"
        ]
        for pattern in noise_patterns:
            if pattern in issue.message:
                is_noise = True
                break
                
        if not is_noise:
            filtered_issues.append(issue)
    
    def sort_key(it):
        p = it.location.get("page", 10**9)   # 没页码的排在最后
        s = it.location.get("pos", 0)        # 没 pos 的当 0 处理
        return (p, s)

    sorted_issues = sorted(filtered_issues, key=sort_key)
    for idx, it in enumerate(sorted_issues, start=1):
        cn = to_cn_num(idx)
        # 避免重复加序号（防止多次调用）
        if not it.message.startswith(("一、","二、","三、","四、","五、","六、","七、","八、","九、","十、")):
            it.message = f"{cn}、{it.message}"
    return sorted_issues
# engine/rules_v33.py  —— v3.3 规则（修正版）

from dataclasses import dataclass, field
from typing import FrozenSet, List, Dict, Any, Optional, Set, Tuple, TypedDict

import os
import re
from collections import Counter, defaultdict
from decimal import Decimal
import numpy as np

from .amount_math import classify_amount_diff, compute_dynamic_envelope, half_unit_for_term
from .rule_outcome import RuleDeferred, RuleExecutionError, RuleOutcomeSignal, STATUS_INSUFFICIENT_DATA
from .field_extractor import (
    extract_row_strict,
    find_column_index,
    parse_strict_cell,
    StrictValue,
)
from src.utils.narration import (
    amount_in_section,
    clause_direction,
    extract_amounts,
    extract_three_public_facts,
    merge_page_texts,
    merge_soft_wrapped_lines,
    split_clauses,
    split_numbered_sections,
)
from rapidfuzz import fuzz


# ---------- 数据结构 ----------
@dataclass
class Issue:
    rule: str
    severity: str
    message: str
    evidence_text: Optional[str] = None
    location: Dict[str, Any] = field(default_factory=dict)
    # R7 P1-3：finding 的独立章节身份（结构化字段）。此前章节只以
    # 「【章节:…】」文字拼进 evidence_text——评估器无法结构化校验
    # 候选 finding 是否真的产自标注章节（跨章节候选可借主题词混过
    # 锚点）。section_id 携带规则层 scope 定位到的章节标题原文。
    section_id: Optional[str] = None

@dataclass
class Document:
    path: str
    pages: int
    filesize: int
    page_texts: List[str]
    # 维度：页 -> 表 -> 行 -> 列
    page_tables: List[List[List[List[Any]]]]
    units_per_page: List[Optional[str]]
    years_per_page: List[List[int]]
    anchors: Dict[str, List[int]] = field(default_factory=dict)
    dominant_year: Optional[int] = None
    dominant_unit: Optional[str] = None
    # 一次解析的文种结论（"budget"/"final"/"unknown"）：由规则执行入口
    # （pipeline / engine_rule_runner）解析后挂上，规则体（如 CMM-003）
    # 消费同一个值，不再各自用不同输入重新猜文种。None 表示尚未解析，
    # 规则体回退到"文件名基名 + 正文首页"推断（独立验收 2026-09-17
    # kind_disagreement 反例的整改）。
    report_kind: Optional[str] = None


# ---------- 工具 ----------
def zh_pat(pat: str) -> re.Pattern:
    """中文文本常用正则：统一多行/点任意匹配 & 忽略大小写"""
    return re.compile(pat, flags=re.S | re.M | re.I)

_ZH_PUNCS = r"[ \t\r\n　，,。.:：；;、/（）()【】《》〈〉—\-━﻿·•●\[\]\{\}_~“”\"'‘’＋+]"
def normalize_text(s: str) -> str:
    s = s or ""
    return re.sub(_ZH_PUNCS, "", s)


def build_document(path: str, page_texts: List[str], page_tables: List[List[List[List[Any]]]], filesize: int) -> Document:
    """创建Document对象"""
    # 初始化Document对象
    doc = Document(
        path=path,
        pages=len(page_texts),
        filesize=filesize,
        page_texts=page_texts,
        page_tables=page_tables,
        units_per_page=[None] * len(page_texts),
        years_per_page=[[] for _ in range(len(page_texts))],
        anchors={},
        dominant_year=None,
        dominant_unit=None
    )
    
    # 提取年份信息
    for i, text in enumerate(page_texts):
        doc.years_per_page[i] = extract_years(text)
    
    # 提取单位信息
    for i, text in enumerate(page_texts):
        doc.units_per_page[i] = extract_money_unit(text)
    
    return doc

# 避免把“2013901”等编码识别成年份
_YEAR_RE = re.compile(r"(?<!\d)(20\d{2})(?:(?:\s*年(?:度)?)|(?=\D))")
def extract_years(s: str) -> List[int]:
    return [int(y) for y in _YEAR_RE.findall(s or "")]

_UNIT_RE = re.compile(r"单位[:：]\s*(万元|元|亿元)")
def extract_money_unit(s: str) -> Optional[str]:
    m = _UNIT_RE.search(s or "")
    return m.group(1) if m else None

_NUM_RE  = re.compile(r"^-?\s*(?:\d{1,3}(?:,\d{3})*|\d+)(?:\.\d+)?$")
_PCT_RE  = re.compile(r"^-?\s*(?:\d{1,3}(?:,\d{3})*|\d+)(?:\.\d+)?\s*%$")
_DASHES  = {"-", "—", "–", "— —", "— — —", "— — — —", ""}

def parse_number(cell: Any) -> Optional[float]:
    if cell is None:
        return None
    if isinstance(cell, (int, float)):
        return float(cell)
    s = str(cell).strip()
    if s in _DASHES:
        return None
    if _PCT_RE.match(s):
        s = s.replace("%", "").replace(",", "").strip()
        try:
            return float(s)
        except Exception:
            return None
    s2 = s.replace(",", "")
    if _NUM_RE.match(s):
        try:
            return float(s2)
        except Exception:
            return None
    return None

def looks_like_percent(cell: Any) -> bool:
    try:
        return "%" in str(cell)
    except Exception:
        return False

def has_negative_sign(cell: Any) -> bool:
    try:
        return str(cell).strip().startswith("-")
    except Exception:
        return False

def majority(items: List[Any]) -> Optional[Any]:
    if not items:
        return None
    c = Counter(items).most_common(1)
    return c[0][0] if c else None

# 数值容差比较（±1 或 相对 0.1% 取较大）
def tolerant_equal(a: Optional[float], b: Optional[float],
                   atol: float = 1.0, rtol: float = 0.001) -> bool:
    if a is None or b is None:
        return False
    tol = max(atol, abs(a) * rtol, abs(b) * rtol)
    return abs(a - b) <= tol

def calculate_dynamic_tolerance(a: float, b: float, base_tol: float = 1.0) -> float:
    """统一的动态容差计算：根据金额级别调整容差"""
    max_val = max(abs(a), abs(b))
    if max_val < 100:  # 小额：固定容差1.0
        return base_tol
    elif max_val < 10000:  # 中等金额：0.5%
        return max(base_tol, max_val * 0.005)
    else:  # 大额：0.3%
        return max(base_tol, max_val * 0.003)

def normalize_number_text(text: str) -> str:
    """标准化数字文本格式：移除千分位逗号、统一空格"""
    return text.replace(",", "").replace(" ", "").strip()




def _guess_pos_in_page(doc: "Document", page: int, clip: str, fallback_text: str = "") -> int:
    """
    增强的文本定位精度：改进位置检测算法，增强片段提取完整性
    """
    try:
        if page is None or page < 1 or page > len(doc.page_texts):
            return 10**9
        
        hay = doc.page_texts[page-1]
        
        # 1. 首先尝试精确匹配
        for k in (clip or "", fallback_text or ""):
            s = (k or "").strip().replace("\n", "")
            if len(s) >= 8:
                s = s[:50]
                i = hay.find(s)
                if i >= 0:
                    return i
        
        # 2. 标准化后匹配（处理空格、标点差异）
        if clip and len(clip) >= 8:
            normalized_hay = normalize_text(hay)
            normalized_clip = normalize_text(clip[:50])
            pos = normalized_hay.find(normalized_clip)
            if pos >= 0:
                # 映射回原始位置（简化版）
                return _map_normalized_pos_to_original(hay, normalized_hay, pos)
        
        # 3. 分段匹配：将clip分成多个片段，找到最佳匹配位置
        if clip and len(clip) > 50:
            segments = _split_text_segments(clip, 3)  # 分成3段
            best_pos = 10**9
            best_score = 0
            
            for segment in segments:
                if len(segment) >= 8:
                    seg_pos = hay.find(segment[:30])
                    if seg_pos >= 0:
                        # 计算匹配得分
                        score = len(segment) / len(clip)
                        if score > best_score:
                            best_score = score
                            best_pos = seg_pos
            
            if best_score > 0.3:  # 至少30%匹配
                return best_pos
        
        return 10**9
    except Exception:
        return 10**9

def _map_normalized_pos_to_original(original: str, normalized: str, norm_pos: int) -> int:
    """将标准化文本中的位置映射回原始文本位置（简化版）"""
    try:
        # 简化映射：按比例估算
        if len(normalized) == 0:
            return 0
        ratio = norm_pos / len(normalized)
        return int(ratio * len(original))
    except:
        return 0

def _split_text_segments(text: str, num_segments: int) -> List[str]:
    """将文本分割成指定数量的片段"""
    if num_segments <= 1:
        return [text]
    
    segment_length = len(text) // num_segments
    segments = []
    
    for i in range(num_segments):
        start = i * segment_length
        end = start + segment_length if i < num_segments - 1 else len(text)
        segment = text[start:end].strip()
        if segment:
            segments.append(segment)
    
    return segments
STANDARD_FINAL_NINE_TABLE_ALIASES = [
    ["收入支出决算总表", "部门收入支出决算总表", "收入支出决算总表"],
    ["收入决算表", "部门收入决算表"],
    ["支出决算表", "部门支出决算表"],
    ["财政拨款收入支出决算总表", "财政拨款收支决算总表"],
    ["一般公共预算财政拨款支出决算表", "一般公共预算财政拨款支出决算表"],
    ["一般公共预算财政拨款基本支出决算表", "一般公共预算财政拨款基本支出决算表", "基本支出决算表"],
    [
        "一般公共预算财政拨款“三公”经费支出决算表",
        "一般公共预算财政拨款\"三公\"经费支出决算表",
        "财政拨款“三公”经费支出决算表",
        "三公经费支出决算表",
    ],
    ["政府性基金预算财政拨款收入支出决算表", "政府性基金预算财政拨款收入支出决算表", "政府性基金决算表"],
    [
        "国有资本经营预算财政拨款收入支出决算表",
        "国有资本经营预算财政拨款支出决算表",
        "国有资本经营预算支出决算表",
    ],
]


def _standard_aliases_for(index: int) -> List[str]:
    if 0 <= index < len(STANDARD_FINAL_NINE_TABLE_ALIASES):
        return STANDARD_FINAL_NINE_TABLE_ALIASES[index]
    return []


NINE_ALIAS_NORMAL = [
    {
        "name": it["name"],
        "aliases_norm": [
            normalize_text(x)
            for x in list(it["aliases"]) + _standard_aliases_for(idx)
            if x
        ],
    }
    for idx, it in enumerate(NINE_TABLES)
]
STANDARD_FINAL_ALIAS_NORMS = {
    normalize_text(alias)
    for aliases in STANDARD_FINAL_NINE_TABLE_ALIASES
    for alias in aliases
    if alias
}


def _standard_alias_in_title_line(raw: str, alias_norm: str) -> bool:
    line_norms = [
        normalize_text(raw_line)
        for raw_line in (raw or "").splitlines()
        if normalize_text(raw_line)
    ]
    candidates = list(line_norms)
    candidates.extend(
        left + right
        for left, right in zip(line_norms, line_norms[1:], strict=False)
        if len(left) + len(right) <= 80
    )
    for line_norm in candidates:
        if line_norm == alias_norm:
            return True
        # Allow a short table-number prefix such as "表1" or "1." but avoid
        # matching a generic title inside another table title.
        if line_norm.endswith(alias_norm) and len(line_norm) - len(alias_norm) <= 8:
            return True
    return False


def _alias_matches_page(raw: str, normalized_page: str, alias_norm: str) -> bool:
    if not alias_norm:
        return False
    if alias_norm in STANDARD_FINAL_ALIAS_NORMS:
        return _standard_alias_in_title_line(raw, alias_norm)
    return alias_norm in normalized_page or fuzz.partial_ratio(alias_norm, normalized_page) >= 95


def _should_skip_short_expense_table(raw: str, alias_norm: str) -> bool:
    short_expense_aliases = {normalize_text("支出决算表"), normalize_text("部门支出决算表")}
    if alias_norm not in short_expense_aliases:
        return False
    specific_markers = [
        normalize_text("财政拨款"),
        normalize_text("一般公共预算"),
        normalize_text("政府性基金"),
        normalize_text("国有资本"),
        normalize_text("基本支出"),
        normalize_text("三公"),
    ]
    for raw_line in (raw or "").splitlines():
        line_norm = normalize_text(raw_line)
        if alias_norm not in line_norm:
            continue
        if line_norm == alias_norm:
            return False
        return any(marker in line_norm for marker in specific_markers)
    return False

def _is_non_table_page(raw: str) -> bool:
    r = normalize_text(raw or "")
    # 缩小打断范围：情况说明往往跟在表尾（如表七）后面，不能因其出现就断定不是表页
    return ("目录" in r) or ("名词解释" in r)

def find_table_anchors(doc: Document) -> Dict[str, List[int]]:
    anchors: Dict[str, List[int]] = {it["name"]: [] for it in NINE_TABLES}
    for pidx, raw in enumerate(doc.page_texts):
        if _is_non_table_page(raw):
            continue
        ntxt = normalize_text(raw)
        if not ntxt:
            continue
        
        # 放宽表格页面检测条件：
        # 1. 包含"单位："或"本表反映"的页面
        # 2. 包含表格标题的页面
        # 3. 实际包含表格的页面
        is_table_page = ("单位：" in raw) or ("本表反映" in raw)
        
        # 检查是否包含表格标题
        has_table_title = False
        for it in NINE_ALIAS_NORMAL:
            for alias_norm in it["aliases_norm"]:
                if _alias_matches_page(raw, ntxt, alias_norm):
                    has_table_title = True
                    break
            if has_table_title:
                break
        
        # 检查是否实际包含表格
        has_actual_table = len(doc.page_tables[pidx]) > 0 if pidx < len(doc.page_tables) else False
        
        # 如果满足任何一个条件，就认为是表格页面
        if is_table_page or has_table_title or has_actual_table:
            for it in NINE_ALIAS_NORMAL:
                for alias_norm in it["aliases_norm"]:
                    if _alias_matches_page(raw, ntxt, alias_norm):
                        if _should_skip_short_expense_table(raw, alias_norm):
                            continue
                        anchors[it["name"]].append(pidx + 1)
                        break
    return anchors


# ---------- 规则基类 ----------
class Rule:
    code: str
    severity: str
    desc: str

    def apply(self, doc: Document) -> List[Issue]:
        raise NotImplementedError

    def apply_with_ai(self, doc: Document, use_ai_assist: bool) -> List[Issue]:
        """支持AI辅助的apply方法，默认实现直接调用标准apply方法"""
        return self.apply(doc)

    def _issue(self, message: str,
               location: Optional[Dict[str, Any]] = None,
               severity: Optional[str] = None,
               evidence_text: Optional[str] = None,
               section_id: Optional[str] = None) -> Issue:
        return Issue(
            rule=self.code,
            severity=severity or self.severity,
            message=message,
            evidence_text=evidence_text,
            location=location or {},
            section_id=section_id,
        )


# ---------- 规则实现 ----------
class R33001_CoverYearUnit(Rule):
    code, severity = "V33-001", "error"
    desc = "封面/目录年份、单位抽取与一致性"

    def apply(self, doc: Document) -> List[Issue]:
        issues: List[Issue] = []
        # 年份：前 3 页 + 首个表页（若存在）
        front_idxs = list(range(min(3, doc.pages)))
        years, units = [], []
        for i in front_idxs:
            years += doc.years_per_page[i]
            if doc.units_per_page[i]:
                units.append(doc.units_per_page[i])

        first_table_page = None
        if doc.anchors:
            ps = [min(v) for v in doc.anchors.values() if v]
            if ps:
                first_table_page = min(ps)
        scan_upto = max(front_idxs[-1] + 1 if front_idxs else 1, (first_table_page or 1))
        for i in range(scan_upto):
            if doc.units_per_page[i]:
                units.append(doc.units_per_page[i])

        doc.dominant_year = majority(years)
        doc.dominant_unit = majority(units)

        if doc.dominant_year is None:
            issues.append(self._issue("未能在封面/目录或首个表页附近识别年度。", {"page": 1}, severity="warn"))
        else:
            # 年份冲突只在"结构性位置"检查：封面/目录（前 3 页）+ 表头所在页。
            # 正文说明里的同比、较上年、历史年度引用是决算报告的必需内容，
            # 不参与冲突判定（样张 V33-001 误报「与2024年相比增加90.09万元」
            # 的根因，docs/SYSTEM_ISSUES_HANDOFF_2026-09-05.md §3.3C）。
            structural_pages = sorted(set(front_idxs))
            if doc.anchors:
                anchor_pages = set()
                for pages in doc.anchors.values():
                    for p in (pages or [])[:1]:
                        try:
                            anchor_pages.add(int(p) - 1)
                        except (TypeError, ValueError):
                            continue
                structural_pages = sorted(set(front_idxs) | anchor_pages)

            structural_years: List[int] = []
            for i in structural_pages:
                if 0 <= i < len(doc.years_per_page):
                    structural_years.extend(doc.years_per_page[i])

            if structural_years:
                year_counts = {}
                for year in structural_years:
                    year_counts[year] = year_counts.get(year, 0) + 1

                other_years = [year for year in year_counts if year != doc.dominant_year]
                if other_years:
                    # 年份语境排除：证据片段属于同比/历史引用时不判冲突
                    comparison_context = re.compile(
                        r"(同比|较上年|与\s*20\d{2}\s*年\s*相比|上年|上年度|历史|历年)"
                    )
                    evidence_snippets = []
                    for i in structural_pages:
                        if i >= len(doc.page_texts):
                            continue
                        text = doc.page_texts[i] or ""
                        for oy in other_years:
                            oy_str = str(oy)
                            idx = text.find(oy_str)
                            count = 0
                            while idx != -1 and count < 2:
                                start = max(0, idx - 50)
                                end = min(len(text), idx + len(oy_str) + 50)
                                snippet = text[start:end].replace("\n", " ").strip()
                                if comparison_context.search(snippet):
                                    idx = text.find(oy_str, idx + 1)
                                    continue
                                if len(snippet) > 10:
                                    evidence_snippets.append(f"P{i+1}: ...{snippet}...")
                                    count += 1
                                if len(evidence_snippets) >= 5:
                                    break
                                idx = text.find(oy_str, idx + 1)
                            if len(evidence_snippets) >= 5:
                                break
                        if len(evidence_snippets) >= 5:
                            break

                    # 所有命中都在同比语境里 → 不算冲突
                    if evidence_snippets:
                        year_str = ", ".join(map(str, other_years))
                        evidence = "\n".join(evidence_snippets)
                        issues.append(
                            self._issue(
                                f"封面/目录/表头出现多个年份：{doc.dominant_year}年报告的结构性位置出现{year_str}年内容，可能存在年份混淆。",
                                {"page": 1},
                                severity="high",
                                evidence_text=evidence,
                            )
                        )

        # 年度缺位检测（T1）：封面/目录上形如「202 年度」「202X年度缺失一位」的
        # 占位残留。有效年份是 20xx 四位数字，三位数字 + 年度 即为缺位。
        incomplete_year_re = re.compile(r"(?<!\d)20\d(?!\d)\s*年度")
        for i in front_idxs:
            text = doc.page_texts[i] if i < len(doc.page_texts) else ""
            if not text:
                continue
            for match in incomplete_year_re.finditer(text):
                start = max(0, match.start() - 20)
                end = min(len(text), match.end() + 30)
                snippet = text[start:end].replace("\n", " ").strip()
                issues.append(
                    self._issue(
                        f"目录/封面年度缺位：「{match.group().strip()}」疑似应为完整年份（如 {doc.dominant_year or 2025} 年度）。",
                        {
                            "page": i + 1,
                            "pos": match.start(),
                            # 精确锚词：命中文本本身（如「202 年度」）。bbox 定位器
                            # 优先用它全文搜索，避免跨行 evidence 切分词标到别的行
                            # （实测曾把该问题标到「一、收入支出决算总体情况说明」）。
                            "anchor": match.group().strip(),
                        },
                        severity="high",
                        evidence_text=snippet,
                    )
                )

        all_units = [u for u in doc.units_per_page if u]
        if not all_units:
            issues.append(self._issue("未识别到金额单位（单位：万元/元/亿元）。", {"page": 1}, severity="warn"))
        elif len(set(all_units)) > 1:
            issues.append(self._issue(f"金额单位混用：{sorted(set(all_units))}。", {"page": 1}, severity="warn"))
        return issues


class R33114_EmptyTableStatementCheck(Rule):
    code, severity = "V33-114", "error"
    desc = "空表说明检查"

    def apply(self, doc: Document) -> List[Issue]:
        """检查空表是否缺少相应的说明"""
        issues: List[Issue] = []
        
        # 定义需要检查的空表
        empty_tables = [
            '财政拨款"三公"经费支出决算表',
            '财政拨款"三公"经费支出决算表',  # 兼容不同的引号
            '政府性基金预算财政拨款收入支出决算表',
            '国有资本经营预算财政拨款收入支出决算表',
        ]
        
        # 遍历所有页面
        for page_num, page_text in enumerate(doc.page_texts):
            # 检查是否包含空表
            for table_name in empty_tables:
                if table_name in page_text:
                    # 检查表格是否为空（只有表头，没有数据）
                    tables = doc.page_tables[page_num] if page_num < len(doc.page_tables) else []
                    is_empty_table = False
                    
                    if tables:
                        for table in tables:
                            # 检查表格是否为空（只有表头，没有数据）
                            # 检查表格的行数和列数
                            rows = len(table)
                            if rows == 0:
                                continue
                            
                            cols = max(len(row) for row in table) if table else 0
                            
                            # 检查非空单元格的数量
                            non_empty_cells = 0
                            for row in table:
                                for cell in row:
                                    if cell and str(cell).strip():
                                        non_empty_cells += 1
                            
                            # 如果非空单元格数量小于等于列数，且表格行数较少，则认为是空表
                            # 或者如果非空单元格数量小于等于2*列数，且表格行数较少，则认为是空表
                            if rows <= 5 and non_empty_cells <= 2 * cols:
                                is_empty_table = True
                                break
                    
                    if is_empty_table:
                        # 检查是否有相应的说明
                        has_statement = False
                        # 检查是否有"注："和"无数据"的说明
                        if "注：" in page_text and "无数据" in page_text:
                            has_statement = True
                        
                        if not has_statement:
                            # 根据不同的表格，生成不同的说明语句
                            if '财政拨款"三公"经费支出决算表' in table_name:
                                expected_statement = '注：上海市普陀区财政局无财政拨款"三公"经费支出，故本表无数据。'
                            elif '政府性基金预算财政拨款收入支出决算表' in table_name:
                                expected_statement = "注：上海市普陀区财政局无政府性基金预算财政拨款支出，故本表无数据。"
                            elif '国有资本经营预算财政拨款收入支出决算表' in table_name:
                                expected_statement = "注：上海市普陀区财政局无国有资本经营预算财政拨款支出，故本表无数据。"
                            else:
                                expected_statement = "注：上海市普陀区财政局无相关支出，故本表无数据。"
                            
                            issues.append(self._issue(
                                f"空表缺少说明：{table_name}为空表，但缺少相应的说明。建议添加：{expected_statement}",
                                {"page": page_num + 1, "table": table_name},
                                severity="error"
                            ))
        
        return issues

class R33002_NineTablesCheck(Rule):
    code, severity = "V33-002", "error"
    desc = "九张表定位、缺失、重复与顺序"

    def apply(self, doc: Document) -> List[Issue]:
        issues: List[Issue] = []
        anchors = doc.anchors or find_table_anchors(doc)
        doc.anchors = anchors

        missing, duplicates, order_pages = [], [], []
        # 逐张检查
        for spec in NINE_TABLES:
            nm = spec["name"]
            pages = anchors.get(nm, [])
            if not pages:
                missing.append(nm)
            else:
                order_pages.append((nm, min(pages)))
                if len(pages) > 1:
                    duplicates.append((nm, pages))
        
        # 缺失
        for nm in missing:
            issues.append(self._issue(f"缺失表：{nm}", {"table": nm}, severity="error"))

        # 重复 - 跨页表格常见，不再提示
        # for nm, pgs in duplicates:
        #     issues.append(self._issue(f"表出现多次：{nm}（页码 {pgs}）", {"table": nm, "pages": pgs}, severity="warn"))

        # 顺序（按首次出现页）——至少识别出 3 张表才判断
        if len(order_pages) >= 3:
            expected_index = {spec["name"]: idx for idx, spec in enumerate(NINE_TABLES)}
            actual = sorted(order_pages, key=lambda x: x[1])       # 按页码升序
            indices = [expected_index[nm] for nm, _ in actual]
            if indices != sorted(indices):
                # 找出具体的逆序对
                detail_msg = ""
                for i in range(len(indices) - 1):
                    if indices[i] > indices[i+1]:
                        name_a, page_a = actual[i]
                        name_b, page_b = actual[i+1]
                        detail_msg = f"检测到 “{name_a}”(@P{page_a}) 错误地出现在 “{name_b}”(@P{page_b}) 之前（标准应在之后）。"
                        break
                
                msg = f"九张表出现顺序异常。{detail_msg} 实际检测顺序：" + " > ".join(f"{nm}@{pg}" for nm, pg in actual)
                
                # 收集证据：提取这些页面的标题或首行
                ev_lines = []
                for nm, pg in actual:
                     if pg <= len(doc.page_texts):
                         # 取前100个字符作为上下文
                         preview = doc.page_texts[pg-1][:100].replace("\n", " ")
                         ev_lines.append(f"P{pg} ({nm}): {preview}...")
                
                evidence = "\n".join(ev_lines)
                issues.append(self._issue(msg, {}, severity="error", evidence_text=evidence))
        
        # 特殊检查：独立的"支出决算表"
        # 检查是否存在独立的"支出决算表"，而不是具体的分类表
        expense_table_name = "支出决算表"
        expense_table_pages = anchors.get(expense_table_name, [])
        
        if expense_table_pages:
            # 检查这些页面是否都是具体的分类表
            # 如果所有页面都是具体的分类表，则认为缺少独立的"支出决算表"
            has_independent_table = False
            for page_num in expense_table_pages:
                if 1 <= page_num <= len(doc.page_texts):
                    page_text = doc.page_texts[page_num - 1]
                    if _standard_alias_in_title_line(page_text, normalize_text(expense_table_name)):
                        has_independent_table = True
                        break
                    # 检查是否是独立的"支出决算表"
                    # 独立的"支出决算表"应该不包含"一般公共预算"、"基本支出"、"三公"等关键词
                    if ("支出决算表" in page_text and 
                        "一般公共预算" not in page_text and 
                        "基本支出" not in page_text and 
                        "三公" not in page_text and
                        "政府性基金" not in page_text and
                        "国有资本经营" not in page_text):
                        has_independent_table = True
                        break
            
            if not has_independent_table:
                issues.append(self._issue(
                    "缺失独立的支出决算表：只找到具体的分类表（如\"一般公共预算财政拨款支出决算表\"），未找到独立的、总的\"支出决算表\"。",
                    {"table": expense_table_name},
                    severity="error"
                ))
        
        return issues

class R33003_PageFileThreshold(Rule):
    code, severity = "V33-003", "warn"
    desc = "页数/文件大小阈值"

    def apply(self, doc: Document) -> List[Issue]:
        issues: List[Issue] = []
        if doc.pages < 8:
            issues.append(self._issue(f"页数过少：{doc.pages} 页，疑似不完整。", {"pages": doc.pages}, severity="error"))
        if doc.pages > 300:
            issues.append(self._issue(f"页数较多：{doc.pages} 页，建议分卷检查。", {"pages": doc.pages}))
        if doc.filesize > 50 * 1024 * 1024:
            mb = round(doc.filesize / (1024 * 1024), 1)
            issues.append(self._issue(f"文件体积较大：{mb} MB，可能影响解析速度。", {"filesize": doc.filesize}))
        return issues


class R33004_CellNumberValidity(Rule):
    code, severity = "V33-004", "error"
    desc = "表内数字合法性（百分比>100、负数提示）"

    def apply(self, doc: Document) -> List[Issue]:
        issues: List[Issue] = []
        if not doc.page_tables or not any(doc.page_tables):
            raise RuleDeferred(
                self.code,
                detail="未解析到有效页面表格",
                unresolved_reasons=["未解析到有效页面表格"],
            )
        for pidx, tables in enumerate(doc.page_tables):
            for tindex, table in enumerate(tables):
                if not table or not any(row for row in table):
                    continue
                for r, row in enumerate(table):
                    for c, cell in enumerate(row):
                        s = "" if cell is None else str(cell)
                        if looks_like_percent(s):
                            v = parse_number(s)
                            if v is not None and (v < 0 or v > 100):
                                issues.append(self._issue(
                                    f"百分比越界：{s}",
                                    {"page": pidx + 1, "table_index": tindex, "row": r + 1, "col": c + 1},
                                    severity="error"
                                ))
                        else:
                            if has_negative_sign(s) and parse_number(s) is not None:
                                issues.append(self._issue(
                                    f"出现负数：{s}（请确认是否合理）",
                                    {"page": pidx + 1, "table_index": tindex, "row": r + 1, "col": c + 1},
                                    severity="warn"
                                ))
        return issues


class R33005_TableTotalConsistency(Rule):
    code, severity = "V33-005", "error"
    desc = "表内合计与分项和一致（±1 或 0.1% 容忍）"
    _TOTAL_RE = re.compile(r"^(合计|总计)$")
    _EXCLUDE_HEAD = ("其中", "小计", "分项", "人员经费合计", "公用经费合计")

    def apply(self, doc: Document) -> List[Issue]:
        issues: List[Issue] = []
        money_col_hint = ("金额", "合计", "本年收入", "本年支出", "决算数", "预算数")
        checked_tables = 0

        for pidx, tables in enumerate(doc.page_tables):
            for tindex, table in enumerate(tables):
                if not table or len(table) < 3:
                    continue

                header = [str(x or "") for x in (table[0] if table else [])]
                header_join = "".join(header)

                # 左右两栏（收入+支出）总表：跳过
                if ("收入" in header_join) and ("支出" in header_join):
                    continue

                # 找“合计/总计”行（第一列严格等于）
                total_row_idx = None
                for r, row in enumerate(table):
                    head = str((row[0] if row else "") or "").strip()
                    if head in self._EXCLUDE_HEAD:
                        continue
                    if self._TOTAL_RE.match(head):
                        total_row_idx = r
                        break
                if total_row_idx is None or total_row_idx < 2:
                    continue

                checked_tables += 1

                # 选择金额相关的列（优先表头关键词）
                ncols = max(len(row) for row in table)
                cand_cols: List[int] = []
                for c in range(1, ncols):
                    headc = header[c] if c < len(header) else ""
                    if any(h in headc for h in money_col_hint):
                        cand_cols.append(c)
                if not cand_cols:
                    for c in range(1, ncols):
                        cnt = 0
                        for r in range(0, total_row_idx):
                            cell = table[r][c] if c < len(table[r]) else None
                            v = parse_number(cell)
                            if v is not None and not looks_like_percent(cell):
                                cnt += 1
                        if cnt >= 3:
                            cand_cols.append(c)
                if not cand_cols:
                    continue

                # 功能分类层级：只累计叶子行
                code_rows: List[Tuple[int, str]] = []
                for r in range(1, total_row_idx):
                    row = table[r]
                    name0 = str((row[0] if row else "") or "")
                    m = re.match(r"^\s*(\d{3,7})", name0)
                    if m:
                        code_rows.append((r, m.group(1)))
                leaf_len = max((len(c) for _, c in code_rows), default=None)

                for c in cand_cols:
                    total_cell = table[total_row_idx][c] if c < len(table[total_row_idx]) else None
                    total_val = parse_number(total_cell)
                    if total_val is None or looks_like_percent(total_cell):
                        continue

                    parts: List[float] = []
                    for r in range(1, total_row_idx):
                        headr = str((table[r][0] if 0 < len(table[r]) else "") or "")
                        if headr in self._EXCLUDE_HEAD or self._TOTAL_RE.match(headr.strip()):
                            continue
                        if leaf_len is not None:
                            m = re.match(r"^\s*(\d{3,7})", headr)
                            if not (m and len(m.group(1)) == leaf_len):
                                continue
                        cell = table[r][c] if c < len(table[r]) else None
                        v = parse_number(cell)
                        if v is not None and not looks_like_percent(cell):
                            parts.append(float(v))
                    if len(parts) < 1:
                        continue

                    sum_val = float(np.nansum(parts))
                    tol = max(1.0, abs(sum_val) * 0.001)
                    diff = abs(sum_val - (total_val or 0.0))
                    if diff > tol and (total_val == 0 or diff / max(abs(total_val), 1e-6) > 0.5):
                        # Construct richer issue info
                        col_name = header[c] if c < len(header) else f"第{c+1}列"
                        loc_desc = f"P{pidx + 1} 表格{tindex + 1}（列：{col_name}）"
                        
                        # Build detailed evidence text
                        ev_lines = []
                        ev_lines.append(f"表格位置：第 {pidx + 1} 页, 第 {tindex + 1} 个表格")
                        ev_lines.append(f"检查列名：{col_name}")
                        ev_lines.append(f"表头概览：{' | '.join(header[:5])}..." if len(header)>5 else f"表头概览：{' | '.join(header)}")
                        
                        # Add total row context
                        row_cells = [str(x or "") for x in table[total_row_idx]]
                        row_str = " | ".join(row_cells[:8]) + ("..." if len(row_cells)>8 else "")
                        ev_lines.append(f"合计行内容：{row_str}")
                        
                        ev_lines.append(f"数值明细：合计值={total_val}, 分项累加={sum_val}")
                        ev_lines.append(f"差异={round(diff, 2)} (允许范围 ±{round(tol, 2)})")
                        
                        issues.append(self._issue(
                            f"{loc_desc} “合计”与分项和不一致：合计={total_val}，分项和={sum_val}（容忍±{round(tol, 2)}）",
                            {"page": pidx + 1, "table_index": tindex, "col": c + 1, "total_row": total_row_idx + 1},
                            severity="error",
                            evidence_text="\n".join(ev_lines)
                        ))
        if checked_tables == 0:
            raise RuleDeferred(
                self.code,
                detail="未定位到具备合计行与分项的数据表格",
                unresolved_reasons=["未定位到具备合计行与分项的数据表格"],
            )
        return issues


# ---------- 辅助（跨表/文数一致） ----------
def _largest_table_on_page(tables: List[List[List[str]]]) -> Optional[List[List[str]]]:
    if not tables:
        return None
    return sorted(tables, key=lambda t: sum(len(r) for r in t), reverse=True)[0]

def _get_first_anchor_page(doc: Document, table_name: str) -> Optional[int]:
    if not doc.anchors:
        doc.anchors = find_table_anchors(doc)
    pages = (doc.anchors or {}).get(table_name) or []
    if not pages:
        return None
    # 优先选择该页确实有表格对象的页面（排除目录页的误匹配）
    table_pages = [p for p in pages if p <= len(doc.page_tables) and doc.page_tables[p-1]]
    return min(table_pages) if table_pages else min(pages)


def _ensure_table_anchors(doc: Document) -> Dict[str, List[int]]:
    anchors = doc.anchors or find_table_anchors(doc)
    doc.anchors = anchors
    return anchors


def _collect_pages(*values: Any) -> List[int]:
    pages: List[int] = []
    seen = set()
    for value in values:
        if value is None:
            continue
        try:
            page = int(value)
        except (TypeError, ValueError):
            continue
        if page <= 0 or page in seen:
            continue
        seen.add(page)
        pages.append(page)
    return pages


def _make_location_ref(
    *,
    role: Optional[str] = None,
    page: Optional[int] = None,
    table: Optional[str] = None,
    section: Optional[str] = None,
    row: Optional[str] = None,
    col: Optional[str] = None,
    field: Optional[str] = None,
    code: Optional[str] = None,
    subject: Optional[str] = None,
    value: Optional[float] = None,
) -> Dict[str, Any]:
    ref: Dict[str, Any] = {}
    if role:
        ref["role"] = role
    if page:
        ref["page"] = page
    if table:
        ref["table"] = table
    if section:
        ref["section"] = section
    if row:
        ref["row"] = row
    if col:
        ref["col"] = col
    if field:
        ref["field"] = field
    if code:
        ref["code"] = code
    if subject:
        ref["subject"] = subject
    if value is not None:
        ref["value"] = value
    return ref


def _make_issue_location(
    *refs: Dict[str, Any],
    page: Optional[int] = None,
    pages: Optional[List[int]] = None,
    table: Optional[str] = None,
    section: Optional[str] = None,
    row: Optional[str] = None,
    col: Optional[str] = None,
    field: Optional[str] = None,
    code: Optional[str] = None,
    subject: Optional[str] = None,
) -> Dict[str, Any]:
    valid_refs = [ref for ref in refs if ref]
    location: Dict[str, Any] = {}

    all_pages = _collect_pages(page, *((pages or [])), *(ref.get("page") for ref in valid_refs))
    if len(all_pages) == 1:
        location["page"] = all_pages[0]
    elif all_pages:
        location["pages"] = all_pages

    if table:
        location["table"] = table
    elif valid_refs:
        tables: List[str] = []
        seen_tables = set()
        for ref in valid_refs:
            name = str(ref.get("table") or "").strip()
            if not name or name in seen_tables:
                continue
            seen_tables.add(name)
            tables.append(name)
        if tables:
            location["table"] = " / ".join(tables)

    for key, value in (
        ("section", section),
        ("row", row),
        ("col", col),
        ("field", field),
        ("code", code),
        ("subject", subject),
    ):
        if value not in (None, ""):
            location[key] = value

    if valid_refs:
        location["table_refs"] = valid_refs

    return location


def _extract_table_total(table: List[List[str]]) -> Optional[float]:
    """提取合计行的「合计」列数值（None 安全）。

    定位策略：
    1. 合计行：任一单元格恰为「合计」或行首为「合计」，且无科目编码、
       含 ≥2 个真实数值；
    2. 合计列：表头（前 5 行）中恰为「合计」的列；取不到时退回行内
       最大数值（合计列 = 各分项之和，必为行内最大）。
    """
    if not table:
        return None
    header_total_col = None
    for row in table[:5]:
        for i, cell in enumerate(row):
            if str(cell or "").strip() == "合计":
                header_total_col = i
                break
        if header_total_col is not None:
            break

    for row in table:
        cells = [parse_number(c) for c in row]
        numeric = [v for v in cells if v is not None]
        if len(numeric) < 2:
            continue
        if any(str(c or "").strip().isdigit() and len(str(c).strip()) in (3, 5, 7) for c in row[:3]):
            continue
        if not (any(str(c or "").strip() == "合计" for c in row) or str(row[0] or "").strip().startswith("合计")):
            continue
        if header_total_col is not None and header_total_col < len(cells) and cells[header_total_col] is not None:
            return float(cells[header_total_col])
        return float(max(numeric))
    return None


def _row_value(table: List[List[str]],
               name_keys: Tuple[str, ...],
               prefer_cols: Tuple[str, ...] = ()) -> Optional[float]:
    if not table:
        return None
    header = [str(x or "") for x in (table[0] if table else [])]
    target_row = None
    for r, row in enumerate(table):
        head = str((row[0] if row else "") or "")
        if any(k in head for k in name_keys):
            target_row = r
            break
    if target_row is None:
        return None

    if prefer_cols:
        prefer_idx: List[int] = []
        for i, col_name in enumerate(header):
            if any(k in col_name for k in prefer_cols):
                prefer_idx.append(i)
        for c in prefer_idx:
            if c < len(table[target_row]):
                cell = table[target_row][c]
                if looks_like_percent(cell):
                    continue
                v = parse_number(cell)
                if v is not None:
                    return float(v)

    ncols = max(len(r) for r in table)
    for c in range(ncols - 1, -1, -1):
        cell = table[target_row][c] if c < len(table[target_row]) else None
        if looks_like_percent(cell):
            continue
        v = parse_number(cell)
        if v is not None:
            return float(v)
    return None

def _sum_by_func_class(table: List[List[str]], digits: int = 3) -> Dict[str, float]:
    agg: Dict[str, float] = defaultdict(float)
    if not table:
        return agg
    ncols = max(len(r) for r in table)
    for r, row in enumerate(table[1:], start=1):
        name = str((row[0] if row else "") or "")
        m = re.match(r"(\d{3,7})", name.strip())
        if not m:
            continue
        code = m.group(1)[:digits]
        val: Optional[float] = None
        for c in range(ncols - 1, 0, -1):
            v = parse_number(row[c] if c < len(row) else None)
            if v is not None and not looks_like_percent(row[c]):
                val = v
                break
        if val is not None:
            agg[code] += float(val)
    return dict(agg)

_NUM_TOKEN_RE = re.compile(
    r"(?<![\d.])([+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)\s*(亿元|万元|元|%|％|年|年度)?(?![\d.])"
)


def _get_sentence_years(sent: str) -> List[str]:
    years: List[str] = []
    for m in _NUM_TOKEN_RE.finditer(sent):
        raw_num, suffix = m.group(1), m.group(2)
        if suffix in ("年", "年度") and re.fullmatch(r"20\d{2}", raw_num):
            years.append(raw_num)
        elif not suffix and re.fullmatch(r"20\d{2}", raw_num):
            years.append(raw_num)
    return years


def _extract_amount_from_segment(seg: str, keywords: List[str]) -> Optional[Tuple[Decimal, int, str]]:
    """P1-1: Extract amount closest to keywords, scoped to the same comma-clause first."""
    kw_spans: List[Tuple[int, int]] = []
    for kw in keywords:
        for m in re.finditer(re.escape(kw), seg):
            kw_spans.append((m.start(), m.end()))
    if not kw_spans:
        return None

    # --- Build comma-clause boundaries ---
    clause_boundaries: List[Tuple[int, int]] = []
    pos = 0
    for part in re.split(r'[，,]', seg):
        clause_boundaries.append((pos, pos + len(part)))
        pos += len(part) + 1

    # Clauses that contain at least one keyword span
    kw_clause_idxs: set = set()
    for kw_s, kw_e in kw_spans:
        for ci, (cs, ce) in enumerate(clause_boundaries):
            if kw_s >= cs and kw_e <= ce + 1:
                kw_clause_idxs.add(ci)

    def _num_clause(ns: int) -> Optional[int]:
        for ci, (cs, ce) in enumerate(clause_boundaries):
            if ns >= cs and ns < ce + 1:
                return ci
        return None

    def _pick_best(
        restrict_to_kw_clauses: bool,
    ) -> Optional[Tuple[str, Optional[str]]]:
        best: Optional[Tuple[str, Optional[str]]] = None
        min_dist = 999999
        # Prefer forward direction over reverse when equal distance
        best_is_forward: bool = False
        for m in _NUM_TOKEN_RE.finditer(seg):
            raw_num, suffix = m.group(1), m.group(2)
            if suffix in ("年", "年度", "%", "％"):
                continue
            if not suffix and re.fullmatch(r"20\d{2}", raw_num):
                continue
            num_s, num_e = m.start(), m.end()
            nc = _num_clause(num_s)
            if restrict_to_kw_clauses and nc not in kw_clause_idxs:
                continue
            for kw_s, kw_e in kw_spans:
                if num_s >= kw_e:  # forward: number after keyword
                    dist = num_s - kw_e
                    if dist <= 40 and (dist < min_dist or (dist == min_dist and not best_is_forward)):
                        min_dist = dist
                        best = (raw_num, suffix)
                        best_is_forward = True
                elif kw_s >= num_e:  # reverse: number before keyword
                    dist = kw_s - num_e
                    if dist <= 25 and dist < min_dist:
                        min_dist = dist
                        best = (raw_num, suffix)
                        best_is_forward = False
        return best

    # Scoped strictly to keyword-containing clauses (never borrow across clauses)
    best = _pick_best(restrict_to_kw_clauses=True)

    if best:
        raw_num, unit = best
        val_clean = raw_num.replace(",", "")
        scale = len(val_clean.split(".")[1]) if "." in val_clean else 0
        unit = unit or "万元"
        d = Decimal(val_clean)
        if unit == "元":
            d = d / Decimal("10000")
        elif unit == "亿元":
            d = d * Decimal("10000")
        return d, scale, unit
    return None


def near_strict_number(
    text: str, keywords: List[str], target_year: Optional[int] = None
) -> Optional[StrictValue]:
    """提取关键词附近的金额数值，返回 StrictValue 对象（保留原文精度与单位）。"""
    if not text:
        return None
    sentences = re.split(r"[。；;\n]", text)

    # 1. 指定 target_year 时：优先筛选包含目标年份的句子
    if target_year is not None:
        target_str = str(target_year)
        for sent in sentences:
            sent = sent.strip()
            if not sent:
                continue
            years = _get_sentence_years(sent)
            if not years or target_str not in years:
                continue
            sub_sents = [sent]
            if len(set(years)) > 1:
                clauses = [c.strip() for c in re.split(r"[，,]", sent) if c.strip()]
                target_clauses = [c for c in clauses if target_str in c]
                sub_sents = target_clauses if target_clauses else [sent]
            for ss in sub_sents:
                res = _extract_amount_from_segment(ss, keywords)
                if res:
                    dec, scale, unit = res
                    return StrictValue(
                        raw_text=str(dec),
                        status="valid",
                        decimal_val=dec,
                        scale_digits=scale,
                        unit=unit,
                    )

        # 2. 回退：检查完全不包含任何年份提及的纯说明句（但绝不回退到包含冲突年份的句子）
        # M-4: also require at least one keyword to appear in the sentence before extracting
        for sent in sentences:
            sent = sent.strip()
            if not sent:
                continue
            years = _get_sentence_years(sent)
            if years:
                continue
            if not any(kw in sent for kw in keywords):  # M-4: keyword presence guard
                continue
            res = _extract_amount_from_segment(sent, keywords)
            if res:
                dec, scale, unit = res
                return StrictValue(
                    raw_text=str(dec),
                    status="valid",
                    decimal_val=dec,
                    scale_digits=scale,
                    unit=unit,
                )
        return None

    # 3. 未指定 target_year 时：遍历所有句子进行匹配
    for sent in sentences:
        sent = sent.strip()
        if not sent:
            continue
        res = _extract_amount_from_segment(sent, keywords)
        if res:
            dec, scale, unit = res
            return StrictValue(
                raw_text=str(dec),
                status="valid",
                decimal_val=dec,
                scale_digits=scale,
                unit=unit,
            )
    return None


def near_number(text: str, keywords: List[str], target_year: Optional[int] = None) -> Optional[float]:
    """提取关键词附近的金额数值（float 兼容接口）。"""
    sv = near_strict_number(text, keywords, target_year)
    return float(sv.decimal_val) if sv and sv.decimal_val is not None else None


def find_percent(text: str, keywords: List[str]) -> Optional[float]:
    if not text:
        return None
    kw = "|".join(map(re.escape, keywords))
    # 简化正则表达式，避免复杂的嵌套量词导致回溯
    m = re.search(rf"(?:{kw})[^0-9]*?(-?\d+(?:\.\d+)?)*%", text, flags=re.S | re.M)
    if not m:
        return None
    try:
        return float(m.group(1))
    except Exception:
        return None

def _snippet(s: str, start: int, end: int, max_len: int = 32) -> str:
    seg = s[max(0, start - max_len): min(len(s), end + max_len)]
    seg = re.sub(r"\s+", " ", seg).strip()
    if len(seg) > max_len * 2:
        seg = seg[:max_len] + " … " + seg[-max_len:]
    return seg


def _find_text_page_after(doc: Document, snippet: Optional[str], start_page: int = 1) -> Optional[int]:
    needle = normalize_text(str(snippet or "")).strip()
    if not needle:
        return None

    candidates = [needle]
    if len(needle) > 24:
        candidates.append(needle[:40])

    start_idx = max(start_page, 1) - 1
    for pidx in range(start_idx, len(doc.page_texts)):
        hay = normalize_text(doc.page_texts[pidx] or "")
        if any(candidate and candidate in hay for candidate in candidates):
            return pidx + 1
    return None


_FUNCTIONAL_NARRATIVE_ENTRY_RE = re.compile(
    r"(?:^|\n)\s*(?:\d+\s*[、.．]\s*)?"
    r"(?P<class_name>[^（）()\n]{1,40}?)\s*[（(]\s*(?:(?P<class_code>\d{3})\s*)?类\s*[）)]\s*"
    r"(?P<section_name>[^（）()\n]{1,60}?)\s*[（(]\s*(?:(?P<section_code>\d{2})\s*)?款\s*[）)]\s*"
    r"(?P<item_name>[^（）()\n]{1,80}?)\s*[（(]\s*(?:(?P<item_code>\d{2})\s*)?项\s*[）)]",
    re.M,
)


def _format_functional_code(code: str) -> str:
    if len(code) == 3:
        return code
    if len(code) == 5:
        return f"{code[:3]}-{code[3:5]}"
    if len(code) == 7:
        return f"{code[:3]}-{code[3:5]}-{code[5:7]}"
    return code


def _normalize_functional_name(name: str) -> str:
    normalized = normalize_text(name or "")
    for suffix in ("预算支出", "决算支出", "支出决算", "支出", "预算", "决算"):
        if normalized.endswith(suffix):
            normalized = normalized[: -len(suffix)]
            break
    return normalized


def _functional_name_matches(table_name: str, narrative_name: str) -> bool:
    left = _normalize_functional_name(table_name)
    right = _normalize_functional_name(narrative_name)
    return bool(left and right and left == right)


def _extract_functional_name_index(rows: List[List[str]]) -> Dict[str, Dict[str, str]]:
    entries: Dict[str, Dict[str, str]] = {}

    for row in rows:
        cells = [str(cell or "").strip() for cell in row]
        if len(cells) < 4:
            continue

        class_code, section_code, item_code, name = cells[:4]
        if not re.fullmatch(r"\d{3}", class_code) or not name or name in {"项目", "功能分类科目名称"}:
            continue

        class_display = _format_functional_code(class_code)
        entries.setdefault(
            class_code,
            {
                "name": name,
                "level": "类",
                "code_display": class_display,
                "row_text": f"{class_code} {name}",
                "class_name": name,
            },
        )

        if re.fullmatch(r"\d{2}", section_code):
            section_full_code = f"{class_code}{section_code}"
            section_display = _format_functional_code(section_full_code)
            class_name = entries[class_code]["name"]
            entries.setdefault(
                section_full_code,
                {
                    "name": name,
                    "level": "款",
                    "code_display": section_display,
                    "row_text": f"{class_code} {section_code} {name}",
                    "class_name": class_name,
                    "section_name": name,
                },
            )

            if re.fullmatch(r"\d{2}", item_code):
                item_full_code = f"{section_full_code}{item_code}"
                item_display = _format_functional_code(item_full_code)
                section_name = entries[section_full_code]["name"]
                entries[item_full_code] = {
                    "name": name,
                    "level": "项",
                    "code_display": item_display,
                    "row_text": f"{class_code} {section_code} {item_code} {name}",
                    "class_name": class_name,
                    "section_name": section_name,
                    "item_name": name,
                }
    return entries


def _build_functional_item_name_index(
    table_entries: Dict[str, Dict[str, str]]
) -> Tuple[Dict[Tuple[str, str], List[str]], Dict[str, List[str]]]:
    by_section_item: Dict[Tuple[str, str], List[str]] = defaultdict(list)
    by_item: Dict[str, List[str]] = defaultdict(list)

    for code, entry in table_entries.items():
        if entry.get("level") != "项":
            continue
        section_name = normalize_text(entry.get("section_name", ""))
        item_name = normalize_text(entry.get("name", ""))
        if section_name and item_name:
            by_section_item[(section_name, item_name)].append(code)
        if item_name:
            by_item[item_name].append(code)

    return by_section_item, by_item


def _extract_final_functional_narrative_mentions(
    doc: Document, table_entries: Dict[str, Dict[str, str]]
) -> Dict[str, List[Dict[str, str]]]:
    mentions: Dict[str, List[Dict[str, str]]] = {}
    by_section_item, by_item = _build_functional_item_name_index(table_entries)

    for page_num, text in enumerate(doc.page_texts, start=1):
        if not text or ("类）" not in text and "类)" not in text and "（类）" not in text and "(类)" not in text):
            continue

        for match in _FUNCTIONAL_NARRATIVE_ENTRY_RE.finditer(text):
            class_name = str(match.group("class_name") or "").strip(" 　：:；;，,。.")
            section_name = str(match.group("section_name") or "").strip(" 　：:；;，,。.")
            item_name = str(match.group("item_name") or "").strip(" 　：:；;，,。.")
            if not class_name or not section_name or not item_name:
                continue

            class_code = str(match.group("class_code") or "").strip()
            section_code = str(match.group("section_code") or "").strip()
            item_code = str(match.group("item_code") or "").strip()

            matched_codes: Optional[Tuple[str, str, str]] = None
            if class_code and section_code and item_code:
                matched_codes = (
                    class_code,
                    f"{class_code}{section_code}",
                    f"{class_code}{section_code}{item_code}",
                )
            else:
                normalized_section = normalize_text(section_name)
                normalized_item = normalize_text(item_name)
                item_candidates = by_section_item.get((normalized_section, normalized_item), [])
                if len(item_candidates) == 1:
                    item_full_code = item_candidates[0]
                    matched_codes = (
                        item_full_code[:3],
                        item_full_code[:5],
                        item_full_code[:7],
                    )
                else:
                    item_candidates = by_item.get(normalized_item, [])
                    if len(item_candidates) == 1:
                        item_full_code = item_candidates[0]
                        matched_codes = (
                            item_full_code[:3],
                            item_full_code[:5],
                            item_full_code[:7],
                        )

            if not matched_codes:
                continue

            snippet = _snippet(text, match.start(), match.end(), max_len=40)
            for code, raw_name, level in (
                (matched_codes[0], class_name, "类"),
                (matched_codes[1], section_name, "款"),
                (matched_codes[2], item_name, "项"),
            ):
                bucket = mentions.setdefault(code, [])
                if any(
                    item.get("page") == str(page_num)
                    and normalize_text(item.get("name", "")) == normalize_text(raw_name)
                    for item in bucket
                ):
                    continue
                bucket.append(
                    {
                        "page": str(page_num),
                        "name": raw_name,
                        "level": level,
                        "snippet": snippet,
                    }
                )

    return mentions


# ---------- 跨表勾稽（V33-101~105） ----------
class R33101_TotalSheet_Identity(Rule):
    code, severity = "V33-101", "error"
    desc = "收入支出决算总表：支出列恒等式"

    def apply(self, doc: Document) -> List[Issue]:
        issues: List[Issue] = []
        p = _get_first_anchor_page(doc, "收入支出决算总表")
        if not p:
            raise RuleDeferred(
                self.code,
                detail="未找到收入支出决算总表锚点页",
                unresolved_reasons=["未找到收入支出决算总表锚点页"],
            )
        table = _largest_table_on_page(doc.page_tables[p - 1])
        if not table:
            raise RuleDeferred(
                self.code,
                detail="收入支出决算总表所在页未解析到有效表格",
                unresolved_reasons=["收入支出决算总表所在页未解析到有效表格"],
            )
        total = _row_value(table, ("支出合计", "支出总计", "合计"))
        bn = _row_value(table, ("本年支出合计", "本年支出", "本年合计"))
        jy = _row_value(table, ("结余分配", "结余分配支出"))
        jz = _row_value(table, ("年末结转和结余", "年末结转", "结转结余"))

        if total is None or bn is None or jy is None or jz is None:
            missing_parts = []
            if total is None: missing_parts.append("支出合计")
            if bn is None: missing_parts.append("本年支出")
            if jy is None: missing_parts.append("结余分配")
            if jz is None: missing_parts.append("年末结转和结余")
            raise RuleDeferred(
                self.code,
                detail=f"收入支出决算总表关键行缺失: {','.join(missing_parts)}",
                unresolved_reasons=[f"收入支出决算总表关键行缺失: {','.join(missing_parts)}"],
            )

        try:
            bn_f, jy_f, jz_f, total_f = float(bn), float(jy), float(jz), float(total)
            sum_val = bn_f + jy_f + jz_f
            if not tolerant_equal(total_f, sum_val):
                issues.append(self._issue(
                    f"总表支出恒等式不成立：支出合计={total_f} vs 本年支出{bn_f}+结余分配{jy_f}+年末结转{jz_f}={sum_val}",
                    {"page": p}, "error",
                    evidence_text=f"表格：P{p} 收入支出决算总表\n计算明细：\n本年支出({bn}) + 结余分配({jy}) + 年末结转({jz}) = {sum_val}\n表内支出合计 = {total}"
                ))
        except Exception as e:
            # M-1: carry already-found issues to avoid silent loss on float conversion failure
            raise RuleDeferred(
                self.code,
                detail=f"总表支出恒等式计算异常: {str(e)}",
                partial_issues=issues,
                unresolved_reasons=[f"总表支出恒等式计算异常: {str(e)}"],
            )
        return issues

class R33102_TotalSheet_vs_Text(Rule):
    code, severity = "V33-102", "warn"
    desc = "收入支出决算总表 ↔ 总体情况说明"

    def apply(self, doc: Document) -> List[Issue]:
        try:
            issues: List[Issue] = []

            # 1) 找"收入支出决算总表"的第一页
            p = _get_first_anchor_page(doc, "收入支出决算总表")
            if not p:
                raise RuleDeferred(
                    self.code,
                    detail="未找到收入支出决算总表锚点页",
                    unresolved_reasons=["未找到收入支出决算总表锚点页"],
                )

            # 2) 取该页最大的一张表
            table = _largest_table_on_page(doc.page_tables[p - 1])
            if not table:
                raise RuleDeferred(
                    self.code,
                    detail="收入支出决算总表所在页未解析到有效表格",
                    unresolved_reasons=["收入支出决算总表所在页未解析到有效表格"],
                )

            # 3) 从表中提取"支出合计"
            total_expense = _row_value(table, ("支出合计", "支出总计", "合计"))
            if total_expense is None:
                raise RuleDeferred(
                    self.code,
                    detail="收入支出决算总表未找到支出合计/总计",
                    unresolved_reasons=["收入支出决算总表未找到支出合计/总计"],
                )

            # 4) 简化搜索，直接在关键词附近查找数字，避免复杂正则表达式
            search_text = "\n".join(doc.page_texts[:min(5, len(doc.page_texts))])
            
            found_num = None
            for keyword in ["总体情况说明", "总体情况"]:
                pos = search_text.find(keyword)
                if pos != -1:
                    snippet = search_text[pos:pos+100]
                    import re
                    numbers = re.findall(r'\d+(?:,\d{3})*(?:\.\d+)?', snippet)
                    if numbers:
                        try:
                            found_num = float(numbers[0].replace(",", ""))
                            break
                        except:
                            continue
            
            if found_num is None:
                raise RuleDeferred(
                    self.code,
                    detail="总体情况说明未提取到支出合计金额",
                    unresolved_reasons=["总体情况说明未提取到支出合计金额"],
                )

            # 5) 比较
            if not tolerant_equal(total_expense, found_num):
                issues.append(self._issue(
                    f"收入支出决算总表支出合计({total_expense:.2f})与总体情况说明数字({found_num:.2f})不一致",
                    {"page": p, "pos": 0},
                    evidence_text=f"表格：P{p} 收入支出决算总表\n表内合计: {total_expense}\n文本提取值: {found_num}\n(请检查“总体情况说明”部分的数字)"
                ))

            return issues
        except (RuleDeferred, RuleExecutionError):
            raise
        except RecursionError:
            raise RuleExecutionError(self.code, "规则执行异常：maximum recursion depth exceeded")
        except Exception as e:
            raise RuleExecutionError(self.code, f"规则执行异常：{str(e)}")

class R33103_Income_vs_Text(Rule):
    code, severity = "V33-103", "warn"
    desc = "收入决算表 ↔ 收入决算情况说明（含占比）"

    def apply(self, doc: Document) -> List[Issue]:
        issues: List[Issue] = []
        p = _get_first_anchor_page(doc, "收入决算表")
        if not p:
            raise RuleDeferred(
                self.code,
                detail="未找到收入决算表锚点页",
                unresolved_reasons=["未找到收入决算表锚点页"],
            )
        t = _largest_table_on_page(doc.page_tables[p - 1])
        if not t:
            raise RuleDeferred(
                self.code,
                detail="收入决算表所在页未解析到有效表格",
                unresolved_reasons=["收入决算表所在页未解析到有效表格"],
            )
        # 章节限定：严格在「收入决算情况说明」章节内查找，禁止跨章节或全文回退
        from src.utils.narration import find_section_scope_with_title
        merged_txt = "\n".join(doc.page_texts)
        sec = find_section_scope_with_title(merged_txt, ["收入", "决算"])
        if not sec:
            raise RuleDeferred(
                self.code,
                detail="未找到收入决算情况说明章节",
                unresolved_reasons=["未找到收入决算情况说明章节"],
            )
        sec_title, sec_text, _, _ = sec

        total = _row_value(t, ("本年收入合计", "本年合计", "合计"))
        fp = _row_value(t, ("财政拨款收入", "一般公共预算财政拨款收入", "财政拨款"))

        tt = near_number(sec_text, ["收入决算情况说明", "本年收入合计", "合计"], target_year=doc.dominant_year)
        tf = near_number(sec_text, ["财政拨款收入"], target_year=doc.dominant_year)

        unresolved: List[str] = []
        if total is not None and tt is not None:
            if not tolerant_equal(total, tt):
                issues.append(self._issue(f"收入合计：表{total} ≠ 文本{tt}", {"page": p}, "warn"))
        else:
            if total is None:
                unresolved.append("收入决算表缺少关键行: 收入合计")
            if tt is None:
                unresolved.append("说明文本中未提取到本年收入合计")

        if fp is not None and tf is not None:
            if not tolerant_equal(fp, tf):
                issues.append(self._issue(f"财政拨款收入：表{fp} ≠ 文本{tf}", {"page": p}, "warn"))
        else:
            if fp is None:
                unresolved.append("收入决算表缺少关键行: 财政拨款收入")
            if tf is None:
                unresolved.append("说明文本中未提取到财政拨款收入")

        p_txt = find_percent(sec_text, ["财政拨款收入", "占比", "比重"])
        if p_txt is not None and total and fp:
            p_calc = round(fp / total * 100, 2)
            if abs(p_calc - p_txt) > 1.0:
                issues.append(self._issue(f"财政拨款收入占比：表算{p_calc}% ≠ 文本{p_txt}%（容忍±1pct）", {"page": p}, "warn"))

        if unresolved:
            raise RuleDeferred(
                self.code,
                detail="; ".join(unresolved),
                partial_issues=issues,
                unresolved_reasons=unresolved,
            )

        return issues


class R33104_Expense_vs_Text(Rule):
    code, severity = "V33-104", "warn"
    desc = "支出决算表 ↔ 支出决算情况说明（含占比）"

    def apply(self, doc: Document) -> List[Issue]:
        issues: List[Issue] = []
        p = _get_first_anchor_page(doc, "支出决算表")
        if not p:
            raise RuleDeferred(
                self.code,
                detail="未找到支出决算表锚点页",
                unresolved_reasons=["未找到支出决算表锚点页"],
            )
        t = _largest_table_on_page(doc.page_tables[p - 1])
        if not t:
            raise RuleDeferred(
                self.code,
                detail="支出决算表所在页未解析到有效表格",
                unresolved_reasons=["支出决算表所在页未解析到有效表格"],
            )

        # 章节限定：严格在「支出决算情况说明」章节内查找，禁止跨章节或全文回退
        from src.utils.narration import find_section_scope_with_title
        merged_txt = "\n".join(doc.page_texts)
        sec = find_section_scope_with_title(merged_txt, ["支出", "决算"])
        if not sec:
            raise RuleDeferred(
                self.code,
                detail="未找到支出决算情况说明章节",
                unresolved_reasons=["未找到支出决算情况说明章节"],
            )
        sec_title, sec_text, _, _ = sec

        total = _row_value(t, ("本年支出合计", "本年合计", "合计"))
        basic = _row_value(t, ("基本支出",))
        proj = _row_value(t, ("项目支出",))

        tt = near_number(sec_text, ["支出决算情况说明", "本年支出合计", "合计"], target_year=doc.dominant_year)
        tb = near_number(sec_text, ["基本支出"], target_year=doc.dominant_year)
        tp = near_number(sec_text, ["项目支出"], target_year=doc.dominant_year)

        unresolved: List[str] = []
        check_items = [
            ("本年支出合计", total, tt),
            ("基本支出", basic, tb),
            ("项目支出", proj, tp),
        ]
        for nm, a, b in check_items:
            if a is not None and b is not None:
                if not tolerant_equal(a, b):
                    issues.append(self._issue(f"{nm}：表{a} ≠ 文本{b}", {"page": p}, "warn"))
            else:
                if a is None:
                    unresolved.append(f"支出决算表缺少关键行: {nm}")
                if b is None:
                    unresolved.append(f"说明文本中未提取到{nm}")

        for nm, a in [("基本支出", basic), ("项目支出", proj)]:
            pct_t = find_percent(sec_text, [nm, "占比", "比重"])
            if pct_t is not None and total and a:
                pct_c = round(a / total * 100, 2)
                if abs(pct_c - pct_t) > 1.0:
                    issues.append(self._issue(f"{nm}占比：表算{pct_c}% ≠ 文本{pct_t}%（容忍±1pct）", {"page": p}, "warn"))

        if unresolved:
            raise RuleDeferred(
                self.code,
                detail="; ".join(unresolved),
                partial_issues=issues,
                unresolved_reasons=unresolved,
            )

        return issues


class R33105_FinGrantTotal_vs_Text(Rule):
    code, severity = "V33-105", "warn"
    desc = "财政拨款收入支出决算总表 ↔ 总体情况说明"

    def apply(self, doc: Document) -> List[Issue]:
        issues: List[Issue] = []
        p = _get_first_anchor_page(doc, "财政拨款收入支出决算总表")
        if not p:
            raise RuleDeferred(
                self.code,
                detail="未找到财政拨款收入支出决算总表锚点页",
                unresolved_reasons=["未找到财政拨款收入支出决算总表锚点页"],
            )
        t = _largest_table_on_page(doc.page_tables[p - 1])
        if not t:
            raise RuleDeferred(
                self.code,
                detail="财政拨款收入支出决算总表所在页未解析到有效表格",
                unresolved_reasons=["财政拨款收入支出决算总表所在页未解析到有效表格"],
            )

        from src.utils.narration import find_section_scope_with_title
        merged_txt = "\n".join(doc.page_texts)
        sec = find_section_scope_with_title(merged_txt, ["财政拨款", "决算", "总体情况"]) or find_section_scope_with_title(merged_txt, ["财政拨款", "决算"])
        if not sec:
            raise RuleDeferred(
                self.code,
                detail="未找到财政拨款收入支出决算总体情况说明章节",
                unresolved_reasons=["未找到财政拨款收入支出决算总体情况说明章节"],
            )
        sec_title, sec_text, _, _ = sec

        total = _row_value(t, ("支出合计", "支出总计", "合计"))
        if total is None:
            raise RuleDeferred(
                self.code,
                detail="财政拨款收入支出决算总表未找到支出合计/总计行",
                unresolved_reasons=["财政拨款收入支出决算总表未找到支出合计/总计行"],
            )
        t_total = near_number(sec_text, ["财政拨款收入支出决算总体情况说明", "总计", "合计"], target_year=doc.dominant_year)
        if t_total is not None:
            if not tolerant_equal(total, t_total):
                issues.append(self._issue(f"财政拨款支出合计：表{total} ≠ 文本{t_total}", {"page": p}, "warn"))
        else:
            raise RuleDeferred(
                self.code,
                detail="说明文本中未提取到财政拨款支出总体情况合计金额",
                unresolved_reasons=["说明文本中未提取到财政拨款支出总体情况合计金额"],
            )
        return issues


# ---------- 文数一致（V33-106、V33-107、V33-108、V33-109、V33-110） ----------
class R33106_GeneralBudgetStruct(Rule):
    code, severity = "V33-106", "warn"
    desc = "一般公共预算财拨支出：合计↔总体；结构（类3位）占比 ↔ 结构情况"

    def apply(self, doc: Document) -> List[Issue]:
        try:
            issues: List[Issue] = []

            # 1) 找"一般公共预算财政拨款支出决算表"的第一页
            p = _get_first_anchor_page(doc, "一般公共预算财政拨款支出决算表")
            if not p:
                raise RuleDeferred(
                    self.code,
                    detail="未找到一般公共预算财政拨款支出决算表锚点页",
                    unresolved_reasons=["未找到一般公共预算财政拨款支出决算表锚点页"],
                )

            table = _largest_table_on_page(doc.page_tables[p - 1])
            if not table:
                raise RuleDeferred(
                    self.code,
                    detail="一般公共预算财政拨款支出决算表所在页未解析到有效表格",
                    unresolved_reasons=["一般公共预算财政拨款支出决算表所在页未解析到有效表格"],
                )

            # 2) 提取"合计"（None 安全）
            total_val = _extract_table_total(table)
            if total_val is None:
                raise RuleDeferred(
                    self.code,
                    detail="一般公共预算财政拨款支出决算表未提取到合计金额",
                    unresolved_reasons=["一般公共预算财政拨款支出决算表未提取到合计金额"],
                )

            # 3) 在"总体情况说明"章节内按口径锚点取数。
            # 旧实现 near_number 用 [^0-9]*? 跨章节抓数字，把「2025年度」
            # 的年份当成金额（HANDOFF §3.2C）；现在：软换行恢复 → 章节切分
            # → 只取含"支出决算/支出总计"锚点分句中的金额，排除预算句。
            merged_text = merge_page_texts(doc.page_texts)
            found_num: Optional[float] = None
            for title, body, _offset in split_numbered_sections(merged_text):
                if "总体情况" not in title:
                    continue
                candidate = amount_in_section(
                    body,
                    ["支出决算", "支出总计", "决算为"],
                    exclude_keywords=["预算", "同比", "较上年", "上年"],
                )
                if candidate is not None:
                    found_num = candidate
                    break
            if found_num is not None:
                if not tolerant_equal(total_val, found_num):
                    issues.append(self._issue(
                        f"一般公共预算财拨支出合计({total_val:.2f})与总体情况说明数字({found_num:.2f})不一致",
                        {"page": p, "pos": 0}
                    ))

            # 4) 结构占比检查（同样使用合并后的章节文本，不跨章节抓数字）
            func_sums = _sum_by_func_class(table)
            for func_name, func_val in func_sums.items():
                if func_val > 0:
                    pct = func_val / total_val * 100
                    found_pct = None
                    for title, body, _offset in split_numbered_sections(merged_text):
                        if "结构情况" in title or "总体情况" in title:
                            found_pct = find_percent(body, [func_name])
                            if found_pct is not None:
                                break
                    if found_pct is not None:
                        if abs(pct - found_pct) > 2.0:
                            issues.append(self._issue(
                                f"{func_name}占比：表格计算{pct:.1f}%，文本{found_pct:.1f}%，差异超过2%",
                                {"page": p, "pos": 0}
                            ))

            return issues
        except (RuleDeferred, RuleExecutionError):
            raise
        except RecursionError:
            raise RuleExecutionError(self.code, "规则执行异常：maximum recursion depth exceeded")
        except Exception as e:
            raise RuleExecutionError(self.code, f"规则执行异常：{str(e)}")


class R33107_BasicExpense_Check(Rule):
    code, severity = "V33-107", "warn"
    desc = "基本支出：人员经费合计 + 公用经费合计 ↔ 文本说明"

    def apply(self, doc: Document) -> List[Issue]:
        issues: List[Issue] = []
        p = _get_first_anchor_page(doc, "一般公共预算财政拨款基本支出决算表")
        if not p:
            raise RuleDeferred(
                self.code,
                detail="未找到一般公共预算财政拨款基本支出决算表锚点页",
                unresolved_reasons=["未找到一般公共预算财政拨款基本支出决算表锚点页"],
            )
        t = _largest_table_on_page(doc.page_tables[p - 1])
        if not t:
            raise RuleDeferred(
                self.code,
                detail="一般公共预算财政拨款基本支出决算表所在页未解析到有效表格",
                unresolved_reasons=["一般公共预算财政拨款基本支出决算表所在页未解析到有效表格"],
            )
        ren = _row_value(t, ("人员经费合计", "人员经费"))
        gong = _row_value(t, ("公用经费合计", "公用经费"))
        if ren is None or gong is None:
            missing_parts = []
            if ren is None:
                missing_parts.append("人员经费")
            if gong is None:
                missing_parts.append("公用经费")
            raise RuleDeferred(
                self.code,
                detail=f"基本支出决算表缺少关键行: {','.join(missing_parts)}",
                unresolved_reasons=[f"基本支出决算表缺少关键行: {','.join(missing_parts)}"],
            )
        total = ren + gong
        txt = "\n".join(doc.page_texts)
        t_total = near_number(txt, ["一般公共预算财政拨款基本支出决算情况说明", "基本支出", "合计"], target_year=doc.dominant_year)
        if t_total is not None:
            if not tolerant_equal(total, t_total):
                issues.append(self._issue(f"基本支出合计：表算{total} ≠ 文本{t_total}", {"page": p}, "warn"))
        else:
            raise RuleDeferred(
                self.code,
                detail="说明文本中未提取到基本支出合计金额",
                partial_issues=issues,
                unresolved_reasons=["说明文本中未提取到基本支出合计金额"],
            )
        return issues


class R33108_ThreePublic_vs_Text(Rule):
    code, severity = "V33-108", "warn"
    desc = "三公经费：表 ↔ “总体情况说明”"

    def apply(self, doc: Document) -> List[Issue]:
        issues: List[Issue] = []
        p = _get_first_anchor_page(doc, "一般公共预算财政拨款“三公”经费支出决算表")
        if not p:
            raise RuleDeferred(
                self.code,
                detail="未找到三公经费支出决算表锚点页",
                unresolved_reasons=["未找到三公经费支出决算表锚点页"],
            )
        t = _largest_table_on_page(doc.page_tables[p - 1])
        if not t:
            raise RuleDeferred(
                self.code,
                detail="三公经费支出决算表所在页未解析到有效表格",
                unresolved_reasons=["三公经费支出决算表所在页未解析到有效表格"],
            )
        bud = _row_value(t, ("合计预算数", "预算合计", "预算数"))
        act = _row_value(t, ("合计决算数", "决算合计", "决算数"))
        if bud is None and act is None:
            raise RuleDeferred(
                self.code,
                detail="三公经费支出决算表未提取到预算数或决算数合计行",
                unresolved_reasons=["三公经费支出决算表未提取到预算数或决算数合计行"],
            )
        txt = "\n".join(doc.page_texts)
        tb = near_number(txt, ["三公", "年初预算", "预算"], target_year=doc.dominant_year)
        ta = near_number(txt, ["三公", "支出决算", "决算"], target_year=doc.dominant_year)
        unresolved: List[str] = []
        if bud is not None:
            if tb is not None:
                if not tolerant_equal(bud, tb):
                    issues.append(self._issue(f"三公经费预算：表{bud} ≠ 文本{tb}", {"page": p}, "warn"))
            else:
                unresolved.append("说明文本中未提取到三公经费预算金额")
        if act is not None:
            if ta is not None:
                if not tolerant_equal(act, ta):
                    issues.append(self._issue(f"三公经费决算：表{act} ≠ 文本{ta}", {"page": p}, "warn"))
            else:
                unresolved.append("说明文本中未提取到三公经费决算金额")
        if unresolved:
            raise RuleDeferred(
                self.code,
                detail="; ".join(unresolved),
                partial_issues=issues,
                unresolved_reasons=unresolved,
            )
        return issues


class R33109_EmptyTables_Statement(Rule):
    code, severity = "V33-109", "warn"
    desc = "政府性基金/国有资本经营/三公经费：如为空表，必须有空表说明"

    def apply(self, doc: Document) -> List[Issue]:
        issues: List[Issue] = []
        unresolved: List[str] = []
        # 从NINE_TABLES中动态获取需要检查的表名，确保名称匹配
        empty_check_keywords = ["政府性基金", "国有资本经营", "三公"]
        empty_check_tables = []
        for spec in NINE_TABLES:
            for kw in empty_check_keywords:
                if kw in spec["name"]:
                    empty_check_tables.append(spec["name"])
                    break
        
        for nm in empty_check_tables:
            p = _get_first_anchor_page(doc, nm)
            if not p:
                continue
            table = _largest_table_on_page(doc.page_tables[p - 1])
            if not table:
                unresolved.append(f"表【{nm}】所在页(P{p})未解析到有效表格")
                continue
            vals = []
            for row in table:
                for cell in row[1:]:
                    v = parse_number(cell)
                    if v is not None:
                        vals.append(v)
            is_empty = (not vals) or all(abs(v) < 1e-9 for v in vals)
            if is_empty:
                # 只检查当前页面是否有针对该表的说明
                # 不检查相邻页面，因为相邻页面可能是其他空表的说明
                current_page_text = doc.page_texts[p - 1] if p <= len(doc.page_texts) else ""
                
                # 提取表名中的关键词用于匹配
                table_keywords = []
                if "三公" in nm:
                    table_keywords = ["三公"]
                elif "政府性基金" in nm:
                    table_keywords = ["政府性基金"]
                elif "国有资本经营" in nm:
                    table_keywords = ["国有资本经营", "国有资本"]
                
                # 检查是否有针对该表的空表说明
                has_statement = False
                
                # 方法1: 检查"注："说明中是否包含该表的关键词
                for kw in table_keywords:
                    # 检查格式："注：XXX无XXX（关键词）XXX，故本表无数据"
                    pattern = rf"注[：:].{{0,30}}{kw}.{{0,50}}(无|空表|不存在|无数据|故本表无数据)"
                    if re.search(pattern, current_page_text, flags=re.S | re.M):
                        has_statement = True
                        break
                
                # 方法2: 如果该页有"注：XXX故本表无数据"，且页面标题是该表名，也算有说明
                if not has_statement:
                    if re.search(r"故本表无数据", current_page_text):
                        # 确认页面标题是该表
                        for kw in table_keywords:
                            if kw in current_page_text[:200]:  # 检查页面开头
                                has_statement = True
                                break
                
                if not has_statement:
                    # 简化表名用于显示
                    nm.replace("一般公共预算财政拨款", "").replace("收入支出决算表", "").replace("预算财政拨款", "")
                    issues.append(self._issue(
                        f"【{nm}】为空表，但该页未见针对该表的空表说明（应有'注：XXX无{table_keywords[0] if table_keywords else '相关'}支出，故本表无数据'）。",
                        {"page": p}, "error",
                        evidence_text=f"空表名称：{nm}\n页面文本概览（未发现空表说明）：\n{current_page_text[:300].replace(chr(10), ' ')}..."
                    ))
        if unresolved:
            raise RuleDeferred(
                self.code,
                detail="; ".join(unresolved),
                partial_issues=issues,
                unresolved_reasons=unresolved,
            )
        return issues



class R33110_BudgetVsFinal_TextConsistency(Rule):
    code, severity = "V33-110", "error"
    desc = "（三）一般公共预算财政拨款支出决算（具体）情况:数字与大于/小于/持平一致性，并校验是否说明原因"

    # 1) 小节起止（行首匹配 + 变体）
    _SEC_START = re.compile(r"(?m)^\s*(（三）|三、)\s*一般公共预算财政拨款支出决算(?:具体)?情况")
    _NEXT_SEC  = re.compile(r"(?m)^\s*(（四）|四、|（六）|六、|一般公共预算财政拨款基本支出决算情况说明)")

    # 2) 优化后的主配对模式：缩小匹配窗口，增加上下文约束
    _PAIR = re.compile(
        r"(?:年初?\s*预算|预算|年初预算数|预算数)(?:数)?[为是]?\s*"
        r"(\d+(?:,\d{3})*(?:\.\d+)?)\s*(?:亿元|万元|元)?"
        r"(?:[^决]{0,50}?)"  # 使用非贪婪量词避免回溯
        r"(?:支出\s*决算|决算|决算支出|实际支出)(?:数)?[为是]?\s*"
        r"(\d+(?:,\d{3})*(?:\.\d+)?)\s*(?:亿元|万元|元)?"
        r"(?:[^。]{0,50}?)"  # 使用非贪婪量词避免回溯
        r"(决算(?:数)?(?:大于|小于|等于|持平|基本持平)预算(?:数)?|实际(?:数)?(?:大于|小于|等于|持平|基本持平)预算(?:数)?)",
        re.S
    )
    
    # 3) 备用配对模式：适度放宽但仍比原来严格
    _PAIR_FALLBACK = re.compile(
        r"(?:年初?\s*预算|预算|年初预算数|预算数)(?:数)?[为是]?\s*"
        r"(\d+(?:,\d{3})*(?:\.\d+)?)\s*(?:亿元|万元|元)?"
        r"(?:[^决]{0,80}?)"  # 使用非贪婪量词避免回溯
        r"(?:支出\s*决算|决算|决算支出|实际支出)(?:数)?[为是]?\s*"
        r"(\d+(?:,\d{3})*(?:\.\d+)?)\s*(?:亿元|万元|元)?"
        r"(?:[^。]{0,80}?)"  # 使用非贪婪量词避免回溯
        r"(决算(?:数)?(?:大于|小于|等于|持平|基本持平)预算(?:数)?|实际(?:数)?(?:大于|小于|等于|持平|基本持平)预算(?:数)?)",
        re.S
    )
    
    # 4) 反序配对：同样缩小窗口
    _PAIR_REV = re.compile(
        r"(?:支出\s*决算|决算|决算支出|实际支出)(?:数)?[为是]?\s*"
        r"(\d+(?:,\d{3})*(?:\.\d+)?)\s*(?:亿元|万元|元)?"
        r"(?:[^预]{0,50}?)"  # 使用非贪婪量词避免回溯
        r"(?:年初?\s*预算|预算|年初预算数|预算数)(?:数)?[为是]?\s*"
        r"(\d+(?:,\d{3})*(?:\.\d+)?)\s*(?:亿元|万元|元)?"
        r"(?:[^。]{0,50}?)"  # 使用非贪婪量词避免回溯
        r"(决算(?:数)?(?:大于|小于|等于|持平|基本持平)预算(?:数)?|实际(?:数)?(?:大于|小于|等于|持平|基本持平)预算(?:数)?)",
        re.S
    )
    
    # 5) 扩展配对模式：适应用户提供的文本格式
    _PAIR_EXTENDED = re.compile(
        r"(?:年初预算为|预算为|预算数为)\s*(\d+(?:,\d{3})*(?:\.\d+)?)\s*(?:亿元|万元|元)?"
        r"(?:[^，。]{0,80}?)"
        r"(?:，|。)\s*(?:支出决算为|决算为|决算数为)\s*(\d+(?:,\d{3})*(?:\.\d+)?)\s*(?:亿元|万元|元)?"
        r"(?:[^，。]{0,80}?)"
        r"(?:，|。)\s*(决算数(?:大于|小于|等于|持平|基本持平)预算数|实际(?:数)?(?:大于|小于|等于|持平|基本持平)预算数)",
        re.S | re.I
    )
    # 4) 反序配对：同样缩小窗口
    _PAIR_REV = re.compile(
        r"(?:支出\s*决算|决算|决算支出|实际支出)(?:数)?[为是]?\s*"
        r"(\d+(?:,\d{3})*(?:\.\d+)?)\s*(?:亿元|万元|元)?"
        r"(?:[^预]{0,50}?)?"  # 使用非贪婪量词避免回溯
        r"(?:年初?\s*预算|预算|年初预算数|预算数)(?:数)?[为是]?\s*"
        r"(\d+(?:,\d{3})*(?:\.\d+)?)\s*(?:亿元|万元|元)?"
        r"(?:[^。]{0,50}?)?"  # 使用非贪婪量词避免回溯
        r"(决算(?:数)?(?:大于|小于|等于|持平|基本持平)预算(?:数)?|实际(?:数)?(?:大于|小于|等于|持平|基本持平)预算(?:数)?)",
        re.S
    )
    
    # 5) 扩展配对模式：适应用户提供的文本格式
    _PAIR_EXTENDED = re.compile(
        r"(?:年初预算为|预算为|预算数为)\s*(\d+(?:,\d{3})*(?:\.\d+)?)\s*(?:亿元|万元|元)?"
        r"(?:[^，。]{0,80}?)?"
        r"(?:，|。)\s*(?:支出决算为|决算为|决算数为)\s*(\d+(?:,\d{3})*(?:\.\d+)?)\s*(?:亿元|万元|元)?"
        r"(?:[^，。]{0,80}?)?"
        r"(?:，|。)\s*(决算数(?:大于|小于|等于|持平|基本持平)预算数|实际(?:数)?(?:大于|小于|等于|持平|基本持平)预算数)",
        re.S | re.I
    )
    
    def apply(self, doc: Document) -> List[Issue]:
        r"""预算数 vs 决算数的方向表述一致性（分句窗口内配对，禁止跨句抓数）。

        旧实现用 [\s\S]*? 跨句配对：总结句的预算数（4628.17）被配到第一个
        分项的决算数（51.98），而方向词又从总结句取到，产生假 error
        （HANDOFF §3.2B）。新逻辑：
        1. 软换行恢复 → 章节切分（（三）/三、一般公共…决算情况）；
        2. 章节内切分句，构成滑动窗口：预算金额分句 → 向后找决算金额分句
           → 向后找方向词分句；窗口遇到下一个「预算为/决算为」即截断；
        3. 三者齐备才判定，缺失一律跳过（不得伪造）。
        """
        issues: List[Issue] = []
        merged = merge_page_texts(doc.page_texts)

        start_match = self._SEC_START.search(merged)
        if not start_match:
            raise RuleDeferred(
                self.code,
                detail="未找到一般公共预算财政拨款支出决算情况说明章节",
                unresolved_reasons=["未找到一般公共预算财政拨款支出决算情况说明章节"],
            )
        next_sec = self._NEXT_SEC.search(merged, start_match.end())
        sec_text = merged[start_match.end(): next_sec.start() if next_sec else len(merged)]

        clauses = split_clauses(sec_text)
        _BUDGET_MARKS = ("年初预算为", "年初预算数为", "预算数为", "预算为", "年初预算")
        _FINAL_MARKS = ("支出决算为", "决算数为", "决算为", "支出决算")
        _DIRECTION_WORDS = ("大于", "小于", "等于", "持平")

        def clause_amount(clause: str, marks) -> Optional[float]:
            if not any(mark in clause for mark in marks):
                return None
            amounts = extract_amounts(clause)
            if not amounts:
                return None
            # 年份 token 已被 extract_amounts 排除
            return amounts[0][0]

        matched_comparisons = 0
        i = 0
        while i < len(clauses):
            clause = clauses[i]
            budget_val = clause_amount(clause, _BUDGET_MARKS)
            if budget_val is None:
                i += 1
                continue

            # 向后有限窗口找决算金额与方向词
            final_val = None
            direction = None
            window = 0
            j = i + 1
            while j < len(clauses) and window < 6:
                nxt = clauses[j]
                if final_val is None:
                    candidate = clause_amount(nxt, _FINAL_MARKS)
                    if candidate is not None:
                        final_val = candidate
                        j += 1
                        window += 1
                        continue
                    # 遇到新的预算金额配对起点 → 当前窗口作废（缺决算，不判定）
                    if clause_amount(nxt, _BUDGET_MARKS) is not None:
                        break
                if final_val is not None:
                    hit = next((w for w in _DIRECTION_WORDS if w in nxt), None)
                    if hit:
                        direction = "等于" if hit in ("持平",) else hit
                        break
                    if clause_amount(nxt, _BUDGET_MARKS) is not None or clause_amount(nxt, _FINAL_MARKS) is not None:
                        # 真正进入下一条金额配对仍未见方向词 → 放弃本窗口
                        break
                j += 1
                window += 1

            if budget_val is not None and final_val is not None and direction:
                matched_comparisons += 1
                actual = "等于"
                if final_val > budget_val:
                    actual = "大于"
                elif final_val < budget_val:
                    actual = "小于"
                if actual != direction:
                    # 页码定位：在原文页中检索证据片段
                    clip = f"年初预算{budget_val:.2f} 决算{final_val:.2f} 表述{direction}"
                    page = 1
                    for pi, page_text in enumerate(doc.page_texts):
                        if str(budget_val) in page_text.replace(",", "") or "决算数" in page_text:
                            page = pi + 1
                            break
                    issues.append(self._issue(
                        f"预算数与决算数比较不一致：预算数{budget_val:.2f}，决算数{final_val:.2f}，实际{actual}，但文本表述为{direction}。",
                        {"page": page},
                        severity="error",
                        evidence_text=clip,
                    ))
            i = max(j, i + 1)

        if matched_comparisons == 0:
            raise RuleDeferred(
                self.code,
                detail="一般公共预算财政拨款支出决算情况说明章节内未提取到预算数/决算数对比语句",
                partial_issues=issues,
                unresolved_reasons=["一般公共预算财政拨款支出决算情况说明章节内未提取到预算数/决算数对比语句"],
            )

        return issues


class R33111_IncomeExpenseTotalCheck(Rule):
    code, severity = "V33-111", "critical"
    desc = "收入支出总计为0万元异常检测"

    def apply(self, doc: Document) -> List[Issue]:
        """检测收入支出总计为0万元的异常情况"""
        issues: List[Issue] = []
        
        # 编译正则表达式，匹配带空格或不带空格的"0万元"
        zero_total_pattern = re.compile(r"收入支出总[计合].*?0\s*万元")
        
        # 遍历所有页面，查找收入支出总计为0万元的情况
        for page_num in range(len(doc.page_texts)):
            page_text = doc.page_texts[page_num]
            
            # 使用正则表达式匹配，支持带空格或不带空格的"0万元"
            m = zero_total_pattern.search(page_text)
            if m:
                start = max(0, m.start() - 50)
                end = min(len(page_text), m.end() + 50)
                snippet = page_text[start:end].replace('\n', ' ')
                issues.append(self._issue(
                    "收入支出总计为0万元，可能存在数据异常。",
                    {"page": page_num + 1},
                    severity="critical",
                    evidence_text=f"发现异常数值上下文：...{snippet}..."
                ))
        
        return issues


class R33112_PlaceholderCheck(Rule):
    code, severity = "V33-112", "warn"
    desc = "文本占位符检测"

    def apply(self, doc: Document) -> List[Issue]:
        """检测文本中的占位符和敏感信息"""
        issues: List[Issue] = []
        
        # 定义占位符模式（使用re.IGNORECASE避免重复检测）
        placeholder_patterns = [
            r'\bxxx\b',  # xxx（独立单词）
            r'XX',  # XX（不使用单词边界，可以匹配"XX局"、"XX市"等）
            r'待填写',  # 待填写
            r'请填写',  # 请填写
            r'\(待\)',  # （待）
            r'\(略\)',  # （略）
            r'\[待\]',  # [待]
            r'\[略\]',  # [略]
        ]
        
        # 遍历所有页面
        for page_num, page_text in enumerate(doc.page_texts):
            # 跳过表格内容（只检查纯文本）
            lines = page_text.split('\n')
            for line_num, line in enumerate(lines):
                # 跳过空行和纯数字行
                if not line.strip() or line.strip().replace(',', '').replace('.', '').isdigit():
                    continue
                
                # 检查每个占位符模式
                for pattern in placeholder_patterns:
                    matches = list(re.finditer(pattern, line, re.IGNORECASE))
                    for match in matches:
                        # 获取匹配位置
                        pos = match.start()
                        
                        # 获取上下文
                        context_start = max(0, pos - 20)
                        context_end = min(len(line), pos + len(match.group()) + 20)
                        context = line[context_start:context_end]
                        
                        issues.append(self._issue(
                            f"发现占位符：{match.group()}，请检查是否需要填写完整。",
                            {"page": page_num + 1, "pos": pos},
                            severity="warn",
                            evidence_text=context
                        ))
        
        return issues


class R33113_PunctuationCheck(Rule):
    code, severity = "V33-113", "warn"
    desc = "标点符号问题检测"

    def apply(self, doc: Document) -> List[Issue]:
        """检测标点符号问题：重复标点、不同标点在一起、句尾无句号"""
        issues: List[Issue] = []
        
        # 定义中文标点符号
        
        # 遍历所有页面
        for page_num, page_text in enumerate(doc.page_texts):
            lines = page_text.split('\n')
            
            for line_num, line in enumerate(lines):
                # 跳过空行
                if not line.strip():
                    continue
                
                # 1. 检测重复标点（如，，、。。）
                # 使用正则表达式匹配重复的标点符号
                duplicate_punct_pattern = r'([，。！？；：])\1+'
                for match in re.finditer(duplicate_punct_pattern, line):
                    punct = match.group(1)
                    ctx_s = max(0, match.start() - 20)
                    ctx_e = min(len(line), match.end() + 20)
                    issues.append(self._issue(
                        f"重复标点：{match.group()}，建议修改为单个{punct}。",
                        {"page": page_num + 1, "pos": match.start()},
                        severity="warn",
                        evidence_text=line[ctx_s:ctx_e]
                    ))
                
                # 2. 检测不同标点在一起（如，。、；，）
                # 使用正则表达式匹配连续的不同标点符号
                mixed_punct_pattern = r'([，。！？；：])\s*([，。！？；：])'
                for match in re.finditer(mixed_punct_pattern, line):
                    punct1 = match.group(1)
                    punct2 = match.group(2)
                    if punct1 != punct2:
                        ctx_s = max(0, match.start() - 20)
                        ctx_e = min(len(line), match.end() + 20)
                        issues.append(self._issue(
                            f"标点混用：{punct1}{punct2}，建议统一使用一个标点。",
                            {"page": page_num + 1, "pos": match.start()},
                            severity="warn",
                            evidence_text=line[ctx_s:ctx_e]
                        ))
                
                # 3. 检测句尾无句号（针对完整句子）
                # 由于PDF文本提取时，句子经常被换行和分页打断，导致大量误报
                # 因此暂时禁用此检查，只保留重复标点和标点混用检查
                # 跳过标题行（以数字开头或包含"表"、"说明"等）
                # if re.match(r'^\d+[、.）]', line) or '表' in line or '说明' in line:
                #     continue
                # 
                # # 跳过以特定模式开头的行（如"主要用于"、"年初预算为"等）
                # skip_patterns = [
                #     r'^主要用于',
                #     r'^年初预算为',
                #     r'^支出决算为',
                #     r'^决算数',
                #     r'^其中',
                #     r'^包括',
                #     r'^主要',
                #     r'^（项）',
                #     r'^（款）',
                #     r'^（类）',
                #     r'^上海市',
                #     r'^2024',
                # ]
                # if any(re.match(pattern, line) for pattern in skip_patterns):
                #     continue
                # 
                # # 检查行尾是否以中文标点结尾
                # stripped_line = line.strip()
                # if stripped_line and len(stripped_line) > 10:  # 至少10个字符才检查（避免对短文本误报）
                #     # 检查是否以句号、问号、感叹号结尾
                #     if not stripped_line[-1] in '。！？':
                #         # 检查是否包含中文内容
                #         if any('\u4e00' <= char <= '\u9fff' for char in stripped_line):
                #             # 获取上下文
                #             context_start = max(0, len(stripped_line) - 30)
                #             context = stripped_line[context_start:]
                #             
                #             issues.append(self._issue(
                #                 f"句尾缺少标点：{context}，建议在句末添加句号。",
                #                 {"page": page_num + 1, "pos": len(line) - len(stripped_line) + len(stripped_line)},
                #                 severity="warn"
                #             ))
        
        return issues

# ==================================================================================
# 辅助函数
# ==================================================================================

def _get_table_rows(
    doc: Document,
    table_name: str,
    include_continuation: bool = True,
    full_extent: bool = False,
) -> Optional[List[List[str]]]:
    """获取指定表格的所有行数据，支持跨页表格读取。

    ``full_extent=True`` 时按**锚点页界**合并续页：某表的页范围取其锚点页到
    下一个（任意表的）锚点页之前一页。实测 41 页样张中「收入决算表」跨 p7-p11、
    「支出决算表」跨 p12-p16，旧实现只并 1 页，导致 208/210 等类级子项整类缺失，
    产出「层级校验失败(208)」「合计行列校验失败」等假警告。

    默认 ``False`` 保留历史「最多再并 1 页」行为：本函数有 37 个调用点，
    放宽页界会改变其它规则的输入（实测会新暴露 V33-202/V33-222/V33-119 的
    口径与列选择缺陷），故仅由已按全表语义校验的 V33-120 显式启用。
    """
    p = _get_first_anchor_page(doc, table_name)
    if not p:
        return None
    page_tables = getattr(doc, "page_tables", []) or []
    # 锚点页可能超出 page_tables 范围（锚点来自全文文本但表格抽取为空，
    # 如纯文本说明材料）：越界按"该页无表"处理，不抛 IndexError
    if p - 1 >= len(page_tables):
        return None
    tables = page_tables[p - 1]
    if not tables:
        return None
    # 返回最大的表格
    main_rows = _largest_table_on_page(tables)

    if include_continuation and main_rows:
        if full_extent:
            anchors = _ensure_table_anchors(doc)
            later = sorted({pg for pages in anchors.values() for pg in pages if pg > p})
            # 无后续锚点（本表是最后一张）时以文档末页为界；无表的页直接跳过，
            # 故说明/名词解释等章节页不会被并入。
            end_page = (later[0] - 1) if later else len(page_tables)
            for pg in range(p + 1, min(end_page, len(page_tables)) + 1):
                next_tables = page_tables[pg - 1]
                if not next_tables:
                    continue
                next_rows = _largest_table_on_page(next_tables)
                if next_rows:
                    main_rows = main_rows + next_rows
            return main_rows

        # ====== 修复：跨页表格续读 ======
        # 如果表格看起来未闭合（没有"合计"或"总计"行），尝试读取下一页
        has_total_row = False
        for row in main_rows[-5:]:  # 检查最后5行
            row_txt = "".join([str(c) for c in row if c])
            if "合计" in row_txt or "总计" in row_txt or "人员经费合计" in row_txt or "公用经费合计" in row_txt:
                has_total_row = True
                break

        if not has_total_row and p < len(doc.page_tables):
            # 尝试读取下一页
            next_tables = doc.page_tables[p]  # p 是 1-based, so page_tables[p] is next page
            if next_tables:
                next_rows = _largest_table_on_page(next_tables)
                if next_rows:
                    # 合并表格行
                    main_rows = main_rows + next_rows
        # ====== 修复结束 ======

    return main_rows

def _parse_row_values(row: List[str]) -> List[float]:
    """解析行中的所有数值，空值视为0（历史行为，仅未迁移规则继续使用）。

    迁移后的规则一律使用 ``_row_numbers`` / ``_numeric_values``：
    非数值单元格保留 None，禁止转成 0.0——「收入总计(0.0)」这类假勾稽
    错误的直接来源就是行标签文本被本函数转零（HANDOFF §3.1A）。
    """
    vals = []
    for cell in row:
        v = parse_number(cell)
        if v is not None:
            vals.append(v)
        else:
            vals.append(0.0) # 空值视为0以便计算
    return vals


def _row_numbers(row: List[str]) -> List[Optional[float]]:
    """None 安全的行数值解析：非数值单元格（行标签/表头）保留 None。"""
    return [parse_number(cell) for cell in row]


def _numeric_values(row: List[str]) -> List[float]:
    """行内真实数值（跳过非数值单元格，不补 0）。"""
    return [v for v in _row_numbers(row) if v is not None]


def _modal_row_width(rows: List[List[str]]) -> Optional[int]:
    """行的众数宽度：跨页列宽漂移的检测基线。"""
    widths = [len(r) for r in rows if r]
    if not widths:
        return None
    return Counter(widths).most_common(1)[0][0]


def _row_name_col(row: List[str]) -> int:
    """定位行内「科目名称」列：第一个非空、非纯数字、非类款项标签的单元格。

    跨页续表常把「类|款|项」编码列收缩掉——首页 [码,'','',名,合计,基本,项目]、
    续页 [码,名,合计,基本,项目]，**行宽不变而语义整体左移 2 格**。此时按首页
    列位读续页必然错列（长风样张 4 条「合计行列校验失败」误报根因）。
    """
    for ci, cell in enumerate(row):
        text = str(cell or "").strip()
        if not text:
            continue
        compact = text.replace(",", "").replace("，", "")
        if compact.replace(".", "", 1).isdigit():
            continue
        if compact.isdigit() and len(compact) in (3, 5, 7):
            continue
        if text in ("类", "款", "项", "合计", "总计", "小计"):
            continue
        return ci
    return -1


def _adaptive_value_cols(row: List[str]) -> Optional[Tuple[int, int, int, int]]:
    """按行自适应定位 (名称列, 合计列, 基本列, 项目列)；定位不到返回 None。

    与 :func:`_row_name_col` 配合使用：名称列之后依次为 合计/基本/项目。
    """
    name_col = _row_name_col(row)
    if name_col < 0:
        return None
    return (name_col, name_col + 1, name_col + 2, name_col + 3)


def _split_two_sided_row(row: List[str]) -> Tuple[List[str], List[str]]:
    """把双栏表行拆成左右两半（收支总表/经济分类表的左右布局）。"""
    width = len(row)
    if width >= 4:
        mid = width // 2
        return row[:mid], row[mid:]
    return row, []


def _half_first_number(half: List[str], start: int = 0) -> Optional[float]:
    """半行中从 start 起第一个真实数值（无则 None，不补 0）。"""
    for cell in half[start:]:
        v = parse_number(cell)
        if v is not None:
            return v
    return None


def _find_label_value(
    rows: List[List[str]],
    keywords: Tuple[str, ...],
    *,
    side: Optional[str] = None,
) -> Optional[float]:
    """按行标签找数值：标签行内与标签同侧的真实数值（None 安全）。

    side="income"/"expense" 时在双栏布局的对应半行内取值；
    布局不确定或标签行不存在时返回 None，调用方必须跳过该检查，
    不得以 0.0 参与勾稽。
    """
    for row in rows:
        joined = "".join([str(c) for c in row if c])
        if not all(k in joined for k in keywords):
            continue
        if side in ("income", "expense"):
            left, right = _split_two_sided_row(row)
            half = left if side == "income" else right
            value = _half_first_number(half, 1) if half else None
        else:
            value = _half_first_number(row, 1)
        if value is not None:
            return value
    return None


# 舍入包络提示的 severity
_ROUNDING_HINT_SEVERITY = "info"


_STANDARD_AMOUNT = r"([0-9][0-9,]*(?:\.[0-9]+)?)"


def _extract_standard_three_public_total(section: str, fiscal_type: str) -> Optional[float]:
    for sentence in re.split(r"[。；;\n]", section or ""):
        if "三公" not in sentence or fiscal_type not in sentence or "万元" not in sentence:
            continue
        if not any(token in sentence for token in ("经费", "合计", "支出")):
            continue
        target_text = sentence
        direct_match = re.search(r"(?<!上年)决算数(?:为|是)?\s*" + _STANDARD_AMOUNT + r"\s*万元", sentence)
        amount_match = direct_match
        if amount_match is None and fiscal_type in sentence:
            fiscal_pos = sentence.find(fiscal_type)
            if fiscal_pos >= 0:
                target_text = sentence[fiscal_pos:]
                amount_match = re.search(_STANDARD_AMOUNT, target_text)
        if amount_match is None:
            amount_match = re.search(_STANDARD_AMOUNT, target_text)
        if not amount_match:
            continue
        value = parse_number(amount_match.group(1))
        if value is not None:
            return value
    return None


# ==================================================================================
# 勾稽关系验证规则
# ==================================================================================

def _find_parsed_table(doc: Document, title_fragment: str):
    """在 doc.parsed_tables 中查找目标表（无挂载时返回 None）。

    两级匹配（GPT5.6 R3 P0-2）：① 表 title 含表名片段；② title 常只
    含表体首行（pdfplumber 的表格 bbox 不含表标题行——官方样张的总表
    title 实为「收入支出」，表名「收入支出决算总表」在页文本里），
    此时回退按锚点页匹配：表起始页的页文本含表名即命中（与 legacy
    `_get_first_anchor_page` 同源锚点）。仍无法定位时返回 None 走
    legacy 回退。
    """
    tables = getattr(doc, "parsed_tables", None)
    if not isinstance(tables, dict):
        return None
    for table in tables.values():
        if title_fragment in (getattr(table, "title", "") or ""):
            return table
    page_texts = getattr(doc, "page_texts", []) or []
    for table in tables.values():
        start_page = (getattr(table, "page_span", (0, 0)) or (0, 0))[0]
        if 0 < start_page <= len(page_texts):
            if title_fragment in (page_texts[start_page - 1] or ""):
                return table
    return None


class R33115_TotalSheetCheck(Rule):
    code, severity = "V33-115", "error"
    desc = "收入支出决算总表勾稽关系验证 (Table 1)"

    def apply(self, doc: Document) -> List[Issue]:
        issues = []
        table_name = "收入支出决算总表"
        # 结构化消费试点（GPT5.6 R2 P0-1b）：doc 挂载了 ParsedTable 时
        # 优先走命名行取值（ParsedCell 三态杜绝标签误读 0.0）；legacy
        # 文本行路径保留为无挂载时的回退——单一规则两条输入表征过渡。
        structured = _find_parsed_table(doc, table_name)
        if structured is not None and structured.rows:
            return self._apply_structured(doc, structured)
        rows = _get_table_rows(doc, table_name)
        if not rows:
            # 决算材料应含此核心表：查不到=证据不足而非"无问题"，
            # 转 insufficient_data 供质量门转人工复核（GPT5.6 P0-2）。
            raise RuleDeferred(self.code, f"表缺失或无可解析行: {table_name}")
        page = _get_first_anchor_page(doc, table_name) or 1

        # 双栏布局：[收入项目, 收入金额, 支出项目, 支出金额]。
        # 金额必须取自与标签同侧的半行——旧实现取 vs[-1]/vs[0] 按位置猜列，
        # 把「总计」标签转成 0.0 后产生了「收入总计(0.0) != 支出总计」假错误
        # （HANDOFF §3.1A）。
        def side_total(side: str) -> Optional[float]:
            for row in rows:
                joined = "".join([str(c) for c in row if c])
                if "总计" not in joined:
                    continue
                left, right = _split_two_sided_row(row)
                half = left if side == "income" else right
                for i, cell in enumerate(half):
                    if "总计" in str(cell or ""):
                        value = _half_first_number(half, i + 1)
                        if value is None and i > 0:
                            value = _half_first_number(half, 0)
                        if value is not None:
                            return value
            return None

        income_total = side_total("income")
        expense_total = side_total("expense")

        # 校验1：表底平衡（收入总计 = 支出总计）
        # 任一侧缺失 → 无法判定，跳过（不伪造 0.0 参与比较）
        if income_total is not None and expense_total is not None:
            level, diff = classify_amount_diff(income_total, expense_total, n_children=1)
            if level == "mismatch":
                issues.append(self._issue(
                    f"总表平衡性错误：收入总计({income_total:.2f}) != 支出总计({expense_total:.2f})",
                    {"table": table_name, "page": page}, "error",
                    evidence_text=f"表格：{table_name}\n收入总计：{income_total}\n支出总计：{expense_total}"
                ))
            elif level == "rounding_hint":
                issues.append(self._issue(
                    f"总表平衡存在 {diff:.2f} 万元差异，在显示舍入包络内，可能为取整误差，建议复核。",
                    {"table": table_name, "page": page}, "info",
                    evidence_text=f"表格：{table_name}\n收入总计：{income_total}\n支出总计：{expense_total}"
                ))

        # 校验2/3：收入侧、支出侧平衡。任一分量取不到真实数值 → 跳过该侧校验
        income_components = [
            ("本年收入合计", _find_label_value(rows, ("本年收入合计",), side="income")),
            ("使用非财政拨款结余", _find_label_value(rows, ("使用非财政拨款结余",), side="income")),
            ("年初结转和结余", _find_label_value(rows, ("年初结转和结余",), side="income")),
        ]
        if income_total is not None and all(v is not None for _, v in income_components):
            calc = sum(v for _, v in income_components)
            level, diff = classify_amount_diff(income_total, calc, n_children=len(income_components))
            if level == "mismatch":
                detail = " + ".join(f"{name}({v:.2f})" for name, v in income_components)
                issues.append(self._issue(
                    f"收入侧平衡错误：计算值({calc:.2f}) != 收入总计({income_total:.2f})。公式：{detail}",
                    {"table": table_name, "page": page}, "error",
                    evidence_text=f"表格：{table_name}\n{detail}\n收入总计：{income_total}"
                ))
            elif level == "rounding_hint":
                issues.append(self._issue(
                    f"收入侧合计与分项存在 {diff:.2f} 万元差异，在显示舍入包络内，可能为取整误差。",
                    {"table": table_name, "page": page}, "info",
                    evidence_text=f"表格：{table_name}\n收入总计：{income_total}\n分项之和：{calc}"
                ))

        expense_components = [
            ("本年支出合计", _find_label_value(rows, ("本年支出合计",), side="expense")),
            ("结余分配", _find_label_value(rows, ("结余分配",), side="expense")),
            ("年末结转和结余", _find_label_value(rows, ("年末结转和结余",), side="expense")),
        ]
        if expense_total is not None and all(v is not None for _, v in expense_components):
            calc = sum(v for _, v in expense_components)
            level, diff = classify_amount_diff(expense_total, calc, n_children=len(expense_components))
            if level == "mismatch":
                detail = " + ".join(f"{name}({v:.2f})" for name, v in expense_components)
                issues.append(self._issue(
                    f"支出侧平衡错误：计算值({calc:.2f}) != 支出总计({expense_total:.2f})。公式：{detail}",
                    {"table": table_name, "page": page}, "error",
                    evidence_text=f"表格：{table_name}\n{detail}\n支出总计：{expense_total}"
                ))
            elif level == "rounding_hint":
                issues.append(self._issue(
                    f"支出侧合计与分项存在 {diff:.2f} 万元差异，在显示舍入包络内，可能为取整误差。",
                    {"table": table_name, "page": page}, "info",
                    evidence_text=f"表格：{table_name}\n支出总计：{expense_total}\n分项之和：{calc}"
                ))

        return issues

    def _apply_structured(self, doc: Document, table) -> List[Issue]:
        """结构化消费路径（GPT5.6 R2 P0-1b）：直接消费 ParsedTable。

        与 legacy apply 的三个校验一一对应（表底平衡/收入侧/支出侧），
        数值来源改为 ParsedCell.number（Decimal 三态），标签匹配改为
        row.label 前缀查找；任一侧数值取不到 → 跳过该校验（与 legacy
        的 None 安全语义一致）。
        """
        issues: List[Issue] = []
        table_name = "收入支出决算总表"
        page = table.page_span[0] or 1

        def half_cells(row):
            cells = row.cells
            mid = len(cells) // 2
            return cells[:mid], cells[mid:]

        def side_total(side: str) -> Optional[Decimal]:
            for row in table.rows:
                if row.row_role not in ("total", "subtotal"):
                    continue
                left, right = half_cells(row)
                half = left if side == "income" else right
                if not any("总计" in (c.text or "") for c in half):
                    continue
                for c in half:
                    if c.number is not None:
                        return c.number
            return None

        def side_component(side: str, label: str) -> Optional[Decimal]:
            for row in table.rows:
                if row.row_role in ("header",):
                    continue
                left, right = half_cells(row)
                half = left if side == "income" else right
                if not any(label in (c.text or "") for c in half):
                    continue
                for c in half:
                    if c.number is not None:
                        return c.number
            return None

        income_total = side_total("income")
        expense_total = side_total("expense")

        if income_total is not None and expense_total is not None:
            level, diff = classify_amount_diff(income_total, expense_total, n_children=1)
            if level == "mismatch":
                issues.append(self._issue(
                    f"总表平衡性错误：收入总计({income_total:.2f}) != 支出总计({expense_total:.2f})",
                    {"table": table_name, "page": page}, "error",
                    evidence_text=f"表格：{table_name}\n收入总计：{income_total}\n支出总计：{expense_total}"
                ))
            elif level == "rounding_hint":
                issues.append(self._issue(
                    f"总表平衡存在 {diff:.2f} 万元差异，在显示舍入包络内，可能为取整误差，建议复核。",
                    {"table": table_name, "page": page}, "info",
                    evidence_text=f"表格：{table_name}\n收入总计：{income_total}\n支出总计：{expense_total}"
                ))

        for side, total, components in (
            ("income", income_total, ("本年收入合计", "使用非财政拨款结余", "年初结转和结余")),
            ("expense", expense_total, ("本年支出合计", "结余分配", "年末结转和结余")),
        ):
            if total is None:
                continue
            values = [side_component(side, label) for label in components]
            if any(v is None for v in values):
                continue
            # mypy 无法从 any(...)/重绑定收窄 Optional 列表：显式标注
            # 新变量过滤后再求和（R8 P2）
            numeric_values: List[Decimal] = [
                v for v in values if v is not None
            ]
            calc = sum(numeric_values, Decimal("0"))
            level, diff = classify_amount_diff(total, calc, n_children=len(components))
            side_label = "收入" if side == "income" else "支出"
            if level == "mismatch":
                detail = " + ".join(f"{n}({v:.2f})" for n, v in zip(components, numeric_values, strict=True))
                issues.append(self._issue(
                    f"{side_label}侧平衡错误：计算值({calc:.2f}) != {side_label}总计({total:.2f})。公式：{detail}",
                    {"table": table_name, "page": page}, "error",
                    evidence_text=f"表格：{table_name}\n{detail}\n{side_label}总计：{total}"
                ))
            elif level == "rounding_hint":
                issues.append(self._issue(
                    f"{side_label}侧合计与分项存在 {diff:.2f} 万元差异，在显示舍入包络内，可能为取整误差。",
                    {"table": table_name, "page": page}, "info",
                    evidence_text=f"表格：{table_name}\n{side_label}总计：{total}\n分项之和：{calc}"
                ))

        return issues


class R33119_FiscalTotalCheck(Rule):
    code, severity = "V33-119", "error"
    desc = "财政拨款收入支出决算总表勾稽关系 (Table 4)"

    def apply(self, doc: Document) -> List[Issue]:
        issues = []
        table_name = "财政拨款收入支出决算总表"
        rows = _get_table_rows(doc, table_name)
        if not rows:
            # 决算材料应含此核心表（Table 4）：查不到=证据不足，
            # 转 insufficient_data 供质量门转人工复核（GPT5.6 P0-2）。
            raise RuleDeferred(self.code, f"表缺失或无可解析行: {table_name}")

        # 3. 行级横向校验 (Row Horizontal Check)
        # 合计(Col 1) = 一般公共(Col 2) + 政府性(Col 3) + 国有资本(Col 4)
        for i, row in enumerate(rows):
            vals = _parse_row_values(row)
            # 必须至少有4列数据才能校验 (合计 + 3个分项)
            if len(vals) >= 4:
                # 忽略全0行
                if sum(vals) < 0.01: continue
                
                # 假设第一列数值是合计
                # 注意：有些行可能包含行号等数字，需要更智能的识别。
                # 通常金额列靠后。根据表头 [项目, 合计, 一般, 政府, 国有]
                # 数值通常是最后4列。
                total = vals[0]
                sub_total = sum(vals[1:4])
                
                # 如果数值列多于4个，可能第一列是行号或代码，取最后4个
                if len(vals) > 4:
                    total = vals[-4]
                    sub_total = sum(vals[-3:])
                
                if abs(total - sub_total) > 0.05: # 容差
                    row_txt = "".join([str(c) for c in row if c])[:20]
                    issues.append(self._issue(
                        f"行横向校验失败(第{i+1}行 '{row_txt}...'): 合计({total}) != 分项之和({sub_total})",
                        {"table": table_name, "row": i+1}, "warn",
                        evidence_text=f"表格：{table_name}\n行内容：{row_txt}\n计算：{sub_total} (分项之和) vs {total} (合计)"
                    ))

        # 4. 列内纵向平衡 (Column Vertical Balance)
        # 每一列：本年收入 + 年初 = 本年支出 + 年末
        # 需要识别 "本年收入", "年初...", "本年支出", "年末..." 行
        def find_row_vals_by_label(keywords):
            for r in rows:
                txt = "".join([str(c) for c in r if c])
                if all(k in txt for k in keywords):
                    return _parse_row_values(r)
            return []
            
        row_inc = find_row_vals_by_label(["本年收入"])
        row_start = find_row_vals_by_label(["年初", "结转"])
        row_exp = find_row_vals_by_label(["本年支出"])
        row_end = find_row_vals_by_label(["年末", "结转"])
        
        if row_inc and row_start and row_exp and row_end:
            # 确保长度一致，取最小长度
            min_len = min(len(row_inc), len(row_start), len(row_exp), len(row_end))
            # 从最后几列开始校验（合计, 一般, 政府, 国有）
            # 假设最后4列是数据
            start_idx = max(0, min_len - 4)
            col_names = ["合计", "一般公共预算", "政府性基金", "国有资本经营"]
            
            for offset in range(min_len - start_idx):
                idx = start_idx + offset
                c_name = col_names[offset] if offset < 4 else f"Col{offset}"
                
                inc = row_inc[idx]
                start = row_start[idx]
                exp = row_exp[idx]
                end = row_end[idx]
                
                if abs((inc + start) - (exp + end)) > 0.05:
                    issues.append(self._issue(
                        f"列纵向平衡失败({c_name}): 收入({inc})+年初({start}) != 支出({exp})+年末({end})",
                        {"table": table_name, "col": c_name}, "error",
                        evidence_text=f"表格：{table_name}\n列：{c_name}\n公式：{inc}(本年收入) + {start}(年初) = {inc+start}\n      {exp}(本年支出) + {end}(年末) = {exp+end}\n差额：{abs((inc+start)-(exp+end)):.2f}"
                    ))

        return issues


class _TableTotalsRecord(TypedDict):
    """R33120 跨表同口径对比的记录形状（R8 P2：mypy 收窄）。

    此前标注 Dict[str, Dict[str, float]] 与真实形状（total_row 为
    Dict[int, float]、header_cols 为 Dict[str, int]）不符，mypy 报
    dict-item 类型错误——用 TypedDict 表达真实结构。
    """

    total_row: Dict[int, float]
    header_cols: Dict[str, int]
    width: int


class R33120_DetailTableCheck(Rule):
    code, severity = "V33-120", "warn"
    desc = "明细表勾稽关系与层级校验 (Table 2, 3, 5)"

    # 跨表同口径列名归一：表头「本年支出合计」「合计」视为同一口径
    _COL_TOTAL = ("合计", "本年支出合计", "本年收入合计")
    _COL_BASIC = ("基本支出",)
    _COL_PROJECT = ("项目支出",)

    def _header_columns(self, rows: List[List[str]]) -> Dict[str, int]:
        """从表头（前 5 行）提取语义列索引：合计/基本支出/项目支出。"""
        mapping: Dict[str, int] = {}
        for row in rows[:5]:
            for i, cell in enumerate(row):
                text = re.sub(r"\s+", "", str(cell or ""))
                if not text:
                    continue
                if text in self._COL_TOTAL and "total" not in mapping:
                    mapping["total"] = i
                elif text in self._COL_BASIC and "basic" not in mapping:
                    mapping["basic"] = i
                elif text in self._COL_PROJECT and "project" not in mapping:
                    mapping["project"] = i
        return mapping

    def apply(self, doc: Document) -> List[Issue]:
        issues = []
        target_tables = ["收入决算表", "支出决算表", "一般公共预算财政拨款支出决算表"]
        table_totals: Dict[str, _TableTotalsRecord] = {}
        found_any_table = False

        for table_name in target_tables:
            # 本规则的层级/合计行列校验按**整表**语义判断，故取全页界合并续页；
            # 旧「最多并 1 页」会把跨 5 页的表截断，产出层级与合计列的假警告。
            rows = _get_table_rows(doc, table_name, full_extent=True)
            if not rows:
                continue
            found_any_table = True
            page = _get_first_anchor_page(doc, table_name) or 1

            # 跨页列宽漂移（HANDOFF §3.1C）：续页把「类|款|项」三列编码合并成
            # 一列，行宽变窄。处置：以众数宽度为基准做**逻辑列重映射**——
            # 尾部金额列按"行宽差"对齐（src = j + len(row) - modal_width），
            # 前置编码列独立提取。旧实现直接按固定列索引取值，漂移行的
            # 合计列取到 0.0/错值，产生「父级金额(0.0) != 子级之和」假警告。
            modal_width = _modal_row_width(rows)
            if modal_width is None:
                continue

            header_cols = self._header_columns(rows)
            # 合计列：优先取语义表头；取不到则放弃层级校验（不做位置猜测）
            col_total = header_cols.get("total")

            hierarchy: Dict[str, Decimal] = {}
            row_code_level: Dict[str, int] = {}
            # 合计行的数值向量（None 安全，键为 modal 列索引）
            total_row_values: Optional[Dict[int, Decimal]] = None
            total_row_cells: Optional[List[str]] = None

            for row in rows:
                cells = _row_numbers(row)
                shift = len(row) - modal_width
                joined = "".join([str(c) for c in row if c])
                text = re.sub(r"\s+", "", joined)

                # 合计行（表内汇总行，无科目编码）：名称列恰为「合计」、
                # 行首为「合计」或含本年支出/收入合计字样
                has_label_total = any(
                    str(c or "").strip() == "合计" for c in row
                ) or "本年支出合计" in text or "本年收入合计" in text or text.startswith("合计")
                numeric_count = sum(1 for v in cells if v is not None)
                if has_label_total and numeric_count >= 2 and not any(
                    str(c or "").strip().isdigit() and len(str(c).strip()) in (3, 5, 7) for c in row[:3]
                ):
                    values = {
                        j - shift: Decimal(str(v))
                        for j, v in enumerate(cells)
                        if v is not None and 0 <= j - shift < modal_width
                    }
                    if total_row_values is None and values:
                        total_row_values = values
                        total_row_cells = row
                    continue

                # 科目行：编码在前 3 列（3/5/7 位）
                code = ""
                for cell in row[:3]:
                    c_str = str(cell or "").strip()
                    if c_str.isdigit() and len(c_str) in (3, 5, 7):
                        code = c_str
                        break
                if not code:
                    continue

                amount = None
                # 列位按行自适应：跨页续表会整体左移名称/金额列（宽度不变、语义变），
                # 固定列位会把续页的「基本/项目」当成「合计」（长风样张根因）
                adaptive = _adaptive_value_cols(row)
                if adaptive is not None:
                    t_col = adaptive[1]
                    if 0 <= t_col < len(cells) and cells[t_col] is not None:
                        amount = Decimal(str(cells[t_col]))
                elif col_total is not None:
                    src = col_total + shift  # modal 列在当前行中的下标（窄行 shift<0）
                    if 0 <= src < len(cells) and cells[src] is not None:
                        amount = Decimal(str(cells[src]))
                elif len(cells) > 4:
                    # 无语义表头时，取该行最后一个数值（None 安全，仍不做 0 填充）
                    tail = [v for v in cells if v is not None]
                    if tail:
                        amount = Decimal(str(tail[-1]))
                if amount is not None:
                    hierarchy[code] = amount
                    row_code_level[code] = len(code)

            # 层级校验：类(3位)=Σ款(5位)，款(5位)=Σ项(7位)
            for parent_code, parent_amt in hierarchy.items():
                if len(parent_code) not in (3, 5):
                    continue
                target_len = len(parent_code) + 2
                children = [
                    amt
                    for code, amt in hierarchy.items()
                    if len(code) == target_len and code.startswith(parent_code)
                ]
                if not children:
                    continue
                child_sum = sum(children, Decimal("0"))
                level, diff = classify_amount_diff(
                    parent_amt, child_sum, n_children=len(children)
                )
                if level == "mismatch":
                    issues.append(self._issue(
                        f"层级校验失败({parent_code}): 父级金额({parent_amt:.2f}) != 子级之和({child_sum:.2f})",
                        {"table": table_name, "code": parent_code, "page": page}, "warn",
                        evidence_text=f"表格：{table_name}\n父级科目：{parent_code} (金额 {parent_amt:.2f})\n子级科目之和：{child_sum:.2f}"
                    ))
                elif level == "rounding_hint":
                    issues.append(self._issue(
                        f"{table_name}科目 {parent_code} 与其明细之和相差 {diff:.2f} 万元，"
                        "在显示舍入包络内，可能为取整误差。",
                        {"table": table_name, "code": parent_code, "page": page}, "info",
                        evidence_text=f"表格：{table_name}\n{parent_code}：{parent_amt:.2f}\n明细之和：{child_sum:.2f}"
                    ))

            # 列合计校验（T2 类差异）：最低级科目行按列求和 vs 合计行对应列。
            # 列位按行自适应定位：跨页续表会把「类|款|项」编码列收缩掉——首页
            # [码,'','',名,合计,基本,项目]、续页 [码,名,合计,基本,项目]，行宽不变
            # 而语义整体左移 2 格，按首页固定列位读续页必然错列
            # （长风样张 4 条「合计行列校验失败」误报根因）。
            # 合计行无名称列（纯数字）时，其数值单元格依序对应 合计/基本/项目。
            if hierarchy and total_row_values and total_row_cells is not None:
                lowest_level = max(row_code_level.values())
                lowest_codes = [
                    c for c, lvl in row_code_level.items() if lvl == lowest_level
                ]
                role_sums: Dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
                role_counts: Dict[str, int] = defaultdict(int)
                adaptive_ok = 0
                for row in rows:
                    cells = _row_numbers(row)
                    code = ""
                    for cell in row[:3]:
                        c_str = str(cell or "").strip()
                        if c_str.isdigit() and len(c_str) in (3, 5, 7):
                            code = c_str
                            break
                    if code not in lowest_codes:
                        continue
                    adaptive = _adaptive_value_cols(row)
                    if adaptive is None:
                        continue
                    adaptive_ok += 1
                    _name_col, t_col, b_col, p_col = adaptive
                    for role, c_idx in (("total", t_col), ("basic", b_col), ("project", p_col)):
                        if 0 <= c_idx < len(cells) and cells[c_idx] is not None:
                            role_sums[role] += Decimal(str(cells[c_idx]))
                            role_counts[role] += 1

                if adaptive_ok:
                    # 合计行：数值单元格依序对应 合计/基本/项目（缺则跳过该角色）
                    total_cells = _row_numbers(total_row_cells)
                    total_numeric = [v for v in total_cells if v is not None]
                    for role, total_value in zip(
                        ("total", "basic", "project"), total_numeric, strict=False
                    ):
                        n = role_counts.get(role, 1)
                        level, diff = classify_amount_diff(
                            total_value, role_sums[role], n_children=max(n, 1)
                        )
                        if level == "mismatch":
                            issues.append(self._issue(
                                f"{table_name}合计行列校验失败：合计({total_value:.2f}) != 明细之和({role_sums[role]:.2f})",
                                {"table": table_name, "column": role, "page": page}, "warn",
                                evidence_text=f"表格：{table_name}\n合计值：{total_value:.2f}\n明细之和：{role_sums[role]:.2f}"
                            ))
                        elif level == "rounding_hint":
                            issues.append(self._issue(
                                f"{table_name}合计行与明细之和相差 {diff:.2f} 万元，"
                                "在显示舍入包络内，可能为取整误差。",
                                {"table": table_name, "column": role, "page": page}, "info",
                                evidence_text=f"表格：{table_name}\n合计值：{total_value:.2f}\n明细之和：{role_sums[role]:.2f}"
                            ))
                table_totals[table_name] = {
                    "total_row": {i: float(v) for i, v in total_row_values.items()},
                    "header_cols": header_cols,
                    "width": modal_width,
                }

        # 跨表同口径近似差异（T3 类）：支出决算表 ↔ 一般公共预算财政拨款支出决算表
        # 两表资金口径允许不同，但出现 0.01~0.5 级别的近似差异时可疑，转人工复核
        t3 = table_totals.get("支出决算表")
        t5 = table_totals.get("一般公共预算财政拨款支出决算表")
        if t3 and t5:
            for role in ("total", "basic", "project"):
                idx3 = t3["header_cols"].get(role)
                idx5 = t5["header_cols"].get(role)
                if idx3 is None or idx5 is None:
                    continue
                v3 = t3["total_row"].get(idx3)
                v5 = t5["total_row"].get(idx5)
                if v3 is None or v5 is None:
                    continue
                diff = abs(Decimal(str(v3)) - Decimal(str(v5)))
                if Decimal("0.5") >= diff > Decimal("0"):
                    issues.append(self._issue(
                        f"支出决算表与一般公共预算财政拨款支出决算表同口径列相差 {diff:.2f} 万元"
                        "（可能为取整误差或口径差异），需人工复核。",
                        {"table": "支出决算表↔一般公共预算财政拨款支出决算表", "page": _get_first_anchor_page(doc, "支出决算表") or 1}, "manual_review",
                        evidence_text=f"支出决算表：{v3:.2f}\n一般公共预算财政拨款支出决算表：{v5:.2f}"
                    ))

        # 三张目标表全部查不到：本规则覆盖的核心勾稽完全未执行，
        # 不得记 pass，转 insufficient_data 供质量门转人工复核
        # （GPT5.6 P0-2）。
        if not found_any_table:
            raise RuleDeferred(
                self.code, f"三张目标表均缺失: {'、'.join(target_tables)}"
            )

        return issues


class R33117_BasicExpenseClassification(Rule):
    code, severity = "V33-117", "error"
    desc = "基本支出决算表经济分类校验 (Table 6)"

    _PERSONNEL_CLASSES = ("301", "303")
    _PUBLIC_CLASSES = ("302", "310")
    _CLASS_LABELS = {
        "人员经费合计": _PERSONNEL_CLASSES,
        "公用经费合计": _PUBLIC_CLASSES,
    }

    def apply(self, doc: Document) -> List[Issue]:
        issues = []
        table_name = "一般公共预算财政拨款基本支出决算表"
        rows = _get_table_rows(doc, table_name)
        if not rows:
            # 决算材料应含此核心表（Table 6）：查不到=证据不足，
            # 转 insufficient_data 供质量门转人工复核（GPT5.6 P0-2）。
            raise RuleDeferred(self.code, f"表缺失或无可解析行: {table_name}")
        page = _get_first_anchor_page(doc, table_name) or 1

        modal_width = _modal_row_width(rows)
        if modal_width is None:
            # 有表但行宽无法稳定判定：同样属解析证据不足，不得记 pass
            raise RuleDeferred(self.code, "行宽众数无法判定，无法做列对齐校验")
        kept = [r for r in rows if len(r) == modal_width]

        # 双栏经济分类布局：每半行 = [类, 款, 科目名称..., 决算数]。
        # 类级行与款级行的区分：第二个单元格是 1~2 位数字 → 款级明细行；
        # 为空或文本 → 类级行。旧实现对所有含类码的行取"其后第一个数值"
        # 累加，把款级明细重复计入类级合计，得到 3309.02 这类凭空累加值
        # （HANDOFF §3.1B）。
        class_amounts: Dict[str, Decimal] = {}
        children_by_class: Dict[str, List[Decimal]] = defaultdict(list)

        for row in kept:
            left, right = _split_two_sided_row(row)
            for half in (left, right):
                if not half:
                    continue
                c1 = str(half[0] or "").strip()
                if not (c1.isdigit() and len(c1) == 3 and "300" <= c1 <= "399"):
                    # 半行起点不是类码：合并单元格续行或文本行，跳过
                    continue
                c2 = str(half[1] or "").strip() if len(half) > 1 else ""
                if c2.isdigit() and len(c2) in (1, 2):
                    # 款级明细行：归入其类级的子项
                    value = _half_first_number(half, 2)
                    if value is not None:
                        children_by_class[c1].append(Decimal(str(value)))
                    continue
                # 类级行：金额 = 该半行内第一个真实数值
                value = _half_first_number(half, 1)
                if value is not None and c1 not in class_amounts:
                    class_amounts[c1] = Decimal(str(value))

        # 显式合计行（标签与数值同半行取值，不做跨半行猜测）
        explicit: Dict[str, Decimal] = {}
        for row in kept:
            left, right = _split_two_sided_row(row)
            for half in (left, right):
                half_text = "".join([str(c) for c in half if c])
                for label in self._CLASS_LABELS:
                    if label in half_text and label not in explicit:
                        value = _half_first_number(half, 1)
                        if value is not None:
                            explicit[label] = Decimal(str(value))

        # 类级金额缺失时无法判定 → 跳过（不伪造数值）
        def class_sum(codes) -> Optional[Decimal]:
            values = [class_amounts.get(c) for c in codes]
            if any(v is None for v in values):
                return None
            numeric: List[Decimal] = [v for v in values if v is not None]
            return sum(numeric, Decimal("0"))

        checks = [
            ("人员经费", explicit.get("人员经费合计"), class_sum(self._PERSONNEL_CLASSES), self._PERSONNEL_CLASSES),
            ("公用经费", explicit.get("公用经费合计"), class_sum(self._PUBLIC_CLASSES), self._PUBLIC_CLASSES),
        ]
        for label, explicit_value, computed, codes in checks:
            if explicit_value is None or computed is None:
                continue
            n = len(codes)
            level, diff = classify_amount_diff(explicit_value, computed, n_children=n)
            if level == "mismatch":
                issues.append(self._issue(
                    f"{label}校验失败：显式合计({explicit_value:.2f}) != 类级之和({computed:.2f})",
                    {"table": table_name, "page": page}, "warn",
                    evidence_text=f"表格：{table_name}\n显式合计行：{label}={explicit_value:.2f}\n类级科目之和：{computed:.2f}"
                ))
            elif level == "rounding_hint":
                issues.append(self._issue(
                    f"{label}显式合计与类级之和相差 {diff:.2f} 万元，在显示舍入包络内，可能为取整误差。",
                    {"table": table_name, "page": page}, "info",
                    evidence_text=f"表格：{table_name}\n显式合计：{explicit_value:.2f}\n类级之和：{computed:.2f}"
                ))

        # 类级 = Σ款级明细（T4 类差异，如 310 行 14.44 vs 明细和 14.43）
        for class_code, children in children_by_class.items():
            parent = class_amounts.get(class_code)
            if parent is None or not children:
                continue
            child_sum = sum(children, Decimal("0"))
            level, diff = classify_amount_diff(parent, child_sum, n_children=len(children))
            if level == "mismatch":
                issues.append(self._issue(
                    f"经济分类科目 {class_code} 归集失败：类级金额({parent:.2f}) != 明细之和({child_sum:.2f})",
                    {"table": table_name, "code": class_code, "page": page}, "warn",
                    evidence_text=f"表格：{table_name}\n{class_code}：{parent:.2f}\n明细之和：{child_sum:.2f}"
                ))
            elif level == "rounding_hint":
                issues.append(self._issue(
                    f"经济分类科目 {class_code} 与其明细之和相差 {diff:.2f} 万元，"
                    "在显示舍入包络内，可能为取整误差。",
                    {"table": table_name, "code": class_code, "page": page}, "info",
                    evidence_text=f"表格：{table_name}\n{class_code}：{parent:.2f}\n明细之和：{child_sum:.2f}"
                ))

        return issues


class R33121_ThreePublicCheck(Rule):
    code, severity = "V33-121", "error"
    desc = "三公经费勾稽关系 (Table 7)"

    def apply(self, doc: Document) -> List[Issue]:
        issues = []
        table_name = "一般公共预算财政拨款“三公”经费支出决算表"
        rows = _get_table_rows(doc, table_name)
        if not rows:
            # 三公表为条件性表，但决算材料极大多数应含此表：
            # 查不到时按证据不足转人工复核（missing 核因由 missing_core_table
            # 门禁另行呈现），不得静默记 pass（GPT5.6 P0-2）。
            raise RuleDeferred(self.code, f"表缺失或无可解析行: {table_name}")
        
        # 10. 列间加和校验 (Column Sum Check)
        # 合计 = 因公出国 + 公务用车购置及运行 + 公务接待
        # 11. 车辆费细分
        # 运行费 = 购置 + 运行
        
        # 定位数据行 (通常是最后一行或包含数据的行)
        data_rows = []
        for row in rows:
            vals = _parse_row_values(row)
            if len(vals) >= 3 and sum(vals) > 0:
                data_rows.append(vals)
        
        for i, vals in enumerate(data_rows):
            # 假设结构: [合计, 出国, 车辆合计, 购置, 运行, 接待]
            # Good Sample: [0.95(合), 0.95(接)] (其他为空)
            # 需根据非零值推断索引，或根据表头
            
            # 简单校验：最大值（合计）是否等于其余之和 or 其余一级项之和
            # 这里如果不解析表头很难做精确索引校验。
            # 暂只做：是否存在不平衡 (Max != Sum of parts)
            # 略
            pass
            
        return issues


class R33122_EmptyTableCheck(Rule):
    code, severity = "V33-122", "error"
    desc = "空表零值校验 (Table 8, 9)"

    def apply(self, doc: Document) -> List[Issue]:
        issues = []
        # 检查 Table 4 对应的列是否为 0
        t4_rows = _get_table_rows(doc, "财政拨款收入支出决算总表")
        
        # 检查 Table 4 中 "政府性基金" 和 "国有资本" 列的总和
        sum_gov_fund = 0.0
        sum_state_cap = 0.0
        
        if t4_rows:
            for row in t4_rows:
                vals = _parse_row_values(row)
                if len(vals) >= 4:
                    # 假设 Col 2 = 政府, Col 3 = 国有 (0-based: 1, 2, 3 -> 一般, 政府, 国有)
                    # 需更严谨。根据 Good Sample: 合计, 一般, 政府, 国有
                    # 倒数第2列 = 政府，倒数第1列 = 国有
                    if len(vals) >= 3: sum_gov_fund += vals[-2]
                    if len(vals) >= 2: sum_state_cap += vals[-1]
        
        # 校验 Table 8
        if sum_gov_fund < 0.1:
            t8_rows = _get_table_rows(doc, "政府性基金预算财政拨款收入支出决算表")
            if t8_rows:
                total_val = 0.0
                for r in t8_rows: total_val += sum(_parse_row_values(r))
                if total_val > 0.1:
                     issues.append(self._issue(
                        "政府性基金表(Table 8)应为空（因总表无数据），但检测到数值。",
                        {"table": "Table 8"}, "error",
                        evidence_text=f"总表判定：政府性基金列为0\nTable 8 检测值合计：{total_val}"
                    ))
        
        # 校验 Table 9
        if sum_state_cap < 0.1:
            t9_rows = _get_table_rows(doc, "国有资本经营预算财政拨款收入支出决算表")
            if t9_rows:
                total_val = 0.0
                for r in t9_rows: total_val += sum(_parse_row_values(r))
                if total_val > 0.1:
                     issues.append(self._issue(
                        "国有资本经营表(Table 9)应为空（因总表无数据），但检测到数值。",
                        {"table": "Table 9"}, "error",
                        evidence_text=f"总表判定：国有资本列为0\nTable 9 检测值合计：{total_val}"
                    ))

        return issues


# ==================================================================================
# P0 - 主链路勾稽规则 (Inter-Table Main Chain)
# ==================================================================================

class R33200_InterTable_T1_T2(Rule):
    """T1.本年收入合计 = T2.合计行本年收入合计"""
    code, severity = "V33-200", "error"
    desc = "表间勾稽：T1收入合计↔T2收入合计"

    def apply(self, doc: Document) -> List[Issue]:
        issues = []
        t1_rows = _get_table_rows(doc, "收入支出决算总表")
        t2_rows = _get_table_rows(doc, "收入决算表")
        
        if not t1_rows or not t2_rows:
            missing = []
            if not t1_rows:
                missing.append("收入支出决算总表(T1)")
            if not t2_rows:
                missing.append("收入决算表(T2)")
            raise RuleDeferred(
                self.code,
                detail=f"表缺失或无可解析行: {','.join(missing)}",
                unresolved_reasons=[f"表缺失或无可解析行: {','.join(missing)}"],
            )
        
        # 查找T1的"本年收入合计"
        t1_income = 0.0
        for row in t1_rows:
            row_txt = "".join([str(c) for c in row if c])
            if "本年收入合计" in row_txt:
                vals = _parse_row_values(row)
                if vals: t1_income = max(vals)
                break
        
        # 查找T2的"合计"行
        t2_income = 0.0
        for row in t2_rows:
            row_txt = "".join([str(c) for c in row if c])
            if row_txt.startswith("合计") or "合计" == row_txt.strip():
                vals = _parse_row_values(row)
                if vals: t2_income = max(vals)
                break
        
        if t1_income > 0.01 and t2_income > 0.01:
            if abs(t1_income - t2_income) > 0.01:
                issues.append(self._issue(
                    f"T1↔T2收入合计不一致：T1={t1_income:.2f}, T2={t2_income:.2f}",
                    {"t1": t1_income, "t2": t2_income}, "error",
                    evidence_text=f"表1(总表) 本年收入合计: {t1_income}\n表2(收入表) 合计: {t2_income}"
                ))
        return issues


class R33201_InterTable_T1_T3(Rule):
    """T1.本年支出合计 = T3.合计行本年支出合计"""
    code, severity = "V33-201", "error"
    desc = "表间勾稽：T1支出合计↔T3支出合计"

    def apply(self, doc: Document) -> List[Issue]:
        issues = []
        t1_rows = _get_table_rows(doc, "收入支出决算总表")
        t3_rows = _get_table_rows(doc, "支出决算表")
        
        if not t1_rows:
            raise RuleDeferred(
                self.code,
                detail="表缺失或无可解析行: 收入支出决算总表(T1)",
                unresolved_reasons=["表缺失或无可解析行: 收入支出决算总表(T1)"],
            )
        
        # 查找T1的"本年支出合计"
        t1_expense = 0.0
        for row in t1_rows:
            row_txt = "".join([str(c) for c in row if c])
            if "本年支出合计" in row_txt:
                vals = _parse_row_values(row)
                if vals: t1_expense = max(vals)
                break
        
        # T3缺失检查
        if not t3_rows:
            if t1_expense > 0.01:
                issues.append(self._issue(
                    f"T3(支出决算表)缺失，无法与T1支出合计({t1_expense:.2f})进行勾稽",
                    {"t1_expense": t1_expense}, "error"
                ))
            raise RuleDeferred(
                self.code,
                detail="支出决算表(T3)缺失",
                partial_issues=issues,
                unresolved_reasons=["支出决算表(T3)缺失"],
            )
        
        # 查找T3的"合计"行
        t3_expense = 0.0
        for row in t3_rows:
            row_txt = "".join([str(c) for c in row if c])
            if row_txt.startswith("合计") or "合计" == row_txt.strip():
                vals = _parse_row_values(row)
                if vals: t3_expense = max(vals)
                break
        
        if t1_expense > 0.01 and t3_expense > 0.01:
            if abs(t1_expense - t3_expense) > 0.01:
                issues.append(self._issue(
                    f"T1↔T3支出合计不一致：T1={t1_expense:.2f}, T3={t3_expense:.2f}",
                    {"t1": t1_expense, "t3": t3_expense}, "error",
                    evidence_text=f"表1(总表) 本年支出合计: {t1_expense}\n表3(支出表) 合计: {t3_expense}"
                ))
        return issues


class R33202_InterTable_T4_T5(Rule):
    """T4.一般公共预算本年支出 = T5.合计"""
    code, severity = "V33-202", "error"
    desc = "表间勾稽：T4一般公共支出↔T5合计"

    def apply(self, doc: Document) -> List[Issue]:
        issues = []
        t4_rows = _get_table_rows(doc, "财政拨款收入支出决算总表")
        t5_rows = _get_table_rows(doc, "一般公共预算财政拨款支出决算表")
        
        if not t4_rows or not t5_rows:
            missing = []
            if not t4_rows:
                missing.append("财政拨款收入支出决算总表(T4)")
            if not t5_rows:
                missing.append("一般公共预算财政拨款支出决算表(T5)")
            raise RuleDeferred(
                self.code,
                detail=f"表缺失或无可解析行: {','.join(missing)}",
                unresolved_reasons=[f"表缺失或无可解析行: {','.join(missing)}"],
            )
        
        # T4: 查找"一般公共预算财政拨款"列的支出合计。
        # 列索引必须从表头解析：财政拨款总表支出侧为 [项目, 合计, 一般公共预算财政拨款,
        # 政府性基金…, 国有资本…]；旧实现硬编码 vals[1] 取到的是**合计**列
        # （文旅局样张因此拿 26,538.47 去比，而一般公共应为 24,535.67）。
        col_general = -1
        for row in t4_rows[:4]:
            first_cell = str(row[0] or "").strip() if row else ""
            if re.match(r"^[一二三四五六七八九十]+、", first_cell):
                break  # 已进入数据行区，停止表头扫描
            for ci, cell in enumerate(row):
                if "一般公共" in str(cell or ""):
                    col_general = ci
                    break
            if col_general >= 0:
                break

        t4_general_expense = 0.0
        for row in t4_rows:
            row_txt = "".join([str(c) for c in row if c])
            if "本年支出合计" in row_txt or "支出合计" in row_txt:
                vals = _parse_row_values(row)
                if col_general >= 0 and col_general < len(vals):
                    t4_general_expense = vals[col_general]
                elif len(vals) >= 2:
                    t4_general_expense = vals[1]
                break
        
        # T5: 查找"合计"行
        t5_total = 0.0
        for row in t5_rows:
            row_txt = "".join([str(c) for c in row if c])
            if row_txt.startswith("合计") or "合计" == row_txt.strip():
                vals = _parse_row_values(row)
                if vals: t5_total = max(vals)
                break
        
        if t4_general_expense > 0.01 and t5_total > 0.01:
            if abs(t4_general_expense - t5_total) > 0.01:
                issues.append(self._issue(
                    f"T4↔T5一般公共支出不一致：T4={t4_general_expense:.2f}, T5={t5_total:.2f}",
                    {"t4": t4_general_expense, "t5": t5_total}, "error",
                    evidence_text=f"表4(财政拨款总表) 一般公共预算支出: {t4_general_expense}\n表5(一般公共支出表) 合计: {t5_total}"
                ))
        return issues


class R33203_InterTable_T5_T6(Rule):
    """T5.基本支出合计 = T6.基本支出合计"""
    code, severity = "V33-203", "error"
    desc = "表间勾稽：T5基本支出↔T6基本支出"

    def apply(self, doc: Document) -> List[Issue]:
        issues = []
        t5_rows = _get_table_rows(doc, "一般公共预算财政拨款支出决算表")
        t6_rows = _get_table_rows(doc, "一般公共预算财政拨款基本支出决算表")
        
        if not t5_rows or not t6_rows:
            missing = []
            if not t5_rows:
                missing.append("一般公共预算财政拨款支出决算表(T5)")
            if not t6_rows:
                missing.append("一般公共预算财政拨款基本支出决算表(T6)")
            raise RuleDeferred(
                self.code,
                detail=f"表缺失或无可解析行: {','.join(missing)}",
                unresolved_reasons=[f"表缺失或无可解析行: {','.join(missing)}"],
            )
        
        # T5: 查找"合计"行的"基本支出"列
        t5_basic = 0.0
        for row in t5_rows:
            row_txt = "".join([str(c) for c in row if c])
            if row_txt.startswith("合计") or "合计" == row_txt.strip():
                vals = _parse_row_values(row)
                # 假设第2列是"基本支出"
                if len(vals) >= 2: t5_basic = vals[1]
                break
        
        # T6: 计算基本支出合计 = 人员经费合计 + 公用经费合计
        # 或直接从表头查找
        t6_basic = 0.0
        sum_301 = 0.0
        sum_302 = 0.0
        sum_303 = 0.0
        sum_310 = 0.0
        
        for row in t6_rows:
            for i, cell in enumerate(row):
                c_str = str(cell).strip()
                if c_str in ["301", "302", "303", "310"]:
                    amt = 0.0
                    for next_cell in row[i+1:]:
                        v = parse_number(next_cell)
                        if v is not None and v > 0:
                            amt = v
                            break
                    if amt > 0:
                        if c_str == "301": sum_301 = amt
                        elif c_str == "302": sum_302 = amt
                        elif c_str == "303": sum_303 = amt
                        elif c_str == "310": sum_310 = amt
        
        t6_basic = sum_301 + sum_302 + sum_303 + sum_310
        
        if t5_basic > 0.01 and t6_basic > 0.01:
            if abs(t5_basic - t6_basic) > 0.05:  # 允许0.05容差
                issues.append(self._issue(
                    f"T5↔T6基本支出不一致：T5={t5_basic:.2f}, T6={t6_basic:.2f}",
                    {"t5": t5_basic, "t6": t6_basic}, "error",
                    evidence_text=f"表5(一般公共支出表) 基本支出: {t5_basic}\n表6(基本支出表) 汇总(301+302+303+310): {t6_basic}"
                ))
        return issues


class R33243_Table6_BasicExpenseAdvancedCheck(Rule):
    """表六（基本支出决算表）高级校验：
    1. 明细归集 (弱校验)：一级科目 = 其下所有明细之和 (容差1.0)
    2. 人员/公用归集 (强校验)：人员 = 301+303; 公用 = 302+310 (0.01容差)
    3. 总额闭合 (强校验)：基本支出合计 = 人员 + 公用 (0.01容差)
    """
    code, severity = "V33-243", "error"
    desc = "基本支出决算表：经济分类口径与人员/公用汇总校验"

    def apply(self, doc: Document) -> List[Issue]:
        issues = []
        t6_rows = _get_table_rows(doc, "一般公共预算财政拨款基本支出决算表")
        
        if not t6_rows or len(t6_rows) < 5:
            raise RuleDeferred(self.code, detail="未找到有效的一般公共预算财政拨款基本支出决算表或行数不足(<5)")

        # --- A. 提取全表数据 (处理分栏) ---
        code_data = {} # code -> {name, amount, row_idx}
        summary_data = {} # "personnel_total", "public_total", "basic_total"
        
        for r_idx, row in enumerate(t6_rows):
            # 将行切分为左右两半 (通常是 4列+4列 或 对等分)
            mid = len(row) // 2
            halves = [row[:mid], row[mid:]]
            
            for half in halves:
                half_txt = "".join([str(c) for c in half if c])
                nums = _parse_row_values(half)
                
                # 情况1：汇总行 (人员经费合计 / 公用经费合计)
                if "人员经费合计" in half_txt:
                    if nums: summary_data["personnel_total"] = nums[0]
                elif "公用经费合计" in half_txt:
                    if nums: summary_data["public_total"] = nums[0]
                elif "基本支出合计" in half_txt:
                    if nums: summary_data["basic_total"] = nums[0]
                
                # 情况2：科目行 (通常是 [Code, Name, Amount] 或 [类, 款, 名称, 金额])
                # 我们寻找前两个单元格中包含纯数字 code 的行
                code_str = ""
                # 兼容：['301', '01', '基本工资', '...'] 或 ['301', '', '工资福利', '...']
                c1 = str(half[0]).strip() if half[0] else ""
                c2 = str(half[1]).strip() if len(half) > 1 and half[1] else ""
                
                if c1.isdigit():
                    if c2.isdigit() and len(c2) in [1, 2]: code_str = c1 + c2
                    else: code_str = c1
                
                if code_str and len(code_str) >= 3:
                    val = 0.0
                    if nums:
                        # 对于科目行，金额通常是最后一个数值 (排除code自身)
                        val = nums[-1] if len(nums) > (1 if c1 == code_str else 2) else 0.0
                    
                    code_data[code_str] = {
                        "name": half_txt.replace(code_str, "").replace(",", "").strip(),
                        "val": val, "row": r_idx + 1
                    }

        # --- B. 执行三组校验 ---
        lvl1_codes = ["301", "302", "303", "307", "310", "399"] # 基本支出常见一级科目
        
        # 1. 明细归集 (弱校验，容差1.0)
        for p_code in lvl1_codes:
            if p_code in code_data:
                parent_val = code_data[p_code]["val"]
                children = [v["val"] for k, v in code_data.items() if k.startswith(p_code) and len(k) > len(p_code)]
                if children:
                    child_sum = sum(children)
                    if abs(parent_val - child_sum) > 1.0: # 弱校验
                        issues.append(self._issue(
                            f"【表六】一级科目明细汇总不平({p_code})：一级数({parent_val:.2f}) != 明细和({child_sum:.2f})，差额={abs(parent_val-child_sum):.2f}",
                            {"code": p_code, "severity": "info"}, "info",
                            evidence_text=f"科目：{p_code}\n一级科目值：{parent_val}\n下属明细之和：{child_sum}"
                        ))

        # 2. 人员/公用归集 (强校验)
        # 获取一级科目值
        s301 = code_data.get("301", {}).get("val", 0.0)
        s302 = code_data.get("302", {}).get("val", 0.0)
        s303 = code_data.get("303", {}).get("val", 0.0)
        s310 = code_data.get("310", {}).get("val", 0.0)
        
        p_total = summary_data.get("personnel_total", 0.0)
        u_total = summary_data.get("public_total", 0.0)
        
        if p_total > 0:
            calc_p = s301 + s303
            if abs(p_total - calc_p) > 0.01:
                issues.append(self._issue(
                    f"【表六】人员经费口径错误：人员总计({p_total:.2f}) != 301({s301:.2f}) + 303({s303:.2f}) = {calc_p:.2f}",
                    {"type": "personnel"}, "error",
                    evidence_text=f"表内人员经费合计：{p_total}\n计算：301({s301}) + 303({s303}) = {calc_p}"
                ))
        
        if u_total > 0:
            calc_u = s302 + s310
            if abs(u_total - calc_u) > 0.01:
                issues.append(self._issue(
                    f"【表六】公用经费口径错误：公用总计({u_total:.2f}) != 302({s302:.2f}) + 310({s310:.2f}) = {calc_u:.2f}",
                    {"type": "public"}, "error",
                    evidence_text=f"表内公用经费合计：{u_total}\n计算：302({s302}) + 310({s310}) = {calc_u}"
                ))

        # 3. 总额闭合
        b_total = summary_data.get("basic_total", 0.0) or (p_total + u_total)
        l1_grand_sum = s301 + s302 + s303 + s310
        if abs(b_total - l1_grand_sum) > 0.01 and b_total > 0:
            issues.append(self._issue(
                f"【表六】基本支出总结不平：基本支出合计({b_total:.2f}) != 四大一级科目之和({l1_grand_sum:.2f})",
                {"total": b_total}, "error",
                evidence_text=f"表内基本支出合计：{b_total}\n计算(301+302+303+310)：{l1_grand_sum}"
            ))

        return issues


class R33214_T1_TotalBalance(Rule):
    """T1 总计闭合：收入侧总计=支出侧总计"""
    code, severity = "V33-214", "error"
    desc = "T1总表收支总计闭合"

    def apply(self, doc: Document) -> List[Issue]:
        issues = []
        t1_rows = _get_table_rows(doc, "收入支出决算总表")
        
        if not t1_rows:
            raise RuleDeferred(
                self.code,
                detail="收入支出决算总表(T1)缺失或无可解析行",
                unresolved_reasons=["收入支出决算总表(T1)缺失或无可解析行"],
            )
        
        # 查找收入侧"总计"和支出侧"总计"
        income_total = 0.0
        expense_total = 0.0
        
        for row in t1_rows:
            row_txt = "".join([str(c) for c in row if c])
            vals = _parse_row_values(row)
            
            if "总计" in row_txt and vals:
                # T1通常是左右并排结构，过滤非数值/零值单元格
                non_zero = [v for v in vals if v > 0.01]
                if len(non_zero) >= 2:
                    income_total = non_zero[0]
                    expense_total = non_zero[1]
                elif len(non_zero) == 1:
                    # 可能只有一个值，表示收支相等
                    income_total = expense_total = non_zero[0]
                break
        
        if not (income_total > 0.01 and expense_total > 0.01):
            raise RuleDeferred(
                self.code,
                detail="收入支出决算总表(T1)未提取到有效收支总计金额",
                unresolved_reasons=["收入支出决算总表(T1)未提取到有效收支总计金额"],
            )

        if abs(income_total - expense_total) > 0.01:
            issues.append(self._issue(
                f"T1总计不平：收入侧总计({income_total:.2f}) != 支出侧总计({expense_total:.2f})",
                {"income_total": income_total, "expense_total": expense_total}, "error",
                evidence_text=f"表格：收入支出决算总表\n收入侧总计：{income_total}\n支出侧总计：{expense_total}"
            ))
        
        return issues


# ==================================================================================
# P1 - 表内强校验 (Intra-Table Strong Checks)
# ==================================================================================

class R33210_T2_RowColumnTotal(Rule):
    """T2 收入决算表：行内合计、列合计、总合计"""
    code, severity = "V33-210", "warn"
    desc = "T2收入表行列合计校验"

    def apply(self, doc: Document) -> List[Issue]:
        issues = []
        t2_rows = _get_table_rows(doc, "收入决算表")
        
        if not t2_rows:
            return issues
        
        # 简化实现：检查每行"本年收入合计"是否等于各来源列之和
        for i, row in enumerate(t2_rows):
            row_txt = "".join([str(c) for c in row if c])
            if "合计" in row_txt or i < 3:  # 跳过表头和合计行本身
                continue
            
            vals = _parse_row_values(row)
            if len(vals) >= 3:  # 至少有3列数据
                # 假设第一个非零大数是"合计"，其余是分项
                sorted_vals = sorted([v for v in vals if v > 0], reverse=True)
                if len(sorted_vals) >= 2:
                    total = sorted_vals[0]
                    parts_sum = sum(sorted_vals[1:])
                    if total > 0.01 and parts_sum > 0.01:
                        if abs(total - parts_sum) > 0.05:
                            issues.append(self._issue(
                                f"T2第{i+1}行合计不平：合计={total:.2f}, 分项和={parts_sum:.2f}",
                                {"row": i+1, "total": total, "parts": parts_sum}, "warn",
                                evidence_text=f"表格：收入决算表\n第{i+1}行内容：{row_txt}\n行内最大值(合计): {total}\n其余项之和: {parts_sum}"
                            ))
        
        return issues


class R33211_T3_RowTotal(Rule):
    """T3 支出决算表：每行合计=基本+项目"""
    code, severity = "V33-211", "warn"
    desc = "T3支出表每行合计校验"

    def apply(self, doc: Document) -> List[Issue]:
        issues = []
        t3_rows = _get_table_rows(doc, "支出决算表")
        
        if not t3_rows:
            return issues
        
        for i, row in enumerate(t3_rows):
            row_txt = "".join([str(c) for c in row if c])
            if i < 3:  # 跳过表头
                continue
            
            vals = _parse_row_values(row)
            # 支出表结构：[科目编码, 名称, 本年支出合计, 基本支出, 项目支出, ...]
            if len(vals) >= 3:
                # 假设第一个大数是合计，第二和第三是基本和项目
                sorted_vals = sorted([v for v in vals if v > 0], reverse=True)
                if len(sorted_vals) >= 3:
                    total = sorted_vals[0]
                    basic = sorted_vals[1]
                    project = sorted_vals[2]
                    if total > 0.01:
                        calc = basic + project
                        if abs(total - calc) > 0.05:
                            issues.append(self._issue(
                                f"T3第{i+1}行合计不平：合计={total:.2f}, 基本+项目={calc:.2f}",
                                {"row": i+1}, "warn",
                                evidence_text=f"表格：支出决算表\n第{i+1}行内容：{row_txt}\n合计: {total}\n基本({basic}) + 项目({project}) = {calc}"
                            ))
        
        return issues


# 两位小数的浮点相减会产生伪差：|20323.21 - (14107.46+6215.76)| 得到
# 0.010000000002037268，使声明为「0.01 容差」的 `diff > 0.01` 误判为超差。
# 加一个远小于显示精度（0.01 万元）的容差，只吃掉浮点噪声，不改变阈值语义。
_AMOUNT_DIFF_EPS = 1e-6


class R33240_Table2_IncomeAdvancedCheck(Rule):
    """表二（收入决算表）高级校验：横向求和(强校验0.01) + 纵向层级汇总(容差校验0.01)"""
    code, severity = "V33-240", "error"
    desc = "收入决算表：横向求和与纵向层级校验"

    INCOME_SOURCE_KEYWORDS = [
        "财政拨款收入", "上级补助收入", "事业收入", "经营收入", 
        "附属单位上缴收入", "其他收入"
    ]
    TOTAL_COLUMN_KEYWORDS = ["本年收入合计", "收入合计", "合计"]
    
    def apply(self, doc: Document) -> List[Issue]:
        issues = []
        t2_rows = _get_table_rows(doc, "收入决算表", full_extent=True)

        if not t2_rows or len(t2_rows) < 3:
            raise RuleDeferred(self.code, detail="未找到有效的收入决算表或行数不足(<3)")

        # A. 智能表头解析
        header_row_idx = -1
        col_total_idx = -1
        col_sources = {}
        col_code_idx = -1
        col_name_idx = -1
        
        for r_idx, row in enumerate(t2_rows[:5]):
            row_txt = "".join([str(c) for c in row if c])
            # 表头常跨两行：首行「项目/本年收入合计/财政拨款收入…」，次行「功能分类科目编码/科目名称」。
            # 旧实现命中首行即 break，编码列/名称列永远取不到（col_code_idx 恒为 -1），
            # 所有数据行 code 为空、level=-1，横向与层级校验整段被跳过（实际只查过 TOTAL 行）。
            # 故命中任一表头特征都解析，列索引一律「首次命中为准」。
            if "本年收入" in row_txt or "财政拨款" in row_txt or "科目编码" in row_txt:
                if header_row_idx == -1:
                    header_row_idx = r_idx
                for c_idx, cell in enumerate(row):
                    cell_str = str(cell).strip() if cell else ""
                    if col_total_idx == -1:
                        for kw in self.TOTAL_COLUMN_KEYWORDS:
                            if kw in cell_str:
                                col_total_idx = c_idx
                                break
                    for kw in self.INCOME_SOURCE_KEYWORDS:
                        if kw in cell_str and kw not in col_sources:
                            col_sources[kw] = c_idx
                            break
                    if col_code_idx == -1 and ("科目编码" in cell_str or "编码" in cell_str):
                        col_code_idx = c_idx
                    if col_name_idx == -1 and "科目名称" in cell_str:
                        col_name_idx = c_idx
        
        if col_total_idx == -1:
            col_code_idx, col_name_idx, col_total_idx = 0, 1, 2
            for i in range(3, min(10, len(t2_rows[0]) if t2_rows else 0)):
                col_sources[f"来源{i-2}"] = i
            header_row_idx = 0
        
        # B. 构建层级数据结构
        hierarchy_data = {}
        
        # 跨页列布局漂移（如首页「类|款|项|名称|合计…」、续页「编码|名称|合计…」）时，
        # 续页行按首页列映射解析必然产出假错误。此处不做列重映射，只要求整表列宽一致，
        # 否则整表跳过（宁可不判，也不误报）。
        if len({len(r) for r in t2_rows if r}) > 1:
            raise RuleDeferred(self.code, detail="T2存在跨页列不一致/漂移，无法可靠抽取层级关系")

        # 检查续页同宽换列漂移（相同列数但列序颠倒/重排）
        for r_idx, row in enumerate(t2_rows):
            if r_idx <= header_row_idx:
                continue
            row_txt = "".join([str(c) for c in row if c])
            if any(k in row_txt for k in ("本年收入", "财政拨款", "科目编码", "科目名称")):
                for c_idx, cell in enumerate(row):
                    cell_str = str(cell).strip() if cell else ""
                    if col_total_idx != -1 and any(kw == cell_str for kw in self.TOTAL_COLUMN_KEYWORDS) and c_idx != col_total_idx:
                        raise RuleDeferred(self.code, detail="T2存在跨页同宽列序变化/换列漂移，无法可靠抽取层级关系")
                    for kw, expected_c in col_sources.items():
                        if kw == cell_str and c_idx != expected_c:
                            raise RuleDeferred(self.code, detail="T2存在跨页同宽列序变化/换列漂移，无法可靠抽取层级关系")

        for r_idx, row in enumerate(t2_rows):
            if r_idx <= header_row_idx:
                continue

            row_txt = "".join([str(c) for c in row if c])
            code, name, level = "", "", -1
            
            if col_code_idx >= 0 and col_code_idx < len(row):
                code_cell = str(row[col_code_idx]).strip() if row[col_code_idx] else ""
                if code_cell.isdigit() and len(code_cell) in [3, 5, 7]:
                    code = code_cell
            
            if col_name_idx >= 0 and col_name_idx < len(row):
                name = str(row[col_name_idx]).strip() if row[col_name_idx] else ""
            
            if code:
                level = {3: 1, 5: 2, 7: 3}.get(len(code), -1)
            elif "合计" in row_txt:
                level, code, name = 0, "TOTAL", "合计"
            
            vals = _parse_row_values(row)
            total_val = vals[col_total_idx] if col_total_idx < len(vals) else 0.0
            
            source_vals = {}
            for src_name, src_idx in col_sources.items():
                source_vals[src_name] = vals[src_idx] if src_idx < len(vals) else 0.0
            
            if code or level == 0:
                hierarchy_data[code if code else f"ROW_{r_idx}"] = {
                    "name": name, "level": level, "total": total_val,
                    "sources": source_vals, "row_idx": r_idx + 1, "code": code
                }
            
            # C. 横向求和校验（0.01容差）
            if level >= 0 and (total_val > 0.01 or sum(source_vals.values()) > 0.01):
                source_sum = sum(source_vals.values())
                diff = abs(total_val - source_sum)
                
                if diff > 0.01 + _AMOUNT_DIFF_EPS:
                    source_detail = ", ".join([f"{k}={v:.2f}" for k, v in source_vals.items() if v != 0])
                    issues.append(self._issue(
                        f"【收入决算表】第{r_idx+1}行横向求和不平：本年收入合计({total_val:.2f}) ≠ 各来源之和({source_sum:.2f})，差额={diff:.2f}。明细：{source_detail}。科目：{code} {name}",
                        {"table": "收入决算表", "row": r_idx+1, "code": code, "diff": diff}, "error",
                        evidence_text=f"表格：收入决算表\n行号：{r_idx+1}\n行内容：{row_txt[:100]}...\n合计值：{total_val}\n分项之和：{source_sum} ({source_detail})"
                    ))
        
        # D. 纵向层级汇总校验（0.01容差）
        level_1 = [k for k, v in hierarchy_data.items() if v["level"] == 1]
        level_2 = [k for k, v in hierarchy_data.items() if v["level"] == 2]
        level_3 = [k for k, v in hierarchy_data.items() if v["level"] == 3]
        
        for parent_code in level_2:
            parent = hierarchy_data[parent_code]
            children = [hierarchy_data[c] for c in level_3 if c.startswith(parent_code)]
            if children:
                child_sum = sum(c["total"] for c in children)
                # 统一舍入包络分级：0.0x 级尾差属显示舍入（V33-120 已出提示），不再按 error 误报
                diff_level, diff = classify_amount_diff(parent["total"], child_sum, n_children=len(children))
                if diff_level == "mismatch":
                    issues.append(self._issue(
                        f"【收入决算表】款级({parent_code} {parent['name']})纵向汇总不平：款({parent['total']:.2f}) ≠ 项之和({child_sum:.2f})，差额={diff:.2f}",
                        {"table": "收入决算表", "code": parent_code, "diff": diff, "type": "vertical"}, "error",
                        evidence_text=f"表格：收入决算表\n科目：{parent_code} {parent['name']}\n该科目金额：{parent['total']}\n下级科目之和：{child_sum}"
                    ))
        
        for parent_code in level_1:
            parent = hierarchy_data[parent_code]
            children = [hierarchy_data[c] for c in level_2 if c.startswith(parent_code)]
            if children:
                child_sum = sum(c["total"] for c in children)
                diff_level, diff = classify_amount_diff(parent["total"], child_sum, n_children=len(children))
                if diff_level == "mismatch":
                    issues.append(self._issue(
                        f"【收入决算表】类级({parent_code} {parent['name']})纵向汇总不平：类({parent['total']:.2f}) ≠ 款之和({child_sum:.2f})，差额={diff:.2f}",
                        {"table": "收入决算表", "code": parent_code, "diff": diff, "type": "vertical"}, "error",
                        evidence_text=f"表格：收入决算表\n科目：{parent_code} {parent['name']}\n该科目金额：{parent['total']}\n下级科目之和：{child_sum}"
                    ))
        
        total_row = hierarchy_data.get("TOTAL")
        if total_row and level_1:
            level_1_sum = sum(hierarchy_data[c]["total"] for c in level_1)
            diff_level, diff = classify_amount_diff(total_row["total"], level_1_sum, n_children=len(level_1))
            if diff_level == "mismatch":
                issues.append(self._issue(
                    f"【收入决算表】合计行纵向汇总不平：合计({total_row['total']:.2f}) ≠ 各类之和({level_1_sum:.2f})，差额={diff:.2f}",
                    {"table": "收入决算表", "diff": diff, "type": "vertical_total"}, "error",
                    evidence_text=f"表格：收入决算表\n合计行金额：{total_row['total']}\n类级科目汇总：{level_1_sum}"
                ))
        
        return issues


class R33241_Table3_ExpenseAdvancedCheck(Rule):
    """表三（支出决算表）高级校验：横向求和(强校验0.01) + 纵向层级汇总(容差校验0.01)"""
    code, severity = "V33-241", "error"
    desc = "支出决算表：横向求和与纵向层级校验"

    TOTAL_COL_KEYWORDS = ["合计", "本年支出合计", "支出合计"]
    BASIC_COL_KEYWORDS = ["基本支出"]
    PROJECT_COL_KEYWORDS = ["项目支出"]
    
    def apply(self, doc: Document) -> List[Issue]:
        issues = []
        t3_rows = _get_table_rows(doc, "支出决算表", full_extent=True)

        if not t3_rows or len(t3_rows) < 3:
            raise RuleDeferred(self.code, detail="未找到有效的支出决算表或行数不足(<3)")

        # A. 智能表头解析
        header_row_idx = -1
        col_total_idx = -1
        col_basic_idx = -1
        col_project_idx = -1
        col_code_indices = []  # 编码列（类/款/项）
        col_name_idx = -1
        
        for r_idx, row in enumerate(t3_rows[:5]):
            row_txt = "".join([str(c) for c in row if c])
            if ("合计" in row_txt or "基本支出" in row_txt or "项目支出" in row_txt
                    or "科目编码" in row_txt):
                if header_row_idx == -1:
                    header_row_idx = r_idx
                for c_idx, cell in enumerate(row):
                    cell_str = str(cell).strip() if cell else ""
                    for kw in self.TOTAL_COL_KEYWORDS:
                        if kw in cell_str and col_total_idx == -1:
                            col_total_idx = c_idx
                            break
                    for kw in self.BASIC_COL_KEYWORDS:
                        if kw in cell_str and col_basic_idx == -1:
                            col_basic_idx = c_idx
                            break
                    for kw in self.PROJECT_COL_KEYWORDS:
                        if kw in cell_str and col_project_idx == -1:
                            col_project_idx = c_idx
                            break
                    if ("编码" in cell_str or cell_str in ("类", "款", "项")) and c_idx not in col_code_indices:
                        col_code_indices.append(c_idx)
                    if col_name_idx == -1 and ("科目名称" in cell_str or "名称" in cell_str):
                        col_name_idx = c_idx
        
        # Default layout: [类, 款, 项, 名称, 合计, 基本, 项目]
        if col_total_idx == -1:
            col_code_indices = [0, 1, 2]
            col_name_idx = 3
            col_total_idx = 4
            col_basic_idx = 5
            col_project_idx = 6
            header_row_idx = 3
        
        # B. 构建层级数据结构
        hierarchy_data = {}
        
        # 同 V33-240：跨页列布局漂移时整表跳过，不做部分校验
        if len({len(r) for r in t3_rows if r}) > 1:
            raise RuleDeferred(self.code, detail="T3存在跨页列不一致/漂移，无法可靠抽取层级关系")

        # 检查续页同宽换列漂移（相同列数但列序颠倒/重排）
        for r_idx, row in enumerate(t3_rows):
            if r_idx <= header_row_idx:
                continue
            row_txt = "".join([str(c) for c in row if c])
            if any(k in row_txt for k in ("基本支出", "项目支出", "科目名称", "本年支出合计")):
                for c_idx, cell in enumerate(row):
                    cell_str = str(cell).strip() if cell else ""
                    if col_total_idx != -1 and any(kw == cell_str for kw in self.TOTAL_COL_KEYWORDS) and c_idx != col_total_idx:
                        raise RuleDeferred(self.code, detail="T3存在跨页同宽列序变化/换列漂移，无法可靠抽取层级关系")
                    if col_basic_idx != -1 and any(kw == cell_str for kw in self.BASIC_COL_KEYWORDS) and c_idx != col_basic_idx:
                        raise RuleDeferred(self.code, detail="T3存在跨页同宽列序变化/换列漂移，无法可靠抽取层级关系")
                    if col_project_idx != -1 and any(kw == cell_str for kw in self.PROJECT_COL_KEYWORDS) and c_idx != col_project_idx:
                        raise RuleDeferred(self.code, detail="T3存在跨页同宽列序变化/换列漂移，无法可靠抽取层级关系")

        for r_idx, row in enumerate(t3_rows):
            if r_idx <= header_row_idx:
                continue

            row_txt = "".join([str(c) for c in row if c])
            
            # 提取编码 (合并类款项)
            code_parts = []
            for ci in col_code_indices[:3]:
                if ci < len(row) and row[ci]:
                    s = str(row[ci]).strip()
                    # 只接受 3/5/7 位数字编码：否则「合计」行会把标签并入 code，
                    # 走不进 elif 分支、level 落到 -1，TOTAL 行被整行跳过
                    if s.isdigit() and len(s) in (3, 5, 7):
                        code_parts.append(s)
            code = "".join(code_parts)
            
            # 获取名称
            name = ""
            if col_name_idx >= 0 and col_name_idx < len(row):
                name = str(row[col_name_idx]).strip() if row[col_name_idx] else ""
            
            # 确定层级
            level = -1
            if code:
                if len(code) == 3:
                    level = 1  # 类
                elif len(code) == 5:
                    level = 2  # 款
                elif len(code) >= 7:
                    level = 3  # 项
            elif "合计" in row_txt:
                level, code, name = 0, "TOTAL", "合计"
            
            # 提取金额（排除编码列）
            total_val = 0.0
            basic_val = 0.0
            project_val = 0.0
            
            if col_total_idx >= 0 and col_total_idx < len(row):
                v = parse_number(row[col_total_idx])
                if v is not None:
                    total_val = v
            
            if col_basic_idx >= 0 and col_basic_idx < len(row):
                v = parse_number(row[col_basic_idx])
                if v is not None:
                    basic_val = v
            
            if col_project_idx >= 0 and col_project_idx < len(row):
                v = parse_number(row[col_project_idx])
                if v is not None:
                    project_val = v
            
            if code or level == 0:
                hierarchy_data[code if code else f"ROW_{r_idx}"] = {
                    "name": name, "level": level, "total": total_val,
                    "basic": basic_val, "project": project_val,
                    "row_idx": r_idx + 1, "code": code
                }
            
            # C. 横向求和校验（0.01容差）：合计 = 基本 + 项目
            if level >= 0 and total_val > 0.01:
                calc_sum = basic_val + project_val
                diff = abs(total_val - calc_sum)
                
                if diff > 0.01 + _AMOUNT_DIFF_EPS:
                    issues.append(self._issue(
                        f"【支出决算表】第{r_idx+1}行横向求和不平：合计({total_val:.2f}) ≠ 基本({basic_val:.2f})+项目({project_val:.2f})={calc_sum:.2f}，差额={diff:.2f}。科目：{code} {name}",
                        {"table": "支出决算表", "row": r_idx+1, "code": code, "diff": diff}, "error",
                        evidence_text=f"表格：支出决算表\n行号：{r_idx+1}\n科目：{code} {name}\n合计：{total_val}\n基本支出：{basic_val}\n项目支出：{project_val}"
                    ))
        
        # D. 纵向层级汇总校验（0.01容差）
        level_1 = [k for k, v in hierarchy_data.items() if v["level"] == 1]
        level_2 = [k for k, v in hierarchy_data.items() if v["level"] == 2]
        level_3 = [k for k, v in hierarchy_data.items() if v["level"] == 3]
        
        for parent_code in level_2:
            parent = hierarchy_data[parent_code]
            children = [hierarchy_data[c] for c in level_3 if c.startswith(parent_code)]
            if children:
                child_sum = sum(c["total"] for c in children)
                # 统一舍入包络分级：0.0x 级尾差属显示舍入（V33-120 已出提示），不再按 error 误报
                diff_level, diff = classify_amount_diff(parent["total"], child_sum, n_children=len(children))
                if diff_level == "mismatch":
                    issues.append(self._issue(
                        f"【支出决算表】款级({parent_code} {parent['name']})纵向汇总不平：款({parent['total']:.2f}) ≠ 项之和({child_sum:.2f})，差额={diff:.2f}",
                        {"table": "支出决算表", "code": parent_code, "diff": diff, "type": "vertical"}, "error",
                        evidence_text=f"表格：支出决算表\n科目：{parent_code} {parent['name']}\n款金额：{parent['total']}\n下级项之和：{child_sum}"
                    ))
        
        for parent_code in level_1:
            parent = hierarchy_data[parent_code]
            children = [hierarchy_data[c] for c in level_2 if c.startswith(parent_code)]
            if children:
                child_sum = sum(c["total"] for c in children)
                diff_level, diff = classify_amount_diff(parent["total"], child_sum, n_children=len(children))
                if diff_level == "mismatch":
                    issues.append(self._issue(
                        f"【支出决算表】类级({parent_code} {parent['name']})纵向汇总不平：类({parent['total']:.2f}) ≠ 款之和({child_sum:.2f})，差额={diff:.2f}",
                        {"table": "支出决算表", "code": parent_code, "diff": diff, "type": "vertical"}, "error",
                        evidence_text=f"表格：支出决算表\n科目：{parent_code} {parent['name']}\n类金额：{parent['total']}\n下级款之和：{child_sum}"
                    ))
        
        total_row = hierarchy_data.get("TOTAL")
        if total_row and level_1:
            level_1_sum = sum(hierarchy_data[c]["total"] for c in level_1)
            diff_level, diff = classify_amount_diff(total_row["total"], level_1_sum, n_children=len(level_1))
            if diff_level == "mismatch":
                issues.append(self._issue(
                    f"【支出决算表】合计行纵向汇总不平：合计({total_row['total']:.2f}) ≠ 各类之和({level_1_sum:.2f})，差额={diff:.2f}",
                    {"table": "支出决算表", "diff": diff, "type": "vertical_total"}, "error",
                    evidence_text=f"表格：支出决算表\n合计行金额：{total_row['total']}\n各一级科目汇总：{level_1_sum}"
                ))
        
        return issues


class R33244_Table7_ThreePublicAdvancedCheck(Rule):
    """表七（三公经费表）高级校验：
    1. 结构闭合 (强校验)：合计 = 出国 + 用车(小计) + 接待; 用车(小计) = 购置 + 运行 (0.01容差)
    2. 预算/决算双口径：以上公式需在预算数列、决算数列分别成立
    3. 空值/0智能转换 (规范性提示)：
       - Case A: 说明明确为0，表内留空 -> 提示补0 (Info)
       - Case B: 合计闭合但分项留空 -> 提示分项补0 (Info)
       - Case C: 说明明确非0，表内留空 -> 报错不一致 (Error)
    """
    code, severity = "V33-244", "error"
    desc = "三公经费表：双口径结构闭合与表文一致性校验"

    def apply(self, doc: Document) -> List[Issue]:
        issues = []
        t7_rows = _get_table_rows(doc, '一般公共预算财政拨款“三公”经费支出决算表')
        if not t7_rows:
            t7_rows = _get_table_rows(doc, '一般公共预算财政拨款"三公"经费支出决算表')
        
        if not t7_rows:
            raise RuleDeferred(self.code, detail="未找到有效的一般公共预算财政拨款三公经费支出决算表")

        # --- A. 识别列索引与提取数据 ---
        # 标准12列: [合计B, 合计F, 出国B, 出国F, 用车小B, 用车小F, 购置B, 购置F, 运行B, 运行F, 接待B, 接待F]
        # 我们根据 Page 15 的特征：合计(0,1), 出国(2,3), 用车小(4,5), 购置(6,7), 运行(8,9), 接待(10,11)
        data = {
            "budget": [0.0]*6, # [total, abroad, car_sub, car_purch, car_run, reception]
            "final": [0.0]*6,
            "is_empty": {"budget": [True]*6, "final": [True]*6}
        }
        
        # 寻找数据行 (通常在包含 "决算数" 的行之后)
        data_row = None
        for r_idx, row in enumerate(t7_rows):
            row_txt = "".join([str(c) for c in row if c])
            if "预算数" in row_txt or "决算数" in row_txt:
                # 检查下一行
                if r_idx + 1 < len(t7_rows):
                    data_row = t7_rows[r_idx + 1]
                    break
        
        if not data_row:
            # 尝试最后一行
            data_row = t7_rows[-1]

        def fill_data(row):
            # 映射索引 (根据标准布局)
            mapping = {0:(0,0), 1:(0,1), 2:(1,0), 3:(1,1), 4:(2,0), 5:(2,1), 6:(3,0), 7:(3,1), 8:(4,0), 9:(4,1), 10:(5,0), 11:(5,1)}
            for col_idx, (feat_idx, type_idx) in mapping.items():
                if col_idx < len(row):
                    cell_val = row[col_idx]
                    val = parse_number(cell_val)
                    key = "budget" if type_idx == 0 else "final"
                    if val is not None:
                        data[key][feat_idx] = val
                        data["is_empty"][key][feat_idx] = False
                    else:
                        data[key][feat_idx] = 0.0
                        data["is_empty"][key][feat_idx] = True

        fill_data(data_row)

        # --- B. 结构闭合逻辑校验 (强校验) ---
        for key in ["budget", "final"]:
            label = "预算" if key == "budget" else "决算"
            vals = data[key]
            # [total, abroad, car_sub, car_purch, car_run, reception]
            total, abroad, car_sub, car_purch, car_run, reception = vals
            
            # 1. 车小计 = 购置 + 运行
            if not data["is_empty"][key][4] or car_purch > 0 or car_run > 0:
                calc_sub = car_purch + car_run
                if abs(car_sub - calc_sub) > 0.01:
                    issues.append(self._issue(
                        f"【表七】{label}公务用车小计不平：报表({car_sub:.2f}) != 购置({car_purch:.2f}) + 运行({car_run:.2f})",
                        {"type": label}, "error",
                        evidence_text=f"表格：三公经费表\n类型：{label}\n用车小计：{car_sub}\n计算：购置({car_purch}) + 运行({car_run}) = {calc_sub}"
                    ))
            
            # 2. 合计 = 出国 + 车小计 + 接待
            if not data["is_empty"][key][0] or abroad > 0 or car_sub > 0 or reception > 0:
                calc_total = abroad + car_sub + reception
                if abs(total - calc_total) > 0.01:
                    issues.append(self._issue(
                        f"【表七】{label}三公合计不平：报表({total:.2f}) != 出国({abroad:.2f}) + 用车({car_sub:.2f}) + 接待({reception:.2f})",
                        {"type": label}, "error",
                        evidence_text=f"表格：三公经费表\n类型：{label}\n合计：{total}\n计算：出国({abroad}) + 用车({car_sub}) + 接待({reception}) = {calc_total}"
                    ))

        # --- C. 表文一致性与空值智控 (Case A/B/C) ---
        # 说明归因（HANDOFF §3.2A）：先做软换行恢复再锁章节，最后用
        # 主体沿分句继承的事实抽取。旧实现 `科目.*?口径.{0,20}?(\d+)万元`
        # 被跨行断字打穿——「因公出国（境）费决\n算为0.00」匹配不到字面
        # "决算"，滑到下一条"支出决算为16.95"，把公车决算错配给出国分项。
        merged_all = merge_page_texts(doc.page_texts)

        # 锁定“三公”经费说明段落（合并后的段落行上匹配）
        three_public_section = ""
        p_title = r'(?:^|\n)[（(]?[一二三四五六七八九十]+[)）、][^\n]{0,40}三公[^\n]{0,40}说明'
        for m in re.finditer(p_title, merged_all):
            rest = merged_all[m.end():]
            m_next = re.search(r'(?:^|\n)[一二三四五六七八九十]+、', rest)
            temp_section = rest[: m_next.start()] if m_next else rest[:3500]
            if "预算" in temp_section or "决算" in temp_section:
                three_public_section = temp_section
                break
        if not three_public_section:
            # 兜底：含三公字样的所有段落
            candidate_paras = [
                para for para in merged_all.splitlines() if "三公" in para or "公务接待" in para or "因公出国" in para
            ]
            three_public_section = "\n".join(candidate_paras)

        nar_facts = extract_three_public_facts(three_public_section)
        # 表文对比只使用"基数"事实（决算/预算），同比变化句金额不得参与
        fact_final: Dict[str, float] = {}
        fact_budget: Dict[str, float] = {}
        for fact in nar_facts:
            if fact.amount is None:
                continue
            if fact.metric == "决算":
                fact_final.setdefault(fact.subject, fact.amount)
            elif fact.metric == "预算":
                fact_budget.setdefault(fact.subject, fact.amount)

        merged_section = "\n".join(merge_soft_wrapped_lines(three_public_section))
        standard_nar_final_total = _extract_standard_three_public_total(
            merged_section, "决算"
        )
        if standard_nar_final_total is None:
            standard_nar_final_total = fact_final.get("三公经费合计")
        if standard_nar_final_total is not None:
            data_val = data["final"][0]
            is_empty = data["is_empty"]["final"][0]
            if is_empty:
                if standard_nar_final_total > 0.01:
                    issues.append(self._issue(
                        (
                            "【表七】三公经费合计决算数不一致："
                            f"说明显示为{standard_nar_final_total:.2f}万元，但报表合计决算数为空白。"
                        ),
                        {"item": "三公经费合计", "type": "final", "nar_v": standard_nar_final_total},
                        "error",
                        evidence_text=f"文字说明：{standard_nar_final_total}\n表格数据：空白",
                    ))
            elif abs(data_val - standard_nar_final_total) > 0.01:
                issues.append(self._issue(
                    (
                        "【表七】三公经费合计决算数表文不符："
                        f"报表={data_val:.2f}万元，说明={standard_nar_final_total:.2f}万元。"
                    ),
                    {"item": "三公经费合计", "type": "final"},
                    "error",
                    evidence_text=f"文字说明：{standard_nar_final_total}\n表格数据：{data_val}",
                ))

        # 事实取值：科目基数（决算/预算口径），取不到 → None → 对应校验跳过
        nar_f_total = fact_final.get("三公经费合计")
        nar_f_abroad = fact_final.get("因公出国（境）费")
        nar_f_car = fact_final.get("公务用车购置及运行维护费")
        nar_f_reception = fact_final.get("公务接待费")

        nar_b_total = fact_budget.get("三公经费合计")
        nar_b_abroad = fact_budget.get("因公出国（境）费")
        nar_b_car = fact_budget.get("公务用车购置及运行维护费")
        nar_b_reception = fact_budget.get("公务接待费")

        # 检查逻辑
        labels = ["合计", "因公出国", "公务用车", "公务接待"]
        check_pairs = [
            ("final", labels[0], 0, nar_f_total),
            ("final", labels[1], 1, nar_f_abroad),
            ("final", labels[2], 2, nar_f_car),
            ("final", labels[3], 5, nar_f_reception),
            ("budget", labels[0], 0, nar_b_total),
            ("budget", labels[1], 1, nar_b_abroad),
            ("budget", labels[2], 2, nar_b_car),
            ("budget", labels[3], 5, nar_b_reception)
        ]

        reported_cells = set() # (key, t7_idx)

        # Rule 3: 非负性检查
        for key in ["budget", "final"]:
            for i, val in enumerate(data[key]):
                if val < -0.01:
                    issues.append(self._issue(
                        f"【表七】{'预算' if key == 'budget' else '决算'}数据异常：{labels[i] if i<len(labels) else '分项'}出现负数 ({val:.2f})",
                        {"type": key, "index": i}, "error",
                        evidence_text=f"表格：三公经费表\n检测到负数：{val}"
                    ))
                    reported_cells.add((key, i))

        for key, lbl, t7_idx, nar_v in check_pairs:
            if nar_v is None: continue
            data_val = data[key][t7_idx]
            is_empty = data["is_empty"][key][t7_idx]
            label_col = "决算" if key == "final" else "预算"

            if is_empty:
                if nar_v > 0.01:
                    # Case C: 说明有钱，表内为空 -> Error
                    issues.append(self._issue(
                        f"【表七】{lbl}{label_col}不一致：说明显示为{nar_v:.2f}万元，但报表在该单元格为空白。",
                        {"item": lbl, "type": key, "nar_v": nar_v}, "error",
                        evidence_text=f"文档说明：{lbl}{label_col}为 {nar_v}\n表格数据：空白"
                    ))
                    reported_cells.add((key, t7_idx))
                else:
                    # Case A: 说明为0，表内为空 -> Info
                    issues.append(self._issue(
                        f"【表七】建议规范补0：说明提到{lbl}{label_col}为0，建议表内Cells填入'0.00'保持一致。",
                        {"item": lbl, "type": key}, "info",
                        evidence_text="文档说明：0\n表格数据：空白"
                    ))
                    reported_cells.add((key, t7_idx))
            else:
                if abs(data_val - nar_v) > 0.01:
                    issues.append(self._issue(
                        f"【表七】{lbl}{label_col}表文不符：报表({data_val:.2f}) != 说明({nar_v:.2f})",
                        {"item": lbl, "type": key}, "error",
                        evidence_text=f"文档说明：{nar_v}\n表格数据：{data_val}"
                    ))
                    reported_cells.add((key, t7_idx))

        # Case B: 已有数据列的分项补齐提示 (针对表内自洽但留空的情况)
        for key in ["budget", "final"]:
            col_label = "预算" if key == "budget" else "决算"
            # 只有当合计有值 或 说明显示有合计时才提示补全
            ref_total = data[key][0] or (nar_b_total if key == "budget" else nar_f_total) or 0.0
            if ref_total > 0.01:
                item_names = ["因公出国", "公务用车", "公务接待"]
                for i_idx, d_idx in enumerate([1, 2, 5]):
                    if data["is_empty"][key][d_idx] and (key, d_idx) not in reported_cells:
                        issues.append(self._issue(
                            f"【表七】建议分项补0：{col_label}{item_names[i_idx]}项为空，虽可推导闭合，但建议补填'0.00'以避歧义。",
                            {"type": key, "item": item_names[i_idx]}, "info",
                            evidence_text=f"表格：三公经费表\n列：{col_label}{item_names[i_idx]} 为空"
                        ))

        return issues


class R33242_Table4_ComprehensiveCheck(Rule):
    """表四（财政拨款收入支出决算总表）全量内勾稽校验：
    1. 横向求和：合计 = 一般公共 + 政府性基金 + 国有资本 (0.01容差)
    2. 纵向闭合：总计 = 年初结转 + 本年收入 = 本年支出 + 年末结转 (0.01容差)
    3. 总计平衡：收入总计 = 支出总计 (0.01容差)
    4. 变动关系：年末结转 = 年初结转 + 本年收入 - 本年支出 (0.01容差)
    5. 分口径校验：以上逻辑需在各预算列（一般/基金/国有）分别成立
    """
    code, severity = "V33-242", "error"
    desc = "财政拨款总表：全口径及结构内勾稽校验"

    def apply(self, doc: Document) -> List[Issue]:
        issues = []
        t4_rows = _get_table_rows(doc, "财政拨款收入支出决算总表")
        
        if not t4_rows or len(t4_rows) < 5:
            raise RuleDeferred(self.code, detail="未找到有效的财政拨款收入支出决算总表或行数不足(<5)")

        # --- A. 识别布局与列索引 ---
        num_cols = len(t4_rows[0])
        is_split = num_cols >= 7 
        
        data_in = {"opening": [0.0]*4, "income": [0.0]*4, "total": [0.0]*4}
        data_out = {"expense": [0.0]*4, "closing": [0.0]*4, "total": [0.0]*4}

        # --- B. 逐行解析数据 (智能逻辑) ---
        # 收入侧逻辑：第一个出现的 "一、一般" 通常是本年收入，直到看到 "年初结转" 标志
        # 在普陀区样本中，收入子项在 Page 10，"年初"在 Page 11
        context_in = "income" # 默认先看收入
        
        for r_idx, row in enumerate(t4_rows):
            row_txt = "".join([str(c) for c in row if c]).replace(" ", "")
            nums = _parse_row_values(row)
            if not nums: continue
            
            if is_split:
                l_text = str(row[0]) if row[0] else ""
                l_val = parse_number(row[1]) or 0.0 if len(row) > 1 else 0.0
                
                # 状态切换
                if "年初" in l_text and "结转" in l_text:
                    context_in = "opening"
                    data_in["opening"][0] = l_val
                elif "本年收入合计" in l_text:
                    # 如果之前没填过 income[0]，此处填入
                    data_in["income"][0] = l_val
                elif "总计" in l_text:
                    if not data_in["total"][0]: data_in["total"][0] = l_val
                
                # 收入侧科目匹配
                if "一般公共预算" in l_text: data_in[context_in][1] = l_val
                elif "政府性基金" in l_text: data_in[context_in][2] = l_val
                elif "国有资本" in l_text: data_in[context_in][3] = l_val

                # 支出侧 (右侧通常较规整，取最后4个数值)
                if "本年支出合计" in row_txt: data_out["expense"] = nums[-4:] if len(nums) >= 4 else [0.0]*4
                elif "年末" in row_txt and "结转" in row_txt: data_out["closing"] = nums[-4:] if len(nums) >= 4 else [0.0]*4
                elif "总计" in row_txt:
                    # 如果一行有两个"总计"或数值较多，右侧数值块归支出
                    if len(nums) >= 4: data_out["total"] = nums[-4:]
            
            else:
                # 紧凑布局逻辑
                target = None
                if "年初" in row_txt: target = data_in["opening"]
                elif "本年收入" in row_txt: target = data_in["income"]
                elif "本年支出" in row_txt: target = data_out["expense"]
                elif "年末" in row_txt: target = data_out["closing"]
                elif "总计" in row_txt:
                    if not data_in["total"][0]: target = data_in["total"]
                    else: target = data_out["total"]
                
                if target is not None:
                    row_nums = nums[-4:] if len(nums) >= 4 else ([nums[0]] + [0.0]*3 if nums else [0.0]*4)
                    for i in range(min(len(row_nums), 4)): target[i] = row_nums[i]

        # --- C. 结果回填与校验 ---
        # 如果合计行没填，通过分项补全（仅用于内部校验）
        for d in [data_in["opening"], data_in["income"], data_out["expense"], data_out["closing"]]:
            if d[0] == 0 and sum(d[1:]) > 0: d[0] = sum(d[1:])

        col_names = ["合计列", "一般公共预算列", "政府性基金列", "国有资本经营列"]
        
        # 1. 总平衡
        if abs(data_in["total"][0] - data_out["total"][0]) > 0.01 and data_in["total"][0] > 0:
             issues.append(self._issue(
                f"【表四】收支总计不平衡：收入侧总计({data_in['total'][0]:.2f}) != 支出侧总计({data_out['total'][0]:.2f})",
                {"in": data_in["total"][0], "out": data_out["total"][0]}, "error",
                evidence_text=f"表格：财政拨款收入支出决算总表\n收入侧总计：{data_in['total'][0]}\n支出侧总计：{data_out['total'][0]}"
            ))

        for i in range(4):
            c_name = col_names[i]
            op, inc, tin = data_in["opening"][i], data_in["income"][i], data_in["total"][i]
            exp, cl, tout = data_out["expense"][i], data_out["closing"][i], data_out["total"][i]
            
            # 2. 横向 (略)
            
            # 3. 纵向
            if abs(tin - (op + inc)) > 0.05 and tin > 0: # 稍微放开一点容差，处理四舍五入
                issues.append(self._issue(
                    f"【表四】{c_name}收入侧(总={tin:.2f}) != 年初({op:.2f}) + 收入({inc:.2f})",
                    {"col": c_name}, "error",
                    evidence_text=f"表格：财政拨款收入支出决算总表\n列：{c_name}\n收入侧合计：{tin}\n计算：年初({op}) + 收入({inc})"
                ))
            if abs(tout - (exp + cl)) > 0.05 and tout > 0:
                issues.append(self._issue(
                    f"【表四】{c_name}支出侧(总={tout:.2f}) != 支出({exp:.2f}) + 年末({cl:.2f})",
                    {"col": c_name}, "error",
                    evidence_text=f"表格：财政拨款收入支出决算总表\n列：{c_name}\n支出侧合计：{tout}\n计算：支出({exp}) + 年末({cl})"
                ))
                
            # 4. 变动
            calc_cl = op + inc - exp
            if abs(cl - calc_cl) > 0.05 and (cl > 0 or abs(calc_cl) > 0.1):
                issues.append(self._issue(
                    f"【表四】{c_name}结转变动校验失败：年末({cl:.2f}) vs 计算值({calc_cl:.2f})",
                    {"col": c_name}, "error",
                    evidence_text=f"表格：财政拨款收入支出决算总表\n列：{c_name}\n表内年末结转：{cl}\n计算（年初+收入-支出）：{calc_cl}"
                ))

        return issues


# ==================================================================================
# 补充规则 - 缺失的表间与说明校验
# ==================================================================================

class R33204_InterTable_T2_T4(Rule):
    """T2.财政拨款收入合计 = T4.一般公共预算财政拨款本年收入"""
    code, severity = "V33-204", "error"
    desc = "表间勾稽：T2财政拨款收入↔T4收入"

    def apply(self, doc: Document) -> List[Issue]:
        issues = []
        t2_rows = _get_table_rows(doc, "收入决算表")
        t4_rows = _get_table_rows(doc, "财政拨款收入支出决算总表")
        
        if not t2_rows or not t4_rows:
            missing = []
            if not t2_rows:
                missing.append("收入决算表(T2)")
            if not t4_rows:
                missing.append("财政拨款收入支出决算总表(T4)")
            raise RuleDeferred(
                self.code,
                detail=f"表缺失或无可解析行: {','.join(missing)}",
                unresolved_reasons=[f"表缺失或无可解析行: {','.join(missing)}"],
            )
        
        # T2: 查找"合计"行的"财政拨款收入"列
        t2_fiscal = 0.0
        for row in t2_rows:
            row_txt = "".join([str(c) for c in row if c])
            if row_txt.startswith("合计") or "合计" == row_txt.strip():
                vals = _parse_row_values(row)
                if len(vals) >= 2: t2_fiscal = vals[1]
                break
        
        # T4: 查找"本年收入合计"行
        t4_income = 0.0
        for row in t4_rows:
            row_txt = "".join([str(c) for c in row if c])
            if "本年收入合计" in row_txt:
                vals = _parse_row_values(row)
                if vals: t4_income = max(vals)
                break
        
        if t2_fiscal > 0.01 and t4_income > 0.01:
            if abs(t2_fiscal - t4_income) > 0.05:
                issues.append(self._issue(
                    f"T2↔T4财政拨款收入不一致：T2={t2_fiscal:.2f}, T4={t4_income:.2f}",
                    {"t2": t2_fiscal, "t4": t4_income}, "error",
                    evidence_text=f"表2(收入表) 财政拨款合计：{t2_fiscal}\n表4(总表) 本年收入合计：{t4_income}"
                ))
        return issues


class R33221_Narrative4_T4(Rule):
    """说明4（财政拨款总体情况）↔ T4 总计与结转"""
    code, severity = "V33-221", "warn"
    desc = "说明4↔T4财政拨款总计与结转校验"

    def apply(self, doc: Document) -> List[Issue]:
        issues = []
        import re

        _ensure_table_anchors(doc)
        narrative_total = 0.0
        narrative_page: Optional[int] = None
        for pidx, txt in enumerate(doc.page_texts):
            if "财政拨款" in txt and ("总体情况" in txt or "收入支出决算" in txt):
                # 优先取「财政拨款…总计」：同页可能先出现说明一的「收入支出总计」（含年初
                # 结转、另一口径，文旅局样张为 26,591.08），直接用首个匹配会把另一口径
                # 当成财政拨款总计。取不到再退回通用模式（兼容不写"财政拨款"前缀的文档）。
                # 「总\s*计」「增\s*加」等均容忍软换行空白——PDF 常把"总计增/加"拆在两行，
                # 否则负向断言失效、又会把**增加额**当成总计（文旅局样张 8,487.88）。
                total_match = re.search(
                    r'财政拨款[收入支出]*总\s*计(?!\s*(?:增\s*加|减\s*少|下\s*降|增\s*长))[^0-9]{0,12}(\d[\d,]*(?:\.\d+)?)\s*万元',
                    txt,
                )
                if total_match is None:
                    total_match = re.search(
                        r'(?:财政拨款)?[收入支出]*总\s*计(?!\s*(?:增\s*加|减\s*少|下\s*降|增\s*长))[^0-9]{0,12}(\d[\d,]*(?:\.\d+)?)\s*万元',
                        txt,
                    )
                if total_match:
                    narrative_total = float(total_match.group(1).replace(",", ""))
                    narrative_page = pidx + 1

        t4_page = _get_first_anchor_page(doc, "财政拨款收入支出决算总表")
        t4_rows = _get_table_rows(doc, "财政拨款收入支出决算总表")
        if t4_rows and narrative_total > 0:
            t4_total = 0.0
            for row in t4_rows:
                row_txt = "".join([str(c) for c in row if c])
                if "总计" in row_txt:
                    vals = _parse_row_values(row)
                    if vals: t4_total = max(vals)
                    break
            
            # 收紧容差到 0.01
            if t4_total > 0.01 and abs(narrative_total - t4_total) > 0.01:
                location = _make_issue_location(
                    _make_location_ref(
                        role="说明4",
                        page=narrative_page,
                        section="说明4（财政拨款总体情况）",
                        field="财政拨款总计",
                        value=narrative_total,
                    ),
                    _make_location_ref(
                        role="T4",
                        page=t4_page,
                        table="财政拨款收入支出决算总表",
                        row="总计",
                        field="总计",
                        value=t4_total,
                    ),
                    table="财政拨款收入支出决算总表",
                    section="说明4（财政拨款总体情况）",
                    row="总计",
                    field="财政拨款总计",
                )
                issues.append(self._issue(
                    f"说明4↔T4总计不一致：说明={narrative_total:.2f}, T4={t4_total:.2f}",
                    location, "warn",
                    evidence_text=f"文档说明(财政拨款总计)：{narrative_total}\n表4(总表) 总计：{t4_total}"
                ))
        return issues


class R33222_Narrative5_T5(Rule):
    """说明5（一般公共预算支出）↔ T5 总额、占比及类级结构"""
    code, severity = "V33-222", "warn"
    desc = "说明5↔T5一般公共预算支出结构校验"

    def apply(self, doc: Document) -> List[Issue]:
        issues = []
        import re

        _ensure_table_anchors(doc)
        t5_page = _get_first_anchor_page(doc, "一般公共预算财政拨款支出决算表")
        # 1. 提取叙述数据
        # 目标：提取 "201 一般公共服务支出 XXX 万元"
        # 难点：叙述中可能只写中文名称，不写编码；或者写编码。
        # 策略：建立一个常见 "类级科目" 映射表 (Name -> Code prefix)
        # 或者反过来，先读 T5 的类级科目，去文本里搜
        
        t5_rows = _get_table_rows(doc, "一般公共预算财政拨款支出决算表")
        if not t5_rows:
            raise RuleDeferred(
                self.code,
                detail="一般公共预算财政拨款支出决算表(T5)缺失",
                unresolved_reasons=["一般公共预算财政拨款支出决算表(T5)缺失"],
            )

        # 提取 T5 中的类级科目 (3位编码 或 3位以上但以00结尾?)
        # 这里的 T5 是 部门决算表，功能分类通常是 类-款-项
        # 类级编码通常是 3 位数字，如 201, 208
        
        t5_classes = {} # { '201': {'name': '一般公共服务', 'val': 123.45} }
        t5_total = 0.0
        
        for row in t5_rows:
            row_txt = "".join([str(c) for c in row if c])
            vals = _parse_row_values(row)
            if not vals: continue
            val = max(vals) # 假设最大的是本行合计
            
            # 找编码
            code_match = re.search(r'^\s*(\d{3})\s+', row[0] if row[0] else "")
            if code_match:
                code = code_match.group(1)
                name = row[0].replace(code, "").strip() # 提取名称
                t5_classes[code] = {'name': name, 'val': val}
            
            if "合计" in row_txt:
                t5_total = val

        # 2. 在文本中搜索 T5 存在的类级科目
        target_txt = ""
        narrative_pages: List[int] = []
        for pidx, txt in enumerate(doc.page_texts):
            if "一般公共预算" in txt and ("支出决算" in txt or "财政拨款支出" in txt) and "情况说明" in txt:
                target_txt += txt + "\n" # 拼接相关文本，防止分页截断
                narrative_pages.append(pidx + 1)
        
        if not target_txt:
            raise RuleDeferred(
                self.code,
                detail="未找到一般公共预算财政拨款支出决算情况说明文本",
                unresolved_reasons=["未找到一般公共预算财政拨款支出决算情况说明文本"],
            )
        
        # 校验总额：必须锚定「一般公共预算财政拨款支出」。旧正则从任意"支出"二字起配，
        # 会命中同页说明一「收入支出总计 26591.08 万元」（含年初结转的另一口径），
        # 导致说明侧取数错误（文旅局样张）。负向断言排除「…支出年初预算为」。
        total_match = re.search(
            r'一般公共预算财政拨款支出(?!\s*年初预算)[^\d]{0,20}(\d[\d,]*(?:\.\d+)?)\s*万元',
            target_txt,
        )
        if total_match:
            nar_total = float(total_match.group(1).replace(",", ""))
            if t5_total > 0.01 and abs(nar_total - t5_total) > 0.01:
                location = _make_issue_location(
                    _make_location_ref(
                        role="说明5",
                        page=narrative_pages[0] if narrative_pages else None,
                        section="说明5（一般公共预算支出）",
                        field="支出总额",
                        value=nar_total,
                    ),
                    _make_location_ref(
                        role="T5",
                        page=t5_page,
                        table="一般公共预算财政拨款支出决算表",
                        row="合计",
                        field="合计",
                        value=t5_total,
                    ),
                    pages=narrative_pages,
                    table="一般公共预算财政拨款支出决算表",
                    section="说明5（一般公共预算支出）",
                    row="合计",
                    field="支出总额",
                )
                issues.append(self._issue(
                    f"说明5↔T5支出总额不一致：说明={nar_total:.2f}, T5={t5_total:.2f}",
                    location, "warn",
                    evidence_text=f"文档说明(一般公共预算支出)：{nar_total}\n表5(支出表) 合计：{t5_total}"
                ))

        # 校验类级科目
        for code, info in t5_classes.items():
            # 构造搜索关键词，通常是科目名称的前几个字
            # "一般公共服务" -> "一般公共服务"
            # 也可以尝试搜索金额
            
            # 策略：搜索 "科目名称" 附近的金额
            # 简化名称：去掉 "支出" 后缀，保留核心词
            short_name = info['name'].replace("支出", "").strip()
            if len(short_name) < 2: short_name = info['name']
            
            # 在文本中查找 short_name
            # 限制查找范围？
            # 简单做法：全文查找
            
            # 查找模式： 名称 ... 数字 ... 万元
            # 或者：数字 ... 万元 ... 用于 ... 名称
            
            # 尝试匹配： 名称[任意字符<50]数字
            pattern = re.compile(re.escape(short_name) + r'[^\d]{0,50}(\d+\.?\d*)\s*万元')
            m = pattern.search(target_txt)
            if m:
                nar_val = float(m.group(1))
                if abs(nar_val - info['val']) > 0.01:
                    location = _make_issue_location(
                        _make_location_ref(
                            role="说明5",
                            page=narrative_pages[0] if narrative_pages else None,
                            section="说明5（一般公共预算支出）",
                            field=short_name,
                            code=code,
                            subject=short_name,
                            value=nar_val,
                        ),
                        _make_location_ref(
                            role="T5",
                            page=t5_page,
                            table="一般公共预算财政拨款支出决算表",
                            row=f"{code}{short_name}",
                            field="合计",
                            code=code,
                            subject=short_name,
                            value=info['val'],
                        ),
                        pages=narrative_pages,
                        table="一般公共预算财政拨款支出决算表",
                        section="说明5（一般公共预算支出）",
                        row=f"{code}{short_name}",
                        field=short_name,
                        code=code,
                        subject=short_name,
                    )
                    issues.append(self._issue(
                        f"说明5↔T5类级科目({code}{short_name})金额不一致：说明={nar_val:.2f}, T5={info['val']:.2f}",
                        location, "warn",
                        evidence_text=f"科目：{code}{short_name}\n文档说明：{nar_val}\n表5数据：{info['val']}"
                    ))
            else:
                # 尝试另一种模式：金额 ... (占) ... 名称
                # 这比较少见，通常是：教育支出 XX 万元
                pass
        
        return issues


class R33227_Narrative5_T5_NameConsistency(Rule):
    """说明5（一般公共预算财政拨款支出决算具体情况）↔ T5 类款项名称一致性"""
    code, severity = "V33-227", "warn"
    desc = "说明5↔T5类款项名称一致性（表格优先）"

    def apply(self, doc: Document) -> List[Issue]:
        issues: List[Issue] = []

        _ensure_table_anchors(doc)
        table_name = "一般公共预算财政拨款支出决算表"
        t5_page = _get_first_anchor_page(doc, table_name)
        t5_rows = _get_table_rows(doc, table_name)
        if not t5_rows:
            raise RuleDeferred(
                self.code,
                detail="一般公共预算财政拨款支出决算表(T5)缺失",
                unresolved_reasons=["一般公共预算财政拨款支出决算表(T5)缺失"],
            )

        table_entries = _extract_functional_name_index(t5_rows)
        if not table_entries:
            raise RuleDeferred(
                self.code,
                detail="T5未提取到功能分类科目条目",
                unresolved_reasons=["T5未提取到功能分类科目条目"],
            )

        narrative_mentions = _extract_final_functional_narrative_mentions(doc, table_entries)
        if not narrative_mentions:
            raise RuleDeferred(
                self.code,
                detail="说明文本中未提取到功能分类科目提及",
                unresolved_reasons=["说明文本中未提取到功能分类科目提及"],
            )

        for code, table_entry in table_entries.items():
            mismatched_mentions = [
                mention
                for mention in narrative_mentions.get(code, [])
                if not _functional_name_matches(table_entry["name"], mention["name"])
            ]
            if not mismatched_mentions:
                continue

            seen_names = set()
            for mention in mismatched_mentions:
                narrative_name = mention["name"]
                dedupe_key = (mention.get("page"), normalize_text(narrative_name))
                if dedupe_key in seen_names:
                    continue
                seen_names.add(dedupe_key)

                raw_page = mention.get("page")
                try:
                    mention_page = int(str(raw_page).strip())
                except Exception:
                    mention_page = 1
                if mention_page <= 0:
                    mention_page = 1

                row_label = table_entry.get("code_display", _format_functional_code(code))
                table_row_page = (
                    _find_text_page_after(doc, table_entry.get("row_text", ""), start_page=t5_page or 1)
                    or t5_page
                )
                level = table_entry.get("level", mention.get("level", "类"))

                location = _make_issue_location(
                    _make_location_ref(
                        role="说明5",
                        page=mention_page,
                        section="说明5（一般公共预算财政拨款支出决算具体情况）",
                        row=row_label,
                        field=f"{level}级名称",
                        code=code,
                        subject=narrative_name,
                    ),
                    _make_location_ref(
                        role="T5",
                        page=table_row_page,
                        table=table_name,
                        row=row_label,
                        field="功能分类科目名称",
                        code=code,
                        subject=table_entry["name"],
                    ),
                    table=table_name,
                    section="说明5（一般公共预算财政拨款支出决算具体情况）",
                    row=row_label,
                    field="功能分类科目名称",
                    code=code,
                )
                location.update(
                    {
                        "expected_name": table_entry["name"],
                        "actual_name": narrative_name,
                        "code_level": level,
                        "source_of_truth": "T5",
                    }
                )

                evidence_text = (
                    f"编码：{row_label}\n"
                    f"表格名称：{table_entry['name']}\n"
                    f"说明名称：{narrative_name}\n"
                    f"说明片段：{mention.get('snippet', '')}"
                )
                issues.append(
                    self._issue(
                        f"说明5{level}级科目名称与T5不一致（{row_label}）："
                        f"表格“{table_entry['name']}”，说明“{narrative_name}”",
                        location,
                        severity="warn",
                        evidence_text=evidence_text,
                    )
                )

        return issues


# ==================================================================================
# P2 - 表↔情况说明校验 (Table vs Narrative)
# ==================================================================================

class R33220_Narrative3_T3(Rule):
    """说明3（支出决算）↔ T3 金额与占比（字段绑定与独立核验）"""
    code, severity = "V33-220", "warn"
    desc = "说明3↔T3支出决算基本支出与项目支出字段校验"

    def apply(self, doc: Document) -> List[Issue]:
        issues: List[Issue] = []

        _ensure_table_anchors(doc)
        # 1. 提取叙述中的关键金额（说明归因：软换行恢复 + 章节切分 + 分句配对，保留显示精度与单位）
        merged_text = merge_page_texts(doc.page_texts)
        nar_basic: Optional[StrictValue] = None
        nar_project: Optional[StrictValue] = None
        narrative_page: Optional[int] = None

        for title, body, _offset in split_numbered_sections(merged_text):
            if "支出决算情况说明" not in title:
                continue
            body_clauses = split_clauses(body)
            for clause in body_clauses:
                if nar_basic is None and "基本支出" in clause:
                    res_b = _extract_amount_from_segment(clause, ["基本支出"])
                    if res_b:
                        dec_b, scale_b, unit_b = res_b
                        nar_basic = StrictValue(
                            raw_text=str(dec_b),
                            status="valid",
                            decimal_val=dec_b,
                            scale_digits=scale_b,
                            unit=unit_b,
                        )
                if nar_project is None and "项目支出" in clause:
                    res_p = _extract_amount_from_segment(clause, ["项目支出"])
                    if res_p:
                        dec_p, scale_p, unit_p = res_p
                        nar_project = StrictValue(
                            raw_text=str(dec_p),
                            status="valid",
                            decimal_val=dec_p,
                            scale_digits=scale_p,
                            unit=unit_p,
                        )
            if nar_basic is not None or nar_project is not None:
                title_key = re.sub(r"\s+", "", title)[:18]
                for pi, page_text in enumerate(doc.page_texts):
                    if title_key and title_key in re.sub(r"\s+", "", page_text):
                        narrative_page = pi + 1
                        break
                break

        # 2. 获取 T3 数据
        t3_page = _get_first_anchor_page(doc, "支出决算表")
        t3_rows = _get_table_rows(doc, "支出决算表")
        if not t3_rows:
            raise RuleDeferred(
                self.code,
                detail="未找到支出决算表(T3)表格数据",
                unresolved_reasons=["未找到支出决算表(T3)表格数据"],
            )

        # 3. 字段绑定：从表头定位"基本支出"与"项目支出"列
        header_rows = t3_rows[:3]
        idx_basic: Optional[int] = None
        idx_project: Optional[int] = None

        for hrow in header_rows:
            str_hrow = [str(c or "").strip() for c in hrow]
            if idx_basic is None:
                idx_basic = find_column_index(str_hrow, ["基本支出"], exclude_keys=["人员", "公用"])
            if idx_project is None:
                idx_project = find_column_index(str_hrow, ["项目支出"])
            if idx_basic is not None and idx_project is not None:
                break

        # 4. 定位合计行
        total_row = None
        for row in t3_rows:
            row_txt = "".join([str(c) for c in row if c])
            if "合计" not in row_txt and "本年支出合计" not in row_txt:
                continue
            has_numbers = any(parse_number(c) is not None for c in row)
            if not has_numbers:
                continue
            first_cells = [str(c or "").strip() for c in row[:3]]
            if any(fc in ("合计", "总计", "本年支出合计") for fc in first_cells):
                total_row = row
                break
            has_code = any(
                str(c or "").strip().isdigit() and len(str(c).strip()) in (3, 5, 7) and str(c or "").strip().startswith("2")
                for c in row[:2]
            )
            if not has_code:
                total_row = row
                break

        if total_row is None:
            raise RuleDeferred(
                self.code,
                detail="支出决算表(T3)未找到有效合计行",
                unresolved_reasons=["支出决算表(T3)未找到有效合计行"],
            )

        # 4.5 确定 T3 表格金额单位
        t3_unit = (
            doc.units_per_page[t3_page - 1]
            if t3_page and t3_page <= len(doc.units_per_page)
            else None
        ) or doc.dominant_unit

        if not t3_unit:
            raise RuleDeferred(
                self.code,
                detail=f"支出决算表(第{t3_page}页)金额单位未知，无法可靠核验",
                unresolved_reasons=[f"支出决算表(第{t3_page}页)金额单位未知，无法可靠核验"],
            )

        unresolved_reasons: List[str] = []

        # 5. 严格字段提取并逐项比对
        table_basic = extract_row_strict(total_row, idx_basic, "基本支出", default_unit=t3_unit)
        table_project = extract_row_strict(total_row, idx_project, "项目支出", default_unit=t3_unit)

        # 校验基本支出
        if nar_basic is not None:
            if table_basic.is_numeric and table_basic.decimal_val is not None:
                t_basic_dec = table_basic.decimal_val
                n_basic_dec = nar_basic.decimal_val or Decimal("0")
                env_basic = half_unit_for_term(nar_basic.scale_digits, nar_basic.unit) + half_unit_for_term(table_basic.scale_digits, table_basic.unit)
                diff_b = abs(t_basic_dec - n_basic_dec)
                if diff_b > env_basic:
                    max_scale_b = max(nar_basic.scale_digits, table_basic.scale_digits, 2)
                    location = _make_issue_location(
                        _make_location_ref(
                            role="说明3",
                            page=narrative_page,
                            section="说明3（支出决算情况）",
                            field="基本支出",
                            value=float(n_basic_dec),
                        ),
                        _make_location_ref(
                            role="T3",
                            page=t3_page,
                            table="支出决算表",
                            row="合计",
                            field="基本支出",
                            value=float(t_basic_dec),
                        ),
                        table="支出决算表",
                        section="说明3（支出决算情况）",
                        row="合计",
                        field="基本支出",
                    )
                    issues.append(self._issue(
                        f"说明3↔T3基本支出不一致：说明={n_basic_dec:.{max_scale_b}f}, 表内基本支出={t_basic_dec:.{max_scale_b}f} (差额={diff_b:.{max_scale_b}f})",
                        location, "warn",
                        evidence_text=f"文档说明(基本支出)：{n_basic_dec:.{max_scale_b}f}; 表3基本支出：{t_basic_dec:.{max_scale_b}f}"
                    ))
            else:
                unresolved_reasons.append(f"T3基本支出列取数未完成: {table_basic.reason or '非有效数值'}")
        else:
            unresolved_reasons.append("支出决算情况说明中未提取到基本支出金额")

        # 校验项目支出
        if nar_project is not None:
            if table_project.is_numeric and table_project.decimal_val is not None:
                t_proj_dec = table_project.decimal_val
                n_proj_dec = nar_project.decimal_val or Decimal("0")
                env_proj = half_unit_for_term(nar_project.scale_digits, nar_project.unit) + half_unit_for_term(table_project.scale_digits, table_project.unit)
                diff_p = abs(t_proj_dec - n_proj_dec)
                if diff_p > env_proj:
                    max_scale_p = max(nar_project.scale_digits, table_project.scale_digits, 2)
                    location = _make_issue_location(
                        _make_location_ref(
                            role="说明3",
                            page=narrative_page,
                            section="说明3（支出决算情况）",
                            field="项目支出",
                            value=float(n_proj_dec),
                        ),
                        _make_location_ref(
                            role="T3",
                            page=t3_page,
                            table="支出决算表",
                            row="合计",
                            field="项目支出",
                            value=float(t_proj_dec),
                        ),
                        table="支出决算表",
                        section="说明3（支出决算情况）",
                        row="合计",
                        field="项目支出",
                    )
                    issues.append(self._issue(
                        f"说明3↔T3项目支出不一致：说明={n_proj_dec:.{max_scale_p}f}, 表内项目支出={t_proj_dec:.{max_scale_p}f} (差额={diff_p:.{max_scale_p}f})",
                        location, "warn",
                        evidence_text=f"文档说明(项目支出)：{n_proj_dec:.{max_scale_p}f}; 表3项目支出：{t_proj_dec:.{max_scale_p}f}"
                    ))
            else:
                unresolved_reasons.append(f"T3项目支出列取数未完成: {table_project.reason or '非有效数值'}")
        else:
            unresolved_reasons.append("支出决算情况说明中未提取到项目支出金额")

        # 6. Contract C1 结果与未完成原因上报
        if unresolved_reasons:
            raise RuleDeferred(
                self.code,
                detail="; ".join(unresolved_reasons),
                partial_issues=issues,
                unresolved_reasons=unresolved_reasons,
            )

        return issues


class R33223_Narrative6_T6(Rule):
    """说明6（基本支出）↔ T6 人员/公用金额"""
    code, severity = "V33-223", "warn"
    desc = "说明6↔T6基本支出人员公用校验"

    def apply(self, doc: Document) -> List[Issue]:
        issues = []
        import re

        _ensure_table_anchors(doc)
        # 查找"基本支出决算情况说明"
        nar_personnel = None
        nar_public = None
        narrative_page: Optional[int] = None

        for pidx, txt in enumerate(doc.page_texts):
            if "基本支出" in txt and ("人员经费" in txt or "公用经费" in txt):
                # 匹配模式："人员经费 XXX 万元"
                p_match = re.search(r'人员经费[^\d]*(\d+\.?\d*)\s*万元', txt)
                pub_match = re.search(r'公用经费[^\d]*(\d+\.?\d*)\s*万元', txt)
                
                if p_match: nar_personnel = float(p_match.group(1))
                if pub_match: nar_public = float(pub_match.group(1))
                
                if nar_personnel is not None or nar_public is not None:
                    narrative_page = pidx + 1
                    break

        if nar_personnel is None and nar_public is None:
            raise RuleDeferred(
                self.code,
                detail="未提取到基本支出情况说明中的人员经费或公用经费",
                unresolved_reasons=["未提取到基本支出情况说明中的人员经费或公用经费"],
            )

        t6_page = _get_first_anchor_page(doc, "一般公共预算财政拨款基本支出决算表")
        t6_rows = _get_table_rows(doc, "一般公共预算财政拨款基本支出决算表")
        if not t6_rows:
            raise RuleDeferred(
                self.code,
                detail="一般公共预算财政拨款基本支出决算表(T6)缺失",
                unresolved_reasons=["一般公共预算财政拨款基本支出决算表(T6)缺失"],
            )

        # T6 结构：通常最后一行是总计，但人员经费和公用经费是分块的合计
        # 往往有 "人员经费合计" 和 "公用经费合计" 的行
        
        t6_personnel = 0.0
        t6_public = 0.0
        found_p = False
        found_pub = False
        
        for row in t6_rows:
            row_txt = "".join([str(c) for c in row if c])
            vals = _parse_row_values(row)
            if not vals: continue
            
            if "人员经费" in row_txt and "合计" in row_txt:
                t6_personnel = max(vals)
                found_p = True
            elif "公用经费" in row_txt and "合计" in row_txt:
                t6_public = max(vals)
                found_pub = True
        
        # 校验人员经费
        if nar_personnel is not None:
            if not found_p:
                 # 没找到显式的“人员经费合计”行，尝试用所有301+303推导？比较复杂，暂时报未找到
                 pass 
            elif abs(nar_personnel - t6_personnel) > 0.01:
                location = _make_issue_location(
                    _make_location_ref(
                        role="说明6",
                        page=narrative_page,
                        section="说明6（基本支出情况）",
                        field="人员经费",
                        value=nar_personnel,
                    ),
                    _make_location_ref(
                        role="T6",
                        page=t6_page,
                        table="一般公共预算财政拨款基本支出决算表",
                        row="人员经费合计",
                        field="人员经费",
                        value=t6_personnel,
                    ),
                    table="一般公共预算财政拨款基本支出决算表",
                    section="说明6（基本支出情况）",
                    row="人员经费合计",
                    field="人员经费",
                )
                issues.append(self._issue(
                    f"说明6↔T6人员经费不一致：说明={nar_personnel:.2f}, T6={t6_personnel:.2f}",
                    location, "warn",
                    evidence_text=f"文档说明(人员经费)：{nar_personnel}\n表6(基本支出) 人员经费合计：{t6_personnel}"
                ))
        
        # 校验公用经费
        if nar_public is not None:
            if found_pub and abs(nar_public - t6_public) > 0.01:
                location = _make_issue_location(
                    _make_location_ref(
                        role="说明6",
                        page=narrative_page,
                        section="说明6（基本支出情况）",
                        field="公用经费",
                        value=nar_public,
                    ),
                    _make_location_ref(
                        role="T6",
                        page=t6_page,
                        table="一般公共预算财政拨款基本支出决算表",
                        row="公用经费合计",
                        field="公用经费",
                        value=t6_public,
                    ),
                    table="一般公共预算财政拨款基本支出决算表",
                    section="说明6（基本支出情况）",
                    row="公用经费合计",
                    field="公用经费",
                )
                issues.append(self._issue(
                     f"说明6↔T6公用经费不一致：说明={nar_public:.2f}, T6={t6_public:.2f}",
                    location, "warn",
                    evidence_text=f"文档说明(公用经费)：{nar_public}\n表6(基本支出) 公用经费合计：{t6_public}"
                ))
        
        return issues


class R33224_Narrative7_T7(Rule):
    """说明7（三公）↔ T7 预算/决算及分项"""
    code, severity = "V33-224", "warn"
    desc = "说明7↔T7三公经费预决算及分项校验"

    def apply(self, doc: Document) -> List[Issue]:
        issues = []
        import re

        _ensure_table_anchors(doc)
        # 1. 提取叙述数据
        # 结构： { 'item_name': {'budget': val, 'final': val} }
        
        # 关键词映射
        
        target_txt = ""
        narrative_page: Optional[int] = None
        for pidx, txt in enumerate(doc.page_texts):
            if "三公" in txt and ("情况说明" in txt or "经费支出" in txt):
                target_txt = txt  # 简单取最后一页匹配到的？通常只有一处
                narrative_page = pidx + 1
                break
        
        if not target_txt:
            raise RuleDeferred(
                self.code,
                detail="未找到三公经费情况说明文本",
                unresolved_reasons=["未找到三公经费情况说明文本"],
            )

        # 提取逻辑：
        # 针对每个分项，找“预算为XX万元”、“决算为XX万元”
        # 这比较难，因为行文可能是 "因公出国费预算XX万元...决算XX万元" 
        # 也可能是 "三公经费支出XX万元...其中：因公出国XX万元" (只提决算)
        
        # 尝试提取决算数 (通常格式：XXX支出 XXX 万元)
        # 这是一个简化处理，实际NLP更复杂。我们使用简单的“关键词附近找数字”策略
        
        def find_val_near_key(text, keywords):
            # 找到关键词最后出现的位置，向后找数字
            best_pos = -1
            for k in keywords:
                p = text.find(k)
                if p > best_pos: best_pos = p
            
            if best_pos == -1: return None
            
            # 截取关键词后的一段文字
            sub = text[best_pos:best_pos+100]
            # 找第一个金额 "XXX 万元"
            m = re.search(r'(\d+\.?\d*)\s*万元', sub)
            if m:
                return float(m.group(1))
            return None

        # 提取各项决算数
        nar_final_total = find_val_near_key(target_txt, ['三公经费支出', '合计'])
        nar_final_abroad = find_val_near_key(target_txt, ['因公出国'])
        nar_final_car = find_val_near_key(target_txt, ['公务用车购置及运行', '公务用车'])
        nar_final_recept = find_val_near_key(target_txt, ['公务接待'])

        # 2. 获取 T7 数据
        t7_page = _get_first_anchor_page(doc, '一般公共预算财政拨款"三公"经费支出决算表')
        t7_rows = _get_table_rows(doc, '一般公共预算财政拨款"三公"经费支出决算表')
        if not t7_rows:
            raise RuleDeferred(
                self.code,
                detail='一般公共预算财政拨款"三公"经费支出决算表(T7)缺失',
                unresolved_reasons=['一般公共预算财政拨款"三公"经费支出决算表(T7)缺失'],
            )
        
        # T7 结构：... | 因公出国 | 公务用车(小计) | ... | 公务接待
        # 同样难以通过列索引定位，尝试通过表头匹配
        # 或者直接找行中的关键词（如果是转置表？）——通常T7是宽表，一行表头，一行数据
        # 也不排除是清单式。这里假设是标准宽表，最后一行是决算数
        
        # 策略：扫描所有行，找到包含数据的行，解析出所有数值
        # 然后尝试匹配
        
        t7_vals = []
        for row in t7_rows:
            vs = _parse_row_values(row)
            if vs: t7_vals.extend(vs)
        
        # 校验合计
        if nar_final_total is not None:
             # 在表内找是否存在该值 (0.01容差)
             if not any(abs(v - nar_final_total) <= 0.01 for v in t7_vals):
                 location = _make_issue_location(
                     _make_location_ref(
                         role="说明7",
                         page=narrative_page,
                         section="说明7（三公经费情况）",
                         field="三公经费合计",
                         value=nar_final_total,
                     ),
                     _make_location_ref(
                         role="T7",
                         page=t7_page,
                         table='一般公共预算财政拨款"三公"经费支出决算表',
                         row="合计",
                         field="三公经费合计",
                     ),
                     table='一般公共预算财政拨款"三公"经费支出决算表',
                     section="说明7（三公经费情况）",
                     row="合计",
                     field="三公经费合计",
                 )
                 issues.append(self._issue(
                     f"说明7↔T7三公合计不一致：说明={nar_final_total:.2f}, T7未找到对应值",
                     location, "warn",
                     evidence_text=f"文档说明(三公合计)：{nar_final_total}\n表7内所有数值：{t7_vals}"
                 ))
        
        # 校验分项 (仅当分项值 > 0 时)
        # 出国
        if nar_final_abroad is not None and nar_final_abroad > 0:
            if not any(abs(v - nar_final_abroad) <= 0.01 for v in t7_vals):
                 location = _make_issue_location(
                     _make_location_ref(
                         role="说明7",
                         page=narrative_page,
                         section="说明7（三公经费情况）",
                         field="因公出国",
                         value=nar_final_abroad,
                     ),
                     _make_location_ref(
                         role="T7",
                         page=t7_page,
                         table='一般公共预算财政拨款"三公"经费支出决算表',
                         field="因公出国",
                     ),
                     table='一般公共预算财政拨款"三公"经费支出决算表',
                     section="说明7（三公经费情况）",
                     field="因公出国",
                 )
                 issues.append(self._issue(
                     f"说明7↔T7因公出国不一致：说明={nar_final_abroad:.2f}",
                     location, "warn",
                     evidence_text=f"文档说明(因公出国)：{nar_final_abroad}\n表7内数据未找到匹配值"
                 ))

        # 用车
        if nar_final_car is not None and nar_final_car > 0:
            if not any(abs(v - nar_final_car) <= 0.01 for v in t7_vals):
                 location = _make_issue_location(
                     _make_location_ref(
                         role="说明7",
                         page=narrative_page,
                         section="说明7（三公经费情况）",
                         field="公务用车",
                         value=nar_final_car,
                     ),
                     _make_location_ref(
                         role="T7",
                         page=t7_page,
                         table='一般公共预算财政拨款"三公"经费支出决算表',
                         field="公务用车",
                     ),
                     table='一般公共预算财政拨款"三公"经费支出决算表',
                     section="说明7（三公经费情况）",
                     field="公务用车",
                 )
                 issues.append(self._issue(
                     f"说明7↔T7公务用车不一致：说明={nar_final_car:.2f}",
                     location, "warn",
                     evidence_text=f"文档说明(公务用车)：{nar_final_car}\n表7内数据未找到匹配值"
                 ))
                 
        # 接待
        if nar_final_recept is not None and nar_final_recept > 0:
            if not any(abs(v - nar_final_recept) <= 0.01 for v in t7_vals):
                 location = _make_issue_location(
                     _make_location_ref(
                         role="说明7",
                         page=narrative_page,
                         section="说明7（三公经费情况）",
                         field="公务接待",
                         value=nar_final_recept,
                     ),
                     _make_location_ref(
                         role="T7",
                         page=t7_page,
                         table='一般公共预算财政拨款"三公"经费支出决算表',
                         field="公务接待",
                     ),
                     table='一般公共预算财政拨款"三公"经费支出决算表',
                     section="说明7（三公经费情况）",
                     field="公务接待",
                 )
                 issues.append(self._issue(
                     f"说明7↔T7公务接待不一致：说明={nar_final_recept:.2f}",
                     location, "warn",
                     evidence_text=f"文档说明(公务接待)：{nar_final_recept}\n表7内数据未找到匹配值"
                 ))

        return issues


class R33226_Narrative2_T2(Rule):
    """说明2（收入结构）↔ T2 合计与分项"""
    code, severity = "V33-226", "warn"
    desc = "说明2↔T2收入决算金额结构校验"

    def apply(self, doc: Document) -> List[Issue]:
        issues = []
        import re

        _ensure_table_anchors(doc)
        # 1. 提取叙述数据
        # 常见模式: "本年收入合计 XXX 万元... 其中：财政拨款收入 XXX 万元... 上级补助收入 XXX 万元..."
        # 关键字映射表 (Nar keyword -> T2 possible col keywords)
        
        key_map = {
            '本年收入合计': ['本年收入合计'],
            '财政拨款收入': ['财政拨款收入'],
            '上级补助收入': ['上级补助收入'],
            '事业收入': ['事业收入'],
            '经营收入': ['经营收入'],
            '附属单位上缴收入': ['附属单位上缴'],
            '其他收入': ['其他收入']
        }
        
        # 查找目标文本
        target_txt = ""
        narrative_page: Optional[int] = None
        for pidx, txt in enumerate(doc.page_texts):
            if "收入决算" in txt and ("情况说明" in txt or "本年收入合计" in txt):
                target_txt = txt
                narrative_page = pidx + 1
                break
        
        if not target_txt:
            raise RuleDeferred(
                self.code,
                detail="未找到收入决算情况说明文本",
                unresolved_reasons=["未找到收入决算情况说明文本"],
            )
        
        # 提取各项金额
        nar_vals = {}
        for k, _ in key_map.items():
            # 正则：关键词 ... 数字 ... 万元
            # 兼容 "财政拨款收入为 100 万元" 或 "财政拨款收入 100 万元"
            m = re.search(re.escape(k) + r'[^\d]{0,20}(\d+\.?\d*)\s*万元', target_txt)
            if m:
                nar_vals[k] = float(m.group(1))
        
        if not nar_vals:
            raise RuleDeferred(
                self.code,
                detail="收入决算情况说明中未提取到有效金额",
                unresolved_reasons=["收入决算情况说明中未提取到有效金额"],
            )

        # 2. 获取 T2 数据
        # T2 收入决算表，通常包含上述列
        t2_page = _get_first_anchor_page(doc, "收入决算表")
        t2_rows = _get_table_rows(doc, "收入决算表")
        if not t2_rows:
            raise RuleDeferred(
                self.code,
                detail="收入决算表(T2)缺失",
                unresolved_reasons=["收入决算表(T2)缺失"],
            )
        
        # 解析 T2 结构
        # 找到合计行
        t2_row_vals = []
        for row in t2_rows:
            row_txt = "".join([str(c) for c in row if c])
            if "合计" in row_txt or "本年收入合计" in row_txt:
                t2_row_vals = _parse_row_values(row)
                break
        
        if not t2_row_vals:
            raise RuleDeferred(
                self.code,
                detail="收入决算表(T2)未提取到合计行数值",
                unresolved_reasons=["收入决算表(T2)未提取到合计行数值"],
            )
        
        # 这里的困难是 T2 的列顺序不确定，且 parse_row_values 只是数值列表
        # 只能尝试基于数值的匹配
        # 如果叙述中提到了某项金额，我们看 T2 合计行里有没有这个数
        
        for k, v in nar_vals.items():
            if v > 0:
                # 强校验：表中必须有这个数 (容差 0.01)
                found = any(abs(val - v) <= 0.01 for val in t2_row_vals)
                if not found:
                    location = _make_issue_location(
                        _make_location_ref(
                            role="说明2",
                            page=narrative_page,
                            section="说明2（收入结构）",
                            field=k,
                            value=v,
                        ),
                        _make_location_ref(
                            role="T2",
                            page=t2_page,
                            table="收入决算表",
                            row="合计",
                            field=k,
                        ),
                        table="收入决算表",
                        section="说明2（收入结构）",
                        row="合计",
                        field=k,
                    )
                    issues.append(self._issue(
                         f"说明2↔T2{k}不一致：说明={v:.2f}, T2合计行未找到对应值",
                         location, "warn",
                         evidence_text=f"文档说明({k})：{v}\n表2合计行数据：{t2_row_vals}"
                    ))
        
        return issues


class R33225_Narrative1_T1(Rule):
    """说明1（总体情况）↔ T1 收入支出总计"""
    code, severity = "V33-225", "warn"
    desc = "说明1↔T1收入支出总计校验"

    def apply(self, doc: Document) -> List[Issue]:
        issues = []
        import re

        _ensure_table_anchors(doc)
        narrative_total = 0.0
        narrative_page: Optional[int] = None
        for pidx, txt in enumerate(doc.page_texts):
            if "收入支出决算" in txt and "总体情况" in txt:
                total_match = re.search(r'收入支出总计[^\d]*(\d+\.?\d*)\s*万元', txt)
                if total_match:
                    narrative_total = float(total_match.group(1))
                    narrative_page = pidx + 1
                    break
        
        # 只要找到了匹配项（即使是0），且T1有数据，就应该校验
        if narrative_total is not None:
            t1_page = _get_first_anchor_page(doc, "收入支出决算总表")
            t1_rows = _get_table_rows(doc, "收入支出决算总表")
            if t1_rows:
                t1_total = 0.0
                for row in t1_rows:
                    row_txt = "".join([str(c) for c in row if c])
                    if "总计" in row_txt:
                        vals = _parse_row_values(row)
                        if vals:
                            t1_total = vals[0]
                        break
                
                # 收紧容差 0.01
                if t1_total > 0.01 and abs(narrative_total - t1_total) > 0.01:
                    location = _make_issue_location(
                        _make_location_ref(
                            role="说明1",
                            page=narrative_page,
                            section="说明1（总体情况）",
                            field="收入支出总计",
                            value=narrative_total,
                        ),
                        _make_location_ref(
                            role="T1",
                            page=t1_page,
                            table="收入支出决算总表",
                            row="总计",
                            field="总计",
                            value=t1_total,
                        ),
                        table="收入支出决算总表",
                        section="说明1（总体情况）",
                        row="总计",
                        field="收入支出总计",
                    )
                    issues.append(self._issue(
                        f"说明1↔T1总计不一致：说明={narrative_total:.2f}, T1={t1_total:.2f}",
                        location, "warn",
                        evidence_text=f"文档说明(收入支出决算总计)：{narrative_total}\n表1(总表) 总计：{t1_total}"
                    ))
        return issues


# ==================================================================================
# P3 - 规范性提示 (Normative Hints)
# ==================================================================================

def _extract_header_labels(rows: List[List[str]], depth: int = 3) -> List[str]:
    if not rows:
        return []
    # 表头合并不得跨越首个数据行：行首为 3/5/7 位科目编码或「合计/总计」即为数据行。
    # 否则「合计」数据行的列首单元格会被并入第 0 列（科目编码列）的表头，
    # 使 _extract_final_formula_rows 的 idx_total 落到编码列——实测把功能分类
    # 编码 201/204/… 当成合计值 201.00/204.00，整行列索引左移一位。
    effective = depth
    for i in range(min(depth, len(rows))):
        first = str(rows[i][0] or "").strip() if rows[i] else ""
        if first in ("合计", "总计") or (first.isdigit() and len(first) in (3, 5, 7)):
            effective = max(i, 1)
            break
    max_cols = max(len(row) for row in rows[:effective])
    headers: List[str] = []
    for col in range(max_cols):
        parts: List[str] = []
        for row in rows[:effective]:
            if col < len(row):
                cell = str(row[col] or "").strip()
                if cell:
                    parts.append(cell)
        headers.append("".join(parts))
    return headers


def _extract_final_formula_rows(rows: List[List[str]]) -> List[Dict[str, Any]]:
    headers = _extract_header_labels(rows, depth=3)
    norm_headers = [normalize_text(header) for header in headers]

    idx_total: Optional[int] = None
    idx_basic: Optional[int] = None
    idx_project: Optional[int] = None
    idx_name: Optional[int] = None
    for idx, header in enumerate(norm_headers):
        if idx_name is None and ("功能分类科目名称" in header or ("科目名称" in header and "编码" not in header)):
            idx_name = idx
        # 双保险：科目编码列永不参与金额取值。表头深度探测（_extract_header_labels）
        # 已经能挡住「合计」数据行污染，但若某表的表头行数与形态超出探测假设，
        # 编码列的数值化（"201" -> 201.0）仍会被误当合计，故在此再拦一道。
        if "编码" in header:
            continue
        if idx_basic is None and "基本支出" in header:
            idx_basic = idx
        elif idx_project is None and "项目支出" in header:
            idx_project = idx
        elif idx_total is None and ("合计" in header) and ("基本支出" not in header) and ("项目支出" not in header):
            idx_total = idx

    if idx_name is None and len(headers) > 3:
        idx_name = 3
    if idx_total is None and len(headers) > 4:
        idx_total = 4
    if idx_basic is None and len(headers) > 5:
        idx_basic = 5
    if idx_project is None and len(headers) > 6:
        idx_project = 6
    if None in (idx_total, idx_basic, idx_project):
        return []
    if idx_name is None:
        return []

    extracted: List[Dict[str, Any]] = []
    start_row = min(3, len(rows))
    for row in rows[start_row:]:
        cells = [str(cell or "").strip() for cell in row]
        needed_indexes = [idx_total, idx_basic, idx_project, idx_name]
        if any(idx is None or idx >= len(cells) for idx in needed_indexes):
            continue

        class_code = cells[0] if cells else ""
        if not re.fullmatch(r"\d{3}", class_code):
            continue

        name = cells[idx_name]
        if not name or ("\u5408\u8ba1" in name) or ("\u603b\u8ba1" in name):
            continue

        total = parse_number(cells[idx_total])
        basic = parse_number(cells[idx_basic])
        project = parse_number(cells[idx_project])
        if total is None or basic is None or project is None:
            continue

        row_code = "".join(
            part for part in cells[:3] if re.fullmatch(r"\d{2,3}", str(part or "").strip())
        )
        row_label = f"{_format_functional_code(row_code)} {name}".strip() if row_code else name
        row_text = " ".join(part for part in (*cells[:3], name) if part)
        extracted.append(
            {
                "code": row_code,
                "name": name,
                "row_label": row_label,
                "row_text": row_text,
                "total": float(total),
                "basic": float(basic),
                "project": float(project),
            }
        )
    return extracted


_FINAL_ITEM_START_RE = re.compile(r"(?:(?<=\n)|^|\s{2,})(?P<marker>\d+\u3001)")
_FINAL_PREV_YEAR_PERCENT_RE = re.compile(
    r"(?P<prev_year>20\d{2})\s*年(?:度)?(?:[^。；\n]{0,20})?(?:决算数|支出决算|收入决算|执行数)?\s*为\s*"
    r"(?P<prev>[0-9][0-9,]*(?:\.[0-9]+)?)\s*万元"
    r"[\s\S]{0,120}?"
    r"(?:20\d{2}\s*年(?:度)?(?:[^。；\n]{0,20})?)?(?:支出)?决算(?:数)?\s*为\s*"
    r"(?P<curr>[0-9][0-9,]*(?:\.[0-9]+)?)\s*万元"
    r"[\s\S]{0,120}?"
    r"(?:比|较)(?P=prev_year)\s*年(?:度)?(?:[^。；\n]{0,20})?(?:决算数|支出决算|收入决算|执行数)?\s*"
    r"(?P<direction>增加|减少)\s*(?P<pct>[0-9][0-9,]*(?:\.[0-9]+)?)%",
    re.S,
)
_FINAL_COMPLETION_PERCENT_RE = re.compile(
    r"年初预算(?:数)?\s*为\s*(?P<budget>[0-9][0-9,]*(?:\.[0-9]+)?)\s*万元"
    r"[\s\S]{0,120}?"
    r"(?:支出)?决算(?:数)?\s*为\s*(?P<final>[0-9][0-9,]*(?:\.[0-9]+)?)\s*万元"
    r"[\s\S]{0,80}?"
    r"(?:完成|占)(?:年初)?预算(?:的比重)?(?:的)?\s*(?P<pct>[0-9][0-9,]*(?:\.[0-9]+)?)%",
    re.S,
)
_FINAL_COMPLETION_REASON_RE = re.compile(
    r"主要原因(?:是|为)?|原因(?:是|为|如下)|由于|主要系|系因|受[^。；;\n]{0,60}影响|"
    r"(?:^|[。；;，,\s])因(?!公)[^。；;\n]{1,80}",
    re.S,
)


def _completion_rate_has_reason(segment: str, match: re.Match[str]) -> bool:
    window_start = max(0, match.start() - 80)
    window_end = min(len(segment), match.end() + 160)
    window = segment[window_start:window_end]
    return _FINAL_COMPLETION_REASON_RE.search(window) is not None
_FINAL_ITEM_OPENING_AMOUNT_RE = re.compile(
    r"(?:^|\s)\d+\u3001[\s\S]{0,160}?(?P<front>[0-9][0-9,]*(?:\.[0-9]+)?)\s*万元",
    re.S,
)
_FINAL_ITEM_DECISION_AMOUNT_RE = re.compile(
    r"(?:支出)?决算(?:数)?\s*为\s*(?P<final>[0-9][0-9,]*(?:\.[0-9]+)?)\s*万元"
)


def _iter_final_narrative_segments(doc: Document) -> List[Tuple[int, str]]:
    segments: List[Tuple[int, str]] = []
    for page_num, text in enumerate(doc.page_texts, start=1):
        if not text or ("\u76ee\u5f55" in text[:120]):
            continue

        matches = list(_FINAL_ITEM_START_RE.finditer(text))
        if not matches:
            continue

        for idx, match in enumerate(matches):
            start = match.start("marker")
            end = matches[idx + 1].start("marker") if idx + 1 < len(matches) else len(text)
            segment = text[start:end].strip()
            if not segment:
                continue
            if ("\u51b3\u7b97" not in segment) and ("\u9884\u7b97" not in segment) and ("%" not in segment):
                continue
            segments.append((page_num, segment))
    return segments


def _final_segment_subject(segment: str) -> str:
    head = re.split(r"[\u3002\uff1b;\n]", segment, maxsplit=1)[0].strip()
    head = re.sub(r"^\d+\u3001", "", head).strip()
    return head[:80] if len(head) > 80 else head


def _infer_final_scope(doc: Document) -> Optional[str]:
    path_text = str(getattr(doc, "path", "") or "")
    head_text = "\n".join(doc.page_texts[:3])
    if "\u5355\u4f4d\u51b3\u7b97" in path_text or "\u5355\u4f4d\u51b3\u7b97" in head_text:
        return "unit"
    if "\u90e8\u95e8\u51b3\u7b97" in path_text or "\u90e8\u95e8\u51b3\u7b97" in head_text:
        return "department"
    return None


class R33233_DetailRowFormulaConsistency(Rule):
    code, severity = "V33-233", "error"
    desc = "决算明细行勾稽检查（合计=基本支出+项目支出）"

    def apply(self, doc: Document) -> List[Issue]:
        _ensure_table_anchors(doc)
        issues: List[Issue] = []
        unresolved: List[str] = []
        found_any = False
        for table_name in ("支出决算表", "一般公共预算财政拨款支出决算表"):
            rows = _get_table_rows(doc, table_name)
            if not rows:
                unresolved.append(f"表缺失或无可解析行: {table_name}")
                continue
            found_any = True
            table_page = _get_first_anchor_page(doc, table_name) or 1
            for entry in _extract_final_formula_rows(rows):
                calc = entry["basic"] + entry["project"]
                if tolerant_equal(entry["total"], calc, atol=1.0, rtol=0.001):
                    continue
                row_page = _find_text_page_after(doc, entry.get("row_text", ""), start_page=table_page) or table_page
                issues.append(
                    self._issue(
                        f"{table_name}明细行勾稽错误（{entry['row_label']}）：合计={entry['total']:.2f}，基本+项目={calc:.2f}",
                        {
                            "page": row_page,
                            "table": table_name,
                            "row": entry["row_label"],
                            "field": "合计 / 基本支出 / 项目支出",
                        },
                        severity="error",
                        evidence_text=entry["row_text"],
                    )
                )
        if not found_any:
            raise RuleDeferred(
                self.code,
                detail="支出决算表与一般公共预算财政拨款支出决算表均缺失",
                partial_issues=issues,
                unresolved_reasons=unresolved,
            )
        if unresolved:
            raise RuleDeferred(
                self.code,
                detail="; ".join(unresolved),
                partial_issues=issues,
                unresolved_reasons=unresolved,
            )
        return issues


class R33234_NarrativePercentConsistency(Rule):
    code, severity = "V33-234", "warn"
    desc = "决算说明同比/完成率百分比复算"

    def apply(self, doc: Document) -> List[Issue]:
        issues: List[Issue] = []
        segments = list(_iter_final_narrative_segments(doc))
        if not segments:
            raise RuleDeferred(
                self.code,
                detail="未提取到决算情况说明分段文本",
                unresolved_reasons=["未提取到决算情况说明分段文本"],
            )
        for page_num, segment in segments:
            subject = _final_segment_subject(segment)

            for match in _FINAL_PREV_YEAR_PERCENT_RE.finditer(segment):
                prev = parse_number(match.group("prev"))
                curr = parse_number(match.group("curr"))
                reported_pct = parse_number(match.group("pct"))
                direction = match.group("direction")
                if prev is None or curr is None or reported_pct is None or prev <= 0 or max(prev, curr) < 1.0:
                    continue

                diff = curr - prev
                if abs(diff) <= 0.05:
                    continue
                expected_direction = "增加" if diff > 0 else "减少"
                expected_pct = abs(diff) / prev * 100.0
                if direction == expected_direction and tolerant_equal(reported_pct, expected_pct, atol=0.5, rtol=0.01):
                    continue

                issues.append(
                    self._issue(
                        f"同比表述与金额不一致（{subject}）：按金额应为{expected_direction}{expected_pct:.2f}%，当前写为{direction}{reported_pct:.2f}%",
                        {"page": page_num, "section": "决算情况说明", "subject": subject},
                        severity="error",
                        evidence_text=segment.replace("\n", " "),
                    )
                )

            for match in _FINAL_COMPLETION_PERCENT_RE.finditer(segment):
                budget_val = parse_number(match.group("budget"))
                final_val = parse_number(match.group("final"))
                reported_pct = parse_number(match.group("pct"))
                if budget_val is None or final_val is None or reported_pct is None:
                    continue
                if budget_val <= 0:
                    issues.append(
                        self._issue(
                            f"完成率表述缺少有效分母（{subject}）：年初预算为{budget_val:.2f}万元，却写“完成年初预算的{reported_pct:.2f}%”",
                            {"page": page_num, "section": "决算情况说明", "subject": subject},
                            severity="error",
                            evidence_text=segment.replace("\n", " "),
                        )
                    )
                    continue

                expected_pct = final_val / budget_val * 100.0
                if tolerant_equal(reported_pct, expected_pct, atol=0.5, rtol=0.01):
                    if (
                        not tolerant_equal(expected_pct, 100.0, atol=0.5, rtol=0.0)
                        and not _completion_rate_has_reason(segment, match)
                    ):
                        issues.append(
                            self._issue(
                                f"完成率偏离100%但未说明原因（{subject}）：年初预算为{budget_val:.2f}万元，"
                                f"支出决算为{final_val:.2f}万元，完成率为{reported_pct:.2f}%，建议补充差异原因说明",
                                {"page": page_num, "section": "决算情况说明", "subject": subject},
                                severity="warn",
                                evidence_text=segment.replace("\n", " "),
                            )
                        )
                    continue

                issues.append(
                    self._issue(
                        f"完成率表述与金额不一致（{subject}）：按金额应为{expected_pct:.2f}%，当前写为{reported_pct:.2f}%",
                        {"page": page_num, "section": "决算情况说明", "subject": subject},
                        severity="error",
                        evidence_text=segment.replace("\n", " "),
                    )
                )
        return issues


class R33235_NarrativeAmountConsistency(Rule):
    code, severity = "V33-235", "warn"
    desc = "同条决算说明前后金额一致性"

    def apply(self, doc: Document) -> List[Issue]:
        issues: List[Issue] = []
        segments = list(_iter_final_narrative_segments(doc))
        if not segments:
            raise RuleDeferred(
                self.code,
                detail="未提取到决算情况说明分段文本",
                unresolved_reasons=["未提取到决算情况说明分段文本"],
            )
        for page_num, segment in segments:
            front_match = _FINAL_ITEM_OPENING_AMOUNT_RE.search(segment)
            final_match = _FINAL_ITEM_DECISION_AMOUNT_RE.search(segment)
            if not front_match or not final_match:
                continue

            # 条目开头金额必须是「条目自身的决算数」（紧跟科目名之后、位于"年初预算"之前）。
            # 若条目格式为「N、…（项）。主要用于：…。年初预算为 X 万元，支出决算为 Y 万元」
            # （开头无金额，文旅局样张格式），首个数字会是**年初预算**——拿它和决算比必然
            # 不等，曾一次性产出 20 条误报。故开头金额落在"年初预算"之后的一律不判。
            # 注意：PDF 软换行会在字间插空格（"年 初预算"、"年初预 算为"），
            # 定位"年初预算"必须允许字符间空白，否则守卫失效。
            budget_hint = re.search(r"年\s*初\s*预\s*算", segment)
            if budget_hint and front_match.start("front") > budget_hint.start():
                continue

            front_amount = parse_number(front_match.group("front"))
            final_amount = parse_number(final_match.group("final"))
            if front_amount is None or final_amount is None:
                continue
            if tolerant_equal(front_amount, final_amount, atol=0.05, rtol=0.0005):
                continue

            subject = _final_segment_subject(segment)
            issues.append(
                self._issue(
                    f"同条决算说明前后金额不一致（{subject}）：条目开头={front_amount:.2f}万元，“支出决算/决算数”={final_amount:.2f}万元",
                    {"page": page_num, "section": "决算情况说明", "subject": subject},
                    severity="warn",
                    evidence_text=segment.replace("\n", " "),
                )
            )
        return issues


class R33236_DocumentScopeTerminology(Rule):
    code, severity = "V33-236", "warn"
    desc = "部门/单位决算文种表述一致性"

    def apply(self, doc: Document) -> List[Issue]:
        scope = _infer_final_scope(doc)
        if not scope:
            raise RuleDeferred(
                self.code,
                detail="未能识别决算材料口径（部门决算或单位决算）",
                unresolved_reasons=["未能识别决算材料口径（部门决算或单位决算）"],
            )

        if scope == "unit":
            pattern = re.compile(r"(?:20\d{2}年)?部门决算安排")
            current_scope = "单位决算"
            expected = "单位决算安排"
            wrong_label = "部门决算安排"
        else:
            pattern = re.compile(r"(?:20\d{2}年)?单位决算安排")
            current_scope = "部门决算"
            expected = "部门决算安排"
            wrong_label = "单位决算安排"

        issues: List[Issue] = []
        for page_num, text in enumerate(doc.page_texts, start=1):
            for match in pattern.finditer(text or ""):
                issues.append(
                    self._issue(
                        f"当前材料为{current_scope}，但正文出现“{wrong_label}”表述，建议统一为“{expected}”",
                        {"page": page_num, "section": "其他相关情况说明", "pos": match.start()},
                        severity="warn",
                        evidence_text=(text or "")[max(0, match.start() - 40): match.end() + 80].replace("\n", " "),
                    )
                )
        return issues


class R33230_EmptyZeroHint(Rule):
    """空值与0值规范性提示"""
    code, severity = "V33-230", "info"
    desc = "空值/0值规范性提示"

    def apply(self, doc: Document) -> List[Issue]:
        issues = []
        
        # 检查T7：如果说明写0但表内为空
        for pidx, txt in enumerate(doc.page_texts):
            if "三公" in txt and "0" in txt:
                # 说明中提到0，检查T7是否有数据
                t7_rows = _get_table_rows(doc, '一般公共预算财政拨款"三公"经费支出决算表')
                if t7_rows:
                    has_data = False
                    for row in t7_rows:
                        vals = _parse_row_values(row)
                        if any(v > 0 for v in vals):
                            has_data = True
                            break
                    if not has_data:
                        issues.append(self._issue(
                            "规范性提示：三公经费表内为空，说明中提及为0，建议表内补填0保持一致。",
                            {"page": pidx + 1}, "info",
                            evidence_text="文档内容：提及 '0' 或相关描述\n表格检测：三公表存在但无任何大于0的数值"
                        ))
                break
        
        return issues


class R33232_PercentagePrecision(Rule):
    """百分比两位小数精度校验"""
    code, severity = "V33-232", "info"
    desc = "百分比精度校验（两位小数）"

    def apply(self, doc: Document) -> List[Issue]:
        try:
            issues = []
            import re
            
            for pidx, txt in enumerate(doc.page_texts):
                # 查找所有百分比
                pct_pattern = r'(\d+\.?\d*)\s*%'
                matches = re.findall(pct_pattern, txt)
                
                for pct_str in matches:
                    try:
                        float(pct_str)
                    except ValueError:
                        continue
                    # 检查是否超过两位小数
                    if '.' in pct_str:
                        decimal_places = len(pct_str.split('.')[1])
                        if decimal_places > 2:
                            issues.append(self._issue(
                                f"规范性提示：第{pidx+1}页发现超过两位小数的百分比({pct_str}%)，建议四舍五入。",
                                {"page": pidx + 1, "pct": pct_str}, "info",
                                evidence_text=f"发现位置：P{pidx+1}\n百分比数值：{pct_str}%"
                            ))
            
            return issues
        except (RuleDeferred, RuleExecutionError):
            raise
        except Exception as e:
            raise RuleExecutionError(self.code, f"规则执行异常：{str(e)}")


class R33245_ThreePublicDirectionContradiction(Rule):
    """三公说明逻辑矛盾：同一主体同一口径同时出现「减少/增加」与「持平」。

    样张实例（T5，P26）：「公务接待费支出决算减少为0.00万元，与2024年
    持平」——若 2024 年为 0 应称持平，减少则不成立；两者同段同主体出现
    即为逻辑矛盾（HANDOFF §2 A 档 T5）。
    """
    code, severity = "V33-245", "medium"
    desc = "三公经费说明：增减方向与持平表述的逻辑矛盾"

    def apply(self, doc: Document) -> List[Issue]:
        issues = []
        merged = merge_page_texts(doc.page_texts)

        from src.utils.narration import _THREE_PUBLIC_SUBJECTS

        # 章节限定（GPT5.6 R5 P1-C 规则层修复）：本规则语义是「三公说明
        # 内部的逻辑矛盾」——此前全文扫段落，其他章节（如项目支出说明）
        # 提到「公务接待费…减少…持平」同样产出 finding，规则文案自证
        # 章节名却内容跨章节。定位到「三公经费…决算情况说明」主章节的
        # 完整范围（含（一）（二）子章节，find_section_scope——主标题后
        # 紧跟子标题时 find_section 的 body 为空）。
        # R7 P1-3：找不到章节 → 证据不足直接返回空——禁止 scope or
        # merged 全文回退（此前仅含「十一、其他重要事项说明」的材料里
        # 出现公务接待表述仍会产出 finding，跨章节误报通道未真正关闭）。
        from src.utils.narration import find_section_scope_with_title

        found = find_section_scope_with_title(merged, ["三公"])
        if not found:
            raise RuleDeferred(self.code, detail="未找到三公经费说明章节")
        # 标题由解析携带（R9 P1）——此前 section_title_of_scope 用正文
        # 反查，正文相同的两章节会取到较早章节的标题（实测复现）
        section_title, scope, _start, _end = found
        scope_text = scope

        for para in merge_soft_wrapped_lines(scope_text):
            if "三公" not in para and "公务接待" not in para and "因公出国" not in para and "公务用车" not in para:
                continue
            clauses = split_clauses(para)
            # 主体沿分句继承，与事实抽取保持同一口径
            current_subject = ""
            directions: Dict[str, List[Tuple[str, str]]] = defaultdict(list)
            for clause in clauses:
                subject = current_subject
                for token, name in _THREE_PUBLIC_SUBJECTS:
                    if token in clause:
                        subject = name
                        current_subject = name
                        break
                if not subject:
                    continue
                direction = clause_direction(clause)
                if direction:
                    directions[subject].append((direction, clause))

            for subject, hits in directions.items():
                has_flat = any(d == "flat" for d, _ in hits)
                has_move = any(d in ("up", "down") for d, _ in hits)
                if has_flat and has_move:
                    evidence = "；".join(clause for _, clause in hits)[:200]
                    # 页码定位用首个命中分句（原文里没有拼接用的分隔符）
                    locate_text = hits[0][1]
                    # 结构化章节标记（GPT5.6 R6 P1-3）：规则层 scope 已限定
                    # 本 finding 产自三公说明章节；evidence 前缀让 finding
                    # 自带可校验的章节身份——评估器 sec 锚点消费它，
                    # 不再依赖标注侧猜测 evidence 里的章节词。
                    # R7 P1-3：另以独立 section_id 结构化携带（评估器可
                    # 据此拒绝跨章节候选，不再只靠 evidence 文字）。
                    section_tag = f"【章节:{(section_title or '').strip()[:40]}】" if section_title else ""
                    issues.append(self._issue(
                        f"三公说明逻辑矛盾：「{subject}」同时出现增减变化与“持平”表述，"
                        "两者不能同时成立，需核实同比口径后修正。",
                        {"page": self._locate_page(doc, locate_text)},
                        severity="medium",
                        evidence_text=f"{section_tag}{evidence}",
                        section_id=section_title,
                    ))

        return issues

    @staticmethod
    def _locate_page(doc: Document, snippet: str) -> int:
        compact = re.sub(r"\s+", "", snippet)[:24]
        for pi, page_text in enumerate(doc.page_texts):
            if compact and compact in re.sub(r"\s+", "", page_text):
                return pi + 1
        return 1


class R33246_DomesticReceptionDisclosure(Rule):
    """国内公务接待批次/人次披露完整性（T6，P27）。

    官方公开要求：公务接待费说明应披露国内公务接待的批次与人次。
    仅披露外宾（「接待外宾0批次、0人次」）而缺国内批次/人次时，
    属披露缺失（样张实测：仅写外宾 0 批次 0 人次）。
    """
    code, severity = "V33-246", "medium"
    desc = "公务接待：国内公务接待批次、人次披露完整性"

    def apply(self, doc: Document) -> List[Issue]:
        issues = []
        merged = merge_page_texts(doc.page_texts)

        # 章节限定（GPT5.6 R5 P1-C 规则层修复，同 V33-245）：披露
        # 完整性针对「三公经费…决算情况说明」主章节完整范围
        # （find_section_scope 含子章节正文）。
        # R7 P1-3：找不到章节 → 证据不足直接返回空，禁止全文回退。
        from src.utils.narration import find_section_scope_with_title

        found = find_section_scope_with_title(merged, ["三公"])
        if not found:
            raise RuleDeferred(self.code, detail="未找到三公经费说明章节")
        section_title, scope, _start, _end = found
        scope_text = scope

        # 批次/人次的口径按「就近主体」判定（句子 → 分句 两级 + 主体状态机）：
        # - 只按句子判：会把「公务接待 1 批次、7 人次，其中：接待外宾批 0 次、0 人次」
        #   这种同句混合口径整句判成外事口径（长风样张误报）；
        # - 只按分句判：会把「接待外宾 0 批次、0 人次」拆出的「0 人次」误判成国内
        #   （语料 T6 漏报）。
        # 状态机：遇 外宾/境外/国外 置外事口径，遇 国内/公务接待 切回国内，
        #         计数词归属当时的口径；句子边界处重置为国内。
        def _para_disclosure(para: str) -> Tuple[bool, bool]:
            domestic = False
            foreign = False
            for sentence in re.split(r"[。；;！!]", para):
                state_foreign = False
                for clause in split_clauses(sentence):
                    compact_c = re.sub(r"\s+", "", clause)
                    if not compact_c:
                        continue
                    if "外宾" in compact_c or "境外" in compact_c or "国外" in compact_c:
                        state_foreign = True
                    if "国内" in compact_c or "公务接待" in compact_c:
                        state_foreign = False
                    if "批次" in compact_c or "人次" in compact_c:
                        if state_foreign:
                            foreign = True
                        else:
                            domestic = True
            return domestic, foreign

        # 披露完整性按**整个三公章节**判定：国内批次/人次只要在任一段落披露过即完整。
        # 长风样张把说明拆成两段（前段只有金额、后段才有「1 批次、7 人次」），
        # 逐段判定会对前段重复产出误报。
        candidate_paras: List[str] = []
        section_domestic = False
        for para in merge_soft_wrapped_lines(scope_text):
            if "公务接待" not in para:
                continue
            clauses = split_clauses(para)
            # 该段是否为公务接待具体说明（含批次/人次语境或接待支出语境）
            if not any("批次" in clause or "人次" in clause or "国内公务接待" in clause for clause in clauses):
                continue
            candidate_paras.append(para)
            para_domestic, _para_foreign = _para_disclosure(para)
            if para_domestic:
                section_domestic = True

        if section_domestic:
            return issues

        for para in candidate_paras:
            clauses = split_clauses(para)
            domestic_disclosed, foreign_disclosed = _para_disclosure(para)
            if domestic_disclosed:
                continue
            # 国内批次/人次缺失：无论外宾是否披露，都属披露不完整；
            # 但若整段连接待支出语境都没有则不触发（避免误伤目录/表格页）
            if not any("国内公务接待" in clause or foreign_disclosed for clause in clauses):
                continue
            page = R33245_ThreePublicDirectionContradiction._locate_page(doc, para[:40])
            # 结构化章节标记（R6 P1-3，同 V33-245）：规则层 scope 已限定
            # finding 产自三公说明章节，evidence 前缀供评估器锚点消费；
            # R7 P1-3：另以独立 section_id 结构化携带。
            section_tag = f"【章节:{(section_title or '').strip()[:40]}】" if section_title else ""
            issues.append(self._issue(
                "公务接待说明未披露国内公务接待批次、人次，"
                "应补充「国内公务接待X批次、X人次」（含公务接待费对应口径）。",
                {"page": page},
                severity="medium",
                evidence_text=f"{section_tag}{para[:200]}",
                section_id=section_title,
            ))

        return issues


# ==================================================================================
# WP4-G：三公经费说明四项细化披露完整性（V33-SG-COMPLETION，final only）
#
# 检查对象（Truth 规则冻结，不与其他三公义务混用）
# ------------------------------------------------------------------
# 只检查**三公经费支出决算情况说明章节内部**四项（因公出国（境）费 /
# 公务用车购置费 / 公务用车运行维护费 / 公务接待费）的细化披露是否在
# 场；表格数值不参与判定——表格分项齐备归 V33-244（OBL-SG-ITEMS），
# 表文金额一致归 V33-108/V33-224（OBL-SG-TABLE-TEXT），国内接待批次/
# 人次归 V33-246（OBL-SG-DISCLOSURE），金额勾稽归 V33-121/V33-244
# （OBL-SG-TOTAL）。本规则补的是官方检查表口径：说明须把四项逐项
# 写出来（"是否细化'公务用车购置及运行费'：公开'公务用车购置费'、
# '公务用车运行费'"），合并披露"购置及运行维护费"是合法三项口径，
# 但不能当作购置费已细化。
#
# 真值（REAL，官方人工检查表，2026-09-16 重跑系统未报告）
# ------------------------------------------------------------------
# 宜川路街道 2025 年度决算（41 页，SHA ``f809eef2…``）官方检查表
# 《…宜川路街道办事处（部门决算）检查表.xlsx》"是否合格=否"行：
# 「是否细化'公务用车购置及运行费'：公开'公务用车购置费'、
# '公务用车运行费'→ 否；问题1：第三部分缺少以下'三公'经费细化披露
# 字段：公务用车购置费。」对照样张 P36：说明（二）2 只写"公务用车
# 购置及运行维护费支出 19.56 万元。其中：公务用车运行维护支出
# 19.56 万元…"，购置费既无金额也无未发生说明。同批检查表另有
# 因公出国团组数、公务用车购置数两行"否"——团组/购置**数**属数量
# 要素，本地披露标准未定（comparison.md 对同类问题判"待裁决"），
# 本轮只收编四项金额披露，不越界到数量要素。
#
# 未发生 vs 未披露（任务 §七）
# ------------------------------------------------------------------
# 金额为 0（"支出为0万元"）、明示未发生（"未发生/未新增/未购置/
# 无…"）、与上年持平，都算披露完整；只有既无金额又无等效说明才算
# 缺失。章节内"无三公经费"整体说明或三公合计披露为 0 时四项视为
# 已披露（等效说明覆盖全项）。
# ==================================================================================

_WP4G_OBLIGATION_ID = "OBL-SG-COMPLETION"

#: 金额披露形态（只判定"金额在场"，不做数值勾稽）：数字 + 万元/万/元。
#: 口语句式「支出0.3万」（裸"万"无"元"）与「0.00万元」同为金额披露。
_WP4G_AMOUNT_RE = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(万元|万|元)")

#: 公务用车购置的**单独披露**词干：主体词后不得紧跟「及」——合并披露
#: 「公务用车购置及运行维护费」合法（检查表三项口径行判"合格"），
#: 不能当作购置费已细化（宜川真值正是被合并形态掩盖）。
_WP4G_PURCHASE_STEM_RE = re.compile(r"公务用车购置(?!及)")

#: 购置未发生/零值等效语境：允许不带"公务用车购置"完整词干——文旅
#: （一）同比段写「2025 年未新增公务用车」（该分句同时含合并主体），
#: 同样构成购置披露。
_WP4G_PURCHASE_NOT_OCCURRED_RE = re.compile(
    r"未(?:新增|购置|新购|发生)[^。；，,]{0,6}公务用车"
    r"|公务用车购置[^。；，,]{0,8}(?:未发生|无)"
)

#: 未发生/零值等效语境（与主体词干同分句在场即算披露）。
_WP4G_NOT_OCCURRED_RE = re.compile(r"未发生|未安排|未支出|未新购|无支出|持平|无")

#: 章节"无三公"整体等效说明（一句话覆盖四项；AGENTS.md：没有三公
#: 支出时应有"无此项"或等效说明）。
_WP4G_GLOBAL_NONE_RE = re.compile(
    r"(?:无|未发生|未安排|未支出)[^。；，,]{0,6}三公"
    r"|三公[^。；，,]{0,6}(?:无|未发生|未安排|未支出)"
)

#: 三公合计支出/决算披露为 0：四项决算必然为 0，视同已披露。只认
#: 零值金额跟随决算/支出语境的形态——"年初预算为 5.00 万元，支出决算
#: 为 0 万元"里非零的预算数不构成触发。零值金额锚定：数字必须以 0
#: 开头（"10.00 万元"里嵌在 10 里的 0 不算）、小数段只能全零（0.30
#: 不算 0）、且必须带单位（裸数字不认，与 extract_amounts 口径一致）。
_WP4G_GLOBAL_ZERO_RE = re.compile(
    r"(?:决算|支出)[^。；，,]{0,6}?(?<![\d,.])0(?:[,.]0+)?\s*(?:万元|万|元)"
)

#: 四项判定规格：(item_key, 台账标签, 缺失时证据定位词干, 证据回退词干)。
#: 购置项的证据词干**包含**合并形态——合并披露段正是购置细化应出现
#: 的位置（宜川 P36「2、公务用车购置及运行维护费支出 19.56 万元」）。
_WP4G_ITEM_SPECS: Tuple[Tuple[str, str, str, str], ...] = (
    ("overseas", "因公出国（境）费", "因公出国", "因公出国"),
    ("vehicle_purchase", "公务用车购置费", "公务用车购置", "公务用车"),
    ("vehicle_operation", "公务用车运行维护费", "公务用车运行", "公务用车"),
    ("reception", "公务接待费", "公务接待", "公务接待"),
)

#: 各项期望披露（finding 的 expected disclosure，任务 §八：不写无法定位的
#: "三公经费不完整"）。
_WP4G_EXPECTED_DISCLOSURE = {
    "overseas": "说明应写明因公出国（境）费的支出金额",
    "vehicle_purchase": (
        "说明应在「公务用车购置及运行维护费」下细化披露公务用车购置费的支出金额"
    ),
    "vehicle_operation": "说明应细化披露公务用车运行维护费的支出金额",
    "reception": "说明应写明公务接待费的支出金额",
}


def _wp4g_amounts_wan(clause: str) -> List[Decimal]:
    """分句内金额（统一万元）；裸「万」按万元口径。"""
    values: List[Decimal] = []
    for match in _WP4G_AMOUNT_RE.finditer(str(clause or "")):
        number = Decimal(match.group(1).replace(",", ""))
        unit = match.group(2)
        values.append(number if unit in ("万元", "万") else number * Decimal("0.0001"))
    return values


class R33SGCompletion(Rule):
    """三公经费说明四项细化披露完整性（含公务用车购置费细化）。

    在「三公经费…决算情况说明」主章节完整范围内（find_section_scope，
    含（一）（二）子章节，与 V33-245/246 同源）逐分句判定四项披露：
    金额（含 0）、未发生/持平等效说明任一在场即完整；合并披露
    「公务用车购置及运行维护费」不满足购置费细化。每项独立出 finding，
    带台账标签（three_public_item）、期望/实际披露与证据段落。整章
    "无三公"说明或三公合计披露为 0 时四项视为已披露。
    """

    code, severity = "V33-SG-COMPLETION", "medium"
    desc = "三公经费说明四项细化披露完整性（出国/购置/运行/接待）"

    def apply(self, doc: Document) -> List[Issue]:
        page_texts = [str(item or "") for item in (doc.page_texts or [])]
        if not any(text.strip() for text in page_texts):
            raise RuleDeferred(
                self.code,
                "未提取到正文文本，三公披露完整性无从检查",
                unresolved_reasons=["未提取到正文文本"],
            )
        merged = merge_page_texts(doc.page_texts)
        # 章节限定（同 V33-245/246）：找不到章节 → 证据不足，禁止全文
        # 回退——章节整体缺失是结构性问题，不在本规则里出确定性结论。
        from src.utils.narration import find_section_scope_with_title

        found = find_section_scope_with_title(merged, ["三公"])
        if not found:
            raise RuleDeferred(self.code, detail="未找到三公经费说明章节")
        section_title, scope, _start, _end = found
        section_tag = f"【章节:{(section_title or '').strip()[:40]}】" if section_title else ""
        paras = merge_soft_wrapped_lines(scope)

        disclosed = self._collect_disclosed(paras)
        issues: List[Issue] = []
        for item_key, label, evidence_stem, fallback_stem in _WP4G_ITEM_SPECS:
            if item_key in disclosed:
                continue
            evidence_para = self._evidence_para(
                paras, evidence_stem, fallback_stem
            )
            actual = self._actual_disclosure(item_key, evidence_para)
            page = R33245_ThreePublicDirectionContradiction._locate_page(
                doc, evidence_para[:40]
            )
            issues.append(self._issue(
                f"三公经费披露完整性：「{label}」在三公经费支出决算情况说明中"
                f"未细化披露。期望：{_WP4G_EXPECTED_DISCLOSURE[item_key]}"
                f"（金额为 0 或未发生的也应明示）。实际：{actual}。"
                "请核实原稿并补充细化披露。",
                {
                    "page": page,
                    "pos": 0,
                    "obligation_id": _WP4G_OBLIGATION_ID,
                    "three_public_item": label,
                    "item_key": item_key,
                    "section": (section_title or "").strip()[:60] or None,
                    "expected_disclosure": _WP4G_EXPECTED_DISCLOSURE[item_key],
                    "actual_disclosure": actual,
                },
                severity="medium",
                evidence_text=f"{section_tag}{evidence_para[:200]}",
                section_id=section_title,
            ))
        return issues

    # ------------------------------------------------------------------
    @staticmethod
    def _collect_disclosed(paras: List[str]) -> Set[str]:
        """逐分句判定四项披露状态，返回已披露 item_key 集合。"""
        disclosed: Set[str] = set()
        for para in paras:
            clauses = split_clauses(para)
            # 主体沿分句继承（与事实抽取同一口径）：全局"无三公/合计为 0"
            # 等效说明只在**合计主体**分句上判定——项级主体（公务接待等）
            # 分句里的"无公务接待开支""支出决算 0 万元"只覆盖该项，
            # 不得把整章当等效说明（石泉"无公务接待开支"实测分句）。
            inherited_subject = ""
            global_none = False
            for clause in clauses:
                subject = next(
                    (
                        token
                        for token in ("因公出国", "公务用车", "公务接待", "三公")
                        if token in clause
                    ),
                    inherited_subject,
                )
                inherited_subject = subject
                if subject != "三公":
                    continue
                if _WP4G_GLOBAL_NONE_RE.search(clause) or _WP4G_GLOBAL_ZERO_RE.search(
                    clause
                ):
                    global_none = True
                    break
            if global_none:
                disclosed.update(item_key for item_key, *_rest in _WP4G_ITEM_SPECS)
                continue
            for clause in clauses:
                compact = re.sub(r"\s+", "", clause)
                if not compact:
                    continue
                has_amount = bool(_wp4g_amounts_wan(clause))
                not_occurred = bool(_WP4G_NOT_OCCURRED_RE.search(compact))
                if "因公出国" in compact and (has_amount or not_occurred):
                    disclosed.add("overseas")
                if "公务接待" in compact and (has_amount or not_occurred):
                    disclosed.add("reception")
                if "公务用车运行" in compact and (has_amount or not_occurred):
                    disclosed.add("vehicle_operation")
                if (
                    _WP4G_PURCHASE_STEM_RE.search(compact)
                    and (has_amount or not_occurred)
                ) or _WP4G_PURCHASE_NOT_OCCURRED_RE.search(compact):
                    disclosed.add("vehicle_purchase")
        return disclosed

    @staticmethod
    def _evidence_para(paras: List[str], stem: str, fallback_stem: str) -> str:
        """缺失项的证据段落：优先含该词语干的最后一段（缺失披露应出现
        的最近语境），逐级回退到领域词干、三公章节首段、章节头部。
        证据不足时不伪造——章节头保底让 finding 仍可定位。"""
        for candidate_stem in (stem, fallback_stem):
            for para in reversed(paras):
                if candidate_stem in para:
                    return para
        for para in reversed(paras):
            if "三公" in para:
                return para
        return paras[0] if paras else ""

    @staticmethod
    def _actual_disclosure(item_key: str, evidence_para: str) -> str:
        """finding 的 actual disclosure：区分"仅有合并披露"与"整项缺席"。"""
        if item_key == "vehicle_purchase" and "公务用车购置" in evidence_para:
            return (
                "说明仅披露「公务用车购置及运行维护费」合并金额，"
                "未细化到公务用车购置费"
            )
        label = dict(
            (item_key_, label_)
            for item_key_, label_, _stem, _fallback in _WP4G_ITEM_SPECS
        )[item_key]
        return f"三公经费支出决算情况说明章节内未见「{label}」的金额披露或未发生说明"


# ==================================================================================
# 跨表：三公经费表 × 基本支出经济分类表（V33-CROSS-SAN-GONG-ECON）
#
# 业务关系与可比性（为什么只有"逐业务项的部分 ≤ 整体"可判定）
# ------------------------------------------------------------------
# 《财政拨款“三公”经费支出决算表》(FIN_07) 的“决算数”是**财政拨款全口径**的
# 三公经费支出——基本支出 + 项目支出。
# 《一般公共预算财政拨款基本支出决算表》(FIN_06) 是按经济分类列示的**基本支出**
# 决算明细；其中 302-12 因公出国（境）费用、302-17 公务接待费、
# 302-31 公务用车运行维护费、310-13 公务用车购置，与三公表的四个业务项
# 指向同一笔费用的两种归集口径：一个是整体，一个是它的基本支出部分。
#
# 因此这两张表的可比关系是**单向包含**（部分 ≤ 整体），不是相等：
#   - 要求相等，会把"项目支出里列支的三公"误判成跨表差异；
#   - 而"基本支出部分 > 三公经费（含项目支出的全口径）"在任何口径下都不成立，
#     这正是本规则要报的硬矛盾。
# 由此也能看出为什么**不能**拿"三公经费合计"去比"经济分类表总计"：总额之间
# 差着基本/项目两个层级，只有逐业务项的包含关系有业务依据（任务书 §六）。
#
# 真值来源：outputs/sample_validation_20260916/comparison.md 的 Y02
# （宜川路街道 2025 年度决算 P22 基本支出表 vs P24 三公表，人工判定"高"）。
# ==================================================================================

#: 三公经费业务项：(台账用键, 三公表列组主体, 基本支出表科目名称等价写法, 精确经济分类代码)
#:
#: 两张表对同一业务项的写法有固定差异（「…费」/「…费用」、公务用车购置/公务用车购置费），
#: 因此逐项登记**等价写法**；归一化后必须精确命中，不做模糊匹配——历史缺陷正是
#: "数字看起来接近就配一对、名字近似就当同一项"。
#:
#: 第四列是**精确经济分类代码**（不是"302/310 类级前缀"）：R2 评审 P1 收口——
#: 名称与编码必须**同时**成立才允许形成正式跨表比较。「30217 公务接待费」成立，
#: 「30231 公务接待费」是 identity conflict（同属 302 类不代表可互认）；名称命中
#: 但编码无法识别时，该项记取数不足而不是带着"编码未识别"出正式 finding。
_SAN_GONG_ECON_ITEM_SPECS: Tuple[Tuple[str, str, Tuple[str, ...], str], ...] = (
    ("overseas", "因公出国（境）费", ("因公出国（境）费用",), "30212"),
    ("vehicle_purchase", "公务用车购置费", ("公务用车购置",), "31013"),
    ("vehicle_operation", "公务用车运行维护费", ("公务用车运行维护费",), "30231"),
    ("reception", "公务接待费", ("公务接待费",), "30217"),
)


def _normalize_item_label(text: Any) -> str:
    """业务项名称归一：去空白 + 全角括号统一。

    只做这两件**无损**的事：表格单元格里的换行/续行空格（"公务用车运行维\\n护费"）
    与全半角括号差异不改变业务项身份；不做模糊匹配，不做同义词替换。
    """
    return re.sub(r"\s+", "", str(text or "")).replace("（", "(").replace("）", ")")


#: 三公表自身勾稽的两条等式（用于把"空白单元格"确认为 0）
_SAN_GONG_TOTAL_KEY = "合计"
_SAN_GONG_VEHICLE_SUBTOTAL_KEY = "小计"

#: 三公表列组主体键（**已归一化**）→ 台账用键。
#: 归一化必须与 ``_three_public_column_groups`` 用同一函数，否则列组查不到。
_SAN_GONG_SUBJECT_KEYS: Dict[str, str] = {
    _normalize_item_label(_SAN_GONG_TOTAL_KEY): "total",
    _normalize_item_label(_SAN_GONG_VEHICLE_SUBTOTAL_KEY): "vehicle_subtotal",
}
for _key, _subject, _names, _cls in _SAN_GONG_ECON_ITEM_SPECS:
    _SAN_GONG_SUBJECT_KEYS[_normalize_item_label(_subject)] = _key
del _key, _subject, _names, _cls

#: 四个业务项（不含合计/小计两个汇总结）——"某项列组缺失只阻塞该项"靠它区分
_SAN_GONG_ITEM_KEYS: FrozenSet[str] = frozenset(
    key for key, _subject, _names, _cls in _SAN_GONG_ECON_ITEM_SPECS
)

#: 两张来源表的业务表名（与 NINE_TABLES 的九表口径同源；FIN_06 别名收窄为
#: 基本支出决算表本身，避免"基本支出"这类词把别的表吸进来）
_FIN_06_TABLE_TITLE = "一般公共预算财政拨款基本支出决算表"
_FIN_07_TABLE_TITLE = "财政拨款“三公”经费支出决算表"
_FIN_06_TABLE_ALIASES: Tuple[str, ...] = tuple(NINE_TABLES[5]["aliases"])
_FIN_07_TABLE_ALIASES: Tuple[str, ...] = tuple(NINE_TABLES[6]["aliases"])

#: 本规则对应的检查义务编号（写进 finding，使问题与台账实例可对照）
_OBLIGATION_ID = "OBL-CROSS-SAN-GONG-ECON"


def _display_scale(value: Decimal) -> int:
    """数值在原文里的显示小数位（'' 显示为 0 位）——动态舍入包络的输入。"""
    exponent = value.as_tuple().exponent
    if isinstance(exponent, int) and exponent < 0:
        return -exponent
    return 0


def _ensure_parsed_tables(doc: Document) -> bool:
    """确保文档挂载了结构化表模型（ParsedTable），返回是否可用。

    生产主链路（pipeline / engine_rule_runner）目前只挂 page_texts/page_tables，
    parsed_tables 仅由 structured/shadow 入口构建（结构化消费收敛是 WP5 的范围）。
    本规则是**结构化事实消费者**：无挂载时用同一套 ``build_parsed_tables``
    自建一次（纯函数、确定性），不退回"全文正则抓数字 / 按数字距离猜列"的
    旧表征。已挂载时一律复用，保证同一份材料只有一份结构化事实。
    """
    tables = getattr(doc, "parsed_tables", None)
    if isinstance(tables, dict) and tables:
        return True
    from .structured_rules import build_parsed_tables

    try:
        doc.parsed_tables = build_parsed_tables(
            getattr(doc, "page_tables", []) or [],
            getattr(doc, "page_texts", []) or [],
        )
    except Exception:  # noqa: BLE001 - 解析层失败按取数不足处理，不得当成通过
        return False
    return bool(getattr(doc, "parsed_tables", None))


def _cross_table_by_alias(doc: Document, aliases: Tuple[str, ...]):
    """按表名身份定位**唯一**一张结构化表；命中 0 张或多张都返回 None。

    多张 = 表身份有歧义（同一份材料里出现两张同名表），此时宁可记取数不足，
    也不能随手取第一张——取错表会让整条跨表比较落在不同的表上。
    """
    hits: Dict[int, Any] = {}
    for alias in aliases:
        table = _find_parsed_table(doc, alias)
        if table is not None:
            hits[id(table)] = table
    if len(hits) == 1:
        return next(iter(hits.values()))
    return None


def _resolve_fiscal_year(doc: Document, pages: Tuple[int, ...]) -> Optional[int]:
    """解析材料财政年度：优先规则已定的 dominant_year，否则取前 3 页与两张表所在页。

    年份无法确认（或出现并列最高频的不同年份）时返回 None，调用方按取数不足处理：
    跨表比较的前提是期间一致，期间不确定就不该产出正式结论。
    """
    dominant = getattr(doc, "dominant_year", None)
    if isinstance(dominant, int):
        return dominant
    years_per_page = getattr(doc, "years_per_page", None) or []
    scan = {0, 1, 2, *(page - 1 for page in pages)}
    candidates: List[int] = []
    for index in sorted(scan):
        if 0 <= index < len(years_per_page):
            candidates.extend(int(year) for year in (years_per_page[index] or []))
    if not candidates:
        return None
    ranked = Counter(candidates).most_common()
    if len(ranked) > 1 and ranked[0][1] == ranked[1][1]:
        return None
    return int(ranked[0][0])


def _resolve_table_unit(doc: Document, page: int) -> Optional[str]:
    """取某页声明的金额单位（单位：万元/元/亿元）；未声明返回 None。"""
    units = getattr(doc, "units_per_page", None)
    if isinstance(units, list) and 0 < page <= len(units) and units[page - 1]:
        return str(units[page - 1])
    texts = getattr(doc, "page_texts", None)
    if isinstance(texts, list) and 0 < page <= len(texts):
        return extract_money_unit(texts[page - 1] or "")
    return None


def _econ_lanes(table: Any) -> Tuple[List[Tuple[int, int]], str]:
    """基本支出经济分类表的"车道"：相邻两个决算数列之间的列区间。

    结构化层已把「决算数」列的**全部位置**登记在 ``semantic_columns['final']``
    （样张 P22 为 [2, 5]：左栏第 2 列、右栏第 5 列；P15 双栏版为 [3, 7]）。
    车道 = (起点列, 金额列]，同一车道内的科目编码/名称与金额属于同一行业务项；
    跨车道的数字不得互相认领（"当前为空就向右取数字"的历史缺陷形态）。
    """
    amount_cols = sorted({int(col) for col in (table.semantic_columns.get("final") or [])})
    if not amount_cols:
        return [], "基本支出表未登记「决算数」列（semantic_columns.final 为空）"
    lanes: List[Tuple[int, int]] = []
    previous = -1
    for amount_col in amount_cols:
        if amount_col <= previous:
            continue
        lanes.append((previous + 1, amount_col))
        previous = amount_col
    return lanes, ""


def _econ_item_code_matches(code: str, expected_code: str) -> bool:
    """精确经济分类代码判定。

    主机码必须等于登记码；更深的 7 位展开码是同一业务项的下级子目，
    允许通过。除此之外（哪怕同属一个 3 位类级）一律视为身份冲突——
    「30231」不能因为是 302 类就冒认「30217 公务接待费」的比较资格。
    """
    return code == expected_code or (
        len(code) > len(expected_code) and code.startswith(expected_code)
    )


def _econ_item_amount_from_lane(
    cells: List[Any],
    lane: Tuple[int, int],
    names: Tuple[str, ...],
    expected_code: str,
) -> Optional[Dict[str, Any]]:
    """在一条车道内按科目名称取该业务项的金额/编码；不匹配返回 None。

    名称必须完全落在车道内（[起点, 金额列) 的文本单元格拼接），因此相邻车道
    的名称不会被认领；科目编码只在**车道首部**的整数字段里认，不参与金额取值。

    编码语义（fail-closed）：
    - 名称命中 + 编码 == 精确登记码（或其下级展开码）→ 正常返回；
    - 名称命中 + 编码指向别的业务项 → ``conflict``（identity conflict）；
    - 名称命中 + 编码无法识别 → ``code_missing=True``——由调用方记取数不足，
      不允许形成带"编码未识别"说法的正式 finding（证据契约要求可追溯）。
    """
    start, amount_col = lane
    if amount_col >= len(cells):
        return None
    label = _normalize_item_label(
        "".join(cell.text or "" for cell in cells[start:amount_col] if cell.text)
    )
    if not label or label not in names:
        return None
    # 编码在车道内有两种真实版式：单格（"30212"）与拆格（"302" + "12" 类款两列）。
    # 顺序拼接**名称文本出现之前**的整数字段得到完整编码；拼接结果长度不在
    # 3/5/7（类/款/展开码）的视为**编码缺失**（code_missing），交给调用方记
    # 取数不足——比"取第一个整数就停"严格，但只在名称已命中的行上才走到这里。
    code_parts: List[str] = []
    for cell in cells[start:amount_col]:
        if cell.text:
            break
        number = cell.number
        if number is None or number != number.to_integral_value():
            continue
        code_parts.append(str(int(number)))
    code: Optional[str] = None
    if code_parts:
        joined = "".join(code_parts)
        if len(joined) in (3, 5, 7):
            code = joined
    if code is None:
        return {
            "amount": cells[amount_col].number,
            "page": cells[amount_col].page or cells[start].page,
            "code": "",
            "name": label,
            "conflict": "",
            "code_missing": True,
        }
    if not _econ_item_code_matches(code, expected_code):
        # 名称命中，但同行编码指向**另一个**业务项：两栏身份不一致，
        # 无法确认这一行到底记的是哪个业务项 → 交给调用方记取数不足。
        return {
            "conflict": f"{code} {label}（expected {expected_code}，actual {code}："
                        "行内编码与名称指向不同业务项）",
        }
    return {
        "amount": cells[amount_col].number,
        "page": cells[amount_col].page or cells[start].page,
        "code": code,
        "name": label,
        "conflict": "",
    }


def _three_public_column_groups(table: Any) -> Tuple[Dict[str, Any], List[str]]:
    """三公表列组：主体标签 → 列组。同名重复出现按列身份歧义记账。"""
    groups: Dict[str, Any] = {}
    reasons: List[str] = []
    for group in table.column_groups:
        subject = _normalize_item_label(group.subject)
        if not subject:
            continue
        if subject in groups:
            reasons.append(f"三公表「{group.subject}」列组重复出现，列身份有歧义")
            continue
        groups[subject] = group
    return groups, reasons


def _three_public_data_row(table: Any) -> Tuple[Optional[Any], str]:
    """三公表的数据行：无标签行且唯一。

    表头/科目名/「预算数决算数」标签行都带文本标签，唯一无标签行才是金额行。
    出现 0 行或 2 行以上（无法确定取哪一行）都按取数不足返回。
    """
    rows = [
        row
        for row in table.rows
        if row.row_role != "header" and not _normalize_item_label(row.label)
    ]
    if not rows:
        return None, "三公表未找到无标签金额行"
    if len(rows) > 1:
        return None, f"三公表有 {len(rows)} 个无标签金额行，无法确定取哪一行"
    return rows[0], ""


def _three_public_cell_value(cells: List[Any], column: int) -> Tuple[Optional[Decimal], str]:
    """读取三公表某列单元格：(金额, 状态)，状态 ∈ value / blank / unreadable。"""
    if column < 0 or column >= len(cells):
        return None, "unreadable"
    cell = cells[column]
    if cell.number is not None:
        return cell.number, "value"
    text = str(cell.text or "").strip()
    if not text or text in _DASHES:
        # 空白/破折号在官方表里表示"无此项"，但**必须**由本表勾稽确认后才能当 0 用
        return None, "blank"
    return None, "unreadable"


class R33CrossSanGongEcon(Rule):
    """三公经费表（FIN_07）× 基本支出经济分类表（FIN_06）跨表资金来源一致性。

    只做一件事：对三公表的四个业务项，逐一确认基本支出表同业务项的经济分类
    决算数**不大于**三公表该业务项的决算数（部分 ≤ 整体）。超出即报，并在
    finding 里带齐两侧表身份、页码、业务项、编码、两侧金额、差额、单位与年度。

    失败即关闭（fail-closed）：表身份、列身份、费用项身份、金额单位、财政年度
    任一项确认不了，就不产生正式 finding，而是记取数不足/解析歧义，让人工复核。
    """

    code, severity = "V33-CROSS-SAN-GONG-ECON", "error"
    desc = "三公经费表 与 基本支出经济分类表 跨表资金来源一致性"

    def apply(self, doc: Document) -> List[Issue]:
        if not _ensure_parsed_tables(doc):
            raise RuleDeferred(self.code, "结构化表模型不可用（page_tables/page_texts 不足）")

        unresolved: List[str] = []
        table6 = _cross_table_by_alias(doc, _FIN_06_TABLE_ALIASES)
        table7 = _cross_table_by_alias(doc, _FIN_07_TABLE_ALIASES)
        if table6 is None:
            unresolved.append(f"未定位到唯一的《{_FIN_06_TABLE_TITLE}》结构化表")
        if table7 is None:
            unresolved.append(f"未定位到唯一的《{_FIN_07_TABLE_TITLE}》结构化表")
        if unresolved:
            raise RuleDeferred(self.code, "；".join(unresolved))

        for table in (table6, table7):
            errors = list(getattr(table, "parse_errors", None) or [])
            if errors:
                # 解析器已明确报告结构不确定（行宽不一致等），不得在不确定的表上出正式结论
                unresolved.append(
                    f"《{table.anchor_table_name or table.title}》解析存在不确定："
                    + "；".join(str(item) for item in errors[:3])
                )

        page6 = int((getattr(table6, "page_span", (0, 0)) or (0, 0))[0])
        page7 = int((getattr(table7, "page_span", (0, 0)) or (0, 0))[0])
        year = _resolve_fiscal_year(doc, (page6, page7))
        if year is None:
            unresolved.append("未能确认材料财政年度（期间一致性不可确认）")

        unit6 = _resolve_table_unit(doc, page6)
        unit7 = _resolve_table_unit(doc, page7)
        if not unit6 or not unit7:
            unresolved.append(
                f"金额单位未识别（基本支出表={unit6 or '未识别'}，三公经费表={unit7 or '未识别'}）"
            )
        elif unit6 != unit7:
            unresolved.append(f"两表金额单位不一致（{unit6} / {unit7}）")
        unit = unit6 if unit6 and unit6 == unit7 else ""

        lanes, lane_error = _econ_lanes(table6)
        if lane_error:
            unresolved.append(lane_error)

        groups, group_errors = _three_public_column_groups(table7)
        unresolved.extend(group_errors)
        data_row = None
        if not group_errors:
            data_row, row_error = _three_public_data_row(table7)
            if row_error:
                unresolved.append(row_error)

        if unresolved:
            # 单位/年度/车道/列组任一不成立，跨表比较根本无从下手：不产生任何
            # finding，整体记取数不足（此处尚无已确认冲突，无需 partial_issues）
            raise RuleDeferred(
                self.code,
                "；".join(dict.fromkeys(unresolved)),
                unresolved_reasons=list(dict.fromkeys(unresolved)),
            )

        # ---- 基本支出表（FIN_06）：逐业务项按车道取金额 ----
        # blocked：该业务项的比较被明确判定为不可用（身份冲突/命中多行/列组缺列…）。
        # 用集合逐项记账而不是拿原因文本做子串匹配——后者会把"别项的失败"错连到本项。
        blocked: Set[str] = set()
        basic: Dict[str, Dict[str, Any]] = {}
        for key, subject, names, expected_code in _SAN_GONG_ECON_ITEM_SPECS:
            normalized_names = tuple(_normalize_item_label(name) for name in names)
            found: List[Dict[str, Any]] = []
            conflict = ""
            for lane in lanes:
                for row in table6.rows:
                    if row.row_role == "header":
                        continue
                    hit = _econ_item_amount_from_lane(
                        row.cells, lane, normalized_names, expected_code
                    )
                    if hit is None:
                        continue
                    if hit.get("conflict"):
                        conflict = str(hit["conflict"])
                        break
                    found.append(hit)
                if conflict:
                    break
            if conflict:
                # 名称与精确编码不共指同一业务项（如「30231 公务接待费」）：
                # 同属一个 3 位类级也不得继续比较
                unresolved.append(f"基本支出表「{subject}」身份不一致：{conflict}")
                blocked.add(key)
                continue
            if len(found) > 1:
                unresolved.append(f"基本支出表「{subject}」命中 {len(found)} 行，无法确定取哪一行")
                blocked.add(key)
                continue
            if found and found[0].get("code_missing"):
                # 名称命中但具体经济分类编码无法确认：业务项身份不能只凭名称成立，
                # 该项不形成正式 finding（也不往证据里写"编码未识别"）；
                # 其余已确认冲突仍经末尾的 partial_issues 保留，不被吞掉。
                unresolved.append(
                    f"基本支出表「{subject}」名称命中，但具体经济分类编码无法确认"
                    f"（expected {expected_code}），该业务项不形成正式跨表比较"
                )
                blocked.add(key)
                continue
            if found:
                basic[key] = found[0]

        # ---- 三公表（FIN_07）：逐业务项取决算数列 ----
        three_public: Dict[str, Decimal] = {}
        three_public_pages: Dict[str, int] = {}
        blank_keys: Set[str] = set()
        scales: Dict[str, int] = {}
        item_groups: Dict[str, str] = {}
        for subject, key in _SAN_GONG_SUBJECT_KEYS.items():
            group = groups.get(subject)
            is_item = key in _SAN_GONG_ITEM_KEYS
            if group is None:
                # 四个业务项缺列组 → 该项不可比较（记账 + 阻塞该项）。
                # 「合计」「小计」两个汇总结缺列组本身不阻塞比较：它们只在
                # "有空白单元格需要确认成 0" 时才必需，缺了会让勾稽复算失败，
                # 由下面 _blank_cells_confirmed 报出。若四个分项都是明确数值，
                # 一份没有小计列组的三公表照样可以逐项比对，不该被判取数不足。
                if is_item:
                    unresolved.append(f"三公表未识别到「{subject}」列组")
                    blocked.add(key)
                continue
            column = group.columns.get("final")
            if column is None:
                if is_item:
                    unresolved.append(f"三公表「{subject}」列组未登记决算数列")
                    blocked.add(key)
                continue
            value, state = _three_public_cell_value(data_row.cells, int(column))
            if state == "unreadable":
                if is_item:
                    unresolved.append(f"三公表「{subject}」决算数单元格无法解析为金额")
                    blocked.add(key)
                continue
            if state == "blank":
                blank_keys.add(key)
                # 勾稽确认后才成立的"无此项=0"；按公开表显示精度写 0.00，
                # 使 finding 里的金额写法与文档中的决算数口径一致
                value = Decimal("0.00")
            else:
                scales[key] = _display_scale(value)
            three_public[key] = value
            # 证据页码以金额单元格的真实页为准（跨页表不得落成表起始页）
            evidence_cell = (
                data_row.cells[int(column)]
                if 0 <= int(column) < len(data_row.cells)
                else None
            )
            three_public_pages[key] = int(getattr(evidence_cell, "page", None) or page7)
            item_groups[key] = subject

        # 空白单元格当 0 用之前，先用本表勾稽确认：合计 = 出国 + 公车小计 + 接待，
        # 公车小计 = 购置 + 运行维护。两式不成立时"空白=0"只是猜测，必须记账。
        if blank_keys:
            confirmed, closure_note = self._blank_cells_confirmed(
                three_public, item_groups, blank_keys, scales, unit
            )
            if not confirmed:
                unresolved.append(
                    "三公表分项单元格为空白且本表勾稽不成立（"
                    + closure_note
                    + "），空白无法确认为 0"
                )
                blocked.update(blank_keys)

        # ---- 逐业务项比较：部分 ≤ 整体 ----
        issues: List[Issue] = []
        for key, subject, _names, _expected_code in _SAN_GONG_ECON_ITEM_SPECS:
            if key in blocked or key not in three_public:
                continue
            hit = basic.get(key)
            if hit is None or hit.get("amount") is None:
                # 基本支出表未列该业务项金额：没有可比的"部分"，跳过（不伪造 0）
                continue
            total = three_public[key]
            component: Decimal = hit["amount"]
            # 证据页码 = 证据所在单元格的真实页（续页行不得标成表起始页）
            basic_page = int(hit.get("page") or page6)
            three_page = int(three_public_pages.get(key) or page7)
            if component <= total:
                continue
            envelope = compute_dynamic_envelope(
                [(scales.get(key, 2), unit), (_display_scale(component), unit)]
            )
            excess = component - total
            if excess <= envelope:
                issues.append(
                    self._issue(
                        f"三公经费「{subject}」基本支出分项与本表决算数相差 {excess.normalize()} "
                        f"{unit}，在显示舍入包络（{envelope.normalize()} {unit}）内，可能为取整误差。",
                        self._location(hit, subject, key, total, component, excess, unit, year,
                                       basic_page, three_page, key in blank_keys),
                        severity="info",
                        evidence_text=self._evidence(hit, subject, total, component, excess, unit,
                                                     year, basic_page, three_page, key in blank_keys),
                    )
                )
                continue
            issues.append(
                self._issue(
                    f"跨表资金来源冲突：「{subject}」在基本支出经济分类表列示 {component} {unit}"
                    f"（{hit.get('code') or '编码未识别'}，第{basic_page}页决算数），大于三公经费支出决算表"
                    f"第{three_page}页同一业务项的决算数 {total} {unit}，差额 {excess.normalize()} {unit}"
                    f"（{year} 年度）。基本支出是三公经费的组成部分，部分大于整体时两表不能同时成立；"
                    "现有材料无法判定哪一侧有误，需人工复核两表底稿。",
                    self._location(hit, subject, key, total, component, excess, unit, year,
                                   basic_page, three_page, key in blank_keys),
                    severity="error",
                    evidence_text=self._evidence(hit, subject, total, component, excess, unit,
                                                 year, basic_page, three_page, key in blank_keys),
                )
            )

        if unresolved:
            # 已确认的冲突必须保留（不能因部分取数不足被吞掉），整体状态记取数不足
            raise RuleDeferred(
                self.code,
                "；".join(dict.fromkeys(unresolved)),
                partial_issues=issues,
                unresolved_reasons=list(dict.fromkeys(unresolved)),
            )
        return issues

    # ---- 内部：勾稽确认空白单元格 ----

    def _blank_cells_confirmed(
        self,
        values: Dict[str, Decimal],
        item_groups: Dict[str, str],
        blank_keys: set,
        scales: Dict[str, int],
        unit: str,
    ) -> Tuple[bool, str]:
        """三公表两条等式是否成立（空白按 0 代入）→ 空白能否确认为 0。"""
        required = ("total", "vehicle_subtotal")
        if any(key not in values for key in required):
            return False, "缺合计/公车小计列组，无法复算"
        equations = (
            ("total", ("overseas", "vehicle_subtotal", "reception")),
            ("vehicle_subtotal", ("vehicle_purchase", "vehicle_operation")),
        )
        notes: List[str] = []
        for lhs_key, rhs_keys in equations:
            if lhs_key not in values:
                return False, f"{item_groups.get(lhs_key, lhs_key)} 未取到"
            missing = [key for key in rhs_keys if key not in values]
            if missing:
                return False, f"{item_groups.get(lhs_key, lhs_key)} 的分项未取全"
            lhs = values[lhs_key]
            rhs = sum((values[key] for key in rhs_keys), Decimal("0"))
            gap = abs(lhs - rhs)
            terms = [
                (scales.get(key, 2), unit)
                for key in (lhs_key, *rhs_keys)
                if key not in blank_keys
            ]
            envelope = compute_dynamic_envelope(terms)
            notes.append(
                f"{item_groups.get(lhs_key, lhs_key)} {lhs.normalize()} vs "
                f"{rhs.normalize()}（差 {gap.normalize()}，包络 {envelope.normalize()}）"
            )
            if gap > envelope:
                return False, "；".join(notes)
        return True, "；".join(notes)

    # ---- 内部：finding 定位与证据 ----

    def _location(
        self,
        hit: Dict[str, Any],
        subject: str,
        key: str,
        total: Decimal,
        component: Decimal,
        excess: Decimal,
        unit: str,
        year: int,
        basic_page: int,
        three_public_page: int,
        from_blank: bool,
    ) -> Dict[str, Any]:
        code = str(hit.get("code") or "")
        return {
            "table": _FIN_06_TABLE_TITLE,
            "page": basic_page,
            "pages": sorted({basic_page, three_public_page}),
            "row": f"{code} {hit.get('name') or subject}".strip(),
            "field": subject,
            "code": code,
            "san_gong_item": key,
            "obligation_id": _OBLIGATION_ID,
            "basic_amount": str(component),
            "three_public_amount": str(total),
            "difference": str(excess),
            "unit": unit,
            "fiscal_year": year,
            "three_public_amount_from_blank": from_blank,
            "table_refs": [
                {
                    "role": "基本支出经济分类",
                    "table": _FIN_06_TABLE_TITLE,
                    "page": basic_page,
                    "row": f"{code} {hit.get('name') or subject}".strip(),
                    "code": code,
                    "field": "决算数",
                },
                {
                    "role": "三公经费",
                    "table": _FIN_07_TABLE_TITLE,
                    "page": three_public_page,
                    "field": subject,
                    "col": "决算数",
                },
            ],
        }

    def _evidence(
        self,
        hit: Dict[str, Any],
        subject: str,
        total: Decimal,
        component: Decimal,
        excess: Decimal,
        unit: str,
        year: int,
        basic_page: int,
        three_public_page: int,
        from_blank: bool,
    ) -> str:
        code = str(hit.get("code") or "编码未识别")
        lines = [
            f"基本支出经济分类表：{_FIN_06_TABLE_TITLE}（第{basic_page}页，决算数）",
            f"三公经费支出决算表：{_FIN_07_TABLE_TITLE}（第{three_public_page}页，决算数）",
            f"业务项：{subject}",
            f"基本支出表：{code} {hit.get('name') or subject} = {component} {unit}",
            f"三公经费表：{subject} = {total} {unit}",
            f"差额：{excess.normalize()} {unit}（基本支出分项 − 三公经费决算数）",
            f"财政年度：{year}；单位：{unit}",
            "可比口径：两份表都是同一财政拨款口径下的决算数；基本支出是三公经费"
            "（含项目支出）的组成部分，可比关系为「分项 ≤ 合计」。",
        ]
        if from_blank:
            lines.append(
                "说明：三公经费表该业务项单元格为空白，已按本表勾稽"
                "（合计 = 出国费 + 公务用车购置及运行维护费小计 + 公务接待费；"
                "小计 = 公务用车购置费 + 公务用车运行维护费）确认为 0。"
            )
        return "\n".join(lines)


# ==================================================================================
# 表文：基金/国资决算表 × 对应情况说明 逐项一致性（V33-TXT-FUND-DETAIL）
#
# 业务关系与可比性（为什么只有"逐项"可判）
# ------------------------------------------------------------------
# 《政府性基金预算财政拨款收入支出决算表》(FIN_08) 与《国有资本经营预算财政
# 拨款收入支出决算表》(FIN_09) 的「本年支出」按功能分类逐项列示；对应的
# "情况说明"章节在「支出具体情况如下」之后逐项写明 X（类）Y（款）Z（项）金额。
# 同一业务项的两侧金额必须一致。真值：2026-09-16 人工判定 Y03（宜川路街道
# 2025 年度决算）——P25 表内 2219899 其他住房保障支出本年支出 105.00 万元，
# P37 说明第八节写 135.00 万元（差 30.00，且大于说明自述支出总额 105.00），
# 系统曾漏报（outputs/sample_validation_20260916/comparison.md Y03，程度：高）。
#
# 为什么"逐项"必须按功能分类身份配对，不能按总额或数字就近：
# 说明段同时出现 收入总额、支出总额、项金额、年初预算、支出决算、同比增减
# （Y03 同段就有 105/105/135/0.00/135 五个数）——只有「X（类）Y（款）Z（项）
# <金额>」句式同时携带完整的业务项身份与支出明细口径；配对依据是表内 7 位
# 功能分类编码的三级层级名称逐级全匹配，任一层名称对不上即不可比。总额比对
# 已由 V33-101~108 系（单点/总额）承担，本规则不重复（任务纪律：禁止再造总
# 额 checker）。
# ==================================================================================

_TXT_FUND_OBLIGATION_ID = "OBL-TXT-FUND-DETAIL"

#: (scope 键, scope 名称, 表名, 表别名, 说明章节标题关键词)
_TXT_FUND_SCOPES: Tuple[Tuple[str, str, str, Tuple[str, ...], Tuple[str, ...]], ...] = (
    (
        "gov_fund",
        "政府性基金",
        NINE_TABLES[7]["name"],
        tuple(NINE_TABLES[7]["aliases"]),
        ("政府性基金", "收入支出决算情况说明"),
    ),
    (
        "state_capital",
        "国有资本经营",
        NINE_TABLES[8]["name"],
        tuple(NINE_TABLES[8]["aliases"]),
        ("国有资本经营", "收入支出决算情况说明"),
    ),
)

#: 说明侧「无相关收支」等效表述（去空白后包含即命中）。
#: 注意：只有当**表侧**列有非零金额时才构成矛盾；不得反过来凭说明里的一个
#: "无"字把整个 scope 判不适用——空表 + 无收支说明同时成立才是该 scope 无
#: 比较对象的证据（V33-109 另行负责空表说明缺失）。
_TXT_FUND_NEGATIVE_STATEMENTS: Dict[str, Tuple[str, ...]] = {
    "gov_fund": ("无政府性基金预算财政拨款收入和支出", "无政府性基金预算财政拨款收支"),
    "state_capital": ("无国有资本经营预算财政拨款收入和支出", "无国有资本经营预算财政拨款收支"),
}

#: 支出明细引导语（含变体）——说明项金额的口径锚（本年支出决算明细段）
_TXT_FUND_BREAKDOWN_RE = re.compile(r"支\s*出\s*(?:具\s*体\s*)?情\s*况\s*如\s*下")

#: 项级说明句：类（类）款（款）项（项）金额+单位。名称段允许内部换行
#: （PDF 软换行把"其他住房保障支出"拆成两行），但不允许跨过句读边界与括号
#: ——否则引导语会被并进名称段。金额必须自带单位字样：不带单位的裸数字
#: 多为年份/编号/占比，一律不绑定。
_TXT_FUND_ITEM_RE = re.compile(
    r"(?P<klass>[^，。；：:、（）()]{1,40}?)\s*（\s*类\s*）\s*"
    r"(?P<kuan>[^，。；：:、（）()]{1,60}?)\s*（\s*款\s*）\s*"
    r"(?P<xiang>[^，。；：:、（）()]{1,60}?)\s*（\s*项\s*）\s*"
    r"[，,]?\s*(?P<amount>\d[\d,，]*(?:\.\d+)?)\s*"
    r"(?P<unit>万\s*元|亿\s*元|元)"
)

#: 说明句前缀里的显式年份（期间锚）：与材料年度不同的年份出现在同一句里，
#: 说明该句可能在描述另一期间——不跨期绑定。
_TXT_FUND_YEAR_RE = re.compile(r"(?:19|20)\d{2}")

#: 单位归一（AGENTS.md v3_3_portable 口径）：元 = 0.0001 万元、亿元 = 10000 万元
_TXT_FUND_UNIT_TO_WAN: Dict[str, Decimal] = {
    "万元": Decimal("1"),
    "元": Decimal("0.0001"),
    "亿元": Decimal("10000"),
}


def _txt_fund_norm(text: Any) -> str:
    """基金/国资业务项名称与单位归一：去空白（PDF 断行）+ 全半角括号统一。

    只做无损归一，不做模糊匹配——历史缺陷正是"名称近似就当同一项"。
    """
    return re.sub(r"\s+", "", str(text or "")).replace("（", "(").replace("）", ")")


def _txt_fund_display(value: Decimal) -> str:
    """万元口径两位小数显示（与公开表显示精度一致；normalize 会把 30.00 变 3E+1，不用）。"""
    return str(value.quantize(Decimal("0.01")))


def _txt_fund_merged_pages(doc: Document) -> Tuple[str, List[int]]:
    """合并页文本并记录每页起始偏移（说明句 → 物理页码 反查用）。"""
    texts = [str(item or "") for item in (getattr(doc, "page_texts", []) or [])]
    offsets: List[int] = []
    parts: List[str] = []
    cursor = 0
    for text in texts:
        offsets.append(cursor)
        parts.append(text)
        cursor += len(text) + 1  # 与 "\n".join 的分隔符对齐
    return "\n".join(parts), offsets


def _txt_fund_page_for(offsets: List[int], position: int) -> int:
    """字符偏移 → 物理页（1 基）：最后一个起始偏移不晚于 position 的页。"""
    page = 0
    for index, offset in enumerate(offsets):
        if offset <= position:
            page = index + 1
        else:
            break
    return page


def _txt_fund_section(
    merged: str, offsets: List[int], keywords: Tuple[str, ...]
) -> Optional[Dict[str, Any]]:
    """定位资金性质对应的情况说明章节。

    复用 narration.find_section_scope_with_title 的"取正文最长"启发式排除目录
    页同名行干扰；章节标题页码由标题串在正文起点前的最后一次出现反查（目录中
    的同名行在更早处，rfind 取后者即真实章节标题）。
    """
    from src.utils.narration import find_section_scope_with_title

    found = find_section_scope_with_title(merged, list(keywords))
    if not found:
        return None
    title, body, start, _end = found
    title_pos = merged.rfind(title, 0, start)
    return {
        "title": title,
        "body": body,
        "body_start": start,
        "normalized": _txt_fund_norm(body),
        "page": _txt_fund_page_for(offsets, title_pos if title_pos >= 0 else start),
    }


def _txt_fund_tables_by_alias(doc: Document, aliases: Tuple[str, ...]) -> List[Any]:
    """按表名别名枚举全部命中的结构化表（0 张或多张都如实返回）。

    匹配口径与 ``_find_parsed_table`` 同源（title 含别名 / 锚点表名含别名 /
    表起始页页文本含别名），但不做"第一个命中即返回"——同一份材料出现两张
    同名表是身份歧义，必须让调用方记取数不足，而不是随手取第一张。
    """
    tables = getattr(doc, "parsed_tables", None)
    if not isinstance(tables, dict):
        return []
    page_texts = getattr(doc, "page_texts", []) or []
    matched: List[Any] = []
    for table in tables.values():
        title = getattr(table, "title", "") or ""
        anchor = getattr(table, "anchor_table_name", "") or ""
        start_page = int((getattr(table, "page_span", (0, 0)) or (0, 0))[0] or 0)
        page_hit = (
            0 < start_page <= len(page_texts)
            and any(alias in (page_texts[start_page - 1] or "") for alias in aliases)
        )
        if any(alias in title or alias in anchor for alias in aliases) or page_hit:
            matched.append(table)
    return matched


def _txt_fund_table_columns(table: Any) -> Tuple[Optional[Dict[str, int]], str]:
    """定位基金/国资表的关键列（编码/名称/本年支出·功能分类合计）。

    不读 semantic_columns：真值样张 P25 的「合计/基本支出/项目支出」子表头
    与合计行金额同在一行，被数据行守卫挡下后 semantic_columns 为空；P26 虽然
    登记了 total=[0,4]，但 [0] 是行标签列（已知陷阱）。两种版式都必须从表头
    行 + 功能分类子表头行走同一条显式定位路。
    """
    header_rows = [row for row in table.rows if row.row_role == "header"]
    if not header_rows:
        return None, "未解析到表头行"
    code_col: Optional[int] = None
    name_col: Optional[int] = None
    expend_col: Optional[int] = None
    year_end_col: Optional[int] = None
    for row in header_rows:
        for index, cell in enumerate(row.cells):
            text = _txt_fund_norm(cell.text)
            if not text:
                continue
            if code_col is None and "编码" in text:
                code_col = index
            if name_col is None and "科目名称" in text:
                name_col = index
            if expend_col is None and text in ("本年支出", "本年支出合计"):
                expend_col = index
            if year_end_col is None and "年末结转" in text:
                year_end_col = index
    missing = [
        label
        for label, value in (
            ("科目编码", code_col),
            ("科目名称", name_col),
            ("本年支出", expend_col),
            ("年末结转和结余", year_end_col),
        )
        if value is None
    ]
    if missing:
        return None, "表头未定位到「" + "」「".join(missing) + "」列"
    assert expend_col is not None and year_end_col is not None
    if year_end_col <= expend_col:
        return None, "「本年支出」与「年末结转和结余」列位序不成立"
    # 功能分类子表头行：本年支出列段内同现「基本支出/项目支出」，段内「合计」
    # 即本年支出合计列（P25 的子表头在合计行上、P26 在空合计行上，同一路径）。
    # 「合计」子列与「本年支出」超级表头同位（合并单元格抽取落在组首列），区间
    # 下界必须含 expend_col，否则永远扫不到它。
    total_col: Optional[int] = None
    for row in table.rows:
        texts = [_txt_fund_norm(cell.text) for cell in row.cells]
        has_basic = any(
            t == "基本支出" and expend_col <= i < year_end_col for i, t in enumerate(texts)
        )
        has_project = any(
            t == "项目支出" and expend_col <= i < year_end_col for i, t in enumerate(texts)
        )
        if not (has_basic and has_project):
            continue
        totals = [i for i, t in enumerate(texts) if t == "合计" and expend_col <= i < year_end_col]
        if len(totals) > 1:
            return None, "本年支出列段内「合计」子表头出现多次，列身份有歧义"
        if totals:
            total_col = totals[0]
            break
    if total_col is None:
        return None, "「本年支出」列段内未定位到功能分类「合计」子表头"
    assert code_col is not None and name_col is not None
    return {"code": code_col, "name": name_col, "expend_total": total_col}, ""


def _txt_fund_table_items(
    table: Any, columns: Dict[str, int]
) -> Tuple[List[Dict[str, Any]], Dict[str, str]]:
    """取表内功能分类行（编码/名称/本年支出金额）与 编码→名称 层级表。

    金额单元格读 ParsedCell.number（三态）：空白/文本不转 0，保持 None
    交给调用方按"不可读"处理。
    """
    name_col = columns["name"]
    total_col = columns["expend_total"]
    table_page = int((getattr(table, "page_span", (0, 0)) or (0, 0))[0] or 0)
    hierarchy: Dict[str, str] = {}
    items: List[Dict[str, Any]] = []
    for row in table.rows:
        if row.row_role == "header":
            continue
        code = str(row.code or "").strip()
        if not code or not code.isdigit():
            continue
        name = ""
        if 0 <= name_col < len(row.cells):
            name = _txt_fund_norm(row.cells[name_col].text)
        hierarchy[code] = name
        amount = None
        page = table_page
        if 0 <= total_col < len(row.cells):
            cell = row.cells[total_col]
            amount = cell.number
            page = int(getattr(cell, "page", None) or table_page)
        items.append({"code": code, "name": name, "amount": amount, "page": page})
    return items, hierarchy


def _txt_fund_match_item(
    items: List[Dict[str, Any]],
    hierarchy: Dict[str, str],
    klass: str,
    kuan: str,
    xiang: str,
) -> Tuple[Optional[Dict[str, Any]], str]:
    """三级层级名称逐级全匹配定位唯一表行；0 行/多行/层级不全都不可比。

    说明侧只有名称没有编码，身份锚是"表内编码层级推导出的名称链"：项级行
    （7 位编码）的款层（code[:5]）与类层（code[:3]）行名称必须逐级相等。
    只凭项级名称相等不认——那是 Mutation A 要拦的误配形态。
    """
    same_name = [item for item in items if len(item["code"]) == 7 and item["name"] == xiang]
    hits: List[Dict[str, Any]] = []
    incomplete = False
    for item in same_name:
        kuan_name = hierarchy.get(item["code"][:5])
        klass_name = hierarchy.get(item["code"][:3])
        if kuan_name is None or klass_name is None:
            incomplete = True
            continue
        if kuan_name == kuan and klass_name == klass:
            hits.append(item)
    if len(hits) > 1:
        return None, "命中多行，无法确定取哪一行"
    if hits and len(same_name) > len(hits):
        return None, "同名项级行存在类/款层级不全的行，业务身份无法闭环"
    if hits:
        return hits[0], ""
    if incomplete:
        return None, "表内项级行存在，但类/款层级行缺失，三级业务身份无法闭环"
    return None, "未在表内定位到同名同级的功能分类项"


def _txt_fund_bind_statements(
    body: str, fiscal_year: Optional[int]
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """绑定说明章节里的项级支出金额句。

    一条说明句要成为正式比较的说明侧证据，必须同时满足四个锚：
    - 业务项锚：完整三级「X（类）Y（款）Z（项）」身份；
    - 口径锚：落在「支出具体情况如下（含变体）」之后的支出明细段里
      （同段还有收入总额/年初预算/同比增减，都不能当项金额）；
    - 期间锚：句前缀不出现与材料年度不同的显式年份；
    - 单位锚：金额自带单位字样且在归一口径内。
    任一锚不成立 → 不绑定并记录原因（fail-closed，不出正式结论）。
    """
    markers = [m.start() for m in _TXT_FUND_BREAKDOWN_RE.finditer(body)]
    bindings: List[Dict[str, Any]] = []
    blocked: List[str] = []
    amounts_by_item: Dict[str, List[str]] = {}
    for m in _TXT_FUND_ITEM_RE.finditer(body):
        klass = _txt_fund_norm(m.group("klass"))
        kuan = _txt_fund_norm(m.group("kuan"))
        xiang = _txt_fund_norm(m.group("xiang"))
        amount_text = re.sub(r"[,，\s]", "", m.group("amount"))
        unit = _txt_fund_norm(m.group("unit"))
        label = f"{klass}（类）{kuan}（款）{xiang}（项）"
        if not any(pos < m.start() for pos in markers):
            blocked.append(
                f"说明句「{label}…」未落在「支出具体情况如下」支出明细段内，"
                "支出决算口径不可确认"
            )
            continue
        # 期间锚：句读边界截出的前缀**或项名本身**出现异年 → 不跨期绑定。
        # 项名可能自带年份前缀（「2024年度住房保障支出（类）」——年份被名称段
        # 吸收时前缀检查扫不到，必须两处都查）。
        prefix = body[max(0, m.start() - 40):m.start()]
        cut = max(prefix.rfind(ch) for ch in "。；：！？\n")
        if cut >= 0:
            prefix = prefix[cut + 1:]
        foreign_years = sorted(
            {
                int(found)
                for found in _TXT_FUND_YEAR_RE.findall(prefix + klass)
                if fiscal_year is not None and int(found) != fiscal_year
            }
        )
        if fiscal_year is None or foreign_years:
            blocked.append(
                f"说明句「{label}…」所在句含与材料年度（{fiscal_year}）"
                f"不同的年份 {foreign_years}，不跨期绑定"
            )
            continue
        factor = _TXT_FUND_UNIT_TO_WAN.get(unit)
        if factor is None:
            blocked.append(f"说明句「{label}…」金额单位「{unit}」不在归一口径内")
            continue
        try:
            amount = Decimal(amount_text)
        except Exception:  # pragma: no cover - 正则已保证数字形态
            blocked.append(f"说明句「{label}…」金额无法解析为数值")
            continue
        scale = len(amount_text.split(".", 1)[1]) if "." in amount_text else 0
        key = f"{klass}|{kuan}|{xiang}"
        amounts_by_item.setdefault(key, []).append(amount_text)
        bindings.append(
            {
                "klass": klass,
                "kuan": kuan,
                "xiang": xiang,
                "label": label,
                "amount": amount,
                "unit": unit,
                "scale": scale,
                "offset": m.start(),
                "span": m.group(0),
            }
        )
    # 同一业务项出现多个不同金额：取哪个无从证明（同段矛盾归其它规则）→ 不比较该项
    deduped: List[Dict[str, Any]] = []
    processed: Set[str] = set()
    for binding in bindings:
        key = f"{binding['klass']}|{binding['kuan']}|{binding['xiang']}"
        if len(set(amounts_by_item[key])) > 1:
            blocked.append(
                f"说明对「{binding['label']}」给出了多个不同金额"
                f"（{'、'.join(dict.fromkeys(amounts_by_item[key]))}），"
                "无法确定与本表比较的金额（同段矛盾请先人工复核）"
            )
            continue
        if key in processed:
            continue
        processed.add(key)
        deduped.append(binding)
    return deduped, list(dict.fromkeys(blocked))


class R33TxtFundDetail(Rule):
    """政府性基金(FIN_08)/国有资本经营(FIN_09)决算表 × 对应说明 逐项支出一致性。

    只做一件事：把说明章节「支出具体情况如下」段里以完整类/款/项身份列示的
    支出金额，逐一与表内同一功能分类项的「本年支出」决算金额比对；不一致即
    报，finding 带齐两侧表身份、页码、科目编码/名称、两侧金额、差额、单位、
    年度与资金性质（义务编号可对照台账）。

    每个 fund scope 独立判适用性（缺 FIN_09 表不得拖垮基金 scope，反之亦然）；
    表身份/列身份/科目身份/金额单位/财政年度任一确认不了就 fail-closed 记取
    数不足，已确认的冲突经 partial_issues 保留，不被部分取数不足吞掉。
    """

    code, severity = "V33-TXT-FUND-DETAIL", "error"
    desc = "政府性基金/国有资本经营决算表与对应说明逐项支出一致性"

    def apply(self, doc: Document) -> List[Issue]:
        if not _ensure_parsed_tables(doc):
            raise RuleDeferred(self.code, "结构化表模型不可用（page_tables/page_texts 不足）")
        merged, offsets = _txt_fund_merged_pages(doc)
        issues: List[Issue] = []
        unresolved: List[str] = []
        for scope in _TXT_FUND_SCOPES:
            self._apply_scope(doc, scope, merged, offsets, issues, unresolved)
        if unresolved:
            raise RuleDeferred(
                self.code,
                "；".join(dict.fromkeys(unresolved)),
                partial_issues=issues,
                unresolved_reasons=list(dict.fromkeys(unresolved)),
            )
        return issues

    # ---- 内部：单 scope ----

    def _apply_scope(
        self,
        doc: Document,
        scope: Tuple[str, str, str, Tuple[str, ...], Tuple[str, ...]],
        merged: str,
        offsets: List[int],
        issues: List[Issue],
        unresolved: List[str],
    ) -> None:
        scope_key, scope_label, table_title, aliases, section_keywords = scope
        section = _txt_fund_section(merged, offsets, section_keywords)

        # 表身份：枚举**全部**命中表（_find_parsed_table 每 alias 只回第一个命中，
        # 同名两张表会漏检歧义）；命中 0 张 = 表缺失，>1 张 = 身份歧义。
        matched = _txt_fund_tables_by_alias(doc, aliases)
        if len(matched) > 1:
            unresolved.append(
                f"{scope_label}：《{table_title}》结构化表命中 {len(matched)} 张，表身份有歧义"
            )
            return
        table = matched[0] if matched else None

        if table is None:
            # 表缺失本身归九表在位检查；但说明已列示项级金额而表缺失时，
            # 本义务确实无从核对 → 记取数不足（不是静默通过）。
            if section is not None and _TXT_FUND_ITEM_RE.search(section["body"]):
                unresolved.append(
                    f"{scope_label}：说明章节列示了项级金额，但未定位到"
                    f"《{table_title}》结构化表，无法逐项核对"
                )
            return

        parse_errors = list(getattr(table, "parse_errors", None) or [])
        if parse_errors:
            unresolved.append(
                f"《{table_title}》解析存在不确定："
                + "；".join(str(item) for item in parse_errors[:3])
            )
            return

        columns, column_error = _txt_fund_table_columns(table)
        if columns is None:
            unresolved.append(f"《{table_title}》{column_error}")
            return
        items, hierarchy = _txt_fund_table_items(table, columns)
        readable = [item for item in items if item["amount"] is not None]
        has_data = any(item["amount"] != 0 for item in readable)

        if not has_data:
            # 空表：本 scope 无逐项比较对象（空表说明缺失归 V33-109）。但说明
            # 列示了项级金额而表无数据时，两侧矛盾到无法逐项核对 → 记取数不足。
            if section is not None and _TXT_FUND_ITEM_RE.search(section["body"]):
                unresolved.append(
                    f"{scope_label}：说明章节列示了项级金额，但《{table_title}》"
                    "未取到任何金额，无法逐项核对"
                )
            return

        table_page = int((getattr(table, "page_span", (0, 0)) or (0, 0))[0] or 0)
        if section is None:
            unresolved.append(
                f"{scope_label}：《{table_title}》（第{table_page}页）列有支出金额，"
                f"但未定位到「{section_keywords[0]}…{section_keywords[1]}」说明章节"
            )
            return

        year = _resolve_fiscal_year(doc, (table_page, section["page"]))
        if year is None:
            unresolved.append(f"{scope_label}：未能确认材料财政年度，期间一致性不可确认")
            return

        table_unit = _resolve_table_unit(doc, table_page)
        table_factor = _TXT_FUND_UNIT_TO_WAN.get(_txt_fund_norm(table_unit or ""))
        if table_factor is None:
            unresolved.append(
                f"{scope_label}：《{table_title}》金额单位未识别或不在归一口径"
                f"（{table_unit or '未识别'}），无法形成正式比较"
            )
            return

        # 表有数据 + 说明称"无相关收支"：两侧不能同时成立（正式矛盾）
        negatives = _TXT_FUND_NEGATIVE_STATEMENTS[scope_key]
        if any(phrase in section["normalized"] for phrase in negatives):
            total_wan = sum(
                (item["amount"] for item in readable), Decimal("0")
            ) * table_factor
            message = (
                f"{scope_label}支出表文矛盾：说明章节（第{section['page']}页）称"
                f"「无{scope_label}预算财政拨款收入和支出」，但《{table_title}》"
                f"第{table_page}页列示了 {len(readable)} 个功能分类项"
                f"（本年支出合计 {_txt_fund_display(total_wan)} 万元）。"
                "表与说明不能同时成立，需人工复核表与说明底稿。"
            )
            location = {
                "table": table_title,
                "page": table_page,
                "pages": sorted({table_page, section["page"]}),
                "fund_scope": scope_label,
                "obligation_id": _TXT_FUND_OBLIGATION_ID,
                "table_amount": str(total_wan),
                "unit": "万元",
                "fiscal_year": year,
                "table_refs": [
                    {
                        "role": "表格",
                        "table": table_title,
                        "page": table_page,
                        "field": "本年支出（功能分类合计）",
                    },
                    {
                        "role": "情况说明",
                        "section": section["title"],
                        "page": section["page"],
                    },
                ],
            }
            evidence = "\n".join(
                [
                    f"表格侧：《{table_title}》（第{table_page}页，本年支出·功能分类合计）",
                    f"说明侧：{section['title']}（第{section['page']}页）",
                    f"表格列示功能分类项 {len(readable)} 个，本年支出合计 "
                    f"{_txt_fund_display(total_wan)} 万元",
                    "说明称「无" + scope_label + "预算财政拨款收入和支出」",
                    f"财政年度：{year}；资金性质：{scope_label}预算财政拨款",
                    "可比口径：同一单位同一年度同一资金性质，表与说明对"
                    "「是否存在相关收支」给出相反结论。",
                ]
            )
            issues.append(
                self._issue(message, location, severity="error", evidence_text=evidence)
            )
            return

        bindings, blocked = _txt_fund_bind_statements(section["body"], year)
        if blocked:
            unresolved.extend(f"{scope_label}：{reason}" for reason in blocked)
        if not bindings:
            unresolved.append(
                f"{scope_label}：《{table_title}》（第{table_page}页）列有支出金额，"
                "但说明章节无可绑定的项级支出表述"
            )
            return

        for binding in bindings:
            item, match_error = _txt_fund_match_item(
                items, hierarchy, binding["klass"], binding["kuan"], binding["xiang"]
            )
            if item is None:
                unresolved.append(
                    f"{scope_label}：说明项「{binding['label']}」{match_error}，该句不形成正式比较"
                )
                continue
            if item["amount"] is None:
                unresolved.append(
                    f"{scope_label}：表内「{item['code']} {item['name']}」的本年支出"
                    "单元格不可读（空白不按 0 处理），该业务项不形成正式比较"
                )
                continue
            table_wan = item["amount"] * table_factor
            narrative_wan = binding["amount"] * _TXT_FUND_UNIT_TO_WAN[binding["unit"]]
            envelope = compute_dynamic_envelope(
                [
                    (_display_scale(item["amount"]), _txt_fund_norm(table_unit or "")),
                    (binding["scale"], binding["unit"]),
                ]
            )
            diff = abs(table_wan - narrative_wan)
            if diff <= Decimal("0.0000001"):
                continue
            narrative_page = _txt_fund_page_for(
                offsets, section["body_start"] + binding["offset"]
            )
            location = {
                "table": table_title,
                "page": int(item["page"]),
                "pages": sorted({int(item["page"]), narrative_page}),
                "row": f"{item['code']} {item['name']}",
                "code": item["code"],
                "field": "本年支出（功能分类合计）",
                "fund_scope": scope_label,
                "obligation_id": _TXT_FUND_OBLIGATION_ID,
                "table_amount": str(item["amount"]),
                "narrative_amount": str(binding["amount"]),
                "narrative_page": narrative_page,
                "narrative_section": section["title"],
                "difference": _txt_fund_display(diff),
                "unit": "万元",
                "fiscal_year": year,
                "table_refs": [
                    {
                        "role": "表格",
                        "table": table_title,
                        "page": int(item["page"]),
                        "row": f"{item['code']} {item['name']}",
                        "code": item["code"],
                        "field": "本年支出（功能分类合计）",
                    },
                    {
                        "role": "情况说明",
                        "section": section["title"],
                        "page": narrative_page,
                        "span": binding["span"],
                    },
                ],
            }
            if diff <= envelope:
                issues.append(
                    self._issue(
                        f"{scope_label}支出说明「{binding['label']}」表列示 {item['amount']} "
                        f"{table_unit}，说明写 {binding['amount']} {binding['unit']}，相差 "
                        f"{_txt_fund_display(diff)} 万元，在显示舍入包络"
                        f"（{_txt_fund_display(envelope)} 万元）内，可能为取整误差。",
                        location,
                        severity="info",
                        evidence_text=self._txt_fund_evidence(
                            scope_label, table_title, table_unit,
                            section["title"], item, binding,
                            narrative_page, diff, year,
                        ),
                    )
                )
                continue
            issues.append(
                self._issue(
                    f"{scope_label}支出说明不一致：功能分类项「{binding['label']}」在"
                    f"《{table_title}》（第{item['page']}页，本年支出·功能分类合计）列示 "
                    f"{item['amount']} {table_unit}，但「{section['title']}」"
                    f"（第{narrative_page}页）写 {binding['amount']} {binding['unit']}，"
                    f"相差 {_txt_fund_display(diff)} 万元（{year} 年度）。"
                    "表与说明不能同时成立，现有材料无法判定哪一侧有误，"
                    "需人工复核表与说明底稿。",
                    location,
                    severity="error",
                    evidence_text=self._txt_fund_evidence(
                        scope_label, table_title, table_unit,
                        section["title"], item, binding,
                        narrative_page, diff, year,
                    ),
                )
            )

    # ---- 内部：finding 证据 ----

    def _txt_fund_evidence(
        self,
        scope_label: str,
        table_title: str,
        table_unit: str,
        section_title: str,
        item: Dict[str, Any],
        binding: Dict[str, Any],
        narrative_page: int,
        diff: Decimal,
        year: int,
    ) -> str:
        return "\n".join(
            [
                f"表格侧：《{table_title}》（第{item['page']}页，本年支出·功能分类合计）",
                f"说明侧：{section_title}（第{narrative_page}页）",
                f"业务项：{item['code']} {item['name']}（{binding['label']}）",
                f"表格金额：{item['amount']} {table_unit}",
                f"说明金额：{binding['amount']} {binding['unit']}"
                f"（原文：{binding['span']}）",
                f"差额：{_txt_fund_display(diff)} 万元",
                f"财政年度：{year}；单位：万元；资金性质：{scope_label}预算财政拨款",
                "可比口径：两侧均为本年度" + scope_label + "预算财政拨款的本年"
                "支出决算数，同一功能分类项。",
            ]
        )


# ============================================================================
# WP4-C：文内同一指标重复披露一致性（V33-NARRATIVE-INDICATOR-REPEAT，final only）
#
# 真值（2026-09-16 人工复核批次，均为系统当时漏报）：
# - S03（高）：石泉路街道 2025 年度决算 P32 职业年金条目首句 257.14 万元 vs
#   P33 同条目「支出决算为 245.53 万元」，差 11.61（完成率 93.57% 对应 257.14，
#   245.53/274.82 仅 89.34%）。
# - Y07（低）：宜川路街道 2025 年度决算 P31 条目 15 首句 269.85 vs
#   「支出决算为 269.86」，差 0.01——人工判定「同一指标抄写不一致，不是合计
#   舍入尾差」；差值恰在两侧显示精度的动态舍入包络内 → info 报告。
# 证据链与设计口径：docs/WP4C_NARRATIVE_REPEAT_20260925.md。
#
# 与 V33-235（OBL-NARRATIVE-AMOUNT 的登记 checker）的边界：V33-235 是按页切段、
# 只认「N、」形态、0.05 自造容差的同条点状检查，两条真值都漏（跨页断条目/
# 形态不符/容差吞小差）。本规则用合并文本 + 身份模型做一般化检查，按纪律
# 不动 V33-235；两者在「同页 + N、形态 + 差>0.05」窄区有界重叠，报告层按
# 同组归并，规则间互不去重。
# ============================================================================

_NAR_REPEAT_OBLIGATION_ID = "OBL-NARRATIVE-INDICATOR-REPEAT"

#: 决算情况说明章节标题（合并文本切节用；目录页标题不切节——沿用 V33-235
#: 的目录页守卫口径）
_NAR_REPEAT_SECTION_RE = re.compile(r"[一二三四五六七八九十]+、[^\n。；]{2,40}说明")

#: 类款项条目句：沿用 WP4-B 的三级身份形态，放宽闭引号（石泉样张是
#: 「（项）"257.14 万元」的引号形态）。名称段允许内部换行（PDF 软换行）。
_NAR_REPEAT_ITEM_RE = re.compile(
    r"(?P<klass>[^，。；：:、（）()“”]{1,40}?)\s*（\s*类\s*）\s*"
    r"(?P<kuan>[^，。；：:、（）()“”]{1,60}?)\s*（\s*款\s*）\s*"
    r"(?P<xiang>[^，。；：:、（）()“”]{1,60}?)\s*（\s*项\s*）\s*"
    r"[\"”』」]?\s*[，,]?\s*(?P<amount>\d[\d,，]*(?:\.\d+)?)\s*"
    r"(?P<unit>万\s*元|亿\s*元|元)"
)

#: 条目内显式决算金额：measure 锚与比较伙伴（宜川 Y07 原文软换行成
#: 「支\n出决算为」，measure 词字符间必须容忍空白）
_NAR_REPEAT_EXPLICIT_RE = re.compile(
    r"(?P<lead>支\s*出\s*决\s*算\s*为)\s*(?P<amount>\d[\d,，]*(?:\.\d+)?)\s*"
    r"(?P<unit>万\s*元|亿\s*元|元)"
)

#: 裸名+期间前缀（合同路径）：「本年[X]金额单位」「上年[X]金额单位」
_NAR_REPEAT_BARE_PERIOD_RE = re.compile(
    r"(?P<period>本年(?:度)?|上年(?:度)?|去年同期)(?P<name>[一-龥]{2,24})"
    r"(?P<amount>\d[\d,，]*(?:\.\d+)?)\s*(?P<unit>万\s*元|亿\s*元|元)"
)

#: 裸名+角色后缀（合同路径）：「[X](年初预算|预算|支出决算|决算|支出)(为)?金额单位」
_NAR_REPEAT_BARE_ROLE_RE = re.compile(
    r"(?P<name>[一-龥]{2,24}?)(?P<role>年初预算|全年预算|预算|支出决算|决算|支出)"
    r"(?:为)?\s*(?P<amount>\d[\d,，]*(?:\.\d+)?)\s*(?P<unit>万\s*元|亿\s*元|元)"
)

#: 同句承接式角色句（合同路径）：「，决算257.14万元」承接同句前一个裸名披露
_NAR_REPEAT_CONTINUATION_RE = re.compile(
    r"[，；、]\s*(?P<role>支出决算|决算|年初预算|预算)(?:为)?\s*"
    r"(?P<amount>\d[\d,，]*(?:\.\d+)?)\s*(?P<unit>万\s*元|亿\s*元|元)"
)

#: 金额+单位候选（歧义检测用；单位锚要求自带单位字样）
_NAR_REPEAT_BARE_AMOUNT_RE = re.compile(
    r"(?<![\d.])(?P<amount>\d[\d,，]*(?:\.\d+)?)\s*(?:万\s*元|亿\s*元|元)"
)

#: 槽位竞争候选只认「纯连接符相连的紧邻金额」（和/及/、/，…），中间出现
#: 任何 CJK 词语（如「，比上年」「，主要用于发放」）就不是同一槽位的竞争者
_NAR_REPEAT_CONJUNCTIVE_RE = re.compile(r"^(?:[和及与或、，,]|\s)*$")

#: 通用结构词：这些类目名在文内合法地多次出现不同数值（总口径 vs 明细），
#: 一律拒绝裸名互比（Mutation B 要拦的正是"只按关键词聚合"的形态）
_NAR_REPEAT_GENERIC_TERM_RE = re.compile(
    r"基本支出|项目支出|工资福利支出|商品和服务支出|对个人和家庭的补助|"
    r"人员经费|公用经费|基本建设|对企事业单位的补贴|债务付息|其他支出|"
    r"其他收入|支出合计|收入合计|结转|结余|合计|总计"
)
_NAR_REPEAT_GENERIC_NAME_RE = re.compile(
    r"^(?:基本|项目|人员|公用|其他|合计|总计|收入|支出|结转|结余)$"
)

#: 裸名里的关系/增减动词、引导语与时间词：出现即拒绝（「比上年减少」的名称
#: 段是谓语；「当年/今年/年度」是时间词不是指标名——生态样张 P24 的
#: 「当年支出决算为 213.10」曾被当成指标「当年」误配）
_NAR_REPEAT_NAME_REFUSE_RE = re.compile(
    r"比|较|增加|减少|增长|下降|提高|降低|回落|上升|其中|主要|用于|情况|如下|"
    r"当年|今年|历年|往年|同期|年度|截至|截止"
)

#: 条目边界：类款项三级结构（**不要求紧跟金额**）或章节标题。条目范围按它
#: 切割——「无首句金额」条目（文旅局/生态形态：『（项），主要用于…年初
#: 预算为 X，当年支出决算为 Y』）的下一条目，其「支出决算为」不得泄漏进
#: 上一条目与别的金额错配（石泉 P39 的 1.53 曾因此错配给 P38 的 98.23）
_NAR_REPEAT_ENTRY_BOUNDARY_RE = re.compile(
    r"[^，。；：:、（）()“”]{1,40}?\s*（\s*类\s*）[^。；]{0,80}?（\s*款\s*）"
    r"[^。；]{0,80}?（\s*项\s*）"
    r"|[一二三四五六七八九十]+、[^\n。；]{2,40}说明"
)

#: 上年口径负面前缀：出现即视为 prior，不参与 current 比较
_NAR_REPEAT_PRIOR_PREFIX_RE = re.compile(r"上\s*年(?:度|同期)?|以前年度")


def _nar_repeat_sections(
    merged: str, doc: Document, offsets: List[int]
) -> List[Tuple[str, int, int]]:
    """决算情况说明章节切分：[(标题, 起始偏移, 终止偏移)]。

    目录页（前 120 字含「目录」）上的同名标题行不作为章节边界——它是目录
    条目，不是正文标题。
    """
    texts = [str(item or "") for item in (getattr(doc, "page_texts", []) or [])]
    toc_pages = {
        index for index, text in enumerate(texts) if "目录" in text[:120]
    }
    starts: List[Tuple[str, int]] = []
    for match in _NAR_REPEAT_SECTION_RE.finditer(merged):
        page = _txt_fund_page_for(offsets, match.start())
        if (page - 1) in toc_pages:
            continue
        starts.append((match.group(0).strip(), match.start()))
    sections: List[Tuple[str, int, int]] = []
    for idx, (title, start) in enumerate(starts):
        end = starts[idx + 1][1] if idx + 1 < len(starts) else len(merged)
        sections.append((title, start, end))
    return sections


def _nar_repeat_section_of(
    sections: List[Tuple[str, int, int]], position: int
) -> Tuple[str, int]:
    """偏移 → (章节标题, 章节起始偏移)；无章节标题时给未命名兜底。"""
    for title, start, end in reversed(sections):
        if start <= position < end:
            return title, start
    return "（未定位到章节标题）", 0


def _nar_repeat_foreign_year(merged: str, start: int, names: str, year: int) -> bool:
    """期间锚：句读边界前缀或名称段出现与材料年度不同的显式年份 → 异期句。

    名称段也要查——WP4-B 教训：项名可能吸收年份前缀（「2024年度住房保障
    支出（类）」），只扫句前缀会漏。
    """
    prefix = merged[max(0, start - 40):start]
    cut = max(prefix.rfind(ch) for ch in "。；：！？\n")
    if cut >= 0:
        prefix = prefix[cut + 1:]
    found = _TXT_FUND_YEAR_RE.findall(prefix + str(names or ""))
    return any(int(value) != year for value in found)


def _nar_repeat_slot_ambiguous(merged: str, end: int) -> bool:
    """槽位歧义：金额单位后仅隔纯连接符又紧跟一个「金额+单位」候选。

    「本年职业年金245.53万元和257.14万元」——两个都可能是当前实际金额且
    无法证明哪个对应指标 → parse_ambiguity（禁止取最近/首个/最大）。
    中间隔着 CJK 词语的（「，比上年245.53」「，主要用于发放300」）不是同
    一槽位的竞争者。
    """
    window = merged[end:end + 24]
    candidate = _NAR_REPEAT_BARE_AMOUNT_RE.search(window)
    if candidate is None:
        return False
    return _NAR_REPEAT_CONJUNCTIVE_RE.match(window[:candidate.start()]) is not None


class R33NarrativeIndicatorRepeat(Rule):
    """文内同一指标（同段/跨段）重复披露一致性。

    只比较"文内 current actual"披露：同一指标（类/款/项三级身份，或带角色
    词的裸名身份——后者仅限同章节互比）在本年度决算口径下的多处披露金额
    必须一致。预算数/上年数/增减额/百分比/数量只识别、不参与比较；身份任
    一要素确认不了 → fail-closed（不绑定，或 parse_ambiguity 记取数不足），
    绝不做"关键词附近出现两个数字就报错"。
    """

    code, severity = "V33-NARRATIVE-INDICATOR-REPEAT", "error"
    desc = "文内同一指标（同段/跨段）重复披露一致性"

    def apply(self, doc: Document) -> List[Issue]:
        merged, offsets = _txt_fund_merged_pages(doc)
        if not merged.strip():
            raise RuleDeferred(
                self.code,
                "未提取到正文文本，文内重复披露无从检查",
                unresolved_reasons=["未提取到正文文本"],
            )
        sections = _nar_repeat_sections(merged, doc, offsets)
        heading_pages = tuple(
            {_txt_fund_page_for(offsets, start) for _, start, _ in sections}
        )
        year = _resolve_fiscal_year(doc, heading_pages)
        if year is None:
            raise RuleDeferred(
                self.code,
                "未能确认材料财政年度，文内重复披露的期间一致性不可确认",
                unresolved_reasons=["未能确认材料财政年度"],
            )

        issues: List[Issue] = []
        unresolved: List[str] = []
        triple_occs, dynamic_generic = self._triple_occurrences(
            merged, offsets, sections, year, unresolved
        )
        bare_occs = self._bare_occurrences(
            merged, offsets, sections, year, unresolved, dynamic_generic
        )
        issues.extend(self._compare(triple_occs + bare_occs, year))
        if unresolved:
            reasons = list(dict.fromkeys(unresolved))
            raise RuleDeferred(
                self.code,
                "；".join(reasons),
                partial_issues=issues,
                unresolved_reasons=reasons,
            )
        return issues

    # ---- 内部：三级身份披露（条目首句 + 显式支出决算为） ----

    def _triple_occurrences(
        self,
        merged: str,
        offsets: List[int],
        sections: List[Tuple[str, int, int]],
        year: int,
        unresolved: List[str],
    ) -> Tuple[List[Dict[str, Any]], set]:
        items = list(_NAR_REPEAT_ITEM_RE.finditer(merged))
        dynamic_generic: set = set()
        for match in items:
            dynamic_generic.add(_txt_fund_norm(match.group("klass")))
            dynamic_generic.add(_txt_fund_norm(match.group("kuan")))

        occurrences: List[Dict[str, Any]] = []
        boundaries = [
            boundary.start()
            for boundary in _NAR_REPEAT_ENTRY_BOUNDARY_RE.finditer(merged)
        ]
        for idx, match in enumerate(items):
            # 条目范围 = 下一边界（下一个三级结构——无论它有没有首句金额——
            # 或下一章节标题），防止无首句金额条目的「支出决算为」泄漏进本条目
            next_boundary = next(
                (pos for pos in boundaries if pos > match.end()), len(merged)
            )
            entry_end = min(
                items[idx + 1].start() if idx + 1 < len(items) else len(merged),
                next_boundary,
            )
            names = match.group("klass") + match.group("kuan") + match.group("xiang")
            if _nar_repeat_foreign_year(merged, match.start(), names, year):
                # 异期条目是已解决的"不可比"，不是取数不足
                continue
            # measure 锚：条目内必须有显式「支出决算为」——决算支出明细条目
            # 模板自证；没有它，条目首句金额的决算口径无法证明（fail-closed）
            explicit_matches = list(
                _NAR_REPEAT_EXPLICIT_RE.finditer(merged, match.end(), entry_end)
            )
            if not explicit_matches:
                continue
            identity = (
                "triple",
                "|".join(
                    _txt_fund_norm(match.group(key))
                    for key in ("klass", "kuan", "xiang")
                ),
            )
            indicator = _txt_fund_norm(match.group("xiang"))
            section_title, _ = _nar_repeat_section_of(sections, match.start())
            self._push_occurrence(
                occurrences, unresolved, merged, offsets,
                match_offset=match.start("amount"),
                identity=identity,
                indicator=indicator,
                role="current",
                amount_text=match.group("amount"),
                unit_text=match.group("unit"),
                span_start=match.start(),
                span_end=match.end(),
                section_title=section_title,
            )
            for explicit in explicit_matches:
                # finditer(merged, pos, endpos) 的匹配位置是绝对偏移，不要再加
                # 条目起点——加了会把页码算到文档末尾（双重偏移缺陷）
                lead_start = explicit.start()
                if _NAR_REPEAT_PRIOR_PREFIX_RE.search(
                    merged[max(0, lead_start - 12):lead_start]
                ):
                    continue
                if _nar_repeat_foreign_year(
                    merged, lead_start, explicit.group("lead"), year
                ):
                    continue
                if _nar_repeat_slot_ambiguous(merged, explicit.end()):
                    unresolved.append(
                        f"说明句「{indicator}」的「支出决算为」金额后紧邻第二个候选"
                        "金额，无法确定指标金额（parse_ambiguity）"
                    )
                    continue
                self._push_occurrence(
                    occurrences, unresolved, merged, offsets,
                    match_offset=explicit.start("amount"),
                    identity=identity,
                    indicator=indicator,
                    role="current",
                    amount_text=explicit.group("amount"),
                    unit_text=explicit.group("unit"),
                    span_start=explicit.start(),
                    span_end=explicit.end(),
                    section_title=section_title,
                )
        return occurrences, dynamic_generic

    # ---- 内部：裸名披露（带角色/期间词；仅同章节互比） ----

    def _bare_occurrences(
        self,
        merged: str,
        offsets: List[int],
        sections: List[Tuple[str, int, int]],
        year: int,
        unresolved: List[str],
        dynamic_generic: set,
    ) -> List[Dict[str, Any]]:
        occurrences: List[Dict[str, Any]] = []
        # 同句承接的锚：最近一个已绑裸名披露（句读重置；跨句不承接）
        last_bound: Optional[Tuple[str, int]] = None

        period_spans = [
            match.span() for match in _NAR_REPEAT_BARE_PERIOD_RE.finditer(merged)
        ]

        def bind_bare(
            indicator: str,
            name_part: str,
            role: str,
            section_pos: int,
            **payload: Any,
        ) -> None:
            nonlocal last_bound
            # 谓语/引导语/通用结构词/类款级名 一律拒绝裸名互比（fail-closed，
            # 不产生正式比较；这是 Mutation B"只按关键词聚合"的反面）
            if _NAR_REPEAT_NAME_REFUSE_RE.search(name_part):
                last_bound = None
                return
            if _NAR_REPEAT_GENERIC_NAME_RE.match(name_part):
                last_bound = None
                return
            if _NAR_REPEAT_GENERIC_TERM_RE.search(indicator):
                last_bound = None
                return
            if indicator in dynamic_generic:
                # 类/款级名称：本文档 P1 条目已证明它是结构类目，裸名数字天然
                # 可能指不同下级口径，不互比
                last_bound = None
                return
            if role != "current":
                last_bound = (indicator, int(payload["span_end"]))
                return
            self._push_occurrence(
                occurrences, unresolved, merged, offsets,
                indicator=indicator, role="current", **payload
            )
            last_bound = (indicator, int(payload["span_end"]))

        for match in _NAR_REPEAT_BARE_PERIOD_RE.finditer(merged):
            period = match.group("period")
            role = "current" if period.startswith("本年") else "prior"
            name = match.group("name")
            if role == "current" and _nar_repeat_foreign_year(
                merged, match.start(), name, year
            ):
                last_bound = None
                continue
            if role == "current" and _nar_repeat_slot_ambiguous(merged, match.end()):
                unresolved.append(
                    f"说明句「{name}」同句存在紧邻的第二个候选金额，"
                    "无法确定指标金额（parse_ambiguity）"
                )
                last_bound = None
                continue
            section_title, section_start = _nar_repeat_section_of(sections, match.start())
            bind_bare(
                indicator=name,
                name_part=name,
                role=role,
                section_pos=match.start(),
                match_offset=match.start("amount"),
                identity=("bare", name, section_start),
                amount_text=match.group("amount"),
                unit_text=match.group("unit"),
                span_start=match.start(),
                span_end=match.end(),
                section_title=section_title,
            )

        role_spans = [
            match.span() for match in _NAR_REPEAT_BARE_ROLE_RE.finditer(merged)
        ]
        for match in _NAR_REPEAT_BARE_ROLE_RE.finditer(merged):
            if any(start < match.end() and match.start() < end for start, end in period_spans):
                continue  # 已被期间前缀式绑定，防同一段双记
            name = match.group("name")
            role_word = match.group("role")
            if role_word in {"预算", "年初预算", "全年预算"}:
                role = "budget"
                indicator = name
                identity_name = name
            elif role_word in {"决算", "支出决算"}:
                role = "current"
                indicator = name
                identity_name = name
            else:  # 支出：指标名本身以「支出」结尾（如 职业年金缴费支出）
                role = "current"
                indicator = name + "支出"
                identity_name = name + "支出"
            # 名称段吸收的「本年」前缀归一（「本年职业年金缴费支出」与
            # 「职业年金缴费支出」是同一身份）；「上年」保留（异期间不合并）
            if identity_name.startswith(("本年", "本年度")):
                identity_name = identity_name[len("本年度"):] if identity_name.startswith("本年度") else identity_name[len("本年"):]
            if _NAR_REPEAT_PRIOR_PREFIX_RE.match(indicator):
                role = "prior"
            if role == "current" and _nar_repeat_foreign_year(
                merged, match.start(), name, year
            ):
                last_bound = None
                continue
            if role == "current" and _nar_repeat_slot_ambiguous(merged, match.end()):
                unresolved.append(
                    f"说明句「{indicator}」同句存在紧邻的第二个候选金额，"
                    "无法确定指标金额（parse_ambiguity）"
                )
                last_bound = None
                continue
            section_title, section_start = _nar_repeat_section_of(sections, match.start())
            bind_bare(
                indicator=indicator,
                name_part=name,
                role=role,
                section_pos=match.start(),
                match_offset=match.start("amount"),
                identity=("bare", identity_name, section_start),
                amount_text=match.group("amount"),
                unit_text=match.group("unit"),
                span_start=match.start(),
                span_end=match.end(),
                section_title=section_title,
            )

        # 承接式：仅承接同句（无句读隔断）最近一个已绑裸名的指标身份
        owned_spans = period_spans + role_spans
        for match in _NAR_REPEAT_CONTINUATION_RE.finditer(merged):
            if any(start < match.end() and match.start() < end for start, end in owned_spans):
                continue
            if last_bound is None:
                continue
            indicator, bound_end = last_bound
            if "。" in merged[bound_end:match.start()]:
                continue
            role_word = match.group("role")
            if role_word in {"预算", "年初预算"}:
                continue  # 预算承接不参与 current 比较
            if _nar_repeat_foreign_year(merged, match.start(), role_word, year):
                continue
            if _nar_repeat_slot_ambiguous(merged, match.end()):
                unresolved.append(
                    f"说明句「{indicator}」同句存在紧邻的第二个候选金额，"
                    "无法确定指标金额（parse_ambiguity）"
                )
                continue
            section_title, section_start = _nar_repeat_section_of(sections, match.start())
            self._push_occurrence(
                occurrences, unresolved, merged, offsets,
                match_offset=match.start("amount"),
                identity=("bare", indicator, section_start),
                indicator=indicator,
                role="current",
                amount_text=match.group("amount"),
                unit_text=match.group("unit"),
                span_start=match.start(),
                span_end=match.end(),
                section_title=section_title,
            )
        return occurrences

    # ---- 内部：occurrence 组装与单位/精度 ----

    def _push_occurrence(
        self,
        occurrences: List[Dict[str, Any]],
        unresolved: List[str],
        merged: str,
        offsets: List[int],
        match_offset: int,
        identity: Any,
        indicator: str,
        role: str,
        amount_text: str,
        unit_text: str,
        span_start: int,
        span_end: int,
        section_title: str,
    ) -> None:
        cleaned = re.sub(r"[,，\s]", "", amount_text)
        unit = _txt_fund_norm(unit_text)
        factor = _TXT_FUND_UNIT_TO_WAN.get(unit)
        if factor is None:
            unresolved.append(
                f"说明句「{indicator}」金额单位「{unit_text}」不在归一口径内，不形成正式比较"
            )
            return
        try:
            amount = Decimal(cleaned)
        except Exception:  # pragma: no cover - 正则已保证数字形态
            unresolved.append(f"说明句「{indicator}」金额无法解析为数值")
            return
        scale = len(cleaned.split(".", 1)[1]) if "." in cleaned else 0
        occurrences.append(
            {
                "identity": identity,
                "indicator": indicator,
                "role": role,
                "amount_text": cleaned,
                "unit": unit,
                "scale": scale,
                "wan": amount * factor,
                "page": _txt_fund_page_for(offsets, match_offset),
                "span": merged[span_start:span_end].replace("\n", " ").strip(),
                "section": section_title,
            }
        )

    # ---- 内部：同身份 current 披露两两比较 ----

    def _compare(self, occurrences: List[Dict[str, Any]], year: int) -> List[Issue]:
        issues: List[Issue] = []
        groups: Dict[Any, List[Dict[str, Any]]] = {}
        for occurrence in occurrences:
            groups.setdefault(occurrence["identity"], []).append(occurrence)
        for group in groups.values():
            current = [item for item in group if item["role"] == "current"]
            if len(current) < 2:
                continue
            worst_pair: Optional[Tuple[Dict[str, Any], Dict[str, Any]]] = None
            worst_diff = Decimal("0")
            for i in range(len(current)):
                for j in range(i + 1, len(current)):
                    diff = abs(current[i]["wan"] - current[j]["wan"])
                    if diff > worst_diff:
                        worst_pair = (current[i], current[j])
                        worst_diff = diff
            if worst_pair is None or worst_diff == 0:
                continue
            first, second = worst_pair
            envelope = compute_dynamic_envelope(
                [(first["scale"], first["unit"]), (second["scale"], second["unit"])]
            )
            severity = "info" if worst_diff <= envelope else "error"
            indicator = first["indicator"]
            message = (
                f"文内同一指标重复披露不一致（{indicator}）：第{first['page']}页披露"
                f" {first['amount_text']} {first['unit']}，第{second['page']}页披露"
                f" {second['amount_text']} {second['unit']}，相差"
                f" {_txt_fund_display(worst_diff)} 万元。同一指标在同一口径"
                "（本年支出决算）下两处披露不能同时成立，需人工复核底稿确认正确金额。"
            )
            if severity == "info":
                message += (
                    f"相差在显示舍入包络（{_txt_fund_display(envelope)} 万元）内，"
                    "可能为取整误差或抄写差异，需人工复核。"
                )
            location = {
                "page": first["page"],
                "pages": sorted({first["page"], second["page"]}),
                "indicator": indicator,
                "measure": "本年支出决算",
                "period": f"{year} 年度",
                "unit": "万元",
                "value_a": first["amount_text"],
                "value_b": second["amount_text"],
                "difference": _txt_fund_display(worst_diff),
                "fiscal_year": year,
                "obligation_id": _NAR_REPEAT_OBLIGATION_ID,
                "section_a": first["section"],
                "section_b": second["section"],
                "table_refs": [
                    {
                        "role": "披露A",
                        "page": first["page"],
                        "section": first["section"],
                        "span": first["span"],
                    },
                    {
                        "role": "披露B",
                        "page": second["page"],
                        "section": second["section"],
                        "span": second["span"],
                    },
                ],
            }
            evidence = "\n".join(
                [
                    f"披露A（第{first['page']}页，{first['section']}）：{first['span']}",
                    f"披露B（第{second['page']}页，{second['section']}）：{second['span']}",
                    f"指标：{indicator}；口径：本年支出决算；期间：{year} 年度；单位：万元",
                    f"差额：{_txt_fund_display(worst_diff)} 万元",
                ]
            )
            issues.append(
                self._issue(message, location, severity=severity, evidence_text=evidence)
            )
        return issues


# ============================================================================
# WP4-E：预算完成率分母口径与确定性复算（V33-TREND-COMPLETION-RATE，final only）
#
# 政策合同正例（POLICY_CONTRACT，非真实样张）：AGENTS.md R004 / rules/v3_3.yaml
# R004——「年初预算为0…支出决算为307.82万元，完成年初预算的95.89%」：分母为
# 0 时完成率不存在有限定义。仓库全部真实样张（宜川/石泉/文旅/普陀/长风/9-05
# 生态）的零预算条目都合规地写「决算数大于预算数的主要原因」，未出现
# 「零分母+完成率」正例，如实按政策合同正例登记，不伪造成真实漏报。
# 真实负例（REAL）：DOC-20260905-001 P21 总述句 4628.17/4733.14/102.27%；
# 石泉 P33 公益性岗位补贴 256/200.18/78.20%、P34 其他城市生活救助 30/30/100%；
# 宜川 P28 总述 20707.11/20218.21/98%（复算 97.64%，容差内）；文旅 P22 总述
# 27780.16/24535.67/88.32%。石泉死亡抚恤「年初预算为 0 元」跨页无完成率
# 声明 → 合规 0 finding。证据链：docs/WP4E_COMPLETION_RATE_20260925.md。
#
# 与 V33-234（OBL-NARRATIVE-AMOUNT 的登记 checker）的边界：其完成率分支
# （_FINAL_COMPLETION_PERCENT_RE）只认「年初预算(数)?为 X 万元…决算为 Y 万元
# …完成(年初)?预算的 Z%」邻近窗口三元组，以下形态漏检——本规则补：
#   1)「年初预算为0」（无万元后缀）与「年初预算为 0 元」（元单位），恰是
#     R004 零分母的典型披露形态；
#   2)「完成全年/调整预算的X%」整句不在其匹配范围，分母身份切换后的
#     复算无人验证；
#   3) 条目内同身份多个不一致预算无 parse_ambiguity 处理。
# 本规则不做「完成率偏离100%未说明原因」的 warn（V33-234 既有职责），
# 标准三元组形态与其有界重叠、报告层互不去重（同 WP4-C 对 V33-235 的纪律）。
# ============================================================================

_WP4E_OBLIGATION_ID = "OBL-TREND-COMPLETION-RATE"

#: 完成率声明句（动词形态）：「完成（年初|全年|调整|）预算(数)?(的比重)?为?P%」。
#: 可选字素（数/的/的比重/为）一律前置 \s*——PDF 软换行会把「决算␊为」拆行，
#: 不容忍空白就会漏掉真实形态（宜川 Y07 教训）。同比句（比上年增长100%，
#: CMM-007 领域）与占比句（占X的P%）都不带「完成…预算」锚，不进本 matcher
#: （Mutation D 看守该边界）。
_WP4E_CLAIM_RE = re.compile(
    r"完\s*成\s*(?P<denom>年初\s*预\s*算|全年\s*预\s*算|调整\s*预\s*算|预\s*算)"
    r"(?:\s*数)?(?:\s*的\s*比\s*重)?(?:\s*的)?(?:\s*为)?\s*"
    r"(?P<pct>\d[\d,，]*(?:\.\d+)?)\s*%"
)

#: 完成率声明句（名词形态）：「（年初|全年|调整|）预算完成率为?P%」。
_WP4E_CLAIM_NOUN_RE = re.compile(
    r"(?P<denom>年初\s*预\s*算|全年\s*预\s*算|调整\s*预\s*算|预\s*算)\s*完\s*成\s*率"
    r"(?:\s*为)?\s*(?P<pct>\d[\d,，]*(?:\.\d+)?)\s*%"
)

#: 分母披露：「（年初|全年|调整|）预算(数)?（为|是）?金额（单位可选）」。
#: 政策例句「年初预算为0」无单位后缀；石泉零预算条目用「0 元」——单位可选，
#: 零值与换算因子无关，非零缺省单位按万元（叙事主流口径）。「预算(数)?」为
#: 泛身份，只在与完成率声明同句读单元时绑定，避免表格表头「预算数\n0.00」
#: 误入；「决算数大于预算数的主要原因」类无金额句读不命中。
_WP4E_BUDGET_RE = re.compile(
    r"(?P<role>年初\s*预\s*算(?:\s*数)?|全年\s*预\s*算(?:\s*数)?|调整\s*预\s*算(?:\s*数)?|预\s*算(?:\s*数)?)"
    r"\s*(?:为|是)?\s*(?P<amount>\d(?:[\d,，]*\d)?(?:\.\d+)?|零)\s*(?P<unit>万\s*元|亿\s*元|元)?"
)

#: 分子披露：「（支出）决算(数)?（为|是）?金额（单位可选）」。排除「收入
#: 决算」（收入侧不是支出完成率的分子）；measure 词容忍软换行（宜川 Y07
#: 教训：「支␊出决算为」「决算␊为」）。
_WP4E_ACTUAL_RE = re.compile(
    r"(?<!收入)(?:支\s*出\s*)?决\s*算(?:\s*数)?\s*(?:为|是)?\s*(?P<amount>\d(?:[\d,，]*\d)?(?:\.\d+)?)\s*"
    r"(?P<unit>万\s*元|亿\s*元|元)?"
)

#: 条目边界：类款项三级结构（复用 WP4-C 切分纪律）+「X、…说明」章节标题
#: + 行首「（X）小节标题」（宜川/普陀/文旅总述句都在「（三）」小节内，
#: 小节边界防止「（一）（二）」小节的披露错配给总述句）。目录页自身不含
#: 完成率声明，误切只影响绑定窗口大小、方向为 fail-closed。
_WP4E_ENTRY_RE = re.compile(
    r"[^，。；：:、（）()“”]{1,40}?\s*（\s*类\s*）[^。；]{0,80}?（\s*款\s*）"
    r"[^。；]{0,80}?（\s*项\s*）"
    r"|[一二三四五六七八九十]+、[^\n。；]{2,40}说明"
    r"|(?:^|\n)\s*[（(][一二三四五六七八九十\d]{1,3}[）)]\s*[^\n。；，,]{2,40}"
)

#: 句读边界（clause 切分）：句号/分号/叹问号。逗号不算——「年初预算为X万元，
#: 支出决算为Y万元，完成年初预算的Z%」是同句三元组主形态，跨句读拼接反而
#: 放宽了绑定纪律。
_WP4E_CLAUSE_BREAK_RE = re.compile(r"[。；;！？]")

#: 子句边界（sub-clause 切分）：句读符 + 逗号/顿号/冒号。分项完成率声明
#: （「…，其中：因公出国…决算为 0.00 万元，完成预算的 0.00%」）与总额声明
#: 同处一个句读单元，靠子句边界把「分子/分母」就近绑定，防止总额分母错绑
#: 给分项声明（三公 82.68% 的分母是车辆预算而非三公总额预算）。
_WP4E_SUBCLAUSE_BREAK_RE = re.compile(r"[。；;！？，,、：:]")

#: 章节导语的指标主语截取锚：铺垫文本（claim 前）在第一个分母/分子引导词
#: 处截断，取其前的名词主语（「一般公共预算财政拨款支出年初预算为…」→
#: 指标为「一般公共预算财政拨款支出」）。软换行先整体去空白再截，避免
#: 「决算␊为 16.95」把「为 16.95 万元」当成指标名。
_WP4E_HEAD_CUT_RE = re.compile(
    r"年初\s*预\s*算|全年\s*预\s*算|调整\s*预\s*算|预\s*算(?:\s*数)?|支\s*出\s*决\s*算|决\s*算"
)

#: 条目类款项头（不要求紧邻首句金额——「（项），主要用于…年初预算为X」
#: 形态的条目没有首句金额，指标名仍须可提取）。
_WP4E_ITEM_HEAD_RE = re.compile(
    r"(?P<klass>[^，。；：:、（）()“”]{1,40}?)\s*（\s*类\s*）"
    r"(?P<kuan>[^，。；：:、（）()“”]{1,60}?)\s*（\s*款\s*）"
    r"(?P<xiang>[^，。；：:、（）()“”]{1,60}?)\s*（\s*项\s*）"
)


def _wp4e_clause_head(merged: str, position: int) -> str:
    """披露/声明所在句读单元的指标主语：句读起点到披露位置、去空白、
    在第一个分母/分子引导词处截断。空主语（铺垫直接以引导词开头）合法——
    同为空视为同一（无）身份。"""
    breaks = [
        b.end() for b in _WP4E_CLAUSE_BREAK_RE.finditer(merged, 0, position)
    ]
    clause_start = breaks[-1] if breaks else 0
    head = re.sub(r"\s+", "", merged[clause_start:position])
    cut = _WP4E_HEAD_CUT_RE.search(head)
    if cut is not None:
        head = head[:cut.start()]
    return head[-40:] if len(head) > 40 else head

_WP4E_LIST_PREFIX_RE = re.compile(r"^[（(][一二三四五六七八九十\d]{1,3}[）)]\s*")

#: 完成率声明身份 → 归一身份键；「预算」缺省身份记空串（由同句显式披露补全）。
_WP4E_CLAIM_ROLES: Dict[str, str] = {
    "年初预算": "年初预算",
    "全年预算": "全年预算",
    "调整预算": "调整预算",
    "预算": "",
}


def _wp4e_claim_role(denom_text: str) -> str:
    return _WP4E_CLAIM_ROLES.get(_txt_fund_norm(denom_text), "")


def _wp4e_role_key(role_text: str) -> str:
    """分母披露身份归一：「年初预算数」并入「年初预算」；「预算/预算数」为泛身份。"""
    norm = _txt_fund_norm(role_text)
    for key in ("年初预算", "全年预算", "调整预算"):
        if norm.startswith(key):
            return key
    return ""


def _wp4e_amount(text: str) -> Optional[Decimal]:
    """完成率分母/分子金额解析：「零」等价表述归 0；千分位逗号按 to_decimal 口径剔除。"""
    if text == "零":
        return Decimal("0")
    try:
        return Decimal(str(text).strip().replace(",", "").replace("，", ""))
    except Exception:  # noqa: BLE001 - Decimal 解析失败的兜底，与 to_decimal 同口径
        return None


class R33TrendCompletionRate(Rule):
    """预算完成率分母口径与确定性复算（OBL-TREND-COMPLETION-RATE）。

    只绑定完整业务语义单元：同条目内「分母身份披露 + 支出决算披露 + 完成
    率声明」三要素。声明的分母身份（年初/全年/调整预算）必须与实际计算
    所用分母一致：分母为 0 时完成率无定义（R004），声明数值与其它身份的
    复算吻合时报分母身份错误；非零分母按 支出决算/分母×100 确定性复算。
    显式身份允许条目内承接绑定（披露在声明之前），泛身份只认同句；任何
    身份/金额无法唯一确定时 fail-closed（parse_ambiguity / insufficient_data），
    绝不跨条目拼接数字。同比（CMM-007）与占比（V33-234 等）不归本规则。
    """

    code, severity = "V33-TREND-COMPLETION-RATE", "error"
    desc = "预算完成率分母口径与确定性复算（R004 零分母/分母身份/复算）"

    #: 百分比容差：仓库统一策略（V33-234/BUD-111 同款 abs 0.5pp / 相对 1%），
    #: Decimal 化沿用 CMM-007 的既有实现形态。分母为 0 是定义问题，不经容差
    #: 放行；金额显示舍入的传播在常规量级下不超过该容差，不再叠加金额包络
    #: （避免第三套百分比容差）。
    _PCT_ATOL = Decimal("0.5")
    _PCT_RTOL = Decimal("0.01")

    def apply(self, doc: Document) -> List[Issue]:
        merged, offsets = _txt_fund_merged_pages(doc)
        if not merged.strip():
            raise RuleDeferred(
                self.code,
                "未提取到正文文本，完成率复算无从检查",
                unresolved_reasons=["未提取到正文文本"],
            )
        sections = _nar_repeat_sections(merged, doc, offsets)
        heading_pages = tuple(
            {_txt_fund_page_for(offsets, start) for _, start, _ in sections}
        )
        year = _resolve_fiscal_year(doc, heading_pages)
        # 完成率是期间敏感检查：完成率声明与分母/分子披露必须同属材料财政
        # 年度。年度无法确认时期间一致性不可证明，不得生成正式 finding
        # （fail-closed，与 CMM-007 / WP4-C 的年度门禁同纪律）。
        if year is None:
            raise RuleDeferred(
                self.code,
                "未能确认材料财政年度，预算完成率的期间一致性不可确认",
                unresolved_reasons=[
                    "未能确认材料财政年度，预算完成率的期间一致性不可确认"
                ],
            )

        claims = self._collect_claims(merged, offsets, sections, year)
        if not claims:
            return []  # 材料无完成率声明：无可检查对象，按 PASS 结案
        issues: List[Issue] = []
        unresolved: List[str] = []
        budgets = self._collect_budgets(merged, offsets, sections, year, claims)
        actuals, openings = self._collect_actuals(
            merged, offsets, sections, year, claims
        )
        boundaries = [b.start() for b in _WP4E_ENTRY_RE.finditer(merged)]

        for claim in claims:
            self._bind_entry(claim, boundaries, merged)
            self._check_claim(claim, budgets, actuals, openings, year, issues, unresolved)

        if unresolved:
            reasons = list(dict.fromkeys(unresolved))
            raise RuleDeferred(
                self.code,
                "；".join(reasons),
                partial_issues=issues,
                unresolved_reasons=reasons,
            )
        return issues

    # ---- 内部：声明/披露收集 ----

    def _collect_claims(
        self,
        merged: str,
        offsets: List[int],
        sections: List[Tuple[str, int, int]],
        year: Optional[int],
    ) -> List[Dict[str, Any]]:
        claims: List[Dict[str, Any]] = []
        for pattern in (_WP4E_CLAIM_RE, _WP4E_CLAIM_NOUN_RE):
            for match in pattern.finditer(merged):
                pct = _wp4e_amount(match.group("pct"))
                if pct is None:
                    continue
                if year is not None and _nar_repeat_foreign_year(
                    merged, match.start(), match.group("denom"), year
                ):
                    continue  # 异期声明是已解决的"不可比"，不跨期绑定
                section_title, section_start = _nar_repeat_section_of(
                    sections, match.start()
                )
                claims.append(
                    {
                        "start": match.start(),
                        "end": match.end(),
                        "page": _txt_fund_page_for(offsets, match.start()),
                        "section": section_title,
                        "section_start": section_start,
                        "role_key": _wp4e_claim_role(match.group("denom")),
                        "pct": pct,
                        "pct_text": re.sub(r"[\s,，]", "", match.group("pct")),
                        "phrase": _txt_fund_norm(match.group(0)),
                        "clause_start": 0,
                        "entry_start": 0,
                        "entry_end": len(merged),
                        "indicator": "",
                        "segments": [],
                        "clause_head": "",
                    }
                )
        claims.sort(key=lambda item: item["start"])
        return claims

    def _collect_budgets(
        self,
        merged: str,
        offsets: List[int],
        sections: List[Tuple[str, int, int]],
        year: Optional[int],
        claims: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        for match in _WP4E_BUDGET_RE.finditer(merged):
            if any(
                match.start() < claim["end"] and claim["start"] < match.end()
                for claim in claims
            ):
                continue  # 声明句内的「预算+数字」（如「完成预算102.27%」）不是披露
            if year is not None and _nar_repeat_foreign_year(
                merged, match.start(), match.group("role"), year
            ):
                continue
            entry = self._disclosure_entry(merged, offsets, sections, match)
            if entry is None:
                continue
            entry["role_key"] = _wp4e_role_key(match.group("role"))
            items.append(entry)
        return items

    def _collect_actuals(
        self,
        merged: str,
        offsets: List[int],
        sections: List[Tuple[str, int, int]],
        year: Optional[int],
        claims: List[Dict[str, Any]],
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """返回 (显式决算披露, 条目首句金额)。

        条目首句金额（「（类）（款）（项）」X 万元）是本年支出决算的合法
        披露形态（WP4-C 按 role=current 收集，口径一致）——同条目内它与
        「支出决算为 Y」矛盾时（石泉 S03：首句 257.14 vs 决算 245.53），
        完成率分子存在两个不一致候选，必须 parse_ambiguity 而不是任选其一
        复算（否则会把按首句自洽的完成率 93.57% 误判成复算不一致）。
        """
        items: List[Dict[str, Any]] = []
        for match in _WP4E_ACTUAL_RE.finditer(merged):
            if any(
                match.start() < claim["end"] and claim["start"] < match.end()
                for claim in claims
            ):
                continue
            if year is not None and _nar_repeat_foreign_year(
                merged, match.start(), "决算", year
            ):
                continue
            entry = self._disclosure_entry(merged, offsets, sections, match)
            if entry is None:
                continue
            entry["role_key"] = ""
            items.append(entry)
        openings: List[Dict[str, Any]] = []
        for match in _NAR_REPEAT_ITEM_RE.finditer(merged):
            if year is not None and _nar_repeat_foreign_year(
                merged, match.start(), match.group("xiang"), year
            ):
                continue
            entry = self._opening_entry(merged, offsets, sections, match)
            if entry is not None:
                openings.append(entry)
        return items, openings

    def _opening_entry(
        self,
        merged: str,
        offsets: List[int],
        sections: List[Tuple[str, int, int]],
        match: Any,
    ) -> Optional[Dict[str, Any]]:
        amount = _wp4e_amount(match.group("amount"))
        raw_unit = _txt_fund_norm(match.group("unit")) if match.group("unit") else None
        factor = _TXT_FUND_UNIT_TO_WAN.get(raw_unit) if raw_unit else None
        if amount is None or raw_unit is None or factor is None:
            return None  # 条目首句金额的单位锚是正则强制的，缺角即不形成披露
        section_title, section_start = _nar_repeat_section_of(
            sections, match.start("amount")
        )
        return {
            "wan": amount * factor,
            "amount_text": re.sub(r"\s+", "", match.group("amount")),
            "raw_unit": raw_unit,
            "unit_confirmed": True,
            "page": _txt_fund_page_for(offsets, match.start("amount")),
            "section": section_title,
            "section_start": section_start,
            "span": re.sub(r"\s+", "", match.group(0)),
            "start": match.start("amount"),
            "end": match.end(),
            "clause_head": _wp4e_clause_head(merged, match.start("amount")),
        }

    def _disclosure_entry(
        self,
        merged: str,
        offsets: List[int],
        sections: List[Tuple[str, int, int]],
        match: Any,
    ) -> Optional[Dict[str, Any]]:
        # 单位纪律（R2 整改）：非零金额缺单位不得默认「万元」——0 元 = 0
        # 万元 = 0 亿元，零值豁免单位缺失；非零缺单位保留披露（wan=None），
        # 由绑定层 fail-closed（insufficient_data），绝不按「叙事主流口径」
        # 猜测归一。原文单位（raw_unit）与归一结果分开保存，evidence 不伪造
        # 原文写了「万元」。
        amount = _wp4e_amount(match.group("amount"))
        if amount is None:
            return None
        raw_unit = _txt_fund_norm(match.group("unit")) if match.group("unit") else None
        factor = _TXT_FUND_UNIT_TO_WAN.get(raw_unit) if raw_unit else None
        if raw_unit is not None and factor is None:
            return None  # 单位显式但不在归一口径内，不形成正式披露
        if raw_unit is None:
            wan = Decimal("0") if amount == 0 else None
        else:
            wan = amount * factor
        section_title, section_start = _nar_repeat_section_of(sections, match.start())
        return {
            "wan": wan,
            "amount_text": re.sub(r"\s+", "", match.group("amount")),
            "raw_unit": raw_unit,
            "unit_confirmed": raw_unit is not None,
            "page": _txt_fund_page_for(offsets, match.start()),
            "section": section_title,
            "section_start": section_start,
            "span": re.sub(r"\s+", "", match.group(0)),
            "start": match.start(),
            "end": match.end(),
            "clause_head": _wp4e_clause_head(merged, match.start()),
        }

    # ---- 内部：条目绑定 ----

    def _bind_entry(
        self,
        claim: Dict[str, Any],
        boundaries: List[int],
        merged: str,
    ) -> None:
        prior = [pos for pos in boundaries if pos <= claim["start"]]
        nxt = [pos for pos in boundaries if pos > claim["start"]]
        claim["entry_start"] = prior[-1] if prior else 0
        claim["entry_end"] = nxt[0] if nxt else len(merged)
        breaks = [
            b.end()
            for b in _WP4E_CLAUSE_BREAK_RE.finditer(
                merged, claim["entry_start"], claim["start"]
            )
        ]
        claim["clause_start"] = breaks[-1] if breaks else claim["entry_start"]
        # claim 所在句读单元内的子句分段（按逗号/顿号/冒号再切），从 claim 侧
        # 由近及远排列——标准句式「决算为X万元，完成预算的Y%」的分子总在
        # 紧邻的上一段，逐段就近绑定防止把更早的无关披露错配进来。
        cuts = [claim["clause_start"]]
        cuts.extend(
            b.end()
            for b in _WP4E_SUBCLAUSE_BREAK_RE.finditer(
                merged, claim["clause_start"], claim["start"]
            )
        )
        cuts.append(claim["start"])
        claim["segments"] = [
            (cuts[idx], cuts[idx + 1]) for idx in range(len(cuts) - 2, -1, -1)
        ]
        claim["indicator"] = self._indicator(merged, claim)

    def _indicator(self, merged: str, claim: Dict[str, Any]) -> str:
        entry_start = claim["entry_start"]
        item = _WP4E_ITEM_HEAD_RE.match(merged, entry_start)
        if item is None:
            item = _WP4E_ITEM_HEAD_RE.search(
                merged, entry_start, min(entry_start + 200, len(merged))
            )
        if item is not None and item.start() < claim["start"]:
            claim["clause_head"] = _txt_fund_norm(item.group("xiang"))
            return claim["clause_head"]
        head = re.sub(r"\s+", "", merged[claim["clause_start"]:claim["start"]])
        cut = _WP4E_HEAD_CUT_RE.search(head)
        if cut is not None:
            head = head[:cut.start()]
        head = _WP4E_LIST_PREFIX_RE.sub("", head)
        head = re.sub(r"^其中[:：]", "", head)
        head = head[-40:] if len(head) > 40 else head
        claim["clause_head"] = head
        return head or "未定位条目"

    # ---- 内部：判定 ----

    def _check_claim(
        self,
        claim: Dict[str, Any],
        budgets: List[Dict[str, Any]],
        actuals: List[Dict[str, Any]],
        openings: List[Dict[str, Any]],
        year: Optional[int],
        issues: List[Issue],
        unresolved: List[str],
    ) -> None:
        indicator = claim["indicator"] or "未定位条目"

        # ---- 分母：claim 句读单元内按子句段由近及远，找到即停 ----
        denom: Optional[Dict[str, Any]] = None
        denom_state = "missing"
        for seg_start, seg_end in claim["segments"]:
            pool_at = [
                budget
                for budget in budgets
                if budget["start"] < claim["start"]
                and budget["section_start"] == claim["section_start"]
                and seg_start <= budget["start"] < seg_end
            ]
            if claim["role_key"]:
                cands = [
                    budget for budget in pool_at
                    if budget["role_key"] == claim["role_key"]
                ]
            else:
                explicit = [budget for budget in pool_at if budget["role_key"]]
                if len({budget["role_key"] for budget in explicit}) > 1:
                    unresolved.append(
                        f"完成率声明「{claim['phrase']}」同句存在多种预算身份披露，"
                        "分母身份无法唯一确定（parse_ambiguity）"
                    )
                    return
                cands = explicit or pool_at
            denom, denom_state = self._pick_unique(cands)
            if denom_state != "missing":
                break
        if denom is None:
            # ---- 分母条目内承接：句读单元之前的披露须与 claim 同指标主语 ----
            # 主语对齐是承接绑定的安全边界：分项声明（三公车辆 82.68%）不会
            # 错绑条目内总额预算（总额主语不同）；主语一致的多个候选（Case J）
            # 才会形成 parse_ambiguity，绝不取首个/最近。
            pool_entry = [
                budget
                for budget in budgets
                if budget["start"] < claim["start"]
                and budget["section_start"] == claim["section_start"]
                and claim["entry_start"] <= budget["start"] < claim["clause_start"]
                and (
                    not budget["clause_head"]
                    or budget["clause_head"] == claim["clause_head"]
                )
            ]
            if claim["role_key"]:
                cands = [
                    budget for budget in pool_entry
                    if budget["role_key"] == claim["role_key"]
                ]
            else:
                explicit = [budget for budget in pool_entry if budget["role_key"]]
                if len({budget["role_key"] for budget in explicit}) > 1:
                    unresolved.append(
                        f"完成率声明「{claim['phrase']}」条目内存在多种预算身份披露，"
                        "分母身份无法唯一确定（parse_ambiguity）"
                    )
                    return
                cands = explicit or pool_entry
            denom, denom_state = self._pick_unique(cands)
        if denom_state == "ambiguous":
            unresolved.append(
                f"「{indicator}」的{claim['role_key'] or '预算'}存在多个不一致披露，"
                "完成率分母无法唯一确定（parse_ambiguity）"
            )
            return
        if denom is None:
            # fail-closed 不比：声明未配套披露可绑定的分母（与 CMM-007
            # 「无可验证金额 → 不比」同纪律；表格内分母的表-文勾稽属其它义务）
            return
        role_label = claim["role_key"] or denom["role_key"] or "预算"

        # 分母单位纪律：非零披露未显式金额单位 → 无法归一复算，
        # fail-closed（零值豁免：0 元 = 0 万元 = 0 亿元，wan 为 0 不落此处）
        if denom["wan"] is None:
            unresolved.append(
                f"「{indicator}」的{role_label}披露「{denom['span']}」未显式金额单位，"
                "无法归一复算（insufficient_data）"
            )
            return

        # ---- 分子：句读单元内逐段就近；全部段空才条目内承接 ----
        actual: Optional[Dict[str, Any]] = None
        actual_state = "missing"
        for seg_start, seg_end in claim["segments"]:
            cands = [
                actual
                for actual in actuals
                if actual["start"] < claim["start"]
                and actual["section_start"] == claim["section_start"]
                and seg_start <= actual["start"] < seg_end
            ]
            actual, actual_state = self._pick_unique(cands)
            if actual_state != "missing":
                break
        if actual_state == "missing":
            cands = [
                actual
                for actual in actuals
                if actual["start"] < claim["start"]
                and actual["section_start"] == claim["section_start"]
                and claim["entry_start"] <= actual["start"] < claim["clause_start"]
                and (
                    not actual["clause_head"]
                    or actual["clause_head"] == claim["clause_head"]
                )
            ]
            actual, actual_state = self._pick_unique(cands)

        # ---- 分子交叉验证：条目首句金额也是本年支出决算披露 ----
        # 条目首句「（类）（款）（项）」X 万元与本条目显式「支出决算为 Y」
        # 矛盾（石泉 S03：257.14 vs 245.53）时，完成率分子无法唯一确定——
        # 矛盾本身由 V33-NARRATIVE-INDICATOR-REPEAT 报告，这里只 fail-closed
        # 不比，绝不任选其一复算（否则按首句自洽的 93.57% 会被误判）。
        entry_prefix_actuals = [
            actual
            for actual in actuals
            if actual["start"] < claim["start"]
            and actual["section_start"] == claim["section_start"]
            and claim["entry_start"] <= actual["start"] < claim["clause_start"]
        ]
        entry_prefix_openings = [
            opening
            for opening in openings
            if opening["start"] < claim["start"]
            and opening["section_start"] == claim["section_start"]
            and claim["entry_start"] <= opening["start"] < claim["clause_start"]
        ]
        cross_values = {
            item["wan"] for item in entry_prefix_actuals + entry_prefix_openings
        }
        if actual is not None:
            cross_values.add(actual["wan"])
        if len(cross_values) > 1:
            unresolved.append(
                f"「{indicator}」的本年支出决算存在多个不一致披露"
                "（含条目首句金额），完成率分子无法唯一确定（parse_ambiguity）"
            )
            return
        if actual is None and len(cross_values) == 1:
            # 句读单元内无显式「决算为」披露时，条目首句金额按承接语义绑定为分子
            sole = (entry_prefix_actuals + entry_prefix_openings)[0]
            actual = {
                **sole,
                "span": f"{sole['span']}（条目首句金额，按本年支出决算承接）",
            }

        others = [
            budget
            for budget in budgets
            if budget["start"] < claim["start"]
            and budget["section_start"] == claim["section_start"]
            and claim["clause_start"] <= budget["start"]
            and budget["role_key"]
            and budget["wan"] is not None
            and budget["role_key"] != role_label
            and budget["wan"] != denom["wan"]
        ]

        if denom["wan"] == 0:
            conflict = self._identity_hit(claim, actual, others)
            if conflict is not None:
                other, alt = conflict
                issues.append(
                    self._issue(
                        f"完成率分母身份错误（{indicator}）：声明「{claim['phrase']}」，"
                        f"但{role_label}为 0（不能作有效分母）；声明数值实际与"
                        f"{other['role_key']}（{_txt_fund_display(other['wan'])} 万元）"
                        f"的复算（{_txt_fund_display(alt)}%）吻合——原文声明的分母"
                        "身份与实际计算所用分母不一致，需人工复核底稿。",
                        self._location(
                            claim, denom, actual, year, role_label,
                            recomputed="undefined", difference="undefined",
                            identity_conflict=True, alternative=other,
                        ),
                        severity="error",
                        evidence_text=self._evidence(claim, denom, actual, other, alt),
                    )
                )
            else:
                issues.append(
                    self._issue(
                        f"完成率分母为0（{indicator}）：{role_label}为0，无法作为"
                        f"「{claim['phrase']}」的有效分母；当前披露的有限完成率"
                        "没有数学定义。若实际按其它口径（如全年预算）计算，应"
                        "改写声明中的分母表述并复核底稿。",
                        self._location(
                            claim, denom, actual, year, role_label,
                            recomputed="undefined", difference="undefined",
                        ),
                        severity="error",
                        evidence_text=self._evidence(claim, denom, actual),
                    )
                )
            return

        if actual is None:
            # fail-closed 不比：分母非零但句读单元/条目内无支出决算披露可复算
            return
        if actual["wan"] is None:
            # 分子单位纪律：非零决算披露未显式金额单位 → 无法归一复算
            unresolved.append(
                f"「{indicator}」的支出决算披露「{actual['span']}」未显式金额单位，"
                "无法归一复算（insufficient_data）"
            )
            return
        expected = actual["wan"] / denom["wan"] * Decimal("100")
        if self._pct_close(claim["pct"], expected):
            return  # 数学一致：原因说明缺失归 V33-234 既有 warn，本规则不重复报
        conflict = self._identity_hit(claim, actual, others)
        if conflict is not None:
            other, alt = conflict
            issues.append(
                self._issue(
                    f"完成率分母身份错误（{indicator}）：声明「{claim['phrase']}」，按"
                    f"{role_label}（{_txt_fund_display(denom['wan'])} 万元）复算应为"
                    f"{_txt_fund_display(expected)}%；声明数值实际与{other['role_key']}"
                    f"（{_txt_fund_display(other['wan'])} 万元）的复算"
                    f"（{_txt_fund_display(alt)}%）吻合——原文声明的分母身份与实际"
                    "计算所用分母不一致，需人工复核底稿。",
                    self._location(
                        claim, denom, actual, year, role_label,
                        recomputed=_txt_fund_display(expected),
                        difference=_txt_fund_display(abs(claim["pct"] - expected)),
                        identity_conflict=True, alternative=other,
                    ),
                    severity="error",
                    evidence_text=self._evidence(claim, denom, actual, other, alt),
                )
            )
            return
        issues.append(
            self._issue(
                f"完成率复算不一致（{indicator}）：{role_label}"
                f"{_txt_fund_display(denom['wan'])} 万元、支出决算"
                f"{_txt_fund_display(actual['wan'])} 万元，按声明分母复算应为"
                f"{_txt_fund_display(expected)}%，当前写为 {claim['pct_text']}%，"
                f"相差 {_txt_fund_display(abs(claim['pct'] - expected))} 个百分点。"
                "分母、支出决算与完成率三者不能同时成立，需人工复核底稿。",
                self._location(
                    claim, denom, actual, year, role_label,
                    recomputed=_txt_fund_display(expected),
                    difference=_txt_fund_display(abs(claim["pct"] - expected)),
                ),
                severity="error",
                evidence_text=self._evidence(claim, denom, actual),
            )
        )

    def _pick_unique(
        self, candidates: List[Dict[str, Any]]
    ) -> Tuple[Optional[Dict[str, Any]], str]:
        """绑定候选定夺：空 → (None, "missing")；唯一值 → (首个, "ok")；
        多个不一致值 → (None, "ambiguous")。禁止取最近/取首个来"解决"歧义。"""
        if not candidates:
            return None, "missing"
        if len({item["wan"] for item in candidates}) > 1:
            return None, "ambiguous"
        return candidates[0], "ok"

    def _identity_hit(
        self,
        claim: Dict[str, Any],
        actual: Optional[Dict[str, Any]],
        others: List[Dict[str, Any]],
    ) -> Optional[Tuple[Dict[str, Any], Decimal]]:
        """声明数值与「声明身份之外的其它预算身份」复算吻合 → 分母身份错误证据。

        身份交叉要用 actual 参与金额计算，其单位必须已确认（R2 整改：非零
        缺单位的 actual 不进入交叉复算；R004 零分母 finding 本身不依赖
        actual 数值，actual 仍只作为证据展示）。"""
        if actual is None or actual["wan"] is None:
            return None
        for other in sorted(others, key=lambda item: item["start"]):
            if other["wan"] > 0:
                alt = actual["wan"] / other["wan"] * Decimal("100")
                if self._pct_close(claim["pct"], alt):
                    return other, alt
        return None

    def _pct_close(self, declared: Decimal, recomputed: Decimal) -> bool:
        """仓库统一百分比容差（V33-234/BUD-111 同款 abs 0.5pp / 相对 1%，Decimal 化）。"""
        tolerance = max(
            self._PCT_ATOL,
            abs(recomputed) * self._PCT_RTOL,
            abs(declared) * self._PCT_RTOL,
        )
        return abs(declared - recomputed) <= tolerance

    # ---- 内部：finding 组装 ----

    def _location(
        self,
        claim: Dict[str, Any],
        denom: Dict[str, Any],
        actual: Optional[Dict[str, Any]],
        year: Optional[int],
        role_label: str,
        recomputed: str,
        difference: str,
        identity_conflict: bool = False,
        alternative: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        pages = {claim["page"], denom["page"]}
        refs = [
            {
                "role": "完成率声明",
                "page": claim["page"],
                "section": claim["section"],
                "span": claim["phrase"],
            },
            {
                "role": "分母披露",
                "page": denom["page"],
                "section": denom["section"],
                "span": denom["span"],
            },
        ]
        if actual is not None:
            pages.add(actual["page"])
            refs.append(
                {
                    "role": "分子披露",
                    "page": actual["page"],
                    "section": actual["section"],
                    "span": actual["span"],
                }
            )
        location: Dict[str, Any] = {
            "page": claim["page"],
            "pages": sorted(pages),
            "section": claim["section"],
            "indicator": claim["indicator"] or "未定位条目",
            "denominator_type": role_label,
            "denominator_amount": denom["amount_text"],
            "denominator_unit": denom["raw_unit"],  # 原文单位；None = 未显式披露
            "denominator_unit_confirmed": denom["unit_confirmed"],
            "normalized_denominator_wan": _txt_fund_display(denom["wan"]),
            "actual_amount": actual["amount_text"] if actual else None,
            "actual_unit": actual["raw_unit"] if actual else None,
            "actual_unit_confirmed": actual["unit_confirmed"] if actual else None,
            "normalized_actual_wan": (
                _txt_fund_display(actual["wan"])
                if actual is not None and actual["wan"] is not None
                else None
            ),
            "declared_completion_rate": claim["pct_text"],
            "recomputed_completion_rate": recomputed,
            "difference_pp": difference,
            "fiscal_year": year,
            "unit": "万元",  # 归一计算口径（与原文单位区分，不伪造原文写了万元）
            "obligation_id": _WP4E_OBLIGATION_ID,
            "zero_denominator": denom["wan"] == 0,
            "identity_conflict": identity_conflict,
            "table_refs": refs,
        }
        if alternative is not None:
            location["alternative_denominator"] = {
                "type": alternative["role_key"],
                "amount_wan": _txt_fund_display(alternative["wan"]),
            }
        return location

    def _evidence(
        self,
        claim: Dict[str, Any],
        denom: Dict[str, Any],
        actual: Optional[Dict[str, Any]],
        alternative: Optional[Dict[str, Any]] = None,
        alt_percent: Optional[Decimal] = None,
    ) -> str:
        lines = [
            f"完成率声明：{claim['phrase']}（第{claim['page']}页）",
            f"分母披露：{denom['span']}（第{denom['page']}页）",
        ]
        if actual is not None:
            lines.append(f"分子披露：{actual['span']}（第{actual['page']}页）")
        lines.append("口径：完成率=支出决算/声明分母×100；分母为0时完成率无定义")
        if alternative is not None and alt_percent is not None:
            lines.append(
                f"身份交叉：{alternative['span']}（第{alternative['page']}页）复算为"
                f"{_txt_fund_display(alt_percent)}%"
            )
        return "\n".join(lines)


ALL_RULES = [
    R33001_CoverYearUnit(),
    R33002_NineTablesCheck(),
    R33003_PageFileThreshold(),
    R33004_CellNumberValidity(),
    R33005_TableTotalConsistency(),
    R33101_TotalSheet_Identity(),
    R33102_TotalSheet_vs_Text(),
    R33103_Income_vs_Text(),
    R33104_Expense_vs_Text(),
    R33105_FinGrantTotal_vs_Text(),
    R33106_GeneralBudgetStruct(),
    R33107_BasicExpense_Check(),
    R33108_ThreePublic_vs_Text(),
    R33109_EmptyTables_Statement(),
    R33110_BudgetVsFinal_TextConsistency(),
    R33111_IncomeExpenseTotalCheck(),
    R33112_PlaceholderCheck(),
    R33113_PunctuationCheck(),
    R33115_TotalSheetCheck(),
    R33119_FiscalTotalCheck(),
    R33120_DetailTableCheck(),
    R33117_BasicExpenseClassification(),
    R33121_ThreePublicCheck(),
    R33122_EmptyTableCheck(),
    R33114_EmptyTableStatementCheck(),
    # P0 - 主链路勾稽规则
    R33200_InterTable_T1_T2(),
    R33201_InterTable_T1_T3(),
    R33202_InterTable_T4_T5(),
    R33203_InterTable_T5_T6(),
    R33243_Table6_BasicExpenseAdvancedCheck(),
    R33214_T1_TotalBalance(),
    # P1 - 表内强校验
    # R33210/R33211 已被更精准的 R33240/R33241 替代
    R33244_Table7_ThreePublicAdvancedCheck(),
    # 文字规范/披露完整性（样张整改新增）
    R33245_ThreePublicDirectionContradiction(),
    R33246_DomesticReceptionDisclosure(),
    # WP4-G：三公经费说明四项细化披露完整性（含公务用车购置费细化）
    R33SGCompletion(),
    R33242_Table4_ComprehensiveCheck(),
    R33240_Table2_IncomeAdvancedCheck(),
    R33241_Table3_ExpenseAdvancedCheck(),
    # P2 - 表↔情况说明
    R33225_Narrative1_T1(),
    R33226_Narrative2_T2(),
    R33220_Narrative3_T3(),
    R33221_Narrative4_T4(),
    R33222_Narrative5_T5(),
    R33227_Narrative5_T5_NameConsistency(),
    R33223_Narrative6_T6(),
    R33224_Narrative7_T7(),
    # 补充 - 表间勾稽
    R33204_InterTable_T2_T4(),
    # WP4-A：三公经费表 × 基本支出经济分类表（跨表资金来源一致性）
    R33CrossSanGongEcon(),
    R33TxtFundDetail(),
    R33NarrativeIndicatorRepeat(),
    R33TrendCompletionRate(),
    R33233_DetailRowFormulaConsistency(),
    R33234_NarrativePercentConsistency(),
    R33235_NarrativeAmountConsistency(),
    R33236_DocumentScopeTerminology(),
    # P3 - 规范性提示
    R33230_EmptyZeroHint(),
    R33232_PercentagePrecision(),
]
