"""Per-kind defhandler modules (ADR-DOE-AGENTS-004 R2, C2).

kind 追加 = このディレクトリに defhandler モジュール 1 個 + kind スキーマ +
conformance green。ここは substrate-clean 領域 — 生 IO(子プロセス起動・
DB 直読み・ファイル入出力・シェル実行)は禁止で、`.semgrep.yaml` の
``doeff-agents-substrate-clean-impls`` が静的に執行する(rule は生成系の
禁止バイト列を全文走査するため、この docstring も該当語の直書きを避ける)。
protocol 物理は substrate effect(SessionStore / Tmux / Fs / Env / Clock /
Proc)の yield のみで表現する。
"""

import hy  # noqa: F401
