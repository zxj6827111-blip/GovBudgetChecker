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
import re
import sys
import time
import unicodedata
from collections import Counter
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


def replay_doc(doc_dir: Path, parse_mode: str) -> Dict[str, Any]:
    pdf_path = next(
        (candidate for candidate in sorted(doc_dir.glob("*.pdf"))), None
    )
    if pdf_path is None:
        raise FileNotFoundError(f"no pdf in corpus doc dir: {doc_dir}")

    page_texts = load_page_texts(pdf_path)
    page_tables = load_page_tables(pdf_path)
    report_kind = infer_report_kind(page_texts, doc_dir.name)

    result: Dict[str, Any] = {
        "doc_id": doc_dir.name,
        "pdf": pdf_path.name,
        "sha256": sha256_file(pdf_path),
        "pages": len(page_texts),
        "report_kind": report_kind,
        "replay_ts": time.time(),
        "parse_mode": parse_mode,
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

    pdf_path = next((c for c in sorted(job_dir.glob("*.pdf"))), None)
    if pdf_path is None:
        return {"job_id": job_dir.name, "skipped": "no_pdf"}

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
    new = run_legacy_rules(page_texts, page_tables, report_kind)

    # 旧规则结果缺失（任务失败/未存 result）时不能当作"旧 0 条"参与对比，
    # 否则会制造 old=0 → new=N 的假 delta（fail-closed 对比口径）。
    has_baseline = old_counts is not None
    old_counts = old_counts if old_counts is not None else {}
    all_rules = sorted(set(old_counts) | set(new["rule_counts"]))
    per_rule_delta = {
        rule: {
            "old": old_counts.get(rule, 0),
            "new": new["rule_counts"].get(rule, 0),
        }
        for rule in all_rules
        if old_counts.get(rule, 0) != new["rule_counts"].get(rule, 0)
    }
    changed = sum(abs(v["new"] - v["old"]) for v in per_rule_delta.values())
    result: Dict[str, Any] = {
        "job_id": job_dir.name,
        "pdf": pdf_path.name,
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
        "parse_sec": parse_sec,
    }
    return result


def _historical_worker(args: Tuple[str, str]) -> Dict[str, Any]:
    """进程池入口：Windows spawn 需要可 pickle 的顶层函数。"""
    job_name, parse_mode = args
    return replay_historical_doc(UPLOADS_DIR / job_name, parse_mode=parse_mode)


def replay_historical(
    parse_mode: str,
    limit: Optional[int],
    resume: bool,
    workers: int,
) -> Dict[str, Any]:
    """扫描 uploads/ 全部历史任务，输出逐规则数量变化聚合报告。"""

    jobs = []
    for job_dir in sorted(UPLOADS_DIR.iterdir()):
        if not job_dir.is_dir():
            continue
        if not list(job_dir.glob("*.pdf")):
            continue
        jobs.append(job_dir.name)
    if limit:
        jobs = jobs[:limit]

    checkpoint_path = OUTPUT_DIR / "historical-partial.json"
    results: List[Dict[str, Any]] = []
    done: set = set()
    if resume and checkpoint_path.exists():
        try:
            partial = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            results = partial.get("results") or []
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
                    checkpoint_path.write_text(
                        json.dumps(
                            {"parse_mode": parse_mode, "results": results},
                            ensure_ascii=False,
                        ),
                        encoding="utf-8",
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
        for r in docs:
            for rule in set(r["old_counts"]) | set(r["new_counts"]):
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
        "processed": len(processed),
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
        stamp = time.strftime("%Y%m%d-%H%M%S")
        out_path = OUTPUT_DIR / f"historical-rules-delta-{stamp}.json"
        out_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(
            f"historical replay: processed={report['processed']} "
            f"skipped={report['skipped']} elapsed={report['elapsed_sec']}s"
        )
        print(f"removed_findings={report['removed_total']} added_findings={report['added_total']}")
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
        stamp = time.strftime("%Y%m%d-%H%M%S")
        out_path = OUTPUT_DIR / f"{doc_dir.name}-{args.parse_mode}-{stamp}.json"
        out_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
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
