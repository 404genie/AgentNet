import re
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


# ── Capability schema entry ────────────────────────────────────────────────────

VALID_IO_TYPES = {"text", "json", "image", "audio", "binary", "boolean", "number"}

class CapabilitySchemaEntry(BaseModel):
    """The input/output contract for a single capability tag."""
    input_type: str = Field(
        ...,
        description=f"Type of input this capability accepts. One of: {', '.join(sorted(VALID_IO_TYPES))}",
        examples=["text"],
    )
    output_type: str = Field(
        ...,
        description=f"Type of output this capability produces. One of: {', '.join(sorted(VALID_IO_TYPES))}",
        examples=["text"],
    )
    parameters: dict[str, Any] = Field(
        default_factory=dict,
        description="Optional static parameters that configure this capability",
        examples=[{"max_length": 500, "language": "en"}],
    )

    @field_validator("input_type", "output_type")
    @classmethod
    def validate_io_type(cls, v: str) -> str:
        normalized = v.strip().lower()
        if normalized not in VALID_IO_TYPES:
            raise ValueError(
                f"'{v}' is not a valid I/O type. Must be one of: {', '.join(sorted(VALID_IO_TYPES))}"
            )
        return normalized


# ── Semver validator ───────────────────────────────────────────────────────────

_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")

def _validate_semver(v: str) -> str:
    if not _SEMVER_RE.match(v.strip()):
        raise ValueError(
            f"'{v}' is not valid semver. Expected format: MAJOR.MINOR.PATCH (e.g. '1.0.0')"
        )
    return v.strip()


# ── Request Schemas ────────────────────────────────────────────────────────────

class RegisterAgentRequest(BaseModel):
    name: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Unique human-readable name for the agent",
        examples=["summarizer-agent-v1"],
    )
    endpoint: str = Field(
        ...,
        description="URL where this agent accepts task requests",
        examples=["https://agents.example.com/summarizer"],
    )
    capabilities: list[str] = Field(
        ...,
        min_length=1,
        description="List of capability tags this agent supports",
        examples=[["summarization", "text-processing"]],
    )
    price: Decimal = Field(
        ...,
        ge=0,
        decimal_places=2,
        description="Price per task in credits",
        examples=[0.50],
    )
    agent_version: str = Field(
        default="1.0.0",
        description="Semantic version of this agent (MAJOR.MINOR.PATCH)",
        examples=["1.0.0", "2.1.3"],
    )
    capability_schema: dict[str, CapabilitySchemaEntry] = Field(
        default_factory=dict,
        description=(
            "Per-capability input/output contract. "
            "Keys must match entries in `capabilities`. "
            "Not all capabilities need a schema entry."
        ),
        examples=[{
            "summarization": {
                "input_type": "text",
                "output_type": "text",
                "parameters": {"max_length": 500}
            }
        }],
    )

    @field_validator("capabilities")
    @classmethod
    def normalize_capabilities(cls, v: list[str]) -> list[str]:
        cleaned = [cap.strip().lower() for cap in v if cap.strip()]
        if not cleaned:
            raise ValueError("capabilities must contain at least one non-empty tag.")
        return cleaned

    @field_validator("name")
    @classmethod
    def normalize_name(cls, v: str) -> str:
        return v.strip()

    @field_validator("agent_version")
    @classmethod
    def validate_version(cls, v: str) -> str:
        return _validate_semver(v)

    @field_validator("capability_schema")
    @classmethod
    def normalize_schema_keys(cls, v: dict) -> dict:
        """Lowercase schema keys so they always match normalized capability tags."""
        return {key.strip().lower(): entry for key, entry in v.items()}

    @model_validator(mode="after")
    def schema_keys_must_match_capabilities(self) -> "RegisterAgentRequest":
        """Every key in capability_schema must be declared in capabilities."""
        if not self.capability_schema:
            return self
        declared = set(self.capabilities)
        schema_keys = set(self.capability_schema.keys())
        unknown = schema_keys - declared
        if unknown:
            raise ValueError(
                f"capability_schema contains keys not in capabilities: {sorted(unknown)}. "
                f"Declared capabilities: {sorted(declared)}"
            )
        return self


# ── Response Schemas ───────────────────────────────────────────────────────────

class AgentResponse(BaseModel):
    id: uuid.UUID
    name: str
    endpoint: str
    capabilities: list[str]
    price: Decimal
    reputation: Decimal
    agent_version: str
    capability_schema: dict[str, Any]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class AgentListResponse(BaseModel):
    agents: list[AgentResponse]
    total: int


# ── Search Query Params ────────────────────────────────────────────────────────

class SearchAgentsParams(BaseModel):
    capability: str | None = Field(
        default=None,
        description="Filter agents by a capability tag (case-insensitive)",
        examples=["summarization"],
    )
    sort_by: Literal["reputation", "price"] = Field(
        default="reputation",
        description="Field to sort results by",
    )
    order: Literal["asc", "desc"] = Field(
        default="desc",
        description="Sort direction",
    )
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)