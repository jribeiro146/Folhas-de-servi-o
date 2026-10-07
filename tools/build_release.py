"""Create a source manifest for a fixed release, without importing the app."""
from pathlib import Path
import argparse
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.services.release_identity import ReleaseIdentityError, write_release_manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--revision", required=True, help="Full Git commit SHA from the release builder")
    args = parser.parse_args()
    try:
        info = write_release_manifest(args.root, revision=args.revision)
    except (ValueError, OSError, ReleaseIdentityError) as exc:
        parser.exit(2, str(exc) + "\n")
    print(json.dumps(info))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
