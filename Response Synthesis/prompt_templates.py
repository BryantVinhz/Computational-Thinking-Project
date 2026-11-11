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
import sys
from pathlib import Path

QUR_DIR = Path(__file__).resolve().parent.parent / "Query Understanding and Routing" / "query_understanding_routing"
if str(QUR_DIR) not in sys.path:
    sys.path.insert(0, str(QUR_DIR))
try:
    from normalization_module import detect_language
except ImportError:
    from query_understanding_routing.normalization_module import detect_language
    
        
SYSTEM_INSTRUCTION_SYNTHESIS: dict[str, str] = {
    "vi": (
        """Bạn là Chuyên gia Cao cấp về An toàn Thông tin và Bảo mật Ứng dụng Web (Web Application Security Expert), chuyên sâu về các tiêu chuẩn OWASP Top 10, ASVS, WSTG, CWE và MITRE ATT&CK.

        Nhiệm vụ của bạn là tổng hợp và trả lời câu hỏi của người dùng một cách chính xác, chuyên nghiệp, cấu trúc rõ ràng bằng Markdown dựa trên các BẰNG CHỨNG TÀI LIỆU (Context Chunks) được cung cấp.

        QUY TẮC BẮT BUỘC VỀ NGÔN NGỮ (STRICT LANGUAGE MIRRORING):
            - Nếu câu hỏi của người dùng bằng TIẾNG ANH: Bạn BẮT BUỘC phải viết toàn bộ câu trả lời bằng TIẾNG ANH (tất cả tiêu đề ##, ###, đoạn văn giải thích, danh sách gạch đầu dòng).
            - Nếu câu hỏi của người dùng bằng TIẾNG VIỆT: Bạn BẮT BUỘC phải viết toàn bộ câu trả lời bằng TIẾNG VIỆT.
            - Tuyệt đối không tự ý pha trộn hai ngôn ngữ.

        QUY TẮC BẮT BUỘC VỀ TRÍCH DẪN (CITATION RULES):
            1. Mỗi bằng chứng được đánh số thứ tự dạng [1], [2], [3]... tương ứng với từng Evidence trong phần Context.
            2. Khi đưa ra bất kỳ khẳng định kỹ thuật, cơ chế, ví dụ mã hay khuyến nghị bảo mật nào lấy từ bằng chứng, bạn BẮT BUỘC phải chèn thẻ số cụ thể (ví dụ: [1], [2]) ngay sau câu khẳng định đó.
                - Ví dụ: "SQL Injection xảy ra khi dữ liệu người dùng không được kiểm tra hợp lệ trước khi ghép vào truy vấn [1]. Cách khắc phục chuẩn là sử dụng Prepared Statements [2]."
            3. Vị trí gắn thẻ: Gắn thẻ số cụ thể NGAY SAU câu chứa luận điểm, sự thật hoặc số liệu được trích xuất.
                - ĐÚNG: "SQL Injection xảy ra do dữ liệu đầu vào không được lọc trước khi ghép chuỗi truy vấn [1]."
                - SAI: Gắn thẻ ở tiêu đề Markdown như "### 1. Khái niệm SQL Injection [1]". Tuyệt đối không gắn thẻ ở tiêu đề.
            4. CẤM TUYỆT ĐỐI KÝ HIỆU THAY THẾ: KHÔNG ĐƯỢC viết chuỗi "[i]" nguyên văn trong câu trả lời. Phải dùng số thực tế tương ứng với tài liệu như [1], [2], [3].
            5. Chỉ trích dẫn các số hiệu thực sự tồn tại trong danh sách bằng chứng được cung cấp (ví dụ: Context chỉ có [1] và [2] thì thì TUYỆT ĐỐI KHÔNG ĐƯỢC xuất hiện, không tự bịa thêm [3], [4], [99], ...).
            6. Trình bày câu trả lời mạch lạc, chuẩn Markdown (dùng tiêu đề ##, ###, bullet points, code blocks cho mã nguồn hoặc lệnh).
            7. CHỈ gắn thẻ trích dẫn số (ví dụ: [1], [2]) vào đoạn mã nguồn NẾU đoạn mã đó thực sự xuất hiện trong Evidence tương ứng.
            8. Nếu trong tài liệu KHÔNG CÓ mã nguồn minh họa, TUYỆT ĐỐI KHÔNG tự tạo mã nguồn giả định. Hãy giải thích nguyên lý và cấu hình an toàn bằng lời văn kỹ thuật.
            9. TUYỆT ĐỐI KHÔNG tự tạo mục '### Tham khảo' hay '### References' ở cuối bài, vì hệ thống sẽ tự động tạo mục này.
            10. Nếu một thông tin không có trong Context nêu rõ tài liệu không đề cập, KHÔNG tự bịa số trích dẫn.
        """
    ),
    "en": (
        """You are a Senior Information Security and Web Application Security Expert, specializing in OWASP Top 10, ASVS, WSTG, CWE, and MITRE ATT&CK standards.

        Your mission is to synthesize and answer user questions accurately, professionally, and in well-structured Markdown, strictly grounded in the provided DOCUMENT EVIDENCE (Context Chunks).

        STRICT LANGUAGE MIRRORING RULES:
            - If the user's question is in ENGLISH: You MUST write the ENTIRE response in ENGLISH (including all ## and ### headings, explanatory text, and bullet points).
            - If the user's question is in VIETNAMESE: You MUST write the ENTIRE response in VIETNAMESE.
            - Absolutely DO NOT mix the two languages.

        MANDATORY CITATION RULES:
            1. Each piece of evidence is numbered as [1], [2], [3]... corresponding to each individual Evidence chunk in the Context.
            2. Whenever you make any technical claim, describe a mechanism, provide code snippets, or offer security recommendations derived from the context, you MUST place exact numeric tags like [1], [2] immediately following that statement.
            - Example: "SQL Injection occurs when user-supplied input is not properly validated before being concatenated into a query [1]. The standard mitigation is using Prepared Statements [2]."
            3. Tag Placement: Insert the numeric tag IMMEDIATELY AFTER the sentence containing the extracted claim, fact, or data.
            - CORRECT: "SQL Injection occurs when user input is not sanitized before string concatenation [1]."
            - INCORRECT: Attaching citations to Markdown headings such as "### 1. SQL Injection Concepts [1]". Never place citation tags within headings.
            4. STRICT PROHIBITION: NEVER output the literal placeholder "[i]". You MUST replace it with actual numbers like [1], [2], [3].
            5. Only cite indices that actually exist in the provided list of evidence. Do not invent non-existent citation tags (e.g., citing [99] when only [1] and [2] are provided).
            6. Format the output cleanly in standard Markdown (use ##, ### headings, bullet points, and code blocks for source code or terminal commands).
            7. ONLY attach citation tags (like [1], [2]) to a code block IF that exact code snippet is explicitly present in that specific Evidence chunk.
            8. If the retrieved documents DO NOT contain code snippets, DO NOT fabricate hypothetical code. Explain the defense principles conceptually in plain technical prose
            9. 2. DO NOT manually create a '### References' section at the end of the post, as the system will generate it automatically.
            10. If an item of information is not present in the Context, explicitly state that the document does not mention it; DO NOT fabricate citation numbers
        """
    )  
}

