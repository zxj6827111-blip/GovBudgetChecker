#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Golden Corpus 重放脚本：用当前解析器和规则真正重跑受保护语料 PDF。

与历史回放 `scripts/replay_analysis.py` 的分工：
- replay_analysis.py 只统计**旧产物**（历史任务的结果 JSON），做结构性指标校验；
- 本脚本用**当前代码**真实执行解析 + 规则（可选 AI），输出逐规则数量，
  用于整改后的样张验收与历史抽检对比。

铁律：只写 `outputs/`，绝不修改 `corpus/` 与历史任务目录（uploads/）。

用法:
    python scripts/replay_golden_corpus.py                      # 全部语料
    python scripts/replay_golden_corpus.py --doc DOC-20260905-001
    python scripts/replay_golden_corpus.py --parse-mode shadow  # legacy+structured 对比

解析三态开关（与生产一致）：
    legacy     现有规则路径（run_rules_with_outcomes）
    structured 结构化解析输入路径（阶段3 迁移规则，未迁移时与 legacy 等价并在
               结果里标注 structured_ready=false）
    shadow     两者都跑并输出逐规则差异对比
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import unicodedata
import uuid
from collections import Counter
from datetime import datetime, timezone
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
OUTPUT_DIR = ROOT / "outputs" / "golden_replay"

# structured 解析视为"可切换（ready）"的最低规则覆盖率（GPT5.6 P0-3）：
# 低于该值时 structured 只能作为 shadow 对比的中间态，不得当作可独立
# 交付的解析路径。当前 9/52 ≈ 17%，远未达到。
STRUCTURED_READY_MIN_COVERAGE = 0.9


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fresh_output_path(prefix: str) -> Path:
    """Build a mode-bearing, millisecond/UUID-qualified output path."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")[:-3]
    return OUTPUT_DIR / f"{prefix}-{stamp}-{uuid.uuid4().hex}.json"


def _write_json_exclusive(path: Path, payload: Dict[str, Any]) -> Path:
    """Write a complete replay artifact without overwriting existing evidence."""
    candidate = path
    for _ in range(3):
        temporary = candidate.with_name(
            f".{candidate.name}.{uuid.uuid4().hex}.tmp"
        )
        try:
            with temporary.open("x", encoding="utf-8", newline="\n") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            # Hard-link publication is atomic and fails when the destination
            # already exists, closing the TOCTOU window of exists()+replace().
            os.link(temporary, candidate)
            return candidate
        except FileExistsError:
            candidate = candidate.with_name(
                f"{candidate.stem}-{uuid.uuid4().hex}{candidate.suffix}"
            )
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
    raise FileExistsError(f"无法排他创建回放产物：{path}")


def _write_checkpoint_atomic(path: Path, payload: Dict[str, Any]) -> None:
    """先完整写临时文件再替换，避免并发续跑留下半个 JSON。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def load_page_texts(pdf_path: Path) -> List[str]:
    """与生产管线一致的页面文本抽取（api/main.py 同源函数）。"""
    import pdfplumber

    from src.services.pdf_page_extract import extract_visible_text_from_page

    texts: List[str] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for page in pdf.pages:
            texts.append(extract_visible_text_from_page(page))
    return texts


def load_page_tables(pdf_path: Path) -> List[List[Any]]:
    import pdfplumber

    from src.services.pdf_page_extract import extract_tables_from_page

    tables: List[List[Any]] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for page in pdf.pages:
            tables.append(extract_tables_from_page(page))
    return tables


def _serialize_finding(issue: Any) -> Dict[str, Any]:
    """finding 统一序列化：带 message 全文与 evidence_text（GPT5.6 P1-4）。

    此前只落 rule/severity/message[:200]/page，评估器只能按规则+页码匹配，
    "规则和页码正确但正文完全无关"的 finding 也会被判为命中。带上
    evidence_text（规则侧的原文证据）与完整 message 后，评估器可以
    做内容重叠校验。
    """
    location = getattr(issue, "location", {}) or {}
    return {
        "rule": str(getattr(issue, "rule", "")),
        "severity": str(getattr(issue, "severity", "")),
        "message": str(getattr(issue, "message", "")),
        "page": location.get("page"),
        "evidence_text": str(getattr(issue, "evidence_text", "") or ""),
        # R7 P1-3：finding 的独立章节身份——评估器可结构化校验
        # sec 锚标注的候选是否真产自该章节（跨章节候选不得晋升 TP）
        "section_id": str(getattr(issue, "section_id", "") or ""),
    }


def run_legacy_rules(page_texts: List[str], page_tables: List[Any], report_kind: str) -> Dict[str, Any]:
    """当前规则路径（与生产 legacy 完全同源）。"""
    from src.engine.pipeline import build_document, run_rules_with_outcomes
    from src.engine.rule_outcome import summarize_rule_outcomes

    doc = build_document(
        path="",
        page_texts=page_texts,
        page_tables=page_tables,
        filesize=0,
    )
    started = time.time()
    issues, outcomes = run_rules_with_outcomes(doc, False, report_kind=report_kind)
    elapsed_ms = int((time.time() - started) * 1000)

    findings = [_serialize_finding(issue) for issue in issues]
    rule_counts = dict(Counter(item["rule"] for item in findings))
    return {
        "mode": "legacy",
        "elapsed_ms": elapsed_ms,
        "finding_total": len(findings),
        "rule_counts": rule_counts,
        "findings": findings,
        "rule_execution_summary": summarize_rule_outcomes(outcomes),
    }


