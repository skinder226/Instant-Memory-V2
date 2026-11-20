from typing import Any
from copy import deepcopy
from uuid import uuid4
from datetime import datetime, timezone

from .schema import MemorySchema


class MemoryFactory:

    @classmethod
    def create(cls, data: dict[str, Any]) -> MemorySchema:
        if not isinstance(data, dict):
            raise TypeError("Memory data must be a dictionary.")

        return MemorySchema.model_validate(data)

    @classmethod
    def to_documents(
        cls,
        memories: list[dict[str, Any]],
        user_id: str,
        speaker: str
    ) -> list[dict[str, Any]]:

        if not isinstance(memories, list):
            raise TypeError("memories must be a list.")

        if not isinstance(user_id, str) or not user_id.strip():
            raise ValueError("A valid user_id is required.")

        documents = []

        for item in memories:
            item = deepcopy(item)

            item["memory_id"] = str(uuid4())
            item["user_id"] = user_id
            item["validity"] = {
                "valid_from": datetime.now(timezone.utc),
                "valid_until": None,
                "is_current": True
            }
            item["source"]["speaker"] = speaker


            memory = cls.create(item)

            documents.append(
                memory.model_dump(mode="python")
            )

        return documents