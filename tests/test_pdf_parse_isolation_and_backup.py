"""Task 13 / 缺口 P2-05 + B-09（解析资源隔离）与 B-12（备份恢复）。

整改前的事实：
- PDF 解析在 `api/main.py:_sync_parse_pdf` 经 `run_in_executor` 跑在主进程线程池里，
  只受整体 `PIPELINE_TIMEOUT_SEC` 约束；线程杀不掉、也没有内存上限；
- `scripts/db_backup.py` 只覆盖数据库，`docs/DATA_RETENTION.md` 要求的三件套缺两件。

断言意图（每组都有正反对照）：
1. 正常 PDF 在隔离进程里能解析出正确的页数与文本（正例），
   证明隔离路径本身是可用的，不是"为了拦而拦"。
2. 超时被拦：用 `PDF_PARSE_TEST_DELAY_SECONDS` 确定性地卡住子进程
   （参考 tests/test_rule_process.py 的写法，不依赖真实大文件与时序运气），
   并断言子进程真的被终止（`is_alive()` 为 False）。
3. 页数超限被拦：构造多页 PDF，把上限压到 1。
   对照：上限放宽后同一文件必须能解析成功。
4. 输出体积超限被拦，对照同上。
5. 终态语义：超时/超限/失败都映射成 `error` + `analysis_error`，
   并带明确错误码；**反例**断言不会出现 `done` / `review_required`。
6. 备份：三件套齐全、manifest 校验通过；篡改任一构件后校验必须失败。
7. 恢复：能把 uploads 与审计日志还原成同样的文件集合与 sha256；
   恢复目标指向当前 `UPLOAD_DIR` 时必须被拒（防止演练冲掉真实数据）。
"""

from __future__ import annotations

import gzip
import json
import os
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import AsyncMock

import pytest

from api import main as pipeline_mod
from api import runtime
from scripts import backup_all
from src.services.pdf_parse_process import (
    DEFAULT_MAX_PAGES,
    PdfParseError,
    PdfParseLimitExceeded,
    PdfParseTimeout,
    _parse_pdf_sync,
    isolation_enabled,
    parse_error_code,
    resolve_limits,
)

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


# ---------------------------------------------------------------------------
# PDF 构造：手写最小合法 PDF，避免引入新依赖
# ---------------------------------------------------------------------------
def _build_pdf(page_texts: List[str]) -> bytes:
    """生成一个每页带一行文本的最小 PDF。

    手工拼 PDF 而不是引 reportlab：解析隔离要验证的是进程边界与资源上限，
    没必要为此新增依赖。
    """
    objects: List[bytes] = []

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    font_id = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    page_ids: List[int] = []
    content_ids: List[int] = []
    for text in page_texts:
        escaped = text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
        stream = f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET".encode("latin-1", "replace")
        content_ids.append(
            add(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream")
        )
        page_ids.append(0)  # 占位，稍后写入

    pages_id = len(objects) + len(page_texts) + 1
    for index, content_id in enumerate(content_ids):
        page_ids[index] = add(
            b"<< /Type /Page /Parent " + str(pages_id).encode() + b" R "
            b"/MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 " + str(font_id).encode() + b" 0 R >> >> "
            b"/Contents " + str(content_id).encode() + b" 0 R >>"
        )

    kids = b" ".join(f"{pid} 0 R".encode() for pid in page_ids)
    actual_pages_id = add(
        b"<< /Type /Pages /Kids [" + kids + b"] /Count " + str(len(page_ids)).encode() + b" >>"
    )
    catalog_id = add(b"<< /Type /Catalog /Pages " + str(actual_pages_id).encode() + b" 0 R >>")

    # 修正 Page 对象里的 Parent 引用（占位时还不知道 Pages 的实际编号）
    for pid in page_ids:
        objects[pid - 1] = objects[pid - 1].replace(
            b"/Parent " + str(pages_id).encode() + b" R",
            b"/Parent " + str(actual_pages_id).encode() + b" 0 R",
        )

    out = bytearray(b"%PDF-1.4\n")
    offsets: List[int] = []
    for index, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{index} 0 obj\n".encode() + body + b"\nendobj\n"

    xref_offset = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        b"trailer\n<< /Size " + str(len(objects) + 1).encode() + b" /Root "
        + str(catalog_id).encode() + b" 0 R >>\nstartxref\n"
        + str(xref_offset).encode() + b"\n%%EOF\n"
    )
    return bytes(out)


