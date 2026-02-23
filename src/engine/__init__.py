"""Parsing and rules engine placeholder package."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from api.config import AppConfig


class RuleEngine:
    """Minimal placeholder that records requested rule evaluations."""

    def __init__(self, config: AppConfig | None = None) -> None:
        self._config = config or AppConfig.load()

    @property
    def rules_file(self) -> Path:
        """Return the rules file path used by the engine."""

        return self._config.rules_file

    def evaluate(self, document_path: Path) -> Iterable[str]:
        """Pretend to evaluate a document and yield triggered rules."""

        _ = document_path
        return []
