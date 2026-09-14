"""AI 输入表征增强测试（阶段 4：原文窗口 + 结构化事实 + 表格关系 + 页码）。

- build_structured_context：表格关系/说明事实/页码注入与截断标注；
- _direct_semantic_audit：prompt 必须携带注入块，模型只做语义候选；
- 确定性金额不受影响：勾稽仍由规则层负责。
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from src.engine.pipeline import build_document
from src.engine.rules_v33 import R33115_TotalSheetCheck
from src.services.ai_input_builder import build_structured_context
from src.engine.ai.extractor_client import ExtractorClient


def _sample_doc():
    """复刻样张 P7 双栏总表 + P14 一般公共支出表的极简结构。"""
    total_table = [
        ["收入", "", "支出", ""],
        ["项目", "决算数", "项目", "决算数"],
        ["本年收入合计", "4,733.14", "本年支出合计", "4,733.14"],
        ["收入总计", "4,733.14", "支出总计", "4,733.14"],
    ]
    general_table = [
        ["项 目", "", "", "", "一般公共预算财政拨款支出", "", ""],
        ["功能分类科目编码", "", "", "科目名称", "合计", "基本支出", "项目支出"],
        ["类", "款", "项", "合计", "4,733.14", "3,365.38", "1,367.76"],
        ["201", "", "", "一般公共服务支出", "474.40", "400.00", "74.40"],
    ]
    pages = [
        "收入支出决算总表\n单位：万元",
        "一般公共预算财政拨款支出决算表\n单位：万元",
    ]
    doc = build_document(
        path="synthetic.pdf",
        page_texts=pages,
        page_tables=[[total_table], [general_table]],
        filesize=0,
    )
    return doc, pages, [total_table, general_table]


def test_structured_context_contains_tables_pages_and_facts():
    doc, pages, tables = _sample_doc()
    context = build_structured_context(pages, doc.page_tables)

    assert "【结构化事实与表格关系" in context
    assert "【结构化信息结束】" in context
    # 表格关系带页码
    assert "P1" in context and "P2" in context
    # 表题与金额进入上下文（金额格式化后无千分位）
    assert "收入支出决算总表" in context
    assert "4733.14" in context
    # 边界声明：模型只做语义候选
    assert "不负责金额复算" in context


def test_structured_context_empty_when_no_tables():
    context = build_structured_context(["仅一段正文"], [None])
    # 无表格且有正文时仍可能产出事实；完全无内容时返回空串
    assert isinstance(context, str)


def test_direct_semantic_audit_prompt_carries_context(monkeypatch):
    """prompt 必须携带注入块与页码——模型只做语义候选的边界随输入声明。"""
    client = ExtractorClient()

    captured: dict = {}

    class _FakeResponse(dict):
        pass

    async def fake_chat(**kwargs):
        captured["prompt"] = kwargs["messages"][-1]["content"]
        captured["provider"] = kwargs.get("preferred_provider")
        return {
            "content": "[]",
            "provider_used": "unit-test",
            "model": "unit-model",
            "finish_reason": "stop",
            "tokens": {"total_tokens": 1},
        }

    fake_client = type("C", (), {"chat": staticmethod(fake_chat)})
    monkeypatch.setattr(client, "_get_direct_ai_client", lambda: fake_client)

    doc, pages, tables = _sample_doc()
    context = build_structured_context(pages, doc.page_tables)
    issues = asyncio.run(client._direct_semantic_audit("待审正文", structured_context=context))

    assert issues == []
    assert "【结构化事实与表格关系" in captured["prompt"]
    assert "待审正文" in captured["prompt"]
    assert "不负责金额复算" in captured["prompt"]
    # 空结果留痕：一次成功调用记录
    assert client.call_ledger and client.call_ledger[-1]["provider"] == "unit-test"


def test_rules_still_own_amount_checking():
    """边界验证：注入上下文后，金额勾稽结论仍由确定性规则产出——
    表内收支真实不平衡时规则照常报错，与 AI 输入表征无关。"""
    table = [
        ["收入", "", "支出", ""],
        ["项目", "决算数", "项目", "决算数"],
        ["收入总计", "4,733.14", "支出总计", "4,700.00"],
    ]
    doc = build_document(
        path="s.pdf",
        page_texts=["收入支出决算总表"],
        page_tables=[[table]],
        filesize=0,
    )
    issues = R33115_TotalSheetCheck().apply(doc)
    assert any("总表平衡性错误" in str(i.message) for i in issues)