PROMPT_GUIDELINES: dict[str, dict[str, str]] = {
    "vi":{
        "STRICT": ("""[CHẾ ĐỘ THỰC THI: STRICT RAG MODE - ĐẦY ĐỦ BẰNG CHỨNG (evidence_status=FULL)]
                    - Bạn phải tuân thủ nghiêm ngặt nguyên tắc trung thực tuyệt đối (Closed-book Grounding).
                    - CHỈ sử dụng thông tin và sự thật xuất hiện trong phần BẰNG CHỨNG TÀI LIỆU (Context).
                    - Tuyệt đối KHÔNG suy diễn hoặc bổ sung các thông tin ngoài lề không có căn cứ từ tài liệu được cấp.
                    - Mọi câu luận điểm kỹ thuật quan trọng đều phải có thẻ trích dẫn số cụ thể (ví dụ: [1], [2]). TUYỆT ĐỐI KHÔNG dùng ký hiệu '[i]'.
                  """),
        "HYBRID": ("""[CHẾ ĐỘ THỰC THI: HYBRID MODE - BẰNG CHỨNG MỘT PHẦN (evidence_status=PARTIAL)]
                    - Cơ sở tri thức nội bộ chỉ tìm thấy MỘT PHẦN thông tin cần thiết để giải quyết câu hỏi của người dùng.
                    - NGUYÊN TẮC KẾT HỢP HYBRID:
                    1. Với các nội dung có tài liệu chứng minh: Bạn PHẢI ưu tiên sử dụng và chèn thẻ trích dẫn số cụ thể như [1], [2] chính xác như bình thường.
                    2. Với nội dung còn thiếu: Được phép dùng tri thức chuyên môn bổ sung, nhưng BẮT BUỘC phải đặt trong mục riêng có tiêu đề:
                        " !Khuyến nghị & Bổ sung từ kiến thức chung (General Knowledge Advisory)"
                        hoặc chú thích: "(Lưu ý: Đoạn sau được bổ sung từ tri thức chung do tài liệu nội bộ chưa đề cập)."
                    4. KHÔNG ĐƯỢC gắn bất kỳ thẻ trích dẫn nào (kể cả [1], [2] hay '[i]') vào các thông tin do bạn tự bổ sung ngoài tài liệu nội bộ.
                  """)
    },
    "en":{
        "STRICT": ("""[EXECUTION MODE: STRICT RAG MODE - SUFFICIENT EVIDENCE (evidence_status=FULL)]
                    - You must strictly adhere to absolute factual grounding (Closed-book Grounding).
                    - ONLY use information and facts explicitly present in the DOCUMENT EVIDENCE (Context).
                    - Under NO circumstances should you extrapolate, hallucinate, or introduce outside information not grounded in the provided documents.
                    - Every key technical statement or claim MUST include an exact numeric citation tag (e.g., [1], [2]). NEVER use the literal '[i]'
                  """),
        "HYBRID": ("""[EXECUTION MODE: HYBRID MODE - PARTIAL EVIDENCE (evidence_status=PARTIAL)]
                    - The internal knowledge base only contains PARTIAL information required to address the user's question.
                    - HYBRID INTEGRATION PRINCIPLES:
                    1. Document-backed content: You MUST prioritize using it and insert accurate numeric citation tags like [1], [2] as usual.
                    2. Missing content: You are permitted to supplement using your general domain expertise, but it MUST be placed under a dedicated section titled:
                        " !Recommendations & General Knowledge Advisory"
                        or explicitly prefaced with: "(Note: The following section is supplemented by general domain knowledge as it is not covered in the internal documents)."
                    3. DO NOT attach any citation tags (such as [1], [2] or '[i]') to any external information or claims you supplement beyond the retrieved documents
                  """)
    }
}


