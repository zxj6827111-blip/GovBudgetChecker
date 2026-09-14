from __future__ import annotations

import json
from pathlib import Path

import pytest

from api import runtime


def _write_status(job_dir: Path, **payload: object) -> None:
    (job_dir / "status.json").write_text(
        json.dumps({"job_id": job_dir.name, **payload}),
        encoding="utf-8",
    )


def test_find_first_pdf_prefers_status_canonical_name(tmp_path: Path) -> None:
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    original = job_dir / "原始预算.pdf"
    original.write_bytes(b"pdf")
    (job_dir / "annotated.pdf").write_bytes(b"derived")
    _write_status(job_dir, filename=original.name)

    assert runtime.find_first_pdf(job_dir) == original


def test_find_first_pdf_rejects_ambiguous_multi_pdf_job(tmp_path: Path) -> None:
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    (job_dir / "one.pdf").write_bytes(b"one")
    (job_dir / "two.pdf").write_bytes(b"two")
    _write_status(job_dir)

    with pytest.raises(ValueError, match="Multiple PDF"):
        runtime.find_first_pdf(job_dir)


def test_find_first_pdf_rejects_conflicting_metadata_references(tmp_path: Path) -> None:
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    (job_dir / "original.pdf").write_bytes(b"original")
    (job_dir / "annotated.pdf").write_bytes(b"derived")
    _write_status(
        job_dir,
        filename="original.pdf",
        saved_path="job/annotated.pdf",
    )

    with pytest.raises(ValueError, match="independently referenced"):
        runtime.find_first_pdf(job_dir)
