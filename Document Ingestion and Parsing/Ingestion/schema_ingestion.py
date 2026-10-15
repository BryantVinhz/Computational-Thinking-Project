from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from enum import Enum


class FileFormat(str, Enum):
    PDF = "PDF"
    MARKDOWN = "MARKDOWN"
    CSV = "CSV"
    JSON = "JSON"
    TXT = "TXT"
    UNKNOWN = "UNKNOWN"


class ProcessingStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    SUCCESS = "success"
    FAILED = "failed"


@dataclass
class DocumentSchema:
    document_id: str
    canonical_title: str
    publisher: str
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class DocumentVersionSchema:
    version_id: str
    document_id: str
    version_tag: str
    file_name: str
    file_path: str
    file_format: str
    file_size_bytes: int
    sha256_hash: str | None
    status: str
    current_stage: str = "INGESTION"
    is_duplicate: bool = False
    error_message: str | None
    updated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict:
        return asdict(self)