"""
Unified configuration constants for Query Understanding & Routing (Contract v1.0).
"""

from __future__ import annotations
import os

# Query length boundaries
MIN_QUERY_LEN: int = 3
MAX_QUERY_LEN: int = 500

# Spam detection thresholds
SPAM_CHAR_THRESHOLD: int = 30
SPAM_TOKEN_THRESHOLD: int = 8

# Conversation history boundaries
# Maximum 3 turn pairs (6 messages) and maximum 2,000 total characters
HISTORY_MAX_TURNS: int = 3  # pairs: user + assistant = 1 pair
HISTORY_MAX_MESSAGES: int = HISTORY_MAX_TURNS * 2  # 6 messages
HISTORY_MAX_CHARS: int = 2000

# LLM execution settings
LLM_TEMPERATURE: float = 0.0
LLM_TIMEOUT_S: float = 30.0
LLM_MAX_RETRIES: int = 2  # Only for parse/schema errors (total 3 attempts)

# Provider & model defaults (configurable via environment variables)
LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "").strip().lower()
LLM_MODEL: str = os.getenv("LLM_MODEL", "")
OLLAMA_HOST: str = os.getenv("OLLAMA_HOST", "http://localhost:11434")
OPENAI_BASE_URL: str = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")

# Clarification threshold
CLARIFY_THRESHOLD: float = 0.6

# Ontology standard slugs
STANDARD_VULNERABILITY_SLUGS: set[str] = {
    "xss",
    "sqli",
    "bac",
    "auth_failures",
    "misconfig",
}

# Intent categories & Task intents
VALID_INTENTS: set[str] = {
    "SEARCH",
    "COMPARE",
    "SYNTHESIS",
    "CLARIFICATION",
    "OUT_OF_SCOPE",
}

# Intents allowed for task execution leading to status=ok
VALID_TASK_INTENTS: set[str] = {
    "SEARCH",
    "COMPARE",
    "SYNTHESIS",
}

# Permitted LLM error codes
VALID_LLM_ERROR_CODES: set[str] = {
    "llm_unavailable",
    "llm_invalid_json",
    "llm_schema_violation",
}

# User-facing messages & Reject reasons
MESSAGES: dict[str, str] = {
    "empty_query": "Câu hỏi đang trống. Vui lòng nhập nội dung cần hỏi.",
    "query_too_short": "Câu hỏi quá ngắn. Vui lòng mô tả rõ hơn.",
    "query_too_long": f"Câu hỏi quá dài. Vui lòng rút gọn (tối đa {MAX_QUERY_LEN} ký tự).",
    "spam_detected": "Nội dung có vẻ không hợp lệ. Vui lòng nhập lại câu hỏi.",
    "invalid_characters": "Câu hỏi chứa ký tự không hợp lệ. Vui lòng nhập lại.",
    "invalid_type": "Dữ liệu đầu vào không hợp lệ.",
    "unsafe_attack_intent": "Không thể hỗ trợ xâm nhập trái phép; có thể hỗ trợ kiểm tra an toàn trong phạm vi được phép, hoặc giải thích cách phòng chống.",
    "prompt_injection_attempt": "Không thể thực hiện yêu cầu này. Bạn có thể hỏi về các lỗ hổng bảo mật web và cách phòng chống.",
    "suspicious_pattern": "Không thể xử lý yêu cầu này. Vui lòng diễn đạt lại câu hỏi về kiến thức bảo mật.",
}

SAFETY_CLARIFY_MESSAGE: str = "Mình không cung cấp hướng dẫn tấn công mục tiêu thực tế. Nếu phục vụ mục đích nghiên cứu hoặc làm bài lab, bạn hãy mô tả kịch bản/lỗ hổng cụ thể để chúng ta cùng phân tích nguyên lý và giải pháp nhé."
OUT_OF_SCOPE_MSG: str = "Câu hỏi này nằm ngoài phạm vi kiến thức bảo mật web của hệ thống (XSS, SQL Injection, Broken Access Control, Authentication Failures, Security Misconfiguration)."
ERROR_MSG: str = "Hệ thống tạm thời không xử lý được yêu cầu. Vui lòng thử lại sau."

