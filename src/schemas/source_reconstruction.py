"""原件片段、候选格和已裁定逻辑格；原始值与裁定身份分别存储。"""
from __future__ import annotations
from dataclasses import dataclass,field,asdict
from typing import Any

@dataclass
class SourceToken:
    token_uid: str
    raw_text: str
    physical_page: int
    bbox: list[float] | None
    source_method: str
    raw_score: float | None = None
    source_ref: dict[str, Any] = field(default_factory=dict)
    def to_dict(self):return asdict(self)

@dataclass
class CellCandidate:
    candidate_uid: str
    source_cell_id: str
    table_candidate_uid: str
    physical_page: int
    bbox: list[float] | None
    raw_text: str
    extraction_method: str
    row_idx: int
    col_idx: int
    row_span: int = 1
    col_span: int = 1
    token_uids: list[str] = field(default_factory=list)
    def to_dict(self):return asdict(self)

@dataclass
class ResolvedLogicalCell:
    logical_cell_uid: str
    table_instance_uid: str
    source_cell_ids: list[str]
    row_idx: int
    col_idx: int
    physical_page: int
    bbox: list[float] | None
    raw_text: str
    value_kind: str
    decimal_value: str | None
    unit: str | None
    resolution_status: str
    semantic_identity: dict[str, Any] = field(default_factory=dict)
    provenance: list[dict] = field(default_factory=list)
    def to_dict(self):return asdict(self)
