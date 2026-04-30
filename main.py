"""CLI entrypoint for Tasklight."""

import argparse
from pathlib import Path

from tasklight.app import run


def main() -> None:
    parser = argparse.ArgumentParser(description="Tasklight agent monitor")
    parser.add_argument(
        "--config",
        "-c",
        type=Path,
        default=Path("tasklight.yaml"),
        metavar="PATH",
        help="Config file (default: ./tasklight.yaml)",
    )
    args = parser.parse_args()
    raise SystemExit(run(args.config))


if __name__ == "__main__":
    main()
