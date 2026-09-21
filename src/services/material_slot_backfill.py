"""历史数据回填盘点：跑之前先知道"会回填成什么样"。

为什么必须先 dry-run
--------------------
历史任务的元数据质量参差不齐：有的没有组织 id，有的年份没认出来，
有的 ``doc_type`` 与 ``report_kind`` 互相矛盾。直接跑回填会把这些问题
一次性固化进数据库——错误的归属一旦写进去，就变成了"系统认定的历史事实"，
后面比没有数据更难纠正。

所以本模块只做**盘点**：把每条历史任务按同一套判定规则过一遍，
报告"多少能自动映射、多少必须人工确认、分别是什么原因"，
一条都不写库。

与线上路径共用同一套判定
------------------------
判定调用 ``material_slot_resolver.decide_slot_allocation``——与线上结构化入库
完全相同的那一个函数。如果两条路径各写一份规则，"dry-run 说能映射 80 条、
真跑只映射了 60 条"就会成为常态，报告也就失去了意义。

只回填可靠事实
--------------
可靠：组织 id 能在组织目录中解析、年份明确、文种明确且无冲突。
不可靠：年份未识别、文种冲突、组织低置信度或不在目录中、同名部门/单位无法确认。
不可靠的一律进 ``mapping_required``，**不为了提高回填率做经验猜测**。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from src.services import material_slot_resolver as resolver

#: 回填只认可这种命名形态的任务目录（32 位十六进制），
#: 避免把 ``uploads/putuo_final_samples/`` 这类样本目录当成任务。
_JOB_ID_HEX_LENGTH = 32


@dataclass
class BackfillScanItem:
    """单个历史任务的盘点结果。"""

    job_id: str
    filename: str = ""
    slot_key: Optional[str] = None
    status: str = resolver.DECISION_UNALLOCATABLE
    reason: str = resolver.REASON_NO_DOCUMENT_KEY
    fiscal_year: Optional[int] = None
    report_kind: str = "unknown"
    subject_org_id: Optional[str] = None
    subject_org_name: str = ""
    detail: Dict[str, Any] = field(default_factory=dict)

    @property
    def bucket(self) -> str:
        return resolver.REASON_BUCKETS.get(self.reason, "other")


@dataclass
class BackfillReport:
    """回填盘点报告。``dry_run`` 恒为 True：本模块不提供写入能力。"""

    dry_run: bool = True
    writes_performed: int = 0
    scanned_jobs: int = 0
    jobs_with_document: int = 0
    buckets: Dict[str, int] = field(default_factory=dict)
    reasons: Dict[str, int] = field(default_factory=dict)
    reason_labels: Dict[str, str] = field(default_factory=dict)
    projected_slots: int = 0
    projected_versions: int = 0
    ambiguous_same_name: int = 0
    auto_mappable_by_kind: Dict[str, int] = field(default_factory=dict)
    samples: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)
    items: List[BackfillScanItem] = field(default_factory=list)

    def to_dict(self, *, include_items: bool = False) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "dry_run": self.dry_run,
            "writes_performed": self.writes_performed,
            "scanned_jobs": self.scanned_jobs,
            "jobs_with_document": self.jobs_with_document,
            "projected_slots": self.projected_slots,
            "projected_versions": self.projected_versions,
            "ambiguous_same_name": self.ambiguous_same_name,
            "auto_mappable_by_kind": dict(sorted(self.auto_mappable_by_kind.items())),
            "buckets": dict(sorted(self.buckets.items())),
            "reasons": dict(sorted(self.reasons.items())),
            "reason_labels": dict(sorted(self.reason_labels.items())),
            "samples": self.samples,
        }
        if include_items:
            payload["items"] = [
                {
                    "job_id": item.job_id,
                    "filename": item.filename,
                    "slot_key": item.slot_key,
                    "status": item.status,
                    "reason": item.reason,
                    "bucket": item.bucket,
                    "fiscal_year": item.fiscal_year,
                    "report_kind": item.report_kind,
                    "subject_org_id": item.subject_org_id,
                    "subject_org_name": item.subject_org_name,
                    "detail": item.detail,
                }
                for item in self.items
            ]
        return payload


def is_job_directory_name(name: str) -> bool:
    """任务目录的判据：32 位十六进制。

    上传路径用 ``os.urandom(16).hex()`` 生成 job_id，形如
    ``3e33f44e1b1414ed064e0896c154363c``。``uploads/`` 下还有人工放置的样本目录，
    它们没有任务语义，混进来会让盘点数字虚高。
    """
    text = str(name or "").strip()
    if len(text) != _JOB_ID_HEX_LENGTH:
        return False
    return all(char in "0123456789abcdefABCDEF" for char in text)


def load_job_metadata(status_path: Path) -> Optional[Dict[str, Any]]:
    """读一条任务的 status.json；读不了返回 None（跳过而不是猜）。"""
    try:
        raw = status_path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def scan_upload_root(
    upload_root: Path,
    *,
    org_records: Optional[List[Dict[str, Any]]] = None,
    max_items: Optional[int] = None,
) -> BackfillReport:
    """盘点一个 uploads 目录，返回报告。**不做任何写入。**"""
    root = Path(upload_root)
    records = org_records if org_records is not None else resolver.load_org_records()

    report = BackfillReport()
    slot_keys: set = set()
    version_keys: set = set()

    if not root.is_dir():
        return report

    for job_dir in sorted(root.iterdir()):
        if not job_dir.is_dir() or not is_job_directory_name(job_dir.name):
            continue
        report.scanned_jobs += 1

        status = load_job_metadata(job_dir / "status.json")
        if status is None:
            item = BackfillScanItem(
                job_id=job_dir.name,
                reason=resolver.REASON_NO_DOCUMENT_KEY,
                detail={"status_json": "missing_or_unreadable"},
            )
            _record(report, item)
            continue

        checksum = _text(status.get("checksum"))
        if checksum:
            report.jobs_with_document += 1

        decision = resolver.decide_slot_allocation(
            metadata=status, org_records=records, checksum=checksum
        )
        identity = decision.identity
        item = BackfillScanItem(
            job_id=job_dir.name,
            filename=str(status.get("filename") or ""),
            slot_key=identity.slot_key if identity is not None else None,
            status=decision.status,
            reason=decision.reason,
            fiscal_year=identity.fiscal_year if identity is not None else None,
            report_kind=identity.report_kind if identity is not None else "unknown",
            subject_org_id=identity.subject_org_id if identity is not None else None,
            subject_org_name=decision.subject_org_name,
            detail=dict(decision.detail),
        )
        if bool(item.detail.get("same_name_ambiguous")):
            report.ambiguous_same_name += 1

        if identity is not None:
            slot_keys.add(identity.slot_key)
            # 版本维度用"槽位+校验和"：同一份 PDF 被分析多次只算一个版本，
            # 这与"重新分析不得生成新业务材料"是同一条纪律。
            version_keys.add((identity.slot_key, checksum or f"job:{job_dir.name}"))

        if decision.ok:
            kind = item.report_kind or "unknown"
            report.auto_mappable_by_kind[kind] = (
                report.auto_mappable_by_kind.get(kind, 0) + 1
            )

        _record(report, item)
        if max_items is not None and len(report.items) >= max_items:
            break

    report.projected_slots = len(slot_keys)
    report.projected_versions = len(version_keys)
    return report


def _record(report: BackfillReport, item: BackfillScanItem) -> None:
    report.items.append(item)
    report.buckets[item.bucket] = report.buckets.get(item.bucket, 0) + 1
    report.reasons[item.reason] = report.reasons.get(item.reason, 0) + 1
    report.reason_labels[item.reason] = resolver.REASON_LABELS.get(item.reason, item.reason)
    bucket_samples = report.samples.setdefault(item.bucket, [])
    if len(bucket_samples) < 5:
        bucket_samples.append(
            {
                "job_id": item.job_id,
                "filename": item.filename,
                "reason": item.reason,
                "fiscal_year": item.fiscal_year,
                "report_kind": item.report_kind,
                "subject_org_name": item.subject_org_name,
            }
        )


def render_markdown(report: BackfillReport, *, upload_root: str = "") -> str:
    """把盘点结果渲染成可读报告。"""
    lines: List[str] = []
    lines.append("# 材料槽位历史回填盘点（dry-run）")
    lines.append("")
    lines.append(
        "本报告**不写入任何数据**。所有数字都是「如果执行回填会怎样」的预演。"
    )
    lines.append("")
    lines.append(f"- 扫描目录：`{upload_root or '(未指定)'}`")
    lines.append(f"- 扫描任务目录数：{report.scanned_jobs}")
    lines.append(f"- 其中带文档校验和：{report.jobs_with_document}")
    lines.append(f"- 预计生成槽位：{report.projected_slots}")
    lines.append(f"- 预计生成文件版本绑定：{report.projected_versions}")
    lines.append(f"- 同名（部门/单位）歧义条数：{report.ambiguous_same_name}")
    lines.append(f"- 实际写入条数：{report.writes_performed}")
    lines.append("")

    lines.append("## 分桶统计")
    lines.append("")
    lines.append("| 分桶 | 条数 |")
    lines.append("| --- | --- |")
    for bucket, count in sorted(report.buckets.items(), key=lambda kv: -kv[1]):
        lines.append(f"| {bucket} | {count} |")
    lines.append("")

    lines.append("## 未自动映射原因")
    lines.append("")
    lines.append("| 原因码 | 说明 | 条数 |")
    lines.append("| --- | --- | --- |")
    for reason, count in sorted(report.reasons.items(), key=lambda kv: -kv[1]):
        label = report.reason_labels.get(reason, reason)
        lines.append(f"| `{reason}` | {label} | {count} |")
    lines.append("")

    if report.auto_mappable_by_kind:
        lines.append("## 可自动映射（按文种）")
        lines.append("")
        for kind, count in sorted(report.auto_mappable_by_kind.items()):
            lines.append(f"- {kind}: {count}")
        lines.append("")

    lines.append("## 样例")
    lines.append("")
    for bucket, samples in sorted(report.samples.items()):
        if bucket == "auto_mappable":
            continue
        lines.append(f"### {bucket}（{report.buckets.get(bucket, 0)} 条）")
        lines.append("")
        for sample in samples:
            name = sample.get("filename") or sample.get("job_id")
            lines.append(
                f"- `{sample.get('reason')}` — {name} "
                f"(年度={sample.get('fiscal_year')}, 文种={sample.get('report_kind')}, "
                f"主体={sample.get('subject_org_name') or '未记录'})"
            )
        lines.append("")

    return "\n".join(lines)


def _text(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    return text or None


def iter_slot_identities(report: BackfillReport) -> Iterable[str]:
    """报告里出现过的槽位键（去重、稳定排序），供人工比对。"""
    keys = {item.slot_key for item in report.items if item.slot_key}
    return sorted(keys)
