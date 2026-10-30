"""Start a loopback-only test app with new synthetic data on every launch."""

import argparse
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.test_runtime import configure, block_outbound_network, seed_synthetic_data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=5012)
    parser.add_argument("--check", action="store_true", help="Validate locally without starting a server")
    args = parser.parse_args()
    root = configure()
    block_outbound_network()
    sources, draft = seed_synthetic_data()
    from src.web.application import create_app
    from src.services.editing_state_service import EditorIdentity
    app = create_app(test_editor_identity=EditorIdentity("demo:technician", "Técnico de teste"))

    manifest = {"mode": "test", "data": str(root), "url": f"http://127.0.0.1:{args.port}",
        "synthetic_originals": len(sources), "synthetic_drafts": 1,
        "outbound_network": "blocked", "mail": "disabled", "teams": "disabled", "workers": "disabled"}
    (root / "test-version.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False), flush=True)
    if args.check:
        with app.test_client() as client:
            assert client.get("/").status_code == 200
            assert client.get("/api/files").status_code == 200
            assert client.get(f"/api/file/{draft.stem}/bootstrap?client_id=qa").status_code == 200
        print("CHECK_OK", flush=True)
        return
    app.run(host="127.0.0.1", port=args.port, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
