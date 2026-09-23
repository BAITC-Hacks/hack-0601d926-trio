import argparse
import json
import logging
from pathlib import Path

from .demo import write_demo
from .pipeline import ROOT, run_pipeline
from .validation import DataValidationError


def main() -> None:
    parser = argparse.ArgumentParser(description="Воспроизводимый локальный pipeline «Граф денег»")
    parser.add_argument("--data", type=Path, default=ROOT / "data")
    parser.add_argument("--out", type=Path, default=ROOT / "outputs")
    parser.add_argument("--generate-demo", type=Path, help="Создать отдельные синтетические parquet и завершить; не перезаписывает файлы")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.generate_demo:
        write_demo(args.generate_demo)
        return
    try:
        result = run_pipeline(args.data, args.out)
    except DataValidationError as exc:
        parser.exit(2, f"Ошибка входных данных: {exc}\n")
    print(json.dumps({"source": result.source, "duration_seconds": round(result.duration_seconds, 3), "stats": result.stats}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
