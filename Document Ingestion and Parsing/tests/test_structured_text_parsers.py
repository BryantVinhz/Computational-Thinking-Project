"""
Test CsvParser / JsonParser / TxtParser. Chạy từ root repo:
    python -m unittest discover -s "Document Ingestion and Parsing/tests" -v
"""
import sys
import tempfile
import unittest
from pathlib import Path

PARSING_DIR = Path(__file__).resolve().parent.parent / "Parsing"
if str(PARSING_DIR) not in sys.path:
    sys.path.insert(0, str(PARSING_DIR))

from parser_utils import PARENT_DIR  # noqa: F401  (bootstrap sys.path)
from path import DATA_DIR
from Ingestion.schema_ingestion import DocumentVersionSchema
from schema_parsing import validate_block
from csv_parser import CsvParser
from json_parser import JsonParser, build_json_path
from txt_parser import TxtParser


def make_version(file_path: Path, file_format: str) -> DocumentVersionSchema:
    return DocumentVersionSchema(
        version_id="ver_test",
        document_id="doc_test",
        version_tag="v1.0",
        file_name=file_path.name,
        file_path=str(file_path),  # đường dẫn tuyệt đối -> to_actual_path giữ nguyên
        file_format=file_format,
        file_size_bytes=file_path.stat().st_size,
        sha256_hash=None,
        status="pending",
    )


class ParserTestCase(unittest.TestCase):
    parser = None
    file_format = ""
    suffix = ""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def parse_text(self, content: str, encoding: str = "utf-8", newline: str = "\n"):
        path = Path(self._tmp.name) / f"sample{self.suffix}"
        with open(path, "w", encoding=encoding, newline=newline) as f:
            f.write(content)
        return self.parse_path(path)

    def parse_path(self, path: Path):
        blocks = self.parser.parse(make_version(path, self.file_format))
        for b in blocks:
            self.assertEqual(validate_block(b), [], b.block_id)
        self.assertEqual([b.block_idx for b in blocks], list(range(len(blocks))))
        return blocks


class TestCsvParser(ParserTestCase):
    parser = CsvParser()
    file_format = "CSV"
    suffix = ".csv"

    def test_basic_rows(self):
        blocks = self.parse_text("id,name\n1,SQLi\n2,XSS\n3,CSRF\n")
        self.assertEqual(len(blocks), 3)
        self.assertEqual([b.locator.start_row for b in blocks], [1, 2, 3])
        self.assertEqual(blocks[1].raw_record, {"id": "2", "name": "XSS"})
        self.assertEqual(blocks[0].text_content, "id: 1\nname: SQLi")
        self.assertEqual(blocks[0].block_id, "ver_test_b00000")

    def test_quoted_comma_and_newline(self):
        blocks = self.parse_text('id,desc\n1,"a, b\nline 2"\n')
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0].raw_record["desc"], "a, b\nline 2")

    def test_semicolon_delimiter(self):
        blocks = self.parse_text("id;name\n1;SQLi\n2;XSS\n")
        self.assertEqual(blocks[0].raw_record, {"id": "1", "name": "SQLi"})

    def test_bom_removed(self):
        blocks = self.parse_text("id,name\n1,a\n", encoding="utf-8-sig")
        self.assertIn("id", blocks[0].raw_record)

    def test_blank_row_keeps_real_position(self):
        blocks = self.parse_text("id,name\n1,a\n,\n3,c\n")
        self.assertEqual([b.locator.start_row for b in blocks], [1, 3])

    def test_header_rename_and_ragged_rows(self):
        blocks = self.parse_text("id,,id\n1\n1,2,3,4\n")
        self.assertEqual(list(blocks[0].raw_record), ["id", "col_2", "id_2"])
        self.assertEqual(blocks[0].raw_record["col_2"], "")
        self.assertEqual(blocks[1].raw_record["_extra"], ["4"])

    def test_header_only_raises(self):
        with self.assertRaises(ValueError):
            self.parse_text("id,name\n")


