#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Golden Corpus 评测脚本：用人工标注计算 TP/FP/FN 与质量指标。

输入：
- corpus/<DOC-ID>/golden.json   人工标注（defect / rounding_hint /
  manual_review / acceptable 四类，含 rule_id、页码、expected_severity）
- outputs/golden_replay/<DOC-ID>-<mode>-<ts>.json  重放产物
  （scripts/replay_golden_corpus.py 生成）

指标（供 P0/P1 门禁使用）：
- TP：标注缺陷（defect）有对应 finding 命中（规则匹配 + 页码匹配）；
- FP：正式 finding 中未命中任何 defect 标注的条目；
- FN：defect 标注未被任何 finding 命中；
- 严重度准确率：TP 中 severity 与 expected_severity 一致的比例；
- 页码准确率：TP 中页码一致的比例；
- 证据可定位率：正式 finding 中证据含页码的比例（门禁要求 100%）；
- acceptable 违规：acceptable 负例页上不得出现任何 finding（如 T7/P28）。

只写 `outputs/`，绝不修改 corpus/ 与历史任务目录。

用法:
    python scripts/evaluate_golden_corpus.py --doc DOC-20260905-001 \
        --replay outputs/golden_replay/DOC-20260905-001-legacy-xxx.json
    # shadow replay（同时含 legacy/structured）必须显式 --mode 指定验收对象：
    python scripts/evaluate_golden_corpus.py --doc DOC-20260905-001 \
        --replay outputs/golden_replay/DOC-20260905-001-shadow-xxx.json --mode structured
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# Windows 控制台默认 GBK：print 含 CJK 特殊字符（如"…·×"）的文件名/
# 规则文本时触发 UnicodeEncodeError 并以退出码 1 结束（GPT5.6 R2 P2-5a，
# 历史回放在报告打印阶段崩溃）。统一 reconfigure 为 UTF-8，且把
# unencodable 字符降级为 replacement 而不是让脚本崩溃。
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # 非 TextIO（如 pytest 捕获流）：跳过
            pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CORPUS_DIR = ROOT / "corpus"
OUTPUT_DIR = ROOT / "outputs" / "golden_eval"

# structured 结果只有在真正消费 parsed_tables 的规则覆盖率达到该阈值
# 后才允许作为独立交付路径通过 Golden Gate。这个阈值与
# scripts/replay_golden_corpus.py 保持一致；缺失/不一致一律 fail-closed。
STRUCTURED_READY_MIN_COVERAGE = 0.9

# severity 归一：finding 的 severity 词表 → 标注词表
_SEVERITY_ALIASES = {
    "high": "high",
    "error": "high",
    "critical": "high",
    "medium": "medium",
    "warn": "medium",
    "warning": "medium",
    "info": "info",
    "hint": "info",
    "low": "info",
    "manual_review": "manual_review",
}


def _norm_severity(value: Any) -> str:
    return _SEVERITY_ALIASES.get(str(value or "").strip().lower(), str(value or "").lower())


def _page_of(finding: Dict[str, Any]) -> Optional[int]:
    page = finding.get("page")
    if page is None:
        location = finding.get("location")
        if isinstance(location, dict):
            page = location.get("page")
    try:
        return int(page) if page is not None else None
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# 内容重叠校验（GPT5.6 P1-4 + review 🟡1 修正）
#
# 此前匹配只看规则+页码，"规则和页码正确但正文完全无关、evidence 为空"
# 的 finding 也会被判为命中。此后 TP 判定还需 evidence 内容重叠：
# 标注 evidence 与 finding 的 evidence_text/message 至少共享一个
# 归一化数字 token，或一个足够长的归一化文本片段。片段窗口随标注长度
# 退化（min(6, len)）——短于 6 字的标注按全串比对，避免"空表说明"这类
# 短证据永远匹配不上的盲区（review 🟡1）。
# ---------------------------------------------------------------------------


def _normalize_for_overlap(text: Any) -> str:
    """去掉空白与标点，只保留字母数字与 CJK 字符，统一小写。"""
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", str(text or "").lower())


# 低区分度数字 token：年份（19xx/20xx）与孤立两位数几乎出现在任何一段
# 决算文字里。交集里**只有**这类 token 时不构成内容证据（GPT5.6 R2
# P1-2 假 TP：同规则同页、内容相反、仅共享 "2025"）；但年份出现在
# 双方**文本片段**中也叠加佐证，不单独作为通过判据。


def _numeric_tokens(text: Any) -> set:
    """提取数字 token（金额/编码/带小数的值），不做区分度过滤。"""
    raw = str(text or "").replace(",", "")
    return {t for t in re.findall(r"\d+(?:\.\d+)?", raw) if len(t) >= 2}


def _distinctive_numeric_tokens(text: Any) -> set:
    """有区分度的数字 token：金额（含小数）、编码（≥3 位整数）。

    年份（19xx/20xx）与孤立两位数被排除——它们单凭自身不构成证据。
    """
    return {
        t
        for t in _numeric_tokens(text)
        if ("." in t or len(t) >= 3) and not (re.fullmatch(r"(?:19|20)\d{2}", t) and "." not in t)
    }


