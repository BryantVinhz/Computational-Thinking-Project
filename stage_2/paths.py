"""
Directory and Path Configuration for Query Understanding & Routing (agy).
"""

from __future__ import annotations

import sys
from pathlib import Path

AGY_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = AGY_DIR.parent
DATA_DIR = (PROJECT_ROOT / "Data").resolve()
DEFAULT_ONTOLOGY_PATH = (AGY_DIR / "ontology.yaml").resolve()
ONTOLOGY_PATH = DEFAULT_ONTOLOGY_PATH

# Ensure both PROJECT_ROOT and AGY_DIR are present in sys.path for seamless imports
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(AGY_DIR) not in sys.path:
    sys.path.insert(0, str(AGY_DIR))
