# 📘 TÀI LIỆU BỔ SUNG & THỐNG NHẤT TIÊU CHUẨN KỸ THUẬT PIPELINE
*(Dành cho tất cả các thành viên trong nhóm Computational Thinking Project)*

---

## 🎯 1. Mục đích tài liệu

Trong file `Note-Computational-Thinking (1).pdf` (Trang 10), Nhóm trưởng đã có lưu ý:
> *"Mấy cái Schema thì đó là tui tìm hiểu sơ bộ, ae xem xem có áp dụng vô được không [...], hoặc lúc làm ae gen AI hỏi nó thử xem có gợi ý cái nào không "quá" cần thiết có thể bỏ thì bỏ, cái nào lặp lại nhiều hoặc có thể tối ưu gì đấy thì có thể chỉnh sửa"*

Sau khi nhóm phụ trách **Step 1 & Step 2 (Ingestion & Parsing)** bắt tay vào hiện thực hóa code, một số cấu trúc trong Note bộc lộ nhược điểm khi chạy thực tế (lồng quá sâu, khó lưu database, khó bắt lỗi và dư thừa trường). 

Tài liệu này được lập ra nhằm:
1. **Chốt tiêu chuẩn kỹ thuật chính thức** từ đầu (Step 1) đến hiện tại (Step 2).
2. **Làm rõ các phần trong Code thay thế cho Note** và lý do tại sao phải dùng code làm chuẩn.
3. **Cung cấp mẫu chuẩn (template/triết lý)** để các bạn làm Step 3 (Normalization), Step 4 (Chunking) và Step 5 (Metadata/SQLite) tiếp nối mượt mà, không bị xung đột schema.

---

## 🌐 2. Quy ước chung của toàn bộ Pipeline (Bắt buộc tuân thủ)

### 2.1. Hệ thống ID xuyên suốt (Traceability Chain)
Mọi dữ liệu đi qua pipeline phải liên kết với nhau theo chuỗi mắt xích:
Document -> Version -> ContentBlock -> Chunk

| Đối tượng | Quy ước Format | Ví dụ thực tế | Nơi tạo ra |
| :--- | :--- | :--- | :--- |
| `document_id` | `doc_{sha256(canonical_title)[:8]}` | `doc_a1b2c3d4` | Step 1 (Ingestion) |
| `version_id` | `{document_id}_v{version_tag}` | `doc_a1b2c3d4_v1.0` | Step 1 (Ingestion) |
| `block_id` | `{version_id}_b{block_idx:05d}` | `doc_a1b2c3d4_v1.0_b00001` | Step 2 (Parsing) |
| `chunk_id` | `{version_id}_c{chunk_idx:05d}` | `doc_a1b2c3d4_v1.0_c00001` | Step 4 (Chunking) |

> [!IMPORTANT]
> **Không tự viết lại logic sinh ID:**
> Sử dụng trực tiếp hàm `make_block_id(version_id, block_idx)` trong `schema_parsing.py` để đảm bảo đồng nhất 5 chữ số đệm (`00001`).

### 2.2. Đường dẫn & Cấu trúc thư mục (`path.py`)
Tất cả các bước phải import đường dẫn từ `Document Ingestion and Parsing/path.py`:
* `DATA_DIR`: Thư mục chứa dữ liệu gốc.
* `INGESTION_OUT_DIR`: Lưu output Step 1 (`output/Ingestion/`).
* `PARSING_OUT_DIR`: Lưu output Step 2 (`output/Parsing/`).
* `NORMALIZATION_OUT_DIR`: Lưu output Step 3 (`output/Normalization/`).
* `CHUNKING_OUT_DIR`: Lưu output Step 4 (`output/Chunking/`).
* `FINAL_OUT_DIR`: Lưu output hoàn thiện / SQLite (`output/Final/`).

**Quy tắc đặt tên file output:** `{version_id}_{step_name}.json`  
*(Ví dụ: `doc_a1b2c3d4_v1.0_parsing.json`)*.

---

## 🛠️ 3. STEP 1: DOCUMENT INGESTION

### 3.1. Các Enum dùng chung (Tuyệt đối không hardcode chuỗi)
Import từ `Document Ingestion and Parsing/Ingestion/schema_ingestion.py`:

```python
from schema_ingestion import FileFormat, ProcessingStatus
```
* `FileFormat`: `PDF`, `MARKDOWN`, `CSV`, `JSON`, `TXT`, `UNKNOWN` *(viết HOA)*.
* `ProcessingStatus`: `pending`, `processing`, `success`, `failed` *(chữ thường)*.

