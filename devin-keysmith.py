#!/usr/bin/env python3
"""devin-keysmith — managed instruction deployment for the Devin CLI.

Deploys a bundled or custom Markdown instruction onto Devin's native
instruction surfaces, preview-first and reversible:

  --mode append   (default)  -> marked block appended to ~/AGENTS.md
                                (user-global, always-on, [Standard] provider)
  --mode plugin              -> plugin scaffold registered through the Devin
                                CLI (`devin plugins install --local`), carrying
                                an always-on AGENTS.md rule at its root
  --mode project             -> marked block appended to <git-root>/AGENTS.md
                                (project layer is git-scoped)
  --mode skills --name <n>   -> %APPDATA%/devin/skills/<n>/SKILL.md
                                (user-global, on-demand activation)

Manifest-owned, atomic, recoverable. Never touches the Devin binary, its
plugin registry or cache, credentials.toml, session databases, state.vscdb,
argv.json, or the CLI .bin caches.

Verified against Devin CLI 3000.10.21 (build 611c1cba).

Zero runtime dependencies. Python 3.8+.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

TOOL_NAME = "devin-keysmith"
VERSION = "0.1.0"
JSON_SCHEMA = "devin-keysmith/v1"

SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")
MANAGED_DIR_NAME = ".devin-keysmith"
MANIFEST_FILENAME = "manifest.json"
BACKUP_DIR_NAME = "backups"

MODE_APPEND = "append"
MODE_GLOBAL = "global"
MODE_PLUGIN = "plugin"
MODE_PROJECT = "project"
MODE_SKILLS = "skills"
MODES = (MODE_GLOBAL, MODE_APPEND, MODE_PLUGIN, MODE_PROJECT, MODE_SKILLS)
# Default is the global Windsurf-memories rule: plugin-carried AGENTS.md rules
# shadow every user/project AGENTS rule by name (verified live — superpowers
# v6.3.0 shadowed ~/AGENTS.md in real sessions), so AGENTS surfaces are not
# reliable defaults.
DEFAULT_MODE = MODE_GLOBAL
GLOBAL_RULE_FILENAME = "global_rules.md"

DEFAULT_SKILL_NAME = "devin-keysmith-instruction"
DEFAULT_PLUGIN_SUFFIX = "instruction"  # full name: devin-keysmith-instruction
DEFAULT_SKILL_DESCRIPTION = "Managed instruction deployed by devin-keysmith."

BUNDLED_PROMPT_NAME = "system-role.md"
# Pinned SHA-256 of examples/system-role.md (persona body sourced from
# zcode-keysmith/examples/system-role.md, with a Devin-specific identity
# override header prepended to counter persona bleed under Devin's stock
# system prompt).
BUNDLED_PROMPT_SHA256 = "401ebf5d0619d40c6399d2b1b452d2b75e5388e6cc0bb77df353cdf9a097c975"

# Marked-block append format (design D3). HTML comments are inert to Markdown
# rendering and never collide with YAML frontmatter.
BLOCK_MARKER_VERSION = "v1"
BLOCK_START_PREFIX = "<!-- devin-keysmith:block:start"
BLOCK_START_SUFFIX = " -->"
BLOCK_END_MARKER = "<!-- devin-keysmith:block:end -->"
BLOCK_START_RE = re.compile(
    r"[ \t]*<!--\s*devin-keysmith:block:start[^>]*-->[ \t]*\r?\n?"
)
BLOCK_END_RE = re.compile(r"[ \t]*<!--\s*devin-keysmith:block:end\s*-->[ \t]*\r?\n?")

# Host files that must never be written, even if a manifest path is corrupted
# (design D6).
READ_ONLY_BASENAMES = {
    "credentials.toml",
    "sessions.db",
    "sessions.db-shm",
    "sessions.db-wal",
    "state.vscdb",
    "argv.json",
    "devin.exe",
    "devin",
}
READ_ONLY_SUFFIXES = {".exe", ".bin", ".vscdb", ".db", ".db-wal", ".db-shm"}
# Consecutive path-part sequences owned by the Devin CLI itself. Our managed
# scaffold (~/.devin-keysmith/plugins/...) and the skills deploy surface
# (%APPDATA%/devin/skills/...) are intentionally NOT in this list.
READ_ONLY_PART_SEQUENCES = (
    ("devin", "cli", "plugins"),
    ("devin", "cli", "bin"),
    ("devin", "cli", "_versions"),
    ("devin", "cli", "mcp"),
)

# Credential-shaped values that must never reach output, logs, or the manifest.
_CREDENTIAL_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"(?i)(api[_-]?key|token|secret|password)\s*[:=]\s*\S+"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{8,}"),
)

ENV_HOME = "DEVIN_KEYSMITH_HOME"
ENV_SKILLS_ROOT = "DEVIN_KEYSMITH_SKILLS_ROOT"
ENV_DEVIN_BIN = "DEVIN_KEYSMITH_DEVIN_BIN"
ENV_MEMORIES_ROOT = "DEVIN_KEYSMITH_MEMORIES_ROOT"
ENV_PLUGINS_CACHE = "DEVIN_KEYSMITH_PLUGINS_CACHE"


class KeysmithError(Exception):
    """Raised for expected, user-facing failures."""

    def __init__(self, message: str, exit_code: int = 1) -> None:
        super().__init__(message)
        self.exit_code = exit_code


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


def _resource_base() -> Path:
    """Base directory for bundled resources.

    PyInstaller unpacks to sys._MEIPASS; source runs resolve next to this file.
    """
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(getattr(sys, "_MEIPASS"))
    return Path(__file__).resolve().parent


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def redact(text: str) -> str:
    """Strip credential-shaped values from anything we print or serialize."""
    result = text
    for pattern in _CREDENTIAL_PATTERNS:
        result = pattern.sub("[redacted]", result)
    return result


# ---------------------------------------------------------------------------
# Roots (design D2)
# ---------------------------------------------------------------------------


@dataclass
class Roots:
    user_home: Path
    skills_root: Path
    managed_dir: Path
    memories_root: Path
    plugins_cache: Optional[Path] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "user_home": redact(str(self.user_home)),
            "skills_root": redact(str(self.skills_root)),
            "managed_dir": redact(str(self.managed_dir)),
            "memories_root": redact(str(self.memories_root)),
            "plugins_cache": redact(str(self.plugins_cache)) if self.plugins_cache else None,
        }


def _resolve_plugins_cache(user_home: Path) -> Optional[Path]:
    override = os.environ.get(ENV_PLUGINS_CACHE)
    if override:
        return Path(override).expanduser().resolve()
    if os.name == "nt" and os.environ.get("APPDATA"):
        return (Path(os.environ["APPDATA"]) / "devin" / "cli" / "plugins" / "cache").resolve()
    candidate = user_home / ".config" / "devin" / "cli" / "plugins" / "cache"
    return candidate.resolve()


def resolve_roots(
    home_override: Optional[str] = None,
    skills_override: Optional[str] = None,
) -> Roots:
    """Resolve every deploy root once.

    Precedence per root: explicit argument > override env var > platform
    default. Path.home() reads %USERPROFILE% on Windows and $HOME elsewhere.
    """
    if home_override:
        user_home = Path(home_override).expanduser()
    elif os.environ.get(ENV_HOME):
        user_home = Path(os.environ[ENV_HOME]).expanduser()
    else:
        user_home = Path.home()
    user_home = user_home.resolve()

    if skills_override:
        skills_root = Path(skills_override).expanduser()
    elif os.environ.get(ENV_SKILLS_ROOT):
        skills_root = Path(os.environ[ENV_SKILLS_ROOT]).expanduser()
    elif os.name == "nt" and os.environ.get("APPDATA"):
        skills_root = Path(os.environ["APPDATA"]) / "devin" / "skills"
    else:
        skills_root = user_home / ".config" / "devin" / "skills"
    skills_root = skills_root.resolve()

    memories_override = os.environ.get(ENV_MEMORIES_ROOT)
    if memories_override:
        memories_root = Path(memories_override).expanduser().resolve()
    else:
        memories_root = (
            user_home / ".codeium" / "windsurf" / "memories"
        ).resolve()

    managed_dir = user_home / MANAGED_DIR_NAME
    return Roots(
        user_home=user_home,
        skills_root=skills_root,
        managed_dir=managed_dir,
        memories_root=memories_root,
        plugins_cache=_resolve_plugins_cache(user_home),
    )


def normalize_rel(path: Path, roots: Roots) -> str:
    """Manifest-friendly path: relative to the managed dir when possible."""
    resolved = path.resolve()
    for base in (roots.managed_dir, roots.user_home, roots.skills_root, roots.memories_root):
        try:
            return resolved.relative_to(base.resolve()).as_posix()
        except ValueError:
            continue
    return resolved.as_posix()


# ---------------------------------------------------------------------------
# Read-only guard (design D6)
# ---------------------------------------------------------------------------


def _has_read_only_sequence(parts_lower: List[str]) -> bool:
    for sequence in READ_ONLY_PART_SEQUENCES:
        span = len(sequence)
        for index in range(0, len(parts_lower) - span + 1):
            if tuple(parts_lower[index : index + span]) == sequence:
                return True
    return False


def _is_read_only_target(path: Path) -> bool:
    name = path.name.lower()
    if name in READ_ONLY_BASENAMES:
        return True
    if path.suffix.lower() in READ_ONLY_SUFFIXES:
        return True
    parts = [part.lower() for part in path.parts]
    if _has_read_only_sequence(parts):
        return True
    return False


def ensure_writable_target(path: Path) -> None:
    if _is_read_only_target(path):
        raise KeysmithError(
            "refusing to write host-managed path: %s" % path, exit_code=3
        )


# ---------------------------------------------------------------------------
# Atomic write + backup
# ---------------------------------------------------------------------------


def atomic_write_text(path: Path, text: str) -> None:
    """Write text via a same-directory temp file plus atomic rename."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=path.name + ".keysmith-tmp-", dir=str(path.parent)
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, str(path))
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def atomic_write_bytes(path: Path, data: bytes) -> None:
    atomic_write_text(path, data.decode("utf-8"))