def evidence_overlaps(annotation_evidence: Any, finding: Dict[str, Any]) -> bool:
    """标注证据与 finding 证据是否内容重叠（数字或长文本片段）。"""
    ann_norm = _normalize_for_overlap(annotation_evidence)
    if not ann_norm:
        # 标注本身没写 evidence：退回规则+页码匹配（不惩罚标注侧的缺失）
        return True
    finding_text = " ".join(str(finding.get(key) or "") for key in ("evidence_text", "message"))
    find_norm = _normalize_for_overlap(finding_text)
    if not find_norm:
        return False
    # 1) 有区分度的数字 token 交集（金额/编码是最直接的共同证据）；
    #    年份/页码位不单独构成证据（R2 假 TP 场景）
    ann_nums = _distinctive_numeric_tokens(annotation_evidence)
    find_nums = _distinctive_numeric_tokens(finding_text)
    if ann_nums and ann_nums & find_nums:
        return True
    # 2) 归一化片段重叠：窗口取 min(6, 标注长度)，短标注按全串比对。
    #    4-5 字窗口作为措辞差异的放宽通道（标注人工措辞与 finding
    #    文案不必完全一致，如「逻辑矛盾」vs「增减变化与持平表述」，
    #    但会共享「公务接待」这类 4 字关键片段）。
    for window in (min(6, len(ann_norm)), 4):
        if window < 4:
            break
        for i in range(0, len(ann_norm) - window + 1):
            if ann_norm[i : i + window] in find_norm:
                return True
    # 3) 超短标注（2-3 字）兜底：全串包含比对（「空表」⊂「缺少空表」）
    if len(ann_norm) < 4 and ann_norm in find_norm:
        return True
    return False


def _anchor_phrases(anchor: str) -> List[str]:
    """把锚文本切成短语级锚点（GPT5.6 R2→R4 收敛设计）。

    location_key 是人工写的定位提示：定位域前缀（toc/sec/tbl/xtbl）
    是分类标记不是锚文本，先剥掉；括号序号（「三公说明(一)公务
    接待费」）、省略号（「第三部分…202 年度部门决算情况说明」）标记
    着措辞的边界。按括号/省略号/顿号切出短语，任一**完整短语**出现
    在 finding 证据中即锚点成立——短语级既有区分度（不放大到任意
    3 字子串），又容忍人工措辞与规则文案的差异（整段匹配过严）。

    R4 修复：此前未剥前缀，`tbl:支出决算表合计行` 归一化成
    `tbl支出决算表合计行` 整段——表锚永远零命中（样张 A-004 实测）。
    """
    anchor = str(anchor or "").strip()
    # 剥定位域前缀（toc:/sec:/tbl:/xtbl:）——分类标记不是锚文本
    prefix, sep, rest = anchor.partition(":")
    if sep and prefix in {"toc", "sec", "tbl", "xtbl"}:
        anchor = rest
    parts = re.split(r"[（）()\[…·、/]|…", anchor)
    phrases: List[str] = []
    for part in parts:
        norm = _normalize_for_overlap(part)
        if len(norm) >= 2:  # ≥2 字（含纯数字短语如 "202"）
            phrases.append(norm)
    generic = {"第三部分", "第一部分", "第二部分", "情况说明", "决算情况"}
    return [p for p in phrases if p not in generic]


def _annotation_anchor_phrases(annotation: Dict[str, Any]) -> List[str]:
    """标注的全部锚点短语：location_key 切分短语 + 对齐短语补充。

    R4 P1-3 配套：零锚点命中即拒配后，历史标注中与规则文案措辞交叉
    的锚点（R2 实测 A-003/005/006/008 四条真命中零命中）需要在标注
    侧补充与规则文案对齐的短语（``anchor_phrases_aligned``，修订记录
    见 ANNOTATIONS.md）——评估器不再单方面迁就措辞差异。

    R5 P1-C：对齐短语必须驻留在 finding 的 evidence_text（原文引文）
    中才有效——命中搜索已 evidence-only，短语取规则实际引文的片段。
    """
    phrases = _anchor_phrases(str(annotation.get("location_key") or ""))
    for extra in annotation.get("anchor_phrases_aligned") or []:
        norm = _normalize_for_overlap(extra)
        if len(norm) >= 2 and norm not in phrases:
            phrases.append(norm)
    return phrases


def _anchor_hit_count(phrases: List[str], finding: Dict[str, Any]) -> int:
    """锚点短语命中数（排序/约束共用的计数器）。

    只按**完整短语**命中计数（不滑窗）。搜索范围**仅限 evidence_text**
    （R5 P1-C：规则生成的 message 是文案不是定位证据——V33-245 的
    message 模板固定含「三公说明」，把它算进命中等于规则文案自证
    章节，任何该规则 finding 都自动过锚点，防线形同虚设）。
    """
    finding_norm = _normalize_for_overlap(str(finding.get("evidence_text") or ""))
    return sum(1 for phrase in phrases if phrase in finding_norm)


