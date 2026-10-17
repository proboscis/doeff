"""ADR-DOE-BUILD-002 の R3b の判断(placement-files・placement-violations)を、引数の repo の根の木に当てる(設計検証 lt-4KTRWD3K1PH2VG5W5X294EZJP4)。
使い方: uv run --no-sync python law_on_tree.py <ADR の冊の path> <repo の根>。違反が在れば 1 で終わり、違反の文を出す。"""
import importlib.util
import sys
from pathlib import Path

import hy  # noqa: F401 — 冊(Hy)を import するため
from doeff import run

spec = importlib.util.spec_from_file_location("adr_build_002", sys.argv[1])
adr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adr)
found = run(adr.placement_files(Path(sys.argv[2])))
violations = run(adr.placement_violations(found))
print("綴る file:", sorted(found))
print("違反:", violations)
sys.exit(1 if violations else 0)
