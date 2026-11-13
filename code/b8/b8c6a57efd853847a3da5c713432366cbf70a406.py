"""原始/有效快照追加写入；数据库恢复时复用已保存的同一候选。"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path

from src.engine.fact_context import FactContext, FACT_CONTEXT_VERSION

SNAPSHOT_VERSION = "analysis-fact-snapshot-v1"


def canonical(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


@dataclass(frozen=True)
class AnalysisFactSnapshot:
    """序列化后冻结，调用者随后修改 Python 对象不会改变候选快照。"""

    payload_json: str

    @classmethod
    def create(cls, *, revision_id, raw, effective, versions):
        if not isinstance(revision_id, str) or not revision_id.startswith("rev-"):
            raise ValueError("public revision_id must be a revision string")
        if raw is None or effective is None:
            raise ValueError("new snapshot requires raw and effective contexts")
        if (
            raw.context_version != FACT_CONTEXT_VERSION
            or effective.context_version != FACT_CONTEXT_VERSION
        ):
            raise ValueError("historical v1 contexts are read-only")
        if raw.file_hash != effective.file_hash:
            raise ValueError("correction cannot change original file")
        for context in (raw, effective):
            if context.compute_version_token() != context.version_token:
                raise ValueError("mutated fact context must be sealed before publication")
        return cls(
            canonical(
                {
                    "snapshot_version": SNAPSHOT_VERSION,
                    "revision_id": revision_id,
                    "source_file_hash": raw.file_hash,
                    "parse_run_uid": raw.parse_run_uid,
                    "raw_digest": raw.version_token,
                    "effective_digest": effective.version_token,
                    "raw_context": raw.to_dict(),
                    "effective_context": effective.to_dict(),
                    "versions": dict(versions),
                }
            )
        )

    @classmethod
    def from_dict(cls, data):
        if data.get("snapshot_version") != SNAPSHOT_VERSION:
            raise ValueError("unsupported fact snapshot version")
        raw = FactContext.from_dict(data["raw_context"])
        effective = FactContext.from_dict(data["effective_context"])
        result = cls.create(
            revision_id=data["revision_id"], raw=raw, effective=effective, versions=data["versions"]
        )
        if result.to_dict() != data:
            raise ValueError("snapshot identity or digests do not match contexts")
        return result

    def to_dict(self):
        return json.loads(self.payload_json)

    def summary(self):
        data = self.to_dict()
        return {
            key: data[key]
            for key in (
                "snapshot_version",
                "revision_id",
                "source_file_hash",
                "parse_run_uid",
                "raw_digest",
                "effective_digest",
                "versions",
            )
        } | {"compatibility": "v2"}

    @property
    def digest(self):
        return hashlib.sha256(self.payload_json.encode()).hexdigest()


def stage_candidate(job_dir, payload, snapshot):
    """先落完整候选，再尝试数据库发布；故障续跑不能重新物化。"""
    target = Path(job_dir) / "pending_fact_publication.json"
    data = {"payload": payload, "snapshot": snapshot.to_dict(), "snapshot_sha256": snapshot.digest}
    temp = target.with_suffix(".tmp")
    temp.write_text(canonical(data), encoding="utf-8")
    temp.replace(target)
    return target


def read_candidate(path):
    data = json.loads(Path(path).read_text())
    snapshot = AnalysisFactSnapshot.from_dict(data["snapshot"])
    if snapshot.digest != data["snapshot_sha256"]:
        raise ValueError("candidate snapshot hash mismatch")
    meta = (data["payload"].get("result") or {}).get("meta") or {}
    if meta.get("fact_snapshot") != snapshot.summary():
        raise ValueError("result does not bind the staged effective snapshot")
    return data["payload"], snapshot


async def persist_fact_snapshot(conn, job_db_id, snapshot):
    """调用者必须放在 analysis_results 的同一个事务中。"""
    data = snapshot.to_dict()
    generation = await conn.fetchval(
        "SELECT analysis_revision FROM analysis_jobs WHERE id=$1", job_db_id
    )
    if isinstance(generation, bool) or not isinstance(generation, int):
        raise ValueError("database generation must be an integer, not revision_id")
    parse_run_id = await conn.fetchval(
        "SELECT id FROM parse_runs WHERE parse_run_uid=$1", data["parse_run_uid"]
    )
    if parse_run_id is None:
        raise ValueError("snapshot publication requires its persisted parse run")
    await conn.execute(
        """INSERT INTO analysis_fact_snapshots
        (job_id, revision_id, analysis_generation, parse_run_id, parse_run_uid, source_file_hash,
         raw_digest, effective_digest, snapshot_sha256, raw_context, effective_context, versions)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10::jsonb,$11::jsonb,$12::jsonb)
        ON CONFLICT (job_id,revision_id) DO NOTHING""",
        job_db_id,
        data["revision_id"],
        generation,
        parse_run_id,
        data["parse_run_uid"],
        data["source_file_hash"],
        data["raw_digest"],
        data["effective_digest"],
        snapshot.digest,
        canonical(data["raw_context"]),
        canonical(data["effective_context"]),
        canonical(data["versions"]),
    )
    saved = await conn.fetchval(
        "SELECT snapshot_sha256 FROM analysis_fact_snapshots WHERE job_id=$1 AND revision_id=$2",
        job_db_id,
        data["revision_id"],
    )
    if saved != snapshot.digest:
        raise ValueError("immutable revision already exists with another snapshot")
