# Changelog

## 0.1.0 — 2026-09-13

Initial release, forked from the omp-keysmith v0.1.1 chassis.

- Four Devin-native deploy modes:
  - `append` (default): marked block in the user-global `~/AGENTS.md` rule,
    in-place replacement on reinstall, existing content preserved.
  - `plugin`: scaffold + registration through `devin plugins install --local -y`,
    uninstall through `devin plugins remove`; optional bundled skill via
    `--include-skill`.
  - `project`: marked block in the git repository root `AGENTS.md`
    (filesystem walk-up to the `.git` boundary).
  - `skills`: `%APPDATA%\devin\skills\<name>\SKILL.md` (Windows) /
    `~/.config/devin/skills/…` with `name`/`description` frontmatter.
- Preview-first with `--yes` gate; previews print the exact Devin CLI command
  plugin mode would run.
- Manifest ownership at `~/.devin-keysmith/manifest.json` with before/after
  hashes and timestamped backups; atomic writes; `recover` for interrupted
  installs; `--force` fully reverses prior deployments.
- Host protection: Devin binary, `_versions/`, plugin registry/cache,
  `credentials.toml`, `sessions.db*`, `state.vscdb`, `argv.json`, `.bin`
  caches are read-only; credential-shaped values redacted from output.
- Commands: `install`, `uninstall`, `recover`, `status`, `doctor`
  (all `--json` capable).
- Test suite: 41 tests against fixture roots and a fake Devin CLI; no real
  Devin state is touched.
- Verified against Devin CLI 3000.10.21 (build 611c1cba).

### E2E findings (real Devin CLI 3000.10.21 on Windows)

- Append mode: deployed block is read by `devin rules show AGENTS`
  (`[Standard]` provider, user layer); uninstall restores the exact
  pre-deploy hash.
- Skills mode: `devin skills list` immediately shows
  `/devin-keysmith-instruction [user,model]` with the deployed description.
- Plugin mode: the real CLI validates the scaffold (`This install will add:
  devin-keysmith-instruction v0.1.0, rule: AGENTS.md (always on)`), but
  `devin plugins install <local-path>` symlinks the source into
  `%APPDATA%\devin\cli\plugins\cache\` and Windows denies symlink creation
  without `SeCreateSymbolicLinkPrivilege` (os error 1314). Enable Windows
  Developer Mode or run from an elevated terminal; `doctor` probes and
  reports symlink capability.
- Git object files are read-only on Windows: removal paths use a
  read-only-tolerant rmtree.

## 0.1.1 — 2026-09-13

- **Breaking (default mode)**: `install` now defaults to `global` — the
  user-global always-on rule at `~/.codeium/windsurf/memories/global_rules.md`
  — after live-session transcripts proved plugin-carried `AGENTS.md` rules
  (superpowers v6.3.0) shadow every AGENTS-named rule by name: `~/AGENTS.md`
  was listed by `devin rules list` but never injected into sessions.
- `doctor` now detects plugin-carried `AGENTS.md` shadows in the plugin cache
  and names the shadowing plugins.
- New override vars: `DEVIN_KEYSMITH_MEMORIES_ROOT`,
  `DEVIN_KEYSMITH_PLUGINS_CACHE`.
- Verified end to end: a probe session answered in persona
  ("[P] Tôi là Pier. Ethan đã đặt tên cho tôi.") after a global-mode deploy.
- Suite: 46 tests green.
- Bundled instruction hardened with a Devin-specific identity-override header
  (persona body unchanged; bundle re-pinned, sha256
  `401ebf5d0619d40c6399d2b1b452d2b75e5388e6cc0bb77df353cdf9a097c975`):
  stock persona text reached every session (3/3 `[P]` prefix) but SWE-1.6
  occasionally blended identities (1/3 "Devin — Cognition named me"); with the
  header, 4/4 probes answered "[P] Pier — named by Ethan."
