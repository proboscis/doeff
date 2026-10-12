"""source の中身で引く bytecode の保存先 — 引く・足す・使った印・壊れた entry の扱いの入口はこの module 1 つ(agora-redesign #1753・
#2799・#3858)。

使い手は 5 つで、どれもここだけを通る(保存先の並びと鍵の綴りの複製を作らない):
  - dataclass が型ごとに作る関数の source の compile(:mod:`doeff_hy_bytecode_guard.dataclass_code`): 文の中身で code を引き、
    compile したら足す(card ki-e77d44aa61e5)。
  - import の読みの口(:mod:`doeff_hy_bytecode_guard.loader_hooks`): 作業木の .pyc が使えない Hy の module の code を、鍵に並ぶ entry の
    記録を照らして引き(:func:`current_code`)、compile したら今の環境の entry として足す(:func:`add_code`)。
  - .pyc の記録の照らしの source の読み(:mod:`doeff_hy_bytecode_guard.bound_source`): macro の閉包の digest の歩みが、辿れない大域を
    覆う source の文の読み(中身と名で決まる純な読み)を引き、無ければ解いて足す(agora-redesign card ki-38508e6e56fd — 新しい
    process の照らしが、同じ中身の source を毎回解き直していた)。
  - .pyc の記録の照らしの code の読み(:mod:`doeff_hy_bytecode_guard.macro_use`): macro の閉包の digest の歩みが読む関数の code の
    読み(名の表・文字列の定数・import・digest の形 — code の中身で決まる純な読み)を、歩み 1 つの entry に並べて引き、読み直した
    時は足す(agora-redesign card ki-be596ac8276f)。
  - doeff-cluster の worker の bytecode の道具(worker/entry/code_prepare.hy と foundation/bytecode_pool.hy): 版をまたいで、中身の同じ
    source の code(:func:`current_code`・:func:`add_code`)と import の名を引き、無い物だけを compile して足す。道具は準備する root の venv の python で走るので、root の版の
    doeff-hy でなく worker の版のこの file を path で読む — だから標準 library だけを import する(この package の他の module も読まない)。

保存先の dir = 環境変数 DOEFF_HY_CODE_STORE(``off`` = 使わない・無ければ ``$XDG_CACHE_HOME/doeff-hy/code-store``)。worker の起動の
script は既定を ``$WORK_DIR/state/doeff-hy-code-store`` に置く(日次の全体検証の task と同じ dir)。

並び: ``<dir>/<鍵の頭 2 字>/<鍵の残り><種類の末尾>``。種類 = ``.code``(marshal した code object — dataclass の関数の code)と
``.imports``(worker の道具の閉包の歩みが読む import の名 — 形は道具が持つ)と ``.binding`` ・ ``.facts``(.pyc の記録の照らしの source の
読みと code の読み — 形は bound_source・macro_use が持つ)。鍵は source の中身の sha256 と、中身の読みを変える物(:func:`code_key` の欄)
だけで決まり、path と版(commit)に依らない。

source の code(:func:`code_key` の鍵 — import の口と worker の道具)は、鍵 1 つの dir に macro の環境ごとの entry を幾つか持つ:
``<dir>/<鍵の頭 2 字>/<鍵の残り>/<記録の指紋>.code``(card ki-6828e9af04b4)。Hy の source は展開が依った macro の記録を code に持ち、
引いた側が今の環境の macro で照らす(照らしは呼び手 — :func:`current_code`)。記録の指紋(:func:`code_fingerprint`)は使えるかを決める
物の全部なので、記録の同じ code は 1 つの entry を書き直し、記録の違う code(doeff の版の違う作業木の import と実行環境の準備)は並ぶ。
鍵 1 つに entry 1 つの並び(鍵の印 2)では、版の違う書き手が同じ鍵の entry を互いに古いと見て compile し直し、上書きし合った(2026-10-12
zeus — 実行環境の準備は同じ版の source でも 1 回に 100〜600 本を焼き直した)。鍵 1 つの entry は :data:`CODE_ENTRIES_PER_KEY` 個までで、
越えたら時刻の古い物から除く。足したばかりの entry は、鍵の他の entry より古い時刻に置く(:func:`add_code`)— 誰かが照らして使うまで
並びの後ろに居て、上限を越えた時に先に除かれる。だから一度きりの環境(macro を直している作業木の検)の entry は互いを除き合い、使い
続けている書き手の entry を押し出さない。上限を越える数の環境が同じ鍵を順に使っても、外れるのは上限を越えた分だけ(全部が外れる崖に
ならない — 設計検証 lt-Y2ZEDKRFVZR3S6T34PNQZ28Z97 の盲検 A)。

使った印: 引いて当たるたびに entry の時刻を今にする(source の code は、照らして使う entry だけ)。worker の掃除(env_store の
sweep-leftovers)は 7 日使われない entry と、中が全部 7 日動いていない dir を消す — native の wheel の保存先(.used の印で dir の時刻を
進める)と同じ 7 日の作法。書きは同じ dir の一時の file に書いてから置き換える(同時に書く別の process と混ざらず、書きかけを読ませない)。

壊れた entry(code として読めない・import の名の形が違う)は使わない: 名指しの 1 行を stderr に出して entry を除き、呼び手は作り直して
足し直す(黙って壊れた物を使わない・黙って捨てない)。
"""

