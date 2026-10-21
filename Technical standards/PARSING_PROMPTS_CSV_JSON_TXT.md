# 🤖 AI Prompts — Step 2 Parsing: CsvParser, JsonParser, TxtParser

*(Bảo soạn cho phần Document Parsing — Nguyên & Bảo, Note trang 3–4)*

**Cách dùng:** mỗi lần gen 1 parser, copy **[PHẦN CHUNG]** + **đúng 1 prompt riêng** (CSV / JSON / TXT) dán vào AI. Đính kèm thêm 3 file: `schema_parsing.py`, `schema_ingestion.py`, `path.py` (và `PIPELINE_TECHNICAL_STANDARDS.md` nếu AI nhận được file dài).

---

## [PHẦN CHUNG] — dán đầu mọi prompt

```text
Bạn là Python engineer viết parser cho Step 2 (Document Parsing) của pipeline xử lý tài liệu bảo mật
(OWASP, CVSS...). Code phải chạy được thật, dùng thư viện chuẩn Python 3.12, không thêm dependency.

## Cấu trúc repo (đã có, KHÔNG sửa các file này)
Computational-Thinking-Project/
├── Data/                                   # dữ liệu gốc
└── Document Ingestion and Parsing/         # = PIPELINE_DIR
    ├── path.py                             # DATA_DIR, INGESTION_OUT_DIR, PARSING_OUT_DIR, to_actual_path()
    ├── Ingestion/schema_ingestion.py       # FileFormat, ProcessingStatus, DocumentVersionSchema
    ├── Parsing/schema_parsing.py           # BlockType, *Locator, ContentBlockSchema,
    │                                       # make_block_id, make_text_hash, validate_block
    └── output/Ingestion/ducuments_version.json   # input của Step 2 (tên file có typo "ducuments", giữ nguyên)

## Bắt buộc tuân thủ
1. Import: schema_parsing.py có dòng `from Ingestion.schema_ingestion import FileFormat`, nên PIPELINE_DIR phải
   nằm trong sys.path. Đặt đầu file parser đoạn bootstrap giống Ingestion/Ingestion.py:
       CURRENT_DIR = Path(__file__).resolve().parent      # .../Parsing
       PARENT_DIR = CURRENT_DIR.parent                     # .../Document Ingestion and Parsing
       for p in (CURRENT_DIR, PARENT_DIR):
           if str(p) not in sys.path:
               sys.path.insert(0, str(p))
   rồi: from path import PARSING_OUT_DIR, to_actual_path
        from Ingestion.schema_ingestion import DocumentVersionSchema, FileFormat
        from schema_parsing import (BlockType, ContentBlockSchema, make_block_id, make_text_hash, validate_block, ...)
2. Không hardcode chuỗi: dùng FileFormat.X.value, BlockType.X.value (các field trong schema là str).
3. ID: document_id, version_id lấy NGUYÊN từ DocumentVersionSchema (dữ liệu thật đang có dạng
   "ver_04040e0917f5", "doc_first_cvss" — KHÔNG tự sinh lại). block_idx đếm từ 0 theo thứ tự xuất hiện,
   block_id = make_block_id(version_id, block_idx). text_hash = make_text_hash(text_content).
4. text_content là bản GỐC: không strip giữa dòng, không gộp khoảng trắng, không chuẩn hóa Unicode
   (Step 3 làm việc đó). text_content không được rỗng/chỉ khoảng trắng — block như vậy thì bỏ qua.
5. Mọi block tạo ra phải qua validate_block(block); nếu trả về lỗi → raise ValueError kèm block_id + lỗi
   (đây là lỗi của parser, không được nuốt).
6. Số dòng/số row đều 1-based, end bao gồm.
7. Đọc file: resolve đường dẫn bằng to_actual_path(version.file_path). Encoding "utf-8-sig" (bỏ BOM);
   nếu UnicodeDecodeError → raise ValueError("Không decode được UTF-8: ...").

## Interface chung (3 parser giống nhau để service gọi thống nhất)
File: Document Ingestion and Parsing/Parsing/<ten>_parser.py

    PARSER_NAME = "..."
    PARSER_VERSION = "..."

    class XxxParser:
        file_format = FileFormat.XXX.value

        def parse(self, version: DocumentVersionSchema) -> list[ContentBlockSchema]:
            """Trả về list block đã validate. Raise ValueError với message rõ ràng (tiếng Việt)
            khi file không parse được — service sẽ ghi vào version.error_message, status = failed."""

- Parser KHÔNG ghi file, KHÔNG sửa status của version (việc của service điều phối).
- Thêm khối `if __name__ == "__main__":` để test tay: đọc INGESTION_OUT_DIR / "ducuments_version.json",
  lọc version có file_format đúng định dạng, status != "failed", is_duplicate == False; parse từng cái;
  ghi PARSING_OUT_DIR / f"{version_id}_parsing.json" = json.dump([b.to_dict() for b in blocks],
  ensure_ascii=False, indent=2); tạo thư mục bằng mkdir(parents=True, exist_ok=True); in số block / lỗi.
- Style: giống code hiện có (class + type hints, docstring/comment tiếng Việt ngắn gọn), không over-engineer.

## Test
Viết pytest ở Document Ingestion and Parsing/tests/test_<ten>_parser.py, dùng tmp_path tạo file mẫu
(KHÔNG thêm file test vào Data/ vì Step 1 sẽ quét nhầm). Tạo DocumentVersionSchema giả với file_path tuyệt đối
(to_actual_path trả nguyên path tuyệt đối). Mỗi test assert validate_block(b) == [] cho mọi block.

## Output trả về cho tôi
1. Code đầy đủ file parser. 2. File test. 3. Giải thích ngắn các quyết định với case biên.
```

