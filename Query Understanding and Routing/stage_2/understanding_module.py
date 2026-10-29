"""Compatibility shim for stage_2.understanding_module."""
import sys
from pathlib import Path

_stage_dir = str(Path(__file__).resolve().parent.parent)
if _stage_dir not in sys.path:
    sys.path.insert(0, _stage_dir)

from query_understanding_routing.understanding_module import *
