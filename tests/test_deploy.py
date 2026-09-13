"""devin-keysmith deploy tests — spec scenarios from
openspec/changes/add-devin-keysmith/specs/devin-instruction-deploy/spec.md.

All tests run against fixture roots (override env vars) and a fake Devin CLI;
no real Devin state is touched.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from conftest import keysmith

BLOCK_START_TOKEN = "devin-keysmith:block:start"


def run_ok(env, *argv):
    code = env.cli(*argv)
    assert code == 0, "expected exit 0 for %r" % (argv,)
    return code


def run_fail(env, code_expected, *argv):
    code = env.cli(*argv)
    assert code == code_expected, "expected exit %d for %r, got %d" % (
        code_expected, argv, code,
    )
    return code


# ---------------------------------------------------------------------------
# Deploy target selection
# ---------------------------------------------------------------------------


class TestModeSelection:
    def test_default_target_is_global_rule(self, env):
        code, payload = env.cli_json("install", "--json")
        assert code == 0
        plan = payload["plan"]
        assert plan["mode"] == "global"
        assert plan["files"][0]["relative"].endswith("global_rules.md")
        assert ".codeium" in plan["files"][0]["path"]

    def test_append_target_explicit(self, env):
        code, payload = env.cli_json("install", "--mode", "append", "--json")
        assert code == 0
        plan = payload["plan"]
        assert plan["mode"] == "append"
        assert plan["files"][0]["relative"] == "AGENTS.md"
        assert plan["files"][0]["path"].startswith(str(env.home))

    def test_plugin_preview_reports_scaffold_and_command(self, env):
        code, payload = env.cli_json("install", "--mode", "plugin", "--json")
        assert code == 0
        plan = payload["plan"]
        relatives = [f["relative"] for f in plan["files"]]
        assert any(r.endswith(".devin-plugin/plugin.json") for r in relatives)
        assert any(r.endswith("AGENTS.md") for r in relatives)
        assert plan["plugin_name"] == "devin-keysmith-instruction"
        assert plan["register_command"][1:] == [
            "plugins", "install", plan["scaffold_dir"], "--local", "-y",
        ]

    def test_project_mode_resolves_git_root(self, env, tmp_path):
        repo = tmp_path / "repo"
        deep = repo / "a" / "b" / "c"
        deep.mkdir(parents=True)
        (repo / ".git").mkdir()
        code, payload = env.cli_json(
            "install", "--mode", "project", "--cwd", str(deep), "--json"
        )
        assert code == 0
        assert payload["plan"]["files"][0]["path"] == str(repo / "AGENTS.md")

    def test_project_mode_worktree_git_file_counts(self, env, tmp_path):
        repo = tmp_path / "wt"
        (repo / "sub").mkdir(parents=True)
        (repo / ".git").write_text("gitdir: ../elsewhere\n", encoding="utf-8")
        code, payload = env.cli_json(
            "install", "--mode", "project", "--cwd", str(repo / "sub"), "--json"
        )
        assert code == 0
        assert payload["plan"]["files"][0]["path"] == str(repo / "AGENTS.md")

    def test_project_mode_without_git_root_fails(self, env, tmp_path):
        outside = tmp_path / "plain"
        outside.mkdir()
        run_fail(env, 1, "install", "--mode", "project", "--cwd", str(outside), "--yes")

    def test_skills_target_carries_name(self, env):
        code, payload = env.cli_json("install", "--mode", "skills", "--name", "my-instruction", "--json")
        assert code == 0
        plan = payload["plan"]
        assert plan["files"][0]["relative"].endswith("my-instruction/SKILL.md")
        assert plan["files"][0]["path"].startswith(str(env.skills_root))

    def test_invalid_skill_name_rejected(self, env):
        run_fail(env, 1, "install", "--mode", "skills", "--name", "../evil", "--yes")


# ---------------------------------------------------------------------------
# Marked-block append semantics
# ---------------------------------------------------------------------------


class TestAppendSemantics:
    def test_clean_append_creates_file(self, env):
        target = env.home / "AGENTS.md"
        assert not target.exists()
        run_ok(env, "install", "--mode", "append", "--yes")
        text = target.read_text(encoding="utf-8")
        assert text.count(BLOCK_START_TOKEN) == 1
        assert "You're Pier" in text  # bundled instruction deployed

    def test_existing_content_preserved(self, env):
        target = env.home / "AGENTS.md"
        original = "# My rules\nline-two\n"
        target.write_text(original, encoding="utf-8")
        run_ok(env, "install", "--mode", "append", "--yes")
        text = target.read_text(encoding="utf-8")
        assert text.startswith("# My rules\nline-two")
        assert BLOCK_START_TOKEN in text
        run_ok(env, "uninstall", "--yes")
        assert target.read_text(encoding="utf-8") == original

    def test_repeated_install_single_block(self, env):
        target = env.home / "AGENTS.md"
        target.write_text("# base\n", encoding="utf-8")
        run_ok(env, "install", "--mode", "append", "--yes")
        run_ok(env, "install", "--mode", "append", "--yes", "--force")
        text = target.read_text(encoding="utf-8")
        assert text.count(BLOCK_START_TOKEN) == 1
        assert text.count("devin-keysmith:block:end") == 1
        assert "# base" in text

    def test_block_replaced_in_place_without_manifest(self, env):
        # Manifest lost (or removed by hand): a fresh install must still
        # collapse to exactly one managed block.
        target = env.home / "AGENTS.md"
        run_ok(env, "install", "--mode", "append", "--yes")
        (env.home / ".devin-keysmith" / "manifest.json").unlink()
        run_ok(env, "install", "--mode", "append", "--yes")
        assert target.read_text(encoding="utf-8").count(BLOCK_START_TOKEN) == 1

    def test_uninstall_restores_exact_bytes(self, env):
        target = env.home / "AGENTS.md"
        original = "# original\r\nwith crlf\r\nand tail without newline"
        target.write_bytes(original.encode("utf-8"))
        run_ok(env, "install", "--mode", "append", "--yes")
        assert target.read_bytes() != original.encode("utf-8")
        run_ok(env, "uninstall", "--yes")
        assert target.read_bytes() == original.encode("utf-8")


# ---------------------------------------------------------------------------
# Plugin mode
# ---------------------------------------------------------------------------


class TestPluginMode:
    def test_scaffold_layout_and_registration(self, env):
        run_ok(env, "install", "--mode", "plugin", "--yes")
        scaffold = env.home / ".devin-keysmith" / "plugins" / "devin-keysmith-instruction"
        manifest_json = json.loads(
            (scaffold / ".devin-plugin" / "plugin.json").read_text(encoding="utf-8")
        )
        assert manifest_json["name"] == "devin-keysmith-instruction"
        assert manifest_json["version"] == "0.1.0"
        assert (scaffold / "AGENTS.md").is_file()

        calls = env.read_log()
        install_calls = [c for c in calls if c[:2] == ["plugins", "install"]]
        assert len(install_calls) == 1
        assert install_calls[0][2] == str(scaffold)
        assert install_calls[0][3:] == ["--local", "-y"]
        assert env.read_state()["plugins"] == ["devin-keysmith-instruction"]

    def test_include_skill_adds_skillmd(self, env):
        run_ok(env, "install", "--mode", "plugin", "--include-skill", "--yes")
        scaffold = env.home / ".devin-keysmith" / "plugins" / "devin-keysmith-instruction"
        skill = scaffold / "skills" / "devin-keysmith-instruction" / "SKILL.md"
        assert skill.is_file()
        assert skill.read_text(encoding="utf-8").startswith("---\nname:")

    def test_uninstall_deregisters_and_removes_scaffold(self, env):
        run_ok(env, "install", "--mode", "plugin", "--yes")
        scaffold = env.home / ".devin-keysmith" / "plugins" / "devin-keysmith-instruction"
        run_ok(env, "uninstall", "--yes")
        assert not scaffold.exists()
        assert env.read_state()["plugins"] == []
        calls = env.read_log()
        assert ["plugins", "remove", "devin-keysmith-instruction"] in calls

    def test_uninstall_tolerates_manual_deregistration(self, env):
        run_ok(env, "install", "--mode", "plugin", "--yes")
        # User removed the plugin via the CLI themselves.
        env.state.write_text(json.dumps({"plugins": []}), encoding="utf-8")
        run_ok(env, "uninstall", "--yes")
        scaffold = env.home / ".devin-keysmith" / "plugins" / "devin-keysmith-instruction"
        assert not scaffold.exists()

    def test_missing_binary_aborts_before_write(self, env, monkeypatch):
        monkeypatch.setenv("DEVIN_KEYSMITH_DEVIN_BIN", str(env.home / "nope.exe"))
        run_fail(env, 1, "install", "--mode", "plugin", "--yes")
        scaffold = env.home / ".devin-keysmith" / "plugins" / "devin-keysmith-instruction"
        assert not scaffold.exists()

    def test_cli_failure_leaves_no_scaffold_after_recover(self, env, monkeypatch):
        monkeypatch.setenv("FAKE_DEVIN_EXIT", "1")
        run_fail(env, 1, "install", "--mode", "plugin", "--yes")
        scaffold = env.home / ".devin-keysmith" / "plugins" / "devin-keysmith-instruction"
        assert scaffold.exists()  # written before registration failed
        assert env.read_state()["plugins"] == []
        monkeypatch.delenv("FAKE_DEVIN_EXIT")
        run_ok(env, "recover", "--yes")
        assert not scaffold.exists()
        code, payload = env.cli_json("status", "--json")
        assert payload["result"]["deployed"] is False
        assert payload["result"]["transaction_pending"] is False

    def test_ghost_registration_refused(self, env, monkeypatch):
        # CLI exits 0 but never lists the plugin: the deploy must be refused.
        monkeypatch.setenv("FAKE_DEVIN_GHOST", "1")
        run_fail(env, 1, "install", "--mode", "plugin", "--yes")


# ---------------------------------------------------------------------------
# Skills mode
# ---------------------------------------------------------------------------


class TestSkillsMode:
    def test_skill_document_has_frontmatter(self, env):
        run_ok(env, "install", "--mode", "skills", "--yes")
        skill = env.skills_root / "devin-keysmith-instruction" / "SKILL.md"
        text = skill.read_text(encoding="utf-8")
        assert text.startswith("---\n")
        assert "name: devin-keysmith-instruction" in text
        assert "description:" in text

    def test_custom_file_frontmatter_derived(self, env, tmp_path):
        source = tmp_path / "custom.md"
        source.write_text(
            "---\nname: my-name\ndescription: my-description\n---\n\nBody here.\n",
            encoding="utf-8",
        )
        run_ok(env, "install", "--mode", "skills", "--name", "custom-skill",
               "--file", str(source), "--yes")
        skill = env.skills_root / "custom-skill" / "SKILL.md"
        text = skill.read_text(encoding="utf-8")
        assert "name: custom-skill" in text          # explicit --name wins
        assert "description: my-description" in text  # derived from file
        assert "Body here." in text

    def test_unmanaged_files_in_skill_dir_left_alone(self, env):
        run_ok(env, "install", "--mode", "skills", "--yes")
        extra = env.skills_root / "devin-keysmith-instruction" / "EXTRA.md"
        extra.write_text("user data", encoding="utf-8")
        run_ok(env, "uninstall", "--yes")
        assert extra.read_text(encoding="utf-8") == "user data"
        assert not (env.skills_root / "devin-keysmith-instruction" / "SKILL.md").exists()

    def test_uninstall_prunes_empty_skill_dirs(self, env):
        run_ok(env, "install", "--mode", "skills", "--name", "probe", "--yes")
        run_ok(env, "uninstall", "--yes")
        assert not (env.skills_root / "probe").exists()


# ---------------------------------------------------------------------------
# Preview / manifest / guarantees
# ---------------------------------------------------------------------------


class TestGuarantees:
    def test_preview_writes_nothing_and_calls_nothing(self, env):
        code, _ = env.cli_json("install", "--mode", "plugin", "--json")
        assert code == 0
        assert not (env.home / "AGENTS.md").exists()
        assert not (env.home / ".devin-keysmith").exists()
        assert env.read_log() == []

    def test_existing_deployment_requires_force(self, env):
        run_ok(env, "install", "--mode", "append", "--yes")
        run_fail(env, 5, "install", "--yes")

    def test_force_replace_append_with_skills_leaves_no_orphan(self, env):
        target = env.home / "AGENTS.md"
        target.write_bytes(b"# before\n")  # pre-existing user rule, exact bytes
        run_ok(env, "install", "--mode", "append", "--yes")  # append block onto it
        assert BLOCK_START_TOKEN in target.read_text(encoding="utf-8")
        run_ok(env, "install", "--mode", "skills", "--force", "--yes")
        run_ok(env, "uninstall", "--yes")
        assert target.read_bytes() == b"# before\n"  # no block, no orphan
        assert not (env.skills_root / "devin-keysmith-instruction").exists()

    def test_backup_created_and_referenced(self, env):
        target = env.home / "AGENTS.md"
        target.write_text("# original\n", encoding="utf-8")
        run_ok(env, "install", "--mode", "append", "--yes")
        manifest = json.loads(
            (env.home / ".devin-keysmith" / "manifest.json").read_text(encoding="utf-8")
        )
        record = manifest["deployment"]["targets"][0]
        assert record["existed_before"] is True
        backup_rel = record["backup"]
        assert backup_rel
        backup = env.home / ".devin-keysmith" / backup_rel
        assert backup.is_file()
        assert backup.read_text(encoding="utf-8") == "# original\n"

    def test_atomic_write_failure_leaves_original(self, env, monkeypatch):
        import os as os_mod
        target = env.home / "AGENTS.md"
        target.write_text("# original\n", encoding="utf-8")
        real_replace = os_mod.replace

        def failing_replace(src, dst):
            raise OSError("simulated crash")

        monkeypatch.setattr(keysmith.os, "replace", failing_replace)
        try:
            with pytest.raises(OSError):
                keysmith.atomic_write_text(target, "# new content\n")
        finally:
            monkeypatch.setattr(keysmith.os, "replace", real_replace)
        assert target.read_text(encoding="utf-8") == "# original\n"
        leftovers = list(target.parent.glob(target.name + ".keysmith-tmp-*"))
        assert leftovers == []

    def test_protected_paths_rejected(self):
        for raw in (
            r"C:\Users\x\AppData\Roaming\devin\cli\plugins\lock.json",
            r"C:\Users\x\AppData\Local\devin\cli\bin\devin.exe",
            r"C:\Users\x\.devin\argv.json",
            r"C:\Users\x\AppData\Roaming\devin\credentials.toml",
            r"C:\Users\x\AppData\Roaming\devin\cli\sessions.db",
            r"C:\Users\x\.devin-shared\sharedStorage\state.vscdb",
            r"C:\Users\x\AppData\Local\devin\cli\model_configs_v5.abc.bin",
        ):
            with pytest.raises(keysmith.KeysmithError):
                keysmith.ensure_writable_target(Path(raw))

    def test_guard_blocks_skills_root_inside_cli_plugins(self, env, monkeypatch):
        poisoned = env.home / "AppData" / "Roaming" / "devin" / "cli" / "plugins"
        poisoned.mkdir(parents=True)
        monkeypatch.setenv("DEVIN_KEYSMITH_SKILLS_ROOT", str(poisoned))
        run_fail(env, 3, "install", "--mode", "skills", "--yes")

    def test_credentials_never_emitted(self, env):
        secret_file = env.home / "secret.md"
        secret_file.write_text(
            "rule with api_key = sk-abcdefghijklmnop1234 inside\n", encoding="utf-8"
        )
        run_ok(env, "install", "--file", str(secret_file), "--json", "--yes")
        code, payload = env.cli_json("status", "--json")
        blob = json.dumps(payload)
        assert "sk-abcdefghijklmnop1234" not in blob


# ---------------------------------------------------------------------------
# Recover
# ---------------------------------------------------------------------------


class TestRecover:
    def test_recover_after_interrupted_plugin_install(self, env, monkeypatch):
        monkeypatch.setenv("FAKE_DEVIN_EXIT", "1")
        run_fail(env, 1, "install", "--mode", "plugin", "--yes")
        monkeypatch.delenv("FAKE_DEVIN_EXIT")
        run_ok(env, "recover", "--yes")
        scaffold = env.home / ".devin-keysmith" / "plugins" / "devin-keysmith-instruction"
        assert not scaffold.exists()
        code, payload = env.cli_json("status", "--json")
        assert payload["result"]["deployed"] is False
        assert payload["result"]["transaction_pending"] is False

    def test_recover_restores_modified_target(self, env):
        target = env.home / "AGENTS.md"
        target.write_bytes(b"# original\n")  # exact bytes: no newline translation
        roots = keysmith.resolve_roots(None, None)
        backup_rel = keysmith.backup_file(target, roots)
        # Simulate an interrupted install: transaction recorded, no deployment.
        manifest = {
            "schema_version": 1,
            "tool": keysmith.TOOL_NAME,
            "transaction": {
                "operation": "install",
                "mode": "append",
                "targets": [{
                    "path": str(target),
                    "relative": "AGENTS.md",
                    "existed_before": True,
                    "before_sha256": keysmith.sha256_bytes(b"# original\n"),
                    "backup": backup_rel,
                }],
                "plugin": None,
            },
            "deployment": None,
        }
        keysmith.save_manifest(roots, manifest)
        target.write_bytes(
            b"# original\n<!-- devin-keysmith:block:start v1 xx -->\n"
        )
        run_ok(env, "recover", "--yes")
        assert target.read_bytes() == b"# original\n"

    def test_recover_without_transaction_is_a_noop(self, env):
        code, text = env.cli_text("recover", "--yes")
        assert code == 0
        assert "no incomplete transaction" in text


# ---------------------------------------------------------------------------
# Status and doctor
# ---------------------------------------------------------------------------


class TestReporting:
    def test_status_json_shape(self, env):
        run_ok(env, "install", "--mode", "append", "--yes")
        code, payload = env.cli_json("status", "--json")
        result = payload["result"]
        assert code == 0
        assert result["deployed"] is True
        assert result["mode"] == "append"
        assert result["targets"][0]["exists"] is True
        assert result["targets"][0]["sha256"]

    def test_doctor_detects_missing_target(self, env):
        run_ok(env, "install", "--mode", "append", "--yes")
        (env.home / "AGENTS.md").unlink()
        code, payload = env.cli_json("doctor", "--json")
        assert code == 1
        assert payload["ok"] is False
        assert any("missing on disk" in issue for issue in payload["result"]["issues"])

    def test_doctor_detects_plugin_drift(self, env):
        run_ok(env, "install", "--mode", "plugin", "--yes")
        env.state.write_text(json.dumps({"plugins": []}), encoding="utf-8")
        code, payload = env.cli_json("doctor", "--json")
        assert code == 1
        assert any("not listed" in issue for issue in payload["result"]["issues"])

    def test_doctor_healthy_after_install(self, env):
        run_ok(env, "install", "--mode", "plugin", "--yes")
        code, payload = env.cli_json("doctor", "--json")
        assert code == 0
        assert payload["ok"] is True
        notes = " ".join(payload["result"]["notes"])
        assert "devin 3000.10.21 (fake)" in notes

    def test_doctor_notes_missing_binary(self, env, monkeypatch):
        monkeypatch.setenv("DEVIN_KEYSMITH_DEVIN_BIN", str(env.home / "nope.exe"))
        code, payload = env.cli_json("doctor", "--json")
        assert code == 0
        assert any("not found" in note for note in payload["result"]["notes"])


# ---------------------------------------------------------------------------
# Bundled content integrity
# ---------------------------------------------------------------------------


class TestBundleIntegrity:
    def test_bundled_hash_pins(self):
        data = (keysmith._resource_base() / "examples" / keysmith.BUNDLED_PROMPT_NAME).read_bytes()
        assert hashlib.sha256(data).hexdigest() == keysmith.BUNDLED_PROMPT_SHA256

    def test_tampered_bundle_refused(self, env, monkeypatch):
        pinned = keysmith.BUNDLED_PROMPT_SHA256
        monkeypatch.setattr(keysmith, "BUNDLED_PROMPT_SHA256", "0" * 64)
        try:
            run_fail(env, 4, "install", "--yes")
        finally:
            monkeypatch.setattr(keysmith, "BUNDLED_PROMPT_SHA256", pinned)
        assert not (env.home / "AGENTS.md").exists()


class TestPluginDiagnostics:
    def test_symlink_privilege_error_carries_hint(self, env, monkeypatch, capsys):
        monkeypatch.setenv(
            "FAKE_DEVIN_STDERR",
            "io error: symlinking C:\\\\x\\\\cache: A required privilege is not "
            "held by the client. (os error 1314)",
        )
        monkeypatch.setenv("FAKE_DEVIN_EXIT", "1")
        code = env.cli("install", "--mode", "plugin", "--yes")
        captured = capsys.readouterr()
        assert code == 1
        assert "Developer Mode" in captured.err


class TestGlobalMode:
    def test_global_round_trip_preserves_content(self, env):
        target = (
            env.home / ".codeium" / "windsurf" / "memories" / "global_rules.md"
        )
        target.parent.mkdir(parents=True)
        target.write_bytes(b"# existing memory\n")
        run_ok(env, "install", "--yes")  # global is the default
        text = target.read_text(encoding="utf-8")
        assert text.startswith("# existing memory")
        assert text.count(BLOCK_START_TOKEN) == 1
        run_ok(env, "install", "--yes", "--force")
        assert target.read_text(encoding="utf-8").count(BLOCK_START_TOKEN) == 1
        run_ok(env, "uninstall", "--yes")
        assert target.read_bytes() == b"# existing memory\n"

    def test_doctor_warns_on_plugin_agents_shadow(self, env):
        cache = env.plugins_cache
        shadow = cache / "github.com_obra_superpowers-abc123" / "6.3.0"
        shadow.mkdir(parents=True)
        (shadow / "AGENTS.md").write_text("CLAUDE.md", encoding="utf-8")
        code, payload = env.cli_json("doctor", "--json")
        assert code == 0
        notes = " ".join(payload["result"]["notes"])
        assert "shadowed" in notes
        assert "superpowers" in notes

    def test_doctor_silent_without_shadow(self, env):
        code, payload = env.cli_json("doctor", "--json")
        assert code == 0
        notes = " ".join(payload["result"]["notes"])
        assert "shadowed" not in notes
