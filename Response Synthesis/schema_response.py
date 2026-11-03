"""
Module: schema_response.py
Stage 4: Response Synthesis, Evidence Verification, and Traceable Response
Contract Version: 1.0

Thống nhất cấu trúc dữ liệu và điểm giao tiếp (Interface Contract) 
giữa Nguyên (Response Synthesis & Orchestration) và Bảo (Evidence Verification & Traceability).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class ClaimStatus(str, Enum):
    """Trạng thái kiểm định của từng nhận định (Claim) đối chiếu với Evidence."""
    SUPPORTED = "SUPPORTED"          # Được chứng minh rõ ràng bởi bằng chứng trích dẫn
    CONTRADICTED = "CONTRADICTED"    # Trái ngược / mâu thuẫn với nội dung bằng chứng
    UNVERIFIED = "UNVERIFIED"        # Không tìm thấy căn cứ xác thực (nghi ngờ ảo giác)


class VerificationStatus(str, Enum):
    """Đánh giá tổng thể độ trung thực (Faithfulness) của câu trả lời."""
    PASSED = "PASSED"                # Đạt chuẩn tin cậy cao (faithfulness_score >= 0.8)
    WARNING = "WARNING"              # Cảnh báo: có một số claim chưa được chứng thực đầy đủ (0.5 <= score < 0.8)
    FAILED = "FAILED"                # Không đạt: phần lớn nội dung là bịa đặt hoặc mâu thuẫn (score < 0.5)
    SKIPPED = "SKIPPED"              # Bỏ qua kiểm định (ví dụ khi hệ thống kích hoạt Abstention)


# =====================================================================
# 1. HỢP ĐỒNG ĐẦU RA CỦA NGUYÊN (SYNTHESIS DRAFT)
# =====================================================================

@dataclass
class SynthesizedDraftSchema:
    """
    Output do module của NGUYÊN (Synthesizer) tạo ra và chuyển giao cho BẢO (Verifier).
    
    Chứa bản thảo câu trả lời do LLM sinh ra dựa trên context và intent,
    kèm các thẻ trích dẫn [1], [2] được chèn trong nội văn.
    """
    draft_answer: str                           # Câu trả lời Markdown thô từ LLM (có tag [1], [2])
    intent: str                                 # SEARCH | COMPARE | SYNTHESIS
    cited_evidence_ids: list[str] = field(default_factory=list) # Danh sách các tag được LLM trích dẫn, VD: ["[1]", "[2]"]
    is_abstained: bool = False                  # True nếu kích hoạt Abstention Policy (thiếu bằng chứng)
    abstention_reason: str | None = None        # Lý do từ chối nếu có
    evidence_status: str = "FULL"               # "FULL" | "PARTIAL" | "NONE" (nhận từ Retrieval)
    generation_mode: str = "STRICT_RAG"         # "STRICT_RAG" | "HYBRID" | "ABSTENTION"
    generation_metadata: dict[str, Any] = field(default_factory=dict) # model_name, latency, temperature...

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SynthesizedDraftSchema:
        return cls(
            draft_answer=data.get("draft_answer", ""),
            intent=data.get("intent", "SEARCH"),
            cited_evidence_ids=data.get("cited_evidence_ids", []),
            is_abstained=data.get("is_abstained", False),
            abstention_reason=data.get("abstention_reason"),
            evidence_status=data.get("evidence_status", "FULL"),
            generation_mode=data.get("generation_mode", "STRICT_RAG"),
            generation_metadata=data.get("generation_metadata", {}),
        )


# =====================================================================
# 2. HỢP ĐỒNG KIỂM ĐỊNH & TRUY VẾT CỦA BẢO (VERIFICATION & TRACEABILITY)
# =====================================================================

@dataclass
class ClaimVerificationResult:
    """Kết quả kiểm định cho từng câu / luận điểm cụ thể (Atomic Claim)."""
    claim_id: str                               # Mã định danh câu (VD: "claim_001")
    claim_text: str                             # Nội dung câu khẳng định
    cited_tag: str | None                       # Thẻ trích dẫn gắn với câu này (VD: "[1]")
    status: ClaimStatus                         # SUPPORTED | CONTRADICTED | UNVERIFIED
    confidence_score: float                     # Điểm tương đồng/tin cậy (0.0 -> 1.0)
    matched_snippet: str | None = None          # Đoạn trích từ Evidence chứng minh cho câu này
    explanation: str | None = None              # Giải thích ngắn gọn lý do đánh giá

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if isinstance(self.status, ClaimStatus):
            data["status"] = self.status.value
        return data


@dataclass
class VerificationReportSchema:
    """Báo cáo tổng hợp kiểm định ảo giác và mức độ trung thực của câu trả lời."""
    faithfulness_score: float                   # Tỷ lệ % số câu được chứng thực (0.0 -> 1.0)
    status: VerificationStatus                  # PASSED | WARNING | FAILED | SKIPPED
    total_claims: int = 0
    supported_claims: int = 0
    contradicted_claims: int = 0
    unverified_claims: int = 0
    claims_detail: list[ClaimVerificationResult] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "faithfulness_score": round(self.faithfulness_score, 4),
            "status": self.status.value if isinstance(self.status, VerificationStatus) else self.status,
            "total_claims": self.total_claims,
            "supported_claims": self.supported_claims,
            "contradicted_claims": self.contradicted_claims,
            "unverified_claims": self.unverified_claims,
            "claims_detail": [c.to_dict() for c in self.claims_detail],
            "warnings": self.warnings,
        }


@dataclass
class TraceableCitationSchema:
    """Thông tin truy vết nguồn gốc chi tiết tương ứng với từng thẻ [1], [2]."""
    tag: str                                    # Thẻ hiển thị trong bài viết, VD: "[1]"
    chunk_id: str                               # Mã định danh chunk (VD: "doc_a1b2c3d4_v1.0_c00001")
    canonical_title: str                        # Tiêu đề chuẩn của tài liệu
    heading_path: list[str] = field(default_factory=list) # Cây mục lục: ["Injection", "Remediation"]
    document_id: str = ""                       # Mã tài liệu
    version_id: str = ""                        # Phiên bản tài liệu
    file_name: str = ""                         # Tên file gốc
    file_format: str = ""                       # PDF | MARKDOWN | CSV | JSON
    location_summary: str = ""                  # Vị trí ngắn gọn (VD: "Page 14, Lines 12-25" hoặc "BBox: [..]")
    locator: dict[str, Any] = field(default_factory=dict) # Chi tiết tọa độ từ database Provenance
    snippet: str = ""                           # Đoạn trích ngắn đại diện cho nguồn để preview

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# =====================================================================
# 3. HỢP ĐỒNG ĐẦU RA TOÀN DIỆN CỦA HỆ THỐNG (FINAL RESPONSE ENVELOPE)
# =====================================================================

@dataclass
class FinalResponseEnvelope:
    """
    Output cuối cùng của toàn bộ Hệ thống RAG (Stage 4 hoàn tất).
    Được trả về cho UI / API hoặc người dùng cuối.
    """
    schema_version: str = "1.0"
    status: str = "ok"                          # "ok" | "abstained" | "clarification_needed" | "error"
    original_query: str = ""                    # Câu hỏi gốc từ người dùng
    resolved_query: str = ""                    # Câu hỏi sau khi làm rõ qua Query Understanding
    intent: str = "SEARCH"                      # SEARCH | COMPARE | SYNTHESIS
    final_answer_markdown: str = ""             # Câu trả lời Markdown hoàn chỉnh (đã kèm Footnotes ở cuối)
    verification: VerificationReportSchema | None = None # Báo cáo kiểm định do Bảo thực hiện
    citations: list[TraceableCitationSchema] = field(default_factory=list) # Danh mục nguồn trích dẫn
    raw_draft: SynthesizedDraftSchema | None = None # Bản nháp gốc của Nguyên (dùng cho audit/debug)
    metadata: dict[str, Any] = field(default_factory=dict) # Thông số đo lường hiệu năng (timing, token count...)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "status": self.status,
            "original_query": self.original_query,
            "resolved_query": self.resolved_query,
            "intent": self.intent,
            "final_answer_markdown": self.final_answer_markdown,
            "verification": self.verification.to_dict() if self.verification else None,
            "citations": [c.to_dict() for c in self.citations],
            "raw_draft": self.raw_draft.to_dict() if self.raw_draft else None,
            "metadata": self.metadata,
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)
