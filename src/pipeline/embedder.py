from __future__ import annotations

from pathlib import Path

from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_openai import OpenAIEmbeddings


def generate_chunk_id(document_id: str, chunk_index: int) -> str:
    return f"{document_id}::chunk_{chunk_index}"


class ReportEmbedder:
    """리포트 청크를 임베딩하여 ChromaDB에 적재한다."""

    def __init__(
        self,
        openai_api_key: str,
        *,
        persist_directory: str = "./data/chromadb",
        collection_name: str = "securities_reports",
        embedding_model: str = "text-embedding-3-small",
    ):
        self.persist_directory = persist_directory
        Path(self.persist_directory).mkdir(parents=True, exist_ok=True)

        embeddings = OpenAIEmbeddings(
            api_key=openai_api_key,
            model=embedding_model,
            dimensions=1536,
            max_retries=3,
            request_timeout=30,
        )
        self.vectorstore = Chroma(
            collection_name=collection_name,
            embedding_function=embeddings,
            persist_directory=self.persist_directory,
            collection_metadata={
                "description": "증권사 애널리스트 리포트 청크 벡터 저장소",
                "hnsw:space": "cosine",
            },
        )

    def embed_and_store(self, documents: list[Document]) -> int:
        if not documents:
            return 0

        ids: list[str] = []
        for idx, document in enumerate(documents):
            metadata = document.metadata or {}
            document_id = str(metadata.get("document_id", "unknown_document"))
            chunk_index = int(metadata.get("chunk_index", idx))
            ids.append(generate_chunk_id(document_id=document_id, chunk_index=chunk_index))

        self.vectorstore.add_documents(documents=documents, ids=ids)
        return len(documents)

    def delete_document(self, document_id: str) -> None:
        # langchain_chroma 버전에 따라 delete 시그니처가 달라 fallback을 둔다.
        try:
            self.vectorstore.delete(where={"document_id": document_id})
        except TypeError:
            self.vectorstore._collection.delete(where={"document_id": document_id})  # type: ignore[attr-defined]

    def get_vectorstore(self) -> Chroma:
        return self.vectorstore

