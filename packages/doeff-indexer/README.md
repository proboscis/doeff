# doeff-indexer

Rust-based static indexer for discovering `Program` and `KleisliProgram` definitions in a doeff
codebase. The tool scans Python modules for:

- Functions decorated with `@do` (reported as Kleisli programs)
- Functions that accept `Program[...]` or `ProgramInterpreter` parameters
- Functions whose return annotation references `Program` or `KleisliProgram`
- Module-level assignments annotated with or returning `Program`/`KleisliProgram`

Each indexed item includes detected type-argument usage so you can query for specific
`Program`/`KleisliProgram` generics.

## Installation

### Via pip (Recommended)

The easiest way to install `doeff-indexer` is via pip. This installs both the CLI binary and the
Python API:

```bash
pip install doeff-indexer
```

After installation, the `doeff-indexer` command will be available in your Python environment:

```bash
doeff-indexer --version
```

### From Source (Development)

For development, you can build from source:

```bash
cd packages/doeff-indexer

# Build the Rust binary only (no Python features)
cargo build --release --no-default-features

# Or build with Python bindings (requires maturin)
maturin develop
```

## CLI Usage

```bash
# Print JSON index for the repository root
doeff-indexer index --root . --pretty

# Find interpreter functions
doeff-indexer find-interpreters --root .

# Find Kleisli functions matching a type argument
doeff-indexer find-kleisli --root . --type-arg MyType

# Find transform functions
doeff-indexer find-transforms --root .

# Write index to a file
doeff-indexer index --root . --output index.json
```

## Python API

The indexer also provides a Python API for programmatic access:

```python
from doeff_indexer import Indexer, SymbolInfo

# Create an indexer for a module
indexer = Indexer.for_module("myproject.core")

# Find symbols with specific tags
symbols = indexer.find_symbols(
    tags=["doeff", "interpreter", "default"],
    symbol_type="function"
)

for sym in symbols:
    print(f"{sym.full_path} at line {sym.line_number}")
```

## IDE Integration

Both VSCode and PyCharm plugins automatically discover `doeff-indexer` from your Python
environment. If you have `doeff` installed via pip, the plugins will use the bundled binary
without any additional configuration.

## Testing

```bash
cargo test
```
