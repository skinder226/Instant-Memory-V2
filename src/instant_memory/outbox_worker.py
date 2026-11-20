# instant_memory/outbox_worker.py
"""
Outbox worker: keeps Pinecone and Neo4j in sync with MongoDB.

    python -m instant_memory.outbox_worker                 run forever
    python -m instant_memory.outbox_worker --backfill      queue ALL active memories, then exit
    python -m instant_memory.outbox_worker --backfill --user-id U
    python -m instant_memory.outbox_worker --retry-failed  requeue rows that exhausted retries
    python -m instant_memory.outbox_worker --stats         print backlog numbers, then exit

How it works
------------
* The engine writes ONE outbox row per memory, with independent state for
  each target (pinecone, neo4j): pending -> processing -> completed | failed.
* Rows are claimed atomically with a lease, so you can run any number of
  worker processes. A worker that crashes mid-row loses its lease and the
  row is picked up again.
* The memory is read from MongoDB at processing time, so the index always
  receives the latest state. All operations are idempotent.
* A completed/failed result is written with a filter on the row `version`.
  If the engine queued a newer change meanwhile, the stale result is
  dropped and the row stays pending for the newer change.
* Failures retry with exponential backoff and jitter, then become "failed"
  (dead letter) after WORKER_MAX_ATTEMPTS. SIGTERM/SIGINT finish the current
  row and exit cleanly.

Neo4j graph model written by this worker (the engine's graph search reads it)
    (:Memory {user_id, memory_id, status, ...})-[:MENTIONS]->(:Entity {user_id, key, name, type})
    (:Entity)-[:RELATES_TO {user_id, memory_id, predicate}]->(:Entity)
Entities are scoped per user. `key` is the lowercase, trimmed entity name.
"""

from __future__ import annotations

import argparse
import json
import logging
import random
import signal
import threading
import time
from datetime import timedelta
from typing import Callable

from pymongo import ASCENDING, ReturnDocument

from instant_memory.config import (
    Settings,
    close_all,
    get_embedder,
    get_engine,
    get_neo4j_driver,
    get_pinecone_index,
    get_settings,
)
from instant_memory.deduplication_engine import (
    DeduplicationEngine,
    embedding_text,
    utc_now,
)
from instant_memory.memory_service import outbox_stats

logger = logging.getLogger("instant_memory.outbox_worker")

try:  # Deleting from a namespace that does not exist yet is not an error.
    from pinecone.exceptions import NotFoundException
except Exception:  # pragma: no cover
    NotFoundException = None


GRAPH_SCHEMA = [
    "CREATE CONSTRAINT memory_identity IF NOT EXISTS "
    "FOR (m:Memory) REQUIRE (m.user_id, m.memory_id) IS UNIQUE",
    "CREATE CONSTRAINT entity_identity IF NOT EXISTS "
    "FOR (e:Entity) REQUIRE (e.user_id, e.key) IS UNIQUE",
]

# Remove the relationships this memory created. Must run BEFORE the
# MENTIONS links are replaced, because it finds them through MENTIONS.
Q_REMOVE_RELATIONSHIPS = """
MATCH (m:Memory {user_id: $user_id, memory_id: $memory_id})
      -[:MENTIONS]->(:Entity)-[r:RELATES_TO {memory_id: $memory_id}]-()
WITH DISTINCT r
DELETE r
"""

Q_UPSERT_MEMORY = """
MERGE (m:Memory {user_id: $user_id, memory_id: $memory_id})
SET m.status = $status,
    m.memory_type = $memory_type,
    m.category = $category,
    m.importance = $importance,
    m.confidence = $confidence,
    m.updated_at = $updated_at
WITH m
OPTIONAL MATCH (m)-[old:MENTIONS]->(:Entity)
DELETE old
"""

Q_LINK_ENTITIES = """
MATCH (m:Memory {user_id: $user_id, memory_id: $memory_id})
UNWIND $entities AS ent
MERGE (e:Entity {user_id: $user_id, key: ent.key})
SET e.name = coalesce(e.name, ent.name),
    e.type = CASE WHEN ent.type <> '' THEN ent.type
                  ELSE coalesce(e.type, '') END
MERGE (m)-[:MENTIONS]->(e)
"""

Q_CREATE_RELATIONSHIPS = """
UNWIND $relationships AS rel
MATCH (s:Entity {user_id: $user_id, key: rel.subject_key})
MATCH (o:Entity {user_id: $user_id, key: rel.object_key})
MERGE (s)-[:RELATES_TO {
    user_id: $user_id,
    memory_id: $memory_id,
    predicate: rel.predicate
}]->(o)
"""

