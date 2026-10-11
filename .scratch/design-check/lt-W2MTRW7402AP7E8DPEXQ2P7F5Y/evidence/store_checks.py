"""設計検証の正常例・失敗例(保存先・副作用・並行の変更シナリオ)を、bound_source.binding_of の公開の口だけで撃つための探り。

使い方: uv run --project <doeff の木> python store_checks.py <mode> <空の作業 dir>
答え: JSON 1 行(mode ごとの観測)。
"""

import json
import os
import subprocess
import sys
from pathlib import Path

from doeff_hy_bytecode_guard import bound_source

TABLE_PY = 'TABLE = {"a": 1}\nTABLE["b"] = 2\n\n\ndef add():\n    TABLE["c"] = 3\n'


def forget() -> None:
    """process の中の覚えを捨てる(次の呼び出しを新しい process と同じにするため)。"""
    for name in ("_SOURCES", "_PARSED", "_BINDINGS"):
        vars(bound_source)[name].clear()


def entries(store: Path) -> list[str]:
    """置き場の束縛の entry の相対 path。"""
    return sorted(str(p.relative_to(store)) for p in store.glob("*/*.binding"))


def binding(path: Path, store: str) -> object:
    """置き場を store にして束縛を読む(覚えは捨ててから)。"""
    os.environ["DOEFF_HY_CODE_STORE"] = store
    forget()
    return bound_source.binding_of(str(path), "TABLE")


def counted_parses() -> dict[str, int]:
    """source を解いた回数を数える口を包む。"""
    count = {"n": 0}
    reading = bound_source._read

    def counting(data: bytes, path: str) -> object:
        count["n"] += 1
        return reading(data, path)

    bound_source._read = counting
    return count


def main() -> None:
    """mode ごとに 1 つの観測を出す。"""
    mode, work = sys.argv[1], Path(sys.argv[2])
    work.mkdir(parents=True, exist_ok=True)
    source = work / "tables.py"
    source.write_text(TABLE_PY, encoding="utf-8")
    fresh = binding(source, "off")
    answer: dict[str, object] = {"mode": mode, "fresh_statements": len(fresh.statements) if fresh else None}
    if mode == "storage-move":
        a, b = work / "store-a", work / "store-b"
        a.mkdir(), b.mkdir()
        in_a = binding(source, str(a))
        parses = counted_parses()
        in_b = binding(source, str(b))
        answer |= {"a_entries": entries(a), "b_entries": entries(b), "same_as_fresh": in_a == fresh and in_b == fresh,
                   "parses_after_move": parses["n"]}
    elif mode == "storage-bad-entry":
        store = work / "store"
        store.mkdir()
        binding(source, str(store))
        (entry,) = store.glob("*/*.binding")
        entry.write_bytes(b"not a marshal entry")
        again = binding(source, str(store))
        answer |= {"same_as_fresh": again == fresh, "entry_rewritten_valid": binding(source, str(store)) == fresh}
    elif mode == "storage-reading-code-changed":
        store = work / "store"
        store.mkdir()
        binding(source, str(store))
        vars(bound_source)["_OWN"]["own"] = bound_source.OwnCode("0" * 64)  # 読み方の code が違う版の代わり
        parses = counted_parses()
        again = binding(source, str(store))
        answer |= {"same_as_fresh": again == fresh, "parses_with_other_reading_code": parses["n"],
                   "entries": len(entries(store))}
    elif mode == "effects-readonly":
        store = work / "store-ro"
        store.mkdir()
        store.chmod(0o555)
        got = binding(source, str(store))
        answer |= {"same_as_fresh": got == fresh, "entries": entries(store)}
        store.chmod(0o755)
    elif mode == "concurrency-parallel":
        store = work / "store"
        store.mkdir()
        child = (
            "import os, sys, json; from doeff_hy_bytecode_guard import bound_source; "
            "b = bound_source.binding_of(sys.argv[1], 'TABLE'); print(json.dumps(repr(b)))"
        )
        env = {**os.environ, "DOEFF_HY_CODE_STORE": str(store)}
        procs = [subprocess.Popen([sys.executable, "-c", child, str(source)], env=env, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True) for _ in range(6)]
        outs = [p.communicate(timeout=120) for p in procs]
        answer |= {"distinct_answers": len({o for o, _ in outs}), "same_as_fresh": all(o.strip() == json.dumps(repr(fresh)) for o, _ in outs),
                   "stderr": [e for _, e in outs if e.strip()], "entries": len(entries(store)),
                   "codes": [p.returncode for p in procs]}
    elif mode == "concurrency-truncated":
        store = work / "store"
        store.mkdir()
        binding(source, str(store))
        (entry,) = store.glob("*/*.binding")
        data = entry.read_bytes()
        entry.write_bytes(data[: len(data) // 2])  # 書きかけの entry の代わり
        again = binding(source, str(store))
        answer |= {"same_as_fresh": again == fresh, "entry_rewritten_valid": binding(source, str(store)) == fresh}
    print(json.dumps(answer, ensure_ascii=False))


main()
