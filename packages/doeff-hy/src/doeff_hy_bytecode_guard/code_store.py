"""source の中身で引く bytecode の保存先 — 引く・足す・使った印・壊れた entry の扱いの入口はこの module 1 つ(agora-redesign #1753・
#2799・#3858)。

使い手は 2 つで、どちらもここだけを通る(保存先の並びと鍵の綴りの複製を作らない):
  - import の読みの口(:mod:`doeff_hy_bytecode_guard.loader_hooks`): 作業木の .pyc が使えない Hy の module の code を引き、compile したら
    足す。
  - doeff-cluster の worker の bytecode の道具(worker/entry/code_prepare.hy と foundation/bytecode_pool.hy): 版をまたいで、中身の同じ
    source の code と import の名を引き、無い物だけを compile して足す。道具は準備する root の venv の python で走るので、root の版の
    doeff-hy でなく worker の版のこの file を path で読む — だから標準 library だけを import する(この package の他の module も読まない)。

保存先の dir = 環境変数 DOEFF_HY_CODE_STORE(``off`` = 使わない・無ければ ``$XDG_CACHE_HOME/doeff-hy/code-store``)。worker の起動の
script は既定を ``$WORK_DIR/state/doeff-hy-code-store`` に置く(日次の全体検証の task と同じ dir)。

並び: ``<dir>/<鍵の頭 2 字>/<鍵の残り><種類の末尾>``。種類 = ``.code``(marshal した code object — Hy の source は展開が依った macro の
記録を code に持ち、引いた側が今の環境の macro で照らす)と ``.imports``(worker の道具の閉包の歩みが読む import の名 — 形は道具が
持つ)。鍵は source の中身の sha256 と、中身の読みを変える物(:func:`code_key` の欄)だけで決まり、path と版(commit)に依らない。

使った印: 引いて当たるたびに entry の時刻を今にする。worker の掃除(env_store の sweep-leftovers)は 7 日使われない entry を消す —
native の wheel の保存先(.used の印で dir の時刻を進める)と同じ 7 日の作法。書きは同じ dir の一時の file に書いてから置き換える
(同時に書く別の process と混ざらず、書きかけを読ませない)。

壊れた entry(code として読めない・import の名の形が違う)は使わない: 名指しの 1 行を stderr に出して entry を除き、呼び手は作り直して
足し直す(黙って壊れた物を使わない・黙って捨てない)。
"""

import os
import sys
from types import CodeType

#: 保存先の dir を指す環境変数(値 = dir の path・``off`` = 使わない)。
STORE_ENV = "DOEFF_HY_CODE_STORE"

#: code の entry の鍵の印(形を変えたら末尾の番号を上げる — 古い鍵の entry は当たらなくなる)。2 = 鍵を compile した bytes そのものから
#: 作る版(agora-redesign #2799)。1 の版の保存先には、compile の後に file を読み直した鍵の下に古い中身の code が入った entry が在り得る
#: ので、新しい版からは引かない。
CODE_TAG = "doeff-hy/code-store/2"

#: entry の種類の末尾。
CODE_SUFFIX = ".code"
IMPORTS_SUFFIX = ".imports"

#: Hy の source の拡張子(doeff-hy は .hyk・.hyp も Hy として読ませる)— code の鍵に module 名と Hy の版を入れるかを分ける。
HY_SOURCE_SUFFIXES: tuple[str, ...] = (".hy", ".hyk", ".hyp")


def store_dir() -> str | None:
    """保存先の dir(使わない設定なら None)— 環境変数 STORE_ENV、無ければ利用者の cache の dir の下。"""
    configured = os.environ.get(STORE_ENV, "").strip()
    if configured == "off":
        return None
    if configured:
        return configured
    base = os.environ.get("XDG_CACHE_HOME", "").strip() or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, "doeff-hy", "code-store")


