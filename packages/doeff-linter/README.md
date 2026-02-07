# doeff-linter

A high-performance linter for enforcing code quality and immutability patterns in Python.

## Features

- **14 specialized rules** for code quality and immutability
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
| DOEFF002 | Mutable Attribute Naming | Mutable attributes must use `mut_` or `_mut` prefix |
| DOEFF003 | Max Mutable Attributes | Limit the number of mutable attributes in a class |
| DOEFF004 | No os.environ Access | Forbid direct access to environment variables |
| DOEFF005 | No Setter Methods | Classes should not have setter methods |
| DOEFF006 | No Tuple Returns | Functions should not return tuples (use dataclasses) |
| DOEFF007 | No Mutable Argument Mutations | Functions should not mutate dict/list/set arguments |
| DOEFF008 | No Dataclass Attribute Mutation | Dataclass instances should be immutable |
| DOEFF009 | Missing Return Type Annotation | Functions should have return type annotations |
| DOEFF010 | Test File Placement | Test files must be under `tests/` directory |
| DOEFF011 | No Flag/Mode Arguments | Use callbacks or protocol objects instead of flag/mode arguments |
| DOEFF012 | No Append Loop Pattern | Use list comprehension instead of empty list + for loop append |
| DOEFF013 | Prefer Maybe Monad | Use `Maybe[T]` instead of `Optional[T]` or `T \| None` |
| DOEFF014 | No Try-Except Blocks | Use doeff's error handling effects instead of try-except |

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
      --output-format <FMT>  Output format: text, json [default: text]
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

