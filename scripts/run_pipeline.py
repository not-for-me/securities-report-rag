from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow direct script execution: `python scripts/run_pipeline.py ...`
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="증권사 리포트 배치 파이프라인 실행")
    parser.add_argument(
        "--pdf",
        dest="pdfs",
        action="append",
        default=[],
        help="처리할 PDF 파일 경로 (여러 번 지정 가능)",
    )
    return parser.parse_args()


def main() -> None:
    from src.config import get_settings
    from src.logging_utils import configure_logging
    from src.pipeline.runner import build_default_pipeline_runner

    args = parse_args()
    settings = get_settings()
    settings.validate_pipeline_settings()
    configure_logging(level=settings.log_level)

    runner = build_default_pipeline_runner(settings)
    targets = [Path(path) for path in args.pdfs] if args.pdfs else None
    result = runner.run(pdf_paths=targets)

    print(f"Pipeline finished: total={result.total}, success={result.success_count}, failed={result.failed_count}")
    for failed in result.failed_files:
        print(f"- failed: {failed['file']} ({failed['error']})")

    if result.failed_count > 0:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
