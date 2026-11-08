import logging, json
import re
from pathlib import Path
from typing import Any
from schema import RetrievalScope

logger = logging.getLogger(__name__)

CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parent
INGESTION_OUTPUT_DIR = PROJECT_ROOT / "Document Ingestion and Parsing" / "output" / "Ingestion"
DOCUMENTS_CATALOG_PATH = INGESTION_OUTPUT_DIR / "documents.json"
VERSIONS_CATALOG_PATH = INGESTION_OUTPUT_DIR / "documents_version.json"

FORMAT_MAPPING = {
    "MD": "MARKDOWN",
    "MARKDOWN": "MARKDOWN",
    "PDF": "PDF",
    "JSON": "JSON",
    "CSV": "CSV",
    "TXT": "TXT",
}

DOC_TYPE_KEYWORDS = {
    "cheatsheet": ["cheat_sheet"],
    "standard": ["asvs"], 
    "specification": ["cvss"]
}

def norm(s: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", (s or "").lower()))

class ScopeBuilder:
    def __init__(
        self,
        documents_catalog: list[dict[str, Any]] | None = None,
        versions_catalog: list[dict[str, Any]] | None = None,
        ontology_dict: dict[str, list[str]] | None = None,
    ):
        if documents_catalog is not None:
            self.doc_catalog = documents_catalog
        elif DOCUMENTS_CATALOG_PATH.exists():
            self.doc_catalog = json.loads(DOCUMENTS_CATALOG_PATH.read_text(encoding="utf-8"))
        else:
            logger.warning("Not found file: %s", DOCUMENTS_CATALOG_PATH)
            self.doc_catalog = []

        if versions_catalog is not None:
            self.ver_catalog = versions_catalog
        elif VERSIONS_CATALOG_PATH.exists():
            self.ver_catalog = json.loads(VERSIONS_CATALOG_PATH.read_text(encoding="utf-8"))
        else:
            logger.warning("Not found file: %s", VERSIONS_CATALOG_PATH)
            self.ver_catalog = []

        self.ontology_dict = {
            k.lower().strip(): [term.lower().strip() for term in v]
            for k, v in (ontology_dict or {}).items()
        }
        self.valid_doc_ids: set[str] = {
            d.get("document_id")
            for d in self.doc_catalog
            if isinstance(d, dict) and "document_id" in d
        }
        
    def _resolve_canonical_doc_ids(self, raw_sources: list[str]) -> list[str]:
        """
        Resolve Any alias, publisher name, or document group into an exact list of document_ids
        """
        resolved_ids = set()
        
        for src in raw_sources:
            clean_src = src.strip().lower()
            if not clean_src:
                continue

            # 1. Exact document_id match
            if clean_src in self.valid_doc_ids:
                resolved_ids.add(clean_src)
                continue

            # Split source query string into an independent set of tokens
            # Example: "first_cvss" -> {"first", "cvss"}; "owasp-top10" -> {"owasp", "top10"}
            src_tokens = set(re.findall(r"[a-z0-9]+", clean_src))
            if not src_tokens:
                continue

            matched = False
            for doc in self.doc_catalog:
                d_id = doc.get("document_id", "").lower()
                title = doc.get("canonical_title", doc.get("title", "")).lower()
                publisher = doc.get("publisher", "").lower()

                # 2. Publisher Match
                if clean_src == publisher and publisher != "":
                    resolved_ids.add(doc["document_id"])
                    matched = True
                    continue

                # 3. Token Subset Match
                # Build a token set representing all document metadata/information
                doc_tokens = set(re.findall(r"[a-z0-9]+", f"{d_id} {title} {publisher}"))

                
                if src_tokens.issubset(doc_tokens):
                    resolved_ids.add(doc["document_id"])
                    matched = True

            if not matched:
                logger.warning("No matching documents found for the given source: '%s'", src)

        return sorted(resolved_ids)

    @staticmethod
    def extract_versions(text: str) -> list[str]:
        """
        Extract all version formats appearing in the sentence:
        - Year: 2021, 2025, 2017...
        - Tag: v4.0, 5.0.0, v3.1, 1.2, 4.0...
        """
        if not text:
            return []
        
        """
        Pattern bắt:
            1. 4-digit year 
            2. Version with 'v' or 'ver' prefix: v4.0, ver5.0.0
            3. Decimal/SemVer format: 4.0, 5.0.0, 3.1
        """
        pattern = r"\b(?:v(?:er(?:sion)?)?\.?\s*)?(\d{4}|\d+(?:\.\d+)+)\b"
        
        matches = re.findall(pattern, text, flags=re.IGNORECASE)

        seen = set()
        results = []
        for m in matches:
            m_clean = m.strip().lower()
            if m_clean not in seen:
                seen.add(m_clean)
                results.append(m_clean)
        return results

    @staticmethod
    def normalize_version_tag(tag: str) -> str:
        """Strip 'v' or 'ver.' prefix for flexible comparison (v4.0 -> 4.0)"""
        return re.sub(r"^v(?:er)?\.?\s*", "", str(tag).strip().lower())

    def _resolve_version_ids(self, target_doc_ids: list[str], raw_version: str) -> list[str]:
        """
        Maps version_tag (e.g., '2025', 'v4.0') to hashed version_id in metadata.db
        """
        if not raw_version:
            return []

        norm_req = self.normalize_version_tag(raw_version)
        matched_version_ids = []

        for ver in self.ver_catalog:
            # Check whether this version belongs to the target document group
            if target_doc_ids and ver.get("document_id") not in target_doc_ids:
                continue

            tag = str(ver.get("version_tag", "")).lower()
            norm_tag = self.normalize_version_tag(tag)
            file_name = ver.get("file_name", "").lower()
            if (norm_req == norm_tag) or (norm_req in file_name):
                matched_version_ids.append(ver["version_id"])

        return sorted(set(matched_version_ids))
    
    @staticmethod
    def _detect_semantic_preferences(query_text: str) -> tuple[list[str], list[str]]:
        """
        Semantic Scoping
        Analyzes query keywords (bilingual English - Vietnamese) to infer:
            1. pref_types: Target chunk types to prioritize
            2. pref_headings: Target section headings appearing in the document's heading_path
        """
        q_lower = query_text.lower().strip()

        PREFERENCE_RULES = [
            # GROUP 1: CONCEPTS / DEFINITIONS / OVERVIEW
            {
                "patterns": [
                    r"\b(khái niệm|là gì|định nghĩa|tổng quan|giới thiệu|bản chất|thế nào là|ý nghĩa)\b",
                    r"\b(what is|what are|definition|define|concept|overview|introduction|background|describe|description|meaning)\b",
                ],
                "types": ["paragraph", "mixed", "heading"],
                "headings": [
                    "Description",
                    "Background",
                    "Overview",
                    "What is",
                    "Introduction",
                    "About",
                ],
            },
            # GROUP 2: PREVENTION / MITIGATION / PROTECTION
            {
                "patterns": [
                    r"\b(phòng chống|khắc phục|ngăn ngừa|giảm thiểu|cách xử lý|biện pháp|phòng ngừa|cách fix|vá lỗi|bảo vệ)\b",
                    r"\b(how to prevent|prevention|remediation|mitigate|mitigation|how to fix|best practices|countermeasure|defense|hardening|protect|recommendation)\b",
                ],
                "types": ["list", "mixed", "paragraph"],
                "headings": [
                    "How to prevent",
                    "Remediation",
                    "Mitigation",
                    "Recommendations",
                    "Hardening",
                    "Defense",
                ],
            },
            # GROUP 3: METRICS / STATISTICAL TABLES
            {
                "patterns": [
                    r"\b(bảng|thống kê|tỷ lệ|số liệu|điểm số|tính điểm|đo lường|thước đo|chỉ số|mức độ ảnh hưởng)\b",
                    r"\b(table|score|score table|metric|statistics|statistical|rate|incidence|coverage|benchmark|calculation|severity)\b",
                ],
                "types": ["table", "mixed"],
                "headings": [
                    "Score table",
                    "Statistics",
                    "Metrics",
                    "Benchmark",
                    "Table",
                ],
            },
            # GROUP 4: EXAMPLES / REAL-WORLD EXPLOITS
            {
                "patterns": [
                    r"\b(kịch bản|tấn công|ví dụ|khai thác|mô phỏng|tình huống|trường hợp|thực tế)\b",
                    r"\b(attack scenario|scenario|example|exploit|exploitation|case study|use case|how it works)\b",
                ],
                "types": ["paragraph", "mixed", "list"],
                "headings": [
                    "Example attack scenarios",
                    "Examples",
                    "Use Cases",
                    "Case Studies",
                ],
            },
            # GROUP 5: RELATED VULNERABILITY CATALOG / CWE IDS / STANDARDS
            {
                "patterns": [
                    r"\b(danh sách cwe|mã cwe|danh mục cwe|liệt kê cwe|tiêu chuẩn|tài liệu tham khảo)\b",
                    r"\b(mapped cwe|cwe list|list of mapped cwes|cwe mapping|related cwe|reference|appendix)\b",
                ],
                "types": ["list", "table"],
                "headings": ["list of Mapped CWEs", "References", "Appendix"],
            },
        ]

        for rule in PREFERENCE_RULES:
            for pattern in rule["patterns"]:
                if re.search(pattern, q_lower):
                    return rule["types"], rule["headings"]

        return [], []

    def expand_concepts(self, task: dict[str, Any], entities: dict[str, Any]) -> list[str]:
        """Extract ontology keywords from entities and task.objective"""
        target_keys = set()

        for v in entities.get("vulnerabilities") or []:
            target_keys.add(v.lower().strip())

        if task and task.get("objective", {}).get("type") == "vulnerabilities":
            for v in task["objective"].get("ids", []):
                target_keys.add(v.lower().strip())

        expansions = []
        for key in target_keys:
            expansions.append(key)
            if self.ontology_dict:
                try:
                    synonyms = self.ontology_dict.get(key) or []
                    expansions.extend(synonyms)
                except Exception:
                    pass

        return sorted(set(expansions))

    def requested_versions(self, text: str) -> list[str]:
        """
        Versions the user explicitly asks for. A number counts as a version request only if it is a
        year (19xx/20xx), carries a v/ver/version prefix, or equals a version_tag in the catalog.
        This keeps protocol numbers such as "TLS 1.2" or "OAuth 2.0" from becoming version filters.
        """
        if not text:
            return []
        known_tags = {
            self.normalize_version_tag(v.get("version_tag", ""))
            for v in self.ver_catalog
            if isinstance(v, dict) and v.get("version_tag")
        }
        pattern = r"\b(v(?:er(?:sion)?)?\.?\s*)?(\d{4}|\d+(?:\.\d+)+)\b"
        results: list[str] = []
        for prefix, number in re.findall(pattern, text, flags=re.IGNORECASE):
            is_year = re.fullmatch(r"(?:19|20)\d{2}", number) is not None
            if (prefix or is_year or number in known_tags) and number not in results:
                results.append(number)
        return results

    def build_scope(self, query_plan: dict[str, Any], task: dict[str, Any] | None = None) -> tuple[RetrievalScope, list[str]]:
        flt = query_plan.get("filter") or {}
        entities = query_plan.get("entities") or {}

        if task and (task.get("objective") or {}).get("type") == "documents":
            target_sources = task["objective"].get("ids", [])
        else:
            target_sources = flt.get("source") or []

        doc_ids = self._resolve_canonical_doc_ids(target_sources)

        # Version precedence: explicit branch version > versions named in the task query > plan filter.
        task_query = task.get("query", "") if task else ""
        explicit_version = task.get("version") if task else None
        if explicit_version:
            requested = [str(explicit_version)]
        else:
            requested = self.requested_versions(task_query)
            if not requested and flt.get("version"):
                requested = [str(flt["version"])]

        version_ids: list[str] = []
        for v_req in requested:
            version_ids.extend(self._resolve_version_ids(doc_ids, v_req))
        version_ids = sorted(set(version_ids))

        # document_type is semantic (cheatsheet/standard/specification) unless it names a physical format.
        raw_type = (flt.get("document_type") or "").strip().lower().replace(".", "")
        file_format = FORMAT_MAPPING.get(raw_type.upper())
        if not file_format and raw_type in DOC_TYPE_KEYWORDS:
            kws = DOC_TYPE_KEYWORDS[raw_type]
            pool = doc_ids or sorted(self.valid_doc_ids)
            narrowed = [d for d in pool if any(k in d.lower() for k in kws)]
            if narrowed:
                doc_ids = narrowed

        # is_relaxed now means "a version was requested but the catalog has none": the caller abstains.
        is_relaxed = bool(requested) and not version_ids
        if is_relaxed:
            logger.warning("Requested version(s) %s not found in catalog", requested)

        filters_list = []
        if doc_ids:
            filters_list.append({"document_id": doc_ids[0]} if len(doc_ids) == 1 else {"document_id": {"$in": doc_ids}})
        if file_format:
            filters_list.append({"file_format": file_format})
        if version_ids:
            filters_list.append({"version_id": version_ids[0]} if len(version_ids) == 1 else {"version_id": {"$in": version_ids}})

        chroma_filter = None
        if len(filters_list) == 1:
            chroma_filter = filters_list[0]
        elif len(filters_list) > 1:
            chroma_filter = {"$and": filters_list}

        # SEMANTIC FILTER
        query_text = (task.get("query") if task else None) or query_plan.get("resolved_query") or ""
        pref_types, pref_headings = self._detect_semantic_preferences(query_text)
        ontology_expansions = self.expand_concepts(task, entities)

        candidate_docs = []
        if not doc_ids and ontology_expansions:
            for doc in self.doc_catalog:
                hay = f" {norm(doc.get('canonical_title', ''))} {norm(doc.get('document_id', ''))} "
                if any(f" {norm(k)} " in hay for k in ontology_expansions if norm(k)):
                    candidate_docs.append(doc["document_id"])

        scope = RetrievalScope(
            chroma_filter=chroma_filter,
            document_ids=doc_ids,
            file_formats=[file_format] if file_format else [],
            version_ids=version_ids,
            is_relaxed=is_relaxed,
            preferred_chunk_types=pref_types,
            preferred_headings=pref_headings,
            candidate_doc_ids=sorted(set(candidate_docs)),
            ontology_keywords=ontology_expansions,
        )
        return scope, ontology_expansions
