#!/usr/bin/env python3
"""Migrate test files from .run() to .run_async()"""

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class MigrationResult:
    """Result of migrating a single file"""
    changed: bool
    count: int


def migrate_file(filepath: Path) -> MigrationResult:
    """Migrate a single file. Returns migration result"""
    content = filepath.read_text()
    original = content

    # Pattern: await <variable>.run(
    # Replace with: await <variable>.run_async(
    pattern = r"(await\s+\w+)\.run\("
    replacement = r"\1.run_async("

    content = re.sub(pattern, replacement, content)

    if content != original:
        filepath.write_text(content)
        count = len(re.findall(pattern, original))
        return MigrationResult(changed=True, count=count)

    return MigrationResult(changed=False, count=0)

def main():
    tests_dir = Path("/Users/s22625/repos/doeff/tests")

    # Find all test files
    test_files = list(tests_dir.glob("test_*.py"))

    total_files = 0
    total_replacements = 0

    print("Migrating test files...")
    print("-" * 60)

    for filepath in sorted(test_files):
        if filepath.suffix != ".py" or ".skip" in str(filepath):
            continue

        result = migrate_file(filepath)
        if result.changed:
            total_files += 1
            total_replacements += result.count
            print(f"✓ {filepath.name}: {result.count} replacements")

    print("-" * 60)
    print(f"Summary: {total_files} files changed, {total_replacements} total replacements")

    # Note: test_do_decorator.py was already migrated
    if total_files == 0:
        print("\nNote: All files already migrated or test_do_decorator.py was the only one needed.")

if __name__ == "__main__":
    main()
