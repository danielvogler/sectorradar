"""The LLM boundary.

One narrow interface, so that the choice of provider is a configuration
decision rather than an architectural one. Everything past this module sees a
validated pydantic model and a token count, never a provider SDK type.

The default is Vertex AI with Application Default Credentials, which needs no
API key in the environment — run ``gcloud auth application-default login``
once. Adding another provider means implementing :class:`LLMClient`, not
touching ``extract.py`` or ``classify.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from sectorradar.config import ConfigError, Settings
from sectorradar.logging import get_logger

log = get_logger(__name__)

T = TypeVar("T", bound=BaseModel)

#: USD per million tokens, (input, output). Used for the cost figures in
#: `sectorradar stats`; approximate by nature, and cheap to keep roughly right.
PRICES: dict[str, tuple[float, float]] = {
    "gemini-2.5-flash-lite": (0.10, 0.40),
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-2.5-pro": (1.25, 10.00),
}
DEFAULT_PRICE = (0.10, 0.40)

#: Milliseconds before a single generation request is abandoned. Generous
#: enough for a long document, short enough that a hung request costs one
#: company rather than the run.
REQUEST_TIMEOUT_MS = 120_000


@dataclass(frozen=True)
class Usage:
    """Token accounting for one call."""

    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""

    @property
    def cost_usd(self) -> float:
        per_in, per_out = PRICES.get(self.model, DEFAULT_PRICE)
        return (self.input_tokens * per_in + self.output_tokens * per_out) / 1_000_000

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            model=self.model or other.model,
        )


@dataclass(frozen=True)
class Structured(Generic[T]):
    """A parsed response and what it cost."""

    value: T | None
    usage: Usage


class LLMClient(Protocol):
    """What the pipeline needs from a language model, and nothing more."""

    model: str

    def structured(
        self, prompt: str, schema: type[T], *, temperature: float = 0.0
    ) -> Structured[T]:
        """Return an instance of ``schema``, or ``None`` if the model would not comply."""
        ...


class VertexClient:
    """Vertex AI via Application Default Credentials."""

    def __init__(self, project: str, location: str, model: str) -> None:
        from google import genai
        from google.genai import types

        self.model = model
        self._client = genai.Client(
            vertexai=True,
            project=project,
            location=location,
            # Without this a single unresponsive request blocks forever. Seen
            # on a real run: extraction sat on one company for 34 minutes with
            # 7 seconds of CPU and an open socket, which would have consumed an
            # entire unattended overnight pass and produced nothing.
            http_options=types.HttpOptions(timeout=REQUEST_TIMEOUT_MS),
        )

    def structured(
        self, prompt: str, schema: type[T], *, temperature: float = 0.0
    ) -> Structured[T]:
        from google.genai import types

        response = self._client.models.generate_content(
            model=self.model,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=schema,
                temperature=temperature,
            ),
        )

        meta = response.usage_metadata
        usage = Usage(
            input_tokens=int(getattr(meta, "prompt_token_count", 0) or 0),
            output_tokens=int(getattr(meta, "candidates_token_count", 0) or 0),
            model=self.model,
        )

        parsed = response.parsed
        if isinstance(parsed, schema):
            return Structured(value=parsed, usage=usage)

        # The SDK hands back None for both "the model emitted broken JSON" and
        # "the JSON did not satisfy the schema", which are very different
        # problems. Re-parse to find out which, and log the field that actually
        # failed — otherwise a systematic schema mismatch looks like random
        # flakiness and stays unfixed.
        text = response.text or ""
        try:
            schema.model_validate_json(text)
        except ValidationError as exc:
            log.warning(
                "llm.schema_violation",
                model=self.model,
                finish_reason=str(getattr(response.candidates[0], "finish_reason", "?"))
                if response.candidates
                else "?",
                errors=[
                    {"field": ".".join(str(p) for p in e["loc"]), "problem": e["msg"]}
                    for e in exc.errors()[:5]
                ],
            )
        except ValueError:
            log.warning(
                "llm.unparseable_response",
                model=self.model,
                finish_reason=str(getattr(response.candidates[0], "finish_reason", "?"))
                if response.candidates
                else "?",
                text=text[:200],
            )
        return Structured(value=None, usage=usage)


class AnthropicClient:
    """Claude, via the Messages API.

    Structured output is done with a single forced tool call rather than by
    asking for JSON in the prompt. The schema becomes the tool's input schema,
    so the API itself constrains the shape and there is no prose to strip, no
    stray markdown fence, and no "here is the JSON you asked for" preamble to
    parse around.
    """

    def __init__(self, api_key: str, model: str) -> None:
        import anthropic

        self.model = model
        self._client = anthropic.Anthropic(
            api_key=api_key,
            timeout=REQUEST_TIMEOUT_MS / 1000,
            max_retries=2,
        )

    def structured(
        self, prompt: str, schema: type[T], *, temperature: float = 0.0
    ) -> Structured[T]:
        tool_name = "record_" + schema.__name__.lower()
        response = self._client.messages.create(
            model=self.model,
            max_tokens=8192,
            temperature=temperature,
            tools=[
                {
                    "name": tool_name,
                    "description": f"Record the extracted {schema.__name__}.",
                    "input_schema": schema.model_json_schema(),
                }
            ],
            # Forced, so the model cannot answer in prose instead.
            tool_choice={"type": "tool", "name": tool_name},
            messages=[{"role": "user", "content": prompt}],
        )

        usage = Usage(
            input_tokens=int(getattr(response.usage, "input_tokens", 0) or 0),
            output_tokens=int(getattr(response.usage, "output_tokens", 0) or 0),
            model=self.model,
        )

        for block in response.content:
            if getattr(block, "type", None) != "tool_use":
                continue
            try:
                return Structured(value=schema.model_validate(block.input), usage=usage)
            except ValidationError as exc:
                log.warning(
                    "llm.schema_violation",
                    model=self.model,
                    stop_reason=str(response.stop_reason),
                    errors=[
                        {"field": ".".join(str(p) for p in e["loc"]), "problem": e["msg"]}
                        for e in exc.errors()[:5]
                    ],
                )
                return Structured(value=None, usage=usage)

        log.warning("llm.no_tool_use", model=self.model, stop_reason=str(response.stop_reason))
        return Structured(value=None, usage=usage)


class OpenAIClient:
    """OpenAI, via structured outputs.

    `response_format` with a strict JSON schema, which is the same bargain the
    Anthropic client strikes with a forced tool call: the API enforces the
    shape rather than the prompt asking politely for it.
    """

    def __init__(self, api_key: str, model: str) -> None:
        from openai import OpenAI

        self.model = model
        self._client = OpenAI(api_key=api_key, timeout=REQUEST_TIMEOUT_MS / 1000, max_retries=2)

    def structured(
        self, prompt: str, schema: type[T], *, temperature: float = 0.0
    ) -> Structured[T]:
        completion = self._client.chat.completions.parse(
            model=self.model,
            temperature=temperature,
            messages=[{"role": "user", "content": prompt}],
            response_format=schema,
        )

        meta = completion.usage
        usage = Usage(
            input_tokens=int(getattr(meta, "prompt_tokens", 0) or 0),
            output_tokens=int(getattr(meta, "completion_tokens", 0) or 0),
            model=self.model,
        )

        parsed = completion.choices[0].message.parsed
        if isinstance(parsed, schema):
            return Structured(value=parsed, usage=usage)

        log.warning(
            "llm.no_parsed_output",
            model=self.model,
            refusal=getattr(completion.choices[0].message, "refusal", None),
        )
        return Structured(value=None, usage=usage)


def get_client(settings: Settings) -> LLMClient:
    """Build the configured client, or explain what is missing."""
    provider = settings.llm_provider.lower()

    if provider == "vertex":
        if not settings.gcp_project:
            msg = (
                "GOOGLE_CLOUD_PROJECT is unset. Vertex AI needs a project and "
                "Application Default Credentials: set it in .env and run "
                "`gcloud auth application-default login`."
            )
            raise ConfigError(msg)
        return VertexClient(settings.gcp_project, settings.gcp_location, settings.llm_model)

    if provider == "anthropic":
        if not settings.anthropic_api_key:
            msg = "SECTORRADAR_LLM_PROVIDER is 'anthropic' but ANTHROPIC_API_KEY is unset in .env"
            raise ConfigError(msg)
        return AnthropicClient(settings.anthropic_api_key, settings.llm_model)

    if provider == "openai":
        if not settings.openai_api_key:
            msg = "SECTORRADAR_LLM_PROVIDER is 'openai' but OPENAI_API_KEY is unset in .env"
            raise ConfigError(msg)
        return OpenAIClient(settings.openai_api_key, settings.llm_model)

    msg = (
        f"unsupported SECTORRADAR_LLM_PROVIDER '{settings.llm_provider}'. "
        "Supported: vertex, anthropic, openai. Adding another means "
        "implementing the LLMClient protocol in llm.py — it has one method."
    )
    raise ConfigError(msg)
