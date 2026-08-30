from typing import Literal

from pydantic import BaseModel, Field


class Message(BaseModel):
    role: Literal["system", "user", "assistant"] = Field(..., description="The role of the message author.")
    content: str = Field(..., description="The content of the message.")


class ChatCompletionRequest(BaseModel):
    tier: Literal["fast", "balanced", "quality"] = Field(
        "balanced", description="The service tier mapping to LLM models."
    )
    messages: list[Message] = Field(..., min_items=1, description="The list of messages for chat completion.")
    stream: bool = Field(False, description="Whether to stream the response chunks.")
    max_tokens: int | None = Field(None, gt=0, description="The maximum number of tokens to generate.")


class Usage(BaseModel):
    input_tokens: int = Field(..., description="The number of input tokens used.")
    output_tokens: int = Field(..., description="The number of output tokens generated.")
    cost_usd: float = Field(..., description="The calculated cost of the request in USD.")


class ChatCompletionResponse(BaseModel):
    id: str = Field(..., description="Gateway correlation request ID.")
    provider: str = Field(..., description="The actual provider that served the request.")
    model: str = Field(..., description="The actual model used.")
    was_fallback: bool = Field(..., description="Whether a fallback provider was used.")
    content: str = Field(..., description="The assistant's completed message.")
    usage: Usage = Field(..., description="Token usage and cost statistics.")
    latency_ms: int = Field(..., description="Total latency measured by the gateway in milliseconds.")


import uuid


class TeamCreateRequest(BaseModel):
    name: str = Field(..., description="Unique human-readable name of the team.")
    monthly_budget_usd: float = Field(..., gt=0.0, description="Monthly budget limit in USD.")
    priority_tier: Literal["high", "normal", "low"] = Field("normal", description="Priority tier for the team.")


class TeamResponse(BaseModel):
    id: uuid.UUID
    name: str
    monthly_budget_usd: float
    priority_tier: str
    is_active: bool

    class Config:
        from_attributes = True


class TeamCreateResponse(TeamResponse):
    api_key: str = Field(..., description="Plaintext API key. Only shown once.")


class ModelAccessRequest(BaseModel):
    logical_tier: Literal["fast", "balanced", "quality"] = Field(..., description="Abstract logical service tier.")
    primary_provider: Literal["gemini", "claude", "groq", "ollama"] = Field(..., description="Primary provider name.")
    primary_model: str = Field(..., description="Exact model name to use.")
    fallback_provider: Literal["gemini", "claude", "groq", "ollama"] | None = Field(None)
    fallback_model: str | None = Field(None)
    rate_limit_rpm: int = Field(60, gt=0, description="Requests per minute limit.")
    rate_limit_tpm: int = Field(50000, gt=0, description="Tokens per minute limit.")


class ModelAccessResponse(BaseModel):
    id: uuid.UUID
    team_id: uuid.UUID
    logical_tier: str
    primary_provider: str
    primary_model: str
    fallback_provider: str | None = None
    fallback_model: str | None = None
    rate_limit_rpm: int
    rate_limit_tpm: int

    class Config:
        from_attributes = True
