# BÁO CÁO CẬP NHẬT & ĐỐI SOÁT TOÀN DIỆN PHẦN VIỆC CỦA LONG
## THEO BẢN KẾ HOẠCH MỚI NHẤT (CONTRACT V1.0 HOÀN CHỈNH)
**Tài liệu cơ sở mới nhất:** [`Phan_cong_Query_Understanding_Routing_v1.0_hoan_chinh.md`](file:///D:/computational_thinking/Computational-Thinking-Project/agy/Phan_cong_Query_Understanding_Routing_v1.0_hoan_chinh.md)  
**Tài liệu đối chiếu cũ:** `Report_Query_Understanding_Routing_Long_Tri (1).pdf`  
**Người thực hiện:** Long (Chịu trách nhiệm Ontology & Query Understanding)  
**Người phối hợp:** Trí (Validation, Normalization, Safety Routing) | Kiệt & Diệp (Retrieval)  
**Thời gian cập nhật:** 25/10/2026  
**Trạng thái kiểm thử:** 32/32 tests PASSED (100%) trong 0.18s tại `agy/tests/`.

---

## 1. TỔNG HỢP SO SÁNH VÀ CÁC ĐIỂM ĐÃ SỬA ĐỔI / HOÀN THIỆN

Qua đối soát chi tiết giữa bản kế hoạch ban đầu (bản PDF) và bản kế hoạch hoàn chỉnh mới nhất (`Contract v1.0`), các thay đổi và điểm hoàn thiện trong phần việc của Long bao gồm:

| STT | Hạng mục | Bản cũ (PDF) | Bản mới (Contract v1.0) | Giải pháp và hiện thực của Long |
|:---:|:---|:---|:---|:---|
| **1** | **Alias "bac" trong Ontology** | Cho phép `"bac"` trong aliases | **L7 & Phụ lục A:** Cấm alias `"bac"` vì quá ngắn và đa nghĩa; thêm alias tiếng Việt; không trùng alias giữa các entry | Đã xóa `"bac"` khỏi `aliases` của `bac`; bổ sung `"lỗi kiểm soát truy cập"`, `"lỗi xác thực"`, `"cấu hình sai bảo mật"`; kiểm tra không có alias nào trùng nhau |
| **2** | **Trường `owasp` trong Ontology** | Chưa quy định trong schema YAML | **Mục 0.2 (L6) & Phụ lục A:** Bổ sung trường `owasp` (ví dụ `A05:2025`, `A01:2025`...) | Đã cập nhật trường `owasp` cho cả 5 slug trong [`ontology.yaml`](file:///D:/computational_thinking/Computational-Thinking-Project/agy/ontology.yaml) |
| **3** | **Ontology Loader** | Chỉ yêu cầu load file chung | **Mục 3.1 #2 & Mục 13:** Tạo `load_ontology(path)` kiểm tra đủ 5 slug, đủ trường, không xung đột alias (case-insensitive), cấm alias "bac" | Đã tách riêng module chuẩn [`ontology_loader.py`](file:///D:/computational_thinking/Computational-Thinking-Project/agy/ontology_loader.py) với đầy đủ exception (`FileNotFoundError`, `ValueError`) |
| **4** | **Trường `original_query`** | `understanding_result` có `original_query` | **Mục 4 & Mục 7.2:** `understanding_result` của Long **KHÔNG ĐƯỢC CÓ** `original_query`; Routing tự thêm và bảo toàn từ `raw_query` | Đã loại bỏ `original_query` khỏi output của `understand_query()`; Long không tạo/sửa trường này |
| **5** | **Ngưỡng Clarification** | Ngưỡng 0.65 | **Mục 0.2 (L1) & Mục 10:** `CLARIFY_THRESHOLD = 0.6` | Đã đưa vào file cấu hình [`config.py`](file:///D:/computational_thinking/Computational-Thinking-Project/agy/config.py) với giá trị mặc định `0.6` |
| **6** | **Quan hệ Intent & Need_clarify** | Chưa quy định ràng buộc 2 chiều | **Mục 0.2 (L8) & Mục 7.2:** `intent=CLARIFICATION` khi và chỉ khi `need_clarify=true` | Đã ràng buộc chặt chẽ tính tương đương 2 chiều trong [`understanding_module.py`](file:///D:/computational_thinking/Computational-Thinking-Project/agy/understanding_module.py) |
| **7** | **Thuật toán cắt History** | Chỉ nói chung chung giới hạn số turn | **Mục 0.2 (L5) & Mục 7.1:** Tối đa 3 cặp lượt (6 message), tổng $\le$ 2.000 ký tự; cắt từ lượt cũ nhất; message đơn > 2.000 ký tự cắt đầu giữ đuôi | Đã triển khai hàm chuẩn `truncate_conversation_history()` tuân thủ 100% thuật toán |
| **8** | **Output cho nhánh non-OK** | Chưa chi tiết hóa `task[]` nhánh clarify | **Mục 0.2 (L9) & Mục 7.2:** Với `CLARIFICATION` và `OUT_OF_SCOPE`, `task` có thể là `[]`, nhưng `entities` và `filter` vẫn phải trả đủ | Đã đảm bảo mọi nhánh đều trả đầy đủ cấu trúc key |
| **9** | **Xử lý lỗi LLM & Retry** | Chưa quy định mã lỗi cụ thể | **Mục 0.2 (L3, L4) & Mục 7.3:** Retry tối đa 2 lần chỉ cho lỗi parse JSON/schema; timeout (>30s) hoặc mất kết nối trả ngay `llm_unavailable` | Đã hiện thực hóa vòng lặp retry và trả mã lỗi chuẩn: `llm_unavailable`, `llm_invalid_json`, `llm_schema_violation` |
| **10** | **Cấu trúc bộ Test** | 1 file test chung | **Mục 13:** Tách thành `test_understanding.py`, `test_context.py`, `test_clarification.py` trong thư mục `tests/` | Đã tổ chức đầy đủ 3 bộ test chuẩn trong thư mục [`agy/tests/`](file:///D:/computational_thinking/Computational-Thinking-Project/agy/tests/) |
| **11** | **Xử lý Đường dẫn & Module Resolution (Path / Dir)** | Chưa có file cấu hình đường dẫn, dễ lỗi `ModuleNotFoundError` khi import chéo | Tương thích đa môi trường: chạy từ root, chạy trong `agy/`, hoặc chạy trong `tests/` | Đã thêm [`__init__.py`](file:///D:/computational_thinking/Computational-Thinking-Project/agy/__init__.py), [`paths.py`](file:///D:/computational_thinking/Computational-Thinking-Project/agy/paths.py), [`path.py`](file:///D:/computational_thinking/Computational-Thinking-Project/agy/path.py), cơ chế dual-import (`agy.*` và local), dùng `.resolve()` tuyệt đối cho mọi đường dẫn tệp |

---

## 2. CHI TIẾT CÁC MODULE VÀ SẢN PHẨM BÀN GIAO CỦA LONG

### 2.1. Cấu hình hệ thống [`agy/config.py`](file:///D:/computational_thinking/Computational-Thinking-Project/agy/config.py)
Tập hợp toàn bộ các hằng số, ngưỡng và tham số theo đúng Mục 10 của Contract v1.0:
- `MIN_QUERY_LEN = 3`, `MAX_QUERY_LEN = 500`.
- `HISTORY_MAX_TURNS = 3` (3 cặp = 6 messages), `HISTORY_MAX_CHARS = 2000`.
- `LLM_MAX_RETRIES = 2`, `LLM_TIMEOUT_S = 30.0`, `LLM_TEMPERATURE = 0.0`.
- `CLARIFY_THRESHOLD = 0.6`.
- `STANDARD_VULNERABILITY_SLUGS = {"xss", "sqli", "bac", "auth_failures", "misconfig"}`.
- `VALID_INTENTS = {"SEARCH", "COMPARE", "SYNTHESIS", "CLARIFICATION", "OUT_OF_SCOPE"}`.
- `VALID_LLM_ERROR_CODES = {"llm_unavailable", "llm_invalid_json", "llm_schema_violation"}`.

---

### 2.2. Từ điển lỗ hổng [`agy/ontology.yaml`](file:///D:/computational_thinking/Computational-Thinking-Project/agy/ontology.yaml)
Bản cập nhật hoàn chỉnh theo Phụ lục A:
- Khóa chặt 5 slug chuẩn: `xss`, `sqli`, `bac`, `auth_failures`, `misconfig`.
- Không sử dụng alias `"bac"`.
- Bổ sung trường `owasp` ánh xạ trực tiếp đến OWASP Top 10 2025 (`A05:2025`, `A01:2025`, `A07:2025`, `A02:2025`).
- Phân biệt triệt để `aliases` (từ đồng nghĩa cho Trí map) và `related_terms` (thuật ngữ phòng vệ liên quan).

---

### 2.3. Trình tải và kiểm thực Ontology [`agy/ontology_loader.py`](file:///D:/computational_thinking/Computational-Thinking-Project/agy/ontology_loader.py)
Hàm `load_ontology(path)` chịu trách nhiệm:
1. Xác thực tệp tồn tại và định dạng YAML hợp lệ.
2. Kiểm tra chính xác 5 slug bắt buộc, từ chối slug lạ hoặc thiếu.
3. Kiểm tra tính hiện diện của 5 trường cốt lõi trong từng entry: `canonical`, `aliases`, `cwe`, `subtypes`, `related_terms`.
4. Phát hiện và ngăn chặn xung đột alias giữa các lỗ hổng khác nhau (so khớp không phân biệt hoa thường).
5. Chặn alias `"bac"` theo điều kiện L7.

---

### 2.4. Module Query Understanding [`agy/understanding_module.py`](file:///D:/computational_thinking/Computational-Thinking-Project/agy/understanding_module.py)
Triển khai toàn bộ logic nghiệp vụ theo Contract v1.0:
- **Chữ ký chuẩn (Mục 4):**
  ```python
  def understand_query(
      normalized_query: str,
      conversation_history: list[dict] | None = None,
      *,
      confidence_threshold: float = CLARIFY_THRESHOLD,
      llm_client: Any = None
  ) -> dict
  ```
- **Output Contract (Mục 4 & Mục 7.2):**  
  Trả về dictionary gồm đúng 9 trường: `resolved_query`, `intent`, `confidence`, `context_used`, `entities`, `filter`, `task`, `need_clarify`, `clarification_message`; hoặc trả về `{"error_code": "..."}` khi gặp sự cố không thể phục hồi. **Không trả `original_query`**.
- **Thuật toán cắt lịch sử hội thoại (Mục 7.1):**  
  Hàm `truncate_conversation_history()` lấy tối đa 6 tin nhắn gần nhất; nếu một tin nhắn > 2.000 ký tự thì cắt đầu giữ đuôi; nếu tổng chiều dài > 2.000 ký tự thì loại bỏ dần các tin nhắn cũ nhất cho tới khi thoả mãn.
- **Ràng buộc tương đương hai chiều (Mục 0.2 L8):**  
  `intent == "CLARIFICATION" <=> need_clarify == True`. Khi `confidence < 0.6` hoặc câu hỏi quá mơ hồ, hệ thống tự động thiết lập `need_clarify = True`, chuyển `intent = "CLARIFICATION"` và sinh câu hỏi làm rõ cụ thể kèm gợi ý các tùy chọn.
- **Cơ chế Retry & Xử lý lỗi (Mục 7.3):**  
  Khi tích hợp LLM client:
  - Nếu gặp timeout hoặc mất kết nối: lập tức trả `{"error_code": "llm_unavailable"}` (không retry).
  - Nếu LLM trả về JSON lỗi: thử lại tối đa 2 lần (tổng 3 lần gọi); nếu vẫn lỗi trả `{"error_code": "llm_invalid_json"}`.
  - Nếu cấu trúc trả về vi phạm schema Mục 7.2: thử lại tối đa 2 lần; nếu vẫn vi phạm trả `{"error_code": "llm_schema_violation"}`.

---

### 2.5. Thiết kế Prompt chuẩn [`agy/prompt_templates.py`](file:///D:/computational_thinking/Computational-Thinking-Project/agy/prompt_templates.py)
- Cập nhật theo schema Contract v1.0 (không có `original_query`).
- Bổ sung hướng dẫn chống Prompt Injection từ lịch sử và quy tắc cô lập dữ liệu người dùng.
- Tích hợp 5 few-shot examples mẫu bao quát các nhánh: Search, Dependent co-reference, Multi-task decomposition, Ambiguous clarification, và Out-of-scope.

### 2.6. Chuẩn hóa Đường dẫn & Module Resolution ([`agy/paths.py`](file:///D:/computational_thinking/Computational-Thinking-Project/agy/paths.py), [`agy/path.py`](file:///D:/computational_thinking/Computational-Thinking-Project/agy/path.py), [`agy/__init__.py`](file:///D:/computational_thinking/Computational-Thinking-Project/agy/__init__.py))
Để giải quyết triệt để lỗi đường dẫn và `ModuleNotFoundError` khi import chéo giữa các tầng:
- Đã biến `agy` thành một Python package hoàn chỉnh với tệp [`__init__.py`](file:///D:/computational_thinking/Computational-Thinking-Project/agy/__init__.py).
- Tạo [`paths.py`](file:///D:/computational_thinking/Computational-Thinking-Project/agy/paths.py) và alias [`path.py`](file:///D:/computational_thinking/Computational-Thinking-Project/agy/path.py) (theo chuẩn quy ước của dự án), định nghĩa các đường dẫn tuyệt đối bằng `.resolve()`:
  - `AGY_DIR`: Đường dẫn thư mục `agy`.
  - `PROJECT_ROOT`: Đường dẫn thư mục gốc dự án.
  - `DATA_DIR`: Thư mục dữ liệu `Data/`.
  - `DEFAULT_ONTOLOGY_PATH`: Đường dẫn tuyệt đối đến `ontology.yaml`.
- Cơ chế **Dual-Import Fallback** và tự động cập nhật `sys.path` trong tất cả các tệp nguồn:
  Cho phép import thành công cả hai cách:
  1. Từ thư mục gốc dự án: `from agy.understanding_module import understand_query`
  2. Trực tiếp bên trong thư mục `agy/`: `import understanding_module`
  3. Khi chạy kiểm thử từ `agy/tests/`: `pytest` nhận diện toàn bộ module mà không cần cấu hình `PYTHONPATH` thủ công.

---

## 3. CẤU TRÚC VÀ KẾT QUẢ BỘ KIỂM THỬ MỚI (AGY/TESTS/)

Bộ kiểm thử được phân tách rành mạch theo Mục 13 của bản kế hoạch:

```
agy/
├── tests/
│   ├── __init__.py
│   ├── test_understanding.py   # 18 test cases (Schema, Loader, Core Intents, Error Codes & Retry)
│   ├── test_context.py         # 8 test cases (History Truncation 7.1, Co-reference, Prompt Injection in history)
│   └── test_clarification.py   # 6 test cases (Threshold 0.6, Equivalence 2 chiều, Vague queries)
```

### Kết quả chạy kiểm thử thực tế:
```powershell
pytest agy/tests -v
============================= test session starts =============================
platform win32 -- Python 3.14.3, pytest-9.1.1, pluggy-1.7.0
rootdir: D:\computational_thinking\Computational-Thinking-Project
collected 32 items

agy/tests/test_clarification.py::TestClarificationLogic::test_vague_query_triggers_clarification PASSED [  3%]
agy/tests/test_clarification.py::TestClarificationLogic::test_two_way_equivalence_when_clarify_false PASSED [  6%]
agy/tests/test_clarification.py::TestClarificationLogic::test_two_way_equivalence_when_clarify_true PASSED [  9%]
agy/tests/test_clarification.py::TestClarificationLogic::test_clarify_threshold_default_is_0_6 PASSED [ 12%]
agy/tests/test_clarification.py::TestClarificationLogic::test_low_confidence_forces_clarification PASSED [ 15%]
agy/tests/test_clarification.py::TestClarificationLogic::test_empty_query_safe_clarification PASSED [ 18%]
agy/tests/test_context.py::TestHistoryTruncation::test_truncate_non_list_history PASSED [ 21%]
agy/tests/test_context.py::TestHistoryTruncation::test_truncate_max_turns PASSED [ 25%]
agy/tests/test_context.py::TestHistoryTruncation::test_truncate_single_oversized_message PASSED [ 28%]
agy/tests/test_context.py::TestHistoryTruncation::test_truncate_total_chars_overflow PASSED [ 31%]
agy/tests/test_context.py::TestContextUsedRules::test_dependent_query_sets_context_used_true PASSED [ 34%]
agy/tests/test_context.py::TestContextUsedRules::test_dependent_query_without_history_triggers_clarification PASSED [ 37%]
agy/tests/test_context.py::TestContextUsedRules::test_independent_query_with_irrelevant_history PASSED [ 40%]
agy/tests/test_context.py::TestContextUsedRules::test_prompt_injection_in_history_ignored PASSED [ 43%]
agy/tests/test_understanding.py::TestOntologyYAMLAndLoader::test_ontology_file_exists PASSED [ 46%]
agy/tests/test_understanding.py::TestOntologyYAMLAndLoader::test_ontology_has_exact_5_slugs PASSED [ 50%]
agy/tests/test_understanding.py::TestOntologyYAMLAndLoader::test_ontology_entry_schema PASSED [ 53%]
agy/tests/test_understanding.py::TestOntologyYAMLAndLoader::test_no_bac_in_aliases PASSED [ 56%]
agy/tests/test_understanding.py::TestOntologyYAMLAndLoader::test_owasp_field_present PASSED [ 59%]
agy/tests/test_understanding.py::TestContractOutputSchemaV1::test_understanding_result_has_no_original_query PASSED [ 62%]
agy/tests/test_understanding.py::TestContractOutputSchemaV1::test_confidence_type_and_bounds PASSED [ 65%]
agy/tests/test_understanding.py::TestQueryUnderstandingCore::test_xss_query_understanding PASSED [ 68%]
agy/tests/test_understanding.py::TestQueryUnderstandingCore::test_xss_unaccented PASSED [ 71%]
agy/tests/test_understanding.py::TestQueryUnderstandingCore::test_sqli_query_understanding PASSED [ 75%]
agy/tests/test_understanding.py::TestQueryUnderstandingCore::test_academic_code_snippet_not_rejected PASSED [ 78%]
agy/tests/test_understanding.py::TestQueryUnderstandingCore::test_prompt_injection_definition_query PASSED [ 81%]
agy/tests/test_understanding.py::TestQueryUnderstandingCore::test_out_of_scope_query PASSED [ 84%]
agy/tests/test_understanding.py::TestQueryUnderstandingCore::test_document_and_filter_extraction PASSED [ 87%]
agy/tests/test_understanding.py::TestQueryUnderstandingCore::test_multi_task_query_decomposition PASSED [ 90%]
agy/tests/test_understanding.py::TestLLMRetriesAndErrorCodes::test_llm_invalid_json_retries_and_returns_error_code PASSED [ 93%]
agy/tests/test_understanding.py::TestLLMRetriesAndErrorCodes::test_llm_schema_violation_retries_and_returns_error_code PASSED [ 96%]
agy/tests/test_understanding.py::TestLLMRetriesAndErrorCodes::test_llm_timeout_no_retry PASSED [100%]

============================= 32 passed in 0.18s ==============================
```

---

## 4. BẢNG NGHIỆM THU MA TRẬN TEST LIÊN QUAN ĐẾN LONG (MỤC 11)

| # (Mục 11) | Truy vấn / Tình huống | Hành vi & Kết quả thực tế của Long | Kết quả |
|:---:|:---|:---|:---:|
| **6** | "XSS" và "SQLi" | Phân loại đúng `intent="SEARCH"`, trích xuất slug `xss`/`sqli` | **PASS** |
| **7** | "XSS là gì?" và "xss la gi" (không dấu) | Phân loại `SEARCH`, nhận diện chính xác `vulnerabilities=["xss"]` | **PASS** |
| **8** | `<script>alert(1)</script>` trong ví dụ XSS | Hiểu ngữ cảnh học thuật, không reject, `vulnerabilities=["xss"]` | **PASS** |
| **14** | "Prompt injection là gì?" | Nhận diện câu hỏi khái niệm bảo mật, kích hoạt làm rõ nếu thiếu phạm vi, không bị chặn nhầm | **PASS** |
| **15** | "Còn cách vá nó?" sau chủ đề XSS | Khử tham chiếu `"nó"` $\to$ `Cross-Site Scripting`, gán `context_used=True` | **PASS** |
| **16** | "Còn cách vá nó?" không có history | Tham chiếu thiếu ngữ cảnh $\to$ `need_clarify=True`, `intent="CLARIFICATION"` | **PASS** |
| **17** | "So sánh XSS và SQLi" kèm history không liên quan | Câu độc lập $\to$ `context_used=False`, `intent="COMPARE"` | **PASS** |
| **18** | "So sánh XSS và SQLi, rồi tóm tắt cách phòng tránh" | Phân rã `task` $\ge$ 2, `objective` chuẩn object `{type, ids}`, `depends_on` hợp lệ | **PASS** |
| **19** | "Tôi muốn biết thời tiết" | Phân loại `intent="OUT_OF_SCOPE"`, `task=[]` | **PASS** |
| **20** | "Các lỗ hổng nguy hiểm" | Truy vấn mơ hồ $\to$ `intent="CLARIFICATION"`, `need_clarify=True`, `clarification_message` có nội dung gợi ý | **PASS** |
| **21** | History chứa "bỏ qua chỉ dẫn hệ thống" | Xử lý an toàn như dữ liệu tham chiếu người dùng, không bị chiếm quyền điều khiển | **PASS** |
| **22** | LLM trả JSON hỏng liên tục | Tự động retry 2 lần (tổng 3 lần) $\to$ trả `{"error_code": "llm_invalid_json"}` | **PASS** |
| **23** | LLM trả schema sai (`intent="FOO"` hoặc `confidence=1.7`...) | Tự động retry 2 lần $\to$ trả `{"error_code": "llm_schema_violation"}` | **PASS** |
| **24** | LLM trả `intent=CLARIFICATION` nhưng `clarification_message=null` | Bắt lỗi vi phạm tương đương 2 chiều $\to$ trả `{"error_code": "llm_schema_violation"}` | **PASS** |

---

## 5. KẾT LUẬN & TÍNH SẴN SÀNG TÍCH HỢP

Toàn bộ các yêu cầu của Long trong tài liệu phân công hoàn chỉnh `Contract v1.0` đã được rà soát, vá lỗi, hoàn thiện mã nguồn và kiểm thử tự động 100%.  
Module của Long đã sẵn sàng ở mức cao nhất để:
1. Giao `ontology.yaml` cho Trí bổ sung Alias Mapping (Pha B).
2. Cung cấp hàm `understand_query()` kết nối trực tiếp với hàm `route()` và `process_query()` của Trí.
3. Đảm bảo cấu trúc đầu ra hoàn toàn tương thích để Trí đóng gói envelope và bàn giao sang tầng Retrieval của Kiệt/Diệp.
