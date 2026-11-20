
from typing import Any
from hashlib import sha256


class GraphFactory:

    @staticmethod
    def create_entity_id(
        user_id: str,
        name: str,
        entity_type: str
    ) -> str:

        raw_id = (
            f"{user_id}:"
            f"{entity_type.strip().lower()}:"
            f"{name.strip().lower()}"
        )

        return sha256(raw_id.encode("utf-8")).hexdigest()

    @staticmethod
    def normalize_entity_name(name: str) -> str:

        name = name.strip()

        aliases = {
            "i": "User",
            "me": "User",
            "myself": "User",
            "the user": "User",
            "user": "User"
        }

        return aliases.get(name.lower(), name)

    @classmethod
    def create(
        cls,
        memory: dict[str, Any]
    ) -> dict[str, Any]:

        if not isinstance(memory, dict):
            raise TypeError("Memory must be a dictionary.")

        memory_id = memory.get("memory_id")
        user_id = memory.get("user_id")

        if not isinstance(memory_id, str) or not memory_id.strip():
            raise ValueError("A valid memory_id is required.")

        if not isinstance(user_id, str) or not user_id.strip():
            raise ValueError("A valid user_id is required.")

        entities = memory.get("entities") or []
        relationships = memory.get("relationships") or []

        if not isinstance(entities, list):
            raise TypeError("Entities must be a list.")

        if not isinstance(relationships, list):
            raise TypeError("Relationships must be a list.")

        # Temporal information from the memory
        timespan = memory.get("timespan") or {}
        validity = memory.get("validity") or {}

        if not isinstance(timespan, dict):
            raise TypeError("Timespan must be a dictionary.")

        if not isinstance(validity, dict):
            raise TypeError("Validity must be a dictionary.")

        # Build entity lookup
        entity_lookup = {}
        graph_entities = []

        for entity in entities:

            if not isinstance(entity, dict):
                raise TypeError(
                    "Each entity must be a dictionary."
                )

            name = entity.get("name")
            entity_type = entity.get("type")

            if not isinstance(name, str) or not name.strip():
                raise ValueError(
                    "Every entity must have a valid name."
                )

            if not isinstance(entity_type, str) or not entity_type.strip():
                raise ValueError(
                    "Every entity must have a valid type."
                )

            name = cls.normalize_entity_name(name)
            entity_type = entity_type.strip()

            entity_key = name.lower()

            if entity_key in entity_lookup:

                existing = entity_lookup[entity_key]

                if existing["type"].lower() != entity_type.lower():
                    raise ValueError(
                        f"Conflicting entity types for: {name}"
                    )

                continue

            entity_id = cls.create_entity_id(
                user_id=user_id,
                name=name,
                entity_type=entity_type
            )

            entity_data = {
                "entity_id": entity_id,
                "user_id": user_id,
                "name": name,
                "type": entity_type
            }

            entity_lookup[entity_key] = entity_data
            graph_entities.append(entity_data)

        # Build relationship edges
        graph_relationships = []

        for relationship in relationships:

            if not isinstance(relationship, dict):
                raise TypeError(
                    "Each relationship must be a dictionary."
                )

            subject = relationship.get("subject")
            predicate = relationship.get("predicate")
            object_ = relationship.get("object")

            if not all(
                isinstance(value, str) and value.strip()
                for value in [subject, predicate, object_]
            ):
                raise ValueError(
                    "Every relationship requires "
                    "subject, predicate and object."
                )

            subject = cls.normalize_entity_name(subject)
            object_ = cls.normalize_entity_name(object_)

            subject_key = subject.lower()
            object_key = object_.lower()

            # Automatically create the User entity if missing.
            for name, key in [
                (subject, subject_key),
                (object_, object_key)
            ]:

                if key == "user" and key not in entity_lookup:

                    user_entity = {
                        "entity_id": cls.create_entity_id(
                            user_id,
                            "User",
                            "Person"
                        ),
                        "user_id": user_id,
                        "name": "User",
                        "type": "Person"
                    }

                    entity_lookup[key] = user_entity
                    graph_entities.append(user_entity)

            subject_data = entity_lookup.get(subject_key)
            object_data = entity_lookup.get(object_key)

            if not subject_data or not object_data:
                raise ValueError(
                    f"Relationship entities not found: "
                    f"{subject} -> {object_}. "
                    "Make sure both entities exist."
                )

            relationship_data = {
                "user_id": user_id,
                "memory_id": memory_id,

                "subject_id": subject_data["entity_id"],
                "predicate": predicate.strip().upper(),
                "object_id": object_data["entity_id"],

                # User-mentioned time period
                "timespan_start": timespan.get("start"),
                "timespan_end": timespan.get("end"),

                # Memory-system validity
                "valid_from": validity.get("valid_from"),
                "valid_until": validity.get("valid_until"),
                "is_current": validity.get("is_current"),

                "confidence": relationship.get(
                    "confidence",
                    memory.get("confidence")
                )
            }

            graph_relationships.append(relationship_data)

        # Memory node data
        graph_memory = {
            "memory_id": memory_id,
            "user_id": user_id,

            "memory_type": memory.get("memory_type"),
            "category": memory.get("category"),

            "confidence": memory.get("confidence"),
            "importance": memory.get("importance"),

            # User-mentioned time period
            "timespan_start": timespan.get("start"),
            "timespan_end": timespan.get("end"),

            # Memory-system validity
            "valid_from": validity.get("valid_from"),
            "valid_until": validity.get("valid_until"),
            "is_current": validity.get("is_current")
        }

        return {
            "memory": graph_memory,
            "entities": graph_entities,
            "relationships": graph_relationships
        }

    @classmethod
    def to_documents(
        cls,
        memories: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:

        if not isinstance(memories, list):
            raise TypeError("Memories must be a list.")

        return [
            cls.create(memory)
            for memory in memories
        ]

from typing import Any
from hashlib import sha256


class GraphFactory:

    @staticmethod
    def create_entity_id(
        user_id: str,
        name: str,
        entity_type: str
    ) -> str:

        raw_id = (
            f"{user_id}:"
            f"{entity_type.strip().lower()}:"
            f"{name.strip().lower()}"
        )

        return sha256(raw_id.encode("utf-8")).hexdigest()

    @staticmethod
    def normalize_entity_name(name: str) -> str:

        name = name.strip()

        aliases = {
            "i": "User",
            "me": "User",
            "myself": "User",
            "the user": "User",
            "user": "User"
        }

        return aliases.get(name.lower(), name)

    @classmethod
    def create(
        cls,
        memory: dict[str, Any]
    ) -> dict[str, Any]:

        if not isinstance(memory, dict):
            raise TypeError("Memory must be a dictionary.")

        memory_id = memory.get("memory_id")
        user_id = memory.get("user_id")

        if not isinstance(memory_id, str) or not memory_id.strip():
            raise ValueError("A valid memory_id is required.")

        if not isinstance(user_id, str) or not user_id.strip():
            raise ValueError("A valid user_id is required.")

        entities = memory.get("entities") or []
        relationships = memory.get("relationships") or []

        if not isinstance(entities, list):
            raise TypeError("Entities must be a list.")

        if not isinstance(relationships, list):
            raise TypeError("Relationships must be a list.")

        # Temporal information from the memory
        timespan = memory.get("timespan") or {}
        validity = memory.get("validity") or {}

        if not isinstance(timespan, dict):
            raise TypeError("Timespan must be a dictionary.")

        if not isinstance(validity, dict):
            raise TypeError("Validity must be a dictionary.")

        # Build entity lookup
        entity_lookup = {}
        graph_entities = []

        for entity in entities:

            if not isinstance(entity, dict):
                raise TypeError(
                    "Each entity must be a dictionary."
                )

            name = entity.get("name")
            entity_type = entity.get("type")

            if not isinstance(name, str) or not name.strip():
                raise ValueError(
                    "Every entity must have a valid name."
                )

            if not isinstance(entity_type, str) or not entity_type.strip():
                raise ValueError(
                    "Every entity must have a valid type."
                )

            name = cls.normalize_entity_name(name)
            entity_type = entity_type.strip()

            entity_key = name.lower()

            if entity_key in entity_lookup:

                existing = entity_lookup[entity_key]

                if existing["type"].lower() != entity_type.lower():
                    raise ValueError(
                        f"Conflicting entity types for: {name}"
                    )

                continue

            entity_id = cls.create_entity_id(
                user_id=user_id,
                name=name,
                entity_type=entity_type
            )

            entity_data = {
                "entity_id": entity_id,
                "user_id": user_id,
                "name": name,
                "type": entity_type
            }

            entity_lookup[entity_key] = entity_data
            graph_entities.append(entity_data)

        # Build relationship edges
        graph_relationships = []

        for relationship in relationships:

            if not isinstance(relationship, dict):
                raise TypeError(
                    "Each relationship must be a dictionary."
                )

            subject = relationship.get("subject")
            predicate = relationship.get("predicate")
            object_ = relationship.get("object")

            if not all(
                isinstance(value, str) and value.strip()
                for value in [subject, predicate, object_]
            ):
                raise ValueError(
                    "Every relationship requires "
                    "subject, predicate and object."
                )

            subject = cls.normalize_entity_name(subject)
            object_ = cls.normalize_entity_name(object_)

            subject_key = subject.lower()
            object_key = object_.lower()

            # Automatically create the User entity if missing.
            for name, key in [
                (subject, subject_key),
                (object_, object_key)
            ]:

                if key == "user" and key not in entity_lookup:

                    user_entity = {
                        "entity_id": cls.create_entity_id(
                            user_id,
                            "User",
                            "Person"
                        ),
                        "user_id": user_id,
                        "name": "User",
                        "type": "Person"
                    }

                    entity_lookup[key] = user_entity
                    graph_entities.append(user_entity)

            subject_data = entity_lookup.get(subject_key)
            object_data = entity_lookup.get(object_key)

            if not subject_data or not object_data:
                raise ValueError(
                    f"Relationship entities not found: "
                    f"{subject} -> {object_}. "
                    "Make sure both entities exist."
                )

            relationship_data = {
                "user_id": user_id,
                "memory_id": memory_id,

                "subject_id": subject_data["entity_id"],
                "predicate": predicate.strip().upper(),
                "object_id": object_data["entity_id"],

                # User-mentioned time period
                "timespan_start": timespan.get("start"),
                "timespan_end": timespan.get("end"),

                # Memory-system validity
                "valid_from": validity.get("valid_from"),
                "valid_until": validity.get("valid_until"),
                "is_current": validity.get("is_current"),

                "confidence": relationship.get(
                    "confidence",
                    memory.get("confidence")
                )
            }

            graph_relationships.append(relationship_data)

        # Memory node data
        graph_memory = {
            "memory_id": memory_id,
            "user_id": user_id,

            "memory_type": memory.get("memory_type"),
            "category": memory.get("category"),

            "confidence": memory.get("confidence"),
            "importance": memory.get("importance"),

            # User-mentioned time period
            "timespan_start": timespan.get("start"),
            "timespan_end": timespan.get("end"),

            # Memory-system validity
            "valid_from": validity.get("valid_from"),
            "valid_until": validity.get("valid_until"),
            "is_current": validity.get("is_current")
        }

        return {
            "memory": graph_memory,
            "entities": graph_entities,
            "relationships": graph_relationships
        }

    @classmethod
    def to_documents(
        cls,
        memories: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:

        if not isinstance(memories, list):
            raise TypeError("Memories must be a list.")

        return [
            cls.create(memory)
            for memory in memories
        ]