import os
import sys
from collections.abc import Callable
from types import CodeType

#: 保存先の dir を指す環境変数(値 = dir の path・``off`` = 使わない)。
STORE_ENV = "DOEFF_HY_CODE_STORE"

#: code の entry の鍵の印(形を変えたら末尾の番号を上げる — 古い鍵の entry は当たらなくなる)。2 = 鍵を compile した bytes そのものから
#: 作る版(agora-redesign #2799)。1 の版の保存先には、compile の後に file を読み直した鍵の下に古い中身の code が入った entry が在り得る
#: ので、新しい版からは引かない。3 = 鍵 1 つの dir に macro の環境ごとの entry を並べる版(card ki-6828e9af04b4)。2 の entry(鍵 1 つに
#: 1 つの file)は前の版の書き手だけが使い、新しい版からは引かない — 前の版の書き手と取り合わず、7 日使われなければ掃除が消す。
CODE_TAG = "doeff-hy/code-store/3"

#: source の code の鍵 1 つに持つ entry の数の上限(足して越えたら、時刻の古い物から除く)。上限は「照らして使い続けている entry」を
#: 何個まで同時に守るかの数 — 足しただけでまだ使われていない entry は後ろに並ぶ(:func:`add_code`)ので、一度きりの環境の数には依らない。
#: 使い続ける環境が上限を越えると、越えた分の環境が毎回外れる(全部は外れない)。多すぎると、どの entry にも合わない新しい環境の読みが、
#: compile の前に全部を照らす手間が増える。
CODE_ENTRIES_PER_KEY = 16

#: 足したばかりの entry の時刻を、鍵の他の entry より前に置く時の下限(今からこれより前には置かない)。掃除の 7 日よりずっと短く — 足した
#: entry が、照らして使われないまま掃除の閾を越えた時刻に置かれて、すぐ消されないため。
NEW_ENTRY_FLOOR_SECONDS = 24 * 3600

#: entry の種類の末尾。
CODE_SUFFIX = ".code"
IMPORTS_SUFFIX = ".imports"
#: macro の閉包の digest の歩みが、辿れない大域を覆う source の文の読み(doeff_hy_bytecode_guard.bound_source — 中身と名で決まる
#: 純な読み。形は bound_source が持つ)。
BINDING_SUFFIX = ".binding"
#: macro の閉包の digest の歩みが読む code object の読み(doeff_hy_bytecode_guard.macro_use — code の中身で決まる純な読み。形は
#: macro_use が持つ)。
FACTS_SUFFIX = ".facts"

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


def store_is_named() -> bool:
    """保存先の dir を環境変数 STORE_ENV で名指しているか(``off`` と空は名指しでない)— 名指しの保存先は、木へ .pyc を書かない
    設定でも足す(書かない設定は木への書きの決めで、木の外の保存先の決めではない・agora-redesign #3737)。名指しの無い既定の dir
    (利用者の cache)には、書かない設定では足さない。"""
    configured = os.environ.get(STORE_ENV, "").strip()
    return configured not in ("", "off")


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