def backup_file(path: Path, roots: Roots) -> str:
    """Copy an existing target into the backup area; return its relative ref."""
    backup_dir = roots.managed_dir / BACKUP_DIR_NAME
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_name = "%s.bak_%s" % (path.name, _stamp())
    backup_path = backup_dir / backup_name
    suffix = 1
    while backup_path.exists():
        backup_path = backup_dir / ("%s.%d" % (backup_name, suffix))
        suffix += 1
    shutil.copy2(str(path), str(backup_path))
    return normalize_rel(backup_path, roots)


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------


def manifest_path(roots: Roots) -> Path:
    return roots.managed_dir / MANIFEST_FILENAME


def load_manifest(roots: Roots) -> Dict[str, Any]:
    path = manifest_path(roots)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise KeysmithError("invalid manifest at %s: %s" % (path, error))
    if not isinstance(data, dict):
        raise KeysmithError("invalid manifest at %s: expected object" % path)
    return data


def save_manifest(roots: Roots, data: Dict[str, Any]) -> None:
    roots.managed_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        manifest_path(roots), json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    )


def clear_manifest(roots: Roots) -> None:
    path = manifest_path(roots)
    if path.exists():
        path.unlink()


# ---------------------------------------------------------------------------
# Git root discovery (design D5)
# ---------------------------------------------------------------------------


def find_git_root(start: Path) -> Optional[Path]:
    """Walk up from `start` to the nearest .git entry (directory or file).

    Pure filesystem walk: no git subprocess, works inside worktrees.
    """
    current = start.resolve()
    while True:
        candidate = current / ".git"
        if candidate.exists():
            return current
        parent = current.parent
        if parent == current:
            return None
        current = parent


# ---------------------------------------------------------------------------
# Marked-block append semantics (design D3)
# ---------------------------------------------------------------------------


def render_block(content: str, deploy_id: str) -> str:
    start = "%s %s %s%s" % (
        BLOCK_START_PREFIX,
        BLOCK_MARKER_VERSION,
        deploy_id,
        BLOCK_START_SUFFIX,
    )
    body = content.rstrip("\n")
    return "\n".join([start, body, BLOCK_END_MARKER])


