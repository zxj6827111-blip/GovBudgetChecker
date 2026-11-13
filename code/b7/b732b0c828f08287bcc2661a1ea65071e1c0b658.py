"""Run production register_source/create_draft with in-memory SQL storage only.
No network, database, review, publish, binding or feature flag mutation.
"""
from __future__ import annotations
import asyncio
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from uuid import uuid4

ROOT = Path("/Volumes/workSSD/software development/GovBudgetChecker")
BASE = Path(__file__).resolve().parent.parent
OUT = Path(__file__).resolve().parent
if (OUT / "verification.json").exists():
    OUT = OUT / ("rerun-" + uuid4().hex[:12])
    OUT.mkdir()
sys.path.insert(0, str(ROOT))
deps = Path("/private/tmp/annual-v3/xlsdeps")
if deps.is_dir(): sys.path.append(str(deps))
from src.services import annual_templates as service


def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def git_bytes(*args): return subprocess.check_output(["git", *args], cwd=ROOT)


def dump(name, value):
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


class MemorySQL:
    """Only the SQL interface used by source registration and draft creation.
    This intentionally does not simulate PostgreSQL constraints or transaction rollback.
    Unknown SQL raises rather than accidentally touching a database.
    """
    def __init__(self):
        self.sources = {}; self.drafts = {}; self.audit = []; self.operations = Counter()
    def transaction(self): return self
    async def __aenter__(self): return self
    async def __aexit__(self, *args): return False
    async def fetch(self, query, *args):
        assert query.startswith("SELECT id FROM disclosure_template_sources WHERE source_sha256=")
        self.operations["lookup_duplicate_source"] += 1
        return [{"id": row["id"]} for row in self.sources.values() if row["source_sha256"] == args[0]]
    async def fetchrow(self, query, *args):
        if query.startswith("SELECT * FROM disclosure_template_sources WHERE id="):
            self.operations["read_source"] += 1
            return self.sources.get(str(args[0]))
        if query.startswith("SELECT * FROM disclosure_template_drafts WHERE id="):
            self.operations["read_draft"] += 1
            return self.drafts.get(str(args[0]))
        raise AssertionError("Unexpected SQL read: " + query)
    async def execute(self, query, *args):
        if query.startswith("INSERT INTO disclosure_template_sources("):
            keys = ["id", "source_sha256", "storage_key", "report_kind", "material_type", "source_report_year",
                    "issuer", "issue_date", "source_description", "extraction_status", "converter", "extraction_error", "actor"]
            self.sources[str(args[0])] = dict(zip(keys, args))
            self.operations["insert_source"] += 1
        elif query.startswith("INSERT INTO disclosure_template_drafts("):
            self.drafts[str(args[0])] = {"id": args[0], "revision": 1, "status": "draft", "data": args[1], "actor": args[2]}
            self.operations["insert_draft"] += 1
        elif query.startswith("INSERT INTO disclosure_template_audit("):
            self.audit.append({"id": str(args[0]), "action": args[1], "actor": args[2], "reason": args[3],
                               "before_state": json.loads(args[4]), "after_state": json.loads(args[5])})
            self.operations["insert_audit"] += 1
        else:
            raise AssertionError("Unexpected SQL mutation: " + query)


def verified_cover_year(kind, raw):
    # Identify explicit report-year cover text, never infer the year from its folder.
    lines = [line.strip() for line in raw.splitlines() if line.strip()][:8]
    pattern = (r"(?<!\d)(20\d{2})年度(?:部门|单位)?决算(?:$|[表情])" if kind == "final"
               else r"(?<!\d)(20\d{2})年区级(?:单位|部门)预算")
    matches = [(line, re.search(pattern, line)) for line in lines]
    hits = [(line, int(match.group(1))) for line, match in matches if match]
    assert hits and len({year for _, year in hits}) == 1, (kind, lines)
    return hits[0][1], hits[0][0]


