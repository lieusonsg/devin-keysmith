# devin-keysmith

Install a reversible instruction onto the **Devin CLI**. Preview first, write
only after you confirm.

Keysmith installs instructions onto local AI coding tools: preview, write,
verify, remove.

`devin-keysmith` targets the **Devin CLI** (`devin`, verified against build
3000.10.21). Once installed, new sessions follow the instruction. The tool
never modifies the Devin binary, its plugin registry or cache,
`credentials.toml`, session databases, `state.vscdb`, or `argv.json`, and it
never reads accounts or credentials.

> [!IMPORTANT]
> This changes **new sessions** started after install. By default you only
> see the plan; `--yes` writes. Start a fresh Devin session afterward.

> Full install guide (tiếng Việt): [docs/install.md](docs/install.md) — requirements, all modes, verification, troubleshooting, exit codes.

## Usage

1. **Preview.** Nothing is written before confirmation.
2. **Install.** `devin-keysmith.py install --yes`
3. **Open a new Devin session.** Only new sessions pick it up.
4. **Remove anytime.** `devin-keysmith.py uninstall --yes`

```bash
python devin-keysmith.py install                 # preview, writes nothing
python devin-keysmith.py install --yes           # apply (global mode)
python devin-keysmith.py status                  # inspect
python devin-keysmith.py doctor                  # validate host + manifest
python devin-keysmith.py uninstall --yes         # reverse
```

## Four targets

Devin exposes several instruction surfaces; `devin-keysmith` writes exactly
one per deployment, selected with `--mode`:

| Mode | Target | Semantics |
|---|---|---|
| `global` (default) | `~/.codeium/windsurf/memories/global_rules.md` | User-global always-on rule (`[Windsurf]` provider, rule name `global_rules`) injected into every session. **Why default**: plugin-carried `AGENTS.md` rules (e.g. superpowers) shadow every AGENTS-named rule by name in real sessions — verified live — while this rule name never collides. |
| `append` | `~/AGENTS.md` | User-global always-on rule (`[Standard]` provider). A marked block is appended; existing content is preserved and the block is replaced in place on reinstall. Only reliable when no installed plugin carries a root `AGENTS.md` (`doctor` reports this). |
| `plugin` | `~/.devin-keysmith/plugins/devin-keysmith-instruction/` registered via `devin plugins install <path> --local -y` | Devin-native plugin carrying an always-on `AGENTS.md` rule (plus an optional `skills/` entry with `--include-skill`). Uninstall deregisters through the CLI and deletes the scaffold. **Windows caveat**: the devin CLI symlinks local plugin sources into its cache, which requires symlink privilege — enable Windows Developer Mode or run from an elevated terminal (verified limitation, build 3000.10.21; `doctor` reports this). |
| `project` | `<git-root>/AGENTS.md` | Project layer — Devin discovers project rules by walking up to the `.git` boundary (walk-up verified; non-git directories are skipped). |
| `skills` | `%APPDATA%\devin\skills\<name>\SKILL.md` (Windows) / `~/.config/devin/skills/…` | User-global skill with `name`/`description` frontmatter; on-demand (`[user, model]`) activation, invoked by name. |

```bash
python devin-keysmith.py install --mode plugin --yes
python devin-keysmith.py install --mode project --cwd /path/inside/repo --yes
python devin-keysmith.py install --mode skills --name my-instruction --file ./my.md --yes
```

## Verified surfaces (Devin CLI 3000.10.21, build 611c1cba)

| Surface | Location | Activation |
|---|---|---|
| Standard rules | `~/AGENTS.md`, `<git-root>/AGENTS.md` | always-on |
| Claude rules | `~/.claude/CLAUDE.md` | always-on (read-only for us) |
| Windsurf rules | `.windsurf/rules/*.md`, `~/.codeium/windsurf/memories/global_rules.md` | always-on (read-only for us) |
| Cursor rules | `.cursor/rules/*.md` | conditional |
| User skills | `%APPDATA%\devin\skills\<n>\SKILL.md`, `%APPDATA%\cognition\skills\`, `~/.agents/skills/` | on-demand |
| Plugins | `.devin-plugin/plugin.json` + root `AGENTS.md`; registry under `%APPDATA%\devin\cli\plugins\` | rules always-on, skills on-demand |

`devin rules list`, `devin skills list`, and `devin plugins list` show what
Devin currently picks up — use them to verify a deployment.

## Safety guarantees

- **Preview-first.** No write, and no Devin CLI invocation, happens without
  `--yes`. Previews print the exact `devin plugins install` command that would
  run.
- **Manifest-owned.** Every file the tool creates or modifies is recorded in
  `~/.devin-keysmith/manifest.json` with before/after hashes and a timestamped
  backup. Unmanaged files are never touched.
- **Atomic writes.** Temp-file-then-rename; a crash never leaves a half-file.
- **Reversible.** `uninstall --yes` restores pre-change bytes or removes
  created files; `recover --yes` repairs an interrupted install; `--force`
  fully reverses a prior deployment (including plugin deregistration) before
  replacing it.
- **Pinned bundle.** The bundled instruction ships with a pinned SHA-256;
  a tampered bundle is refused before anything is written.
- **Host protection.** The Devin executable, `_versions/`, plugin
  registry/cache, `credentials.toml`, `sessions.db*`, `state.vscdb`,
  `argv.json`, and `.bin` caches are read-only, enforced on every operation.
  Credential-shaped values are redacted from all output and the manifest.

## Environment overrides (testing)

| Variable | Effect |
|---|---|
| `DEVIN_KEYSMITH_HOME` | Overrides the user home used to resolve all roots |
| `DEVIN_KEYSMITH_SKILLS_ROOT` | Overrides the skills deploy root |
| `DEVIN_KEYSMITH_DEVIN_BIN` | Overrides the Devin CLI executable used in plugin mode |
| `DEVIN_KEYSMITH_MEMORIES_ROOT` | Overrides the Windsurf memories root (global mode) |
| `DEVIN_KEYSMITH_PLUGINS_CACHE` | Overrides the plugin cache used for shadow detection |

Binary discovery for plugin mode: `DEVIN_KEYSMITH_DEVIN_BIN` → `PATH` →
`%LOCALAPPDATA%\devin\cli\bin\devin.exe`.

## Tests

```bash
python -m pytest tests/ -q
```

The suite runs entirely against fixture directories and a fake Devin CLI; it
never touches real Devin state. Python 3.8+, stdlib only (pytest for tests).

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Success (including deliberate no-op) |
| 1 | Expected failure (bad mode, missing git root, CLI registration failure) |
| 3 | Refused: target path is host-managed/read-only |
| 4 | Bundled instruction hash mismatch |
| 5 | A managed deployment already exists (use `--force` or uninstall) |

## License

MIT — see [LICENSE](LICENSE).