# Safety and Prompt Injection Regex Patterns
# Safety patterns are written against folded text: lowercase, no diacritics, "đ" -> "d".
INJECTION_PATTERNS: list[str] = [
    r"\b(bo qua|quen di|ignore|disregard|forget)\b.{0,25}\b(huong dan|chi dan|chi thi|lenh|instructions?|prompts?|rules?)\b",
    r"\b(in ra|hien thi|tiet lo|cho toi xem|reveal|print|show|display)\b.{0,20}\b(system prompt|prompt he thong|hidden prompt)\b",
    r"\b(ban|you)\s+(bay gio la|are now)\b",
    r"\b(developer mode|dan mode)\b",
]
INJECTION_TOPIC_PATTERN: str = r"\b(prompt injection|injection prompt|jailbreak)\b"

ATTACK_VERB_PATTERN: str = r"\b(hack(ing)?|khai thac|exploit(ing)?|bypass|dump|crack|chiem quyen|lay cap|danh cap)\b"
# Counts as an attack verb only when a target is also present
WEAK_ATTACK_VERB_PATTERN: str = r"\b(tan cong|xam nhap)\b"

UNAUTHORIZED_PATTERN: str = r"(\bkhong (duoc phep|xin phep|duoc uy quyen|co (su )?(cho )?phep)\b|\bchua duoc phep\b|\btrai phep\b|\bcua nguoi khac\b|\bunauthori[sz]ed\b)"

TARGET_PATTERNS: list[str] = [
    r"\b(trang web|website|web|site|server|may chu)\s+(nay|do|kia|cua)\b",
    r"https?://",
    r"\b[a-z0-9-]+(\.[a-z0-9-]+)*\.(com|net|org|vn|edu|gov|io)\b",
    r"\b\d{1,3}(\.\d{1,3}){3}\b",
]

# Strong authorization context: own system or a known lab
STRONG_AUTH_PATTERN: str = r"(\b(cua toi|cua minh|cua chung toi)\b|\bdvwa\b|\bwebgoat\b|\bjuice shop\b|\bmoi truong (kiem thu|lab)\b|(?<!khong )(?<!chua )\bduoc phep\b)"

# Explanation framing
EXPLAIN_PATTERN: str = r"\b(la gi|giai thich|tai sao|why|what is|khai niem|dinh nghia)\b"

# Request for instructions
REQUEST_CUE_PATTERN: str = r"\b(huong dan|cach|lam sao|lam the nao|how to|tutorial|buoc|step|giup toi|cho toi)\b"

# Unauthorized access phrased as an action
UNAUTH_ACCESS_PATTERN: str = r"(\b(truy cap|dang nhap|access)\b.{0,25}\b(trai phep|khong duoc phep|khong xin phep|unauthori[sz]ed)\b|\b(trai phep|unauthori[sz]ed)\b.{0,25}\b(truy cap|access)\b)"

# Defensive, educational or authorized context
MITIGATION_PATTERN: str = r"(\bla gi\b|\bphong (chong|tranh)\b|\bchong\b|\bngan chan\b|\bkhac phuc\b|(?<!khong )(?<!chua )\bduoc phep\b|\bcach (va|phat hien)\b|\bva (loi|lo hong)\b|\bphat hien\b|\blab\b|\bdvwa\b|\bwebgoat\b|\bjuice shop\b|\b(cua toi|cua minh|cua chung toi)\b|\bmoi truong (kiem thu|lab)\b|\bde hieu\b|\bgiai thich\b|\bdefen[cs]e\b|\bprevent\w*\b|\bmitigat\w*\b|\bremediat\w*\b|\bwhat is\b|\bdetect\w*\b)"
