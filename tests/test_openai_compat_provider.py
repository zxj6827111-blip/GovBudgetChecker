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
