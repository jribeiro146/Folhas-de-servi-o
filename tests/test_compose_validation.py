"""Exercise the public verifier with normalized Compose JSON, without Docker."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


def configuration():
    services = {}
    for name in ("app", "worker", "prepare"):
        services[name] = {
            "image": "synthetic:verified",
            "volumes": [{"type": "volume", "source": "app-data", "target": "/app/data"}],
        }
        if name != "prepare":
            services[name]["volumes"].append({
                "type": "bind", "source": "/tmp/synthetic-logs", "target": "/app/logs",
                "bind": {"create_host_path": False},
            })
    services["prepare"].update({
        "profiles": ["maintenance"], "network_mode": "none",
        "command": ["python", "-B", "tools/prepare_runtime.py", "init", "--data-dir", "/app/data"],
    })
    return {"services": services}


def invoke(config, version=""):
    return subprocess.run(
        [sys.executable, "-B", str(ROOT / "tools/check_compose.py"),
         "--expected-image", "synthetic:verified", "--compose-version", version],
        input=json.dumps(config), text=True, capture_output=True, timeout=10,
    )


@pytest.mark.parametrize("legacy_json", [False, True])
def test_accepts_explicit_false_and_compose_227_omitted_false(legacy_json):
    config = configuration()
    if legacy_json:
        # compose-go 2.1 uses omitempty for this bool (including the empty bind).
        for name in ("app", "worker"):
            del config["services"][name]["volumes"][1]["bind"]
    result = invoke(config, "2.27.0" if legacy_json else "")
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["status"] == "PASS"
    assert report["services"]["app"]["create_host_path"] is False


@pytest.mark.parametrize("version", ["", "new-version", "2.27.0", "v2.27.0"])
@pytest.mark.parametrize("empty_bind", [False, True])
def test_omission_is_accepted_only_for_confirmed_227_serialization(version, empty_bind):
    config = configuration()
    for name in ("app", "worker"):
        logs = config["services"][name]["volumes"][1]
        if empty_bind:
            logs["bind"] = {}
        else:
            del logs["bind"]
    result = invoke(config, version)
    assert result.returncode == (0 if version in {"2.27.0", "v2.27.0"} else 2)


@pytest.mark.parametrize("unsafe", [True, "false", 0, None])
def test_refuses_bind_creation_or_invalid_bool_without_echoing_configuration(unsafe):
    config = configuration()
    config["services"]["app"]["volumes"][1]["bind"]["create_host_path"] = unsafe
    config["services"]["app"]["environment"] = {"PRIVATE_VALUE": "do-not-echo-this-sentinel"}
    result = invoke(config)
    assert result.returncode == 2
    assert "do-not-echo-this-sentinel" not in result.stdout + result.stderr
    assert json.loads(result.stderr)["error"] == "compose_contract_mismatch"


@pytest.mark.parametrize("change", ["image", "network", "environment", "data", "code", "autostart"])
def test_refuses_wrong_image_or_operational_mounts_and_prepare_side_effects(change):
    config = configuration()
    app, prepare = config["services"]["app"], config["services"]["prepare"]
    if change == "image":
        app["image"] = "wrong:version"
    elif change == "network":
        prepare["network_mode"] = "host"
    elif change == "environment":
        prepare["environment"] = {"FS_SECRET_KEY": "synthetic-not-permitted-here"}
    elif change == "data":
        app["volumes"][0]["source"] = "operational-data"
    elif change == "code":
        app["volumes"].append({"type": "bind", "source": "/code", "target": "/opt/folhas"})
    else:
        app["depends_on"] = {"prepare": {"condition": "service_completed_successfully"}}
    assert invoke(config).returncode == 2