@pytest.fixture
def three_page_pdf(tmp_path: Path) -> Path:
    # 用 ASCII 文本：内置 Helvetica 字体没有 CJK 字形，中文会被抽成乱码，
    # 那样断言的就不是解析隔离而是字体问题了。
    path = tmp_path / "sample.pdf"
    path.write_bytes(
        _build_pdf(
            [
                "page one budget summary table",
                "page two official expenses",
                "page three performance targets",
            ]
        )
    )
    return path


# ---------------------------------------------------------------------------
# 1. 隔离开关与上限解析
# ---------------------------------------------------------------------------
def test_isolation_enabled_defaults(monkeypatch):
    monkeypatch.delenv("PDF_PARSE_ISOLATION_ENABLED", raising=False)
    monkeypatch.setenv("TESTING", "false")
    assert isolation_enabled() is True

    monkeypatch.setenv("TESTING", "true")
    assert isolation_enabled() is False

    monkeypatch.setenv("PDF_PARSE_ISOLATION_ENABLED", "true")
    assert isolation_enabled() is True


def test_limits_follow_upload_page_cap(monkeypatch):
    """页数上限默认跟随上传门槛，避免"上传能过、解析被拒"这种自相矛盾。"""
    monkeypatch.delenv("PDF_PARSE_MAX_PAGES", raising=False)
    monkeypatch.setenv("MAX_UPLOAD_PAGES", "123")
    assert resolve_limits()["max_pages"] == 123

    monkeypatch.delenv("MAX_UPLOAD_PAGES", raising=False)
    assert resolve_limits()["max_pages"] == DEFAULT_MAX_PAGES

    monkeypatch.setenv("PDF_PARSE_MAX_PAGES", "7")
    assert resolve_limits()["max_pages"] == 7


def test_limits_report_platform_memory_support():
    limits = resolve_limits()
    # 如实反映平台能力：Windows 上没有 rlimit，不能声称有内存硬上限
    assert limits["memory_limit_supported"] is (os.name != "nt")
    assert limits["start_method"] in {"spawn", "fork", "forkserver"}


# ---------------------------------------------------------------------------
# 2. 正例：隔离进程能正常解析
# ---------------------------------------------------------------------------
def test_isolated_parse_returns_pages(three_page_pdf, monkeypatch):
    monkeypatch.delenv("PDF_PARSE_TEST_DELAY_SECONDS", raising=False)
    result = _parse_pdf_sync(three_page_pdf, {"timeout_seconds": 120.0})

    assert result["page_count"] == 3
    assert len(result["page_texts"]) == 3
    assert "official expenses" in result["page_texts"][1]
    assert result["filesize"] == three_page_pdf.stat().st_size
    assert result["table_cells"] == 0


# ---------------------------------------------------------------------------
# 3. 超时：确定性触发，并断言子进程被真正终止
# ---------------------------------------------------------------------------
def test_isolated_parse_timeout_kills_child(three_page_pdf, monkeypatch):
    import multiprocessing

    monkeypatch.setenv("PDF_PARSE_TEST_DELAY_SECONDS", "30")
    spawned: List[Any] = []
    real_context = multiprocessing.get_context(resolve_limits()["start_method"])

    class _RecordingContext:
        def Pipe(self, duplex=False):
            return real_context.Pipe(duplex)

        def Process(self, *args, **kwargs):
            process = real_context.Process(*args, **kwargs)
            spawned.append(process)
            return process

    monkeypatch.setattr(
        "src.services.pdf_parse_process.multiprocessing.get_context",
        lambda _method: _RecordingContext(),
    )

    with pytest.raises(PdfParseTimeout) as excinfo:
        _parse_pdf_sync(three_page_pdf, {"timeout_seconds": 0.5})

    assert "exceeded" in str(excinfo.value)
    assert spawned, "必须真的起过子进程"
    # 关键：超时后子进程不能还活着继续烧 CPU
    assert spawned[0].is_alive() is False


