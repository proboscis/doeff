# Case: PyCharm Plugin Not Finding Symbols

## Problem

When clicking the gutter icon on a Program variable in PyCharm, you see an error:
```
Symbol 'variable_name' not found in indexer.
Found 0 symbols: no symbols
```

The error popup now shows diagnostic information including the indexer binary path and command executed.

## Root Cause

The PyCharm plugin was using an outdated `doeff-indexer` binary that didn't have the `--file` filter support needed for the new indexer-based module path lookup.

## Investigation Steps

1. **Check the indexer binary location** - The error popup shows which binary is being used (e.g., `/Users/username/.cargo/bin/doeff-indexer`)

2. **Test the binary manually** - Run the command shown in the error popup to see if it works:
   ```bash
   /Users/username/.cargo/bin/doeff-indexer index --root /path/to/project --file /path/to/file.py
   ```

3. **Check for --file support** - Verify the indexer has the `--file` filter:
   ```bash
   doeff-indexer index --help | grep -A 2 "Filter by file"
   ```

4. **Check for multiple installations** - See if there are multiple indexer binaries:
   ```bash
   ls -la ~/.cargo/bin/doeff-indexer ~/.local/bin/doeff-indexer
   which -a doeff-indexer
   ```

## Solution

**Always use `cargo install` to install the indexer** - this is the standard Rust installation method and ensures a single, consistent binary location:

```bash
cd /path/to/doeff
cargo install --path packages/doeff-indexer --force
```

This installs to `~/.cargo/bin/doeff-indexer`, which is the first location the PyCharm plugin searches.

### Why `cargo install` instead of manual copying?

- **Standard Rust convention** - `~/.cargo/bin/` is the standard location for Rust binaries
- **Single source of truth** - Prevents having multiple outdated copies in different locations
- **Automatic PATH setup** - Cargo setup adds `~/.cargo/bin` to PATH
- **Plugin compatibility** - PyCharm plugin searches `~/.cargo/bin` first

### If you previously manually copied the binary

Remove any manual copies to avoid confusion:
```bash
rm ~/.local/bin/doeff-indexer  # If it exists
```

## Plugin Diagnostic Features

The PyCharm plugin now shows detailed diagnostics in error popups:

1. **Symbol count** - How many symbols were found in the file
2. **Symbol names** - The first 10 symbols found (or "no symbols" if empty)
3. **Indexer binary path** - Exact path to the indexer being used
4. **Command executed** - Full command that was run, for manual testing

Example error:
```
Symbol 'test_pipeline' not found in indexer.
Found 8 symbols: default_env, loguru_interceptor, pure_interpreter, show_count, test_pipeline, ...
Indexer: /Users/username/.cargo/bin/doeff-indexer
Command: /Users/username/.cargo/bin/doeff-indexer index --root /Users/username/repos/project --file /Users/username/repos/project/src/module/file.py
File may have been modified - try saving and clicking again.
```

## Verification

After installing, verify the indexer works:

```bash
# Check version
doeff-indexer --version

# Verify --file filter exists
doeff-indexer index --help | grep file

# Test on a specific file
cd /path/to/your/project
doeff-indexer index --root . --file path/to/file.py | jq '.entries | length'
```

Should return a non-zero count of symbols.

## Related Changes

- Commit: `bdfe704` - Refactored PyCharm plugin to use indexer for module paths
- Added `--file` filter to indexer CLI for file-specific symbol lookup
- Removed the manual `findModulePath()` calculation in favor of indexer lookup
- Added comprehensive error diagnostics to plugin

## Prevention

**Always reinstall the indexer after making changes:**

```bash
# After modifying packages/doeff-indexer/src/
cargo install --path packages/doeff-indexer --force
```

The `--force` flag ensures the binary is replaced even if the version number hasn't changed.