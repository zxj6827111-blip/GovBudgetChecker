#!/usr/bin/env python3
"""PDF 入口整改（2026-10-24 复核）验收工具：整改前后用同一把尺子量。

    # 采集：整改前已冻结一份基线；整改后对同一批原件再采一次
    python scripts/verify_intake_remediation.py collect \
        --out outputs/intake_remediation_20261024/after
    # 比较：按整改计划的验收条目逐条给出 PASS / FAIL / REVIEW
    python scripts/verify_intake_remediation.py compare \
        --baseline outputs/intake_remediation_20261024/baseline \
        --current outputs/intake_remediation_20261024/after

三个采集阶段：

* ``intake``：全部原件。上传预检（封面、文种、年度、画像）+ 自动归属决定 + 冲突检查一致性。
* ``parse``：按哈希去重后的原生 PDF。统一解析对象 → 统一事实 → 入库回执，留存逐事实指纹。
* ``e2e``：cases 文件列出的原件，走离线生产管线 ``api.main._run_pipeline``，留存问题与复核原因。

只读原件；不连数据库、不调 AI；组织库先复制到临时目录再读，不写 ``data/``。
指标口径与阈值见 ``docs/PDF入口整改计划_20261024.md``，已知样本与期望结果见
``scripts/intake_remediation_cases.json``。整改中如果入口函数改名或改签名，只改下方
「适配层」并在交付说明里写明；不得改指标、阈值和比较逻辑。
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import gzip
import hashlib
import inspect
import json
import os
import re
import shutil
import statistics
import sys
import tempfile
import time
import unicodedata
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from unittest import mock

_REPO_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_CORPUS = Path.home() / "Desktop" / "决算测试上海市"
_DEFAULT_CASES = _REPO_ROOT / "scripts" / "intake_remediation_cases.json"
_YEAR_RE = re.compile(r"\s*20\d{2}\s*年")
_AMOUNT_RE = re.compile(r"^-?\d{1,3}(?:,\d{3})*\.\d{2}$|^-?\d+\.\d{2}$")


# ---------------------------------------------------------------------------
# 通用：原件清单、文件名主体、名称归一
# ---------------------------------------------------------------------------


def corpus_files(corpus: Path) -> List[Path]:
    return sorted(p for p in corpus.rglob("*") if p.is_file() and p.suffix.lower() == ".pdf")


def rel(path: Path, corpus: Path) -> str:
    return path.relative_to(corpus).as_posix()


def filename_org(path: Path) -> str:
    """文件名中的主体名：只作对照基准，文件名本身也可能写错。

    兼容「××2022年度决算」「2022年度××单位决算」「××2022决算」三种写法。
    """
    stem = path.name[:-4] if path.name.lower().endswith(".pdf") else path.name
    stem = re.sub(r"^\s*20\d{2}\s*年(?:度)?", "", stem)
    stem = re.split(r"\s*20\d{2}(?!\d)", stem, maxsplit=1)[0]
    return re.sub(r"(?:部门|单位)?(?:决算|预算)$", "", stem.strip()).strip()


def filename_year(path: Path, corpus: Path) -> int:
    match = re.search(r"(20\d{2})\s*年", path.name)
    return int(match.group(1)) if match else int(rel(path, corpus)[:4])


def name_key(value: Any) -> str:
    """比较用名称键：NFKC、去空白和括号别名、去「本级/本部」尾缀。只用于验收对照。"""
    text = re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(value or "")))
    text = re.sub(r"\([^()]*\)", "", text)
    text = re.sub(r"^上海(?!市)", "上海市", text)
    return re.sub(r"(?:本级|本部)$", "", text)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_jsonl_gz(path: Path, rows: Iterable[Dict[str, Any]]) -> int:
    count = 0
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            count += 1
    return count


def read_jsonl_gz(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


# ---------------------------------------------------------------------------
# 适配层：只在这里接触生产入口
# ---------------------------------------------------------------------------


def _worker_init(org_data_dir: str, upload_dir: str) -> None:
    os.environ.update(
        GOVBUDGET_AUTH_ENABLED="false", DATABASE_URL="", AI_ASSIST_ENABLED="false",
        PDF_PARSE_ISOLATION_ENABLED="false", ORG_DATA_DIR=org_data_dir, UPLOAD_DIR=upload_dir,
    )
    os.chdir(_REPO_ROOT)
    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))
    import logging

    logging.disable(logging.CRITICAL)


def adapter_preflight(path: Path) -> Dict[str, Any]:
    """上传预检（与 /api/documents/preflight 同一函数）。"""
    from api.routes.upload import _inspect_document_preflight

    return _inspect_document_preflight(filename=path.name, pdf_path=path)


def adapter_auto_bind(path: Path) -> Optional[Dict[str, Any]]:
    """上传未手选组织时的自动归属决定；拦截落盘，只记录「会绑到谁」。"""
    from api import runtime
    from api.routes import upload

    bound: Dict[str, Any] = {}

    def record(job_id, org_id, *, match_type="auto", confidence=1.0, **_):
        org = runtime.require_org_storage().get_by_id(org_id)
        bound.update(organization_id=org_id, organization_name=getattr(org, "name", None),
                     level=getattr(org, "level", None), match_type=match_type, confidence=confidence)
        return dict(bound)

    with mock.patch.object(runtime, "find_first_pdf", lambda _job_dir: path), \
            mock.patch.object(runtime, "set_job_organization", record):
        upload._auto_match_organization("intake-acceptance", path.name, None, preflight=None)
    return bound or None


def adapter_conflict_consistency(preflight: Dict[str, Any]) -> Dict[str, Optional[str]]:
    """同一候选按序列化字段与按 name 字段各查一次冲突，两者必须一致（契约检查）。"""
    from api.routes.upload import _organization_binding_conflict

    current = preflight.get("current") or {}
    if not current:
        return {"serialized": None, "by_name": None}
    as_name = {**current, "name": current.get("organization_name")}
    return {
        "serialized": (_organization_binding_conflict(preflight, current) or {}).get("code"),
        "by_name": (_organization_binding_conflict(preflight, as_name) or {}).get("code"),
    }


def adapter_fact_context(artifact, *, org_name: str, fiscal_year: int, org_level: Optional[str]):
    """统一事实。整改后若支持 ``org_level``（组织库层级兜底口径）就传入。"""
    from src.engine.fact_context import build_fact_context_from_artifact

    kwargs = dict(org_name=org_name, fiscal_year=fiscal_year, report_kind="final",
                  doc_type="dept_final", require_complete_identity=True)
    if "org_level" in inspect.signature(build_fact_context_from_artifact).parameters:
        kwargs["org_level"] = org_level
    return build_fact_context_from_artifact(artifact, **kwargs)


# ---------------------------------------------------------------------------
# 采集
# ---------------------------------------------------------------------------


def _intake_one(task: Dict[str, Any]) -> Dict[str, Any]:
    path = Path(task["path"])
    out: Dict[str, Any] = {"file": task["rel"], "sha256": task["sha256"], "filename_org": filename_org(path)}
    try:
        import pymupdf

        with pymupdf.open(path) as doc:
            texts = [page.get_text() for page in doc]
        out["pages"] = len(texts)
        out["no_text_pages"] = sum(1 for text in texts if not text.strip())
        out["first_page_has_text"] = bool(texts and texts[0].strip())
        pre = adapter_preflight(path)
        profile = {}
        try:
            from api import runtime

            cover = runtime.extract_cover_metadata(
                page_texts=runtime.extract_pdf_page_texts(path, max_pages=3), filename=path.name)
            profile = cover.get("profile") or {}
            out["profile_status"] = cover.get("profile_status")
        except Exception as exc:  # 画像失败也是一项结果
            out["profile_error"] = repr(exc)[:200]
        current = pre.get("current") or {}
        out.update(
            report_kind=pre.get("report_kind"), doc_type=pre.get("doc_type"), report_year=pre.get("report_year"),
            cover_title=str(pre.get("cover_title") or "")[:120], cover_org_name=pre.get("cover_org_name") or "",
            cover_org_label=pre.get("cover_org_label") or "", scope_hint=pre.get("scope_hint") or "",
            preflight_current=current.get("organization_name"), preflight_level=current.get("level"),
            match_basis=current.get("match_basis"), confidence=current.get("confidence"),
            suggestions=[(s.get("organization_name"), s.get("level"), s.get("confidence"), s.get("match_basis"))
                         for s in (pre.get("suggestions") or [])[:3]],
            conflicts=[c.get("field") for c in (profile.get("conflicts") or [])] if isinstance(profile, dict) else [],
            conflict_check=adapter_conflict_consistency(pre),
        )
        bound = adapter_auto_bind(path)
        out["bound"] = bound.get("organization_name") if bound else None
        out["bound_level"] = bound.get("level") if bound else None
    except Exception as exc:
        out["error"] = repr(exc)[:300]
    return out


def _profile_probe(probe: Dict[str, Any]) -> Dict[str, Any]:
    """构造封面的画像（混合文种等），不依赖原件。"""
    from src.services.document_profile_resolver import resolve_document_profile

    profile = resolve_document_profile(filename=probe.get("filename", "probe.pdf"),
                                       page_texts=[probe["title"], "正文"])
    data = profile.to_dict() if hasattr(profile, "to_dict") else dict(profile.__dict__)

    def value(field):
        item = data.get(field)
        return item.get("value") if isinstance(item, dict) else item

    return {"report_kind": value("report_kind"), "report_year": value("report_year"),
            "profile_status": data.get("profile_status"),
            "conflicts": [c.get("field") for c in data.get("conflicts") or []]}


def _fact_key(fact) -> str:
    return "|".join(str(v) for v in (fact.table_code, fact.indicator, fact.subject_code, fact.budget_stage,
                                     fact.period, fact.normalized_value))


def _parse_one(task: Dict[str, Any]) -> Dict[str, Any]:
    path = Path(task["path"])
    out: Dict[str, Any] = {"file": task["rel"], "sha256": task["sha256"]}
    try:
        from src.engine.fact_context import operand_rejection_reason
        from src.services.pdf_parser import PDFParser
        from src.services.table_import_receipt import build_table_import_receipt

        started = time.time()
        artifact = PDFParser(None).build_parse_artifact(str(path))
        out["parse_seconds"] = round(time.time() - started, 2)
        org = filename_org(path)
        ctx = adapter_fact_context(artifact, org_name=org, fiscal_year=task["year"], org_level=task.get("org_level"))
        facts = list(ctx.facts) if ctx else []
        receipt = build_table_import_receipt(artifact, facts, org_name=org)
        coverage = receipt.get("fiscal_coverage") or {}
        reasons = collections.Counter()
        for table in receipt.get("tables") or []:
            for cell in table.get("amount_cells") or []:
                if cell.get("reason"):
                    reasons[cell["reason"]] += 1
        rejections = collections.Counter(operand_rejection_reason(f, org) or "admitted" for f in facts)
        out.update(
            fragments=[[t.page_number, t.table_code, max((len(r) for r in t.rows), default=0), len(t.rows),
                        t.extraction_method] for t in artifact.table_fragments],
            amount_cells=coverage.get("amount_cell_count"), admitted=coverage.get("rule_admitted_count"),
            pending=coverage.get("pending_review_count"), mapping_incomplete=coverage.get("mapping_incomplete_table_count"),
            arithmetic_conflicts=coverage.get("column_arithmetic_conflict_count"),
            pending_reasons=dict(reasons), fact_count=len(facts),
            identity_incomplete=sum(1 for f in facts if f.extra_dims.get("identity_incomplete")),
            rejections=dict(rejections.most_common(6)),
            # 逐事实指纹：带科目编码的事实（真实行）；无编码事实单独计数（含空行造成的幻影重复）。
            facts=sorted(_fact_key(f) + "|" + f.recognition_status for f in facts if f.subject_code),
            uncoded_facts=sum(1 for f in facts if not f.subject_code),
        )
    except Exception as exc:
        out["error"] = repr(exc)[:300]
    return out


def _e2e_one(task: Dict[str, Any]) -> Dict[str, Any]:
    path = Path(task["path"])
    out: Dict[str, Any] = {"file": task["rel"]}
    try:
        from api import main as api_main

        os.environ["DATABASE_URL"] = ""
        job_id = hashlib.md5(task["rel"].encode()).hexdigest()
        job_dir = Path(os.environ["UPLOAD_DIR"]) / job_id
        shutil.rmtree(job_dir, ignore_errors=True)
        job_dir.mkdir(parents=True)
        shutil.copy(path, job_dir / path.name)
        year = task["year"]
        status = {
            "job_id": job_id, "filename": path.name, "fiscal_year": str(year), "report_year": year,
            "doc_type": "dept_final", "report_kind": "final", "checksum": task["sha256"], "status": "queued",
            "use_local_rules": True, "use_ai_assist": False, "mode": "legacy",
            "organization_id": task.get("org_id"), "organization_name": task.get("org_name") or filename_org(path),
        }
        (job_dir / "status.json").write_text(json.dumps(status, ensure_ascii=False), encoding="utf-8")
        started = time.time()
        asyncio.run(api_main._run_pipeline(job_dir))
        final = json.loads((job_dir / "status.json").read_text(encoding="utf-8"))
        issues = (final.get("result") or {}).get("issues") or {}
        flat = issues.get("all") if isinstance(issues, dict) and isinstance(issues.get("all"), list) else (
            [x for v in issues.values() if isinstance(v, list) for x in v] if isinstance(issues, dict) else issues)
        seen, unique = set(), []
        for item in flat or []:
            key = (item.get("rule_id"), item.get("message"))
            if key not in seen:
                seen.add(key)
                unique.append({"rule_id": item.get("rule_id"), "severity": item.get("severity"),
                               "title": str(item.get("title") or "")[:80], "message": str(item.get("message") or "")[:240],
                               "page": item.get("page_number")})
        out.update(
            seconds=round(time.time() - started, 1), status=final.get("status"),
            conclusion=final.get("analysis_conclusion"), issues=unique,
            review_reasons=[{"code": r.get("code"), "message": str(r.get("message") or "")[:300]}
                            for r in final.get("review_reasons") or []],
        )
    except Exception as exc:
        out["error"] = repr(exc)[:300]
    finally:
        shutil.rmtree(Path(os.environ.get("UPLOAD_DIR", "/nonexistent")) / hashlib.md5(task["rel"].encode()).hexdigest(),
                      ignore_errors=True)
    return out


def _org_directory(org_data_dir: Path) -> Dict[str, Dict[str, Any]]:
    payload = json.loads((org_data_dir / "organizations.json").read_text(encoding="utf-8"))
    orgs = payload if isinstance(payload, list) else payload.get("organizations", payload)
    orgs = list(orgs.values()) if isinstance(orgs, dict) else orgs
    index: Dict[str, Dict[str, Any]] = {}
    for org in orgs:
        index.setdefault(name_key(org.get("name")), org)
    return index


def collect(args: argparse.Namespace) -> int:
    corpus = Path(args.corpus).expanduser().resolve()
    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    stages = set(args.stages.split(","))
    cases = json.loads(Path(args.cases).read_text(encoding="utf-8"))
    files = corpus_files(corpus)
    if args.only:
        wanted = set(json.loads(Path(args.only).read_text(encoding="utf-8")))
        files = [p for p in files if rel(p, corpus) in wanted]
    if args.limit:
        files = files[: args.limit]

    # 组织库复制到临时目录：读锁文件、缓存都落在副本里。
    source_org_dir = Path(args.org_data_dir).resolve()
    work = Path(tempfile.mkdtemp(prefix="intake-acceptance-"))
    org_copy = work / "org"
    org_copy.mkdir()
    for name in ("organizations.json", "job_org_links.json"):
        if (source_org_dir / name).exists():
            shutil.copy(source_org_dir / name, org_copy / name)
    upload_dir = work / "uploads"
    upload_dir.mkdir()
    directory = _org_directory(org_copy)

    shas = {p: _sha256(p) for p in files}
    meta = {
        "collected_at": time.strftime("%Y-%m-%d %H:%M:%S"), "corpus": str(corpus), "file_count": len(files),
        "org_data_dir": str(source_org_dir), "org_directory_sha256": _sha256(source_org_dir / "organizations.json"),
        "stages": sorted(stages), "source_fingerprint": _source_fingerprint(),
    }
    tasks = [{"path": str(p), "rel": rel(p, corpus), "sha256": shas[p], "year": filename_year(p, corpus)} for p in files]
    with ProcessPoolExecutor(args.jobs, initializer=_worker_init, initargs=(str(org_copy), str(upload_dir))) as pool:
        if "intake" in stages:
            count = _write_jsonl_gz(out_dir / "intake.jsonl.gz", pool.map(_intake_one, tasks, chunksize=4))
            print(f"intake: {count} 份")
            probe = cases.get("profile", {}).get("mixed_title_probe")
            if probe:
                try:
                    meta["mixed_title_probe"] = pool.submit(_profile_probe, probe).result()
                except Exception as exc:
                    meta["mixed_title_probe"] = {"error": repr(exc)[:300]}
        if "parse" in stages:
            seen, unique = set(), []
            for task in tasks:
                if task["sha256"] in seen:
                    continue
                seen.add(task["sha256"])
                org = directory.get(name_key(filename_org(Path(task["path"]))))
                level = (org or {}).get("level")
                unique.append({**task, "org_level": level if level in {"department", "unit"} else None})
            count = _write_jsonl_gz(out_dir / "parse.jsonl.gz", pool.map(_parse_one, unique, chunksize=2))
            print(f"parse: {count} 份（去重后）")
    if "e2e" in stages:
        wanted = {case["file"] for case in cases.get("e2e", [])}
        e2e_tasks = []
        for task in tasks:
            if task["rel"] in wanted:
                org = directory.get(name_key(filename_org(Path(task["path"])))) or {}
                e2e_tasks.append({**task, "org_id": org.get("id"), "org_name": org.get("name")})
        # 生产管线在导入时读取环境变量，单独一个进程池，进程内串行执行。
        with ProcessPoolExecutor(min(args.jobs, 4), initializer=_worker_init,
                                 initargs=(str(org_copy), str(upload_dir))) as pool:
            count = _write_jsonl_gz(out_dir / "e2e.jsonl.gz", pool.map(_e2e_one, e2e_tasks))
        print(f"e2e: {count} 份")
    (out_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    shutil.rmtree(work, ignore_errors=True)
    return 0


def _source_fingerprint() -> Dict[str, str]:
    """工作区常有未提交改动，按目录内容哈希记录被测代码版本。"""
    result = {}
    for folder in ("src", "api", "config", "rules"):
        digest = hashlib.sha256()
        for path in sorted((_REPO_ROOT / folder).rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts and path.suffix in {".py", ".yaml", ".yml", ".json"}:
                digest.update(path.relative_to(_REPO_ROOT).as_posix().encode())
                digest.update(path.read_bytes())
        result[folder] = digest.hexdigest()[:16]
    return result


# ---------------------------------------------------------------------------
# 比较与判定
# ---------------------------------------------------------------------------


class Verdicts:
    def __init__(self) -> None:
        self.rows: List[Dict[str, Any]] = []

    def add(self, item: str, ok: Optional[bool], expected: str, actual: str, detail: Any = None) -> None:
        verdict = "REVIEW" if ok is None else ("PASS" if ok else "FAIL")
        self.rows.append({"item": item, "verdict": verdict, "expected": expected, "actual": actual, "detail": detail})


def _by_file(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {row["file"]: row for row in rows}


def _bound_ok(row: Dict[str, Any], expect: Dict[str, Any]) -> bool:
    bound = row.get("bound")
    if "not" in expect and bound and name_key(bound) in {name_key(x) for x in expect["not"]}:
        return False
    if "one_of" in expect:
        allowed = {name_key(x) for x in expect["one_of"]}
        return bound is None and expect.get("allow_unbound", True) or (bound is not None and name_key(bound) in allowed)
    return True


def compare(args: argparse.Namespace) -> int:
    base_dir, cur_dir = Path(args.baseline), Path(args.current)
    cases = json.loads(Path(args.cases).read_text(encoding="utf-8"))
    v = Verdicts()
    _compare_intake(v, _by_file(read_jsonl_gz(base_dir / "intake.jsonl.gz")),
                    _by_file(read_jsonl_gz(cur_dir / "intake.jsonl.gz")), cases)
    probe_spec = cases.get("profile", {}).get("mixed_title_probe")
    if probe_spec:
        meta = json.loads((cur_dir / "meta.json").read_text(encoding="utf-8")) if (cur_dir / "meta.json").exists() else {}
        got = meta.get("mixed_title_probe") or {}
        ok = (bool(got) and "error" not in got and got.get("report_kind") not in {"budget", "final"}
              and got.get("report_year") in {None, ""})
        v.add("B6 混合文种标题待确认", ok, "文种待确认、年度不自动取值", json.dumps(got, ensure_ascii=False))
    _compare_parse(v, _by_file(read_jsonl_gz(base_dir / "parse.jsonl.gz")),
                   _by_file(read_jsonl_gz(cur_dir / "parse.jsonl.gz")), cases)
    _compare_e2e(v, _by_file(read_jsonl_gz(base_dir / "e2e.jsonl.gz")),
                 _by_file(read_jsonl_gz(cur_dir / "e2e.jsonl.gz")), cases)
    report = {"baseline": str(base_dir), "current": str(cur_dir), "verdicts": v.rows,
              "summary": dict(collections.Counter(r["verdict"] for r in v.rows))}
    target = Path(args.report or cur_dir / "acceptance_report.json")
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    width = max(len(r["item"]) for r in v.rows) if v.rows else 10
    for row in v.rows:
        print(f"[{row['verdict']:6}] {row['item']:<{width}}  期望 {row['expected']}  实际 {row['actual']}")
    print("合计:", report["summary"], "→", target)
    return 1 if any(r["verdict"] == "FAIL" for r in v.rows) else 0


def _compare_intake(v: Verdicts, base: Dict[str, Dict], cur: Dict[str, Dict], cases: Dict[str, Any]) -> None:
    if not cur:
        v.add("intake", False, "有采集结果", "缺 intake.jsonl.gz")
        return
    errors = [f for f, r in cur.items() if "error" in r]
    v.add("A0 预检无异常", not errors, "0 份报错", f"{len(errors)} 份", errors[:10])
    org = cases["org_binding"]
    high_level = [f for f, r in cur.items() if r.get("bound_level") in set(org["must_not_bind_level"])]
    v.add("A1 自动绑定到市/区级根节点", not high_level, "0 份", f"{len(high_level)} 份（基线 "
          f"{sum(1 for r in base.values() if r.get('bound_level') in set(org['must_not_bind_level']))}）", high_level[:20])
    bad = [c["file"] for c in org["expectations"] if c["file"] in cur and not _bound_ok(cur[c["file"]], c)]
    v.add("A2 已知错绑样本", not bad, f"{len(org['expectations'])} 例全部符合期望", f"{len(bad)} 例不符", bad[:20])
    diverge = [f for f, r in cur.items() if (r.get("conflict_check") or {}).get("serialized")
               != (r.get("conflict_check") or {}).get("by_name")]
    v.add("A3 冲突检查字段契约一致", not diverge, "0 份不一致", f"{len(diverge)} 份", diverge[:10])
    variants = [f for f in org["name_variants_must_bind"] if f in cur and not cur[f].get("bound")]
    v.add("A4 写法差异不得误拒", not variants, f"{len(org['name_variants_must_bind'])} 例仍能绑定",
          f"{len(variants)} 例未绑定", variants)
    # 回归：基线已正确绑定（与文件名主体等价）的，整改后不得改绑到其他组织或丢失绑定。
    regress = [f for f, r in base.items() if r.get("bound") and name_key(r["bound"]) == name_key(r.get("filename_org"))
               and f in cur and name_key(cur[f].get("bound")) != name_key(r["bound"])]
    v.add("A5 基线正确绑定不回退", len(regress) == 0, "0 份", f"{len(regress)} 份", regress[:20])
    unbound = [f for f, r in cur.items() if not r.get("bound")]
    v.add("A6 未自动绑定数", len(unbound) <= org["max_unbound"], f"≤ {org['max_unbound']}（组织库清洗后）",
          f"{len(unbound)}（基线 {sum(1 for r in base.values() if not r.get('bound'))}）", unbound[:30])
    agree = sum(1 for r in cur.values() if r.get("bound") and name_key(r["bound"]) == name_key(r.get("filename_org")))
    v.add("A7 绑定结果与文件名主体等价", None, "仅报告，逐例复核差异",
          f"{agree}（基线 {sum(1 for r in base.values() if r.get('bound') and name_key(r['bound']) == name_key(r.get('filename_org')))}）")

    prof = cases["profile"]
    empty = [f for f, r in cur.items() if r.get("first_page_has_text") and not r.get("cover_org_name")]
    v.add("B1 首页有字但封面主体为空", len(empty) <= prof["max_empty_cover_org"], f"≤ {prof['max_empty_cover_org']}",
          f"{len(empty)}（基线 {sum(1 for r in base.values() if r.get('first_page_has_text') and not r.get('cover_org_name'))}）", empty[:20])
    wrong_cover = [f for f, r in cur.items() if r.get("cover_org_name") and f in base and base[f].get("cover_org_name")
                   and name_key(r["cover_org_name"]) != name_key(base[f]["cover_org_name"])]
    v.add("B2 基线已取到的封面主体不变", None, "仅报告，逐例复核", f"{len(wrong_cover)} 份变化", wrong_cover[:20])
    nonstd = [f for f in prof["non_standard"] if f in cur and cur[f].get("report_kind") in {"budget", "final"}]
    v.add("B3 非预决算材料不按预决算处理", not nonstd, f"{len(prof['non_standard'])} 份均非 budget/final",
          f"{len(nonstd)} 份仍判为预决算", nonstd)
    std_changed = [f for f, r in base.items() if r.get("report_kind") == "final" and f not in prof["non_standard"]
                   and f in cur and cur[f].get("report_kind") != "final"]
    v.add("B4 标准决算文种不回退", not std_changed, "0 份", f"{len(std_changed)} 份", std_changed[:20])
    year_conflict = [f for f, r in cur.items() if r.get("report_year") and int(r["report_year"]) != int(f[:4])]
    v.add("B5 年度与目录年份一致", not year_conflict, "0 份冲突", f"{len(year_conflict)} 份", year_conflict[:10])


def _real_facts(row: Dict[str, Any]) -> collections.Counter:
    """可进规则的带编码事实：(表,指标,编码,阶段,期间,金额)。"""
    keys = collections.Counter()
    for item in row.get("facts") or []:
        *identity, status = item.rsplit("|", 1)
        if status not in {"low_confidence", "ambiguous"}:
            keys["|".join(identity)] += 1
    return keys


def _compare_parse(v: Verdicts, base: Dict[str, Dict], cur: Dict[str, Dict], cases: Dict[str, Any]) -> None:
    if not cur:
        v.add("parse", False, "有采集结果", "缺 parse.jsonl.gz")
        return
    errors = [f for f, r in cur.items() if "error" in r]
    v.add("C0 解析无异常", not errors, "0 份报错", f"{len(errors)} 份", errors[:10])
    pairs = [(f, base[f], cur[f]) for f in cur if f in base and "error" not in base[f] and "error" not in cur[f]]

    def total(rows, key):
        return sum(int(r.get(key) or 0) for r in rows)

    b_rows, c_rows = [b for _, b, _ in pairs], [c for _, _, c in pairs]
    v.add("C1 进入规则的金额格", total(c_rows, "admitted") >= total(b_rows, "admitted"), "不低于基线",
          f"{total(c_rows, 'admitted')}（基线 {total(b_rows, 'admitted')}）")
    v.add("C2 待复核金额格", total(c_rows, "pending") <= total(b_rows, "pending"), "不高于基线",
          f"{total(c_rows, 'pending')}（基线 {total(b_rows, 'pending')}）")
    worse = [f for f, b, c in pairs if (c.get("admitted") or 0) < (b.get("admitted") or 0)
             or (c.get("pending") or 0) > (b.get("pending") or 0)]
    v.add("C3 单份文件不变差", not worse, "0 份 admitted 下降或 pending 上升", f"{len(worse)} 份", worse[:20])
    lost: Dict[str, List[str]] = {}
    for f, b, c in pairs:
        missing = _real_facts(b) - _real_facts(c)
        if missing:
            lost[f] = list(missing.elements())[:5]
    v.add("C4 基线可用事实不丢失", not lost, "0 份丢失带编码的可用事实（金额与身份不变）",
          f"{len(lost)} 份", {k: lost[k] for k in list(lost)[:10]})
    blocked = [f for f, r in cur.items() if r.get("fact_count") and (r.get("identity_incomplete") or 0) >= 0.9 * r["fact_count"]]
    v.add("C5 整份事实因身份不完整被挡", not blocked, "0 份", f"{len(blocked)} 份（基线 "
          f"{sum(1 for r in base.values() if r.get('fact_count') and (r.get('identity_incomplete') or 0) >= 0.9 * r['fact_count'])}）", blocked)
    b_time = [b["parse_seconds"] for _, b, _ in pairs if b.get("parse_seconds")]
    c_time = [c["parse_seconds"] for _, _, c in pairs if c.get("parse_seconds")]
    if b_time and c_time:
        ratio = statistics.median(c_time) / max(statistics.median(b_time), 0.01)
        v.add("C6 解析耗时中位数", ratio <= 1.25, "≤ 基线 × 1.25", f"× {ratio:.2f}")
    for case in cases["parse"]:
        row = cur.get(case["file"])
        if not row or "error" in row:
            v.add(f"C7 {case['id']}", False, "有结果", "缺失或报错")
            continue
        unknown_pages = sorted({p for p, code, *_ in row.get("fragments") or [] if str(code).startswith("UNKNOWN")})
        checks = []
        if "admitted_min" in case:
            checks.append(((row.get("admitted") or 0) >= case["admitted_min"], f"admitted {row.get('admitted')} ≥ {case['admitted_min']}"))
        if "pending_max" in case:
            checks.append(((row.get("pending") or 0) <= case["pending_max"], f"pending {row.get('pending')} ≤ {case['pending_max']}"))
        if "identity_incomplete_max" in case:
            checks.append(((row.get("identity_incomplete") or 0) <= case["identity_incomplete_max"],
                           f"identity_incomplete {row.get('identity_incomplete')} ≤ {case['identity_incomplete_max']}"))
        if "no_unknown_pages" in case:
            bad = sorted(set(case["no_unknown_pages"]) & set(unknown_pages))
            checks.append((not bad, f"UNKNOWN 页 {bad or '无'}"))
        v.add(f"C7 {case['id']}", all(ok for ok, _ in checks), case["expect"], "；".join(text for _, text in checks))
    for case in cases["table_identity"]:
        row = cur.get(case["file"])
        codes = {code for page, code, *_ in (row or {}).get("fragments") or [] if page == case["page"]}
        v.add(f"C8 {case['id']}", case["code"] in codes, f"第{case['page']}页 = {case['code']}", ",".join(sorted(codes)) or "无")


def _issue_hits(row: Dict[str, Any], spec: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [i for i in row.get("issues") or [] if (not spec.get("rule_id") or i.get("rule_id") == spec["rule_id"])
            and all(token in (i.get("message") or "") + (i.get("title") or "") for token in spec.get("contains", []))]


def _insufficient_rules(row: Dict[str, Any]) -> Optional[int]:
    """复核原因 rules_insufficient_data 中「有 N 条规则」的 N；没有该原因记 0。"""
    if not row.get("review_reasons") and not row.get("status"):
        return None
    for reason in row.get("review_reasons") or []:
        if reason.get("code") == "rules_insufficient_data":
            match = re.search(r"有\s*(\d+)\s*条规则", reason.get("message") or "")
            return int(match.group(1)) if match else None
    return 0


def _compare_e2e(v: Verdicts, base: Dict[str, Dict], cur: Dict[str, Dict], cases: Dict[str, Any]) -> None:
    if not cur:
        v.add("e2e", False, "有采集结果", "缺 e2e.jsonl.gz")
        return
    for case in cases["e2e"]:
        row = cur.get(case["file"])
        if not row or "error" in row:
            v.add(f"D {case['id']}", False, "全管线跑通", (row or {}).get("error", "缺失"))
            continue
        problems = []
        for spec in case.get("must_not_have", []):
            if _issue_hits(row, spec):
                problems.append(f"仍有 {spec.get('rule_id')} {spec.get('contains')}")
        for spec in case.get("must_have", []):
            if not _issue_hits(row, spec):
                problems.append(f"缺少 {spec.get('rule_id')} {spec.get('contains')}")
        codes = [r["code"] for r in row.get("review_reasons") or []]
        for code in case.get("review_reason_absent", []):
            if code in codes:
                problems.append(f"复核原因仍含 {code}")
        if case.get("rules_insufficient_lower_than_baseline"):
            before = _insufficient_rules(base.get(case["file"]) or {})
            after = _insufficient_rules(row)
            if before is None or after >= before:
                problems.append(f"取数不足规则 {after} 条，未低于基线 {before}")
        if case.get("control"):
            # 对照样本：问题集合与基线逐条比较，差异全部列出由验收人复核。
            b = {(i["rule_id"], i["message"]) for i in (base.get(case["file"]) or {}).get("issues") or []}
            c = {(i["rule_id"], i["message"]) for i in row.get("issues") or []}
            changed = {"消失": sorted(b - c)[:10], "新增": sorted(c - b)[:10]}
            v.add(f"D {case['id']}", None if (b - c or c - b) else True, "与基线一致或差异有合理解释",
                  f"消失 {len(b - c)} 新增 {len(c - b)}", changed)
            continue
        v.add(f"D {case['id']}", not problems, case["expect"], "；".join(problems) or "符合")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    col = sub.add_parser("collect", help="对语料采集验收数据")
    col.add_argument("--corpus", default=str(_DEFAULT_CORPUS))
    col.add_argument("--out", required=True)
    col.add_argument("--cases", default=str(_DEFAULT_CASES))
    col.add_argument("--org-data-dir", default=str(_REPO_ROOT / "data"),
                     help="组织库目录（含 organizations.json）；验收清洗后的组织库时指向其副本")
    col.add_argument("--stages", default="intake,parse,e2e")
    col.add_argument("--jobs", type=int, default=8)
    col.add_argument("--limit", type=int, default=0, help="只取前 N 份（调试用，正式验收不得使用）")
    col.add_argument("--only", help="JSON 文件：只采集其中列出的相对路径（调试用）")
    cmp_ = sub.add_parser("compare", help="比较两次采集并逐条判定")
    cmp_.add_argument("--baseline", required=True)
    cmp_.add_argument("--current", required=True)
    cmp_.add_argument("--cases", default=str(_DEFAULT_CASES))
    cmp_.add_argument("--report")
    args = parser.parse_args(argv)
    return collect(args) if args.command == "collect" else compare(args)


if __name__ == "__main__":
    sys.exit(main())
