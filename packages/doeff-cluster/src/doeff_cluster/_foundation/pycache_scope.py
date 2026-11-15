"""root の venv の python が、root の .pyc の置き場(PYTHONPYCACHEPREFIX)を root の木の source だけに使う口。

worker は実行環境の root の木の .pyc を木の中(source の隣の __pycache__)に置かず、root ごとの置き場(root の直下の .doeff-pycache —
env_marker_model の ROOT-PYCACHE)に置き、root の venv で起きる子(job・入口の検め・待ちの子・焼きの道具・準備の確かめ)に
PYTHONPYCACHEPREFIX でその置き場を渡す(木を読み専用で置けるように・木の中へ .pyc の hardlink を張らないように)。ただ Python の prefix は
process の全部の source に効くので、そのままでは標準 library(sys.base_prefix の下)と venv に入れた package(sys.prefix の下 — uv sync の
--compile-bytecode が隣の __pycache__ に焼いた物)の .pyc も置き場で探し、見つからずに起動のたびに compile し直す(#3695 — 起動の秒の過半)。
この口は importlib の cache_from_source を包み、interpreter と venv の prefix の下の source だけを隣の __pycache__ に戻す(置き場を使うのは
それ以外 — root の木の source)。PYTHONPYCACHEPREFIX の無い process では何もしない。

置き方: worker の準備が root の venv の site-packages へこの file を _doeff_pycache_scope.py として写し、`import` の 1 行の .pth
(env_prepare_model の PYCACHE-SCOPE-PTH — 名の順で venv のほかの .pth より先)を置く(env_translation の WriteImportRoots)。root の版の
doeff に依らない(標準 library だけを import する — 起動の .pth の段で読まれる)。worker の側でこの module を import しても何も包まない
(包むのは install を呼んだ時だけ)。
"""

import importlib.util
import os
import sys
from collections.abc import Callable

import _frozen_importlib_external as external


# 層 foundation の文脈と役の名乗り(DOEFF104)— 隣の otlp_post.py と同じ。
MODULE_TAGS = {"context": "doeff-cluster", "role": "foundation"}


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


def install() -> None:
    """この process の cache_from_source(importlib の読みの口と importlib.util の名の両方)を scoped で包むため。置き場の無い process・
    包み済みの process では何もしない。"""
    if not sys.pycache_prefix or getattr(external.cache_from_source, "doeff_pycache_scope", False):
        return
    wrapped = scoped(external.cache_from_source, kept_dirs())
    wrapped.doeff_pycache_scope = True
    external.cache_from_source = wrapped
    importlib.util.cache_from_source = wrapped
