"""排水の印(``<state_dir>/drain``)の解釈の 1 点(card acp:kanban-issue:ki-b5e0d04de958 D1・設計の改訂 1a)。

「何をもって印ありとするか」はここにだけ置く(今日 = file の在否)。読み手は 2 つで、どちらもこの答えだけを使い、
file の在否や中身を自分で読まない:

* ACP 側の process の ``runtime.drain_port`` — 在る間は新しい手番を受けず capacity 0 を名乗る(ADR-DOE-AGENTS-012 R59)。
* host process の TERM の handler — 停止の拍に 1 回読み、切った行の cause の語を決める
  (``headless_protocol.stop_cause_category``)。

**書く関数を置かない**。印の書き手は doeff の外に居る(pool の pod の preStop・Mac の入れ替えの道具)。host process が
自分で印を作ると、印の無い停止が「計画された停止」の予算へ移る(盲検 B の反例)。書きの API が無いことは
ADR-DOE-AGENTS-012 の針が静的に検める。

``acp/`` の外の module なので、host process(ACP 側の module を import しない)も import してよい。
"""

import os


def declared(path: str | None) -> bool:
    """印が在るか(path が無い = 印を読まない起動 = 印なし)。中身は読まない。"""
    return path is not None and os.path.exists(path)


def reason_line(path: str | None) -> str:
    """印の中身の 1 行目(log と行の散文の尾に足すだけ — 判断には使わない)。読めない・空・path なし = ""。"""
    if path is None:
        return ""
    try:
        with open(path, encoding="utf-8") as handle:
            text = handle.read().strip()
    except (OSError, UnicodeDecodeError):
        return ""
    return text.splitlines()[0] if text else ""
