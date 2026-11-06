# doeff-linter layer rules and editor-facing output — specification

This document describes the layer rules DOEFF101-108 and the conventions of `--output-format editor-json`. The source of truth for rule judgments is doeff-linter;
the editor (doeff-runner) only displays this output and does not make judgments itself.

## 1. Invocation

| Invocation | Meaning |
|---|---|
| `doeff-linter --output-format editor-json [<path>…]` | Judges the whole repo. If a path is given, only the violations and modules of files under it are emitted (judgment is still made over the whole repo) |
| `doeff-linter --output-format editor-json --stdin --path <file>` | Judges the stdin contents as `<file>` (pre-save contents). Only that file's violations and modules are emitted. Rules that judge only over the whole repo are not run (the rules judged are named by `judged_rules` — section 2) |
| `doeff-linter --output-format editor-json --semantic <file>` | Judges the definitions of the saved `<file>` by asking Jev (semantic rules — section 10) |
| `doeff-linter --output-format editor-json --stdin --path <file> --semantic --semantic-changed` | Asks Jev only about definitions in the stdin contents whose contents changed (definitions with no answer in the cache). Definitions that are half-written and unreadable are not asked (for when the editor stops typing during editing) |
| `--config <file>` | The settings file. It may be in the form of a pyproject.toml with `[tool.doeff-linter]`, or a TOML containing only the section contents |
| `--root <dir>` | The repo root. Layer locations, the registry, and key paths are relative to it |
| `doeff-linter --output-format editor-json --baseline-report <file> [<path>…]` | Compares against the editor-json output `<file>` produced at the baseline (the tip of main), and emits criticals not present at the baseline in `new_critical` (see "Comparison with the baseline" below) |
| `doeff-linter --commit-hook --report-out <file>` | Runs the commit hook (section 3, "Scope the commit hook applies to"), and writes the two measurements, the HEAD version and the staged paths (the list of measured paths, editor-json violations, and judged rules), as one JSON to `<file>` (section 3, "Commit hook report"). The blocking decision, exit code, and stderr text are the same as when there are no arguments |

