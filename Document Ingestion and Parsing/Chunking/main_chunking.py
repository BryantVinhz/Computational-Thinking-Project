import json
import logging
from pathlib import Path
import sys
from factory import ChunkingStrategyFactory
from chunking import ChunkingService

CURRENT_DIR = Path(__file__).resolve().parent
PARENT_DIR = CURRENT_DIR.parent
for p in (CURRENT_DIR, PARENT_DIR):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from path import NORMALIZATION_OUT_DIR, CHUNKING_OUT_DIR

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

STRATEGY_FOLDER_MAP = {
    "fixed_size": "Fixed size",
    "structure_aware": "Structure aware",
    "semantic": "Semantic",
}

def normalize_strategy_name(strategy_name: str) -> str:
    return strategy_name.strip().lower().replace("-", "_")

def get_strategy_chunk_dir(strategy_name: str) -> Path:
    norm = normalize_strategy_name(strategy_name)
    folder_name = STRATEGY_FOLDER_MAP.get(norm, norm.replace("_", " ").capitalize())
    return CHUNKING_OUT_DIR / folder_name

def save_chunking_file(output_schema, dest_path: Path) -> None:
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    with dest_path.open("w", encoding="utf-8") as f:
        json.dump(output_schema.to_dict(), f, ensure_ascii=False, indent=2)


def process_single_file(file_path: Path, strategy_name: str, max_tokens: int, overlap_tokens: int) -> bool:
    try:
        """Read block data from the Normalization JSON file"""
        with file_path.open("r", encoding="utf-8") as f:
            blocks = json.load(f)

        if not isinstance(blocks, list) or not blocks:
            raise ValueError(f"File {file_path.name} does not contain a valid sequence of blocks")
        
        ver_id = blocks[0].get("version_id", file_path.stem.replace("_normalization", ""))
        norm_strategy = normalize_strategy_name(strategy_name)
        
        strategy = ChunkingStrategyFactory.create(
            norm_strategy,
            max_tokens=max_tokens,
            overlap_tokens=overlap_tokens,
            similarity_threshold=0.78,
            breakpoint_percentile=80.0,
            overlap_sentences=1,
        )
        service = ChunkingService(strategy=strategy, validate=True)
        output = service.run(blocks)

        # File output format: {version_id}_{strategy}.json
        out_dir = get_strategy_chunk_dir(norm_strategy)
        output_file_name = f"{ver_id}_{norm_strategy}.json"
        dest_file_path = out_dir / output_file_name
        save_chunking_file(output, dest_file_path)

        logger.info(
            f"Completed {file_path.name} -> {output_file_name} "
            f"({len(output.chunks)} chunks, {len(output.chunk_sources)} sources)"
        )
        return True
    except Exception as exc:
        logger.error(f"Failed to process file {file_path.name}: {exc}")
        return False


def run_pipeline(strategy_name: str, max_tokens: int = 650, overlap_tokens: int = 50):
    out_dir = get_strategy_chunk_dir(strategy_name)
    out_dir.mkdir(parents=True, exist_ok=True)

    input_files = sorted(NORMALIZATION_OUT_DIR.glob("*.json"))
    if not input_files:
        logger.warning(f"No JSON file found in folder: {NORMALIZATION_OUT_DIR}")
        return

    logger.info("=" * 50)
    logger.info("CHUNKING PROCESS")
    logger.info(f"- Folder Input  (step 3) : {NORMALIZATION_OUT_DIR}")
    logger.info(f"- Folder Output (step 4)    : {out_dir}")
    logger.info(f"- Strategy                : {strategy_name} (max_tokens={max_tokens})")
    logger.info(f"- Total file              : {len(input_files)}")
    logger.info("=" * 50)

    success_count = 0
    for file_path in input_files:
        if process_single_file(file_path, strategy_name=strategy_name, max_tokens=max_tokens, overlap_tokens=overlap_tokens):
            success_count += 1

    logger.info("=" * 50)
    logger.info(f"Done: {success_count}/{len(input_files)} file have been processed successfully")
    logger.info("=" * 50)


if __name__ == "__main__":
    run_pipeline(strategy_name="fixed_size", max_tokens=650, overlap_tokens=50)
    run_pipeline(strategy_name="structure_aware", max_tokens=650, overlap_tokens=50)
    run_pipeline(strategy_name="semantic", max_tokens=650, overlap_tokens=0)