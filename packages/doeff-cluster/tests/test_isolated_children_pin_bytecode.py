"""`-I` で子の Python を起こす検は、同じ行で `-B` を併せる。

訳: `-I`(isolated)は PYTHON* の環境変数を読まないので、根の conftest が置く PYTHONDONTWRITEBYTECODE=1 が子に効かない。
`-B` を付けない子は、起動の .pth が読む module(_virtualenv・doeff_hy_bytecode_guard)と、道具が読む deploy の module
(uv_cache)の .pyc を checkout に書き、bytecode の見張りが session の終わりに落ちる。packages の処理ステージは 4 つの
package を並べて走らせるので、同じ時に走る別の package の検まで落ちる(日次 2026-11-09 00:56 JST の回 — test_python_installs と
test_boot_roots の子が `-I` だけで起こしていた)。

確かめ方: この dir の検の file を読み、`"-I"` を含む行かその次の行が `"-B"` を含むかを見る(argv を 1 項 1 行に並べる形も読む)。
外す file は WRITES_ONLY_UNDER_TMP に名と訳で名指す(-I の子が .pyc を書く事そのものを確かめる検で、子は一時の dir に作った venv の
python で一時の dir の木を import する — checkout へ書かない)。
"""

from pathlib import Path

TESTS = Path(__file__).resolve().parent

#: `-I` の子に `-B` を併せない file と訳。子が .pyc を書く事そのものを確かめるので `-B` を足すと検の意味が消え、子の python も読む木も
#: 一時の dir(tmp_path)の下に在る。
WRITES_ONLY_UNDER_TMP = {
    "test_root_venv_owns_pycache_place.py": "-I の子が root の .pyc の置き場へ書く事を確かめる(venv・木・置き場は tmp_path の下)",
}


def test_every_isolated_child_also_skips_bytecode() -> None:
    stale = sorted(name for name in WRITES_ONLY_UNDER_TMP if not (TESTS / name).is_file())
    assert not stale, f"外す名に在って dir に無い file(名指しを消す): {stale}"
    bare = [
        f"{path.name}:{number}"
        for path in sorted([*TESTS.glob("*.py"), *TESTS.glob("*.hy")])
        if path != Path(__file__).resolve() and path.name not in WRITES_ONLY_UNDER_TMP
        for lines in [path.read_text(encoding="utf-8").splitlines()]
        for number, line in enumerate(lines, start=1)
        if '"-I"' in line and '"-B"' not in " ".join(lines[number - 1 : number + 1])
    ]
    assert not bare, f"`-I` だけで子の Python を起こす行(`-B` を同じ行か次の行に併せる): {bare}"