---

## [PROMPT 1] — CsvParser

```text
## Nhiệm vụ: viết Parsing/csv_parser.py — class CsvParser
PARSER_NAME = "python-csv", PARSER_VERSION = platform.python_version()

## Ràng buộc schema cho CSV
- file_format = FileFormat.CSV.value; block_type hợp lệ: TABLE, RECORD. Ta dùng RECORD (1 dòng dữ liệu = 1 block)
  để mỗi kết quả search trích dẫn được đúng dòng nguồn. Không sinh thêm block TABLE trùng nội dung.
- locator = CsvLocator(table_idx=0, start_row=n, end_row=n); n là thứ tự dòng DỮ LIỆU, 1-based,
  KHÔNG tính header (dòng ngay sau header là row 1).

## Logic
1. Mở file với newline="" + encoding "utf-8-sig", dùng module csv (để xử lý đúng ô có dấu phẩy,
   dấu ngoặc kép, xuống dòng bên trong ô).
2. Đoán delimiter bằng csv.Sniffer().sniff(sample 64KB, delimiters=",;\t|"); lỗi thì fallback ",".
3. Dòng đầu tiên không rỗng = header. Chuẩn hóa tên cột: strip; tên rỗng → "col_{i}" (i 1-based);
   tên trùng → thêm hậu tố "_2", "_3"...
4. Với mỗi dòng dữ liệu (đếm n tăng dần cho MỌI dòng sau header, kể cả dòng bị bỏ qua, để số row khớp file):
   - Dòng mà mọi ô đều rỗng/khoảng trắng → bỏ qua (không tạo block).
   - Dòng ít cột hơn header → pad "" cho đủ. Dòng nhiều cột hơn → phần dư vào raw_record["_extra"] (list).
   - raw_record = dict {tên_cột: giá_trị_gốc} theo thứ tự cột.
   - text_content = các dòng "tên_cột: giá_trị" nối bằng "\n", BỎ các ô rỗng; giá trị giữ nguyên gốc.
5. File không có header hoặc không có dòng dữ liệu nào → raise ValueError("CSV không có dòng dữ liệu").
6. csv.Error (ví dụ ngoặc kép không đóng) → raise ValueError kèm số dòng (reader.line_num).

## Test cần có
- CSV thường 3 dòng → 3 RECORD, start_row = 1,2,3, raw_record đúng.
- Ô chứa dấu phẩy + xuống dòng trong ngoặc kép → vẫn 1 record.
- Delimiter ";" được nhận ra.
- Có BOM ở đầu → tên cột đầu không dính "﻿".
- Dòng rỗng ở giữa → bị bỏ qua nhưng row của dòng sau vẫn đúng vị trí thật.
- Header trùng/rỗng → được đổi tên. Dòng thiếu/thừa cột.
- File chỉ có header → ValueError.
(Lưu ý: Data/OWASP_test_v1.0.csv hiện 0 byte và đã bị Step 1 reject, nên test hoàn toàn bằng tmp_path.)
```

