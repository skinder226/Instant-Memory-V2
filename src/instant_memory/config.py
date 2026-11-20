# instant_memory/config.py
"""
Environment-driven settings and process-wide singletons.

Every process (web app, worker, scripts) builds its clients through this
module, so the engine and the outbox worker can never disagree about which
databases, embedding model or thresholds are in use.

Required environment variables
    MONGODB_URI          e.g. mongodb+srv://...  (must be a replica set for
                         atomic writes; include retryWrites=true&w=majority)
    OPENROUTER_API_KEY   used by Jev

Optional
    MONGODB_DB (instant_memory), MONGODB_TIMEOUT_MS (10000), MONGODB_MAX_POOL (100)

    Pinecone (vector search):   PINECONE_API_KEY + PINECONE_INDEX (or PINECONE_HOST)
    Embeddings (needed with Pinecone; OpenAI-compatible API):
        EMBEDDING_API_KEY (falls back to OPENAI_API_KEY), EMBEDDING_MODEL
        (text-embedding-3-small), EMBEDDING_BASE_URL, EMBEDDING_DIMENSIONS
    Neo4j (graph search):       NEO4J_URI + NEO4J_USER + NEO4J_PASSWORD

    JEV_MODEL, JEV_URL, JEV_MIN_CONFIDENCE (0.80), JEV_STRICT_CONFIDENCE (0.90)
    DEDUP_CANDIDATE_LIMIT (8), DEDUP_VECTOR_MIN_SCORE (0.0)

    Worker: WORKER_LEASE_SECONDS (120), WORKER_MAX_ATTEMPTS (8),
            WORKER_BACKOFF_BASE (5), WORKER_BACKOFF_CAP (600),
            WORKER_IDLE_SECONDS (2)
    LOG_LEVEL (INFO)
"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from typing import Any, Callable

from pymongo import MongoClient

from instant_memory.deduplication_engine import (
    JEV_MIN_CONFIDENCE,
    JEV_STRICT_CONFIDENCE,
    DeduplicationEngine,
    JevClient,
)

try:  # optional dependency
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass

logger = logging.getLogger("instant_memory.config")


# ============================================================
# SETTINGS
# ============================================================

def _get(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    return value if value not in (None, "") else default


def _require(name: str) -> str:
    value = _get(name)

    if value is None:
        raise RuntimeError(f"Missing required environment variable: {name}")

    return value


def _number(name: str, default, cast):
    raw = _get(name)

    if raw is None:
        return default

    try:
        return cast(raw)
    except ValueError:
        raise RuntimeError(f"{name} must be a valid {cast.__name__}: {raw!r}")


@dataclass(frozen=True)
class Settings:
    mongodb_uri: str
    mongodb_db: str
    mongodb_timeout_ms: int
    mongodb_max_pool: int

    jev_api_key: str
    jev_min_confidence: float
    jev_strict_confidence: float
    candidate_limit: int
    vector_min_score: float

    pinecone_api_key: str | None
    pinecone_index: str | None
    pinecone_host: str | None

    embedding_api_key: str | None
    embedding_base_url: str | None
    embedding_model: str
    embedding_dimensions: int | None

    neo4j_uri: str | None
    neo4j_user: str | None
    neo4j_password: str | None

    worker_lease_seconds: int
    worker_max_attempts: int
    worker_backoff_base: float
    worker_backoff_cap: float
    worker_idle_seconds: float

    log_level: str

    @property
    def pinecone_enabled(self) -> bool:
        return bool(self.pinecone_api_key)

    @property
    def neo4j_enabled(self) -> bool:
        return bool(self.neo4j_uri)


def load_settings() -> Settings:
    settings = Settings(
        mongodb_uri=_require("MONGODB_URI"),
        mongodb_db=_get("MONGODB_DB", "instant_memory"),
        mongodb_timeout_ms=_number("MONGODB_TIMEOUT_MS", 10_000, int),
        mongodb_max_pool=_number("MONGODB_MAX_POOL", 100, int),
        jev_api_key=_require("OPENROUTER_API_KEY"),
        jev_min_confidence=_number(
            "JEV_MIN_CONFIDENCE", JEV_MIN_CONFIDENCE, float
        ),
        jev_strict_confidence=_number(
            "JEV_STRICT_CONFIDENCE", JEV_STRICT_CONFIDENCE, float
        ),
        candidate_limit=_number("DEDUP_CANDIDATE_LIMIT", 8, int),
        vector_min_score=_number("DEDUP_VECTOR_MIN_SCORE", 0.0, float),
        pinecone_api_key=_get("PINECONE_API_KEY"),
        pinecone_index=_get("PINECONE_INDEX"),
        pinecone_host=_get("PINECONE_HOST"),
        embedding_api_key=_get("EMBEDDING_API_KEY", _get("OPENAI_API_KEY")),
        embedding_base_url=_get("EMBEDDING_BASE_URL"),
        embedding_model=_get("EMBEDDING_MODEL", "text-embedding-3-small"),
        embedding_dimensions=_number("EMBEDDING_DIMENSIONS", None, int),
        neo4j_uri=_get("NEO4J_URI"),
        neo4j_user=_get("NEO4J_USER"),
        neo4j_password=_get("NEO4J_PASSWORD"),
        worker_lease_seconds=_number("WORKER_LEASE_SECONDS", 120, int),
        worker_max_attempts=_number("WORKER_MAX_ATTEMPTS", 8, int),
        worker_backoff_base=_number("WORKER_BACKOFF_BASE", 5.0, float),
        worker_backoff_cap=_number("WORKER_BACKOFF_CAP", 600.0, float),
        worker_idle_seconds=_number("WORKER_IDLE_SECONDS", 2.0, float),
        log_level=(_get("LOG_LEVEL", "INFO") or "INFO").upper(),
    )

    # Fail at startup, not on the first memory.
    if settings.pinecone_enabled:
        if not (settings.pinecone_index or settings.pinecone_host):
            raise RuntimeError(
                "PINECONE_API_KEY is set but neither PINECONE_INDEX nor "
                "PINECONE_HOST is."
            )

        if not settings.embedding_api_key:
            raise RuntimeError(
                "Pinecone needs embeddings: set EMBEDDING_API_KEY "
                "(or OPENAI_API_KEY)."
            )

    if settings.neo4j_enabled and not (
        settings.neo4j_user and settings.neo4j_password
    ):
        raise RuntimeError(
            "NEO4J_URI is set but NEO4J_USER / NEO4J_PASSWORD are missing."
        )

    return settings


# ============================================================
# CLIENT FACTORIES
# ============================================================

def build_embedder(settings: Settings) -> Callable[[str], list[float]]:
    """
    OpenAI-compatible embeddings. If the project already has an embedding
    function, replace the body of this function with it. It is the only
    place embeddings are created, for both the engine and the worker.
    """
    from openai import OpenAI

    client = OpenAI(
        api_key=settings.embedding_api_key,
        base_url=settings.embedding_base_url,
        timeout=30,
        max_retries=3,
    )

    def embed(text: str) -> list[float]:
        request: dict[str, Any] = {
            "model": settings.embedding_model,
            "input": text,
        }

        if settings.embedding_dimensions:
            request["dimensions"] = settings.embedding_dimensions

        return client.embeddings.create(**request).data[0].embedding

    return embed


def _build_mongo_db(settings: Settings):
    client = MongoClient(
        settings.mongodb_uri,
        tz_aware=True,
        serverSelectionTimeoutMS=settings.mongodb_timeout_ms,
        maxPoolSize=settings.mongodb_max_pool,
    )

    return client[settings.mongodb_db]


def _build_pinecone_index(settings: Settings):
    from pinecone import Pinecone

    client = Pinecone(api_key=settings.pinecone_api_key)

    if settings.pinecone_host:
        return client.Index(host=settings.pinecone_host)

    return client.Index(settings.pinecone_index)


def _build_neo4j_driver(settings: Settings):
    from neo4j import GraphDatabase

    driver = GraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_user, settings.neo4j_password),
    )

    driver.verify_connectivity()  # fail at startup if unreachable

    return driver


# ============================================================
# SINGLETONS
# ============================================================

_lock = threading.RLock()
_cache: dict[str, Any] = {}


def _once(name: str, factory: Callable[[], Any]) -> Any:
    with _lock:
        if name not in _cache:
            _cache[name] = factory()

        return _cache[name]


def get_settings() -> Settings:
    return _once("settings", load_settings)


def get_mongo_db():
    return _once("mongo_db", lambda: _build_mongo_db(get_settings()))


def get_pinecone_index():
    """None when Pinecone is not configured."""
    settings = get_settings()

    if not settings.pinecone_enabled:
        return None

    return _once("pinecone", lambda: _build_pinecone_index(settings))


def get_neo4j_driver():
    """None when Neo4j is not configured."""
    settings = get_settings()

    if not settings.neo4j_enabled:
        return None

    return _once("neo4j", lambda: _build_neo4j_driver(settings))


def get_embedder():
    """None when Pinecone is not configured."""
    settings = get_settings()

    if not settings.pinecone_enabled:
        return None

    return _once("embedder", lambda: build_embedder(settings))


def get_engine() -> DeduplicationEngine:
    def build() -> DeduplicationEngine:
        settings = get_settings()

        engine = DeduplicationEngine(
            get_mongo_db(),
            pinecone_index=get_pinecone_index(),
            neo4j_driver=get_neo4j_driver(),
            embedding_function=get_embedder(),
            jev=JevClient(api_key=settings.jev_api_key),
            candidate_limit=settings.candidate_limit,
            min_confidence=settings.jev_min_confidence,
            strict_confidence=settings.jev_strict_confidence,
            vector_min_score=settings.vector_min_score,
        )

        logger.info(
            "Engine ready (transactions=%s, pinecone=%s, neo4j=%s)",
            engine.use_transactions,
            engine.sync_targets["pinecone"],
            engine.sync_targets["neo4j"],
        )

        return engine

    return _once("engine", build)


def close_all() -> None:
    """Close network clients. Call on shutdown."""
    with _lock:
        mongo_db = _cache.get("mongo_db")
        neo4j_driver = _cache.get("neo4j")

        if neo4j_driver is not None:
            try:
                neo4j_driver.close()
            except Exception:
                logger.exception("Failed to close Neo4j driver")

        if mongo_db is not None:
            try:
                mongo_db.client.close()
            except Exception:
                logger.exception("Failed to close MongoDB client")

        _cache.clear()