from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.reprocess_uploads import (
    _evaluate_gate,
    _load_job_ids,
    _prepare_copy,
    _resolve_output_dir,
)


def test_reprocess_output_must_be_outside_source_and_new(tmp_path: Path) -> None:
    source = tmp_path / "uploads"
    source.mkdir()
    with pytest.raises(ValueError):
        _resolve_output_dir(source, str(source))
    with pytest.raises(ValueError):
        _resolve_output_dir(source, str(source / "nested"))

    existing = tmp_path / "existing"
    existing.mkdir()
    with pytest.raises(ValueError):
        _resolve_output_dir(source, str(existing))


def test_reprocess_manifest_accepts_array_and_object(tmp_path: Path) -> None:
    array_manifest = tmp_path / "array.json"
    array_manifest.write_text(json.dumps(["job-a", "job-b"]), encoding="utf-8")
    assert _load_job_ids(str(array_manifest)) == {"job-a", "job-b"}

    object_manifest = tmp_path / "object.json"
    object_manifest.write_text(json.dumps({"job_ids": ["job-a"]}), encoding="utf-8")
    assert _load_job_ids(str(object_manifest)) == {"job-a"}


def test_reprocess_manifest_rejects_non_string_entries(tmp_path: Path) -> None:
    manifest = tmp_path / "bad.json"
    manifest.write_text(json.dumps({"job_ids": ["job-a", ""]}), encoding="utf-8")
    with pytest.raises(ValueError):
        _load_job_ids(str(manifest))


def test_offline_reprocess_gate_never_claims_database_identity_is_verified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _PassingCheck:
        def to_dict(self):
            return {"name": "fixture", "passed": True, "detail": "ok"}

    monkeypatch.setattr(
        "scripts.check_replay_thresholds.evaluate",
        lambda report: [_PassingCheck()],
    )
    checks = _evaluate_gate(
        {
            "mode": "offline_reprocess",
            "reprocess": {
                "candidate_count": 2,
                "prepared_count": 2,
                "preparation_failures": [],
                "run_failures": [],
                "database_identity_revalidated": False,
            },
        }
    )

    assert {item["name"] for item in checks} == {
        "fixture",
        "reprocess_execution",
        "database_identity_revalidation",
    }
    assert next(item for item in checks if item["name"] == "reprocess_execution")["passed"]
    assert not next(
        item for item in checks if item["name"] == "database_identity_revalidation"
    )["passed"]


def test_reprocess_uses_status_named_pdf_when_job_has_annotated_derivative(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "uploads"
    source_dir = source_root / "job-a"
    source_dir.mkdir(parents=True)
    original_name = "原始预算.pdf"
    (source_dir / original_name).write_bytes(b"original")
    (source_dir / "annotated.pdf").write_bytes(b"derived")
    (source_dir / "status.json").write_text(
        json.dumps(
            {
                "job_id": "job-a",
                "status": "done",
                "filename": original_name,
            }
        ),
        encoding="utf-8",
    )
    output_root = tmp_path / "reprocess"
    output_root.mkdir()

    item = _prepare_copy(
        source_dir,
        output_root,
        source_root=source_root,
        correct_metadata=False,
    )

    assert item["canonical_pdf_selection"] == "status_filename"
    assert item["ignored_pdf_names"] == ["annotated.pdf"]
    assert (output_root / "job-a" / original_name).is_file()
    assert not (output_root / "job-a" / "annotated.pdf").exists()
