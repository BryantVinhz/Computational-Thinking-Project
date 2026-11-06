"""
CSV Parser: each data row -> 1 RECORD block, enabling search results to cite the exact source row.
"""
import csv
import io
import platform

from parser_utils import build_block, read_text, run_cli
from Ingestion.schema_ingestion import DocumentVersionSchema, FileFormat
from schema_parsing import BlockType, ContentBlockSchema, CsvLocator

PARSER_NAME = "python-csv"
PARSER_VERSION = platform.python_version()

SNIFF_SAMPLE_SIZE = 64 * 1024
CANDIDATE_DELIMITERS = ",;\t|"


class CsvParser:
    file_format = FileFormat.CSV.value

    def parse(self, version: DocumentVersionSchema) -> list[ContentBlockSchema]:
        text = read_text(version)
        reader = csv.reader(io.StringIO(text, newline=""), dialect=self._sniff_dialect(text))

        try:
            header = self._read_header(reader)
            if header is None:
                raise ValueError("CSV has no header")

            blocks: list[ContentBlockSchema] = []
            # row_num counts every row after header (including empty rows) to match actual file positions
            for row_num, row in enumerate(reader, start=1):
                if not any(cell.strip() for cell in row):
                    continue
                record = self._to_record(header, row)
                blocks.append(build_block(
                    version,
                    block_idx=len(blocks),
                    block_type=BlockType.RECORD.value,
                    text_content=self._to_text(record),
                    locator=CsvLocator(table_idx=0, start_row=row_num, end_row=row_num),
                    parser_name=PARSER_NAME,
                    parser_version=PARSER_VERSION,
                    raw_record=record,
                ))
        except csv.Error as e:
            raise ValueError(f"CSV format error at line {reader.line_num}: {e}") from e

        if not blocks:
            raise ValueError("CSV has no data rows")
        return blocks

    @staticmethod
    def _sniff_dialect(text: str) -> type[csv.Dialect] | csv.Dialect:
        try:
            return csv.Sniffer().sniff(text[:SNIFF_SAMPLE_SIZE], delimiters=CANDIDATE_DELIMITERS)
        except csv.Error:
            return csv.excel  # default to comma delimiter

    @staticmethod
    def _read_header(reader) -> list[str] | None:
        """Use the first non-empty row as header; rename empty or duplicate columns."""
        for row in reader:
            if not any(cell.strip() for cell in row):
                continue
            header: list[str] = []
            seen: dict[str, int] = {}
            for i, name in enumerate(row, start=1):
                name = name.strip() or f"col_{i}"
                if name in seen:
                    seen[name] += 1
                    name = f"{name}_{seen[name]}"
                else:
                    seen[name] = 1
                header.append(name)
            return header
        return None

    @staticmethod
    def _to_record(header: list[str], row: list[str]) -> dict:
        """Pair header with row values; pad missing columns with '' and store extra columns in _extra."""
        padded = row + [""] * (len(header) - len(row))
        record = dict(zip(header, padded))
        if len(row) > len(header):
            record["_extra"] = row[len(header):]
        return record

    @staticmethod
    def _to_text(record: dict) -> str:
        lines = []
        for key, value in record.items():
            if key == "_extra":
                value = ", ".join(value)
            if value.strip():
                lines.append(f"{key}: {value}")
        return "\n".join(lines)


if __name__ == "__main__":
    run_cli(CsvParser())
