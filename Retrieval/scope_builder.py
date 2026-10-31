import logging
import re
from typing import Any
from schema import RetrievalScope

logger = logging.getLogger(__name__)

FORMAT_MAPPING = {
    "MD": "MD",
    "MARKDOWN": "MD",
    "PDF": "PDF",
    "JSON": "JSON",
    "CSV": "CSV",
    "TXT": "TXT",
}


class ScopeBuilder:
    def __init__(
        self,
        documents_catalog: list[dict[str, Any]] | None = None,
        versions_catalog: list[dict[str, Any]] | None = None,
        ontology_dict: dict[str, list[str]] | None = None,
    ):
        self.doc_catalog = documents_catalog or [] # Document catalog loaded from documents.json
        self.ver_catalog = versions_catalog or [] # Version catalog loaded from documents_version.json
        self.ontology_dict = ontology_dict

        self.valid_doc_ids: set[str] = {d["document_id"] for d in self.doc_catalog}

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

    def _resolve_version_ids(self, target_doc_ids: list[str], version_tag_req: str) -> list[str]:
        """
        Maps version_tag (e.g., '2025', 'v4.0') to hashed version_id in metadata.db
        """
        if not version_tag_req:
            return []

        v_req = str(version_tag_req).strip().lower()
        matched_version_ids = []

        for ver in self.ver_catalog:
            # Check whether this version belongs to the target document group
            if target_doc_ids and ver.get("document_id") not in target_doc_ids:
                continue

            tag = str(ver.get("version_tag", "")).lower()
            if v_req == tag or v_req in ver.get("file_name", "").lower():
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
                    synonyms = self.ontology_dict.get_synonyms(key) or []
                    expansions.extend(synonyms)
                except Exception:
                    pass

        return sorted(set(expansions))

    def build_scope(self, query_plan: dict[str, Any], task: dict[str, Any] | None = None) -> tuple[RetrievalScope, list[str]]:
        flt = query_plan.get("filter", {})
        entities = query_plan.get("entities", {})


        target_sources = []
        if task and task.get("objective", {}).get("type") == "documents":
            target_sources = task["objective"].get("ids", [])
        else:
            target_sources = flt.get("source") or []

        doc_ids = self._resolve_canonical_doc_ids(target_sources)

        # Filter file_format
        raw_fmt = flt.get("document_type")
        file_format = FORMAT_MAPPING.get(raw_fmt.upper().replace(".", ""), raw_fmt.upper()) if raw_fmt else None

        # Filter version
        version_ids = self._resolve_version_ids(doc_ids, flt.get("version"))

        is_relaxed = False
        if flt.get("version") and not version_ids:
            logger.warning("Không tìm thấy version_id nào khớp với '%s'. Tự động nới lỏng bỏ lọc version.", flt.get("version"))
            is_relaxed = True
            
            
        # Compiles filter clauses for ChromaDB
        filters_list = []
        if doc_ids:
            if len(doc_ids) == 1:
                filters_list.append({"document_id": doc_ids[0]})
            else:
                filters_list.append({"document_id": {"$in": doc_ids}})

        if file_format:
            filters_list.append({"file_format": file_format})

        if version_ids:
            if len(version_ids) == 1:
                filters_list.append({"version_id": version_ids[0]})
            else:
                filters_list.append({"version_id": {"$in": version_ids}})

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
                title = doc.get("canonical_title", "").lower()
                doc_id = doc.get("document_id", "").lower()
                if Any(k in title or k in doc_id for k in ontology_expansions):
                    candidate_docs.append(doc["document_id"])
                    
        scope = RetrievalScope(
            chroma_filter=chroma_filter,
            document_ids=doc_ids,
            file_formats=[file_format] if file_format else [],
            version_ids=version_ids,
            is_relaxed=False,
            preferred_chunk_types=pref_types,
            preferred_headings=pref_headings,
            candidate_doc_ids=sorted(set(candidate_docs)),
            ontology_keywords=ontology_expansions
        )

        return scope, ontology_expansions
