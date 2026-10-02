"""统一解析产物与财政事实契约（ZCode 整改 ZC-01，任务包 §二.1）。

这一层是"同一份解析事实"的唯一权威定义：规则、结构化入库、证据、持久化
共同消费这里的对象，不再各持一套解析语义。

设计约定（任务包 §二.2）：

- ``table_code`` 是表种，不是实例 ID；同种表可以存在多个独立实例，
  实例身份由 ``instance_uid`` 承载。
- 原始金额与归一金额分别保存；JSON 序列化时 Decimal 用十进制字符串，
  浮点字段只作显示兼容，不参与计算。
- 空白、零、破折号、无此项、未识别分别处理（``CellPresence``），
  只有明确的表注或模板约定允许把特定符号解释为零。
- 问题的严重度与是否确认分开保存属于 ReviewReport/CheckReceipt 的职责；
  本模块只固定六态子检查状态常量。
- 模型识别分数保留为识别分数（confidence），不得冒充业务正确概率。
- 物理页从 1 开始；印刷页码独立保存；未知页码/位置保持 None。
"""

from __future__ import annotations

import dataclasses
import hashlib
import uuid
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# 六态子检查状态（任务包 §二.2，ZC-03 的回执消费此常量）
# ---------------------------------------------------------------------------

SUB_CHECK_STATUSES = (
    "pass",
    "fail",
    "not_applicable",
    "insufficient_data",
    "parse_error",
    "execution_error",
)

# ---------------------------------------------------------------------------
# 证据引用
# ---------------------------------------------------------------------------

#: 证据来源方式
EVIDENCE_SOURCE_METHODS = ("native_text", "ocr", "manual", "derived")
#: 证据验证状态：默认未验证；"已验证"必须由真实核验写入（ZC-00 证据门）
EVIDENCE_VERIFICATION_STATUSES = ("unverified", "verified", "failed")
#: 定位等级（任务包 §二.4）
LOCATION_LEVELS = ("exact_cell", "exact_span", "region", "unresolved")


@dataclass
class EvidenceRef:
    """一条可回溯的原文/单元格证据。

    ``evidence_id`` 由文件哈希 + 物理页 + 来源对象派生，同一来源稳定可复现；
    页码未知保持 ``None``，不得默认第 1 页（ZC-00 纪律延续）。
    """

    file_hash: str
    physical_page: Optional[int] = None
    printed_page: Optional[str] = None
    source_cell_id: Optional[str] = None
    source_block_id: Optional[str] = None
    quote: str = ""
    bbox: Optional[Tuple[float, float, float, float]] = None
    source_method: str = "native_text"
    verification_status: str = "unverified"
    location_level: str = "unresolved"

    def __post_init__(self) -> None:
        if self.source_method not in EVIDENCE_SOURCE_METHODS:
            raise ValueError(f"invalid evidence source_method: {self.source_method}")
        if self.verification_status not in EVIDENCE_VERIFICATION_STATUSES:
            raise ValueError(f"invalid verification_status: {self.verification_status}")
        if self.location_level not in LOCATION_LEVELS:
            raise ValueError(f"invalid location_level: {self.location_level}")
        if self.physical_page is not None and (
            not isinstance(self.physical_page, int)
            or isinstance(self.physical_page, bool)
            or self.physical_page < 1
        ):
            raise ValueError(f"invalid physical_page: {self.physical_page}")

    @property
    def evidence_id(self) -> str:
        payload = "|".join(
            str(part)
            for part in (
                self.file_hash,
                self.physical_page,
                self.source_cell_id or self.source_block_id or "",
                self.quote,
            )
        )
        return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]

    def to_dict(self) -> Dict[str, Any]:
        data = dataclasses.asdict(self)
        data["evidence_id"] = self.evidence_id
        data["bbox"] = list(self.bbox) if self.bbox else None
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EvidenceRef":
        bbox = data.get("bbox")
        return cls(
            file_hash=str(data.get("file_hash") or ""),
            physical_page=_opt_int(data.get("physical_page")),
            printed_page=_opt_str(data.get("printed_page")),
            source_cell_id=_opt_str(data.get("source_cell_id")),
            source_block_id=_opt_str(data.get("source_block_id")),
            quote=str(data.get("quote") or ""),
            bbox=tuple(float(item) for item in bbox) if bbox else None,
            source_method=str(data.get("source_method") or "native_text"),
            verification_status=str(data.get("verification_status") or "unverified"),
            location_level=str(data.get("location_level") or "unresolved"),
        )


