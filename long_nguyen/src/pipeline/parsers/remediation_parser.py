"""
src/pipeline/parsers/remediation_parser.py
Specialized parser for OWASP Cheat Sheets and remediation guides.
Covers Tasks B.1 through B.5.
"""

import re
from dataclasses import dataclass, field
from typing import List, Dict, Any

# Task B.2: Language Normalization Dictionary
LANGUAGE_MAP = {
    "py": "python", "python": "python", "python3": "python",
    "java": "java",
    "csharp": "csharp", "cs": "csharp", "c#": "csharp", "dotnet": "csharp",
    "vbnet": "vbnet", "vb": "vbnet",
    "js": "javascript", "javascript": "javascript", "node": "javascript", "nodejs": "javascript",
    "ts": "typescript", "typescript": "typescript",
    "php": "php", "php5": "php", "php7": "php",
    "sql": "sql", "pgsql": "sql", "mysql": "sql", "plsql": "sql",
    "rb": "ruby", "ruby": "ruby",
    "go": "go", "golang": "go",
    "rs": "rust", "rust": "rust",
    "html": "html", "xml": "xml",
    "nginx": "nginx", "apache": "apache", "http": "http",
    "sh": "bash", "bash": "bash", "shell": "bash"
}

@dataclass
class RemediationCodeBlock:
    snippet_id: str
    chunk_id: str
    language: str
    code_content: str
    code_role: str      # 'secure_solution' | 'vulnerable_antipattern' | 'neutral_example'
    defense_tier: str   # 'primary' | 'secondary' | 'defense_in_depth' | 'anti_pattern'
    heading_path: str
    line_start: int
    line_end: int
    explanation: str = ""

@dataclass
class DefenseDirective:
    directive_id: str
    rule_number: str
    rule_statement: str
    enforcement_level: str  # 'MANDATORY' | 'RECOMMENDED' | 'PROHIBITED'
    heading_path: str

@dataclass
class ParsedRemediationDoc:
    doc_id: str
    title: str
    owasp_category: str
    cwe_ids: List[str]
    sections: List[Dict[str, Any]] = field(default_factory=list)
    code_snippets: List[RemediationCodeBlock] = field(default_factory=list)
    directives: List[DefenseDirective] = field(default_factory=list)