Q_DELETE_MEMORY = """
MATCH (m:Memory {user_id: $user_id, memory_id: $memory_id})
OPTIONAL MATCH (m)-[:MENTIONS]->(e:Entity)
WITH m, collect(e.key) AS keys
DETACH DELETE m
RETURN keys
"""

Q_DELETE_ORPHAN_ENTITIES = """
UNWIND $keys AS key
MATCH (e:Entity {user_id: $user_id, key: key})
WHERE NOT (e)--()
DELETE e
"""


class OutboxWorker:

    def __init__(
        self,
        engine: DeduplicationEngine,
        *,
        pinecone_index=None,
        neo4j_driver=None,
        embed: Callable[[str], list[float]] | None = None,
        embedding_dimensions: int | None = None,
        lease_seconds: int = 120,
        max_attempts: int = 8,
        backoff_base: float = 5.0,
        backoff_cap: float = 600.0,
        idle_seconds: float = 2.0,
        stop_event: threading.Event | None = None,
    ):
        if pinecone_index is not None and embed is None:
            raise ValueError("embed is required when pinecone_index is set.")

        self.engine = engine
        self.pinecone = pinecone_index
        self.neo4j = neo4j_driver
        self.embed = embed
        self.embedding_dimensions = embedding_dimensions

        self.lease_seconds = lease_seconds
        self.max_attempts = max_attempts
        self.backoff_base = backoff_base
        self.backoff_cap = backoff_cap
        self.idle_seconds = idle_seconds
        self.stop = stop_event or threading.Event()

        self.targets: list[str] = []

        if pinecone_index is not None:
            self.targets.append("pinecone")

        if neo4j_driver is not None:
            self.targets.append("neo4j")

    # --------------------------------------------------------
    # Claim / complete / fail
    # --------------------------------------------------------

    def _claim(self, target: str) -> dict | None:
        """Atomically take one eligible row for this target."""
        now = utc_now()

        status = f"{target}.status"
        next_at = f"{target}.next_attempt_at"
        locked = f"{target}.locked_until"

        return self.engine.outbox.find_one_and_update(
            {
                "$or": [
                    # Ready (and past its backoff delay).
                    {
                        status: "pending",
                        "$or": [{next_at: None}, {next_at: {"$lte": now}}],
                    },
                    # A worker died: its lease expired.
                    {status: "processing", locked: {"$lt": now}},
                ]
            },
            {
                "$set": {
                    status: "processing",
                    locked: now + timedelta(seconds=self.lease_seconds),
                },
                "$inc": {f"{target}.attempts": 1},
            },
            sort=[("updated_at", ASCENDING)],
            return_document=ReturnDocument.AFTER,
        )

    def _finish(self, target: str, row: dict, fields: dict, outcome: str):
        result = self.engine.outbox.update_one(
            # Version guard: never overwrite a newer queued change.
            {"_id": row["_id"], "version": row["version"]},
            {"$set": {f"{target}.{k}": v for k, v in fields.items()}},
        )

        if result.matched_count == 0:
            logger.info(
                "%s %s memory_id=%s skipped: a newer change was queued "
                "while processing",
                target, outcome, row["memory_id"],
            )

    def _complete(self, target: str, row: dict):
        self._finish(
            target,
            row,
            {
                "status": "completed",
                "error": None,
                "synced_at": utc_now(),
                "locked_until": None,
                "next_attempt_at": None,
            },
            "completed",
        )

    def _backoff(self, attempts: int) -> float:
        delay = min(self.backoff_cap, self.backoff_base * (2 ** (attempts - 1)))
        return delay * random.uniform(0.8, 1.2)

    def _fail(self, target: str, row: dict, exc: Exception):
        attempts = row[target]["attempts"]  # already counted by _claim
        error = repr(exc)[:1000]

        if attempts >= self.max_attempts:
            logger.error(
                "%s sync FAILED permanently memory_id=%s after %d attempts: %s",
                target, row["memory_id"], attempts, error,
            )

            fields = {
                "status": "failed",
                "error": error,
                "locked_until": None,
                "next_attempt_at": None,
            }
        else:
            delay = self._backoff(attempts)

            logger.warning(
                "%s sync failed memory_id=%s (attempt %d/%d), retry in %.0fs: %s",
                target, row["memory_id"], attempts, self.max_attempts,
                delay, error,
            )

            fields = {
                "status": "pending",
                "error": error,
                "locked_until": None,
                "next_attempt_at": utc_now() + timedelta(seconds=delay),
            }

        self._finish(target, row, fields, "failure")

    # --------------------------------------------------------
    # Pinecone
    # --------------------------------------------------------

    def _pinecone_delete(self, memory_id: str, namespace: str):
        try:
            self.pinecone.delete(ids=[memory_id], namespace=namespace)
        except Exception as exc:
            if NotFoundException is not None and isinstance(exc, NotFoundException):
                return

            raise

    def _sync_pinecone(self, row: dict, memory: dict | None):
        memory_id = row["memory_id"]
        namespace = row["user_id"]  # the engine queries this namespace

        if (
            row["operation"] == "delete"
            or memory is None
            or memory.get("status") != "active"
        ):
            self._pinecone_delete(memory_id, namespace)
            return

        vector = self.embed(
            embedding_text(
                memory["content"], memory["summary"], memory["category"]
            )
        )

        if (
            self.embedding_dimensions
            and len(vector) != self.embedding_dimensions
        ):
            raise ValueError(
                f"Embedding has {len(vector)} dimensions, expected "
                f"{self.embedding_dimensions}."
            )

        self.pinecone.upsert(
            vectors=[{
                "id": memory_id,
                "values": vector,
                "metadata": {
                    "memory_id": memory_id,
                    "user_id": memory["user_id"],
                    "status": "active",
                    "memory_type": memory.get("memory_type", ""),
                    "category": memory.get("category", ""),
                },
            }],
            namespace=namespace,
        )

    # --------------------------------------------------------
    # Neo4j
    # --------------------------------------------------------

    def ensure_graph_schema(self):
        """Create the uniqueness constraints (idempotent)."""
        if self.neo4j is None:
            return

        with self.neo4j.session() as session:
            for statement in GRAPH_SCHEMA:
                session.run(statement).consume()

    @staticmethod
    def _graph_params(memory: dict) -> dict:
        entities: dict[str, dict] = {}

        def add_entity(name: str, entity_type: str = ""):
            name = name.strip()

            if not name:
                return

            key = name.lower()
            current = entities.setdefault(
                key, {"key": key, "name": name, "type": ""}
            )

            if entity_type:
                current["type"] = entity_type

        for entity in memory.get("entities", []):
            add_entity(str(entity.get("name", "")), str(entity.get("type", "")))

        relationships = []

        for rel in memory.get("relationships", []):
            subject, predicate, obj = (
                str(rel.get(field, "")).strip()
                for field in ("subject", "predicate", "object")
            )

            if not (subject and predicate and obj):
                continue

            # Every entity a memory touches is linked by MENTIONS, so
            # cleanup can always find its relationships.
            add_entity(subject)
            add_entity(obj)

            relationships.append({
                "subject_key": subject.lower(),
                "object_key": obj.lower(),
                "predicate": predicate,
            })

        updated_at = memory.get("updated_at")

        return {
            "user_id": memory["user_id"],
            "memory_id": memory["memory_id"],
            "status": memory.get("status", "active"),
            "memory_type": memory.get("memory_type", ""),
            "category": memory.get("category", ""),
            "importance": float(memory.get("importance", 0.0)),
            "confidence": float(memory.get("confidence", 0.0)),
            "updated_at": updated_at.isoformat() if updated_at else None,
            "entities": list(entities.values()),
            "relationships": relationships,
        }

    @staticmethod
    def _graph_upsert(tx, params: dict):
        scope = {
            "user_id": params["user_id"],
            "memory_id": params["memory_id"],
        }

        tx.run(Q_REMOVE_RELATIONSHIPS, **scope).consume()
        tx.run(Q_UPSERT_MEMORY, **params).consume()

        if params["entities"]:
            tx.run(
                Q_LINK_ENTITIES, **scope, entities=params["entities"]
            ).consume()

        if params["relationships"]:
            tx.run(
                Q_CREATE_RELATIONSHIPS,
                **scope,
                relationships=params["relationships"],
            ).consume()

    @staticmethod
    def _graph_delete(tx, user_id: str, memory_id: str):
        scope = {"user_id": user_id, "memory_id": memory_id}

        tx.run(Q_REMOVE_RELATIONSHIPS, **scope).consume()

        record = tx.run(Q_DELETE_MEMORY, **scope).single()
        keys = record["keys"] if record else []

        if keys:
            tx.run(
                Q_DELETE_ORPHAN_ENTITIES, user_id=user_id, keys=keys
            ).consume()

    def _sync_neo4j(self, row: dict, memory: dict | None):
        user_id = row["user_id"]
        memory_id = row["memory_id"]

        deleting = (
            row["operation"] == "delete"
            or memory is None
            or memory.get("status") != "active"
        )

        if deleting:
            def work(tx):
                self._graph_delete(tx, user_id, memory_id)
        else:
            params = self._graph_params(memory)

            def work(tx):
                self._graph_upsert(tx, params)

        with self.neo4j.session() as session:
            write = getattr(session, "execute_write", None) or getattr(
                session, "write_transaction"
            )
            write(work)  # may be re-run on transient errors: idempotent

    # --------------------------------------------------------
    # Main loop
    # --------------------------------------------------------

    def process_one(self, target: str) -> bool:
        """Process one row for a target. False when nothing was eligible."""
        row = self._claim(target)

        if row is None:
            return False

        started = time.monotonic()

        try:
            memory = self.engine.memories.find_one({
                "user_id": row["user_id"],
                "memory_id": row["memory_id"],
            })

            if target == "pinecone":
                self._sync_pinecone(row, memory)
            else:
                self._sync_neo4j(row, memory)

        except Exception as exc:
            self._fail(target, row, exc)

        else:
            self._complete(target, row)

            logger.info(
                "%s synced memory_id=%s op=%s ms=%d",
                target, row["memory_id"], row["operation"],
                (time.monotonic() - started) * 1000,
            )

        return True

    def run_forever(self):
        if not self.targets:
            logger.warning(
                "No sync targets configured (set PINECONE_* and/or NEO4J_*)."
            )
            return

        self.ensure_graph_schema()

        logger.info("Outbox worker started, targets=%s", self.targets)

        while not self.stop.is_set():
            did_work = False

            for target in self.targets:
                if self.stop.is_set():
                    break

                try:
                    did_work = self.process_one(target) or did_work
                except Exception:
                    # MongoDB trouble while claiming. Back off, don't die.
                    logger.exception("Outbox claim failed")
                    self.stop.wait(self.idle_seconds * 5)

            if not did_work:
                self.stop.wait(self.idle_seconds)

        logger.info("Outbox worker stopped.")

    def retry_failed(self) -> int:
        """Requeue rows that exhausted their retries."""
        total = 0

        for target in self.targets:
            result = self.engine.outbox.update_many(
                {f"{target}.status": "failed"},
                {"$set": {
                    f"{target}.status": "pending",
                    f"{target}.attempts": 0,
                    f"{target}.error": None,
                    f"{target}.next_attempt_at": None,
                    f"{target}.locked_until": None,
                }},
            )

            total += result.matched_count

        return total


