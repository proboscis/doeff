# doeff-linter

A high-performance linter for enforcing code quality and immutability patterns in Python.

## Features

- **27 specialized rules** for code quality, immutability, and workflow replay safety
- **Layer rules DOEFF101-108** (both Hy and Python): judge the conventions of the pure layers and the layers that touch the outside world, with per-repo settings (see "Layer rules" below)
- **Editor output** `--output-format editor-json` (shown by doeff-runner. This linter is the source of truth for judgments)
- **Configurable via pyproject.toml**
- **noqa comments** for per-line rule suppression
- **Fast** - written in Rust for maximum performance
- **JSON Lines logging** for violation tracking and statistics

## Installation

### From Source (Rust)

```bash
cd packages/doeff-linter
cargo install --path .
```

## Quick Start

```bash
# Lint all Python files in the current directory
doeff-linter

# Lint specific files or directories
doeff-linter src/ tests/test_specific.py

# Show detailed output
doeff-linter --verbose

# Output as JSON
doeff-linter --output-format json
```

## Configuration

Configure the linter in your `pyproject.toml`:

```toml
[tool.doeff-linter]
# Enable all rules except specific ones
enable = ["ALL"]
disable = ["DOEFF004"]

# Or enable only specific rules
# enable = ["DOEFF001", "DOEFF002", "DOEFF007"]

# Exclude paths (applies to directory scanning by default)
exclude = [".venv", "build", "tests/fixtures"]

# Log violations to a file for later analysis (JSON Lines format)
log_file = ".doeff-lint.jsonl"

# Rule-specific configuration
[tool.doeff-linter.rules.DOEFF003]
max_mutable_attributes = 3

[tool.doeff-linter.rules.DOEFF009]
skip_private_functions = true
skip_test_functions = true
```

## Available Rules

