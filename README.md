# Computational-Thinking-Project
Intelligent Document Workspace: Search, compare, and synthesize PDFs, notes, reports, and structured records with traceable evidence

**New Approach Reference: Retriever-Free Retrieval: Navigating Documents with Decision Models Paper**: `[https://arxiv.org/abs/2610.35868]`

```
Computational-Thinking-Project/
│
├── 📄 main.py                          # Điểm chạy chính (Interactive CLI kết nối toàn bộ luồng RAG)
├── 📄 llm.py                           # Client kết nối FastAPI model (ColabLLMClient)
├── 📄 Evaluation.json                  # Bộ dữ liệu test cases đánh giá pipeline
├── 📄 FastAPI_Model.ipynb              # Notebook triển khai model trên Google Colab
├── 📄 pyproject.toml                   # Cấu hình môi trường và dependencies
├── 📄 README.md                        # Tài liệu tổng quan dự án
│
├── 📁 Data/                            # Kho tài liệu gốc (OWASP Top 10, ASVS, Cheat Sheets, CWE, CVSS)
│   ├── First_CVSS-Specification_v4.0.pdf
│   ├── First_CVSS_v4.0.json
│   ├── MITRE_CWE-List.json
│   ├── OWASP_Top10-*.md
│   ├── OWASP_AVSS-*.md
│   └── OWASP_A0*-Cheat-Sheet_*.md
│
├── 📁 Data extra/                      # Dữ liệu bổ sung, benchmark, và file trung gian
│
├── 📁 Document Ingestion and Parsing/  # [STAGE 1] Xử lý, chuẩn hóa, cắt đoạn và lưu trữ tài liệu
│   ├── path.py                         # Định nghĩa đường dẫn I/O các bước
│   ├── Ingestion/                      # Thu nạp file, kiểm tra hash, định danh tài liệu
│   │   ├── Ingestion.py
│   │   └── service.py
│   ├── Parsing/                        # Bóc tách định dạng (Markdown, PDF, JSON, CSV, TXT)
│   │   ├── markdown_parser.py
│   │   ├── pdf_parser.py
│   │   └── json_parser.py
│   ├── Normalization/                  # Chuẩn hóa văn bản, xử lý khoảng trắng, Unicode
│   │   ├── Normalization.py
│   │   └── text_cleaner.py
│   ├── Chunking/                       # Cắt đoạn theo cấu trúc (Structure-aware chunking)
│   │   ├── chunking.py
│   │   ├── structure_aware.py
│   │   └── token_counter.py
│   ├── Metadata/                       # Tạo metadata 9 trường và lưu trữ Vector DB
│   │   ├── extractor_sqlite.py
│   │   └── vector_store.py             # ChromaDB vector store (BAAI/bge-m3)
│   ├── Provenance and Traceability/    # Lưu trữ phả hệ dữ liệu vào SQLite (workspace_data.db)
│   │   ├── database.py
│   │   ├── provenance_repo.py
│   │   └── provenance_resolver.py
│   └── output/                         # Thư mục lưu kết quả xử lý của các giai đoạn
│
├── 📁 Query Understanding and Routing/ # [STAGE 2] Hiểu truy vấn, phân loại ý định và định tuyến
│   ├── manual_test.py                  # Script test tay cho Stage 2
│   └── query_understanding_routing/
│       ├── ontology.yaml               # Từ điển tri thức lỗ hổng (canonical, aliases, cwe)
│       ├── ontology_loader.py          # Nạp từ điển ontology
│       ├── validation_module.py        # Kiểm duyệt an toàn (Safety check, Prompt Injection)
│       ├── normalization_module.py     # Chuẩn hóa câu hỏi, ánh xạ alias về canonical, detect_language
│       ├── understanding_module.py     # Phân loại Intent, bóc tách thực thể, resolve coreference
│       ├── routing_module.py           # Sinh Query Plan (tạo danh sách task, filters)
│       ├── pipeline.py                 # Pipeline điều phối Stage 2 (process_query)
│       ├── prompt_templates.py         # Mẫu prompt phân tích intent cho LLM
│       └── tests/                      # Bộ test tự động (Pytest) cho Stage 2
│
├── 📁 Retrieval/                       # [STAGE 3] Truy xuất dữ liệu đa phương thức (Hybrid Retrieval)
│   ├── scope_builder.py                # Xây dựng phạm vi tìm kiếm (metadata filter, semantic scoping)
│   ├── query_vectorizer.py             # Nhúng vector cho query (BAAI/bge-m3)
│   ├── bm25_retrieval.py               # Sparse Retrieval (BM25Okapi)
│   ├── dense_retrieval.py              # Dense Retrieval (ChromaDB HNSW Cosine)
│   ├── rrf.py                          # Kết hợp thứ hạng (Reciprocal Rank Fusion - k=60)
│   ├── reranker.py                     # Cross-Encoder Reranker & Lọc ngưỡng tin cậy tau (Abstention)
│   ├── MMR.py                          # Tối ưu độ đa dạng và khử trùng lặp (Maximal Marginal Relevance)
│   ├── evidence_package.py             # Đóng gói bằng chứng, đánh số tag [1], [2] và sinh LLM Context
│   ├── ontology_utils.py               # Tiện ích nạp aliases/CWE từ ontology sang BM25
│   └── retrieval_system.py             # Facade Service điều phối toàn bộ pipeline Retrieval
│
└── 📁 Response Synthesis/              # [STAGE 4] Sinh câu trả lời, kiểm định độ trung thực và trích dẫn
    ├── prompt_templates.py             # Mẫu prompt sinh câu trả lời theo Intent (SEARCH, COMPARE, SYNTHESIS)
    ├── synthesizer.py                  # Bộ sinh câu trả lời gọi LLM (STRICT RAG, HYBRID, ABSTENTION)
    ├── verifier.py                     # Bộ kiểm định độ trung thực (EvidenceVerifier, Faithfulness Score)
    ├── traceable_formatter.py          # Định dạng danh mục tài liệu tham chiếu (Footnotes & Citations)
    ├── schema_response.py              # Data classes và Envelope đầu ra chuẩn hóa (Contract v1.0)
    └── rag_orchestrator.py             # Bộ điều phối trung tâm End-to-End (Stage 2 -> Stage 3 -> Stage 4)