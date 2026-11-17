import sys
from pathlib import Path
import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from path import DEFAULT_ONTOLOGY_PATH as ONTOLOGY_PATH

def load_ontology_terms(path: Path = ONTOLOGY_PATH):
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    terms, labels = {}, {}
    for slug, e in data.items():
        labels[slug] = e["canonical"]
        english_aliases = [a for a in e["aliases"] if a.isascii()]
        terms[slug] = list(dict.fromkeys([e["canonical"], *english_aliases, *e["cwe"]]))
    return terms, labels