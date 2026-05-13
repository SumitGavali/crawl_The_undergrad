"""
QdrantVectorStore — Vector database interface for KnowledgeOS
"""

import hashlib
import logging
from typing import Optional

import numpy as np
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    VectorParams,
    PointStruct,
    Filter,
    FieldCondition,
    MatchValue,
)

logger = logging.getLogger("knowledgeos.vector_store")


class QdrantVectorStore:
    """Manages vector storage and retrieval using Qdrant."""

    def __init__(self, url: str, collection_name: str):
        """
        Connect to Qdrant and ensure collection exists.
        
        Args:
            url: Qdrant server URL (e.g., http://localhost:6333)
            collection_name: Name of the collection to use
        """
        self.client = QdrantClient(url=url)
        self.collection_name = collection_name
        self.vector_size = 384  # all-MiniLM-L6-v2 embedding size
        
        # Create collection if it doesn't exist
        try:
            self.client.get_collection(collection_name)
            logger.info(f"✓ Connected to existing collection '{collection_name}'")
        except Exception:
            self.client.create_collection(
                collection_name=collection_name,
                vectors_config=VectorParams(
                    size=self.vector_size,
                    distance=Distance.COSINE
                )
            )
            logger.info(f"✓ Created new collection '{collection_name}'")

    @staticmethod
    def _generate_id(file_path: str, chunk_index: int) -> str:
        """Generate a unique ID for a chunk based on file path and chunk index."""
        key = f"{file_path}::{chunk_index}"
        return hashlib.md5(key.encode()).hexdigest()

    def upsert(self, chunks: list[dict], vectors: np.ndarray) -> None:
        """
        Store chunks and their vectors in Qdrant.
        
        Args:
            chunks: List of chunk metadata dicts (file_path, text, page, file_type, chunk_index, source)
            vectors: Numpy array of shape (n_chunks, 384) containing embeddings
        """
        if len(chunks) != len(vectors):
            raise ValueError(f"Mismatch: {len(chunks)} chunks but {len(vectors)} vectors")
        
        points = []
        for chunk, vector in zip(chunks, vectors):
            point_id = self._generate_id(
                chunk.get("file_path", ""),
                chunk.get("chunk_index", 0)
            )
            
            points.append(PointStruct(
                id=point_id,
                vector=vector.tolist(),
                payload=chunk
            ))
        
        # Batch upsert
        self.client.upsert(
            collection_name=self.collection_name,
            points=points
        )
        logger.info(f"✓ Upserted {len(points)} points to Qdrant")

    def search(
        self,
        query_vector: np.ndarray,
        top_k: int,
        sources: Optional[list[str]] = None
    ) -> list[dict]:
        """
        Search for similar vectors in Qdrant.
        
        Args:
            query_vector: Query embedding vector (384-dim)
            top_k: Number of results to return
            sources: Optional list of source values to filter by
            
        Returns:
            List of dicts with 'score' and 'payload' keys
        """
        # Build filter if sources specified
        query_filter = None
        if sources:
            from qdrant_client.models import MatchAny
            query_filter = Filter(
                must=[
                    FieldCondition(
                        key="source",
                        match=MatchAny(any=sources)
                    )
                ]
            )
        
        results = self.client.query_points(
            collection_name=self.collection_name,
            query=query_vector.tolist(),
            limit=top_k,
            query_filter=query_filter
        )
        
        return [
            {
                "score": hit.score,
                "payload": hit.payload
            }
            for hit in results.points
        ]

    def delete_by_source(self, source: str) -> None:
        """
        Delete all points with a specific source value.
        
        Args:
            source: Source value to match (e.g., "local", "network_drive")
        """
        self.client.delete(
            collection_name=self.collection_name,
            points_selector=Filter(
                must=[
                    FieldCondition(
                        key="source",
                        match=MatchValue(value=source)
                    )
                ]
            )
        )
        logger.info(f"✓ Deleted all points with source='{source}'")

    def get_stats(self) -> dict:
        """
        Get collection statistics.
        
        Returns:
            Dict with total point count and collection info
        """
        try:
            collection_info = self.client.get_collection(self.collection_name)
            return {
                "total_points": collection_info.points_count,
                "vector_size": collection_info.config.params.vectors.size,
                "distance": collection_info.config.params.vectors.distance.name,
            }
        except Exception as e:
            logger.error(f"Failed to get stats: {e}")
            return {"total_points": 0, "error": str(e)}