def remove_block(text: str) -> Tuple[str, bool]:
    """Strip the managed block if present. Return (cleaned, found)."""
    match_start = BLOCK_START_RE.search(text)
    if not match_start:
        return text, False
    match_end = BLOCK_END_RE.search(text, match_start.end())
    if not match_end:
        # Unterminated block: drop from the start marker to end of text.
        return text[: match_start.start()], True
    cleaned = text[: match_start.start()] + text[match_end.end() :]
    cleaned = cleaned.replace("\n\n\n", "\n\n")
    return cleaned, True


def apply_block(existing_text: str, block: str) -> str:
    """Replace the managed block in place, or append after existing content."""
    cleaned, _found = remove_block(existing_text)
    if cleaned and not cleaned.endswith("\n"):
        cleaned += "\n"
    if not cleaned:
        return block + "\n"
    return cleaned + "\n" + block + "\n"


# ---------------------------------------------------------------------------
# Frontmatter handling (skills mode)
# ---------------------------------------------------------------------------


def split_frontmatter(text: str) -> Tuple[Dict[str, str], str]:
    """Split a leading YAML frontmatter block into flat key/value pairs."""
    if not text.startswith("---"):
        return {}, text
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return {}, text
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            block = "\n".join(lines[1:index])
            body = "\n".join(lines[index + 1 :])
            pairs: Dict[str, str] = {}
            for line in block.split("\n"):
                if ":" in line and not line.startswith((" ", "\t", "-")):
                    key, _, value = line.partition(":")
                    pairs[key.strip()] = value.strip()
            return pairs, body
    return {}, text


def build_skill_document(
    name: str, description: str, content: str
) -> str:
    pairs, body = split_frontmatter(content)
    final_name = name or pairs.get("name", "")
    final_description = description or pairs.get("description", DEFAULT_SKILL_DESCRIPTION)
    body = body if body else content
    front = "\n".join(
        [
            "---",
            "name: %s" % final_name,
            "description: %s" % final_description,
            "---",
        ]
    )
    return front + "\n\n" + body.lstrip("\n")


# ---------------------------------------------------------------------------
# Deploy plan
# ---------------------------------------------------------------------------


@dataclass
class FilePlan:
    path: Path
    relative: str
    kind: str  # "append-target" | "skill-file" | "plugin-manifest" | "plugin-rule" | "plugin-skill"
    content: bytes  # final bytes to write
    existed_before: bool
    before_sha256: Optional[str]
    backup: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "relative": self.relative,
            "path": redact(str(self.path)),
            "kind": self.kind,
            "existed_before": self.existed_before,
            "before_sha256": self.before_sha256,
            "content_sha256": sha256_bytes(self.content),
            "backup": self.backup,
        }


@dataclass
class DeployPlan:
    mode: str
    name: Optional[str]
    files: List[FilePlan] = field(default_factory=list)
    plugin_name: Optional[str] = None
    scaffold_dir: Optional[Path] = None
    include_skill: bool = False
    register_command: Optional[List[str]] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "name": self.name,
            "files": [plan.as_dict() for plan in self.files],
            "plugin_name": self.plugin_name,
            "scaffold_dir": redact(str(self.scaffold_dir)) if self.scaffold_dir else None,
            "register_command": self.register_command,
        }


def _require_safe_name(raw: Optional[str], fallback: str, label: str) -> str:
    name = fallback if raw is None else raw
    if not name or not SAFE_NAME_RE.fullmatch(name):
        raise KeysmithError("invalid %s name: %r" % (label, name))
    return name


def build_plan(
    roots: Roots,
    mode: str,
    name: Optional[str],
    content: bytes,
    deploy_id: str,
    cwd: Optional[Path],
    include_skill: bool,
) -> DeployPlan:
    text = content.decode("utf-8")
    plan = DeployPlan(mode=mode, name=name, include_skill=include_skill)

    if mode == MODE_GLOBAL:
        # User-global always-on rule under the Windsurf-memories surface.
        # Devin injects this rule into every session (verified live) and its
        # name never collides with plugin AGENTS rules.
        target = roots.memories_root / GLOBAL_RULE_FILENAME
        ensure_writable_target(target)
        existing = target.read_text(encoding="utf-8") if target.is_file() else ""
        final = apply_block(existing, render_block(text, deploy_id)).encode("utf-8")
        plan.files.append(_file_plan(target, roots, "global-rule-target", final))

    elif mode == MODE_APPEND:
        target = roots.user_home / "AGENTS.md"
        ensure_writable_target(target)
        existing = target.read_text(encoding="utf-8") if target.is_file() else ""
        final = apply_block(existing, render_block(text, deploy_id)).encode("utf-8")
        plan.files.append(_file_plan(target, roots, "append-target", final))

    elif mode == MODE_PROJECT:
        start = cwd.resolve() if cwd else Path.cwd()
        git_root = find_git_root(start)
        if git_root is None:
            raise KeysmithError(
                "no git root found at or above %s; project mode deploys into "
                "the repository root AGENTS.md" % start
            )
        target = git_root / "AGENTS.md"
        ensure_writable_target(target)
        existing = target.read_text(encoding="utf-8") if target.is_file() else ""
        final = apply_block(existing, render_block(text, deploy_id)).encode("utf-8")
        plan.files.append(_file_plan(target, roots, "append-target", final))

    elif mode == MODE_SKILLS:
        skill_name = _require_safe_name(name, DEFAULT_SKILL_NAME, "skill")
        plan.name = skill_name
        target = roots.skills_root / skill_name / "SKILL.md"
        ensure_writable_target(target)
        final = build_skill_document(skill_name, "", text).encode("utf-8")
        plan.files.append(_file_plan(target, roots, "skill-file", final))

    elif mode == MODE_PLUGIN:
        plugin_name = "devin-keysmith-" + _require_safe_name(
            name, DEFAULT_PLUGIN_SUFFIX, "plugin"
        )
        plan.plugin_name = plugin_name
        scaffold = roots.managed_dir / "plugins" / plugin_name
        plan.scaffold_dir = scaffold
        ensure_writable_target(scaffold)

        manifest_json = json.dumps(
            {
                "name": plugin_name,
                "version": VERSION,
                "description": "Managed instruction deployed by %s." % TOOL_NAME,
                "author": {"name": TOOL_NAME},
                "license": "MIT",
                "keywords": ["keysmith", "instruction"],
            },
            indent=2,
            ensure_ascii=False,
        )
        plan.files.append(
            _file_plan(
                scaffold / ".devin-plugin" / "plugin.json",
                roots,
                "plugin-manifest",
                (manifest_json + "\n").encode("utf-8"),
            )
        )
        plan.files.append(
            _file_plan(scaffold / "AGENTS.md", roots, "plugin-rule", content)
        )
        if include_skill:
            plan.files.append(
                _file_plan(
                    scaffold / "skills" / plugin_name / "SKILL.md",
                    roots,
                    "plugin-skill",
                    build_skill_document(plugin_name, "", text).encode("utf-8"),
                )
            )
        binary = find_devin_bin()
        if binary is None:
            raise KeysmithError(
                "plugin mode requires the devin CLI; set %s, put devin on PATH, "
                "or install to %%LOCALAPPDATA%%\\devin\\cli\\bin" % ENV_DEVIN_BIN
            )
        plan.register_command = [
            str(binary),
            "plugins",
            "install",
            str(scaffold),
            "--local",
            "-y",
        ]
    else:
        raise KeysmithError("unknown mode: %s" % mode)

    return plan


