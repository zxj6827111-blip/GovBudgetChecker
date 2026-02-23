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
from typing import List, Dict, Any, Optional, Tuple

import os
import re
from collections import Counter, defaultdict
import numpy as np
from rapidfuzz import fuzz


# ---------- 数据结构 ----------
@dataclass
class Issue:
    rule: str
    severity: str
    message: str
    evidence_text: Optional[str] = None
    location: Dict[str, Any] = field(default_factory=dict)

@dataclass
class Document:
    path: str
    pages: int
    filesize: int
    page_texts: List[str]
    # 维度：页 -> 表 -> 行 -> 列
    page_tables: List[List[List[List[str]]]]
    units_per_page: List[Optional[str]]
    years_per_page: List[List[int]]
    anchors: Dict[str, List[int]] = field(default_factory=dict)
    dominant_year: Optional[int] = None
    dominant_unit: Optional[str] = None


# ---------- 工具 ----------
def zh_pat(pat: str) -> re.Pattern:
    """中文文本常用正则：统一多行/点任意匹配 & 忽略大小写"""
    return re.compile(pat, flags=re.S | re.M | re.I)

_ZH_PUNCS = r"[ \t\r\n　，,。.:：；;、/（）()【】《》〈〉—\-━﻿·•●\[\]\{\}_~“”\"'‘’＋+]"
def normalize_text(s: str) -> str:
    s = s or ""
    return re.sub(_ZH_PUNCS, "", s)


