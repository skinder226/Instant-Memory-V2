
from typing import Any
from datetime import datetime, timezone

from ..schema import OutboxSchema


class OutboxFactory:

    @classmethod
    def create(cls, data: dict[str, Any]) -> OutboxSchema:

        if not isinstance(data, dict):
            raise TypeError("Outbox data must be a dictionary.")

        return OutboxSchema.model_validate(data)

    @classmethod
    def to_documents(
        cls,
        memories: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:

        if not isinstance(memories, list):
            raise TypeError("memories must be a list.")

        documents = []

        for memory in memories:

            if not isinstance(memory, dict):
                raise TypeError("Each memory must be a dictionary.")

            memory_id = memory.get("memory_id")

            if not isinstance(memory_id, str) or not memory_id.strip():
                raise ValueError("A valid memory_id is required.")

            now = datetime.now(timezone.utc)

            outbox_data = {
                "memory_id": memory_id,

                "pinecone": {
                    "status": "pending",
                    "error": None,
                    "synced_at": None
                },

                "neo4j": {
                    "status": "pending",
                    "error": None,
                    "synced_at": None
                },

                "created_at": now,
                "updated_at": now
            }

            outbox = cls.create(outbox_data)

            documents.append(
                outbox.model_dump(mode="python")
            )

        return documents
