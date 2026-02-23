# engine/pipeline.py
from __future__ import annotations
import os, time
from typing import Dict, Any, List, Optional  # ✅ 增加 Optional
import pdfplumber

from .rules_v33 import ALL_RULES, build_document, order_and_number_issues, Issue

def _extract_tables_from_page(page) -> List[List[List[str]]]:
    # 返回：该页的多张表；每张表是 2D 数组（行→列）
    tables: List[List[List[str]]] = []
    try:
        t1 = page.extract_tables(table_settings={
            "vertical_strategy": "lines",
            "horizontal_strategy": "lines",
            "intersection_tolerance": 3,
            "min_words_vertical": 1,
            "min_words_horizontal": 1,
        }) or []
        tables += t1
    except Exception:
        pass
    try:
        if not tables:
            t2 = page.extract_tables() or []
            tables += t2
    except Exception:
        pass
    norm_tables: List[List[List[str]]] = []
    for tb in tables:
        norm_tables.append([[("" if c is None else str(c)).strip() for c in row] for row in (tb or [])])
    return norm_tables

def run_rules(doc, use_ai_assist=False):
    """
    执行规则检查
    :param doc: 文档对象
    :param use_ai_assist: 是否使用AI辅助
    """
    # 直接使用传统规则引擎，避免混合验证的异步问题
    import logging
    logger = logging.getLogger(__name__)
    logger.info(f"使用传统规则引擎，AI辅助: {use_ai_assist}")
    
    issues = []
    issues = []
    for rule_obj in ALL_RULES:
        try:
            # 兼容 ALL_RULES 中既有类又有实例的情况
            if isinstance(rule_obj, type):
                rule = rule_obj()
            else:
                rule = rule_obj
                
            # 如果规则支持AI辅助，传递参数
            if hasattr(rule, 'apply_with_ai') and use_ai_assist:
                issues.extend(rule.apply_with_ai(doc, use_ai_assist))
            else:
                issues.extend(rule.apply(doc))
        except Exception as e:
            # 如果是实例，获取code；如果是类，获取code属性
            code = getattr(rule_obj, 'code', 'UNKNOWN')
            if hasattr(rule_obj, '__class__') and hasattr(rule_obj.__class__, 'code'):
                code = rule_obj.__class__.code
                
            issues.append(Issue(
                rule=code, severity="hint",
                message=f"规则执行异常：{e}",
                location={"page": 1, "pos": 0}
            ))
    
    return order_and_number_issues(doc, issues)

# ===== 在此行下面粘贴 =====

def _issue_to_dict(x) -> dict:
    if isinstance(x, dict):
        rule_code = x.get("rule", "")
        return {
            "rule": rule_code,
            "rule_id": rule_code,  # 添加rule_id字段
            "severity": (x.get("severity") or "info"),
            "message": x.get("message", ""),
            "location": (x.get("location") or {}),
        }
    rule_code = getattr(x, "rule", "") or ""
    return {
        "rule": rule_code,
        "rule_id": rule_code,  # 添加rule_id字段
        "severity": getattr(x, "severity", None) or "info",
        "message": getattr(x, "message", "") or "",
        "location": getattr(x, "location", None) or {},
    }

def _norm_sev(s: Optional[str]) -> str:  # ✅ 参数改为 Optional[str]
    s = (s or "").lower()
    if s in ("error", "err", "fatal", "critical"):
        return "error"
    if s in ("warn", "warning"):
        return "warn"
    return "info"


def build_issues_payload(doc, use_ai_assist=False) -> dict:
    """
    把规则结果打包成前端需要的结构：
    {
      "issues": {
        "error": [...],
        "warn":  [...],
        "info":  [...],
        "all":   [...]
      }
    }
    """
    raw_list = run_rules(doc, use_ai_assist)  # List[Issue]
    items = [_issue_to_dict(x) for x in raw_list]
    for it in items:
        it["severity"] = _norm_sev(it.get("severity"))

    buckets = {"error": [], "warn": [], "info": []}
    for d in items:
        buckets[d["severity"]].append(d)
    buckets["all"] = items

    # 关键：返回必须带 "issues" 这个键
    return {"issues": buckets}
