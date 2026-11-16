"""LLM access behind one small interface, with an Ollama implementation.

Adding another provider means writing one subclass of LLMClient.
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)


class LLMUnavailableError(RuntimeError):
    """The LLM server could not be reached or rejected the request."""


class LLMOutputError(RuntimeError):
    """The LLM kept returning output that does not match the requested schema."""


@dataclass(frozen=True)
class CallStats:
    """What one LLM call cost; used to explain where extraction time goes."""

    prompt_tokens: int
    output_tokens: int
    seconds: float
    thinking_chars: int


@dataclass(frozen=True)
class LLMHealth:
    reachable: bool
    model_available: bool


class LLMClient(ABC):
    """Minimal provider-neutral interface used by question answering and extraction."""

    @abstractmethod
    def generate(self, system: str, prompt: str, temperature: float = 0.0) -> str:
        """Return free text for the prompt."""

    @abstractmethod
    def generate_structured[T: BaseModel](self, system: str, prompt: str, schema: type[T]) -> T:
        """Return output parsed into `schema`; retries once on invalid output."""

    @abstractmethod
    def health(self) -> LLMHealth:
        """Report whether the server answers and the configured model is installed."""


class OllamaClient(LLMClient):
    """Talks to Ollama's /api/chat endpoint, using JSON-schema structured output."""

    def __init__(
        self,
        base_url: str,
        model: str,
        num_ctx: int = 8192,
        timeout_seconds: float = 300.0,
        think: bool = False,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._model = model
        self._num_ctx = num_ctx
        self._think = think
        self._http = httpx.Client(
            base_url=base_url.rstrip("/"), timeout=timeout_seconds, transport=transport
        )
        self.calls: list[CallStats] = []

    def _chat(
        self,
        messages: list[dict[str, str]],
        temperature: float,
        think: bool,
        response_format: dict[str, Any] | None = None,
    ) -> str:
        body: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "stream": False,
            "think": think,
            "options": {"temperature": temperature, "num_ctx": self._num_ctx},
        }
        if response_format is not None:
            body["format"] = response_format
        try:
            response = self._http.post("/api/chat", json=body)
        except httpx.HTTPError as exc:
            raise LLMUnavailableError(
                f"Ollama is not reachable at {self._http.base_url} ({exc})."
            ) from exc
        if response.status_code == 404:
            raise LLMUnavailableError(
                f"Model {self._model!r} is not installed in Ollama. Run: ollama pull {self._model}"
            )
        if response.is_error:
            raise LLMUnavailableError(
                f"Ollama returned HTTP {response.status_code}: {response.text[:300]}"
            )
        data = response.json()
        message = data.get("message", {})
        stats = CallStats(
            prompt_tokens=int(data.get("prompt_eval_count", 0)),
            output_tokens=int(data.get("eval_count", 0)),
            seconds=int(data.get("total_duration", 0)) / 1e9,
            thinking_chars=len(message.get("thinking") or ""),
        )
        self.calls.append(stats)
        logger.info(
            "LLM call: %d prompt tokens, %d output tokens, %.1f s, %d thinking characters",
            stats.prompt_tokens,
            stats.output_tokens,
            stats.seconds,
            stats.thinking_chars,
        )
        return message.get("content", "")

    def generate(self, system: str, prompt: str, temperature: float = 0.0) -> str:
        """Return free text for the prompt."""
        messages = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
        return self._chat(messages, temperature, think=self._think)

    def generate_structured[T: BaseModel](self, system: str, prompt: str, schema: type[T]) -> T:
        """Return output parsed into `schema`, with temperature 0 and thinking off.

        A small model occasionally breaks the schema; the bad reply is shown back to
        it once with the validation error before giving up.
        """
        messages = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
        json_schema = schema.model_json_schema()
        last_error = ""
        for attempt in range(2):
            content = self._chat(messages, 0.0, think=False, response_format=json_schema)
            try:
                return schema.model_validate_json(content)
            except ValidationError as exc:
                last_error = str(exc).splitlines()[0]
                logger.warning("Invalid structured output (attempt %d): %s", attempt + 1, exc)
                messages += [
                    {"role": "assistant", "content": content},
                    {
                        "role": "user",
                        "content": "That reply was not valid JSON for the required schema "
                        f"({last_error}). Reply again with only valid JSON.",
                    },
                ]
        raise LLMOutputError(f"The language model returned invalid JSON twice ({last_error}).")

    def health(self) -> LLMHealth:
        """Report whether Ollama answers and the configured model is pulled."""
        try:
            response = self._http.get("/api/tags", timeout=5.0)
            response.raise_for_status()
        except httpx.HTTPError:
            return LLMHealth(reachable=False, model_available=False)
        names = {m.get("name") for m in response.json().get("models", [])}
        wanted = self._model if ":" in self._model else f"{self._model}:latest"
        return LLMHealth(reachable=True, model_available=wanted in names)
