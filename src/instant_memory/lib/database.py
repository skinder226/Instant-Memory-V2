
import os
import re
import time

from dotenv import load_dotenv
from pymongo import MongoClient, ASCENDING
from pinecone import Pinecone, ServerlessSpec
from neo4j import GraphDatabase


load_dotenv()


class DatabaseManager:

    def __init__(self):
        # MongoDB
        self.mongo_client = None
        self.mongo_db = None

        # Pinecone
        self.pinecone = None
        self.pinecone_index = None

        # Neo4j
        self.neo4j = None

    # --------------------------------------------------
    # CONNECT ALL DATABASES
    # --------------------------------------------------

    def connect(self):
        try:
            self.connect_mongodb()
            self.connect_pinecone()
            self.connect_neo4j()

            return self

        except Exception:
            self.close()
            raise

    # --------------------------------------------------
    # MONGODB
    # --------------------------------------------------

    def connect_mongodb(self):

        mongo_uri = (
            os.getenv("MONGODB_URI")
            or os.getenv("MONGO_URI")
        )

        database_name = (
            os.getenv("MONGODB_DATABASE")
            or os.getenv("MONGO_DB")
            or os.getenv("MONGODB_DB")
        )

        if not mongo_uri:
            raise ValueError("MongoDB URI is missing from .env")

        if not database_name:
            raise ValueError("MongoDB database name is missing from .env")

        self.mongo_client = MongoClient(mongo_uri)

        self.mongo_client.admin.command("ping")

        self.mongo_db = self.mongo_client[database_name]

        print("MongoDB connected")

        self.setup_mongodb_indexes()

    def setup_mongodb_indexes(self):

        self.mongo_db["memories"].create_index(
            [
                ("user_id", ASCENDING),
                ("memory_id", ASCENDING)
            ],
            unique=True
        )

        print("MongoDB indexes created")

    def collection(self, name):

        if self.mongo_db is None:
            raise RuntimeError("MongoDB is not connected")

        return self.mongo_db[name]

    # --------------------------------------------------
    # PINECONE
    # --------------------------------------------------

    def connect_pinecone(self):

        api_key = os.getenv("PINECONE_API_KEY")

        index_name = (
            os.getenv("PINECONE_INDEX_NAME")
            or os.getenv("PINECONE_INDEX")
            or "instant-memory"
        )

        if not api_key:
            raise ValueError("PINECONE_API_KEY is missing from .env")

        self.pinecone = Pinecone(api_key=api_key)

        existing_indexes = self.pinecone.list_indexes().names()

        if index_name not in existing_indexes:

            print(f"Creating Pinecone index: {index_name}")

            self.pinecone.create_index(
                name=index_name,
                dimension=1536,
                metric="cosine",
                spec=ServerlessSpec(
                    cloud="aws",
                    region="us-east-1"
                )
            )

            while not self.pinecone.describe_index(
                index_name
            ).status["ready"]:
                time.sleep(1)

        else:
            print(
                f"Pinecone index '{index_name}' already exists."
            )

        self.pinecone_index = self.pinecone.Index(index_name)

        print(f"Connected to Pinecone index: {index_name}")

    def index(self):

        if self.pinecone_index is None:
            raise RuntimeError("Pinecone is not connected")

        return self.pinecone_index

    def upsert_memories(
        self,
        documents,
        user_id,
        batch_size=100
    ):

        if not documents:
            return {"upserted_count": 0}

        if self.pinecone_index is None:
            raise RuntimeError("Pinecone is not connected")

        total_upserted = 0

        for start in range(0, len(documents), batch_size):

            batch = documents[start:start + batch_size]

            response = self.pinecone_index.upsert(
                vectors=batch,
                namespace=user_id
            )

            total_upserted += response.upserted_count

        print(
            f"Inserted {total_upserted} embeddings into Pinecone index."
        )

        return {"upserted_count": total_upserted}

    # --------------------------------------------------
    # NEO4J
    # --------------------------------------------------

    def connect_neo4j(self):

        uri = os.getenv("NEO4J_URI")

        username = (
            os.getenv("NEO4J_USERNAME")
            or os.getenv("NEO4J_USER")
        )

        password = os.getenv("NEO4J_PASSWORD")

        if not uri:
            raise ValueError("NEO4J_URI is missing from .env")

        if not username:
            raise ValueError("NEO4J_USERNAME is missing from .env")

        if not password:
            raise ValueError("NEO4J_PASSWORD is missing from .env")

        self.neo4j = GraphDatabase.driver(
            uri,
            auth=(username, password)
        )

        self.neo4j.verify_connectivity()

        print("Neo4j connected")

        self.setup_graph_constraints()

    def setup_graph_constraints(self):

        queries = [

            """
            CREATE CONSTRAINT memory_id_unique IF NOT EXISTS
            FOR (m:Memory)
            REQUIRE m.memory_id IS UNIQUE
            """,

            """
            CREATE CONSTRAINT entity_id_unique IF NOT EXISTS
            FOR (e:Entity)
            REQUIRE e.entity_id IS UNIQUE
            """

        ]

        with self.neo4j.session() as session:

            for query in queries:
                session.run(query).consume()

        print("Neo4j constraints created")

    # --------------------------------------------------
    # UPSERT GRAPHS
    # --------------------------------------------------

    def upsert_graphs(self, graphs):

        if self.neo4j is None:
            raise RuntimeError("Neo4j is not connected")

        if not graphs:
            return []

        results = []

        with self.neo4j.session() as session:

            for graph in graphs:

                result = session.execute_write(
                    self._create_graph,
                    graph
                )

                results.append(result)

        return results

    # --------------------------------------------------
    # CREATE GRAPH
    # --------------------------------------------------

    def _create_graph(self, tx, graph):

        memory = graph["memory"]
        entities = graph["entities"]
        relationships = graph["relationships"]

        # ----------------------------------------------
        # 1. CREATE MEMORY NODE
        # ----------------------------------------------

        result = tx.run(
            """
            MERGE (m:Memory {
                memory_id: $memory_id
            })

            ON CREATE SET
                m.user_id = $user_id

            WITH m

            WHERE m.user_id = $user_id

            SET
                m.memory_type = $memory_type,
                m.category = $category,
                m.confidence = $confidence,
                m.importance = $importance,
                m.timespan_start = $timespan_start,
                m.timespan_end = $timespan_end,
                m.valid_from = $valid_from,
                m.valid_until = $valid_until,
                m.is_current = $is_current,
                m.updated_at = datetime()

            RETURN m.memory_id AS memory_id
            """,
            **memory
        ).single()

        if result is None:
            raise ValueError(
                f"Memory {memory['memory_id']} belongs to another user."
            )

        # ----------------------------------------------
        # 2. CREATE ENTITY NODES
        # ----------------------------------------------

        if entities:

            tx.run(
                """
                UNWIND $entities AS entity

                MERGE (e:Entity {
                    entity_id: entity.entity_id
                })

                ON CREATE SET
                    e.user_id = entity.user_id

                WITH e, entity

                WHERE e.user_id = entity.user_id

                SET
                    e.name = entity.name,
                    e.type = entity.type,
                    e.updated_at = datetime()

                RETURN e.entity_id AS entity_id
                """,
                entities=entities
            ).consume()

            # ------------------------------------------
            # 3. CONNECT MEMORY TO ENTITIES
            # ------------------------------------------

            tx.run(
                """
                UNWIND $entities AS entity

                MATCH (m:Memory {
                    memory_id: $memory_id,
                    user_id: $user_id
                })

                MATCH (e:Entity {
                    entity_id: entity.entity_id,
                    user_id: $user_id
                })

                MERGE (m)-[:MENTIONS]->(e)
                """,
                entities=entities,
                memory_id=memory["memory_id"],
                user_id=memory["user_id"]
            ).consume()

        # ----------------------------------------------
        # 4. CREATE MEANINGFUL RELATIONSHIPS
        # ----------------------------------------------

        for rel in relationships:

            predicate = rel["predicate"].upper()

            # Only allow safe relationship type names.
            if not re.fullmatch(
                r"[A-Z][A-Z0-9_]*",
                predicate
            ):
                raise ValueError(
                    f"Invalid relationship type: {predicate}"
                )

            query = f"""
            MATCH (s:Entity {{
                entity_id: $subject_id,
                user_id: $user_id
            }})

            MATCH (o:Entity {{
                entity_id: $object_id,
                user_id: $user_id
            }})

            MATCH (m:Memory {{
                memory_id: $memory_id,
                user_id: $user_id
            }})

            MERGE (s)-[r:`{predicate}` {{
                memory_id: $memory_id,
                user_id: $user_id
            }}]->(o)

            SET
                r.timespan_start = $timespan_start,
                r.timespan_end = $timespan_end,
                r.valid_from = $valid_from,
                r.valid_until = $valid_until,
                r.is_current = $is_current,
                r.confidence = $confidence,
                r.updated_at = datetime()

            RETURN type(r) AS relationship_type
            """

            tx.run(
                query,
                subject_id=rel["subject_id"],
                object_id=rel["object_id"],
                memory_id=rel["memory_id"],
                user_id=rel["user_id"],
                timespan_start=rel["timespan_start"],
                timespan_end=rel["timespan_end"],
                valid_from=rel["valid_from"],
                valid_until=rel["valid_until"],
                is_current=rel["is_current"],
                confidence=rel["confidence"]
            ).consume()

        return memory["memory_id"]

    # --------------------------------------------------
    # NEO4J QUERY
    # --------------------------------------------------

    def graph(self, query, parameters=None):

        if self.neo4j is None:
            raise RuntimeError("Neo4j is not connected")

        with self.neo4j.session() as session:

            result = session.run(
                query,
                parameters or {}
            )

            return [
                record.data()
                for record in result
            ]

    # --------------------------------------------------
    # CLOSE CONNECTIONS
    # --------------------------------------------------

    def close(self):

        if self.mongo_client is not None:
            self.mongo_client.close()
            self.mongo_client = None
            self.mongo_db = None

        if self.neo4j is not None:
            self.neo4j.close()
            self.neo4j = None

        self.pinecone_index = None
        self.pinecone = None

        print("Database connections closed")
