"""Tests validating the sample manifest for GovBudgetChecker."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any, cast

import pytest

import yaml
from api.config import DEFAULT_RULES_FILE_STR

MANIFEST_PATH = Path("samples/manifest.yaml")
EXPECTED_TABLES = {
    "收入支出决算总表",
    "财政拨款收支决算总表",
    "一般公共预算财政拨款支出决算明细表",
    "一般公共预算财政拨款基本支出决算表",
    "政府性基金预算财政拨款支出决算表",
    "国有资本经营预算财政拨款支出决算表",
    "“三公”经费支出决算表",
    "机关运行经费支出决算表",
    "政府采购情况表",
}


@pytest.fixture(scope="module")
def manifest() -> dict[str, Any]:
    """Load and return the manifest document."""

    with MANIFEST_PATH.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
        return cast(dict[str, Any], data)


def _iter_samples(data: dict[str, Any], sample_type: str) -> Iterable[dict[str, Any]]:
    return (
        sample
        for sample in data.get("samples", [])
        if sample.get("type") == sample_type
    )


def test_manifest_meta_defaults(manifest: dict[str, Any]) -> None:
    """The manifest should align with default configuration."""

    meta = manifest.get("meta", {})
    assert meta.get("rules_file") == DEFAULT_RULES_FILE_STR


def test_template_sample_tables(manifest: dict[str, Any]) -> None:
    """Templates must enumerate the full set of reference tables."""

    templates = list(_iter_samples(manifest, "template"))
    assert templates, "At least one template sample must be defined."

    for template in templates:
        expect = template.get("expect", {})
        structure = expect.get("structure_check", {})
        tables = set(structure.get("must_have_tables", []))
        assert tables == EXPECTED_TABLES
        assert structure.get("min_tables") == len(EXPECTED_TABLES)
        rules_not = expect.get("rules_should_not_trigger", [])
        assert rules_not, "Template samples must list rules that stay inactive."
        assert isinstance(rules_not, list)


def test_good_sample_expectations(manifest: dict[str, Any]) -> None:
    """Good samples should only list non-triggering rules and required tables."""

    goods = list(_iter_samples(manifest, "good"))
    assert goods, "At least one good sample must be defined."

    for sample in goods:
        expect = sample.get("expect", {})
        assert "rules_should_trigger" not in expect
        rules_not = expect.get("rules_should_not_trigger", [])
        assert rules_not, "Good samples must enumerate rules that remain silent."
        assert set(expect.get("must_find_tables", [])), "Good samples must list required tables."


def test_bad_sample_expectations(manifest: dict[str, Any]) -> None:
    """Bad samples should declare triggering rules with minimum hits."""

    bad_samples = list(_iter_samples(manifest, "bad"))
    assert bad_samples, "At least one bad sample must be defined."

    for sample in bad_samples:
        expect = sample.get("expect", {})
        triggers = expect.get("rules_should_trigger", [])
        assert triggers, "Bad samples must list triggering rules."
        for rule in triggers:
            assert "id" in rule, "Each rule expectation must include an identifier."
            min_hits = rule.get("min_hits")
            missing_tables = rule.get("missing_tables")
            if min_hits is not None:
                assert isinstance(min_hits, int) and min_hits >= 1
            if missing_tables is not None:
                assert list(missing_tables), "Missing tables must not be empty."
