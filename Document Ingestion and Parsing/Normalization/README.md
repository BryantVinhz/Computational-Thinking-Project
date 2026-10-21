# Step 3: Text Cleaning & Normalization

Normalizes parsed text blocks for the RAG pipeline while preserving attack payloads byte-for-byte.

## Data Flow & Usage
Data flows through **mask -> clean -> unmask**:
1. Mask   — fenced code, inline code, and safety-net payload lines
            are replaced with `__PAYLOAD_i__` placeholders.
2. Clean  — normalizes newlines, Unicode (NFC), spaces, blank lines,
            and PDF hyphens on the non-payload portions.
3. Unmask — restores original code/payloads in a single pass.
            Blocks typed `code` bypass cleaning entirely.

### Output Schema
The output preserves all original keys from Step 2 (including `text_content`, `structured_content`, `source_locator`, and `metadata`) and enriches the block with flat root-level fields:
- `normalized_id`: Unique identifier formatted as `nrm_{block_id}`.
- `normalizer_version`: Version string of the normalizer.
- `normalized_text`: Cleaned text payload (`text_content` is preserved unchanged).
- `normalized_structured_content`: Cleaned table content with normalized cells (present only for `table` blocks with structured content; original `structured_content` is preserved unchanged).
- `is_code_or_payload`: Boolean indicating if code or attack payload was detected/bypassed.
- `has_modified`: Boolean indicating if text or table cells were modified.
- `char_count_delta`: Difference in character count, computed as `len(text_content) - len(normalized_text)`. A positive number indicates characters were removed.

```bash
python text_cleaner.py --input sample_input.json --output sample_out.json
```

## Design Decisions
- **Single module (`text_cleaner.py`)**: Uses plain `dict`s for simplicity, zero third-party dependencies.
- **Inherit-and-extend**: output is a deep copy of the Step 2 block with Step 3 fields added. All original Step 2 keys and values are preserved without dropping any fields.
- **NFC over NFKC**: Preserves security/math symbols (`≥`, `⇒`) without destructive decomposing.
- **`[ \t]+` over `\s+`**: Collapses spaces without flattening newlines in Markdown lists and tables.
- **Single-pass unmask**: Prevents recursive substitution when payloads contain placeholder strings.

## Known Limitations
- Lines matching safety-net patterns are protected in their entirety; extraneous whitespace around payloads on those lines is therefore not cleaned.
- Unwrapped attack payloads outside the 6 safety-net patterns are not protected against space normalization.
- If an unwrapped payload shares the exact same line with a backtick (`), the safety net will skip that line, leaving the payload unprotected.
- PDF hyphen-join logic strictly merges characters (e.g., `well-\nknown` becomes `wellknown`).
