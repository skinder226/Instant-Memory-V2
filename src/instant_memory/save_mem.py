
import os
from pprint import pprint

from dotenv import load_dotenv

from instant_memory.factories.pinecone_factory import PineconeFactory
from .llm import Extractor
from instant_memory.memory_service import MemoryService
from rich import print
from .lib.database import DatabaseManager
from .factories.embedding_factory import EmbeddingFactory
from .factories.graph_factory import GraphFactory

load_dotenv()

# Initialize your LLM


# Initialize memory service
memory = MemoryService(llm=Extractor)
embedding_factory = EmbeddingFactory()




def save_memory(user_id: str, user_input: str, speaker: str = "user") -> list[dict]:

    all_documents = memory.save_memory(
        user_id=user_id,
        user_input=user_input
    )


    # Deduplicate the memories

    



    outbox_documents = all_documents["outbox"]
    documents = all_documents["memories"]


    

    print(result.model_dump_json(indent=2))


    print(f"\nExtracted {len(documents)} memories {documents}")

    db_manager = DatabaseManager().connect()
    memories_collection = db_manager.collection("memories")
    outbox_collection = db_manager.collection("outbox")
    

    for document in documents:
        print("\n-----------------------------")
        print(document)

    print("\n-----------------------------")

    insert_result = memories_collection.insert_many(documents)
    insert_result = outbox_collection.insert_many(outbox_documents)

    embeddings = embedding_factory.embed_memories(documents)

    # insert embeddings into Pinecone
    try:
        pinecone_documents = PineconeFactory.to_documents(
        memories=documents,
        embeddings=embeddings
        )


        pinecone_index = db_manager.index()

        pinecone_result = pinecone_index.upsert(vectors=pinecone_documents, namespace=user_id)

        print(f"\nInserted {len(pinecone_documents)} embeddings into Pinecone index. and pinecone responce {pinecone_result}")

        graph_documents = GraphFactory.to_documents(
        documents
    )
        
    except Exception as e:
        print(f"Error inserting embeddings into Pinecone: {e}")


    try:
    
        print("\n========== GRAPH DOCUMENTS ==========")

        print(f"\nGraph documents: {graph_documents}")

        print("=====================================\n")

        graph = db_manager

        graph_reuslt = graph.upsert_graphs(graph_documents)

        print(f"\nInserted {len(graph_documents)} graph documents into Neo4j. and graph responce {graph_reuslt}")

    except Exception as e:
        print(f"Error inserting graph documents into Neo4j: {e}")








    print(f"\nInserted {len(insert_result.inserted_ids)} documents into MongoDB.")
       
    

    return documents


if __name__ == "__main__":

    result = save_memory(
        user_id="user_123",
        user_input=(
            "In instant memory system i am using python "
        ),
        speaker="user"
    )


# I want to add two deduplication layers first when the model is genrating the memories and second when the memories are being saved to the database. This will help in avoiding duplicate entries and ensure that the memory system remains efficient and clean.