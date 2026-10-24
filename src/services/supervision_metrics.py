"""主管端纯统计口径：只确认当前材料，复核完成不等于整改完成。"""

from collections import Counter
from datetime import datetime, timezone
from typing import Any

CONFIRMED = frozenset({"confirmed", "in_package"})


def normalize_severity(value: Any) -> str:
    """沿用规则引擎的 legacy 映射；未知级别原样保留，不推断其风险。"""
    severity = str(value or "").strip().lower()
    return {"fatal": "critical", "error": "high", "warn": "medium",
            "warning": "medium", "hint": "low"}.get(severity, severity)


def normalize_finding(finding: dict) -> dict:
    # 只规范返回值与新快照；不能改写已经签署的历史复核记录。
    return {**finding, "severity": normalize_severity(finding.get("severity"))}


def json_value(value: Any, default: Any = None) -> Any:
    import json
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return default
    return default if value is None else value


def iso(value: Any) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else str(value) if value else None


def category(title: str) -> str:
    # 仅分类真实问题的标题；无法分类时保留“其他”，不猜规则编号。
    for name, words in (
        ("三公经费", ("三公", "公务接待", "公务用车", "因公出国")),
        ("绩效公开", ("绩效",)),
        ("公开要素完整性", ("缺失", "缺少", "未找到", "未披露", "缺表", "缺项")),
        ("比例与同比口径", ("占比", "比例", "同比", "增长", "减少", "年初预算", "完成率", "分母")),
        ("文字与科目表述", ("文字", "重复", "科目", "编码", "名称", "表述")),
        ("表内表间勾稽", ("合计", "勾稽", "金额", "数值", "总计", "一致", "支出")),
        ("公开要素完整性", ("说明", "公开")),
    ):
        if any(word in title for word in words):
            return name
    return "其他问题"


def summarize(items: list[dict], baseline_ids: set[str] | None) -> tuple[dict, list[dict]]:
    expected = [item for item in items if baseline_ids is not None and item["slot_id"] in baseline_ids
                and item["applicability_status"] != "not_applicable"]
    expected_ids = {item["slot_id"] for item in expected}
    groups: dict[str, dict] = {}
    for item in items:
        key = item.get("department_id") or item["organization_id"]
        group = groups.setdefault(key, {"id": key, "name": item.get("department_name") or item["organization_name"],
            "material_count": 0, "uploaded": 0, "reviewed": 0, "problem_materials": 0,
            "confirmed": 0, "severe": 0, "pending": 0, "expected": None if baseline_ids is None else 0,
            "received_expected": None if baseline_ids is None else 0,
            "missing": None if baseline_ids is None else 0, "repeat_materials": 0, "max_rounds": 0})
        group["material_count"] += 1
        group["uploaded"] += int(item["uploaded"])
        group["confirmed"] += item.get("confirmed_count") or 0
        group["severe"] += sum(normalize_severity(i.get("severity")) in {"critical", "high"} for i in item["findings"])
        group["pending"] += item.get("pending_count") or 0
        group["reviewed"] += int(item["review_status"] == "completed")
        group["problem_materials"] += int(item["review_status"] == "completed" and bool(item.get("confirmed_count")))
        group["repeat_materials"] += int(item["rounds_count"] >= 2 and bool(item.get("confirmed_count")))
        group["max_rounds"] = max(group["max_rounds"], item["rounds_count"])
        if baseline_ids is not None and item["slot_id"] in expected_ids:
            group["expected"] += 1
            group["received_expected"] += int(item["uploaded"])
            group["missing"] += int(not item["uploaded"])
    for group in groups.values():
        group["problem_rate"] = group["problem_materials"] / group["reviewed"] if group["reviewed"] else None
        # 判断“全部未交”只看已确认应交清单中的材料，不能拿历史任务凑分母。
        owed = [i for i in expected if (i.get("department_id") or i["organization_id"]) == group["id"]]
        group["empty"] = bool(owed) and all(not i["uploaded"] for i in owed) if baseline_ids is not None else None
        group["partial"] = bool(group["missing"]) and not group["empty"] if baseline_ids is not None else None
    counts = Counter(category(f["title"]) for item in items for f in item["findings"])
    unidentified = sum(max(0, (item.get("confirmed_count") or 0) - len(item["findings"])) for item in items)
    if unidentified:
        counts["历史确认问题未细分"] += unidentified
    summary = {
        "material_count": len(items), "uploaded": sum(i["uploaded"] for i in items),
        "expected": len(expected) if baseline_ids is not None else None,
        "received_expected": sum(i["uploaded"] for i in expected) if baseline_ids is not None else None,
        "missing": sum(not i["uploaded"] for i in expected) if baseline_ids is not None else None,
        "empty_departments": sum(bool(g["empty"]) for g in groups.values()) if baseline_ids is not None else None,
        "partial_departments": sum(bool(g["partial"]) for g in groups.values()) if baseline_ids is not None else None,
        "confirmed": sum(i.get("confirmed_count") or 0 for i in items),
        "pending": sum(i.get("pending_count") or 0 for i in items),
        "pending_checks": sum(i.get("pending_checks") or 0 for i in items),
        "unknown_coverage": sum(i["analyzed"] and i.get("pending_checks") is None for i in items),
        "unknown_analysis": sum(i["confirmed_count"] is None for i in items),
        "reviewed": sum(i["review_status"] == "completed" for i in items),
        "analyzed": sum(i["analyzed"] for i in items),
        "failed": sum(i["status"] == "failed" for i in items),
        "overdue_missing": sum(i["overdue"] for i in items),
        "repeat_materials": sum(i["rounds_count"] >= 2 and bool(i.get("confirmed_count")) for i in items),
        "unregistered_history": sum(i["history_incomplete"] for i in items),
        "department_count": len(groups), "unit_count": len({i["organization_id"] for i in items if i["subject_kind"] == "unit"}),
        "categories": [{"name": name, "count": count} for name, count in counts.most_common()],
    }
    return summary, list(groups.values())


def overdue(due_at: Any, uploaded: bool, applicability: str) -> bool:
    if not isinstance(due_at, datetime) or uploaded or applicability != "applicable":
        return False
    # 数据库日期有时区；无时区的旧记录不推断为逾期。
    return due_at.tzinfo is not None and datetime.now(timezone.utc) > due_at


def paginate(rows: list[dict], page: int, page_size: int) -> dict:
    start = (page - 1) * page_size
    return {"items": rows[start:start + page_size], "page": page, "page_size": page_size,
            "total": len(rows), "total_pages": (len(rows) + page_size - 1) // page_size}