### 3.2. So sánh Schema Step 1: Note vs Code

```
       NOTE (Bản phác thảo)                    CODE CHUẨN (schema_ingestion.py)
┌─────────────────────────────────┐        ┌───────────────────────────────────────┐
│ # Document                      │        │ @dataclass class DocumentSchema:      │
│   document_id                   │        │     document_id: str                  │
│   name                          │ ─────> │     canonical_title: str  # Chuẩn hoá │
│   created_at                    │        │     publisher: str                    │
│                                 │        │     created_at: str (ISO UTC)         │
│ # Document_version              │        │                                       │
│   version_id                    │        │ @dataclass class DocumentVersionSchema:│
│   document_id                   │        │     version_id: str                   │
│   version_number                │ ─────> │     document_id: str                  │
│   file_name                     │        │     version_tag: str      # ví dụ 1.0 │
│   file_path                     │        │     file_name: str                    │
│   file_format                   │        │     file_path: str        # posix rel │
│   file_size_bytes               │        │     file_format: str                  │
│   sha256_hash                   │        │     file_size_bytes: int              │
│   status                        │        │     sha256_hash: str | None           │
│   created_at                    │        │     status: str                       │
└─────────────────────────────────┘        │     current_stage: str    # INGESTION │
                                           │     is_duplicate: bool                │
                                           │     error_message: str | None         │
                                           │     updated_at: str                   │
                                           └───────────────────────────────────────┘
```

> **Tại sao dùng Code thay vì Note?**
> 1. Code bổ sung `is_duplicate` và `error_message`: Khi một file lỗi hoặc trùng lặp mã hash, pipeline không bị dừng đột ngột mà ghi nhận nguyên nhân rõ ràng.
> 2. `canonical_title` chuẩn hóa tiêu đề tài liệu thay vì chỉ lấy `name` đơn thuần.

---

## 🧱 4. STEP 2: DOCUMENT PARSING

Đây là phần có nhiều thay đổi lớn nhất so với Note nhằm đảm bảo tính ổn định và chuẩn bị cho cơ sở dữ liệu SQLite.

### 4.1. Bảng đối chiếu trường (Field Mapping)

| Trường trong Note | Trường chuẩn trong Code | Kiểu dữ liệu | Lý do thay thế / Tối ưu |
| :--- | :--- | :--- | :--- |
| `parser_type` | `parser_name` + `parser_version` | `str`, `str` | **Tách rõ phiên bản**: Tránh lỗi khi nâng cấp thư viện (VD: `pymupdf` 1.23 vs 1.24 trích xuất tọa độ khác nhau). |
| `block.source_locator` (chứa chung cả bbox, page, line, row) | `locator` (Đa hình theo `file_format`) | `PdfLocator` / `LineLocator` / `CsvLocator` / `JsonLocator` | **Tránh dư thừa trường rỗng (`null`)**: Markdown không thể có `bbox`, PDF không có `start_row`. Code tách riêng theo định dạng file. |
| `block.structured_content` (`header_cols`, `rows`) | `table_header_cols` + `table_rows` | `list[str]`, `list[list[str]]` | **Làm phẳng cấp lồng**: Đưa trực tiếp ra schema chính, dễ dàng truy xuất `block.table_rows`. |
| *(Không có trong note)* | `text_hash` | `str` (SHA256) | **Bảo toàn dữ liệu**: Đảm bảo `text_content` không bị sửa đổi ngoài ý muốn ở các bước sau. |
| *(Không có trong note)* | `continues_from_previous` | `bool` | **Xử lý bảng nhiều trang**: Bảng trong file PDF chạy qua nhiều trang được gắn cờ nối tiếp. |
| *(Không có trong note)* | `raw_record` | `dict \| None` | **Hỗ trợ JSON/CSV**: Lưu bản ghi cấu trúc gốc khi cần. |
| *(Không có trong note)* | `to_flat_dict()` | `method` | **Hỗ trợ bạn Diệp lưu SQLite**: Tự động chuyển đổi locator thành cột phẳng trong database chỉ với 1 lời gọi hàm. |

### 4.2. Cấu trúc Locator đa hình (Polymorphic Locators)