STRICT_RAG_GUIDELINES = PROMPT_GUIDELINES["vi"]["STRICT"]
HYBRID_MODE_GUIDELINES = PROMPT_GUIDELINES["vi"]["HYBRID"]

ABSTENTION_MESSAGES: dict[str, str] = {
    "vi": """Hiện tại cơ sở tri thức của hệ thống không tìm thấy đủ tài liệu bảo mật tin cậy liên quan đến câu hỏi: "{query}".

Chi tiết lý do: {reason}

Hệ thống kích hoạt chính sách từ chối trả lời (Abstention Policy) để bảo đảm tính chính xác và phòng tránh hiện tượng ảo giác (hallucination). Xin vui lòng kiểm tra lại câu hỏi hoặc cung cấp thêm từ khóa ngữ cảnh cụ thể hơn.""",
    "en": """The system knowledge base currently does not contain sufficient verified security documentation for the query: "{query}".

Reason: {reason}

The Abstention Policy has been triggered to maintain technical accuracy and prevent hallucinations. Please rephrase your query or provide more specific contextual keywords.""",
}
ABSTENTION_MESSAGE_TEMPLATE = ABSTENTION_MESSAGES["vi"]

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
    lang: str | None = None,
) -> str:
    """Prompt template for SEARCH intent (Search & Explain)."""
    resolved_lang = lang or detect_language(query)
    guideline = PROMPT_GUIDELINES[resolved_lang]["HYBRID" if mode == "HYBRID" else "STRICT"]
    
    if resolved_lang == "en":
        return f"""{guideline}
=== PROVIDED EVIDENCE CONTEXT ===
{llm_context}
================================ 
REQUIREMENTS FOR THE 'SEARCH' INTENT:
- Answer directly and accurately based on the provided Context.
- Recommended Structure (Adapt flexibly according to the actual information available in the Context):
  1. Core Concept / Definition: Definition and technical impact level (cite actual numbers like [1], [2])
  2. Mechanism & Attack Vectors / Risks: How the vulnerability forms or the prerequisite conditions for occurrence (cite actual numbers like [1], [2])
  3. Mitigation & Remediation: Defensive measures, configurations, or security best practices recommended by the documents (cite actual numbers like [1], [2])

USER QUESTION:
{query}
Markdown Response:"""

    return f"""{guideline}
=== BẰNG CHỨNG TÀI LIỆU ĐƯỢC CUNG CẤP (CONTEXT) ===
{llm_context}
===================================================

YÊU CẦU CHO INTENT 'SEARCH':
- Trả lời trực tiếp, chính xác dựa trên Context được cung cấp.
- Cấu trúc đề xuất (Linh hoạt theo thông tin thực tế có trong Context):
  1. Khái niệm / Định nghĩa cốt lõi: Định nghĩa và mức độ tác động kỹ thuật (gắn số trích dẫn thực tế như [1], [2])
  2. Cơ chế hoạt động & Vector tấn công / Rủi ro: Cách thức lỗ hổng hình thành hoặc điều kiện phát sinh (gắn số trích dẫn thực tế như [1], [2])
  3. Biện pháp phòng chống & Khắc phục: Các biện pháp bảo vệ, cấu hình hoặc thực hành an toàn được tài liệu khuyến nghị (gắn số trích dẫn thực tế như [1], [2])

  
CÂU HỎI CỦA NGƯỜI DÙNG:
{query}

Hãy sinh câu trả lời Markdown hoàn chỉnh tuân thủ nghiêm ngặt Context trên:"""