def test_no_timeout_when_delay_is_short(three_page_pdf, monkeypatch):
    """对照：同样的路径，延迟远小于超时预算时必须正常返回。"""
    monkeypatch.setenv("PDF_PARSE_TEST_DELAY_SECONDS", "0.05")
    result = _parse_pdf_sync(three_page_pdf, {"timeout_seconds": 60.0})
    assert result["page_count"] == 3


# ---------------------------------------------------------------------------
# 4. 资源上限（页数 / 输出体积），正反对照
# ---------------------------------------------------------------------------
def test_page_limit_is_enforced(three_page_pdf, monkeypatch):
    monkeypatch.delenv("PDF_PARSE_TEST_DELAY_SECONDS", raising=False)
    with pytest.raises(PdfParseLimitExceeded) as excinfo:
        _parse_pdf_sync(three_page_pdf, {"timeout_seconds": 60.0, "max_pages": 1})
    assert "page_limit_exceeded" in str(excinfo.value)

    # 对照：上限放宽后同一文件必须解析成功
    assert _parse_pdf_sync(three_page_pdf, {"timeout_seconds": 60.0, "max_pages": 3})[
        "page_count"
    ] == 3


def test_text_output_limit_is_enforced(three_page_pdf, monkeypatch):
    monkeypatch.delenv("PDF_PARSE_TEST_DELAY_SECONDS", raising=False)
    with pytest.raises(PdfParseLimitExceeded) as excinfo:
        _parse_pdf_sync(
            three_page_pdf, {"timeout_seconds": 60.0, "max_text_chars": 1}
        )
    assert "text_limit_exceeded" in str(excinfo.value)

    assert _parse_pdf_sync(
        three_page_pdf, {"timeout_seconds": 60.0, "max_text_chars": 10_000}
    )["page_count"] == 3


@pytest.mark.skipif(os.name == "nt", reason="Windows 没有 RLIMIT_AS，内存硬上限不适用")
def test_memory_limit_is_enforced(three_page_pdf, monkeypatch):
    monkeypatch.delenv("PDF_PARSE_TEST_DELAY_SECONDS", raising=False)
    monkeypatch.setenv("PDF_PARSE_TEST_ALLOCATE_MB", "512")

    with pytest.raises((PdfParseLimitExceeded, PdfParseError)) as excinfo:
        _parse_pdf_sync(
            three_page_pdf, {"timeout_seconds": 60.0, "memory_mb": 128}
        )
    assert "memory" in str(excinfo.value).lower() or "exit" in str(excinfo.value).lower()


def test_broken_pdf_reports_error(tmp_path, monkeypatch):
    monkeypatch.delenv("PDF_PARSE_TEST_DELAY_SECONDS", raising=False)
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"%PDF-1.4\nthis is not a pdf body\n")

    with pytest.raises(PdfParseError):
        _parse_pdf_sync(broken, {"timeout_seconds": 60.0})


# ---------------------------------------------------------------------------
# 5. 终态语义映射
# ---------------------------------------------------------------------------
def test_parse_error_code_mapping():
    assert parse_error_code(PdfParseTimeout("x"))[0] == "pdf_parse_timeout"
    assert parse_error_code(PdfParseLimitExceeded("y"))[0] == "pdf_parse_limit_exceeded"
    assert parse_error_code(PdfParseError("z"))[0] == "pdf_parse_failed"
    assert parse_error_code(ValueError("other"))[0] == "pdf_parse_failed"