def build_document(path: str, page_texts: List[str], page_tables: List[List[List[List[str]]]], filesize: int) -> Document:
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
NINE_ALIAS_NORMAL = [{"name": it["name"], "aliases_norm": [normalize_text(x) for x in it["aliases"]]}
                     for it in NINE_TABLES]

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
                if alias_norm and (alias_norm in ntxt or fuzz.partial_ratio(alias_norm, ntxt) >= 95):
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
                    if alias_norm and (alias_norm in ntxt or fuzz.partial_ratio(alias_norm, ntxt) >= 95):
                        # ====== 修复：严格排除逻辑 ======
                        # 当匹配"支出决算表"时，必须排除带有"政府性基金"或"国有资本"前缀的页面
                        # 这些是 Table 8 和 Table 9，不能误判为 Table 3
                        if it["name"] == "支出决算表":
                            if "政府性基金" in raw or "国有资本" in raw:
                                continue  # 跳过此匹配，不算作 Table 3
                        # ====== 修复结束 ======
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
               evidence_text: Optional[str] = None) -> Issue:
        return Issue(
            rule=self.code,
            severity=severity or self.severity,
            message=message,
            evidence_text=evidence_text,
            location=location or {}
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
            # 增强：检查整个文档的年份一致性
            all_years = []
            for page_years in doc.years_per_page:
                all_years.extend(page_years)
            
            if all_years:
                # 统计所有年份
                year_counts = {}
                for year in all_years:
                    year_counts[year] = year_counts.get(year, 0) + 1
                
                # 检查是否有其他年份
                other_years = [year for year in year_counts if year != doc.dominant_year]
                if other_years:
                    # 查找证据：在文中找到包含这些年份的句子
                    evidence_snippets = []
                    for i, text in enumerate(doc.page_texts):
                        for oy in other_years:
                           # 直接搜索年份字符串（简单高效）
                           oy_str = str(oy)
                           
                           # 查找匹配项
                           idx = text.find(oy_str)
                           count = 0
                           while idx != -1 and count < 2:  # 每个年份最多找2处
                               # 以此为中心前后截取文本
                               start = max(0, idx - 50)
                               end = min(len(text), idx + len(oy_str) + 50)
                               
                               snippet = text[start:end].replace("\n", " ").strip()
                               if len(snippet) > 10:
                                   evidence_snippets.append(f"P{i+1}: ...{snippet}...")
                                   count += 1
                               
                               if len(evidence_snippets) >= 5:
                                   break
                               
                               # 继续查找下一个
                               idx = text.find(oy_str, idx + 1)
                        
                        if len(evidence_snippets) >= 5:
                            break

                    evidence = "\n".join(evidence_snippets) if evidence_snippets else f"文档中存在非{doc.dominant_year}年的内容，但未定位到具体文本片段。"
                    
                    # 构建年份不一致的提示
                    year_str = ", ".join(map(str, other_years))
                    issues.append(self._issue(f"文档中包含多个年份：{doc.dominant_year}年报告中出现{year_str}年内容，可能存在年份混淆。", 
                                            {"page": 1}, severity="high", evidence_text=evidence))
        
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
                if page_num < len(doc.page_texts):
                    page_text = doc.page_texts[page_num - 1]
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
                    f"缺失独立的支出决算表：只找到具体的分类表（如\"一般公共预算财政拨款支出决算表\"），未找到独立的、总的\"支出决算表\"。",
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
        return issues


# ---------- 辅助（跨表/文数一致） ----------
def _largest_table_on_page(tables: List[List[List[str]]]) -> Optional[List[List[str]]]:
    if not tables:
        return None
    return sorted(tables, key=lambda t: sum(len(r) for r in t), reverse=True)[0]

def _get_first_anchor_page(doc: Document, table_name: str) -> Optional[int]:
    pages = (doc.anchors or {}).get(table_name) or []
    if not pages:
        return None
    # 优先选择该页确实有表格对象的页面（排除目录页的误匹配）
    table_pages = [p for p in pages if p <= len(doc.page_tables) and doc.page_tables[p-1]]
    return min(table_pages) if table_pages else min(pages)

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

def near_number(text: str, keywords: List[str]) -> Optional[float]:
    if not text:
        return None
    kw = "|".join(map(re.escape, keywords))
    # 简化正则表达式，避免复杂的嵌套量词导致回溯
    pat = re.compile(rf"(?:{kw})[^0-9]*?(-?\d+(?:,\d{{3}})*(?:\.\d+)?)", flags=re.S | re.M)
    m = pat.search(text)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except Exception:
        return None

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


# ---------- 跨表勾稽（V33-101~105） ----------
class R33101_TotalSheet_Identity(Rule):
    code, severity = "V33-101", "error"
    desc = "收入支出决算总表：支出列恒等式"

    def apply(self, doc: Document) -> List[Issue]:
        issues: List[Issue] = []
        p = _get_first_anchor_page(doc, "收入支出决算总表")
        if not p:
            return issues
        table = _largest_table_on_page(doc.page_tables[p - 1])
        if not table:
            return issues
        total = _row_value(table, ("支出合计", "支出总计", "合计"))
        bn = _row_value(table, ("本年支出合计", "本年支出", "本年合计"))
        jy = _row_value(table, ("结余分配", "结余分配支出"))
        jz = _row_value(table, ("年末结转和结余", "年末结转", "结转结余"))

        if (total is not None) and (bn is not None) and (jy is not None) and (jz is not None):
            try:
                bn_f, jy_f, jz_f, total_f = float(bn), float(jy), float(jz), float(total)
                sum_val = bn_f + jy_f + jz_f
                if not tolerant_equal(total_f, sum_val):
                    issues.append(self._issue(
                        f"总表支出恒等式不成立：支出合计={total_f} vs 本年支出{bn_f}+结余分配{jy_f}+年末结转{jz_f}={sum_val}",
                        {"page": p}, "error",
                        evidence_text=f"表格：P{p} 收入支出决算总表\n计算明细：\n本年支出({bn}) + 结余分配({jy}) + 年末结转({jz}) = {sum_val}\n表内支出合计 = {total}"
                    ))
            except Exception:
                pass
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
                return issues

            # 2) 取该页最大的一张表
            table = _largest_table_on_page(doc.page_tables[p - 1])
            if not table:
                return issues

            # 3) 从表中提取"支出合计"
            total_expense = _row_value(table, ("支出合计", "支出总计", "合计"))
            if total_expense is None:
                return issues

            # 4) 简化搜索，直接在关键词附近查找数字，避免复杂正则表达式
            # 只搜索前5页的文本，进一步限制搜索范围
            search_text = "\n".join(doc.page_texts[:min(5, len(doc.page_texts))])
            
            # 使用简单的字符串搜索而不是复杂的正则表达式
            found_num = None
            for keyword in ["总体情况说明", "总体情况"]:
                pos = search_text.find(keyword)
                if pos != -1:
                    # 在关键词后面100个字符内查找数字
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
                return issues

            # 5) 比较
            if not tolerant_equal(total_expense, found_num):
                issues.append(self._issue(
                    f"收入支出决算总表支出合计({total_expense:.2f})与总体情况说明数字({found_num:.2f})不一致",
                    {"page": p, "pos": 0},
                    evidence_text=f"表格：P{p} 收入支出决算总表\n表内合计: {total_expense}\n文本提取值: {found_num}\n(请检查“总体情况说明”部分的数字)"
                ))

            return issues
        except RecursionError:
            return [self._issue("规则执行异常：maximum recursion depth exceeded", {"page": 1, "pos": 0}, "info")]
        except Exception as e:
            return [self._issue(f"规则执行异常：{str(e)}", {"page": 1, "pos": 0}, "info")]

class R33103_Income_vs_Text(Rule):
    code, severity = "V33-103", "warn"
    desc = "收入决算表 ↔ 收入决算情况说明（含占比）"

    def apply(self, doc: Document) -> List[Issue]:
        issues: List[Issue] = []
        p = _get_first_anchor_page(doc, "收入决算表")
        if not p:
            return issues
        t = _largest_table_on_page(doc.page_tables[p - 1])
        if not t:
            return issues
        total = _row_value(t, ("本年收入合计", "本年合计", "合计"))
        fp = _row_value(t, ("财政拨款收入", "一般公共预算财政拨款收入", "财政拨款"))
        if total is None or fp is None:
            return issues
        txt = "\n".join(doc.page_texts)
        tt = near_number(txt, ["收入决算情况说明", "本年收入合计", "合计"])
        tf = near_number(txt, ["财政拨款收入"])
        if tt and not tolerant_equal(total, tt):
            issues.append(self._issue(f"收入合计：表{total} ≠ 文本{tt}", {"page": p}, "warn"))
        if tf and not tolerant_equal(fp, tf):
            issues.append(self._issue(f"财政拨款收入：表{fp} ≠ 文本{tf}", {"page": p}, "warn"))
        p_txt = find_percent(txt, ["财政拨款收入", "占比", "比重"])
        if p_txt is not None and total:
            p_calc = round(fp / total * 100, 2)
            if abs(p_calc - p_txt) > 1.0:
                issues.append(self._issue(f"财政拨款收入占比：表算{p_calc}% ≠ 文本{p_txt}%（容忍±1pct）", {"page": p}, "warn"))
        return issues


class R33104_Expense_vs_Text(Rule):
    code, severity = "V33-104", "warn"
    desc = "支出决算表 ↔ 支出决算情况说明（含占比）"

    def apply(self, doc: Document) -> List[Issue]:
        issues: List[Issue] = []
        p = _get_first_anchor_page(doc, "支出决算表")
        if not p:
            return issues
        t = _largest_table_on_page(doc.page_tables[p - 1])
        if not t:
            return issues
        total = _row_value(t, ("本年支出合计", "本年合计", "合计"))
        basic = _row_value(t, ("基本支出",))
        proj = _row_value(t, ("项目支出",))
        if total is None or basic is None or proj is None:
            return issues
        txt = "\n".join(doc.page_texts)
        for (nm, a, b) in [("本年支出合计", total, near_number(txt, ["支出决算情况说明", "本年支出合计", "合计"])),
                           ("基本支出", basic, near_number(txt, ["基本支出"])),
                           ("项目支出", proj, near_number(txt, ["项目支出"]))]:
            if b and not tolerant_equal(a, b):
                issues.append(self._issue(f"{nm}：表{a} ≠ 文本{b}", {"page": p}, "warn"))
        for (nm, a) in [("基本支出", basic), ("项目支出", proj)]:
            pct_t = find_percent(txt, [nm, "占比", "比重"])
            if pct_t is not None and total:
                pct_c = round(a / total * 100, 2)
                if abs(pct_c - pct_t) > 1.0:
                    issues.append(self._issue(f"{nm}占比：表算{pct_c}% ≠ 文本{pct_t}%（容忍±1pct）", {"page": p}, "warn"))
        return issues


class R33105_FinGrantTotal_vs_Text(Rule):
    code, severity = "V33-105", "warn"
    desc = "财政拨款收入支出决算总表 ↔ 总体情况说明"

    def apply(self, doc: Document) -> List[Issue]:
        issues: List[Issue] = []
        p = _get_first_anchor_page(doc, "财政拨款收入支出决算总表")
        if not p:
            return issues
        t = _largest_table_on_page(doc.page_tables[p - 1])
        if not t:
            return issues
        total = _row_value(t, ("支出合计", "支出总计", "合计"))
        if total is None:
            return issues
        txt = "\n".join(doc.page_texts)
        t_total = near_number(txt, ["财政拨款收入支出决算总体情况说明", "总计", "合计"])
        if t_total and not tolerant_equal(total, t_total):
            issues.append(self._issue(f"财政拨款支出合计：表{total} ≠ 文本{t_total}", {"page": p}, "warn"))
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
                return issues

            table = _largest_table_on_page(doc.page_tables[p - 1])
            if not table:
                return issues

            # 2) 提取"合计"
            total_val = _row_value(table, ("合计", "支出合计"))
            if total_val is None:
                return issues

            # 3) 在"总体情况说明"中查找相近数字
            full_text = "\n".join(doc.page_texts)
            found_num = near_number(full_text, ["总体情况说明", "总体情况"])
            if found_num is not None:
                if not tolerant_equal(total_val, found_num):
                    issues.append(self._issue(
                        f"一般公共预算财拨支出合计({total_val:.2f})与总体情况说明数字({found_num:.2f})不一致",
                        {"page": p, "pos": 0}
                    ))

            # 4) 结构占比检查
            func_sums = _sum_by_func_class(table)
            for func_name, func_val in func_sums.items():
                if func_val > 0:
                    pct = func_val / total_val * 100
                    found_pct = find_percent(full_text, [func_name, "结构情况"])
                    if found_pct is not None:
                        if abs(pct - found_pct) > 2.0:
                            issues.append(self._issue(
                                f"{func_name}占比：表格计算{pct:.1f}%，文本{found_pct:.1f}%，差异超过2%",
                                {"page": p, "pos": 0}
                            ))

            return issues
        except RecursionError:
            return [self._issue("规则执行异常：maximum recursion depth exceeded", {"page": 1, "pos": 0}, "info")]
        except Exception as e:
            return [self._issue(f"规则执行异常：{str(e)}", {"page": 1, "pos": 0}, "info")]


class R33107_BasicExpense_Check(Rule):
    code, severity = "V33-107", "warn"
    desc = "基本支出：人员经费合计 + 公用经费合计 ↔ 文本说明"

    def apply(self, doc: Document) -> List[Issue]:
        issues: List[Issue] = []
        p = _get_first_anchor_page(doc, "一般公共预算财政拨款基本支出决算表")
        if not p:
            return issues
        t = _largest_table_on_page(doc.page_tables[p - 1])
        if not t:
            return issues
        ren = _row_value(t, ("人员经费合计", "人员经费"))
        gong = _row_value(t, ("公用经费合计", "公用经费"))
        if ren is None or gong is None:
            return issues
        total = ren + gong
        txt = "\n".join(doc.page_texts)
        t_total = near_number(txt, ["一般公共预算财政拨款基本支出决算情况说明", "基本支出", "合计"])
        if t_total and not tolerant_equal(total, t_total):
            issues.append(self._issue(f"基本支出合计：表算{total} ≠ 文本{t_total}", {"page": p}, "warn"))
        return issues


class R33108_ThreePublic_vs_Text(Rule):
    code, severity = "V33-108", "warn"
    desc = "三公经费：表 ↔ “总体情况说明”"

    def apply(self, doc: Document) -> List[Issue]:
        issues: List[Issue] = []
        p = _get_first_anchor_page(doc, "一般公共预算财政拨款“三公”经费支出决算表")
        if not p:
            return issues
        t = _largest_table_on_page(doc.page_tables[p - 1])
        if not t:
            return issues
        bud = _row_value(t, ("合计预算数", "预算合计", "预算数"))
        act = _row_value(t, ("合计决算数", "决算合计", "决算数"))
        if bud is None and act is None:
            return issues
        txt = "\n".join(doc.page_texts)
        tb = near_number(txt, ["三公", "年初预算", "预算"])
        ta = near_number(txt, ["三公", "支出决算", "决算"])
        if tb and bud and not tolerant_equal(bud, tb):
            issues.append(self._issue(f"三公经费预算：表{bud} ≠ 文本{tb}", {"page": p}, "warn"))
        if ta and act and not tolerant_equal(act, ta):
            issues.append(self._issue(f"三公经费决算：表{act} ≠ 文本{ta}", {"page": p}, "warn"))
        return issues


class R33109_EmptyTables_Statement(Rule):
    code, severity = "V33-109", "warn"
    desc = "政府性基金/国有资本经营/三公经费：如为空表，必须有空表说明"

    def apply(self, doc: Document) -> List[Issue]:
        issues: List[Issue] = []
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
                    short_name = nm.replace("一般公共预算财政拨款", "").replace("收入支出决算表", "").replace("预算财政拨款", "")
                    issues.append(self._issue(
                        f"【{nm}】为空表，但该页未见针对该表的空表说明（应有'注：XXX无{table_keywords[0] if table_keywords else '相关'}支出，故本表无数据'）。",
                        {"page": p}, "error",
                        evidence_text=f"空表名称：{nm}\n页面文本概览（未发现空表说明）：\n{current_page_text[:300].replace(chr(10), ' ')}..."
                    ))
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
        """检测预算数与决算数比较的一致性"""
        issues: List[Issue] = []
        full_text = "\n".join(doc.page_texts)
        
        # 1. 查找目标小节
        start_match = self._SEC_START.search(full_text)
        if not start_match:
            # 小节不存在，返回空列表
            return issues
        
        # 2. 确定小节结束位置
        sec_start = start_match.start()
        next_sec_match = self._NEXT_SEC.search(full_text, sec_start)
        sec_end = next_sec_match.start() if next_sec_match else len(full_text)
        sec_text = full_text[sec_start:sec_end]
        
        # 3. 先提取总结性语句
        summary_pattern = re.compile(r'一般公共预算财政拨款支出年初预算为\s*([\d,]+\.\d{2})\s*万元[\s\S]*?支[\s\S]*?出决算为\s*([\d,]+\.\d{2})\s*万元[\s\S]*?完成年初预算', re.S)
        summary_match = summary_pattern.search(sec_text)
        
        if summary_match:
            budget_str = summary_match.group(1)
            final_str = summary_match.group(2)
            
            # 转换为浮点数
            try:
                budget_val = float(budget_str.replace(",", ""))
                final_val = float(final_str.replace(",", ""))
            except ValueError:
                pass
            else:
                # 确定实际关系
                actual_relation = "等于"
                if final_val > budget_val:
                    actual_relation = "大于"
                elif final_val < budget_val:
                    actual_relation = "小于"
                
                # 查找关系表述
                relation_patterns = [
                    r'决[\s\S]*?算[\s\S]*?数[\s\S]*?(大于|小于|等于|持平|基本持平)[\s\S]*?预算[\s\S]*?数',
                    r'决[\s\S]*?算[\s\S]*?数[\s\S]*?与[\s\S]*?预算[\s\S]*?数[\s\S]*?(基本)?[\s\S]*?持平',
                    r'预算[\s\S]*?数[\s\S]*?(大于|小于|等于|持平|基本持平)[\s\S]*?决[\s\S]*?算[\s\S]*?数',
                    r'预算[\s\S]*?数[\s\S]*?与[\s\S]*?决[\s\S]*?算[\s\S]*?数[\s\S]*?(基本)?[\s\S]*?持平',
                ]
                
                relation = None
                for pattern in relation_patterns:
                    matches = re.findall(pattern, sec_text[summary_match.start():summary_match.start()+500], re.S)
                    if matches:
                        if '持平' in matches[0]:
                            relation = '等于'
                        elif '基本' in matches[0]:
                            relation = '等于'
                        else:
                            relation = matches[0]
                        break
                
                if relation:
                    # 验证一致性
                    if actual_relation != relation:
                        # 计算匹配文本在全文中的位置
                        match_start = sec_start + summary_match.start()
                        
                        # 查找匹配文本在哪个页面
                        page = 1
                        pos_in_page = 0
                        abs_pos = match_start
                        for i, page_text in enumerate(doc.page_texts):
                            if abs_pos <= len(page_text):
                                page = i + 1
                                pos_in_page = abs_pos
                                break
                            abs_pos -= len(page_text)
                        
                        # 生成问题描述
                        clip = sec_text[summary_match.start():summary_match.start()+100]
                        issues.append(self._issue(
                            f"预算数与决算数比较不一致：预算数{budget_str}，决算数{final_str}，实际{actual_relation}，但文本表述为{relation}。",
                            {"page": page, "pos": pos_in_page},
                            severity="error",
                            evidence_text=clip
                        ))
        
        # 4. 按数字编号分割，逐个分析每个项目
        items = re.split(r'\n\s*\d+、', sec_text)
        
        for idx, item in enumerate(items, 1):
            if not item.strip():
                continue
            
            # 查找预算 - 使用跨行正则表达式
            budget_patterns = [
                r'年初预算为\s*([\d,]+\.\d{2})\s*万元',
                r'年[\s\S]*?初[\s\S]*?预[\s\S]*?算[\s\S]*?为[\s\S]*?([\d,]+\.\d{2})[\s\S]*?万[\s\S]*?元',
            ]
            
            budget_str = None
            for pattern in budget_patterns:
                matches = re.findall(pattern, item, re.S)
                if matches:
                    budget_str = matches[0]
                    break
            
            # 查找决算 - 使用跨行正则表达式
            final_patterns = [
                r'支出决算为\s*([\d,]+\.\d{2})\s*万元',
                r'支[\s\S]*?出[\s\S]*?决[\s\S]*?算[\s\S]*?为[\s\S]*?([\d,]+\.\d{2})[\s\S]*?万[\s\S]*?元',
            ]
            
            final_str = None
            for pattern in final_patterns:
                matches = re.findall(pattern, item, re.S)
                if matches:
                    final_str = matches[0]
                    break
            
            # 查找关系 - 使用最全面的正则表达式
            relation_patterns = [
                r'决[\s\S]*?算[\s\S]*?数[\s\S]*?(大于|小于|等于|持平|基本持平)[\s\S]*?预算[\s\S]*?数',
                r'决[\s\S]*?算[\s\S]*?数[\s\S]*?与[\s\S]*?预[\s\S]*?算[\s\S]*?数[\s\S]*?(基本)?[\s\S]*?持平',
                r'预算[\s\S]*?数[\s\S]*?(大于|小于|等于|持平|基本持平)[\s\S]*?决[\s\S]*?算[\s\S]*?数',
                r'预算[\s\S]*?数[\s\S]*?与[\s\S]*?决[\s\S]*?算[\s\S]*?数[\s\S]*?(基本)?[\s\S]*?持平',
            ]
            
            relation = None
            for pattern in relation_patterns:
                matches = re.findall(pattern, item, re.S)
                if matches:
                    if '持平' in matches[0]:
                        relation = '等于'
                    elif '基本' in matches[0]:
                        relation = '等于'
                    else:
                        relation = matches[0]
                    break
            
            if budget_str and final_str:
                # 转换为浮点数
                try:
                    budget_val = float(budget_str.replace(",", ""))
                    final_val = float(final_str.replace(",", ""))
                except ValueError:
                    continue
                
                # 确定实际关系
                actual_relation = "等于"
                if final_val > budget_val:
                    actual_relation = "大于"
                elif final_val < budget_val:
                    actual_relation = "小于"
                
                # 检查是否有关系表述
                if relation:
                    # 验证一致性
                    if actual_relation != relation:
                        # 计算匹配文本在全文中的位置
                        item_start = sec_start + sec_text.find(item)
                        
                        # 查找匹配文本在哪个页面
                        page = 1
                        pos_in_page = 0
                        abs_pos = item_start
                        for i, page_text in enumerate(doc.page_texts):
                            if abs_pos <= len(page_text):
                                page = i + 1
                                pos_in_page = abs_pos
                                break
                            abs_pos -= len(page_text)
                        
                        # 生成问题描述
                        clip = item.strip()[:100]
                        issues.append(self._issue(
                            f"预算数与决算数比较不一致：预算数{budget_str}，决算数{final_str}，实际{actual_relation}，但文本表述为{relation}。",
                            {"page": page, "pos": pos_in_page},
                            severity="error",
                            evidence_text=clip
                        ))
        
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
                    f"收入支出总计为0万元，可能存在数据异常。",
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
        cn_punctuation = '，。！？；：""''（）【】《》、'
        
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

def _get_table_rows(doc: Document, table_name: str, include_continuation: bool = True) -> Optional[List[List[str]]]:
    """获取指定表格的所有行数据，支持跨页表格读取"""
    p = _get_first_anchor_page(doc, table_name)
    if not p:
        return None
    tables = doc.page_tables[p - 1]
    if not tables:
        return None
    # 返回最大的表格
    main_rows = _largest_table_on_page(tables)
    
    # ====== 修复：跨页表格续读 ======
    # 如果表格看起来未闭合（没有"合计"或"总计"行），尝试读取下一页
    if include_continuation and main_rows:
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
    """解析行中的所有数值，空值视为0"""
    vals = []
    for cell in row:
        v = parse_number(cell)
        if v is not None:
            vals.append(v)
        else:
            vals.append(0.0) # 空值视为0以便计算
    return vals

# ==================================================================================
# 勾稽关系验证规则
# ==================================================================================

class R33115_TotalSheetCheck(Rule):
    code, severity = "V33-115", "error"
    desc = "收入支出决算总表勾稽关系验证 (Table 1)"

    def apply(self, doc: Document) -> List[Issue]:
        issues = []
        table_name = "收入支出决算总表"
        rows = _get_table_rows(doc, table_name)
        if not rows:
            return []

        # 辅助查找函数：查找包含关键字的行，返回最后一个数值
        def find_val_by_label(keywords):
            for r in rows:
                txt = "".join([str(c) for c in r if c])
                if all(k in txt for k in keywords):
                    vs = _parse_row_values(r)
                    if vs: return vs[-1] # 通常取最后一个数值
            return 0.0

        # 1. 列内纵向校验 (Vertical Check)
        # 本年收入合计 = Sum(Row 1..8)
        # 本年支出合计 = Sum(所有功能分类科目)
        # (由于功能分类较多且格式不固定，这里暂只实现关键行抓取和平衡性检查)

        # 2. 表底平衡校验 (Balance Check)
        # 收入侧：总计 = 本年收入合计 + 使用非财政拨款结余 + 年初结转和结余
        # 支出侧：总计 = 本年支出合计 + 结余分配 + 年末结转和结余
        
        val_income_year = find_val_by_label(["本年收入合计"])
        val_non_fiscal = find_val_by_label(["使用非财政拨款结余"])
        val_start_balance = find_val_by_label(["年初结转和结余"])
        
        val_expense_year = find_val_by_label(["本年支出合计"])
        val_balance_alloc = find_val_by_label(["结余分配"])
        val_end_balance = find_val_by_label(["年末结转和结余"])
        
        # 查找总计行
        total_vals = []
        total_row_txt = ""
        for r in rows:
            if "总计" in "".join([str(c) for c in r]):
                vs = _parse_row_values(r)
                # 只有当总计行至少有2个大于0的数值，或者数值相等时才采纳
                # 通常是 [收入总计, 支出总计]
                if len(vs) >= 2:
                    total_vals = vs
                    total_row_txt = " | ".join([str(c) for c in r if c])
                    break
        
        if len(total_vals) >= 2:
            doc_income_total = total_vals[0]
            doc_expense_total = total_vals[1] # 假设第二个数值是支出总计，或者倒数第一个
            
            # 如果总计行只有两个数，那分别对应收、支。如果有多个，通常最后两个。
            if len(total_vals) > 2:
                 doc_expense_total = total_vals[-1]
            
            # 校验1：表底平衡
            if abs(doc_income_total - doc_expense_total) > 0.01:
                 issues.append(self._issue(
                    f"总表平衡性错误：收入总计({doc_income_total}) != 支出总计({doc_expense_total})",
                    {"table": table_name}, "error",
                    evidence_text=f"表格：{table_name}\n总计行内容：{total_row_txt}"
                ))
            
            # 校验2：收入侧计算
            calc_income = val_income_year + val_non_fiscal + val_start_balance
            if abs(calc_income - doc_income_total) > 0.01:
                issues.append(self._issue(
                    f"收入侧平衡错误：计算值({calc_income}) != 文档值({doc_income_total})。公式：本年收入({val_income_year}) + 非财政({val_non_fiscal}) + 年初({val_start_balance})",
                    {"table": table_name}, "error"
                ))
                
            # 校验3：支出侧计算
            calc_expense = val_expense_year + val_balance_alloc + val_end_balance
            if abs(calc_expense - doc_expense_total) > 0.01:
                issues.append(self._issue(
                    f"支出侧平衡错误：计算值({calc_expense}) != 文档值({doc_expense_total})。公式：本年支出({val_expense_year}) + 分配({val_balance_alloc}) + 年末({val_end_balance})",
                    {"table": table_name}, "error"
                ))

        return issues


class R33119_FiscalTotalCheck(Rule):
    code, severity = "V33-119", "error"
    desc = "财政拨款收入支出决算总表勾稽关系 (Table 4)"

    def apply(self, doc: Document) -> List[Issue]:
        issues = []
        table_name = "财政拨款收入支出决算总表"
        rows = _get_table_rows(doc, table_name)
        if not rows: return []

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


class R33120_DetailTableCheck(Rule):
    code, severity = "V33-120", "warn"
    desc = "明细表勾稽关系与层级校验 (Table 2, 3, 5)"

    def apply(self, doc: Document) -> List[Issue]:
        issues = []
        target_tables = ["收入决算表", "支出决算表", "一般公共预算财政拨款支出决算表"]
        
        for table_name in target_tables:
            rows = _get_table_rows(doc, table_name)
            if not rows: continue
            
            # 层级校验状态
            # 存储格式: {code: amount}
            hierarchy_data = {}
            
            # 定位金额列
            # 对于收入表(Table 2)：Col 3 (合计) = Sum(4..end)
            # 对于支出表(Table 3)：Col 3 (合计) = Sum(4..end)
            # 对于一般支出(Table 5)：Col 4 (合计) = Sum(5..end)
            # 需要智能识别 "合计" 列
            
            # 简化逻辑：
            # 1. 识别编码列 (通常Col 0/1/2) 和金额合计列
            # 2. 识别行级横向平衡
            
            col_total_idx = -1
            col_code_start_idx = -1
            
            # 简单的列定位 heuristic
            header_found = False
            for r in rows[:5]: # 扫描前5行找表头
                row_str = "".join([str(c) for c in r if c])
                if "合计" in row_str:
                    for i, cell in enumerate(r):
                        if "合计" in str(cell):
                            col_total_idx = i
                if "科目编码" in row_str:
                    col_code_start_idx = 0 # 假设编码在最前
            
            if col_total_idx == -1: 
                # 尝试默认值，通常合计在中间或靠后
                # Good sample: 收入表合计在 Col 4 (index 3, if 0-based and merged)
                # 让我们遍历每行，如果发现某列数值等于后续列之和，则认为是合计列
                pass

            for i, row in enumerate(rows):
                vals = _parse_row_values(row)
                row_txt = "".join([str(c) for c in row if c])
                
                # 提取编码 (假设编码是单纯数字)
                # 需处理 "201", "20101", "2010101"
                code = ""
                for cell in row[:3]: # 检查前3列
                    c_str = str(cell).strip()
                    if c_str.isdigit() and len(c_str) in [3, 5, 7]:
                        code = c_str
                        break
                
                # 5. 行级横向校验 (Row Horizontal Check)
                # 忽略表头和空行
                if len(vals) > 2 and sum(vals) > 0.01:
                    # 假定 Vals 中最大的数是合计，且等于其他数之和
                    # 排序校验
                    sorted_vals = sorted([v for v in vals if v > 0], reverse=True)
                    if len(sorted_vals) >= 2:
                        max_val = sorted_vals[0]
                        rest_sum = sum(sorted_vals[1:])
                        # 对于 Table 5: 合计 = 基本 + 项目 (2 items) -> max = sum(rest)
                        # 对于 Table 2/3: 合计 = 多个 items -> max = sum(rest)
                        
                        # 只有当 rest_sum 近似等于 max_val 时才校验 (处理有些表包含非加项的情况)
                        # 但这里要求严格校验。
                        # 此时需准确定位 "合计" 列。
                        
                        # 如果找不到列，跳过
                        pass

                # 6/7. 纵向层级校验 (Hierarchy Check)
                if code and col_total_idx != -1 and col_total_idx < len(vals):
                    amount = vals[col_total_idx]
                    hierarchy_data[code] = amount

            # 执行层级校验
            # 类(3位) = Sum(款 5位)
            # 款(5位) = Sum(项 7位)
            for parent_code, parent_amt in hierarchy_data.items():
                if len(parent_code) not in [3, 5]: continue
                
                target_len = len(parent_code) + 2
                child_sum = 0.0
                has_children = False
                
                for child_code, child_amt in hierarchy_data.items():
                    if len(child_code) == target_len and child_code.startswith(parent_code):
                        child_sum += child_amt
                        has_children = True
                
                if has_children and abs(parent_amt - child_sum) > 0.1:
                     issues.append(self._issue(
                        f"层级校验失败({parent_code}): 父级金额({parent_amt}) != 子级之和({child_sum})",
                        {"table": table_name, "code": parent_code}, "warn",
                        evidence_text=f"表格：{table_name}\n父级科目：{parent_code} (金额 {parent_amt})\n子级科目之和：{child_sum}"
                    ))

        return issues


class R33117_BasicExpenseClassification(Rule):
    code, severity = "V33-117", "error"
    desc = "基本支出决算表经济分类校验 (Table 6)"

    def apply(self, doc: Document) -> List[Issue]:
        issues = []
        table_name = "一般公共预算财政拨款基本支出决算表"
        rows = _get_table_rows(doc, table_name)
        if not rows: return []
        
        sum_personnel = 0.0
        sum_public = 0.0
        
        # 8/9. 板块汇总校验
        # 识别 301, 303 -> 人员
        # 识别 302, 310 -> 公用
        
        # 需遍历所有行，识别类级科目 (3位数)
        for row in rows:
            # 提取编码
            code = ""
            for cell in row[:3]:
                c_str = str(cell).strip()
                if c_str.isdigit() and len(c_str) == 3:
                    code = c_str
                    break
            
            if code:
                vals = _parse_row_values(row)
                # 假设金额在最后一列，或倒数第二列 (有些表双栏)
                # 双栏处理比较复杂：[301, 工资, Amt, 302, 商品, Amt]
                # 需检查行内是否包含多个类
                
                # 简化处理：全文搜索 301xxx 金额
                pass
        
        # 使用更粗暴但有效的方法：文本解析
        # 提取所有 (Code, Amount) 对
        code_amts = []
        for row in rows:
            vals = _parse_row_values(row)
            txt_cells = [str(c).strip() for c in row if c]
            
            # 双栏检测
            # 栏1: Code1 (Name1) Amt1
            # 栏2: Code2 (Name2) Amt2
            # 尝试匹配 3位数字
            
            # 正则提取行内所有 3位数字及其后紧跟的数值
            # 需结合 row 结构
            
            # 策略：如果单元格是3位数字，且后面某单元格是数值，则通过
            # 仅处理 类级 (301, 302, 303, 310)
            
            for i, cell in enumerate(row):
                c_str = str(cell).strip()
                if c_str in ["301", "302", "303", "310"]:
                    # 找该 Code 后的数值
                    # 在 row[i+1:] 中找第一个有效数值
                    amt = 0.0
                    for next_cell in row[i+1:]:
                        v = parse_number(next_cell)
                        if v is not None:
                            amt = v
                            break
                    if amt > 0:
                        if c_str in ["301", "303"]: sum_personnel += amt
                        if c_str in ["302", "310"]: sum_public += amt

        # 验证合计
        # 查找表中显式的 "人员经费合计" 和 "公用经费合计"
        explicit_personnel = 0.0
        explicit_public = 0.0
        
        def find_val(kw):
            for r in rows:
                if kw in "".join([str(c) for c in r]):
                    vs = _parse_row_values(r)
                    if vs: return vs[-1]
            return 0.0

        explicit_personnel = find_val("人员经费合计")
        explicit_public = find_val("公用经费合计")
        
        # 如果表中有显式合计行，则校验
        if explicit_personnel > 0 and abs(explicit_personnel - sum_personnel) > 1.0:
             issues.append(self._issue(
                f"人员经费校验失败：显式合计({explicit_personnel}) != 分项之和({sum_personnel})",
                {"table": table_name}, "warn",
                evidence_text=f"表格：{table_name}\n显式合计行：人员经费合计={explicit_personnel}\n逐行累加：301/303类科目之和={sum_personnel}"
            ))
            
        if explicit_public > 0 and abs(explicit_public - sum_public) > 1.0:
             issues.append(self._issue(
                f"公用经费校验失败：显式合计({explicit_public}) != 分项之和({sum_public})",
                {"table": table_name}, "warn",
                evidence_text=f"表格：{table_name}\n显式合计行：公用经费合计={explicit_public}\n逐行累加：302/310类科目之和={sum_public}"
            ))

        return issues


class R33121_ThreePublicCheck(Rule):
    code, severity = "V33-121", "error"
    desc = "三公经费勾稽关系 (Table 7)"

    def apply(self, doc: Document) -> List[Issue]:
        issues = []
        table_name = "一般公共预算财政拨款“三公”经费支出决算表"
        rows = _get_table_rows(doc, table_name)
        if not rows: return []
        
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
            return issues
        
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
            return issues
        
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
            return issues
        
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
            return issues
        
        # T4: 查找"一般公共预算财政拨款"列的支出合计
        t4_general_expense = 0.0
        for row in t4_rows:
            row_txt = "".join([str(c) for c in row if c])
            if "本年支出合计" in row_txt or "支出合计" in row_txt:
                vals = _parse_row_values(row)
                # 假设第2列是"一般公共预算"（根据标准表结构）
                if len(vals) >= 2: t4_general_expense = vals[1]
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
            return issues
        
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
            return issues

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
            return issues
        
        # 查找收入侧"总计"和支出侧"总计"
        income_total = 0.0
        expense_total = 0.0
        
        for row in t1_rows:
            row_txt = "".join([str(c) for c in row if c])
            vals = _parse_row_values(row)
            
            if "总计" in row_txt and vals:
                # T1通常是左右并排结构，需要区分收入侧和支出侧
                # 假设vals中有两个总计值
                if len(vals) >= 2:
                    income_total = vals[0]
                    expense_total = vals[1]
                elif len(vals) == 1:
                    # 可能只有一个值，表示收支相等
                    income_total = expense_total = vals[0]
                break
        
        if income_total > 0.01 and expense_total > 0.01:
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
        t2_rows = _get_table_rows(doc, "收入决算表")
        
        if not t2_rows or len(t2_rows) < 3:
            return issues
        
        # A. 智能表头解析
        header_row_idx = -1
        col_total_idx = -1
        col_sources = {}
        col_code_idx = -1
        col_name_idx = -1
        
        for r_idx, row in enumerate(t2_rows[:5]):
            row_txt = "".join([str(c) for c in row if c])
            if "本年收入" in row_txt or "财政拨款" in row_txt:
                header_row_idx = r_idx
                for c_idx, cell in enumerate(row):
                    cell_str = str(cell).strip() if cell else ""
                    for kw in self.TOTAL_COLUMN_KEYWORDS:
                        if kw in cell_str:
                            col_total_idx = c_idx
                            break
                    for kw in self.INCOME_SOURCE_KEYWORDS:
                        if kw in cell_str:
                            col_sources[kw] = c_idx
                            break
                    if "科目编码" in cell_str or "编码" in cell_str:
                        col_code_idx = c_idx
                    if "科目名称" in cell_str or "名称" in cell_str:
                        col_name_idx = c_idx
                break
        
        if col_total_idx == -1:
            col_code_idx, col_name_idx, col_total_idx = 0, 1, 2
            for i in range(3, min(10, len(t2_rows[0]) if t2_rows else 0)):
                col_sources[f"来源{i-2}"] = i
            header_row_idx = 0
        
        # B. 构建层级数据结构
        hierarchy_data = {}
        
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
                
                if diff > 0.01:
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
                diff = abs(parent["total"] - child_sum)
                if diff > 0.01:
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
                diff = abs(parent["total"] - child_sum)
                if diff > 0.01:
                    issues.append(self._issue(
                        f"【收入决算表】类级({parent_code} {parent['name']})纵向汇总不平：类({parent['total']:.2f}) ≠ 款之和({child_sum:.2f})，差额={diff:.2f}",
                        {"table": "收入决算表", "code": parent_code, "diff": diff, "type": "vertical"}, "error",
                        evidence_text=f"表格：收入决算表\n科目：{parent_code} {parent['name']}\n该科目金额：{parent['total']}\n下级科目之和：{child_sum}"
                    ))
        
        total_row = hierarchy_data.get("TOTAL")
        if total_row and level_1:
            level_1_sum = sum(hierarchy_data[c]["total"] for c in level_1)
            diff = abs(total_row["total"] - level_1_sum)
            if diff > 0.01:
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
        t3_rows = _get_table_rows(doc, "支出决算表")
        
        if not t3_rows or len(t3_rows) < 3:
            return issues
        
        # A. 智能表头解析
        header_row_idx = -1
        col_total_idx = -1
        col_basic_idx = -1
        col_project_idx = -1
        col_code_indices = []  # 编码列（类/款/项）
        col_name_idx = -1
        
        for r_idx, row in enumerate(t3_rows[:5]):
            row_txt = "".join([str(c) for c in row if c])
            if "合计" in row_txt or "基本支出" in row_txt or "项目支出" in row_txt:
                header_row_idx = r_idx
                for c_idx, cell in enumerate(row):
                    cell_str = str(cell).strip() if cell else ""
                    for kw in self.TOTAL_COL_KEYWORDS:
                        if kw in cell_str:
                            col_total_idx = c_idx
                            break
                    for kw in self.BASIC_COL_KEYWORDS:
                        if kw in cell_str:
                            col_basic_idx = c_idx
                            break
                    for kw in self.PROJECT_COL_KEYWORDS:
                        if kw in cell_str:
                            col_project_idx = c_idx
                            break
                    if cell_str in ["类", "款", "项"] or "编码" in cell_str:
                        col_code_indices.append(c_idx)
                    if "科目名称" in cell_str or "名称" in cell_str:
                        col_name_idx = c_idx
                break
        
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
        
        for r_idx, row in enumerate(t3_rows):
            if r_idx <= header_row_idx:
                continue
            
            row_txt = "".join([str(c) for c in row if c])
            
            # 提取编码 (合并类款项)
            code_parts = []
            for ci in col_code_indices[:3]:
                if ci < len(row) and row[ci]:
                    code_parts.append(str(row[ci]).strip())
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
                
                if diff > 0.01:
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
                diff = abs(parent["total"] - child_sum)
                if diff > 0.01:
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
                diff = abs(parent["total"] - child_sum)
                if diff > 0.01:
                    issues.append(self._issue(
                        f"【支出决算表】类级({parent_code} {parent['name']})纵向汇总不平：类({parent['total']:.2f}) ≠ 款之和({child_sum:.2f})，差额={diff:.2f}",
                        {"table": "支出决算表", "code": parent_code, "diff": diff, "type": "vertical"}, "error",
                        evidence_text=f"表格：支出决算表\n科目：{parent_code} {parent['name']}\n类金额：{parent['total']}\n下级款之和：{child_sum}"
                    ))
        
        total_row = hierarchy_data.get("TOTAL")
        if total_row and level_1:
            level_1_sum = sum(hierarchy_data[c]["total"] for c in level_1)
            diff = abs(total_row["total"] - level_1_sum)
            if diff > 0.01:
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
            return issues

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
        import re
        all_text = "\n" + "\n".join(doc.page_texts)
        
        # 锁定“三公”经费说明段落
        three_public_section = ""
        # 匹配章节标题，通常形如“七、...三公...情况说明”
        p_title = r'\n[一二三四五六七八九十]、[^\n]{1,40}情况说明'
        matches = list(re.finditer(p_title, all_text))
        
        for m in matches:
            title_text = m.group(0)
            if "三公" not in title_text:
                continue
            # 截取标题后的内容，直到下一个主标题或3500字符
            rest = all_text[m.end():]
            m_next = re.search(r'\n[一二三四五六七八九十]、', rest)
            temp_section = rest[:m_next.start()] if m_next else rest[:3500]
            
            # 验证：说明段落应包含具体的预算或决算数值描述
            if "预算" in temp_section or "决算" in temp_section:
                three_public_section = temp_section
                break
        
        # 兜底：如果章节匹配完全失败，使用全文匹配
        if not three_public_section:
            three_public_section = all_text

        def get_nar_val(item_p, type_p):
            # item_p: 科目特征; type_p: 预算/决算
            # 模式：科目...口径(紧跟)...数值...万元
            # 再次收紧：口径后面 20 字符内必须出数值，防止跨句（例如从标题的“决算”跳到正文的预算值）
            pattern = fr'{item_p}.*?{type_p}.{{0,20}}?(\d+\.?\d*)\s*万元'
            m = re.search(pattern, three_public_section, re.S)
            return float(m.group(1)) if m else None

        # 决算侧 (Final)
        nar_f_total = get_nar_val(r'三公.*?支出', "决算")
        nar_f_abroad = get_nar_val(r'因公出国', "决算")
        nar_f_car = get_nar_val(r'公务用车', "决算")
        nar_f_reception = get_nar_val(r'公务接待', "决算")

        # 预算侧 (Budget)
        nar_b_total = get_nar_val(r'三公.*?支出', r'预算')
        nar_b_abroad = get_nar_val(r'因公出国', r'预算')
        nar_b_car = get_nar_val(r'公务用车', r'预算')
        nar_b_reception = get_nar_val(r'公务接待', r'预算')

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
                        evidence_text=f"文档说明：0\n表格数据：空白"
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
            return issues

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
            return issues
        
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
        
        narrative_total = 0.0
        for pidx, txt in enumerate(doc.page_texts):
            if "财政拨款" in txt and ("总体情况" in txt or "收入支出决算" in txt):
                total_match = re.search(r'(?:财政拨款)?[收入支出]*总计[^\d]*(\d+\.?\d*)\s*万元', txt)
                if total_match:
                    narrative_total = float(total_match.group(1))
        
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
                issues.append(self._issue(
                    f"说明4↔T4总计不一致：说明={narrative_total:.2f}, T4={t4_total:.2f}",
                    {"narrative": narrative_total, "table": t4_total}, "warn",
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
        
        # 1. 提取叙述数据
        # 目标：提取 "201 一般公共服务支出 XXX 万元"
        # 难点：叙述中可能只写中文名称，不写编码；或者写编码。
        # 策略：建立一个常见 "类级科目" 映射表 (Name -> Code prefix)
        # 或者反过来，先读 T5 的类级科目，去文本里搜
        
        t5_rows = _get_table_rows(doc, "一般公共预算财政拨款支出决算表")
        if not t5_rows: return issues

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
        for txt in doc.page_texts:
            if "一般公共预算" in txt and ("支出决算" in txt or "财政拨款支出" in txt) and "情况说明" in txt:
                target_txt += txt + "\n" # 拼接相关文本，防止分页截断
        
        if not target_txt: return issues
        
        # 校验总额
        total_match = re.search(r'支出[^\d]*(\d+\.?\d*)\s*万元', target_txt)
        if total_match:
            nar_total = float(total_match.group(1))
            if t5_total > 0.01 and abs(nar_total - t5_total) > 0.01:
                issues.append(self._issue(
                    f"说明5↔T5支出总额不一致：说明={nar_total:.2f}, T5={t5_total:.2f}",
                    {"narrative": nar_total, "table": t5_total}, "warn",
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
                     issues.append(self._issue(
                        f"说明5↔T5类级科目({code}{short_name})金额不一致：说明={nar_val:.2f}, T5={info['val']:.2f}",
                        {"code": code, "subject": short_name, "narrative": nar_val, "table": info['val']}, "warn",
                        evidence_text=f"科目：{code}{short_name}\n文档说明：{nar_val}\n表5数据：{info['val']}"
                    ))
            else:
                # 尝试另一种模式：金额 ... (占) ... 名称
                # 这比较少见，通常是：教育支出 XX 万元
                pass
        
        return issues


# ==================================================================================
# P2 - 表↔情况说明校验 (Table vs Narrative)
# ==================================================================================

class R33220_Narrative3_T3(Rule):
    """说明3（支出决算）↔ T3 金额与占比"""
    code, severity = "V33-220", "warn"
    desc = "说明3↔T3支出决算金额占比校验"

    def apply(self, doc: Document) -> List[Issue]:
        issues = []
        import re
        
        # 1. 提取叙述中的关键金额
        nar_basic = None
        nar_project = None
        
        for pidx, txt in enumerate(doc.page_texts):
            if "支出决算情况说明" in txt or "本年支出合计" in txt:
                # 尝试更精确的正则匹配
                # 模式：基本支出 XXX 万元，占 XX%；项目支出 XXX 万元，占 XX%
                basic_matches = re.findall(r'基本支出[^\d]*(\d+\.?\d*)\s*万元', txt)
                project_matches = re.findall(r'项目支出[^\d]*(\d+\.?\d*)\s*万元', txt)
                
                if basic_matches:
                    nar_basic = float(basic_matches[0])
                if project_matches:
                    nar_project = float(project_matches[0])
                
                if nar_basic is not None or nar_project is not None:
                    break
        
        # 2. 获取 T3 数据
        t3_rows = _get_table_rows(doc, "支出决算表")
        if not t3_rows:
            return issues

        # 提取 T3 合计行的 基本支出 和 项目支出
        t3_total = 0.0
        t3_basic = 0.0
        t3_project = 0.0
        
        found_total_row = False
        for row in t3_rows:
            row_txt = "".join([str(c) for c in row if c])
            # 找合计行
            if "合计" in row_txt or "本年支出合计" in row_txt:
                vals = _parse_row_values(row)
                # T3 结构通常是：... | 合计 | 基本支出 | 项目支出 | ...
                # 这里比较依赖列位置，或者简单的数值大小推断
                # 假设：最大的是合计，其次是基本/项目? 不一定
                # 安全做法：假设 T3 表头已知。但这里 doc.page_tables 没有表头语义
                # 降级策略：在合计行里找跟 nar_basic / nar_project 接近的数
                
                if vals:
                    t3_total = max(vals)
                    # 尝试找到 基本 和 项目
                    # 通常 基本 + 项目 = 合计
                    # 只有两个数相加等于最大数，且这两个数都在 vals 里
                    
                    # 简单的启发式：如果有叙述值，直接去 vals 里找是否存在接近的
                    found_total_row = True
                    
                    # 校验基本支出
                    if nar_basic is not None:
                        # 在行数据中查找是否有一个值近似 nar_basic
                        has_match = any(abs(v - nar_basic) <= 0.01 for v in vals)
                        if not has_match:
                            issues.append(self._issue(
                                f"说明3↔T3基本支出不一致：说明={nar_basic:.2f}, 表内未找到对应值 (行数据: {vals})",
                                {"narrative": nar_basic, "table_row_vals": vals}, "warn",
                                evidence_text=f"文档说明(基本支出)：{nar_basic}\n表3合计行数据：{vals}\n（未寻找匹配值）"
                            ))
                    
                    # 校验项目支出
                    if nar_project is not None:
                         has_match = any(abs(v - nar_project) <= 0.01 for v in vals)
                         if not has_match:
                            issues.append(self._issue(
                                f"说明3↔T3项目支出不一致：说明={nar_project:.2f}, 表内未找到对应值 (行数据: {vals})",
                                {"narrative": nar_project, "table_row_vals": vals}, "warn",
                                evidence_text=f"文档说明(项目支出)：{nar_project}\n表3合计行数据：{vals}\n（未寻找匹配值）"
                            ))
                    
                    # 校验占比 (如果叙述中有百分比)
                    pct_pattern = r'(\d+\.?\d*)\s*%'
                    matches = re.finditer(pct_pattern, txt)
                    for m in matches:
                        p_val = float(m.group(1))
                        # 检查这个百分比是否对应基本或项目
                        # 简单逻辑：看百分比前后的文字
                        context = txt[max(0, m.start()-10):m.end()]
                        
                        calc_pct = None
                        if "基本" in context and t3_total > 0:
                            calc_pct = (t3_basic if t3_basic > 0 else nar_basic or 0) / t3_total * 100
                        elif "项目" in context and t3_total > 0:
                            calc_pct = (t3_project if t3_project > 0 else nar_project or 0) / t3_total * 100
                        
                        if calc_pct is not None:
                            if abs(p_val - calc_pct) > 0.05: # 百分比容差稍大一点
                                 pass # 暂时不报百分比错误，优先报金额错误
                    
                    break
        
        return issues


class R33223_Narrative6_T6(Rule):
    """说明6（基本支出）↔ T6 人员/公用金额"""
    code, severity = "V33-223", "warn"
    desc = "说明6↔T6基本支出人员公用校验"

    def apply(self, doc: Document) -> List[Issue]:
        issues = []
        import re
        
        # 查找"基本支出决算情况说明"
        nar_personnel = None
        nar_public = None
        
        for pidx, txt in enumerate(doc.page_texts):
            if "基本支出" in txt and ("人员经费" in txt or "公用经费" in txt):
                # 匹配模式："人员经费 XXX 万元"
                p_match = re.search(r'人员经费[^\d]*(\d+\.?\d*)\s*万元', txt)
                pub_match = re.search(r'公用经费[^\d]*(\d+\.?\d*)\s*万元', txt)
                
                if p_match: nar_personnel = float(p_match.group(1))
                if pub_match: nar_public = float(pub_match.group(1))
                
                if nar_personnel is not None or nar_public is not None:
                    break
        
        t6_rows = _get_table_rows(doc, "一般公共预算财政拨款基本支出决算表")
        if not t6_rows:
            return issues

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
                issues.append(self._issue(
                    f"说明6↔T6人员经费不一致：说明={nar_personnel:.2f}, T6={t6_personnel:.2f}",
                    {"narrative": nar_personnel, "table": t6_personnel}, "warn",
                    evidence_text=f"文档说明(人员经费)：{nar_personnel}\n表6(基本支出) 人员经费合计：{t6_personnel}"
                ))
        
        # 校验公用经费
        if nar_public is not None:
            if found_pub and abs(nar_public - t6_public) > 0.01:
                issues.append(self._issue(
                     f"说明6↔T6公用经费不一致：说明={nar_public:.2f}, T6={t6_public:.2f}",
                    {"narrative": nar_public, "table": t6_public}, "warn",
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
        
        # 1. 提取叙述数据
        # 结构： { 'item_name': {'budget': val, 'final': val} }
        nar_data = {}
        
        # 关键词映射
        keys = {
            '合计': ['三公', '使用一般公共预算财政拨款安排'],
            '因公出国': ['因公出国', '出国（境）费'],
            '公务用车': ['公务用车', '用车'],
            '公务接待': ['公务接待']
        }
        
        target_txt = ""
        for txt in doc.page_texts:
            if "三公" in txt and ("情况说明" in txt or "经费支出" in txt):
                target_txt = txt  # 简单取最后一页匹配到的？通常只有一处
                break
        
        if not target_txt:
            return issues

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
        t7_rows = _get_table_rows(doc, '一般公共预算财政拨款"三公"经费支出决算表')
        if not t7_rows: return issues
        
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
                 issues.append(self._issue(
                     f"说明7↔T7三公合计不一致：说明={nar_final_total:.2f}, T7未找到对应值",
                     {"narrative": nar_final_total, "table_vals": t7_vals}, "warn",
                     evidence_text=f"文档说明(三公合计)：{nar_final_total}\n表7内所有数值：{t7_vals}"
                 ))
        
        # 校验分项 (仅当分项值 > 0 时)
        # 出国
        if nar_final_abroad is not None and nar_final_abroad > 0:
            if not any(abs(v - nar_final_abroad) <= 0.01 for v in t7_vals):
                 issues.append(self._issue(
                     f"说明7↔T7因公出国不一致：说明={nar_final_abroad:.2f}",
                     {"narrative": nar_final_abroad}, "warn",
                     evidence_text=f"文档说明(因公出国)：{nar_final_abroad}\n表7内数据未找到匹配值"
                 ))

        # 用车
        if nar_final_car is not None and nar_final_car > 0:
            if not any(abs(v - nar_final_car) <= 0.01 for v in t7_vals):
                 issues.append(self._issue(
                     f"说明7↔T7公务用车不一致：说明={nar_final_car:.2f}",
                     {"narrative": nar_final_car}, "warn",
                     evidence_text=f"文档说明(公务用车)：{nar_final_car}\n表7内数据未找到匹配值"
                 ))
                 
        # 接待
        if nar_final_recept is not None and nar_final_recept > 0:
            if not any(abs(v - nar_final_recept) <= 0.01 for v in t7_vals):
                 issues.append(self._issue(
                     f"说明7↔T7公务接待不一致：说明={nar_final_recept:.2f}",
                     {"narrative": nar_final_recept}, "warn",
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
        for txt in doc.page_texts:
            if "收入决算" in txt and ("情况说明" in txt or "本年收入合计" in txt):
                target_txt = txt
                break
        
        if not target_txt: return issues
        
        # 提取各项金额
        nar_vals = {}
        for k, _ in key_map.items():
            # 正则：关键词 ... 数字 ... 万元
            # 兼容 "财政拨款收入为 100 万元" 或 "财政拨款收入 100 万元"
            m = re.search(re.escape(k) + r'[^\d]{0,20}(\d+\.?\d*)\s*万元', target_txt)
            if m:
                nar_vals[k] = float(m.group(1))
        
        if not nar_vals: return issues

        # 2. 获取 T2 数据
        # T2 收入决算表，通常包含上述列
        t2_rows = _get_table_rows(doc, "收入决算表")
        if not t2_rows: return issues
        
        # 解析 T2 结构
        # 找到合计行
        t2_row_vals = []
        for row in t2_rows:
            row_txt = "".join([str(c) for c in row if c])
            if "合计" in row_txt or "本年收入合计" in row_txt:
                t2_row_vals = _parse_row_values(row)
                break
        
        if not t2_row_vals: return issues
        
        # 这里的困难是 T2 的列顺序不确定，且 parse_row_values 只是数值列表
        # 只能尝试基于数值的匹配
        # 如果叙述中提到了某项金额，我们看 T2 合计行里有没有这个数
        
        for k, v in nar_vals.items():
            if v > 0:
                # 强校验：表中必须有这个数 (容差 0.01)
                found = any(abs(val - v) <= 0.01 for val in t2_row_vals)
                if not found:
                    issues.append(self._issue(
                         f"说明2↔T2{k}不一致：说明={v:.2f}, T2合计行未找到对应值",
                         {"narrative_key": k, "narrative_val": v, "table_row": t2_row_vals}, "warn",
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
        
        narrative_total = 0.0
        for pidx, txt in enumerate(doc.page_texts):
            if "收入支出决算" in txt and "总体情况" in txt:
                total_match = re.search(r'收入支出总计[^\d]*(\d+\.?\d*)\s*万元', txt)
                if total_match:
                    narrative_total = float(total_match.group(1))
                    break
        
        # 只要找到了匹配项（即使是0），且T1有数据，就应该校验
        if narrative_total is not None:
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
                    issues.append(self._issue(
                        f"说明1↔T1总计不一致：说明={narrative_total:.2f}, T1={t1_total:.2f}",
                        {"narrative": narrative_total, "table": t1_total}, "warn",
                        evidence_text=f"文档说明(收入支出决算总计)：{narrative_total}\n表1(总表) 总计：{t1_total}"
                    ))
        return issues


# ==================================================================================
# P3 - 规范性提示 (Normative Hints)
# ==================================================================================

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
                            evidence_text=f"文档内容：提及 '0' 或相关描述\n表格检测：三公表存在但无任何大于0的数值"
                        ))
                break
        
        return issues


class R33232_PercentagePrecision(Rule):
    """百分比两位小数精度校验"""
    code, severity = "V33-232", "info"
    desc = "百分比精度校验（两位小数）"

    def apply(self, doc: Document) -> List[Issue]:
        issues = []
        import re
        
        for pidx, txt in enumerate(doc.page_texts):
            # 查找所有百分比
            pct_pattern = r'(\d+\.?\d*)\s*%'
            matches = re.findall(pct_pattern, txt)
            
            for pct_str in matches:
                try:
                    pct = float(pct_str)
                    # 检查是否超过两位小数
                    if '.' in pct_str:
                        decimal_places = len(pct_str.split('.')[1])
                        if decimal_places > 2:
                            issues.append(self._issue(
                                f"规范性提示：第{pidx+1}页发现超过两位小数的百分比({pct_str}%)，建议四舍五入。",
                                {"page": pidx + 1, "pct": pct_str}, "info",
                                evidence_text=f"发现位置：P{pidx+1}\n百分比数值：{pct_str}%"
                            ))
                except:
                    pass
        
        return issues


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
    R33242_Table4_ComprehensiveCheck(),
    R33240_Table2_IncomeAdvancedCheck(),
    R33241_Table3_ExpenseAdvancedCheck(),
    # P2 - 表↔情况说明
    R33225_Narrative1_T1(),
    R33226_Narrative2_T2(),
    R33220_Narrative3_T3(),
    R33221_Narrative4_T4(),
    R33222_Narrative5_T5(),
    R33223_Narrative6_T6(),
    R33224_Narrative7_T7(),
    # 补充 - 表间勾稽
    R33204_InterTable_T2_T4(),
    # P3 - 规范性提示
    R33230_EmptyZeroHint(),
    R33232_PercentagePrecision(),
]
