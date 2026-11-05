# Giai đoạn 4: Response Synthesis, Evidence Verification & Traceable Response

Thư mục chứa mã nguồn và tài liệu kỹ thuật cho giai đoạn cuối cùng của hệ thống RAG trong khuôn khổ **Computational Thinking Project**.

---

## 1. Phân công trách nhiệm (Role Assignment)

* **Nguyên (Response Synthesis & Orchestration):**
  * `schema_response.py`: Thống nhất Data Contract (v1.0), bổ sung `evidence_status` và `generation_mode` cho Hybrid Mode.
  * `prompt_templates.py`: Xây dựng prompt templates cho các intent `SEARCH`, `COMPARE`, `SYNTHESIS`, kèm chỉ thị `STRICT_RAG` và `HYBRID_MODE`.
  * `synthesizer.py`: Gọi LLM, sinh bản thảo câu trả lời (`SynthesizedDraftSchema`) có gắn thẻ trích dẫn nội văn `[1]`, `[2]`.
  * `rag_orchestrator.py`: Kết nối luồng hoàn chỉnh từ Query Understanding -> Retrieval -> Stage 4.
  * `test_synthesis_orchestration.py`: Bộ kiểm thử tự động toàn bộ tầng Synthesis & Orchestration.
* **Bảo (Evidence Verification & Traceability):**
  * `verifier.py`: Trích xuất claim, đối chiếu với `AssembledEvidence`, tính `faithfulness_score` (`VerificationReportSchema`).
  * `traceable_formatter.py`: Trích xuất các thẻ `[1]`, `[2]`, bóc tách thông tin định vị (`TraceableCitationSchema`) và sinh mục tham chiếu Footnotes/References cuối bài viết.

---

## 2. Giao thức tương tác (Contract Overview)

Tất cả các định dạng dữ liệu đã được định nghĩa và chuẩn hóa tại: [`schema_response.py`](file:///D:/hcmus_iv/Tư%20duy%20tính%20toán/Project/Computational-Thinking-Project/Response%20Synthesis/schema_response.py)

```
[Retrieval Output: AssembledEvidence + evidence_status]
                          │
                          ▼
                 [Synthesizer (Nguyên)]
             (Strict RAG / Hybrid / Abstention)
                          │
                          ▼
               SynthesizedDraftSchema
                          │
                          ▼
          ┌───────────────┴───────────────┐
          ▼                               ▼
    [Verifier (Bảo)]    [TraceableFormatter (Bảo)]
          │                               │
          ▼                               ▼
VerificationReportSchema  list[TraceableCitationSchema]
          └───────────────┬───────────────┘
                          │
                          ▼
                FinalResponseEnvelope
```

---

## 3. Các chế độ sinh câu trả lời (Synthesis Modes)

Tầng Synthesis tiếp nhận `evidence_status` từ Retrieval (`RetrievalSystem.retrieve`):
1. **`STRICT_RAG` (`evidence_status == "FULL"`):**
   - Đầy đủ bằng chứng (từ 2 chunks trở lên và không có task nào bị thiếu).
   - Mô hình chỉ được dùng thông tin trong ngữ cảnh được cấp, bắt buộc trích dẫn `[1]`, `[2]` cho từng luận điểm.
2. **`HYBRID` (`evidence_status == "PARTIAL"`):**
   - Bằng chứng chỉ có một phần (ít hơn 2 chunks hoặc có sub-task bị thiếu dữ liệu).
   - Mô hình trả lời phần có tài liệu kèm `[1]`, `[2]`, đồng thời bổ sung kiến thức an toàn thông tin chuyên sâu cho phần thiếu kèm chú thích rõ ràng trong mục `General Knowledge Advisory`.
3. **`ABSTENTION` (`evidence_status == "NONE"` hoặc `status == "clarification_needed"`):**
   - Kích hoạt chính sách từ chối an toàn hoặc yêu cầu người dùng làm rõ để loại bỏ hoàn toàn nguy cơ ảo giác.

---

## 4. Cách import và sử dụng

```python
import sys
from pathlib import Path

STAGE_4_DIR = Path(__file__).resolve().parent
if str(STAGE_4_DIR) not in sys.path:
    sys.path.insert(0, str(STAGE_4_DIR))

from schema_response import FinalResponseEnvelope, SynthesizedDraftSchema
from synthesizer import ResponseSynthesizer
from rag_orchestrator import RAGOrchestrator
from verifier import EvidenceVerifier
from traceable_formatter import TraceableFormatter

# 1. Khởi tạo Synthesizer, Verifier, Formatter & Orchestrator
synthesizer = ResponseSynthesizer()
orchestrator = RAGOrchestrator(
    synthesizer=synthesizer,
    verifier=EvidenceVerifier(),
    traceable_formatter=TraceableFormatter(),
)

# 2. Chạy từ kết quả Retrieval
envelope: FinalResponseEnvelope = orchestrator.run_from_retrieval(
    query="Cách phòng chống SQL Injection?",
    retrieval_output=retrieval_output,
)

print(envelope.final_answer_markdown)
print(envelope.verification.status, envelope.verification.faithfulness_score)
```

---

## 5. Evidence Verification & Traceability (Bảo)

**`EvidenceVerifier.verify(draft, evidence_list) -> VerificationReportSchema`**
1. Tách `draft_answer` thành claim theo câu / dòng (bỏ tiêu đề, ghi chú `(...)`, header bảng, code block).
2. Claim có thẻ hợp lệ chỉ được đối chiếu với **đúng** evidence được trích; claim không thẻ được dò trên toàn bộ evidence (SUPPORTED kèm cảnh báo thiếu trích dẫn).
3. `SUPPORTED` khi điểm hỗ trợ >= `support_threshold` (mặc định 0.5); `CONTRADICTED` khi khớp nội dung nhưng lệch phủ định; còn lại `UNVERIFIED` (kể cả thẻ trỏ tới evidence không tồn tại).
4. `faithfulness_score = supported / claim dựa trên tài liệu`. Ở `HYBRID`, claim thuộc mục `General Knowledge Advisory` được liệt kê nhưng không tính điểm.
5. Trạng thái: `PASSED` >= 0.8, `WARNING` >= 0.5, `FAILED` < 0.5, `SKIPPED` khi abstention.

Điểm mặc định là độ phủ từ vựng (unigram + bigram), chạy offline. Khi câu trả lời và tài liệu khác ngôn ngữ, truyền hàm ngữ nghĩa:

```python
verifier = EvidenceVerifier(similarity_fn=lambda claim, passage: cosine(embed(claim), embed(passage)))
```

**`TraceableFormatter.format_citations(draft, evidence_list) -> (markdown, citations)`**
- Ánh xạ `[n]` theo `evidence_id` của Retrieval; chỉ liệt kê thẻ thực sự xuất hiện trong câu trả lời.
- `location_summary` sinh từ locator Provenance: `Page 3-4` (PDF), `Lines 12-25` (MD/TXT), `Rows 4-6` (CSV), `JSON $.path` (JSON); `locator["spans"]` giữ toàn bộ block + offset để audit.
- Thẻ không có evidence tương ứng được đánh dấu ⚠️ trong mục References thay vì bị bỏ qua.

Chạy test: `python -m unittest test_verification_traceability test_synthesis_orchestration`