def match_annotation(
    annotation: Dict[str, Any],
    findings: List[Dict[str, Any]],
    consumed: set,
) -> Optional[Dict[str, Any]]:
    """标注 ↔ finding 匹配（GPT5.6 R4 P1-3 终版语义）：

    候选门槛 = 规则一致（或标注无规则）+ 页码一致 + evidence 内容重叠
    （区分度数字 token 交集，或 4-6 字/超短全串的文本片段重叠——
    年份/孤立两位数不单独构成证据）。

    **锚点约束（零命中拒配）**：标注带 location_key（或补充锚点短语）
    且可提取有效短语时，只有命中锚点的候选允许成为 TP；零命中即拒配
    返回 None（计入 FN/待复核）——「唯一候选来自其他章节」不再晋升
    为 TP。标注无锚点时无定位约束，纯证据排序。措辞交叉的历史锚点由
    标注侧补充 anchor_phrases_aligned 对齐（见 ANNOTATIONS.md）。

    每条 finding 只能被一条标注消费（consumed 去重）。
    优先返回综合得分最高的 finding：区分度数字交集 > 锚点短语命中
    > 共享文本片段长度，使"最贴近原始数字与定位域的证据"优先被消费。
    """
    # R6 P1-4 真值冻结：allowed_rule_ids 列该真值可由哪些规则命中
    # （V33-202 原始标注 + V33-120 当前实现近似路径均可）；旧字段
    # rule_id 兼容
    allowed_rules = {
        str(r).strip().upper() for r in (annotation.get("allowed_rule_ids") or []) if str(r).strip()
    }
    legacy_rule = str(annotation.get("rule_id") or "").strip().upper()
    if legacy_rule and legacy_rule not in allowed_rules:
        allowed_rules.add(legacy_rule)
    page = annotation.get("page")
    annotation_evidence = annotation.get("evidence")
    anchor_phrases = _annotation_anchor_phrases(annotation)
    candidates: List[Tuple[int, Dict[str, Any]]] = []
    for finding in findings:
        if id(finding) in consumed:
            continue
        if page is not None and _page_of(finding) != page:
            continue
        if allowed_rules:
            finding_rule = str(finding.get("rule") or "").strip().upper()
            if finding_rule not in allowed_rules:
                continue
        if not evidence_overlaps(annotation_evidence, finding):
            continue
        # 综合得分：区分度数字交集 > 锚点命中数 > 共享文本片段长度
        finding_text = " ".join(str(finding.get(key) or "") for key in ("evidence_text", "message"))
        shared_nums = len(
            _distinctive_numeric_tokens(annotation_evidence)
            & _distinctive_numeric_tokens(finding_text)
        )
        anchor_hits = _anchor_hit_count(anchor_phrases, finding)
        ann_norm = _normalize_for_overlap(annotation_evidence)
        find_norm = _normalize_for_overlap(finding_text)
        shared_text = 0
        for size in range(min(len(ann_norm), len(find_norm)), 5, -1):
            if ann_norm[:size] in find_norm or any(
                ann_norm[i : i + size] in find_norm for i in range(0, len(ann_norm) - size + 1)
            ):
                shared_text = size
                break
        score = shared_nums * 10000 + anchor_hits * 100 + shared_text
        candidates.append((score, finding, anchor_hits))
    if not candidates:
        return None
    # 锚点约束（GPT5.6 R4 P1-3 + R5 P1-C 终版语义）：
    # - 标注可提取有效锚点短语（location_key 切分 + 对齐补充）→ 只有
    #   命中锚点的候选允许成为 TP；零命中即**拒配**（FN/待复核），
    #   "唯一候选来自其他章节"不得晋升 TP；
    # - sec/toc 域额外要求**章节标记短语**在 evidence 中命中（R5 P1-C：
    #   行内主题词如「公务接待费」不构成章节证明——其他章节提到同一
    #   主题时 evidence 也会含它）；标注可用 section_phrases_aligned
    #   补充章节标记（规则 evidence 引文起点常是章节标题行）。
    # - 标注无锚点短语 → 无定位约束，纯证据排序。
    if anchor_phrases:
        anchor_hit_candidates = [c for c in candidates if c[2] > 0]
        if not anchor_hit_candidates:
            return None  # 零锚点命中：unmatched，不晋升 TP
        candidates = anchor_hit_candidates
    # 独立章节校验（R7 P1-3 → R8 P1 收紧）：sec 锚标注的 finding 必须
    # 携带非空 section_id（结构化章节标识），且用**全短语**章节锚匹配。
    # R8 关掉两个 fail-open 通道：① 此前无 section_id 的候选退回锚点
    # 语义即可晋升 TP（缺章节标识的 finding 不能证明产自目标章节）；
    # ② 此前允许锚短语的 2 字前缀（「九、公务管理情况说明」仅共享
    # 「公务」两字即可命中三公真值）。章节锚 = 标注声明的
    # section_title_phrases_aligned（golden 已补章节标题短语，与
    # section_phrases_aligned 的 evidence 驻留词职责分离），未声明时
    # 用 location_key 锚短语——整短语包含判定，不做前缀宽松。
    if str(annotation.get("location_key") or "").partition(":")[0] == "sec":
        # R9 P1：任何 sec 真值都强制非空 section_id——此前该要求挂在
        # `if section_anchors:` 内，location_key="sec:情况说明" 这类短语
        # 被通用词过滤后无有效锚，缺失 section_id 的 finding 仍可晋升
        # TP（实测绕过）。章节标识是非空硬前提，与锚无关。
        candidates = [c for c in candidates if str(c[1].get("section_id") or "").strip()]
        if not candidates:
            return None  # sec 真值缺结构化章节标识：不得晋升 TP
        declared_section = [
            _normalize_for_overlap(m)
            for m in annotation.get("section_title_phrases_aligned") or []
            if _normalize_for_overlap(m)
        ]
        section_anchors = declared_section or list(anchor_phrases)
        if section_anchors:
            section_candidates = []
            for c in candidates:
                section_norm = _normalize_for_overlap(str(c[1].get("section_id") or ""))
                if any(anchor in section_norm for anchor in section_anchors):
                    section_candidates.append(c)
            if not section_candidates:
                return None  # 章节域不符的候选不得晋升 TP
            candidates = section_candidates
    # 章节标记约束（R5 P1-C）：sec/toc 锚的章节标记短语。
    # - 标注**显式声明** section_phrases_aligned（章节词的 evidence 驻留
    #   形态）→ 硬约束：零命中拒配（跨章节候选不晋升 TP）；
    # - 未声明 → 章节标记退位为纯排序加分：规则层已对 V33-245/246 做
    #   章节 scope 限定（finding 必然产自该章节），此时 evidence 无
    #   章节词不构成异常；硬拒配只会误伤（样张 A-002 实测——V33-245
    #   的 evidence 模板是矛盾分句拼接，天然不含章节词）。
    declared_marks = [
        m for m in annotation.get("section_phrases_aligned") or [] if _normalize_for_overlap(m)
    ]
    if declared_marks:
        normalized_marks = [_normalize_for_overlap(m) for m in declared_marks]
        finding_norm_of = {
            id(c[1]): _normalize_for_overlap(str(c[1].get("evidence_text") or ""))
            for c in candidates
        }
        section_hit = [
            c for c in candidates if any(m in finding_norm_of[id(c[1])] for m in normalized_marks)
        ]
        if not section_hit:
            return None  # 声明的章节词零命中：跨章节候选不晋升 TP
        candidates = section_hit
    candidates.sort(key=lambda triple: triple[0], reverse=True)
    return candidates[0][1]


