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

        self.model = model
        self._client = genai.Client(vertexai=True, project=project, location=location)

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

    msg = (
        f"unsupported SECTORRADAR_LLM_PROVIDER '{settings.llm_provider}'. "
        "Supported: vertex. Adding another means implementing the LLMClient "
        "protocol in llm.py."
    )
    raise ConfigError(msg)
