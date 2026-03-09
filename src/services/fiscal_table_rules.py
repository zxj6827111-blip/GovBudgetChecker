"""
Canonical rules for the nine fiscal tables.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata
from typing import Dict, Iterable, Optional

from rapidfuzz import fuzz

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
            "business_income": ("事业收入",),
            "operational_income": ("经营收入",),
            "other_income": ("其他收入",),
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
            "general_public_budget": ("一般公共预算财政拨款",),
            "government_fund_budget": ("政府性基金预算财政拨款",),
            "state_capital_budget": ("国有资本经营预算财政拨款",),
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
            "vehicle_purchase_operation": ("公务用车购置及运行维护费",),
            "vehicle_purchase": ("公务用车购置费",),
            "vehicle_operation": ("公务用车运行维护费",),
            "reception": ("公务接待费",),
        },
        classification_type="three_public",
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
            "total_actual": ("合计", "决算数"),
            "basic_actual": ("基本支出",),
            "project_actual": ("项目支出",),
        },
        classification_type="government_fund",
    ),
    "FIN_09_state_capital": TableRule(
        code="FIN_09_state_capital",
        aliases=(
            "国有资本经营预算财政拨款支出决算表",
            "国有资本经营预算支出决算表",
            "国有资本经营预算支出功能分类预算表",
            "国有资本经营预算财政拨款支出预算表",
        ),
        required_headers=("合计",),
        optional_headers=("基本支出", "项目支出"),
        measure_aliases={
            "total_actual": ("合计", "决算数"),
            "basic_actual": ("基本支出",),
            "project_actual": ("项目支出",),
        },
        classification_type="state_capital",
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
    normalized_headers = [normalize_text(header) for header in headers if normalize_text(header)]

    if "编制说明" in normalized_title:
        return None, 0.0

    best_code: Optional[str] = None
    best_score = 0.0

    for code, rule in NINE_TABLE_RULES.items():
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
                coverage_base = max(
                    len(normalized_title) or 1,
                    len(normalized_hint) or 1,
                    len(normalized_alias),
                )
                exact_alias_coverage = max(
                    exact_alias_coverage,
                    len(normalized_alias) / coverage_base,
                )
                alias_score = max(alias_score, 0.86 + min(0.12, exact_alias_coverage * 0.16))

        header_hits = 0
        for header in rule.required_headers:
            normalized_header = normalize_text(header)
            if any(
                normalized_header in candidate
                or fuzz.partial_ratio(candidate, normalized_header) >= 82
                for candidate in normalized_headers
            ):
                header_hits += 1

        required_score = (
            header_hits / len(rule.required_headers)
            if rule.required_headers
            else 0.0
        )
        score = alias_score * 0.72 + required_score * 0.28
        if exact_alias_coverage >= 0.85:
            score = max(score, 0.98)
        elif exact_alias_coverage >= 0.7:
            score = max(score, 0.93)

        fin_code = [part.lower() for part in code.split("_", 2)[:2]]
        hint_match = all(part in normalized_hint for part in fin_code)
        if hint_match:
            score = max(score, 0.92)

        if score > best_score:
            best_score = score
            best_code = code

    if best_score < 0.55:
        return None, best_score

    return best_code, min(best_score, 0.99)