# ============================================================
# ENTRYPOINT
# ============================================================

def build_worker(stop_event: threading.Event | None = None) -> OutboxWorker:
    settings: Settings = get_settings()

    return OutboxWorker(
        get_engine(),
        pinecone_index=get_pinecone_index(),
        neo4j_driver=get_neo4j_driver(),
        embed=get_embedder(),
        embedding_dimensions=settings.embedding_dimensions,
        lease_seconds=settings.worker_lease_seconds,
        max_attempts=settings.worker_max_attempts,
        backoff_base=settings.worker_backoff_base,
        backoff_cap=settings.worker_backoff_cap,
        idle_seconds=settings.worker_idle_seconds,
        stop_event=stop_event,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="instant_memory outbox worker")
    parser.add_argument("--backfill", action="store_true",
                        help="queue all active memories for sync, then exit")
    parser.add_argument("--user-id", help="limit --backfill to one user")
    parser.add_argument("--retry-failed", action="store_true",
                        help="requeue rows that exhausted their retries, then exit")
    parser.add_argument("--stats", action="store_true",
                        help="print backlog numbers, then exit")
    args = parser.parse_args(argv)

    settings = get_settings()

    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    stop = threading.Event()

    try:
        if args.stats:
            print(json.dumps(outbox_stats(), indent=2, default=str))
            return 0

        if args.backfill:
            queued = get_engine().reindex_all(user_id=args.user_id)
            logger.info("Queued %d memories for sync.", queued)
            return 0

        worker = build_worker(stop)

        if args.retry_failed:
            logger.info("Requeued %d failed rows.", worker.retry_failed())
            return 0

        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, lambda *_: stop.set())

        worker.run_forever()
        return 0

    finally:
        close_all()


if __name__ == "__main__":
    raise SystemExit(main())