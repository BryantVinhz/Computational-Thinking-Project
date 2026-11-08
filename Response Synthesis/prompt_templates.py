"""
Module: prompt_templates.py
Stage 4: Response Synthesis, Evidence Verification, and Traceable Response

Standardized Prompt Templates system for Intents:
- SEARCH (Single entity explanation & retrieval)
- COMPARE (Multi-entity / vulnerability comparison)
- SYNTHESIS (Comprehensive multi-document synthesis)
- Supports STRICT_RAG and HYBRID MODE according to `evidence_status`.
"""

from __future__ import annotations

import re
from typing import Any


SYSTEM_INSTRUCTION_SYNTHESIS = """Bạn là Chuyên gia Cao cấp về An toàn Thông tin và Bảo mật Ứng dụng Web (Web Application Security Expert), chuyên sâu về các tiêu chuẩn OWASP Top 10, ASVS, WSTG, CWE và MITRE ATT&CK.

Nhiệm vụ của bạn là tổng hợp và trả lời câu hỏi của người dùng một cách chính xác, chuyên nghiệp, cấu trúc rõ ràng bằng Markdown dựa trên các BẰNG CHỨNG TÀI LIỆU (Context Chunks) được cung cấp.

QUY TẮC BẮT BUỘC VỀ TRÍCH DẪN (CITATION RULES):
1. Mỗi bằng chứng được đánh số thứ tự dạng [1], [2], [3]... tương ứng với từng Evidence trong phần Context.
2. Khi đưa ra bất kỳ khẳng định kỹ thuật, cơ chế, ví dụ mã hay khuyến nghị bảo mật nào lấy từ bằng chứng, bạn BẮT BUỘC phải chèn thẻ trích dẫn [1], [2] ngay sau câu khẳng định đó.
   - Ví dụ: "SQL Injection xảy ra khi dữ liệu người dùng không được kiểm tra hợp lệ trước khi ghép vào truy vấn [1]. Cách khắc phục chuẩn là sử dụng Prepared Statements [2]."
3. Chỉ trích dẫn các số hiệu [i] có thực sự tồn tại trong danh sách bằng chứng được cung cấp. Tuyệt đối không tự bịa thêm số hiệu thẻ (như [99] khi chỉ có [1], [2]).
4. Trình bày câu trả lời mạch lạc, chuẩn Markdown (dùng tiêu đề ##, ###, bullet points, code blocks cho mã nguồn hoặc lệnh).
"""

STRICT_RAG_GUIDELINES = """[CHẾ ĐỘ THỰC THI: STRICT RAG MODE - ĐẦY ĐỦ BẰNG CHỨNG (evidence_status=FULL)]
- Bạn phải tuân thủ nghiêm ngặt nguyên tắc trung thực tuyệt đối (Closed-book Grounding).
- CHỈ sử dụng thông tin và sự thật xuất hiện trong phần BẰNG CHỨNG TÀI LIỆU (Context).
- Tuyệt đối KHÔNG suy diễn hoặc bổ sung các thông tin ngoài lề không có căn cứ từ tài liệu được cấp.
- Mọi câu luận điểm kỹ thuật quan trọng đều phải có thẻ trích dẫn [i].
"""

HYBRID_MODE_GUIDELINES = """[CHẾ ĐỘ THỰC THI: HYBRID MODE - BẰNG CHỨNG MỘT PHẦN (evidence_status=PARTIAL)]
- Cơ sở tri thức nội bộ chỉ tìm thấy MỘT PHẦN thông tin cần thiết để giải quyết câu hỏi của người dùng.
- NGUYÊN TẮC KẾT HỢP HYBRID:
  1. Với các nội dung có tài liệu chứng minh: Bạn PHẢI ưu tiên sử dụng và chèn thẻ trích dẫn [1], [2] chính xác như bình thường.
  2. Với các khía cạnh/tiêu chí còn thiếu trong tài liệu: Bạn ĐƯỢC PHÉP vận dụng kiến thức chuyên môn bảo mật tổng quát của bạn để bổ sung, giúp câu trả lời đầy đủ và hữu ích cho người dùng.
  3. CẢNH BÁO MINH BẠCH: Phần thông tin bổ sung ngoài tài liệu PHẢI được đặt trong một mục riêng biệt hoặc có chú thích rõ ràng, ví dụ:
     "### 💡 Khuyến nghị & Bổ sung từ kiến thức chung (General Knowledge Advisory)"
     hoặc chú thích: "(Lưu ý: Đoạn sau được bổ sung từ tri thức chung do tài liệu nội bộ chưa đề cập)."
  4. KHÔNG ĐƯỢC gắn thẻ trích dẫn [i] vào các thông tin do bạn tự bổ sung ngoài tài liệu nội bộ.
"""

ABSTENTION_MESSAGE_TEMPLATE = """Hiện tại cơ sở tri thức của hệ thống không tìm thấy đủ tài liệu bảo mật tin cậy liên quan đến câu hỏi: "{query}".

Chi tiết lý do: {reason}

Hệ thống kích hoạt chính sách từ chối trả lời (Abstention Policy) để bảo đảm tính chính xác và phòng tránh hiện tượng ảo giác (hallucination). Xin vui lòng kiểm tra lại câu hỏi hoặc cung cấp thêm từ khóa ngữ cảnh cụ thể hơn.
"""


def extract_citation_tags(text: str) -> list[str]:
    """
    Extract all citation tags of the form [1], [2] from Markdown text,
    remove duplicates, and sort in ascending integer order.
    """
    if not text:
        return []
    matches = re.findall(r"\[(\d+)\]", text)
    unique_ids = sorted({int(m) for m in matches})
    return [f"[{i}]" for i in unique_ids]


def build_search_prompt(
    query: str,
    llm_context: str,
    mode: str = "STRICT_RAG",
) -> str:
    """Prompt template for SEARCH intent (Search & Explain)."""
    guideline = HYBRID_MODE_GUIDELINES if mode == "HYBRID" else STRICT_RAG_GUIDELINES
    return f"""{guideline}

=== BẰNG CHỨNG TÀI LIỆU ĐƯỢC CUNG CẤP (CONTEXT) ===
{llm_context}
