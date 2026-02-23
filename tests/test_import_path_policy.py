from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCAN_ROOTS = ("api", "src", "scripts", "tests")
LEGACY_IMPORT_ROOTS = {"services", "engine", "providers", "schemas"}


def _iter_python_files() -> list[Path]:
    files: list[Path] = []
    for root in SCAN_ROOTS:
        base = REPO_ROOT / root
        if not base.exists():
            continue
        for path in base.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            if path == Path(__file__).resolve():
                continue
            files.append(path)
    return files


def _collect_legacy_import_violations(path: Path) -> list[str]:
    violations: list[str] = []
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    rel_path = path.relative_to(REPO_ROOT).as_posix()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in LEGACY_IMPORT_ROOTS:
                    violations.append(f"{rel_path}:{node.lineno} import {alias.name}")
        elif isinstance(node, ast.ImportFrom) and node.module:
            root = node.module.split(".")[0]
            if root in LEGACY_IMPORT_ROOTS:
                violations.append(f"{rel_path}:{node.lineno} from {node.module} import ...")

    return violations


def test_no_legacy_import_paths_in_repo_python_code() -> None:
    violations: list[str] = []
    for path in _iter_python_files():
        violations.extend(_collect_legacy_import_violations(path))

    assert not violations, "Legacy import paths were found:\n" + "\n".join(sorted(violations))


def test_legacy_package_init_files_removed() -> None:
    for legacy_root in sorted(LEGACY_IMPORT_ROOTS):
        assert not (REPO_ROOT / legacy_root / "__init__.py").exists()


@pytest.mark.parametrize(
    "legacy_module",
    [
        "services.ai_client",
        "engine.pipeline",
        "providers.base",
        "schemas.issues",
    ],
)
def test_legacy_modules_are_no_longer_importable(legacy_module: str) -> None:
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(legacy_module)