class RemediationParser:
    """Parses OWASP Markdown Cheat Sheets into structured remediation entities."""

    # Task B.3: Multi-Signal Polarity Keywords
    NEGATIVE_HEADING_RE = re.compile(
        r'\b(anatomy of|vulnerable|what not to do|insecure|flaw|bad example|pitfalls|attack|discouraged)\b',
        re.IGNORECASE
    )
    POSITIVE_HEADING_RE = re.compile(
        r'\b(safe|defense|defenses|solution|prepared statement|parameterized|remediated|compliant|correct|prevention)\b',
        re.IGNORECASE
    )
    NEGATIVE_TEXT_RE = re.compile(
        r'\b(vulnerable|flaw|exploit|attacker can enter|do not use|incorrectly|unsafe|incorrect|bad|insecure|avoid|anti-pattern)\b',
        re.IGNORECASE
    )
    POSITIVE_TEXT_RE = re.compile(
        r'\b(safe approach|to prevent|remediated version|recommended implementation|safe version|correct|secure|solution|recommended)\b',
        re.IGNORECASE
    )

    # Task B.5: Rule & Directive Regex
    DIRECTIVE_RE = re.compile(
        r'^(?:[-*]\s+|\d+\.\s+)?(?:\*\*)?(?:Rule\s+(\d+|#\d+)|Option\s+(\d+))\s*:\s*(.+?)(?:\*\*)?$',
        re.IGNORECASE
    )

    def __init__(self, doc_id: str, owasp_category: str, cwe_ids: List[str]):
        self.doc_id = doc_id
        self.owasp_category = owasp_category
        self.cwe_ids = cwe_ids

    def normalize_language(self, raw_lang: str) -> str:
        if not raw_lang:
            return "text"
        cleaned = raw_lang.strip().lower()
        return LANGUAGE_MAP.get(cleaned, cleaned)

    def parse_markdown(self, markdown_text: str) -> ParsedRemediationDoc:
        lines = markdown_text.splitlines()
        active_headings: Dict[int, str] = {}
        parsed_doc = ParsedRemediationDoc(
            doc_id=self.doc_id,
            title="",
            owasp_category=self.owasp_category,
            cwe_ids=self.cwe_ids
        )

        in_code_block = False
        current_code_lines: List[str] = []
        current_code_lang = "text"
        code_start_line = 0
        current_explanation: List[str] = []

        # Track section chunks for Task C.2 dual-table storage
        current_section_lines: List[str] = []
        section_start_line = 1
        current_heading_level = 1
        section_has_code = False

        heading_regex = re.compile(r'^(#{1,6})\s+(.+)$')
        code_fence_regex = re.compile(r'^```\s*([\w#+-]+)?')

        def flush_section(end_line: int):
            nonlocal current_section_lines, section_has_code
            content = "\n".join(current_section_lines).strip()
            if content:
                breadcrumb = " > ".join([active_headings[k] for k in sorted(active_headings.keys())]) or parsed_doc.title or self.doc_id
                section_idx = len(parsed_doc.sections)
                parsed_doc.sections.append({
                    "chunk_id": f"chk_{self.doc_id}_{section_idx:04d}",
                    "content": f"[CheatSheet: {breadcrumb}]\n{content}",
                    "heading_path": breadcrumb,
                    "heading_level": current_heading_level,
                    "line_start": section_start_line,
                    "line_end": max(section_start_line, end_line),
                    "has_code": section_has_code
                })
            current_section_lines = []
            section_has_code = False

        for idx, line in enumerate(lines, start=1):
            # Task B.2: Detect Code Fences
            fence_match = code_fence_regex.match(line.strip())
            if fence_match:
                current_section_lines.append(line)
                if not in_code_block:
                    in_code_block = True
                    section_has_code = True
                    current_code_lang = self.normalize_language(fence_match.group(1) or "text")
                    current_code_lines = []
                    code_start_line = idx
                else:
                    in_code_block = False
                    code_end_line = idx
                    code_content = "\n".join(current_code_lines)
                    breadcrumb = " > ".join([active_headings[k] for k in sorted(active_headings.keys())])
                    preceding_text = " ".join(current_explanation[-3:])
                    first_code_lines = "\n".join(current_code_lines[:5])

                    role = self._classify_code_role(breadcrumb, preceding_text, first_code_lines)
                    tier = self._classify_defense_tier(breadcrumb, role)

                    current_chunk_idx = len(parsed_doc.sections)
                    chunk_id = f"chk_{self.doc_id}_{current_chunk_idx:04d}"
                    snippet_id = f"snip_{self.doc_id}_{code_start_line}_{current_code_lang}"

                    snippet = RemediationCodeBlock(
                        snippet_id=snippet_id,
                        chunk_id=chunk_id,
                        language=current_code_lang,
                        code_content=code_content,
                        code_role=role,
                        defense_tier=tier,
                        heading_path=breadcrumb,
                        line_start=code_start_line,
                        line_end=code_end_line,
                        explanation="\n".join(current_explanation[-3:])
                    )
                    parsed_doc.code_snippets.append(snippet)
                    current_explanation = []
                continue

            if in_code_block:
                # Preserve exact whitespace and special characters inside code blocks
                current_code_lines.append(line)
                current_section_lines.append(line)
                continue

            # Task B.1: Heading Tree & Breadcrumb State Machine
            h_match = heading_regex.match(line)
            if h_match:
                flush_section(idx - 1)
                section_start_line = idx
                level = len(h_match.group(1))
                current_heading_level = level
                # Clean markdown anchors like {#safe-java}
                raw_heading = h_match.group(2).strip()
                clean_heading = re.sub(r'\s*\{#[\w-]+\}\s*$', '', raw_heading).strip()

                if level == 1 and not parsed_doc.title:
                    parsed_doc.title = clean_heading

                # Prune deeper or equal levels
                active_headings = {k: v for k, v in active_headings.items() if k < level}
                active_headings[level] = clean_heading
                current_section_lines.append(line)
                current_explanation = []
                continue

            current_section_lines.append(line)

            # Task B.5: Actionable Defense Directives Parser
            stripped = line.strip()
            if stripped:
                current_explanation.append(stripped)
                dir_match = self.DIRECTIVE_RE.match(stripped)
                if dir_match:
                    rule_num = dir_match.group(1) or dir_match.group(2)
                    statement = dir_match.group(3).strip()
                    breadcrumb = " > ".join([active_headings[k] for k in sorted(active_headings.keys())])
                    enforcement = self._classify_enforcement(statement)
                    parsed_doc.directives.append(
                        DefenseDirective(
                            directive_id=f"dir_{self.doc_id}_{idx}",
                            rule_number=str(rule_num),
                            rule_statement=statement,
                            enforcement_level=enforcement,
                            heading_path=breadcrumb
                        )
                    )

        flush_section(len(lines))
        return parsed_doc

    def _classify_code_role(self, heading_path: str, preceding_text: str, first_code_lines: str) -> str:
        # Scope 3: Check in-code comments first for explicit overrides
        if self.NEGATIVE_TEXT_RE.search(first_code_lines) and not self.POSITIVE_TEXT_RE.search(first_code_lines):
            return "vulnerable_antipattern"

        # Scope 1: Heading breadcrumb signal (Highest Weight)
        if self.NEGATIVE_HEADING_RE.search(heading_path):
            return "vulnerable_antipattern"

        # Scope 2: Preceding paragraph lookback
        neg_in_text = bool(self.NEGATIVE_TEXT_RE.search(preceding_text))
        pos_in_text = bool(self.POSITIVE_TEXT_RE.search(preceding_text))
        pos_in_heading = bool(self.POSITIVE_HEADING_RE.search(heading_path))

        # Conservative fail-safe rule: if negative indicators appear without clear positive remediation framing
        if neg_in_text and not pos_in_text:
            return "vulnerable_antipattern"
        if pos_in_heading or pos_in_text or self.POSITIVE_TEXT_RE.search(first_code_lines):
            return "secure_solution"
        return "neutral_example"

    def _classify_defense_tier(self, heading_path: str, code_role: str) -> str:
        path_lower = heading_path.lower()
        if code_role == "vulnerable_antipattern" or "discouraged" in path_lower or "what not to do" in path_lower:
            return "anti_pattern"
        if "primary defense" in path_lower or "defense option 1" in path_lower or "defense option 2" in path_lower or "prepared statement" in path_lower or "parameterized" in path_lower:
            return "primary"
        if "secondary defense" in path_lower or "defense option 3" in path_lower or "input validation" in path_lower:
            return "secondary"
        return "defense_in_depth"

    def _classify_enforcement(self, statement: str) -> str:
        upper = statement.upper()
        if any(w in upper for w in ["DISCOURAGED", "AVOID", "DO NOT", "NEVER"]):
            return "PROHIBITED"
        if any(w in upper for w in ["MUST", "ALWAYS", "REQUIRED"]):
            return "MANDATORY"
        return "RECOMMENDED"
