"""识别器能将竖直词框自动转正读字，字数本身不能证明页面方向。"""

from src.services.ocr_orientation import content_quality, choose_angle
from src.services.ocr_client import OcrPageResult, OcrBlock


def test_vertical_detected_word_does_not_certify_page_is_upright():
    text = "一般公共预算财政拨款收入"
    vertical = OcrPageResult(1, blocks=[OcrBlock(text, 0.999, [10, 10, 35, 210], 1)])
    horizontal = OcrPageResult(1, blocks=[OcrBlock(text, 0.995, [10, 10, 210, 35], 1)])
    assert content_quality(vertical) == 0
    assert content_quality(horizontal) >= 8
    assert choose_angle(
        {0: content_quality(vertical), 90: content_quality(horizontal), 180: 0, 270: 0}
    ) == (90, "resolved")