# ---------------------------------------------------------------------------
# 单元格与表
# ---------------------------------------------------------------------------


class CellPresence:
    """单元格取值语义：空白/零/破折号/无此项/未识别分别处理。"""

    VALUE = "value"          # 有真实数值
    BLANK = "blank"          # 空白
    ZERO = "zero"            # 明确的 0（含 0.00）
    DASH = "dash"            # 破折号/长横线
    NOT_APPLICABLE = "not_applicable"  # "无此项"/"不涉及"
    UNRECOGNIZED = "unrecognized"      # OCR 或抽取失败

    @classmethod
    def detect(cls, raw_text: Optional[str]) -> str:
        text = str(raw_text or "").strip()
        if not text or text in {"—", "–", "-", "──", "--", "―"}:
            return cls.BLANK if not text else cls.DASH
        compact = text.replace(",", "").replace("，", "")
        if compact in {"0", "0.0", "0.00", "0.000", "0.0000"}:
            return cls.ZERO
        if any(token in text for token in ("无此项", "不涉及", "无。")):
            return cls.NOT_APPLICABLE
        return cls.VALUE


@dataclass
class ParsedCell:
    """统一单元格：原始值、单位与万元归一值并存（ZC-01 / SYS-15）。"""

    row_idx: int
    col_idx: int
    raw_text: str
    normalized_text: str
    numeric_value: Optional[float]
    page_number: int
    bbox: Optional[Tuple[float, float, float, float]]
    is_header: bool
    confidence: float
    unit_hint: Optional[str]
    extraction_method: str
    row_span: int = 1
    col_span: int = 1
    # ZC-01：万元归一值（Decimal），与 numeric_value（页面字面值）严格区分；
    # 序列化为十进制字符串。
    normalized_value_wan: Optional[Decimal] = None
    presence: str = CellPresence.VALUE

    def __post_init__(self) -> None:
        self.presence = CellPresence.detect(self.raw_text)
        # ZC-01（SYS-15）：单位换算在解析时完成一次——numeric_value 是页面字面值，
        # normalized_value_wan 是万元口径归一值；无单位信息时不猜口径（保持 None）。
        if self.normalized_value_wan is None and self.unit_hint and self.numeric_value is not None:
            from src.engine.amount_math import normalize_amount_to_wan

            self.normalized_value_wan = normalize_amount_to_wan(
                self.numeric_value, self.unit_hint
            )

    def to_dict(self) -> Dict[str, Any]:
        data = dataclasses.asdict(self)
        data["normalized_value_wan"] = (
            str(self.normalized_value_wan) if self.normalized_value_wan is not None else None
        )
        data["bbox"] = list(self.bbox) if self.bbox else None
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ParsedCell":
        raw_wan = data.get("normalized_value_wan")
        bbox = data.get("bbox")
        return cls(
            row_idx=int(data["row_idx"]),
            col_idx=int(data["col_idx"]),
            raw_text=str(data.get("raw_text") or ""),
            normalized_text=str(data.get("normalized_text") or ""),
            numeric_value=_opt_float(data.get("numeric_value")),
            page_number=int(data.get("page_number") or 1),
            bbox=tuple(float(item) for item in bbox) if bbox else None,
            is_header=bool(data.get("is_header")),
            confidence=float(data.get("confidence") or 0.0),
            unit_hint=_opt_str(data.get("unit_hint")),
            extraction_method=str(data.get("extraction_method") or "lines"),
            row_span=int(data.get("row_span") or 1),
            col_span=int(data.get("col_span") or 1),
            normalized_value_wan=Decimal(str(raw_wan)) if raw_wan is not None else None,
            presence=str(data.get("presence") or CellPresence.VALUE),
        )


