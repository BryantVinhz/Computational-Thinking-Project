import json
import sqlite3

# Paths relative to project root
DB_PATH = "long_nguyen/data/database/workspace.db"
OUTPUT_JSON = "long_nguyen/data/database/workspace.json"

def export_db_to_json(db_path: str, output_path: str):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row  # Access columns by name
    cursor = conn.cursor()

    # Discover user tables (ignoring internal sqlite metadata tables)
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
    tables = [row[0] for row in cursor.fetchall()]

    database_data = {}

    for table in tables:
        cursor.execute(f"SELECT * FROM {table}")
        rows = [dict(row) for row in cursor.fetchall()]
        database_data[table] = rows
        print(f"[+] Exported table '{table}': {len(rows)} rows")

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(database_data, f, indent=2, ensure_ascii=False)

    print(f"\n[SUCCESS] Successfully saved full database dump to: {output_path}")
    conn.close()

if __name__ == "__main__":
    export_db_to_json(DB_PATH, OUTPUT_JSON)