def _truth_group(annotation: Dict[str, Any]) -> str:
    """真值分组：证据面后缀归一——T4a/T4b 是同一真值 T4 的两个证据面。

    R6 P1-4 冻结语义（ANNOTATIONS.md）：同一真值的多个证据面**任一
    命中即该真值命中**。分组取 truth_id 去掉尾部小写后缀（T4a→T4、
    T5b→T5）；无后缀的 truth_id（T1…）组即自身；缺 truth_id 时退回
    annotation_id。
    """
    truth_id = str(annotation.get("truth_id") or annotation.get("annotation_id") or "")
    return re.sub(r"[a-z]+$", "", truth_id) or truth_id


def _consume_truth_annotations(
    annotations: List[Dict[str, Any]],
    findings: List[Dict[str, Any]],
    consumed: set,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], int]:
    """按真值组聚类消费标注（defect/hint 共用，review 🟡2）。

    匹配阶段：每个证据面**独立尝试**匹配自己的 finding（同一真值的两
    个证据面在样张上各有对应 finding——T5a=310 行明细差、T5b=公用经费
    显示和差，跳过第二面会把它对应的 finding 变成 FP）。
    计数阶段：missed 按真值组去重——组内**任一**面命中即该真值命中；
    其余面未命中不构成缺口（review 验收口径：单条 finding 命中另一面
    不再报 miss）；整组全部面都未命中才计 **1** 个 missed（明细带全部
    面，召回缺口按真值计，不按标注面放大）。
    返回 (matched 明细, missed 明细[组去重], 命中组数)。
    """
    matched: List[Dict[str, Any]] = []
    missed: List[Dict[str, Any]] = []
    hit_groups: set = set()
    pending: Dict[str, List[Dict[str, Any]]] = {}
    for annotation in annotations:
        group = _truth_group(annotation)
        finding = match_annotation(annotation, findings, consumed)
        if finding is None:
            pending.setdefault(group, []).append(annotation)
            continue
        hit_groups.add(group)
        consumed.add(id(finding))
        matched.append(
            {
                "annotation_id": annotation.get("annotation_id"),
                "truth_id": annotation.get("truth_id"),
                "truth_group": group,
                "rule_id": annotation.get("rule_id"),
                "allowed_rule_ids": sorted(annotation.get("allowed_rule_ids") or []),
                "matched_rule": finding.get("rule"),
                "expected_page": annotation.get("page"),
                "expected_severity": annotation.get("expected_severity"),
                "actual_severity": _norm_severity(finding.get("severity")),
                "severity_ok": _norm_severity(finding.get("severity"))
                == _norm_severity(annotation.get("expected_severity")),
                "page_ok": _page_of(finding) == annotation.get("page"),
            }
        )
    for group, faces in pending.items():
        if group in hit_groups:
            continue
        missed.append(
            {
                "truth_group": group,
                "annotation_id": faces[0].get("annotation_id"),
                "annotation_ids": [f.get("annotation_id") for f in faces],
                "rule_id": faces[0].get("rule_id"),
                "page": faces[0].get("page"),
                "evidence": faces[0].get("evidence"),
                "faces": faces,
            }
        )
    return matched, missed, len(hit_groups)


