"""Shared fixtures for devin-keysmith tests.

The engine ships as `devin-keysmith.py` (hyphen in the filename), so it is
loaded by path rather than imported by name — once, at conftest import, so
every fixture and test shares the SAME module instance (monkeypatching module
attributes in tests must affect the code under test).

Every test runs against fixture roots via the override environment variables;
the suite never touches real Devin state, and the Devin CLI is replaced by a
recording fake.
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

FAKE_DEVIN_PY = r'''\
import json, os, sys
args = sys.argv[1:]
with open(os.environ["FAKE_DEVIN_LOG"], "a", encoding="utf-8") as handle:
    handle.write(json.dumps(args) + "\n")
exit_override = os.environ.get("FAKE_DEVIN_EXIT")
if exit_override:
    sys.stderr.write(os.environ.get("FAKE_DEVIN_STDERR", "fake devin failure"))
    sys.exit(int(exit_override))
state_path = os.environ["FAKE_DEVIN_STATE"]
state = {"plugins": []}
if os.path.exists(state_path):
    with open(state_path, encoding="utf-8") as handle:
        state = json.load(handle)
if not args or args[0] == "--version":
    print("devin 3000.10.21 (fake)")
    sys.exit(0)
if args[:2] == ["plugins", "list"]:
    print("Installed plugins")
    for plugin in state["plugins"]:
        print("  - %s v9.9.9" % plugin)
    sys.exit(0)
if args[:2] == ["plugins", "install"]:
    if os.environ.get("FAKE_DEVIN_GHOST"):
        print("installed ghost")
        sys.exit(0)
    source = args[2]
    manifest_path = os.path.join(source, ".devin-plugin", "plugin.json")
    with open(manifest_path, encoding="utf-8") as handle:
        name = json.load(handle)["name"]
    if name not in state["plugins"]:
        state["plugins"].append(name)
    with open(state_path, "w", encoding="utf-8") as handle:
        json.dump(state, handle)
    print("installed %s" % name)
    sys.exit(0)
if args[:2] == ["plugins", "remove"]:
    name = args[2]
    if name in state["plugins"]:
        state["plugins"].remove(name)
    with open(state_path, "w", encoding="utf-8") as handle:
        json.dump(state, handle)
    print("removed %s" % name)
    sys.exit(0)
print("unknown command: %s" % args, file=sys.stderr)
sys.exit(64)
'''

_spec = importlib.util.spec_from_file_location("devin_keysmith_main", REPO / "devin-keysmith.py")
keysmith = importlib.util.module_from_spec(_spec)
sys.modules["devin_keysmith_main"] = keysmith
_spec.loader.exec_module(keysmith)


@pytest.fixture(scope="session")
def keysmith_module():
    return keysmith


class FixtureEnv:
    def __init__(self, home: Path, skills_root: Path, log: Path, state: Path,
                 binary: Path, plugins_cache: Path):
        self.home = home
        self.skills_root = skills_root
        self.log = log
        self.state = state
        self.binary = binary
        self.plugins_cache = plugins_cache

    def cli(self, *argv: str) -> int:
        return keysmith.main(list(argv))

    def cli_text(self, *argv: str):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = self.cli(*argv)
        return code, buffer.getvalue()

    def cli_json(self, *argv: str):
        code, text = self.cli_text(*argv)
        return code, json.loads(text)

    def read_log(self):
        if not self.log.exists():
            return []
        rows = self.log.read_text(encoding="utf-8").strip().splitlines()
        return [json.loads(row) for row in rows if row]

    def read_state(self):
        if not self.state.exists():
            return {"plugins": []}
        return json.loads(self.state.read_text(encoding="utf-8"))


@pytest.fixture()
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FixtureEnv:
    home = tmp_path / "home"
    skills_root = tmp_path / "skills"
    home.mkdir()
    skills_root.mkdir()

    fake_dir = tmp_path / "fake-bin"
    fake_dir.mkdir()
    fake_py = fake_dir / "fake_devin.py"
    fake_py.write_text(FAKE_DEVIN_PY, encoding="utf-8")

    if os.name == "nt":
        binary = fake_dir / "devin.cmd"
        binary.write_text(
            '@python "%s" %%*\n' % fake_py, encoding="utf-8"
        )
    else:
        binary = fake_dir / "devin"
        binary.write_text(
            '#!/bin/sh\nexec python3 "%s" "$@"\n' % fake_py, encoding="utf-8"
        )
        binary.chmod(0o755)

    log = fake_dir / "calls.log"
    state = fake_dir / "plugins-state.json"

    monkeypatch.setenv("DEVIN_KEYSMITH_HOME", str(home))
    monkeypatch.setenv("DEVIN_KEYSMITH_SKILLS_ROOT", str(skills_root))
    monkeypatch.setenv("DEVIN_KEYSMITH_DEVIN_BIN", str(binary))
    monkeypatch.setenv("FAKE_DEVIN_LOG", str(log))
    monkeypatch.setenv("FAKE_DEVIN_STATE", str(state))
    plugins_cache = tmp_path / "plugins-cache"
    monkeypatch.setenv("DEVIN_KEYSMITH_PLUGINS_CACHE", str(plugins_cache))
    monkeypatch.delenv("FAKE_DEVIN_EXIT", raising=False)
    monkeypatch.delenv("FAKE_DEVIN_GHOST", raising=False)

    return FixtureEnv(home, skills_root, log, state, binary, plugins_cache)
