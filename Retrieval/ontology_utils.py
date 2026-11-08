from pathlib import Path
import yaml

ONTOLOGY_PATH = (Path(__file__).resolve().parent.parent
                 / "Query Understanding and Routing"
                 / "query_understanding_routing" / "ontology.yaml")

def load_ontology_terms(path: Path = ONTOLOGY_PATH):
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    terms, labels = {}, {}
    for slug, e in data.items():
        labels[slug] = e["canonical"]
        english_aliases = [a for a in e["aliases"] if a.isascii()]
        terms[slug] = list(dict.fromkeys([e["canonical"], *english_aliases, *e["cwe"]]))
    return terms, labels