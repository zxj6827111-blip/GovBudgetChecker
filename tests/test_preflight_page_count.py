"""UI 重建第二批 Task 5：preflight 响应新增 page_count 字段。

上传中心待上传文件列表需要在上传前显示真实页数（原型图"18.4 MB · 48 页"），
这里补测新增的 `get_pdf_page_count_from_bytes()` 与 `/api/documents/preflight`
响应体里的 `page_count` 字段：正常 PDF 返回真实页数，损坏/非 PDF 内容返回
None（不得返回 0——0 意味着"已确认这份文件有 0 页"）。
"""

from __future__ import annotations

import io
import os

os.environ.setdefault("TESTING", "true")

from fastapi.testclient import TestClient

from api import runtime
from api.main import app

API_KEY = os.getenv("GOVBUDGET_API_KEY", "change_me_to_a_strong_secret")


def _headers() -> dict:
    return {"X-API-Key": API_KEY}


def _single_page_pdf_bytes() -> bytes:
    return (
        b"%PDF-1.4\n"
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n"
        b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] >> endobj\n"
        b"xref\n0 4\n0000000000 65535 f \n"
        b"trailer << /Size 4 /Root 1 0 R >>\nstartxref\n0\n%%EOF\n"
    )


def test_get_pdf_page_count_from_bytes_returns_real_count():
    assert runtime.get_pdf_page_count_from_bytes(_single_page_pdf_bytes()) == 1


def test_get_pdf_page_count_from_bytes_returns_none_for_garbage():
    """反例：损坏/非 PDF 内容必须返回 None，不得返回 0（0 是"已确认零页"的真实数据）。"""
    assert runtime.get_pdf_page_count_from_bytes(b"not a pdf at all") is None


def test_get_pdf_page_count_from_bytes_returns_none_for_empty():
    assert runtime.get_pdf_page_count_from_bytes(b"") is None


def test_preflight_response_includes_real_page_count(tmp_path, monkeypatch):
    upload_root = tmp_path / "uploads"
    upload_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(runtime, "UPLOAD_ROOT", upload_root)
    runtime._JOB_SUMMARY_CACHE.clear()

    client = TestClient(app)
    response = client.post(
        "/api/documents/preflight",
        headers=_headers(),
        files={"file": ("sample.pdf", io.BytesIO(_single_page_pdf_bytes()), "application/pdf")},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["page_count"] == 1


def test_preflight_response_page_count_is_none_for_corrupted_upload(tmp_path, monkeypatch):
    """反例：preflight 收到无法解析的内容时 page_count 必须是 None，不是 0。"""
    upload_root = tmp_path / "uploads"
    upload_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(runtime, "UPLOAD_ROOT", upload_root)
    runtime._JOB_SUMMARY_CACHE.clear()

    client = TestClient(app)
    response = client.post(
        "/api/documents/preflight",
        headers=_headers(),
        files={"file": ("broken.pdf", io.BytesIO(b"this is not a real pdf"), "application/pdf")},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["page_count"] is None
