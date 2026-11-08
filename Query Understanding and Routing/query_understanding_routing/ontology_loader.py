"""Ontology Loader and Validator (Contract v1.0)."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict, Set

import yaml

MODULE_DIR = Path(__file__).resolve().parent
STAGE_DIR = MODULE_DIR.parent
PROJECT_ROOT = STAGE_DIR.parent

for p in [PROJECT_ROOT, STAGE_DIR, MODULE_DIR]:
    p_str = str(p)
    if p_str not in sys.path:
        sys.path.insert(0, p_str)

try:
    from query_understanding_routing.config import STANDARD_VULNERABILITY_SLUGS
except ImportError:
    try:
        from .config import STANDARD_VULNERABILITY_SLUGS
    except ImportError:
        from config import STANDARD_VULNERABILITY_SLUGS

DEFAULT_ONTOLOGY_PATH = Path(os.getenv("ONTOLOGY_PATH", str((MODULE_DIR / "ontology.yaml").resolve()))).resolve()
REQUIRED_FIELDS = ["canonical", "aliases", "cwe", "subtypes", "related_terms"]


def load_ontology(path: str | Path = DEFAULT_ONTOLOGY_PATH) -> Dict[str, Dict[str, Any]]:
    """
    Load and rigorously validate ontology YAML according to Contract v1.0 Section 3.1.

    Validations performed:
    1. File exists and is valid YAML.
    2. Exactly 5 standard slugs: xss, sqli, bac, auth_failures, misconfig.
    3. Each entry contains all required fields (canonical, aliases, cwe, subtypes, related_terms).
    4. Aliases do not collide across different slugs (case-insensitive).
    5. Disallows overly short or ambiguous aliases like 'bac'.

    Raises:
        FileNotFoundError: If the specified file does not exist.
        ValueError: If ontology schema or alias consistency checks fail.
    """
    file_path = Path(path).resolve()
    if not file_path.is_file():
        # Fallback check relative to module or stage directory
        for candidate in [MODULE_DIR / path, STAGE_DIR / path]:
            if candidate.is_file():
                file_path = candidate.resolve()
                break
        else:
            raise FileNotFoundError(f"Ontology file not found at: {path}")

    with open(file_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict):
        raise ValueError("Ontology root must be a mapping/dict.")

    loaded_slugs = set(data.keys())
    if loaded_slugs != STANDARD_VULNERABILITY_SLUGS:
        missing = STANDARD_VULNERABILITY_SLUGS - loaded_slugs
        extra = loaded_slugs - STANDARD_VULNERABILITY_SLUGS
        raise ValueError(
            f"Invalid ontology slugs! Expected exactly {STANDARD_VULNERABILITY_SLUGS}. "
            f"Missing: {missing}, Extra: {extra}"
        )

    # Track aliases across all entries to prevent cross-slug collisions
    alias_to_slug: Dict[str, str] = {}

    for slug, entry in data.items():
        if not isinstance(entry, dict):
            raise ValueError(f"Entry for slug '{slug}' must be a dictionary.")

        # Check required fields
        for field in REQUIRED_FIELDS:
            if field not in entry:
                raise ValueError(f"Slug '{slug}' is missing required field: '{field}'")

        if not isinstance(entry["canonical"], str) or not entry["canonical"].strip():
            raise ValueError(f"Slug '{slug}' field 'canonical' must be a non-empty string.")

        for list_field in ["aliases", "cwe", "subtypes", "related_terms"]:
            if not isinstance(entry[list_field], list):
                raise ValueError(f"Slug '{slug}' field '{list_field}' must be a list.")

        # Validate aliases
        for alias in entry["aliases"]:
            cleaned_alias = str(alias).strip().lower()
            if not cleaned_alias:
                continue

            # Check L7 rule: do not use overly short/ambiguous alias like "bac"
            if cleaned_alias == "bac":
                raise ValueError("Alias 'bac' is forbidden due to ambiguity (Contract v1.0 L7).")

            if cleaned_alias in alias_to_slug:
                existing_slug = alias_to_slug[cleaned_alias]
                raise ValueError(
                    f"Alias collision detected! Alias '{alias}' in slug '{slug}' "
                    f"conflicts with slug '{existing_slug}'."
                )
            alias_to_slug[cleaned_alias] = slug

    return data