def run_structured_rules(page_texts: List[str], page_tables: List[Any], report_kind: str) -> Dict[str, Any]:
    """结构化解析输入路径。

    阶段3 的迁移规则（V33-115/117/120/202/203/220/241/243/244 首批）通过
    `src.engine.structured_rules` 提供的结构化输入执行；未迁移部分不执行。

    structured_ready 的判定（GPT5.6 P0-3/R3 P0-2 两轮诚实化）：覆盖率
    分子用 **STRUCTURED_PARSING_CONSUMERS**（真正消费 doc.parsed_tables、
    从命名行三态取数的规则）——登记在适配器的 9 条中只有 V33-115 完成
    消费链路，其余 8 条输入仍是 legacy 表征，按登记数报覆盖率是误导。
    """
    from src.engine.pipeline import build_document
    from src.engine.rule_outcome import summarize_rule_outcomes
    from src.engine.rules_v33 import ALL_RULES as FINAL_ALL_RULES
    from src.engine.structured_rules import (
        STRUCTURED_MIGRATED_RULES,
        STRUCTURED_PARSING_CONSUMERS,
        run_structured_rules as _run,
    )

    doc = build_document(path="", page_texts=page_texts, page_tables=page_tables, filesize=0)
    started = time.time()
    issues, outcomes = _run(doc, report_kind=report_kind)
    elapsed_ms = int((time.time() - started) * 1000)
    findings = [_serialize_finding(issue) for issue in issues]
    consumer_count = len(STRUCTURED_PARSING_CONSUMERS)
    total_count = len(FINAL_ALL_RULES)
    coverage = round(consumer_count / total_count, 4) if total_count else 0.0
    return {
        "mode": "structured",
        "structured_ready": coverage >= STRUCTURED_READY_MIN_COVERAGE,
        "structured_coverage": coverage,
        "parsing_consumer_rules": sorted(STRUCTURED_PARSING_CONSUMERS),
        "parsing_consumer_count": consumer_count,
        "adapter_migrated_rules": sorted(STRUCTURED_MIGRATED_RULES),
        "final_rule_total": total_count,
        "ready_note": (
            f"真正消费 parsed_tables 的规则 {consumer_count}/{total_count}"
            f"（覆盖率 {coverage:.1%}）；另有 {len(STRUCTURED_MIGRATED_RULES) - consumer_count} 条"
            "登记在适配器但输入仍为 legacy 表征；"
            f"覆盖率 ≥ {STRUCTURED_READY_MIN_COVERAGE:.0%} 才视为可切换（ready）"
        ),
        "elapsed_ms": elapsed_ms,
        "finding_total": len(findings),
        "rule_counts": dict(Counter(item["rule"] for item in findings)),
        "findings": findings,
        "rule_execution_summary": summarize_rule_outcomes(outcomes),
    }


def infer_report_kind(page_texts: List[str], doc_id: str) -> str:
    from src.engine.pipeline import _resolve_report_kind
    from src.engine.pipeline import build_document as _bd

    doc = _bd(path=f"{doc_id}.pdf", page_texts=page_texts, page_tables=[], filesize=0)
    return _resolve_report_kind(doc)


def normalize_rule_key(rule: str) -> str:
    """规则键归一化：legacy 与 structured 共用同一规则编号。

    规则编号本身形如 V33-001 / CMM-004（尾部数字是编号的一部分，
    不可截断），这里只做去空白与大写归一。
    """
    return unicodedata.normalize("NFKC", str(rule or "")).strip().upper()


def _migration_scope(parse_mode: str) -> Optional[set]:
    """structured 模式历史对比的合法规则域（R7 P1-4）。

    structured 解析路径只执行 STRUCTURED_MIGRATED_RULES（九条迁移集），
    旧结果却是**全量规则集**（V33-235/CMM-004 等未迁移规则也在其中）。
    用「旧全集 ∪ 新九条」计算差异会把未迁移规则的旧计数全部算成
    removed（历史实测 removed=131 假象）——delta/removed 必须限定在
    迁移集内，未迁移规则的旧计数单独报告为 coverage_gap。
    legacy 模式返回 None（不限定——历史回归对比需要全量规则域）。
    """
    if parse_mode != "structured":
        return None
    from src.engine.structured_rules import STRUCTURED_MIGRATED_RULES

    return {normalize_rule_key(r) for r in STRUCTURED_MIGRATED_RULES}


def _restricted_rule_delta(
    old_counts: Dict[str, int],
    new_counts: Dict[str, int],
    scope: Optional[set],
) -> Tuple[Dict[str, Dict[str, int]], Dict[str, int], List[str]]:
    """逐规则差异计算（R7 P1-4）：scope 非空时只对比域内规则。

    返回 (per_rule_delta, coverage_gap, out_of_scope_new)：
    - per_rule_delta：域内（或全部，scope=None）新旧计数不同的规则；
    - coverage_gap：域外规则的历史旧计数——未被 structured 重跑覆盖，
      不是"被消除"，单列报告、不参与 delta/removed；
    - out_of_scope_new（R7 /review）：域外**新**计数——structured 路径
      真实执行过却不属于迁移集登记（适配器与
      STRUCTURED_MIGRATED_RULES 漂移）。必须显式进入 delta 而非静默
      丢弃（静默丢弃会把已执行的规则变化从历史对比中抹掉）。
    """
    rules = set(old_counts) | set(new_counts)
    out_of_scope_new: List[str] = []
    if scope is not None:
        out_of_scope_new = sorted(
            k for k in set(new_counts) if normalize_rule_key(k) not in scope
        )
        rules &= scope
        rules |= set(out_of_scope_new)
    per_rule_delta = {
        rule: {"old": old_counts.get(rule, 0), "new": new_counts.get(rule, 0)}
        for rule in sorted(rules)
        if old_counts.get(rule, 0) != new_counts.get(rule, 0)
    }
    coverage_gap = (
        {
            rule: old_counts[rule]
            for rule in sorted(old_counts)
            if old_counts[rule]
            and normalize_rule_key(rule) not in scope
            # R9 P1：已真实执行的域外规则（适配器漂移）不是覆盖缺口——
            # 此前旧计数同时进 delta 和 coverage_gap（实测 V33-999 old=2
            # 两处重复），与"未被 structured 重跑"的定义冲突
            and normalize_rule_key(rule) not in {
                normalize_rule_key(k) for k in out_of_scope_new
            }
        }
        if scope is not None
        else {}
    )
    return per_rule_delta, coverage_gap, out_of_scope_new


