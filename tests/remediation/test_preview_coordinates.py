"""证据框按解析坐标（pdfplumber）存储，预览图按可见页渲染；MediaBox 原点非零时画框前必须换算。"""
import pdfplumber
import pymupdf as fitz
import pytest

from api.routes.files import _page_geometry, _render_preview_png


@pytest.fixture
def shifted_pdf(tmp_path):
    path = tmp_path / "shifted.pdf"
    doc = fitz.open()
    for _ in range(2):
        doc.new_page(width=842, height=595).insert_text((400, 130), "45441298", fontsize=12)
    # 与上海 2022 文旅局原件一致：第 1 页 MediaBox 原点 (-12, -12)，第 2 页原点为 0。
    doc.xref_set_key(doc.page_xref(0), "MediaBox", "[-12 -12 830 583]")
    doc.save(path)
    doc.close()
    return path


def test_native_boxes_are_moved_onto_the_visible_page(shifted_pdf):
    with pdfplumber.open(shifted_pdf) as pdf:
        native = next(w for w in pdf.pages[0].extract_words() if w["text"] == "45441298")
    with fitz.open(shifted_pdf) as doc:
        visible = next(w for w in doc[0].get_text("words") if w[4] == "45441298")
    geometry = _page_geometry(shifted_pdf)["native_to_preview"]
    assert set(geometry) == {"1"}  # 原点为 0 的页不下发换算
    a, b, c, d, e, f = geometry["1"]
    moved = [a * native["x0"] + e, d * native["top"] + f, a * native["x1"] + e, d * native["bottom"] + f]
    assert (a, b, c, d) == pytest.approx((1, 0, 0, 1)) and abs(e) == pytest.approx(12) and abs(f) == pytest.approx(12)
    # 两个库的字形竖向范围算法不同，横向比边、竖向比中线。
    assert moved[0] == pytest.approx(visible[0], abs=.5) and moved[2] == pytest.approx(visible[2], abs=.5)
    assert (moved[1] + moved[3]) / 2 == pytest.approx((visible[1] + visible[3]) / 2, abs=2)
    # 后端画框同样换算：渲染时不会把框裁成空。
    assert _render_preview_png(shifted_pdf, page_number=1, padding=0, scale=1,
                               bbox=[native["x0"], native["top"], native["x1"], native["bottom"]])