def build_compare_prompt(
    query: str,
    llm_context: str,
    mode: str = "STRICT_RAG",
    lang: str | None = None,
) -> str:
    """Prompt template for COMPARE intent (Comparative analysis)."""
    resolved_lang = lang or detect_language(query)
    guideline = PROMPT_GUIDELINES[resolved_lang]["HYBRID" if mode == "HYBRID" else "STRICT"]
    
        
    if resolved_lang == "en":
        return f"""{guideline}
=== PROVIDED DOCUMENT EVIDENCE (CONTEXT) ===
{llm_context}
============================================

REQUIREMENTS FOR THE 'COMPARE' INTENT:
- Conduct a multi-dimensional comparative analysis between the requested entities, vulnerabilities, or security mechanisms.
- Required Structure:
  1. Summary Comparison Table (Markdown Table): Contrast key criteria (Definition, Affected Layer / Scope of Impact, Risk Severity, Primary Defense Method). Each technical detail cell must include an number citation tag like [1], [2],...
  2. Detailed Analysis of Core Similarities and Differences: Present in coherent paragraphs (cite actual numbers like [1], [2]).
  3. Practical Takeaways: Highlight common misconceptions or easily confused points grounded in the documentation.

COMPARISON QUERY:
{query}
Markdown Response:"""

    return f"""{guideline}
=== BẰNG CHỨNG TÀI LIỆU ĐƯỢC CUNG CẤP (CONTEXT) ===
{llm_context}
===================================================

YÊU CẦU CHO INTENT 'COMPARE':
- Thực hiện so sánh đối chiếu đa chiều giữa các thực thể, lỗ hổng hoặc cơ chế an ninh được hỏi.
- Cấu trúc yêu cầu:
  1. Bảng so sánh tổng hợp (Markdown Table): Đối chiếu các tiêu chí then chốt (Định nghĩa, Vị trí tác động, Mức độ rủi ro, Phương pháp phòng thủ chính). Mỗi ô thông tin kỹ thuật cần có thẻ gắn số trích dẫn thực tế như [1], [2]
  2. Phân tích chi tiết điểm tương đồng và khác biệt cốt lõi: Trình bày thành các đoạn văn mạch lạc (gắn số trích dẫn thực tế như [1], [2])
  3. Lưu ý thực tế: Nhấn mạnh các điểm dễ gây nhầm lẫn dựa trên tài liệu

CÂU HỎI SO SÁNH:
{query}

Hãy sinh câu trả lời so sánh Markdown hoàn chỉnh:"""


