"""Read the independent worker channel without loading .env, web or a queue."""
import argparse
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.services.worker_health import read_health, safe_directory, storage_id


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--health-dir", required=True)
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--expected-sha256")
    args = parser.parse_args(argv)
    if args.expected_sha256 and not re.fullmatch(r"[0-9a-f]{64}", args.expected_sha256):
        parser.error("--expected-sha256 exige um SHA256 completo.")
    try:
        directory = safe_directory(args.health_dir)
        result = read_health(directory, storage_id(args.data_dir), expected_sha256=args.expected_sha256)
    except (OSError, ValueError):
        result = {"state": "configuration_invalid"}
    print(json.dumps(result, ensure_ascii=True, allow_nan=False))
    return 0 if result["state"] in {"idle", "cycling"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