@dataclass
class ParsedTable:
    """一个物理表片段（可能是一张表的整页部分，也可能是独立表）。

    跨页逻辑表实例见 :class:`LogicalTableInstance`；这里不再把"表种"当身份。
    """

    table_code: str
    title: str
    page_number: int
    table_index: int
    bbox: Tuple[float, float, float, float]
    header_row_count: int
    confidence: float
    extraction_method: str
    unit_hint: Optional[str]
    rows: List[List[str]]
    cells: List[ParsedCell]
    # ZC-01：片段身份。同一片段内的单元格属于同一 fragment_uid；
    # 跨页合并产生逻辑实例时保留各片段 uid 以便回溯。
    fragment_uid: str = field(default_factory=lambda: uuid.uuid4().hex)
    # AC-02：合并片段时保留被合并的原始物理片段 uid——逻辑实例可回溯到
    # 每一页的原始片段，不因合并而丢失来源身份。
    source_fragment_uids: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        data = dataclasses.asdict(self)
        data["bbox"] = list(self.bbox) if self.bbox else None
        data["cells"] = [cell.to_dict() for cell in self.cells]
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ParsedTable":
        return cls(
            table_code=str(data.get("table_code") or "UNKNOWN"),
            title=str(data.get("title") or ""),
            page_number=int(data.get("page_number") or 1),
            table_index=int(data.get("table_index") or 0),
            bbox=tuple(float(item) for item in data.get("bbox") or (0, 0, 0, 0)),
            header_row_count=int(data.get("header_row_count") or 0),
            confidence=float(data.get("confidence") or 0.0),
            extraction_method=str(data.get("extraction_method") or "lines"),
            unit_hint=_opt_str(data.get("unit_hint")),
            rows=[list(map(str, row)) for row in data.get("rows") or []],
            cells=[ParsedCell.from_dict(cell) for cell in data.get("cells") or []],
            fragment_uid=str(data.get("fragment_uid") or uuid.uuid4().hex),
            source_fragment_uids=[str(item) for item in data.get("source_fragment_uids") or []],
        )


@dataclass
class ParsedTextBlock:
    """原始页文字块，供规则上下文和证据定位共同引用。"""

    block_uid: str
    page_number: int
    text: str
    bbox: Optional[Tuple[float, float, float, float]] = None
    confidence: Optional[float] = None
    extraction_method: str = "native_text"

    def to_dict(self) -> Dict[str, Any]:
        data = dataclasses.asdict(self)
        data["bbox"] = list(self.bbox) if self.bbox else None
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ParsedTextBlock":
        bbox = data.get("bbox")
        return cls(
            block_uid=str(data.get("block_uid") or uuid.uuid4().hex),
            page_number=int(data.get("page_number") or 1),
            text=str(data.get("text") or ""),
            bbox=tuple(float(item) for item in bbox) if bbox else None,
            confidence=_opt_float(data.get("confidence")),
            extraction_method=str(data.get("extraction_method") or "native_text"),
        )


@dataclass
class OcrTableCandidate:
    """OCR 提取的候选表格；未完成身份及逐格置信度校验前不得参与规则计算。"""

    candidate_uid: str
    page_number: int
    rows: List[List[str]]
    render_bbox: Optional[Tuple[float, float, float, float]] = None
    page_bbox: Optional[Tuple[float, float, float, float]] = None
    coordinate_transform_id: Optional[str] = None
    verification_status: str = "unverified"
    extraction_method: str = "ocr"

    def to_dict(self) -> Dict[str, Any]:
        data = dataclasses.asdict(self)
        data["render_bbox"] = list(self.render_bbox) if self.render_bbox else None
        data["page_bbox"] = list(self.page_bbox) if self.page_bbox else None
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "OcrTableCandidate":
        render_bbox = data.get("render_bbox")
        page_bbox = data.get("page_bbox")
        return cls(
            candidate_uid=str(data.get("candidate_uid") or uuid.uuid4().hex),
            page_number=int(data.get("page_number") or 1),
            rows=[[str(cell or "") for cell in row] for row in data.get("rows") or []],
            render_bbox=tuple(float(item) for item in render_bbox) if render_bbox else None,
            page_bbox=tuple(float(item) for item in page_bbox) if page_bbox else None,
            coordinate_transform_id=_opt_str(data.get("coordinate_transform_id")),
            verification_status=str(data.get("verification_status") or "unverified"),
            extraction_method=str(data.get("extraction_method") or "ocr"),
        )


