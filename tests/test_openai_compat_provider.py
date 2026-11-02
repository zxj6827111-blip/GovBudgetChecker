import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.providers.base import LLMErrorType
from src.providers.openai_compat import OpenAICompatProvider


def _provider() -> OpenAICompatProvider:
    return OpenAICompatProvider(
        api_key="test-key",
        base_url="https://example.com/v1",
        default_model="gpt-4o-mini",
    )


def test_parse_error_handles_list_payload() -> None:
    provider = _provider()
    err = provider.parse_error(429, [{"error": "busy"}])
    assert err.error_type == LLMErrorType.RATE_LIMIT


def test_parse_error_handles_dict_payload() -> None:
    provider = _provider()
    err = provider.parse_error(401, {"error": {"message": "invalid key"}})
    assert err.error_type == LLMErrorType.AUTHENTICATION


def test_should_use_responses_api_when_legacy_protocol_message() -> None:
    provider = _provider()
    data = {
        "error": {
            "message": "Unsupported legacy protocol: /v1/chat/completions is not supported. Please use /v1/responses.",
            "type": "invalid_request_error",
        }
    }
    assert provider._should_use_responses_api(400, data)


def test_extract_responses_content_from_output_text() -> None:
    provider = _provider()
    data = {"output_text": "[]"}
    assert provider._extract_responses_content(data) == "[]"


def test_opencode_gateway_sends_stable_session_header() -> None:
    provider = OpenAICompatProvider(
        api_key="test-key",
        base_url="https://opencode.ai/zen/go/v1",
        default_model="deepseek-v4.1-flash",
    )
    headers = provider.get_headers()
    assert headers["x-opencode-session"].startswith("gbc-")
    assert provider.get_headers()["x-opencode-session"] == headers["x-opencode-session"]


def test_non_opencode_gateway_has_no_session_header() -> None:
    assert "x-opencode-session" not in _provider().get_headers()


def test_reasoning_options_are_added_to_payload() -> None:
    provider = OpenAICompatProvider(
        api_key="test-key",
        base_url="https://example.com/v1",
        default_model="deepseek-v4.1-flash",
        reasoning_effort="max",
        thinking=True,
    )
    payload = {}
    provider._apply_reasoning_options(payload)
    assert payload == {"reasoning_effort": "max", "thinking": {"type": "enabled"}}


def test_reasoning_options_absent_by_default() -> None:
    payload = {}
    _provider()._apply_reasoning_options(payload)
    assert payload == {}
