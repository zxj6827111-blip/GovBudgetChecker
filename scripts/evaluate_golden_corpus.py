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
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
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
        if ("." in t or len(t) >= 3)
        and not (re.fullmatch(r"(?:19|20)\d{2}", t) and "." not in t)
    }


def evidence_overlaps(annotation_evidence: Any, finding: Dict[str, Any]) -> bool:
    """标注证据与 finding 证据是否内容重叠（数字或长文本片段）。"""
    ann_norm = _normalize_for_overlap(annotation_evidence)
    if not ann_norm:
        # 标注本身没写 evidence：退回规则+页码匹配（不惩罚标注侧的缺失）
        return True
    finding_text = " ".join(
        str(finding.get(key) or "") for key in ("evidence_text", "message")
    )
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
    finding_norm = _normalize_for_overlap(
        str(finding.get("evidence_text") or "")
    )
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
        str(r).strip().upper()
        for r in (annotation.get("allowed_rule_ids") or [])
        if str(r).strip()
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
        finding_text = " ".join(
            str(finding.get(key) or "") for key in ("evidence_text", "message")
        )
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
                ann_norm[i : i + size] in find_norm
                for i in range(0, len(ann_norm) - size + 1)
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
    # 独立章节校验（R7 P1-3）：sec 锚标注 + finding 携带 section_id
    # （结构化字段，非 evidence 文字）时，章节域必须同源——section_id
    # 归一文本要包含锚短语或其 2 字前缀（章节标题措辞与标注短语允许
    # 差异：「三公说明」vs「财政拨款"三公"经费支出决算情况说明」共享
    # 「三公」）。「其他重要事项说明 + 公务接待费」的跨章节候选被拒，
    # 此前只能靠 evidence 文字、主题词混过即可晋升 TP。
    # finding 无 section_id（旧产物/未迁移规则）：退回锚点语义，不惩罚。
    if (
        str(annotation.get("location_key") or "").partition(":")[0] == "sec"
        and anchor_phrases
    ):
        section_candidates = []
        for c in candidates:
            section_id = str(c[1].get("section_id") or "").strip()
            if not section_id:
                section_candidates.append(c)
                continue
            section_norm = _normalize_for_overlap(section_id)
            if any(
                p in section_norm or (len(p) >= 2 and p[:2] in section_norm)
                for p in anchor_phrases
            ):
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
        m
        for m in annotation.get("section_phrases_aligned") or []
        if _normalize_for_overlap(m)
    ]
    if declared_marks:
        normalized_marks = [_normalize_for_overlap(m) for m in declared_marks]
        finding_norm_of = {
            id(c[1]): _normalize_for_overlap(str(c[1].get("evidence_text") or ""))
            for c in candidates
        }
        section_hit = [
            c
            for c in candidates
            if any(m in finding_norm_of[id(c[1])] for m in normalized_marks)
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


def evaluate(doc_id: str, replay_path: Path) -> Dict[str, Any]:
    golden_path = CORPUS_DIR / doc_id / "golden.json"
    golden = json.loads(golden_path.read_text(encoding="utf-8"))
    replay = json.loads(replay_path.read_text(encoding="utf-8"))

    run = replay.get("legacy") or replay.get("structured") or {}
    findings: List[Dict[str, Any]] = run.get("findings") or []

    labels = golden.get("labels", [])
    defects = [lb for lb in labels if lb.get("label") == "defect"]
    hints = [
        lb for lb in labels
        if lb.get("label") in ("rounding_hint", "manual_review")
    ]
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
    matched, missed, _ = _consume_truth_annotations(
        defects, findings, matched_finding_ids
    )
    hint_matched, hint_missed, hint_groups_hit = _consume_truth_annotations(
        hints, findings, matched_finding_ids
    )

    tp = len(matched)
    fn = len(missed)
    # FP：未被任何标注（缺陷或预期提示）消费的 finding——提示级预期输出
    # 不是误报（HANDOFF §2 三档真值模型）
    fp = sum(1 for finding in findings if id(finding) not in matched_finding_ids)

    severity_ok = [item for item in matched if item["severity_ok"]]
    page_ok = [item for item in matched if item["page_ok"]]

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

    locatable = [
        f for f in findings if _page_of(f) is not None and _has_text_evidence(f)
    ]
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
        "replay": str(replay_path.relative_to(ROOT)) if replay_path.is_relative_to(ROOT) else str(replay_path),
        "evaluated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "finding_total": len(findings),
        "rule_counts": run.get("rule_counts", {}),
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": precision,
        "recall": recall,
        "hint_total": len(hints),
        "hint_matched": len(hint_matched),
        "hint_groups_total": len({_truth_group(a) for a in hints}),
        "hint_groups_hit": hint_groups_hit,
        "hint_missed_count": len(hint_missed),
        "severity_accuracy": round(len(severity_ok) / tp, 4) if tp else None,
        "page_accuracy": round(len(page_ok) / tp, 4) if tp else None,
        "locatable_evidence_rate": locatable_rate,
        "unlocatable_findings": [
            {
                "rule": f.get("rule"),
                "page": f.get("page"),
                "missing": (
                    "page"
                    if _page_of(f) is None
                    else "evidence_text"
                ),
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
    # R7 P0-1：硬问题必须 3/3 全命中——只查 fn==0 挡不住"缩减真值集"
    # 的假绿（删掉一条 defect 标注后 fn 仍为 0、tp 降为 2 也能过）。
    # 验收标准（HANDOFF §7）是 T1/T5/T6 三条硬问题全命中，tp 锁定为 3。
    if report["tp"] != 3:
        failures.append(
            f"TP={report['tp']}，硬问题召回要求 3/3（HANDOFF §2 T1/T5/T6，"
            "真值集不得缩减）"
        )
    # R7 P0-1：舍入提示进入硬门禁。验收标准是 T2/T3/T4 三组全命中；
    # 同时锁定 hint_groups_total==3——只查"hit==total"挡不住把真值组
    # 从 3 缩到 2 的假绿（历史实测 hint 2/2 假通过）。两侧都必须等于 3。
    if report["hint_groups_total"] != 3 or report["hint_groups_hit"] != 3:
        failures.append(
            f"舍入门禁：hint 真值命中 {report['hint_groups_hit']}/{report['hint_groups_total']}，"
            "要求 3/3（HANDOFF §2 T2/T3/T4 三组，truth_id 编号须对齐"
            "HANDOFF 权威口径且真值集不得缩减）"
        )
    if report["hint_missed_count"] != 0:
        failures.append(
            f"舍入门禁：hint_missed={report['hint_missed_count']}，要求 0"
        )
    if report["precision"] is not None and report["precision"] < 0.95:
        failures.append(f"精确率 {report['precision']} < 0.95")
    if report["recall"] is not None and report["recall"] < 0.98:
        failures.append(f"召回率 {report['recall']} < 0.98")
    if report["locatable_evidence_rate"] not in (None, 1.0):
        failures.append(f"证据可定位率 {report['locatable_evidence_rate']} < 1.0")
    if report["acceptable_violations"]:
        failures.append(
            f"acceptable 负例出现 {len(report['acceptable_violations'])} 处违规 finding"
        )
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--doc", required=True, help="语料 DOC-ID")
    parser.add_argument("--replay", required=True, help="重放产物 JSON 路径")
    args = parser.parse_args()

    replay_path = Path(args.replay)
    if not replay_path.is_absolute():
        replay_path = ROOT / replay_path
    if not replay_path.exists():
        print(f"replay file not found: {replay_path}", file=sys.stderr)
        return 1

    report = evaluate(args.doc, replay_path)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out_path = OUTPUT_DIR / f"{args.doc}-eval-{stamp}.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"TP={report['tp']} FP={report['fp']} FN={report['fn']} "
          f"precision={report['precision']} recall={report['recall']}")
    print(f"hint真值命中 {report['hint_groups_hit']}/{report['hint_groups_total']} "
          f"（证据面 {report['hint_matched']}/{report['hint_total']}）"
          f" severity_accuracy={report['severity_accuracy']} "
          f"page_accuracy={report['page_accuracy']} "
          f"locatable_evidence_rate={report['locatable_evidence_rate']}")
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
        print(f"report -> {out_path.relative_to(ROOT)}")
        return 2
    print("GATE-PASS")
    print(f"report -> {out_path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
