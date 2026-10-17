import os
from src.pipeline.parsers.remediation_parser import RemediationParser

FENCE = chr(96) * 3

SAMPLE_MD = f"""# SQL Injection Prevention
## Anatomy of Vulnerability
{FENCE}java
String query = "SELECT * FROM users WHERE name = " + input;
{FENCE}
## Primary Defenses
### Defense Option 1: Parameterized Queries
#### Safe Java Example
{FENCE}java
PreparedStatement stmt = conn.prepareStatement("SELECT * FROM users WHERE name = ?");
stmt.setString(1, input);
{FENCE}
"""

def test_heading_breadcrumbs():
    parser = RemediationParser(doc_id="test_sqli", owasp_category="A05:2025", cwe_ids=["CWE-89"])
    doc = parser.parse_markdown(SAMPLE_MD)
    assert len(doc.code_snippets) == 2
    assert "Anatomy of Vulnerability" in doc.code_snippets[0].heading_path
    assert "Primary Defenses > Defense Option 1" in doc.code_snippets[1].heading_path

def test_code_role_classification():
    parser = RemediationParser(doc_id="test_sqli", owasp_category="A05:2025", cwe_ids=["CWE-89"])
    doc = parser.parse_markdown(SAMPLE_MD)
    # Snippet 1 is vulnerable
    assert doc.code_snippets[0].code_role == "vulnerable_antipattern"
    assert doc.code_snippets[0].defense_tier == "anti_pattern"
    # Snippet 2 is secure
    assert doc.code_snippets[1].code_role == "secure_solution"
    assert doc.code_snippets[1].defense_tier == "primary"

def test_fixture_edge_cases():
    fixture_path = "tests/fixtures/remediation_sample.md"
    with open(fixture_path, "r", encoding="utf-8") as f:
        md_text = f.read()
    parser = RemediationParser(doc_id="test_fixture", owasp_category="A05:2025", cwe_ids=["CWE-89"])
    doc = parser.parse_markdown(md_text)
    assert doc.code_snippets[1].language == "csharp"
    assert len(doc.directives) == 1
    assert doc.directives[0].enforcement_level == "MANDATORY"

if __name__ == "__main__":
    test_heading_breadcrumbs()
    test_code_role_classification()
    test_fixture_edge_cases()
    print("[PASS] All unit tests in tests/test_remediation_parser.py passed!")