def diff_legacy_structured(legacy: Dict[str, Any], structured: Dict[str, Any]) -> Dict[str, Any]:
    legacy_counts = {normalize_rule_key(k): v for k, v in legacy["rule_counts"].items()}
    structured_counts = {normalize_rule_key(k): v for k, v in structured["rule_counts"].items()}
    all_rules = sorted(set(legacy_counts) | set(structured_counts))
    return {
        "rules_changed": [
            {
                "rule": rule,
                "legacy": legacy_counts.get(rule, 0),
                "structured": structured_counts.get(rule, 0),
                "delta": structured_counts.get(rule, 0) - legacy_counts.get(rule, 0),
            }
            for rule in all_rules
            if legacy_counts.get(rule, 0) != structured_counts.get(rule, 0)
        ],
        "legacy_total": legacy["finding_total"],
        "structured_total": structured["finding_total"],
    }


def _load_corpus_inputs(doc_dir: Path) -> Tuple[str, List[str], List[List[Any]]]:
    """语料输入加载：优先真实 PDF；无 PDF 时按 SHA 回退入库 fixture。

    GPT5.6 R6 P1-5：corpus PDF 不入库（大文件），干净 checkout 上
    replay 直接 FileNotFoundError → replay+evaluate 不可复现（pytest
    可跑，评测链路断）。回退逻辑：用 tests/fixtures 的序列化解析产物，
    校验其 source_pdf_sha256 与 golden.json 声明的样张 SHA 一致后使用。

    review 🟡1（fail-closed 补强）：回退的前提是「fixture 与该 doc 的
    PDF 同源」——此前 golden.json 缺失时整个 SHA 校验被跳过、静默回退
    样张数据（扩语料后一个忘了放 golden.json 的新 doc 会评测错数据）。
    现在：golden.json 缺失 → 拒绝；fixture 内嵌 doc_id 与 doc_dir 不符
    → 拒绝；SHA 任一为空或不一致 → 拒绝。任何一步不满足都明确报错，
    不允许「拿样张数据评测别的 doc」。
    """
    pdf_candidates = sorted(
        (
            candidate
            for candidate in doc_dir.iterdir()
            if candidate.is_file() and candidate.suffix.lower() == ".pdf"
        ),
        key=lambda item: item.name,
    )
    pdf_path: Optional[Path] = None
    if pdf_candidates:
        # R8 P1：有 PDF 也要核对 golden 声明的源 PDF SHA——此前该分支
        # 直接返回，corpus PDF 被替换或与标注版本不一致仍静默回放
        # （配合评估侧 doc_id/SHA 绑定，篡改链路在回放处即被切断）。
        golden_path = doc_dir / "golden.json"
        if not golden_path.exists():
            raise FileNotFoundError(
                f"no golden.json in corpus doc dir: {doc_dir}——"
                "有 PDF 也必须有 golden 作同源证明（fail-closed，R8 P1）"
            )
        golden_sha = str(
            json.loads(golden_path.read_text(encoding="utf-8")).get("sha256") or ""
        ).strip()
        matches = [
            candidate
            for candidate in pdf_candidates
            if sha256_file(candidate) == golden_sha
        ]
        if len(matches) != 1:
            raise RuntimeError(
                f"{doc_dir.name} 的 PDF 候选数为 {len(pdf_candidates)}，"
                f"按 golden SHA 唯一匹配到 {len(matches)} 个；拒绝按文件名猜测"
                "（fail-closed）"
            )
        pdf_path = matches[0]
        pdf_sha = sha256_file(pdf_path)
        if not golden_sha or pdf_sha != golden_sha:
            raise RuntimeError(
                f"{doc_dir.name} 的 PDF SHA({pdf_sha}) 与 golden 声明"
                f"({golden_sha or '(空)'}) 不一致——corpus PDF 被替换或 golden "
                "过期，请用 scripts/build_sample_fixture.py 重新生成"
                "（fail-closed，R8 P1）"
            )
        return (
            str(pdf_path),
            load_page_texts(pdf_path),
            load_page_tables(pdf_path),
        )
    golden_path = doc_dir / "golden.json"
    if not golden_path.exists():
        raise FileNotFoundError(
            f"no pdf in corpus doc dir: {doc_dir}，且无 golden.json——"
            "fixture 回退缺少同源性证明，拒绝静默回退（fail-closed，"
            "review 🟡1）。请放置语料 PDF，或补全 golden.json 后重试。"
        )
    golden = json.loads(golden_path.read_text(encoding="utf-8"))
    fixture_path = ROOT / "tests" / "fixtures" / "sample_page_data.json"
    if not fixture_path.exists():
        raise FileNotFoundError(
            f"no pdf in corpus doc dir: {doc_dir}, and fixture missing: {fixture_path}"
        )
    payload = json.loads(fixture_path.read_text(encoding="utf-8"))
    fixture_doc = str(payload.get("doc_id") or "").strip()
    if fixture_doc != doc_dir.name:
        raise RuntimeError(
            f"fixture doc_id={fixture_doc!r} 与 doc_dir={doc_dir.name!r} 不符——"
            f"fixture 是 DOC-20260905-001 的解析产物，不能用于评测别的 doc；"
            f"请用 scripts/build_sample_fixture.py 为该 doc 生成独立 fixture。"
        )
    expected_sha = str(golden.get("sha256") or "").strip()
    fixture_sha = str(payload.get("source_pdf_sha256") or "").strip()
    if not expected_sha or not fixture_sha or expected_sha != fixture_sha:
        raise RuntimeError(
            f"fixture SHA 与 golden 声明不一致（golden={expected_sha or '(空)'} "
            f"fixture={fixture_sha or '(空)'}）——fixture 过期或 golden 缺 SHA，"
            "请用 scripts/build_sample_fixture.py 重新生成。"
        )
    return (
        "(fixture)",
        list(payload["page_texts"]),
        list(payload["page_tables"]),
    )


