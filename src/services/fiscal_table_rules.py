"""
Canonical rules for the nine fiscal tables.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import os
import re
import unicodedata
from typing import Dict, Iterable, Optional

from rapidfuzz import fuzz

#: 回退开关：关闭「独占标记」强制归类，恢复纯别名/表头打分。
TITLE_MARKER_ENABLED = os.environ.get(
    "PDF_DISABLE_TITLE_MARKER", ""
).strip().lower() not in {"1", "true", "yes", "on"}

_RADICAL_TRANSLATION = str.maketrans(
    {
        "\u2ed4": "门",
        "\u2ecb": "车",
    }
)


def normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", (value or "")).translate(_RADICAL_TRANSLATION)
    normalized = normalized.strip().lower()
    normalized = re.sub(r"[\s\u3000\xa0]+", "", normalized)
    normalized = normalized.replace("（", "(").replace("）", ")")
    normalized = normalized.replace("：", ":").replace("－", "-")
    return normalized


@dataclass(frozen=True)
class TableRule:
    code: str
    aliases: tuple[str, ...]
    required_headers: tuple[str, ...]
    optional_headers: tuple[str, ...]
    measure_aliases: Dict[str, tuple[str, ...]]
    classification_type: str
    #: 该表独占的标题关键词。命中后其他表不得再以泛别名（如「支出决算表」）
    #: 抢走该标题——各地版式会把同一张表写成不同长标题，泛别名越短越容易
    #: 在长标题上拿到精确命中，从而挤掉真正的表种。
    distinctive: tuple[str, ...] = ()
    #: 多级表头的叶子指标：(父级关键词, 叶子表头)。父级同时覆盖多列时
    #: （如「公务用车购置及运行费」下的 小计/购置费/运行费），只有叶子能
    #: 区分各列；父级别名单独命中多列会因同分被整组丢弃。
    leaf_aliases: Dict[str, tuple[tuple[str, str], ...]] = field(default_factory=dict)


NINE_TABLE_RULES: Dict[str, TableRule] = {
    "FIN_01_income_expenditure_total": TableRule(
        code="FIN_01_income_expenditure_total",
        aliases=(
            "收入支出决算总表",
            "收入支出总表",
            "收支决算总表",
            "收入支出预算总表",
            "财务收支预算总表",
            "部门财务收支预算总表",
            "部门收支预算总表",
        ),
        required_headers=("本年收入", "本年支出"),
        optional_headers=("年初结转和结余", "年末结转和结余", "使用非财政拨款结余"),
        measure_aliases={
            "income_actual": ("本年收入", "本年收入合计", "收入决算数"),
            "expenditure_actual": ("本年支出", "本年支出合计", "支出决算数"),
            "beginning_balance": ("年初结转和结余", "上年结转和结余"),
            "ending_balance": ("年末结转和结余", "结余分配", "使用非财政拨款结余"),
        },
        classification_type="summary",
    ),
    "FIN_02_income": TableRule(
        code="FIN_02_income",
        aliases=("收入决算表", "部门收入决算表", "收入预算总表", "部门收入预算总表"),
        required_headers=("合计", "财政拨款收入"),
        optional_headers=("事业收入", "经营收入", "其他收入"),
        measure_aliases={
            "total_actual": ("合计", "收入合计", "决算数"),
            "fiscal_allocation": ("财政拨款收入",),
            "superior_subsidy_income": ("上级补助收入",),
            "business_income": ("事业收入",),
            "operational_income": ("经营收入",),
            "other_income": ("其他收入",),
            "subsidiary_income": ("附属单位上缴收入",),
        },
        classification_type="income",
    ),
    "FIN_03_expenditure": TableRule(
        code="FIN_03_expenditure",
        aliases=("支出决算表", "部门支出决算表", "支出预算总表", "部门支出预算总表"),
        required_headers=("合计", "基本支出", "项目支出"),
        optional_headers=("上缴上级支出", "经营支出", "对附属单位补助支出"),
        measure_aliases={
            "total_actual": ("合计", "支出合计", "决算数"),
            "basic_actual": ("基本支出",),
            "project_actual": ("项目支出",),
            "to_superior_actual": ("上缴上级支出",),
            "operational_actual": ("经营支出",),
            "subsidiary_actual": ("对附属单位补助支出",),
        },
        classification_type="expenditure",
    ),
    "FIN_04_fiscal_grant_total": TableRule(
        code="FIN_04_fiscal_grant_total",
        aliases=(
            "财政拨款收入支出决算总表",
            "财政拨款收支决算总表",
            "财政拨款收支预算总表",
            "部门财政拨款收支预算总表",
            "财政拨款收入支出预算总表",
        ),
        required_headers=("一般公共预算财政拨款",),
        optional_headers=("政府性基金预算财政拨款", "国有资本经营预算财政拨款"),
        measure_aliases={
            # 决算版表头写「一般公共预算财政拨款」，预算版（如上海 2022）只写
            # 「一般公共预算 / 政府性基金预算 / 国有资本经营预算」。
            "general_public_budget": ("一般公共预算财政拨款", "一般公共预算"),
            "government_fund_budget": ("政府性基金预算财政拨款", "政府性基金预算"),
            "state_capital_budget": ("国有资本经营预算财政拨款", "国有资本经营预算"),
        },
        classification_type="summary",
    ),
    "FIN_05_general_public_expenditure": TableRule(
        code="FIN_05_general_public_expenditure",
        aliases=(
            "一般公共预算财政拨款支出决算表",
            "一般公共预算财政拨款支出表",
            "一般公共预算支出功能分类预算表",
            "部门一般公共预算支出功能分类预算表",
            "一般公共预算支出功能分类表",
        ),
        required_headers=("合计", "基本支出", "项目支出"),
        optional_headers=(),
        measure_aliases={
            "total_actual": ("合计", "支出合计", "决算数"),
            "basic_actual": ("基本支出",),
            "project_actual": ("项目支出",),
        },
        classification_type="function",
    ),
    "FIN_06_basic_expenditure": TableRule(
        code="FIN_06_basic_expenditure",
        aliases=(
            "一般公共预算财政拨款基本支出决算表",
            "基本支出决算明细表",
            "一般公共预算基本支出部门预算经济分类预算表",
            "一般公共预算基本支出经济分类预算表",
            "基本支出部门预算经济分类预算表",
            "基本支出预算经济分类表",
        ),
        required_headers=("人员经费", "公用经费"),
        optional_headers=("合计",),
        measure_aliases={
            "total_actual": ("合计", "决算数"),
            "personnel_actual": ("人员经费",),
            "public_actual": ("公用经费",),
        },
        classification_type="economic",
    ),
    "FIN_07_three_public": TableRule(
        code="FIN_07_three_public",
        aliases=(
            "财政拨款“三公”经费支出决算表",
            "一般公共预算财政拨款“三公”经费及机关运行经费支出决算表",
            "“三公”经费及机关运行经费支出决算表",
            "三公经费及机关运行经费支出决算表",
            "三公经费支出决算表",
            "三公经费表",
            "三公经费和机关运行经费预算表",
            "部门“三公”经费和机关运行经费预算表",
        ),
        required_headers=("合计", "因公出国(境)费"),
        optional_headers=("公务用车购置及运行维护费", "公务接待费"),
        measure_aliases={
            "budget": ("预算数", "年初预算数"),
            "actual": ("决算数", "支出决算数"),
            "total_actual": ("合计",),
            "overseas": ("因公出国(境)费", "因公出国（境）费"),
            "vehicle_purchase_operation": ("公务用车购置及运行维护费", "公务用车购置及运行费"),
            "vehicle_purchase": ("公务用车购置费",),
            "vehicle_operation": ("公务用车运行维护费", "公务用车运行费"),
            "reception": ("公务接待费",),
            # 只认带阶段词的列名：表题「…和机关运行经费预算表」会进入文字策略
            # 影子候选的表头，裸「机关运行经费」会让影子获得映射而成为第二实例。
            "org_operation_expense": ("机关运行经费预算数",),
        },
        classification_type="three_public",
        distinctive=("三公",),
        leaf_aliases={
            "vehicle_purchase_operation": (("公务用车购置及运行", "小计"), ("公务用车购置及运行", "合计")),
            "vehicle_purchase": (("公务用车购置及运行", "购置费"), ("公务用车购置及运行", "公务用车购置费")),
            "vehicle_operation": (
                ("公务用车购置及运行", "运行费"), ("公务用车购置及运行", "运行维护费"),
                ("公务用车购置及运行", "公务用车运行费"), ("公务用车购置及运行", "公务用车运行维护费"),
            ),
        },
    ),
    "FIN_08_gov_fund": TableRule(
        code="FIN_08_gov_fund",
        aliases=(
            "政府性基金预算财政拨款收入支出决算表",
            "政府性基金预算财政拨款支出决算表",
            "政府性基金预算支出功能分类预算表",
            "政府性基金预算财政拨款支出预算表",
        ),
        required_headers=("合计",),
        optional_headers=("基本支出", "项目支出"),
        measure_aliases={
            "beginning_balance": ("年初结转和结余", "年初结转结余", "上年结转和结余"),
            "ending_balance": ("年末结转和结余", "年末结转结余"),
            "income_actual": ("本年收入", "本年收入合计"),
            "total_actual": ("合计", "小计", "决算数"),
            "basic_actual": ("基本支出",),
            "project_actual": ("项目支出",),
        },
        classification_type="government_fund",
        distinctive=("政府性基金",),
    ),
    "FIN_09_state_capital": TableRule(
        code="FIN_09_state_capital",
        aliases=(
            "国有资本经营预算财政拨款收入支出决算表",
            "国有资本经营预算财政拨款支出决算表",
            "国有资本经营预算支出决算表",
            "国有资本经营预算支出功能分类预算表",
            "国有资本经营预算财政拨款支出预算表",
        ),
        required_headers=("合计",),
        optional_headers=("基本支出", "项目支出"),
        measure_aliases={
            "beginning_balance": ("年初结转和结余", "年初结转结余", "上年结转和结余"),
            "ending_balance": ("年末结转和结余", "年末结转结余"),
            "income_actual": ("本年收入", "本年收入合计"),
            "total_actual": ("合计", "小计", "决算数"),
            "basic_actual": ("基本支出",),
            "project_actual": ("项目支出",),
        },
        classification_type="state_capital",
        distinctive=("国有资本经营",),
    ),
}


def iter_header_aliases(rule: TableRule) -> Iterable[str]:
    for header in rule.required_headers:
        yield header
    for header in rule.optional_headers:
        yield header
    for aliases in rule.measure_aliases.values():
        for alias in aliases:
            yield alias


def match_measure(header_text: str, rule: TableRule) -> Optional[str]:
    normalized_header = normalize_text(header_text)
    best_measure: Optional[str] = None
    best_score = 0

    for measure, aliases in rule.measure_aliases.items():
        for alias in aliases:
            normalized_alias = normalize_text(alias)
            if not normalized_alias:
                continue
            score = fuzz.partial_ratio(normalized_header, normalized_alias)
            if normalized_alias in normalized_header:
                score = max(score, 95)
            if score > best_score and score >= 78:
                best_score = score
                best_measure = measure

    return best_measure


def detect_table_code(
    title: str,
    headers: Iterable[str],
    source_hint: Optional[str] = None,
) -> tuple[Optional[str], float]:
    normalized_title = normalize_text(title)
    normalized_hint = normalize_text(source_hint or "")
    normalized_headers = [
        normalize_text(header) for header in headers
        if normalize_text(header) and not _is_narrative_text(normalize_text(header))
        and len(normalize_text(header)) <= 80
    ]

    # A paragraph/definition may quote the complete table name and contain all
    # fiscal keywords. Neither fuzzy title similarity nor such "headers" is
    # evidence that the paragraph is the table itself.
    if _is_narrative_text(normalized_title):
        return None, 0.0

    canonical_hint = next(
        (code for code in NINE_TABLE_RULES if normalize_text(code) == normalized_hint),
        None,
    )
    if canonical_hint is not None:
        # The original parser can see the title above the grid. A second pass
        # over cells often sees only "收入 支出" or a continuation row; keep the
        # supplied identity rather than reclassifying it from that row.
        return canonical_hint, 0.92
    if _is_narrative_text(normalized_hint):
        normalized_hint = ""

    # 独占标记：某些表的关键词只可能出现自己身上（三公 / 政府性基金 /
    # 国有资本经营）。各地版式会把标题写长（如「一般公共预算财政拨款三公
    # 经费及机关运行经费支出决算表」），而「支出决算表」这类短泛别名在长
    # 标题上也能精确命中，按覆盖率比较会误抢。命中唯一标记时直接锁定表种。
    marker_codes: set[str] = set()
    if TITLE_MARKER_ENABLED:
        def markers_in(text: str) -> set[str]:
            return {
                code for code, rule in NINE_TABLE_RULES.items()
                if rule.distinctive and any(normalize_text(marker) in text for marker in rule.distinctive)
            }

        # 标记优先看标题。表头拼接的 hint 里，「政府性基金预算」「国有资本经营
        # 预算」也是财政拨款收支总表的列名；6 列版总表只剩其一时，hint 标记会
        # 把精确命中「财政拨款收支预算总表」的标题锁成 FIN_08。
        marker_codes = markers_in(normalized_title)
        title_has_alias = any(
            normalize_text(alias) in normalized_title
            for rule in NINE_TABLE_RULES.values() for alias in rule.aliases
        )
        if not marker_codes and not title_has_alias:
            marker_codes = markers_in(normalized_hint)
        if len(marker_codes) > 1:
            # 多个独占标记同时出现属于异常标题，不做强制，回退原有打分。
            marker_codes = set()
    active_rules = {
        code: rule
        for code, rule in NINE_TABLE_RULES.items()
        if not marker_codes or code in marker_codes
    }

    has_exact_alias = any(
        normalize_text(alias) in candidate
        for rule in active_rules.values() for alias in rule.aliases
        for candidate in (normalized_title, normalized_hint)
    )
    unit_breakdown = is_unit_breakdown_header(normalized_headers)

    best_code: Optional[str] = None
    best_score = 0.0
    # 特异性 tie-break：泛别名（如「支出决算表」）靠 partial_ratio 也能在
    # 「一般公共预算财政拨款支出决算表」上拿满分，与精确别名同分。
    # 此时标题对别名文本的覆盖率（exact_alias_coverage）更高者才是正确表码——
    # 否则样张 P14 的 FIN_05 会被先注册的 FIN_03 抢走（识别缺口根因）。
    best_exact_coverage = 0.0

    for code, rule in active_rules.items():
        alias_score = 0.0
        exact_alias_coverage = 0.0
        for alias in rule.aliases:
            normalized_alias = normalize_text(alias)
            alias_score = max(
                alias_score,
                fuzz.partial_ratio(normalized_title, normalized_alias) / 100,
                fuzz.partial_ratio(normalized_hint, normalized_alias) / 100,
            )
            if normalized_alias and (
                normalized_alias in normalized_title or normalized_alias in normalized_hint
            ):
                # 覆盖率按别名实际命中的目标计算：命中标题时以标题长度为分母，
                # 命中表头拼接时才用 hint 长度——统一用 max(len(title), len(hint))
                # 做分母会把长 hint 稀释掉精确别名信号（FIN_05 识别缺口根因之二）。
                if normalized_alias in normalized_title:
                    coverage_base = max(len(normalized_title), len(normalized_alias))
                else:
                    coverage_base = max(len(normalized_hint), len(normalized_alias))
                exact_alias_coverage = max(
                    exact_alias_coverage,
                    len(normalized_alias) / coverage_base,
                )
                alias_score = max(alias_score, 0.86 + min(0.12, exact_alias_coverage * 0.16))

        header_hits = 0
        header_hit_columns: set[int] = set()
        for header in rule.required_headers:
            normalized_header = normalize_text(header)
            matching_columns = {
                index for index, candidate in enumerate(normalized_headers) if (
                normalized_header in candidate
                or fuzz.ratio(candidate, normalized_header) >= 82
                )
            }
            if matching_columns:
                header_hits += 1
                header_hit_columns.update(matching_columns)

        required_score = (
            header_hits / len(rule.required_headers)
            if rule.required_headers
            else 0.0
        )
        if not exact_alias_coverage:
            if has_exact_alias or unit_breakdown:
                continue
            # Fuzzy words alone also match chapter headings, organization lists
            # and arbitrary body text. Retain fuzzy/OCR table candidates only
            # with a table-like heading and independent column-header support.
            table_heading = any(
                len(candidate) >= 4 and "表" in candidate
                for candidate in (normalized_title, normalized_hint)
            ) or _title_truncates_alias(normalized_title, rule)
            required_support = min(2, len(rule.required_headers))
            if (not table_heading or header_hits < required_support
                    or len(header_hit_columns) < required_support or len(normalized_headers) < 2):
                continue
        score = alias_score * 0.72 + required_score * 0.28
        if exact_alias_coverage:
            score = max(score, 0.86)
        if exact_alias_coverage >= 0.85:
            score = max(score, 0.98)
        elif exact_alias_coverage >= 0.7:
            score = max(score, 0.93)

        if score > best_score:
            best_score = score
            best_code = code
            best_exact_coverage = exact_alias_coverage
        elif (
            abs(score - best_score) < 0.02
            and exact_alias_coverage > best_exact_coverage + 0.15
        ):
            # 高分平局：标题与更特异别名完整匹配的规则胜出
            best_score = score
            best_code = code
            best_exact_coverage = exact_alias_coverage

    if best_score < 0.55:
        return None, best_score

    return best_code, min(best_score, 0.99)


def is_unit_breakdown_header(normalized_headers: Iterable[str]) -> bool:
    """按预算单位逐行列示的附加表（如「部门财政拨款支出预算明细表」）。

    行维度是序号/预算单位，列名与九表相同，但行不是科目，不能归入九表。
    """
    headers = list(normalized_headers)
    return (any(header in {"预算单位", "单位名称", "预算单位名称"} for header in headers)
            and not any("科目" in header or header == "项目" for header in headers))


_AUXILIARY_GRANT_TITLE_RE = re.compile(r"^(?:20\d{2}年度?)?(?:预算单位|部门|单位)?财政拨款支出预算表$")


def is_auxiliary_grant_expenditure_title(title: object) -> bool:
    """上海部分单位另附的「财政拨款支出预算表」（合计/人员经费/公用经费/项目支出）。

    表头与支出总表相似，但它不是九表之一，按表头归入 FIN_03 会错列。
    """
    first = str(title or "").strip().splitlines()[0] if str(title or "").strip() else ""
    return bool(_AUXILIARY_GRANT_TITLE_RE.match(normalize_text(first)))


_PREAMBLE_LABEL_RE = re.compile(r"(?:编制部门|编制单位|预算单位|决算单位|金额单位):|^单位:")
_PREAMBLE_TITLE_RE = re.compile(r"^(?:20\d{2}年度?)?[^\d,.]*表$")


def is_table_preamble_row(row: Iterable[object]) -> bool:
    """表格线框进来的表题、编制单位、金额单位行：既不是表头，也不是数据。

    单位版预算有的把这几行画在表格外框里，表头因此被挤到第 6、7 行。
    """
    texts = {normalize_text(str(value or "")) for value in row} - {""}
    return 0 < len(texts) <= 2 and all(
        _PREAMBLE_LABEL_RE.search(text) or _PREAMBLE_TITLE_RE.match(text) for text in texts)


def _title_truncates_alias(normalized_title: str, rule: TableRule, minimum: int = 8) -> bool:
    """标题以本表某个别名的开头收尾，即原件把长表题印截断了。

    如上海 2022 预算把「一般公共预算基本支出部门预算经济分类预算表」印成
    「2022年部门一般公共预算基本支出」。截断标题本身不含「表」字，只有与
    表头支撑同时成立时才作为表题证据。
    """
    for alias in rule.aliases:
        normalized_alias = normalize_text(alias)
        for length in range(len(normalized_alias) - 1, minimum - 1, -1):
            if normalized_title.endswith(normalized_alias[:length]):
                return True
    return False


def _is_narrative_text(normalized: str) -> bool:
    return bool(re.search(
        r"编制说明|情况说明|总体说明|名词(?:解释|释义)|术语(?:解释|定义)|定义:|"
        r"是指|指的是|表(?:中|内)|表:?(?:反映|指|是|用于|显示|列示)|根据.*表|"
        r"反映(?:单位|部门|本年|本单位)|主要原因|较上年|同比|[。；;]",
        normalized,
    ))