def _group_accuracy(matched: List[Dict[str, Any]], field: str) -> Optional[float]:
    """按命中真值组计算准确率，避免证据面重复放大分子。

    同一真值组的多个 evidence faces 仍保留在 matched 中供审计，但一个组
    对指标最多贡献一次。若一个组的多个已匹配面在该指标上有任一错误，
    该组按错误计，避免正确面掩盖同组的错误面。
    """
    if not matched:
        return None
    statuses_by_group: Dict[str, List[bool]] = {}
    for item in matched:
        group = str(item.get("truth_group") or "")
        statuses_by_group.setdefault(group, []).append(bool(item.get(field)))
    correct_groups = sum(1 for statuses in statuses_by_group.values() if all(statuses))
    return round(correct_groups / len(statuses_by_group), 4)


def evaluate(doc_id: str, replay_path: Path, mode: str = "auto") -> Dict[str, Any]:
    golden_path = CORPUS_DIR / doc_id / "golden.json"
    golden = json.loads(golden_path.read_text(encoding="utf-8"))
    replay = json.loads(replay_path.read_text(encoding="utf-8"))

    # R8 P1：replay 必须与 golden 同源——doc_id + 源 PDF SHA 双重绑定。
    # 此前只校验内容不校验身份，篡改 replay 的 doc_id/SHA 后仍
    # GATE-PASS（实测 DOC-WRONG / sha256=deadbeef）。任何一步不符
    # 都明确拒绝，不允许评测错源产物（fail-closed）。
    replay_doc_id = str(replay.get("doc_id") or "").strip()
    if replay_doc_id != doc_id:
        raise ValueError(
            f"replay doc_id={replay_doc_id or '(空)'} 与评测目标 {doc_id} 不符——"
            "拒绝评测错源产物（fail-closed，R8 P1）"
        )
    golden_sha = str(golden.get("sha256") or "").strip()
    replay_sha = str(replay.get("sha256") or "").strip()
    if not golden_sha or not replay_sha or replay_sha != golden_sha:
        raise ValueError(
            f"replay SHA({replay_sha or '(空)'}) 与 golden SHA({golden_sha or '(空)'}) "
            "不一致——replay 不是该 golden 的产物，拒绝评测（fail-closed，R8 P1）"
        )

    # R8 P0：解析模式显式化。shadow replay 同时含 legacy/structured，
    # 此前无条件取 legacy，structured 路径失败被静默掩盖（实测同一样张
    # structured 4 findings / TP=0 / FN=3，shadow 仍 GATE-PASS）。
    # auto 只用于单结果 replay；双结果必须显式 --mode 指定验收对象。
    legacy_run = replay.get("legacy") if isinstance(replay.get("legacy"), dict) else None
    structured_run = (
        replay.get("structured") if isinstance(replay.get("structured"), dict) else None
    )
    runs_present = [
        name
        for name, run in (("legacy", legacy_run), ("structured", structured_run))
        if run is not None
    ]
    selected_mode = mode
    if mode == "auto":
        if legacy_run is not None and structured_run is not None:
            raise ValueError(
                "shadow replay 同时含 legacy/structured 两套结果，必须显式 "
                "--mode legacy|structured 指定验收对象（auto 拒绝猜测，"
                "避免 structured 路径失败被 legacy 掩盖，R8 P0）"
            )
        if legacy_run is not None:
            selected_mode = "legacy"
            run = legacy_run
        elif structured_run is not None:
            selected_mode = "structured"
            run = structured_run
        else:
            run = {}
    elif mode == "legacy":
        if legacy_run is None:
            raise ValueError(
                f"replay 无 legacy 结果（现有: {runs_present}），无法按 --mode legacy 验收"
            )
        run = legacy_run
        selected_mode = "legacy"
    elif mode == "structured":
        if structured_run is None:
            raise ValueError(
                f"replay 无 structured 结果（现有: {runs_present}），无法按 --mode structured 验收"
            )
        run = structured_run
        selected_mode = "structured"
    else:
        # 直接调用方传错 mode（绕过 argparse choices）时必须明确失败，
        # 不得静默落入 structured 分支（/review 加固）
        raise ValueError(f"unknown mode: {mode!r}，允许 auto/legacy/structured")
    findings: List[Dict[str, Any]] = run.get("findings") or []

    labels = golden.get("labels", [])
    # R9 P1：sec 标注必须在 golden 加载阶段声明章节标题短语——缺章节
    # 锚的标注无法做结构化章节校验，fail-closed 拒绝，不等到匹配阶段
    # 静默退化（location_key="sec:情况说明" 这类无有效锚的标注实测可
    # 绕过章节保护）。
    for annotation in labels:
        if str(annotation.get("location_key") or "").partition(":")[0] != "sec":
            continue
        titles = [
            _normalize_for_overlap(m)
            for m in annotation.get("section_title_phrases_aligned") or []
            if _normalize_for_overlap(m)
        ]
        if not titles:
            raise ValueError(
                f"sec 标注 {annotation.get('annotation_id')} 缺 "
                "section_title_phrases_aligned——章节锚缺失无法做结构化"
                "章节校验（fail-closed，R9 P1）"
            )
    defects = [lb for lb in labels if lb.get("label") == "defect"]
    hints = [lb for lb in labels if lb.get("label") in ("rounding_hint", "manual_review")]
    acceptable = [lb for lb in labels if lb.get("label") == "acceptable"]

    matched: List[Dict[str, Any]] = []
    missed: List[Dict[str, Any]] = []
    hint_matched: List[Dict[str, Any]] = []
    hint_missed: List[Dict[str, Any]] = []
    matched_finding_ids: set = set()

    # R6 P1-4 + review 🟡2：truth_id 聚类验收（defect/hint 两侧一致）。
    # 同一真值的多个证据面（T4a/T4b → 组 T4）任一命中即该真值命中，
    # 组内其余面不再要求独立命中（也不计入 missed）；整组全部面都
    # 未命中才计 1 个 missed——召回缺口按真值计，不按标注面放大。
    matched, missed, _ = _consume_truth_annotations(defects, findings, matched_finding_ids)
    hint_matched, hint_missed, hint_groups_hit = _consume_truth_annotations(
        hints, findings, matched_finding_ids
    )

    # R9 P0：defect 验收按**真值组**计——此前 tp = len(matched) 按证据
    # 面计，T1a/T1b/T1c 三个面归一同一 T1 时报告 TP=3/FN=0、门禁假绿
    # （实测可绕过「T1/T5/T6 三组」锁定）。tp 改计命中组数；证据面
    # 命中数另存 defect_faces_matched；命中组明细进 defect_groups_hit
    # 供门禁直接校验恰好命中 T1/T5/T6。
    tp = len({item["truth_group"] for item in matched})
    fn = len(missed)
    defect_groups_total = len({_truth_group(a) for a in defects})
    defect_groups_hit = sorted({item["truth_group"] for item in matched})
    defect_faces_matched = len(matched)
    # FP：未被任何标注（缺陷或预期提示）消费的 finding——提示级预期输出
    # 不是误报（HANDOFF §2 三档真值模型）
    fp = sum(1 for finding in findings if id(finding) not in matched_finding_ids)

    severity_accuracy = _group_accuracy(matched, "severity_ok")
    page_accuracy = _group_accuracy(matched, "page_ok")

    # 证据可定位率（GPT5.6 R2 P1-2 强化）：正式 finding 需要**双证据**——
    # 页码 + 非空 evidence_text/message（此前只查页码，"有页码无证据文本"
    # 也算可定位）。缺任一即不可定位，进入未达标明细。
    # 证据可定位率（GPT5.6 R2 P1-2 + R3 P1-5 两级强化）：
    # - R2：页码 + 文本（此前仅页码）；
    # - R3：文本必须是非空 **evidence_text**（规则侧的原文引文，如
    #   表名/金额/「总计」标签行）。规则生成的 message 是文案不是
    #   证据——"有页码 + 通用 message、evidence_text 空"的 finding
    #   不允许算作可定位。缺项进入 unlocatable_findings 明细。
    def _has_text_evidence(f: Dict[str, Any]) -> bool:
        return bool(str(f.get("evidence_text") or "").strip())

    locatable = [f for f in findings if _page_of(f) is not None and _has_text_evidence(f)]
    unlocatable = [f for f in findings if f not in locatable]
    locatable_rate = round(len(locatable) / len(findings), 4) if findings else None

    # acceptable 负例违规：负例页上不得出现任何 finding
    acceptable_violations = []
    for annotation in acceptable:
        page = annotation.get("page")
        hits = [f for f in findings if page is not None and _page_of(f) == page]
        if hits:
            acceptable_violations.append(
                {
                    "annotation_id": annotation.get("annotation_id"),
                    "page": page,
                    "finding_count": len(hits),
                    "rules": sorted({str(f.get("rule")) for f in hits}),
                }
            )

    precision = round(tp / (tp + fp), 4) if (tp + fp) else None
    recall = round(tp / (tp + fn), 4) if (tp + fn) else None

    return {
        "doc_id": doc_id,
        # 报告记录实际被评测的结果模式。auto 选择单一 structured 结果
        # 时不能继续写成 auto，否则 structured readiness 门禁会被绕过。
        "mode": selected_mode,
        "runs_present": runs_present,
        "replay": str(replay_path.relative_to(ROOT))
        if replay_path.is_relative_to(ROOT)
        else str(replay_path),
        "evaluated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "finding_total": len(findings),
        "rule_counts": run.get("rule_counts", {}),
        "structured_ready": (
            run.get("structured_ready") if selected_mode == "structured" else None
        ),
        "structured_coverage": (
            run.get("structured_coverage") if selected_mode == "structured" else None
        ),
        "structured_consumer_count": (
            run.get("parsing_consumer_count") if selected_mode == "structured" else None
        ),
        "structured_final_rule_total": (
            run.get("final_rule_total") if selected_mode == "structured" else None
        ),
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "defect_groups_total": defect_groups_total,
        "defect_groups_hit": defect_groups_hit,
        "defect_faces_matched": defect_faces_matched,
        "precision": precision,
        "recall": recall,
        "hint_total": len(hints),
        "hint_matched": len(hint_matched),
        "hint_groups_total": len({_truth_group(a) for a in hints}),
        "hint_groups_hit": hint_groups_hit,
        "hint_groups_hit_ids": sorted({item["truth_group"] for item in hint_matched}),
        "hint_missed_count": len(hint_missed),
        "severity_accuracy": severity_accuracy,
        "page_accuracy": page_accuracy,
        "locatable_evidence_rate": locatable_rate,
        "unlocatable_findings": [
            {
                "rule": f.get("rule"),
                "page": f.get("page"),
                "missing": ("page" if _page_of(f) is None else "evidence_text"),
            }
            for f in unlocatable
        ],
        "acceptable_violations": acceptable_violations,
        "matched": matched,
        "hint_hits": hint_matched,
        "missed": [
            {
                "truth_group": item["truth_group"],
                "annotation_id": item["annotation_id"],
                "annotation_ids": item["annotation_ids"],
                "rule_id": item["rule_id"],
                "page": item["page"],
                "evidence": item["evidence"],
            }
            for item in missed
        ],
        "hint_missed": [
            {
                "truth_group": item["truth_group"],
                "annotation_id": item["annotation_id"],
                "annotation_ids": item["annotation_ids"],
                "rule_id": item["rule_id"],
                "page": item["page"],
                "evidence": item["evidence"],
            }
            for item in hint_missed
        ],
        "false_positive_details": [
            {
                "rule": str(f.get("rule")),
                "page": _page_of(f),
                "message": str(f.get("message"))[:120],
            }
            for f in findings
            if id(f) not in matched_finding_ids
        ],
    }