def _file_plan(path: Path, roots: Roots, kind: str, content: bytes) -> FilePlan:
    existed = path.is_file()
    return FilePlan(
        path=path,
        relative=normalize_rel(path, roots),
        kind=kind,
        content=content,
        existed_before=existed,
        before_sha256=sha256_file(path) if existed else None,
    )


def resolve_source(source_file: Optional[str]) -> Tuple[bytes, str, str]:
    """Return (content bytes, source descriptor, expected sha256)."""
    if source_file:
        path = Path(source_file).expanduser()
        if not path.is_file():
            raise KeysmithError("source file not found: %s" % path)
        data = path.read_bytes()
        return data, "file:%s" % path.resolve(), sha256_bytes(data)
    bundled = _resource_base() / "examples" / BUNDLED_PROMPT_NAME
    if not bundled.is_file():
        raise KeysmithError("bundled instruction missing: %s" % bundled)
    data = bundled.read_bytes()
    actual = sha256_bytes(data)
    if actual != BUNDLED_PROMPT_SHA256:
        raise KeysmithError(
            "bundled instruction hash mismatch: expected %s, got %s"
            % (BUNDLED_PROMPT_SHA256, actual),
            exit_code=4,
        )
    return data, "bundled:%s" % BUNDLED_PROMPT_NAME, actual


def render_plan(
    plan: DeployPlan, source_desc: str, apply: bool
) -> List[str]:
    lines = [
        "%s %s" % (TOOL_NAME, VERSION),
        "  operation : %s" % ("install" if apply else "install (preview)"),
        "  mode      : %s" % plan.mode,
        "  source    : %s" % source_desc,
        "",
        "  files that would change:",
    ]
    for file_plan in plan.files:
        verb = "overwrite" if file_plan.existed_before else "create"
        lines.append("    - %s  [%s]" % (file_plan.relative, verb))
    if plan.register_command:
        lines.append("")
        lines.append("  devin CLI step:")
        lines.append("    $ %s" % " ".join(plan.register_command))
    lines.append("")
    if not apply:
        lines.append("  preview only; re-run with --yes to write.")
    else:
        lines.append("  applying.")
    return lines


# ---------------------------------------------------------------------------
# Devin CLI integration (design D4, plugin mode only)
# ---------------------------------------------------------------------------


def find_devin_bin() -> Optional[Path]:
    """DEVIN_KEYSMITH_DEVIN_BIN > PATH > %LOCALAPPDATA%\\devin\\cli\\bin\\devin.exe."""
    override = os.environ.get(ENV_DEVIN_BIN)
    if override:
        candidate = Path(override).expanduser()
        return candidate if candidate.exists() else None
    which = shutil.which("devin") or shutil.which("devin.exe")
    if which:
        return Path(which)
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        candidate = Path(os.environ["LOCALAPPDATA"]) / "devin" / "cli" / "bin" / "devin.exe"
        if candidate.exists():
            return candidate
    return None


def run_devin(binary: Path, args: List[str]) -> Tuple[int, str, str]:
    completed = subprocess.run(
        [str(binary)] + list(args),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
        check=False,
    )
    return completed.returncode, completed.stdout or "", completed.stderr or ""


def plugin_listed(binary: Path, plugin_name: str) -> Optional[bool]:
    """True/False when the plugin list is readable; None when it is not."""
    code, stdout, _stderr = run_devin(binary, ["plugins", "list"])
    if code != 0:
        return None
    for line in stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith("•") or stripped.startswith("-"):
            tokens = stripped.lstrip("•-").strip().split()
            if tokens and tokens[0] == plugin_name:
                return True
    return False


def _symlink_capable(roots: Roots) -> bool:
    """Probe whether this process may create symlinks (Windows privilege)."""
    probe_dir = roots.managed_dir / ".probe"
    probe_dir.mkdir(parents=True, exist_ok=True)
    probe_target = probe_dir / ("link-%s" % _stamp())
    try:
        probe_target.symlink_to(probe_dir, target_is_directory=True)
    except (OSError, NotImplementedError):
        return False
    finally:
        try:
            probe_target.unlink()
            probe_dir.rmdir()
        except OSError:
            pass
    return True


