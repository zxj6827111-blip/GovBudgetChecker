"""年度公开时限（客户/财政通知下发的预算、决算公开截止日）。

审校与采集都要用它，但它不是推断出来的：公开时限每年随人大批准、财政批复日期变动，
区里年度通知又常常只写"编报"时间不写"公开"时间，所以这里只保存人工确认过的日期。
没有记录的年度，下游必须按"未知"处理，不得回落到任何默认月日。
"""

from __future__ import annotations

import datetime as dt
import fcntl
import json
import os
import threading
from pathlib import Path
from typing import Any, Dict, List

from pydantic import BaseModel, field_validator

KINDS = ("预算", "决算")
LEVELS = ("部门", "单位")

_lock = threading.RLock()


def calendar_path() -> Path:
    return Path(
        os.getenv(
            "DISCLOSURE_CALENDAR_PATH",
            str(Path(__file__).resolve().parents[2] / "data" / "disclosure-calendar.json"),
        )
    )


class CalendarEntry(BaseModel):
    """一条 (年度, 类别, 层级) 的公开截止日。deadline 留空表示"未确认"。"""

    year: int
    kind: str
    level: str
    deadline: str = ""
    source: str = ""
    note: str = ""

    @field_validator("year")
    @classmethod
    def validate_year(cls, value: int) -> int:
        if not 2000 <= int(value) <= 2100:
            raise ValueError("年度需在 2000-2100 之间")
        return int(value)

    @field_validator("kind")
    @classmethod
    def validate_kind(cls, value: str) -> str:
        value = (value or "").strip()
        if value not in KINDS:
            raise ValueError("类别只能是 预算 或 决算")
        return value

    @field_validator("level")
    @classmethod
    def validate_level(cls, value: str) -> str:
        value = (value or "").strip()
        if value not in LEVELS:
            raise ValueError("层级只能是 部门 或 单位")
        return value

    @field_validator("deadline")
    @classmethod
    def validate_deadline(cls, value: str) -> str:
        text = (value or "").strip().replace("年", "-").replace("月", "-").replace("日", "")
        text = text.replace("/", "-").replace(".", "-")
        if not text:
            return ""
        parts = [p for p in text.split("-") if p]
        if len(parts) != 3:
            raise ValueError("截止日格式应为 YYYY-MM-DD")
        try:
            date = dt.date(int(parts[0]), int(parts[1]), int(parts[2]))
        except ValueError as exc:
            raise ValueError(f"截止日不是有效日期: {exc}") from exc
        return date.isoformat()

    @field_validator("source", "note")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return (value or "").strip()

    def key(self) -> tuple[int, str, str]:
        return (self.year, self.kind, self.level)


def _read(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    with _lock, path.open(encoding="utf-8") as fh:
        try:
            payload = json.load(fh)
        except json.JSONDecodeError:
            return []
    rows = payload.get("rows") if isinstance(payload, dict) else payload
    return rows if isinstance(rows, list) else []


def _write(path: Path, rows: List[Dict[str, Any]]) -> None:
    ordered = sorted(rows, key=lambda r: (int(r.get("year") or 0), r.get("kind") or "", r.get("level") or ""))
    body = json.dumps({"rows": ordered}, ensure_ascii=False, indent=2) + "\n"
    with _file_lock(path):
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text(body, encoding="utf-8")
        os.replace(temp, path)


class _file_lock:
    def __init__(self, path: Path) -> None:
        self.path = path

    def __enter__(self):
        self.handle = self.path.with_suffix(".lock").open("a+b")
        fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc) -> None:
        fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        self.handle.close()


def load_rows(path: Path | None = None) -> List[Dict[str, Any]]:
    return _read(path or calendar_path())


def upsert(entry: CalendarEntry, path: Path | None = None) -> Dict[str, Any]:
    target = path or calendar_path()
    row = {**entry.model_dump(), "updated_at": dt.datetime.now().isoformat(timespec="seconds")}
    with _lock, _file_lock(target):
        rows = [r for r in _read(target) if CalendarEntry(**{k: r.get(k) for k in ("year", "kind", "level",
                "deadline", "source", "note")}).key() != entry.key()]
        rows.append(row)
        ordered = sorted(rows, key=lambda r: (int(r.get("year") or 0), r.get("kind") or "", r.get("level") or ""))
        temp = target.with_suffix(target.suffix + ".tmp")
        temp.write_text(json.dumps({"rows": ordered}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temp, target)
    return row


def resolve(kind: str, year: int, level: str, path: Path | None = None) -> str:
    """该年度该层级的截止日；未填返回空串，调用方必须按"未知"处理。"""
    for row in load_rows(path):
        if (str(row.get("kind")) == kind and int(row.get("year") or 0) == int(year)
                and str(row.get("level")) == level):
            return str(row.get("deadline") or "")
    return ""


def by_year(path: Path | None = None) -> Dict[str, Dict[str, Dict[str, Any]]]:
    """{年度: {"预算":{"部门":{...},"单位":{...}}, "决算":{...}}}，缺的层级不落键。"""
    out: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for row in load_rows(path):
        year = str(row.get("year") or "")
        kind = str(row.get("kind") or "")
        level = str(row.get("level") or "")
        if year and kind and level:
            out.setdefault(year, {}).setdefault(kind, {})[level] = row
    return out