---

## [PROMPT 2] — JsonParser

```text
## Nhiệm vụ: viết Parsing/json_parser.py — class JsonParser
PARSER_NAME = "python-json", PARSER_VERSION = platform.python_version()

## Ràng buộc schema cho JSON
- file_format = FileFormat.JSON.value; block_type hợp lệ DUY NHẤT: RECORD.
- locator = JsonLocator(json_path=...), json_path bắt buộc bắt đầu bằng "$".
- raw_record: dict | None → luôn là dict (nếu phần tử không phải dict thì bọc {"value": x}).

## Dữ liệu thật để tham khảo: Data/First_CVSS_v4.0.json (JSON Schema của CVSS 4.0)
Root là object gồm: "$schema", "$id", "title", "type" (scalar); "license" (list 19 chuỗi);
"$defs" (object 33 định nghĩa, mỗi cái là object có type/enum/default...); "required" (list chuỗi);
"properties" (object 34 trường, mỗi cái {"$ref": "#/$defs/..."}); "allOf" (list 1 object); "unevaluatedProperties".

## Quy tắc chia record (generic, không hardcode tên key của CVSS)
Gọi một giá trị là "collection" nếu nó là list hoặc dict khác rỗng và TẤT CẢ phần tử con đều là dict/list.
1. Root là list → mỗi phần tử là 1 record tại "$[i]".
2. Root là dict → duyệt key theo đúng thứ tự trong file:
   - value là collection → mỗi phần tử con là 1 record: "$.key[i]" (list) hoặc "$.key.child" (dict).
   - value còn lại (scalar, list scalar, dict nhỏ chứa scalar) → gom chung vào MỘT record "metadata" tại "$"
     (raw_record chỉ chứa các key được gom). Record này đứng ở vị trí block_idx 0 nếu có.
   → Với CVSS: 1 record "$" (schema/id/title/type/license/required/unevaluatedProperties),
     33 record "$['$defs'].xxx", 34 record "$.properties.xxx", 1 record "$.allOf[0]".
3. Root là scalar → 1 record "$" với raw_record {"value": x}.
4. KHÔNG resolve "$ref" (giữ nguyên chuỗi), không đổi kiểu dữ liệu trong raw_record.

## json_path
- Key là identifier (regex ^[A-Za-z_][A-Za-z0-9_]*$) → ".key"; ngược lại (có "$", "-", khoảng trắng, chữ số đầu...)
  → "['key']" với escape \ và '. Ví dụ: $['$defs'].attackVectorType, $.properties.vectorString, $.allOf[0].
- Viết hàm riêng build_json_path(parent: str, key: str | int) -> str và test nó.

## text_content
- Làm phẳng record thành các dòng "đường.dẫn.tương.đối: giá trị" nối "\n", theo thứ tự key gốc.
  Dòng đầu là tên record (key con hoặc "[i]") để text có ngữ cảnh khi search, ví dụ:
      attackVectorType
      type: string
      enum: N, A, L, P
- list toàn scalar → nối ", " trên 1 dòng; list chứa object → đánh index "anyOf[0].required: ...".
- Giá trị: str giữ nguyên; số/bool/null → dạng JSON (true/false/null), không phải True/None của Python.
- Record làm phẳng ra rỗng (ví dụ {} hoặc []) → bỏ qua, không tạo block.

## Lỗi
- json.JSONDecodeError → raise ValueError(f"JSON không hợp lệ: dòng {e.lineno} cột {e.colno}: {e.msg}").
- Không tạo được record nào → ValueError.

## Test cần có
- Chạy trên Data/First_CVSS_v4.0.json thật: đúng 1 + 33 + 34 + 1 = 69 block (in ra để đối chiếu nếu khác,
  giải thích vì sao), mọi block validate pass, json_path của $defs dùng bracket.
- Root là list object; root là scalar; key có dấu "'" / khoảng trắng; record rỗng bị bỏ; JSON hỏng → ValueError
  có số dòng; ký tự tiếng Việt giữ nguyên trong output (ensure_ascii=False).
```

