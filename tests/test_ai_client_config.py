import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.engine.ai.extractor_client import ExtractorConfig
from src.services.ai_client import AIClient, AIClientConfig


def test_ai_client_config_adds_env_slot_providers(monkeypatch, tmp_path) -> None:
    config_path = tmp_path / "providers.yaml"
    config_path.write_text("region: cn\nfallback_chain: []\nproviders: {}\n", encoding="utf-8")

    monkeypatch.setenv("AI_MAIN_BASE_URL", "https://main.example.com/v1")
    monkeypatch.setenv("AI_MAIN_API_KEY", "main-key")
    monkeypatch.setenv("AI_MAIN_MODEL", "gpt-5.4")
    monkeypatch.setenv("AI_MAIN_PROVIDER_TYPE", "openai_compat")
    monkeypatch.setenv("AI_BACKUP_BASE_URL", "https://backup.example.com/v1")
    monkeypatch.setenv("AI_BACKUP_API_KEY", "backup-key")
    monkeypatch.setenv("AI_BACKUP_MODEL", "gemini-2.5-pro")
    monkeypatch.setenv("AI_LOCATOR_BASE_URL", "https://locator.example.com/v1")
    monkeypatch.setenv("AI_LOCATOR_API_KEY", "locator-key")
    monkeypatch.setenv("AI_LOCATOR_MODEL", "gemini-2.5-flash")

    cfg = AIClientConfig.from_yaml(str(config_path))

    assert "main" in cfg.providers
    assert "backup" in cfg.providers
    assert "locator" in cfg.providers
    assert cfg.providers["main"]["provider_type"] == "openai_compat"
    assert cfg.fallback_chain[:3] == ["main", "backup", "locator"]


def test_ai_client_config_honors_env_fallback_chain(monkeypatch, tmp_path) -> None:
    config_path = tmp_path / "providers.yaml"
    config_path.write_text("region: cn\nfallback_chain: [gemini_main]\nproviders: {}\n", encoding="utf-8")

    monkeypatch.setenv("AI_MAIN_BASE_URL", "https://main.example.com/v1")
    monkeypatch.setenv("AI_MAIN_API_KEY", "main-key")
    monkeypatch.setenv("AI_MAIN_MODEL", "gpt-5.4")
    monkeypatch.setenv("AI_FALLBACK_CHAIN", "main,backup,locator")

    cfg = AIClientConfig.from_yaml(str(config_path))

    assert cfg.fallback_chain == ["main", "backup", "locator"]


def test_ai_client_initializes_env_slot_provider(monkeypatch, tmp_path) -> None:
    config_path = tmp_path / "providers.yaml"
    config_path.write_text("region: cn\nfallback_chain: []\nproviders: {}\n", encoding="utf-8")

    monkeypatch.setenv("AI_MAIN_BASE_URL", "https://main.example.com/v1")
    monkeypatch.setenv("AI_MAIN_API_KEY", "main-key")
    monkeypatch.setenv("AI_MAIN_MODEL", "gpt-5.4")
    monkeypatch.setenv("AI_MAIN_PROVIDER_TYPE", "openai_compat")

    client = AIClient(config_path=str(config_path))

    assert "main" in client.loaded_providers
    assert "main" in client.get_available_providers()


def test_extractor_config_audit_defaults_to_main(monkeypatch) -> None:
    monkeypatch.setenv("AI_MAIN_PROVIDER", "main")
    monkeypatch.setenv("AI_MAIN_MODEL", "gpt-5.4")
    monkeypatch.delenv("AI_AUDIT_PROVIDER", raising=False)
    monkeypatch.delenv("AI_AUDIT_MODEL", raising=False)

    cfg = ExtractorConfig()

    assert cfg.audit_provider == "main"
    assert cfg.audit_model == "gpt-5.4"


def test_ai_client_env_slot_empty_enabled_uses_default_true(monkeypatch, tmp_path) -> None:
    config_path = tmp_path / "providers.yaml"
    config_path.write_text("region: cn\nfallback_chain: []\nproviders: {}\n", encoding="utf-8")

    monkeypatch.setenv("AI_MAIN_BASE_URL", "https://main.example.com/v1")
    monkeypatch.setenv("AI_MAIN_API_KEY", "main-key")
    monkeypatch.setenv("AI_MAIN_MODEL", "gpt-5.4")
    monkeypatch.setenv("AI_MAIN_PROVIDER_TYPE", "openai_compat")
    monkeypatch.setenv("AI_MAIN_ENABLED", "")

    client = AIClient(config_path=str(config_path))

    assert "main" in client.loaded_providers
