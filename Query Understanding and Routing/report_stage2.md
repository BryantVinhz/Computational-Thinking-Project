# BÁO CÁO PHÂN CÔNG & THIẾT KẾ TRIỂN KHAI (CONTRACT V1.0)
## Query Understanding & Routing | Phần việc của Long
*Chi tiết đầy đủ xem tại [report.md](file:///D:/computational_thinking/Computational-Thinking-Project/agy/report.md).*

Đã đối soát toàn bộ bản kế hoạch mới nhất [`Phan_cong_Query_Understanding_Routing_v1.0_hoan_chinh.md`](file:///D:/computational_thinking/Computational-Thinking-Project/agy/Phan_cong_Query_Understanding_Routing_v1.0_hoan_chinh.md) với bản cũ và hoàn tất các sửa đổi theo đúng yêu cầu:
1. **Ontology & Loader:** Xóa alias `"bac"`, thêm trường `owasp`, triển khai `load_ontology` trong `ontology_loader.py` chống xung đột alias.
2. **Contract Output Schema:** `understand_query()` trả `understanding_result` KHÔNG chứa `original_query` (do Routing tự gắn).
3. **Clarify Threshold:** Chốt `CLARIFY_THRESHOLD = 0.6` trong `config.py`.
4. **Tương đương 2 chiều:** `intent == "CLARIFICATION" <=> need_clarify == True`.
5. **Cắt lịch sử hội thoại:** Thuật toán chuẩn Section 7.1 (tối đa 3 cặp lượt / 6 messages, tổng <= 2.000 ký tự).
6. **Xử lý lỗi LLM & Retry:** Retry tối đa 2 lần, hỗ trợ các mã lỗi `llm_unavailable`, `llm_invalid_json`, `llm_schema_violation`.
7. **Kiểm thử tự động:** Đạt 32/32 tests PASSED (100%) tại `agy/tests/`.