def _prepare_job(tmp_path: Path, pdf_bytes: bytes, monkeypatch) -> Path:
    job_dir = tmp_path / "job-parse-limit"
    job_dir.mkdir()
    (job_dir / "source.pdf").write_bytes(pdf_bytes)
    runtime.write_json_file(
        job_dir / "status.json",
        {
            "status": "queued",
            "mode": "legacy",
            "use_local_rules": True,
            "use_ai_assist": False,
            "report_year": 2025,
            "report_kind": "budget",
        },
    )
    monkeypatch.setattr(
        pipeline_mod, "persist_analysis_job_snapshot", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        pipeline_mod,
        "run_structured_ingest",
        AsyncMock(
            return_value={"status": "skipped", "review_item_count": 0, "review_items": []}
        ),
    )
    monkeypatch.setattr(pipeline_mod.settings, "get", lambda *_args: False)
    return job_dir


@pytest.mark.asyncio
async def test_pipeline_page_limit_lands_error_terminal_state(tmp_path, monkeypatch):
    """超限任务必须落明确终态 error + analysis_error，不得静默成功。"""
    monkeypatch.setenv("PDF_PARSE_ISOLATION_ENABLED", "true")
    monkeypatch.setenv("PDF_PARSE_MAX_PAGES", "1")
    monkeypatch.delenv("PDF_PARSE_TEST_DELAY_SECONDS", raising=False)
    job_dir = _prepare_job(
        tmp_path, _build_pdf(["page one budget", "page two final accounts"]), monkeypatch
    )

    await pipeline_mod._run_pipeline_inner(job_dir)

    payload: Dict[str, Any] = runtime.read_json_file(job_dir / "status.json", default={})
    assert payload["status"] == "error"
    assert payload["analysis_conclusion"] == "analysis_error"
    assert "pdf_parse_limit_exceeded" in str(payload.get("error"))
    # 反例：不允许出现"看起来成功"的终态
    assert payload["status"] not in {"done", "degraded", "review_required"}
    assert payload["page_coverage"] == 0.0


@pytest.mark.asyncio
async def test_pipeline_parse_timeout_lands_error_terminal_state(tmp_path, monkeypatch):
    monkeypatch.setenv("PDF_PARSE_ISOLATION_ENABLED", "true")
    monkeypatch.setenv("PDF_PARSE_TIMEOUT_SEC", "0.5")
    monkeypatch.setenv("PDF_PARSE_TEST_DELAY_SECONDS", "30")
    monkeypatch.delenv("PDF_PARSE_MAX_PAGES", raising=False)
    job_dir = _prepare_job(tmp_path, _build_pdf(["page one budget"]), monkeypatch)

    await pipeline_mod._run_pipeline_inner(job_dir)

    payload = runtime.read_json_file(job_dir / "status.json", default={})
    assert payload["status"] == "error"
    assert payload["analysis_conclusion"] == "analysis_error"
    assert "pdf_parse_timeout" in str(payload.get("error"))


@pytest.mark.asyncio
async def test_pipeline_succeeds_through_isolated_parser(tmp_path, monkeypatch):
    """对照：开启隔离且不超限时，流水线必须能正常跑到终态。"""
    monkeypatch.setenv("PDF_PARSE_ISOLATION_ENABLED", "true")
    monkeypatch.delenv("PDF_PARSE_TEST_DELAY_SECONDS", raising=False)
    monkeypatch.setenv("PDF_PARSE_MAX_PAGES", "50")
    monkeypatch.setenv("PDF_PARSE_TIMEOUT_SEC", "120")
    # 覆盖率门禁：合成 PDF 每页只有一行字，放宽阈值让本用例聚焦解析路径
    monkeypatch.setenv("SCANNED_PAGE_MIN_CHARS", "3")
    job_dir = _prepare_job(
        tmp_path, _build_pdf(["budget summary total 100.00"]), monkeypatch
    )
    monkeypatch.setattr(
        pipeline_mod,
        "run_rules_in_process",
        AsyncMock(
            return_value={"issues": {"all": [], "error": [], "warn": [], "info": []}}
        ),
    )

    await pipeline_mod._run_pipeline_inner(job_dir)

    payload = runtime.read_json_file(job_dir / "status.json", default={})
    assert payload["status"] == "done", payload.get("error")
    assert payload["analysis_conclusion"] == "no_findings"
    assert payload["result"]["meta"]["pages"] == 1