def _plain(value: object) -> bool:
    """value が記録の形の値か — 文字列・bytes・整数・浮動小数・None と、それらの組の入れ子(repr が process と木に依らない値 —
    frozenset のように並びが hash に依る値は入れない)。記録の欄が後の版でこの形を外れると、記録の違う code が 1 つの決まった名に
    潰れて上書きし合うので、記録に足す欄はこの形で書く。"""
    match value:
        case str() | bytes() | int() | float() | None:
            return True
        case tuple():
            return all(_plain(item) for item in value)
        case _:
            return False


def code_fingerprint(code: CodeType) -> str:
    """source の code の entry の名 — code の定数の末尾(doeff-hy が macro の記録を置く所 — :mod:`doeff_hy_bytecode_guard.records`)の
    sha256 の頭 32 字。記録は entry を使えるかを決める物の全部(Hy の版・使った macro と digest・…)なので、記録の同じ code は同じ名
    (compile した木の path は記録に入らない)、記録の違う code は違う名になる。記録の欄の意味は読まない(:func:`_plain` の形なら同じ
    作り方)— worker の道具は root の版の記録を、worker の版のこの file で名にするので、記録の印(RECORD_TAG)の違う版の
    記録にも同じ名の作り方が効く。末尾の定数がその形でない code(記録の無い code — Python の source)は 1 つの決まった名。"""
    import hashlib  # 保存先へ足す時だけ読む

    last = code.co_consts[-1] if code.co_consts else None
    text = repr(last) if isinstance(last, tuple) and _plain(last) else ""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


def _code_dir(store: str, key: str) -> str:
    """source の code の鍵 1 つの entry を並べる dir(鍵の頭 2 字を dir に分ける — 他の種類と同じ)。"""
    return os.path.join(store, key[:2], key[2:])


def _used_at(item: os.DirEntry[str]) -> int | None:
    """entry の使った時刻(ns)— 一覧の後で別の process が除いた entry は None(もう無い物)。"""
    try:
        return item.stat(follow_symlinks=False).st_mtime_ns
    except FileNotFoundError:
        return None


def _stamped_entries(store: str, key: str) -> tuple[tuple[int, str], ...]:
    """source の code の鍵 key の entry の #(時刻 ns path) の列 — 時刻の新しい順(同じ時刻は名の順)。鍵の dir が無ければ空。書きかけの
    一時の file(末尾が CODE_SUFFIX でない)は数えない。読めない dir は名指しの 1 行を出して空(呼び手は compile する)。"""
    directory = _code_dir(store, key)
    try:
        with os.scandir(directory) as listing:
            stamped = [(_used_at(item), item.path) for item in listing if item.name.endswith(CODE_SUFFIX)]
    except FileNotFoundError:
        return ()
    except OSError as error:
        print(f"doeff-hy: bytecode の保存先の鍵の dir を読めない({error})— {directory} を使わずに作り直す", file=sys.stderr, flush=True)
        return ()
    return tuple(sorted(((at, path) for at, path in stamped if at is not None), key=lambda pair: (-pair[0], pair[1])))


def code_entries(store: str, key: str) -> tuple[str, ...]:
    """source の code の鍵 key の entry の path の列 — 時刻の新しい順(照らして使った entry が前・足しただけの entry は後ろ — 読み手は
    この順に照らす)。鍵の dir が無ければ空。"""
    return tuple(path for _, path in _stamped_entries(store, key))


def current_code(store: str, key: str, is_current: Callable[[CodeType], bool]) -> CodeType | None:
    """source の code の鍵 key の entry のうち、is_current(記録を今の環境の macro で照らす — 呼び手が持つ)が真の最初の 1 つ(使った
    時刻の新しい順に照らす・無ければ None)。使った印は使う entry にだけ付ける — 照らして合わなかった entry の時刻は進めない(数の上限で
    除く順と掃除の 7 日を、その entry を使う書き手の使いだけで決める)。壊れた entry は :func:`stored_code` が名指して除き、次を照らす。"""
    for path in code_entries(store, key):
        code = stored_code(path, touch=False)
        if code is not None and is_current(code):
            touch_entry(path)
            return code
    return None


