#!/usr/bin/env python3
"""2025 决算模板对照最小试运行闭环 harness（试运行专用，不进正式规则链）。

r3（2026-10-31，按 outputs/tpl_trial_r2_review_20261031/review.json F1 条件
取数口径整改）：

1. template_fixed_sentence_checks._evaluate_condition 取数口径重写：只认
   “当年决算”口径指标金额（F1 = 本年度“三公”经费财政拨款支出决算合计；
   F2 = 本年度国有资本经营预算财政拨款收入、支出两项）；预算口径（年初
   预算为/预算数/完成预算的）、增减额、含非目标年份 20XX年度 的分句
   （。；切分）一律排除；所需指标齐且全为零才判零值成立，任一指标取不到
   → 条件未知，绝不报缺失。判定流其余语义（章节定位、标准空表表述优先、
   信号混叠、槽位实例化、候选分级）不变。
2. 审核反例 6 例（outputs/tpl_trial_r2_review_20261031/probe_conditions.py
   口径）纳入 R 组自动断言：unknown_control→未检查、both_current_zero_control
   →命中、both_current_positive_control→不适用、incomplete_zero_false_positive
   →未检查、budget_vs_actual_false_negative→命中、prior_year_vs_actual_false
   _negative→命中；旧列 = r2 模块（git show 15afb6a4 导出到 /tmp 后 importlib
   加载）判定，必须与审核 actual（condition_results.json）一致。
3. 结果目录改 outputs/tpl_trial_20261031_r3；integrity 冻结引用补 r2
   integrity 与 r2 审核证据，另对 v1/r2/phase1_prep（含 v3 core 118 清单）
   三个冻结根做“打包前后哈希不变”校验，任何漂移即拒绝发布。

r2（2026-10-31，按 outputs/tpl_trial_review_20261031/review.json 收尾）：

1. 从 uploads/.templates 两份 2025 决算模板 extracted.txt 构建试运行导出包
   ``trial_export_2025_final.zip``（catalog.json + checksums.json，结构同
   outputs/tpl_chain_verify_20261031 的离线通道；adoption_kind='issued' 且
   target_year==source_report_year，无需兼容证明）。anchors=[A1,A2,A3] 与
   trial_fixed_sentences=[F1,F2] 为候选内容，状态"待人工确认"；固定语句条目
   新增所属章节锚 section_anchor。
2. 对 11 类场景 × 2 种归档模式运行 material_writing.load（离线分支），挂
   build_structure_view 与生产解析器（PDFParser.build_parse_artifact，离线
   纯函数入口）产物，运行 CMM-018/019/027/008、模板锚点检查（A1/A2/A3）与
   固定语句试运行检查（v2 判定流：章节定位 + 条件判定 + 章节范围句比对）。
3. 固定语句条件对照组（a–f 小页文本探针）以 git show c633affe 的旧模块出
   旧判定，连同新判定写 old_vs_new.json（旧模块只写 /tmp，不改仓库）；
   r3 起新增审核反例 6 例的 r2（15afb6a4）旧模块对照（review_rows）。
4. 产出 template_content_candidates.csv/.md、trial_report.json/.md、
   old_vs_new.json、integrity.json、场景副本 PDF。
5. 发布纪律：结果目录 outputs/tpl_trial_20261031_r3 已存在且非空 → 退出码
   非 0 拒绝；否则写入同目录隐藏 staging，自校验（断言全过 + 完整性复算
   + 冻结根哈希复算）后 os.replace 原子发布。

确定性：不使用 random；zip 使用固定 ZipInfo 时间戳；PDF 副本清空元数据并
no_new_id 保存；报告不含墙钟时间，报告内的产物路径一律用发布约定相对路径
（outputs/tpl_trial_20261031_r3/...），与实际落盘目录解耦，同输入重跑字节级
一致。

调用边界：固定语句检查模块（template_fixed_sentence_checks）只由本 harness
调用，尚未接入上传主链；发布模板或打开 GBC_TEMPLATE_SCOPE 线上开关也不会
自动接入该模块。本脚本设置的 GBC_TEMPLATE_SCOPE/GBC_TEMPLATE_EXPORT 仅为本
进程离线运行的环境变量，不是任何生产配置变更。
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import unicodedata
import zipfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

SCHEMA = "gbc.template-trial.v2"
TRIAL_YEAR = 2025
PACKAGE_ID = "tpl-trial-2025-final"
DEPT_SOURCE_ID = "9b09cc2f-3f13-44a0-ae4c-888bae343557"
UNIT_SOURCE_ID = "eacee742-42b2-418d-95f0-30494aa6c52b"
TRIAL_NOTE = "试运行候选内容，未经人工发布确认"

#: r3 结果目录（发布约定名）；报告/完整性清单里的路径一律基于它，与
#: --output-dir 实际落盘位置解耦，保证重跑字节一致。
R3_DIR_NAME = "tpl_trial_20261031_r3"

#: 旧版对照（review.json F1/F2）所需的两份 c633affe 模块快照（只写 /tmp）。
LEGACY_FIXED_PATH = Path("/tmp/old_fixed_c633affe.py")
LEGACY_ANCHOR_PATH = Path("/tmp/old_anchor_c633affe.py")

#: 审核反例对照（tpl_trial_r2_review_20261031）所需的 r2 模块快照（只写 /tmp）。
LEGACY_R2_FIXED_PATH = Path("/tmp/old_fixed_15afb6a4.py")

#: 冻结根（只读）：发布前后哈希必须不变；漂移即拒绝发布（Mimosa 友好：
#: 路径规范化校验后逐文件 digest.update，不做串拼接哈希）。
FROZEN_ROOTS = ("outputs/tpl_trial_20261031", "outputs/tpl_trial_20261031_r2",
                "outputs/phase1_prep")

#: 主链接入状态标注（试运行条目逐条携带）。
CHAIN_STATUS_ANCHOR = "尚未接入主链（试运行候选锚点：未发布、未绑定，生产消费者拿不到该候选内容）"
CHAIN_STATUS_FIXED = ("尚未接入主链（template_fixed_sentence_checks 仅由本 harness 调用；"
                      "发布模板或开 GBC_TEMPLATE_SCOPE 线上开关也不会自动接入该模块）")

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


def frozen_root_snapshot(relative_root: str) -> Dict[str, Any]:
    """冻结根快照：排序遍历目录内全部文件，rel 路径与 sha256 逐段 digest.update
    汇成滚动摘要（发布前后复算比对，漂移即拒绝发布；不写任何文件）。"""
    if ".." in Path(relative_root).parts:
        raise ValueError("拒绝相对上跳冻结根：" + relative_root)
    root = (REPO_ROOT / relative_root).resolve()
    if not root.is_dir():
        raise ValueError("冻结根必须是已存在目录：" + relative_root)
    digest = hashlib.sha256()
    file_count = 0
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(REPO_ROOT).as_posix()
        digest.update(b"f:")
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(file_sha256(path).encode("utf-8"))
        digest.update(b"\n")
        file_count += 1
    return {"path": relative_root, "file_count": file_count, "rolling_sha256": digest.hexdigest()}


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
            # 所属章节锚（v2 新增，review F1 修复）：固定句所属的第三部分小节标题
            # 原文，判定流先定位该章节再作条件判定与句比对。
            "section_anchor": context_heading,
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
#: 条件成立自然正例样本（S4）的机构槽位（桌面母体 2025，原文自然含 F1/F2 句）。
FIXED_SENTENCE_ORG_SLOT = "上海市体育彩票管理中心"


def build_parse_artifact_cached(path: Path, cache: Dict[str, Any]) -> Any:
    """生产解析器离线产物缓存：同一物理文件只解析一次（纯函数，不连库）。"""
    from src.services.pdf_parser import PDFParser

    key = str(path)
    if key not in cache:
        cache[key] = PDFParser(None).build_parse_artifact(key)
    return cache[key]


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
    """逐页文本：与生产同一取数路径（pdfplumber extract_text，位置序）。

    v1 用 pymupdf get_text()（内容流序）；造错副本里 insert_textbox 追加的
    内容流位于页尾，位置序与流序不一致会让章节范围判定拿到错序文本。
    生产取数（pdf_page_extract.extract_visible_text_from_page，含可见字符
    过滤）就是 pdfplumber 位置序，r2 起与生产对齐。
    """
    import pdfplumber

    from src.services.pdf_page_extract import extract_visible_text_from_page

    with pdfplumber.open(str(path)) as document:
        return [extract_visible_text_from_page(page) for page in document.pages]


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
# r2 新增场景副本：目录三例与表格三态（造错全部为合法受控 redaction）。
# ---------------------------------------------------------------------------

A2_BODY_TITLE = "收入支出决算总表"
PART4_TOKEN = "第四部分"


def _line_rects(page) -> List[Tuple[Any, str]]:
    """页面文本行的 (矩形, 去空白文本) 列表（redaction 定位用，确定性）。"""
    import pymupdf

    rects: List[Tuple[Any, str]] = []
    for block in page.get_text("dict")["blocks"]:
        if block.get("type") != 0:
            continue
        for line in block.get("lines") or []:
            text = "".join(span["text"] for span in line["spans"])
            if text.strip():
                rects.append((pymupdf.Rect(line["bbox"]), flat(text)))
    return rects


def build_scenario7_toc_keep(src: Path, dst: Path) -> Dict[str, Any]:
    """⑦ S1 副本：仅 redact 正文“第四部分 名词解释”标题，目录区（含续页）保留。

    review F2 反例的正面构造：修复后目录条目不充当正文证据，可靠覆盖下应报
    缺失；目录页的保留状态用文本层断言。
    """
    from src.services.template_anchor_checks import toc_region_pages

    original_pages = pdf_pages(src)
    toc_pages = toc_region_pages(original_pages)
    document = open_deterministic(src)
    redacted: List[int] = []
    for index, page in enumerate(document, start=1):
        if index in toc_pages:
            continue
        targets = [rect for rect, text in _line_rects(page)
                   if PART4_TOKEN in text or text == flat("名词解释")]
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
    sha = save_deterministic(document, dst)
    copy_pages = pdf_pages(dst)
    flat_copy = [flat(page) for page in copy_pages]
    # 目录区里原本含“第四部分名词解释”的页，造错后必须原样保留（目录条目未动）。
    toc_pages_with_heading = [index for index in sorted(toc_pages)
                              if PART4_HEADING in flat(original_pages[index - 1])]
    toc_kept = bool(toc_pages_with_heading) and all(
        PART4_HEADING in flat_copy[index - 1] for index in toc_pages_with_heading)
    body_cleared = all(PART4_TOKEN not in flat_copy[index - 1]
                       for index in range(1, len(copy_pages) + 1) if index not in toc_pages)
    return {
        "builder": "build_scenario7_toc_keep",
        "toc_pages_kept": sorted(toc_pages),
        "toc_pages_with_entry": toc_pages_with_heading,
        "redacted_pages": redacted,
        "assert_toc_entry_kept": bool(toc_kept),
        "assert_body_heading_absent": bool(body_cleared),
        "sha256": sha,
    }


def _a2_qualified_fragments(artifact: Any) -> List[Any]:
    """A2 正文表题的合格片段（page_text_fallback 占位表不算，与生产消费者同语）。"""
    target = flat(A2_BODY_TITLE)
    return [fragment for fragment in artifact.table_fragments
            if flat(fragment.title) == target and fragment.extraction_method != "page_text_fallback"
            and fragment.rows]


def _a2_fragment_page_bbox(src: Path, artifact_cache: Dict[str, Any]) -> Tuple[int, List[float]]:
    """取 A2 正文表题对应片段的页码与 bbox（生产解析器产物，不手拼）。"""
    artifact = build_parse_artifact_cached(src, artifact_cache)
    target = flat(A2_BODY_TITLE)
    for fragment in artifact.table_fragments:
        if flat(fragment.title) == target and fragment.bbox:
            return fragment.page_number, [float(v) for v in fragment.bbox]
    raise ValueError("生产解析产物中未找到 A2 正文表题片段：" + A2_BODY_TITLE)


def build_scenario10_table_only(src: Path, dst: Path, artifact_cache: Dict[str, Any]) -> Dict[str, Any]:
    """⑩ S1 副本：redact A2 表格区域（含框线），保留表标题与页眉。

    只有标题、无合格表实例 → normative_table 锚点应“未检查”（pending 原因含
    “对应表实例”）；造错后用生产解析器复查副本确认实例消失。
    """
    import pymupdf

    page_number, bbox = _a2_fragment_page_bbox(src, artifact_cache)
    document = open_deterministic(src)
    page = document[page_number - 1]
    rect = pymupdf.Rect(bbox[0] - 3.0, bbox[1] - 3.0, bbox[2] + 3.0, bbox[3] + 3.0)
    page.add_redact_annot(rect)
    page.apply_redactions(graphics=pymupdf.PDF_REDACT_LINE_ART_REMOVE_IF_TOUCHED)
    sha = save_deterministic(document, dst)
    copy_pages = pdf_pages(dst)
    title_kept = flat(A2_BODY_TITLE) in flat(copy_pages[page_number - 1])
    # 造错复查：副本重新过生产解析器，A2 合格表实例必须消失（不手拼假表实例）；
    # page_text_fallback 占位表不算合格实例（与生产消费者同语）。
    copy_artifact = build_parse_artifact_cached(dst, artifact_cache)
    table_removed = not _a2_qualified_fragments(copy_artifact)
    return {
        "builder": "build_scenario10_table_only",
        "table_page": page_number,
        "table_bbox": [round(v, 1) for v in bbox],
        "redact_rect": [round(v, 1) for v in (rect.x0, rect.y0, rect.x1, rect.y1)],
        "assert_title_kept": bool(title_kept),
        "assert_table_instance_removed": bool(table_removed),
        "copy_parse_summary": {"table_fragments": len(copy_artifact.table_fragments),
                               "logical_tables": len(copy_artifact.logical_tables),
                               "qualified_a2_fragments": len(_a2_qualified_fragments(copy_artifact))},
        "sha256": sha,
    }


def build_scenario11_title_and_table(src: Path, dst: Path, artifact_cache: Dict[str, Any]) -> Dict[str, Any]:
    """⑪ S1 副本：redact A2 表标题与表格区域（保留页眉，页面仍有文本）。

    可靠覆盖下表标题确实缺失 → normative_table 锚点应“命中”（warn，规划
    CMM-029）；目录区排除修复后目录续页条目不再掩盖该缺失。
    """
    import pymupdf

    page_number, bbox = _a2_fragment_page_bbox(src, artifact_cache)
    document = open_deterministic(src)
    page = document[page_number - 1]
    rects = [pymupdf.Rect(bbox[0] - 3.0, bbox[1] - 3.0, bbox[2] + 3.0, bbox[3] + 3.0)]
    title_rect = next((rect for rect, text in _line_rects(page) if text == flat(A2_BODY_TITLE)), None)
    if title_rect is None:
        raise ValueError("未在第" + str(page_number) + "页定位到 A2 表标题行")
    rects.append(pymupdf.Rect(title_rect.x0 - 2.0, title_rect.y0 - 2.0, title_rect.x1 + 2.0, title_rect.y1 + 2.0))
    for rect in rects:
        page.add_redact_annot(rect)
    page.apply_redactions(graphics=pymupdf.PDF_REDACT_LINE_ART_REMOVE_IF_TOUCHED)
    sha = save_deterministic(document, dst)
    copy_pages = pdf_pages(dst)
    page_text = flat(copy_pages[page_number - 1])
    title_absent = flat(A2_BODY_TITLE) not in page_text
    page_has_other_text = bool(page_text) and "决算表" in page_text
    copy_artifact = build_parse_artifact_cached(dst, artifact_cache)
    table_removed = not _a2_qualified_fragments(copy_artifact)
    return {
        "builder": "build_scenario11_title_and_table",
        "table_page": page_number,
        "table_bbox": [round(v, 1) for v in bbox],
        "assert_title_absent": bool(title_absent),
        "assert_page_keeps_other_text": bool(page_has_other_text),
        "assert_table_instance_removed": bool(table_removed),
        "copy_parse_summary": {"table_fragments": len(copy_artifact.table_fragments),
                               "logical_tables": len(copy_artifact.logical_tables),
                               "qualified_a2_fragments": len(_a2_qualified_fragments(copy_artifact))},
        "sha256": sha,
    }


def build_scenario9_modify_natural(src: Path, dst: Path) -> Dict[str, Any]:
    """⑨ S4 副本（条件成立自然正例上的合法造错）：redact F1 原句后写回
    “……财政拨款支出安排。”。

    S4 原文三公章节就是空表说明语境（无三公支出叙述），与 v1 场景⑤“先注入
    再改写”不同：本例不改条件语境，只改动词，是条件成立材料上的合法造错，
    证明改动候选判据（规划 CMM-028）在真实空表材料上成立。
    """
    import pymupdf

    sentence = FIXED_SENTENCE_ORG_SLOT + "2025年度无“三公”经费财政拨款支出。"
    modified = FIXED_SENTENCE_ORG_SLOT + "2025年度无“三公”经费财政拨款支出安排。"
    original_pages = pdf_pages(src)
    target_page = None
    line_rects: List[Tuple[Any, str]] = []
    for index, page_text in enumerate(original_pages, start=1):
        if flat(SANGONG_SECTION_HEADING) not in flat(page_text) or flat(sentence) not in flat(page_text):
            continue
        document = open_deterministic(src)
        line_rects = _line_rects(document[index - 1])
        document.close()
        target_page = index
        break
    if target_page is None:
        raise ValueError("未在 S4 文本层定位到 F1 原句所在页")
    # 滑窗逐行还原原句所在的最小连续行窗口（原句折行时覆盖全部行）：
    # 优先“去空白后与原句全等”的窗口；全等不存在才退回最小包含窗口。
    # 不能按机构名前缀起窗——同页六、章节的句子同样以机构名开头。
    target_flat = flat(sentence)
    exact_window: Optional[Tuple[int, int]] = None
    contain_window: Optional[Tuple[int, int]] = None
    for start in range(len(line_rects)):
        accumulated = ""
        for end in range(start, min(start + 6, len(line_rects))):
            accumulated += line_rects[end][1]
            acc_flat = flat(accumulated)
            if acc_flat == target_flat:
                if exact_window is None:
                    exact_window = (start, end)
                break
            if target_flat in acc_flat and contain_window is None:
                contain_window = (start, end)
                break
    window = exact_window or contain_window
    if not window:
        raise ValueError("未能逐行还原 F1 原句（第" + str(target_page) + "页）")
    collected: List[Any] = [line_rects[i][0] for i in range(window[0], window[1] + 1)]
    union = collected[0]
    for rect in collected[1:]:
        union |= rect
    rect = pymupdf.Rect(union.x0 - 2.0, union.y0 - 2.0, union.x1 + 2.0, union.y1 + 2.0)
    document = open_deterministic(src)
    page = document[target_page - 1]
    page.add_redact_annot(rect)
    page.apply_redactions()
    rc = page.insert_textbox(rect, modified + "\n", fontname="china-s", fontsize=10.5)
    if rc < 0:
        raise ValueError("写回改动句失败（返回值 %s）" % rc)
    sha = save_deterministic(document, dst)
    copy_pages = pdf_pages(dst)
    original_absent = all(flat(sentence) not in flat(page) for page in copy_pages)
    modified_present = any(flat(modified) in flat(page) for page in copy_pages)
    # 改动句必须仍落在七、三公说明章节内（先于下一个八、小节标题），且章节标题未动：
    # 只改动词，不改条件语境（条件语境完整性是本场景与 v1 场景⑤的本质区别）。
    page_flat = flat(copy_pages[target_page - 1])
    heading_flat = flat(SANGONG_SECTION_HEADING)
    next_flat = flat("八、政府性基金预算财政拨款收入支出决算情况说明")
    heading_at = page_flat.find(heading_flat)
    next_at = page_flat.find(next_flat)
    modified_at = page_flat.find(flat(modified))
    section_intact = (heading_at >= 0 and next_at > heading_at
                      and heading_at < modified_at < next_at)
    return {
        "builder": "build_scenario9_modify_natural",
        "target_page": target_page,
        "redacted_line_window": [window[0], window[1]],
        "assert_original_present_before": True,
        "assert_original_absent": bool(original_absent),
        "assert_modified_present": bool(modified_present),
        "assert_modified_within_section": bool(section_intact),
        "natural_sentence": sentence,
        "modified_sentence": modified,
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
    from src.services.template_anchor_checks import _headings, _matching_table_instances, additional_anchor_issues

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
        # 正文命中页（生产 _headings 同源，含目录区排除后的结果）；表格锚点另
        # 记录唯一匹配实例的 uid/页码/bbox 摘要，供三态报告回溯。
        heading_pages = sorted({item["page"] for item in _headings(doc, anchor["text"])})
        instances: List[Dict[str, Any]] = []
        if kind == "normative_table" and verdict == "正常":
            for matched in _matching_table_instances(doc, anchor["text"], _headings(doc, anchor["text"])):
                instances.append({"instance_uid": matched["instance_uid"],
                                  "fragment_uid": matched["fragment_uid"],
                                  "page": matched["page"],
                                  "bbox": [round(float(v), 1) for v in matched["bbox"]]})
        results.append({
            "anchor_id": anchor["id"],
            "kind": kind,
            "scope": anchor["scope"],
            "text": anchor["text"],
            "basis": anchor["basis"],
            "chain_status": CHAIN_STATUS_ANCHOR,
            "execution_status": "未检查" if verdict == "未检查" else "实际执行",
            "planned_rule_scope": "CMM-029(规划)" if verdict == "命中" else None,
            "severity_suggestion": {"命中": "medium", "未检查": None, "正常": None}.get(verdict),
            "verdict": verdict,
            "material_pages": located_pages(pages, anchor["text"]),
            "heading_pages": heading_pages,
            "instances": instances,
            "issues": [serialize_issue(item) for item in mine_issues],
            "pending_reasons": mine_pending,
        })
    return results


def run_one(scenario: Dict[str, Any], mode: Dict[str, Any], trial: Dict[str, Any],
            artifact_cache: Dict[str, Any]) -> Dict[str, Any]:
    from src.engine.checklist_identity_rules import CMM018_ScopeTerminology, CMM019_CoverElements, CMM027_TemplatePositionScope
    from src.engine.checklist_support import is_toc_page
    from src.engine.checklist_text_rules import CMM008_TemplateResidue
    from src.engine.rules_v33 import build_document
    from src.services.document_profile_resolver import detect_cover_facts, resolve_document_profile
    from src.services.material_writing import load as writing_load
    from src.services.structure_view import build_structure_view
    from src.services.template_fixed_sentence_checks import CHAIN_STATUS as FIXED_CHAIN_STATUS
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
    # 生产解析器离线产物（PDFParser.build_parse_artifact，纯函数不连库）：
    # 同一物理文件只解析一次（含造错副本）；normative_table 锚点据此回溯对应表实例。
    parse_summary: Dict[str, Any] = {"attached": False}
    try:
        artifact = build_parse_artifact_cached(document_path, artifact_cache)
    except Exception as exc:  # 解析失败如实记录为未挂载，不冒充检查结论。
        parse_summary = {"attached": False, "error": type(exc).__name__ + ": " + str(exc)}
        artifact = None
    if artifact is not None:
        doc.parse_artifact = artifact
        parse_summary = {"attached": True, "pages": artifact.page_count,
                         "table_fragments": len(artifact.table_fragments),
                         "logical_tables": len(artifact.logical_tables)}

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
    for entry in checks["trial_fixed_sentences"]:
        entry["chain_status"] = FIXED_CHAIN_STATUS
    toc_pages = [index + 1 for index, page in enumerate(pages) if is_toc_page(page)]

    result = {
        "scenario_id": scenario["id"],
        "scenario_desc": scenario["desc"],
        "mode": mode["id"],
        "mode_desc": mode["desc"],
        "sample_id": scenario["sample_id"],
        # 报告记录发布约定路径（副本为 outputs/tpl_trial_20261031_r3/<文件名>），
        # 与实际落盘 staging 目录解耦，保证重跑字节一致。
        "document_path": scenario.get("published_path") or str(document_path),
        "document_sha256": file_hash,
        "is_modified_copy": bool(scenario.get("builder")),
        "build_report": scenario.get("build_report"),
        "parse_artifact": parse_summary,
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
# 旧版对照与条件对照组（review F1/F2 反例；旧模块快照只写 /tmp，不改仓库）。
# ---------------------------------------------------------------------------


def load_legacy_modules() -> Tuple[Any, Any]:
    """从 c633affe 提取旧版两模块到 /tmp 并以 importlib 加载（只读 git，不改仓库）。"""
    pairs = [
        (LEGACY_FIXED_PATH, "src/services/template_fixed_sentence_checks.py", "legacy_fixed_c633affe"),
        (LEGACY_ANCHOR_PATH, "src/services/template_anchor_checks.py", "legacy_anchor_c633affe"),
    ]
    modules = []
    for target, repo_path, name in pairs:
        source = subprocess.run(
            ["git", "show", "c633affe:" + repo_path],
            cwd=str(REPO_ROOT), check=True, capture_output=True, text=True).stdout
        target.write_text(source, encoding="utf-8")
        spec = importlib.util.spec_from_file_location(name, target)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        modules.append(module)
    return modules[0], modules[1]


def load_r2_fixed_module() -> Any:
    """从 15afb6a4（r2）提取固定语句模块到 /tmp 并以 importlib 加载（只读 git）。

    审核反例 6 例的“旧判定”必须出自 r2 模块本身（r2 败绩 = 审核 actual，
    outputs/tpl_trial_r2_review_20261031/condition_results.json）。
    """
    source = subprocess.run(
        ["git", "show", "15afb6a4:src/services/template_fixed_sentence_checks.py"],
        cwd=str(REPO_ROOT), check=True, capture_output=True, text=True).stdout
    LEGACY_R2_FIXED_PATH.write_text(source, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("legacy_fixed_15afb6a4", LEGACY_R2_FIXED_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["legacy_fixed_15afb6a4"] = module
    spec.loader.exec_module(module)
    return module


def _probe_doc(pages: List[str], entry: Dict[str, Any], org_name: Optional[str]):
    """小页文本探针 doc：与 outputs/tpl_trial_review_20261031/probe_conditions.py 同构。"""
    resolution = {"status": "confirmed", "value": "unit", "source": "archive",
                  "snapshot": {"available": True, "target_year": TRIAL_YEAR,
                               "content": {"config": {"trial_fixed_sentences": [entry]}}}}
    profile = SimpleNamespace(writing_scope=resolution)
    if org_name is not None:
        profile.organization_name = SimpleNamespace(value=org_name, source="cover_label")
    return SimpleNamespace(document_profile=profile, page_texts=pages)


def _verdict_tuple(result: Dict[str, Any]) -> Dict[str, Any]:
    return {"verdict": result.get("verdict"),
            "candidate_type": result.get("candidate_type"),
            "severity_suggestion": result.get("severity_suggestion"),
            "condition_state": result.get("condition_state")}


def run_fixed_sentence_probes(entries_by_id: Dict[str, Dict[str, Any]], legacy_fixed: Any,
                              artifact_cache: Dict[str, Any], natural_sample: Path,
                              natural_copy: Path) -> List[Dict[str, Any]]:
    """固定语句条件对照组 a–f：旧模块（c633affe）与新模块同页文本对照判定。

    a condition_unknown、b explicit_zero_amounts、c positive_amounts、
    d other_section_quotation 为 review.json F1 反例（旧版失败、新版通过）；
    e 槽位放行正例、e2 机构槽位不一致、m 信号混叠（旧⑤路径）、
    f 自然正例与条件成立材料上的合法造错（真实 S4 文本层）。
    """
    from src.services.template_fixed_sentence_checks import trial_fixed_sentence_findings

    heading = "九、国有资本经营预算财政拨款收入支出决算情况说明"
    part3 = "第三部分 决算情况说明"
    cover = "2025年度决算"
    part4 = "第四部分 名词解释"
    f2 = entries_by_id["F2"]
    f1 = entries_by_id["F1"]
    probe_org = "上海市测试环境局"
    natural_pages = pdf_pages(natural_sample)
    natural_copy_pages = pdf_pages(natural_copy)

    def pages(*lines: str) -> List[str]:
        return [cover] + list(lines)

    cases: List[Tuple[str, str, List[str]]] = [
        ("a_condition_unknown", "F2", pages(part3 + "\n" + heading + "\n本节内容待核实。")),
        ("b_explicit_zero_amounts", "F2", pages(part3 + "\n" + heading +
                                               "\n国有资本经营预算财政拨款收入0.00万元，支出0.00万元。")),
        ("c_positive_amounts", "F2", pages(part3 + "\n" + heading +
                                           "\n国有资本经营预算财政拨款收入10.00万元，支出10.00万元。")),
        ("d_other_section_quotation", "F2", pages(part3 + "\n" + heading + "\n本节内容待核实。",
                                                  part4 + "\n引用往年说明示例：2025年度无国有资本经营预算财政拨款收入和支出。")),
        ("e_slot_release_positive", "F2", pages(part3 + "\n" + heading +
                                                "\n" + probe_org + "2025年度无国有资本经营预算财政拨款收入和支出。")),
        ("e2_slot_mismatch", "F2", pages(part3 + "\n" + heading +
                                         "\n上海市普陀区XX局2025年度无国有资本经营预算财政拨款收入和支出。")),
        ("m_mixed_signals_superseded_scenario5", "F1",
         pages(part3 + "\n" + SANGONG_SECTION_HEADING +
               "\n“三公”经费财政拨款支出年初预算为155.83万元，支出决算为124.34万元。\n" +
               F1_SENTENCE_ORG_SLOT + "2025年度无“三公”经费财政拨款支出安排。")),
    ]
    org_by_case = {"e_slot_release_positive": probe_org, "e2_slot_mismatch": probe_org}
    rows: List[Dict[str, Any]] = []
    for name, entry_id, case_pages in cases:
        entry = entries_by_id[entry_id]
        org = org_by_case.get(name, "上海市普陀区XX局")
        old_doc = _probe_doc(case_pages, entry, None)
        new_doc = _probe_doc(case_pages, entry, org)
        old_result = legacy_fixed.trial_fixed_sentence_findings(old_doc)[0]
        new_result = trial_fixed_sentence_findings(new_doc)[0]
        rows.append({"case": name, "entry_id": entry_id,
                     "probe_org": org, "pages": case_pages,
                     "old": _verdict_tuple(old_result), "new": _verdict_tuple(new_result),
                     "new_reason": new_result.get("reason"),
                     "new_section_pages": new_result.get("section_pages"),
                     "new_condition_basis": new_result.get("condition_basis")})
    # f：条件成立自然正例与合法造错（真实样本文本层；旧模块 tail-only，新模块全句锚定）。
    for name, entry_id, sample_pages in (
            ("f_natural_condition_holds_F1", "F1", natural_pages),
            ("f_natural_condition_holds_F2", "F2", natural_pages),
            ("f_natural_modified_F1", "F1", natural_copy_pages)):
        entry = entries_by_id[entry_id]
        old_result = legacy_fixed.trial_fixed_sentence_findings(
            _probe_doc(sample_pages, entry, None))[0]
        new_result = trial_fixed_sentence_findings(
            _probe_doc(sample_pages, entry, FIXED_SENTENCE_ORG_SLOT))[0]
        rows.append({"case": name, "entry_id": entry_id, "probe_org": FIXED_SENTENCE_ORG_SLOT,
                     "pages": "<S4 真实样本逐页文本，共" + str(len(sample_pages)) + "页>",
                     "old": _verdict_tuple(old_result), "new": _verdict_tuple(new_result),
                     "new_reason": new_result.get("reason"),
                     "new_section_pages": new_result.get("section_pages"),
                     "new_condition_basis": new_result.get("condition_basis")})
    return rows


#: 审核反例 6 例（outputs/tpl_trial_r2_review_20261031/probe_conditions.py 口径）：
#: (案例名, 条目, 反例段落, r3 期望 (判定, 候选类型), r2 审核 actual (判定, 候选类型))。
#: r2 审核 actual 取自同目录 condition_results.json（15afb6a4 实际败绩）。
REVIEW_PROBE_CASES: Tuple[Tuple[str, str, str, Tuple[str, Optional[str]], Tuple[str, Optional[str]]], ...] = (
    ("unknown_control", "F2", "本节内容待核实。",
     ("未检查", None), ("未检查", None)),
    ("both_current_zero_control", "F2", "国有资本经营预算财政拨款收入0.00万元，支出0.00万元。",
     ("命中", "缺失候选"), ("命中", "缺失候选")),
    ("both_current_positive_control", "F2", "国有资本经营预算财政拨款收入10.00万元，支出10.00万元。",
     ("不适用", None), ("不适用", None)),
    ("incomplete_zero_false_positive", "F2", "国有资本经营预算财政拨款收入0.00万元，支出情况待核实。",
     ("未检查", None), ("命中", "缺失候选")),
    ("budget_vs_actual_false_negative", "F1", "“三公”经费财政拨款支出年初预算为10.00万元，支出决算为0.00万元。",
     ("命中", "缺失候选"), ("不适用", None)),
    ("prior_year_vs_actual_false_negative", "F2",
     "2025年度国有资本经营预算财政拨款收入0.00万元、支出0.00万元；2024年度收入10.00万元、支出10.00万元。",
     ("命中", "缺失候选"), ("不适用", None)),
)


def run_review_condition_probes(entries_by_id: Dict[str, Dict[str, Any]], r2_fixed: Any
                                ) -> List[Dict[str, Any]]:
    """审核反例 6 例：r2 模块（15afb6a4）与 r3 模块同页文本对照判定。

    口径与 outputs/tpl_trial_r2_review_20261031/probe_conditions.py 完全一致：
    小页文本 = 封面 + 第三部分标题 + 条目 section_anchor + 反例段落；机构槽位
    = 上海市测试局（source 非 filename）；target_year = 2025。断言要求：
    r2 判定 == 审核 actual（败绩复现），r3 判定 == 修复预期（全部达标）。
    """
    from src.services.template_fixed_sentence_checks import trial_fixed_sentence_findings

    org = "上海市测试局"
    rows: List[Dict[str, Any]] = []
    for name, entry_id, paragraph, expected_new, r2_actual in REVIEW_PROBE_CASES:
        entry = entries_by_id[entry_id]
        case_pages = ["2025年度决算",
                      "第三部分 决算情况说明\n" + entry["section_anchor"] + "\n" + paragraph]
        old_result = r2_fixed.trial_fixed_sentence_findings(_probe_doc(case_pages, entry, org))[0]
        new_result = trial_fixed_sentence_findings(_probe_doc(case_pages, entry, org))[0]
        rows.append({"case": name, "entry_id": entry_id, "paragraph": paragraph,
                     "probe_org": org, "pages": "<小页文本×2>",
                     "expected_new": {"verdict": expected_new[0], "candidate_type": expected_new[1]},
                     "r2_actual": {"verdict": r2_actual[0], "candidate_type": r2_actual[1]},
                     "old": _verdict_tuple(old_result), "new": _verdict_tuple(new_result),
                     "new_reason": new_result.get("reason"),
                     "new_section_pages": new_result.get("section_pages"),
                     "new_condition_basis": new_result.get("condition_basis")})
    return rows


def probe_toc_anchor(legacy_anchor: Any) -> List[Dict[str, Any]]:
    """目录/正文对照（review F2）：旧/新 _headings 在同组小页文本上的命中页。"""
    from src.services.template_anchor_checks import _headings

    toc_full = "目录\n第一部分 概况\n第二部分 决算表\n第三部分 决算情况说明\n第四部分 名词解释"
    toc_cont = "七、财政拨款“三公”经费支出决算情况说明\n八、政府性基金\n九、国有资本\n第四部分名词解释"
    body = "第一部分 概况\n本单位职能是testing叙述。"
    expected = "第四部分 名词解释"

    def doc(pages: List[str]) -> SimpleNamespace:
        return SimpleNamespace(page_texts=pages)

    rows = []
    cover_page = "封面2025年度决算"
    only_toc = [cover_page, toc_full, toc_cont, body, "正文其余内容，无第四部分标题。"]
    rows.append({"case": "toc_kept_body_deleted", "pages": ["<小页文本×5>"],
                 "old_heading_pages": sorted({item["page"] for item in legacy_anchor._headings(doc(only_toc), expected)}),
                 "new_heading_pages": sorted({item["page"] for item in _headings(doc(only_toc), expected)})})
    with_body = [cover_page, toc_full, toc_cont, body, "第四部分\n名词解释\n一、财政拨款收入：指……。"]
    rows.append({"case": "toc_kept_body_wrapped_heading_present",
                 "pages": ["<小页文本×5>"],
                 "old_heading_pages": sorted({item["page"] for item in legacy_anchor._headings(doc(with_body), expected)}),
                 "new_heading_pages": sorted({item["page"] for item in _headings(doc(with_body), expected)})})
    return rows


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
        "- anchors：A1、A2、A3 进入试运行导出包 `config.anchors`（r2 起 A2 运行挂生产解析器离线产物，",
        "  展示 正常/未检查/缺失 三态；表实例回溯用 PDFParser.build_parse_artifact，不手拼）。",
        "- trial_fixed_sentences：F1、F2 进入试运行导出包 `config.trial_fixed_sentences`（含 v2 新增",
        "  `section_anchor` 所属章节锚），由 `src/services/template_fixed_sentence_checks.py`（试运行专用）消费；",
        "  该模块仅由本 harness 调用，发布模板或开线上开关也不会自动接入主链。",
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
        "# 2025 决算模板对照最小试运行闭环报告（r3）",
        "",
        "- 导出包：" + report["template_export"]["zip_path"] + "（sha256 `" + report["template_export"]["zip_sha256"] + "`）",
        "- 发布内容哈希：" + report["template_export"]["content_sha256"],
        "- 说明：" + report["template_export"]["note"],
        "- A2 表格锚点已挂生产解析器离线产物（PDFParser.build_parse_artifact），展示 正常/未检查/缺失 三态。",
        "- 固定语句判定为 v2 判定流（章节定位+条件判定+章节范围句比对）：改动候选（规划 CMM-028，low）、",
        "  缺失候选（规划 CMM-029，medium），全部待人工复核；条件未知绝不报缺失。",
        "- r3 条件取数口径：只认当年决算口径指标金额（F1=本年度“三公”经费财政拨款支出决算合计；",
        "  F2=本年度国有资本经营预算财政拨款收入、支出两项），预算口径/增减额/非目标年份分句一律排除；",
        "  指标齐且全为零才判零值成立，任一指标取不到 → 条件未知，绝不报缺失（审核反例 6 例见 R 组断言）。",
        "- 主链接入状态：" + report["chain_status"]["trial_fixed_sentences"],
        "- 执行状态标注：" + report["execution_status_legend"],
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
    for tag, key in (("A", "A"), ("T 目录三例", "T"), ("C 表格三态", "C"), ("D 条件成立组", "D"),
                     ("P 新旧对照", "P"), ("R 审核反例", "R")):
        for item in report["verification"][key]["items"]:
            lines.append("- **" + tag + "** " + item["name"] + "：" + ("通过" if item["passed"] else "失败") + "；" + item["detail"])
    lines += ["", "## 新旧判定对照（c633affe 旧模块 vs v2 判定流）", ""]
    lines.append("| case | 条目 | 旧判定 | 新判定 | condition_state | 章节页 |")
    lines.append("|---|---|---|---|---|---|")
    for row in report["probes"]["rows"]:
        old = row["old"]
        new = row["new"]
        old_text = old["verdict"] + ("/" + old["candidate_type"] if old.get("candidate_type") else "")
        new_text = new["verdict"] + ("/" + new["candidate_type"] if new.get("candidate_type") else "")
        lines.append("| " + row["case"] + " | " + row["entry_id"] + " | " + old_text + " | " + new_text +
                     " | " + str(new.get("condition_state")) + " | " + str(row.get("new_section_pages")) + " |")
    lines += ["", "### 目录/正文 _headings 对照", ""]
    for row in report["probes"]["toc_rows"]:
        lines.append("- " + row["case"] + "：旧命中页 " + str(row["old_heading_pages"]) +
                     " → 新命中页 " + str(row["new_heading_pages"]))
    lines += ["", "### 审核反例 6 例（r2 15afb6a4 vs r3 取数口径；r2 败绩=审核 actual，r3 全部达标）", ""]
    lines.append("| case | 条目 | r2 判定（审核 actual） | r3 判定 | condition_state |")
    lines.append("|---|---|---|---|---|")
    for row in report["probes"]["review_rows"]:
        old = row["old"]
        new = row["new"]
        old_text = old["verdict"] + ("/" + old["candidate_type"] if old.get("candidate_type") else "")
        new_text = new["verdict"] + ("/" + new["candidate_type"] if new.get("candidate_type") else "")
        lines.append("| " + row["case"] + " | " + row["entry_id"] + " | " + old_text + " | " + new_text +
                     " | " + str(new.get("condition_state")) + " |")
    lines += ["", "## 确定性", "",
              "- trial_report.json 重跑字节级一致：报告内产物路径用发布约定相对路径（与落盘目录解耦）。",
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
            "- 解析产物：" + json.dumps(run.get("parse_artifact"), ensure_ascii=False),
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
            lines.append("- 锚点 " + anchor["anchor_id"] + "（" + anchor["kind"] + "，原文「" + str(anchor["text"]) + "」，" +
                         anchor["chain_status"] + "，" + anchor["execution_status"] + "）："
                         + anchor["verdict"] + "；正文命中页 " + str(anchor["heading_pages"]) +
                         "；材料页 " + str(anchor["material_pages"]) +
                         ("；实例：" + json.dumps(anchor["instances"], ensure_ascii=False) if anchor["instances"] else "") +
                         ("；pending：" + "；".join(anchor["pending_reasons"]) if anchor["pending_reasons"] else "") +
                         "；依据：" + anchor["basis"])
            for issue in anchor["issues"]:
                lines.append("  - [" + str(issue.get("severity")) + "] " + str(issue.get("message")))
        for entry in run["checks"]["trial_fixed_sentences"]:
            side = entry.get("template_side") or {}
            material = entry.get("material_side") or {}
            lines.append("- 固定语句 " + str(entry.get("id")) + "（" + str(entry.get("chain_status")) + "，" +
                         str(entry.get("execution_status")) + "）：" + str(entry.get("verdict")) +
                         ("（" + entry["candidate_type"] + "，" + str(entry.get("severity_suggestion")) + "，" + entry["rule_scope"] + "）"
                          if entry.get("candidate_type") else "") +
                         "；条件状态 " + str(entry.get("condition_state")) +
                         "；章节页 " + str(entry.get("section_pages")) +
                         "；模板侧 行" + str(side.get("line")) + " 原文「" + str(side.get("original")) + "」" +
                         ("；材料侧 第" + str(material.get("page")) + "页「" + str(material.get("text")) + "」" if material else "") +
                         "；条件依据：" + str(entry.get("condition_basis")) +
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
    # S4：条件成立自然正例（2025 桌面母体 grep 文本层，原文自然含 F1/F2 句）。
    "S4": {"path": "/Users/zhangxingjin/Desktop/决算测试上海市/2025/上海市体育局/上海市体育彩票管理中心2025年度决算.pdf",
           "org_name": "上海市体育彩票管理中心", "level": "unit"},
}

MODES = [
    {"id": "mode1_no_archive_basis", "desc": "未选择组织基准（archive_scope=None，无 archive_basis）", "archive_basis": False},
    {"id": "mode2_upload_selection", "desc": "提供 archive_basis（上传所选层级作检查基准，封面主体一致性裁决）", "archive_basis": True},
]

#: 副本文件的发布约定名（报告/完整性清单引用；实际落盘在 staging，原子发布后
#: 即 outputs/tpl_trial_20261031_r3/<name>）。
COPY_NAMES = {
    "scenario4": "scenario4_缺第四部分标题_S1副本.pdf",
    "scenario5": "scenario5_改F1固定语句_S2副本.pdf",
    "scenario6": "scenario6_改封面可填字段_S2副本.pdf",
    "scenario7": "scenario7_目录保留删正文第四部分_S1副本.pdf",
    "scenario9": "scenario9_条件成立改F1句_S4副本.pdf",
    "scenario10": "scenario10_A2删表区留标题_S1副本.pdf",
    "scenario11": "scenario11_A2删标题与表区_S1副本.pdf",
}

#: 表格三态（A2）正文表实例的期望页码（冻结样本，sha256 已在 integrity 固定）。
A2_INSTANCE_EXPECTED_PAGES = {"S1": 7, "S2": 7, "S3": 5}


def published_rel(name: str) -> str:
    """发布约定相对路径（与实际落盘目录解耦，保证重跑字节一致）。"""
    return "outputs/" + R3_DIR_NAME + "/" + name


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=REPO_ROOT / "outputs" / R3_DIR_NAME)
    parser.add_argument("--samples-root", type=Path,
                        default=Path("/Users/zhangxingjin/Desktop/决算测试上海市/2025"))
    args = parser.parse_args()

    output_dir = args.output_dir.resolve()
    # ---- r3 发布纪律：已存在非空 → 退出码非 0 拒绝；否则 staging 自校验后原子发布 ----
    if output_dir.exists():
        if not output_dir.is_dir():
            print("[refuse] 输出路径已存在且不是目录：" + str(output_dir), file=sys.stderr)
            return 2
        if any(output_dir.iterdir()):
            print("[refuse] 结果目录已存在且非空，按发布纪律拒绝覆盖：" + str(output_dir), file=sys.stderr)
            return 2
    staging = output_dir.parent / ("." + output_dir.name + ".staging")
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)

    # ---- 冻结根快照（v1/r2/phase1_prep，含 v3 core 118 清单）：发布前基线 ----
    frozen_before = [frozen_root_snapshot(root) for root in FROZEN_ROOTS]
    print("[frozen] 基线快照：" + json.dumps(
        [{"path": item["path"], "file_count": item["file_count"]} for item in frozen_before],
        ensure_ascii=False))

    # 边界纪律：离线运行不读业务库；模板开关只在本进程内指向 harness 构建的试运行导出包。
    os.environ.pop("DATABASE_URL", None)
    os.environ["GBC_TEMPLATE_SCOPE"] = "1"
    os.environ.pop("GBC_BUDGET_TEMPLATE_SCOPE", None)

    anchors, fixed, sources = build_candidates()
    output_zip = safe_output_path(staging, "trial_export_2025_final.zip")
    trial = build_trial_export(output_zip)
    os.environ["GBC_TEMPLATE_EXPORT"] = str(output_zip)
    os.environ["GBC_TEMPLATE_EXPORT_SHA256"] = trial["zip_sha256"]

    # 样本路径校验。
    samples_root = args.samples_root.resolve()
    if not samples_root.is_dir():
        raise ValueError("样本根目录必须是已存在目录：" + str(samples_root))
    for sample_id, spec in SAMPLES.items():
        path = require_input_pdf(Path(spec["path"]))
        if samples_root.resolve() not in path.parents:
            raise ValueError("样本不在 --samples-root 之下：" + str(path))
        spec["resolved"] = path
        spec["sha256"] = file_sha256(path)

    # 生产解析器离线产物缓存（源样本先建；造错副本按需追加）。
    artifact_cache: Dict[str, Any] = {}
    for spec in SAMPLES.values():
        build_parse_artifact_cached(spec["resolved"], artifact_cache)

    # ---- 场景副本构建（写入 staging；报告只引用发布约定名） ----
    scenario4 = build_scenario4(SAMPLES["S1"]["resolved"], safe_output_path(staging, COPY_NAMES["scenario4"]))
    scenario5 = build_scenario5(SAMPLES["S2"]["resolved"], safe_output_path(staging, COPY_NAMES["scenario5"]))
    scenario6 = build_scenario6(SAMPLES["S2"]["resolved"], safe_output_path(staging, COPY_NAMES["scenario6"]))
    scenario7 = build_scenario7_toc_keep(SAMPLES["S1"]["resolved"], safe_output_path(staging, COPY_NAMES["scenario7"]))
    scenario9 = build_scenario9_modify_natural(SAMPLES["S4"]["resolved"], safe_output_path(staging, COPY_NAMES["scenario9"]))
    scenario10 = build_scenario10_table_only(SAMPLES["S1"]["resolved"], safe_output_path(staging, COPY_NAMES["scenario10"]), artifact_cache)
    scenario11 = build_scenario11_title_and_table(SAMPLES["S1"]["resolved"], safe_output_path(staging, COPY_NAMES["scenario11"]), artifact_cache)

    scenarios = [
        {"id": "①S1正常部门", "sample_id": "S1", "desc": "S1 上海市统计局 2025 年度部门决算（正常部门样本，原文）",
         "path": SAMPLES["S1"]["resolved"], "selected_org_name": SAMPLES["S1"]["org_name"], "selected_level": SAMPLES["S1"]["level"]},
        {"id": "②S2正常单位", "sample_id": "S2", "desc": "S2 上海市统计局（本级）2025 年度决算（正常单位样本，原文）",
         "path": SAMPLES["S2"]["resolved"], "selected_org_name": SAMPLES["S2"]["org_name"], "selected_level": SAMPLES["S2"]["level"]},
        {"id": "③S3写法混用", "sample_id": "S3", "desc": "S3 上海市文化和旅游局执法总队 2025 年度部门决算（封面/目录写法混用，原文）",
         "path": SAMPLES["S3"]["resolved"], "selected_org_name": SAMPLES["S3"]["org_name"], "selected_level": SAMPLES["S3"]["level"]},
        {"id": "④S1目录与正文都删第四部分", "sample_id": "S1", "desc": "S1 副本 redact 删除目录续页与正文的“第四部分 名词解释”标题（造错：缺章节）",
         "path": safe_output_path(staging, COPY_NAMES["scenario4"]), "published_path": published_rel(COPY_NAMES["scenario4"]),
         "builder": "build_scenario4", "build_report": scenario4,
         "selected_org_name": SAMPLES["S1"]["org_name"], "selected_level": SAMPLES["S1"]["level"]},
        {"id": "⑤S2改F1固定语句", "sample_id": "S2", "desc": "S2 副本注入 F1 原句后 redaction 改动为“财政拨款支出安排”（旧造错路径；S2 三公章节实为支出叙述，新判定流记信号混叠）",
         "path": safe_output_path(staging, COPY_NAMES["scenario5"]), "published_path": published_rel(COPY_NAMES["scenario5"]),
         "builder": "build_scenario5", "build_report": scenario5,
         "selected_org_name": SAMPLES["S2"]["org_name"], "selected_level": SAMPLES["S2"]["level"]},
        {"id": "⑥S2仅改封面字段", "sample_id": "S2", "desc": "S2 副本仅封面新增联系电话/邮政编码（允许填写字段，应零误报）",
         "path": safe_output_path(staging, COPY_NAMES["scenario6"]), "published_path": published_rel(COPY_NAMES["scenario6"]),
         "builder": "build_scenario6", "build_report": scenario6,
         "selected_org_name": SAMPLES["S2"]["org_name"], "selected_level": SAMPLES["S2"]["level"]},
        {"id": "⑦S1目录保留删正文", "sample_id": "S1", "desc": "S1 副本仅 redact 正文“第四部分 名词解释”，目录区（含续页）保留（review F2 反例正面构造）",
         "path": safe_output_path(staging, COPY_NAMES["scenario7"]), "published_path": published_rel(COPY_NAMES["scenario7"]),
         "builder": "build_scenario7_toc_keep", "build_report": scenario7,
         "selected_org_name": SAMPLES["S1"]["org_name"], "selected_level": SAMPLES["S1"]["level"]},
        {"id": "⑧S4条件成立自然正例", "sample_id": "S4", "desc": "S4 上海市体育彩票管理中心 2025 年度决算（原文自然含 F1/F2 句，空表语境）",
         "path": SAMPLES["S4"]["resolved"], "selected_org_name": SAMPLES["S4"]["org_name"], "selected_level": SAMPLES["S4"]["level"]},
        {"id": "⑨S4改F1固定语句", "sample_id": "S4", "desc": "S4 副本 redact F1 原句后写回“……财政拨款支出安排。”（条件成立材料上的合法造错，覆盖 v1 场景⑤）",
         "path": safe_output_path(staging, COPY_NAMES["scenario9"]), "published_path": published_rel(COPY_NAMES["scenario9"]),
         "builder": "build_scenario9_modify_natural", "build_report": scenario9,
         "selected_org_name": SAMPLES["S4"]["org_name"], "selected_level": SAMPLES["S4"]["level"]},
        {"id": "⑩S1表区删留标题", "sample_id": "S1", "desc": "S1 副本 redact A2 表格区域（含框线）保留表标题（只有标题无合格表实例 → 未检查）",
         "path": safe_output_path(staging, COPY_NAMES["scenario10"]), "published_path": published_rel(COPY_NAMES["scenario10"]),
         "builder": "build_scenario10_table_only", "build_report": scenario10,
         "selected_org_name": SAMPLES["S1"]["org_name"], "selected_level": SAMPLES["S1"]["level"]},
        {"id": "⑪S1删标题与表区", "sample_id": "S1", "desc": "S1 副本 redact A2 表标题与表格区域、保留页眉（可靠覆盖下标题缺失 → 命中）",
         "path": safe_output_path(staging, COPY_NAMES["scenario11"]), "published_path": published_rel(COPY_NAMES["scenario11"]),
         "builder": "build_scenario11_title_and_table", "build_report": scenario11,
         "selected_org_name": SAMPLES["S1"]["org_name"], "selected_level": SAMPLES["S1"]["level"]},
    ]

    runs: Dict[str, Dict[str, Any]] = {}
    for mode in MODES:
        for scenario in scenarios:
            key = mode["id"] + ":" + scenario["id"]
            runs[key] = run_one(scenario, mode, {"anchors": anchors}, artifact_cache)
            print("[run] " + key + " -> resolution=" + str(runs[key]["resolution"]["status"]) +
                  "/" + str(runs[key]["resolution"]["value"]) + " anchors=" +
                  "/".join(a["verdict"] for a in runs[key]["checks"]["anchors"]) +
                  " fixed=" + "/".join(e["verdict"] for e in runs[key]["checks"]["trial_fixed_sentences"]))

    # ---- 固定语句条件对照组与目录/正文对照（旧 c633affe vs 新；审核反例 r2 15afb6a4 vs 新） ----
    entries_by_id = {entry["id"]: entry for entry in fixed}
    legacy_fixed, legacy_anchor = load_legacy_modules()
    r2_fixed = load_r2_fixed_module()
    probe_rows = run_fixed_sentence_probes(entries_by_id, legacy_fixed, artifact_cache,
                                           SAMPLES["S4"]["resolved"],
                                           safe_output_path(staging, COPY_NAMES["scenario9"]))
    review_rows = run_review_condition_probes(entries_by_id, r2_fixed)
    toc_rows = probe_toc_anchor(legacy_anchor)

    # ---- 断言 A（构建与零误报） ----
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
    assertion_a.append({"name": "⑦目录区条目保留、正文标题清除（文本层断言）",
                        "passed": bool(scenario7["assert_toc_entry_kept"] and scenario7["assert_body_heading_absent"]),
                        "detail": "目录区页：" + str(scenario7["toc_pages_kept"]) + "；redact 页：" + str(scenario7["redacted_pages"])})
    assertion_a.append({"name": "⑨条件成立材料造错：原句消失、改动句存在且仍落在七、三公章节内（章节标题未动）",
                        "passed": bool(scenario9["assert_original_absent"] and scenario9["assert_modified_present"]
                                       and scenario9["assert_modified_within_section"]),
                        "detail": "第" + str(scenario9["target_page"]) + "页；原句「" + scenario9["natural_sentence"] +
                                  "」→ 改动句「" + scenario9["modified_sentence"] + "」"})
    assertion_a.append({"name": "⑩表区已删（生产解析器复查副本：A2 表实例消失）、表标题保留",
                        "passed": bool(scenario10["assert_title_kept"] and scenario10["assert_table_instance_removed"]),
                        "detail": "表页：" + str(scenario10["table_page"]) + "；bbox：" + str(scenario10["table_bbox"]) +
                                  "；副本解析：" + json.dumps(scenario10["copy_parse_summary"])})
    assertion_a.append({"name": "⑪表标题与表区已删、页面仍有其他文本（absence 可裁决）、A2 表实例消失",
                        "passed": bool(scenario11["assert_title_absent"] and scenario11["assert_page_keeps_other_text"]
                                       and scenario11["assert_table_instance_removed"]),
                        "detail": "表页：" + str(scenario11["table_page"]) + "；副本解析：" + json.dumps(scenario11["copy_parse_summary"])})
    verification_a = {"items": assertion_a, "scenario6_equals_scenario2": equality_by_mode}

    def fixed_entry(run: Dict[str, Any], identifier: str) -> Dict[str, Any]:
        return next(item for item in run["checks"]["trial_fixed_sentences"] if item["id"] == identifier)

    def anchor_entry(run: Dict[str, Any], identifier: str) -> Dict[str, Any]:
        return next(item for item in run["checks"]["anchors"] if item["anchor_id"] == identifier)

    def both_modes(scenario_id: str) -> List[Dict[str, Any]]:
        return [runs[key] for key in runs if key.endswith(":" + scenario_id)]

    # ---- 断言 T（目录三例，review F2） ----
    toc_assertions = []
    t7 = []
    for run in both_modes("⑦S1目录保留删正文"):
        a3 = anchor_entry(run, "A3")
        t7.append(a3["verdict"] == "命中" and bool(a3["issues"]) and not a3["heading_pages"]
                  and all(issue.get("severity") == "warn" for issue in a3["issues"]))
    toc_assertions.append({"name": "T1 目录保留+仅删正文第四部分 → A3=命中（warn，规划 CMM-029），两种模式均命中",
                           "passed": bool(t7) and all(t7),
                           "detail": "判定数：" + str(len(t7)) + "；目录区页：" + str(scenario7["toc_pages_kept"])})
    t4 = []
    for run in both_modes("④S1目录与正文都删第四部分"):
        a3 = anchor_entry(run, "A3")
        t4.append(a3["verdict"] == "命中" and bool(a3["issues"]) and not a3["heading_pages"])
    toc_assertions.append({"name": "T2 目录与正文都删（场景④）→ A3=命中，两种模式均命中",
                           "passed": bool(t4) and all(t4), "detail": "判定数：" + str(len(t4))})
    t1 = []
    for run in both_modes("①S1正常部门"):
        a3 = anchor_entry(run, "A3")
        a1 = anchor_entry(run, "A1")
        t1.append(a3["verdict"] == "正常" and a3["heading_pages"] == [28]
                  and a1["verdict"] == "正常" and a1["heading_pages"] == [24])
    toc_assertions.append({"name": "T3 正文折行标题正例保持：①A3/A1 命中正文页（28/24）而非目录续页（3）",
                           "passed": bool(t1) and all(t1),
                           "detail": "A3 正文命中页：" + str(anchor_entry(runs[MODES[0]["id"] + ":①S1正常部门"], "A3")["heading_pages"]) +
                                     "；A1：" + str(anchor_entry(runs[MODES[0]["id"] + ":①S1正常部门"], "A1")["heading_pages"])})
    verification_toc = {"items": toc_assertions, "probe_rows": toc_rows}

    # ---- 断言 C（表格三态，A2 + 真实解析产物） ----
    table_assertions = []
    c1 = []
    for run in both_modes("①S1正常部门") + both_modes("②S2正常单位") + both_modes("③S3写法混用"):
        a2 = anchor_entry(run, "A2")
        expect = A2_INSTANCE_EXPECTED_PAGES[run["sample_id"]]
        c1.append(a2["verdict"] == "正常" and len(a2["instances"]) == 1
                  and a2["instances"][0]["page"] == expect)
    table_assertions.append({"name": "C1 正常：①②③ A2 标题与唯一可回溯表实例匹配（实例页 S1/S2=7、S3=5）",
                             "passed": bool(c1) and all(c1),
                             "detail": "实例摘要：" + json.dumps(
                                 anchor_entry(runs[MODES[0]["id"] + ":①S1正常部门"], "A2")["instances"], ensure_ascii=False)})
    c2 = []
    for run in both_modes("⑩S1表区删留标题"):
        a2 = anchor_entry(run, "A2")
        c2.append(a2["verdict"] == "未检查" and a2["pending_reasons"]
                  and any("对应表实例" in reason for reason in a2["pending_reasons"])
                  and run["parse_artifact"].get("attached"))
    table_assertions.append({"name": "C2 未检查：⑩只有标题无合格表实例 → A2=未检查（pending 原因含“对应表实例”），解析产物已挂",
                             "passed": bool(c2) and all(c2),
                             "detail": "示例原因：" + anchor_entry(runs[MODES[0]["id"] + ":⑩S1表区删留标题"], "A2")["pending_reasons"][0]})
    c3 = []
    for run in both_modes("⑪S1删标题与表区"):
        a2 = anchor_entry(run, "A2")
        c3.append(a2["verdict"] == "命中" and bool(a2["issues"]) and not a2["heading_pages"]
                  and all(issue.get("severity") == "warn" for issue in a2["issues"]))
    table_assertions.append({"name": "C3 缺失：⑪可靠覆盖下表标题缺失 → A2=命中（warn，规划 CMM-029）",
                             "passed": bool(c3) and all(c3), "detail": "判定数：" + str(len(c3))})
    verification_table = {"items": table_assertions, "parse_artifact_note": "全部运行挂生产解析器离线产物（PDFParser.build_parse_artifact，纯函数不连库）"}

    # ---- 断言 D（条件成立自然正例与合法造错，覆盖 v1 ⑤） ----
    d_hits = []
    for run in both_modes("⑧S4条件成立自然正例"):
        f1 = fixed_entry(run, "F1")
        f2 = fixed_entry(run, "F2")
        d_hits.append(f1["verdict"] == "正常" and f1.get("condition_state") == "空表成立"
                      and f2["verdict"] == "正常")
    d_modified = []
    for run in both_modes("⑨S4改F1固定语句"):
        f1 = fixed_entry(run, "F1")
        d_modified.append(f1["verdict"] == "命中" and f1.get("candidate_type") == "改动候选"
                          and f1.get("severity_suggestion") == "low" and f1.get("rule_scope") == "CMM-028(规划)")
    d_scenario5 = []
    for run in both_modes("⑤S2改F1固定语句"):
        f1 = fixed_entry(run, "F1")
        d_scenario5.append(f1["verdict"] == "待复核" and f1.get("condition_state") == "条件未知")
    d6_clean = []
    for mode in MODES:
        key2 = mode["id"] + ":②S2正常单位"
        key6 = mode["id"] + ":⑥S2仅改封面字段"
        d6_clean.append(equality_by_mode[mode["id"]] and
                        all(entry["verdict"] != "命中" for entry in runs[key6]["checks"]["trial_fixed_sentences"]) and
                        all(anchor["verdict"] != "命中" for anchor in runs[key6]["checks"]["anchors"]))
    verification_d = {
        "items": [
            {"name": "D1 条件成立自然正例：⑧S4 原文 F1/F2=正常（空表成立），两种模式一致",
             "passed": bool(d_hits) and all(d_hits),
             "detail": "判定数：" + str(len(d_hits)) + "；样本：上海市体育彩票管理中心2025年度决算（2025 桌面母体）"},
            {"name": "D2 合法造错：⑨S4 改动词后 F1=命中（改动候选 low，规划 CMM-028），两种模式一致",
             "passed": bool(d_modified) and all(d_modified), "detail": "判定数：" + str(len(d_modified))},
            {"name": "D3 旧⑤路径（实际支出材料上注入+改写）新判定流记信号混叠待复核，不再冒充改动候选",
             "passed": bool(d_scenario5) and all(d_scenario5), "detail": "判定数：" + str(len(d_scenario5))},
            {"name": "⑥无误报：锚点与固定语句判定与②一致且无新增命中（两种模式）",
             "passed": bool(d6_clean) and all(d6_clean),
             "detail": "模式结果：" + json.dumps(equality_by_mode, ensure_ascii=False)},
        ],
    }

    # ---- 断言 P（四反例旧/新判定对照 + 槽位/混叠/自然样本对照） ----
    expected_old = {
        "a_condition_unknown": ("命中", "缺失候选"),
        "b_explicit_zero_amounts": ("不适用", None),
        "c_positive_amounts": ("不适用", None),
        "d_other_section_quotation": ("正常", None),
    }
    expected_new = {
        "a_condition_unknown": ("未检查", None, None, "条件未知"),
        "b_explicit_zero_amounts": ("命中", "缺失候选", "medium", "零值成立"),
        "c_positive_amounts": ("不适用", None, None, "条件不成立"),
        "e_slot_release_positive": ("正常", None, None, "空表成立"),
        "e2_slot_mismatch": ("命中", "改动候选", "low", "空表成立"),
        "m_mixed_signals_superseded_scenario5": ("待复核", None, None, "条件未知"),
        "f_natural_condition_holds_F1": ("正常", None, None, "空表成立"),
        "f_natural_condition_holds_F2": ("正常", None, None, "空表成立"),
        "f_natural_modified_F1": ("命中", "改动候选", "low", "空表成立"),
    }
    probe_assertions: List[Dict[str, Any]] = []
    rows_by_case = {row["case"]: row for row in probe_rows}
    for case, (verdict, candidate) in expected_old.items():
        row = rows_by_case[case]
        actual = (row["old"]["verdict"], row["old"]["candidate_type"])
        probe_assertions.append({"name": "P-旧 " + case + " 与 review.json 记录一致（" + verdict +
                                         ("/" + candidate if candidate else "") + "）",
                                 "passed": actual == (verdict, candidate),
                                 "detail": "旧判定：" + json.dumps(row["old"], ensure_ascii=False)})
    for case, expected in expected_new.items():
        row = rows_by_case[case]
        actual = (row["new"]["verdict"], row["new"]["candidate_type"],
                  row["new"]["severity_suggestion"], row["new"]["condition_state"])
        probe_assertions.append({"name": "P-新 " + case + " 符合修复预期（" + "/".join(
                                     str(item) for item in expected if item is not None) + "）",
                                 "passed": actual == expected,
                                 "detail": "新判定：" + json.dumps(row["new"], ensure_ascii=False)})
    d_row = rows_by_case["d_other_section_quotation"]
    probe_assertions.append({"name": "P-新 d_other_section_quotation 非正常（引用不算正文证据）",
                             "passed": d_row["new"]["verdict"] in ("未检查", "待复核"),
                             "detail": "新判定：" + json.dumps(d_row["new"], ensure_ascii=False)})
    toc_old_kept = toc_rows[0]
    probe_assertions.append({"name": "P-目录 旧版把目录续页条目当正文命中、新版排除（保留目录+删正文 → 新版无正文命中）",
                             "passed": toc_old_kept["old_heading_pages"] == [3] and toc_old_kept["new_heading_pages"] == [],
                             "detail": "old=" + str(toc_old_kept["old_heading_pages"]) + " new=" + str(toc_old_kept["new_heading_pages"])})
    toc_body = toc_rows[1]
    probe_assertions.append({"name": "P-目录 正文折行标题正例：新版仍命中正文页（目录页不再混入）",
                             "passed": toc_body["new_heading_pages"] == [5],
                             "detail": "old=" + str(toc_body["old_heading_pages"]) + " new=" + str(toc_body["new_heading_pages"])})
    verification_probes = {"items": probe_assertions, "rows": probe_rows}

    # ---- 断言 R（审核反例 6 例：r2 败绩复现 + r3 全部达标，tpl_trial_r2_review_20261031） ----
    review_assertions: List[Dict[str, Any]] = []
    for row in review_rows:
        new = row["new"]
        review_assertions.append({
            "name": "R-新 " + row["case"] + " → " + row["expected_new"]["verdict"] +
                    ("（" + row["expected_new"]["candidate_type"] + "）" if row["expected_new"]["candidate_type"] else "") +
                    "，取数口径达标",
            "passed": (new["verdict"] == row["expected_new"]["verdict"]
                       and new["candidate_type"] == row["expected_new"]["candidate_type"]),
            "detail": "r3 判定：" + json.dumps(new, ensure_ascii=False)})
        review_assertions.append({
            "name": "R-旧 " + row["case"] + " 与审核 actual 一致（" + row["r2_actual"]["verdict"] +
                    ("，" + row["r2_actual"]["candidate_type"] if row["r2_actual"]["candidate_type"] else "") + "）",
            "passed": (row["old"]["verdict"] == row["r2_actual"]["verdict"]
                       and row["old"]["candidate_type"] == row["r2_actual"]["candidate_type"]),
            "detail": "r2(15afb6a4) 判定：" + json.dumps(row["old"], ensure_ascii=False)})
    verification_review = {
        "items": review_assertions,
        "rows": review_rows,
        "review_evidence": "outputs/tpl_trial_r2_review_20261031/（probe_conditions.py / condition_results.json / review.json）",
    }

    report = {
        "schema": SCHEMA,
        "trial_note": TRIAL_NOTE,
        "fix_notes": {
            "F1": "template_fixed_sentence_checks 判定流重写：先定位所属章节锚（section_anchor，目录区排除+折行容忍），"
                  "章节内条件判定（空表成立/零值成立/条件不成立/条件未知，0.00 不当作有实际支出，未知绝不报缺失），"
                  "句比对限定章节范围并按已审核槽位实例化（机构名取 profile.organization_name 且 source 非 filename）。",
            "F2": "template_anchor_checks._headings 最小修复：toc_region_pages 从“目录”表头页向后传播目录区（连续条目形态页），"
                  "目录区内命中不计为正文证据；点线引导/页码尾巴条目行另在行级排除；正文折行标题正例不回退。",
            "table_trial": "A2 表格锚点改挂生产解析器离线产物（PDFParser.build_parse_artifact），展示 正常/未检查/缺失 三态。",
            "condition_scope_r3": "条件取数口径修订（tpl_trial_r2_review_20261031 F1）：只认当年决算口径指标金额"
                                  "（F1=本年度“三公”经费财政拨款支出决算合计；F2=本年度国有资本经营预算财政拨款收入、"
                                  "支出两项），预算口径/增减额/非目标年份分句（。；切分）一律排除；指标齐且全为零才判"
                                  "零值成立，任一指标取不到 → 条件未知，绝不报缺失。审核反例 6 例纳入 R 组断言。",
        },
        "chain_status": {"anchors": CHAIN_STATUS_ANCHOR, "trial_fixed_sentences": CHAIN_STATUS_FIXED},
        "execution_status_legend": "每个条目带 execution_status（实际执行/未检查）与 chain_status（尚未接入主链）；"
                                   "固定语句模块与试运行锚点均为“尚未接入主链”：发布模板或打开 GBC_TEMPLATE_SCOPE 线上开关也"
                                   "不会自动接入固定语句模块，试运行候选内容未经发布绑定也不会被生产消费者拿到。",
        "template_export": {
            "zip_path": published_rel("trial_export_2025_final.zip"), "zip_sha256": trial["zip_sha256"],
            "content_sha256": trial["content_sha256"], "config_content_sha256": trial["config_content_sha256"],
            "algorithm": trial["algorithm"], "note": trial["note"],
            "capability_scope": trial["capability_scope"],
            "adoption_kind": "issued",
            "anchors": trial["anchors"], "trial_fixed_sentences": trial["trial_fixed_sentences"],
        },
        "samples": {key: {"path": str(spec["resolved"]), "sha256": spec["sha256"]}
                    for key, spec in sorted(SAMPLES.items())},
        "modified_copies": {
            "scenario4": {**scenario4, "path": published_rel(COPY_NAMES["scenario4"])},
            "scenario5": {**scenario5, "path": published_rel(COPY_NAMES["scenario5"])},
            "scenario6": {**scenario6, "path": published_rel(COPY_NAMES["scenario6"])},
            "scenario7": {**scenario7, "path": published_rel(COPY_NAMES["scenario7"])},
            "scenario9": {**scenario9, "path": published_rel(COPY_NAMES["scenario9"])},
            "scenario10": {**scenario10, "path": published_rel(COPY_NAMES["scenario10"])},
            "scenario11": {**scenario11, "path": published_rel(COPY_NAMES["scenario11"])},
        },
        "probes": {"rows": probe_rows, "toc_rows": toc_rows, "review_rows": review_rows},
        "runs": runs,
        "verification": {
            "A": verification_a,
            "B": {"run_count": len(runs), "verdict_matrix": verdict_matrix(runs)},
            "T": verification_toc,
            "C": verification_table,
            "D": verification_d,
            "P": verification_probes,
            "R": verification_review,
        },
        "production_boundaries": [
            "固定语句模块（template_fixed_sentence_checks）只由本 harness 调用：不接 api/、不进正式规则注册表；发布模板或打开 "
            "GBC_TEMPLATE_SCOPE 线上开关也只会启用已有生产消费者，不会自动接入该模块。",
            "r3 条件取数口径只认当年决算口径指标金额：预算口径、增减额与非目标年份分句一律排除；指标不完整时一律条件未知"
            "（未检查/待复核），绝不报缺失；未认定标签金额如实计数但不参与裁决（章节文本模式的保守边界）。",
            "试运行导出包与候选内容未发布、未绑定年度，生产消费者拿不到 A1–A3/F1–F2；本脚本的 GBC_TEMPLATE_SCOPE/GBC_TEMPLATE_EXPORT "
            "只是本进程离线运行环境变量，不是生产配置变更。",
            "目录区排除按“目录表头页向后传播的连续条目形态页”识别；紧邻目录续页、整页只有一个孤立编号标题的正文页会被并入目录区，"
            "本语料不存在该形态（T3 在真实样本上验证传播边界），边界已如实记录。",
            "场景⑤（实际支出材料上注入+改写）在新判定流下记“信号混叠/待复核”：标准空表表述与非零金额同现时条件无法裁决；"
            "改动候选的合法造错正例改由场景⑨（条件成立材料 S4 上的受控改写）承担。",
            "固定语句缺失候选与改动候选判据存在重叠范围（同一未命中情形），全部按试运行判据标注并转人工复核。",
            "A2 表格锚点的三态验证依赖生产解析器离线产物；造错副本（⑩⑪）用受控 redaction（含框线移除）构造，"
            "并以解析器复查副本确认表实例消失，不手拼假表实例。",
        ],
    }

    old_vs_new_path = safe_output_path(staging, "old_vs_new.json")
    old_vs_new_path.write_text(json.dumps(
        {"schema": "gbc.trial-old-vs-new.v2",
         "reviewed_commit": "c633affe",
         "note": "旧模块经 git show 导出到 /tmp 后 importlib 加载（未修改仓库）；"
                 "c633affe 四反例 a–d 旧版失败、新版通过（probe_rows）；"
                 "15afb6a4（r2）审核反例 6 例旧版按混合口径误判、r3 取数口径全部达标（review_rows），"
                 "r2 败绩与 outputs/tpl_trial_r2_review_20261031/condition_results.json 一致",
         "legacy_sources": {"fixed": "git show c633affe:src/services/template_fixed_sentence_checks.py",
                            "anchor": "git show c633affe:src/services/template_anchor_checks.py",
                            "fixed_r2": "git show 15afb6a4:src/services/template_fixed_sentence_checks.py"},
         "review_evidence": {"dir": "outputs/tpl_trial_r2_review_20261031",
                             "files": ["probe_conditions.py", "condition_results.json", "review.json"]},
         "probe_rows": probe_rows, "toc_rows": toc_rows, "review_rows": review_rows},
        ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")

    report_path = safe_output_path(staging, "trial_report.json")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    md_path = safe_output_path(staging, "trial_report.md")
    render_report_md(report, md_path)

    csv_path = safe_output_path(staging, "template_content_candidates.csv")
    md_candidates = safe_output_path(staging, "template_content_candidates.md")
    write_candidates_csv(csv_path, anchors, fixed, sources)
    write_candidates_md(md_candidates, anchors, fixed, sources)

    meta = {
        "algorithm": trial["algorithm"], "zip_sha256": trial["zip_sha256"],
        "content_sha256": trial["content_sha256"], "config_content_sha256": trial["config_content_sha256"],
        "package_id": PACKAGE_ID, "adoption_kind": "issued",
        "capability_scope": trial["capability_scope"], "note": TRIAL_NOTE,
        "anchors": trial["anchors"], "trial_fixed_sentences": trial["trial_fixed_sentences"],
        "db_writes": 0, "business_jobs_touched": 0,
        "publish_discipline": "staging 自校验（断言全过+完整性复算+冻结根哈希复算）后 os.replace 原子发布；输出目录已存在非空则拒绝",
        "env": {"GBC_TEMPLATE_SCOPE": "1（仅本进程离线运行，非生产配置变更）", "GBC_TEMPLATE_EXPORT": "已设置（staging 导出包）",
                "GBC_TEMPLATE_EXPORT_SHA256": "已设置", "DATABASE_URL": "空（离线分支）"},
    }
    meta_path = safe_output_path(staging, "offline_export_meta.json")
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    # ---- 冻结根复算：v1/r2/phase1_prep 打包前后哈希必须不变（漂移即拒绝发布） ----
    frozen_after = [frozen_root_snapshot(root) for root in FROZEN_ROOTS]
    frozen_errors = []
    for before, after in zip(frozen_before, frozen_after):
        if before != after:
            frozen_errors.append("冻结根哈希漂移：" + before["path"] +
                                 "（基线 " + before["rolling_sha256"][:16] + " → 现值 " +
                                 after["rolling_sha256"][:16] + "）")
    if frozen_errors:
        print("[publish-abort] " + "; ".join(frozen_errors), file=sys.stderr)
        return 1

    integrity_files = {
        "scripts/phase1_prep/run_template_trial.py": file_sha256(Path(__file__).resolve()),
        "src/services/template_fixed_sentence_checks.py": file_sha256(REPO_ROOT / "src/services/template_fixed_sentence_checks.py"),
        "src/services/template_anchor_checks.py": file_sha256(REPO_ROOT / "src/services/template_anchor_checks.py"),
        published_rel("trial_export_2025_final.zip"): trial["zip_sha256"],
        published_rel("offline_export_meta.json"): file_sha256(meta_path),
        published_rel("template_content_candidates.csv"): file_sha256(csv_path),
        published_rel("template_content_candidates.md"): file_sha256(md_candidates),
        published_rel("trial_report.json"): file_sha256(report_path),
        published_rel("trial_report.md"): file_sha256(md_path),
        published_rel("old_vs_new.json"): file_sha256(old_vs_new_path),
    }
    for name, build in (("scenario4", scenario4), ("scenario5", scenario5), ("scenario6", scenario6),
                        ("scenario7", scenario7), ("scenario9", scenario9), ("scenario10", scenario10),
                        ("scenario11", scenario11)):
        integrity_files[published_rel(COPY_NAMES[name])] = build["sha256"]
    integrity = {
        "schema": "gbc.trial-integrity.v3",
        "note": "脚本与产物 sha256 清单（逐文件 digest.update；integrity.json 自身不在清单内）；"
                "文件键为发布约定相对路径，与实际落盘目录解耦",
        "repo_root": str(REPO_ROOT),
        "published_dir": "outputs/" + R3_DIR_NAME,
        "files": integrity_files,
        "frozen_references": {
            "v1_integrity": {"path": "outputs/tpl_trial_20261031/integrity.json",
                             "sha256": file_sha256(REPO_ROOT / "outputs/tpl_trial_20261031/integrity.json")},
            "r2_integrity": {"path": "outputs/tpl_trial_20261031_r2/integrity.json",
                             "sha256": file_sha256(REPO_ROOT / "outputs/tpl_trial_20261031_r2/integrity.json")},
            "r2_review": {"path": "outputs/tpl_trial_r2_review_20261031/review.json",
                          "sha256": file_sha256(REPO_ROOT / "outputs/tpl_trial_r2_review_20261031/review.json")},
            "r2_review_condition_results": {"path": "outputs/tpl_trial_r2_review_20261031/condition_results.json",
                                            "sha256": file_sha256(REPO_ROOT / "outputs/tpl_trial_r2_review_20261031/condition_results.json")},
            "v3_integrity": {"path": "outputs/phase1_prep/v3/integrity.json",
                             "sha256": file_sha256(REPO_ROOT / "outputs/phase1_prep/v3/integrity.json")},
            "v3_core_sample_118": {"path": "outputs/phase1_prep/v3/desktop_core_sample.csv",
                                   "sha256": file_sha256(REPO_ROOT / "outputs/phase1_prep/v3/desktop_core_sample.csv")},
            "review_evidence": {"path": "outputs/tpl_trial_review_20261031/review.json",
                                "sha256": file_sha256(REPO_ROOT / "outputs/tpl_trial_review_20261031/review.json")},
        },
        "frozen_roots": {
            "roots": frozen_after,
            "unchanged_verified": True,
            "note": "v1/r2/phase1_prep（含 v3 core 118 清单）打包前后逐文件滚动摘要一致；"
                    "漂移时本脚本在写 integrity.json 之前即以退出码 1 拒绝发布",
        },
    }
    integrity_path = safe_output_path(staging, "integrity.json")
    integrity_path.write_text(json.dumps(integrity, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    # ---- staging 自校验：断言全过（含 R 审核反例组）+ 完整性复算，通过后才原子发布 ----
    failed = [item["name"] for group in (assertion_a, toc_assertions, table_assertions,
                                         verification_d["items"], probe_assertions,
                                         verification_review["items"])
              for item in group if not item["passed"]]
    verify_errors: List[str] = list(failed)
    product_prefix = "outputs/" + R3_DIR_NAME + "/"
    for rel, expected_sha in integrity_files.items():
        # 产物键 → staging 内复算；仓库文件键（脚本/服务模块）→ 仓库内复算。
        source = staging / rel.rsplit("/", 1)[-1] if rel.startswith(product_prefix) else REPO_ROOT / rel
        actual = file_sha256(source)
        if actual != expected_sha:
            verify_errors.append("完整性复算不一致：" + rel)
    if verify_errors:
        print("[publish-abort] staging 自校验失败，保留 staging 供检查：", json.dumps(verify_errors, ensure_ascii=False),
              file=sys.stderr)
        return 1
    if output_dir.exists():
        output_dir.rmdir()  # 仅当为空（前面已拒绝非空目录）
    os.replace(staging, output_dir)
    print("[published] " + str(output_dir))
    print("[done] runs=" + str(len(runs)) + " failed_assertions=0 integrity_files=" + str(len(integrity_files)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
