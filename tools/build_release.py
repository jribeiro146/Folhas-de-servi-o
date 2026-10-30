"""Package a tested clean Git HEAD into a reproducible ZIP, without importing the app."""
from pathlib import Path
import argparse
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.services.release_identity import ReleaseIdentityError
from src.services.release_package import build_release
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=ROOT, help="Clean tested Git checkout")
    parser.add_argument("--revision", required=True, help="Full Git SHA, exactly matching HEAD")
    parser.add_argument("--output", type=Path, required=True, help="New ZIP file in an existing directory")
    args = parser.parse_args()
    try:
        info = build_release(args.repo, args.revision, args.output)
    except (ValueError, OSError, ReleaseIdentityError, subprocess.TimeoutExpired) as exc:
        parser.exit(2, str(exc) + "\n")
    print(json.dumps(info))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
