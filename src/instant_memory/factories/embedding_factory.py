
from typing import Any
import os

from dotenv import load_dotenv
from langchain_google_genai import GoogleGenerativeAIEmbeddings


load_dotenv()


class EmbeddingFactory:

    def __init__(self):

        api_key = os.getenv("GOOGLE_API_KEY")

        if not api_key:
            raise ValueError("GOOGLE_API_KEY is missing.")

        # Embeddings for storing memories
        self.document_model = GoogleGenerativeAIEmbeddings(
            model="gemini-embedding-001",
            google_api_key=api_key,
            task_type="RETRIEVAL_DOCUMENT",
            output_dimensionality=1536
        )

        # Embeddings for searching memories
        self.query_model = GoogleGenerativeAIEmbeddings(
            model="gemini-embedding-001",
            google_api_key=api_key,
            task_type="RETRIEVAL_QUERY",
            output_dimensionality=1536
        )

    # -------------------------
    # SINGLE TEXT EMBEDDING
    # -------------------------

    def embed_text(self, text: str) -> list[float]:

        if not isinstance(text, str) or not text.strip():
            raise ValueError("A valid text is required.")

        embedding = self.document_model.embed_query(text)

        return embedding

    # -------------------------
    # MULTIPLE TEXT EMBEDDINGS
    # -------------------------

    def embed_texts(
        self,
        texts: list[str]
    ) -> list[list[float]]:

        if not isinstance(texts, list):
            raise TypeError("texts must be a list.")

        if not texts:
            return []

        if not all(
            isinstance(text, str) and text.strip()
            for text in texts
        ):
            raise ValueError("All texts must be non-empty strings.")

        embeddings = []

        # Process in batches of 100
        for i in range(0, len(texts), 100):

            batch = texts[i:i + 100]

            batch_embeddings = (
                self.document_model.embed_documents(batch)
            )

            embeddings.extend(batch_embeddings)

        return embeddings

    # -------------------------
    # MEMORY DOCUMENT EMBEDDINGS
    # -------------------------


    def embed_memories(
        self,
        memories: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:

        if not isinstance(memories, list):
            raise TypeError("memories must be a list.")

        if not memories:
            return []

        texts = []
        memory_ids = []

        for memory in memories:

            if not isinstance(memory, dict):
                raise TypeError("Each memory must be a dictionary.")

            memory_id = memory.get("memory_id")
            content = memory.get("content")
            summary = memory.get("summary")

            if not isinstance(memory_id, str) or not memory_id.strip():
                raise ValueError("A valid memory_id is required.")

            if not isinstance(content, str) or not content.strip():
                raise ValueError("Every memory must contain valid content.")

            if not isinstance(summary, str) or not summary.strip():
                raise ValueError("Every memory must contain a valid summary.")

            text = f"""
            Content: {content}

            Summary: {summary}
            """

            memory_ids.append(memory_id)
            texts.append(text.strip())

        embeddings = self.embed_texts(texts)

        # Combine each memory ID with its corresponding vector
        return [
            {
                "id": memory_id,
                "values": embedding
            }
            for memory_id, embedding in zip(memory_ids, embeddings)
        ]

    
    # -------------------------
    # QUERY EMBEDDING
    # -------------------------

    def embed_query(self, text: str) -> list[float]:

        if not isinstance(text, str) or not text.strip():
            raise ValueError("A valid query is required.")

        return self.query_model.embed_query(text)
