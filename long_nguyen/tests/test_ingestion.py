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


def test_ingestion_service_json_manifests(tmp_path):
    import json
    from src.pipeline.ingestion import IngestionService

    raw_dir = tmp_path / "raw_data"
    raw_dir.mkdir()
    manifest_dir = tmp_path / "manifest_out"
    manifest_dir.mkdir()

    # 1. Valid file with canonical naming convention: Publisher_Topic_Version.ext
    f1 = raw_dir / "OWASP_Top10-A01-Broken-Access-Control_2025.md"
    f1.write_text("# Access Control\nRules for access control.", encoding="utf-8")

    # 2. Empty file (should be rejected)
    f2 = raw_dir / "OWASP_test_v1.0.csv"
    f2.write_text("", encoding="utf-8")

    # Run IngestionService
    service = IngestionService(raw_dir=raw_dir, manifest_dir=manifest_dir, project_root=tmp_path)
    stats = service.run()

    assert stats["total_scanned"] == 2
    assert stats["new_ingested"] == 1
    assert stats["failed"] == 1

    # Check documents.json
    docs_file = manifest_dir / "documents.json"
    assert docs_file.exists()
    docs_data = json.loads(docs_file.read_text(encoding="utf-8"))
    assert len(docs_data) == 1
    doc = docs_data[0]
    assert doc["document_id"] == "doc_owasp_top10_a01_broken_access_control"
    assert doc["publisher"] == "OWASP"
    assert "canonical_title" in doc
    assert "created_at" in doc

    # Check ducuments_version.json
    versions_file = manifest_dir / "ducuments_version.json"
    assert versions_file.exists()
    versions_data = json.loads(versions_file.read_text(encoding="utf-8"))
    assert len(versions_data) == 1
    ver = versions_data[0]
    assert ver["document_id"] == "doc_owasp_top10_a01_broken_access_control"
    assert ver["version_tag"] == "2025"
    assert ver["file_format"] == "MARKDOWN"
    assert ver["status"] == "pending"
    assert ver["current_stage"] == "INGESTED"
    assert ver["is_duplicate"] is False
    assert len(ver["sha256_hash"]) == 64

    # Check ingestion_rejected.json
    rejected_file = manifest_dir / "ingestion_rejected.json"
    assert rejected_file.exists()
    rejected_data = json.loads(rejected_file.read_text(encoding="utf-8"))
    assert len(rejected_data) == 1
    rej = rejected_data[0]
    assert rej["document_id"] == "doc_owasp_test"
    assert rej["status"] == "failed"
    assert rej["current_stage"] == "INGESTION"
    assert rej["sha256_hash"] is None
    assert "0 bytes" in rej["error_message"]