@dataclass
class LogicalTableInstance:
    """跨页逻辑表实例：同种表可有多个独立实例（ZC-01 / SYS-16）。

    ``instance_uid`` 是实例唯一身份；主体、年度、资金范围是身份的一部分——
    不同主体/年度的同种表不互相覆盖。
    """

    instance_uid: str
    table_code: str
    title: str
    fragment_uids: List[str]
    page_start: int
    page_end: int
    org_name: Optional[str] = None
    fiscal_year: Optional[int] = None
    fund_scope: Optional[str] = None
    row_count: int = 0
    confidence: float = 0.0
    column_mappings: List[Dict[str, Any]] = field(default_factory=list)
    #: 续表身份存在冲突（表头/单位/主体年度不一致）时置位，拼接结果不得参与确定性勾稽
    identity_conflict: bool = False
    conflict_reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "LogicalTableInstance":
        return cls(
            instance_uid=str(data.get("instance_uid") or uuid.uuid4().hex),
            table_code=str(data.get("table_code") or "UNKNOWN"),
            title=str(data.get("title") or ""),
            fragment_uids=[str(item) for item in data.get("fragment_uids") or []],
            page_start=int(data.get("page_start") or 1),
            page_end=int(data.get("page_end") or 1),
            org_name=_opt_str(data.get("org_name")),
            fiscal_year=_opt_int(data.get("fiscal_year")),
            fund_scope=_opt_str(data.get("fund_scope")),
            row_count=int(data.get("row_count") or 0),
            confidence=float(data.get("confidence") or 0.0),
            column_mappings=[dict(item) for item in data.get("column_mappings") or []],
            identity_conflict=bool(data.get("identity_conflict")),
            conflict_reasons=[str(item) for item in data.get("conflict_reasons") or []],
        )


# ---------------------------------------------------------------------------
# 财政事实
# ---------------------------------------------------------------------------


@dataclass
class FiscalFact:
    """统一财政事实：身份 + 原始值 + 归一值 + 证据（任务包 §五.1）。

    事实 ID 区分主体、年度、范围、指标和来源位置；不同来源的候选事实
    不因金额相同而合并。
    """

    fact_uid: str
    indicator: str                     # 指标（如 total_actual / basic_actual / san_gong_total）
    raw_value: Optional[str]           # 原文字符串（不改写）
    unit: Optional[str]                # 原始单位（元/万元/亿元…）
    normalized_value: Optional[Decimal]  # 万元归一值（Decimal）
    display_precision: Optional[int] = None
    org_name: Optional[str] = None
    org_scope: Optional[str] = None            # 合并范围：部门汇总/本级/单位
    fiscal_year: Optional[int] = None
    period: Optional[str] = None               # 本年/上年/年初/全年
    fund_scope: Optional[str] = None           # 全口径/财政拨款/一般公共预算/基金/国资
    budget_stage: Optional[str] = None         # 年初预算/调整后预算/全年预算/决算
    subject_code: Optional[str] = None         # 功能/经济分类编码或项目 ID
    subject_name: Optional[str] = None
    classification_level: Optional[int] = None
    parent_subject_code: Optional[str] = None
    hierarchy_path: List[str] = field(default_factory=list)
    table_code: Optional[str] = None
    instance_uid: Optional[str] = None
    row_order: Optional[int] = None
    recognition_status: str = "unverified"     # verified/missing/ambiguous/low_confidence/unverified
    confidence: Optional[float] = None
    evidence_ids: List[str] = field(default_factory=list)
    extra_dims: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        data = dataclasses.asdict(self)
        data["normalized_value"] = (
            str(self.normalized_value) if self.normalized_value is not None else None
        )
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "FiscalFact":
        normalized = data.get("normalized_value")
        return cls(
            fact_uid=str(data.get("fact_uid") or uuid.uuid4().hex),
            indicator=str(data.get("indicator") or ""),
            raw_value=_opt_str(data.get("raw_value")),
            unit=_opt_str(data.get("unit")),
            normalized_value=Decimal(str(normalized)) if normalized is not None else None,
            display_precision=_opt_int(data.get("display_precision")),
            org_name=_opt_str(data.get("org_name")),
            org_scope=_opt_str(data.get("org_scope")),
            fiscal_year=_opt_int(data.get("fiscal_year")),
            period=_opt_str(data.get("period")),
            fund_scope=_opt_str(data.get("fund_scope")),
            budget_stage=_opt_str(data.get("budget_stage")),
            subject_code=_opt_str(data.get("subject_code")),
            subject_name=_opt_str(data.get("subject_name")),
            classification_level=_opt_int(data.get("classification_level")),
            parent_subject_code=_opt_str(data.get("parent_subject_code")),
            hierarchy_path=[str(item) for item in data.get("hierarchy_path") or []],
            table_code=_opt_str(data.get("table_code")),
            instance_uid=_opt_str(data.get("instance_uid")),
            row_order=_opt_int(data.get("row_order")),
            recognition_status=str(data.get("recognition_status") or "unverified"),
            confidence=_opt_float(data.get("confidence")),
            evidence_ids=[str(item) for item in data.get("evidence_ids") or []],
            extra_dims=dict(data.get("extra_dims") or {}),
        )

    def identity_key(self) -> Tuple[Any, ...]:
        """事实身份键：主体+范围+年度+期间+资金+阶段+指标+科目+实例。

        金额**不**参与身份——不同来源的同名事实不因金额相同而互相覆盖。
        """
        return (
            self.org_name,
            self.org_scope,
            self.fiscal_year,
            self.period,
            self.fund_scope,
            self.budget_stage,
            self.indicator,
            self.subject_code,
            self.instance_uid,
        )


