import sys
from pathlib import Path

# Ensure path.py in the parent directory can be imported when running directly in VS Code
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

    print("=== STEP 1 EXECUTION RESULTS: DOCUMENT INGESTION ===")
    print(f"- Scanned Data Directory       : {DATA_DIR}")
    print(f"- Total Files Scanned          : {result['total_scanned']}")
    print(f"- Newly Ingested Files (New)   : {result['new_ingested']}")
    print(f"- Unchanged Skipped (Skip)     : {result['skipped_unchanged']}")
    print(f"- Duplicate Content (Dup)      : {result['duplicates']}")
    print(f"- Failed Files (Failed)        : {result['failed']}")
    print(f"=> Output Saved At             : {INGESTION_OUT_DIR}")
