
from typing import Any
from datetime import datetime

from ..schema import MemorySchema


class PineconeFactory:

    @classmethod
    def create(
        cls,
        memory: dict[str, Any],
        embedding: dict[str, Any]
    ) -> dict[str, Any]:

        if not isinstance(memory, dict):
            raise TypeError("Memory must be a dictionary.")

        if not isinstance(embedding, dict):
            raise TypeError("Embedding must be a dictionary.")

        memory_id = memory.get("memory_id")
        embedding_id = embedding.get("id")
        values = embedding.get("values")
        memory.pop("_id", None)  # Remove MongoDB's internal ID if present

        if not isinstance(memory_id, str) or not memory_id.strip():
            raise ValueError("A valid memory_id is required.")

        if memory_id != embedding_id:
            raise ValueError(
                f"Memory ID mismatch: {memory_id} != {embedding_id}"
            )

        if not isinstance(values, list) or not values:
            raise ValueError("A valid embedding vector is required.")

        if not all(
            isinstance(value, (int, float)) and not isinstance(value, bool)
            for value in values
        ):
            raise ValueError("Embedding must contain only numbers.")

        validated_memory = MemorySchema.model_validate(memory)
        memory_data = validated_memory.model_dump(mode="python")

        metadata = {
            "user_id": memory_data["user_id"],
            "memory_type": memory_data["memory_type"],
            "category": memory_data["category"],
            "confidence": memory_data["confidence"],
            "importance": memory_data["importance"],
            "stability": memory_data["stability"],
            "reusability": memory_data["reusability"],
        }

        timespan = memory_data.get("timespan")
        if timespan:
            if timespan.get("start") is not None:
                metadata["timespan_start"] = timespan["start"]

            if timespan.get("end") is not None:
                metadata["timespan_end"] = timespan["end"]

        validity = memory_data.get("validity", {})

        if validity.get("valid_from") is not None:
            metadata["valid_from"] = cls.serialize_datetime(
                validity["valid_from"]
            )

        if validity.get("valid_until") is not None:
            metadata["valid_until"] = cls.serialize_datetime(
                validity["valid_until"]
            )

        metadata["is_current"] = validity.get("is_current", True)

        source = memory_data.get("source", {})

        if source.get("speaker") is not None:
            metadata["speaker"] = source["speaker"]

        if source.get("source_type") is not None:
            metadata["source_type"] = source["source_type"]

        if source.get("timestamp") is not None:
            metadata["source_timestamp"] = cls.serialize_datetime(
                source["timestamp"]
            )

        return {
            "id": memory_id,
            "values": values,
            "metadata": metadata
        }

    @classmethod
    def to_documents(
        cls,
        memories: list[dict[str, Any]],
        embeddings: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:

        if not isinstance(memories, list):
            raise TypeError("memories must be a list.")

        if not isinstance(embeddings, list):
            raise TypeError("embeddings must be a list.")

        embedding_lookup = {}

        for embedding in embeddings:

            if not isinstance(embedding, dict):
                raise TypeError("Each embedding must be a dictionary.")

            embedding_id = embedding.get("id")

            if not isinstance(embedding_id, str) or not embedding_id.strip():
                raise ValueError("Every embedding must have a valid ID.")

            if embedding_id in embedding_lookup:
                raise ValueError(f"Duplicate embedding ID: {embedding_id}")

            embedding_lookup[embedding_id] = embedding

        documents = []

        for memory in memories:

            if not isinstance(memory, dict):
                raise TypeError("Each memory must be a dictionary.")

            memory_id = memory.get("memory_id")

            if memory_id not in embedding_lookup:
                raise ValueError(
                    f"No embedding found for memory ID: {memory_id}"
                )

            document = cls.create(
                memory=memory,
                embedding=embedding_lookup[memory_id]
            )

            documents.append(document)

        return documents

    @staticmethod
    def serialize_datetime(value: datetime) -> str:
        return value.isoformat()
