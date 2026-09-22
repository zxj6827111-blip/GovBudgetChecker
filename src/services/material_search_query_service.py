"""全局材料搜索（WP2-C）：查询解析、SQL 匹配、命中归因与当前页明细。

职责边界
--------
本模块**只做三件事**：``parse`` / ``match`` / ``explain``。不建槽位、不改状态、
不写复核结论，也不读文件系统：搜索请求既不看 ``uploads/`` 目录，也不读任何
``status.json`` —— 材料与任务的关系全部来自台账四张表的 SQL（§二十七）。

为什么搜索对象必须是 Material Slot
----------------------------------
仓库里已经有"所有跑过的任务"这个视角（``/api/jobs`` + 任务历史页），
但那个视角回答不了"我要找的那份材料在哪"：同一个单位、同一年度、同一文种的
材料会经过多次运行，按任务看就是一堆长得差不多的条目。全局搜索因此**按槽位查库、
按槽位返回**；用户拿 job id 来搜时，先把它精确关联到文件版本再定位槽位
（§九/§二十三）。禁止把前端 ``filter()`` 全量任务列表当成搜索实现（§十）。

三条不可让步的纪律
------------------
1. **未知不猜。** 关系（本部/本级）解析不出来时不退化成"主体层级是单位"——
   直属单位也是单位；此时该约束命中不了任何槽位，并在 meta 里如实标注
   ``relationship_resolution='unavailable'``（§十八）。
2. **历史命中不改写当前真值。** 命中历史文件名/历史运行只影响 ``matched_*``
   与命中原因，``current_document_version_id`` 永远来自槽位指针（§二十一/§二十二）。
3. **匹配语义只有一处。** WHERE 里的 token 匹配、ORDER BY 里的精确匹配、
   Python 侧的命中归因（``matched_fields``）必须表达同一件事：忽略大小写的子串。
   前两者在 SQL，后者在 Python —— 由 ``escape_like`` / ``ilike_literal`` 这一对
   函数承担，且两侧都有反例测试（``%``/``_`` 按普通字符处理，§二十）。

SQL 预算（§三十二/§六十六）
---------------------------
- 主查询 **1 条**：匹配 + 排序 + 分页，``COUNT(*) OVER ()`` 顺带给出 total；
- 当前页明细 **1 条**：本页 ≤50 个槽位的文件版本与运行（当前运行、命中版本、
  命中任务、review candidate 全从这一条里算），不存在"每条结果再查一次"的 N+1；
- 越界页兜底 **+1 条 COUNT**：请求的页码超出范围时主查询返回 0 行，
  ``COUNT(*) OVER ()`` 没有行可以承载 total，此时不补一条计数就会把
  "第 3 页空"报成"一条都没搜到"。这是唯一一条额外 SQL，且只在越界页发生。

关于全文检索基础设施（§六十七）
--------------------------------
本轮不引入 ``pg_trgm`` / Elasticsearch / Meilisearch。匹配走参数化 ILIKE +
``fiscal_document_versions(slot_id)`` / ``analysis_jobs(job_uuid)`` 既有索引。
按 1 万级槽位估算，主查询的额外开销是"每个候选行 × 每个 token"的少数几次
索引探查（文件名 EXISTS 与 job-uuid EXISTS 各走一条索引），属于可接受范围。
如果将来 EXPLAIN 证明必须上扩展，那是**报告**，不是擅自加基础设施。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from src.schemas.material_search import (
    MaterialSearchItem,
    MaterialSearchReviewCandidate,
)
from src.services.material_detail_query_service import (
    _json_dict,
    select_current_run,
)
from src.services.material_ledger_query_service import is_head_unit_name, relationship_of

# ---- 查询解析（§十三~§二十） -------------------------------------------------

#: 明确的 4 位财政年度 token。与材料接口的 ``fiscal_year`` 查询参数同一区间
#: （``ge=2000, le=2099``）：解析得出的年度必须能被接口自己再表达一次。
_FISCAL_YEAR_RE = re.compile(r"^20\d{2}$")

#: 文种别名（查询解析用别名，**不修改数据库里的 report_kind**，§十五）。
REPORT_KIND_ALIASES: Dict[str, str] = {
    "预算": "budget",
    "预算报告": "budget",
    "budget": "budget",
    "决算": "final",
    "决算报告": "final",
    "final": "final",
}

#: 关系别名。本轮只支持"本部/本级"这一种 refinement（§十一）：
#: 其余取值（department_summary / subordinate_unit）没有可靠的查询词，
#: 与其猜一个词，不如不支持——搜索结果的命中原因必须可解释。
RELATIONSHIP_ALIASES: Dict[str, str] = {
    "本部": "head_unit",
    "本级": "head_unit",
    "本级单位": "head_unit",
    "head_unit": "head_unit",
}

#: 查询串长度约束（q trim 之后）。上限与前端提示同一份口径，
#: 由路由层的校验依赖保证，这里只做解析。
MIN_QUERY_LENGTH = 2
MAX_QUERY_LENGTH = 200

#: 文本 token 上限。超过就拒绝（422）而不是静默丢弃 token ——
#: 丢弃会改变 AND 语义：用户以为"这些词都要命中"，实际只命中了前几个。
MAX_TEXT_TOKENS = 8

#: ``analysis_jobs.job_uuid`` 是 ``VARCHAR(36)``。比它长的 token 不可能等于
#: 任何 job_uuid，因此这类 token **不生成** job 匹配子查询：这是一个不可能
#: 产生假阴性的剪枝（长度约束来自列定义本身，不是猜出来的形状规则）。
MAX_JOB_UUID_LENGTH = 36

#: 精确关联字段在 JSONB 里的路径。与 WP2-B 的处理记录逐字一致：
#: 两处各写一份，迟早出现"处理记录挂得上、搜索搜不到"。
#:
#: **按别名参数化**而不是写成常量：本模块有三处语句引用 ``analysis_jobs``
#: （token 的 EXISTS 用 ``tj``、排序表达式用 ``oj``、当前页明细用 ``j``），
#: 常量里写死一个别名，换一处引用就会得到"表 tj 丢失 FROM 子句项"这种
#: 只有真库才报得出来的错误（假连接不解析 SQL）。真库用例已覆盖该形态。
def _structured_version_path(alias: str) -> str:
    return f"({alias}.metadata -> 'structured_ingest' ->> 'document_version_id')"


def _numeric_guard(alias: str) -> str:
    """``document_version_id`` 必须是纯数字才允许参与 bigint 比较。

    没有这个守卫，一条写坏的 metadata 会让整条查询抛 cast 异常，
    而不是"这次运行不参与关联"（fail-closed）。
    """
    return f"{_structured_version_path(alias)} ~ '^[0-9]+$'"

#: LIKE 转义字符。用户文本里的 ``%`` ``_`` ``\`` 一律按普通字符处理（§二十），
#: 因此模式串先转义再交给参数化查询，SQL 侧显式声明 ``ESCAPE '\'``。
_LIKE_ESCAPE = "\\"


class MaterialSearchQueryError(ValueError):
    """查询串本身自相矛盾或超出可解析范围。

    消息**只含静态文案**（不含用户输入）：它会顺着 ``HTTPException(detail=...)``
    进入响应体与日志，按仓库口径运行时值一律不进 message
    （``scripts/check_log_message_safety.py`` 规则 4）。
    """


@dataclass(frozen=True)
class MaterialSearchQuery:
    """解析后的查询串。

    ``text_tokens`` 是**有效 token**：年度/文种/关系词都已经被消费掉，
    不会再当成普通文本去模糊匹配（§十四：``2024`` 就是财政年度，
    不是"某个字段里含 2024"）。
    """

    raw: str
    fiscal_year: Optional[int]
    report_kind: Optional[str]
    relationship: Optional[str]
    text_tokens: Tuple[str, ...]


def parse_search_query(raw: str) -> MaterialSearchQuery:
    """把用户输入拆成 年度 / 文种 / 关系 / 文本 token（§十三）。

    规则：

    - 明确的 4 位年份（2000–2099）-> ``fiscal_year``；
    - 文种别名 -> ``report_kind``（预算/预算报告/budget；决算/决算报告/final）；
    - 关系别名 -> ``relationship``（本部/本级）；
    - 其余 -> 文本 token，按空白切分、**大小写不敏感去重**；
    - 两个不同的年度 / 两种不同的文种 = 自相矛盾的查询，抛错而不是取其一
      （取其一会让用户拿到"看起来对、其实是另一年"的结果）。
    """
    tokens = [token for token in re.split(r"\s+", str(raw or "").strip()) if token]

    fiscal_year: Optional[int] = None
    report_kind: Optional[str] = None
    relationship: Optional[str] = None
    text: List[str] = []

    for token in tokens:
        if _FISCAL_YEAR_RE.match(token):
            value = int(token)
            if fiscal_year is not None and fiscal_year != value:
                raise MaterialSearchQueryError("查询中的财政年度互相矛盾，请只保留一个年度")
            fiscal_year = value
            continue

        lowered = token.lower()
        kind = REPORT_KIND_ALIASES.get(lowered)
        if kind is not None:
            if report_kind is not None and report_kind != kind:
                raise MaterialSearchQueryError("查询中的文种互相矛盾，请只保留预算或决算之一")
            report_kind = kind
            continue

        relation = RELATIONSHIP_ALIASES.get(lowered)
        if relation is not None:
            if relationship is not None and relationship != relation:
                raise MaterialSearchQueryError("查询中的关系修饰词互相矛盾")
            relationship = relation
            continue

        text.append(token)

    deduped: List[str] = []
    seen: set = set()
    for token in text:
        key = token.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(token)

    if len(deduped) > MAX_TEXT_TOKENS:
        raise MaterialSearchQueryError("查询词过多，请缩减到 8 个词以内")

    return MaterialSearchQuery(
        raw=str(raw or "").strip(),
        fiscal_year=fiscal_year,
        report_kind=report_kind,
        relationship=relationship,
        text_tokens=tuple(deduped),
    )


# ---- 匹配语义（SQL 与 Python 唯一的一对实现） --------------------------------


def escape_like(value: Any) -> str:
    r"""把用户文本转成"按普通字符匹配"的 ILIKE 模式串（§二十）。

    ``%`` 变成"全库"，``_`` 变成"任意一个字符" —— 后者更隐蔽：搜 ``unit_a``
    会连 ``unitXa`` 一起返回。两者都必须转义。反斜杠自身要先转义，
    否则用户输入的 ``\%`` 会绕过转义把 ``%`` 又变回通配符。
    """
    text = str(value or "")
    escaped = text.replace(_LIKE_ESCAPE, _LIKE_ESCAPE * 2)
    escaped = escaped.replace("%", f"{_LIKE_ESCAPE}%").replace("_", f"{_LIKE_ESCAPE}_")
    return f"%{escaped}%"


def ilike_literal(field: Any, token: Any) -> bool:
    """Python 侧的"忽略大小写子串"判定，与 SQL 的 ``ILIKE`` 同义。

    因为模式串由 ``escape_like`` 转义过，``%``/``_`` 都是普通字符，
    "ILIKE 模式"与"子串包含"在这套输入下等价。命中归因
    （``matched_fields``）必须与 WHERE 用同一语义，否则会出现
    "搜索命中了、但界面说不出为什么命中"。
    """
    haystack = str(field or "").lower()
    needle = str(token or "").lower()
    return bool(needle) and needle in haystack


def head_unit_subject_ids(records: Sequence[Any]) -> List[str]:
    """组织目录里"部门本级/本部"的**主体 id** 集合（§十七）。

    只复用 WP2-A 的 ``is_head_unit_name`` 判定，不另写一套名称规则：

    - 只看 ``level == 'unit'`` 的节点（部门自己是 department_summary，不是本部）；
    - 单位向上找到最近的 ``department`` 祖先，把"单位名 == 部门名"或
      "带本级/本部标志"的判为本部；
    - 找不到部门祖先的孤立单位**不进**本部集合：只有名称标志一种证据时由
      ``is_head_unit_name`` 自己决定，本函数不替它猜层级。

    返回排序后的 id 列表：SQL 参数与测试断言因此都是确定的。
    """
    by_id = {
        str(getattr(record, "id", "") or ""): record
        for record in records
        if str(getattr(record, "id", "") or "")
    }

    result: List[str] = []
    for org_id, record in by_id.items():
        if str(getattr(record, "level", "") or "") != "unit":
            continue
        unit_name = str(getattr(record, "name", "") or "").strip()
        if not unit_name:
            continue

        department_name: Optional[str] = None
        seen: set = set()
        parent_id = str(getattr(record, "parent_id", "") or "")
        while parent_id and parent_id not in seen:
            seen.add(parent_id)
            parent = by_id.get(parent_id)
            if parent is None:
                break
            if str(getattr(parent, "level", "") or "") == "department":
                department_name = str(getattr(parent, "name", "") or "").strip() or None
                break
            parent_id = str(getattr(parent, "parent_id", "") or "")

        if is_head_unit_name(unit_name, department_name):
            result.append(org_id)

    return sorted(result)


# ---- 过滤条件 ---------------------------------------------------------------


@dataclass(frozen=True)
class MaterialSearchFilters:
    """搜索的**权限**与**解析结果**（年度/文种/关系不在本类里）。

    年度、文种、关系这三个条件**只**存在于 ``MaterialSearchQuery``：它们是
    "用户输入解析出来的东西"。把它们再抄一份到这里，就会出现两份真值，
    而两份真值必然漂移（本类曾经同时持有 ``fiscal_year``，
    真库用例当场抓到"查 2024 把 fiscal_year=NULL 的槽位也返回了"）。
    所以这里只放两样东西：

    ``head_unit_org_ids``
        "本部/本级"解析出来的主体 id 集合。目录不可用时是**空集合**
        （不是 None、更不是"所有单位"）：空集合命中不了任何槽位，
        也就是"不给关系命中"，fail-closed（§十八）。"解析失败"与
        "目录里确实没有本部"的区别由响应的 meta 表达。

    ``visible_org_ids``
        与 WP2-A 逐字一致：``None`` = 管理员不过滤；列表 = 只保留三个组织列
        任一命中的槽位。**没有任何授权时传空列表**（而不是 None），
        空列表命中不了任何行。
    """

    head_unit_org_ids: Sequence[str] = ()
    visible_org_ids: Optional[Sequence[str]] = None


class _ParamBag:
    """按顺序收集参数并返回 ``$n`` 占位符。

    单独一个小类是因为本模块的 WHERE 需要**同一个参数被引用多次**
    （一条 ILIKE 模式要同时用于区县名/部门名/主体名/文件名四个字段），
    而 WP2-A 的 ``MaterialSlotFilters`` 是"一个条件一个参数"的形态。
    """

    __slots__ = ("params",)

    def __init__(self) -> None:
        self.params: List[Any] = []

    def add(self, value: Any) -> str:
        self.params.append(value)
        return f"${len(self.params)}"


def _token_job_exists(token_index: str) -> str:
    """token 是否命中"该槽位某个文件版本上精确关联的分析任务"。

    关联依据与 WP2-B 的处理记录**完全一致**：``analysis_jobs.metadata`` 的
    ``structured_ingest.document_version_id``。不按文件名、不按组织名、
    不按时间接近、不按目录名猜（§二十三）。``job_uuid`` 等值比较走
    ``idx_jobs_uuid``，因此这里的 EXISTS 是"按索引取一行再核验归属"。
    """
    return (
        "EXISTS (SELECT 1 FROM analysis_jobs tj"
        " JOIN fiscal_document_versions tv2"
        f"   ON tv2.id = {_structured_version_path('tj')}::bigint"
        f"  AND {_numeric_guard('tj')}"
        f" WHERE tj.job_uuid = {token_index} AND tv2.slot_id = s.id)"
    )


def build_search_where(
    query: MaterialSearchQuery, *, filters: MaterialSearchFilters
) -> Tuple[str, List[Any]]:
    """生成 WHERE 片段与参数（纯函数，可离线穷举反例）。

    结构（§十九）：

    - 结构化条件之间 AND；
    - **每个文本 token 必须命中至少一个允许搜索的字段**；
    - 字段之间 OR：区县名 / 主管部门名 / 主体名 / 任一文件版本的文件名 /
      任一精确关联任务的 job_uuid。

    年度/文种/关系取自 ``query``（解析结果的唯一持有者），
    不取自 ``filters``：两处各存一份必然出现"查询串说的是 2024、
    SQL 里没有这个条件"这类静默错位。
    """
    bag = _ParamBag()
    clauses: List[str] = []

    if query.fiscal_year is not None:
        clauses.append(f"s.fiscal_year = {bag.add(int(query.fiscal_year))}")

    if query.report_kind in ("budget", "final"):
        clauses.append(f"s.report_kind = {bag.add(query.report_kind)}")

    if query.relationship == "head_unit":
        # 目录不可用时是空集合：``ANY('{}')`` 命中不了任何行，
        # 也就是"不给关系命中"，而不是悄悄退化成"所有单位"（§十八）。
        ids = [str(value) for value in filters.head_unit_org_ids]
        clauses.append(f"s.subject_org_id = ANY({bag.add(ids)}::text[])")

    if filters.visible_org_ids is not None:
        # 权限谓词与 WP2-A 的三列 OR 逐字同源：授权的可能是区县、部门或单位。
        # 写成 AND 会把 department / unit 授权全部误杀。
        index = bag.add([str(value) for value in filters.visible_org_ids])
        clauses.append(
            f"(s.jurisdiction_org_id = ANY({index}::text[])"
            f" OR s.department_org_id = ANY({index}::text[])"
            f" OR s.subject_org_id = ANY({index}::text[]))"
        )

    for token in query.text_tokens:
        pattern = bag.add(escape_like(token))
        fields = [
            f"s.jurisdiction_name ILIKE {pattern} ESCAPE '{_LIKE_ESCAPE}'",
            f"s.department_name ILIKE {pattern} ESCAPE '{_LIKE_ESCAPE}'",
            f"s.subject_org_name ILIKE {pattern} ESCAPE '{_LIKE_ESCAPE}'",
            "EXISTS (SELECT 1 FROM fiscal_document_versions tv"
            f" WHERE tv.slot_id = s.id AND tv.original_filename ILIKE {pattern}"
            f" ESCAPE '{_LIKE_ESCAPE}')",
        ]
        if len(token) <= MAX_JOB_UUID_LENGTH:
            fields.append(_token_job_exists(bag.add(token)))
        clauses.append("(" + " OR ".join(fields) + ")")

    return (" AND ".join(clauses) if clauses else "TRUE"), bag.params


def build_search_order(query: MaterialSearchQuery, bag: _ParamBag) -> str:
    """稳定排序（§三十）：精确命中优先，其次最近更新，最后 id 兜底。

    优先级：job id 精确匹配 > 文件名精确匹配 > 单位名精确匹配 >
    主管部门名精确匹配 > 更新时间倒序 > slot_id。

    为什么不做"相关度打分"：分数的权重是拍出来的，而这几级优先级是**能复现的
    事实**（精确相等 vs 子串）。用户刷新页面时顺序不变，是搜索可信的前提。
    """
    if not query.text_tokens:
        return "s.updated_at DESC NULLS LAST, s.id"

    tokens = bag.add(list(query.text_tokens))
    job_tokens = bag.add(
        [token for token in query.text_tokens if len(token) <= MAX_JOB_UUID_LENGTH]
    )

    unit_exact = (
        f"EXISTS (SELECT 1 FROM unnest({tokens}::text[]) AS tk"
        " WHERE lower(coalesce(s.subject_org_name, '')) = lower(tk))"
    )
    department_exact = (
        f"EXISTS (SELECT 1 FROM unnest({tokens}::text[]) AS tk"
        " WHERE lower(coalesce(s.department_name, '')) = lower(tk))"
    )
    filename_exact = (
        f"EXISTS (SELECT 1 FROM unnest({tokens}::text[]) AS tk WHERE EXISTS ("
        " SELECT 1 FROM fiscal_document_versions fv"
        " WHERE fv.slot_id = s.id"
        " AND lower(coalesce(fv.original_filename, '')) = lower(tk)))"
    )
    # job 精确匹配用**大小写敏感**的等值比较，与 WHERE 里的 ``tj.job_uuid = $n``
    # 逐字一致：两处若一个敏感一个不敏感，会出现"排序把它当成精确命中、
    # 但它是靠别的字段才进来的"这种无法解释的结果。
    job_exact = (
        f"EXISTS (SELECT 1 FROM unnest({job_tokens}::text[]) AS tk WHERE EXISTS ("
        " SELECT 1 FROM analysis_jobs oj"
        " JOIN fiscal_document_versions ov"
        f"   ON ov.id = {_structured_version_path('oj')}::bigint"
        f"  AND {_numeric_guard('oj')}"
        " WHERE oj.job_uuid = tk AND ov.slot_id = s.id))"
    )

    return (
        f"CASE WHEN {job_exact} THEN 0 ELSE 1 END, "
        f"CASE WHEN {filename_exact} THEN 0 ELSE 1 END, "
        f"CASE WHEN {unit_exact} THEN 0 ELSE 1 END, "
        f"CASE WHEN {department_exact} THEN 0 ELSE 1 END, "
        "s.updated_at DESC NULLS LAST, s.id"
    )


# ---- 结果归因（命中原因、命中版本、当前运行） --------------------------------


#: 行里的 JSONB 字段（``metadata``）读成 dict 的唯一入口：直接复用 WP2-B 的实现
#: （模块顶部已导入 ``_json_dict``）。两处各写一份，迟早出现"处理记录读得出
#: metadata、搜索读不出"。它带下划线表示"不属于 __all__"，不是"不许复用同一口径"。


def group_page_details(
    rows: Sequence[Dict[str, Any]],
) -> Dict[str, Dict[str, List[Dict[str, Any]]]]:
    """把"版本 × 运行"的 join 行按槽位归并（纯函数）。

    一条明细行 = 一个（文件版本 × 关联运行）；没有运行的版本只有一行、
    运行列为 NULL，因此归并时要按版本 id 去重，否则"跑过 3 次分析的版本"
    会被当成 3 个版本。
    """
    grouped: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
    seen_versions: Dict[str, set] = {}

    for row in rows:
        slot_id = str(row.get("slot_id") or "")
        if not slot_id:
            continue
        entry = grouped.setdefault(slot_id, {"versions": [], "runs": []})

        version_id = _to_int(row.get("document_version_id"))
        if version_id is not None:
            seen = seen_versions.setdefault(slot_id, set())
            if version_id not in seen:
                seen.add(version_id)
                entry["versions"].append(row)

        if _to_int(row.get("id")) is not None:
            entry["runs"].append(row)

    return grouped


def _to_int(value: Any) -> Optional[int]:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _is_current_version(row: Dict[str, Any], current_pointer: Optional[int]) -> bool:
    return current_pointer is not None and _to_int(row.get("document_version_id")) == current_pointer


def select_matched_version(
    versions: Sequence[Dict[str, Any]],
    *,
    tokens: Sequence[str],
    current_pointer: Optional[int],
) -> Optional[Dict[str, Any]]:
    """命中的文件版本（可以是历史版本，§二十一）。

    选择顺序：当前版本优先（用户搜的是现在这份材料时先给当前文件名）→
    创建时间新 → 版本 id 大。最后一档保证结果只由数据决定：同一天创建的两个
    同名版本，每次查询都返回同一个。
    """
    matched = [
        row for row in versions if any(ilike_literal(row.get("original_filename"), t) for t in tokens)
    ]
    if not matched:
        return None

    def key(row: Dict[str, Any]) -> Tuple[int, float, int]:
        created = row.get("version_created_at")
        created_rank = -(created.timestamp()) if isinstance(created, datetime) else float("inf")
        is_current_rank = 0 if _is_current_version(row, current_pointer) else 1
        return (is_current_rank, created_rank, -(_to_int(row.get("document_version_id")) or 0))

    return sorted(matched, key=key)[0]


def select_matched_job(
    runs: Sequence[Dict[str, Any]], *, tokens: Sequence[str]
) -> Optional[Dict[str, Any]]:
    """命中的处理任务：``job_uuid`` 与 token **精确相等**（§二十三）。

    不做前缀/子串匹配：job id 是机器标识，模糊匹配只会让"搜到了"变成
    "看起来搜到了"。查询大小写敏感（与 SQL 的等值比较一致，走 job_uuid 索引）。
    多条命中时取 ``analysis_jobs.id`` 最大的那条，结果因此是确定的。
    """
    wanted = set(tokens)
    matched = [row for row in runs if str(row.get("job_uuid") or "") in wanted]
    if not matched:
        return None
    return sorted(matched, key=lambda row: -(_to_int(row.get("id")) or 0))[0]


def _review_candidate(
    runs: Sequence[Dict[str, Any]],
    *,
    current_pointer: Optional[int],
    job_access: Optional[Callable[[Dict[str, Any]], bool]],
) -> Optional[MaterialSearchReviewCandidate]:
    """当前版本的"当前运行"能否成为复核入口（§二十六/§四十七）。

    两个条件缺一不可：

    1. 运行必须挂在 ``current_document_version_id`` 上（历史版本的运行永远
       不是复核候选，哪怕它更"新"）；
    2. 调用方给出的访问判定（路由层传入 ``user_can_access_job``）必须为真。
       **槽位可见不等于任务可见**，两者是不同权限主体。

    当前运行的选择复用 WP2-B 的唯一实现 ``select_current_run``：
    搜索页与处理记录页因此不会各说一个"最新运行"。
    """
    if current_pointer is None or job_access is None:
        return None

    linked = [
        row
        for row in runs
        if _to_int(row.get("document_version_id")) == current_pointer
        and _to_int(row.get("id")) is not None
    ]
    if not linked:
        return None

    current_run = select_current_run(linked)
    if current_run is None:  # pragma: no cover - linked 非空时必有值
        return None

    payload = job_access_payload(current_run)
    if not job_access(payload):
        return None

    return MaterialSearchReviewCandidate(
        job_uuid=str(current_run.get("job_uuid") or ""),
        status=str(current_run.get("status") or ""),
    )


def job_access_payload(row: Dict[str, Any]) -> Dict[str, Any]:
    """给 ``user_can_access_job`` 用的最小载荷，**只来自数据库**。

    不读 ``status.json``（§二十七）：搜索路径不允许为了判断权限去翻文件系统。
    ``organization_id`` / ``created_by`` 取不到时如实为 None —— 库里的
    ``analysis_jobs.organization_id`` 是另一套组织的数值外键，与组织目录的
    md5 id 不是一回事，因此只信 ``metadata`` 里的那个（那是入库时写进去的
    组织目录 id）。都判断不了时 ``user_can_access_job`` 会返回 False
    （非管理员看不到这个复核入口），这是 fail-closed 的正确方向。
    """
    metadata = _json_dict(row.get("metadata"))
    return {
        "job_id": str(row.get("job_uuid") or ""),
        "organization_id": metadata.get("organization_id"),
        "created_by": metadata.get("created_by"),
    }


#: 命中原因的输出顺序（稳定，前端按固定顺序渲染标签）。
_MATCHED_FIELD_ORDER: Tuple[str, ...] = (
    "unit",
    "department",
    "jurisdiction",
    "current_filename",
    "historical_filename",
    "job_id",
    "fiscal_year",
    "report_kind",
    "relationship",
)


def matched_field_codes(
    *,
    query: MaterialSearchQuery,
    row: Dict[str, Any],
    matched_version: Optional[Dict[str, Any]],
    matched_job: Optional[Dict[str, Any]],
    detail: Dict[str, List[Dict[str, Any]]],
) -> List[str]:
    """这条结果"为什么会出现"（§三十一）。

    文本命中按字段逐个判定（与 SQL 的 ILIKE 同一语义）；结构化条件
    （财政年度/文种/关系）只要出现在查询里就一定参与了过滤，因此直接计入。
    文件名命中区分当前/历史版本；job 命中只给 ``job_id`` 一个码 ——
    它是当前版本还是历史版本由 ``matched_job_version_is_current`` 表达，
    不塞进命中原因里重复一遍。
    """
    codes: List[str] = []

    def text_hit(field: Any) -> bool:
        return any(ilike_literal(field, token) for token in query.text_tokens)

    if text_hit(row.get("subject_org_name")):
        codes.append("unit")
    if text_hit(row.get("department_name")):
        codes.append("department")
    if text_hit(row.get("jurisdiction_name")):
        codes.append("jurisdiction")

    if matched_version is not None:
        current_pointer = _to_int(row.get("current_document_version_id"))
        codes.append(
            "current_filename"
            if _is_current_version(matched_version, current_pointer)
            else "historical_filename"
        )

    if matched_job is not None:
        codes.append("job_id")

    if query.fiscal_year is not None:
        codes.append("fiscal_year")
    if query.report_kind is not None:
        codes.append("report_kind")
    if query.relationship is not None:
        codes.append("relationship")

    # 去重 + 按固定顺序输出（同一字段多个 token 命中时只出一个标签）。
    order = {code: index for index, code in enumerate(_MATCHED_FIELD_ORDER)}
    return sorted(dict.fromkeys(codes), key=lambda code: order.get(code, 99))


def _current_filename(
    detail: Dict[str, List[Dict[str, Any]]], current_pointer: Optional[int]
) -> Optional[str]:
    """当前文件版本的文件名。指针为空时不从历史版本里挑一个顶上（§二十二）。"""
    if current_pointer is None:
        return None
    for row in detail.get("versions", []):
        if _to_int(row.get("document_version_id")) == current_pointer:
            name = str(row.get("original_filename") or "").strip()
            return name or None
    return None


def build_search_item(
    row: Dict[str, Any],
    *,
    query: MaterialSearchQuery,
    detail: Dict[str, List[Dict[str, Any]]],
    job_access: Optional[Callable[[Dict[str, Any]], bool]] = None,
) -> MaterialSearchItem:
    """一行主查询结果 + 明细 -> 响应 DTO（纯函数，可离线穷举）。"""
    current_pointer = _to_int(row.get("current_document_version_id"))
    versions = detail.get("versions", [])
    runs = detail.get("runs", [])

    matched_version = select_matched_version(
        versions, tokens=query.text_tokens, current_pointer=current_pointer
    )
    matched_job = select_matched_job(runs, tokens=query.text_tokens)

    return MaterialSearchItem(
        slot_id=str(row.get("slot_id") or ""),
        slot_key=str(row.get("slot_key") or ""),
        jurisdiction_id=_text_or_none(row.get("jurisdiction_org_id")),
        jurisdiction_name=_text_or_none(row.get("jurisdiction_name")),
        department_org_id=_text_or_none(row.get("department_org_id")),
        department_name=_text_or_none(row.get("department_name")),
        subject_org_id=str(row.get("subject_org_id") or ""),
        subject_org_name=str(row.get("subject_org_name") or ""),
        subject_kind=str(row.get("subject_kind") or "unknown"),
        material_scope=str(row.get("material_scope") or "unknown"),
        # 关系由现有字段推导（与部门矩阵同一函数），名字只用于展示归类。
        relationship=relationship_of(
            subject_org_id=row.get("subject_org_id"),
            department_id=row.get("department_org_id"),
            subject_kind=row.get("subject_kind"),
            material_scope=row.get("material_scope"),
            subject_org_name=row.get("subject_org_name"),
            department_name=row.get("department_name"),
        ),
        fiscal_year=_to_int(row.get("fiscal_year")),
        report_kind=str(row.get("report_kind") or "unknown"),
        caliber=str(row.get("caliber") or "unknown"),
        status=str(row.get("status") or ""),
        status_reason=_text_or_none(row.get("status_reason")),
        applicability_status=str(row.get("applicability_status") or "applicable"),
        current_document_version_id=current_pointer,
        current_filename=_current_filename(detail, current_pointer),
        matched_fields=matched_field_codes(
            query=query,
            row=row,
            matched_version=matched_version,
            matched_job=matched_job,
            detail=detail,
        ),
        matched_filename=(
            _text_or_none(matched_version.get("original_filename"))
            if matched_version is not None
            else None
        ),
        matched_document_version_id=(
            _to_int(matched_version.get("document_version_id"))
            if matched_version is not None
            else None
        ),
        matched_version_is_current=(
            _is_current_version(matched_version, current_pointer)
            if matched_version is not None
            else None
        ),
        matched_job_uuid=(
            _text_or_none(matched_job.get("job_uuid")) if matched_job is not None else None
        ),
        matched_job_version_is_current=(
            _is_current_version(matched_job, current_pointer)
            if matched_job is not None
            else None
        ),
        review_candidate=_review_candidate(
            runs, current_pointer=current_pointer, job_access=job_access
        ),
        updated_at=row.get("updated_at"),
    )


def _text_or_none(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    return text or None


# ---- 服务 ------------------------------------------------------------------


class MaterialSearchQueryService:
    """全局材料搜索只读查询。构造时传入一个 asyncpg 连接。

    ``self._conn`` 只需要实现 ``fetch(sql, *args)``（与 WP2-A/B 同一条约定）。
    """

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    async def search(
        self,
        *,
        query: MaterialSearchQuery,
        filters: MaterialSearchFilters,
        page: int = 1,
        page_size: int = 20,
        job_access: Optional[Callable[[Dict[str, Any]], bool]] = None,
    ) -> Tuple[List[MaterialSearchItem], int]:
        """返回 (本页结果, 命中总数)。

        ``job_access`` 是路由层注入的权限判定（通常是
        ``user_can_access_job`` 的偏函数）。缺省为 None 表示"不给出复核入口"，
        与"任务不可见"是同一种输出：搜索结果的第二个动作只能 fail-closed。
        """
        where, where_params = build_search_where(query, filters=filters)
        bag = _ParamBag()
        bag.params.extend(where_params)
        order = build_search_order(query, bag)
        limit_index = bag.add(max(1, int(page_size or 1)))
        offset_index = bag.add(max(0, (max(1, int(page or 1)) - 1) * max(1, int(page_size or 1))))

        rows = list(
            await self._conn.fetch(
                f"""
            /* material_search:main */
            SELECT s.id::text AS slot_id,
                   s.slot_key,
                   s.jurisdiction_org_id,
                   s.jurisdiction_name,
                   s.department_org_id,
                   s.department_name,
                   s.subject_org_id,
                   s.subject_org_name,
                   s.subject_kind,
                   s.material_scope,
                   s.fiscal_year,
                   s.report_kind,
                   s.caliber,
                   s.status,
                   s.status_reason,
                   s.applicability_status,
                   s.current_document_version_id,
                   s.updated_at,
                   COUNT(*) OVER ()::int AS total_count
            FROM material_slots s
            WHERE {where}
            ORDER BY {order}
            LIMIT {limit_index} OFFSET {offset_index}
            """,
                *bag.params,
            )
        )

        if rows:
            total = int(rows[0].get("total_count") or 0)
        elif page > 1:
            # 越界页：主查询没有行可以承载窗口函数算出的 total。
            # 不补这一条计数，"第 3 页没有内容"会被读成"一条都没搜到"。
            total = await self._count_matches(where, where_params)
        else:
            total = 0

        details = await self._load_page_details([str(row.get("slot_id") or "") for row in rows])
        items = [
            build_search_item(
                row,
                query=query,
                detail=details.get(str(row.get("slot_id") or ""), {}),
                job_access=job_access,
            )
            for row in rows
        ]
        return items, total

    async def _count_matches(self, where: str, params: Sequence[Any]) -> int:
        rows = list(
            await self._conn.fetch(
                f"""
            /* material_search:count */
            SELECT COUNT(*)::int AS total_count
            FROM material_slots s
            WHERE {where}
            """,
                *params,
            )
        )
        for row in rows:
            return int(row.get("total_count") or 0)
        return 0

    async def _load_page_details(self, slot_ids: Sequence[str]) -> Dict[str, Dict[str, List[Dict[str, Any]]]]:
        """本页槽位的文件版本与关联运行。一条 SQL，按页大小有界（§三十二）。

        投影刻意与 WP2-B 的 ``load_version_run_rows`` 保持一致（列名、
        ``LEFT JOIN`` 语义、关联谓词写在 ``ON`` 而不是 ``WHERE``）：
        搜索页与详情页对"哪次运行属于哪个版本"的理解必须逐字相同。
        """
        ids = [slot_id for slot_id in slot_ids if slot_id]
        if not ids:
            return {}

        rows = await self._conn.fetch(
            f"""
            /* material_search:page */
            SELECT s.id::text AS slot_id,
                   s.current_document_version_id,
                   v.id AS document_version_id,
                   v.original_filename,
                   v.created_at AS version_created_at,
                   j.id,
                   j.job_uuid,
                   j.status,
                   j.mode,
                   j.started_at,
                   j.completed_at,
                   j.created_at,
                   j.updated_at,
                   j.metadata
            FROM material_slots s
            LEFT JOIN fiscal_document_versions v ON v.slot_id = s.id
            LEFT JOIN analysis_jobs j
                   ON {_numeric_guard('j')}
                  AND {_structured_version_path('j')}::bigint = v.id
            WHERE s.id = ANY($1::uuid[])
            """,
            list(ids),
        )
        return group_page_details(rows)


__all__ = [
    "MIN_QUERY_LENGTH",
    "MAX_QUERY_LENGTH",
    "MAX_TEXT_TOKENS",
    "MAX_JOB_UUID_LENGTH",
    "REPORT_KIND_ALIASES",
    "RELATIONSHIP_ALIASES",
    "MaterialSearchQueryError",
    "MaterialSearchQuery",
    "MaterialSearchFilters",
    "MaterialSearchQueryService",
    "parse_search_query",
    "escape_like",
    "ilike_literal",
    "head_unit_subject_ids",
    "build_search_where",
    "build_search_order",
    "build_search_item",
    "group_page_details",
    "select_matched_version",
    "select_matched_job",
    "matched_field_codes",
    "job_access_payload",
]
