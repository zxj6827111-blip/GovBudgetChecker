"""只读探测当前生产函数；合成反例，不代表34份原件新增失败。

独立执行：.venv/bin/python outputs/pdf_input_technical_diagnosis_20261017/numeric_identity_probes.py
只在本脚本目录写入同名JSON；不改源PDF、生产代码、冻结验收记录或服务。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.services.document_profile_resolver import resolve_document_profile
from src.services.fiscal_fact_materializer import FiscalFactMaterializer
from src.services.pdf_parser import PDFParser


def source(path: str, lines: list[int]) -> dict:
    target = ROOT / path
    return {
        "path": path,
        "lines": lines,
        "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
    }


def main() -> None:
    parser = PDFParser.__new__(PDFParser)
    numeric_inputs = [
        "-12.30", "－12.30", "−12.30", "—12.30", "--12.30",
        "（12.30）", "12.30%", "0.00", "—", "",
    ]
    numeric_outputs = [
        {"input": text, "actual_numeric_value": parser._parse_numeric(text)}
        for text in numeric_inputs
    ]

    class SyntheticPage:
        def extract_text(self) -> str:
            return "表A 单位：万元\n表B 单位：元"

    page = SyntheticPage()
    unit_title = "表B"
    unit_rows = [["科目", "金额"], ["单位：元", ""]]
    unit_output = parser._detect_unit_hint(page, unit_title, unit_rows)

    year_inputs = {
        "filename": "2025年某局决算.pdf",
        "page_texts": ["上海市某局\n2024年度部门决算"],
    }
    profile = resolve_document_profile(**year_inputs)
    year = profile.report_year
    year_output = {
        "value": year.value,
        "source": year.source,
        "evidence": year.evidence,
        "rejected": year.rejected,
    }

    label_inputs = [{"raw_text": "其中：所属单位合计补助支出"}]
    label_output = FiscalFactMaterializer(None)._extract_classification(label_inputs)
    results = {
        "scope": "synthetic_function_level_probes",
        "is_new_original_pdf_acceptance_failure": False,
        "statement": "仅证明所列函数对合成输入的当前行为；未据此认定现有34份原件新增故障。",
        "production_mutated": False,
        "probes": [
            {
                "category": "numeric_sign_and_type",
                "entry_point": "PDFParser._parse_numeric",
                "source": source("src/services/pdf_parser.py", [1795, 1813, 1815, 1818, 1824]),
                "cases": numeric_outputs,
                "semantics_to_preserve": {
                    "−12.30": "Unicode减号应保留负数含义；当前不识别。",
                    "—12.30": "长横线与数字的组合不能删除横线后默认为正数；应结合原字符身份识别或明确歧义。",
                    "--12.30": "双横线与数字组合不能删除横线后默认为正数；应识别占位符、分离字符或明确歧义。",
                    "12.30%": "必须保留百分比类型，不能与无百分号金额具有相同、无类型的数值输出。",
                    "0.00": "明确零值必须与空白和单独破折号区分；本次数值函数对0及空白的基础行为符合此要求。",
                },
                "existing_protections": ["ASCII/全角负号及中文括号已支持", "纯空白/纯长横线不转零", "数字间空白合并受到拒绝保护"],
            },
            {
                "category": "unit_spatial_scope",
                "entry_point": "PDFParser._detect_unit_hint",
                "source": source("src/services/pdf_parser.py", [1172, 1178, 1180, 1182, 1186]),
                "input": {"page_text": page.extract_text(), "title": unit_title, "rows": unit_rows},
                "actual_unit": unit_output,
                "semantics_to_preserve": "表B明确声明元；同页表A万元不能覆盖B。应基于表/列/格及附近说明的空间作用域绑定单位；本合成页面无bbox，只证明现实现按全页首命中取值。",
                "expected_synthetic_table_unit": "元",
                "existing_protections": ["无单位时归一函数不默认万元", "已知单位冲突的续表不合并"],
            },
            {
                "category": "report_year_source_conflict",
                "entry_point": "resolve_document_profile",
                "source": source("src/services/document_profile_resolver.py", [427, 433, 434, 435, 438, 442]),
                "input": year_inputs,
                "actual_report_year": year_output,
                "semantics_to_preserve": "封面明确报告年度2024而文件名2025。不能无冲突记录地把文件名作为事实年度；应优先原件标题并保留矛盾候选。",
                "expected_source_report_year": 2024,
                "existing_protections": ["识别不到年度保持None，不默认2000", "显式指定年份可以单独识别来源"],
            },
            {
                "category": "total_subject_substring",
                "entry_point": "FiscalFactMaterializer._extract_classification",
                "source": source("src/services/fiscal_fact_materializer.py", [303, 307, 310, 312, 314]),
                "input_label_cells": label_inputs,
                "actual_classification": label_output,
                "semantics_to_preserve": "包含合计二字的完整业务标签不自动等于整表total；需要完整标签、所在列与行层级共同确认。原文必须保留。",
                "expected_without_additional_total_proof": {"classification_code": None, "classification_name": label_inputs[0]["raw_text"]},
                "existing_protections": ["完整合计/总计标记可归一空格", "来源原格不因该归一改写"],
            },
        ],
    }
    output = Path(__file__).with_suffix(".json")
    output.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "probe_categories": len(results["probes"]),
                      "synthetic_function_level_only": True}, ensure_ascii=False))


if __name__ == "__main__":
    main()