| Rule ID | Name | Description |
|---------|------|-------------|
| DOEFF001 | Builtin Shadowing | Functions should not shadow Python builtin names |
| DOEFF002 | Mutable Attribute Naming | Mutable attributes must use `mut_` or `_mut` prefix (class's own attributes only — what a base class outside the repo owns, e.g. Textual's `App.sub_title`, is not reported) |
| DOEFF003 | Max Mutable Attributes | Limit the number of mutable attributes in a class |
| DOEFF004 | No os.environ Access | Forbid direct access to environment variables |
| DOEFF005 | No Setter Methods | Classes should not have setter methods |
| DOEFF006 | No Tuple Returns | Functions should not return tuples (use dataclasses) |
| DOEFF007 | No Mutable Argument Mutations | Functions should not mutate dict/list/set arguments |
| DOEFF008 | No Dataclass Attribute Mutation | Dataclass instances should be immutable |
| DOEFF009 | Missing Return Type Annotation | Functions should have return type annotations |
| DOEFF010 | Test File Placement | Test files inside an importable package (`__init__.py`) must be under a `tests/` directory |
| DOEFF011 | No Flag/Mode Arguments | Use callbacks or protocol objects instead of flag/mode arguments |
| DOEFF012 | No Append Loop Pattern | Use list comprehension instead of empty list + for loop append |
| DOEFF013 | Prefer Maybe Monad | Use `Maybe[T]` instead of `Optional[T]` or `T \| None` |
| DOEFF014 | No Try-Except Blocks | Use doeff's error handling effects instead of try-except |
| DOEFF032 | Rule Population Declaration Breach | A module in a layer excluded from the rule population (`:exempt` in architecture.hy) imports a business module, or the package declaration cannot be read so the population cannot be decided |
| DOEFF101 | Layer Import Direction | A layer's modules import only modules of the layers that the settings allow |
| DOEFF102 | Layer Forbidden Module | Do not import directly the modules forbidden per layer (such as I/O modules) |
| DOEFF103 | Types-Only Layer | Do not define functions or handlers in a types-only layer |
| DOEFF104 | Module Declares Tags | Definitions in a layer's modules declare their context and role with tags |
| DOEFF105 | Role Matches Layer | A tag's role is one allowed in that layer |
| DOEFF106 | Raw Side Effect Placement | Definitions that touch raw side effects directly are placed only in allowed layers (Hy) |
| DOEFF107 | Raw Side Effect Via Call | Notice (info, Hy) of a definition that reaches a raw side effect through the definitions it calls |
| DOEFF108 | Environment Name In Business Code | Do not put environment words in the names of business files, handlers, or assembly functions |
| DOEFF109 | Service Boundary | The judgment and translation layers of service A do not read the same layers of service B (B's intent and the shared location can be read) |
| DOEFF110 | No defn | Write Hy definitions with defk, not defn / defn/a |
| DOEFF111 | deff Needs A Reason | A deff needs a `; defk にできない: <reason>` comment (the marker text is configurable via `definitions.deff_reason_marker`) |
| DOEFF112 | Definition Tags Required | defk, deff, defp, defhandler and defeffect declare the required keys in :tags |
| DOEFF113 | Context Matches Service | A tag's :context disagrees with the service of its location (info) |
| DOEFF119 | Class Touches The World Or Holds State | A class that touches the outside world is an error; a class that rewrites self is a warning; a class with only fields gets an info recommending defrecord |
| DOEFF118 | Tests Are deftest | Tests in the test locations are written with deftest (do not place bindings of defn, deff, defk or fn whose name is test) |
| DOEFF121 | Shape Check In Judgment | A definition in a judgment layer checks a string-keyed `(.get x "field")` with isinstance on that field (shape checks of JSON go to protocol's defwire) |
| DOEFF122 | Hand-Written Failure Rethrow | A match arm receives a declared failure type and only returns the received value or a re-wrapped one (use `(<- (Raise …))` and on-raise) |
| DOEFF123 | Bind Then Return | `(return x)` right after `(<- x T (f …))`, with x not used elsewhere |
| DOEFF124 | Fields Joined Into Text | Two or more fields of the same value are joined with strings using `+` or an f-string |
| DOEFF125 | Rebuilt Accumulator | `(:= xs (+ xs #(…)))` inside for / while (use a comprehension) |
| DOEFF128 | Unreadable Hy File | The reader cannot read a Hy file that rules read to the end (an error regardless of which rules are enabled — a missing violation is not silenced) |
| DOEFF126 | defk Called Bare | A defk is called bare where its answer is used as a value (comparison, arithmetic, built-in function, method, condition, source of iteration, record field, an argument of a defk call whose receiving parameter's `:pre` type cannot accept a Program) (error) |
| DOEFF127 | Effects Disagree With Inference | A defk with `:effects` raises an effect not in the declaration (position = the call it went through) or does not raise a declared effect (position = the name inside `:effects`) (warning) |
| DOEFF175 | Stale Branch On A Declared Absent Or Failure | A stale branch that separates, as values, the types declared by a defeffect in `:absent` / `:failure` — on a variable bound to that effect's answer (`(<- x (E …))`, `(val x (! (E …)))`) — by a match arm or isinstance (including a tuple of types and a cond branch) (critical; not lowered by the registry — `<-` does not bind that type, so the branch is never taken. Use maybe / on-raise / `:absent`) |
| DOEFF130 | Translation Emits Intent | A handler of the translation layer (protocol by default) — a defhandler, or a function receiving `[effect k]` — emits a business intent (a type of the intent layer by default). Follows imported defks (the limit is `translation_effects.max_depth`) (error; spec section 21) |
| DOEFF120 | JsonValue Outside Wire Modules | JsonValue and JsonObject may be used only by the generic resolvers and by the foundation send/receive modules listed in `:wire-modules` of architecture.hy |

## Layer rules (Hy and Python)

DOEFF101-108 are rules judged from the repo's module list and the settings. They separate the "pure layer that holds business judgment" from the "layer that touches the outside world",
and check the direction of imports, tags, where raw side effects are placed, and environment words. Layer names, roles, environment words and the location of the registry are not
written into Rust; all of them are received from per-repo settings. A rule with no settings section emits nothing.

Hy files are read with the hy-index analysis of doeff-indexer (called as a function) and the reader. Python files are read with rustpython.
For the detailed conventions (key spellings, severities, how to read), see [docs/SPECIFICATION.md](docs/SPECIFICATION.md); for per-rule examples, see
[docs/rules/DOEFF101-108.md](docs/rules/DOEFF101-108.md).

```toml
[tool.doeff-linter.layers]
order = ["core", "intent", "protocol", "foundation", "entry"]   # in order of distance from the outside world
paths = { core = "app/core", intent = "app/intent", protocol = "app/protocol", foundation = "app/foundation", entry = "app/entry" }
exclude = ["tests", "__pycache__", "conftest.py"]              # names of dirs and files placed outside the layer rules
types_only = ["intent"]                                        # layers that define no functions or handlers (DOEFF103)

[tool.doeff-linter.layers.allow_imports]                       # layers each layer may import (DOEFF101)
core = ["core", "intent"]
intent = ["intent"]
protocol = ["protocol", "intent"]
foundation = ["foundation"]
entry = ["core", "intent", "protocol", "foundation", "entry"]

[tool.doeff-linter.layers.forbid_modules]                      # modules each layer must not import directly (DOEFF102)
core = ["subprocess", "socket", "http", "urllib", "httpx"]

[tool.doeff-linter.roles]                                      # the closed list of roles, and the roles allowed per layer (DOEFF105)
names = ["type", "judgment", "program", "intent", "protocol", "foundation", "entry"]
[tool.doeff-linter.roles.by_layer]
core = ["type", "judgment", "program"]
intent = ["intent", "type"]

[tool.doeff-linter.roles.describe]                             # descriptions of roles (inserted into the violation reason text)
translation = "Translation handler — currently the role of layer protocol"

[tool.doeff-linter.layers.describe.core]                       # descriptions of layers (used in the output's layers and the "why" text of violations)
summary = "Business judgment and Programs"
knows = "Business judgment (when, to whom, what)"
does_not_know = "Who the counterparty is and how to communicate"
question = "If the way of communicating changes, does this code stay the same?"

[tool.doeff-linter.raw_side_effects]                           # layers that may touch raw side effects directly (DOEFF106, 107)
allowed_layers = ["foundation", "entry"]

[tool.doeff-linter.environment_names]                          # environment words not to put in business names (DOEFF108)
words = ["production", "emulated", "fake", "local", "machine", "wire"]
paths = ["app/orders", "app/intent/daily_report_*"]            # locations of business files (a trailing * is a prefix match)
exclude = ["app/entry"]
exclude_parts = ["tests"]
assembly_files = ["handler_sets.hy"]                           # assembly files where every top-level definition name is checked

[[tool.doeff-linter.laws]]                                     # mapping between rules and ADR laws (the output's law / adr and the key spelling)
name = "core-imports-only-intent"
adr = "ADR-APP-CHOOSE-ENVIRONMENT-BY-HANDLER-SET"
rules = ["DOEFF101", "DOEFF102"]
layers = ["core"]
statement = "module ∈ modules of layer core ⇒ …"

[tool.doeff-linter.registry]                                   # registry of known breaches, and the rules being reconciled
dirs = ["scripts/layer_imports/BREACHES"]                      # one key per file (line 1 of *.txt is the key)
files = []                                                     # one key per line
reconciling = ["DOEFF104"]                                     # rules being reconciled are lowered to info
```

- Key: `<path from the repo root>::<law name, or the rule ID if none>[::<detail>]` (detail = import target, `definitions`, role, etc.).
- Severity: a new breach is an error, a breach listed in the registry is a warning (`registered: true`), and a rule being reconciled is info. The weak evidence of DOEFF106
  (found only by a method name) is a warning, and DOEFF107 is always info.
- Exit codes: 0 = no errors, 1 = errors, 2 = bad arguments or settings (a mismatch in setting names is not silently dropped), 75 = not run
  (the wait for the machine's check slot exceeded the limit, so it ended without linting anything — it can be retried, and is neither red nor green). 75 is returned only by the heavy modes (the default run,
  the whole repo with editor-json, `--hook`, `--commit-hook`) — at startup it hands itself to the command wrapper `run` of the check-slot tool under HOME (the location is
  `CANON_UNDER_HOME` in `src/check_slot.rs`) to take a slot, and the wrapper's limit exit code becomes the
  linter's exit code as is (`src/check_slot.rs`). On machines without the tool, it runs without taking a slot. Callers that judge by exit code should read 75 as not run.
- Explanation: a layer-rule violation has "what this is (subject)", "why it is a violation (reason)" and "the law statement", which appear in editor-json's `explanation`, the text
  output, and the agent hook's text. Each module's `layer_reason` is what decided the layer (the location of its path, or a mismatch with its tags).
- `noqa` comments only affect the per-statement rules of Python. For a known breach of a layer rule, put a key in the registry.

### Location patterns and services (DOEFF109, 113)

A layer's location can be written as a list of spellings, and the segment `*` matches a service name (during migration, the layer-first form and the service-first form are listed side by side).

```toml
[tool.doeff-linter.layers]
paths = { core = ["app/*/core", "app/core"], intent = ["app/*/intent", "app/intent"], foundation = "app/foundation" }

[tool.doeff-linter.services]
shared = ["shared"]                 # locations readable from every service (app/shared/…)
guarded_layers = ["core", "protocol"]
open_layers = ["intent"]            # layers that other services may read
exceptions = [{ from = "orders", to = "billing" }]
check_context = true                # check a tag's :context against the service (DOEFF113, info)
```

A file matching two locations takes the one with more segments (the more specific). It appears in the module's `service` and in the explanation text ("layer core of service billing").

### How definitions are written (DOEFF110-112)

```toml
[tool.doeff-linter.definitions]     # population (if paths is empty, all Hy of the repo)
paths = ["app", "services"]
exclude = ["packages/doeff-hy/src/doeff_hy/macros"]   # e.g. the owner of the macros
deff_reason_marker = "defk にできない:"

[tool.doeff-linter.tags]
required = ["context", "role"]      # keys required by DOEFF112
module_default = true               # whether the module-head MODULE-TAGS can supply them
```

The severity of a breach listed in the registry can be set per rule (default warning; a new breach is always an error). A registry carried together with the settings is
written in `config_files` (relative to the dir of the settings file).

```toml
[tool.doeff-linter.rules.DOEFF110]
registered_severity = "info"

[tool.doeff-linter.registry]
config_files = ["definition-breaches.txt"]
```

A defn inside `eval-and-compile` / `eval-when-compile` (a function used at macro expansion time) is outside DOEFF110. Existing breaches become warnings when listed in the registry
(one key per line in `registry.files`; key = `<path>::<rule>::<definition name>`), and only new breaches are errors.

### architecture.hy (the single declaration of services and layers — DOEFF114-117)

If you write `defarchitecture` (root, layers, shared, foundation) and `defservice` (description, `:depends-on`, `:layers`,
`:public-contract`) in the topmost `architecture.hy` of the repo, doeff-linter reads them without executing. When it exists, write layers, roles and services only there, leaving
the rule on/off switches, severities and the registry in the TOML.
For the shape, and the correspondence with Tach, import-linter, Nx and Deptrac, see section 9 of [docs/SPECIFICATION.md](docs/SPECIFICATION.md).

### Semantic rules (DOEFF201, 202 — Jev)

Jev (TypeSafe) is asked only with `--semantic` (specified files, or files changed in git) and `--semantic-all`, and the answers are kept in `.doeff-linter/semantic-cache/` at the repo root.
Adding `--semantic-changed` asks only about definitions whose contents changed (definitions with no answer in the cache).
Definitions that are unfinished and unreadable are not asked. Normal runs do not call Jev: the editor's single file (`--stdin`) only reads the cache, and whole runs and hooks
read the cache; if a proxy that remembers Jev calls (`[tool.doeff-linter.semantic] proxy_url`) is configured, definitions not in the cache are asked of the proxy
"only when it remembers". The severity is only warning or info. The destination and key are decided as in doeff-jev (`JEV_*` -> `~/.config/jev/client.json` ->
TypeSafe direct, `TYPESAFE_API_KEY`), and if the repo has `proxy_url`, the proxy is used (only the proxy's token is sent). For details see section 10 of
[docs/SPECIFICATION.md](docs/SPECIFICATION.md).

### Reasons for plain functions, and how to write tests (DOEFF110, 111, 118, 203)

Write `; defk にできない: <free-form reason>` on a deff. What is deterministically a violation is: no comment, an empty reason, or only "same as above" (DOEFF111).
Whether to accept the reason is decided by Jev (DOEFF203), choosing from architecture.hy's `:plain-callable-reasons` (acceptable reasons) and
`:rejected-plain-callable-reasons` (types of unacceptable reasons and how to fix them). In test locations (`definitions.test_paths`), tests must be deftest only (DOEFF118).
For details see section 11 of [docs/SPECIFICATION.md](docs/SPECIFICATION.md).

### Contents of a class (DOEFF119, DOEFF204)

defclass is classified by its contents, not by its name. A class whose methods or field initial values touch raw side effects is an error (move it to a foundation handler); a class whose methods rewrite its self fields is a
warning (put the state in a handler's `(session var …)`); a class with only fields gets an info recommending defrecord. Classes that inherit exceptions, Enum, Protocol, or
bases from outside libraries are not reported. Only classes that match none of these and have methods with processing are asked of Jev (DOEFF204)
as value / external-world / stateful / other. For details see section 12 of [docs/SPECIFICATION.md](docs/SPECIFICATION.md).

### Where JsonValue is used (DOEFF120)

`JsonValue`, `JSONValue`, `JsonObject` and `JSONObject` (types that merely wrap a plain dict with a name) may be used only by the generic resolvers (`doeff_hy._wire`,
`doeff_records._wire`) and by send/receive modules that are listed in architecture.hy's `:wire-modules` (a pattern of module spellings) and are also in the foundation layer.
Other modules see only typed values whose shape a resolver has checked. One finding per module (key `<path>::DOEFF120`). A module listed in `:wire-modules` but
outside foundation is not allowed, and the reason is written in the explanation. How the allowed places are decided is in one spot, `json_value_allowance`, and can be swapped.
For details see [docs/rules/DOEFF120.md](docs/rules/DOEFF120.md) and section 13 of [docs/SPECIFICATION.md](docs/SPECIFICATION.md).

### Smell rules (DOEFF121-125, DOEFF205)

These pick up forms written by hand that could be written with types and effects. The default severity is warning
(it can be lowered with `[tool.doeff-linter.rules.<ID>] severity = "info"`; never made an error). DOEFF121 applies only to files of the judgment layers listed in `[tool.doeff-linter.smells]
shape_check_layers`, and DOEFF122-125 apply to the whole `definitions` population. The "failure type" of DOEFF122 is not hard-coded by
name; it is taken from `{:failure True}` in the head dict of a defrecord and from the `:failure` / `:absent` declarations of a defeffect (resolved to the
module through imports). DOEFF205 asks Jev whether a definition whose role is judgment / program mixes shape checks and judgment
(`[tool.doeff-linter.semantic] mixed_concerns = { layer = "core" }`). For details see section 14 of [docs/SPECIFICATION.md](docs/SPECIFICATION.md).

## Rewrite commands (fix)

```bash
# Turn defn into defk, and rewrite callers inside the repo to <- / ! (DOEFF110, DOEFF126)
doeff-linter fix defn-to-defk --path scripts/module_tags.hy
doeff-linter fix defn-to-defk --path app/intent --dry-run                  # only plan, do not write
doeff-linter fix defn-to-defk --path scripts --report /tmp/fix.json        # also write a JSON report
doeff-linter fix defn-to-defk --path scripts --strict                      # convert only functions whose callers can all be fixed
```

- Definition: `(defn #^ R name [#^ T x …] …)` -> `(defk name [x …] {:pre [(: x T) …] :post [(: % R)]} …)`. The contract is built from the `#^` annotations (the defk
  macro requires the types of all parameters and the return type). For a type with type arguments such as `(get list int)`, the original type is written in the contract and the parameter's annotation is kept.
  A parameter whose default is None becomes `(| T None)`, and `float` becomes `(| float int)`. An existing `{:pre … :post … :tags …}` is kept, and only missing keys are added.
- Callers: a bind to a single name `(setv x (f a))` / `(val x (f a))` becomes `(<- x (f a))`; anything else (including the tail) becomes `(! (f a))`. If the file's require lacks `defk` or `<-`,
  they are added (`!` is a symbol that the defk macro expands, not a macro to require).
- Places not rewritten (reported as needing judgment): inside comprehensions, inside plain functions (defn, deff, fn), the module top level, inside macros, a `->` step, places that pass the function itself
  as a value, references from Python and toml, and import aliases. A defn that is already passed to `<-` / `!` / `run` returns a Program (it is an assembly), so it is not converted.
- Forms not applied: dunders, `:async`, a comment of `defk にできない:` (the settings' `definitions.deff_reason_marker`) or "keep as defn", inside `eval-and-compile`,
  class methods, and excluded paths (the default is `DEFAULT_EXCLUDES` in `src/project/defn_to_defk.rs`; add more with `--exclude`).
- After the conversion, DOEFF126 is run with the same reader, and the remaining bare calls are reported. Exit code 0 = none remaining, 1 = some remaining, 2 = error.

## Editor output (editor-json)

```bash
doeff-linter --output-format editor-json                              # whole repo (cwd = repo root)
doeff-linter --output-format editor-json --stdin --path <file>        # one file with its pre-save contents
doeff-linter --output-format editor-json --config lint.toml --root .  # specify the settings file and repo root
```

The output is one JSON of the contract lint-contract version 1 (`version`, `root`, `violations`, `modules`, `rules`, `errors`). Each violation has
`rule` (the DOEFF ID), `law`, `adr`, `severity`, `path`, `range` (0-based line, UTF-16 column), `message`, `hint`, `key`, and
`registered`. Line-only rules (DOEFF001-031) also output the range of that line. For the details of the shape, see
[docs/SPECIFICATION.md](docs/SPECIFICATION.md). The shape of the existing `--output-format json` is unchanged (layer-rule violations are listed in the same shape).

## Inline Suppression

Use `noqa` comments to suppress rules on specific lines:

```python
# Suppress specific rule
def dict():  # noqa: DOEFF001
    return {}

# Suppress all rules on a line
data["key"] = value  # noqa
```

### File-Level Suppression

Suppress rules for an entire file by placing `noqa: file` at the top of the file (before any code):

```python
# noqa: file=DOEFF001
"""This module intentionally uses builtin names as function names."""

def dict():  # No violation reported
    return {}

def list():  # No violation reported
    return []
```

File-level noqa variants:

```python
# noqa: file              # Suppress ALL rules for entire file
# noqa: file=DOEFF001     # Suppress specific rule for entire file
# noqa: file=DOEFF001,DOEFF002  # Suppress multiple rules for entire file
```

**Note:** File-level noqa must appear before any code. Only comments, blank lines, and module docstrings are allowed to precede it.

## CLI Options

```
Usage: doeff-linter [OPTIONS] [PATHS]...

Arguments:
  [PATHS]...  Files or directories to lint

Options:
      --enable <RULES>       Enable specific rules (comma-separated)
      --disable <RULES>      Disable specific rules (comma-separated)
      --exclude <PATTERNS>   Exclude paths matching patterns
      --force-exclude        Apply exclusion rules to explicit file paths
      --output-format <FMT>  Output format: text, json, editor-json [default: text]
      --config <FILE>        Settings file (in the shape of pyproject.toml, or a TOML with only the contents of [tool.doeff-linter])
      --root <DIR>           Repo root (the base for layer locations, the registry, and key paths)
      --stdin                Read pre-save contents from stdin (with editor-json and --path)
      --path <FILE>          Which file to judge the --stdin contents as
      --log-file <PATH>      Log violations to file [default: .doeff-lint.jsonl]
      --no-log               Disable logging to file
      --modified             Only lint git-modified files
      --no-config            Ignore pyproject.toml configuration
      --hook                 Run as Cursor stop hook
  -v, --verbose              Show verbose output
  -h, --help                 Print help
  -V, --version              Print version
```

## Exclusion Behavior

By default, exclusion patterns from `pyproject.toml` (and `--exclude`) only apply when **scanning directories**. When you explicitly specify file paths, those files are linted regardless of exclusion patterns.

Use `--force-exclude` to apply exclusion rules even to explicitly specified files:

```bash
# Without --force-exclude: .venv/lib/foo.py will be linted
doeff-linter .venv/lib/foo.py

# With --force-exclude: .venv/lib/foo.py will be excluded (if .venv is in exclude list)
doeff-linter --force-exclude .venv/lib/foo.py
```

This is useful when piping file lists from external tools (like `git diff`, IDE file watchers, etc.) that may include files you want to exclude.

**Note:** The `--modified` mode always applies exclusions, similar to `--force-exclude`.

## Logging and Statistics

The linter logs all detected violations to a file in JSON Lines format by default for later analysis and statistics tracking.

**Default log file:** `.doeff-lint.jsonl`

### Disable Logging

```bash
doeff-linter --no-log
```

### Custom Log File Path

Via CLI:
```bash
doeff-linter --log-file custom-path.jsonl
```

Via `pyproject.toml`:
```toml
[tool.doeff-linter]
log_file = "custom-path.jsonl"
```

### Log Format

Each line in the log file is a JSON object containing:

```json
{
  "timestamp": 1733126639,
  "datetime": "2025-12-02T07:23:59Z",
  "files_scanned": 42,
  "total_violations": 15,
  "error_count": 3,
  "warning_count": 12,
  "info_count": 0,
  "run_mode": "normal",
  "enabled_rules": ["DOEFF001", "DOEFF002", ...],
  "violations": [
    {
      "rule_id": "DOEFF006",
      "file_path": "src/utils.py",
      "line": 42,
      "severity": "error",
      "message": "...",
      "source_line": "def parse() -> tuple:"
    }
  ]
}
```

### CLI Statistics

View statistics from the log file:

```bash
doeff-linter stats
doeff-linter stats --trend    # Include daily trend
doeff-linter stats custom.jsonl  # Use custom log file
```

Example output:
```
════════════════════════════════════════════════════════════
 DOEFF-LINTER STATISTICS 
════════════════════════════════════════════════════════════

📊 Overview
  Total lint runs:      42
  Total files scanned:  156
  Total violations:     234

🎯 By Severity
  Errors:   12 (5.1%)
  Warnings: 220 (94.0%)
  Info:     2 (0.9%)

📋 By Rule
  DOEFF014    89  ████████████████████
  DOEFF013    67  ███████████████
  DOEFF012    34  ████████
  ...
```

### HTML Report

Generate an interactive HTML dashboard:

```bash
doeff-linter report
doeff-linter report -o my-report.html
doeff-linter report --open  # Open in browser after generation
```

The report includes:
- Summary statistics cards
- Violations by rule (bar chart)
- Severity distribution (donut chart)
- Top files by violations
- Daily activity trend

### Raw Log Analysis

You can also analyze logs with `jq`:

```bash
# Count violations by rule
cat .doeff-lint.jsonl | jq -s '[.[].violations[]] | group_by(.rule_id) | map({rule: .[0].rule_id, count: length})'

# Get total violations over time
cat .doeff-lint.jsonl | jq '{date: .datetime, total: .total_violations}'

# Find most frequent violation locations
cat .doeff-lint.jsonl | jq -s '[.[].violations[]] | group_by(.file_path) | map({file: .[0].file_path, count: length}) | sort_by(-.count) | .[0:10]'
```

## Cursor Integration (Stop Hook)

The linter can run as a [Cursor stop hook](https://cursor.com/ja/docs/agent/hooks) to automatically check code quality after the AI agent completes its work.

### Setup

1. Build and install the linter:
```bash
cd packages/doeff-linter
cargo build --release
# Copy to a location in your PATH, or use the full path
cp target/release/doeff-linter ~/.local/bin/
```

2. Create `.cursor/hooks.json` in your project root:
```json
{
  "version": 1,
  "hooks": {
    "stop": [
      {
        "command": "doeff-linter --hook"
      }
    ]
  }
}
```

3. Restart Cursor.

### How it Works

When the Cursor agent completes a task:
1. The linter receives the workspace paths via stdin
2. It scans all Python files for violations (respecting `exclude` patterns from `pyproject.toml`)
3. If errors are found, it sends a `followup_message` asking the agent to fix them
4. The agent automatically continues to fix the identified issues

The hook only triggers follow-up for **errors** (not warnings), preventing infinite loops while ensuring critical issues are addressed.

**Note:** The hook mode automatically applies exclusion rules from `pyproject.toml` to all files (equivalent to `--force-exclude`).

### Example Output

When violations are found, the hook outputs:
```json
{
  "followup_message": "The doeff-linter found code quality issues...\n\n## DOEFF006 - No Tuple Returns\n**Problem:** Returning raw tuples reduces code readability...\n**How to fix:** Use a dataclass or NamedTuple...\n\n- `src/utils.py:42` → `def parse_result() -> tuple[str, int]:`\n\nPlease fix these issues following the suggestions above."
}
```

## License

MIT License - see LICENSE file for details.
