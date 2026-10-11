"""設計検証 lt-9TQ4W7B2R3TJ8W808QFDDZ83VP の変異: 変異の木(doeff の detach の作業木)に違反の形を 1 つずつ当て、守る検を走らせ、
元の文へ書き戻す。引数: 変異の木の根・出力の dir・変異の名(省けば全部)。各変異の出力 = <名>.txt(命令・終了の code・pytest の出力)。"""
import subprocess
import sys
from pathlib import Path

root, out = Path(sys.argv[1]), Path(sys.argv[2])
only = set(sys.argv[3:])
pkg = root / "packages/doeff-hy"
src = pkg / "src/doeff_hy_bytecode_guard"

A_NAIVE_READ = '''    remembered = _REMEMBERED.get(key)
    if remembered is not None and remembered.subject is subject and _confirmed(key, remembered):
        return remembered.closure
'''
A_NAIVE_READ_NEW = A_NAIVE_READ + '''    import json as _json  # 変異: 候補 (a) の素直な形 — digest を依った file の状態と一緒に置き場へ覚え、次の process は状態だけ確かめる
    from doeff_hy_bytecode_guard import code_store as _cs
    _store = _cs.store_dir()
    _path = None if _store is None else os.path.join(_store, "mut-a-" + hashlib.sha256(repr(key).encode()).hexdigest())
    if _path is not None and os.path.exists(_path):
        _saved = _json.loads(open(_path).read())
        if all(_file_state(p) == FileState(p, m, s) for p, m, s in _saved["files"]):
            return ClosureDigest(_saved["digest"], tuple(_saved["modules"]))
'''
A_NAIVE_WRITE = '''    _REMEMBERED[key] = RememberedDigest(subject, states, closure)
    return closure
'''
A_NAIVE_WRITE_NEW = '''    _REMEMBERED[key] = RememberedDigest(subject, states, closure)
    if _path is not None:
        open(_path, "w").write(_json.dumps({"digest": digest, "modules": list(modules), "files": [[s.path, s.mtime_ns, s.size] for s in states]}))
    return closure
'''

MUTATIONS = {
    "no-close": (
        "覚えを置く口が閉じる時に覚えを閉じない(継いだ context に覚えが残る)",
        [("loader_hooks.py", "            scope.close()\n            IMPORT_SCOPE.reset(token)", "            IMPORT_SCOPE.reset(token)"),
         ("macro_use.py", "        scope.close()\n        IMPORT_SCOPE.reset(token)", "        IMPORT_SCOPE.reset(token)")],
        "outer_import_ended or running_import",
    ),
    "no-owner": (
        "覚えを置いた thread の印を確かめない(開いている間は、中で起きた thread も覚えを共有する)",
        [("import_scope.py", "if scope is None or scope.closed or scope.owner != get_ident():", "if scope is None or scope.closed:")],
        "outer_import_ended or running_import",
    ),
    "no-hy-key": (
        "code の読みの entry の鍵から Hy の版を外す",
        [("macro_use.py", "sys.version, hy.__version__, place,", "sys.version, place,")],
        "keyed_by_the_hy_version or module_its_key_covers",
    ),
    "moved-reading": (
        "code の読みが呼ぶ _mangle を別の file(code_store.py)へ移して import する",
        [("macro_use.py", '''def _mangle(text: str) -> str:
    """Hy の名の mangle(Hy は記録を作る時と照らす時には読み込み済み)。"""
    import hy

    return hy.mangle(text)
''', "from doeff_hy_bytecode_guard.code_store import _mangle  # 変異: 読みの関数を別の file へ移した\n"),
         ("code_store.py", None, '''

def _mangle(text: str) -> str:  # 変異: macro_use から移した読みの関数
    import hy

    return hy.mangle(text)
''')],
        "keyed_by_the_hy_version or module_its_key_covers",
    ),
    "a-naive": (
        "候補 (a) の素直な形: digest を依った file の状態と一緒に置き場へ覚え、次の process は file の状態だけを確かめて使う",
        [("macro_use.py", A_NAIVE_READ, A_NAIVE_READ_NEW), ("macro_use.py", A_NAIVE_WRITE, A_NAIVE_WRITE_NEW)],
        "macro_changed_in_another_tree or changed_value_from_another_module_is_stale_in_a_second or changed_environment_value_is_stale_in_a_second or another_top_package_is_stale_after",
    ),
    "no-against-executed": (
        "盲検 A の直しを外す: 歩みが今の disk の file を、module を実行した時の状態と比べない",
        [("macro_use.py", "        self.files[module.__name__] = file\n        self._against_executed(file)\n", "        self.files[module.__name__] = file\n"),
         ("macro_use.py", "        if isinstance(file, str):\n            self._against_executed(file)\n", "")],
        "loaded_module_changed",
    ),
    "hy-repr-import": (
        "文の読みの鍵を作るために、読み込まれていない hy.core.hy_repr を読み込む(hy.repr を引く)",
        [("bound_source.py", '    if _HY_REPR_MODULE not in sys.modules:\n        return f"{hy.__version__}\\0default"\n', "")],
        "does_not_import_hy_repr",
    ),
    "direct-read": (
        "覚えを current_scope() を通さず IMPORT_SCOPE.get() で直に読む(L2062 の形 — 継いだ thread・写した context の値も使う)",
        [("macro_use.py", "    scope = current_scope()\n    if scope is None:\n        return _file_state(path)\n",
          "    scope = IMPORT_SCOPE.get()\n    if scope is None:\n        return _file_state(path)\n")],
        "read_only_through_current_scope",
    ),
    "hy-repr-custom": (
        "hy.repr の表に Hy の外の関数が登録されていても、Hy の文の読みに置き場を使う",
        [("bound_source.py", '    if any(not row.function.startswith(("hy.", "builtins.")) for row in rows):\n        return None\n', "")],
        "custom_hy_repr",
        "tests/test_bound_source_store.py",
    ),
}

head = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
for name, (what, edits, select, *files) in MUTATIONS.items():
    if only and name not in only:
        continue
    tests = files or ["tests/test_macro_use_record.py"]
    originals = {}
    try:
        for file, old, new in edits:
            path = src / file
            text = originals.setdefault(path, path.read_text())
            current = path.read_text()
            if old is None:
                path.write_text(current + new)
            else:
                assert current.count(old) == 1, (name, file, old[:60])
                path.write_text(current.replace(old, new))
        diff = subprocess.run(["git", "-C", str(root), "diff"], capture_output=True, text=True).stdout
        command = ["uv", "run", "--frozen", "pytest", "-q", "-p", "no:cacheprovider", *tests, "-k", select]
        done = subprocess.run(command, cwd=pkg, capture_output=True, text=True, timeout=900)
        report = (f"変異: {name} — {what}\n対象の版: {head}(変異の木 {root})\n命令(cwd {pkg}): {' '.join(command[:-1])} '{select}'\n"
                  f"終了の code: {done.returncode}\n\n--- 当てた差分 ---\n{diff}\n--- pytest の出力 ---\n{done.stdout}{done.stderr}")
        (out / f"mut-{name}.txt").write_text(report)
        summary = [line for line in done.stdout.splitlines() if line.startswith(("FAILED", "E   ")) or " passed" in line or " failed" in line]
        print(f"== {name}: exit {done.returncode}")
        print("\n".join(line[:260] for line in summary[-8:]))
    finally:
        for path, text in originals.items():
            path.write_text(text)
left = subprocess.run(["git", "-C", str(root), "status", "--short"], capture_output=True, text=True).stdout
print("書き戻した後の木:", left or "差分なし")
