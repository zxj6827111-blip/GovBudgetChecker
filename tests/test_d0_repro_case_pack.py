"""离线复现脚本必须携带完整抽取结果，失败时不能留下半份输入。"""

from contextlib import nullcontext
from types import SimpleNamespace

import pdfplumber
import pytest

from scripts import d0_repro_case_pack
from src.services import engine_rule_runner, pdf_page_extract


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_second_page", [False, True])
async def test_task_repro_preserves_tables_or_falls_back_atomically(
    monkeypatch, tmp_path, fail_second_page
):
    pages = [SimpleNamespace(index=0), SimpleNamespace(index=1)]
    tables = [[[["项目", "金额"], ["甲", "1.00"]]], []]
    captured = []

    class Runner:
        async def run_rules(self, context, rules, config):
            captured.append(context)
            assert config.ai_enabled is False
            return []

        def get_rule_execution_summary(self):
            return {}

    def extract_tables(page):
        if fail_second_page and page.index == 1:
            raise ValueError("second page extraction failed")
        return tables[page.index]

    monkeypatch.setattr(
        pdfplumber, "open", lambda path: nullcontext(SimpleNamespace(pages=pages))
    )
    monkeypatch.setattr(
        pdf_page_extract, "extract_visible_text_from_page", lambda page: f"第{page.index + 1}页"
    )
    monkeypatch.setattr(pdf_page_extract, "extract_tables_from_page", extract_tables)
    monkeypatch.setattr(engine_rule_runner, "EngineRuleRunner", Runner)

    await d0_repro_case_pack.run(
        str(tmp_path / "original.pdf"), "测试单位", 2025, "dept_final",
        str(tmp_path / "result.json"), task_dir=str(tmp_path / "old-task")
    )

    context = captured[0]
    assert context.page_texts == ([] if fail_second_page else ["第1页", "第2页"])
    assert context.page_tables == ([] if fail_second_page else tables)
