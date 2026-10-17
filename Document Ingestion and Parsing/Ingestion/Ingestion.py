import sys
from pathlib import Path

# Đảm bảo import được path.py ở thư mục cha khi bấm nút Run trực tiếp trên VS Code
CURRENT_DIR = Path(__file__).resolve().parent
PARENT_DIR = CURRENT_DIR.parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))
if str(PARENT_DIR) not in sys.path:
    sys.path.insert(0, str(PARENT_DIR))

from path import DATA_DIR, INGESTION_OUT_DIR
from service import IngestionService

if __name__ == "__main__":
    ingestion_service = IngestionService(
        raw_dir=DATA_DIR,
        manifest_dir=INGESTION_OUT_DIR,
    )
    result = ingestion_service.run()

    print("=== KẾT QUẢ CHẠY BƯỚC 1: DOCUMENT INGESTION ===")
    print(f"- Thư mục Data quét          : {DATA_DIR}")
    print(f"- Tổng số file quét được     : {result['total_scanned']}")
    print(f"- File mới nạp (New)         : {result['new_ingested']}")
    print(f"- File đã có, bỏ qua (Skip)  : {result['skipped_unchanged']}")
    print(f"- File trùng nội dung (Dup)  : {result['duplicates']}")
    print(f"- File lỗi (Failed)          : {result['failed']}")
    print(f"=> Đã lưu Output tại         : {INGESTION_OUT_DIR}")