def content_key(parts: tuple[str, ...], source: bytes) -> str:
    """entry の鍵 — 中身の読みを変える物の列 parts(先頭は種類と形の版の印)と source の中身の sha256(16 進)。parts は 1 つずつ
    NUL で区切る(隣り合う欄の境目が動いても同じ鍵にならない)。"""
    import hashlib  # 保存先を使う時だけ読む

    digest = hashlib.sha256()
    for part in parts:
        digest.update(part.encode("utf-8"))
        digest.update(b"\0")
    digest.update(source)
    return digest.hexdigest()


def code_key(path: str, source: bytes, module_name: str, hy_version: str, cache_tag: str, optimize: int) -> str:
    """source 1 つの code の鍵。Hy の source は module 名(相対の require を解く)と Hy の版(展開)も入れる。Python の source の
    compile はそのどちらにも依らないので、中身・Python の版の印(cache_tag)・最適化の段だけ。macro の依存は鍵に入れず、当たった
    entry の記録で確かめる(compile の前には依存が分からない)。"""
    hy = path.endswith(HY_SOURCE_SUFFIXES)
    parts = (CODE_TAG, module_name if hy else "", hy_version if hy else "", cache_tag, str(optimize))
    return content_key(parts, source)


def entry_path(store: str, key: str, suffix: str) -> str:
    """鍵の entry の path(鍵の頭 2 字を dir に分ける)。"""
    return os.path.join(store, key[:2], key[2:] + suffix)


def read_entry(path: str) -> bytes | None:
    """entry の中身を読む(無ければ None)。無い以外の理由で読めない entry は名指しの 1 行を出して None(呼び手は作り直す)。当たれば
    entry の時刻を今にする(掃除が使っている entry を消さない)。"""
    try:
        with open(path, "rb") as entry:
            data = entry.read()
    except FileNotFoundError:
        return None
    except OSError as error:
        print(f"doeff-hy: bytecode の保存先の entry を読めない({error})— {path} を使わずに作り直す", file=sys.stderr, flush=True)
        return None
    try:
        os.utime(path)
    except OSError as error:
        # 読んだ中身は使える。時刻を進められないと、掃除がこの entry を 7 日で消す(次に要る時に作り直すだけ)。
        print(f"doeff-hy: bytecode の保存先の entry の時刻を進められない({error})— {path}", file=sys.stderr, flush=True)
    return data


def write_entry(path: str, data: bytes) -> str | None:
    """entry を書く(同じ dir の一時の file に書いてから置き換える)。答え = 書けなかった理由(書けたら None)— 保存先は速さのため
    だけなので呼び手の結果は変わらないが、呼び手は理由を名指して出す(書けない保存先を黙って使い続けない)。"""
    import tempfile  # 保存先へ書く時だけ読む

    directory = os.path.dirname(path)
    try:
        os.makedirs(directory, exist_ok=True)
        handle, temporary = tempfile.mkstemp(dir=directory, suffix=".tmp")
    except OSError as error:
        return f"{directory} に書けない: {error}"
    try:
        with os.fdopen(handle, "wb") as out:
            out.write(data)
        os.replace(temporary, path)
    except OSError as error:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass  # 置き換えの前に消えていれば、消す物は無い
        return f"{path} を置けない: {error}"
    return None


def discard_entry(path: str, problem: str) -> None:
    """壊れた entry を名指しの 1 行で除く(呼び手は作り直して足し直す)。"""
    print(f"doeff-hy: bytecode の保存先の entry が壊れている({problem})— {path} を除いて作り直す", file=sys.stderr, flush=True)
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass  # 同時に別の process が除いていれば、消す物は無い


def stored_code(path: str) -> CodeType | None:
    """code の entry を引く(無ければ None)。code として読めない entry は discard_entry で除いて None。macro の記録の照らしは呼び手
    (Hy の source だけ — 今の環境の macro の file と照らす)。"""
    data = read_entry(path)
    if data is None:
        return None
    import marshal  # 当たった時だけ読む

    try:
        code = marshal.loads(data)
    except (EOFError, ValueError, TypeError) as error:
        discard_entry(path, f"marshal で読めない: {error}")
        return None
    if not isinstance(code, CodeType):
        discard_entry(path, f"code object でない: {type(code).__name__}")
        return None
    return code