---

## [PROMPT 3] — TxtParser

```text
## Nhiệm vụ: viết Parsing/txt_parser.py — class TxtParser
PARSER_NAME = "txt-rule-parser", PARSER_VERSION = "1.0.0" (parser tự viết, tăng version khi đổi luật tách block)

## Ràng buộc schema cho TXT
- file_format = FileFormat.TXT.value; block_type hợp lệ: PARAGRAPH, LIST, CODE (KHÔNG có HEADING —
  dòng IN HOA hay dòng ngắn kiểu tiêu đề vẫn là PARAGRAPH; validate_block sẽ báo lỗi nếu dùng heading).
- locator = LineLocator(start_line, end_line), 1-based, end bao gồm, ứng đúng dòng trong file gốc.
- code_language chỉ set cho CODE (lấy từ info string của fence, ví dụ ```python → "python"; không có → None).

## Logic tách block (đọc text bằng read_text("utf-8-sig") rồi .splitlines() — xử lý được \n, \r\n)
1. Fence code: dòng bắt đầu bằng ``` hoặc ~~~ (sau khi lstrip) mở CODE block cho tới dòng fence đóng tương ứng
   (bao gồm cả dòng trống bên trong và cả 2 dòng fence). Fence không đóng → CODE kéo tới hết file.
   Xử lý fence TRƯỚC mọi luật khác.
2. Ngoài fence: tách nhóm theo dòng trống (dòng chỉ có khoảng trắng). Mỗi nhóm phân loại:
   - CODE nếu MỌI dòng thụt >= 4 space hoặc bắt đầu bằng tab (payload/command mẫu trong tài liệu bảo mật).
   - Trong nhóm, dòng bullet khớp ^\s*([-*•+]|\d+[.)]|[a-zA-Z][.)])\s+ ; dòng thụt lề ngay sau bullet là
     dòng tiếp nối của item đó. Một chuỗi liên tiếp bullet(+tiếp nối) = LIST; phần còn lại = PARAGRAPH.
     → Nhóm "Mô tả:\n- a\n- b" tách thành PARAGRAPH (dòng 1) + LIST (dòng 2–3), mỗi block có line range riêng.
3. text_content = các dòng gốc của block nối "\n" (không strip, không gộp khoảng trắng, không bỏ ký hiệu bullet).
4. File rỗng/toàn khoảng trắng → raise ValueError("TXT không có nội dung").
5. Không cắt nhỏ đoạn dài (Step 4 Chunking lo việc đó).

## Test cần có
- 2 đoạn văn cách 1 dòng trống → 2 PARAGRAPH, line range đúng (đoạn 2 bắt đầu sau dòng trống).
- Nhiều dòng trống liên tiếp, dòng trống ở đầu/cuối file.
- Bullet "-", "*", "•", "1.", "2)", "a)" → LIST; item có dòng tiếp nối thụt lề.
- Paragraph + list trong cùng nhóm → tách 2 block.
- Fence ```bash chứa dòng trống bên trong → 1 CODE, code_language="bash"; fence không đóng.
- Khối thụt 4 space (ví dụ payload SQLi "    ' OR 1=1 --") → CODE, text giữ nguyên thụt lề.
- File CRLF và file có BOM → line number vẫn đúng.
- File rỗng → ValueError.
(Lưu ý: Data/ hiện chưa có file .txt nào, test hoàn toàn bằng tmp_path.)
```

---

## ✅ Checklist sau khi AI trả code (tự kiểm trước khi commit)

- [ ] `python "Document Ingestion and Parsing/Parsing/json_parser.py"` chạy từ root repo được, sinh `output/Parsing/ver_04040e0917f5_parsing.json`.
- [ ] Mọi block `validate_block(b) == []`; `pytest "Document Ingestion and Parsing/tests"` pass.
- [ ] Không file nào trong `schema_*.py` / `path.py` bị AI sửa.
- [ ] Không hardcode `"CSV"`, `"record"`... — dùng Enum.
- [ ] Interface `parse(version) -> list[ContentBlockSchema]` khớp với parser PDF/Markdown của Nguyên (nếu Nguyên dùng tên khác thì thống nhất lại 1 kiểu trước khi merge).
