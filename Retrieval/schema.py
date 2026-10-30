from dataclasses import dataclass, field
from typing import Any

@dataclass
class RetrievalScope:
    """
    Configuration for retrieval scope, hard filters, and scoring preferences when retrieving chunks
    Attributes:
        chroma_filter: Metadata filter applied directly to ChromaDB
        document_ids: List of mandatory document IDs to retrieve from
        file_formats: Target file formats to filter by (e.g., pdf, docx)
        version_ids: Specific document versions to retrieve
        is_relaxed: Flag indicating whether search conditions are relaxed
        preferred_chunk_types: Chunk types (e.g., table, paragraph)
        preferred_headings: Document headings 
        candidate_doc_ids: Candidate document IDs identified from coarse filtering or routing
        ontology_keywords: Ontology terms or synonyms 
    """
    chroma_filter: dict[str, Any] | None = None
    document_ids: list[str] = field(default_factory=list)
    file_formats: list[str] = field(default_factory=list)
    version_ids: str | None = None
    is_relaxed: bool = False


    preferred_chunk_types: list[str] = field(default_factory=list)  
    preferred_headings: list[str] = field(default_factory=list)     
    candidate_doc_ids: list[str] = field(default_factory=list)      
    ontology_keywords: list[str] = field(default_factory=list)      


@dataclass
class ScopedRetrievalPayload:
    """
    Complete data packet passed into the Hybrid Retrieval layer
    Attributes:
        task_id: Unique identifier for the task or session for tracing and debugging
        intent_task: Query intent (e.g., vulnerability_search, comparison, QA)
        original_query: Original raw query submitted by the user
        query_bm25: Preprocessed query string optimized for keyword search (BM25)
        query_vector: Vector embedding representation for semantic dense search
        scope: Scope and associated filtering criteria (RetrievalScope)
    """
    
    task_id: str
    intent_task: str
    original_query: str
    query_bm25: str   
    query_vector: list[float]      
    scope: RetrievalScope