def check_gates(report: Dict[str, Any]) -> List[str]:
    """门禁检查（整改验收口径，非 Golden Corpus 20 份正式门禁）。"""
    failures: List[str] = []
    if report["fp"] != 0:
        failures.append(f"FP={report['fp']}，要求 0（样张 52 条误报必须清零）")
    if report["fn"] != 0:
        failures.append(f"FN={report['fn']}，硬问题召回要求 3/3")
    # R7 P0-1 → R9 P0：硬问题门禁直接校验**命中真值组恰好是 T1/T5/T6**。
    # 此前只查 tp==3（tp 按证据面计）——T1a/T1b/T1c 三个面归一同一 T1
    # 时报告 TP=3/FN=0、门禁假绿（实测可绕过「三组」锁定）；只查
    # fn==0 又挡不住缩减真值集。现在：组数必须等于 3 且命中组集必须
    # 恰为 {T1, T5, T6}（HANDOFF §2 权威编号）。
    if report["defect_groups_total"] != 3 or set(report["defect_groups_hit"]) != {"T1", "T5", "T6"}:
        failures.append(
            f"硬问题真值组 {report['defect_groups_hit']}/{report['defect_groups_total']}，"
            "要求恰好命中 HANDOFF §2 T1/T5/T6 三组（真值集不得缩减，"
            "也不得按证据面虚增）"
        )
    # R7 P0-1：舍入提示进入硬门禁。验收标准不仅是 3/3，还必须是
    # HANDOFF §2 指定的 T2/T3/T4。只检查数量会让其它三个真值组伪装成
    # 全部命中；只检查 hit==total 又挡不住把真值集缩减成 2/2。
    hint_hit = report.get("hint_groups_hit")
    hint_hit_ids = report.get("hint_groups_hit_ids")
    hint_hit_set = set(hint_hit_ids) if isinstance(hint_hit_ids, list) else None
    expected_hint_groups = {"T2", "T3", "T4"}
    if (
        report.get("hint_groups_total") != 3
        or hint_hit != 3
        or hint_hit_set != expected_hint_groups
    ):
        failures.append(
            f"舍入门禁：hint 真值命中 {hint_hit!r}/{report.get('hint_groups_total')!r}，"
            "要求恰好命中 HANDOFF §2 的 T2/T3/T4 三组（truth_id 集合必须对齐，"
            "且不得用旧版数量字段代替组身份）"
        )
    if report["hint_missed_count"] != 0:
        failures.append(f"舍入门禁：hint_missed={report['hint_missed_count']}，要求 0")
    if report["precision"] is not None and report["precision"] < 0.95:
        failures.append(f"精确率 {report['precision']} < 0.95")
    if report["recall"] is not None and report["recall"] < 0.98:
        failures.append(f"召回率 {report['recall']} < 0.98")
    for metric_name, metric_label in (
        ("severity_accuracy", "严重度准确率"),
        ("page_accuracy", "页码准确率"),
    ):
        metric = report.get(metric_name)
        if not isinstance(metric, (int, float)) or isinstance(metric, bool) or metric != 1.0:
            failures.append(f"{metric_label}({metric_name}) {metric!r}，要求 1.0")
    if report.get("mode") == "structured":
        coverage = report.get("structured_coverage")
        consumer_count = report.get("structured_consumer_count")
        total_count = report.get("structured_final_rule_total")
        coverage_valid = (
            isinstance(coverage, (int, float))
            and not isinstance(coverage, bool)
            and math.isfinite(float(coverage))
            and 0.0 <= float(coverage) <= 1.0
        )
        count_valid = (
            isinstance(consumer_count, int)
            and not isinstance(consumer_count, bool)
            and isinstance(total_count, int)
            and not isinstance(total_count, bool)
            and 0 <= consumer_count <= total_count
            and total_count > 0
            and round(consumer_count / total_count, 4) == coverage
        )
        if not coverage_valid or not count_valid:
            failures.append(
                "structured 覆盖率/消费者计数缺失或不一致，禁止作为独立路径通过"
            )
        elif float(coverage) < STRUCTURED_READY_MIN_COVERAGE:
            failures.append(
                f"structured parsed_tables 消费覆盖率 {coverage} < "
                f"{STRUCTURED_READY_MIN_COVERAGE}，禁止切换生产"
            )
        if report.get("structured_ready") is not True:
            failures.append("structured_ready=false，结构化路径尚未达到可切换条件")
    if report["locatable_evidence_rate"] not in (None, 1.0):
        failures.append(f"证据可定位率 {report['locatable_evidence_rate']} < 1.0")
    if report["acceptable_violations"]:
        failures.append(
            f"acceptable 负例出现 {len(report['acceptable_violations'])} 处违规 finding"
        )
    return failures


