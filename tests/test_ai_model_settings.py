import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.services import ai_model_settings as settings


def _profile(**overrides) -> dict:
    base = {
        "id": "model-test",
        "name": "测试模型",
        "provider_type": "openai_compat",
        "base_url": "https://example.com/v1",
        "model": "deepseek-v4.1-flash",
        "api_key": "sk-test",
    }
    base.update(overrides)
    return base


def test_reasoning_effort_accepts_supported_levels() -> None:
    for level in ("low", "medium", "high", "max", "MAX"):
        assert settings.ModelProfile.model_validate(_profile(reasoning_effort=level)).reasoning_effort == level.lower()


def test_reasoning_effort_rejects_unknown_levels() -> None:
    with pytest.raises(ValidationError):
        settings.ModelProfile.model_validate(_profile(reasoning_effort="ultra"))


def test_blank_reasoning_effort_normalizes_to_none() -> None:
    assert settings.ModelProfile.model_validate(_profile(reasoning_effort="  ")).reasoning_effort is None


def test_save_round_trips_reasoning_options_without_plaintext_key(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AI_MODEL_SETTINGS_PATH", str(tmp_path / "settings.json"))
    saved = settings.save_settings(
        settings.ModelSettingsUpdate(
            enabled=True,
            active_profile_id="model-test",
            profiles=[settings.ModelProfile.model_validate(_profile(reasoning_effort="max", thinking=True))],
        )
    )
    profile = saved["profiles"][0]
    assert profile["reasoning_effort"] == "max"
    assert profile["thinking"] is True
    assert "api_key" not in profile

    runtime = settings.resolve_runtime()
    assert runtime["reasoning_effort"] == "max"
    assert runtime["thinking"] is True
    assert runtime["api_key"] == "sk-test"
