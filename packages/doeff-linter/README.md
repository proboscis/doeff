# doeff-linter

A high-performance linter for enforcing code quality and immutability patterns in Python.

## Features

- **10 specialized rules** for code quality and immutability
- **Configurable via pyproject.toml**
- **noqa comments** for per-line rule suppression
- **Fast** - written in Rust for maximum performance

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

# Exclude paths
exclude = [".venv", "build", "tests/fixtures"]

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

## Inline Suppression

Use `noqa` comments to suppress rules on specific lines:

```python
# Suppress specific rule
def dict():  # noqa: DOEFF001
    return {}

# Suppress all rules on a line
data["key"] = value  # noqa
```

## CLI Options

```
Usage: doeff-linter [OPTIONS] [PATHS]...

Arguments:
  [PATHS]...  Files or directories to lint

Options:
      --enable <RULES>       Enable specific rules (comma-separated)
      --disable <RULES>      Disable specific rules (comma-separated)
      --exclude <PATTERNS>   Exclude paths matching patterns
      --output-format <FMT>  Output format: text, json [default: text]
      --modified             Only check git-modified files
      --no-config            Ignore pyproject.toml configuration
  -v, --verbose              Show verbose output
  -h, --help                 Print help
  -V, --version              Print version
```

## License

MIT License - see LICENSE file for details.