- Order of searching for settings: `--config` if given. Otherwise search upward from the current dir for a pyproject.toml that has `[tool.doeff-linter]`.
- Repo root: `--root` if given. Otherwise the settings' `root = "<path relative to the settings file's dir>"` (for a monorepo,
  make a package's `src/` the root so module names match import names). Otherwise the dir of the pyproject.toml found. When `--config` is given, it is
  the current dir (the settings' `root` is relative to the dir of the `--config` file).
- `--stdin` and `--path` can be used only with editor-json. `--stdin` without `--path` gives exit code 2.

### Exit codes

| Code | Meaning |
|---|---|
| 0 | No error violations (warnings and info may be present) |
| 1 | There are error violations (new violations not in the registry) |
| 2 | Bad argument, unreadable settings, mismatched names in the settings, or a value of the wrong type (reason on stderr) |
| 3 | No error violations, but something could not be measured (not green): a definition that semantic rules should have asked about got no answer (Jev unreachable, no key, or calibration could not be fired), or a named Hy file is outside the range the linter walks (see "Outside the named scope" below, #2821) |
| 4 | Only with `--baseline-report`: there is a critical not present at the baseline (a new critical). Judged before 1 and 3 (2 always takes top priority) |

### Outside the named scope

Layer rules judge only the Hy files walked under the repo root (`root`). Naming a Hy file outside the root (for example, the `tests/` of a package whose root is `src`)
judged nothing, and previously exited with code 0 silently — which can be read as "measured and passed". Now, among the named paths (file or dir — a dir expands to the Hy files
under it), Hy files outside the walked range are named, and if there are no error violations the exit code is 3.

- **text / json output**: one line per file on stderr: "`doeff-linter: out of scope — <path>(outside the range the linter walks = Hy files under root <root> — …)`".
  `<path>` is a normalized absolute path (the same form as a violation's path).
- **editor-json output**: the top-level field `out_of_scope` (a list of normalized absolute paths in lexicographic order — the same form as a violation's `path`). `null` for a run with no names (`.`) and for `--stdin`.
  The version is not bumped (a field addition). The commit hook (`--commit-hook`) reads this field and names them one per line (it does not block — it names what was not measured).
- **Not counted**: non-Hy files (Python rules gather files from the named paths and apply, so they measure even outside the range), nonexistent paths,
  `--modified` (it is not naming), and the service read and the layer declaration file (the settings' `architecture` or the root's `architecture.hy` — even if placed outside the root,
  the declaration rules judge it. doeff-cluster's commit hook always adds this file when it passes paths).
- **Files within the walked range that no rule judges are not counted** (as of the current version): files outside layer locations, and files under a segment matching `:exclude` (default `tests`)
  are not judged by layer rules even under the root, but are not named here. Widening the criterion to "whether any rule judges it" (`project::is_judged_file`)
  is not included now (decision 2 of #2821 — because it would block commits that touch files under the root that nothing judges. The numbers are in the table of plan A-2).
- Reverting: remove this field, `project::hy_files::outside_walk`, and its 3 callers (the normal entry, editor-json, and the commit hook).

### Dirs added to the walked range (the `include` setting)

`[tool.doeff-linter] include = ["tests"]` (relative to the settings file's dir) — adds a dir outside the root to the walked range. Walked range = under the root + under the
`include` dirs (defined in one place = `project::hy_files::walked`).

- **What it enters**: the population of the definition-writing rules (whole-project runs and one-file runs — selected by `paths` and `test_paths` of `[tool.doeff-linter.definitions]`),
  unreadable files (DOEFF128), and the "outside the named scope" judgment above. **It does not enter the layer rules** (layer locations are only under architecture's `:root`).
  The set of defk names that bare defk calls (DOEFF126) compare against is collected from under the root (defks in `include` dirs are not counted).
- **Naming**: a file outside the root is a relative path from the root that contains `..` (`../tests/x.hy` — the same as the declaration file key `paths::declared_rel`). Violation keys,
  `definitions.paths` (e.g. `"../tests"`), and `test_paths` (e.g. `"../tests/**/test_*.hy"`) are matched against this naming.
- **A nonexistent dir** is a settings error (it does not silently walk nothing).
- Reverting: delete the settings line (the package's settings). To revert the whole feature, delete `include`, `walked`, `walked_rel`, and their callers.

### Comparison with the baseline (`--baseline-report`)

The pre-merge check and the commit hook keep no saved list of known violations; they run the linter at both the baseline and the commit, and pass if the set of the commit's critical identifiers is
contained in the baseline's set (decision A of #1762). Running the baseline side is the caller's job; the linter only reads the baseline's output file and compares
(if the linter rebuilt the git tree itself, the fact cache and the handling of the repo root would be doubled).

- **What is compared**: violations whose `level` is `critical` (including those lowered to warning by being in the registry — severity is not lowered by the registry).
- **Identifier**: `<path>::<rule>::<name>` (line numbers not included). For a violation with a key, the `key` itself; for a violation without a key (Python's per-statement rules), the `message` in place of the name.
- **A set, not a count**: a commit that fixed 3 and added 1 has one new critical even though the count dropped.
- **File move / rename**: when the rule and name are the same, only the path differs, and the baseline's identifier is now gone, they are regarded as the same violation, one to one.
  If the baseline's identifier remains and the same name appears at another path, it is a new violation.
- **Output**: the top-level field `new_critical` of editor-json (a list of identifiers in lexicographic order). `null` when not compared with a baseline. The version is not bumped (a field addition).
- **Scope**: in a run that names paths, only violations under the named paths are compared. The baseline's output is also produced by naming the same paths (the caller's job).
- **Rules with no list of known violations** (rules the registry does not lower — DOEFF206-209, 211): in a run that names paths (the files staged for the commit hook, and the checks of
  the changed parts before registration), every critical of that rule under the named paths goes into `new_critical`, even if the baseline has the same identifier — a changed file
  with a hit is blocked (granularity is per file). A run with no names compares only against the baseline difference (the overall count is reported daily).
  Rules that declare they need the whole repo (DOEFF206, 207, 209) are not applied in the commit hook; the whole-repo verification looks at them (see
  "Scope the commit hook applies to" below).
  The commit hook writes the blocking hits divided by whether the same identifier exists in the HEAD version (`commit_hook::split_by_head` — the blocking decision is unchanged):
  those that do not are written as "not on HEAD critical: <identifier>", and those that do as
  "existing hit in a changed file (also on main; this rule is not lowered by the known list): <identifier>", and if the latter exist, one line is added:
  "If you change this file, fix that hit in the same change (do not skip the hook)" (existing hits were also written as "not in HEAD",
  which was read as a hook malfunction).
- **Errors**: exit code 2 if the baseline file is unreadable, is not JSON, has no `violations` list, or when used with text / json output.
- Reverting: delete this field, the argument, and `src/baseline.rs` (no impact if no caller uses it yet).

If the settings (any level of pyproject's `[tool.doeff-linter]`, or architecture.hy) contain a **key this binary does not know**, or a well-formed
rule ID (`DOEFF` plus 3 digits) that this binary does not have, it does not stop with exit code 2. It skips reading only that key (reference), runs the remaining rules, and emits at that line of the settings file a
warning violation **DOEFF100** (a key the settings do not know). Settings can get ahead of the binary (after a new key is written, until
the binary in the store is rebuilt from the main line), and stopping everything then would empty the editor's violations pane and silence the hook. A typo
looks the same way. DOEFF100 is emitted even if it is not in the `enable` list, and stops only when named in `disable`. The source of truth for known keys is the definition of the settings struct
(`serde_ignored` collects keys that are not in the definition, with paths — no key table is kept by hand).

`--version` names the doeff commit it was built from (`doeff-linter 0.2.0 (doeff <commit>)`). The commit is the build env
`DOEFF_LINTER_BUILD_COMMIT` (passed by the automatic rebuild), or, for a hand build, git's HEAD (`+dirty` if the linter or indexer dir has uncommitted changes
).

With `--modified` (text, json), layer-rule violations are also emitted only for changed files.

Known violations in the registry (warning) and violations of rules under reconciliation (info) do not give exit code 1. This is so that CI and agent hooks "block only new violations"
(this decision can be reverted; to revert, replace `EditorReport::has_errors` with whether any violation exists).

## 2. Shape of editor-json (version 2)

```jsonc
{
  "version": 2,
  "linter": {"version": "0.2.0", "commit": "<sha>"},  // the binary that produced this output and the doeff commit it was built from (a field added to version 1)
  "root": "/abs/repo",                       // normalized repo root
  "layers": [                                // layer order (farthest from the outside world first) and descriptions — from settings layers.describe (fields not given are null)
    {"name": "core", "summary": "business judgment and Program", "knows": "…", "does_not_know": "…", "question": "question to ask when unsure"}
  ],
  "violations": [
    {
      "rule": "DOEFF101",                    // rule ID
      "law": "core-imports-only-intent",     // name of the law tied in settings (null if none)
      "adr": "ADR-…",                        // the law's ADR (null if none)
      "severity": "error",                   // error | warning | info
      "path": "/abs/repo/controllers/core/goal.hy",
      "range": {"start": {"line": 12, "character": 8}, "end": {"line": 12, "character": 40}},
      "message": "…",                        // what the violation is (English)
      "hint": "…",                           // one line on how to fix it
      "key": "controllers/core/goal.hy::core-imports-only-intent::controllers.foundation.records",  // layer rules only. null for Python rules
      "registered": false,                   // whether it is in the registry
      "base_severity": "error",              // the rule's own severity before lowering by the registry or reconciliation (field added in version 2)
      "standing": "new",                     // new | registered (known in the registry) | reconciling (lowered to info while reconciling)
      "level": "critical",                   // severity of the rule (settings rules.<ID>.level; otherwise from base_severity) — not lowered by the registry
      "explanation": {                       // layer rules only (null for Python rules)
        "subject": "what this is (the import target and its layer / the definition and its kind / the file's layer, and what decided it)",
        "reason": "why it is a violation (a sentence connecting the layer's description and the rule)",
        "law_statement": "verbatim :statement of the tied law, or null"
      }
    }
  ],
  "modules": [                               // modules in the layer population (material for the map)
    {"path": "/abs/…/goal.hy", "layer": "core", "context": "kanban", "role": "program", "violations": 1,
     "service": "kanban",                     // the service that matched the `*` segment of the location (null if layers come first)
     "layer_reason": "decided by the path's location — under controllers/core/ is layer core (…). The tag role = program is also a role of this layer"}
  ],
  "rules": [                                 // enabled rules, and laws with no hook (whether judged in this run is in judged_rules)
    {"rule": "DOEFF101", "adr": "ADR-…", "statement": "core-imports-only-intent: …", "wired": true,
     "title": "import against the layer direction",      // short English name (the shape of the violation)
     "family": "layer"}                       // rule family (layer, tags, raw, naming, place, definition, class, wire, smell, jev, python, law)
  ],
  "judged_rules": ["DOEFF016", "DOEFF101", "NOQA001"],  // IDs of rules judged in this run (lexicographic order; contract update 8) — see below
  "errors": [],                              // reasons for files, registry, and catalog that could not be read
  "new_critical": null,                      // only with --baseline-report: list of identifiers of criticals not at the baseline (section 1, "Comparison with the baseline")
  "out_of_scope": null                       // only for a run that names paths: named Hy files outside the walked range (section 1, "Outside the named scope")
}
```

- Positions are 0-based lines and UTF-16 code unit columns (the same as VS Code's Position). Columns are counted in UTF-16 even on lines containing Japanese or emoji.
- Line-only rules (DOEFF001-031) take as the range from the start of the violation's text to the end of the line.
- `path` is a normalized absolute path (with symlinks and `..` resolved). A file inside the repo root becomes `root` joined with the path from the repo root, in both whole runs and `--stdin` runs, so the editor can replace results by the same path. When narrowing by path arguments, they are compared with the same normalization.
- `modules[].context` and `role` are the module's head tags, or the first definition's tags if none. Files outside the layer population do not appear in `modules`.
- `rules`: for each enabled rule, one entry per tied law if it has any (`statement` = `<law name>: <law statement>`), otherwise one entry with the rule's statement.
  A layer rule whose settings section is absent is `wired: false` (it emits no violations). A law with an empty `rules` (a law with no hook) puts the law's name in `rule` with `wired: false`.
  `title` is a short English name (the shape of the violation; for a law with no hook, "a convention with no automatic check yet"). `family` is the rule family
  (a closed set: `layer`, `tags`, `raw`, `naming`, `place`, `definition`, `class`, `wire`, `smell`, `jev`, `python`, `law`.
  Python's per-statement rules (DOEFF001-031, NOQA001, unknown IDs) are `python`, and a law with no hook is `law`) — used when the editor groups and shows
  the list of rules. The linter holds the judgment of names and families; the editor keeps no copy.

- `severity` is the severity after lowering, and `base_severity` is the rule's own severity. An error in the registry goes to `registered_severity` (default warning),
  a warning in it goes to info, and a rule under reconciliation goes to info. The editor uses these two and `standing` to count "how many violations of heavy rules remain, new versus known"
  (the judgment of the reason for lowering is the linter's). Python's per-statement rules are not lowered, so the two are the same and `standing` is `new`.

- `judged_rules` (contract update 8): IDs of the rules judged in this run (lexicographic order, no duplicates). Judged = the rule's hits were
  fully emitted (rules with 0 hits are listed too). Every rule in `violations` is in this list.
  - Whole run: all enabled rules. A whole run that names paths excludes only DOEFF166 (lines of the registry that do not match) (matching findings may lie outside the named paths).
  - One-file run (`--stdin --path`): all of Python's per-statement rules, and those layer rules that declare they can judge on one file
    (`ProjectRule::judged_on_one_file`). False rules — rules whose hits attach to architecture.hy, registry lines, or a declared `:files` group
    (DOEFF117, 141, 147-149, 159-163, 166), rules that follow the definition graph over the whole repo (DOEFF132, 133, 136, 137, 143, 155-158, 164, 165, 167),
    and rules that do not yet have a one-file branch (DOEFF135, 146) — are not run and are not in this list.
  - A run that runs Python's rules also lists the noqa notice `NOQA001`. DOEFF100 (a key the settings do not know) and DOEFF128 (an unreadable Hy file) are listed in both kinds of run,
    regardless of the list of enabled rules.
  - Using the result of a one-file run, the editor replaces only the violations of the rules in this list, for that file, and leaves violations of rules not in the list as in the whole run's result.
    Old output with no such field replaces all of the file's violations as before.
  - It is not made a field of `rules[]` because `rules[]` is the list of enabled rules and does not carry the rules of violations emitted outside it (NOQA001, and DOEFF100 / DOEFF128 which are emitted even if not in enable).
    The version is not bumped (a field addition — bumping the version would make old editors that read only versions 1 and 2 discard the whole output and leave the violations pane empty).

- `signatures`, `bindings` (version 2): the headings and binding types of defk / deff in a `--stdin` Hy file. Empty lists in a whole run. Shape and reading are in section 16.
- `rewrites` (field added to version 2): display replacements for a `--stdin` Hy file that show calls in definition bodies in the form `f(a, b)`. Empty list in a whole run. Section 17.
- `bodies` (field added to version 2): per-definition lines of body text in a `--stdin` Hy file (drawn by the reading pane). Empty list in a whole run. Section 20.

### Explanation sentences (explanation, layer_reason)

- The sentence templates exist for each rule in one place only, `src/project/explain.rs`, and the layer name, location, layer description (`layers.describe`),
  role description (`roles.describe`), layers that allow raw side effects, and the law's statement are inserted from the settings. No layer meaning is written in Rust.
- A layer with no description gets its sentence built only from the layer order ("the layer farthest from the outside world", "the second layer in order of distance from the outside world", ...) and the rule's convention.
- `layer_reason`: the layer is decided by the path's location. When the tag's role is not allowed in that layer, it says "the path and the tags disagree" and adds the layer
  that allows that role (or "not a role of any layer" if none). The same disagreement is also written in DOEFF105's subject.
- The human-readable output (text) and the agent hook's text also emit, for each layer-rule violation, lines for "what this is", "why", "law", "how to fix", and "key".

## 3. Settings

The fields of each section are as in the README's "layer rules" example. On reading, the following are checked, and if any one disagrees it gives exit code 2 and a list of reasons:
a layer appearing twice in `order`, a layer name not in `order` (keys and values of `paths` and `allow_imports`, `forbid_modules`, `types_only`, `roles.by_layer`,
`raw_side_effects.allowed_layers`, `laws[].layers`), a role not in `roles.names`, a `registry.reconciling` that is not a layer rule, a `laws[].rules` ID not in doeff-linter, a `layers` of a law that has only layer-independent rules (DOEFF108), a layer location that is empty, `.`, an absolute path, or contains `..`, nested layer locations, `roles` and `tags` without `layers`, and unknown fields directly under `[tool.doeff-linter]` and in each section.

| Section | Fields | Default |
|---|---|---|
| `layers` | `order`, `paths` (layer -> dir), `exclude` (exact match of dir or file names), `extensions` | extensions = hy, hyk, hyp, py |
| `layers.allow_imports` | layer -> layers it may import | layers not written are unrestricted |
| `layers.forbid_modules` | layer -> spellings of modules not to import directly (prefix match — `urllib.request` matches `urllib.request.urlopen` but not `urllib.parse`) | none |
| `layers` | `types_only` (layer names), `function_definers` | defk, deff, defp, defpp, defhandler, defn |
| `tags` | `module_variable_hy`, `module_variable_py`, `contract_definers`, `plain_definers`, `effect_definers`, `record_definers` | MODULE-TAGS, MODULE_TAGS, defk deff defp defpp defhandler, defn defclass defenum, defeffect, defrecord defwire (the head dict's `:tags` is read as the definition's tags) |
| `roles` | `names`, `by_layer`, `describe` (role -> description. Old roles removed from the list can be written too) | DOEFF105 is not applied to layers with no by_layer |
| `layers.describe.<layer>` | `summary`, `knows`, `does_not_know`, `question` | if absent, the description fields are null |
| `raw_side_effects` | `allowed_layers`, `catalog_extra` (JSON in the same form as hy-index's `--raw-catalog-extra`) | — |
| `environment_names` | `words`, `paths`, `exclude`, `exclude_parts`, `extensions`, `assembly_files` | extensions = hy, hyk, hyp, py |
| `program_sleep` | `paths`, `exclude`, `exclude_parts` (the population of DOEFF210 = where Hy programs live. If the section is absent, DOEFF210 does not judge) | if paths is empty, all Hy of the repo |
| `test_sleep` | `parts`, `names`, `exclude` (the population of DOEFF211 = where Hy tests live. If the section is absent, DOEFF211 does not judge) | — |
| `laws` (array) | `name`, `adr`, `statement`, `rules`, `layers` | if layers is empty, all layers |
| `translation_effects` | `handler_layers`, `intent_layers`, `max_depth` (DOEFF130 — section 21. DOEFF174's reach uses the same `max_depth`), `domain_layers` (DOEFF174's business layers — if written, a wrong name is a settings error) | protocol, intent, 8; domain_layers is whichever of core and intent are in the declaration |
| `commit_hook` | `whole_repo_rules` (a retired key — not read, just named in one line. Whether a rule needs the whole repo is declared by the rule, and the hook does not apply that rule), `timeout_s` (the limit in seconds per child linter run; 0 means no limit; when exceeded, it is treated as unmeasurable and the commit is blocked — see "Commit hook limit" below) | none, 20 |
| `registry` | `dirs` (a dir of one-key-one-file), `files` (one key per line), `config_files` (one key per line, relative to the settings file's dir), `reconciling` | dirs and files are from the repo root |
| `rules.<ID>` | `registered_severity` (severity of a violation in the registry: error, warning, info) | warning |
| `rules.<ID>` | `level` (severity of the rule: critical, major, minor, info. Can be written for any rule. Not lowered by the registry — the axis on which the editor counts "untouched criticals") | the "default severity" table below; rules not in the table take it from the rule's own severity (error = major, warning = minor, info = info) |

`enable` and `disable` take effect on both Python rules and layer rules (`ALL` includes both).

The linter that fires `--commit-hook` is the one built once on the machine from the cross-section of the doeff commit each repo pinned — the entry point of the build is
`packages/doeff-linter/scripts/linter_snapshot.py` (`uv run --script <path> <doeff checkout> <commit>` prints the path of the binary in the store;
if it cannot build, it ends with 1 and the caller falls back to its own environment's linter). Repos do not keep a copy of the build.

### Scope the commit hook applies to (cisco-c8's decision of 2026-10-11)

`--commit-hook` applies, among enabled rules, only those decided by the contents of a single file plus the settings (the `quick` of `--split-rules` — regardless of whether the declaration file changed),
to the staged paths and to their HEAD versions (including the HEAD versions of files deleted in the stage). It does not measure the whole repo (path `.`).

- What it does not apply: hits of rules that declare they need the whole repo (rules whose `whole_repo` in `--list-rules` is true) — both those inside staged files and those attached to files outside
  the change (architecture.hy, registry tables, other sources). Hits attached to unstaged files in a commit that changed the declaration file.
  All of these are looked at by the whole-repo verification (each repo's daily run — every 4 hours for agora-controllers).
- Reason: the whole-repo comparison (measuring the tip tree and the HEAD tree over the whole repo) takes 45-100 seconds and 1.5-1.7 GB per run in agora-controllers, and the 23 conversations
  fixing the daily red committed at the same time and filled the machine (zeus load 102/24 cores, swap all used — 2026-10-11). A whole-repo rule builds the whole-repo inference world even when
  applied to one file (DOEFF132 takes 28 seconds on one file, about 20 seconds in total for 42 rules), so applying it only to staged paths does not make it lighter.
- The split of the gate's entry `--split-rules` (a change to the declaration file sends all rules to `whole`) is unchanged — the caller uses it for the whole-repo comparison.

### Commit hook limit (original issue #2723)

`--commit-hook` cuts off each child linter run at a limit in seconds (`--commit-hook-timeout-s` wins over the settings' `commit_hook.timeout_s`; default 20).
0 means no limit (it measures to the end without cutting off).

- The limit is measured in the child's CPU seconds (utime and stime in `/proc/<pid>/stat`, plus cutime and cstime of waited-for descendants). On a busy machine
  only the wall seconds grow, and when it was measured by wall, a commit with 0 hits was cut off every time and blocked unless there was no limit (zeus load 42-73). A child that does not finish without using CPU
  is cut off by a wall fallback limit (limit x 4). On a machine where the child's CPU seconds cannot be read (a Mac with no /proc), wall seconds are used as before.
  The decision is in one place, `cut_measure`, and the blocking line names the yardstick (`cut off at the CPU-seconds limit of N s`, `cut off at the wall limit of N s (child CPU seconds
  cannot be read on this machine)`, `cut off at the wall backstop limit of M s (... a child that does not finish without using CPU)`).

- When cut off, it blocks the commit with exit code 1 (before, it passed with 0, and workers did not re-check the commit that passed, and a DOEFF167 hit from a commit that changed
  the declaration file got into main).
- When it blocks, stderr has 3 lines (the head is `doeff-linter commit-hook: `):
  1. Which comparison, on which tree, for which rules, was cut off at which yardstick's limit of how many seconds (`... could not be measured — the child linter of ... was cut off at the CPU-second limit of N seconds.
     The hits of this rule have not been checked, so the commit is blocked ...`).
  2. `Command to redo the same comparison without a limit: <command>` — one line made of the same linter's absolute path, `--commit-hook`, `--root <root>`, `--config <settings file>`
     (or `--no-config` if none), the `--enable` / `--disable` arguments, and `--commit-message <file of the body>` (if given),
     plus `--commit-hook-timeout-s 0`. In a form that can be pasted into a shell as is (words containing spaces and the like are enclosed in ').
  3. `Confirm that this command exits with code 0 (0 hits that stop the commit) before committing again. Do not use --no-verify`
- The judgment when not cut off is unchanged.
- The tree that measures the HEAD version is placed one per repo under the cache root and advanced by diffs (#3858), so from the second run only changed files are rewritten.

### Commit hook report (`--report-out`)

`--commit-hook --report-out <file>` writes the hook's two measurements (the HEAD version and the staged paths) as one JSON to `<file>` (a relative path is from
the current dir). A conversation's commit hook sends this report, with the conversation id, to the record, as material for reading, per commit, the violations the conversation added or removed
(point 2 of the design record project-health-ide).

```jsonc
{
  "root": "/abs/repo",            // root of the tip (the root of violation paths)
  "code": 1,                      // the hook's exit code (see "When not judged" below)
  "measurements": [
    { "target": "HEAD",  "paths": ["app/x.hy"], "violations": [ /* editor-json violations (section 2) */ ], "judged_rules": ["DOEFF016", "NOQA001"] },
    { "target": "stage", "paths": ["app/x.hy"], "violations": [ /* same */ ], "judged_rules": ["DOEFF016", "NOQA001"] }
  ]
}
```

- **Measurements**: `target` = what was measured (`HEAD` = the HEAD version, `stage` = the version of the staged paths measured in the working tree). `paths` = the paths passed to the child linter
  (relative to the root — for the HEAD version, the staged paths that exist in HEAD plus files deleted in the stage). `violations` = in the same form as the child linter's editor-json violations
  (`violations[]` in section 2 — for the HEAD version, after re-rooting the HEAD tree's paths under the tip's root, so they can be compared with the stage version by the same path).
  `judged_rules` = the child linter's editor-json `judged_rules` (only rules decided by one file — rules that need the whole repo are not applied by the hook, so they are not listed).
- **Measurements that fire no child** (empty stage, no rules to apply, no paths the linter reads, the HEAD version of a first commit or only-new files): that measurement's
  `paths`, `violations`, and `judged_rules` are empty. There are still two measurements, HEAD and stage.
- **When not judged**: when cut off at the limit (exit code 1) or when git or the linter did not run (exit code 2), `measurements` is empty.
- **Unchanged**: the blocking decision, exit code, and stderr text (the same as when there are no arguments). The editor-json version (the child's output fields are carried as they are).
- **When it cannot be written**: one line is added to stderr, `doeff-linter commit-hook: cannot write report <file> (exit code unchanged): <reason>`, and the exit code is
  unchanged (recording diagnostics does not block the commit).
- Passing it to a run without `--commit-hook`, or to the gate's entry `--split-rules`, is an argument error (exit code 2). It is not added to the no-limit redo command (the "Commit hook
  limit" above).
- Reverting: delete the argument `--report-out`, `commit_hook::Measurement`, `report_of`, and the write-out in `commit_hook::run` (no impact if the sending hook is not yet using it).

### Default severity

Violations of responsibility boundaries are critical in every repo (because keeping the responsibility boundaries is the premise for keeping doeff code in a form that tests can verify). Copying per repo would let one side go silently stale, so the default table is placed in one place in doeff-linter (`ProjectRule::default_level` —
an exhaustive match, so a new rule cannot be added without deciding), and a repo's `rules.<ID>.level` in settings writes only what differs from the default (if written, the repo's declaration wins).

| Default | Rules |
|---|---|
| critical — responsibility boundaries | DOEFF101 (layer direction), 102 (module forbidden in a layer), 103 (function in a types-only layer), 106 (direct evidence of a raw side effect), 109 (service boundary), 116 (service dependency), 130 (a translation handler emits a business intent) |
| critical — others | DOEFF114, 115 (location not in the declaration), 126 (calling a defk bare — a Program flows as a value), 128 (unreadable file — judgment is missing, and 0 hits does not mean pass), 210 (a program bypasses the handler and sleeps on the wall clock) |
| major — Jev's judgment (until calibration is done) | DOEFF201 (business judgment in a translation layer), 202 (means of communication in a judgment layer), 205 (shape checking mixed with judgment) |
| from the rule's own severity | all rules not above (including Python's per-statement rules DOEFF001-031) |

Jev's rules (201, 202, 205) are not made critical until calibration is done and they can be trusted (the change to
critical in #942 was reverted). They can also come out as info by probability, but since level is an axis of the rule, info hits are counted as major too (misses go to the list of misjudgments — section 10).

## 4. Population

- **Location pattern**: the value of `layers.paths` is a spelling or a list of spellings. The segment `*` matches a service name (at most one, the whole segment). When a file
  matches two locations, the one with more segments wins; if equal, the earlier layer in the layer order.
- **Population of the definition rules** (DOEFF110-112): Hy files under `definitions.paths` (if empty, all Hy of the repo — `.venv`, `node_modules`, `target`, and `.git` are not descended into)
  that are not under `definitions.exclude` and whose path segments are not in `definitions.exclude_parts`.

- **Layer population**: files under each layer's dir whose extension is in `layers.extensions` and none of whose path segments is in `layers.exclude`.
  A module's spelling is the path from the repo root with the extension removed and `/` replaced with `.` (`__init__` is left as is).
- **Business files** (DOEFF108): files under any of `environment_names.paths` (a trailing `*` is a prefix match on the path), not under `exclude`,
  none of whose segments is in `exclude_parts`, and whose extension is in `extensions`.
- **Program locations** (DOEFF210): among the Hy files in the walked range (under the root, plus the settings' `include`), those under any of `program_sleep.paths` (if empty,
  all; a trailing `*` is a prefix match on the path), not under `program_sleep.exclude`, and none of whose segments is in `program_sleep.exclude_parts`.
  It is kept separate from the population of the definition rules (`definitions`) — so this rule alone can be applied to programs in locations where the definition-writing rule group is not applied.
- **Test locations** (DOEFF211): among the Hy files in the walked range (under the root, plus the settings' `include`), those where any path segment (excluding the file name) is in
  `test_sleep.parts`, or the file name matches any glob of `test_sleep.names`, and which match none of `test_sleep.exclude`
  (an exclude line means everything under it if it is a dir, the file itself if it is a file, and a trailing `*` is a prefix match on the path). It is kept separate from program locations —
  a program's sleep (DOEFF210) excludes handler implementations, and a test's sleep (DOEFF211) excludes the places that declared realtime.

## 5. Rules

A key is `<path from the repo root>::<segment>[::<detail>]`. `<segment>` is the name of the law tied to the rule and the file's layer, or the rule's ID if none.

| Rule | How it judges | Detail | Position |
|---|---|---|---|
| DOEFF101 | Resolves the import target to a module in the population (that module itself, or a `module.name` module), and if it is another layer outside `allow_imports`, it is a violation. Outside the population (doeff, the standard library) is not counted. Imports of the same target count as one | spelling of the import target | the first import symbol |
| DOEFF102 | If the spelling of the import target is the same as one of `forbid_modules`, or a submodule / name under it, it is a violation (the first one hit when checked in name order). One per module hit | spelling of the module hit | the first import symbol |
| DOEFF103 | One if a module in a `types_only` layer has a function definition (in Hy, a top-level expression whose head is in `function_definers`; in Python, a top-level def) | `definitions` | name of the first function |
| DOEFF104 | There is a definition without tags and the module has no head tags either, or there is no definition at all and no head tags | none | name of the first definition without tags / line 1 |
| DOEFF105 | For each effective tag, it is a violation if role or context is missing or empty, or the role is outside `roles.by_layer` (the same key may appear many times in one module) | role (`None` if absent) | the tag dict |
| DOEFF106 | It is a violation if the per-definition direct evidence of hy-index version 3 (raw.direct) is in a Hy definition in a layer outside `allowed_layers`. In a repo that writes `:world-handlers` in architecture.hy, it is not allowed by layer but only in the files of the modules of definitions in the roster (`allowed_layers` cannot be written, and it applies even without a raw_side_effects section). Strong evidence is error, weak evidence is warning. Evidence that overlaps through nesting counts once, for the inner definition | `<definition>::<evidence name>` | the evidence symbol |
| DOEFF107 | Emits evidence by way of (raw.via — whole-project runs only) as info, with the path. One per definition for the same path and evidence name. It does not enter definitions declared in architecture.hy's `:world-handlers` (the raw side effects beyond them are that definition's responsibility). If reached by another path that does not go through a declared definition, it hits | `<definition>::via::<path>::<evidence name>` | the definition's name |
| DOEFF131 | Names, outside the roster's definitions (and nested definitions within them), a doeff real-I/O handler listed in `:wraps` of `:world-handlers`. Both references passed as values (the list of `with-handlers`) and calls count; import lines do not. The target of naming is the index reference's `target` (the same name resolution as calls). One per same handler in the same definition. Default severity critical (#1140) | `<definition>::world::<module:name>` | the symbol named |
| DOEFF106, 131 (outside layers) | In a repo that writes `:world-handlers`, DOEFF106 and 131 are also applied to Hy files outside the layer locations (dirs outside the layers, and dirs outside `:root` listed in `:raw-io-roots` — only `:root` if not written). Test files (path segment in :exclude; name test_ / conftest), files with defadr, and hits inside deftest are excluded (edge tests are DOEFF133). The message says "outside the layer locations" | same as DOEFF106, 131 | same |
| DOEFF132 | Each definition in `:world-handlers` — the module is not in a layer location (no such module, or outside the layers), is in a layer other than foundation, or a Hy module has no such definition. Whole-project runs only. Default severity critical (#1141) | `<module:name>` | the roster element in architecture.hy |
| DOEFF133 | Derives the kind of test (local / edge) from what it reaches — builds, in the whole index, the edges between definitions (calls, references, nesting), and traces backward from the roster's definitions, definitions that name a `:wraps` handler, and definitions with strong raw I/O evidence. A deftest that is reached is an edge test and needs the `:edge-mark` marker (a deftest's `:marks` or the module's `pytestmark`), and a deftest that is not reached must not have the marker. Mismatches are emitted as critical, and the edge message writes the path by which it is reached. Python tests are not counted (R6). A Python function in the repo named by a Hy definition (`a/b.py` or `a/b/__init__.py` from the repo root) is traced with no depth limit through strong raw I/O evidence in it (names in the catalog reached by import, builtins at the head of a call — type annotations and except types are not counted) and callees decided by name (functions in the same module, imported functions, class construction and `self.m` / `cls.m`, calls back into Hy definitions). Attribute calls on values and runtime imports are not traced (the same range as the Hy graph). The symbol of an element of a deftest's `:interpreters` is resolved from the import to the definition of a name constant, and that definition, plus the assembly that the definition module's dict literal pairs `{name-constant assembly …}` (the table conftest uses to look up name -> assembly) assigns to that name, are added to what the deftest reaches — a contract test where only the real assembly reaches the outside world is an edge test. String elements (names looked up by conftest's Python table) are not traced. Modules with broken syntax or that cannot be read are named in the report's errors. It applies only when `:edge-mark` is written (#1142) | `<definition>::edge` / `<definition>::local` | the deftest's name |
| DOEFF137 | An allowlisted handler with no edge test — for each handler in `:world-handlers`, one hit if no deftest that has a non-empty `:interpreters` (an element of `:interpreters` is a symbol of a constant outside the file, so it is not resolved, and only whether it is a non-empty list is looked at — reading a deftest's settings dict is the same as DOEFF133's `:marks`) reaches that handler's definition by following the same definition-edge graph as DOEFF133 (calls, references, nesting). A deftest without `:interpreters` that merely reaches it is not counted. A handler with a reasoned `:contract-test (none …)` is not judged; a reasonless `:contract-test none` is one hit even if an edge test exists (#1796). For each pair (stand-in, real) in the counterpart roster `:stand-ins`, one hit if no deftest (a contract test) has a non-empty `:interpreters` and reaches both the stand-in's definition and the real definition by the same graph (when the real one is in a package outside the index, it is traced from definitions that name the real one directly. A definition that holds the interpreter-name string `"module:name"` / `"module:name@<variant>"` is also counted as a definition that names that handler; `:contract-test` is the same as `:world-handlers`). Whole-project runs only; the conditions to enable it are the same as DOEFF133 (`:world-handlers` and `:edge-mark`). Default severity critical | `<module:name>`; for a stand-in pair, `<stand-in module:name>-><real module:name>` | the roster element in architecture.hy |
| DOEFF135 | Tests are only deftest — selects files by the spelling types of `:test-forms {:tests [..] :check-scripts [..] :runners [..]}`, and emits one per file for: `def test_*` in Python files of :tests (python-test; the count goes in the message), per-module skips in files of :tests (`pytest.skip` at the unindented level, `allow_module_level`, `mark.skip` of `pytestmark` — a multi-line binding where the binding name and the marker are on different lines is also read until the parenthesis closes, pointing at the line of the binding name; #1426), files of :check-scripts (check-script), and files of :runners (runner). Whole-project runs only. Default severity critical (#1144) | `python-test` / `module-skip` / `check-script` / `runner` | the head of the file or the line of the first hit |
| DOEFF136 | A service not run in a simulated environment — in a repo that writes `:verification-environment`, for each service that has an entry layer, trace callers backward (calls, references, nesting — the same graph as DOEFF133) from the definitions in that service's entry-layer dir (`<root>/<service's dir>/entry`), and if no deftest under the simulated environment's dir is reached, critical at the defservice position (key = the service's name). A service that declares an entry layer but has 0 definitions is not counted (there is no assembly to run). Tests inside a service are outside the simulated environment, so they are not counted. The index also reads `defsystem` (doeff-cluster's system declaration) as a definition, so the path deftest -> defsystem -> entry also reaches (R5, #1111). |
| DOEFF163 | A service that does not declare invariants — for each service that has code (the same population as DOEFF136: it has entry in :layers and one or more Hy definitions under `<root>/<service's dir>/entry`. However, a service whose defservice writes `:entry-modules` enters the population by declaration, and one hit if there is no Hy file for that module), one hit if the defservice's `:invariants` (a list of business-invariant functions `"module.path:function"`) is absent or empty, one hit if the named function's definition is not in the repo's Hy index, and one hit if the definition's `:tags` `:role` is not `judgment` (location does not matter — `*_invariants.hy` in the simulated environment is fine). The position is the defservice's name. Default critical; a gap in the registry is a warning. Whole-project runs only | `<service>` / `<service>::<module:function>` / `<service>::entry::<module>` | the defservice's name |
| DOEFF173 | A service that does not declare a system — for every defservice in architecture.hy (services without an entry layer are also counted): one hit if the defservice's `:system` (the system placed in the cluster, `"module.path:defsystem name"`; a list if there are several) is absent or empty; one hit if the reason of `:system {:exempt "reason"}` is empty (exempt may be written for a service left on the old path, or for a component service with no process — no main and no deploy, used only by being read by other services. The linter does not judge the content of the reason); and for each name pointed at (`:system {:part-of "module.path:defsystem name"}` = that service's code runs inside another service's system: it applies to the one system pointed at, and the location is relaxed to "an entry layer of some service" — a non-part-of declaration pointing at another service's system is red): one hit if there is no definition or it is not a defsystem (only defsystem gets the static description `__doeff_system__` — judged by the index kind), one hit if the module is outside that service's entry layer (outside that module for a service with `:entry-modules`), one hit if the argument count is not one, one hit if a type annotation on the foundation argument (`[#^ T foundation]`, doeff L983) is present and the head of that type is a bare `Callable`, a mapping, or a tuple with no element types (the annotation is counted as a name, and the job functions' types are not looked at); and if there is no annotation, for each job function that takes one argument (the foundation) as a positional argument, one hit if that argument's type (`(: argument type)` in `:pre`) is absent or its head is a bare `Callable`, a mapping, or a tuple with no element types (`dict`, `Mapping`, `FrozenMap`, `tuple`, `list`, `Any`, `JsonBody`, etc.), and one hit if no job takes the foundation. If a defsystem exists it is automatically included in the generic simulated cluster test, so a per-system test is not required (paired with DOEFF136). The position is the defservice's name. Default critical. Whole-project runs only | `<service>` / `<service>::exempt` / `<service>::<module:name>` / `<service>::<module:name>::params` / `<service>::<module:name>::unused` / `<service>::<module:name>::<job function>` | the defservice's name |
| DOEFF142 | Client or mutable store as a handler argument — for each argument of a `defhandler` (including nested ones) in a Hy file matching :files of `:handler-arguments {:files [..] :exclude [..] :store-names [..] :store-suffixes [..] :keep-mark "…" :value-types [..]}`, classifies it as: `client` (type name ends in Client / Connection / Pool / Engine; name is client / conn / connection / ...-client / ...-conn), `container` (dict / list / set / bytearray / MutableMapping ... — including `(get dict …)`), `object` (a class that is not frozen in the index of the repo's Hy and Python classes, and a type not in the index — frozen dataclasses, frozen settings, Enum, NamedTuple, defrecord, and :value-types are values), or `named` (no type annotation and the name is in :store-names / :store-suffixes). A handler whose body (up to the next top-level form) has a comment with :keep-mark is not counted. The judgment is decided only by the file passed, and the class index is looked up through a per-file cache (even a one-file run does not re-read the repo). Default severity critical (#1366) | `<handler>::<argument>` | the argument's position |
| DOEFF143 | A fake handler that answers a business effect — `:business-fakes {:simulation [..] :assembly [..] :tests [..] :skip [..] :production [..] :sets [..] :simulation-prefix "…" :production-prefix "…" :entry-string-modules [..] :entry-string-files [..] :business-modules [..] :lower-layer-modules [..] :external-effects "dir" :counterexamples "dir" :unserved "dir"}`. The definition-edge graph (the same as DOEFF133; edges inside a system's values also count as edges that run in production) is traced forward from the simulation roots (definitions in :simulation and :assembly, and definitions of :simulation-prefix in files of :sets) and from the production entries (definitions of :production-prefix of :sets among files of :production, defsystem, names inside `(when (= __name__ "__main__") …)`, and entry strings `"<module>:<name>"` — the body of :entry-string-files is also read). A defhandler clause reachable only from the simulation roots that answers an effect of :business-modules not as a tap (in the clause form below: with not a single ending that passes the received effect straight through), and is in neither the external-world table nor the counterexample table, is critical (key = `<handler>::<effect>`). A clause reachable only from the simulation roots that answers an effect of :lower-layer-modules not as a tap is also critical (a fake of a lower layer is only the lower layer's one canonical version; key = `lower:<handler>::<effect>`). Table rot is also emitted: a row of the external-world table that no fake answers, or that has no production answerer (an `isinstance(effect, X)` in Python of production code also counts as an answerer; those listed in :unserved are excluded). The clause form (the one judgment used in common by DOEFF143, 155, 156, 157, 158, 164, 167, 206): each clause ending is classed as either passing the received effect straight outward (re-performing — a name bound with `(<- name [type] effect)`, `(<- name (head …))` that lists the received arguments as they are, `(setv name (yield effect))`, or `(with_handlers [...] re-perform)`, or a name that was rebound from it with `(:= name bound-name)` and the like, whose `resume` / `transfer`; a clause's `(resume None)` that re-performed the same effect; `(reperform effect)`; `(pass)`; or a `:when` clause), or a value of its own (`resume` / `transfer` of any other value, `finish`, `reperform` of a different value, an answer re-performed under `with_handlers`), and `(resume (if condition A B))` is classed per branch. A clause with a passing ending is a tap (excluded from the fake judgments of DOEFF143, 155, 156, 157, 158 — including fault injection that re-performs in only one branch), and a clause with an own-value ending is a clause that needs a table row (hits of the counterexample table, DOEFF164, 167, 206). A clause can have both. `raise` is not counted as an ending. If a clause's fields contain the name `effect`, that name is not the received effect. Clauses of the same head of the same handler are combined field by field with OR. The names, ways of ending, and argument counts of the operations that make clause endings (`resume` and the others listed here) are not written in this rule's source; it reads the generated artifact `data/clause_ops.json`, written out from doeff-hy's clause-operation table `CLAUSE-OPS` (`packages/doeff-hy/src/doeff_hy/_clause_ops.hy`) (`src/project/clause_ops.rs`) — an operation that ends by resuming is judged by its value, one that passes through by whether it has arguments and by the re-performing expression, one that finishes by its own value, and an operation that exits by exception is not counted as an ending. If a row is added to the table, rewrite it with `uv run python scripts/export_clause_ops.py --write` and the judgment follows (forgetting to rewrite and writing directly into the file turn `tests/test_linter_clause_ops.py` red, and writing a name directly into the source turns the test in business_fakes.rs red). A table is a dir of one-key-one-file (the first line is the key, the reason from the second line). A nonexistent dir is read as an empty table (one line of notice) for :unserved (a shrinking-direction table that pins violations), but cannot be read (errors) for the external-world table and the counterexample table (declarations). Whole-project runs only. Default severity critical (#1189) | `<handler>::<effect>` / `lower:<handler>::<effect>` / `external-unused::…` / `external-unserved::…` | the clause's position, the table's dir |
| DOEFF176 | Stale rows of the counterexample table that nothing hits — the declaration is the `:counterexamples` of `:business-fakes`, the same as DOEFF143. A row of the counterexample table matches no clause of any deliberately broken handler (clauses reached only from DOEFF157's tests are also checked — clauses that answer a business effect or an effect of :lower-layer-modules. What counts as a hit on the table is a clause that has an ending returning its own value, and clauses that re-perform in only one branch count too). The judgment is made in the same one place as DOEFF143, and it is split from DOEFF143 so that the count of fakes (DOEFF143) and the count of stale table rows can be counted separately by rule number. Default severity critical; how it is lowered by the registry is the same as DOEFF143 | `counterexample-unused::<row key>` | the table's dir |
| DOEFF155 | Violations of assembly shape — `:assembly-shape {:translation-point "with-*-translation" :retired-function "…" :translations "…" :translation-layer "…" :intent-layer "…"}` (the assembly layer, the set files, the business modules, and the external-world table are read from `:business-fakes` — only when both are written). What it emits: the production file of :sets remains; a definition of :retired-function remains in the assembly layer; in the one point of the assembly layer's translation list (a defk whose name is of the type :translation-point), `(with-handlers [#* list …] body)` lists imported names that are not a translation list (a translation list = a :translations constant at the :translation-layer's location, or a list in an old module of a service outside the location whose elements are all from business answerers outside the location; a list of val in the same module is opened into); list ordering (no list that answers an :intent-layer effect of another service that this list re-performs comes before it); a :translations constant lists definitions of another service's :translation-layer; the one point of the translation list lists its own argument with `#*` (it does not receive the list from the caller); and the dir of `:verification-environment` and the files of :tests open and list translation lists with `#*` (the lists the one point may list, and the lists that the assembly layer's :translation-layer handlers or translation lists are bundled into) (simulation and tests call the same one point as production). Whole-project runs only. Default severity critical (#1189) | `set-file` / `retired` / `stray:<name>` / `parameter:<argument>` / `loose:<list>` / `order:<list>:<effect>` / `own:<definition>` | the definition's position, the set file, the definition where it was opened |
| DOEFF156 | The translation target or the foundation answers a business effect — the declaration is the same as DOEFF155. A translation handler reached from a :translations constant names a business effect it does not itself answer (always for :intent-layer effects; otherwise those that handlers of the same list do not answer); every handler in the same list that answers the re-performed effect comes after it in the list (only when the constant is a literal list); and among the handlers reached by the set-function of a :sets file (:simulation-prefix / :production-prefix), those outside :translations in the same module answer a business effect not as a tap. A business effect = an effect of :business-modules that is not in the external-world table. Whole-project runs only. Default severity critical | `target:<handler>:<effect>` / `inner:<handler>:<effect>` / `foundation:<set function>:<handler>:<effect>` | the handler's or set function's position |
| DOEFF157 | Test-only fakes — the declaration is the same `:business-fakes` as DOEFF143. A clause of a definition reached from the test roots (all definitions in files of :tests) and reached from neither a production entry nor a simulation root answers an effect of :business-modules or :lower-layer-modules not as a tap, and is in neither the external-world table nor the counterexample table: critical (copies of the business in the production code's location that only tests use are counted too). A deliberately broken counterexample handler is listed in the counterexample table with a reason. Whole-project runs only. Default severity critical (#1189) | `<handler>::<effect>` | the clause's position |
| DOEFF158 | The answerer of an intent effect is not a single translation — the declaration is the same as DOEFF155 (both `:business-fakes` and `:assembly-shape`). Among clauses reached from production entries, it counts those where the effect's defining module is at a location in the :intent-layer layer and which answer not as a tap. Critical if the clause's file is outside the :translation-layer layer, and if two or more :translation-layer handlers answer the same effect, it emits all of them (ADR R9 — one and the same translation for production and simulation). Whole-project runs only. Default severity critical | `outside:<handler>::<effect>` / `shared:<handler>::<effect>` | the clause's position |
| DOEFF164 | A service with no counterexample of a broken handler — the declaration is the same as DOEFF143's `:counterexamples` of `:business-fakes`, plus `:verification-environment`. A counterexample clause = a clause that matches a key in the counterexample table, is not reached from a production entry, and has an ending returning its own value (DOEFF143's clause form). The owner is the service dir that contains the file defining the clause's effect (a foundation effect if it is not under any service). For each service that has definitions in its entry layer, it is satisfied if even one deftest reaches (tracing the same graph as DOEFF136 backward) a counterexample clause owned by that service or by the foundation and also reaches a definition in that service's entry layer; if there is none, critical at the defservice position (lowered by registered_severity if it is in the registry). In addition, the table matching of DOEFF143 and 157 counts a clause as a hit for a clause of any effect, as long as the key of a clause that is not reached from production and has an own-value ending is in the table (a broken handler that answers a foundation effect can also be listed in the table). Whole-project runs only. Default severity critical (#1155) | the service's name | the defservice's position |
| DOEFF165 | Missing coverage of intent effects — the declaration is the same as DOEFF158 (both `:business-fakes` and `:assembly-shape`). For each defeffect whose declaring file's location is in the :intent-layer layer, three columns are read from the same reachability graph as DOEFF143: whether it is emitted from a local test (among the definitions reached from the test files' definitions, those that name that effect, excluding answerers [effect clauses, defhandler] and declarations), a simulation answerer (a handler of an effect clause reached from the simulation roots), and a production answerer (a handler of an effect clause reached from the production entries). An effect with any column empty is one hit, and the three columns are shown in the body and in `--explain`. The owner service is the defservice whose dir contains the declaring file's location (longest prefix match). Effects declared by a Python class are not counted (outside the index graph). Whole-project runs only. Default severity critical (#1561 K3 turns the report-only table into a failure. Known gaps in the repo's registry are warnings) | `<service>::<effect>` (`-::<effect>` for an effect with no service) | the effect declaration's position |
| DOEFF206 | The verification environment itself answers an intent effect — three declarations, `:business-fakes`, `:verification-environment`, and `:assembly-shape` (:intent-layer) (the conditions to enable it are `:business-fakes` and `:verification-environment`. Without :intent-layer there are 0 hits). There are two kinds of hits: (a) Rows of tables that let violations through — `:external-effects` (key = full name of the effect), `:lower-layer-passages`, and `:test-only-fakes` (key = `<path>::<handler>::<effect>`; what follows the last `::` is the effect) — that let through an effect whose defining module is in the :intent-layer layer. It is emitted at the row's file. A nonexistent dir for `:lower-layer-passages` or `:test-only-fakes` is read as empty (one line of notice). (b) A handler clause in the verification environment's dir (under `<root>/<:verification-environment>/` — read the same as the location exemptions DOEFF114 and 115, `Architecture::verification_environment_dir`) answers an effect of the intent layer. What is excluded is only a clause that has no ending returning its own value (DOEFF143's clause form — every ending passes the received effect straight through) and a clause with the key `<path>::<handler>::<effect>` in the counterexample table. A clause that changes the re-performed answer, builds another value, or passes its own value in only one branch needs a table row (the same judgment as DOEFF143, 164, 167). The intent-layer judgment is the same as DOEFF158 and 165 (the layer of the file of the module defining the effect) — effects of doeff's package do not hit. Whole-project runs only. Default severity critical. **Not lowered by the registry** (even if listed, it is error and not registered) | (a) `pass:<table key>`, (b) `verification:<handler>::<effect>` | (a) the table row's file, (b) the effect's clause |
| DOEFF207 | A pair of an outside location that a production system's job reads has no writer — the declaration is architecture.hy's `:outside-writers {"<location>:<name>" "writer" …}` (a table of pairs written by writers that are not production-system jobs — tools in outside repos, human operations, tasks that are not jobs — and the names of the writers). It applies only to repos that write it. The population is the same production system as DOEFF173 (the systems pointed at by a defservice's `:system` and by `{:part-of …}`; the same system once) — the jobs of simulation and test defsystems that are not production systems count neither reads nor writers. There are two kinds of hits: (a) a production system job's row has no `:reads` or `:writes` (optional fields in doeff's defsystem; `#{}` if unused). (b) a job's `:reads` pair is in no production job's `:writes`, no `:writes` of the contract of a defk or deff reached from a production entry, and not in the `:outside-writers` table (a form that reads a record with no writer in production, and in the local simulation a stand-in makes that part and goes green). A writer that is not a job (such as a task's Program passed from a system job by a foundation value) declares itself with `:writes #{…}` in the definition's contract dict (doeff-hy's FUNCTION-CONTRACT-KEYS; only the shape is checked at expansion time). The production entries are the same as DOEFF143 (production code's defsystem in `:business-fakes`, the production set function, definitions named by the `__main__` clause, and entry strings), and only declarations of definitions reached forward from there are counted — declarations of simulation-only definitions or unused definitions are not counted. In a repo that does not write `:business-fakes`, only a job's `:writes`. The shape of the field values (a set of strings, with the spelling `<location>:<name>`) is checked by doeff-hy's defsystem expansion — here the spellings are only collected and matched. When the named system does not exist or is not a defsystem, DOEFF173 emits it. Whole-project runs only. Default severity critical. **Not lowered by the registry** (this kind of violation is found by doeff-linter) | `<system>::<job>::reads`, `<system>::<job>::writes` (missing field), `<system>::<job>::<pair>` (a read with no writer) | the job's row |
| DOEFF208 | Production code wakes a job by waiting on a record change — the declaration is `:business-fakes` (applies only to repos that write it). The population is the Hy files of production code (files that match `:production` and do not match `:tests`, `:simulation`, or `:skip` — the same range of declaration as DOEFF143's production entries and DOEFF207's writers that are not jobs), excluding the inside of the doeff-records package (path segment `doeff_records`) — this rule looks at users (the owner of the effect and the source deletes the source itself when switching to the event foundation). There are two kinds of hits: (a) making a record change the source of events — a call to doeff-records' signal-source factories `read-signal-handler` and `records-signal-handler` (matched on the last segment of the head's spelling — `event-source.read-signal-handler` also hits). (b) waiting on a record change — emitting `WatchChanges` or `WatchEvents` with seconds (`:timeout`, or the third positional argument) that is not a literal 0 (`0`, `0.0`). Variables, expressions, and splats (`#*`, `#**`) cannot be called 0, so they hit. The form without seconds is the default 0 (read once without waiting) and does not hit. What does not hit: a read with seconds 0 (the form where the receiver reads the record to catch up at startup or reconnect), writes to records, re-performing inside a defhandler that answers a record effect (where the head of a clause right below is `WatchChanges` / `WatchEvents`), the spelling in a `match` pattern, comments, and forms discarded by `#_`. Python files are not looked at. Whole-project runs only. Default severity critical. **Not lowered by the registry** (the hits that exist now are not put in a table to pass — a design that writes records and waits on records is found and forbidden by the linter, separating records from startup) | `<enclosing top-level definition's name>::<head's spelling>` (e.g. `with-turn-events::read-signal-handler`, `waited-signal::WatchChanges`) | the call's head symbol |
| DOEFF209 | A Program in production code repeatedly goes to fetch the same thing with a time wait in between — the declaration is `:business-fakes` (applies only to repos that write it). The population is the same Hy files of production code as DOEFF208 (files that match `:production` and do not match `:tests`, `:simulation`, or `:skip`), and unlike DOEFF208 it does not exclude the inside of the doeff-records package (this rule looks at the writers of the wait itself). What it looks at is the source of the time at which it wakes: (1) wait spellings — `(Delay seconds)`, `(WaitTicks _ seconds …)`, and module-qualified `sleep` (such as `time.sleep`) (a yield with seconds being the literal 0, and deadline waits containing `(- deadline now)`, are excluded), and the time of `ArmTimer` / `ArmedTimer` (second positional or `:at`) that contains `(+ now …)` or contains a name bound from `(+ now …)`. now = within the same definition, names bound from `GetTime`, `now-ms`, `clock-ms`, and from definitions with no arguments whose body calls them, and names bound by a conversion call that takes only such a name. For a wait inside a repetition, only a now bound inside the repetition is counted (a time computed from a now read once before the repetition is the same time every iteration). (2) Helpers — the repo is re-read until no change and collected (not held in a roster): helpers that pass a time (an argument flows to the time position by following rebindings), helpers that receive now (the time contains `(+ argument …)` or a name bound from it), helpers that return a wait (definitions that have a wait spelling outside the repetition), and effects answered by waiting (a `defhandler` clause that always waits without going through a branch — clauses of WatchChanges and WatchEvents excluded). The head of a call is resolved to a definition in the same file or by `(import module [name])`, and an unresolvable `a.b` is resolved when the name matches exactly one. (3) Repetition — inside the body of a `while`, `loop`, or a clause of `event-loop`, or a definition that calls itself (`for` iterates over a finite collection, so it is not counted). A definition applied once and exited is not hit; the call inside a repetition that calls that definition is hit. The inside of a call that starts another task (`Spawn`, wrappers with spawn in the name) is not looked at (a single wait of the Program passed). (4) The key argument `:poll-seconds` that passes a re-ask interval to a library (a value that is not a literal 0) hits regardless of repetition. What does not hit: deadlines derived from rows or schedules, the upper-limit seconds when waiting for a change (DOEFF208's side), request timeouts, `match` patterns, the head of a handler clause (`(Delay [seconds] …)`), inside quote and `#_`, ways of picking up completion outside doeff's scheduler (Python — outside the rule), and deadlines for timing out (where the tag of `ArmTimer` / `ArmedTimer` is a top-level `val` / `setv` name, at least one branch `(cond (= tag name) body …)` / `(if (= tag name) body …)` comparing against that tag exists in the population, and every body is one that exits the repetition with `(stop …)` — the upper-limit seconds for waiting for a stop signal. A tag for which even one body repeats the same read after arriving is not excluded). The classification is the last segment of the key detail: `periodic` (purely re-fetching by time) and `retry` (re-fetching after unreachable — the wait spelling, the tag of the deadline set, the expression binding the time, the type of the enclosing match clause, or the helper's name contains retry / unreachable / unavailable, or all waits in the helper are retry). Hits of the same head and same classification in the same definition are merged into the one at the first place, and the remaining lines are shown in the body. The reason to pass by writing it at the place: on the line of the hit, or on a comment-only line immediately before it, `; 時間で取り直す理由: <word>` (placed the same way as DOEFF111's `; cannot be a defk: <reason>`; the marker spelling `時間で取り直す理由` = "reason for re-fetching by time" is matched by the code, so it is kept in Japanese). The words are a closed set — `相手に変更の知らせが無い: <counterpart> — <how it was confirmed>` (= "the counterpart has no change notification"; both the counterpart's name and the basis of confirmation [a section of the counterpart's API document, a line of the counterpart's code, or the command tried and its result] are non-empty. Counterparts known to have change notifications [Kubernetes, the coordinator's GET /watch, the doeff-events foundation, and the record service's change signal — matched by whether the counterpart's name, lowercased with spaces removed and `_` made `-`, contains any of the aliases (kubernetes, k8s, kube-apiserver / coordinator, /watch / doeff-events / 記録のservice, 記録の変更の合図, agora-record, doeff-records)] are not passed, and the body says "this counterpart has change notifications"), `書くだけ: 生存の印` / `書くだけ: 報告` / `書くだけ: 期限の延長` (= "write only: liveness mark / report / deadline extension"), and `届かない間だけの繋ぎ直し` (= "reconnecting only while unreachable"; only for hits of classification retry — a form that tries, with a pause, whether it connects only while an unreachable mark or failure answer exists, does not go to fetch data, and clears the mark when it returns). A word outside the set or one that does not fit the classification is not passed, and the body adds the reason. Whole-project runs only. Default severity critical. **Not lowered by the registry** (repeated fetches are all to be fixed, limited to the last resort) | `<enclosing top-level definition's name>::<head's spelling>::<classification>` (e.g. `watch-custody::wait-own-deadline::periodic`, `making-loop::ticked::retry`) | the call's head symbol |
| DOEFF210 | A Hy program sleeps on the wall clock — the population is the settings' `program_sleep` (program locations — applies only to repos that write it; section 4). A call inside the body of a defk or defp (including inside an fn, defn, or deff that is literally inside) whose head is `time.sleep` / `asyncio.sleep` is a violation. The head is resolved from imports in the file (including those inside functions): `t.sleep` for `(import time :as t)`, names bound by `(import asyncio [sleep])` and `(import time [sleep :as nap])`, and `sleep` for `(import time *)`. For `(Await (asyncio.sleep seconds))`, the inner call hits. What does not hit: a call whose seconds is the literal 0 (it only yields once), handler implementations (the body of a defhandler; a defk / deff / defn / fn whose argument list is `[effect k]`; a defk / defp whose contract dict's `:tags` `:role` is `foundation` — only when the definition writes it in its own dict, not exempted by the module's head tags), definitions that are not programs (top-level deff, defn, and methods of defclass), comments, `#_`, and quoted forms. A `sleep` that is not from a sleep module (your own function, another library) is not counted. It is decided by a single file (one-file runs judge it too). Default critical (doeff's ADR-DOE-TIME-001 R1 and R2 — Python's same convention is semgrep's doeff-no-sleep-in-programs) | `<program's name>::<time.sleep or asyncio.sleep>` | the sleep call's form |
| DOEFF211 | A Hy test sleeps on the wall clock without declaring that real time is the subject — the population is the settings' `test_sleep` (test locations — applies only to repos that write it; section 4). A call whose head is `time.sleep` / `asyncio.sleep` (import aliases and bound names are read the same as DOEFF210, and the method spelling `(.sleep time seconds)` too) and the rephrased sleep `(.wait (threading.Event) seconds)` (waiting with seconds on an Event made on the spot) are violations. The kind of definition does not matter (deftest, defn, defk, deff, methods of a class, top-level expressions). What does not hit: a sleep whose seconds is the literal 0, comments, `#_`, quotes, the inside of strings, and places that declared realtime — the whole of a file with a module top-level `(val pytestmark pytest.mark.realtime)` (`var` too; the value is one marker or a list or tuple of markers), the inside of a deftest with `"realtime"` in `:marks` of the meta map (the first element after the argument list and the leading string), and the inside of a `defn` with `pytest.mark.realtime` in its decorator list (the `[…]` right after the head). A `setv` binding, a non-top-level binding, and a map of a non-deftest definition are not declarations (only the forms doeff-hy records as pytest markers are read). The detail of the key is `<enclosing top-level definition's name>::<sleep function>` (`<module>` outside a definition). Not lowered by the registry. The matching against the roster that lists the declared tests together with reasons is held by the repo's own test (in doeff, tests/semgrep/test_realtime_tests_registry.py). Definition = `src/project/test_sleep.rs`, test = `tests/test_sleep.rs` (doeff's ADR-DOE-TIME-001 R8) |
| DOEFF174 | A business layer uses a lower layer's effect or handler directly — the declaration is the `:lower-layer-modules` of `:business-fakes` in architecture.hy (applies only to repos that write it; if not written, it shows as wired false in the list of rules). The population is the top-level definitions (`defk`, `deff`, `defn`, `defp`, `defhandler`) of the Hy files of the business layers (settings `translation_effects.domain_layers`; if not written, whichever of the default names core and intent are in the declaration). What hits: (a) a call executed in the body (`(<- …)`, `(! …)`) whose head is from a module of `:lower-layer-modules` (e.g. `ReadRow` of `doeff_records._effects`) — if the head is a repo defk, it is traced with the same reachability as DOEFF130 down to `max_depth` (including forms that reach through a shared defk). Executing a definition outside the repo, in another module of the same package (the module obtained by dropping the last segment of the `:lower-layer-modules` spelling — `doeff_records` for `doeff_records._effects`), such as a defk that wraps effects like `read-typed` of `doeff_records._typed`, also hits — definitions outside the repo are not in the inference table and cannot be traced beyond, so executing one is regarded as a raw operation at that point. (b) The name of a handler placed in an element of the `with_handlers` list or as the first argument of `with_handler` / `WithHandler` (the head if the element is a call, the symbol if a symbol, the inner symbols for a `#*` splat) that is from the same package (e.g. `read-signal-handler` of `doeff_records._event_source`, `http-records-handler` of `doeff_records._http_client`). What does not hit: imports and uses of value types (`Row`, `KeepFor`, `RowType`, `typed-row`, `ChangedRow`, `SignalTables` — those that appear neither in the head of an executed call nor in the handler list being applied. Imports are not forbidden at the module level — `event_source` puts types and handlers in the same module), domain effects (types of the intent layer), definitions of the translation layer, foundation layer, and assembly layer, and inside quotes. **Forms it cannot catch**: places that apply a handler received as an answer or an argument as a value (`(with_handlers (list opened.handlers) …)` — field reads, arguments, and expression handlers whose names are not statically determined are not counted. The translation handler on the side that builds the applied value fixes this by not passing raw handlers to the business layer). The first one place in body order per definition and per name. One-file runs (the editor) judge it too. Default severity critical, **not lowered by the known list (registry)** (raw reads and writes of records are a handler's job, not a job's. A form in which a handler translates business effects into record effects and a job emits record effects raw is forbidden by the linter. Scope: inside the cluster only handlers [protocol and foundation layers] use the record service's raw operations, and outside only the domain API) | `<definition name (mangled)>::<effect or handler name including its module (mangled)>` (e.g. `tail_reading::doeff_records._event_source.read_signal_handler`) | the executed call's head, or the symbol of the applied handler |
| DOEFF167 | An invariant clause that has neither a counterexample nor a reason for exclusion — DOEFF164 (green if a service has one counterexample), made finer per clause. A clause's name is the value returned by an `:invariants` function and cannot be read statically, so it is declared in the defservice's `:clauses ["clause" …]`, and a clause with no counterexample gets its reason written in `:clause-exemptions {"clause" "reason" …}` (inside `:clauses`; the reason is non-empty). Which clause a counterexample clause breaks is declared in lines `breaks: <service>::<clause> …` from the second line on of the counterexample table (one key per file for `:counterexamples`). The population and the effective counterexample clauses are the same as DOEFF164 (services with definitions in the entry layer; clauses that match a table key and are not reached from a production entry). For each clause, it is satisfied if even one deftest reaches the declaring clause and reaches a definition in that service's entry layer, a clause excluded with a reason is not counted, and if neither exists, critical at the defservice position (key detail `<service>::<clause>`). A service that has code but does not write `:clauses` gets one under the service's name. A `breaks:` that names a service or clause not in the declaration, or a spelling mistake, is a settings error (errors). Whole-project runs only. Default severity critical | `<service>::<clause>` (for a missing declaration, the service's name) | the defservice's position |
| DOEFF168 | A business layer branches on an environment-name value or a dry-run flag — in Hy files of the layers in `:layers` of `:environment-branches`, it counts the string literals of `:values` that appear in the arguments of comparison forms (`=`, `!=`, `is`, `is-not`, `in`, `not-in`; also elements of collection literals) and in the type of a `match` clause, and the symbols named in `:flags` inside branch forms (the condition of `if`, `when`, `unless`, each condition of `cond`, the subject of `match`; compared after mangling the last dot-separated segment). Comments, forms discarded by `#_`, quoted forms, and anything outside a branch condition are not counted. Locations whose layer is unknown are outside the layer rules, so they are not counted. The spellings and layers exist only in the repo's declaration. Default severity critical | `value:<spelling>` or `flag:<spelling>` | the literal or symbol hit |
| DOEFF169 | A keyword field name in a match class pattern contains `-` — over all Hy files in layer locations (no settings needed), hits keyword arguments whose name contains `-` in a class pattern (a parenthesized form whose head is a symbol other than `\|` — nested ones inside the type and the arguments of class patterns are followed too) inside the type of a `match` clause (the `:if` guard is not counted as part of the type). Hy 1.3.1's `match` emits the class pattern's keyword as `case Class(a-b=…)` without mangling it into an attribute name, so that clause matches no value and silently falls through to the next clause (often the default `_`). A field written with `_` (`:a_b`), positional-argument patterns, keywords in a clause's body and guard, outside a match, comments, quotes, and `#_` do not hit. Inside a `(defk …)` form (arguments, contract, body) does not hit either — defk converts the field names of class patterns of matches inside it into attribute names with `hy.mangle` at expansion time (doeff-hy's `doeff_hy/_match_fields.py`), so `:a-b` matches there. The key detail is `<class>:<field name>`. Default severity critical | `<class>:<field name>` | the keyword's position |
| DOEFF170 | A layer that passes values by type alone has a defwire — hits top-level `defwire` declarations in Hy files of layers declared with `:wire-free True` in architecture.hy's layers (TOML settings use `layers.wire_free`). Wire types (declarations with the field-name mapping `:names`) and parse / dump are placed in the translation layer, and this layer holds only types that have no field names. In a repo with no declared layer, the judgment is not wired. Default severity critical | `wire:<name>` | the defwire's name |
| DOEFF171 | Contracts and fields in a layer that passes values by type alone contain mappings or tuples with no element types — among the `:pre [(: argument T)]` and `:post [(: % T)]` of defk, deff, and defn, the field types (`(#^ T name)`) of defclass, defrecord, and defwire in Hy files of `:wire-free True` layers, and the field annotations of classes in Python files of the same layers (dataclass, TypedDict, class `name: T` — added; before, only Hy files were counted, and a `Mapping[` field in core's .py was silently missed), it hits mappings (dict, Dict, Mapping, MutableMapping, JsonValue, JsonObject, JsonBody, OpaqueJson, FrozenMap — also `(of dict K V)` with element types), tuples and lists with no element types (tuple, list), fixed-length tuples, and tuples with open element types (union branches and the contents of containers are also looked at — using DOEFF144's reading of types). Homogeneous sequences (`(of tuple X ...)`, `(of list X)`) and typed records do not hit. The `:post` tuple of a sort-key function (named `…key-of` or `sort-key`) is excluded. A mapping that looks up data should be a key -> value function or an effect, and the spelling that builds a mapping is placed at one point in the translation layer. Default severity critical | `pre:<definition>:<argument>`, `post:<definition>`, `field:<Class>.<field>` | the type annotation |
| DOEFF172 | Where mappings are placed (a smell; counts only) — in the bodies of top-level defk, deff, and defn in Hy files in layer locations (excluding the leading docstring and contract dict — in any order, #2265. A `{…}` in a match type position does not build a value, so it is not counted, #2310), one hit per definition for places that build a mapping (`{…}`, `(dfor …)`, `(dict …)` — `#{…}` sets are not counted); and one hit per field when the type of a field of defclass, defrecord, or defwire outside the layers that pass values by type alone (`:wire-free True`), or a class field annotation in a Python file (#2203), is a mapping (the same mapping vocabulary as DOEFF171). A point whose purpose is to spell or read an external shape is not counted if it declares `:spells` / `:reads` in the contract's `:tags` with the name of the external shape (a closed list of `"json"`, `"http"`, `"env"`, `"schema"`, `"records"` [the shape of doeff-records' API] — the same as doeff-hy's SPELLS. A value outside the list is not regarded as a declaration) (in a layer that passes values by type alone, it is counted even if declared). A field of the relay type `OpaqueJson`, whose contents are not read (including a union with None), is not counted as a mapping (a union with another mapping is counted, #2515). A field whose element type is `OpaqueJson` in a tuple / list (`(get tuple #(OpaqueJson ...))`, `(of tuple OpaqueJson ...)`, `tuple[OpaqueJson, ...]`, `list[OpaqueJson]` — including a union with None) also just carries zero or more relays, so it is not counted (a union of OpaqueJson and another mapping as the element type is counted, #2586). A field that is a union of `OpaqueJson` and non-mapping types (records and the like) (`(| OpaqueJson Record)`, `OpaqueJson | Record | None`) is also not counted as a relay (a union that includes a mapping branch is counted, #2629). A defwire with a field of a mapping of an external shape as per contract does not count the field if it declares `:spells` / `:reads` with the shape's name in the head dict's `:tags` (a defrecord is a value type, so the declaration has no effect, #2515). Inside a defhandler (the cache is held by the effect layer) and definitions whose `:post` is a mapping (the one point of spelling whose purpose is to build a mapping) are not counted. Fields in layers that pass values by type alone are looked at by DOEFF171. The hits' weight is warning; default severity major (agora's data is in the DB and read by effects, and in-memory mappings are only for caches and held by handlers) | `built:<definition>`, `field:<Class>.<field>` | the definition's name or the field's type annotation |
| DOEFF175 | Stale branches on an effect whose declaration was switched — in top-level definitions (`defk`, `deff`, `defn`, `defn/a`, `defp`, `defpp`, `deftest`) of the population of the definition rules (Hy files in `paths` and `test_paths` of `definitions`), for a name x that binds the answer of effect `E` — `(<- x (E …))`, `(<- x T (E …))`, `(<- x (E …) :absent F)`, `(val\|var\|setv\|setx\|:= x (! (E …)))` — or a `(! (E …))` written directly without binding, it hits branches that split, as values, the types that `E` declared with `:absent` / `:failure` of defeffect: arms of `(match x (T …) …)` (each alternative of `(| (A) (B))` too) and `(isinstance x T)` (each type of a type list `#(A B)`, `[A B]`, `(| A B)`, `(tuple [A B])` too; also inside cond / if / when / and / not). `<-` and `!` do not bind the declared absent / failure types but let them escape to the caller's scope as Absent / Raise (ADR-DOE-CORE-EFFECTS-003 R5), so those branches are never taken. Types are compared after being resolved to the module by imports and modules (the same as DOEFF122 — even if a same-named type is in another module, only the declared one). What does not hit: branches on types declared `:value`, branches on success types, branches on effects with no declaration, defhandler (a clause is the side that receives the effect, not the side that binds the answer — not read per definition), types declared by another effect, types inside pattern fields, names rebound and names hidden by arguments, `for`, `with`, `let`, `except`, comprehensions, or match patterns, answers wrapped in `maybe` / `result` / `on-raise` / `absent-as` (the head is not an effect), answers that went through a defk, quotes, and comments and `#_`. Details are in section 22. Judged in both whole runs and one-file runs (the editor). Default severity critical, **not lowered by the known list (registry)** (a gate for the switch — that version is not landed until the hits reach 0; law declarations-switch-one-effect-at-a-time) | `<definition name (mangled)>::<bound name>::<type spelling>` (the form that splits directly with `!` without binding uses the effect's spelling instead of the bound name. e.g. `revise_input::body::Missing`) | the match arm's pattern, or the isinstance type's symbol |
| DOEFF166 | Stale rows of the registry that nothing hits — among the keys of `dirs`, `files`, and `config_files` of `[tool.doeff-linter.registry]`, those for which the rule pointed to by the key's separator (the second part of `<path>::<law name or rule ID>[::<detail>]`) was **judged in this run** (the judgment is wired, it is not a semantic rule, and it is not `reconciling`), yet match the key of no finding. In a whole run where DOEFF166 is enabled, the rule the key names is applied in the same run even if it is not in `enable` — those hits are used only for 166's judgment and are not reported as findings (so even a run with only `--enable DOEFF166` does not miss rows that do not match). Keys from which the rule cannot be derived by the separator (keys of other checks that share the registry dir) and keys that name semantic rules or `reconciling` rules are not judged. Only whole runs that name no paths (with named files or a pre-save single file, the hit findings may lie outside the range). It is wired only in repos that configured a registry. Default severity critical (#1706 — do not loosen a shrinking-direction registry by forgetting to delete) | the registry's key | the head of the registry file it is listed in (the key's `*.txt` for a dir) |
| DOEFF144 | Types of the public surface are bare mappings or bare tuples — reads the type annotations of the public surface (names not starting with `_`; `Class.field` is looked at by its last segment) of Hy and Python files matching `:typed-values {:files [..] :except [..]}` (globs are anchored at the root, as in DOEFF150): class fields (`(#^ T name)`, `#^ T name`, `(setv #^ T name …)` directly under Hy's defclass, defrecord, and defwire / `name: T` directly under a Python class) and return values of functions and methods (`(defn #^ T name …)`, `:post [(: % T)]` of defk / deff, `-> T`. Nested functions are not counted). Red = bare mappings (dict, Dict, Mapping, MutableMapping, the same names in typing / collections.abc, JsonValue, JSONValue, JsonObject, JSONObject), bare tuples (tuple, Tuple), mappings whose values are object / Any, fixed-length tuples `tuple[A, B]`, containers holding them as elements and union branches, and branches of a string type `"A \| B"`. Not red = `dict[str, Row]`, `tuple[X, ...]`, FrozenMap. For the `:post` of a defk / deff whose name has a type annotation, only mappings are red, and for a `:post` with no annotation, bare tuples are red too. The form in which a Hy defn / defk / deff answers with a tuple literal `#(a b)` of length 2 or more (the last expression, or `(return #(a b))`) is also red. Judged by a single file, and named paths are handled the same as DOEFF150. Default severity critical | `field:<Class.field>` / `return:<name or Class.method>` / `post:<name>` / `pair:<name>` | the type annotation (for a returned tuple, the tuple literal) |
| DOEFF145 | A type declaration's `@dataclass` lacks kw_only=True — among .pyi files matching `:record-stubs {:files [..] :except [..]}` that have a same-named .hy in the same dir, for the .hy's top-level `(defrecord name …)` or `(defclass [decorators …] name …)` that has `(dataclass … :kw-only True …)` among its decorators (the name is mangled to a Python name), the .pyi's top-level class declares `@dataclass` / `@dataclasses.dataclass` (the call form too) without a literal `kw_only=True`. Judged by a single file. Default severity critical (#1191) | the class's name | the `@dataclass` decorator |
| DOEFF146 | A vocabulary confined to one judgment point — for each group in `:single-point-vocabulary`, reads files that match `:files` (globs anchored at the repo root) and do not match `:except` (the one judgment point), and emits one per file if any line matches any of `:patterns` (regular expressions `r"…"`, one per line) (the number of matching lines goes in the message). Default severity critical (#1371 — originally a temporary test in agora-controllers, check_screen_slice_single_point.hy) | the group's name | the first matching line of the file |
| DOEFF150 | Spellings decided not to be used — for each group in `:retired-words`, reads files that match `:files` (globs anchored at the repo root — a pattern without `/` is only files directly under the root) and do not match `:except`. `:in lines` (default) counts, per line, `:words` that stand alone as words (not adjacent to letters, `_`, or `-`) and `:patterns` (regular expressions `r"…"`, one per line), and does not count lines containing a `:rule-lines` spelling. Only spellings in code that is actually used are counted, not those in comments, docstrings, or documents (decision Q2-3 of #1762): `.md` files are not counted (per file); in Hy, `;` comments (from a `;` outside a string to the end of the line) and docstrings of `def…` forms (defk, defn, deff, defclass, defhandler, deftest, defadr, ...) (the first string after the name, argument list, and optional `{…}` meta, with a form still following it) are not counted; in Python, `#` comments (outside strings) and docstrings (triple-quoted strings starting at the first non-whitespace of a line) are not counted; in shell, toml, and other files, `#` comments outside quotes (from a `#` at line start or after whitespace to the end of the line — `$#` and `${#…}` are not comments) are not counted. In every kind, a shebang (`#!`) on line 1 is counted. Symbols, field names, and strings that are not docstrings are counted — a command string (`"cd x && PYTHONPATH=. hy"`) is a spelling that gets executed, so it is counted. `:in names` looks only at the names of definitions (Hy's `def…` forms and the left side of `setv`, `val`, `var`; Python's def and class names). `:in paths` looks only at file names (before the last `.` — dir names and contents are not looked at), and does not let files with retired names be placed again. `:rule-lines` can be written only with `:in lines`. `:contract-files [<path from the repo root> …]` (writable only with `:in lines`) is a list of contract JSON files; when architecture.hy is read, it collects the names of object keys at any depth, the string elements of arrays under the key `enum`, and the string values of the key `const` (the contract's spellings) (comments, example values, and the contents of other arrays are not collected). If a `:words` word is in the contract's spellings, that word is not counted only in these 3 places — strings whose contents are exactly that word (an ordinary Hy string `"mail"`; a single-line string without prefix `"mail"` or `'mail'` in `.py` / `.pyi` Python), field definition names in the body of a Hy defwire (`(#^ str mail)`, `(setv #^ T mail v)`), and strings in the `:reads` / `:writes` sets of a Hy defsystem job row (the spelling `<location>:<name>`) where the name part after the first `:` is exactly that word (the mail in `#{"record:mail"}`). Variables, arguments, fields of defrecord / defclass, loop variables, attribute reads (`x.mail`), long strings containing the word, and words not in the contract are counted as before, and this does not apply to `:patterns`. A missing file, a non-JSON file, no contract spelling at all, an empty list, or a path outside the root is a declaration error (exit code 2). A group that is not written works as before. Judged by a single file, so if paths are named, only files under them are read (the whole repo is not read). Default severity critical | for `:words` hits, the word; for `:patterns` hits, the group's name | the spelling hit |
| DOEFF151 | Calls decided not to be used — for each group in `:retired-calls`, counts the `(call …)` forms (head symbol is a `:calls` spelling) in Hy files that match `:files`. Not counted: comments, strings, forms discarded with `#_`, and places that merely name it as a value. Python files are not counted. Inside a module of a boundary part (`:boundary-parts`), only calls whose spelling can be classified by the raw-side-effect catalog and whose target (mapped the same way as DOEFF106 — `time.time` is clock) is declared by the part in `:touches` are not counted (#1894). The treatment of named paths and the severity are the same as DOEFF150 (#1193) | the call's spelling | the call's head symbol |
| DOEFF109 | A violation if the layer of a file that has a service (one that matched the `*` of the location) is in `services.guarded_layers`, and the import target is a module in a layer guarded by another service. Allowed if the target is a shared location, a layer in `open_layers`, or a pair in the exceptions | the import target's spelling | the first import symbol |
| DOEFF110 | A Hy `defn` / `defn/a` definition (with decorators too). The inside of `do` is also seen as top level. The inside of `eval-and-compile` / `eval-when-compile` is outside | the definition's name | the definition's name |
| DOEFF111 | A `deff` definition whose own line or the comment on the line just before it (after `;`) lacks `definitions.deff_reason_marker`, or whose reason is empty or "same as above" and its variants (the marker's default spelling is `cannot be a defk:`; the earlier default `defk にできない:` — the same phrase in Japanese — is read as well, whatever the setting) | the definition's name | the definition's name |
| DOEFF119 | Classifies a business Hy file's defclass by the evidence in its contents (section 12) | the class's name | the class's name |
| DOEFF120 | A Hy or Python module in a repo that has architecture.hy uses JsonValue, JSONValue, JsonObject, or JSONObject and is not an allowed module (a generic resolver, or a foundation module listed in `:wire-modules`) (section 13). One per module | none | the first use |
| DOEFF118 | In files matching `definitions.test_paths`, a binding of a defn, defn/a, deff, defk, or fn whose name starts with `test_` | the definition's name | the definition's name |
| DOEFF112 | The :tags of a definition whose head is in `tags.require_on` (for defeffect, the dict's :tags) lack a key from `tags.required` (a non-empty string). With `module_default`, it is supplied from the key in the module's head tags | the definition's name | the definition's name |
| DOEFF113 | The :context of the tags of a file that has a service differs from the service's name (`-` and `_` are seen as the same; shared locations are not looked at). info | the mismatching :context | the tag dict |
| DOEFF108 | Splits the business file's name (the name without extension), the Hy top-level handlers (defhandler and functions that take `[effect k]`), and the names of top-level definitions in `assembly_files` by `-`, `_`, and `.`, and it is a violation if they hit `words`. All-uppercase names (constants) are not looked at | none (file name) / the mangled definition's name | line 1 / the definition's name |

### How tags are read (Hy)

- The module's head tags: the first of the top-level `(setv|val MODULE-TAGS {…})` (exactly 3 elements).
- A definition's tags: in a top-level expression whose head is in `contract_definers`, looking at the elements up to the 4th from the name, the `:tags {…}` of the first dict
  after skipping strings and `[…]` (the inside of the dict is read as pairs of keys and values). An expression whose head is in `effect_definers` (default defeffect) has the form `(defeffect name "doc"? {:fields […] :answer type :tags {…}})`, and the `:tags` of the first dict, after skipping the docstring, among up to the 2nd element from the name. The old form that merely lists keys and values is not read as tags. An expression whose head is in `plain_definers` is counted as having no tags.
- A dict is regarded as "declared" only when it has one or more keyword keys with string values (an empty dict has not declared).
- If there are two definitions of the same name, the later one's tags overwrite the earlier one's.
- Effective tags = all the definitions' tags + the module's head tags (when some definition has no tags, or no definition has any tags).
- How imports are read: `(import …)` is counted not only at the top level but also inside functions and quotes. `(import m [a b])` gives `m.a` and `m.b`,
  `[a :as b]` gives both `m.a` and `m.b`, and `(import m :as x)` gives `m`. Relative spellings are resolved from the module's spelling. Names are fixed up with Hy's mangle.

### How tags are read (Python)

The top-level `MODULE_TAGS = {…}` (a dict whose keys and values are both constants) is taken as the module's head tags. There are no per-definition tags. Imports are counted inside functions, classes,
if, try, and so on, and `from m import a` gives `m.a`, `import a.b` gives `a.b`.

## 6. Performance

- A whole run indexes all Hy files under the repo root with hy-index if DOEFF107 is enabled (because the evidence by way of crosses files).
  If disabled, it indexes only the files it judges.
- A `--stdin` one-file run judges with only the list of files in the layer dirs (not read) and the analysis of that one file. It emits no evidence by way of.
- The range of the repo that a `--stdin` one-file run reads is **only the files of the modules the written file names** (on a busy Pod, reading all of the repo's
  Hy files took 2 to 7 seconds and was cut off by the 4-second limit of the advice hook after writing). Tables are looked up by names including the module, and the name looked up is either the name obtained by resolving the written file's symbols with
  import bindings and modules (`smells::Scope::qualify`), or a name held by the facts of the files reached, so reading from the modules the written file names until it closes
  gives the same answer as reading the whole repo.
  - Tables of inference for effects, types, and defk (DOEFF127, 130, 174, plus headings, bindings, rewrites, and bodies): `World::build_for_file` — starting from the named modules, add the modules that the facts name (called shots, types,
    effect answers and failures) and read until it closes. Inference is decided only by what lies beyond the reachable defks, so it is not placed in the cache and is run every time.
  - Names of defk and fields of records (DOEFF126): built from only the files of the named modules.
  - The index of locations of layer modules (DOEFF101, 109, 130, and others that look up the import target): judges only the files of the modules looked up (`module_index.rs`). The reading of contents when inferring the layer of a module outside the
    locations from its tag role is also only for the modules looked up.
  - Finding a file from a module's spelling does not walk the tree but probes segment by segment of the path (`module_probe.rs` — avoiding reading dirs in a cold working tree).
  - Three things whose answers would change with only the named files (failure types = declared by `:failure` of a defeffect in another file; Programs passed as arguments = passed by callers in other files; class frozen-ness =
    whether the same name exists anywhere in the repo) are built **only when looked up**. The judgment of the shapes that look them up is done first (an arm that merely returns the received value, a definition that calls its argument bare, a handler argument judged by type),
    and most files do not look them up (in a sample of 150 files of agora-controllers, 3 for Programs passed as arguments, 3 for failure types, and 1 for class frozen-ness).
    - Only class frozen-ness is built from the whole repo when looked up (`deferred.rs`).
    - Failure types and Programs passed as arguments have their lookup names fixed (the type of a match arm, a function of the written file), so **only files that literally contain the spelling of that name** are analyzed
      (`spelled_failures.rs`, `spelled_callers.rs`; the way to probe for spellings is in `spelling.rs`). Both declarations and callers necessarily contain the spelling of the last segment of the resolved name literally (mangle only turns `-` into `_`;
      a name with a non-identifier character in it cannot have its spelling fixed, so it is not narrowed). Whether a passed symbol is a defk is decided only by the symbol's module file (the naming of `defk_names_over`).
      The answer is the same as when looking up a set built from the whole repo (checked against all the callees and type names of real repos).
    - The set of files that have failure-type declarations (files containing `:failure` or `:absent`) does not depend on the type name, so it is collected from the repo only once on the first query (`OnceLock`), and for each name that set
      is narrowed by spelling and resolved. Re-reading all of the repo's Hy per name took 4 seconds of CPU on a file with 20 match-arm types (the 4,866-line `cli_host.hy`).
    - Resolving a declaring file (the types that file declares) is also decided only by the file's contents and the way of reading, and does not depend on the type name. It is resolved only once per file within a run (`parsed`), and also remembered in the facts store
      (`facts_cache::per_file_content` — the same kind `failure-types` and the same key as whole runs, so a whole run reads facts this one placed and this one reads facts a whole run placed. Empty facts are placed too).
      Only the declaring files that spell the type name are placed, and the repo-wide table is not passed. The form of re-resolving all the declaring files every time a different name is looked up used 246 files and 0.4 seconds of CPU per name for a name that spells everything, like `|`.
    - The waiting to walk the list of the repo's Hy and the waiting to read the files found are all run as overlapping waits (`hy_files::collect_reading`, `bulk_read::pipeline` — up to 128).
      The set and order of the list are the same as `hy_index::collect_hy_files`.
    - The threads that overlap waits are increased **only when the wait appears to be a disk wait** (`bulk_read::Widen`). While reading from the page cache the work does not get faster, and only the CPU of starting threads and synchronizing increases
      (reading the repo's 4,144 Hy files from the page cache on a busy machine took 7 to 14 seconds of CPU with 128 threads and 0.4 to 0.8 seconds with 1), so it starts with 4, and once three rounds in a row take 500 microseconds or more per item
      (a single delay from a thread being stopped does not count), it increases once up to the number the caller decided. On a cold working tree it grows within the first few items, so the number of overlapping waits does not change.
    - Looking up the callers' facts happens after the registry, the effect world, and the table of named modules have been built in a run. If the written file has a definition that calls its own argument bare,
      the reading of the repo by the callee's name is started on another thread from the head of the run (`SpelledCallers::started` — the callees are estimated with the set of defk being empty, and include all the callees actually looked up. If not contained, it is re-read when looked up).
  - CPU for large files is proportional to the number of lines, not its square: the name resolution that resolves the written file's symbols (`hy_index::qualify::link`) builds the table (definition names, import aliases,
    imported names) only once per file and now merely looks up that table per call (before, it scanned all definitions and imports per call and called `mangle` call count x (definition count + import count) times).
    `index_source` for a 10,053-line file went from 4,306 ms to 92 ms. `qualify_tests.rs` in `doeff-indexer` checks that the number of `mangle` calls is proportional to the file size, and the order of resolution (an earlier-written import wins; a definition in the same file wins over an import).
  - A one-file run neither reads nor writes the per-root tables of facts (it looks up facts by content hash — it reads facts a whole run placed, and a whole run reads facts this one placed).
  - The registry and judgment lists (dirs of one-key-one-file — agora-controllers' `scripts/doeff_lint/SMELL-BREACHES` has 447 files) read all the files. Each working tree has different inodes, so
    on a cold working tree it waits on the disk for each file — read in order, the waits add up. The read waits overlap with `bulk_read::fan_out` (up to 32 threads; results in input order; answers are the same as when read in order,
    and notices of unreadable files are also in file order). The rayon pool (4 threads for a one-file run) is for CPU work, so it is not used.
    A one-file hook is fired many times, and if everything is opened each time, on a Pod where the page cache is evicted within minutes it waits on the disk every time (on a day with 55-66% IO pressure, 494 of the 596 opens in one run of a cold working tree were
    this dir). So if the fingerprint of the files in the dir (path, size, modification time, inode) is the same as in the previous run, the result of the previous read is used without reading the contents
    (`dir_stamp::remembered` — the result is placed in the facts store, keyed by the fingerprint). Even when cold, stat takes 0.02 seconds for 449 calls. If even one file was updated less than 2 seconds ago, it is not remembered
    (the same guard as git's racy). A notice of an unreadable file carries the file's absolute path, so a read with notices is not remembered. A same fingerprint with different contents happens only when someone writes in a way that resets the modification time and keeps the
    size (`touch -r`, `cp -p`), and the registry has no such practice.
  - Tests: `tests/single_file_reads.rs` (the answer is the same as a whole run, facts of modules not named are not placed, the whole table is not passed), and each of the tests in `signatures.rs`, `call_view.rs`, and `body_view.rs`
    asserts the equivalence of the "whole-repo table" and the "named table". That the waits of reading the registry overlap is checked with `bulk_read::watch` (the maximum number of jobs in progress at the same time), and that an unchanged registry is not read the second time, and that after a change / addition / deletion / immediately after an update it is re-read and the answer (`registered`) follows, are checked with `tests/registry_remembered.rs`.
- Reading the declaration (architecture.hy) is a fixed cost per run: agora-controllers' declaration (530KB) was built with `Regex::new` for the 195 regular expressions of `:patterns` / `:pattern` only to check that they were readable (the built ones were
  discarded, and the rules that use them rebuilt them), and was read twice, at the head of the run and in the layer-exclusion query (`population_of`). The check is now syntax only (`architecture::check_regex` — it asks `Regex::new` again only when unreadable, and the error text is the same),
  and the declaration read at the head of the run is passed to the queries (`population::remember_declaration` — only when it was read with the declaration's dir as the repo root). The CPU of the run head (`prepare`) went from a median of 0.050 to 0.020 seconds, and the CPU of one run on a small file from 0.164 to 0.062 seconds (median of 25 runs, load 30, no LTO).
- Measured (agora-controllers main line, 2026-09-27): whole run about 1.1 seconds, one file under 10 milliseconds.
- Measured (agora-controllers main line Hy 2900 files, 2026-10-12, Pod load 70-100, old and new alternated under the same load): opens in a one-file run 12,077 -> about 600, opendir 2,837 -> 5-9,
  stat 39,472 -> about 2,000. The median warm wall time 0.40 -> 0.18 seconds (CPU 0.58 -> 0.20 seconds), and the CPU of the first run on a new working tree 5.1 -> 0.1 seconds. Most of the remaining opens are the registry's
  one-key-one-file (about 450 files — `scripts/doeff_lint/SMELL-BREACHES`).
- Measured (same main line, 2026-10-16, Pod load 72-83, working trees with a cold page cache [checked with mincore] chosen separately for old and new, fired once each with a shared cache): reading the registry in a cold working tree
  took a median of 2.4 seconds serially (maximum 3.6-10.8 seconds — open+read of 449 files. readdir is 0.006 seconds, all stat 0.021 seconds). With 8 cold working trees at varied concurrency: serial 6.4 seconds / 8 threads 0.93 seconds /
  16 threads 0.62 seconds / 32 threads 0.37 seconds (median). One cold run of the fixed version: 26 files in the form that does not read the whole took a median wall time of 1.10 seconds and a maximum of 1.96 seconds (before the fix, 27 files with a median of 2.25 seconds, a maximum of 3.59 seconds, and
  6 over 3 seconds), and the registry stage went from a median of 1.44 to 0.33 seconds.
- Measured (same main line, 2026-10-17, Pod load 85-130, working trees with a cold page cache chosen separately for old [main before the fix, built with the fast profile] and new, fired once each with a shared cache, built with the same no-LTO settings):
  for the file that looks up Programs passed as arguments (`requests_projection.hy`), the wall time of the first cold run was a median of 33-46 seconds (before the fix) -> 4.5 seconds (maximum 5.2 seconds). `triage.hy` was 56.9 seconds (maximum 73) -> 5.4 seconds (maximum 6.3).
  The file that looks up failure types (`conversation_input.hy`) was 37.3 seconds -> 7.2 seconds (one run included 3-19 seconds of `facts-cache.roots`). Intermediate values during the fix (`requests_projection.hy`, median): the form that narrows by spelling and walks the tree in order 17-25 seconds (9-15 seconds of the walking wait alone) ->
  the form that lines up the walk per layer 7.6 seconds -> the form that reads while walking 5.7 seconds -> the form that starts the read from the head 4.5 seconds. Reading 2839 cold files was 3.3 seconds with 32 threads, 1.8 seconds with 128, and 1.9 seconds with 256 (median).
  The output did not differ by even one byte across 406 runs on real repos (including 42 runs with DOEFF126 hits and runs with DOEFF122 hits).
- Measured (same main line, 2026-10-17, Pod load 60-76, IO pressure 30-36%, cold working trees [checked with mincore] chosen separately for old and new, fired once each with a shared cache, 4 pairs): for `cli_host.hy` (4,866 lines) with 20 match-arm types,
  the wall time of the first cold run was a median of 5.3 seconds (4.4-6.0 seconds) -> 3.2 seconds (3.1-5.3 seconds — the one pair at 5.3 seconds was a load-76 run). The `failure-types` stage went from 2.3 seconds to 1.5 seconds
  (from the form that re-reads the repo's Hy for every name to the form that reads it once). In the warm state (in the page cache) there is no difference between old and new (median 1.11 -> 1.13 seconds).
  CPU for large files (`qualify::link`, continued measurements on the same day): the warm wall time of `test_server.hy` (10,052 lines) was 9.5 -> 1.5 seconds, and `react.hy` (3,715 lines) 10.6 -> 1.2 seconds.
- Measured (same main line Hy 4,144 files, 2026-10-19, Pod load 30-70, old [origin/main 5d16a1729] and new built with the same no-LTO settings, fired in the same sequence of runs, facts stores separate for old and new, measured after warming):
  the CPU of one hook run (fired with the written files left in the hook's log) was 1.67 seconds in the old version for 3 reasons, all of which were places that read a repo sitting in the page cache — reading the declaration (above), reading all of the repo's Hy with 128 threads
  (most of the CPU was sys), and re-resolving declaring files per name.
  - 150 runs chosen according to the run frequency from the log after 10-18 12:00 (121 files): the average CPU per run 0.80-1.02 -> 0.24 seconds, p99 10.1 -> 1.3 seconds.
  - The actual 290 runs from 10-19 08:00 to 16:00 in the same order (second pass): average 0.285 -> 0.161 seconds (the 89% of runs that do not read the repo 0.214 -> 0.123; the 9% that look up failure types 0.90 -> 0.55).
  - A small file (`policy_types.hy`): CPU 0.19 -> 0.06 seconds.
  - Cold working trees (checked with mincore, different working trees for old and new, shared cache): for the file that looks up failure types, the wall time of the first run was a median of 4.9 -> 3.0 seconds and CPU 15 -> 1.1 seconds. For the file that looks up Programs passed as arguments, 3.06 -> 3.05 seconds and CPU 2.6 -> 2.8 seconds (unchanged).
  - Answers: the output of 150 runs did not differ by even one byte except the linter-version field. The output of 280 runs (40 files x 7) with fragments that add violations (a flat defn, os.environ, a bare sleep, a record with no tags, a match on failure types, an unclosed parenthesis) added, and
    13 runs of DOEFF122 that looks up failure types from another file, are the same too. The hook's advice text is the same over the actual 139 runs (excluding the binary's path in the line on how to check).
- Not reading the contents of unchanged files: the 3 places that look at all the repo's files (spellings of callers of Programs passed as arguments, spellings of failure-type declarations, class frozen-ness) and
  the registry look up the facts of files whose git index row matches the working tree's stat (size, modification time in ns, inode) by the index's blob id, without reading the contents (`git_index.rs` — stage 0 rows of index versions 2 and 3.
  Rows with the same or later time than the index's modification time [racy], files not in the index, and files whose stat does not match have their contents read). Facts are decided by the blob's contents alone, so the answer in any working tree is the same as when the contents are read.
  - Facts are placed as one table per chunk (about 64 rows, at most 256 rows) of the index order divided by path hash (`blob_chunks.rs` — the store's name is the path at the head of the chunk). Even on a cold working tree it only opens about 70 store
    files, not the about 4,600 of the form that placed facts one blob per file. Changing a file does not change chunk names, and only adds new blob rows to the table. A table keeps rows of past versions up to 3 times the chunk's current row count
    (so working trees of old commits hit too), and a blob not in the table is built by reading its contents, and after confirming that the file did not change while reading and that the contents are the same as the index's blob, it is added to the table at the end of the run.
  - The fingerprint of a registry dir builds the files matching the index by blob id (`dir_stamp::by_blobs` — it looks up the result read by another working tree of the same commit, and does not read the registry). For the few
    files that diverge from the index, the blob id is built from the contents (if there are many, it falls back to the previous stat fingerprint).
  - The spelling memory (the Bloom filter of `blob_spell.rs`) uses 16 bits per word (rounded up to a power of 2) and 8 positions. The false positive of wrongly saying a name "might be spelled" is about 0.05% or less. With the previous 8 bits and 3 positions,
    one name was wrong about 1-3% of the time, and `requests_projection.hy`, which searches for 12 callee names, re-read about 500 of 4,620 files even on a warm working tree (19-34 after the fix).
  - Whether to increase the threads that overlap waits is decided by whether that thread asked for a disk read (`ru_inblock` of `getrusage`'s `RUSAGE_THREAD`) (`bulk_read::Widen` — when distinguished by time,
    it increased even for delays where a thread was merely stopped on a busy machine).
  - The upper-limit cleanup that counts the facts store (once an hour; on zeus about 400,000 facts and 3-19 seconds — the hook that drew the turn was cut off by its limit) is handed, with an in-progress mark, to the linter's own
    `sweep-facts-cache` entry and not waited for (new session, no standard I/O, nice 10. If it cannot be handed over, it is cleaned within that run). The cleanup that counts only root dirs and the cleanup of dirs whose root has disappeared
    are light, so they remain within the run. Tests clean within the run with `DOEFF_LINTER_SWEEP_INLINE` and look at the result.
- Measured (2026-10-23, agora-controllers main line Hy 4,620 files, release without LTO, the facts store is the machine's shared one):
  - Answers: the editor-json of old (branch base a16b2b97f) and new did not differ by even one byte except the linter-version field, over 1,240 runs of real repo files x 5 rewrites (as is, a binding turned into a plain assignment, `:effects` removed, an effect that is not fired added,
    a class type added to a handler argument) (the version with the spelling memory fixed) and 1,195 runs with a different selection (the version with the walk fixed)
    (including 20 hits of DOEFF122, 77 of DOEFF126, and 3 of DOEFF142).
  - Old working trees with a cold page cache (checked with mincore, fired once each): with the previous version of the chunk tables (one table per file), at load 122-152, 4 of 10 pairs exceeded 4 seconds (2.6-5.3 seconds). With the current version,
    at load 30-56, all 10 pairs took 0.7-2.8 seconds. CPU is proportional to the number of blobs not in the table (15 misses 1.2 seconds, 1,327 misses 29.7 seconds).
  - New working trees (cut from origin/main, the first run after dropping file contents from the page cache with posix_fadvise; the inode and dir caches cannot be dropped): at load 28-35, all 10 pairs took 0.4-2.9 seconds.
    0 blobs fell outside the table.
  - Before the follow-up work of the walk was finished in the thread's own hands, after threads grew to 128 on a cold read, they contended for a single lock over tiny jobs (one stat), and the CPU of the first run on a new working tree
    came to 10-34 seconds in sys alone in some runs (19 files read, 25,000 forced context switches). After the fix (`bulk_read::pipeline` — handing off to a thread that waits only after a slow job), 10 pairs on
    new working trees took 0.46-2.9 seconds and 22 pairs on old cold working trees took 0.05-2.3 seconds (load 29-48). The CPU of cold runs varies greatly with the number of blobs not in the table and with the machine's memory pressure
    (around 500 misses, 1.2-11.8 seconds across 10 minutes on the same day).
  - The CPU of one run on a warm working tree is 0.35-0.5 seconds (user 0.23-0.38 seconds, sys 0.11-0.14 seconds — when the machine's memory is tight, sys is 0.3-1 second). Of that, the stage that walks all the repo's files,
    stats them, and looks up the table is 0.22-0.30 seconds.
- Remaining:
  - The lower bound of a warm run's CPU is decided by stat on all the repo's files (to check whether they match the index). To bring one run's CPU below 0.3 seconds, a resident process that remembers the walk per working tree and receives changes
    by notification (in the form of git's fsmonitor) is needed.
  - The CPU of a cold run is decided by reading the contents of each blob not in the table and by the machine's memory pressure (1-13 seconds per run).

## 7. Matching against existing judgments

It was confirmed that the key sets (including the duplicate counts) of DOEFF101-105 match agora-controllers' `breaches-of` in `scripts/module_tags.hy`,
on main-line 46bbb24b (23 modules, 11 hits) and c3d41111 (18 modules, 15 hits). DOEFF108 replaced the environment words of `assembly-breaches` in `scripts/check_business_fakes.hy`
(which used reachability analysis and the external-world effect table) with a name-only check, so it emits more by the amount of not looking at
which effects a handler answers (15 more on main, and 0 fewer).

## 8. Limits of reading

- DOEFF106 and 107 are Hy only (because hy-index's facts are Hy only). Side effects in a module's top-level expressions (outside definitions) are not counted.
- DOEFF108 judges by name only and does not look at whether a handler answers a business effect.
- Import targets are resolved only to modules in the population. Even within the repo, modules outside the layer dirs are not counted.

## 9. architecture.hy — the single declaration of services and layers

The repo's top-level `architecture.hy` (or the settings' `architecture = "<path relative to the settings file>"`) is the only place that declares services and layers.
If the declaration file is outside the root (such as the root architecture.hy of a package whose root is `src/` by the settings' `root`), the key's path is relative to the root and contains `..`
(`../architecture.hy::DOEFF163::…` — absolute paths per machine are not put in keys).
doeff-linter reads this **without executing it**, using doeff-indexer's Hy reader. If present, layers and roles are copied from here, and the TOML's
`[tool.doeff-linter.layers]`, `roles`, and `services` cannot be placed (a double declaration is a settings error). Only rule on/off, severity, registry locations, and
law correspondence are left to the TOML. If absent, it works from the TOML settings as before. doeff-hy's runtime macros (defarchitecture, defservice, layer) are not yet implemented.

**Layers removed from the rule population.** A layer names modules with `:modules [module-name …]` (dot-separated module names — modules under them are included),
and with `:exempt [(rule rule-ID "reason") …]` it can remove, for the named modules only, Python's per-statement rules (DOEFF001-032) from the population
(example: a watcher that comes in from `.pth` at Python startup has no entry point outside a Program that receives settings through Ask, so DOEFF004 is removed). Among the definition-writing
rules, DOEFF110, 111, 112, 118, and 119, which judge per definition file, can be removed in the same form (example: a defn in a module that is called as a Python function at macro-expansion time
cannot return a Program, so DOEFF110 is removed). Even in a module with DOEFF110 removed, a defn that cannot be reached by following the bodies of top-level functions from any defmacro form in the package
(the dir of the declaration file — excluding test dirs) is not removed and fires
(a guard for runtime-time definitions, 2nd delivery of #2913 — names are matched by spelling only, and names inside quasiquote = the parts that expanded code calls also count as reached).
The one point of removal is right after the definition's judgment, so a whole run and
a one-file run (`--stdin`, the commit hook's child) give the same answer. DOEFF126 (calling a defk bare), which is critical, and rules that are not judged per file, layers, services, and
Jev's rules cannot be removed. An empty reason, an ID of a rule that cannot be removed, an unknown ID, the same rule twice, and an `:exempt` without `:modules` are reading errors (exit code 2 if it is a declaration the run reads;
DOEFF032 below if it is a declaration used to look up the population — definition-writing rules remove nothing under an unreadable declaration). The population of Python rules is decided per file from the **nearest architecture.hy going up from the file**
(the declaration of that package's owner — it does not look above the repo root's `.git`). It is looked up separately from the declaration the run reads
(the settings' `architecture`, the root's architecture.hy), so root runs that do not name a package's declaration (doeff's pre-commit, `make lint-doeff`)
follow the same exclusion. A module's name is built by going up the dirs that have `__init__.py` (independent of the settings' `root` and of the declaration's `:root`). If a module of a removed layer
imports a module of that layer's `:forbid-modules`, DOEFF032 (error) — because if business code gets into a removed layer, the removed rules would not hit that code either.
When the declaration of the package that owns the file cannot be read, nothing is removed (every rule hits as is), and DOEFF032 names that it cannot be read.

```hy
(defarchitecture agora-controllers
  :root "controllers"                         ; the root where service dirs are placed
  :layers [(layer core :summary "…" :knows "…" :does-not-know "…" :question "…"
                       :roles [type judgment program] :imports [core intent] :forbid-modules ["httpx"])
           (layer intent … :types-only True)
           (layer protocol …) (layer foundation …)
           (layer entry … :dependency-layers [intent protocol])]   ; farthest from the outside world first. :dependency-layers = layers that may be read as dependencies (default :open-layers)
  :shared "shared"                            ; root/shared/<layer>/ — readable from any service
  :verification-environment "agora_sim"      ; one location for the simulated environment (a verification environment that runs all services with production assembly but with only handlers replaced) — not a service. Not DOEFF114 or 115 (it does not accept a list). DOEFF136 counts the deftests under this as "local tests"
  :foundation foundation                      ; root/foundation/ — a layer outside services (a layer of the same name is required)
  :open-layers [intent]                       ; layers that may be read from another service (default intent)
  :placed-dependencies [core intent protocol] ; layers that may depend only on modules of a fixed location (DOEFF140 — not applied if not written)
  :blind-definitions [(blind "controllers.screen.core.entrance:entrance-guarantee" :forbid-words ["view.policy" "message-class"]
                        :no-imports True :allow-requires ["doeff-hy.macros"] :why "…")]  ; definitions judged only from fixed materials (DOEFF141)
  :allowed-heads [(allowed-heads "controllers.screen.entry.server:_confined" :heads ["defk" ":" "<-" "Try" "when" "return"]
                    :why "…")]  ; definitions that fix the heads allowed to be called (DOEFF147)
  :call-sites [(call-site "Try" :files ["controllers/screen/**/*.hy"] :except ["controllers/screen/**/tests/**"]
                 :sites [(site "controllers.screen.entry.server:_confined" :count 1)] :why "…")]  ; places and counts where a head may be called (DOEFF159)
  :broad-catches [(broad-catch "screen" :files ["controllers/screen/**/*.hy" "controllers/screen/**/*.py"] :except ["controllers/screen/**/tests/**"]
                   :carriers [(carrier "controllers.screen.core.deferred:carry-to-inbox" :event "Queued")] :why "…")]  ; broad exception catches only at the carrying boundary (DOEFF160)
  :roles {:judgment "a function that makes business judgments" …}   ; role descriptions
  :wire-modules ["controllers.foundation.records_client"]  ; foundation modules that do the sending and receiving of JSON itself (DOEFF120, section 13)
  :edge-mark "real_world"                  ; name of the pytest marker for edge tests (DOEFF133 — requires :world-handlers)
  :static-readers ["controllers.shared.core.foundation_closure:ClosureCase"]  ; definitions that merely read a passed value without executing it — references inside this call's arguments are not made "reach" edges of DOEFF133 and 136 (a module's marker reads only a pytestmark declaration that starts at the beginning of a line)
  :systems {:carriers ["scripts.declare_system:SystemPart"] :runners ["doeff_cluster._sim.local:sim-cluster"]}  ; system values and the entries that run systems — a test that reaches the outside world from the definition of a defsystem (body and call arguments) and from the arguments of calls of :carriers is an edge test only when it also reaches one of :runners (DOEFF133; as before if not written)
  :edge-touches [http db process clock cluster network thread]  ; touch targets counted as edges (DOEFF133 — all if not written)
  :test-forms {:tests ["test_*.hy" "test_*.py"] :check-scripts ["scripts/check_*.hy"] :runners ["*_deftest_runner.hy"]}  ; conventions on the shape of tests (DOEFF135)
  :retired-words [(retired-words "vocabulary" :words ["mail"] :files ["controllers/**/*.hy" "scripts/**/*.sh"]   ; spellings decided not to be used (DOEFF150)
                    :except ["controllers/kanban/forbidden-terms.json"] :rule-lines ["do not use"]
                    :contract-files ["docs/contracts/record-service.json"] :instead "Message")   ; strings of contract spellings and defwire field definitions are not counted
                  (retired-words "names" :patterns [r"(?i)conversation"] :files ["controllers/chat/**/*.hy"] :in names :instead "chat, agent")]
  :retired-calls [(retired-calls "clock" :calls ["Now" "time.time"] :files ["controllers/**/*.hy"] :except ["**/tests/**"]   ; calls decided not to be used (DOEFF151)
                    :instead "(GetMonotonic) or (GetTime)")]
  :environment-branches {:values ["production" "emulated"] :flags ["dry-run"] :layers [core intent]}   ; environment branches in business layers (DOEFF168)
  :semantic-lines [(line "line 1" :rules [DOEFF201] :text "what a protocol definition may do is …"   ; the drawn lines put into Jev's questions (DOEFF201, 202, 205; section 10)
                     :fires [{:code #[code[(defk access-of-held …)]code] :why "This definition decides whether to hand over or refuse by looking at the deadline."}]
                     :silent [#[code[(defk send-task …)]code]])]
  :handler-arguments {:files ["src/**/*.hy"] :exclude ["**/tests/**"] :store-names ["state" "store"] :store-suffixes ["-store"] :keep-mark "reason to keep as argument:"}  ; conventions on handler arguments (DOEFF142)
  :business-fakes {:simulation ["sim/**"] :assembly ["*/entry/**"] :tests ["**/tests/**"] :production ["src/**"] :sets ["**/handler_sets.hy"] :simulation-prefix "emulated" :production-prefix "production" :business-modules ["app.orders"] :external-effects "tables/EXTERNAL-EFFECTS" :lower-layer-passages "tables/LOWER-LAYER-PASSAGES" :test-only-fakes "tables/TEST-ONLY-FAKES"}  ; conventions on fake handlers (DOEFF143, DOEFF206)
  :assembly-shape {:translation-point "with-*-translation" :retired-function "handlers-of" :translations "TRANSLATION-HANDLERS" :translation-layer "protocol" :intent-layer "intent"}  ; assembly shape (DOEFF155, 156)
  :outside-writers {"ledger:land-item" "merge-queue and land.py" "records:pane-receipt" "ai pane-hand"}  ; pairs written by writers that are not production-system jobs (DOEFF207)
  :typed-values {:files ["controllers/**/*.hy" "controllers/**/*.py"] :except ["**/tests/**" "**/adr/**"]}   ; files whose public-surface type annotations are read (DOEFF144)
  :record-stubs {:files ["controllers/**/*.pyi"]}   ; type declarations matched against the same-named .hy (DOEFF145)
  :single-point-vocabulary [(vocabulary-scope "job-phase" :patterns [r"\bJOB-PHASE-[A-Z]+\b"]   ; vocabulary confined to one judgment point (DOEFF146)
                              :files ["controllers/screen/glue/**"] :except ["controllers/screen/glue/slice.hy"] :instead "read slice.hy's answer")]
  :confined-spellings [(confined-spelling "screen-http" :patterns [r"\(HttpRequest\s"] :files ["controllers/screen/**"]   ; spellings with fixed files where they may be written (DOEFF148)
                         :except ["controllers/screen/tests/**" "controllers/screen/protocol/*.hy"] :why "…")
                       (confined-spelling "web-push-private-key" :patterns [r"agora-web-push-vapid"]   ; names bound to a group's spelling are also the group's spelling (:names-bound-in)
                         :names-bound-in ["controllers/screen/entry/system.hy" "controllers/screen/protocol/web_push.hy"]
                         :files ["controllers/**"] :except ["controllers/screen/entry/system.hy" "controllers/screen/protocol/web_push.hy"] :why "…")]
  :counted-spellings [(counted-spelling "screen-post-seats" :pattern r"\x22POST\x22" :files ["controllers/screen/protocol/record_service.hy"]   ; spellings with a fixed count (DOEFF161)
                        :within ["append-record-events"] :count 1 :why "…")]
  :effect-census [(effect-census "screen" :files ["controllers/screen/effects.py" "controllers/screen/intent/socket.hy"]   ; the full set of effect declarations (DOEFF162)
                    :effects ["PostIntake" "Send" …] :why "…")]
  :field-holders [(field-holders "record-cache" :type "RecordCache" :files ["controllers/screen/core/*_types.py"]   ; the lineup of classes that hold a type's field (DOEFF149)
                    :holders ["ConversationSent"] :why "…")]
  :world-handlers [(world-handler "controllers.foundation.host:with-agora-process"   ; allowlist of definitions that may touch the outside world (note below)
                     :touches [http file clock env] :answers [HttpRequest ReadText]
                     :wraps ["doeff_core_effects._os_file:os-file-handler"])
                   (world-handler "controllers.foundation.clock:real-clock" :touches [clock]
                     :contract-test (none :doeff-test "packages/doeff-time/tests/test_clock.hy::test-clock-contract"))]   ; handlers that do not require an edge test (DOEFF137 — a none with a reason)
  :stand-ins [(stand-in "controllers.agora_sim.peers.live_session_peer:with-live-session-peer"   ; roster of counterpart stand-ins (note below, DOEFF137)
                :real "doeff_openai.live:with-live-session")]
  :boundary-parts [(boundary-part "controllers.agora_sim.local_screen_socket_client"   ; boundary parts that reach the real outside world from the simulated environment (note below)
                     :touches [network clock thread] :reason "…")]
  :exclude ["tests" "__pycache__" "conftest.py"]    ; as the default
  :extensions ["hy" "py"]                           ; default hy, hyk, hyp, py
  :shared "shared")  ; :legacy is abolished (writing it is a settings error — modules outside the declaration are all DOEFF114 and 115, and existing ones go in the registry)
(defservice land-notice "notice of landing" {:depends-on [messaging] :layers [core intent protocol entry]})
```

- Layer locations are `root/*/<layer>` (services and shared), and the foundation layer is `root/<foundation>`.
- Even outside the declaration, a module in a dir under root that has a layer name as a segment (a dir where the layer comes first — `controllers/core/…`) gets the layer rules by inferring the layer from the :role tag
  (if one layer allows the role, that layer; if two or more, the layer of the path segment; if it cannot be inferred, it is placed in the path segment's layer and the tag rules DOEFF104 and 105 give the reason).
  The module's `layer_reason` is "decided by the tag — …". Dirs of old features (with no layer-name segment) do not enter the population of the layer rules.
- `:legacy` is abolished (no escape hatch for old things is provided; all hits are emitted).
- A service's dir is its name with `-` changed to `_` (`land-notice` -> `controllers/land_notice/`). It can be changed with `{:dir "…"}`.
- A service with `{:public-contract http}` has a public contract that is only an HTTP entry. Listing it in another service's `:depends-on` is a settings error
  (it stops the shortcut of reading in-process — so that a service holding location state does not let a second owner be created). If not written = in-process.
  The value is only `http` (others are settings errors). It cannot be listed, so importing that service's module hits DOEFF116.
- `{:invariants ["module.path:function" …]}` is the list of a service's business-invariant functions (pure judgments that take records and return a list of breaches, `:role "judgment"`).
 If not written = None. A spelling that is not `module:name`, writing the same named target twice, and a value that is not a list are settings errors. A missing declaration and a named target that misses are DOEFF163.
- `{:entry-modules ["pkg.module" …]}` is the list of the entry modules of the service's code. A repo that has no layer dir (`<root>/<dir>/entry/`)
  (such as merge-queue, a repo divided by feature dirs) lets DOEFF163 judge "a service that has code" by this declaration — a service that writes it enters the
  population, and if the module's Hy file (`pkg/module.hy`) is not in the repo it is a DOEFF163 gap (`entry::<module>`). If not written = None (judged by the entry layer's dir
  as before). DOEFF136, 164, and 167 also decide the population and entry by the same declaration (for a service that writes it, the definitions in that module's files
  stand in for the entry layer's definitions, and it checks whether the deftests in the simulated environment and the deftests that reach counterexample clauses reach there). An empty list, a spelling that is not a `.`-separated module name,
  writing the same module twice, and a value that is not a list are settings errors.
- `:world-handlers` is the allowlist of definitions that may touch the outside world (only a few handlers may touch the outside world).
  The fields of an element `(world-handler "module.path:name" :touches [..] :answers [..]? :wraps [..]? :contract-test (none …)?)` are: `:touches` = targets touched (required; the closed words
  http, db, file, process, clock, env, cluster, network, thread), `:answers` = names of effects answered, `:wraps` = doeff real-I/O handlers run inside
  (`"module:name"`; definitions in the roster cannot be written), and `:contract-test` = the marker of a handler that does not require an edge test (the definition of how to write it is DOEFF137's fix text; the value is
  `(none :doeff-test "file::name")` / `(none :repo-test "file::name")` with the test name of the reason, read as the type `ContractTest::Waived(ContractTestWaiver::{DoeffContractTest, CoveredByRepoTest}(TestNodeId))`.
  The symbol `none` without a reason is readable but DOEFF137 fires. Other values and empty or malformed test names are settings errors; if not written, DOEFF137 requires an edge test). Writing the roster requires `:foundation`. In a repo that writes the roster, `[tool.doeff-linter.raw_side_effects]
  allowed_layers` is a double declaration (a settings error) — where raw I/O is allowed is decided only by the roster (the rules are added).
  Misreadings (a spelling that is not `module:name`, a word outside the set, writing the same definition or word twice, an element without `:touches`) are settings errors.
- `:stand-ins` is the roster of pairs of a counterpart handler that answers in place of the real one in the simulation and the real handler it stands in for (an extension of DOEFF137 — modeled on
  verified fakes and Pact: each counterpart has a contract test, and the same test is run against the real one). The fields of an element `(stand-in "module.path:name" :real "module.path:name" :contract-test (none …)?)`
  are: head = the counterpart's definition, `:real` = the real handler (required; a definition in a package outside the index is fine), and `:contract-test` = written and read the same as `:world-handlers`
  (a pair with a reasoned none is not judged; a none without a reason fires). Writing the roster requires `:edge-mark` (the same as DOEFF137's condition to enable). Misreadings
  (a spelling that is not `module:name`, an element without `:real`, `:real` being the counterpart itself, writing the same pair twice, an unknown key) are settings errors.
- `:boundary-parts` names modules that are boundary parts reaching the real outside world from the simulated environment — like entry points that humans run or edge stands, modules whose role is the real I/O
  itself and which are not handlers that answer effects. The fields of an element `(boundary-part "module.path" :touches [..] :reason "…")` are:
  `:touches` = the kinds of raw side effects allowed (required; the same closed words as `:world-handlers`) and `:reason` = why the real I/O itself is the role (required). Inside a declared
  module, evidence whose target (the raw-I/O classification mapped the same as above) is in `:touches` is not hit by DOEFF106 (both for files inside layers and files outside layer
  locations). Evidence outside the kinds and modules with no declaration hit as before. The evidence remains in the index, so DOEFF133 counts a deftest that reaches a part as an edge test and
  requires the `:edge-mark` marker. It takes effect only in repos that write `:world-handlers` (repos that decide what is allowed by the roster). Misreadings (a spelling that is not a dotted module,
  a word outside the set, writing the same module or word twice, an element without `:touches` or `:reason`) are settings errors.
- Real I/O handlers are known from doeff's catalog `data/world_handlers.json` (bundled with doeff-linter). A catalog element = `{"handler": "module:name", "touches": [..], "why": "…"}`. `:wraps` takes only those in the catalog (others are settings errors). Handlers of packages outside doeff are in a catalog file of the same shape kept by the package's repo; the using repo names it in `[tool.doeff-linter] world_catalogs = ["../<repo>/<file>.json"]` (paths from the config file's dir), and its rows are read on top of the bundled catalog (a row the bundled catalog or an earlier file already has stays the earlier row; a file that does not exist is read as no rows — the using repo's checkout of the package's repo can be behind for a while — and a :wraps that needs it is the settings error that names the file; a file that exists but cannot be read is a settings error). `--world-catalog <path>` (repeatable) replaces the settings' list: the commit hook passes the tip's catalogs as absolute paths to both of its runs (the HEAD tree is placed under the fact cache's root, where relative paths do not reach the package's repo), and `--split-rules` prints them as `world_catalogs` for the merge gate to pass the same way. DOEFF131 targets every handler in the catalog, and DOEFF133 counts as edges only those among the catalog's handlers, the roster's definitions, and raw-I/O evidence whose target matches `:edge-touches` (raw I/O is classified with async and thread mapped to thread, and time and random mapped to clock. A seeded `random.Random` is excluded from raw-I/O evidence — see "Seeded pseudo-randomness and external I/O" later).
- `:semantic-lines` is the drawn line for where, in the rules asked to Jev (DOEFF201, 202, 205), a violation begins. The fields of an element
  `(line "name" :rules [DOEFF201 …] :text "…" :fires [example …]? :silent [example …]?)` are: `:rules` = the rules of the questions the line is put into
  (any of DOEFF201, 202, 205; one or more), `:text` = the sentence of the line (not empty), `:fires` = examples that are violations under this line, and `:silent` = examples that are not violations
  (both may be omitted). An example = a code string `"<code>"`, or a pair of code and a one-sentence reason why it fires / does not fire `{:code "<code>" :why "…"}`
  (`:why` may be omitted. Writing the code as a bracket string `#[code[…]code]` avoids escaping quotes). The sentences and examples exist only in the repo's declaration, and the linter holds none
  (the source of truth is the one place in architecture.hy). How they are put into the questions is in section 10. Unknown rules, rules that are not Jev questions, Jev questions that take no drawn line
  (DOEFF203, 204), empty sentences, a missing `:rules` or `:text`, writing the same name or rule twice, empty code, a pair without `:code`, and an example with an empty `:why` are
  settings errors. It is not placed in editor-json's
  `architecture` (it is material for the questions, and the editor does not read it).
- Misreadings (unknown keys, duplicated services or layers, names of layers or services that do not exist, a missing :foundation layer) are settings errors in the form `architecture.hy:line:column: reason`
  (exit code 2).
- At the top level of editor-json is `architecture` (name, root, layers (name, summary, knows, does_not_know, question, roles), shared, foundation,
  open_layers, services (name, dir, description, depends_on, layers, public_contract — `in-process` or `http`, invariants — a list of `{module, name}` or null, entry_modules — a list of module spellings or null)). null if absent.

| Rule | How it judges | Key detail | Position |
|---|---|---|---|
| DOEFF114 | A module under root does not belong to any declared layer of a declared service, to a shared layer, or to foundation (directly under root, directly under a service's dir, or inside a dir not in the declaration — dirs where the layer comes first and dirs of old features alike, no exceptions). One per file. The hint gives a suggested move (`<root>/<:context tag>/<the path segment's layer or the :role tag's layer>/<name>`). `__init__` is excluded | none | the head of the file |
| DOEFF115 | A dir directly under root is not a declared service, shared, or foundation / a dir inside a service is not a declared layer. One per dir (the key's path is the dir) | none | the head of the dir's first file |
| DOEFF116 | When a module of service A imports a module of service B, B is not in A's :depends-on, or the target is not a layer that the layer of A's module may read as a dependency (that layer's `:dependency-layers`, or `:open-layers` if absent). Only the assembly layer (entry in agora) can also read the dependency's translation handlers with `:dependency-layers [intent protocol]`. Shared and foundation are not services, so they are not looked at. The declaration replaces DOEFF109 (in a repo that has architecture.hy, DOEFF109 settings cannot be placed) | the import target | the import symbol |
| DOEFF117 | A declared dependency (B in A's :depends-on) is not read by any module of A. info. Whole-project runs only | `A>B` | the defservice's name in architecture.hy |
| DOEFF140 | A module in a `:placed-dependencies` layer (service and shared locations — old layer-first dirs and foundation are excluded) imports a module outside the layer locations under root (directly under a service's dir, or inside a dir not in the declaration). The target is decided by the file of the import's spelling (the parent module's file if absent), and things in the layer index, package markers (`__init__`), and outside root are not counted. Judged only from the passed file's imports and locations (the whole-repo index is not read). One per same module. Default critical | the target module | the first import |
| DOEFF141 | Definitions judged only from fixed materials — for each definition in `:blind-definitions`, if a spelling from `:forbid-words` appears (as a substring; comments excluded) in the bodies of the repo Hy definitions transitively reachable from the definition by calls and by naming (passing as values) (including nested ones; the targets the index resolved names to, only modules that have a Hy file in the repo), one hit per reached definition and word. With `:no-imports True`, one per module for the definition's module's imports and requires (requires of `:allow-requires` modules are macro loads, so excluded). If the declared definition does not exist, one at architecture.hy's position (so a population of 0 is not made green). Only the declared module and the module files reached are read (the whole-repo index is not built). Whole-project runs only. Default critical | `<reached definition>:<word>` / `import:<module>` / `missing` | the word's first occurrence, the import, or the architecture.hy declaration |
| DOEFF147 | Definitions that fix the heads allowed to be called — for each definition in `:allowed-heads`, if the spelling of the head of a `( … )` in the definition's form (including nested ones) (symbols and keywords — special forms and macros included) is not in `:heads`, one per head. Strings, comments, forms discarded with `#_`, and elements of tuples, `[ … ]`, and `{ … }` are not counted as heads. If the declared definition (a form at the file's top level whose head starts with `def`) does not exist, one at architecture.hy's position (so a population of 0 is not made green). Only the declared module's file is read (no index is built either). Whole-project runs only. Default critical (#1413) | `<head>` / `missing` | the head's first occurrence, or the architecture.hy declaration |
| DOEFF148 | Spellings with fixed files where they may be written — for each group in `:confined-spellings`, reads Hy and Python (`.hy`, `.py`, `.pyi`) files that match `:files` (globs anchored at the repo root) and do not match `:except`, and if any of `:patterns` matches the body with comments removed (the inside of strings is counted — unlike DOEFF146), one per file (the number of hits goes in the message). If `:except` is empty, it is a spelling written nowhere in `:files`. A group with no file matching `:files` gets one at architecture.hy's position (so a population of 0 is not made green). A group that writes `:names-bound-in [<Hy file from the repo root> …]` also makes the names bound, within those files, to values that match the group's spelling into the group's spelling — the names of the pairs of `:environ {"name" "value" …}` in doeff-cluster's job declarations (at any depth; the value is a string) and the names of the top-level `(val name value)` and `(setv name value)` (including the names mangled to Python; the value is matched by the spelling of the form with comments removed — a list that lines up reference names, or an assembled name, too). Bindings inside functions are not read. It adds until names bound to values that match the added names stop increasing. A name's spelling gets `\b` attached if its end is a word character (`KEY_REF` does not match `KEY_REFS`. Hy's `-` is not a word character, so `KEY-REF-ENV` also matches `MY-KEY-REF-ENV` — the same breadth as a hand-written copy). Names like the reference names of secret locations, which keep being added to the declaration, are derived from the source of truth rather than held as a regex copy (with a copy, reading a new name in another file does not turn red — agora-controllers' group web-push-private-key). A file that binds names must be a Hy file matching `:except` (a binding in an outside file itself hits), and an empty list, outside the root, outside `:except`, an unreadable file, a file with unclosed parentheses, and a file from which no name can be derived are declaration errors when architecture.hy is read (the same as the per-file check of `:contract-files` — so the name in a file whose constant was moved inside a function does not silently drop out of the group). The hit's message writes the first hit's spelling, and the explanation writes the derived names. It walks only dirs at the head of a glob that contain no `*`. Whole-project runs only. Default critical (#1436 — originally agora-controllers' temporary judgment intake_only_rules.hy) | `<group>` / `<group>:missing` | the file's first hit, or the architecture.hy declaration |
| DOEFF161 | Spellings with a fixed count — for each declaration in `:counted-spellings`, counts the hits of `:pattern` in files that match `:files` (any extension — for Hy and Python comments are removed and strings counted; others are read as is), and one hit if it does not match `:count N` (exactly) or `:at-least N` (at least). If `:within [..]` is given, it counts inside the named top-level definitions (forms whose head starts with `def`) of Hy files, one definition at a time. If there is no file or definition to count, one at architecture.hy's position (so a population of 0 is not made green). Whole-project runs only. Default critical (#1437 — originally agora-controllers' temporary judgment intake_only_rules.hy) | `<name>` / `<name>:<definition>` / `<name>:missing` / `<name>:<definition>:missing` | the first hit or the head of the definition, or the architecture.hy declaration |
| DOEFF162 | The full set of effect declarations — for each declaration in `:effect-census`, collects, in Hy and Python files of `:files` (comments excluded), class declarations that inherit `:base` (default `EffectBase`) (Python `class X(… base …):`, Hy `(defclass [decorators] X [… base …])`. If `:base` is `EffectBase`, Hy `(defeffect X …)` too — defeffect always inherits EffectBase, #1464) and compares them with the `:effects` list. A declaration not in the list and a second declaration of the same name are one each at the declaration's position, and a listed effect with no declaration and a declaration with no file matching `:files` are one each at architecture.hy's position. Whole-project runs only. Default critical (#1438 — originally agora-controllers' temporary judgment intake_only_rules.hy part 2) | `<name>:<effect>` / `<name>:<effect>:twice` / `<name>:<effect>:missing` / `<name>:missing` | the declaration's position, or the architecture.hy declaration |
| DOEFF149 | The lineup of classes that hold a type's field — for each declaration in `:field-holders`, among module-level classes in Python files (`.py`, `.pyi`) matching `:files`, counts as holders the classes whose directly-under-body field (an annotated assignment `name: annotation`) annotation has the `:type` spelling as a word, and compares them with the `:holders` list (empty is allowed = no class holds it). A holder not in the list is one at the class's position, and a class in the list that does not hold (or does not exist) the field is one each at architecture.hy's position. If `:classes` is written, only the named classes are counted, and a missing class is also one. A declaration with no Python file matching `:files` is also one (so a population of 0 is not made green). A file that does not become a syntax tree is reported as unreadable. Whole-project runs only. Default critical (originally agora-controllers' temporary judgment record_body_rules.hy part 3) | `<name>:<class>` / `<name>:<class>:absent` / `<name>:<class>:missing` / `<name>:missing` | the class's position, or the architecture.hy declaration |
| DOEFF159 | Places and counts where a head may be called — for each declaration in `:call-sites`, collects the `(head …)` calls (excluding inside strings, comments, and `#_`) in Hy files matching the `:files` glob (excluding `:except`), and a call not inside any `:sites` definition is one per file. For each place, if `:count` is written, a mismatch in the number of calls; if `:parent` is written, a mismatch in the head of the immediately enclosing form; if `:branch` is written, one for a call outside the branch of a cond, when, if, or unless whose condition form has that symbol. If the place's definition does not exist or 0 files match `:files`, one at architecture.hy's position (so a population of 0 is not made green). It walks only the dirs at the literal head of the glob. Whole-project runs only. Default critical (#1414) | `<head>:outside` / `<head>:count:<name>` / `<head>:parent:<name>` / `<head>:branch:<name>` / `<head>:missing:<name>` / `<head>:empty` | the first call, the place's definition name, or the architecture.hy declaration |
| DOEFF160 | Broad exception catches only at the carrying boundary — for each group in `:broad-catches`, collects broad catches in Hy and Python files matching the `:files` glob (excluding `:except`). A broad catch = Hy `(except [] …)`, and `(except [type] …)` or `(except [name type] …)` where the type form has `Exception`, `BaseException`, or `AssertionError` (the last `.`-separated segment) (excluding inside strings, comments, and `#_`); in Python, an `except:` with no type, and an `except …:` whose type is those names, attributes, or tuples of them. What is allowed is only, inside a `:carriers` definition (a top-level form of a Hy file), a catch that binds the exception to a name and in which a call of `(:event's event …)` inside the catch passes that name directly as an argument. A catch outside a boundary is one per file, and a catch inside a boundary that does not bind a name or does not pass it to the event is one per boundary. If the boundary's definition does not exist or 0 files match `:files`, one at architecture.hy's position (so a population of 0 is not made green). If a Python file cannot be read as syntax, it is a run error. Whole-project runs only. Default critical (originally agora-controllers' temporary judgment fault_boundary_rules.hy (d)) | `<name>:outside` / `<name>:unbound:<definition name>` / `<name>:missing:<definition name>` / `<name>:empty` | the first catch, or the architecture.hy declaration |
| DOEFF113 | A :context mismatch within a declared service is raised to warning | | |

### Correspondence with existing tools

| Idea | doeff-linter (architecture.hy) | Tach | import-linter | Nx | Deptrac / ArchUnit |
|---|---|---|---|---|---|
| Declaring components | `defservice` (dir = service) | `modules` of `tach.toml` | `containers` of a contract | project | definitions of layer / slice |
| Dependencies between components | `:depends-on` (DOEFF116) | `depends_on` | `independence` and `forbidden` contracts | tag `depConstraints` | ruleset / `slices().should().notDependOnEachOther()` |
| Public interface | `:open-layers` (the intent layer) | `interfaces` | — | public entry point | — |
| Layers inside a component | `:layers` and a layer's `:imports` (DOEFF101) | `layers` | `layers` contract | tag `onlyDependOnLibsWithTags` | layer ruleset / `layeredArchitecture()` |
| Things not in the declaration | DOEFF114, 115 | — (files that do not belong to a module are out of scope) | — | — | Deptrac's uncovered report |
| Unused dependencies | DOEFF117 | `tach check --exact` (unused depends_on) | — | — | — |
| Pinning known violations | registry (`registry`) and `registered_severity` | exclusion settings | `ignore_imports` | — | baseline / `FreezingArchRule` |

The intent layer corresponds to Tach's interfaces — what another service may read is only the request and answer types (intent) that the service exposes outward.

## 10. Semantic rules (DOEFF201, 202 — Jev)

"What the code is doing", which deterministic rules cannot read, is asked of Jev (a TypeSafe System One model) as a Noul (probability) question.

| Rule | Question (kept in English — one place in `src/project/semantic.rs`) | Layers applied (settings) | Default threshold | Default severity (section 3) |
|---|---|---|---|---|
| DOEFF201 | Does it make business judgments (whom to permit, business rules, destinations, business outcomes) beyond rephrasing a request in the counterpart's way of speaking (jev-lint J2)? | `semantic.business_decision.layers` | warning p ≥ 0.8, info p ≥ 0.6 | critical |
| DOEFF202 | Does it know the means of communication (URLs and queries, HTTP methods and statuses, JSON wire, SQL, destination addresses) or the untyped shape of external data (reading and building dicts and JSON values by field name — whether or not json.loads is present) (jev-lint J3; the two are asked with equal weight)? | `semantic.transport_knowledge.layers` | warning p ≥ 0.6, info p ≥ 0.4 | critical |
| DOEFF203 | Does a deff's reason comment qualify as an accepted reason (section 11)? | `semantic.plain_callable` | warning_min 0.4, info_min 0.4 | from severity |
| DOEFF204 | Is a class that has methods with processing a value / external-world / stateful / other (section 12)? | `semantic.class_role` | warning_min 0.7, info_min 0.5 | from severity |
| DOEFF205 | Does a judgment / program definition mix shape checking with judgment (section 14)? | `semantic.mixed_concerns` | warning_min 0.7, info_min 0.5 | critical |

- **The default for a whole run (the whole repo for text / json / editor-json) uses the cache and fires only changed definitions** (rules that use Jev also run every time, everywhere, and use the cache by default): targets = files in path arguments, or files changed in git if none. Of those, only definitions with no answer in the local
  cache are fired, and for the remaining definitions with no answer, a proxy, if configured, is asked "only if it remembers" (see "Proxy that remembers Jev calls" below).
  A definition that should have been asked but got no answer is counted in `semantic.unmeasured` (could not be measured), and the exit code is 3 even if there are no error violations.
- **The default for a one-file `--stdin` (the hook right after writing, the editor) only reads the cache** (so that the layer-rule notice is not lost within the hook's overall 3-second limit; decision A of
  #1190). Naming `--semantic-changed` fires only definitions with no answer in the cache.
- `--semantic` = re-fire the target definitions even if the cache has an answer; `--semantic-all` = fire all definitions of the configured layers; `--semantic-cache-only` = fire nothing (only read the cache;
  not counted as could-not-measure — for runs where there is no network).
- **If a path named with `--semantic` / `--semantic-changed` does not exist on disk, it exits with code 2 without asking** (the one-line reason lists the nonexistent paths in quotes —
  zsh does not split an unquoted `$FILES` into words, and all the paths arrive as one nonexistent path joined by spaces, and before this the run silently ended with 0 questions, calibration not-run, and
  exit code 0). When a named Hy file has no definition that becomes a question (outside the question's layers, no top-level definition, half-written), that file is named on one line of
  `errors` (the exit code is unchanged — having nothing to ask is not an error, but it does not stay silent about why it is 0).
- **A definition that is half-written and unreadable (unclosed parenthesis, unmatched closing parenthesis, unclosed string) is not asked in any run** (counted as undetermined).
- Definitions asked = top-level defn, defk, deff, defp, defpp, defhandler, defeffect, defclass, defrecord, and defenum in Hy of the configured layers.
- state = the definition's name, kind, file, the source with the declared `:tags` removed (cut at `semantic.source_limit` characters = default 1,800), and the description of the layer it is placed in (architecture.hy's layer description, or
  `layers.describe`).
- **Drawn lines**: in a repo that writes architecture.hy's `:semantic-lines` (section 9), the `instructions` of the questions of DOEFF201, 202, and 205 include, in declaration order,
  the lines that have that rule in `:rules` — `"lines": [{"name", "text", "violating_examples": [example …], "complying_examples": [example …]} …]`
  (example = `{"code": …, "why": …}`; an example without `:why` is `{"code": …}` — #1995), plus the reading guide for the lines and the direction of the answer, `"lines_note"` (English,
  one place in `src/project/semantic.rs` — if a line differs from the general explanation, follow the line; code similar to an example that fires is on the violating side = true for DOEFF201 and 202, mixed for DOEFF205;
  judge by the reason in the example's `why`, not by how close the code looks).
  **The `instructions` of a question that includes lines are sent as a list of one-key objects in declaration order** (#1995): `[{"question": …}, general examples or note …,
  {"lines_note": …}, {"lines": […]}]` — DOEFF201 = question, business_decision_examples, not_business_decision_examples; DOEFF202 = question, note,
  transport_knowledge_examples, not_transport_knowledge_examples (#2059 — before, it was only question and `instructions` was a string); DOEFF205 = question, note,
  then lines_note and lines. When sent as a single-key object, it was ordered by key name, and in DOEFF205 the general note
  came after the lines, so "if it differs from the note above, follow the lines" read backwards. TypeSafe's `instructions` accepts any of string, object, and array
  (https://docs.typesafe.ai/api — read on 2026-10-01). The example code is passed with the declared `:tags` removed, the same as the definition's source. The text and why of the lines are
  put in as written in the declaration, and the question text stays in English. Questions to which no lines apply and questions of repos with no declaration stay as they are (the single-key object and the cache key do not change either).
  Adding or changing lines changes that rule's question key, and definitions go back to having no answer (re-asking is done together in a calibration run). The calibration watch's examples are also asked with the same question
  (the one that includes the lines).
- cache = the repo root's `.doeff-linter/semantic-cache/<key>.json` (placed outside git — whether to add it to `.gitignore` is per repo). key = sha256(model, question JSON, layer description,
  source with tags removed). The declared role is not in the key and is compared in code after the judgment. A definition with no answer in the cache is not made a violation and is counted in the top-level `semantic.unjudged` (it is not defaulted to pass).
  Besides the probability and choice, a cache answer holds the cost `reported_cost_usd` carried in the answer (only when upstream carried it; an answer that does not carry it is null, distinguished from 0) and
  `input_tokens` and `output_tokens` (only when present in usage). The older field `cost_usd` (which wrote 0 even when not carried) is not read — the cost of a previous answer is read as
  unknown.
- The severity is only warning or info (not error until measuring hits and misses is finished — the settings have no error field either). Misses go in the list of misjudgments (see "List of misjudgments and list of positives" below).
- The destination, model, and key are decided the same way as doeff's `packages/doeff-jev/src/doeff_jev/_target.py` (copied into Rust — the way of deciding is one):
  environment variables `JEV_BASE_URL` / `JEV_MODEL` / `JEV_WIRE` / `JEV_API_KEY` / `JEV_API_KEY_FILE` -> settings file `~/.config/jev/client.json` -> default = direct to TypeSafe
  (`https://api.typesafe.ai/v1/systemone`, model `jev-latest`, key `TYPESAFE_API_KEY` -> `~/.config/jev/api_key`). A gateway only when named.
  The communication shape (direct and gateway requests and answers) is a copy of the same package's `wire.py`. The key's value is not written to settings, output, logs, or the cache key.
- `semantic.workers` (default 8) concurrent calls, timeout `semantic.timeout_seconds` (default 30), and 429 and 5xx are retried up to 3 times. Definitions that could not be fired have their reason piled into `errors` and are counted as undetermined.
- **Calibration watch**: in each run that fires, one non-borderline positive and one negative per question from the bundled set (`data/semantic_calibration.json` — non-borderline labels from jev-lint) are asked once, and if they fall outside the band of positive p ≥ 0.8 and negative p ≤ 0.2,
  the cache is discarded and a warning is put in `errors` (suspected change in the model's contents). The model's versioned name that answered goes in `semantic.served_model` (only direct answers return it).

Settings:

```toml
[tool.doeff-linter.semantic]
business_decision = { layers = ["protocol"], warning = 0.8, info = 0.6 }
transport_knowledge = { layers = ["core"], warning = 0.6, info = 0.4 }
workers = 8
timeout_seconds = 30
source_limit = 1800
# A proxy that remembers Jev calls (repo proboscis/jev-proxy) — not used if absent
proxy_url = "http://jev-proxy.example:8878/v1/systemone"
proxy_peek_timeout_ms = 5000                     # default (the upper limit for the whole set of bundles of "only if remembered" questions combined)
```

**A proxy that remembers Jev calls**: a server that remembers answers by the sha256 of the normalized body (state, question, model) and passes only first-time keys to the real Jev
(repo `proboscis/jev-proxy` — moved from doeff's `packages/doeff-jev-proxy` on 2026-10-01; its entry is the same as TypeSafe's
`/v1/systemone`).

- The destination is pointed at by the per-repo setting `proxy_url` (not a machine-wide environment variable — repos that do not point at it work as before). If the environment variable `JEV_BASE_URL` exists, it wins.
- No Authorization header is sent to the proxy (nor the TypeSafe key — the proxy does not ask the caller's identity; the proxy's token was removed on 2026-10-02).
- Whole runs and hooks ask "only if remembered" about readable definitions not in the local cache, using bundles of proxy keys (`POST <proxy_url>/peek`, body
  `{"keys": [key …]}`, bundles of 1,000 fired one after another — definitions are not fired one at a time and the body is not sent. The proxy returns only the answers it remembers as
  `{"answers": {key: answer}}` and does not call the real Jev). The returned answers are written to the local cache. If the proxy is unreachable or times out, the remaining bundles are not fired and it works
  on the local cache alone.
- The proxy key = sha256(`jev-proxy-key-1` + newline + the body in a fixed spelling — object keys sorted by code point, no separator whitespace, characters left in UTF-8).
  That the proxy (`normalize-request` in `key.hy`) and the linter (`proxy_key` in `semantic.rs`) build the same key is checked by tests in both repos
  reading the proxy's sample (decimals are not in the sample because their spelling differs by language — the linter's body has no decimals). The source of truth for the sample is the proxy repo's
  `tests/key_contract.json` (the server decides the key, and the caller conforms), and the linter's `tests/proxy_key_contract.json` is a byte-for-byte copy as of when it was moved (2026-10-01).
  When changing the key rules, fix the samples and implementations of both repos in the same set of changes.
- The calibration watch's questions carry `Cache-Control: no-cache` (they do not use the memory — so that the proxy's memory does not hide a change in the model's contents).

editor-json: a violation's `source` (`linter` = deterministic rules, `jev` = semantic judgment) and `probability` (Jev violations only), and the top-level `semantic`
(`model`, `wire`, `judged`, `unjudged`, `unmeasured` (the number that could not be measured — definitions that could not be asked of Jev, and definitions for which the bundle reading the proxy's memory did not return within the waiting
`proxy_peek_timeout_ms`. The latter are not put in `unjudged`), `asked`, `peeked` (the number of answers the proxy remembered and returned), `cost_usd` (the sum over calls where upstream put the cost in the answer —
only Vercel's AI Gateway puts it, and direct to TypeSafe does not), `cost_unreported` (the number of calls that called upstream but carried no cost — if not 0,
`cost_usd` is a lower bound), `remembered` (the number of calls answered by the proxy's memory or by riding along — the header `x-jev-proxy` is `hit` / `coalesced`. Upstream was not called, so
cost and tokens are not counted), `input_tokens`, `output_tokens` (the sum of usage over calls that called upstream),
`served_model`, `calibration` = not-run / ok / drifted / failed, `false_positives`, `labeled` — see the sections below). `wire` is the shape and the way of deciding the destination (e.g. `direct(default)`, `direct(env)`, `direct(repo)` = the repo's proxy).

### List of misjudgments and list of positives

Jev's judgments are probabilities, and high-probability misses and low-probability hits are mixed. If misses were put in the registry, they would remain as "known violations" with a severity (level),
so hits that a person judged to be misses are placed in a store separate from the registry, and are **not emitted as violations and not counted**.

```toml
[tool.doeff-linter.semantic]
false_positives = ["scripts/doeff_lint/JEV-FALSE-POSITIVES"]   # list of misjudgments (counterexamples)
true_positives = ["scripts/doeff_lint/JEV-TRUE-POSITIVES"]     # hits that a person judged to be real violations (positives)
```

- Both are lists of dirs from the repo root. Each `*.txt` in them is one judgment: line 1 is the violation's key (the spelling of section 5 — copy editor-json's `key` as is),
  and line 2 onward is the person's reason for the judgment (non-empty lines read joined by spaces). **A file with no reason is not read as a judgment**, and the reason is put in `errors`
  (a judgment for which no reason can be written is not silently made effective). Unreadable dirs and files also go in `errors`.
- Only hits of the semantic rules (DOEFF201-205) take effect. A hit whose key is in the misjudgment list is not put in `violations`, and the number removed is put in the top-level
  `semantic.false_positives` (the text output shows it in the stderr summary line "N semantic rule misjudgments"). It is removed even if it is also in the registry.
- The list of positives does not change how violations are emitted (even if listed, it is not emitted if it does not reach the threshold). It is read only as material for measuring hits and misses.
- If the same key is in both lists, it is put in `errors` as a contradiction and is not read as either.
- `semantic.labeled` = matching a person's judgments against Jev's answers:
  `positives` / `negatives` = `{listed: the number listed, judged: of those, the number with a Jev answer, flagged: of those, the number that become hits at the current threshold}`,
  `items` = per judgment with an answer, `{key, rule, expect(true = positive), probability, flagged}` (in key order). Answers that do not reach the threshold are listed too
  (material for re-deciding thresholds). Judgments with no answer (undetermined definitions, definitions that no longer exist) are counted only in `listed`.
  `probability` is the probability compared with the threshold, read the same as the calibration watch: for yes / no questions, the answer's probability; for choice questions, **the probability of the target word**
  (DOEFF204 = `external-world`, DOEFF205 = `mixed`). It is not the probability of the chosen word — an answer of `judgment-only` 0.97 and `mixed` 0.02 is 0.02
  (before, it carried the chosen word's probability, and the threshold table for 205 was built on a misreading — #1944).
- Calibration procedure: ask the files with judgments via `--semantic <file>…` (or `--semantic-all`), and read the positives' `flagged / judged`
  (the share of hits caught) and the counterexamples' `flagged / judged` (the share of misses emitted) in `semantic.labeled`. This is separate from the bundled calibration watch (the check of whether the model's contents changed), and
  is not included in the watch's band (many human judgments are near the threshold, so being outside the band does not necessarily mean the model changed).
- Limits of reading: the key is built from the definition's name, so a judgment stays in effect even if the definition's contents change (the same as the registry). A person reviews judgments for definitions whose contents changed a lot.


## 11. Reasons for plain functions (deff) and how to write tests — DOEFF110, 111, 118, 203

Reading settings is the job of effects such as Ask and is not made an exception — tests that are not deftest are forbidden — whether a reason sentence is acceptable is judged by Jev: no label for the kind of reason is required. Whether it is accepted is decided by Jev looking at the reason sentence and the source.

In `defarchitecture` of architecture.hy, declare the types of accepted reasons and rejected reasons (names, descriptions, and fixes are not written in Rust):

```hy
:plain-callable-reasons [(reason library-callback "an outside library calls a plain function in a fixed form (sorted's key, dataclass's __post_init__, etc.)")
                         (reason macro-time "called at macro-expansion time")]
:rejected-plain-callable-reasons [(reason config-read "reads a settings file, environment variables, or arguments" :fix "make it a defk that receives settings through an effect such as Ask")
                                  (reason test-helper "the body of a test, or a helper that builds a test's values" :fix "make it a deftest, and make the helper a defk called with (<- …)")
                                  (reason handler-assembly "assembles a list of handlers" :fix "make it (defk handlers-of [foundation] …)")]
```

- Comment form: `; cannot be a defk: <free-form reason>` (on the definition's line, or on a comment-only line just before it; the earlier Japanese spelling `defk にできない:` of the marker is read as well). `; cannot be a defk(<kind>): <reason>` is also accepted but not required.
- **DOEFF111** (deterministic): only a missing comment, an empty reason, and "ditto" and its variants (the Japanese words 同上, 上と同じ, 上に同じ, 前と同じ, 同前 = "ditto", "same as above", "same as above", "same as before", "ditto", matched by the code) are errors. The content of the reason is not judged.
  The list of accepted reasons is inserted into the reason.
- **DOEFF110**: if the same-line comment of a defn names a kind in the list, hint = "make it a deff (reason X)", otherwise "make it a defk".
- **DOEFF203** (semantic, Jev, Choice): for each deff that has a reason sentence, the definition's source (tags removed) and the reason sentence are made the state, and Jev is made to choose from the accepted reasons, the rejected types, and none
  (the question text is in English in one place, `plain_callable_wire` in `src/project/semantic.rs`). Severity:
  If an answer that is not accepted (a type or none) is chosen and its probability is at least `semantic.plain_callable.warning_min` (default 0.4) -> warning, below that -> info. Even if an accepted reason is chosen,
  if the sum of the probabilities of the non-accepted answers is at least `info_min` (default 0.4) -> info. It is never an error. The reason and hint show the chosen rejected type's `:fix`.
  It fires only with `--semantic` / `--semantic-all`. The thresholds are provisional (until measuring hits and misses is finished).
- **DOEFF118** (tests are only deftest): in Hy files matching a glob of `definitions.test_paths` (`**` = zero or more segments, `*` = any spelling within a segment, a spelling without `/` is matched against the file name),
  a `defn`, `defn/a`, `deff`, or `defk` whose mangled name starts with `test_`, and `(setv name (fn …))` / `(val name (fn …))`, is an error. The key is
  `<path>::DOEFF118::<name>`. If it is in the registry, `registered_severity`.

## 12. What is inside a class — DOEFF119, DOEFF204

Convention (a value such as Point2D may be a class, but a client or store should be a handler and an effect. That distinction may be judged by Jev). It is not judged by name but separated by evidence in the contents. The population is the business Hy files of `definitions`.

- **Allowed (not emitted)**: a class whose base is an exception, Enum, or Protocol (name ends in Error, Exception, Warning, Enum, Flag, Protocol, NamedTuple, or TypedDict), or
  a class from a module outside the repo (the leading segment of the import-bound module is not in the repo root; a bare name with no binding is a builtin).
- **DOEFF119 error — touches the outside world**: there is strong hy-index version 3 evidence of raw side effects (raw.direct or raw.via — the same catalog as DOEFF106)
  in a definition inside the class (a method, a field's initial value) or in the class expression itself. A resource placed in a field in `__init__` (`(setv self.http (httpx.Client))`) falls here too.
  Fix = a foundation handler (the resource goes in `(session val …)` directly under a defhandler, which answers effects such as ListRows and PutRow).
- **DOEFF119 warning — holds changing state**: a method other than `__init__`, `__post_init__`, and `__new__` rewrites a field of self (the target of `setv`/`setx` is
  `self.x`, `(get self.x k)`, or `(. self x)`; `+=` and the like; `del`; `setattr self`; or mutating methods such as `(.append self.x …)`). Fix = "values are defrecord (immutable),
  behavior is a pure function that returns a new value, state is placed in one place, a handler's (session var …) such as world, and changes flow through effects. Even when rewriting is needed for speed,
  the rewriting is only inside the handler".
- **DOEFF119 info — fields only**: there is no method with processing other than dunders (even if only a `__post_init__` check). Fix = defrecord (:tags, :check).
- Evidence by way of (raw.via) is computed only in whole runs. A one-file run (saving in the editor) judges only by direct evidence.
- **DOEFF204 (Jev, Choice, warning or info only)**: asks only about classes for which DOEFF119 emits nothing and which have methods with processing other than dunders. state = the class's
  source (tags removed, cut at `source_limit`) and the field declarations (`name: type`). Choices = value / external-world / stateful / other (the question text is in one place,
  `ClassRole` in `src/project/semantic.rs`). external-world or stateful gives a warning at `semantic.class_role.warning_min` (default 0.7) or above, and
  info at `info_min` (default 0.5) or above. The calibration watch compares the external-world probability of 2 synthetic examples (a front desk that holds a client in a field, and Point2D).

## 13. Where JsonValue is used — DOEFF120

Convention: do not let JsonValue be used directly; use values with types resolved and checked, as pydantic does. Only generic resolvers and foundation modules that do the sending and receiving themselves may touch JsonValue (a type that merely wraps the plain `dict | list | str | int | float | bool | None` with a name).
Other modules see only typed values. Examples and settings are in [rules/DOEFF120.md](rules/DOEFF120.md).

- **Condition to be enabled**: architecture.hy exists (`project_wired` is whether architecture exists).
- **Population**: all of the repo's Hy (hy, hyk, hyp) and Python (py) files. Hidden dirs starting with `.` and `node_modules`, `target`, `__pycache__`, `venv`, and
  `site-packages` are not descended into. Files that contain none of the four names in the body are not read.
- **What is counted**: in Hy, symbols of the reader whose last `.` segment is `JsonValue`, `JSONValue`, `JsonObject`, or `JSONObject` (imports, definitions, annotations, contracts,
  inside tuples, inside quotes). Strings, comments, docstrings, and `#_` forms are not counted. In Python, lexical names (names, attribute names, import names), and words (not adjacent to identifier characters) inside annotations (arguments,
  return values, `x: T`) and in strings of type aliases (the value of `X: TypeAlias = …` and `type X = …`). Docstrings, comments, and f-strings are not counted.
- **Allowed modules** (`json_value_allowance` — the policy exists in this one function only and can be swapped):
  1. The module's spelling (the path from the repo root with the extension removed and `/` replaced with `.`) is `doeff_hy._wire` or `doeff_records._wire`, or ends with `.` + that.
  2. It matches a pattern of architecture.hy's `:wire-modules` (`.`-separated; `*` is any spelling within a segment, `**` is zero or more segments) and also falls in the `:foundation` layer by layer location.
     Even if it matches, it is not allowed outside foundation (the reason goes in the explanation).
- **Violation**: one per module, error. Key `<path>::DOEFF120` (no detail). The position is the name of the first use. The explanation's subject has the name used, the count, the first line, and the other lines
  (up to 20 lines; the rest as a count). If it is in the registry, `registered_severity`.
- **Settings errors**: an element of `:wire-modules` that is not a module spelling (contains `/`, an empty segment, `**` within a segment), the same spelling written twice, and a declaration with no `:foundation`.
- **Reverting**: remove the rule (values already turned into types are fine as they are and cause no harm).

## 14. Smell rules — DOEFF121-125, DOEFF205

Adds smell rules. The subject matter is decide-tag in agora-controllers' controllers/kanban/core/tag_judgment.hy
(at the time of the decision, the linter emitted nothing for this file). The record is doeff's ADR-DOE-HY-007 R9-R11.

Settings:

```toml
[tool.doeff-linter.smells]
shape_check_layers = ["core"]          # layers DOEFF121 applies to (judgment layers). If absent, DOEFF121 is not wired
[tool.doeff-linter.rules.DOEFF124]
severity = "info"                      # severity of a smell rule (warning or info — default warning; error cannot be chosen)
[tool.doeff-linter.semantic]
mixed_concerns = { layer = "core", roles = ["judgment", "program"], warning_min = 0.7, info_min = 0.5 }   # DOEFF205
```

| Rule | Shape that hits | Key detail | Position |
|---|---|---|---|
| DOEFF121 | In a definition in a file of a judgment layer, an `(isinstance … T)` on the `n` of `(val\|var\|setv\|setx\|:=\|<- n (.get x "field"))` or on `(.get x "field")` itself | `<definition>::<field>` | the `.get` expression |
| DOEFF122 | The type at the head of the pattern of a `(match subject …)` arm is a failure type, the arm's body (the last expression if it is a `do`) is `(return E)`, and E refers to the subject, a name bound by the pattern, an `:as` name, or a name made from them in the arm (such as `(<- n T X)`) | `<definition>::<subject's name>` (the type's name if the subject is an expression) | the pattern |
| DOEFF123 | In two adjacent children `(<- x …)` `(return x)`, x (`x` and `x.field`) appears exactly 2 times in the definition | `<definition>::<x>` | the `<-` expression |
| DOEFF124 | A `(+ …)` containing string arguments, with two or more fields `v.a`, `(str v.b)`, `(repr v.c)` of the same value v / an f-string `{v.a}…{v.b}` | `<definition>::<v>` | the expression |
| DOEFF125 | Inside a for / while (comprehensions are not counted), `(:=\|setv\|setx xs (+ xs #(…)))` or `(… xs (+ xs […]))` | `<definition>::<xs>` | the expression |

- Only one is emitted for the same detail of the same definition. The population is the business Hy files of `definitions` (including test locations — decided by the population settings).
- **Failure types** (DOEFF122): reads, among the repo's Hy files, those containing the spelling `:failure` or `:absent`, and collects the names of `(defrecord name "doc"? {… :failure True …} …)` and
  the types of `(defeffect name "doc"? {… :failure [A …] :absent [B …]})`. Names are resolved to modules by the file's imports (`(import m [T])` -> `m.T`; `n.T` of `(import m :as n)` ->
  `m.T`) and by where definitions are (a bare name with no binding -> that file's module), and the arm's type is resolved the same way before comparing — even if a same-named type is in another module,
  only the declared one is a failure type. If there is no declaration at all, DOEFF122 emits nothing.
- **Severity**: default warning (ADR-DOE-HY-007 R9 — raised from info because stages 1 and 2 of Absent / Raise entered the main line). A warning in the registry is info.
- **DOEFF205** (Jev, Choice, warning or info only): for top-level functions (defk, deff, defn, defp) whose role (the role in the definition's :tags, or the module's head tags if absent) is in `roles`,
  makes Jev choose from mixed / shape-only / judgment-only / neither. state = the definition's source (tags removed, cut at `source_limit`) and
  the description of the `layer` layer (summary, knows, does_not_know). mixed gives a warning at `warning_min` or above and info at `info_min` or above. The calibration watch compares the mixed probability of
  decide-tag (positive) and card-tags-of (counterexample). The question text is in one place, `MixedConcerns` in `src/project/semantic.rs`.
  The question's general note calls shape checking "checking untyped input that came from outside", and does not count as shape checking a type-union branch, an untyped dict that did not come from outside (a code constant,
  the record of its own tally), or a branch that merely returns a refusal value from the answer (the problem sentence) of the one function entrusted with shape checking (the previous note
  called isinstance and dict reads shape checking without exception, which disagreed with agora-controllers' line 4).
  If architecture.hy's `:semantic-lines` has a line with `:rules [DOEFF205]`, the line's text and the examples that fire and do not fire go in the question's `instructions`
  (the "Drawn lines" of section 10 — what counts as shape checking and what counts as judgment is decided by the repo's declaration).

## 15. Bare calls of defk — DOEFF126

A convention (reversible). The facts: in the fix round of #798, `latest-by-ref` was changed to a defk, but
the deff that calls it (`text-at`, `expected-inputs`) and 4 test files kept calling it bare — calling a defk bare returns a Program rather than the answer, and because of a type
error it does not fall but flows on quietly as a wrong value (found by a test). The record is doeff's ADR-DOE-HY-007 R13.

- **The set of defk**: collects the top-level `(defk name …)` (also inside `do` and `eval-and-compile`) of repo Hy files (those containing the spelling `(defk`) as
  `<module>.<name>`. The head of a call is resolved to a module by the file's imports and where definitions are before comparing (the same as `smells::Scope`). Calls that cannot be followed
  (a function received as an argument, a method) are not picked up.
- **Where it picks up** (where the answer is used as a value): the arguments of comparisons, arithmetic, and boolean combinations (`=`, `+`, `in`, `not`, `and`, ...) and of builtin functions that read the answer
  (`len`, `str`, `get`, `sorted`, `isinstance`, ...), the target and arguments of methods (`(.get (f …) "field")`, `(.append out (f …))`) and attributes (`(. (f …) field)`), conditions (the head of `if`, `when`,
  `unless`, `while`, and the conditions of `cond`), the source of repetition (the source of a `for` binding, the source of a comprehension), and record fields (the arguments of a call that constructs a type with a capitalized head —
  excluding types of doeff's package and types at the position emitted as an effect `(<- (T …))` and `(! (T …))`). The branches of `if`, `when`, `cond`, `do`, and `let` inside those positions
  are the same.
- **Fields that carry a Program are not picked up**: for a field of a repo Hy `defrecord` whose declared type annotation names `Program` or `EffectBase`
  (`(#^ (| Program EffectBase) prepare)`, `#^ Program later`, `doeff.Program` — compared by the last segment of the annotation's words), it is a declaration that carries a Program,
  so the value of `:field value` in a call that constructs the type is not a place where the answer is used. Still picked up: a field with no type annotation, an `object` / `Any` field, a field hidden by an alias
  (an alias is not spelled Program), a value passed positionally, and a type defined outside the repo (such as a Python dataclass). The fact: doeff-records'
  `start-records-server` has `(RecordsServing … :prepare (_ready-handlers …))` — a field for a Program that `serve-records` runs as a preparation task.
- **Where it does not pick up**: the right side of `(<- …)`, `(! …)`, `(return …)`, arguments of a call that receives a Program (including passing to a repo function — it may be a function that
  receives a Program and runs it, which cannot be followed), binding to a name, and the form returned as a function's answer. The first version was "everything except places where it is passed as a Program", but on agora's main line it came to 239 hits
  (mostly passing to run-on, in-record, run-wired, and the like that receive and run a Program), and since a miss is costly at error severity, it was narrowed to
  places where the answer is used (1 on the main line).
- **`defsystem` is a definition** (doeff-hy — expanded into a function, and the expression of a job row `(name (job-function foundation …) :needs … :environ …)` is
  the Program value itself of "name -> Program", not executed). It is descended into with the same judgment as inside a definition, so the job row's expression is not picked up, and only places inside it where the answer is
  used (such as `:environ {"X" (str (f …))}`) are picked up. The key's `<definition>` is the system's name. The fact: on agora's main line, 43 job rows of a module-top-level defsystem
  were hitting as `<module>`.
- **Severity**: error (because it is a silent mistake). If in the registry, `registered_severity` (default warning). The population is the business files of `definitions` and test locations.
- **Key**: `<path>::DOEFF126::<definition>::<defk called>` (`<module>` outside a definition). The position is the call expression.
- **Bare calls of a function received as an argument** (the second form): among all calls `(g … argument …)` in the repo, collects those whose argument is the name of a repo defk or `(fnk …)`
  (which position for a positional argument, the name for a keyword argument), and looks that name up in the argument list of the callee g's top-level definition (defk, deff, defn, defn/a). If a call `(name …)` with that name at its head in
  g's body is outside the places that pass a Program (the right side of `(<- …)`, `(! …)`, `(return …)`, `(yield …)`, and arguments of a call that receives a Program),
  that call in the callee is made a violation (also a binding to a name `(setv v (name …))` — the caller passes a defk, so a Program
  gets bound). Key `<path>::DOEFF126::<definition>::<argument>`, and the explanation gives where it was passed and what was passed. The fact: `field-of` of `rows-by-text` at agora L1550.
- **Ranges it cannot pick up** (ADR-DOE-HY-007 R14): the caller puts the defk in a variable or field before passing it, or wraps it with partial and the like before passing it; the callee passes that argument
  on to another function and calls it bare there (only one level is followed); the callee is a method, a nested function, or something that cannot be looked up by name; and callers outside the repo.

## 16. Headings and binding types of defk — editor-json version 2

Material for the editor (doeff-runner) to draw the types, effects, and tags of defk in a read-only display. The way types are read is placed in one place in this linter's
`src/project/signatures.rs`, and the editor keeps no copy (a source is not rewritten by hand,
so there is no conversion in the writing direction). Only when `--stdin --path <file>` is used, that file's part is emitted (empty in a whole run).

```jsonc
"signatures": [{
  "kind": "defk",                        // defk | deff
  "name": "fetch", "path": "/abs/core/flow.hy",
  "range": {…},                          // range of the name
  "full_range": {…},                     // the whole definition form
  "contract_range": {…},                 // the contract dict {:pre … :post … :effects … :tags …} (null if absent)
  "params": [{"name": "id", "type": TypeRef | null}],   // `(: name type)` of :pre. null if not written
  "answer": TypeRef | null,              // `(: % type)` of :post (or the name's annotation #^ T if absent)
  "absent": true,                        // whether the answer is Maybe (Absent can escape to the caller)
  "raises": [TypeRef],                   // types of Raise that can escape to the caller
  "effects": {"declared": [EffectRef] | null,   // :effects (#800). null if not written
              "inferred": [EffectRef],          // inferred (below)
              "complete": true},                // whether inference could trace everything — false if it fires an untraceable call (a function outside the repo, deff, method), and
                                                // inferred is only what was seen (the editor does not draw "no effects" even if it is empty)
  "tags": {"context": "demo", "role": "program"}
}],
"bindings": [{
  "form": "<-",                          // <- | val | var | setv | :=
  "modifier": "lazy" | null,             // the word before (lazy val …) / (session var …) (form is val / var)
  "name": "row", "path": "…",
  "range": {…}, "form_range": {…}, "head_range": {…},   // the name; the whole form (including parentheses); the head symbol
  "annotation_range": {…} | null,        // the T of (<- x T e)
  "value_range": {…} | null,
  "type": TypeRef | null,                // null if unknown (not filled in with a different type)
  "origin": "effect",                    // annotation | effect | call | literal | constructor | var | unknown
  "absent": true, "raises": [TypeRef]
}]
// TypeRef = {"kind": "name", "name": "Row", "definition": {"path", "range"} | null}
//         | {"kind": "union", "members": [TypeRef]} | {"kind": "apply", "head": TypeRef, "args": [TypeRef]}
//         | {"kind": "unknown", "text": "the spelling as written"}
// EffectRef = {"name", "definition": {"path", "range"} | null, "answer": TypeRef | null, "absent": [TypeRef], "failure": [TypeRef]}
```

- **Type expressions**: a symbol is a name, `(| a b)` is a union (nested ones are flattened), `(of H a …)` is an application, and anything else is an unreadable expression (`unknown`). A name is resolved to a module
  by the file's imports and where definitions are (`smells::Scope`), and if it matches a definition in the repo's Hy (defrecord, defwire, defenum, defclass, defeffect, deftype, and
  a val whose head is capitalized), the position goes into `definition`. Builtin types and names that cannot be resolved are null.
- **Inferred effects**: if the head of a call fired in the body (the right side of `<-`, the content of `! …`) is a defeffect, that effect; if a defk, that defk's
  inference is added. It is a fixed point over a table collected from all of the repo's Hy files (a pre-save file is read from the stdin content). Effects received by a handler are not subtracted
  (the set is an estimate from above). Disagreements with the declaration are emitted as they are, and the editor shows them.
- **Absent and Raise**: firing an effect that declares `:absent` (or a defk that is `absent`) makes the answer a Maybe. `(<- … :absent F)` turns into a Raise of F's head type,
  and inside `absent-as` the Absent is received. Raise = the `:failure` of the fired effect plus the Raise of the fired defk (inside the program of `on-raise`
  it is subtracted).
- **Binding types**: `(<- x T e)` is the annotation (Absent / Raise from e), and `(<- x e)` is the value answer if e's head is an effect (the elements of `:answer` with
  `:absent` and `:failure` removed) and that defk's answer if a defk. `val` / `var` / `setv` are literals (strings, numbers, booleans, None, dicts, lists), calls of type names,
  answers of deff, and `(! e)`. `(var x None)` is a place where a different value is put later, so it is unknown (null). `(:= x v)` is the type of `(var x …)` in the same top-level definition.
  Insides of quotes are not read.
- **Time**: in a pre-save one-file run, all of the repo's Hy files are read to build the table (0.4 to 1.2 seconds on about 950 files of agora-controllers).
- **Words the editor does not know**: when the linter adds a word to a closed set (rule families, kinds of headings, forms and origins of bindings, kinds of type expressions),
  the editor does not discard the output, but shows only that item in the default look and shows "the extension is old" (#848).
- **Arguments of assert**: the condition and message of `(assert condition message)` are also places where the answer is used (putting a defk in a condition makes the Program always true, so the test always passes).
- **Using a name after binding it** (within one definition only): if a name that binds a bare defk call with `setv`, `val`, `var`, `:=`, or `let` appears in a place where the answer is used
  (comparisons, builtin functions, the target and arguments of methods, `name.method` calls, attributes, conditions, the source of repetition), the bound call is made a violation.
  Forms that pass or return it as a Program are not picked up. A definition that also binds the same name with `(<- name …)` does not follow that name. Passing the bound name to another definition, and
  putting it in a container and taking it out, cannot be picked up (ADR-DOE-HY-007 R14).

## 17. Display replacement that shows calls in the form `f(a, b)` — editor-json `rewrites`

Material for the editor to **merely show** calls in the bodies of defk / deff in a form close to Python (showing calls as `f(a,b)` rather than `(f a b)`, and leaving the `!` mark for effects in the middle of an expression — plan A). The Hy source is not changed. The reading of expression shapes is
in one place, `src/project/call_view.rs`, and the editor draws without re-reading the Hy. A field added to version 2 (old editors skip it).
Only when `--stdin --path <file>` is used, that file's part is emitted (empty in a whole run).

```jsonc
"rewrites": [{
  "kind": "call",                 // call | method | infix | prefix | perform | bind | subscript | attribute
  "path": "/abs/core/flow.hy",
  "range": {…},                   // the whole original expression (including parentheses)
  "original": "(f a :k v)",       // the original lisp (character for character)
  "text": "f(a, k=v)",            // the display with inner replacements applied too (newlines and indentation as in the original)
  "edits": [{"range": {…}, "text": "(", "effect": null}],   // hides the original character range (empty means just insert) and shows text. effect = the name of the effect for the device's picture
  "parts": [{"range": {…}, "name": "f", "role": "defk",      // effect | defk | deff | type | function | builtin | local | method
             "definition": {"path", "range"} | null, "answer": TypeRef | null}],
  "parent": 3 | null              // number of the outer replacement (when the outside is shown as the original lisp, the inside is also the original lisp)
}]
```

| lisp | Display |
|---|---|
| `(f a b)`, `(f a :key v)`, `(f #* xs)` | `f(a, b)`, `f(a, key=v)`, `f(*xs)` |
| `(.get row "k")`, `(get row "k")`, `(. row id)` | `row.get("k")`, `row["k"]`, `row.id` |
| `(+ a b)`, `(= a b)`, `(is-not x None)`, `(not x)` | `a + b`, `a == b`, `x is not None`, `not x` (with Python precedence; parentheses only where it changes) |
| `(! (f a))` | `!f(a)` (if the fired call is an effect, the effect's name goes in the `!` edit) |
| `(<- (f a))` (a `<-` with no name) | `<- f(a)` |
| `(Effect a)` | `Effect(a)` (the effect's name goes in the `(` edit. Inside `!`, the `!` holds it) |

- **Heads read as calls**: names imported, definitions in the repo (defk, deff, types, defeffect), top-level definitions in this file, Python builtins, local names inside a definition
  (arguments and bindings), and names of the form `a.b`. Macros brought in by `require`, control forms (`when`, `if`, `match`, `for` …), and unknown heads stay as lisp
  (only the calls inside are replaced). Operations inside a lisp form keep their parentheses (`(when (a and b) …)`).
- **Indentation**: not rebuilt. Where arguments continue onto the next line, only a `,` is inserted after the previous argument. Operations and methods whose head and first term are on different lines stay as lisp.
- Name bindings (`(<- x T e)`, `val` …) are drawn by `bindings` in section 16, and this replaces only the value expressions. Insides of quotes are not read.

## 18. Disagreement between the `:effects` declaration and inference — DOEFF127

Convention: the linter's notices go at the place of violation, with what is violated attached. Before, the extension (doeff-runner) compared the declaration and the inference inside a defk's heading and showed them in labels such as "no declaration" and "2 violations".
The judgment is moved to the linter and emitted as a rule violation at the place of violation (the extension only draws).

- **Target**: only defk that write `:effects`. `:effects` is optional (#800), so a defk that does not write it is out of scope ("no declaration" is not made a violation).
  The population is the business files of `definitions` (including test locations — if under `paths`).
- **Inference**: the same `signatures::World` (one place) as the headings in section 16. The head of a call fired in the body (the right side of `(<- …)`, `(! …)`) is resolved to a module,
  and if it is an effect, that; if it is a repo defk, its inference is followed and collected. A form that puts a branch (`if`, `when`, `cond`, `match`, `do`, `let`) in the firing position
  counts each branch's call as fired one at a time.
- **Two kinds of violation**:
  - Raising an effect not in the declaration — position = the head of the first fired call that leads to that effect (the defk's name if it goes through a defk). The sentence writes the effect raised and
    the call it went through.
  - Not raising a declared effect — position = that name inside `:effects`. A defk that fires even one **untraceable call** (a function outside the repo, deff, method, or something that cannot be looked up by name) does not
    emit this, because the inferred set may be incomplete. The first version (without this suppression) had 19 of 23 hits on agora's main line be misses of this form
    (protocol defks that go through a foundation function such as `read-typed`), so it was suppressed (4 on the main line — all effects not in the declaration).
- **Severity**: warning (a reversible decision, #849). The reason it is not an error = inference is an estimate from above that does not subtract effects received by a handler, and can report an effect received
  inside the body as "not in the declaration". The reason it is not info = since the heading labels are dropped and it is emitted only at the place of violation, a severity that is not overlooked is needed.
  If in the registry, `registered_severity`.
- **Key**: `<path>::DOEFF127::<definition>::<effect>` (effect is the spelling without the module).


## 19. (Retired) DOEFF129

Removed (the axis of splitting judgment / program by the presence of effects is not a responsibility boundary). The number is not reused for another rule.


## 21. A translation handler emits a business intent — DOEFF130

A responsibility-boundary rule. A handler in the translation layer (protocol) merely re-emits the intent it received as doeff's generic effects
(HttpRequest, reads and writes of doeff-records, clock, file, process, Ask …) and does not emit business intents (types of the intent layer).
If it emits a business intent, the translation handler takes on business judgment and flow.

- **Target**: top-level handlers in Hy modules of the layers in the settings' `translation_effects.handler_layers` (default protocol) — `(defhandler name …)`, and
  `defk`, `deff`, and `defn` whose arguments are exactly `[effect k]`. The population is the layer population (section 4). A defk that is not a handler is not a target (if a handler
  calls it, it is followed as a callee).
- **Business intent**: the name obtained by resolving the head of an executed call to a module is a capitalized name (a type) in a module of a layer in `translation_effects.intent_layers` (default intent).
  It may be a `defeffect` or a `defclass` (the intent layer holds only types — executing a type means emitting an intent).
  The module is resolved by the layer population's index (package `__init__` too). Only intents of the **same service** as the handler are counted — emitting another service's intent
  (a public contract) is allowed as translation's job (the same reading as DOEFF156's "other services' public effects").
  When the service of either the handler or the intent is not determined (a layer-first location), it is counted as before.
- **Inference**: looks in order at the calls executed in the handler's body (`(<- …)`, `(! …)` — read by the same `signatures::World` as section 18), and if the head is a business intent
  it hits; if the head is a repo defk it proceeds to that defk's executed calls (also through an imported defk). The upper limit on the number of defks followed is
  `translation_effects.max_depth` (default 8; 0 means only intents emitted directly in the handler's body). The same defk is looked at only once within the same handler
  (it stops at a call cycle). What lies beyond untraceable calls (a function outside the repo, deff, method) is not counted.
  - Checks that judge only by names in a definition's body (DOEFF201, agora's `check_business_fakes.hy`) slip past the form where a handler emits an intent by way of an imported function.
    This rule follows through to the defk to close that hole (the independent review of #942).
- **Place**: the head of the first call in the handler's body that leads to that intent. The sentence writes the handler, layer, path of defks passed through (`a -> b`), and intent.
  One per handler and intent.
- **Severity**: error (a settled rule — judged only by the handler's body and the repo's definitions). The severity declaration (`rules.DOEFF130.level`) is written in the user side's settings
  (agora uses critical — #957).
- **Key**: `<path>::<law or DOEFF130>::<handler>::<intent>` (handler is the mangled spelling, intent is the name including the module).
- **Settings**: if the section `[tool.doeff-linter.translation_effects]` is not written, the default (protocol, intent, 8) applies and it hits only when layers of those names both exist.
  If the section is written but a layer name does not exist, it is a settings error (exit code 2).
- **The side it does not make**: effects of the foundation defined in the repo are not judged (foundation is the place for generic effects). Python handlers are not read.


## 16. Unreadable Hy files — DOEFF128

- A file for which the reader (doeff-indexer's hy_index::reader) finds an unclosed parenthesis or string has violations missing from rule judgments, as far as it could read.
  Before, only the reason sentence was piled into the overall `errors`, the editor's violations pane for that file was empty, and the hook said nothing (2026-09-28 — agora's
  controllers/durable/protocol/contract.hy could not be read because of a reader bug).
- Files that rules read (layer locations, the population of the definition rules, test locations, and the population of business names) that cannot be read are emitted, regardless of the list of enabled rules, as
  an error violation DOEFF128 (key `<path>::DOEFF128`, outside the registry) at the first unreadable place. It appears in all of the editor, hook, and text.
- The reader skips the insides of an f-string's replacement field `{…}` as a Hy expression (strings in the field, nested f-strings, and a parenthesis not closing a string
  — the same as Hy's `hy.read_many`). `{{` is a literal `{`.

## 20. Body text of definitions — editor-json `bodies`

The reading pane of doeff-runner (webview) shows the substance of definitions and so on as HTML cards, and **shows only the body as text**. This is the material for the lines of that body. The shape of the body text
has been approved by the operator, and the source of truth for the table is section 2.2 of `docs/design/hy-reading-plane/artifacts/v2/design.md`
and section 2 of `v3/design.md` (the source of truth for meaning is ADR-DOE-HY-006). The reading of Hy forms is placed in one place, `src/project/body_view.rs`, and the pane only draws lines and
character ranges (the decision of #849 "keep no copy of the way of reading"). For reading calls (what counts as a call, whether parentheses are needed), the reader of `call_view.rs` in section 17 is called
one expression at a time. A field added to version 2 (old editors skip it). Only when `--stdin --path <file>` is used, that
file's defk / deff parts are emitted (empty in a whole run).

```jsonc
"bodies": [{
  "kind": "defk",                   // defk | deff (the same definitions as signatures)
  "name": "judged", "path": "/abs/core/conversation_input.hy",
  "range": {…}, "full_range": {…},  // the name's range, and the whole definition form (same as signatures)
  "lines": [{
    "line": 49,                     // the source line (0-based — the pane adds 1 to show it). If one line has two statements, the same number continues
    "depth": 0,                     // indentation level (outermost of the body = 0; the contents of when / if / match / for are one deeper)
    "pad": 0,                       // whitespace added after the level (the pane puts the text after "  " × depth + " " × pad)
    "segments": [                   // the text of the line = the text of the segments joined
      {"text": "val", "role": "keyword", "range": {…}, "effect": null, "definition": null},
      {"text": " ", "role": "text", "range": null, "effect": null, "definition": null},
      {"text": "str | None", "role": "type", "range": {…}, …},        // range = the T of (<- x T e) (null if there is no annotation)
      {"text": "target", "role": "name", "range": {…}, …},
      {"text": "⇐", "role": "bind", "range": null, …},
      {"text": "target-of", "role": "call", "range": {…}, "definition": {"path", "range"} | null},
      {"text": "SettleIntake", "role": "effect", "range": {…}, "effect": "SettleIntake", …}
    ],
    "binding": 3 | null,            // the binding this line draws (the index into bindings of the same output)
    "warning": {"kind": "setv", "message": "Use (val x …) instead of setv, …"} | null
  }]
}]
```

- **Roles of text** (a closed set): `keyword` (`val`, `var`, `lazy`, `session`, `setv`, `return`, `resume`, `when`, `while`, `if`, `else`, `match`, `cond`, `for`, `in`, `try`, `except`, `as`, `finally`, `raise`, `from`, `continue`, `break`), `type` (the binding's type),
  `unknown-type` (the mark `?` for an unknown type), `name` (the bound name), `bind` (`⇐`), `assign` (`=` and `:=`), `effect` (the head of a call that builds an effect value —
  the pane chooses the picture by the `effect` name), `call` (the head of any other call), `text` (arguments, operators, literals, separators), `lisp` (a form not in the table), and `comment` (a line made from a whole-line comment `;; …` in the middle of the body turned into `# …`).
- **range**: text that came from the source has the source's range, and text that is not in the source, such as separators, `⇐`, and `?`, is null. The spelling of `keyword` and `type` can
  change (`<-` -> `val`, `(| A B)` -> `A | B`). For the other roles, the text and the text of the range are character-for-character the same.
- **Type**: the same as the type of `bindings` in section 16 (the `type` of the binding at the `binding` index is spelled `A | B`, `H[a, b]`). `?` if unknown (not filled in with a different
  type).

| Original form | Body text |
|---|---|
| `(<- x T e)` / `(<- x e)` | `val T x ⇐ e` |
| `(val x e)` / `(var x e)` | `val T x = e` / `var T x = e` |
| `(val x ! e)` / `(val x (! e))` | `val T x ⇐ e` (a fired value — the same meaning as `(<- x e)`) |
| `(lazy val x e)` / `(lazy var x e)` / `(session val x e)` / `(session var x e)` | `lazy val T x = e` … |
| `(:= x v)` | `x := v` |
| `(setv x e)` in the body | `setv x = e` + `warning` (toward val / var) |
| `(E a)` / `(! (E a))` / `(<- (E a))` (E is an effect) | `E(a)` (E has the `effect` role; `!` is not shown) |
| `(! (f a))` / `(<- (f a))` (not an effect) | `!f(a)` |
| `(f a b)`, `(.m o a)`, `(get d k)`, `(. o a)`, `(f a :k v)`, operations | the same as the replacements in section 17 (`f(a, b)`, `o.m(a)`, `d[k]`, `o.a`, `f(a, k=v)`, `a + b`) |
| `(return v)` / `(resume v)` | `return v` / `resume v` |
| `(<- x T e :absent F)` | `:absent F` after `val T x ⇐ e`, kept as `lisp` |
| `(when c …)` | `when c` + the contents one level deeper |
| `(if c a b)` / `(if c a)` | `if c` + a, `else` + b (else is aligned to the if's column, and the line number is b's line) |
| `(match v P x P :if g y …)` | `match v` + per arm `P → x` / `P if g → y` (before `→`, align to the maximum arm pattern width plus one space). Patterns: `(C)` -> `C`, `(C a :k p)` -> `C(a, k=p)`, `(\| p q)` -> `p \| q`, `[p q]` -> `[p, q]`, names and literals as is |
| `(for [x xs] …)` / `(for [[a b] xs] …)` | `for x in xs` / `for a, b in xs` + the contents one level deeper |
| `(lfor x xs :if c e)` / `gfor` / `sfor` | `[e for x in xs if c]` / `(e for …)` / `{e for …}` (stacked clauses are `for … for …`) |
| `(do a b …)` | the word `do` is not shown, and the contents are lined up (in a statement position, at the current level; after an arm's `→`, the first continues on that line and the rest are aligned to that column) |
| `(cond c x … True z)` | `cond` + per arm `c → x` (conditions aligned in width; the last `True` is `else →`). If any condition does not fit on one line, the whole cond is lisp |
| `(while c …)` | `while c` + the contents one level deeper |
| `(try … (except [e T] …) (except [[A B]] …) (except [] …) (else …) (finally …))` | `try` / `except T as e` / `except (A, B)` / `except` / `else` / `finally` (clause words at the try's column, contents one level deeper) |
| `(continue)` / `(break)` / `(raise)` / `(raise e)` / `(raise e :from c)` | `continue` / `break` / `raise` / `raise e` / `raise e from c` |
| `(setv (get x k) v)` / `(setv (. o a) v)` / `(setv o.a v)` | `x[k] = v` / `o.a = v` (rewriting contents — not a binding, so no setv warning is attached; outside ADR-DOE-HY-006) |
| a whole-line `;; …` between statements in a block | a `# …` line (role `comment`, with a source range) |
| `(del x …)` / `(assert c m)` | `del x, …` / `assert c, m` |
| `(if c a b)` inside an expression | `a if c else b` (wrapped in parentheses as an operand or a method target). In binding / return values and after an arm's `→`, it opens into a vertical `if` / `else` if over 100 characters |
| `(fn [x] e)` / `(fn [a b] e)` / `(fn [] e)` inside an expression | `x ⇒ e` / `(a, b) ⇒ e` / `() ⇒ e` (arguments are names only and the body is one expression. `#*` and default values stay as lisp) |
| `(cut xs a b)` / `(cut xs b)` / `(cut xs)` / `(cut xs a b s)` | `xs[a:b]` (a `None` end is empty) / `xs[:b]` / `xs[:]` / `xs[a:b:s]` |
| comprehensions inside an expression (`lfor` / `gfor` / `sfor` / `dfor`) | `[e for …]` / `(e for …)` / `{e for …}` / `{k: v for …}` (`:setv` / `:do` clauses stay as lisp) |
| literals `#(a b)` / `#(a)` / `[a b]` / `#{a b}` / `{k v}` | `(a, b)` / `(a,)` / `[a, b]` / `{a, b}` / `{k: v}` (a dict whose keys are keywords keeps its original text) |
| `cond` / `match` / `do` / `try` in a binding or return value position | opened vertically (the first line continues on the binding's line, and the contents align to the column of the block's words) |
| Forms not in the table (unknown macros, `unless`, `:setv` / `:do` in comprehensions, a `for` whose name is not a pair, a `match` with a missing arm, patterns not in the table, a destructuring setv to a tuple, an expression with a comment in the middle of a line) | kept as the original lisp (the `lisp` role). Not drawn by guessing |

- **Flattening**: forms inside expressions (ternary, lambda, cut, comprehensions, literals), a cond's conditions, a match's patterns and `:if`, and the head of an except are drawn on one line,
  even if multi-line in the source, with newlines and indentation made a single space. If there is a comment in the middle of a line (a `;` outside a string), it is not flattened and stays as lisp (because the comment would swallow the characters after it).

- **Islands of lisp**: parentheses inside an expression that were not replaced (unknown heads) are emitted, only those parentheses, as the original lisp with the `lisp` role (the calls inside are not replaced either).
- **Multiple lines**: where an expression or lisp continues onto the next line, a new line is made (`line` = that source line, `depth` the same), and the difference of the source's indentation from the head of the statement is added to
  `pad`.
- **Blocks that start inside an arm**: the contents of a when / if / match / for that starts after `→`, as in `A → match v`, are held at the arm's level + 1 and with `pad` aligned to the column of the block's words
  (characters are counted as in the pane — one level = two spaces, one character = one column).
- **Comprehensions**: if the parts (body, name, source, condition) each fit on one line, they are put on one line even if multi-line in the source (the line number is the line of the comprehension's head. The range of the characters after it
  points to the later source line). If they do not fit, it stays as an island of lisp.
- **A string at the head of a body**: when statements follow, it is not shown as an explanation (a body of a single string alone is shown, since that string is the answer).
- The reading of bindings in section 16 was fixed in the same change: the pair of elements `(val x ! e)` (Hy's reader reads `!(e)` also as the two elements `!` and `(e)` — ADR-DOE-HY-006 §3)
  is folded into a fired value, and `(lazy val x e)` and `(session var x e)` are read as bindings, with the preceding word carried in the `bindings` field `modifier` (`lazy` | `session` | null)
  (a field added to version 2).

### Seeded pseudo-randomness and external I/O (DOEFF133)

The raw-I/O index does not count `random.Random(seed)` with an explicit seed (Hy's `(random.Random seed)` and `:x seed`) as external I/O. Not only numeric constants but also variable seeds are treated as explicit arguments. Methods of the generated instance, methods after assigning the instance to an alias, type annotations of `random.Random`, and imports inside functions are not by themselves external I/O either. A method with the same spelling as the module import (`(.random rng)`, `(. rng (random))`) is distinguished from a reference to the module itself.

A `random.Random()` with the seed omitted, an explicit `None`, a construction whose seed cannot be confirmed because it is only a splat argument, module-global `random.random()` and the like, `SystemRandom`, `uuid.uuid4`, and `secrets` remain external I/O. When the expression that builds the seed itself reads a clock or the like, that expression's I/O remains. Whether a variable's runtime value is `None` is not inferred.

The index holds the shape of call arguments and type-only references as internal facts, and also carries them into the per-file cache. The shape of the public JSON is not changed. Because the extraction source of external I/O was fixed, no exception table only for DOEFF133 is added.

## 22. Stale branches on an effect whose declaration was switched — DOEFF175

ADR-DOE-CORE-EFFECTS-003 R14 (4) (the tool for stage 4 — the gate of the procedure that switches the stage-3 declaration and the stage-5 callers in the same version per effect). The test for the law
declarations-switch-one-effect-at-a-time goes through this rule. The types that defeffect declares with `:absent` / `:failure` are not bound as an answer value by `<-` and `!`
(absence escapes to the caller's scope as Absent and failure as Raise — R5), so a branch (a match arm, isinstance) that splits the declared types as values on a variable that binds that effect's answer
is never taken and silently falls to the next branch (often the success branch). This rule picks up the old branches left in callers after a version that switched one effect, and does not land
that version until the hits reach 0.

- **Reading declarations**: built the same way as DOEFF122's reading of failure types (`smells::failure_types_in`), with per-effect declarations added. From `(defeffect name "doc"? {… :absent [A …] :failure [B …] :value [C …]})` in repo Hy files that contain the spelling `:failure` or
  `:absent`, it collects, per effect name (the name including the module), the set of absent types and the set of failure types. Types are resolved to modules by the file's imports and where definitions are.
  The `:value` list is not read (values are bound as answers). A defeffect that writes neither `:absent` nor `:failure` has
  no declaration — it hits nothing. A whole run uses the per-file cache (`failure-types`), and a one-file run (the editor) collects only from the declaring files that spell the effect name being looked up
  (`SpelledFailures` — the same build as DOEFF122, with the same answers).
- **Bound names**: reads through the definition from the front and remembers names.
  - `(<- x (E …))`, `(<- x T (E …))`, `(<- x (E …) :absent F)`: if the head of the right side is the symbol `E`, x is E's answer. Effects are classes, so only a name whose last head segment begins with an uppercase letter counts as an effect (`ReadRow`, `effects.ReadRow`; calls of a defk such as `read-typed` and calls of methods are not counted — a one-file run reads the repo's declaration files every time it looks up a declaration, so this reduces the number of lookups. A defeffect that begins with a lowercase letter cannot be read). A call whose head is not an effect (a defk, `maybe`, `on-raise` …) makes x remembered as another value.
  - `(val\|var\|setv\|setx\|:= x (! (E …)))`: x is E's answer. Other right sides are other values.
  - A name rebound to another value, arguments of `fn` and `defk`, names of `for` / `with` / `let` / `except`, names bound by a match pattern and `:as` names, and names bound by an assembled binding stop being the effect's answer
    (the memory is dropped. Names of `for` and `with` are dropped in later statements too). Inside comprehensions (`lfor`, `gfor`, `sfor`, `dfor`) nothing is remembered. It leans toward missing rather than hitting wrongly.
  - Branches are read only in the order of binding (flow is not followed) — a binding inside `when` / `if` also affects later branches of the same definition.
- **Branches it hits**: when the subject is a remembered name or a directly written `(! (E …))`,
  - the arms of `(match subject pattern arm …)` — the type at the head of the pattern (`(T …)`) and the heads of alternatives of `(| (A) (B))`. The keys `:as` and `:if` are skipped. Types inside a pattern's field (`(Row :value (Missing))`) are not the answer's type, so they are not followed.
  - `(isinstance subject type-expression)` — each symbol of a type expression: one symbol, `#(A B)`, `[A B]`, `(| A B)`, `(tuple [A B])`. An isinstance inside `cond` / `if` / `when` / `and` / `not` is also picked up by walking the tree.
  It hits only when the type is in E's `:absent` / `:failure` (success types, `:value` types, types declared only by another effect, and same-named types from another module do not hit).
- **What it does not read**: `defhandler` (a clause is the side that receives the effect — not read per definition), top-level forms other than `defhandler` (`defrecord`, `defeffect`, `val` …), insides of quotes, and comments and `#_`.
  An answer that went through a defk, as in `(<- x (read-typed …))`, is not followed — it is fixed as a branch on that defk's return type, in the version where the effect of the defk it goes through is switched.
- **Population**: Hy files under `paths` of `definitions` and test locations in `test_paths` (the same as DOEFF126 — test callers also go stale on a switch). It is not judged in a repo with no `definitions` section
  (`wired` in `rules` is false). Declarations (defeffect) are collected from all of the repo's Hy files, so the declaring file may be outside the population. doeff's own population is only the 3 body dirs of `paths`, and
  ADRs (`docs/adr/*.hy`) and doeff-core-effects' probe tests (`tests/test_outcome_binds.hy` and others — they define effects with declarations as samples) are outside the population.
- **Key and position**: `<path>::DOEFF175::<definition>::<bound name>::<type spelling>` (the form that splits directly with `(! (E …))` uses E's spelling instead of the bound name). One per same name and type in the same definition. Position = the match arm's pattern or the isinstance's type symbol.
- **Severity**: critical, **not lowered by the registry** (`lowered_by_registry` is false), not `exemptible_by_layer`. So that the gate does not open, it is put in neither the known list nor `:exempt`.
- **The message**: "E declared T as absent (failure). <- does not bind x to T, so this branch (match arm / isinstance) is never taken — use maybe / on-raise / :absent". The fix (R14 (3)):
  delete the branch and leave only the success path. Where you want to do something on absence, use `(<- x (E …) :absent <failure>)` or `absent-as`; where you want to do something on failure, use `on-raise`; and where absence or failure is carried around as a value, wrap with `(maybe (E …))` / `(result (E …))` and receive Nothing / Err.
- **Tests**: unit `src/project/stale_outcome_branches.rs` (shapes, shapes that do not hit, rebound names), and the binary-running `tests/stale_outcome_branch.rs` with the fixture `tests/fixtures/stale_outcome_branch/`
  (the 17 lines marked "; fires" in `branches.hy` match the set of hit lines, 12 definitions that do not hit, removing the declaration gives 0, adding to an effect with no declaration makes it hit, a one-file run gives the same keys as a whole run, it is not lowered by the registry,
  the population, and 0 hits in doeff's own production code).