# ---------------------------------------------------------------------------
# 解析运行与整体产物
# ---------------------------------------------------------------------------


@dataclass
class ParsePageState:
    """逐页状态：文本层质量与识别风险。"""

    page_number: int
    has_text_layer: bool
    text_chars: int = 0
    is_scanned: bool = False
    rotation: int = 0
    note: str = ""
    ocr_status: str = "not_run"
    ocr_text_chars: int = 0
    ocr_table_count: int = 0
    ocr_error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ParsePageState":
        return cls(
            page_number=int(data.get("page_number") or 1),
            has_text_layer=bool(data.get("has_text_layer")),
            text_chars=int(data.get("text_chars") or 0),
            is_scanned=bool(data.get("is_scanned")),
            rotation=int(data.get("rotation") or 0),
            note=str(data.get("note") or ""),
            ocr_status=str(data.get("ocr_status") or "not_run"),
            ocr_text_chars=int(data.get("ocr_text_chars") or 0),
            ocr_table_count=int(data.get("ocr_table_count") or 0),
            ocr_error=_opt_str(data.get("ocr_error")),
        )


@dataclass
class ParseArtifact:
    """一次解析运行的完整产物（任务包 §二.1）。

    同一份 artifact 供规则、证据、结构化入库共同消费；
    ``parse_run_uid`` 绑定原件哈希、解析器、配置与 OCR 模型指纹。
    """

    source_file_hash: str
    parser_name: str = "pdfplumber"
    parser_fingerprint: str = ""
    config_fingerprint: str = ""
    ocr_model_fingerprint: Optional[str] = None
    parse_run_uid: str = field(default_factory=lambda: uuid.uuid4().hex)
    page_count: int = 0
    page_states: List[ParsePageState] = field(default_factory=list)
    text_blocks: List[ParsedTextBlock] = field(default_factory=list)
    ocr_table_candidates: List[OcrTableCandidate] = field(default_factory=list)
    table_fragments: List[ParsedTable] = field(default_factory=list)
    logical_tables: List[LogicalTableInstance] = field(default_factory=list)
    #: bbox 坐标系描述（统一 PDF 页面坐标；渲染/旋转/裁剪变换在此登记）
    coordinate_space: str = "pdf_page_points"
    coordinate_transforms: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_file_hash": self.source_file_hash,
            "parser_name": self.parser_name,
            "parser_fingerprint": self.parser_fingerprint,
            "config_fingerprint": self.config_fingerprint,
            "ocr_model_fingerprint": self.ocr_model_fingerprint,
            "parse_run_uid": self.parse_run_uid,
            "page_count": self.page_count,
            "page_states": [page.to_dict() for page in self.page_states],
            "text_blocks": [block.to_dict() for block in self.text_blocks],
            "ocr_table_candidates": [table.to_dict() for table in self.ocr_table_candidates],
            "table_fragments": [fragment.to_dict() for fragment in self.table_fragments],
            "logical_tables": [instance.to_dict() for instance in self.logical_tables],
            "coordinate_space": self.coordinate_space,
            "coordinate_transforms": self.coordinate_transforms,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ParseArtifact":
        return cls(
            source_file_hash=str(data.get("source_file_hash") or ""),
            parser_name=str(data.get("parser_name") or "pdfplumber"),
            parser_fingerprint=str(data.get("parser_fingerprint") or ""),
            config_fingerprint=str(data.get("config_fingerprint") or ""),
            ocr_model_fingerprint=_opt_str(data.get("ocr_model_fingerprint")),
            parse_run_uid=str(data.get("parse_run_uid") or uuid.uuid4().hex),
            page_count=int(data.get("page_count") or 0),
            page_states=[ParsePageState.from_dict(item) for item in data.get("page_states") or []],
            text_blocks=[ParsedTextBlock.from_dict(item) for item in data.get("text_blocks") or []],
            ocr_table_candidates=[
                OcrTableCandidate.from_dict(item) for item in data.get("ocr_table_candidates") or []
            ],
            table_fragments=[ParsedTable.from_dict(item) for item in data.get("table_fragments") or []],
            logical_tables=[
                LogicalTableInstance.from_dict(item) for item in data.get("logical_tables") or []
            ],
            coordinate_space=str(data.get("coordinate_space") or "pdf_page_points"),
            coordinate_transforms=dict(data.get("coordinate_transforms") or {}),
        )

    def cells(self) -> List[Tuple[ParsedTable, ParsedCell]]:
        """展开全部（片段, 单元格）对，供持久化与核验遍历。"""
        return [
            (fragment, cell)
            for fragment in self.table_fragments
            for cell in fragment.cells
        ]


