"""root の venv の python が、root の .pyc の置き場を root の木の source だけに使う口。

worker は実行環境の root の木の .pyc を木の中(source の隣の __pycache__)に置かず、root ごとの置き場(root の直下の .doeff-pycache —
env_marker_model の ROOT-PYCACHE)に置く(木を読み専用で置けるように・木の中へ .pyc の hardlink を張らないように)。置き場は root の venv が
持つ: 準備がこの module の隣(root の venv の site-packages)に置き場の path を書いた file(PREFIX_FILE)を置き、この口が起動の時にその
file から sys.pycache_prefix を置く。process の環境変数(PYTHONPYCACHEPREFIX)では運ばない — 環境変数は起こし口ごとに届いたり届かなかったり
する(-I で起こす子は読まない・環境を組み直す子には無い・別の root の子へそのまま継がれる — 2026-11-15 の本番)が、root の venv の python は起こし方に
関わらず site-packages の .pth を読む(-I でも。読まないのは -S だけ)。継いだ PYTHONPYCACHEPREFIX と -X pycache_prefix は、この venv の
置き場で上書きする(file が空・無い venv では置き場を使わない — 別の root の置き場へ書かない)。この module の import の前に .pth の
行が継いだ置き場を外す(この module と、それが import する標準 library の .pyc も継いだ置き場へ書かない — env_prepare_model の
PYCACHE-SCOPE-LINE)。

ただ Python の prefix は process の全部の source に効くので、そのままでは標準 library(sys.base_prefix の下)と venv に入れた package
(sys.prefix の下 — uv sync の --compile-bytecode が隣の __pycache__ に焼いた物)の .pyc も置き場で探し、見つからずに起動のたびに compile
し直す(#3695 — 起動の秒の過半)。この口は importlib の cache_from_source を包み、interpreter と venv の prefix の下の source だけを隣の
__pycache__ に戻す(置き場を使うのはそれ以外 — root の木の source)。

置き方: worker の準備が root の venv の site-packages へこの file を _doeff_pycache_scope.py として写し、`import` の 1 行の .pth
(env_prepare_model の PYCACHE-SCOPE-PTH — 名の順で venv のほかの .pth より先)と空の PREFIX_FILE を置き(env_translation の
WriteImportRoots)、焼く前に PREFIX_FILE へ置き場を書く(CompileTrees)。root の版の doeff に依らない(標準 library だけを import する —
起動の .pth の段で読まれる)。worker の側でこの module を import しても何も包まない(包むのは install を呼んだ時だけ)。
"""

import importlib.util
import os
import sys
from collections.abc import Callable

import _frozen_importlib_external as external


# 層 foundation の文脈と役の名乗り(DOEFF104)— 隣の otlp_post.py と同じ。
MODULE_TAGS = {"context": "doeff-cluster", "role": "foundation"}

#: root の venv の置き場の path を書く file の名(この module の隣 — root の venv の site-packages。中身は置き場の絶対 path の 1 行・
#: 空 = 置き場を使わない venv)。
PREFIX_FILE = "_doeff_pycache_prefix"


def kept_dirs() -> tuple[str, ...]:
    """隣の __pycache__ を使い続ける source の dir の頭(末尾の / 付き): interpreter と venv の prefix(標準 library と入れた package)。
    root の venv が依存のキーごとの共有の venv を使う薄い venv の時、入れた package は共有の venv の下に在る — 薄い venv の bin は
    共有の venv の bin への symlink なので、bin の実の path の親(共有の venv)も足す(普通の venv では prefix そのもの)。"""
    prefixes = (sys.prefix, sys.base_prefix, sys.exec_prefix, sys.base_exec_prefix)
    shared = os.path.dirname(os.path.realpath(os.path.join(sys.prefix, "bin")))
    return tuple(sorted({os.path.join(os.path.abspath(p), "") for p in (*prefixes, shared) if p}))


def scoped(prefixed: Callable[..., str], kept: tuple[str, ...]) -> Callable[..., str]:
    """置き場の在る cache_from_source(prefixed)を、kept の下の source だけ隣の __pycache__ の path に戻す関数にするため(.pyc の名 —
    cache の印と最適化の段 — は prefixed の答えの名をそのまま使う)。"""

    def cache_from_source(path: str | os.PathLike[str], debug_override: bool | None = None, *, optimization: object = None) -> str:
        cached = prefixed(path, debug_override, optimization=optimization)
        source = os.fspath(path)
        if not os.path.abspath(source).startswith(kept):
            return cached
        return os.path.join(os.path.dirname(source), "__pycache__", os.path.basename(cached))

    return cache_from_source


def venv_prefix(site: str) -> str | None:
    """site(この module の在る dir — root の venv の site-packages)の PREFIX_FILE から、venv の置き場を読むため(file が無い・空・
    読めない = None — 置き場を使わない venv)。"""
    try:
        with open(os.path.join(site, PREFIX_FILE), encoding="utf-8") as handle:
            text = handle.read().strip()
    except OSError:
        return None
    return text or None


def install() -> None:
    """この process の .pyc の置き場を、この module の在る venv の置き場にするため: sys.pycache_prefix を PREFIX_FILE の置き場で置き直し
    (継いだ環境変数と -X の値は使わない — 置き場の無い venv では None)、置き場が在れば cache_from_source(importlib の読みの口と
    importlib.util の名の両方)を scoped で包む。包み済みの process では包み直さず、置き場だけ置き直す(site は同じ .pth を 2 度読む事が
    在り — Python 3.14 の venv で見た — 2 度目の .pth の行が外した置き場を戻す)。"""
    sys.pycache_prefix = venv_prefix(os.path.dirname(os.path.abspath(__file__)))
    if not sys.pycache_prefix or getattr(external.cache_from_source, "doeff_pycache_scope", False):
        return
    wrapped = scoped(external.cache_from_source, kept_dirs())
    wrapped.doeff_pycache_scope = True
    external.cache_from_source = wrapped
    importlib.util.cache_from_source = wrapped
