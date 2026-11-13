"""Register revised originals/create drafts only, in an isolated schema or explicit SQL stub."""
from __future__ import annotations
import argparse
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
import urllib.parse
from uuid import uuid4

ROOT=Path("/Volumes/workSSD/software development/GovBudgetChecker")
BASE=Path(__file__).resolve().parent.parent
OUT=Path(__file__).resolve().parent
if (OUT/"verification.json").exists():
    OUT=OUT/("rerun-"+uuid4().hex[:12]);OUT.mkdir()
sys.path.insert(0,str(ROOT))
if Path("/private/tmp/annual-v3/xlsdeps").is_dir():sys.path.append("/private/tmp/annual-v3/xlsdeps")
from src.services import annual_templates as service

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def git(*args):return subprocess.check_output(["git",*args],cwd=ROOT)
def dump(name,value):(OUT/name).write_text(json.dumps(value,ensure_ascii=False,indent=2,default=str),encoding="utf-8")

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


async def connect_postgres():
    import asyncpg
    from src.db.migrations import MIGRATIONS
    from src.db.annual_template_migration import MIGRATION
    config=json.loads(Path("/private/tmp/annual-v3/runtime/env.json").read_text())
    parsed=urllib.parse.urlparse(config["DATABASE_URL"])
    assert parsed.hostname in ("127.0.0.1","localhost") and parsed.port==25462
    assert parsed.path.strip("/")=="gbc_annual_test"
    info={"host":parsed.hostname,"port":parsed.port,"database":"gbc_annual_test"}
    try:
        conn=await asyncpg.connect(config["DATABASE_URL"],timeout=5)
    except Exception as exc:
        dump("database-access.json",{**info,"connected":False,"exception_type":type(exc).__name__})
        return None,{**info,"connected":False,"exception_type":type(exc).__name__}
    assert await conn.fetchval("SELECT current_database()") == "gbc_annual_test"
    schema="annual_revised_archive_"+uuid4().hex[:16]
    await conn.execute('CREATE SCHEMA "'+schema+'"')
    await conn.execute('SET search_path TO "'+schema+'"')
    assert await conn.fetchval("SELECT current_schema()") == schema
    # Apply only the project's real DDL needed by these metadata services, all in the brand-new schema.
    # Exclude document-writing decisions because those refer to unrelated fiscal analysis tables.
    for sql in MIGRATION["sql"][:5]:await conn.execute(sql)
    for migration in MIGRATIONS:
        if migration["id"].startswith(("2026-10-28_0043_","2026-10-28_0044_")):
            for sql in migration["sql"]:await conn.execute(sql)
    info.update(connected=True,schema=schema,ddl_scope="annual metadata tables from 0042(first 5 statements),0043,0044")
    dump("database-access.json",info)
    return conn,info


def cover_year(kind,raw):
    lines=[line.strip() for line in raw.splitlines() if line.strip()][:8]
    pattern=(r"(?<!\d)(20\d{2})年度(?:部门|单位)?决算(?:$|[表情])" if kind=="final"
             else r"(?<!\d)(20\d{2})年(?:区级)?(?:单位|部门)预算")
    hits=[(line,int(match.group(1))) for line in lines if (match:=re.search(pattern,line))]
    assert hits and len({year for _,year in hits})==1,(kind,lines)
    return hits[0][1],hits[0][0]