def replay_doc(doc_dir: Path, parse_mode: str) -> Dict[str, Any]:
    pdf_name, page_texts, page_tables = _load_corpus_inputs(doc_dir)
    report_kind = infer_report_kind(page_texts, doc_dir.name)

    result: Dict[str, Any] = {
        "doc_id": doc_dir.name,
        "pdf": pdf_name,
        "sha256": (
            sha256_file(Path(pdf_name))
            if pdf_name != "(fixture)"
            else json.loads(
                (ROOT / "tests" / "fixtures" / "sample_page_data.json").read_text(
                    encoding="utf-8"
                )
            )["source_pdf_sha256"]
        ),
        "pages": len(page_texts),
        "report_kind": report_kind,
        "replay_ts": time.time(),
        "parse_mode": parse_mode,
        "input_source": "fixture" if pdf_name == "(fixture)" else "pdf",
    }

    if parse_mode == "legacy":
        result["legacy"] = run_legacy_rules(page_texts, page_tables, report_kind)
    elif parse_mode == "structured":
        result["structured"] = run_structured_rules(page_texts, page_tables, report_kind)
    elif parse_mode == "shadow":
        result["legacy"] = run_legacy_rules(page_texts, page_tables, report_kind)
        result["structured"] = run_structured_rules(page_texts, page_tables, report_kind)
        result["shadow_diff"] = diff_legacy_structured(
            result["legacy"], result["structured"]
        )
    else:
        raise ValueError(f"unknown parse mode: {parse_mode}")
    return result


# ---------------------------------------------------------------------------
# 历史回放（--historical）：对 uploads/ 的历史 PDF 用当前规则无 AI 重跑，
# 与任务里保存的旧规则结果做逐规则数量对比。只读历史任务目录，只写 outputs/。
# ---------------------------------------------------------------------------

UPLOADS_DIR = ROOT / "uploads"