def register_plugin(plan: DeployPlan) -> str:
    assert plan.register_command is not None and plan.plugin_name is not None
    binary = Path(plan.register_command[0])
    code, _stdout, stderr = run_devin(
        binary, plan.register_command[1:]
    )
    if code != 0:
        message = "devin plugins install failed (exit %d): %s" % (
            code, redact(stderr.strip() or "no stderr")
        )
        if "symlinking" in stderr and ("1314" in stderr or "privilege" in stderr.lower()):
            message += (
                " — the devin CLI symlinks local plugin sources into its cache, "
                "which Windows denies without symlink privilege. Enable Windows "
                "Developer Mode (Settings > Privacy & security > For developers) "
                "or run this from an elevated terminal, then retry."
            )
        raise KeysmithError(message)
    listed = plugin_listed(binary, plan.plugin_name)
    if listed is False:
        raise KeysmithError(
            "devin plugins install exited 0 but plugin %s is not listed"
            % plan.plugin_name
        )
    return "registered plugin %s through the devin CLI" % plan.plugin_name


def deregister_plugin(plugin_name: str) -> str:
    """Remove a plugin through the CLI; tolerate already-deregistered state."""
    binary = find_devin_bin()
    if binary is None:
        return "devin binary not found; skipped deregistration of %s" % plugin_name
    listed = plugin_listed(binary, plugin_name)
    if listed is False:
        return "plugin %s already deregistered" % plugin_name
    if listed is None:
        return "could not read plugin list; skipped deregistration of %s" % plugin_name
    code, _stdout, stderr = run_devin(binary, ["plugins", "remove", plugin_name])
    if code != 0:
        raise KeysmithError(
            "devin plugins remove failed (exit %d): %s"
            % (code, redact(stderr.strip() or "no stderr"))
        )
    return "deregistered plugin %s through the devin CLI" % plugin_name


# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------


def do_install(args: argparse.Namespace) -> int:
    roots = resolve_roots(args.home, args.skills_root)
    mode = args.mode or DEFAULT_MODE
    if mode not in MODES:
        raise KeysmithError("invalid mode: %s" % mode)
    content, source_desc, source_sha = resolve_source(args.file)
    deploy_id = uuid.uuid4().hex[:12]
    cwd = Path(args.cwd).expanduser() if args.cwd else None
    plan = build_plan(
        roots, mode, args.name, content, deploy_id, cwd, bool(args.include_skill)
    )

    as_json = bool(args.json)
    if not args.yes:
        if as_json:
            envelope = {
                "schema": JSON_SCHEMA,
                "tool": TOOL_NAME,
                "version": VERSION,
                "operation": "install",
                "preview": True,
                "apply": False,
                "ok": True,
                "roots": roots.as_dict(),
                "source": source_desc,
                "plan": plan.as_dict(),
                "exit_code": 0,
            }
            print(redact(json.dumps(envelope, indent=2, ensure_ascii=False)))
        else:
            print("\n".join(render_plan(plan, source_desc, apply=False)))
        return 0

    existing_manifest = load_manifest(roots)
    replaced_actions: List[str] = []
    if existing_manifest.get("deployment") and not args.force:
        raise KeysmithError(
            "a managed deployment already exists (mode=%s). "
            "Run uninstall first, or pass --force to replace."
            % existing_manifest["deployment"].get("mode"),
            exit_code=5,
        )
    if existing_manifest.get("deployment"):
        replaced_actions = _rollback_deployment(roots, existing_manifest)
        # The rollback may have removed or restored targets the plan captured
        # at preview time; rebuild against the post-rollback disk state so
        # existed_before/backup decisions match reality.
        plan = build_plan(
            roots, mode, args.name, content, deploy_id, cwd, bool(args.include_skill)
        )

    # Transaction marker: carries the full pre-state of every target so an
    # interrupted install can be rolled back by `recover`.
    transaction = {
        "operation": "install",
        "mode": mode,
        "started_at": _utc_now(),
        "targets": [
            {
                "path": redact(str(file_plan.path)),
                "relative": file_plan.relative,
                "existed_before": file_plan.existed_before,
                "before_sha256": file_plan.before_sha256,
                "backup": None,
            }
            for file_plan in plan.files
        ],
        "plugin": {
            "name": plan.plugin_name,
            "scaffold_dir": redact(str(plan.scaffold_dir)) if plan.scaffold_dir else None,
            "registered": False,
        }
        if mode == MODE_PLUGIN
        else None,
    }
    manifest: Dict[str, Any] = {
        "schema_version": 1,
        "tool": TOOL_NAME,
        "version": VERSION,
        "created_at": existing_manifest.get("created_at", _utc_now()),
        "updated_at": _utc_now(),
        "transaction": transaction,
        "deployment": None,
    }
    save_manifest(roots, manifest)

    try:
        for index, file_plan in enumerate(plan.files):
            if file_plan.existed_before:
                backup = backup_file(file_plan.path, roots)
                file_plan.backup = backup
                transaction["targets"][index]["backup"] = backup
                # Persist the backup reference before mutating the target.
                save_manifest(roots, manifest)
            atomic_write_bytes(file_plan.path, file_plan.content)
            after = sha256_file(file_plan.path)
            if after != sha256_bytes(file_plan.content):
                raise KeysmithError("post-write hash mismatch on %s" % file_plan.relative)

        register_action: Optional[str] = None
        if mode == MODE_PLUGIN:
            register_action = register_plugin(plan)
            transaction["plugin"]["registered"] = True
            save_manifest(roots, manifest)

        deployment = {
            "id": deploy_id,
            "mode": mode,
            "name": plan.name,
            "source": source_desc,
            "source_sha256": source_sha,
            "deployed_at": _utc_now(),
            "targets": [
                {
                    "path": redact(str(file_plan.path)),
                    "relative": file_plan.relative,
                    "existed_before": file_plan.existed_before,
                    "before_sha256": file_plan.before_sha256,
                    "after_sha256": sha256_file(file_plan.path),
                    "backup": file_plan.backup,
                }
                for file_plan in plan.files
            ],
            "plugin": {
                "name": plan.plugin_name,
                "scaffold_dir": redact(str(plan.scaffold_dir)) if plan.scaffold_dir else None,
                "registered": mode == MODE_PLUGIN,
            }
            if mode == MODE_PLUGIN
            else None,
        }
        manifest["deployment"] = deployment
        manifest["transaction"] = None
        manifest["updated_at"] = _utc_now()
        save_manifest(roots, manifest)
    except Exception:
        # Leave the transaction marker in place so `recover` can repair.
        raise

    if as_json:
        envelope = {
            "schema": JSON_SCHEMA,
            "tool": TOOL_NAME,
            "version": VERSION,
            "operation": "install",
            "preview": False,
            "apply": True,
            "ok": True,
            "roots": roots.as_dict(),
            "source": source_desc,
            "plan": plan.as_dict(),
            "result": {
                "deployment_id": deployment["id"],
                "mode": mode,
                "registered": register_action,
                "replaced": replaced_actions,
            },
            "exit_code": 0,
        }
        print(redact(json.dumps(envelope, indent=2, ensure_ascii=False)))
    else:
        for action in replaced_actions:
            print("replaced: %s" % action)
        for file_plan in plan.files:
            print("wrote %s" % file_plan.relative)
        if register_action:
            print("plugin : %s" % register_action)
    return 0


