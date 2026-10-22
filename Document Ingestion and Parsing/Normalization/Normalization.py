import sys
from pathlib import Path

# Đảm bảo import được path.py ở thư mục cha khi bấm nút Run trực tiếp trên VS Code
CURRENT_DIR = Path(__file__).resolve().parent
PARENT_DIR = CURRENT_DIR.parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))
if str(PARENT_DIR) not in sys.path:
    sys.path.insert(0, str(PARENT_DIR))

from path import NORMALIZATION_IN_DIR, NORMALIZATION_OUT_DIR
from text_cleaner import TextNormalizer, LOCAL_INPUT_DIR, LOCAL_OUTPUT_DIR

if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    input_dir = NORMALIZATION_IN_DIR
    output_dir = NORMALIZATION_OUT_DIR

    # Nếu thư mục Parsing chưa có file, fallback sang thư mục input nội bộ của Normalization
    if not (input_dir.exists() and any(input_dir.glob("*.json"))) and LOCAL_INPUT_DIR.exists():
        input_dir = LOCAL_INPUT_DIR
        output_dir = LOCAL_OUTPUT_DIR

    normalizer = TextNormalizer()
    results = normalizer.normalize_directory(input_dir, output_dir)

    print("=== KẾT QUẢ CHẠY BƯỚC 3: TEXT NORMALIZATION ===")
    print(f"- Thư mục Input quét         : {input_dir}")
    print(f"- Tổng số file xử lý         : {len(results)}")
    total_blocks = sum(r.get("total", 0) for r in results)
    total_protected = sum(r.get("protected", 0) for r in results)
    total_modified = sum(r.get("modified", 0) for r in results)
    total_delta = sum(r.get("delta", 0) for r in results)
    print(f"- Tổng số block              : {total_blocks}")
    print(f"- Block được bảo vệ (Code)   : {total_protected}")
    print(f"- Block có thay đổi          : {total_modified}")
    print(f"- Độ lệch ký tự (Delta)      : {total_delta}")
    print(f"=> Đã lưu Output tại         : {output_dir}")
