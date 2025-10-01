#!/usr/bin/env python3
"""Quick test of doeff-indexer Python API"""

from doeff_indexer import Indexer


def test_basic_api():
    """Test that the Python API works"""
    print("Testing doeff-indexer Python API...")

    # Create indexer for doeff module
    indexer = Indexer.for_module("doeff")
    print(f"✓ Created indexer: {indexer}")

    # Test find_symbols
    symbols = indexer.find_symbols(tags=["doeff"], symbol_type=None)
    print(f"✓ Found {len(symbols)} symbols with 'doeff' tag")

    # Test get_module_hierarchy
    hierarchy = indexer.get_module_hierarchy()
    print(f"✓ Module hierarchy: {hierarchy}")

    print("\n✅ All basic API tests passed!")

if __name__ == "__main__":
    test_basic_api()