# ---------------------------------------------------------------------------
# 检查回执与报告骨架（ZC-03/ZC-04 消费；ZC-01 先固定契约）
# ---------------------------------------------------------------------------


@dataclass
class CheckReceipt:
    """子检查回执：每个适用检查关系一条，不能用规则级成功替代。"""

    rule_id: str
    sub_check_id: str
    required_fact_ids: List[str] = field(default_factory=list)
    operand_fact_ids: List[str] = field(default_factory=list)
    formula: Optional[str] = None
    tolerance: Dict[str, Any] = field(default_factory=dict)
    status: str = "insufficient_data"
    incomplete_reason: Optional[str] = None
    issue_refs: List[str] = field(default_factory=list)
    obligation_id: Optional[str] = None
    evaluation_basis: str = "fiscal_fact"
    fact_migration_status: str = "complete"
    policy_version: str = "v3_3_portable"

    def __post_init__(self) -> None:
        if self.status not in SUB_CHECK_STATUSES:
            raise ValueError(f"invalid sub-check status: {self.status}")
        if self.evaluation_basis not in {"fiscal_fact", "legacy_document", "ai_candidate"}:
            raise ValueError(f"invalid evaluation_basis: {self.evaluation_basis}")
        if self.fact_migration_status not in {"complete", "pending", "not_applicable"}:
            raise ValueError(f"invalid fact_migration_status: {self.fact_migration_status}")

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CheckReceipt":
        return cls(
            rule_id=str(data.get("rule_id") or ""),
            sub_check_id=str(data.get("sub_check_id") or ""),
            required_fact_ids=[str(i) for i in data.get("required_fact_ids") or []],
            operand_fact_ids=[str(i) for i in data.get("operand_fact_ids") or []],
            formula=_opt_str(data.get("formula")),
            tolerance=dict(data.get("tolerance") or {}),
            status=str(data.get("status") or "insufficient_data"),
            incomplete_reason=_opt_str(data.get("incomplete_reason")),
            issue_refs=[str(i) for i in data.get("issue_refs") or []],
            obligation_id=_opt_str(data.get("obligation_id")),
            evaluation_basis=str(data.get("evaluation_basis") or "fiscal_fact"),
            fact_migration_status=str(data.get("fact_migration_status") or "complete"),
            policy_version=str(data.get("policy_version") or "v3_3_portable"),
        )


# ---------------------------------------------------------------------------
# 序列化辅助
# ---------------------------------------------------------------------------


def dumps_decimal(value: Optional[Decimal]) -> Optional[str]:
    """Decimal → 十进制字符串（JSON 契约：金额用字符串承载）。"""
    return str(value) if value is not None else None


def parse_decimal(raw: Any) -> Optional[Decimal]:
    """任意输入 → Decimal；失败返回 None（不抛异常，不猜值）。"""
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, Decimal):
        return raw
    try:
        return Decimal(str(raw).strip())
    except (InvalidOperation, ValueError, TypeError):
        return None


def _opt_str(value: Any) -> Optional[str]:
    text = str(value).strip() if value is not None else ""
    return text or None


def _opt_int(value: Any) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _opt_float(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
