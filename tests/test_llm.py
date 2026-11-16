import json

import httpx
import pytest
from pydantic import BaseModel

from app.llm import LLMOutputError, LLMUnavailableError, OllamaClient


class Answer(BaseModel):
    value: int


def client_with(replies: list[str], requests: list[dict]) -> OllamaClient:
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"message": {"content": replies.pop(0)}})

    return OllamaClient("http://ollama", "qwen3:4b", transport=httpx.MockTransport(handler))


def test_structured_output_sends_schema_with_thinking_off_and_temperature_zero() -> None:
    requests: list[dict] = []
    llm = client_with(['{"value": 4}'], requests)
    assert llm.generate_structured("sys", "2+2?", Answer) == Answer(value=4)
    body = requests[0]
    assert body["format"] == Answer.model_json_schema()
    assert body["think"] is False
    assert body["options"] == {"temperature": 0.0, "num_ctx": 8192}
    assert body["stream"] is False


def test_invalid_json_is_retried_once_then_succeeds() -> None:
    requests: list[dict] = []
    llm = client_with(["not json", '{"value": 4}'], requests)
    assert llm.generate_structured("sys", "2+2?", Answer).value == 4
    assert len(requests) == 2
    assert requests[1]["messages"][-2] == {"role": "assistant", "content": "not json"}


def test_invalid_json_twice_raises_clear_error() -> None:
    llm = client_with(["nope", '{"value": "four"}'], [])
    with pytest.raises(LLMOutputError, match="invalid JSON twice"):
        llm.generate_structured("sys", "2+2?", Answer)


def test_unreachable_server_raises_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    llm = OllamaClient("http://ollama", "qwen3:4b", transport=httpx.MockTransport(handler))
    with pytest.raises(LLMUnavailableError, match="not reachable"):
        llm.generate("sys", "hi")
    assert llm.health().reachable is False


def test_missing_model_gives_pull_hint() -> None:
    transport = httpx.MockTransport(lambda _r: httpx.Response(404, json={"error": "not found"}))
    llm = OllamaClient("http://ollama", "qwen3:4b", transport=transport)
    with pytest.raises(LLMUnavailableError, match="ollama pull qwen3:4b"):
        llm.generate("sys", "hi")


def test_health_checks_that_model_is_pulled() -> None:
    tags = {"models": [{"name": "qwen3:4b"}, {"name": "llama3:latest"}]}
    transport = httpx.MockTransport(lambda _r: httpx.Response(200, json=tags))
    assert OllamaClient("http://o", "qwen3:4b", transport=transport).health().model_available
    assert OllamaClient("http://o", "llama3", transport=transport).health().model_available
    assert not OllamaClient("http://o", "qwen3:8b", transport=transport).health().model_available
