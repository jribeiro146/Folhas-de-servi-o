from pathlib import Path

import src.config as config


def test_detect_default_base_path_prefers_parent_with_excel(monkeypatch, tmp_path):
    project_root = tmp_path / "project"
    exe_dir = project_root / "dist" / "FolhasServico"
    (project_root / "Excel").mkdir(parents=True)
    exe_dir.mkdir(parents=True)

    monkeypatch.setattr(config.sys, "frozen", True, raising=False)
    monkeypatch.setattr(config.sys, "executable", str(exe_dir / "FolhasServico.exe"))
    monkeypatch.chdir(exe_dir)

    detected = config._detect_default_base_path()

    assert detected == project_root
