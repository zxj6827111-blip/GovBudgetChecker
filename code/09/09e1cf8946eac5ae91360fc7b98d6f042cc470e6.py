#!/usr/bin/env python3
"""Prepare empty independent intake slots, never generate human frozen truth."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import itertools
import json
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "docs/acceptance_templates"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def prepare(destination: Path, known_roots: list[Path]) -> dict:
    """Snapshot code/exclusions and create pending worksheets for a custodian."""
    destination = destination.resolve()
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("output directory must be empty; preserve earlier preparation records")
    destination.mkdir(parents=True, exist_ok=True)
    excluded: dict[str, list[str]] = {}
    for directory in known_roots:
        for path in sorted(directory.rglob("*.pdf")) if directory.exists() else []:
            if path.is_file() and destination not in path.parents:
                excluded.setdefault(sha(path), []).append(str(path.resolve()))
    exclusion = {
        "scope": "known_development_materials_and_outputs_do_not_relabel_as_blind",
        "sha256_groups": excluded,
        "near_duplicate_review": "pending_independent_human_review",
    }
    write(destination / "development_exclusions.json", exclusion)

    names = (
        subprocess.check_output(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], cwd=ROOT
        )
        .decode()
        .split("\0")
    )
    extensions = {
        ".py",
        ".ts",
        ".tsx",
        ".js",
        ".mjs",
        ".css",
        ".json",
        ".yaml",
        ".yml",
        ".toml",
        ".lock",
        ".txt",
        ".sh",
    }
    paths = [
        ROOT / name
        for name in sorted(set(names))
        if name
        and not name.startswith(("outputs/", ".mimosa/", "corpus/", "samples/", "docs/"))
        and (
            Path(name).suffix in extensions
            or Path(name).name.startswith("Dockerfile")
            or name in {"AGENTS.md", ".env.example", ".dockerignore"}
        )
    ]
    code = {str(path.relative_to(ROOT)): sha(path) for path in paths if path.is_file()}
    write(destination / "code_manifest.json", code)
    shutil.copytree(TEMPLATES, destination / "templates", dirs_exist_ok=True)

    slots = []
    for split, document, organization, source in itertools.product(
        ("development", "blind"), ("budget", "final"), ("department", "unit"), ("native", "scanned")
    ):
        for number in range(1, 5):
            slot_id = f"{split}-{document}-{organization}-{source}-{number:02}"
            slot = dict(
                slot_id=slot_id,
                status="pending_original_and_human_review",
                split=split,
                document_type=document,
                organization_type=organization,
                source_type=source,
                actual_doc_id=None,
                source_url=None,
                source_sha256=None,
                organization=None,
                year=None,
                reviewer=None,
            )
            slots.append(slot)
            directory = destination / "intake" / split / slot_id
            directory.mkdir(parents=True)
            for name in ("golden", "label", "scan_attestations"):
                shutil.copyfile(
                    TEMPLATES / f"{name}.template.json", directory / f"{name}.pending.json"
                )
            write(directory / "intake.pending.json", slot)
    write(destination / "slots.pending.json", {"scope": "empty_slots_not_samples", "slots": slots})
    write(
        destination / "dataset.pending.json", {"schema_version": "zc07.dataset.v1", "documents": []}
    )
    shutil.copyfile(TEMPLATES / "e2e.template.json", destination / "e2e.pending.json")
    freeze = json.loads((TEMPLATES / "freeze_record.template.json").read_text())
    freeze["source_snapshot_sha256"] = sha(destination / "code_manifest.json")
    model = ROOT / "models/MANIFEST.sha256"
    freeze["model_manifest_sha256"] = sha(model) if model.is_file() else None
    freeze["evaluator_sha256"] = sha(ROOT / "scripts/run_zc07_acceptance.py")
    write(destination / "freeze_record.pending.json", freeze)
    receipt = dict(
        schema_version="gbc.independent-intake-preparation.v1",
        status="prepared_pending_independent_humans_and_materials",
        prepared_at=datetime.now(timezone.utc).isoformat(),
        git_head=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        code_manifest_sha256=sha(destination / "code_manifest.json"),
        exclusion_manifest_sha256=sha(destination / "development_exclusions.json"),
        required_documents={"development": 32, "blind": 32},
        required_blind_defect_groups=100,
        actual_frozen_documents=0,
        actual_human_defect_groups=0,
        business_metrics=None,
        slot_count=len(slots),
    )
    write(destination / "preparation_receipt.json", receipt)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--known-development-root", type=Path, action="append")
    args = parser.parse_args()
    roots = args.known_development_root or [ROOT / "samples", ROOT / "corpus", ROOT / "outputs"]
    receipt = prepare(args.output, roots)
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