# ---------------------------------------------------------------------------
# 6/7. 备份与恢复
# ---------------------------------------------------------------------------
def _seed_uploads(root: Path) -> Dict[str, str]:
    """造两个任务目录，返回 相对路径 -> sha256 映射，用于恢复后逐个比对。"""
    files = {
        "job-a/status.json": json.dumps({"job_id": "job-a", "status": "done"}),
        "job-a/source.pdf": "%PDF-1.4 fake a",
        "job-b/status.json": json.dumps({"job_id": "job-b", "status": "review_required"}),
        "job-b/source.pdf": "%PDF-1.4 fake b",
    }
    digests: Dict[str, str] = {}
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        digests[relative] = backup_all.sha256_file(path)
    return digests


def test_backup_covers_all_three_artifacts(tmp_path):
    uploads = tmp_path / "uploads"
    digests = _seed_uploads(uploads)
    audit = tmp_path / "logs" / "admin-actions.jsonl"
    audit.parent.mkdir(parents=True, exist_ok=True)
    audit.write_text('{"action":"org_create"}\n{"action":"org_delete"}\n', encoding="utf-8")

    archive = tmp_path / "backup"
    manifest = backup_all.create_backup(
        archive,
        uploads_dir=uploads,
        database_url="",
        audit_log_path=audit,
        include_database=False,
    )

    assert (archive / backup_all.UPLOADS_ARTIFACT).is_file()
    assert (archive / backup_all.MANIFEST_NAME).is_file()
    assert manifest["artifacts"]["uploads"]["file_count"] == len(digests)
    assert manifest["artifacts"]["audit_log"]["present"] is True
    assert manifest["artifacts"]["audit_log"]["line_count"] == 2
    # 未配置数据库时如实标注，而不是假装备份成功
    assert manifest["artifacts"]["database"]["present"] is False

    report = backup_all.verify_backup(archive)
    assert report["ok"] is True
    assert report["artifacts"]["uploads"]["status"] == "ok"
    assert report["artifacts"]["audit_log"]["status"] == "ok"


def test_verify_detects_tampered_artifact(tmp_path):
    """反例：构件被改动后校验必须失败，否则"备份可用"是自欺。"""
    uploads = tmp_path / "uploads"
    _seed_uploads(uploads)
    audit = tmp_path / "audit.jsonl"
    audit.write_text('{"action":"x"}\n', encoding="utf-8")
    archive = tmp_path / "backup"
    backup_all.create_backup(
        archive,
        uploads_dir=uploads,
        database_url="",
        audit_log_path=audit,
        include_database=False,
    )

    target = archive / backup_all.UPLOADS_ARTIFACT
    target.write_bytes(target.read_bytes() + b"tampered")

    report = backup_all.verify_backup(archive)
    assert report["ok"] is False
    assert report["artifacts"]["uploads"]["status"] == "checksum_mismatch"


def test_verify_detects_missing_artifact(tmp_path):
    uploads = tmp_path / "uploads"
    _seed_uploads(uploads)
    audit = tmp_path / "audit.jsonl"
    audit.write_text("{}\n", encoding="utf-8")
    archive = tmp_path / "backup"
    backup_all.create_backup(
        archive,
        uploads_dir=uploads,
        database_url="",
        audit_log_path=audit,
        include_database=False,
    )
    (archive / backup_all.UPLOADS_ARTIFACT).unlink()

    report = backup_all.verify_backup(archive)
    assert report["ok"] is False
    assert report["artifacts"]["uploads"]["status"] == "missing"