def _absolute_from_record(record: Dict[str, Any], roots: Roots) -> Path:
    """Resolve a manifest target path (absolute) or managed-relative ref."""
    raw = record.get("path") or record.get("relative")
    if not raw:
        raise KeysmithError("manifest target record lacks a path")
    path = Path(raw)
    if path.is_absolute():
        return path
    return roots.managed_dir / raw


def _rmtree(path: Path) -> None:
    """rmtree that clears Windows read-only bits (e.g. git object files)."""
    import stat

    def _on_error(func, target, _exc_info):
        os.chmod(target, stat.S_IWRITE)
        func(target)

    shutil.rmtree(str(path), onerror=_on_error)


def _prune_empty_parents(path: Path, roots: Roots) -> None:
    """Remove empty parent directories left behind under our deploy roots."""
    boundaries = [
        roots.skills_root.resolve(),
        (roots.managed_dir / "plugins").resolve(),
    ]
    current = path.parent.resolve()
    for boundary in boundaries:
        try:
            if boundary == current or boundary not in current.parents:
                continue
        except ValueError:
            continue
        walker = current
        while walker != boundary:
            try:
                walker.rmdir()
            except OSError:
                break  # not empty (or permission) — stop climbing
            walker = walker.parent


def _restore_target(roots: Roots, record: Dict[str, Any], dry: bool) -> str:
    target = _absolute_from_record(record, roots)
    if not record.get("existed_before"):
        if target.exists() and not dry:
            if target.is_dir() and not target.is_file():
                _rmtree(target)
            else:
                target.unlink()
                _prune_empty_parents(target, roots)
        return "removed %s" % record.get("relative", target.name)
    backup_rel = record.get("backup")
    if not backup_rel:
        raise KeysmithError(
            "manifest entry lacks a backup for %s" % record.get("relative", target)
        )
    backup = roots.managed_dir / backup_rel
    if not backup.is_file():
        raise KeysmithError(
            "backup missing for %s: %s" % (record.get("relative", target), backup_rel)
        )
    if not dry:
        shutil.copy2(str(backup), str(target))
        restored = sha256_file(target)
        if record.get("before_sha256") and restored != record["before_sha256"]:
            raise KeysmithError(
                "restored hash mismatch on %s" % record.get("relative", target)
            )
    return "restored %s" % record.get("relative", target)


def _rollback_deployment(roots: Roots, manifest: Dict[str, Any]) -> List[str]:
    """Fully reverse a committed deployment: plugin first, then every target."""
    deployment = manifest.get("deployment") or {}
    actions: List[str] = []
    plugin = deployment.get("plugin")
    if plugin and plugin.get("registered"):
        actions.append(deregister_plugin(plugin["name"]))
    for record in deployment.get("targets", []):
        actions.append(_restore_target(roots, record, dry=False))
    if plugin and plugin.get("scaffold_dir"):
        scaffold = Path(plugin["scaffold_dir"])
        if scaffold.is_dir():
            _rmtree(scaffold)
            actions.append("removed scaffold %s" % scaffold)
    return actions


def do_uninstall(args: argparse.Namespace) -> int:
    roots = resolve_roots(args.home, args.skills_root)
    manifest = load_manifest(roots)
    deployment = manifest.get("deployment")
    if not deployment:
        message = "no managed deployment found under %s" % roots.managed_dir
        if args.json:
            print(redact(json.dumps({
                "schema": JSON_SCHEMA, "tool": TOOL_NAME, "version": VERSION,
                "operation": "uninstall", "preview": not args.yes, "apply": bool(args.yes),
                "ok": True, "roots": roots.as_dict(), "result": {"actions": []},
                "diagnostics": [message], "exit_code": 0,
            }, indent=2, ensure_ascii=False)))
        else:
            print(message)
        return 0

    targets = deployment.get("targets", [])
    actions_planned = [
        "remove %s" % record["relative"] if not record.get("existed_before")
        else "restore %s" % record["relative"]
        for record in targets
    ]
    plugin = deployment.get("plugin")
    if plugin and plugin.get("registered"):
        actions_planned.append("deregister plugin %s" % plugin["name"])

    if not args.yes:
        if args.json:
            print(redact(json.dumps({
                "schema": JSON_SCHEMA, "tool": TOOL_NAME, "version": VERSION,
                "operation": "uninstall", "preview": True, "apply": False, "ok": True,
                "roots": roots.as_dict(), "plan": {"actions": actions_planned},
                "exit_code": 0,
            }, indent=2, ensure_ascii=False)))
        else:
            print("%s uninstall (preview)" % TOOL_NAME)
            for action in actions_planned:
                print("  - %s" % action)
            print("\n  preview only; re-run with --yes to apply.")
        return 0

    actions = _rollback_deployment(roots, manifest)
    clear_manifest(roots)

    if args.json:
        print(redact(json.dumps({
            "schema": JSON_SCHEMA, "tool": TOOL_NAME, "version": VERSION,
            "operation": "uninstall", "preview": False, "apply": True, "ok": True,
            "roots": roots.as_dict(), "result": {"actions": actions}, "exit_code": 0,
        }, indent=2, ensure_ascii=False)))
    else:
        print("uninstalled:")
        for action in actions:
            print("  - %s" % action)
    return 0


