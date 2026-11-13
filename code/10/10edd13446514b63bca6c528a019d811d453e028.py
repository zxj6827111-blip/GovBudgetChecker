"""宽标题保留原始文字尺寸，不把合成长条算成真实材料验收。"""

from PIL import Image, ImageDraw

from src.services.ocr_tiles import tile_boxes, sparse_wide_strips, MAX_WIDTH, MAX_HEIGHT


def test_wide_sparse_title_gets_whole_original_strip_and_regular_page_coverage(monkeypatch):
    monkeypatch.delenv("OCR_TILE_WIDTH", raising=False)
    image = Image.new("RGB", (2400, 3200), "white")
    draw = ImageDraw.Draw(image)
    for x in range(350, 1900, 50):
        draw.rectangle((x, 300, x + 30, 350), fill="black")
    boxes = tile_boxes(image)
    assert boxes[0][0] <= 350 and boxes[0][2] >= 1880
    assert boxes[0][1] <= 300 and boxes[0][3] > 350
    assert boxes[0][2] - boxes[0][0] > 960
    assert (boxes[0][2] - boxes[0][0]) * (boxes[0][3] - boxes[0][1]) <= 960 * 640
    assert all(b[2] - b[0] <= MAX_WIDTH and b[3] - b[1] <= MAX_HEIGHT for b in boxes)
    for y in range(0, 3200, 31):
        for x in range(0, 2400, 31):
            assert any(a <= x < c and b <= y < d for a, b, c, d in boxes)


def test_table_rules_and_dense_text_do_not_generate_wide_whole_row_text_strips():
    import numpy as np

    image = Image.new("L", (2400, 1000), 255)
    draw = ImageDraw.Draw(image)
    for y in range(100, 900, 100):
        draw.line((50, y, 2350, y), fill=0, width=2)
    assert not sparse_wide_strips(np.asarray(image) < 210, 960)


def test_light_stamp_does_not_force_two_wide_title_lines_into_one_giant_crop():
    image = Image.new("RGB", (2400, 3200), "white")
    draw = ImageDraw.Draw(image)
    draw.ellipse((900, 100, 1450, 650), outline=(250, 180, 180), width=14)
    for x in range(350, 1900, 50):
        draw.rectangle((x, 300, x + 30, 350), fill="black")
    boxes = tile_boxes(image)
    assert boxes[0][2] - boxes[0][0] > 960
    assert boxes[0][3] - boxes[0][1] < 200
    assert image.getpixel((900, 375)) == (250, 180, 180)


def test_adjacent_short_report_kind_line_stays_with_wide_identity_heading():
    image = Image.new("RGB", (2400, 3200), "white")
    draw = ImageDraw.Draw(image)
    for x in range(350, 1900, 50):
        draw.rectangle((x, 300, x + 30, 350), fill="black")
    for x in range(900, 1200, 50):
        draw.rectangle((x, 390, x + 30, 440), fill="black")
    box = tile_boxes(image)[0]
    assert box[1] < 300 and box[3] > 440
    assert (box[2] - box[0]) * (box[3] - box[1]) <= 960 * 640
