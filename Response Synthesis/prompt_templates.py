"""
Module: prompt_templates.py
Stage 4: Response Synthesis, Evidence Verification, and Traceable Response

Xây dựng hệ thống Prompt Templates chuẩn hóa cho các Intent:
- SEARCH (Tìm kiếm giải thích đơn lẻ)
- COMPARE (So sánh đa thực thể / lỗ hổng)
- SYNTHESIS (Tổng hợp chuyên sâu đa tài liệu)
- Kèm hỗ trợ chế độ STRICT_RAG và HYBRID MODE theo `evidence_status`.
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
    Trích xuất toàn bộ các thẻ trích dẫn dạng [1], [2] từ văn bản Markdown,
    loại bỏ trùng lặp và sắp xếp theo thứ tự số nguyên tăng dần.
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
    """Prompt dành riêng cho Intent SEARCH (Tìm kiếm & Giải thích)."""
    guideline = HYBRID_MODE_GUIDELINES if mode == "HYBRID" else STRICT_RAG_GUIDELINES
    return f"""{guideline}

=== BẰNG CHỨNG TÀI LIỆU ĐƯỢC CUNG CẤP (CONTEXT) ===
{llm_context}
===================================================

YÊU CẦU CHO INTENT 'SEARCH':
- Trả lời trực tiếp, chính xác và có chiều sâu kỹ thuật cho câu hỏi của người dùng.
- Cấu trúc đề xuất:
  1. Khái niệm / Định nghĩa cốt lõi (kèm [i])
  2. Cơ chế hoạt động & Vector tấn công / Rủi ro (kèm [i])
  3. Biện pháp phòng chống, khắc phục và mã mẫu an toàn (Secure Code Example) (kèm [i])

CÂU HỎI CỦA NGƯỜI DÙNG:
{query}

Hãy sinh câu trả lời Markdown hoàn chỉnh:"""


def build_compare_prompt(
    query: str,
    llm_context: str,
    mode: str = "STRICT_RAG",
) -> str:
    """Prompt dành riêng cho Intent COMPARE (So sánh đối chiếu)."""
    guideline = HYBRID_MODE_GUIDELINES if mode == "HYBRID" else STRICT_RAG_GUIDELINES
    return f"""{guideline}

=== BẰNG CHỨNG TÀI LIỆU ĐƯỢC CUNG CẤP (CONTEXT) ===
{llm_context}
===================================================

YÊU CẦU CHO INTENT 'COMPARE':
- Thực hiện so sánh đối chiếu đa chiều giữa các thực thể, lỗ hổng hoặc cơ chế an ninh được hỏi.
- BẮT BUỘC phải bao gồm:
  1. Bảng so sánh tổng hợp (Markdown Table) với các tiêu chí tối thiểu: Khái niệm, Vị trí/Tầng tác động, Mức độ rủi ro (CVSS/CWE), Phương thức khai thác, Biện pháp phòng vệ chính.
  2. Phân tích chi tiết điểm tương đồng và khác biệt cốt lõi (gắn thẻ trích dẫn [i]).
  3. Kịch bản thực tế áp dụng hoặc tình huống dễ gây nhầm lẫn giữa chúng.

CÂU HỎI SO SÁNH:
{query}

Hãy sinh câu trả lời so sánh Markdown hoàn chỉnh:"""


def build_synthesis_prompt(
    query: str,
    llm_context: str,
    mode: str = "STRICT_RAG",
) -> str:
    """Prompt dành riêng cho Intent SYNTHESIS (Tổng hợp đa chiều toàn diện)."""
    guideline = HYBRID_MODE_GUIDELINES if mode == "HYBRID" else STRICT_RAG_GUIDELINES
    return f"""{guideline}

=== BẰNG CHỨNG TÀI LIỆU ĐƯỢC CUNG CẤP (CONTEXT) ===
{llm_context}
===================================================

YÊU CẦU CHO INTENT 'SYNTHESIS':
- Tổng hợp kiến thức từ nhiều nguồn tài liệu thành một tài liệu tham vấn an toàn thông tin toàn diện và có cấu trúc mạch lạc.
- Cấu trúc đề xuất:
  ## 1. Tóm tắt điều hành (Executive Summary)
  ## 2. Bối cảnh & Phân tích rủi ro kỹ thuật (Technical Threat Landscape)
  ## 3. Chiến lược phòng vệ theo chiều sâu (Defense-in-Depth Roadmap)
  ## 4. Ánh xạ Tiêu chuẩn & Khung hướng dẫn (OWASP / CWE / ASVS Mapping)
- Chèn trích dẫn [i] xuyên suốt các mục nội dung.

YÊU CẦU TỔNG HỢP:
{query}

Hãy sinh bản thảo tổng hợp Markdown hoàn chỉnh:"""


def build_synthesis_messages(
    query: str,
    llm_context: str,
    intent: str = "SEARCH",
    evidence_status: str = "FULL",
    conversation_history: list[dict[str, Any]] | None = None,
) -> list[dict[str, str]]:
    """
    Xây dựng danh sách tin nhắn (Messages Payload) chuẩn bị cho LLM Client.
    Tự động chọn prompt builder phù hợp dựa trên intent và evidence_status.
    """
    mode = "HYBRID" if evidence_status.upper() == "PARTIAL" else "STRICT_RAG"
    normalized_intent = (intent or "SEARCH").upper()

    if normalized_intent == "COMPARE":
        user_prompt = build_compare_prompt(query, llm_context, mode=mode)
    elif normalized_intent == "SYNTHESIS":
        user_prompt = build_synthesis_prompt(query, llm_context, mode=mode)
    else:  # Fallback to SEARCH
        user_prompt = build_search_prompt(query, llm_context, mode=mode)

    messages: list[dict[str, str]] = [
        {"role": "system", "content": SYSTEM_INSTRUCTION_SYNTHESIS}
    ]

    # Bổ sung tối đa 4 lượt hội thoại gần nhất nếu có
    if conversation_history:
        for turn in conversation_history[-4:]:
            role = str(turn.get("role", "user"))
            content = str(turn.get("content", ""))
            if content.strip():
                messages.append({"role": role, "content": content})

    messages.append({"role": "user", "content": user_prompt})
    return messages
