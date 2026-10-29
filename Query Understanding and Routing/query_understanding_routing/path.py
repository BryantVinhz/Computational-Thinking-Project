"""Path configuration alias (compatible with project path conventions)."""

try:
    from .paths import (
        AGY_DIR,
        DATA_DIR,
        DEFAULT_ONTOLOGY_PATH,
        ONTOLOGY_PATH,
        PACKAGE_DIR,
        PROJECT_ROOT,
        STAGE_DIR,
        stage2_DIR,
    )
except ImportError:
    from paths import (  # type: ignore
        AGY_DIR,
        DATA_DIR,
        DEFAULT_ONTOLOGY_PATH,
        ONTOLOGY_PATH,
        PACKAGE_DIR,
        PROJECT_ROOT,
        STAGE_DIR,
        stage2_DIR,
    )

__all__ = [
    "AGY_DIR",
    "DATA_DIR",
    "DEFAULT_ONTOLOGY_PATH",
    "ONTOLOGY_PATH",
    "PACKAGE_DIR",
    "PROJECT_ROOT",
    "STAGE_DIR",
    "stage2_DIR",
]
