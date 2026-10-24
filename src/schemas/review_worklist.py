"""人工复核清单：材料当前版本与复核进度，与分析任务状态分开。"""

from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class ReviewWorklistItem(BaseModel):
    item_id: str
    slot_id: Optional[str] = None
    job_id: Optional[str] = None
    document_version_id: Optional[int] = None
    filename: Optional[str] = None
    organization_id: Optional[str] = None
    organization_name: Optional[str] = None
    department_name: Optional[str] = None
    fiscal_year: Optional[int] = None
    report_kind: str = "unknown"
    review_status: str
    pending_findings: Optional[int] = None
    pending_checks: Optional[int] = None
    reviewer: Optional[str] = None
    reviewed_at: Optional[str] = None
    updated_at: Optional[str] = None


class ReviewWorklistData(BaseModel):
    items: List[ReviewWorklistItem] = Field(default_factory=list)
    counts: Dict[str, int] = Field(default_factory=dict)
    pending_total: int = 0
    viewer: str


class ReviewWorklistResponse(BaseModel):
    ok: bool = True
    data: ReviewWorklistData