async def main(mode):
    manifest=json.loads((BASE/"archive-manifest.json").read_text())
    imported={r["relative_path"]:r for r in json.loads((BASE/"import-compatibility.json").read_text())["rows"]}
    assert sha(manifest["archive"])==manifest["archive_sha256"]
    code_files=("src/services/annual_templates.py","src/services/disclosure_template_import.py")
    code_hashes={name:sha(ROOT/name) for name in code_files}
    before_diff=hashlib.sha256(git("diff","--binary")).hexdigest()
    before_head=git("rev-parse","HEAD").decode().strip()
    flags={k:os.environ.get(k) for k in ("GBC_TEMPLATE_SCOPE","GBC_BUDGET_TEMPLATE_SCOPE")}
    if mode=="postgres":
        conn,db_info=await connect_postgres()
        if conn is None:
            print(json.dumps({"mode":"postgres_access_failed","exception_type":db_info["exception_type"],"no_database_validation_claimed":True}));return 2
    else:conn=MemorySQL();db_info={"connected":False,"mode":"explicit_SQL_stub"}
    stage=Path(tempfile.mkdtemp(prefix="gbc-revised-rar-service-"))
    old_storage=os.environ.get("GBC_TEMPLATE_STORAGE");os.environ["GBC_TEMPLATE_STORAGE"]=str(stage/"templates")
    sources=[];drafts=[];groups={}
    try:
        for item in manifest["files"]:
            prior=imported[item["relative_path"]];source=Path(prior["stable_path"])
            assert sha(source)==item["sha256"]
            original=Path(prior["original_source"])
            assert sha(original)==prior["original_source_sha256"]
            kind=prior["kind"];role=prior["role"]
            raw=(Path(prior["audit_directory"])/"extracted.txt").read_text()
            year,title=cover_year(kind,raw)
            assert year==prior["source_year"] and year in (2023,2024,2025)
            metadata={"report_kind":kind,"material_type":role,"source_report_year":year,
                      "issuer":"来源待核（用户修订稿）","release_reference":"","issue_date":"",
                      "description":"用户修订包的隔离service技术验证；非未经修改官方原件，不审核、不发布、不绑定"}
            result=await service.register_source(conn,path=source,metadata=metadata,actor="isolated-revised-service-audit")
            assert result["extraction_status"]=="needs_visual_verification" and result["sha256"]==item["sha256"]
            directory=service.source_dir(result["id"]);fresh=(directory/"extracted.txt").read_text()
            assert cover_year(kind,fresh)==(year,title)
            sources.append({"relative_path":item["relative_path"],"stable_path":str(source),"original_sha256":prior["original_source_sha256"],
                            "revised_sha256":item["sha256"],"source_id":result["id"],"kind":kind,"role":role,"year":year,
                            "year_evidence":{"source":"extracted_cover_text","text":title},"metadata":metadata,
                            "extraction_status":result["extraction_status"],"converter":result["converter"],
                            "temporary_storage":str(directory),"source_receipt":service.source_receipt(result["id"])})
            assert role not in groups.setdefault((kind,year),{});groups[(kind,year)][role]=result["id"]
        assert len(sources)==12 and len(groups)==6
        for (kind,year),ids in sorted(groups.items()):
            assert set(ids)=={"unit","department"}
            draft=await service.create_draft(conn,{"unit_source_id":ids["unit"],"department_source_id":ids["department"]},"isolated-revised-service-audit")
            data=draft["data"]
            assert draft["status"]=="draft" and draft["revision"]==1
            assert data["source_pair_consistent"] is False and data["config"]["supported_pair"] is True
            assert data["source_report_year"]==year and data["report_kind"]==kind
            drafts.append({"kind":kind,"year":year,"draft_id":draft["id"],"status":draft["status"],"supported_pair":data["config"]["supported_pair"],
                           "source_pair_consistent":data["source_pair_consistent"],"pair_diff_count":len(data["pair_diff"]),
                           "prompt_candidate_count":len(data["config"]["prompts"]),"draft":draft})
        if mode=="postgres":
            counts={table:await conn.fetchval("SELECT count(*) FROM "+table) for table in
                    ("disclosure_template_sources","disclosure_template_drafts","disclosure_template_packages","disclosure_template_binding_revisions")}
            assert counts=={"disclosure_template_sources":12,"disclosure_template_drafts":6,"disclosure_template_packages":0,"disclosure_template_binding_revisions":0}
            audit=[dict(r) for r in await conn.fetch("SELECT action,count(*) AS count FROM disclosure_template_audit GROUP BY action ORDER BY action")]
            assert {r["action"]:r["count"] for r in audit}=={"import":12,"draft":6}
        else:
            counts={"in_memory_sources":len(conn.sources),"in_memory_drafts":len(conn.drafts),"packages":0,"bindings":0}
            audit=conn.audit;assert Counter(r["action"] for r in audit)=={"import":12,"draft":6}
        assert all(sha(Path(imported[f["relative_path"]]["stable_path"]))==f["sha256"] for f in manifest["files"])
        assert code_hashes=={name:sha(ROOT/name) for name in code_files}
        assert flags=={k:os.environ.get(k) for k in flags}
        dump("sources.json",sources);dump("drafts.json",drafts);dump("audit.json",audit)
        summary={"mode":"production_service_with_isolated_postgresql" if mode=="postgres" else "production_service_with_in_memory_SQL_stub",
                 "head":before_head,"algorithm":service.ALGORITHM,"database":db_info,"table_counts":counts,
                 "source_count":12,"draft_count":6,"supported_pairs":6,"source_pair_consistent_count":0,
                 "source_files_unchanged":True,"code_files_unchanged":True,"feature_flags_unchanged":True,
                 "tracked_dirty_diff_unchanged":before_diff==hashlib.sha256(git("diff","--binary")).hexdigest(),
                 "tracked_diff_sha256_before":before_diff,"storage":str(stage/"templates"),"code_sha256":code_hashes,
                 "limits":["No source batch approval, visual review, review, publish, binding, capability enablement, HTTP or browser operation",
                           "All original and revised source bytes preserved; user revisions are not represented as untouched official originals"],
                 "pairs":[{k:d[k] for k in ("kind","year","supported_pair","source_pair_consistent","pair_diff_count","prompt_candidate_count")} for d in drafts]}
        if mode!="postgres":summary["limits"].append("SQL and database constraints were not validated; only SQL storage was stubbed")
        dump("verification.json",summary)
        dump("checksums.json",{f.name:sha(f) for f in sorted(OUT.iterdir()) if f.is_file() and f.name!="checksums.json"})
        print(json.dumps({"mode":summary["mode"],"registered_sources":12,"created_drafts":6,"supported_pairs":6,
                          "source_pair_consistent_count":0,"database_schema":db_info.get("schema"),"summary":str(OUT/"verification.json")},ensure_ascii=False))
        return 0
    finally:
        if old_storage is None:os.environ.pop("GBC_TEMPLATE_STORAGE",None)
        else:os.environ["GBC_TEMPLATE_STORAGE"]=old_storage
        if mode=="postgres":await conn.close()

if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("--mode",choices=("postgres","stub"),required=True)
    raise SystemExit(asyncio.run(main(parser.parse_args().mode)))