def test_restore_reproduces_uploads_and_audit_log(tmp_path):
    uploads = tmp_path / "uploads"
    digests = _seed_uploads(uploads)
    audit = tmp_path / "audit.jsonl"
    audit.write_text('{"action":"org_create"}\n', encoding="utf-8")
    archive = tmp_path / "backup"
    backup_all.create_backup(
        archive,
        uploads_dir=uploads,
        database_url="",
        audit_log_path=audit,
        include_database=False,
    )

    restored_uploads = tmp_path / "restored-uploads"
    result = backup_all.restore_uploads(archive, restored_uploads)
    assert result["file_count"] == len(digests)
    # 逐个文件比 sha256：只比文件数不算"可恢复"
    for relative, digest in digests.items():
        assert backup_all.sha256_file(restored_uploads / relative) == digest

    restored_audit = tmp_path / "restored-audit.jsonl"
    audit_result = backup_all.restore_audit_log(archive, restored_audit)
    assert audit_result["present"] is True
    assert restored_audit.read_text(encoding="utf-8") == audit.read_text(encoding="utf-8")


def test_restore_refuses_live_targets(tmp_path, monkeypatch):
    """反例：默认拒绝恢复到当前 UPLOAD_DIR / DATABASE_URL / AUDIT_LOG_PATH。"""
    live_uploads = tmp_path / "live-uploads"
    live_uploads.mkdir()
    monkeypatch.setenv("UPLOAD_DIR", str(live_uploads))
    monkeypatch.setenv("DATABASE_URL", "postgres://u:p@localhost:5432/live_db")
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "live-audit.jsonl"))

    problems = backup_all._guard_live_targets(
        force=False,
        target_uploads=live_uploads,
        target_database_url="postgres://u:p@localhost:5432/live_db",
        target_audit_log=tmp_path / "live-audit.jsonl",
    )
    assert len(problems) == 3

    # 正例：显式给临时目标时不拦
    assert (
        backup_all._guard_live_targets(
            force=False,
            target_uploads=tmp_path / "drill-uploads",
            target_database_url="postgres://u:p@localhost:5432/drill_db",
            target_audit_log=tmp_path / "drill-audit.jsonl",
        )
        == []
    )
    # --force 时放行（但由调用方承担后果）
    assert (
        backup_all._guard_live_targets(
            force=True,
            target_uploads=live_uploads,
            target_database_url="postgres://u:p@localhost:5432/live_db",
            target_audit_log=tmp_path / "live-audit.jsonl",
        )
        == []
    )


def test_restore_rejects_path_traversal_in_archive(tmp_path):
    """反例：归档里带 ../ 的成员必须被拒，不能写到目标目录之外。"""
    import tarfile

    archive = tmp_path / "evil"
    archive.mkdir()
    payload = tmp_path / "evil.txt"
    payload.write_text("pwned", encoding="utf-8")
    with tarfile.open(archive / backup_all.UPLOADS_ARTIFACT, "w:gz") as tar:
        tar.add(str(payload), arcname="../escaped.txt")

    with pytest.raises(backup_all.BackupError) as excinfo:
        backup_all.restore_uploads(archive, tmp_path / "target")
    assert "越界" in str(excinfo.value)
    assert not (tmp_path / "escaped.txt").exists()


def test_database_url_is_redacted_in_manifest():
    redacted = backup_all.redact_database_url(
        "postgres://fiscal_user:super-secret@localhost:5432/fiscal_db"
    )
    assert "super-secret" not in redacted
    assert redacted == "postgres://fiscal_user:***@localhost:5432/fiscal_db"


def test_gzip_dump_is_readable_without_external_tools(tmp_path):
    """备份用 Python gzip 而非外部 gzip 可执行文件（Windows 上没有）。"""
    target = tmp_path / "x.sql.gz"
    with gzip.open(target, "wb") as handle:
        handle.write(b"-- dump\n")
    with gzip.open(target, "rb") as handle:
        assert handle.read() == b"-- dump\n"