def do_recover(args: argparse.Namespace) -> int:
    roots = resolve_roots(args.home, args.skills_root)
    manifest = load_manifest(roots)
    transaction = manifest.get("transaction")
    if not transaction:
        message = "no incomplete transaction found"
        if args.json:
            print(redact(json.dumps({
                "schema": JSON_SCHEMA, "tool": TOOL_NAME, "version": VERSION,
                "operation": "recover", "preview": not args.yes, "apply": bool(args.yes),
                "ok": True, "roots": roots.as_dict(), "result": {"actions": []},
                "diagnostics": [message], "exit_code": 0,
            }, indent=2, ensure_ascii=False)))
        else:
            print(message)
        return 0

    targets = transaction.get("targets", [])
    planned = ["revert %s" % entry.get("relative", entry) for entry in targets]
    plugin = transaction.get("plugin")
    if plugin and plugin.get("scaffold_dir"):
        planned.append("remove scaffold %s" % plugin["scaffold_dir"])
    if not args.yes:
        if args.json:
            print(redact(json.dumps({
                "schema": JSON_SCHEMA, "tool": TOOL_NAME, "version": VERSION,
                "operation": "recover", "preview": True, "apply": False, "ok": True,
                "roots": roots.as_dict(), "plan": {"actions": planned}, "exit_code": 0,
            }, indent=2, ensure_ascii=False)))
        else:
            print("%s recover (preview)" % TOOL_NAME)
            for action in planned:
                print("  - %s" % action)
            print("\n  preview only; re-run with --yes to apply.")
        return 0

    actions: List[str] = []
    for entry in targets:
        if isinstance(entry, dict):
            actions.append(_restore_target(roots, entry, dry=False))
        else:
            leftover = Path(entry)
            if leftover.parent.is_dir():
                for partial in leftover.parent.glob(leftover.name + ".keysmith-tmp-*"):
                    partial.unlink()
                    actions.append("removed partial write %s" % partial.name)
    if plugin and plugin.get("scaffold_dir"):
        scaffold = Path(plugin["scaffold_dir"])
        if scaffold.is_dir():
            _rmtree(scaffold)
            actions.append("removed scaffold %s" % scaffold)
    if plugin and plugin.get("registered"):
        actions.append(deregister_plugin(plugin["name"]))

    # An interrupted install never committed a deployment.
    if transaction.get("operation") == "install":
        manifest["deployment"] = None
    manifest["transaction"] = None
    manifest["updated_at"] = _utc_now()
    save_manifest(roots, manifest)

    if args.json:
        print(redact(json.dumps({
            "schema": JSON_SCHEMA, "tool": TOOL_NAME, "version": VERSION,
            "operation": "recover", "preview": False, "apply": True, "ok": True,
            "roots": roots.as_dict(), "result": {"actions": actions}, "exit_code": 0,
        }, indent=2, ensure_ascii=False)))
    else:
        print("recovered:")
        for action in actions:
            print("  - %s" % action)
    return 0


def do_status(args: argparse.Namespace) -> int:
    roots = resolve_roots(args.home, args.skills_root)
    manifest = load_manifest(roots)
    deployment = manifest.get("deployment")
    result: Dict[str, Any] = {"deployed": False, "roots": roots.as_dict()}
    if deployment:
        targets = deployment.get("targets", [])
        plugin = deployment.get("plugin")
        result.update(
            {
                "deployed": True,
                "mode": deployment.get("mode"),
                "deployment_id": deployment.get("id"),
                "source": deployment.get("source"),
                "source_sha256": deployment.get("source_sha256"),
                "deployed_at": deployment.get("deployed_at"),
                "plugin": plugin,
                "targets": [
                    {
                        "relative": record["relative"],
                        "exists": _absolute_from_record(record, roots).is_file(),
                        "sha256": record.get("after_sha256"),
                    }
                    for record in targets
                ],
            }
        )
    result["transaction_pending"] = bool(manifest.get("transaction"))

    if args.json:
        print(redact(json.dumps({
            "schema": JSON_SCHEMA, "tool": TOOL_NAME, "version": VERSION,
            "operation": "status", "preview": True, "apply": False, "ok": True,
            "result": result, "exit_code": 0,
        }, indent=2, ensure_ascii=False)))
    else:
        print("%s status" % TOOL_NAME)
        print("  home     : %s" % roots.user_home)
        print("  skills   : %s" % roots.skills_root)
        print("  managed  : %s" % roots.managed_dir)
        if not result["deployed"]:
            print("  deployed : no")
        else:
            print("  deployed : yes (%s)" % result["mode"])
            print("  source   : %s" % result["source"])
            if result.get("plugin"):
                print("  plugin   : %s" % result["plugin"].get("name"))
            for target in result["targets"]:
                print(
                    "  target   : %s [%s]"
                    % (target["relative"], "present" if target["exists"] else "MISSING")
                )
        if result["transaction_pending"]:
            print("  WARNING  : an incomplete transaction is pending; run recover --yes")
    return 0


def _find_plugin_agents_shadows(roots: Roots) -> List[str]:
    """Plugin cache dirs whose root AGENTS.md shadows AGENTS-named rules."""
    if not roots.plugins_cache or not roots.plugins_cache.is_dir():
        return []
    shadows: List[str] = []
    for candidate in roots.plugins_cache.glob("*/*"):
        if not candidate.is_dir():
            continue
        if (candidate / "AGENTS.md").is_file():
            shadows.append(candidate.parent.name)
    return sorted(set(shadows))


