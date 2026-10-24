"""封面原件固定回归：409条室后缀机构事实，以及文件名不能替代封面。"""

import hashlib
import json
import shutil
from pathlib import Path

import pdfplumber
import pymupdf
import pytest

from src.engine.fact_context import build_fact_context_from_artifact
from src.schemas.document_profile import PROFILE_RESOLVER_VERSION
from src.services.document_profile_resolver import resolve_document_profile
from src.services.pdf_parser import PDFParser


FIXTURES = Path(__file__).parents[1] / "fixtures/native_profile_v26"
CASES = json.loads((FIXTURES / "manifest.json").read_text())


def _public_profile_and_facts(path, *, filename=None):
    artifact = PDFParser(None).build_parse_artifact(str(path))
    with pdfplumber.open(path) as pdf:
        texts = [page.extract_text() or "" for page in pdf.pages]
    profile = resolve_document_profile(filename=filename or path.name, page_texts=texts)
    context = build_fact_context_from_artifact(
        artifact,
        org_name=profile.organization_name.value,
        fiscal_year=profile.year,
        report_kind=profile.kind,
    )
    assert context is not None
    return profile, context, texts


@pytest.fixture(scope="module")
def parsed_cases():
    results = {}
    for case in CASES:
        path = FIXTURES / case["filename"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == case["fixture_sha256"]
        results[case["doc_id"]] = _public_profile_and_facts(path)
    return results


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["doc_id"])
def test_source_cover_owner_reaches_every_public_fiscal_fact(parsed_cases, case):
    profile, context, texts = parsed_cases[case["doc_id"]]
    expected = case["expected_owner"]
    # 真值取自解析前冻结的封面文字与原页词坐标，不能以被测facts的值反填。
    cover_words = "".join(word[4] for word in case["source_cover_words"])
    assert expected in "".join(cover_words.split())
    assert expected in "".join(texts[0].split())
    assert profile.organization_name.value == expected
    assert profile.organization_name.source == "page_text"
    assert expected in profile.organization_name.evidence
    assert profile.resolver_version == PROFILE_RESOLVER_VERSION
    assert PROFILE_RESOLVER_VERSION == "document-profile-v5-native-cover-room-owner"
    assert context.org_name == expected
    assert len(context.facts) == case["expected_core_facts"]
    assert {fact.org_name for fact in context.facts} == {expected}


def test_room_owner_regression_contains_all_409_affected_facts(parsed_cases):
    room_ids = {"SH2024N9_02", "SH2024N9_03"}
    assert sum(len(parsed_cases[doc_id][1].facts) for doc_id in room_ids) == 409
    assert all(parsed_cases[doc_id][0].organization_name.value.endswith("室") for doc_id in room_ids)


@pytest.mark.parametrize("filename", ["上海市金融监督管理局.pdf", "上海市人民政府研究室.pdf"])
def test_renaming_financial_office_pdf_does_not_change_source_owner(tmp_path, filename):
    case = next(case for case in CASES if case["doc_id"] == "SH2024N9_05")
    path = tmp_path / filename
    shutil.copyfile(FIXTURES / case["filename"], path)
    profile, context, _ = _public_profile_and_facts(path)
    assert profile.organization_name.value == "中共上海市委金融委员会办公室"
    assert {fact.org_name for fact in context.facts} == {case["expected_owner"]}


@pytest.mark.parametrize("cover, expected", [
    ("上海市人民政府\n研究室\n2024年度部门决算", "上海市人民政府研究室"),
    ("浙江省人民政府\n参事室\n2024年度部门决算", "浙江省人民政府参事室"),
    ("中共广东省委\n研究室\n2024年度部门决算", "中共广东省委研究室"),
    ("甲市人民政府研究室\n乙市人民政府参事室\n2024年度部门决算", None),
    ("上海市人民政府研究室（\n2024年度部门决算", None),
    ("2024年度部门决算\n本报告由财务室编制\n第二部分会议室支出情况", None),
])
def test_native_cover_line_breaks_and_uncertain_owner(cover, expected):
    profile = resolve_document_profile(filename="上海市人民政府研究室.pdf", page_texts=[cover])
    assert profile.organization_name.value == expected
    if expected is None:
        assert profile.organization_name.source == "unresolved"


def test_body_room_mentions_and_misleading_filename_do_not_fill_unknown_facts(tmp_path):
    case = next(case for case in CASES if case["doc_id"] == "SH2024N9_03")
    path = tmp_path / "上海市人民政府研究室.pdf"
    # 仅替换测试副本封面；后续财表保持原生字符和网格，检查真实公开入口传递空身份。
    with pymupdf.open(FIXTURES / case["filename"]) as source, pymupdf.open() as pdf:
        page = pdf.new_page(width=595, height=842)
        page.insert_text((100, 100), "2024年度部门决算", fontname="china-s", fontsize=16)
        page.insert_text((100, 140), "本报告由财务室编制", fontname="china-s", fontsize=12)
        pdf.insert_pdf(source, from_page=1)
        pdf.save(path)
    profile, context, _ = _public_profile_and_facts(path)
    assert profile.organization_name.value is None
    assert context.org_name is None
    assert len(context.facts) == 203
    assert {fact.org_name for fact in context.facts} == {None}
