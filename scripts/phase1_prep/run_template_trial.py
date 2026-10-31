#!/usr/bin/env python3
"""2025 决算模板对照最小试运行闭环 harness（试运行专用，不进正式规则链）。

职责（全部离线，不读业务库、不写业务库、不发布、不改绑定）：

1. 从 uploads/.templates 两份 2025 决算模板 extracted.txt 构建试运行导出包
   ``trial_export_2025_final.zip``（catalog.json + checksums.json，结构同
   outputs/tpl_chain_verify_20261031 的离线通道；adoption_kind='issued' 且
   target_year==source_report_year，无需兼容证明）。anchors=[A1,A2,A3] 与
   trial_fixed_sentences=[F1,F2] 为候选内容，状态"待人工确认"。
2. 对 6 类场景 × 2 种归档模式共 12 次运行 material_writing.load（离线分支），
   挂 build_structure_view，运行 CMM-018/019/027/008、模板锚点检查
   （additional_anchor_issues 两个 kind）与固定语句试运行检查。
3. 产出 template_content_candidates.csv/.md（确认清单）、trial_report.json/
   .md（结论矩阵）、integrity.json（脚本与产物 sha256）、场景副本 PDF。

确定性：不使用 random；zip 使用固定 ZipInfo 时间戳；PDF 副本清空元数据并
no_new_id 保存；报告不含墙钟时间。同输入重跑必须字节级一致。
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import os
import re
import sys
import unicodedata
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

SCHEMA = "gbc.template-trial.v1"
TRIAL_YEAR = 2025
PACKAGE_ID = "tpl-trial-2025-final"
DEPT_SOURCE_ID = "9b09cc2f-3f13-44a0-ae4c-888bae343557"
UNIT_SOURCE_ID = "eacee742-42b2-418d-95f0-30494aa6c52b"
TRIAL_NOTE = "试运行候选内容，未经人工发布确认"

# ---------------------------------------------------------------------------
# 中文正则集中区（少量常量；模板提示语提取与年度槽位切分，不再散落他处）。
# ---------------------------------------------------------------------------

#: 与 annual_templates.create_draft 同一模板提示语清单（固定算法，不允许外部注入）。
TEMPLATE_PROMPT_PATTERNS: Tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern)
    for pattern in (
        r"无[^。:：]{1,80}的[，,]仍保留该表格式[，,]并在本表下方作下述说明[:：]",
        r"列明公务接待批次[、,，]人次",
        r"按上述格式依次说明到各项级科目",
        r"[（(]如持平作持平表述[）)]",
        r"列明公务用车购置数量",
        r"无[^:：。]{1,80}的(?:单位|部门)[，,]作下述说明[:：]",
        r"[（(]不涉及的不列[）)]",
    )
)

#: 报告年度槽位：固定语句原文中机构名槽位与核对尾串的分界。
REPORT_YEAR_TOKEN_RE = re.compile(r"20\d{2}")


# ---------------------------------------------------------------------------
# 路径与摘要工具（Mimosa 友好：路径规范化校验、逐字段 digest.update、无 random）。
# ---------------------------------------------------------------------------


def safe_output_path(root: Path, *parts: str) -> Path:
    """路径规范化校验（输出侧）：拒绝 ".." 与绝对路径片段，根目录必须存在。

    所有写入 outputs/tpl_trial_20261031 的文件都经此函数拼装，保证不会因
    场景名等外部字符串拼出越界或相对上跳路径；根目录不存在时直接失败，
    不静默创建目录树。
    """
    if not root.exists() or not root.is_dir():
        raise ValueError("输出根目录必须存在且为目录：" + str(root))
    resolved_root = root.resolve()
    for part in parts:
        candidate = Path(part)
        if not part or part in (".", "..") or ".." in candidate.parts or candidate.is_absolute():
            raise ValueError("拒绝非法路径片段：" + repr(part))
    resolved = resolved_root.joinpath(*parts).resolve()
    if resolved != resolved_root and resolved_root not in resolved.parents:
        raise ValueError("路径越界：" + "/".join(parts))
    return resolved


def require_input_pdf(path: Path) -> Path:
    """路径规范化校验（输入侧）：必须为已存在的 .pdf 文件，拒绝 ".."。"""
    if ".." in path.parts:
        raise ValueError("拒绝相对上跳输入路径：" + str(path))
    resolved = path.resolve()
    if resolved.suffix.lower() != ".pdf" or not resolved.is_file():
        raise ValueError("输入必须是已存在的 PDF 文件：" + str(resolved))
    return resolved


def file_sha256(path: Path) -> str:
    """逐块 digest.update 计算文件 sha256；不做路径/字段拼接串哈希。"""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def payload_sha256(payload: bytes) -> str:
    digest = hashlib.sha256()
    digest.update(payload)
    return digest.hexdigest()


def canonical_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def content_sha256(value: Any) -> str:
    """逐字段 digest.update 计算发布内容哈希（键、值分别写入，不用 f-string 拼样式串）。"""
    digest = hashlib.sha256()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key in sorted(node, key=str):
                digest.update(b"k:")
                digest.update(str(key).encode("utf-8"))
                walk(node[key])
        elif isinstance(node, (list, tuple)):
            for item in node:
                walk(item)
        else:
            digest.update(b"v:")
            digest.update(canonical_text(node).encode("utf-8"))

    walk(value)
    return digest.hexdigest()


def flat(text: Any) -> str:
    """比较用归一：去全部空白（与模板审校锚点 needle 同口径，NFKC + 去空白）。"""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(text or "")))


# ---------------------------------------------------------------------------
# 模板来源与候选内容。
# ---------------------------------------------------------------------------


def template_storage() -> Path:
    from src.services.annual_templates import storage

    return storage()


def load_template_source(source_id: str) -> Dict[str, Any]:
    directory = template_storage() / source_id
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    extracted = (directory / "extracted.txt").read_text(encoding="utf-8")
    extracted_sha = payload_sha256(extracted.encode("utf-8"))
    if extracted_sha != manifest.get("text_sha256"):
        raise ValueError("extracted.txt 与 manifest text_sha256 不一致：" + source_id)
    lines = extracted.splitlines()
    return {
        "source_id": source_id,
        "directory": directory,
        "manifest": manifest,
        "extracted": extracted,
        "lines": lines,
        "extracted_sha256": extracted_sha,
        "original_sha256": manifest.get("sha256"),
        "material_type": (manifest.get("metadata") or {}).get("material_type"),
        "original_filename": (manifest.get("metadata") or {}).get("original_filename"),
    }


def locate_all(lines: List[str], needle: str) -> List[int]:
    """返回与归一文本全等出现的 1 起始行号列表。"""
    target = flat(needle)
    return [index + 1 for index, line in enumerate(lines) if flat(line) == target]


def require_lines(hits: List[int], expected: List[int], label: str) -> None:
    """行号与 2026-10-31 已核对清单必须一致；模板文本变化时直接失败，不得静默。"""
    if hits != expected:
        raise ValueError("模板行号与已核对清单不一致：" + label + " 实际 " + repr(hits))


def build_candidates() -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    """构建锚点 A1/A2/A3 与固定语句 F1/F2 候选条目（全部待人工确认）。"""
    dept = load_template_source(DEPT_SOURCE_ID)
    unit = load_template_source(UNIT_SOURCE_ID)

    a1_text = "七、财政拨款“三公”经费支出决算情况说明"
    a2_text = "一、收入支出决算总表"
    a2_body_text = "收入支出决算总表"
    a3_text = "第四部分 名词解释"
    f1_original = "上海市普陀区XX局2025年度无“三公”经费财政拨款支出。"
    f2_original = "上海市普陀区XX局2025年度无国有资本经营预算财政拨款收入和支出。"
    f1_context_dept = "无“三公”经费财政拨款支出的部门，作下述说明："
    f1_context_unit = "无“三公”经费财政拨款支出的单位，作下述说明："
    f2_context_dept = "无国有资本经营预算财政拨款收入支出的部门，作下述说明："
    f2_context_unit = "无国有资本经营预算财政拨款收入支出的单位，作下述说明："

    # 行号核对（部门模板 9b09cc2f / 单位模板 eacee742，2026-10-31 已核对清单）。
    require_lines(locate_all(dept["lines"], a1_text), [32, 885], "A1 部门（目录32/正文885）")
    require_lines(locate_all(unit["lines"], a1_text), [32, 855], "A1 单位（目录32/正文855）")
    require_lines(locate_all(dept["lines"], a2_text), [16], "A2 部门（目录16）")
    require_lines(locate_all(unit["lines"], a2_text), [16], "A2 单位（目录16）")
    require_lines(locate_all(dept["lines"], a2_body_text), [76], "A2 部门正文表题（行76，无编号）")
    require_lines(locate_all(unit["lines"], a2_body_text), [48], "A2 单位正文表题（行48，无编号）")
    require_lines(locate_all(dept["lines"], a3_text), [37, 925], "A3 部门（目录37/正文925）")
    require_lines(locate_all(unit["lines"], a3_text), [37, 894], "A3 单位（目录37/正文894）")
    require_lines(locate_all(dept["lines"], f1_original), [898], "F1 部门原文行898")
    require_lines(locate_all(unit["lines"], f1_original), [868], "F1 单位原文行868")
    require_lines(locate_all(dept["lines"], f1_context_dept), [897], "F1 部门引导行897")
    require_lines(locate_all(unit["lines"], f1_context_unit), [867], "F1 单位引导行867")
    require_lines(locate_all(dept["lines"], f2_original), [910], "F2 部门原文行910")
    require_lines(locate_all(unit["lines"], f2_original), [880], "F2 单位原文行880")
    require_lines(locate_all(dept["lines"], f2_context_dept), [909], "F2 部门引导行909")
    require_lines(locate_all(unit["lines"], f2_context_unit), [879], "F2 单位引导行879")

    a1_lines = {
        "department": {"body_line": 885, "toc_line": 32},
        "unit": {"body_line": 855, "toc_line": 32},
    }
    a2_lines = {
        "department": {"toc_line": 16, "body_table_line": 76},
        "unit": {"toc_line": 16, "body_table_line": 48},
    }
    a3_lines = {
        "department": {"toc_line": 37, "body_line": 925},
        "unit": {"toc_line": 37, "body_line": 894},
    }
    f1_lines = {"department": {"line": 898, "context_line": 897}, "unit": {"line": 868, "context_line": 867}}
    f2_lines = {"department": {"line": 910, "context_line": 909}, "unit": {"line": 880, "context_line": 879}}

    anchors = [
        {
            "id": "A1", "kind": "normative_section", "scope": "all",
            "text": flat(a1_text), "display_text": a1_text,
            "basis": "两模板第三部分说明章节同名标题（部门模板行885，单位模板行855；目录行32）；候选依据，待人工确认",
            "source_id": DEPT_SOURCE_ID, "source_report_year": TRIAL_YEAR, "occurrence": 1,
            "provenance": a1_lines,
        },
        {
            "id": "A2", "kind": "normative_table", "scope": "all",
            "text": flat(a2_text), "display_text": a2_text,
            "basis": "两模板第二部分决算表目录表列标题（行16；正文表题为无编号的“收入支出决算总表”，部门行76/单位行48）；候选依据，待人工确认",
            "source_id": DEPT_SOURCE_ID, "source_report_year": TRIAL_YEAR, "occurrence": 1,
            "provenance": a2_lines,
        },
        {
            "id": "A3", "kind": "normative_section", "scope": "all",
            "text": flat(a3_text), "display_text": a3_text,
            "basis": "两模板第四部分名词解释标题（部门模板行925，单位模板行894；目录行37）；候选依据，待人工确认",
            "source_id": DEPT_SOURCE_ID, "source_report_year": TRIAL_YEAR, "occurrence": 1,
            "provenance": a3_lines,
        },
    ]

    def fixed_sentence(identifier: str, original: str, check_core: str, guard: str,
                       context_heading: str, context_token: str, condition: str,
                       lines: Dict[str, Dict[str, int]], basis: str) -> Dict[str, Any]:
        flat_original = flat(original)
        year_match = REPORT_YEAR_TOKEN_RE.search(flat_original)
        if not year_match:
            raise ValueError("固定语句原文缺少报告年度槽位：" + identifier)
        tail = flat_original[year_match.start():]
        if check_core not in tail:
            raise ValueError("核对核心不在核对尾串内：" + identifier)
        return {
            "id": identifier, "kind": "fixed_sentence", "scope": "all", "guard": guard,
            "condition": condition, "original": original, "check_core": check_core, "tail": tail,
            "parent_section": "第三部分", "context_heading": context_heading, "context_token": context_token,
            "template_side": {
                "source_id": DEPT_SOURCE_ID, "material_type": "department",
                "line": lines["department"]["line"], "context_line": lines["department"]["context_line"],
                "unit_source_id": UNIT_SOURCE_ID, "unit_material_type": "unit",
                "unit_line": lines["unit"]["line"], "unit_context_line": lines["unit"]["context_line"],
                "source_sha256": content_sha256(original), "extracted_sha256": "",
            },
            "basis": basis,
        }

    # source_sha256 语义：模板原件 .doc 的 sha256（=manifest sha256）。
    fixed = [
        fixed_sentence(
            "F1", f1_original, "无“三公”经费财政拨款支出", "sangong",
            "七、财政拨款“三公”经费支出决算情况说明", "“三公”经费",
            "材料处于“三公”经费空表说明语境",
            f1_lines,
            "两模板空表说明固定语句（部门行897引导/898原文，单位行867引导/868原文）；候选依据，待人工确认",
        ),
        fixed_sentence(
            "F2", f2_original, "无国有资本经营预算财政拨款收入和支出", "state_capital",
            "九、国有资本经营预算财政拨款收入支出决算情况说明", "国有资本",
            "材料处于国有资本经营预算空表说明语境（两模板均有对应行，scope=all）",
            f2_lines,
            "两模板空表说明固定语句（部门行909引导/910原文，单位行879引导/880原文）；候选依据，待人工确认",
        ),
    ]
    for entry in fixed:
        entry["template_side"]["source_sha256"] = dept["original_sha256"]
        entry["template_side"]["unit_source_sha256"] = unit["original_sha256"]
        entry["template_side"]["extracted_sha256"] = dept["extracted_sha256"]
        entry["template_side"]["unit_extracted_sha256"] = unit["extracted_sha256"]

    sources = {"department": dept, "unit": unit}
    return anchors, fixed, sources


# ---------------------------------------------------------------------------
# 试运行导出包构建（离线通道，结构同 outputs/tpl_chain_verify_20261031）。
# ---------------------------------------------------------------------------


def write_deterministic_zip(target: Path, entries: List[Tuple[str, bytes]]) -> None:
    """固定 ZipInfo 时间戳与权限位写入 zip，保证同内容字节级一致。"""
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, payload in sorted(entries, key=lambda item: item[0]):
            info = zipfile.ZipInfo(filename=name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, payload)


def build_trial_export(output_zip: Path) -> Dict[str, Any]:
    from src.services.annual_templates import ALGORITHM, config_2024
    from src.services.annual_templates import sha as canonical_content_sha

    anchors, fixed, sources = build_candidates()
    dept, unit = sources["department"], sources["unit"]
    flat_dept = flat(dept["extracted"])
    flat_unit = flat(unit["extracted"])

    config = config_2024()
    config["domain"] = "final"
    config["supported_pair"] = all(
        token in view for token, view in (
            ("年度部门决算", flat_dept), ("本表反映部门本年度", flat_dept),
            ("年度决算", flat_unit), ("本表反映单位本年度", flat_unit)))
    config["native"] = (
        dict(config["native"]) if config["native"].get("sentence") in flat_unit else {})
    config["prompts"] = sorted({
        match.group()
        for view in (flat_dept, flat_unit)
        for pattern in TEMPLATE_PROMPT_PATTERNS
        for match in pattern.finditer(view)
    })
    config["anchors"] = [dict(item) for item in anchors]
    config["trial_fixed_sentences"] = [dict(item) for item in fixed]

    receipts = {
        "department": {"manifest.json": payload_sha256((dept["directory"] / "manifest.json").read_bytes()),
                       "extracted.txt": dept["extracted_sha256"]},
        "unit": {"manifest.json": payload_sha256((unit["directory"] / "manifest.json").read_bytes()),
                 "extracted.txt": unit["extracted_sha256"]},
    }
    content = {
        "source_receipts": receipts,
        "source_pair_consistent": True,
        "unit_source_id": UNIT_SOURCE_ID,
        "department_source_id": DEPT_SOURCE_ID,
        "report_kind": "final",
        "source_report_year": TRIAL_YEAR,
        "config": config,
        "review_record": {"decision": None, "visual_verified": False, "note": TRIAL_NOTE,
                          "trial_basis": "候选内容直接取自 extracted.txt 已核对行号，未走发布审核"},
    }
    capability_scope = {"writing_auto": True, "position_checks": True, "residue": True, "structure": True}
    # content_sha256 必须与 offline_snapshot 的校验算法一致（annual_templates.sha，
    # canonical JSON）；报告里的 config_content_sha256 才用本脚本逐字段摘要。
    package = {
        "id": PACKAGE_ID, "version": "1.0.0-trial", "report_kind": "final",
        "source_report_year": TRIAL_YEAR, "content": content, "content_sha256": canonical_content_sha(content),
        "unit_source_id": UNIT_SOURCE_ID, "department_source_id": DEPT_SOURCE_ID,
        "predecessor_id": None, "capability_scope": capability_scope,
        "review_record": {"note": TRIAL_NOTE}, "validation_record": {"note": TRIAL_NOTE},
    }
    binding = {
        "region": "上海市", "overlay": "", "report_kind": "final", "target_year": TRIAL_YEAR,
        "revision": 1, "package_id": PACKAGE_ID, "adoption_kind": "issued",
        "capability_scope": capability_scope,
        "basis": {"basis": "试运行离线导出（issued 当年正式下发口径，target_year==source_report_year）；" + TRIAL_NOTE},
    }
    current = {"region": "上海市", "overlay": "", "report_kind": "final", "target_year": TRIAL_YEAR}
    catalog = {"current": [current], "bindings": [binding], "packages": [package], "algorithm": ALGORITHM}
    catalog_bytes = canonical_text(catalog).encode("utf-8")
    checksums = {"catalog.json": payload_sha256(catalog_bytes)}
    write_deterministic_zip(output_zip, [("catalog.json", catalog_bytes),
                                         ("checksums.json", canonical_text(checksums).encode("utf-8"))])
    return {
        "zip_path": output_zip, "zip_sha256": file_sha256(output_zip),
        "content_sha256": package["content_sha256"],
        "config_content_sha256": content_sha256(config),
        "algorithm": ALGORITHM, "note": TRIAL_NOTE,
        "capability_scope": capability_scope,
        "anchors": [item["id"] for item in anchors],
        "trial_fixed_sentences": [item["id"] for item in fixed],
    }


# ---------------------------------------------------------------------------
# 场景副本构建（pymupdf redaction / insert_textbox；确定性保存）。
# ---------------------------------------------------------------------------

PART4_HEADING = "第四部分名词解释"
F1_SENTENCE_ORG_SLOT = "上海市统计局（本级）"
SANGONG_SECTION_HEADING = "七、财政拨款“三公”经费支出决算情况说明"
COVER_FIELDS_TEXT = "联系电话：021-12345678\n邮政编码：200000\n"


def open_deterministic(path: Path):
    import pymupdf

    document = pymupdf.open(str(path))
    document.set_metadata({"title": "", "author": "", "subject": "", "keywords": "",
                           "creator": "", "producer": "", "creationDate": "", "modDate": ""})
    return document


def save_deterministic(document, target: Path) -> str:
    document.save(str(target), garbage=3, deflate=True, no_new_id=True)
    document.close()
    return file_sha256(target)


def pdf_pages(path: Path) -> List[str]:
    import pymupdf

    with pymupdf.open(str(path)) as document:
        return [page.get_text() for page in document]


def flat_pages(pages: List[str]) -> List[str]:
    return [flat(page) for page in pages]


def locate_page(pages: List[str], needle: str, occurrence: str = "last") -> Optional[int]:
    hits = [index + 1 for index, page in enumerate(pages) if needle in flat(page)]
    if not hits:
        return None
    return hits[-1] if occurrence == "last" else hits[0]


def build_scenario4(src: Path, dst: Path) -> Dict[str, Any]:
    """④ S1 副本：删除“第四部分 名词解释”标题（目录续页与正文一并删，缺章节造错）。"""
    import pymupdf

    original_pages = pdf_pages(src)
    before_pages = locate_page(original_pages, PART4_HEADING, "first"), locate_page(original_pages, PART4_HEADING, "last")
    document = open_deterministic(src)
    redacted = []
    for index, page in enumerate(document, start=1):
        detail = page.get_text("dict")
        lines = []
        for block in detail["blocks"]:
            if block.get("type") != 0:
                continue
            for line in block.get("lines") or []:
                text = "".join(span["text"] for span in line["spans"])
                if text.strip():
                    lines.append((pymupdf.Rect(line["bbox"]), flat(text)))
        part4 = [rect for rect, text in lines if "第四部分" in text]
        if not part4:
            continue
        bands = [(rect.y0 - 4.0, rect.y1 + 4.0) for rect in part4]
        targets: List[pymupdf.Rect] = []
        for rect, text in lines:
            if not text:
                continue
            if "第四部分" not in text and "名词解释" not in text:
                continue
            for low, high in bands:
                if not (rect.y1 < low or rect.y0 > high):
                    targets.append(rect)
                    break
        seen = set()
        for rect in targets:
            key = (round(rect.x0, 1), round(rect.y0, 1), round(rect.x1, 1), round(rect.y1, 1))
            if key in seen:
                continue
            seen.add(key)
            page.add_redact_annot(rect)
        if seen:
            page.apply_redactions()
            redacted.append(index)
    # save_deterministic 内部会关闭 document；副本随后重开用于断言。
    sha = save_deterministic(document, dst)
    copy_pages = pdf_pages(dst)
    flat_copy = [flat(page) for page in copy_pages]
    heading_absent = all(PART4_HEADING not in page for page in flat_copy)
    token_absent = all("第四部分" not in page for page in flat_copy)
    return {
        "redacted_pages": redacted,
        "before_pages": [value for value in before_pages if value],
        "assert_heading_absent": bool(heading_absent and token_absent),
        "assert_original_had_heading": bool(before_pages[0] and before_pages[1]),
        "sha256": sha,
    }


def build_scenario5(src: Path, dst: Path) -> Dict[str, Any]:
    """⑤ S2 副本：注入 F1 模板原句后用 redaction 改动为“……财政拨款支出安排。”。

    S2 原文三公章节为实际支出叙述，不含 F1 句；为测试改动候选判据，先注入
    机构槽位替换后的模板原句，再以 redaction 删除并以 insert_textbox 写回
    改动句。断言：注入后原句存在；改后原句消失、新句存在。
    """
    import pymupdf

    sentence = F1_SENTENCE_ORG_SLOT + "2025年度无“三公”经费财政拨款支出。"
    modified = F1_SENTENCE_ORG_SLOT + "2025年度无“三公”经费财政拨款支出安排。"

    # 第一步：注入模板原句（三公说明章节页正文下方空白区）。
    stage1_path = dst.with_name(dst.stem + "_stage1.pdf")
    document = open_deterministic(src)
    pages = [page.get_text() for page in document]
    target_page = locate_page(pages, SANGONG_SECTION_HEADING, "last")
    if not target_page:
        raise ValueError("未定位到三公说明章节页")
    page = document[target_page - 1]
    blocks = [block["bbox"][3] for block in page.get_text("dict")["blocks"] if block.get("type") == 0]
    rect = pymupdf.Rect(60.0, min(max(blocks) + 10.0, page.rect.height - 82.0),
                        page.rect.width - 60.0, page.rect.height - 40.0)
    rc = page.insert_textbox(rect, sentence + "\n", fontname="china-s", fontsize=10.5)
    if rc < 0:
        raise ValueError("注入模板原句失败（返回值 %s）" % rc)
    stage1_sha = save_deterministic(document, stage1_path)
    stage1_pages = pdf_pages(stage1_path)
    injected_present = any(flat(sentence) in flat(page) for page in stage1_pages)

    # 第二步：redaction 删除原句矩形文本，写回改动句。
    document = open_deterministic(stage1_path)
    page = document[target_page - 1]
    page.add_redact_annot(rect)
    page.apply_redactions()
    rc = page.insert_textbox(rect, modified + "\n", fontname="china-s", fontsize=10.5)
    if rc < 0:
        raise ValueError("写回改动句失败（返回值 %s）" % rc)
    sha = save_deterministic(document, dst)
    stage1_path.unlink()

    copy_pages = pdf_pages(dst)
    flat_copy = [flat(value) for value in copy_pages]
    original_absent = all(flat(sentence) not in value for value in flat_copy)
    modified_present = any(flat(modified) in value for value in flat_copy)
    return {
        "target_page": target_page,
        "assert_injected_original_present": bool(injected_present),
        "assert_original_absent": bool(original_absent),
        "assert_modified_present": bool(modified_present),
        "injected_sentence": sentence,
        "modified_sentence": modified,
        "stage1_sha256": stage1_sha,
        "sha256": sha,
    }


def build_scenario6(src: Path, dst: Path) -> Dict[str, Any]:
    """⑥ S2 副本：仅封面新增允许填写字段（联系电话/邮政编码数字），正文零改动。"""
    import pymupdf

    document = open_deterministic(src)
    page = document[0]
    rect = pymupdf.Rect(60.0, 700.0, 300.0, 760.0)
    rc = page.insert_textbox(rect, COVER_FIELDS_TEXT, fontname="china-s", fontsize=10.5)
    if rc < 0:
        raise ValueError("封面字段写入失败（返回值 %s）" % rc)
    sha = save_deterministic(document, dst)
    original_pages = pdf_pages(src)
    copy_pages = pdf_pages(dst)
    same_body = [flat(a) == flat(b) for a, b in zip(original_pages[1:], copy_pages[1:])]
    cover_only = (
        len(original_pages) == len(copy_pages)
        and all(same_body)
        and flat(COVER_FIELDS_TEXT) in flat(copy_pages[0])
        and flat(copy_pages[0]).startswith(flat(original_pages[0]))
    )
    return {
        "assert_only_cover_changed": bool(cover_only),
        "cover_fields": COVER_FIELDS_TEXT.strip(),
        "sha256": sha,
    }


# ---------------------------------------------------------------------------
# 检查运行。
# ---------------------------------------------------------------------------


def serialize_issue(issue: Any) -> Dict[str, Any]:
    location = getattr(issue, "location", None) or {}
    anchor = location.get("template_anchor")
    payload = {
        "rule": getattr(issue, "rule", None),
        "severity": getattr(issue, "severity", None),
        "message": getattr(issue, "message", None),
        "evidence_text": getattr(issue, "evidence_text", None),
        "page": location.get("page"),
        "checklist_item": location.get("checklist_item"),
        "document_level": bool(location.get("document_level")),
    }
    if isinstance(anchor, dict):
        payload["template_anchor_text"] = anchor.get("text")
    if location.get("expected"):
        payload["expected"] = location.get("expected")
    return payload


def apply_rule(rule: Any, doc: Any) -> Dict[str, Any]:
    from src.engine.rule_outcome import RuleDeferred, RuleNotApplicable

    try:
        issues = rule.apply(doc)
        return {"status": "fail" if issues else "pass", "issues": [serialize_issue(i) for i in issues]}
    except RuleDeferred as exc:
        return {"status": "insufficient_data", "detail": exc.detail,
                "issues": [serialize_issue(i) for i in exc.partial_issues]}
    except RuleNotApplicable as exc:
        return {"status": "not_applicable", "detail": exc.detail, "issues": []}
    except Exception as exc:  # 试运行如实记录执行异常，不冒充检查结论。
        return {"status": "execution_error", "detail": type(exc).__name__ + ": " + str(exc), "issues": []}


#: 与 template_anchor_checks._NUMBER 同口径的标题编号前缀（报告侧定位用）。
_TITLE_NUMBER_PREFIX_RE = re.compile(
    r"^(?:第[一二三四五六七八九十百\d]+部分|[一二三四五六七八九十百\d]+[、.．]|[（(][一二三四五六七八九十百\d]+[）)])")


def located_pages(pages: List[str], needle: str) -> List[int]:
    """报告侧定位：非空、非目录页中锚点标题所在的页码（供人读报告参照）。

    与生产 _headings 同口径：整页包含归一全文，或逐行去编号后与标题全等
    （正文表题可能不带“一、”编号）；定位失败不参与判定，仅供报告阅读。
    """
    from src.engine.checklist_support import is_toc_page

    target = flat(needle)
    stripped = _TITLE_NUMBER_PREFIX_RE.sub("", target, count=1)
    hits = []
    for index, page in enumerate(pages, start=1):
        if not str(page or "").strip() or is_toc_page(page):
            continue
        if target in flat(page):
            hits.append(index)
            continue
        for line in page.splitlines():
            if _TITLE_NUMBER_PREFIX_RE.sub("", flat(line), count=1) == stripped:
                hits.append(index)
                break
    return sorted(set(hits))


def anchor_results(anchors: List[Dict[str, Any]], doc: Any, pages: List[str]) -> List[Dict[str, Any]]:
    from src.engine.checklist_content_rules import CMM021_TableTitleNormative, CMM023_SectionTemplate
    from src.services.template_anchor_checks import additional_anchor_issues

    section_issues, section_pending = additional_anchor_issues(CMM023_SectionTemplate(), doc, "normative_section")
    table_issues, table_pending = additional_anchor_issues(CMM021_TableTitleNormative(), doc, "normative_table")
    results = []
    for anchor in anchors:
        kind = anchor["kind"]
        issues = section_issues if kind == "normative_section" else table_issues
        pendings = section_pending if kind == "normative_section" else table_pending
        mine_issues = [item for item in issues
                       if (item.location or {}).get("template_anchor", {}).get("text") == anchor["text"]
                       or (item.location or {}).get("expected") == anchor["text"]]
        mine_pending = [item for item in pendings if anchor["text"] in item]
        if mine_issues:
            verdict = "命中"
        elif mine_pending:
            verdict = "未检查"
        else:
            verdict = "正常"
        results.append({
            "anchor_id": anchor["id"],
            "kind": kind,
            "scope": anchor["scope"],
            "text": anchor["text"],
            "basis": anchor["basis"],
            "planned_rule_scope": "CMM-029(规划)" if verdict == "命中" else None,
            "severity_suggestion": {"命中": "medium", "未检查": None, "正常": None}.get(verdict),
            "verdict": verdict,
            "material_pages": located_pages(pages, anchor["text"]),
            "issues": [serialize_issue(item) for item in mine_issues],
            "pending_reasons": mine_pending,
        })
    return results


def run_one(scenario: Dict[str, Any], mode: Dict[str, Any], trial: Dict[str, Any]) -> Dict[str, Any]:
    from src.engine.checklist_identity_rules import CMM018_ScopeTerminology, CMM019_CoverElements, CMM027_TemplatePositionScope
    from src.engine.checklist_support import is_toc_page
    from src.engine.checklist_text_rules import CMM008_TemplateResidue
    from src.engine.rules_v33 import build_document
    from src.services.document_profile_resolver import detect_cover_facts, resolve_document_profile
    from src.services.material_writing import load as writing_load
    from src.services.structure_view import build_structure_view
    from src.services.template_fixed_sentence_checks import trial_fixed_sentence_findings

    document_path = require_input_pdf(Path(scenario["path"]))
    pages = pdf_pages(document_path)
    file_hash = file_sha256(document_path)
    cover = detect_cover_facts(pages)
    profile = resolve_document_profile(filename=document_path.name, page_texts=pages)

    archive_basis = None
    if mode.get("archive_basis"):
        archive_basis = {
            "organization_id": "trial-org",
            "organization_name": scenario["selected_org_name"],
            "level": scenario["selected_level"],
            "source": "upload_selection",
        }
    spans = [{"page": index, "start": 0, "end": len(text), "source_method": "native_text"}
             for index, text in enumerate(pages, start=1)]
    resolution = asyncio.run(writing_load(
        "tpl-trial-20261031", file_hash, profile, pages,
        source_spans=spans, archive_basis=archive_basis, cover_name=cover.get("cover_org_name")))

    profile.writing_scope = resolution
    doc = build_document(str(document_path), list(pages), [], document_path.stat().st_size)
    doc.report_kind = "final"
    doc.document_profile = profile
    doc.structure_view = build_structure_view(pages, None, {"scanned_pages": []})
    doc.writing_page_texts = list(pages)
    doc.writing_source_spans = spans

    snapshot = resolution.get("snapshot") or {}
    config = snapshot.get("effective_config") or (snapshot.get("content") or {}).get("config") or {}
    checks = {
        "CMM-018": apply_rule(CMM018_ScopeTerminology(), doc),
        "CMM-019": apply_rule(CMM019_CoverElements(), doc),
        "CMM-027": apply_rule(CMM027_TemplatePositionScope(), doc),
        "CMM-008": apply_rule(CMM008_TemplateResidue(), doc),
        "anchors": anchor_results(trial["anchors"], doc, pages),
        "trial_fixed_sentences": trial_fixed_sentence_findings(doc),
    }
    toc_pages = [index + 1 for index, page in enumerate(pages) if is_toc_page(page)]

    result = {
        "scenario_id": scenario["id"],
        "scenario_desc": scenario["desc"],
        "mode": mode["id"],
        "mode_desc": mode["desc"],
        "sample_id": scenario["sample_id"],
        "document_path": str(document_path),
        "document_sha256": file_hash,
        "is_modified_copy": bool(scenario.get("builder")),
        "build_report": scenario.get("build_report"),
        "page_count": len(pages),
        "toc_pages_skipped_by_iter_pages": toc_pages,
        "cover": {"title": cover.get("cover_title"), "org_name": cover.get("cover_org_name")},
        "archive_basis": archive_basis,
        "profile": {"report_kind": "final",
                    "year": getattr(profile.year, "value", None) if profile.year is not None else None,
                    "jurisdiction": getattr(getattr(profile, "jurisdiction", None), "value", None)},
        "selected": {
            "available": snapshot.get("available"),
            "reason": snapshot.get("reason") or "",
            "package_id": snapshot.get("package_id"),
            "source_report_year": snapshot.get("source_report_year"),
            "target_year": snapshot.get("target_year"),
            "revision": snapshot.get("revision"),
            "adoption_kind": snapshot.get("adoption_kind"),
            "capability_scope": snapshot.get("capability_scope"),
            "offline_export_sha256": snapshot.get("offline_export_sha256"),
        },
        "config_content_sha256": content_sha256(config),
        "resolution": {
            "observed_template_style": resolution.get("observed_template_style"),
            "status": resolution.get("status"),
            "value": resolution.get("value"),
            "source": resolution.get("source"),
            "reason": resolution.get("reason"),
            "cover_independent_scope": resolution.get("cover_independent_scope"),
            "archive_subject_mismatch": resolution.get("archive_subject_mismatch"),
            "archive_conflict": resolution.get("archive_conflict"),
            "template_capability_pending": resolution.get("template_capability_pending"),
            "evidence": [{"page": item.get("page"), "kind": item.get("kind"), "scope": item.get("scope"),
                          "text": item.get("text")} for item in resolution.get("evidence", [])],
        },
        "checks": checks,
    }
    return result


# ---------------------------------------------------------------------------
# 确认清单输出。
# ---------------------------------------------------------------------------

CANDIDATE_COLUMNS = ["候选编号", "建议分类", "适用层级", "模板侧-部门来源ID", "部门extracted行号",
                     "模板侧-单位来源ID", "单位extracted行号", "模板原件文件名", "原件sha256（=manifest sha256）",
                     "提取文本sha256", "原文全文", "核对核心/锚点说明", "条件语境/依据", "状态", "备注"]


def write_candidates_csv(path: Path, anchors: List[Dict[str, Any]], fixed: List[Dict[str, Any]],
                         sources: Dict[str, Dict[str, Any]]) -> None:
    dept, unit = sources["department"], sources["unit"]
    with open(path, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(CANDIDATE_COLUMNS)
        for anchor in anchors:
            provenance = anchor["provenance"]
            writer.writerow([
                anchor["id"], anchor["kind"], anchor["scope"],
                DEPT_SOURCE_ID, json.dumps(provenance["department"], ensure_ascii=False),
                UNIT_SOURCE_ID, json.dumps(provenance["unit"], ensure_ascii=False),
                dept["original_filename"] + " / " + unit["original_filename"],
                dept["original_sha256"] + " / " + unit["original_sha256"],
                dept["extracted_sha256"] + " / " + unit["extracted_sha256"],
                anchor["display_text"], "锚点原文（NFKC 去空白后：" + anchor["text"] + "）",
                anchor["basis"], "候选（待人工确认）",
                "两模板同名同文；试运行导出包 anchors 已包含",
            ])
        for entry in fixed:
            side = entry["template_side"]
            writer.writerow([
                entry["id"], entry["kind"], entry["scope"],
                side["source_id"], json.dumps({"原文行": side["line"], "引导行": side["context_line"]}, ensure_ascii=False),
                side["unit_source_id"], json.dumps({"原文行": side["unit_line"], "引导行": side["unit_context_line"]}, ensure_ascii=False),
                dept["original_filename"] + " / " + unit["original_filename"],
                side["source_sha256"] + " / " + side["unit_source_sha256"],
                side["extracted_sha256"] + " / " + side["unit_extracted_sha256"],
                entry["original"], "核对核心：" + entry["check_core"] + "；核对尾串：" + entry["tail"],
                entry["condition"] + "；" + entry["basis"], "候选（待人工确认）",
                "试运行导出包 trial_fixed_sentences 已包含；核对尾串仅允许机构名槽位差异",
            ])


def write_candidates_md(path: Path, anchors: List[Dict[str, Any]], fixed: List[Dict[str, Any]],
                        sources: Dict[str, Dict[str, Any]]) -> None:
    dept, unit = sources["department"], sources["unit"]
    lines: List[str] = [
        "# 2025 决算模板候选内容确认清单（试运行）",
        "",
        "- 生成方式：直接读取 `uploads/.templates/" + DEPT_SOURCE_ID + "`（部门模板）与",
        "  `uploads/.templates/" + UNIT_SOURCE_ID + "`（单位模板）的 `extracted.txt`，行号 1 起始。",
        "- 两份原件均为用户 2026-10-29 提供的修订稿（非未经修改的官方原件），发布批次待核；",
        "  本清单全部条目状态为**候选（待人工确认）**，不冒充已发布标准。",
        "- 原件 sha256 = manifest.json 的 sha256 字段（.doc 原件哈希）；提取文本 sha256 = extracted.txt 哈希。",
        "- 说明：任务下达时预估的固定语句行号（部门 440/452、单位 421）与实际提取文本不符，",
        "  本清单以 2026-10-31 实际核对行号为准：F1 部门 897 引导/898 原文、单位 867/868；",
        "  F2 部门 909/910、单位 879/880（单位侧找到对应行，故 F2 scope=all 并记双侧行号）。",
        "",
    ]
    for anchor in anchors:
        provenance = anchor["provenance"]
        lines += [
            "## " + anchor["id"] + "（建议分类：" + anchor["kind"] + "，适用层级：" + anchor["scope"] + "，状态：候选）",
            "",
            "- 锚点原文：" + anchor["display_text"] + "（归一后 `" + anchor["text"] + "`）",
            "- 部门模板 " + dept["original_filename"] + "（sha256 `" + dept["original_sha256"] + "`）：行 "
            + json.dumps(provenance["department"], ensure_ascii=False),
            "- 单位模板 " + unit["original_filename"] + "（sha256 `" + unit["original_sha256"] + "`）：行 "
            + json.dumps(provenance["unit"], ensure_ascii=False),
            "- 依据：" + anchor["basis"],
            "",
        ]
    for entry in fixed:
        side = entry["template_side"]
        lines += [
            "## " + entry["id"] + "（建议分类：" + entry["kind"] + "，适用层级：" + entry["scope"] + "，状态：候选）",
            "",
            "- 原文全文：" + entry["original"],
            "- 核对核心：`" + entry["check_core"] + "`；核对尾串：`" + entry["tail"] + "`",
            "- 部门模板：引导行 " + str(side["context_line"]) + "、原文行 " + str(side["line"]),
            "- 单位模板：引导行 " + str(side["unit_context_line"]) + "、原文行 " + str(side["unit_line"]),
            "- 条件语境：" + entry["condition"],
            "- 依据：" + entry["basis"],
            "",
        ]
    lines += [
        "## 试运行配置映射",
        "",
        "- anchors：A1、A2、A3 进入试运行导出包 `config.anchors`（A2 用于演示离线无 parse_artifact 时",
        "  “对应表实例未识别→未检查”）。",
        "- trial_fixed_sentences：F1、F2 进入试运行导出包 `config.trial_fixed_sentences`，",
        "  由 `src/services/template_fixed_sentence_checks.py`（试运行专用）消费。",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# 报告输出。
# ---------------------------------------------------------------------------


def check_digest(checks: Dict[str, Any]) -> Dict[str, Any]:
    """⑥ 对比用：仅检查结论（不含路径/哈希等差异字段）。"""
    return {
        "CMM-018": checks["CMM-018"], "CMM-019": checks["CMM-019"],
        "CMM-027": checks["CMM-027"], "CMM-008": checks["CMM-008"],
        "anchors": checks["anchors"], "trial_fixed_sentences": checks["trial_fixed_sentences"],
    }


def verdict_matrix(runs: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows = []
    for key in sorted(runs):
        run = runs[key]
        checks = run["checks"]
        row = {
            "run": key,
            "resolution": run["resolution"]["status"] + ("/" + str(run["resolution"]["value"]) if run["resolution"]["value"] else ""),
            "CMM-018": checks["CMM-018"]["status"],
            "CMM-019": checks["CMM-019"]["status"],
            "CMM-027": checks["CMM-027"]["status"],
            "CMM-008": checks["CMM-008"]["status"],
        }
        for anchor in checks["anchors"]:
            row["anchor_" + anchor["anchor_id"]] = anchor["verdict"]
        for entry in checks["trial_fixed_sentences"]:
            row["fixed_" + entry["id"]] = entry["verdict"] + (
                "/" + entry.get("candidate_type") if entry.get("candidate_type") else "")
        row["archive_subject_mismatch"] = bool(run["resolution"].get("archive_subject_mismatch"))
        rows.append(row)
    return rows


def render_report_md(report: Dict[str, Any], path: Path) -> None:
    lines: List[str] = [
        "# 2025 决算模板对照最小试运行闭环报告",
        "",
        "- 导出包：" + report["template_export"]["zip_path"] + "（sha256 `" + report["template_export"]["zip_sha256"] + "`）",
        "- 发布内容哈希：" + report["template_export"]["content_sha256"],
        "- 说明：" + report["template_export"]["note"],
        "- 锚点 A2 在离线无 parse_artifact 场景下按设计应全部“未检查”（pending 原因含“对应表实例”）。",
        "- 固定语句判定为试运行判据：改动候选（规划 CMM-028，low）、缺失候选（规划 CMM-029，medium），",
        "  全部待人工复核；锚点缺失命中同样按规划 CMM-029 呈现（warn）。",
        "",
        "## 结论矩阵",
        "",
    ]
    rows = report["verification"]["B"]["verdict_matrix"]
    headers = ["run", "resolution", "CMM-018", "CMM-019", "CMM-027", "CMM-008",
               "anchor_A1", "anchor_A2", "anchor_A3", "fixed_F1", "fixed_F2", "archive_subject_mismatch"]
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("|" + "---|" * len(headers))
    for row in rows:
        lines.append("| " + " | ".join(str(row.get(h, "")) for h in headers) + " |")
    lines += ["", "## 断言结果", ""]
    for item in report["verification"]["A"]["items"]:
        lines.append("- **A** " + item["name"] + "：" + ("通过" if item["passed"] else "失败") + "；" + item["detail"])
    for item in report["verification"]["D"]["items"]:
        lines.append("- **D** " + item["name"] + "：" + ("通过" if item["passed"] else "失败") + "；" + item["detail"])
    lines += ["", "## 确定性", "",
              "- trial_report.json 重跑字节级一致：见交付说明（harness 第二次运行比对）。",
              "- 导出包 sha256：" + report["template_export"]["zip_sha256"],
              "", "## 生产边界观察", ""]
    for note in report["production_boundaries"]:
        lines.append("- " + note)
    lines += ["", "## 各运行明细", ""]
    for key in sorted(report["runs"]):
        run = report["runs"][key]
        lines += [
            "### " + key,
            "",
            "- 场景：" + run["scenario_desc"] + "（模式：" + run["mode_desc"] + "）",
            "- 材料：" + run["document_path"] + "（sha256 `" + run["document_sha256"] + "`）",
            "- 模板选择：available=" + str(run["selected"]["available"]) + "，package=" + str(run["selected"]["package_id"]) +
            "，source_report_year=" + str(run["selected"]["source_report_year"]) + "，target_year=" + str(run["selected"]["target_year"]) +
            "，caps=" + json.dumps(run["selected"]["capability_scope"], ensure_ascii=False),
            "- 配置哈希（config content_sha256）：" + run["config_content_sha256"],
            "- 写法裁决：status=" + str(run["resolution"]["status"]) + "，value=" + str(run["resolution"]["value"]) +
            "，source=" + str(run["resolution"]["source"]) + "，reason=" + str(run["resolution"]["reason"]) +
            "，observed=" + str(run["resolution"]["observed_template_style"]) +
            "，archive_subject_mismatch=" + str(run["resolution"]["archive_subject_mismatch"]),
        ]
        for name in ("CMM-018", "CMM-019", "CMM-027", "CMM-008"):
            entry = run["checks"][name]
            suffix = ("；" + entry["detail"]) if entry.get("detail") else ""
            lines.append("- " + name + "：" + entry["status"] + suffix)
            for issue in entry.get("issues", [])[:3]:
                lines.append("  - [" + str(issue.get("severity")) + "] 第" + str(issue.get("page")) + "页 " + str(issue.get("message")))
        for anchor in run["checks"]["anchors"]:
            lines.append("- 锚点 " + anchor["anchor_id"] + "（" + anchor["kind"] + "，原文「" + str(anchor["text"]) + "」）："
                         + anchor["verdict"] + "；材料页 " + str(anchor["material_pages"]) +
                         ("；pending：" + "；".join(anchor["pending_reasons"]) if anchor["pending_reasons"] else "") +
                         "；依据：" + anchor["basis"])
            for issue in anchor["issues"]:
                lines.append("  - [" + str(issue.get("severity")) + "] " + str(issue.get("message")))
        for entry in run["checks"]["trial_fixed_sentences"]:
            side = entry.get("template_side") or {}
            material = entry.get("material_side") or {}
            lines.append("- 固定语句 " + str(entry.get("id")) + "：" + str(entry.get("verdict")) +
                         ("（" + entry["candidate_type"] + "，" + str(entry.get("severity_suggestion")) + "，" + entry["rule_scope"] + "）"
                          if entry.get("candidate_type") else "") +
                         "；模板侧 行" + str(side.get("line")) + " 原文「" + str(side.get("original")) + "」" +
                         ("；材料侧 第" + str(material.get("page")) + "页「" + str(material.get("text")) + "」" if material else "") +
                         "；原因：" + str(entry.get("reason")))
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# 主流程。
# ---------------------------------------------------------------------------

SAMPLES = {
    "S1": {"path": "/Users/zhangxingjin/Desktop/决算测试上海市/2025/上海市统计局/上海市统计局2025年度部门决算.pdf",
           "org_name": "上海市统计局", "level": "department"},
    "S2": {"path": "/Users/zhangxingjin/Desktop/决算测试上海市/2025/上海市统计局/上海市统计局（本级）2025年度决算.pdf",
           "org_name": "上海市统计局（本级）", "level": "unit"},
    "S3": {"path": "/Users/zhangxingjin/Desktop/决算测试上海市/2025/上海市文化和旅游局执法总队/上海市文化和旅游局执法总队2025年度部门决算.pdf",
           "org_name": "上海市文化和旅游局", "level": "department"},
}

MODES = [
    {"id": "mode1_no_archive_basis", "desc": "未选择组织基准（archive_scope=None，无 archive_basis）", "archive_basis": False},
    {"id": "mode2_upload_selection", "desc": "提供 archive_basis（上传所选层级作检查基准，封面主体一致性裁决）", "archive_basis": True},
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=REPO_ROOT / "outputs" / "tpl_trial_20261031")
    parser.add_argument("--samples-root", type=Path,
                        default=Path("/Users/zhangxingjin/Desktop/决算测试上海市/2025"))
    args = parser.parse_args()

    output_dir = args.output_dir.resolve()
    if not output_dir.exists():
        output_dir.mkdir(parents=True)
    if not output_dir.is_dir():
        raise ValueError("输出目录必须是目录：" + str(output_dir))

    # 边界纪律：离线运行不读业务库；模板开关指向本 harness 构建的试运行导出包。
    os.environ.pop("DATABASE_URL", None)
    os.environ["GBC_TEMPLATE_SCOPE"] = "1"
    os.environ.pop("GBC_BUDGET_TEMPLATE_SCOPE", None)

    anchors, fixed, sources = build_candidates()
    output_zip = safe_output_path(output_dir, "trial_export_2025_final.zip")
    trial = build_trial_export(output_zip)
    os.environ["GBC_TEMPLATE_EXPORT"] = str(output_zip)
    os.environ["GBC_TEMPLATE_EXPORT_SHA256"] = trial["zip_sha256"]

    # 样本路径校验与场景副本构建。
    samples_root = args.samples_root.resolve()
    if not samples_root.is_dir():
        raise ValueError("样本根目录必须是已存在目录：" + str(samples_root))
    for sample_id, spec in SAMPLES.items():
        path = require_input_pdf(Path(spec["path"]))
        if samples_root.resolve() not in path.parents:
            raise ValueError("样本不在 --samples-root 之下：" + str(path))
        spec["resolved"] = path
        spec["sha256"] = file_sha256(path)

    scenario4 = build_scenario4(SAMPLES["S1"]["resolved"], safe_output_path(output_dir, "scenario4_缺第四部分标题_S1副本.pdf"))
    scenario5 = build_scenario5(SAMPLES["S2"]["resolved"], safe_output_path(output_dir, "scenario5_改F1固定语句_S2副本.pdf"))
    scenario6 = build_scenario6(SAMPLES["S2"]["resolved"], safe_output_path(output_dir, "scenario6_改封面可填字段_S2副本.pdf"))

    scenarios = [
        {"id": "①S1正常部门", "sample_id": "S1", "desc": "S1 上海市统计局 2025 年度部门决算（正常部门样本，原文）",
         "path": SAMPLES["S1"]["resolved"], "selected_org_name": SAMPLES["S1"]["org_name"], "selected_level": SAMPLES["S1"]["level"]},
        {"id": "②S2正常单位", "sample_id": "S2", "desc": "S2 上海市统计局（本级）2025 年度决算（正常单位样本，原文）",
         "path": SAMPLES["S2"]["resolved"], "selected_org_name": SAMPLES["S2"]["org_name"], "selected_level": SAMPLES["S2"]["level"]},
        {"id": "③S3写法混用", "sample_id": "S3", "desc": "S3 上海市文化和旅游局执法总队 2025 年度部门决算（封面/目录写法混用，原文）",
         "path": SAMPLES["S3"]["resolved"], "selected_org_name": SAMPLES["S3"]["org_name"], "selected_level": SAMPLES["S3"]["level"]},
        {"id": "④S1缺第四部分", "sample_id": "S1", "desc": "S1 副本 redact 删除“第四部分 名词解释”标题（造错：缺章节）",
         "path": safe_output_path(output_dir, "scenario4_缺第四部分标题_S1副本.pdf"), "builder": "build_scenario4",
         "build_report": scenario4, "selected_org_name": SAMPLES["S1"]["org_name"], "selected_level": SAMPLES["S1"]["level"]},
        {"id": "⑤S2改F1固定语句", "sample_id": "S2", "desc": "S2 副本注入 F1 原句后 redaction 改动为“财政拨款支出安排”（造错：固定语句被改）",
         "path": safe_output_path(output_dir, "scenario5_改F1固定语句_S2副本.pdf"), "builder": "build_scenario5",
         "build_report": scenario5, "selected_org_name": SAMPLES["S2"]["org_name"], "selected_level": SAMPLES["S2"]["level"]},
        {"id": "⑥S2仅改封面字段", "sample_id": "S2", "desc": "S2 副本仅封面新增联系电话/邮政编码（允许填写字段，应零误报）",
         "path": safe_output_path(output_dir, "scenario6_改封面可填字段_S2副本.pdf"), "builder": "build_scenario6",
         "build_report": scenario6, "selected_org_name": SAMPLES["S2"]["org_name"], "selected_level": SAMPLES["S2"]["level"]},
    ]

    runs: Dict[str, Dict[str, Any]] = {}
    for mode in MODES:
        for scenario in scenarios:
            key = mode["id"] + ":" + scenario["id"]
            runs[key] = run_one(scenario, mode, {"anchors": anchors})
            print("[run] " + key + " -> resolution=" + str(runs[key]["resolution"]["status"]) +
                  "/" + str(runs[key]["resolution"]["value"]) + " anchors=" +
                  "/".join(a["verdict"] for a in runs[key]["checks"]["anchors"]) +
                  " fixed=" + "/".join(e["verdict"] for e in runs[key]["checks"]["trial_fixed_sentences"]))

    # ---- 断言 A / D ----
    assertion_a = []
    assertion_a.append({"name": "④删标题后“第四部分名词解释”在文本层不存在（含“第四部分”字样全清）",
                        "passed": bool(scenario4["assert_original_had_heading"] and scenario4["assert_heading_absent"]),
                        "detail": "删除页：" + str(scenario4["redacted_pages"]) + "；删除前出现页：" + str(scenario4["before_pages"])})
    assertion_a.append({"name": "⑤注入原句存在、redact 改动后原句消失且新句存在",
                        "passed": bool(scenario5["assert_injected_original_present"] and scenario5["assert_original_absent"]
                                       and scenario5["assert_modified_present"]),
                        "detail": "第" + str(scenario5["target_page"]) + "页；原句「" + scenario5["injected_sentence"] +
                                  "」→ 新句「" + scenario5["modified_sentence"] + "」"})
    equality_by_mode = {}
    for mode in MODES:
        key2 = mode["id"] + ":②S2正常单位"
        key6 = mode["id"] + ":⑥S2仅改封面字段"
        equal = check_digest(runs[key2]["checks"]) == check_digest(runs[key6]["checks"])
        equality_by_mode[mode["id"]] = equal
        assertion_a.append({"name": "⑥仅封面字段变化时核心检查结果与②完全一致（" + mode["id"] + "）",
                            "passed": bool(equal),
                            "detail": "对比范围：CMM-018/019/027/008 + 锚点 + 固定语句"})
    verification_a = {"items": assertion_a, "scenario6_equals_scenario2": equality_by_mode}

    a2_pending_reasons = {}
    for key, run in runs.items():
        a2 = next(item for item in run["checks"]["anchors"] if item["anchor_id"] == "A2")
        a2_pending_reasons[key] = a2["pending_reasons"]
    a2_all_pending = all(reasons and any("对应表实例" in reason for reason in reasons)
                         for reasons in a2_pending_reasons.values())

    def fixed_entry(run: Dict[str, Any], identifier: str) -> Dict[str, Any]:
        return next(item for item in run["checks"]["trial_fixed_sentences"] if item["id"] == identifier)

    def anchor_entry(run: Dict[str, Any], identifier: str) -> Dict[str, Any]:
        return next(item for item in run["checks"]["anchors"] if item["anchor_id"] == identifier)

    d4_hits = []
    for key, run in runs.items():
        if not run["scenario_id"].startswith("④"):
            continue
        a3 = anchor_entry(run, "A3")
        d4_hits.append(a3["verdict"] == "命中" and bool(a3["issues"])
                       and all(issue.get("severity") == "warn" for issue in a3["issues"]))
    d5_hits = []
    for key, run in runs.items():
        if not run["scenario_id"].startswith("⑤"):
            continue
        f1 = fixed_entry(run, "F1")
        d5_hits.append(f1["verdict"] == "命中" and f1.get("candidate_type") == "改动候选"
                       and f1.get("severity_suggestion") == "low" and f1.get("rule_scope") == "CMM-028(规划)")
    d6_clean = []
    for mode in MODES:
        key2 = mode["id"] + ":②S2正常单位"
        key6 = mode["id"] + ":⑥S2仅改封面字段"
        d6_clean.append(equality_by_mode[mode["id"]] and
                        all(entry["verdict"] != "命中" for entry in runs[key6]["checks"]["trial_fixed_sentences"]) and
                        all(anchor["verdict"] != "命中" for anchor in runs[key6]["checks"]["anchors"]))

    verification_d = {
        "items": [
            {"name": "A2（表格锚点）在离线无 parse_artifact 下 12/12 运行均“未检查”且 pending 原因含“对应表实例”",
             "passed": bool(a2_all_pending),
             "detail": "示例原因：" + next(iter(a2_pending_reasons.values()))[0]},
            {"name": "④缺章节：A3=命中（warn，规划 CMM-029），两种模式均命中",
             "passed": bool(d4_hits) and all(d4_hits),
             "detail": "判定数：" + str(len(d4_hits))},
            {"name": "⑤改句：F1=命中（改动候选 low，规划 CMM-028），两种模式均命中",
             "passed": bool(d5_hits) and all(d5_hits),
             "detail": "判定数：" + str(len(d5_hits))},
            {"name": "⑥无误报：锚点与固定语句判定与②一致且无新增命中（两种模式）",
             "passed": bool(d6_clean) and all(d6_clean),
             "detail": "模式结果：" + json.dumps(equality_by_mode, ensure_ascii=False)},
        ],
        "A2_pending_sample_reasons": {key: reasons[0] for key, reasons in sorted(a2_pending_reasons.items())[:2]},
    }

    report = {
        "schema": SCHEMA,
        "trial_note": TRIAL_NOTE,
        "template_export": {
            "zip_path": str(output_zip), "zip_sha256": trial["zip_sha256"],
            "content_sha256": trial["content_sha256"], "config_content_sha256": trial["config_content_sha256"],
            "algorithm": trial["algorithm"], "note": trial["note"],
            "capability_scope": trial["capability_scope"],
            "adoption_kind": "issued",
            "anchors": trial["anchors"], "trial_fixed_sentences": trial["trial_fixed_sentences"],
        },
        "samples": {key: {"path": str(spec["resolved"]), "sha256": spec["sha256"]}
                    for key, spec in sorted(SAMPLES.items())},
        "modified_copies": {
            "scenario4": {**{k: v for k, v in scenario4.items() if k != "sha256"},
                          "path": str(safe_output_path(output_dir, "scenario4_缺第四部分标题_S1副本.pdf")),
                          "sha256": scenario4["sha256"]},
            "scenario5": {**{k: v for k, v in scenario5.items() if k != "sha256"},
                          "path": str(safe_output_path(output_dir, "scenario5_改F1固定语句_S2副本.pdf")),
                          "sha256": scenario5["sha256"]},
            "scenario6": {**{k: v for k, v in scenario6.items() if k != "sha256"},
                          "path": str(safe_output_path(output_dir, "scenario6_改封面可填字段_S2副本.pdf")),
                          "sha256": scenario6["sha256"]},
        },
        "runs": runs,
        "verification": {"A": verification_a, "B": {"run_count": len(runs), "verdict_matrix": verdict_matrix(runs)},
                         "D": verification_d},
        "production_boundaries": [
            "is_toc_page 只识别以“目录”开头的页；目录续页（样本第 3 页）不被跳过，模板锚点 _headings 会把目录续页上的标题行当作命中。"
            "因此场景④须把目录续页与正文的“第四部分 名词解释”一并删除才能触发缺失路径；该边界已如实记录，生产修复另行立项。",
            "场景⑤所用 S2 原文三公章节为实际支出叙述（无 F1 句），按造错手段先注入机构槽位替换后的模板原句，再以 redaction 改动；判据路径（核心在页而尾串被改）与自然改写一致。",
            "场景⑥封面原文无联系电话/邮政编码字段，按“允许填写字段”口径新增该两字段（数字内容），正文逐页零改动。",
            "固定语句缺失候选与改动候选判据存在重叠范围（同一未命中情形），全部按试运行判据标注并转人工复核。",
        ],
    }

    report_path = safe_output_path(output_dir, "trial_report.json")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    md_path = safe_output_path(output_dir, "trial_report.md")
    render_report_md(report, md_path)

    csv_path = safe_output_path(output_dir, "template_content_candidates.csv")
    md_candidates = safe_output_path(output_dir, "template_content_candidates.md")
    write_candidates_csv(csv_path, anchors, fixed, sources)
    write_candidates_md(md_candidates, anchors, fixed, sources)

    meta = {
        "algorithm": trial["algorithm"], "zip_sha256": trial["zip_sha256"],
        "content_sha256": trial["content_sha256"], "config_content_sha256": trial["config_content_sha256"],
        "package_id": PACKAGE_ID, "adoption_kind": "issued",
        "capability_scope": trial["capability_scope"], "note": TRIAL_NOTE,
        "anchors": trial["anchors"], "trial_fixed_sentences": trial["trial_fixed_sentences"],
        "db_writes": 0, "business_jobs_touched": 0,
        "env": {"GBC_TEMPLATE_SCOPE": "1", "GBC_TEMPLATE_EXPORT": "已设置",
                "GBC_TEMPLATE_EXPORT_SHA256": "已设置", "DATABASE_URL": "空（离线分支）"},
    }
    safe_output_path(output_dir, "offline_export_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    integrity_files = {
        "scripts/phase1_prep/run_template_trial.py": file_sha256(Path(__file__).resolve()),
        "src/services/template_fixed_sentence_checks.py": file_sha256(REPO_ROOT / "src/services/template_fixed_sentence_checks.py"),
        "outputs/tpl_trial_20261031/trial_export_2025_final.zip": trial["zip_sha256"],
        "outputs/tpl_trial_20261031/offline_export_meta.json": file_sha256(safe_output_path(output_dir, "offline_export_meta.json")),
        "outputs/tpl_trial_20261031/template_content_candidates.csv": file_sha256(csv_path),
        "outputs/tpl_trial_20261031/template_content_candidates.md": file_sha256(md_candidates),
        "outputs/tpl_trial_20261031/trial_report.json": file_sha256(report_path),
        "outputs/tpl_trial_20261031/trial_report.md": file_sha256(md_path),
        "outputs/tpl_trial_20261031/scenario4_缺第四部分标题_S1副本.pdf": scenario4["sha256"],
        "outputs/tpl_trial_20261031/scenario5_改F1固定语句_S2副本.pdf": scenario5["sha256"],
        "outputs/tpl_trial_20261031/scenario6_改封面可填字段_S2副本.pdf": scenario6["sha256"],
    }
    integrity = {
        "schema": "gbc.trial-integrity.v1",
        "note": "脚本与产物 sha256 清单（逐文件 digest.update；integrity.json 自身不在清单内）",
        "repo_root": str(REPO_ROOT),
        "files": integrity_files,
    }
    safe_output_path(output_dir, "integrity.json").write_text(
        json.dumps(integrity, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    failed = [item["name"] for item in [*assertion_a, *verification_d["items"]] if not item["passed"]]
    print("[done] runs=" + str(len(runs)) + " failed_assertions=" + json.dumps(failed, ensure_ascii=False))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