def _old_rule_counts(status_path: Path) -> Optional[Dict[str, int]]:
    """从历史任务的 status.json 读旧规则结果并按规则计数。

    legacy 任务的结果在 result.issues.all；dual 任务在 result.rule_findings。
    两者都是纯规则产物（不含 AI findings），可作对比基线。
    """
    try:
        payload = json.loads(status_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    result = payload.get("result")
    if not isinstance(result, dict):
        return None
    items: List[Dict[str, Any]] = []
    issues = result.get("issues")
    if isinstance(issues, dict) and isinstance(issues.get("all"), list):
        items = [x for x in issues["all"] if isinstance(x, dict)]
    else:
        rule_findings = result.get("rule_findings")
        if isinstance(rule_findings, list):
            items = [x for x in rule_findings if isinstance(x, dict)]
    if not items:
        return {}
    counts: Counter = Counter()
    for item in items:
        rule = str(item.get("rule_id") or item.get("rule") or "UNKNOWN").strip().upper()
        counts[rule] += 1
    return dict(counts)


def replay_historical_doc(
    job_dir: Path,
    parse_mode: str = "legacy",
    doc_timeout_sec: float = 180.0,
) -> Dict[str, Any]:
    """单个历史任务的当前规则重跑 + 旧结果对比。"""

    from src.services.pdf_selection import select_canonical_pdf

    try:
        pdf_path = select_canonical_pdf(job_dir)
    except FileNotFoundError:
        return {"job_id": job_dir.name, "skipped": "no_pdf"}
    except ValueError as exc:
        return {"job_id": job_dir.name, "skipped": f"ambiguous_pdf: {exc}"[:200]}

    status_path = job_dir / "status.json"
    old_counts = _old_rule_counts(status_path) if status_path.exists() else None
    old_meta: Dict[str, Any] = {}
    if status_path.exists():
        try:
            payload = json.loads(status_path.read_text(encoding="utf-8"))
            old_meta = {
                "mode": payload.get("mode"),
                "report_kind": payload.get("report_kind"),
                "doc_type": payload.get("doc_type"),
                "status": payload.get("status"),
            }
        except Exception:
            pass

    started = time.time()
    try:
        page_texts = load_page_texts(pdf_path)
        page_tables = load_page_tables(pdf_path)
    except Exception as exc:  # noqa: BLE001 - 单份失败要留痕继续
        return {
            "job_id": job_dir.name,
            "pdf": pdf_path.name,
            "skipped": f"parse_failed: {type(exc).__name__}: {exc}"[:200],
        }
    parse_sec = round(time.time() - started, 2)

    # 规则集路由口径必须与旧结果一致：优先用任务存储的 report_kind
    # （上传时由 doc_type/文件名归一），其次按真实文件名推断，
    # 最后按首页文本推断。
    report_kind = str(old_meta.get("report_kind") or "").strip().lower()
    if report_kind not in ("budget", "final"):
        report_kind = infer_report_kind(page_texts, pdf_path.name)
    # GPT5.6 R4 P1-4：parse_mode 必须真实生效——此前无条件 run_legacy_rules，
    # 报告标记 structured/shadow 却实际执行 legacy，构成虚假验证通道。
    if parse_mode == "structured":
        new = run_structured_rules(page_texts, page_tables, report_kind)
    elif parse_mode == "shadow":
        # shadow 对比语义：legacy 与 structured 各跑一遍，新口径取 legacy
        # （与旧结果对比的 delta 必须同口径），structured 结果附带留痕
        new = run_legacy_rules(page_texts, page_tables, report_kind)
        new = {**new, "shadow_structured": run_structured_rules(page_texts, page_tables, report_kind)}
    else:
        new = run_legacy_rules(page_texts, page_tables, report_kind)
        if parse_mode not in ("legacy",):
            raise ValueError(f"unknown parse mode: {parse_mode}")

    # 旧规则结果缺失（任务失败/未存 result）时不能当作"旧 0 条"参与对比，
    # 否则会制造 old=0 → new=N 的假 delta（fail-closed 对比口径）。
    has_baseline = old_counts is not None
    old_counts = old_counts if old_counts is not None else {}
    # R7 P1-4：structured 模式的 delta 限定在迁移集内——旧结果是全量
    # 规则集，未迁移规则（V33-235/CMM-004 等）的旧计数不是"被消除"，
    # 单独报告为 coverage_gap（历史实测 removed=131 假象由此产生）。
    scope = _migration_scope(parse_mode)
    per_rule_delta, coverage_gap, out_of_scope_new = _restricted_rule_delta(
        old_counts, new["rule_counts"], scope
    )
    changed = sum(abs(v["new"] - v["old"]) for v in per_rule_delta.values())
    result: Dict[str, Any] = {
        "job_id": job_dir.name,
        "pdf": pdf_path.name,
        "sha256": sha256_file(pdf_path),
        "pages": len(page_texts),
        "report_kind_old": old_meta.get("report_kind"),
        "report_kind_new": report_kind,
        "mode_old": old_meta.get("mode"),
        "has_baseline": has_baseline,
        "old_total": sum(old_counts.values()),
        "new_total": new["finding_total"],
        "rule_execution_summary": new["rule_execution_summary"],
        "old_counts": old_counts,
        "new_counts": new["rule_counts"],
        "per_rule_delta": per_rule_delta,
        "changed_total": changed,
        "coverage_gap": coverage_gap,
        "coverage_gap_total": sum(coverage_gap.values()),
        "out_of_scope_new": out_of_scope_new,
        "parse_sec": parse_sec,
    }
    # shadow 模式：structured 侧结果留痕（与旧结果对比的 delta 仍是
    # legacy 同口径；structured 侧只做记录，供逐规则差异分析）
    if parse_mode == "shadow" and new.get("shadow_structured"):
        result["shadow_structured"] = {
            "finding_total": new["shadow_structured"]["finding_total"],
            "rule_counts": new["shadow_structured"]["rule_counts"],
            "rule_execution_summary": new["shadow_structured"][
                "rule_execution_summary"
            ],
        }
    return result


def _historical_worker(args: Tuple[str, str]) -> Dict[str, Any]:
    """进程池入口：Windows spawn 需要可 pickle 的顶层函数。"""
    job_name, parse_mode = args
    return replay_historical_doc(UPLOADS_DIR / job_name, parse_mode=parse_mode)


def _stored_report_kind(job_dir: Path) -> str:
    """历史任务存储的 report_kind（status.json），无则空串。"""
    try:
        payload = json.loads((job_dir / "status.json").read_text(encoding="utf-8"))
        return str(payload.get("report_kind") or "").strip().lower()
    except Exception:
        return ""


def _cached_result_valid(result: Dict[str, Any]) -> bool:
    """缓存结果是否仍有效：job 目录的 PDF 与缓存 SHA 一致。

    review P1-6 补完（PDF SHA 纳入缓存校验）：此前检查点只验
    parse_mode + 代码指纹 + job_set，同一 job_id 的 PDF 被替换后旧缓存
    仍被复用。恢复时对每条缓存结果重算当前 PDF 的 SHA——不一致即丢弃
    （该任务重新执行）。skipped/failed 条目不带 sha256 → 直接失效重跑
    （恢复语义：只有完整跑完的条目才能续）。
    """
    job_id = result.get("job_id")
    pdf_name = result.get("pdf")
    if not job_id or not pdf_name or not result.get("sha256"):
        return False
    try:
        from src.services.pdf_selection import select_canonical_pdf

        pdf_path = select_canonical_pdf(UPLOADS_DIR / str(job_id))
        return (
            pdf_path.name == str(pdf_name)
            and sha256_file(pdf_path) == result.get("sha256")
        )
    except Exception:
        return False


def replay_historical(
    parse_mode: str,
    limit: Optional[int],
    resume: bool,
    workers: int,
) -> Dict[str, Any]:
    """扫描 uploads/ 全部历史任务，输出逐规则数量变化聚合报告。"""

    jobs = []
    from src.services.pdf_selection import select_canonical_pdf

    for job_dir in sorted(UPLOADS_DIR.iterdir()):
        if not job_dir.is_dir():
            continue
        try:
            select_canonical_pdf(job_dir)
        except FileNotFoundError:
            continue
        except ValueError:
            # 保留 PDF 身份不明确的任务，让逐任务结果显式记录
            # ambiguous_pdf，而不是静默从历史任务集剔除。
            pass
        jobs.append(job_dir.name)
    if limit and parse_mode == "structured":
        # 强制抽取 final 样本（R6 P1-6 补完）：--limit 抽样时优先决算类
        # 任务——结构化迁移的 delta 聚合只认 report_kind=final，预算任务
        # 全部进 not_applicable；此前抽样顺序固定时可能抽到 0 份 final，
        # removed_findings 报告因此失去意义。
        jobs.sort(
            key=lambda name: (
                _stored_report_kind(UPLOADS_DIR / name) != "final",
                name,
            )
        )
    if limit:
        jobs = jobs[:limit]

    # 检查点按 parse_mode 隔离（R4 修复）+ 按当前任务集过滤（R5 P2-F）
    # + 缓存指纹（R6 P1-6）：代码/规则/解析器变更后旧缓存必须作废——
    # 此前只校验 parse_mode + job_set，规则改了仍复用陈旧结果。
    # 指纹 = 引擎版本 + 规则集长度 + build_parsed_tables/规则源文件的
    # 内容哈希（同一工作副本内演进自动失效缓存）。
    def _cache_fingerprint() -> str:
        digest = hashlib.sha256()
        from src.utils.provenance import ENGINE_VERSION

        digest.update(str(ENGINE_VERSION).encode("utf-8"))
        from src.engine.rules_v33 import ALL_RULES as _ALL

        digest.update(str(len(_ALL)).encode("utf-8"))
        for src in (
            ROOT / "src" / "engine" / "structured_rules.py",
            ROOT / "src" / "engine" / "rules_v33.py",
            ROOT / "src" / "engine" / "budget_rules.py",
            ROOT / "src" / "engine" / "common_rules.py",
            ROOT / "src" / "engine" / "amount_math.py",
            ROOT / "src" / "engine" / "rule_outcome.py",
            ROOT / "src" / "engine" / "pipeline.py",
            ROOT / "src" / "utils" / "narration.py",
        ):
            digest.update(str(src).encode("utf-8"))
            digest.update(src.read_bytes())
        return digest.hexdigest()

    fingerprint = _cache_fingerprint()
    checkpoint_path = OUTPUT_DIR / f"historical-partial-{parse_mode}.json"
    results: List[Dict[str, Any]] = []
    done: set = set()
    job_set = set(jobs)
    if resume and checkpoint_path.exists():
        try:
            partial = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            # 双保险①：缓存结果的 parse_mode 或代码指纹与本次不一致时整体作废
            cached_mode = str(partial.get("parse_mode") or "")
            cached_fp = str(partial.get("cache_fingerprint") or "")
            if cached_mode == parse_mode and cached_fp == fingerprint:
                # 双保险②：只保留当前任务集合内、且 PDF 未被替换的缓存
                # 结果（--limit 变更/任务删除/PDF 更换后的陈旧条目不再
                # 混入；PDF SHA 校验见 _cached_result_valid）
                results = [
                    r
                    for r in (partial.get("results") or [])
                    if isinstance(r, dict)
                    and r.get("job_id") in job_set
                    and _cached_result_valid(r)
                ]
                done = {r.get("job_id") for r in results if r.get("job_id")}
        except Exception:
            results, done = [], set()

    pending = [name for name in jobs if name not in done]
    started = time.time()

    if workers > 1:
        from concurrent.futures import ProcessPoolExecutor, as_completed

        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(_historical_worker, (name, parse_mode)): name
                for name in pending
            }
            for idx, future in enumerate(as_completed(futures), start=1):
                try:
                    results.append(future.result())
                except Exception as exc:  # noqa: BLE001
                    results.append(
                        {
                            "job_id": futures[future],
                            "skipped": f"worker_failed: {type(exc).__name__}: {exc}"[:200],
                        }
                    )
                if idx % 20 == 0:
                    elapsed = round(time.time() - started, 1)
                    print(
                        f"[historical] {idx}/{len(pending)} processed, elapsed={elapsed}s",
                        flush=True,
                    )
                    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
                    _write_checkpoint_atomic(
                        checkpoint_path,
                        {
                            "parse_mode": parse_mode,
                            "cache_fingerprint": fingerprint,
                            "results": results,
                        },
                    )
    else:
        for idx, name in enumerate(pending, start=1):
            try:
                results.append(_historical_worker((name, parse_mode)))
            except Exception as exc:  # noqa: BLE001
                results.append(
                    {"job_id": name, "skipped": f"failed: {type(exc).__name__}: {exc}"[:200]}
                )
            if idx % 20 == 0:
                print(f"[historical] {idx}/{len(pending)}", flush=True)

    # ---- 聚合 ----
    # 无旧基线的文档只记录 new_counts，不参与 delta 聚合与 top30
    processed = [r for r in results if "old_counts" in r and r.get("has_baseline")]
    no_baseline = [r for r in results if "old_counts" in r and not r.get("has_baseline")]
    skipped = [r for r in results if "old_counts" not in r]
    # not_applicable 分组（GPT5.6 R6 P1-6）：structured 模式下 report_kind
    # 路由后不适用（budget/unknown 不执行 V33 决算迁移集，total_rules=0）。
    # 此前这些任务仍计入 processed/delta——预算任务的旧预算规则结果被
    # 误读成 "structured removed_findings"（R6 smoke 实测 removed=31 假象）。
    # 不适用任务单列，不进 delta 聚合与 top30。
    if parse_mode == "structured":
        not_applicable = [
            r
            for r in processed
            if str(r.get("report_kind_new") or "").lower() != "final"
        ]
        processed = [
            r
            for r in processed
            if str(r.get("report_kind_new") or "").lower() == "final"
        ]
    else:
        not_applicable = []
    skip_reasons = dict(
        Counter(r.get("skipped", "unknown").split(":")[0] for r in skipped)
    )
    if no_baseline:
        skip_reasons["no_baseline"] = len(no_baseline)

    # 旧结果是**历史代码版本**产出的：当时 report_kind=unknown 也会路由到
    # 预算/决算专项规则集（后来已修复为只跑通用规则）。为把"本次整改的
    # 规则修复影响"与"历史路由行为差异"分开，聚合分两层：
    # - consistent_routing：新旧 report_kind 一致 → delta 纯粹来自规则逻辑；
    # - routing_changed：old 有类型而 new=unknown（或反向）→ 单独列出。
    consistent = [r for r in processed if r.get("report_kind_old") == r.get("report_kind_new") and r.get("report_kind_new") in ("budget", "final")]
    routing_changed = [r for r in processed if r not in consistent]

    def aggregate(docs: List[Dict[str, Any]]) -> Dict[str, Dict[str, int]]:
        agg: Dict[str, Dict[str, int]] = {}
        # R7 P1-4：structured 模式的聚合同样限定在迁移集内（与单份
        # delta 同域——未迁移规则的旧计数不构成 removed）。
        scope = _migration_scope(parse_mode)
        for r in docs:
            rules = set(r["old_counts"]) | set(r["new_counts"])
            if scope is not None:
                rules &= scope
                # R8 P1：漂移规则（真实执行但未登记迁移集）已显式进入
                # 单份 per_rule_delta——聚合必须同步补回该域，否则
                # docs_changed 累加对域外键 KeyError（实测 V33-999 崩溃）
                rules |= set(r.get("out_of_scope_new") or [])
            for rule in rules:
                entry = agg.setdefault(rule, {"old": 0, "new": 0, "docs_changed": 0})
                entry["old"] += r["old_counts"].get(rule, 0)
                entry["new"] += r["new_counts"].get(rule, 0)
        for r in docs:
            for rule in r["per_rule_delta"]:
                agg[rule]["docs_changed"] += 1
        for entry in agg.values():
            entry["delta"] = entry["new"] - entry["old"]
        return agg

    rule_agg = aggregate(consistent)
    routing_rule_agg = aggregate(routing_changed)

    # R7 P1-4：未迁移规则的旧计数聚合为 coverage_gap（结构化路径不
    # 重跑这些规则，其历史计数不参与 delta/removed——单独报告覆盖
    # 缺口，历史回归结论才有可比性）。
    gap_agg: Dict[str, int] = {}
    for r in processed:
        for rule, cnt in (r.get("coverage_gap") or {}).items():
            gap_agg[rule] = gap_agg.get(rule, 0) + cnt

    # R7 /review：域外新规则（适配器与 STRUCTURED_MIGRATED_RULES 漂移）
    # 聚合留痕——出现即提示迁移集登记与适配器执行列表不同步。
    # R9 P1：遍历**所有成功执行的 structured 结果**（含无历史基线
    # 任务）——此前只遍历 processed，无基线任务真实执行的漂移规则
    # 被漏报（实测结果明细含 V33-999，adapter_scope_drift 却为空）。
    drift_agg: Counter = Counter()
    for r in processed + no_baseline:
        for rule in r.get("out_of_scope_new") or []:
            drift_agg[rule] += 1

    ranked_docs = sorted(
        consistent, key=lambda r: (-r["changed_total"], r["job_id"])
    )
    top_changed = [
        {
            "job_id": r["job_id"],
            "pdf": r["pdf"],
            "old_total": r["old_total"],
            "new_total": r["new_total"],
            "changed_total": r["changed_total"],
            "per_rule_delta": r["per_rule_delta"],
        }
        for r in ranked_docs[:30]
    ]
    routing_changed_docs = [
        {
            "job_id": r["job_id"],
            "pdf": r["pdf"],
            "report_kind_old": r.get("report_kind_old"),
            "report_kind_new": r.get("report_kind_new"),
            "old_total": r["old_total"],
            "new_total": r["new_total"],
        }
        for r in sorted(routing_changed, key=lambda r: (-r["changed_total"], r["job_id"]))
    ]

    # 复核工作量的量级：被削掉的 findings / 新增的 findings
    removed_total = sum(agg["old"] - agg["new"] for agg in rule_agg.values() if agg["delta"] < 0)
    added_total = sum(agg["new"] - agg["old"] for agg in rule_agg.values() if agg["delta"] > 0)

    return {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "parse_mode": parse_mode,
        "jobs_total": len(jobs),
        "sampled_final_count": sum(
            1 for name in jobs if _stored_report_kind(UPLOADS_DIR / name) == "final"
        ),
        "processed": len(processed),
        "not_applicable_count": len(not_applicable),
        "not_applicable_kinds": dict(
            Counter(str(r.get("report_kind_new")) for r in not_applicable)
        ),
        "consistent_routing_docs": len(consistent),
        "routing_changed_docs": len(routing_changed),
        "routing_changed_note": (
            "old 结果由历史代码版本产出（unknown 当时也路由专项规则集），"
            "routing_changed 层的差异不是本次整改造成的回归"
        ),
        "skipped": len(skipped),
        "skip_reasons": skip_reasons,
        "no_baseline_count": len(no_baseline),
        "elapsed_sec": round(time.time() - started, 1),
        "rule_aggregate": dict(
            sorted(rule_agg.items(), key=lambda kv: abs(kv[1]["delta"]), reverse=True)
        ),
        "routing_changed_rule_aggregate": dict(
            sorted(
                routing_rule_agg.items(), key=lambda kv: abs(kv[1]["delta"]), reverse=True
            )
        ),
        "removed_total": removed_total,
        "added_total": added_total,
        "coverage_gap_total": sum(gap_agg.values()),
        "coverage_gap_rule_aggregate": dict(
            sorted(gap_agg.items(), key=lambda kv: (-kv[1], kv[0]))
        ),
        "coverage_gap_note": (
            "structured 模式只执行 STRUCTURED_MIGRATED_RULES（九条迁移集）——"
            "未迁移规则不重跑，其历史旧计数不是'被消除的 findings'，单列"
            "于此供覆盖缺口评估，不参与 removed/added（R7 P1-4）。"
        ),
        "adapter_scope_drift": dict(sorted(drift_agg.items())),
        "adapter_scope_drift_note": (
            "structured 实际执行过但不在 STRUCTURED_MIGRATED_RULES 登记内"
            "的规则——出现即迁移集登记与适配器执行列表漂移，需同步"
            "（漂移规则已显式进入 delta，不静默丢弃）。"
        ),
        "top_changed_docs_for_manual_review": top_changed,
        # 注意与上方 "routing_changed_docs"（计数）区分：此处是变更文档
        # 明细清单（此前与计数同名，字典重复键导致计数被列表覆盖，
        # GPT5.6 P2 指出后拆分为独立键名）。
        "routing_changed_top_docs": routing_changed_docs[:30],
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--doc", default=None, help="只重放指定 DOC-ID（默认全部）")
    parser.add_argument(
        "--parse-mode",
        default="legacy",
        choices=["legacy", "structured", "shadow"],
        help="解析三态开关：legacy / structured / shadow",
    )
    parser.add_argument(
        "--historical",
        action="store_true",
        help="历史回放：扫描 uploads/ 的历史 PDF，当前规则无 AI 重跑并对比旧结果",
    )
    parser.add_argument("--limit", type=int, default=None, help="历史回放只处理前 N 个任务")
    parser.add_argument(
        "--no-resume", dest="resume", action="store_false", help="忽略断点续跑检查点"
    )
    parser.add_argument(
        "--workers", type=int, default=4, help="历史回放的并行进程数（默认 4）"
    )
    args = parser.parse_args()

    if args.historical:
        report = replay_historical(
            parse_mode=args.parse_mode,
            limit=args.limit,
            resume=args.resume,
            workers=max(1, args.workers),
        )
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        out_path = _fresh_output_path(
            f"historical-rules-delta-{args.parse_mode}"
        )
        out_path = _write_json_exclusive(out_path, report)
        print(
            f"historical replay: processed={report['processed']} "
            f"skipped={report['skipped']} elapsed={report['elapsed_sec']}s"
        )
        print(f"removed_findings={report['removed_total']} added_findings={report['added_total']}")
        if report.get("coverage_gap_total"):
            print(
                f"coverage_gap_findings={report['coverage_gap_total']} "
                "（未迁移规则旧计数，不参与 delta）"
            )
        print("逐规则变化（按 |delta| 降序，前 20）:")
        for rule, agg in list(report["rule_aggregate"].items())[:20]:
            print(
                f"  {rule}: old={agg['old']} new={agg['new']} delta={agg['delta']:+d} "
                f"(docs_changed={agg['docs_changed']})"
            )
        print("变化最大的文档（前 30，供人工复核）:")
        for item in report["top_changed_docs_for_manual_review"]:
            print(
                f"  {item['job_id']} {item['pdf'][:30]} "
                f"old={item['old_total']} new={item['new_total']}"
            )
        print(f"report -> {out_path.relative_to(ROOT)}")
        return 0

    if not CORPUS_DIR.exists():
        print(f"corpus dir not found: {CORPUS_DIR}", file=sys.stderr)
        return 1

    doc_dirs = (
        [CORPUS_DIR / args.doc]
        if args.doc
        else sorted(path for path in CORPUS_DIR.iterdir() if path.is_dir())
    )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    exit_code = 0
    for doc_dir in doc_dirs:
        if not doc_dir.is_dir():
            continue
        try:
            result = replay_doc(doc_dir, args.parse_mode)
        except Exception as exc:  # noqa: BLE001 - 回放失败要留痕继续下一份
            result = {"doc_id": doc_dir.name, "error": f"{type(exc).__name__}: {exc}"}
            exit_code = 2
        out_path = _fresh_output_path(f"{doc_dir.name}-{args.parse_mode}")
        out_path = _write_json_exclusive(out_path, result)
        summary = result.get("legacy") or result.get("structured") or {}
        print(
            f"{doc_dir.name}: mode={args.parse_mode} "
            f"findings={summary.get('finding_total', '?')} -> {out_path.relative_to(ROOT)}"
        )
        if result.get("shadow_diff"):
            changed = result["shadow_diff"]["rules_changed"]
            print(f"  shadow 差异规则 {len(changed)} 条")
            for item in changed[:20]:
                print(
                    f"  {item['rule']}: legacy={item['legacy']} "
                    f"structured={item['structured']} (Δ{item['delta']})"
                )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
