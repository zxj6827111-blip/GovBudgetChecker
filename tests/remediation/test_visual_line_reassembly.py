"""D1 阅读顺序修复：视觉行重组（同行粗体/异字号词块不再被行聚类拆散）。

背景（2026-11-12 民政局批次诊断）：福利院三公说明同一视觉行
"公务用车运行维护支出2.22万元。" 中，粗体金额 "2.22" 的基线比正文低约
3.2pt，pdfplumber 按 top 差聚行时被划成下一行并排到正文之后，正文变成
"…支出 万元。…"，金额与单位分离，导致 CMM-001 金额提取失败与
V33-SG-COMPLETION "未细化披露" 误报。
"""

import hashlib
import json
from pathlib import Path

import pytest

from src.services.visual_line_reassembly import (
    cluster_words_into_lines,
    join_line_text,
    reassemble_page_text,
)

FIXTURE = Path(__file__).parents[1] / "fixtures/visual_line/welfare_p22_words.json"


def _word(text, x0, top, x1, bottom):
    return {"text": text, "x0": x0, "top": top, "x1": x1, "bottom": bottom}


def test_bold_baseline_offset_word_stays_inline():
    # 原件几何：正文 top=268.1，粗体金额 top=271.34（重叠率 0.78 > 0.5）。
    words = [
        _word("公务用车运行维护支出", 119.88, 268.1, 269.88, 283.1),
        _word("万元。主要用于", 310.8, 268.1, 505.56, 283.1),
        _word("2.22", 273.6, 271.34, 306.9, 286.34),
    ]
    lines = cluster_words_into_lines(words)
    assert len(lines) == 1
    assert [w["text"] for w in lines[0]] == ["公务用车运行维护支出", "2.22", "万元。主要用于"]
    assert "公务用车运行维护支出 2.22 万元。主要用于" in reassemble_page_text(words).split("\n")


def test_adjacent_real_lines_are_not_merged():
    # 行距 16pt、字高 15pt 的紧凑双行：重叠率 0，不得误并。
    line1 = _word("第一行", 100.0, 200.0, 150.0, 215.0)
    line2 = _word("第二行", 100.0, 216.0, 150.0, 231.0)
    lines = cluster_words_into_lines([line2, line1])
    assert len(lines) == 2
    assert lines[0][0]["text"] == "第一行" and lines[1][0]["text"] == "第二行"


def test_lines_sorted_by_top_then_x_within_line():
    words = [
        _word("右", 300.0, 100.0, 330.0, 114.0),
        _word("左", 100.0, 100.0, 130.0, 114.0),
        _word("下行", 100.0, 130.0, 150.0, 144.0),
    ]
    lines = cluster_words_into_lines(words)
    assert [[w["text"] for w in line] for line in lines] == [["左", "右"], ["下行"]]


def test_join_line_text_inserts_space_at_word_boundaries():
    # extract_words 只在字符间隙超过其 x 容差处才分词，extract_text 在同样的
    # 间隙也插空格；因此词边界一律以空格相连，与既有页文本行为一致。
    line = [
        _word("事业", 100.0, 200.0, 120.0, 214.0),
        _word("单位", 120.4, 200.0, 140.0, 214.0),
        _word("48.74", 150.0, 200.0, 175.0, 214.0),
    ]
    assert join_line_text(line) == "事业 单位 48.74"
    # 空隙阈值可按需收紧（相邻紧贴词不插空格）。
    assert join_line_text(line, space_gap=0.5) == "事业单位 48.74"


def test_empty_and_invalid_words_ignored():
    assert cluster_words_into_lines([]) == []
    assert reassemble_page_text([{"text": "  "}, {"text": ""}]) == ""
    lines = cluster_words_into_lines([_word("有效", 0, 0, 10, 10), {"text": ""}])
    assert len(lines) == 1 and lines[0][0]["text"] == "有效"


@pytest.fixture(scope="module")
def welfare_p22_words():
    data = json.loads(FIXTURE.read_text())
    # 夹具必须来自冻结原件（诊断清单 sha256 5fb8913f…），防止掉包。
    assert data["source_pdf_sha256"].startswith("5fb8913fd0deb113")
    return data


def test_real_page_bold_amount_rejoins_unit(welfare_p22_words):
    lines = cluster_words_into_lines(welfare_p22_words["words"])
    texts = [join_line_text(line) for line in lines]
    target = [t for t in texts if "公务用车运行维护支出" in t]
    assert target, texts
    # 修复后：金额回到主体词与"万元"之间，同属一行。
    assert "公务用车运行维护支出 2.22 万元。" in target[0]


def test_real_page_bold_amount_split_under_naive_top_sort(welfare_p22_words):
    # 根因对照：按 pdfplumber 的 top 聚类（容差 3pt），粗体金额自成一行落在正文后。
    words = sorted(welfare_p22_words["words"], key=lambda w: (w["top"], w["x0"]))
    naive_lines, current, anchor_top = [], [], None
    for word in words:
        if anchor_top is None or abs(word["top"] - anchor_top) <= 3:
            current.append(word)
            anchor_top = word["top"] if anchor_top is None else anchor_top
        else:
            naive_lines.append(current)
            current, anchor_top = [word], word["top"]
    if current:
        naive_lines.append(current)
    joined = [join_line_text(line) for line in naive_lines]
    body = [t for t in joined if "公务用车运行维护支出" in t]
    assert body and "2.22" not in body[0], "对照失效：说明该页排版已变，请重新审视夹具"


def test_fixture_integrity(welfare_p22_words):
    raw = FIXTURE.read_bytes()
    assert hashlib.sha256(raw).hexdigest()  # 夹具文件自身可复核
    assert welfare_p22_words["page_number"] == 22
