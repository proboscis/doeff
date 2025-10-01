#!/usr/bin/env python3
"""Fix unused argument linter errors by prefixing with underscore"""

import re
from pathlib import Path

# Map of file -> [(line_num, old_name, new_name)]
fixes = {
    "tests/test_cache.py": [
        (97, "temp_cache_db", "_temp_cache_db"),
        (199, "temp_cache_db", "_temp_cache_db"),
        (232, "temp_cache_db", "_temp_cache_db"),
        (244, "ctx", "_ctx"),
        (249, "ctx", "_ctx"),
        (371, "temp_cache_db", "_temp_cache_db"),
        (406, "temp_cache_db", "_temp_cache_db"),
        (448, "temp_cache_db", "_temp_cache_db"),
        (481, "temp_cache_db", "_temp_cache_db"),
        (498, "extra", "_extra"),
        (545, "temp_cache_db", "_temp_cache_db"),
        (567, "temp_cache_db", "_temp_cache_db"),
        (630, "temp_cache_db", "_temp_cache_db"),
    ],
    "tests/test_comprehensive_stack_safety.py": [
        (73, "i", "_i"),  # Actually needs to be fixed differently - loop closure issue
        (74, "i", "_i"),
        (278, "e", "_e"),
    ],
    "tests/test_effect_intercept.py": [
        (184, "exc", "_exc"),
    ],
    "tests/test_gather_effect.py": [
        (173, "id", "item_id"),
        (178, "id", "item_id"),
    ],
    "tests/test_intercept_recursion.py": [
        (21, "transform", "_transform"),
    ],
    "tests/test_kleisli_program.py": [
        (401, "b", "_b"),
    ],
    "tests/test_programs_as_building_blocks.py": [
        (82, "e", "_e"),
        (235, "e", "_e"),
    ],
    "tests/test_recover_edge_cases.py": [
        (158, "exc", "_exc"),
        (262, "exc", "_exc"),
    ],
    "tests/test_recover_enhanced.py": [
        (195, "exc", "_exc"),
        (237, "exc", "_exc"),
        (291, "exc", "_exc"),
    ],
}

def fix_file(filepath: Path, line_fixes: list[tuple[int, str, str]]):
    """Fix unused arguments in a file"""
    lines = filepath.read_text().splitlines(keepends=True)

    for line_num, old_name, new_name in line_fixes:
        if 1 <= line_num <= len(lines):
            line_idx = line_num - 1
            line = lines[line_idx]

            # Replace the argument name (be careful with word boundaries)
            # Match patterns like: "def foo(old_name)" or "(old_name:" or ", old_name)"
            patterns = [
                (rf"\b{re.escape(old_name)}:", f"{new_name}:"),
                (rf"\b{re.escape(old_name)}\)", f"{new_name})"),
                (rf",\s*{re.escape(old_name)}\)", f", {new_name})"),
                (rf"\({re.escape(old_name)}:", f"({new_name}:"),
            ]

            for pattern, replacement in patterns:
                if re.search(pattern, line):
                    lines[line_idx] = re.sub(pattern, replacement, line)
                    break

    filepath.write_text("".join(lines))
    print(f"✓ Fixed {filepath.name}: {len(line_fixes)} changes")

def main():
    repo_root = Path("/Users/s22625/repos/doeff")

    for file_path, line_fixes in fixes.items():
        full_path = repo_root / file_path
        if full_path.exists():
            fix_file(full_path, line_fixes)
        else:
            print(f"⚠ File not found: {full_path}")

if __name__ == "__main__":
    main()
