# instant_memory/memory_service.py
"""
The API the rest of the project calls.

    from instant_memory.memory_service import save_memory

    outcome = save_memory(memory_dict)

Failure contract
----------------
* Invalid input raises pydantic.ValidationError immediately (not retried).
* Transient failures (Jev/network/MongoDB connection) are retried with
  exponential backoff. If they persist, the exception is re-raised.
* An exception always means NOTHING was written. The same memory_id can be
  submitted again later; an already-handled memory returns
  {"status": "already_processed"}.
* For batches use save_memories(): one bad memory never aborts the rest, and
  failed items are returned so the caller can persist or retry them.
"""

from __future__ import annotations

import logging
import time
from datetime import timezone
from typing import Iterable

import requests
from pymongo.errors import ConnectionFailure

from instant_memory.config import (
    get_engine,
    get_neo4j_driver,
    get_pinecone_index,
)
from instant_memory.deduplication_engine import (
    JevError,
    MemorySchema,
    utc_now,
)

logger = logging.getLogger("instant_memory.service")

# Errors where trying again can succeed. ConnectionFailure covers
# AutoReconnect, NetworkTimeout and ServerSelectionTimeoutError.
RETRYABLE = (JevError, requests.RequestException, ConnectionFailure)


# ============================================================
# SAVING MEMORIES
# ============================================================

def save_memory(
    memory: MemorySchema | dict,
    *,
    max_retries: int = 3,
    backoff_seconds: float = 1.0,
) -> dict:
    """Deduplicate and store one memory. Returns the engine outcome."""
    engine = get_engine()

    memory_id = (
        memory.memory_id if isinstance(memory, MemorySchema)
        else memory.get("memory_id")
    )

    started = time.monotonic()

    for attempt in range(max_retries + 1):
        try:
            outcome = engine.process(memory)
            break

        except RETRYABLE as exc:
            if attempt == max_retries:
                logger.error(
                    "save_memory failed after %d attempts "
                    "(memory_id=%s): %r",
                    attempt + 1, memory_id, exc,
                )
                raise

            delay = backoff_seconds * (2 ** attempt)

            logger.warning(
                "Transient error (memory_id=%s, attempt %d/%d), "
                "retrying in %.1fs: %r",
                memory_id, attempt + 1, max_retries + 1, delay, exc,
            )

            time.sleep(delay)

    result = outcome.get("result") or {}
    search = outcome.get("search") or {}

    logger.info(
        "memory processed memory_id=%s status=%s action=%s source=%s "
        "candidates=%s search_errors=%s ms=%d",
        memory_id,
        outcome.get("status"),
        result.get("action"),
        outcome.get("decision_source"),
        search.get("unique_candidates"),
        len(search.get("errors", [])),
        (time.monotonic() - started) * 1000,
    )

    if search.get("errors"):
        # Ingestion still succeeded, but a search stage was degraded.
        logger.warning(
            "Degraded search for memory_id=%s: %s",
            memory_id, search["errors"],
        )

    return outcome


def save_memories(memories: Iterable[MemorySchema | dict], **kwargs) -> dict:
    """
    Save many memories. Failures are isolated per item.

    Returns {"processed": int, "outcomes": [...], "failed": [{"memory", "error"}]}
    """
    outcomes: list[dict] = []
    failed: list[dict] = []

    for memory in memories:
        try:
            outcomes.append(save_memory(memory, **kwargs))
        except Exception as exc:
            logger.exception("Batch item failed")
            failed.append({"memory": memory, "error": repr(exc)})

    return {
        "processed": len(outcomes),
        "outcomes": outcomes,
        "failed": failed,
    }


# ============================================================
# REVIEW QUEUE
# ============================================================

def list_reviews(user_id: str, limit: int = 50) -> list[dict]:
    """Memories Jev was not confident enough to act on."""
    return get_engine().list_pending_reviews(user_id, limit=limit)


def resolve_review(
    user_id: str,
    review_id: str,
    action: str,
    target_memory_id: str | None = None,
    resolved_by: str = "human",
) -> dict:
    """Apply a final decision: new | duplicate | update | supersede."""
    return get_engine().resolve_review(
        user_id,
        review_id,
        action=action,
        target_memory_id=target_memory_id,
        resolved_by=resolved_by,
    )


def approve_review(user_id: str, review_id: str, resolved_by: str = "human") -> dict:
    """Accept the action Jev suggested for a queued memory."""
    engine = get_engine()

    review = engine.reviews.find_one(
        {"review_id": review_id, "user_id": user_id, "status": "pending"}
    )

    if review is None:
        raise ValueError("No pending review with that id for this user.")

    if not review.get("suggested_action"):
        raise ValueError(
            "Jev made no suggestion for this review. "
            "Use resolve_review() with an explicit action."
        )

    return engine.resolve_review(
        user_id,
        review_id,
        action=review["suggested_action"],
        target_memory_id=review.get("suggested_target_memory_id"),
        resolved_by=resolved_by,
    )


def reject_review(user_id: str, review_id: str, resolved_by: str = "human") -> dict:
    """Ignore Jev's suggestion and store the memory as independent."""
    return get_engine().resolve_review(
        user_id, review_id, action="new", resolved_by=resolved_by
    )


# ============================================================
# MONITORING
# ============================================================

def outbox_stats() -> dict:
    """Per-target sync counts and the age of the oldest pending change."""
    engine = get_engine()
    stats: dict = {}

    for target, enabled in engine.sync_targets.items():
        if not enabled:
            continue

        counts = {
            str(doc["_id"]): doc["count"]
            for doc in engine.outbox.aggregate([
                {"$group": {"_id": f"${target}.status", "count": {"$sum": 1}}}
            ])
        }

        oldest = engine.outbox.find_one(
            {f"{target}.status": "pending"},
            sort=[("updated_at", 1)],
        )

        age = 0.0

        if oldest:
            ts = oldest["updated_at"]

            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)

            age = (utc_now() - ts).total_seconds()

        stats[target] = {
            "counts": counts,
            "oldest_pending_age_seconds": round(age, 1),
        }

    stats["pending_reviews"] = engine.reviews.count_documents(
        {"status": "pending"}
    )

    return stats


def health_check() -> dict:
    """
    For a readiness endpoint. "ok" is False only when a required
    dependency is down. Alert separately on large outbox backlogs or
    many failed rows using outbox_stats().
    """
    report: dict = {"ok": True, "checked_at": utc_now().isoformat()}

    engine = get_engine()

    try:
        engine.memories.database.command("ping")
        report["mongodb"] = "ok"
    except Exception as exc:
        report["mongodb"] = repr(exc)
        report["ok"] = False

    driver = get_neo4j_driver()

    if driver is not None:
        try:
            driver.verify_connectivity()
            report["neo4j"] = "ok"
        except Exception as exc:
            report["neo4j"] = repr(exc)
            report["ok"] = False

    index = get_pinecone_index()

    if index is not None:
        try:
            index.describe_index_stats()
            report["pinecone"] = "ok"
        except Exception as exc:
            report["pinecone"] = repr(exc)
            report["ok"] = False

    return report