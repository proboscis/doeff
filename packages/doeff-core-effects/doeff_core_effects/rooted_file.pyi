"""rooted_file.hy の公開面の型(file の effect を 1 つの dir の下へ移して撃ち直す答え手 — 型検査を受ける消費者向け)。"""

from collections.abc import Callable

from doeff import Program

def rooted_file_handler(root: str) -> Callable[[Program], Program]: ...