def _write_eval_report_atomic(path: Path, report: Dict[str, Any]) -> None:
    """先完整写临时文件，再原子发布评测报告。

    文件名已经带 mode、时间和 UUID，调用方还会用 ``open('x')`` 语义
    避免同名覆盖；这里再把写入与发布分开，防止进程在 JSON 尚未写完时
    被中断而留下一个看似存在、实际不可解析的最终报告。临时文件使用
    ``x`` 创建，并在异常路径清理。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="") as report_file:
            json.dump(report, report_file, ensure_ascii=False, indent=2)
            report_file.write("\n")
            report_file.flush()
            os.fsync(report_file.fileno())
        # 在 Windows/NTFS 和 POSIX 上，硬链接创建都是原子的且不会覆盖
        # 已存在目标；它同时避免了“先 exists 再 replace”的 TOCTOU 窗口。
        # 临时文件与目标位于同一目录，满足跨卷限制。
        os.link(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--doc", required=True, help="语料 DOC-ID")
    parser.add_argument("--replay", required=True, help="重放产物 JSON 路径")
    parser.add_argument(
        "--mode",
        default="auto",
        choices=["auto", "legacy", "structured"],
        help="验收的解析模式（R8 P0）：shadow replay 同时含 legacy/structured "
        "时必须显式指定；auto 仅用于单结果 replay，遇双结果拒绝猜测",
    )
    args = parser.parse_args()

    replay_path = Path(args.replay)
    if not replay_path.is_absolute():
        replay_path = ROOT / replay_path
    if not replay_path.exists():
        print(f"replay file not found: {replay_path}", file=sys.stderr)
        return 1

    try:
        report = evaluate(args.doc, replay_path, mode=args.mode)
    except ValueError as exc:
        # R8 P0/P1：身份绑定与模式歧义是评测前提错误，不是指标失败——
        # 明确报错并失败退出，不落 GATE 报告（避免把错源产物写成 PASS）
        print(f"EVAL-REJECTED: {exc}", file=sys.stderr)
        return 2
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    report_mode = str(report.get("mode") or args.mode)
    out_path: Optional[Path] = None
    for attempt in range(100):
        collision_suffix = f"-{attempt}" if attempt else ""
        candidate = OUTPUT_DIR / (
            f"{args.doc}-eval-{report_mode}-{stamp}-{uuid.uuid4().hex}{collision_suffix}.json"
        )
        try:
            _write_eval_report_atomic(candidate, report)
        except FileExistsError:
            # 目标可能由同秒并发评测或固定 UUID 的测试先占用；换后缀，
            # 绝不覆盖已有证据。
            continue
        out_path = candidate
        break
    if out_path is None:
        raise RuntimeError("无法为评测报告分配唯一文件名（已重试 100 次）")
    try:
        display_path = out_path.relative_to(ROOT)
    except ValueError:
        display_path = out_path

    print(
        f"TP={report['tp']} FP={report['fp']} FN={report['fn']} "
        f"precision={report['precision']} recall={report['recall']}"
    )
    print(
        f"hint真值命中 {report['hint_groups_hit']}/{report['hint_groups_total']} "
        f"（证据面 {report['hint_matched']}/{report['hint_total']}）"
        f" severity_accuracy={report['severity_accuracy']} "
        f"page_accuracy={report['page_accuracy']} "
        f"locatable_evidence_rate={report['locatable_evidence_rate']}"
    )
    if report["hint_missed"]:
        print(f"hint_missed: {report['hint_missed']}")
    if report["acceptable_violations"]:
        print(f"acceptable violations: {report['acceptable_violations']}")
    if report["missed"]:
        print(f"missed: {report['missed']}")
    gate_failures = check_gates(report)
    if gate_failures:
        for failure in gate_failures:
            print(f"GATE-FAIL: {failure}")
        print(f"report -> {display_path}")
        return 2
    print("GATE-PASS")
    print(f"report -> {display_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
