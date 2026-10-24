"""
Ontology Loader and Validator (Contract v1.0).
Maintainer: Long (Query Understanding Lead)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict, Set

import yaml

AGY_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = AGY_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(AGY_DIR) not in sys.path:
    sys.path.insert(0, str(AGY_DIR))

try:
    from stage_2.config import STANDARD_VULNERABILITY_SLUGS
except ImportError:
    from config import STANDARD_VULNERABILITY_SLUGS

DEFAULT_ONTOLOGY_PATH = (AGY_DIR / "ontology.yaml").resolve()
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
    file_path = Path(path)
    if not file_path.is_file():
        raise FileNotFoundError(f"Ontology file not found at: {file_path}")

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
