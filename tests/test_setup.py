import json
import os
import stat

import pytest

from shopclaw import setup as wizard


def test_claude_desktop_config_path_per_os(monkeypatch, tmp_path):
    monkeypatch.setattr(wizard.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(wizard.platform, "system", lambda: "Darwin")
    assert str(wizard.claude_desktop_config_path()).endswith(
        "Library/Application Support/Claude/claude_desktop_config.json"
    )
    monkeypatch.setattr(wizard.platform, "system", lambda: "Windows")
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    assert wizard.claude_desktop_config_path() == tmp_path / "Roaming" / "Claude" / "claude_desktop_config.json"
    monkeypatch.setattr(wizard.platform, "system", lambda: "Linux")
    assert str(wizard.claude_desktop_config_path()).endswith(".config/Claude/claude_desktop_config.json")


def test_env_file_roundtrip_merges_and_keeps_other_keys(tmp_path):
    path = tmp_path / "sub" / ".env"
    wizard.write_env_file(path, {"A": "1", "B": "two"})
    wizard.write_env_file(path, {"B": "three", "C": "4"})
    assert wizard.read_env_file(path) == {"A": "1", "B": "three", "C": "4"}


@pytest.mark.skipif(os.name != "posix", reason="permission bits are POSIX only")
def test_env_file_is_private(tmp_path):
    path = tmp_path / ".env"
    wizard.write_env_file(path, {"SECRET": "x"})
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_read_env_file_ignores_comments_and_quotes(tmp_path):
    path = tmp_path / ".env"
    path.write_text('# comment\n\nA="quoted"\nB=plain\nnot a pair\n', encoding="utf-8")
    assert wizard.read_env_file(path) == {"A": "quoted", "B": "plain"}
    assert wizard.read_env_file(tmp_path / "missing") == {}


def test_merge_desktop_config_creates_file(tmp_path):
    path = tmp_path / "Claude" / "claude_desktop_config.json"
    backup = wizard.merge_desktop_config(path, "/usr/bin/python3", ["-m", "shopclaw"])
    assert backup is None
    data = json.loads(path.read_text())
    assert data["mcpServers"]["shopclaw"] == {"command": "/usr/bin/python3", "args": ["-m", "shopclaw"]}


def test_merge_desktop_config_preserves_other_servers_and_backs_up(tmp_path):
    path = tmp_path / "claude_desktop_config.json"
    original = {"theme": "dark", "mcpServers": {"other": {"command": "x"}}}
    path.write_text(json.dumps(original), encoding="utf-8")

    backup = wizard.merge_desktop_config(path, "/py", ["-m", "shopclaw"])

    assert backup is not None and json.loads(backup.read_text()) == original
    data = json.loads(path.read_text())
    assert data["theme"] == "dark"
    assert data["mcpServers"]["other"] == {"command": "x"}
    assert data["mcpServers"]["shopclaw"]["command"] == "/py"


def test_merge_desktop_config_replaces_old_entry_without_secrets(tmp_path):
    path = tmp_path / "c.json"
    path.write_text(json.dumps({"mcpServers": {"shopclaw": {"command": "old", "env": {"X": "1"}}}}))
    wizard.merge_desktop_config(path, "/new", ["-m", "shopclaw"])
    entry = json.loads(path.read_text())["mcpServers"]["shopclaw"]
    assert entry == {"command": "/new", "args": ["-m", "shopclaw"]}
    assert "env" not in entry


def test_merge_desktop_config_rejects_broken_json(tmp_path):
    path = tmp_path / "c.json"
    path.write_text("{ not json", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        wizard.merge_desktop_config(path, "/py", ["-m", "shopclaw"])
    assert path.read_text() == "{ not json"  # untouched


def test_run_check_reports_missing_values(capsys):
    assert wizard.run_check({"GOOGLE_ADS_CLIENT_ID": "x"}) == 1
    assert "Missing saved values" in capsys.readouterr().out


def test_run_check_success_and_failure(monkeypatch, capsys):
    values = {
        "GOOGLE_ADS_DEVELOPER_TOKEN": "t",
        "GOOGLE_ADS_CLIENT_ID": "i",
        "GOOGLE_ADS_CLIENT_SECRET": "s",
        "GOOGLE_ADS_REFRESH_TOKEN": "r",
    }
    monkeypatch.setattr(wizard, "check_access", lambda v: ["1234567890", "1112223333"])
    assert wizard.run_check(values) == 0
    assert "2 account(s) reachable" in capsys.readouterr().out

    def boom(v):
        raise RuntimeError("invalid_grant: Token has been expired or revoked")

    monkeypatch.setattr(wizard, "check_access", boom)
    assert wizard.run_check(values) == 1
    assert "--reauth" in capsys.readouterr().out


def test_server_loads_home_env_without_overriding_process_env(monkeypatch, tmp_path):
    from shopclaw import config

    monkeypatch.setattr(config.Path, "home", classmethod(lambda cls: tmp_path))
    (tmp_path / ".shopclaw").mkdir()
    (tmp_path / ".shopclaw" / ".env").write_text("SHOPCLAW_TEST_A=from_file\nSHOPCLAW_TEST_B=from_file\n")
    monkeypatch.setenv("SHOPCLAW_TEST_B", "from_process")
    monkeypatch.delenv("SHOPCLAW_TEST_A", raising=False)
    monkeypatch.chdir(tmp_path)

    config.load_env_files()

    assert os.environ["SHOPCLAW_TEST_A"] == "from_file"
    assert os.environ["SHOPCLAW_TEST_B"] == "from_process"
    monkeypatch.delenv("SHOPCLAW_TEST_A", raising=False)