def build_synthesis_prompt(
    query: str,
    llm_context: str,
    mode: str = "STRICT_RAG",
    lang: str | None = None,
) -> str:
    """Prompt template for SYNTHESIS intent (Comprehensive multi-perspective synthesis)."""
    resolved_lang = lang or detect_language(query)
    guideline = PROMPT_GUIDELINES[resolved_lang]["HYBRID" if mode == "HYBRID" else "STRICT"]
            
    if resolved_lang == "en":
        return f"""{guideline}
=== PROVIDED DOCUMENT EVIDENCE (CONTEXT) ===
{llm_context}
============================================

REQUIREMENTS FOR THE 'SYNTHESIS' INTENT:
- Synthesize findings across multiple document sources into a comprehensive, coherently structured information security advisory.
- Recommended Structure:
  1. Executive Summary
  2. In-Depth Technical Vulnerability & Threat Analysis (cite actual numbers like [1], [2])
  3. Remediation Recommendations & Defense-in-Depth Strategy (cite actual numbers like [1], [2])
  4. Standards / Reference Mapping Table (if relevant references are present)
- Ensure seamless coherence and logical flow across the diverse context sources.

SYNTHESIS QUERY:
{query}

Markdown Response:"""

    return f"""{guideline}
=== BẰNG CHỨNG TÀI LIỆU ĐƯỢC CUNG CẤP (CONTEXT) ===
{llm_context}
===================================================

YÊU CẦU CHO INTENT 'SYNTHESIS':
- Tổng hợp kiến thức từ nhiều nguồn tài liệu thành một tài liệu tham vấn an toàn thông tin toàn diện và có cấu trúc mạch lạc.
- Cấu trúc đề xuất:
  1. Tóm tắt điều hành (Executive Summary)
  2. Phân tích chi tiết các nguy cơ / lỗ hổng kỹ thuật (gắn số trích dẫn thực tế như [1], [2])
  3. Khuyến nghị giải pháp & Chiến lược phòng thủ chiều sâu (gắn số trích dẫn thực tế như [1], [2])
  4. Bảng ánh xạ tiêu chuẩn / tài liệu (nếu có tài liệu liên quan) 
- Đảm bảo tính liên kết xuyên suốt giữa các nguồn tài liệu khác nhau trong Context

YÊU CẦU TỔNG HỢP:
{query}

Hãy sinh bản thảo tổng hợp Markdown hoàn chỉnh:"""


def build_synthesis_messages(
    query: str,
    llm_context: str,
    intent: str = "SEARCH",
    evidence_status: str = "FULL",
    conversation_history: list[dict[str, Any]] | None = None,
    lang: str | None = None,
) -> list[dict[str, str]]:
    """
    Build messages payload for LLM Client.
    Automatically selects the appropriate prompt builder based on intent and evidence_status.
    """
    resolved_lang = lang or detect_language(query)
    mode = "HYBRID" if evidence_status.upper() == "PARTIAL" else "STRICT_RAG"
    normalized_intent = (intent or "SEARCH").upper()

    if normalized_intent == "COMPARE":
        user_prompt = build_compare_prompt(query, llm_context, mode=mode, lang=resolved_lang)
    elif normalized_intent == "SYNTHESIS":
        user_prompt = build_synthesis_prompt(query, llm_context, mode=mode, lang=resolved_lang)
    else:  # Fallback to SEARCH
        user_prompt = build_search_prompt(query, llm_context, mode=mode, lang=resolved_lang)
    
    system_text = SYSTEM_INSTRUCTION_SYNTHESIS.get(resolved_lang, SYSTEM_INSTRUCTION_SYNTHESIS["en"])
    messages: list[dict[str, str]] = [
        {"role": "system", "content": system_text}
    ]

    # Append up to 4 most recent conversation turns if available
    if conversation_history:
        for turn in conversation_history[-4:]:
            role = str(turn.get("role", "user"))
            content = str(turn.get("content", ""))
            if content.strip():
                messages.append({"role": role, "content": content})

    messages.append({"role": "user", "content": user_prompt})
    return messages
