"""Check normalized `docker compose config --format json`; emit only safe fields.

Feed synthetic configuration only. This checks the delivery topology, not server
credentials, host permissions, image contents or application readiness.
"""
import argparse
import json
import sys


def require(condition):
    if not condition:
        raise ValueError("Compose configuration differs from the delivery contract")


def validate(config, expected_image):
    services = config["services"]
    require(set(services) == {"app", "worker", "prepare"})
    prepare = services["prepare"]
    require(prepare["profiles"] == ["maintenance"] and prepare["network_mode"] == "none")
    require(not prepare.get("env_file") and not prepare.get("environment"))
    require(prepare["command"] == ["python", "-B", "tools/prepare_runtime.py", "init", "--data-dir", "/app/data"])
    summary = {}
    for name, service in services.items():
        require(service["image"] == expected_image)
        mounts = service["volumes"]
        targets = sorted(v["target"] for v in mounts)
        require(targets == (["/app/data"] if name == "prepare" else ["/app/data", "/app/logs"]))
        data = next(v for v in mounts if v["target"] == "/app/data")
        require(data["type"] == "volume" and data["source"] == "app-data")
        require("prepare" not in service.get("depends_on", {}))
        summary[name] = {"image": expected_image, "targets": targets}
        if name != "prepare":
            logs = next(v for v in mounts if v["target"] == "/app/logs")
            # Compose 2.27 / compose-go 2.1 serializes this bool with omitempty.
            # In its NORMALIZED JSON, absent means false. Short bind syntax
            # normalizes to explicit true and is rejected, as are strings/null.
            require(logs["type"] == "bind")
            require(logs.get("bind", {}).get("create_host_path", False) is False)
            summary[name]["create_host_path"] = False
    summary["prepare"]["network_mode"] = "none"
    return {"status": "PASS", "services": summary}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-image", required=True)
    args = parser.parse_args()
    try:
        result = validate(json.load(sys.stdin), args.expected_image)
    except (ValueError, TypeError, KeyError, AttributeError, StopIteration):
        # Do not echo arbitrary input, which could contain expanded credentials.
        print('{"status":"FAIL","error":"compose_contract_mismatch"}', file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
