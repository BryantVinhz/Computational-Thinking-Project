"""
tests/test_ingestion.py
Unit tests for Stage 1 Data Ingestion Pipeline.
"""

import sys
import os
from pathlib import Path

# Add project root to sys.path so tests can resolve `src`
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pytest
import sqlite3
from src.pipeline.ingestion import DataIngestionPipeline

@pytest.fixture
def test_pipeline(tmp_path):
    db_file = tmp_path / "test_workspace.db"
    pipeline = DataIngestionPipeline(str(db_file))
    return pipeline

@pytest.fixture
def sample_files(tmp_path):
    data_dir = tmp_path / "raw"
    data_dir.mkdir()
    
    # 1. Normal valid markdown file
    valid_md = data_dir / "valid_cheat_sheet.md"
    valid_md.write_text("# SQL Injection\nPrevent SQLi with prepared statements.", encoding="utf-8")
    
    # 2. Normal valid json file
    valid_json = data_dir / "taxonomy.json"
    valid_json.write_text('{"category": "A01", "name": "Access Control"}', encoding="utf-8")
    
    # 3. Empty file (0 bytes)
    empty_file = data_dir / "empty.txt"
    empty_file.write_text("", encoding="utf-8")
    
    return data_dir

def test_file_discovery(test_pipeline, sample_files):
    files = test_pipeline.discover_files(sample_files)
    file_names = [f.name for f in files]
    assert "valid_cheat_sheet.md" in file_names
    assert "taxonomy.json" in file_names
    assert "empty.txt" in file_names

def test_valid_ingestion(test_pipeline, sample_files):
    md_file = sample_files / "valid_cheat_sheet.md"
    result = test_pipeline.ingest_file(md_file, domain_category="remediation")
    
    assert result.status == "pending"
    assert result.is_duplicate is False
    assert len(result.sha256_hash) == 64

    # Verify SQLite row
    with sqlite3.connect(test_pipeline.db_path) as conn:
        conn.row_factory = sqlite3.Row
        doc = conn.execute("SELECT * FROM documents WHERE document_id = ?", (result.document_id,)).fetchone()
        assert doc is not None
        assert doc["domain_category"] == "remediation"

def test_duplicate_detection(test_pipeline, sample_files):
    md_file = sample_files / "valid_cheat_sheet.md"
    res1 = test_pipeline.ingest_file(md_file)
    test_pipeline.update_status(res1.version_id, "success")
    
    # Ingest the exact same file again
    res2 = test_pipeline.ingest_file(md_file)
    assert res2.is_duplicate is True
    assert res2.version_id == res1.version_id

def test_empty_file_rejection(test_pipeline, sample_files):
    empty_file = sample_files / "empty.txt"
    result = test_pipeline.ingest_file(empty_file)
    
    assert result.status == "failed"
    assert "0 bytes" in result.error_message

def test_status_lifecycle_updates(test_pipeline, sample_files):
    md_file = sample_files / "valid_cheat_sheet.md"
    result = test_pipeline.ingest_file(md_file)
    
    test_pipeline.update_status(result.version_id, "processing")
    with sqlite3.connect(test_pipeline.db_path) as conn:
        status = conn.execute("SELECT status FROM document_versions WHERE version_id = ?", (result.version_id,)).fetchone()[0]
        assert status == "processing"

    test_pipeline.update_status(result.version_id, "success")
    with sqlite3.connect(test_pipeline.db_path) as conn:
        status = conn.execute("SELECT status FROM document_versions WHERE version_id = ?", (result.version_id,)).fetchone()[0]
        assert status == "success"
