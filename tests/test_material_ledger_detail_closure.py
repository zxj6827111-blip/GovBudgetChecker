"""WP2-B 独立 Review 两个合并阻塞项的收口测试。

本文件只覆盖这两件事，不重复已有的时间轴/详情/RBAC 用例：

A. **正式 finding 计数必须等于仓库唯一权威口径**
   ``analysis_jobs`` 的正式 finding 定义在 ``src/services/evidence_guard.py``：
   ``count_formal_findings`` / ``is_formal_finding``。权威语义是
   "只要没有被 evidence guard 降级就属于正式 finding"，``error`` / ``warn`` /
   ``info`` 三种严重度**都算**。
   材料详情页把正式 finding 按严重度拆成「正式问题 / 信息提示」两个**展示分组**，
   但 ``formal_issue_count`` 必须直接取权威函数的结果 —— 用 ``len(正式问题那一栏)``
   对外报数，会让同一份分析在审核工作台/质量门禁与材料详情上给出两个问题数。

B. **处理记录不回显任何原始 error_message**
   ``_safe_error_summary`` 采用 fail-closed：只要数据库里存在非空 error_message，
   一律返回固定文案。不做"看起来安全就放行"的黑名单判断（盘符/路径前缀/连接串
   永远补不完，"安全"与"不安全"的错误文本也没有稳定分类标准）。
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone

import pytest

os.environ.setdefault("TESTING", "true")

from src.services.evidence_guard import count_formal_findings
from src.services.material_detail_query_service import (
    MaterialDetailQueryService,
    _safe_error_summary,
    count_canonical_formal_findings,
    partition_findings,
)
from support_material_detail_db import (
    FakeDetailConnection,
    make_job_row,
    make_result_row,
    make_slot_row,
    make_version_row,
)

NOW = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)
DEGRADED = "degraded_missing_evidence"

#: 统一的固定安全摘要（普通用户唯一会看到的那句话）。
SAFE_ERROR_SUMMARY = "处理失败（详情见任务日志）"


def _run(coro):
    return asyncio.run(coro)


def _finding(finding_id: str, severity: str, *, degraded: bool = False) -> dict:
    finding = {"id": finding_id, "severity": severity, "title": f"{finding_id} 标题"}
    if degraded:
        finding["evidence_status"] = DEGRADED
        finding["original_severity"] = severity
    return finding


def _analysis_conn(*, ai_findings, rule_findings):
    """构造"当前版本上有一条已落库结果"的假连接。"""
    conn = FakeDetailConnection(
        slots=[
            make_slot_row(
                id="slot-1",
                subject_org_id="org-A",
                current_document_version_id=22,
                updated_at=NOW,
            )
        ],
        versions=[
            make_version_row(document_version_id=22, slot_id="slot-1", version_created_at=NOW)
        ],
        jobs=[
            make_job_row(
                id=502,
                job_uuid="job-v2",
                metadata={"structured_ingest": {"document_version_id": 22}},
                completed_at=NOW,
            )
        ],
        results=[
            make_result_row(
                job_id=502, ai_findings=ai_findings, rule_findings=rule_findings
            )
        ],
    )
    return MaterialDetailQueryService(conn)


def _detail(service: MaterialDetailQueryService):
    slot = _run(service.load_slot_row("slot-1"))
    return _run(service.slot_detail(slot))


# ==== A：正式 finding 计数 ====================================================


def test_formal_issue_count_matches_canonical_count_formal_findings():
    """评审给的三条样本：high（formal）、info（formal）、降级（非 formal）。

    权威口径下期望是 **2**（high + info），而不是"error/warn 那一栏"的 1。
    """
    ai_findings = [
        _finding("A", "high"),
        _finding("B", "info"),
        _finding("C", "manual_review", degraded=True),
    ]
    rule_findings: list = []

    expected = count_formal_findings(
        {"ai_findings": ai_findings, "rule_findings": rule_findings}
    )
    assert expected == 2
    assert count_canonical_formal_findings(ai_findings, rule_findings) == expected


def test_formal_info_is_counted_but_still_presented_in_info_bucket():
    """info 计入计数，但在页面上仍单独成「信息提示」分组。"""
    ai_findings = [_finding("A", "high"), _finding("B", "info")]
    formal, manual_review, info_items = partition_findings(ai_findings, [])

    assert [item.finding_id for item in formal] == ["A"]
    assert [item.finding_id for item in info_items] == ["B"]
    assert manual_review == []

    canonical = count_canonical_formal_findings(ai_findings, [])
    assert canonical == 2
    assert len(formal) + len(info_items) == canonical
    # 关键反例：用"正式问题那一栏"报数会得到 1，与权威口径不一致。
    assert len(formal) != canonical


def test_degraded_finding_is_not_counted_as_formal():
    """被降级的条目既不计入计数，也不落在任何一个"正式"分组里。"""
    ai_findings = [_finding("C", "manual_review", degraded=True)]
    rule_findings = [_finding("D", "high")]

    formal, manual_review, info_items = partition_findings(ai_findings, rule_findings)
    assert [item.finding_id for item in manual_review] == ["C"]
    assert [item.finding_id for item in formal] == ["D"]
    assert info_items == []
    assert count_canonical_formal_findings(ai_findings, rule_findings) == 1


@pytest.mark.parametrize(
    "ai_findings,rule_findings",
    [
        ([], []),
        ([_finding("A", "high")], []),
        ([], [_finding("R", "warn")]),
        ([_finding("A", "high"), _finding("B", "info")], [_finding("R", "critical")]),
        ([_finding("A", "medium"), _finding("B", "low")], []),
        ([_finding("A", "error"), _finding("B", "fatal")], []),
        (
            [_finding("A", "high"), _finding("C", "manual_review", degraded=True)],
            [_finding("R", "info"), _finding("S", "warn")],
        ),
        ([_finding("X", "high", degraded=True), _finding("Y", "info", degraded=True)], []),
    ],
)
def test_canonical_count_agrees_with_evidence_guard_for_many_shapes(ai_findings, rule_findings):
    """多样本对照：任意严重度/降级组合下两个函数必须给出同一个数。

    意义在于：将来有人再动 severity bucket，只要权威函数没变，这条就会红，
    Material Detail 不可能悄悄漂出第二套计数。
    """
    assert count_canonical_formal_findings(
        ai_findings, rule_findings
    ) == count_formal_findings({"ai_findings": ai_findings, "rule_findings": rule_findings})


def test_slot_detail_formal_issue_count_equals_canonical_function():
    """端到端（走假连接）：详情给出的数字就是权威函数的结果。"""
    ai_findings = [
        _finding("A", "high"),
        _finding("B", "info"),
        _finding("C", "manual_review", degraded=True),
    ]
    service = _analysis_conn(ai_findings=ai_findings, rule_findings=[])
    analysis = _detail(service).current_analysis

    expected = count_formal_findings({"ai_findings": ai_findings, "rule_findings": []})
    assert expected == 2
    assert analysis.available is True
    assert analysis.formal_issue_count == expected

    # 三个展示分组仍然分得开
    assert [item.finding_id for item in analysis.formal_findings] == ["A"]
    assert [item.finding_id for item in analysis.info_findings] == ["B"]
    assert [item.finding_id for item in analysis.manual_review_items] == ["C"]

    # 恒等式：总计 = 正式问题 + 信息提示
    assert analysis.formal_issue_count == (
        len(analysis.formal_findings) + len(analysis.info_findings)
    )


def test_slot_detail_count_matches_canonical_across_severity_shapes():
    """换几组数据再对照一次，确认接口层没有自己的第二套算术。"""
    cases = [
        ([_finding("A", "high")], []),
        ([_finding("A", "high"), _finding("B", "info")], [_finding("R", "warn")]),
        ([_finding("A", "info")], []),
        ([], []),
        ([_finding("A", "high", degraded=True)], [_finding("R", "info")]),
    ]
    for ai_findings, rule_findings in cases:
        service = _analysis_conn(ai_findings=ai_findings, rule_findings=rule_findings)
        analysis = _detail(service).current_analysis
        assert analysis.formal_issue_count == count_formal_findings(
            {"ai_findings": ai_findings, "rule_findings": rule_findings}
        )


def test_slot_detail_zero_is_canonical_and_not_the_formal_bucket_length():
    """全是 info 时：权威计数是 N（不是 0），而"正式问题"那一栏是空的。

    这条专门堵住"用正式问题栏长度报数"的实现：那样会得到 0，
    页面就会在明明有正式 finding 的情况下显示"已确认没有正式 finding"。
    """
    ai_findings = [_finding("A", "info"), _finding("B", "info")]
    service = _analysis_conn(ai_findings=ai_findings, rule_findings=[])
    analysis = _detail(service).current_analysis

    assert analysis.formal_issue_count == 2
    assert analysis.formal_findings == []
    assert len(analysis.info_findings or []) == 2


# ==== B：error_summary 的安全反例 =============================================


@pytest.mark.parametrize(
    "raw",
    [
        r"C:\secret\a.pdf",
        r"D:\secret\a.pdf",
        r"E:\secret\a.pdf",
        r"Z:\secret\a.pdf",
        r"E:\Software Development\GovBudgetChecker\uploads\secret.pdf",
        "/tmp/secret/a.pdf",
        "/mnt/data/secret/a.pdf",
        "/home/user/a.pdf",
        "/srv/app/secret/a.pdf",
        r"\\server\share\a.pdf",
        "postgresql://user:password@host/db",
        "postgres://user:password@host/db",
        'Traceback (most recent call last):\n  File "x.py", line 1',
        "parser failed because invalid xref",
        "ValueError: organization not matched for unit-abc",
        "token=abcdef123456 rejected",
    ],
)
def test_safe_error_summary_never_exposes_raw_error_text(raw):
    """§十/§十二/§十三：只要 error_message 非空，一律固定文案。"""
    assert _safe_error_summary(raw) == SAFE_ERROR_SUMMARY


@pytest.mark.parametrize(
    "fragment",
    [
        "secret",
        "password",
        "E:",
        "Z:",
        "/tmp/",
        "/mnt/",
        "/home/",
        "/srv/",
        "server",
        "Traceback",
        "token",
        "invalid xref",
        "user:password",
        "GovBudgetChecker",
    ],
)
def test_safe_error_summary_contains_no_fragment_of_the_original(fragment):
    """反向断言：返回文案里不能出现原文的任何片段。"""
    raw = (
        r"E:\Software Development\GovBudgetChecker\secret.pdf "
        "/tmp/private/result.json "
        r"\\server\share\internal.pdf "
        "postgresql://user:password@host/db Traceback invalid xref token"
    )
    summary = _safe_error_summary(raw)
    assert summary == SAFE_ERROR_SUMMARY
    assert fragment not in summary


# 下面四条按"泄露类别"逐条命名。上面的参数化用例已经覆盖它们，
# 这里再显式列出来的目的只有一个：让 Review 能按类别逐项核对覆盖面，
# 而不是去数参数表里有没有自己关心的那一种形态。

WINDOWS_DRIVE_SAMPLES = [
    r"C:\secret\a.pdf",
    r"D:\secret\a.pdf",
    r"E:\secret\a.pdf",
    r"F:\secret\a.pdf",
    r"Z:\secret\a.pdf",
    r"E:\Software Development\GovBudgetChecker\uploads\secret.pdf",
]
UNIX_PATH_SAMPLES = [
    "/tmp/secret/a.pdf",
    "/mnt/data/secret/a.pdf",
    "/home/user/a.pdf",
    "/root/secret/a.pdf",
    "/srv/app/secret/a.pdf",
    "/usr/local/secret/a.pdf",
]
UNC_SAMPLES = [
    r"\\server\share\a.pdf",
    r"\\fileserver\budget\2024\secret.pdf",
]
CONNECTION_STRING_SAMPLES = [
    "postgresql://user:password@host/db",
    "postgres://user:password@host/db",
    "amqp://user:password@host:5672/vhost",
    "host=db.internal user=admin password=secret dbname=fiscal",
]


@pytest.mark.parametrize("raw", WINDOWS_DRIVE_SAMPLES)
def test_safe_error_summary_never_exposes_windows_drive_path(raw):
    """Windows 盘符路径（含本项目开发环境实际使用的 E:\\）。"""
    assert _safe_error_summary(raw) == SAFE_ERROR_SUMMARY


@pytest.mark.parametrize("raw", UNIX_PATH_SAMPLES)
def test_safe_error_summary_never_exposes_unix_path(raw):
    """Unix 绝对路径（含 /tmp、/mnt、/root、/srv、/usr）。"""
    assert _safe_error_summary(raw) == SAFE_ERROR_SUMMARY


@pytest.mark.parametrize("raw", UNC_SAMPLES)
def test_safe_error_summary_never_exposes_unc_path(raw):
    """UNC 网络路径（\\\\server\\share 形态）。"""
    assert _safe_error_summary(raw) == SAFE_ERROR_SUMMARY


@pytest.mark.parametrize("raw", CONNECTION_STRING_SAMPLES)
def test_safe_error_summary_never_exposes_connection_string(raw):
    """连接串（URL 形态与 key=value 形态）。"""
    assert _safe_error_summary(raw) == SAFE_ERROR_SUMMARY


def test_safe_error_summary_is_none_for_empty_values():
    """没有错误就没有摘要：空值返回 None，而不是"处理失败"。

    把"没失败"渲染成"处理失败"会让运行状态与错误摘要互相矛盾。
    """
    for value in (None, "", "   ", "\n\n", "\t"):
        assert _safe_error_summary(value) is None


def test_safe_error_summary_does_not_sanitize_partially():
    """§十一：不做部分脱敏 —— 非空返回值只可能是那一条固定文案。"""
    raw = r"E:\a\b\secret.pdf"
    summary = _safe_error_summary(raw)
    assert summary == SAFE_ERROR_SUMMARY
    # 去掉固定文案之后不应残留任何原文片段
    assert summary is not None
    assert summary.replace(SAFE_ERROR_SUMMARY, "") == ""


def test_run_item_uses_the_fail_closed_summary():
    """走一遍 _run_item：运行 DTO 上的 error_summary 也必须是固定文案。"""
    service = _analysis_conn(ai_findings=[], rule_findings=[])
    slot = _run(service.load_slot_row("slot-1"))
    runs = _run(service.run_history(slot))
    # 默认样本没有错误信息 → None（不是"处理失败"）
    assert runs[0].error_summary is None

    failing = _analysis_conn(ai_findings=[], rule_findings=[])
    for job in failing._conn.jobs:  # noqa: SLF001 - 测试里刻意改样本
        job["status"] = "error"
        job["error_message"] = r"E:\Software Development\GovBudgetChecker\secret.pdf"
    slot = _run(failing.load_slot_row("slot-1"))
    failed_run = _run(failing.run_history(slot))[0]
    assert failed_run.status == "error"
    assert failed_run.error_summary == SAFE_ERROR_SUMMARY
    assert "secret" not in str(failed_run.model_dump())
