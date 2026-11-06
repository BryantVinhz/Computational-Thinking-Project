"""Directory and path configuration for Query Understanding & Routing."""

from __future__ import annotations

import os
import sys
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
STAGE_DIR = PACKAGE_DIR.parent
PROJECT_ROOT = STAGE_DIR.parent

DATA_DIR = Path(os.getenv("DATA_DIR", str(PROJECT_ROOT / "Data"))).resolve()
DEFAULT_ONTOLOGY_PATH = (PACKAGE_DIR / "ontology.yaml").resolve()
ONTOLOGY_PATH = Path(os.getenv("ONTOLOGY_PATH", str(DEFAULT_ONTOLOGY_PATH))).resolve()

AGY_DIR = PACKAGE_DIR
stage2_DIR = PACKAGE_DIR

for p in [PROJECT_ROOT, STAGE_DIR, PACKAGE_DIR]:
    p_str = str(p)
    if p_str not in sys.path:
        sys.path.insert(0, p_str)