class TestJsonParser(ParserTestCase):
    parser = JsonParser()
    file_format = "JSON"
    suffix = ".json"

    def test_json_path_builder(self):
        self.assertEqual(build_json_path("$", "$defs"), "$['$defs']")
        self.assertEqual(build_json_path("$", "properties"), "$.properties")
        self.assertEqual(build_json_path("$", 0), "$[0]")
        self.assertEqual(build_json_path("$", "it's"), "$['it\\'s']")

    def test_real_cvss_file(self):
        blocks = self.parse_path(DATA_DIR / "First_CVSS_v4.0.json")
        paths = [b.locator.json_path for b in blocks]
        self.assertEqual(len(blocks), 1 + 33 + 34 + 1)
        self.assertEqual(paths[0], "$")
        self.assertIn("$['$defs'].attackVectorType", paths)
        self.assertIn("$.properties.vectorString", paths)
        self.assertIn("$.allOf[0]", paths)
        self.assertIn("license", blocks[0].raw_record)

    def test_root_list(self):
        blocks = self.parse_text('[{"id": 1, "ok": true}, {"id": 2, "x": null}]')
        self.assertEqual([b.locator.json_path for b in blocks], ["$[0]", "$[1]"])
        self.assertEqual(blocks[0].text_content, "[0]\nid: 1\nok: true")

    def test_root_scalar_and_empty_records(self):
        self.assertEqual(self.parse_text('"hello"')[0].raw_record, {"value": "hello"})
        blocks = self.parse_text('{"items": [{}, {"a": 1}]}')
        self.assertEqual([b.locator.json_path for b in blocks], ["$.items[1]"])

    def test_unicode_preserved(self):
        blocks = self.parse_text('{"tiêu đề": "Lỗ hổng"}')
        self.assertEqual(blocks[0].text_content, "tiêu đề: Lỗ hổng")

    def test_invalid_json_reports_line(self):
        with self.assertRaisesRegex(ValueError, "dòng 2"):
            self.parse_text('{\n  "a": ,\n}')


class TestTxtParser(ParserTestCase):
    parser = TxtParser()
    file_format = "TXT"
    suffix = ".txt"

    def kinds(self, blocks):
        return [(b.block_type, b.locator.start_line, b.locator.end_line) for b in blocks]

    def test_paragraphs_and_blank_lines(self):
        blocks = self.parse_text("\n\nDoan 1 dong 1\nDoan 1 dong 2\n\n\nDoan 2\n\n")
        self.assertEqual(self.kinds(blocks), [("paragraph", 3, 4), ("paragraph", 7, 7)])
        self.assertEqual(blocks[0].text_content, "Doan 1 dong 1\nDoan 1 dong 2")

    def test_bullets_with_continuation(self):
        blocks = self.parse_text("- a\n  tiep noi\n* b\n• c\n1. d\n2) e\na) f\n")
        self.assertEqual(self.kinds(blocks), [("list", 1, 7)])

    def test_paragraph_then_list_split(self):
        blocks = self.parse_text("Mo ta:\n- a\n- b\n")
        self.assertEqual(self.kinds(blocks), [("paragraph", 1, 1), ("list", 2, 3)])

    def test_fenced_code_with_blank_line(self):
        blocks = self.parse_text("Intro\n```bash\nls\n\ncat x\n```\nSau\n")
        self.assertEqual(self.kinds(blocks), [("paragraph", 1, 1), ("code", 2, 6), ("paragraph", 7, 7)])
        self.assertEqual(blocks[1].code_language, "bash")

    def test_unclosed_fence(self):
        blocks = self.parse_text("```\nabc\n\ndef\n")
        self.assertEqual(self.kinds(blocks), [("code", 1, 4)])

    def test_indented_payload_is_code(self):
        blocks = self.parse_text("Payload:\n\n    ' OR 1=1 --\n    admin'--\n")
        self.assertEqual(blocks[1].block_type, "code")
        self.assertEqual(blocks[1].text_content, "    ' OR 1=1 --\n    admin'--")

    def test_crlf_and_bom(self):
        blocks = self.parse_text("A\r\n\r\nB\r\n", encoding="utf-8-sig", newline="")
        self.assertEqual(self.kinds(blocks), [("paragraph", 1, 1), ("paragraph", 3, 3)])
        self.assertEqual(blocks[0].text_content, "A")

    def test_empty_raises(self):
        with self.assertRaises(ValueError):
            self.parse_text("  \n\n")


if __name__ == "__main__":
    unittest.main()
