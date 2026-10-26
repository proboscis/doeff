"""開発の記録(doeff-commits.md)の完全な例: Claudeとの共著の行を持つcommitを、同じProgramで2通りに数える。

doeffのリポジトリの根で、次の1行で走らせる(PyPIのdoeffだけを使う)。

    uv run --no-project --with "doeff==0.7.0" python publications/zenn-use-cases-v0/examples/commit_share.py
"""

import re  # 共著の行を探す。
import subprocess  # 本番のハンドラで、gitを走らせる。
from dataclasses import dataclass  # 依頼を、書き換えないデータ型で表す。

from doeff import Effect, Pass, Resume, do, handler, run, with_handlers  # 依頼・再開・委譲・実行。

CLAUDE = re.compile(r"^Co-Authored-By: Claude", re.I | re.M)  # 本文の行頭の、Claudeとの共著の行。
MEASURED = "c0dfc3f24"  # 記事の数を数えた、2026年10月18日のmainのcommit。


@dataclass(frozen=True)  # 発行した依頼を書き換えない型にする。
class ReadMessages(Effect):  # 「commitの本文を全部読みたい」という依頼。
    rev: str  # どのcommitまでの履歴を読むか。


@do  # 呼ぶと、数える手順(Program)ができる。
def claude_share(rev):  # 共著の行を持つcommitの数と、全体の数を返す。
    messages = yield ReadMessages(rev)  # commitの本文の一覧を、外側に頼む。
    marked = sum(1 for message in messages if CLAUDE.search(message))  # 共著の行を持つ本文を数える。
    return marked, len(messages)  # 共著の数と、全体の数の組。


@handler  # Programに取り付けられるハンドラにする。
def fixed_log(effect, k):  # テスト用に、決めた3つの本文を答える。
    if isinstance(effect, ReadMessages):  # 本文の依頼には、
        return (yield Resume(k, ["Fix typo",  # 共著の行が無い本文と、
                                 "Add Gather\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>",  # 共著の本文と、
                                 "Refactor\n\nCo-authored-by: Claude Fable 5 <noreply@anthropic.com>"]))  # 小文字の行の本文。
    return (yield Pass(effect, k))  # 担当外の依頼は、外側のハンドラへ渡す。


@handler  # Programに取り付けられるハンドラにする。
def git_log(effect, k):  # 本番用に、今いるリポジトリのgitの履歴から本文を読む。
    if isinstance(effect, ReadMessages):  # 本文の依頼には、
        argv = ["git", "log", "-z", "--format=%B", effect.rev]  # 本文を、NUL文字で区切って出させる。
        out = subprocess.run(argv, capture_output=True, text=True, check=True).stdout  # gitを走らせて読む。
        return (yield Resume(k, out.split("\0")[:-1]))  # 末尾の区切りの後の空を除いた、本文の一覧を返す。
    return (yield Pass(effect, k))  # 担当外の依頼は、外側のハンドラへ渡す。


def test_fixed_log():  # テスト用のハンドラで、3つのうち2つを共著と数える。
    assert run(with_handlers([fixed_log], claude_share("main"))) == (2, 3)  # 大文字と小文字の違う行も数える。


def test_git_log():  # 本番用のハンドラで、記事の数と同じになる。
    assert run(with_handlers([git_log], claude_share(MEASURED))) == (3811, 5566)  # 2026年10月18日までの数。


if __name__ == "__main__":  # ファイルを直接実行した場合だけ数える。
    print(run(with_handlers([fixed_log], claude_share("main"))))  # (2, 3)と表示される。
    print(run(with_handlers([git_log], claude_share(MEASURED))))  # (3811, 5566)と表示される。
