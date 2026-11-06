"""
JSON Parser: splits a file into RECORD blocks using general structural rules (key-name agnostic):
- Root is list -> each element is 1 record at $[i].
- Root is dict -> keys whose values are a "collection" (list/dict where all children are list/dict) yield
  1 record per child; remaining keys are grouped into 1 metadata record at "$".
- Root is scalar -> 1 record at "$".
"""
import json
import platform
import re

from parser_utils import build_block, read_text, run_cli
from Ingestion.schema_ingestion import DocumentVersionSchema, FileFormat
from schema_parsing import BlockType, ContentBlockSchema, JsonLocator

PARSER_NAME = "python-json"
PARSER_VERSION = platform.python_version()

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def build_json_path(parent: str, key: str | int) -> str:
    """Append one step to JSONPath: index -> [i], identifier -> .key, otherwise -> ['key']."""
    if isinstance(key, int):
        return f"{parent}[{key}]"
    if _IDENTIFIER.match(key):
        return f"{parent}.{key}"
    escaped = key.replace("\\", "\\\\").replace("'", "\\'")
    return f"{parent}['{escaped}']"


def _is_collection(value) -> bool:
    children = value.values() if isinstance(value, dict) else value if isinstance(value, list) else None
    return bool(children) and all(isinstance(c, (dict, list)) for c in children)


def _scalar_to_text(value) -> str:
    # Keep str as-is; format numbers/bools/null as JSON literals (true/false/null)
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def _flatten(value, prefix: str = "") -> list[str]:
    """Flatten a value into 'path.to.key: value' lines preserving original key order."""
    if isinstance(value, dict):
        lines = []
        for k, v in value.items():
            lines.extend(_flatten(v, f"{prefix}.{k}" if prefix else str(k)))
        return lines
    if isinstance(value, list):
        if all(not isinstance(v, (dict, list)) for v in value):
            return [f"{prefix}: {', '.join(_scalar_to_text(v) for v in value)}"] if value else []
        lines = []
        for i, v in enumerate(value):
            lines.extend(_flatten(v, f"{prefix}[{i}]"))
        return lines
    return [f"{prefix}: {_scalar_to_text(value)}" if prefix else _scalar_to_text(value)]


class JsonParser:
    file_format = FileFormat.JSON.value

    def parse(self, version: DocumentVersionSchema) -> list[ContentBlockSchema]:
        try:
            data = json.loads(read_text(version))
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON: line {e.lineno} column {e.colno}: {e.msg}") from e

        blocks: list[ContentBlockSchema] = []
        for json_path, title, value in self._split_records(data):
            lines = _flatten(value)
            if not "".join(lines).strip():
                continue  # empty record ({}, [], "") has no searchable content
            text = "\n".join([title, *lines]) if title else "\n".join(lines)
            blocks.append(build_block(
                version,
                block_idx=len(blocks),
                block_type=BlockType.RECORD.value,
                text_content=text,
                locator=JsonLocator(json_path=json_path),
                parser_name=PARSER_NAME,
                parser_version=PARSER_VERSION,
                raw_record=value if isinstance(value, dict) else {"value": value},
            ))

        if not blocks:
            raise ValueError("JSON contains no non-empty records")
        return blocks

    @staticmethod
    def _split_records(data) -> list[tuple[str, str | None, object]]:
        """Return list of (json_path, record title, value)."""
        if isinstance(data, list):
            return [(build_json_path("$", i), f"[{i}]", item) for i, item in enumerate(data)]
        if not isinstance(data, dict):
            return [("$", None, data)]

        metadata: dict = {}
        collections: list[tuple[str, str | None, object]] = []
        for key, value in data.items():
            if not _is_collection(value):
                metadata[key] = value
                continue
            parent = build_json_path("$", key)
            children = value.items() if isinstance(value, dict) else enumerate(value)
            for child_key, child in children:
                title = child_key if isinstance(child_key, str) else f"{key}[{child_key}]"
                collections.append((build_json_path(parent, child_key), title, child))

        # Place metadata record first so block_idx 0 describes the document as a whole
        return ([("$", None, metadata)] if metadata else []) + collections


if __name__ == "__main__":
    run_cli(JsonParser())
