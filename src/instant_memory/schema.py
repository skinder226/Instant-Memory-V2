
from typing import Literal
from datetime import datetime

from pydantic import BaseModel, Field, ConfigDict, field_validator


# ==========================================
# ENTITY SCHEMA
# ==========================================

class EntitySchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    type: str


# ==========================================
# RELATIONSHIP SCHEMA
# ==========================================

class RelationshipSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject: str
    predicate: str
    object: str


# ==========================================
# TIMESPAN SCHEMA
# ==========================================

class TimeSpanSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Time explicitly mentioned by the user
    start: str | None = None
    end: str | None = None


# ==========================================
# VALIDITY SCHEMA
# ==========================================

class ValiditySchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Application-managed memory validity
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    is_current: bool = True


# ==========================================
# SOURCE SCHEMA
# ==========================================

class SourceSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    speaker: Literal[
        "user",
        "assistant",
        "system",
    ] = "user"

    timestamp: datetime | None = None

    source_type: Literal[
        "conversation",
        "document",
        "tool",
        "observation",
        "external",
    ] = "conversation"

    @field_validator("source_type", mode="before")
    @classmethod
    def normalize_source_type(cls, value):
        if value in ("user", "assistant"):
            return "conversation"
        return value


# ==========================================
# UNIVERSAL MEMORY SCHEMA
# ==========================================

class MemorySchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Application-generated fields
    memory_id: str
    user_id: str

    # Memory classification
    memory_type: Literal[
        "personal",
        "semantic",
        "episodic",
        "procedural",
        "temporal",
        "task",
    ]

    # Main memory information
    content: str
    summary: str
    category: str

    # Related information
    entities: list[EntitySchema] = Field(default_factory=list)
    relationships: list[RelationshipSchema] = Field(default_factory=list)

    # User-mentioned time period
    timespan: TimeSpanSchema = Field(
        default_factory=TimeSpanSchema
    )

    # Memory quality
    confidence: float = Field(ge=0.0, le=1.0)
    importance: float = Field(ge=0.0, le=1.0)

    stability: Literal[
        "stable",
        "changing",
        "temporary",
        "unknown",
    ]

    reusability: Literal[
        "high",
        "medium",
        "low",
    ]

    # Application-managed validity
    validity: ValiditySchema = Field(
        default_factory=ValiditySchema
    )

    # Origin information
    source: SourceSchema = Field(
        default_factory=SourceSchema
    )

    # Additional information
    metadata: dict = Field(default_factory=dict)



from typing import Literal
from datetime import datetime, timezone

from pydantic import BaseModel, Field, ConfigDict


# ==========================================
# DATABASE SYNC SCHEMA
# ==========================================

class DatabaseSyncSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal[
        "pending",
        "processing",
        "completed",
        "failed",
    ] = "pending"

    error: str | None = None

    synced_at: datetime | None = None


# ==========================================
# OUTBOX SCHEMA
# ==========================================

class OutboxSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Related memory
    memory_id: str

    # Database synchronization status
    pinecone: DatabaseSyncSchema = Field(
        default_factory=DatabaseSyncSchema
    )

    neo4j: DatabaseSyncSchema = Field(
        default_factory=DatabaseSyncSchema
    )

    # Outbox timestamps
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