def add_code(store: str, key: str, code: CodeType) -> str | None:
    """code を source の code の鍵 key の entry に足す(名 = :func:`code_fingerprint` — 記録の同じ entry は書き直す)。足した entry の時刻は
    鍵の他の entry のどれよりも前に置き(今から :data:`NEW_ENTRY_FLOOR_SECONDS` より前にはしない)、照らして使われた時に初めて前へ出す。
    足した後に鍵の entry が :data:`CODE_ENTRIES_PER_KEY` 個を越えたら、今足した entry を残して、時刻の古い物から除く — 除かれるのは、
    足しただけで使われていない entry と、使われなくなって久しい entry。答え = 書けなかった理由(:func:`write_entry` と同じ — 書けたら
    None)。"""
    import marshal  # 保存先へ足す時だけ読む

    path = os.path.join(_code_dir(store, key), code_fingerprint(code) + CODE_SUFFIX)
    problem = write_entry(path, marshal.dumps(code))
    if problem is not None:
        return problem
    others = tuple(pair for pair in _stamped_entries(store, key) if pair[1] != path)
    _place_after(path, others)
    for _, stale in others[CODE_ENTRIES_PER_KEY - 1 :]:
        try:
            os.unlink(stale)
        except FileNotFoundError:
            pass  # 同時に別の process が除いていれば、消す物は無い
    return None


def _place_after(path: str, others: tuple[tuple[int, str], ...]) -> None:
    """足した entry の時刻を、鍵の他の entry(others — 時刻の新しい順)のどれよりも 1 秒前に置く(今から NEW_ENTRY_FLOOR_SECONDS より前には
    しない)。置けない時は名指しの 1 行(entry は使える — 並びの頭に居るだけ)。"""
    import time  # 保存先へ足す時だけ読む

    now = time.time_ns()
    oldest = others[-1][0] if others else now
    at = max(now - NEW_ENTRY_FLOOR_SECONDS * 1_000_000_000, oldest - 1_000_000_000)
    try:
        os.utime(path, ns=(at, at))
    except OSError as error:
        print(f"doeff-hy: bytecode の保存先の足した entry の時刻を置けない({error})— {path}", file=sys.stderr, flush=True)


def touch_entry(path: str) -> None:
    """entry の時刻を今にする(使った印 — 掃除が使っている entry を消さない)。進められない時は名指しの 1 行(中身は使える)。"""
    try:
        os.utime(path)
    except OSError as error:
        # 読んだ中身は使える。時刻を進められないと、掃除がこの entry を 7 日で消す(次に要る時に作り直すだけ)。
        print(f"doeff-hy: bytecode の保存先の entry の時刻を進められない({error})— {path}", file=sys.stderr, flush=True)


def read_entry(path: str, *, touch: bool = True) -> bytes | None:
    """entry の中身を読む(無ければ None)。無い以外の理由で読めない entry は名指しの 1 行を出して None(呼び手は作り直す)。当たれば
    entry の時刻を今にする(touch が偽なら進めない — 照らしてから使う呼び手が :func:`touch_entry` で進める)。"""
    try:
        with open(path, "rb") as entry:
            data = entry.read()
    except FileNotFoundError:
        return None
    except OSError as error:
        print(f"doeff-hy: bytecode の保存先の entry を読めない({error})— {path} を使わずに作り直す", file=sys.stderr, flush=True)
        return None
    if touch:
        touch_entry(path)
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


def stored_code(path: str, *, touch: bool = True) -> CodeType | None:
    """code の entry を引く(無ければ None)。code として読めない entry は discard_entry で除いて None。macro の記録の照らしは呼び手
    (Hy の source だけ — 今の環境の macro の file と照らす)。touch の意味は :func:`read_entry` と同じ。"""
    data = read_entry(path, touch=touch)
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