def do_doctor(args: argparse.Namespace) -> int:
    roots = resolve_roots(args.home, args.skills_root)
    issues: List[str] = []
    notes: List[str] = []

    binary = find_devin_bin()
    if binary is None:
        notes.append("devin CLI binary not found (plugin mode unavailable)")
    else:
        code, stdout, _stderr = run_devin(binary, ["--version"])
        version_line = stdout.strip().splitlines()[0] if stdout.strip() else "unknown"
        notes.append(
            "devin CLI: %s%s"
            % (binary, "" if code != 0 else " (%s)" % version_line)
        )
        if os.name == "nt" and not _symlink_capable(roots):
            notes.append(
                "symlink privilege missing: plugin mode needs Windows Developer "
                "Mode or an elevated terminal (other modes unaffected)"
            )

    manifest: Dict[str, Any] = {}
    try:
        manifest = load_manifest(roots)
    except KeysmithError as error:
        issues.append(str(error))

    deployment = manifest.get("deployment") or {}
    for record in deployment.get("targets", []):
        target = _absolute_from_record(record, roots)
        if not target.is_file():
            issues.append("manifest target missing on disk: %s" % record["relative"])
            continue
        if record.get("after_sha256") and sha256_file(target) != record["after_sha256"]:
            issues.append("manifest target hash drift: %s" % record["relative"])
        if record.get("existed_before") and record.get("backup"):
            if not (roots.managed_dir / record["backup"]).is_file():
                issues.append("backup missing: %s" % record["backup"])

    plugin = deployment.get("plugin")
    if plugin and plugin.get("registered"):
        if binary is None:
            notes.append(
                "plugin %s registered but devin binary unavailable; cannot verify"
                % plugin["name"]
            )
        else:
            listed = plugin_listed(binary, plugin["name"])
            if listed is False:
                issues.append(
                    "plugin %s recorded as registered but not listed by the devin CLI"
                    % plugin["name"]
                )
            elif listed is None:
                notes.append("could not read the devin plugin list to verify %s" % plugin["name"])
    if manifest.get("transaction"):
        issues.append("incomplete transaction pending; run recover --yes")

    # Plugin-carried AGENTS.md rules shadow every user/project AGENTS rule by
    # name in real sessions (verified live: superpowers v6.3.0 shadowed
    # ~/AGENTS.md). Surface that so append/project deployments are not
    # mistaken for dead tooling.
    shadows = _find_plugin_agents_shadows(roots)
    if shadows:
        notes.append(
            "AGENTS-named rules are shadowed by installed plugin(s): %s — "
            "append/project modes will not reach sessions; prefer global mode"
            % ", ".join(shadows)
        )

    ok = not issues
    exit_code = 0 if ok else 1
    result = {
        "ok": ok,
        "roots": roots.as_dict(),
        "devin_bin": redact(str(binary)) if binary else None,
        "issues": issues,
        "notes": notes,
    }

    if args.json:
        print(redact(json.dumps({
            "schema": JSON_SCHEMA, "tool": TOOL_NAME, "version": VERSION,
            "operation": "doctor", "preview": True, "apply": False, "ok": ok,
            "result": result, "exit_code": exit_code,
        }, indent=2, ensure_ascii=False)))
    else:
        print("%s doctor" % TOOL_NAME)
        print("  home   : %s" % roots.user_home)
        print("  skills : %s" % roots.skills_root)
        print("  managed: %s" % roots.managed_dir)
        for note in notes:
            print("  note   : %s" % note)
        for issue in issues:
            print("  issue  : %s" % issue)
        print("  result : %s" % ("healthy" if ok else "problems found"))
    return exit_code


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--home", dest="home", metavar="PATH", default=None,
                        help="User home override (default: $%s or the OS home)" % ENV_HOME)
    parser.add_argument("--skills-root", dest="skills_root", metavar="PATH", default=None,
                        help="Skills root override (default: $%s or the platform "
                             "Devin skills root)" % ENV_SKILLS_ROOT)
    parser.add_argument("--json", action="store_true", help="Emit stable JSON (%s)" % JSON_SCHEMA)


def build_argparser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="devin-keysmith.py",
        description="Install or inspect devin-keysmith managed instruction deployment.",
    )
    parser.add_argument("--version", action="store_true")
    sub = parser.add_subparsers(dest="command")

    install = sub.add_parser("install", help="Deploy the managed instruction (preview unless --yes)")
    install.add_argument("--mode", choices=list(MODES), default=DEFAULT_MODE,
                         help="Deploy surface: append (~/AGENTS.md), plugin "
                              "(devin plugins install --local), project "
                              "(<git-root>/AGENTS.md), skills (user SKILL.md)")
    install.add_argument("--name", metavar="NAME",
                         help="Skill name (--mode skills) or plugin name suffix (--mode plugin)")
    install.add_argument("--file", metavar="PATH", help="Deploy this file instead of the bundle")
    install.add_argument("--cwd", metavar="PATH",
                         help="Directory to resolve the git root from (--mode project)")
    install.add_argument("--include-skill", action="store_true",
                         help="Also ship a SKILL.md inside the plugin scaffold (--mode plugin)")
    install.add_argument("--force", action="store_true",
                         help="Replace an existing managed deployment")
    install.add_argument("--yes", action="store_true", help="Apply changes (default: preview only)")
    add_common(install)

    uninstall = sub.add_parser("uninstall", help="Remove the managed deployment")
    uninstall.add_argument("--yes", action="store_true", help="Apply changes (default: preview only)")
    add_common(uninstall)

    recover = sub.add_parser("recover", help="Repair an interrupted deployment")
    recover.add_argument("--yes", action="store_true", help="Apply changes (default: preview only)")
    add_common(recover)

    status = sub.add_parser("status", help="Report managed deployment state")
    add_common(status)

    doctor = sub.add_parser("doctor", help="Validate roots, manifest, and Devin CLI consistency")
    add_common(doctor)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_argparser()
    args = parser.parse_args(argv)

    if args.version:
        print("%s %s" % (TOOL_NAME, VERSION))
        return 0

    if not args.command:
        parser.print_help()
        return 2

    try:
        if args.command == "install":
            return do_install(args)
        if args.command == "uninstall":
            return do_uninstall(args)
        if args.command == "recover":
            return do_recover(args)
        if args.command == "status":
            return do_status(args)
        if args.command == "doctor":
            return do_doctor(args)
        parser.print_help()
        return 2
    except KeysmithError as error:
        message = redact(str(error))
        if getattr(args, "json", False):
            print(redact(json.dumps({
                "schema": JSON_SCHEMA, "tool": TOOL_NAME, "version": VERSION,
                "operation": args.command, "preview": None, "apply": None, "ok": False,
                "error": message, "exit_code": error.exit_code,
            }, indent=2, ensure_ascii=False)))
        else:
            sys.stderr.write("[error] %s\n" % message)
        return error.exit_code


if __name__ == "__main__":
    sys.exit(main())