Trong code `schema_parsing.py`, mỗi loại tài liệu chỉ dùng đúng locator của nó:
* **PDF**: `PdfLocator(page_number, bbox=[x0, y0, x1, y1], is_ocr, ocr_confidence)` (gốc tọa độ trên-trái, đơn vị point chuẩn PyMuPDF).
* **Markdown / TXT**: `LineLocator(start_line, end_line)`.
* **CSV**: `CsvLocator(table_idx, start_row, end_row)`.
* **JSON**: `JsonLocator(json_path="$.vulnerabilities[0]")`.

### 4.3. Enum `BlockType` chuẩn
Import trực tiếp từ `schema_parsing.py`:
```python
from schema_parsing import BlockType
# Các giá trị: HEADING, PARAGRAPH, LIST, TABLE, CODE, FIGURE, CAPTION, FOOTNOTE, HEADER_FOOTER, RECORD
```

---

## 🚀 5. HƯỚNG DẪN TIẾP NỐI CHO CÁC BƯỚC SAU

Để toàn bộ pipeline không bị "gãy" khi ghép nối, các thành viên phụ trách các bước sau cần áp dụng các tiêu chuẩn sau:

### 🔹 5.1. Dành cho Step 3: Text Normalization (Trí phụ trách)
* **Quy tắc vàng về nội dung:** Không được ghi đè lên trường `text_content`. 
  * `text_content`: Giữ nguyên bản gốc (raw) từ Step 2.
  * Tạo thêm trường `normalized_text: str`: Chứa văn bản sau khi xóa khoảng trắng thừa, chuẩn hóa Unicode/ký tự xuống dòng.
* **Tái sử dụng trường:** Giữ nguyên `block_id`, `version_id`, `locator`, `heading_path` để duy trì truy vết.
* **Bổ sung các cờ (flags) như Note đề xuất:**
  * `normalization_flags: list[str]` (ví dụ: `["cleaned_whitespace", "removed_blank_lines"]`)
  * `is_code_or_payload: bool` (nếu là code mẫu/payload thì bỏ qua chuẩn hóa để tránh hỏng cú pháp pentest).
  * `char_count_delta: int` (chênh lệch số ký tự trước và sau chuẩn hóa).

### 🔹 5.2. Dành cho Step 4: Document Chunking (Kiệt phụ trách)
* **Quy ước `chunk_id`:** Bắt buộc tuân theo `{version_id}_c{chunk_idx:05d}`.
* **Thay đổi quan trọng so với Note:** 
  * Trong Note ghi `source_block_idx` (chỉ 1 số nguyên).
  * **Thực tế:** Một chunk (đoạn ngữ cảnh) thường gom từ **nhiều block** liên tiếp (ví dụ: 1 Heading + 2 Paragraph).
  * 👉 **Đổi thành:** `source_block_ids: list[str]` chứa danh sách các `block_id` cấu thành nên chunk đó.
* **Tọa độ / Locator kế thừa:** Gộp danh sách các `page_number` hoặc dòng từ các block nguồn vào `page_range` / `line_range`.

### 🔹 5.3. Dành cho Step 5: Metadata & SQLite Storage (Diệp phụ trách)
* **Không cần viết code bóc tách JSON lồng nhau:** 
  * Gọi hàm `block.to_flat_dict()` từ `ContentBlockSchema`.
  * Hàm này đã làm phẳng toàn bộ `page_number`, `bbox`, `start_line`, `end_line`, `start_row`,... thành các key ngang hàng, sẵn sàng `INSERT INTO blocks_table`.
* **Bổ sung Metadata An toàn thông tin:**
  * Thêm các trường metadata bảo mật theo định hướng của Note: `security_entities`, `owasp_category`, `cwe_ids`, `severity`.

---

## 📋 6. Bảng Checklist Tự Kiểm Tra (Self-Checklist)

Mỗi khi thành viên viết code cho bước của mình, hãy kiểm tra danh sách này trước khi commit:

- [ ] Đã import `FileFormat`, `BlockType` từ các schema dùng chung thay vì tự gõ chuỗi `"PDF"`, `"paragraph"`.
- [ ] Tên file output lưu đúng theo định dạng `{version_id}_{step_name}.json`.
- [ ] Thư mục output được lấy từ các hằng số trong `path.py`.
- [ ] Không làm mất liên kết ID (`document_id` -> `version_id` -> `block_id` -> `chunk_id`).
- [ ] Đã gọi hàm kiểm định nếu có (như `validate_block(block)`).

---
*Tài liệu này là căn cứ kỹ thuật chính thức cho toàn bộ pipeline của nhóm.*
