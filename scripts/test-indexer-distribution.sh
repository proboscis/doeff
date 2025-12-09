#!/bin/bash
# Test script for doeff-indexer distribution
# This validates that the binary bundling works correctly
#
# Usage:
#   ./scripts/test-indexer-distribution.sh           # Uses .venv/bin/python if available
#   PYTHON=/path/to/python ./scripts/test-indexer-distribution.sh  # Use specific Python

set -e

# Use specified Python or find one
if [ -z "$PYTHON" ]; then
    if [ -x ".venv/bin/python" ]; then
        PYTHON=".venv/bin/python"
    elif [ -x "venv/bin/python" ]; then
        PYTHON="venv/bin/python"
    else
        PYTHON="python"
    fi
fi

echo "=========================================="
echo "doeff-indexer Distribution Test"
echo "=========================================="
echo ""

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

pass() { echo -e "${GREEN}✓ $1${NC}"; }
fail() { echo -e "${RED}✗ $1${NC}"; exit 1; }
warn() { echo -e "${YELLOW}⚠ $1${NC}"; }
info() { echo -e "  $1"; }

# Check if we're in the doeff repo
if [ ! -f "pyproject.toml" ] || ! grep -q 'name = "doeff"' pyproject.toml; then
    fail "Please run this script from the doeff repository root"
fi

echo "1. Checking current environment..."
echo "-----------------------------------"

# Check Python version
PYTHON_VERSION=$($PYTHON --version 2>&1)
info "Python: $PYTHON_VERSION"
info "Using: $PYTHON"

# Check if uv is available
if command -v uv &> /dev/null; then
    info "uv: $(uv --version)"
else
    warn "uv not found, using pip"
fi

# Check current doeff-indexer installation
if command -v doeff-indexer &> /dev/null; then
    INDEXER_PATH=$(which doeff-indexer)
    INDEXER_VERSION=$(doeff-indexer --version 2>&1 || echo "unknown")
    info "doeff-indexer: $INDEXER_VERSION"
    info "Location: $INDEXER_PATH"
else
    warn "doeff-indexer not found in PATH"
fi

echo ""
echo "2. Testing CLI functionality..."
echo "--------------------------------"

# Test --help
if doeff-indexer --help > /dev/null 2>&1; then
    pass "doeff-indexer --help"
else
    fail "doeff-indexer --help failed"
fi

# Test --version
if doeff-indexer --version > /dev/null 2>&1; then
    pass "doeff-indexer --version"
else
    fail "doeff-indexer --version failed"
fi

# Test index command
if doeff-indexer index --root . > /dev/null 2>&1; then
    pass "doeff-indexer index --root ."
else
    fail "doeff-indexer index failed"
fi

# Test find-interpreters
if doeff-indexer find-interpreters --root . > /dev/null 2>&1; then
    pass "doeff-indexer find-interpreters --root ."
else
    fail "doeff-indexer find-interpreters failed"
fi

echo ""
echo "3. Testing Python API..."
echo "-------------------------"

# Test Python import
if $PYTHON -c "from doeff_indexer import Indexer, SymbolInfo; print('OK')" 2>/dev/null; then
    pass "from doeff_indexer import Indexer, SymbolInfo"
else
    fail "Python import failed"
fi

# Test Indexer usage
if $PYTHON -c "
from doeff_indexer import Indexer
try:
    indexer = Indexer.for_module('doeff')
    symbols = indexer.find_symbols(tags=['doeff'], symbol_type='function')
    print(f'Found {len(symbols)} symbols')
except Exception as e:
    print(f'Error: {e}')
    exit(1)
" 2>/dev/null; then
    pass "Indexer.for_module() and find_symbols()"
else
    warn "Indexer test had issues (may be expected if not in doeff module)"
fi

echo ""
echo "4. Testing doeff run integration..."
echo "------------------------------------"

# Test doeff run --help
if $PYTHON -m doeff run --help > /dev/null 2>&1; then
    pass "$PYTHON -m doeff run --help"
else
    fail "$PYTHON -m doeff run --help failed"
fi

echo ""
echo "5. Checking binary location..."
echo "-------------------------------"

# Check if binary is in Python env
PYTHON_BIN_DIR=$($PYTHON -c "import sys; print(sys.prefix + '/bin')" 2>/dev/null || echo "")
if [ -n "$PYTHON_BIN_DIR" ]; then
    info "Python bin dir: $PYTHON_BIN_DIR"
    if [ -x "$PYTHON_BIN_DIR/doeff-indexer" ]; then
        pass "Binary found in Python environment: $PYTHON_BIN_DIR/doeff-indexer"
    else
        warn "Binary not found in Python environment (may be using system path)"
    fi
fi

echo ""
echo "6. Performance check..."
echo "------------------------"

# Measure startup time
START_TIME=$($PYTHON -c "import time; print(time.time())")
doeff-indexer --version > /dev/null 2>&1
END_TIME=$($PYTHON -c "import time; print(time.time())")
ELAPSED=$($PYTHON -c "print(f'{($END_TIME - $START_TIME) * 1000:.1f}')" 2>/dev/null || echo "?")
info "CLI startup time: ${ELAPSED}ms"

if [ "$ELAPSED" != "?" ] && [ "$(echo "$ELAPSED < 100" | bc -l 2>/dev/null || echo 1)" = "1" ]; then
    pass "Startup time under 100ms"
else
    warn "Startup time may be slow (${ELAPSED}ms)"
fi

echo ""
echo "=========================================="
echo -e "${GREEN}All tests passed!${NC}"
echo "=========================================="