async def main():
    manifest_path = BASE / "archive-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    previous_import = json.loads((BASE / "import-compatibility.json").read_text())
    imported = {r["relative_path"]: r for r in previous_import["rows"]}
    assert sha(manifest["archive"]) == manifest["archive_sha256"]
    before_diff = hashlib.sha256(git_bytes("diff", "--binary")).hexdigest()
    before_head = git_bytes("rev-parse", "HEAD").decode().strip()
    before_flags = {k: os.environ.get(k) for k in ("GBC_TEMPLATE_SCOPE", "GBC_BUDGET_TEMPLATE_SCOPE")}
    stage = Path(tempfile.mkdtemp(prefix="gbc-rar-service-"))
    old_storage = os.environ.get("GBC_TEMPLATE_STORAGE")
    os.environ["GBC_TEMPLATE_STORAGE"] = str(stage / "templates")
    conn = MemorySQL(); sources = []; groups = {}; drafts = []
    try:
        for item in manifest["files"]:
            source = Path(item["path"])
            assert sha(source) == item["sha256"]
            prior = imported[item["relative_path"]]
            raw = (Path(prior["audit_directory"]) / "extracted.txt").read_text()
            kind = prior["candidate_kind"]; role = prior["candidate_role"]
            year, cover = verified_cover_year(kind, raw)
            assert year in (2023, 2024, 2025)
            metadata = {"report_kind": kind, "material_type": role, "source_report_year": year,
                        "issuer": "来源待核", "issue_date": "", "release_reference": "",
                        "description": "用户压缩包的隔离服务技术验证；来源未获准，不审核、不发布、不绑定"}
            result = await service.register_source(conn, path=source, metadata=metadata, actor="isolated-service-compatibility")
            assert result["sha256"] == item["sha256"] and result["extraction_status"] == "needs_visual_verification"
            directory = service.source_dir(result["id"])
            fresh_raw = (directory / "extracted.txt").read_text()
            assert verified_cover_year(kind, fresh_raw) == (year, cover)
            receipt = service.source_receipt(result["id"])
            entry = {"relative_path": item["relative_path"], "original_sha256": item["sha256"],
                     "source_id": result["id"], "kind": kind, "role": role, "source_report_year": year,
                     "year_evidence": {"source": "extracted_cover_text", "text": cover},
                     "extraction_status": result["extraction_status"], "converter": result["converter"],
                     "text_characters": len(fresh_raw), "fresh_text_sha256": sha(directory / "extracted.txt"),
                     "metadata": metadata, "source_receipt": receipt, "temporary_storage": str(directory)}
            sources.append(entry)
            assert role not in groups.setdefault((kind, year), {})
            groups[(kind, year)][role] = result["id"]
        assert len(sources) == 12 and len(groups) == 6
        for (kind, year), ids in sorted(groups.items()):
            assert set(ids) == {"unit", "department"}
            draft = await service.create_draft(conn, {"unit_source_id": ids["unit"], "department_source_id": ids["department"]},
                                               "isolated-service-compatibility")
            content = draft["data"]
            assert draft["status"] == "draft" and draft["revision"] == 1
            assert content["source_pair_consistent"] is False
            assert content["report_kind"] == kind and content["source_report_year"] == year
            assert content["config"]["supported_pair"] is True
            drafts.append({"kind": kind, "source_report_year": year, "draft_id": draft["id"], "status": draft["status"],
                           "revision": draft["revision"], "supported_pair": content["config"]["supported_pair"],
                           "source_pair_consistent": content["source_pair_consistent"], "pair_diff_count": len(content["pair_diff"]),
                           "prompt_candidate_count": len(content["config"]["prompts"]), "draft": draft})
        assert Counter(row["action"] for row in conn.audit) == {"import": 12, "draft": 6}
        assert all(sha(row["path"]) == row["sha256"] for row in manifest["files"])
        assert before_diff == hashlib.sha256(git_bytes("diff", "--binary")).hexdigest()
        assert before_head == git_bytes("rev-parse", "HEAD").decode().strip()
        assert before_flags == {k: os.environ.get(k) for k in before_flags}
        dump("sources.json", sources); dump("drafts.json", drafts); dump("audit.json", conn.audit)
        dump("verification.json", {"mode": "production_service_with_in_memory_sql_stub", "head": before_head,
             "algorithm": service.ALGORITHM, "source_count": len(sources), "draft_count": len(drafts),
             "supported_pairs": sum(d["supported_pair"] for d in drafts), "source_pair_consistent_count": 0,
             "source_files_unchanged": True, "tracked_dirty_diff_unchanged": True, "feature_flags_unchanged": True,
             "tracked_diff_sha256": before_diff, "storage": str(stage / "templates"), "operations": dict(conn.operations),
             "postgresql_attempt": {"host": "127.0.0.1", "port": 25462, "connected": False, "exception_type": "PermissionError"},
             "limitations": ["SQL execution and database constraints not validated; memory stub substituted only SQL storage",
                             "No HTTP/API or browser operation", "No visual review, source-batch approval, review, publish, binding or capability enablement"],
             "code_sha256": {name: sha(ROOT / name) for name in ("src/services/annual_templates.py", "src/services/disclosure_template_import.py")},
             "pairs": [{k: d[k] for k in ("kind", "source_report_year", "supported_pair", "source_pair_consistent", "pair_diff_count", "prompt_candidate_count")} for d in drafts]})
        dump("checksums.json", {p.name: sha(p) for p in sorted(OUT.iterdir()) if p.is_file() and p.name != "checksums.json"})
        print(json.dumps({"mode": "SQL_stub_only", "registered_sources": len(sources), "created_drafts": len(drafts),
                          "supported_pairs": sum(d["supported_pair"] for d in drafts), "source_pair_consistent_count": 0,
                          "summary": str(OUT / "verification.json")}, ensure_ascii=False))
    finally:
        if old_storage is None: os.environ.pop("GBC_TEMPLATE_STORAGE", None)
        else: os.environ["GBC_TEMPLATE_STORAGE"] = old_storage


if __name__ == "__main__": asyncio.run(main())
