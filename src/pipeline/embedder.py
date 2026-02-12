from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_upstage import UpstageEmbeddings


def generate_chunk_id(document_id: str, chunk_index: int) -> str:
    return f"{document_id}::chunk_{chunk_index}"


@dataclass(slots=True)
class VectorSnapshot:
    ids: list[str]
    documents: list[str]
    metadatas: list[dict[str, Any]]
    embeddings: list[list[float]] | None


class ReportEmbedder:
    """리포트 청크를 임베딩하여 ChromaDB에 적재한다."""

    def __init__(
        self,
        api_key: str,
        *,
        persist_directory: str = "./data/chromadb",
        collection_name: str = "securities_reports",
        embedding_model: str = "embedding-query",
    ):
        self.persist_directory = persist_directory
        Path(self.persist_directory).mkdir(parents=True, exist_ok=True)

        embeddings = UpstageEmbeddings(
            api_key=api_key,
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

        ids = self._build_chunk_ids(documents)
        self.vectorstore.add_documents(documents=documents, ids=ids)
        return len(documents)

    def replace_document(self, *, document_id: str, documents: list[Document]) -> int:
        if not documents:
            self.delete_document(document_id)
            return 0

        ids = self._build_chunk_ids(documents)
        self.delete_document(document_id)
        self.vectorstore.add_documents(documents=documents, ids=ids)
        return len(documents)

    def snapshot_document(self, document_id: str) -> VectorSnapshot:
        collection = self._collection()
        payload = collection.get(
            where={"document_id": document_id},
            include=["documents", "metadatas", "embeddings"],
        )
        ids = [str(item) for item in payload.get("ids", [])]
        documents = [str(item) for item in payload.get("documents", [])]
        metadatas = [dict(item) for item in payload.get("metadatas", [])]
        raw_embeddings = payload.get("embeddings")
        embeddings = None
        if raw_embeddings is not None:
            embeddings = [list(map(float, embedding)) for embedding in raw_embeddings]

        return VectorSnapshot(
            ids=ids,
            documents=documents,
            metadatas=metadatas,
            embeddings=embeddings,
        )

    def restore_snapshot(self, *, document_id: str, snapshot: VectorSnapshot) -> None:
        self.delete_document(document_id)
        if not snapshot.ids:
            return

        collection = self._collection()
        if snapshot.embeddings is not None and len(snapshot.embeddings) == len(snapshot.ids):
            collection.upsert(
                ids=snapshot.ids,
                documents=snapshot.documents,
                metadatas=snapshot.metadatas,
                embeddings=snapshot.embeddings,
            )
            return

        documents = [
            Document(page_content=content, metadata=metadata)
            for content, metadata in zip(snapshot.documents, snapshot.metadatas, strict=False)
        ]
        self.vectorstore.add_documents(documents=documents, ids=snapshot.ids)

    def delete_document(self, document_id: str) -> None:
        # langchain_chroma 버전에 따라 delete 시그니처가 달라 fallback을 둔다.
        try:
            self.vectorstore.delete(where={"document_id": document_id})
        except TypeError:
            self.vectorstore._collection.delete(where={"document_id": document_id})  # type: ignore[attr-defined]

    def get_vectorstore(self) -> Chroma:
        return self.vectorstore

    def _collection(self) -> Any:
        if hasattr(self.vectorstore, "_collection"):
            return self.vectorstore._collection  # type: ignore[attr-defined]
        raise RuntimeError("Unable to access underlying Chroma collection")

    @staticmethod
    def _build_chunk_ids(documents: list[Document]) -> list[str]:
        ids: list[str] = []
        for idx, document in enumerate(documents):
            metadata = document.metadata or {}
            document_id = str(metadata.get("document_id", "unknown_document"))
            chunk_index = int(metadata.get("chunk_index", idx))
            ids.append(generate_chunk_id(document_id=document_id, chunk_index=chunk_index))
        return ids
