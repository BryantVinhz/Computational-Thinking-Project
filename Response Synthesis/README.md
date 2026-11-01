# Giai đoạn 4: Response Synthesis, Evidence Verification & Traceable Response

Thư mục chứa mã nguồn và tài liệu kỹ thuật cho giai đoạn cuối cùng của hệ thống RAG trong khuôn khổ **Computational Thinking Project**.

---

## 1. Phân công trách nhiệm (Role Assignment)

* **Nguyên (Response Synthesis & Orchestration):**
  * `prompt_templates.py`: Xây dựng prompt templates cho các intent `SEARCH`, `COMPARE`, `SYNTHESIS`.
  * `synthesizer.py`: Gọi LLM, sinh bản thảo câu trả lời (`SynthesizedDraftSchema`) có gắn thẻ trích dẫn nội văn `[1]`, `[2]`.
  * `rag_orchestrator.py`: Kết nối luồng hoàn chỉnh từ Query Understanding -> Retrieval -> Stage 4.
* **Bảo (Evidence Verification & Traceability):**
  * `verifier.py`: Trích xuất claim, đối chiếu với `AssembledEvidence`, tính `faithfulness_score` (`VerificationReportSchema`).
  * `traceable_formatter.py`: Trích xuất các thẻ `[1]`, `[2]`, bóc tách thông tin định vị (`TraceableCitationSchema`) và sinh mục tham chiếu Footnotes/References cuối bài viết.

---

## 2. Giao thức tương tác (Contract Overview)

Tất cả các định dạng dữ liệu đã được định nghĩa và chuẩn hóa tại:
👉 [`schema_response.py`](file:///D:/hcmus_iv/Tư duy tính toán/Project/Computational-Thinking-Project/Response%20Synthesis/schema_response.py)

```
[Retrieval Output: AssembledEvidence]
                  │
                  ▼
         [Synthesizer (Nguyên)]
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

## 3. Cách import và sử dụng

```python
import sys
from pathlib import Path

# Thêm đường dẫn nếu cần
STAGE_4_DIR = Path(__file__).resolve().parent
if str(STAGE_4_DIR) not in sys.path:
    sys.path.insert(0, str(STAGE_4_DIR))

from schema_response import (
    ClaimStatus,
    VerificationStatus,
    ClaimVerificationResult,
    VerificationReportSchema,
    TraceableCitationSchema,
    SynthesizedDraftSchema,
    FinalResponseEnvelope,
)
```
