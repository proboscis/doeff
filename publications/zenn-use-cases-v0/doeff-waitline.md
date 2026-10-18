---
title: "Claude Codeが止まる前に、待っている物を1行書かせる"
emoji: "⏳"
type: "tech"
topics: ["claudecode", "python", "doeff", "ai", "設計"]
published: false
---

Claude Codeの会話を何十も並べると、止まった会話のどれが自分の番なのか、画面からは分かりません。筆者は、ターンの最後の行に待っている物を書かせ、書かずに止まる時はStop hookで1度だけ止めています。2026年10月18日に数えると、直近24時間に止まった会話520個のうち、筆者を待つのは16個でした。

止まった会話は、どれも同じ「入力待ち」に見えます。

- 筆者の判断を待つ会話と、CIや別の会話の返事を待つ会話が混ざります。
- 自分の次の作業を残したまま、止まっている会話もあります。
- 全部を開いて最後の返事を読むと、確かめるだけで時間が消えます。

この記事は、Claude CodeのStop hookで、ターンの終わりに待ちの1行を書かせた記録です。仕組みの全体は、5本目「[1人でClaude Codeを最大50並べたら、詰まるのは人だった](doeff-fleet.md)」です。hookの話は、8本目「[CLAUDE.mdに決まり851本を書かず、hookで要る時だけ見せる](doeff-hooks.md)」です。連載が初めての方は[1本目の入門](doeff-main.md)から、コードは[GitHub](https://github.com/proboscis/doeff)から読めます。

## まず短いコード — 最後の行にWAITが無ければ、1度だけ止める

Stop hookは、AIが返事を書き終えて止まる直前に呼ばれる命令です。標準入力のJSONには、最後の返事の文`last_assistant_message`が入っています。命令が`"decision": "block"`を返すと、Claude Codeは止まらずに、添えた理由を読んで続けます。

```python
import itertools  # 最後の行から上へ、WAITの行が続く間だけ読む。
import json  # hookに届く出来事のJSONを読み、答えをJSONで書く。
import re  # 返事の行が、WAITの行の形かを見る。
import sys  # 標準入力から出来事を読む。

WAIT_LINE = re.compile(r"^WAIT:\s*(user:(?:decide|review|discuss)|work)\s*[—-]\s*(.+)$")  # 待ちの1行の形。区切りは「—」か「-」。
GUIDE = ("止まる前に、何を待っているかを最後の行に書いてください。\n"  # 止めた時に、AIへ返す文。
         "WAIT: user:decide — <あなたに決めてほしい事>\n"  # 人の判断を待つ時の書き方。
         "WAIT: work — <待っているCIや、別の会話の名>")  # CIや別の会話を待つ時の書き方。


def waits_of(reply):  # 返事の末尾に続くWAITの行を、上から順の組にする。
    matches = (WAIT_LINE.match(line.strip()) for line in reversed(reply.strip().splitlines()))  # 最後の行から上へ見る。
    tail = list(itertools.takewhile(bool, matches))  # WAITでない行に着いたら止める。本文の途中の行は拾わない。
    return [found.groups() for found in reversed(tail)]  # 例: [("user:decide", "AかBか")]


def on_stop(event):  # Stopの出来事1つから、止まらせてよいかを決める。
    if event.get("stop_hook_active"):  # このhookで1度止めた後の停止なら、
        return None  # 必ず通す。止め続けると、ターンが終われなくなる。
    if waits_of(event.get("last_assistant_message", "")):  # 最後の行にWAITがあれば、
        return None  # 何も返さず、止まらせる。
    return {"decision": "block", "reason": GUIDE}  # 無ければ1度だけ止め、書き方を教える。


if __name__ == "__main__":  # Claude Codeから呼ばれた時だけ、標準入出力を使う。
    answer = on_stop(json.load(sys.stdin))  # 標準入力の出来事から決める。
    if answer:  # 止める時だけ書く。
        print(json.dumps(answer, ensure_ascii=False))  # 標準出力のJSONを、Claude Codeが読む。
```

- 待ちの1行は、`WAIT: <種類> — <理由>`の形です。
- 種類は4つです。筆者を待つ`user:decide`・`user:review`・`user:discuss`と、外を待つ`work`です。
- 行が無ければ、書き方の文を返して1度だけ止めます。

`stop_hook_active`は、Stop hookで止めた後の停止で`true`になる欄です。ここで通すので、AIが行を書けない時もターンは終わります。

hookは、Claude Codeの設定ファイルで出来事に結びつけます。上のコードを`.claude/hooks/wait_line.py`に置いた時の例です。

```json
{
  "hooks": {
    "Stop": [
      {
        "hooks": [{"type": "command", "command": "uv run --no-project python .claude/hooks/wait_line.py"}]
      }
    ]
  }
}
```

手で出来事を渡すと、次のように答えます。

```bash
echo '{"last_assistant_message": "直しました。"}' | uv run --no-project python .claude/hooks/wait_line.py  # 行が無いので、止める答えが1行出る。
echo '{"last_assistant_message": "直しました。\nWAIT: work — CIの結果"}' | uv run --no-project python .claude/hooks/wait_line.py  # 何も出ない。
echo '{"stop_hook_active": true, "last_assistant_message": "直しました。"}' | uv run --no-project python .claude/hooks/wait_line.py  # 2度目は通す。
```

## 全体の図 — 1行で、止まった会話を3つに分ける

![止まった会話を、最後の1行で3つに分ける](/images/zenn-use-cases-v0/generated/waitline-concept.png)
*1行が無いと、止まった会話はどれも同じに見えます。1行を読めば、筆者の番・外を待つ・続けてよい、の3つに分けられます。*

1行を書かせると、止まった会話を機械が3つに分けられます。

| 最後の行の例 | 意味 | 扱い |
| --- | --- | --- |
| `WAIT: user:decide — AかBか` | 筆者の判断を待つ | 筆者の番の一覧に出す |
| `WAIT: work — CIの結果` | 外から届く物を待つ | 一覧から外し、届くのを待つ |
| `WAIT: work — 単位2を続ける` | 外を待たず、自分の作業が残る | 人の代わりに「続けて」を送る |

筆者の画面は、1つ目だけを筆者の番として並べます。2つ目は、届いた返事や知らせが、その会話の次のターンを始めます。3つ目は、後の節で書きます。

## 種類を、閉じた4語にする

この1行は、2026年7月18日に、筆者の手元の会話から始めました。最初は、`user`に種類を付けるかは任意でした。同じ日のうちに、必ず付ける形にしました。筆者にしてほしい事で、筆者の手間が違うからです。

| 種類 | 筆者にしてほしい事 | 筆者の手間 |
| --- | --- | --- |
| `user:decide` | 選ぶ・許す | 短い・答えれば会話はすぐ動く |
| `user:review` | 出来た物を見る | 読む時間が要る |
| `user:discuss` | 議論する | まとまった時間が要る |
| `work` | 無い | 0 |

種類の語は、この4つに閉じています。AIが別の語を書くと、行と読まずに止めます。読み方の決まりは、例の表にして試験にします。

```python
CASES = [  # 返事の全文と、読めるべき行の組。
    ("直しました。\n\nWAIT: work — CIの結果", [("work", "CIの結果")]),  # 最後の1行を読む。
    ("済みました。\nWAIT: user:decide — AかBか\nWAIT: work — 別の会話の返事",  # 末尾に続く2行は、
     [("user:decide", "AかBか"), ("work", "別の会話の返事")]),  # 上から順に2つ読む。
    ("前は WAIT: user:decide — AかB で終えた。\n今は続きを進めています。", []),  # 本文の途中の行は読まない。
    ("見てください。\nWAIT: user — 出来た物", []),  # 種類の無いuserは、行と読まない。
    ("", []),  # 空の返事も、行が無いと読む。
]


def test_read_and_stop():  # 読み方と止め方を、同じ表で確かめる。
    for reply, expected in CASES:  # 例を1つずつ見る。
        assert waits_of(reply) == expected, reply  # 読めた行が、表と同じ。
        first = on_stop({"stop_hook_active": False, "last_assistant_message": reply})  # 1度目の停止。
        assert (first is None) == bool(expected), reply  # 行が無い時だけ止める。
        assert on_stop({"stop_hook_active": True, "last_assistant_message": reply}) is None, reply  # 2度目は通す。
```

- 本文の途中の`WAIT:`は読みません。前のターンの話を引いただけの事があるからです。
- 末尾に続く行は、上から順に全部読みます。筆者と別の会話の両方を待つ事もあります。
- 2度目の停止は、行が無くても必ず通します。

本物では、書かせる側と読む側が、同じ例の表を読んで試験をします。書かせる側はClaude Codeのhookで、読む側は会話を並べる仕組みです。2つの読み方が、黙ってずれないためです。本物は、行の前後の`**`などの飾りも外して読みます。

## 1度だけ止め、2度目は通す

止めるのは、1回の停止につき1度だけです。止め続けると、行を書けない会話がいつまでも止まれません。

- モデルのAPIが落ちた時や、文脈が尽きた時は、AIは行を書けません。
- 止め続けると、同じ書き直しを何度も頼み、トークンを使い続けます。
- 行が無いまま通った会話は、何も待っていない会話として残ります。

止めた時に返す文には、書き方の例を全部入れます。AIは、その文だけを読んで書き直せます。

## 読む側 — ターンの終わりを、doeffのProgramにする

止まった返事は、会話を並べる仕組みが読みます。読んだら、その会話の今の待ちを置き換えます。外を待たない`work`だけなら、人の代わりに「続けて」を1通置きます。

ここでdoeffを使います。doeffは、処理の中の「書く」「送る」といった操作を、外側の部品で差し替えられるPythonのライブラリです。処理は操作を外側へ依頼するだけで、依頼に答える部品をハンドラと呼びます。

```python
from dataclasses import dataclass  # 依頼を、書き換えない値にする。

from doeff import Effect, Pass, Resume, do, handler, run, with_handlers  # 依頼・再開・委譲・実行を使う。

PARTY = re.compile(r"@[\w.-]+|(?<![A-Za-z])CI(?![A-Za-z])|(?<!この)会話|利用者|配備|ジョブ")  # 外の相手を名で書いた印。


@dataclass(frozen=True)  # 一度出した依頼を、途中で書き換えない。
class DeclareWait(Effect):  # 「この会話の今の待ちを、この行で置き換えたい」という依頼。
    waits: tuple  # 読めたWAITの行。空なら、待ちの解消。


@dataclass(frozen=True)  # 一度出した依頼を、途中で書き換えない。
class PostContinue(Effect):  # 「同じ会話へ、続きの入力を1通置きたい」という依頼。
    body: str  # 置く入力の本文。


@do  # 呼ぶと、ターンの終わりの手順(Program)ができる。
def close_turn(reply):  # 終わったターンの返事1つを、待ちの申告と続きに変える。
    waits = tuple(waits_of(reply))  # 最後の行を読む。
    yield DeclareWait(waits)  # 会話の今の待ちを、丸ごと置き換える。
    alone = [reason for kind, reason in waits if kind == "work" and not PARTY.search(reason)]  # 相手の無いwork。
    if waits and len(alone) == len(waits):  # 全部が、外の相手を書かないworkなら、
        yield PostContinue("続けて — " + "・".join(alone))  # 人の代わりに「続けて」を置く。
    return waits  # 読めた行を返す。


def fake_board(board, posted, session):  # 試験用: 申告と続きを、メモリに書くハンドラ。
    @handler  # Programに取り付けられるハンドラにする。
    @do  # ハンドラもyieldで、再開や委譲を指示する。
    def answer(effect, k):  # effectは届いた依頼、kは止まった処理の続き。
        if isinstance(effect, DeclareWait):  # 待ちの申告は、
            board[session] = effect.waits  # 会話ごとの表を置き換える。
            return (yield Resume(k, None))  # 処理の続きを動かす。
        if isinstance(effect, PostContinue):  # 続きの入力は、
            posted[session] = effect.body  # 置いた物として記録する。
            return (yield Resume(k, None))  # 処理の続きを動かす。
        return (yield Pass(effect, k))  # 担当外の依頼は、外側のハンドラへ渡す。
    return answer  # 表を閉じ込めたハンドラを返す。


def test_sort_and_continue():  # 3つの会話の返事を、あなたの番・外を待つ・続けるに分ける。
    board, posted = {}, {}  # 会話ごとの今の待ちと、置いた続きの入力。
    replies = {"a": "案を2つ書きました。\nWAIT: user:decide — AかBか",  # 筆者の判断を待つ会話。
               "b": "マージの列に載せました。\nWAIT: work — CIの結果",  # CIを待つ会話。
               "c": "単位1を終えました。\nWAIT: work — 単位2をこの会話で続ける"}  # 自分の次の作業を書いた会話。
    for session, reply in replies.items():  # 会話ごとに、ターンの終わりを処理する。
        run(with_handlers([fake_board(board, posted, session)], close_turn(reply)))  # 試験用のハンドラで動かす。
    assert [s for s, waits in board.items() if any(k.startswith("user") for k, _ in waits)] == ["a"]  # 筆者の番はaだけ。
    assert posted == {"c": "続けて — 単位2をこの会話で続ける"}  # 続きはcにだけ置く。
```

- `close_turn`は、待ちの申告と続きの入力を、外側へ依頼するだけです。
- `@do`を付けた関数は、呼ぶと実行の手順(`Program`)を返します。
- ハンドラは、届いた依頼`effect`と、止まった処理の続き`k`を受け取ります。
- `Resume(k, None)`で続きを動かし、担当外の依頼は`Pass`で外側へ渡します。

![ターンの終わりから、次のターンまで](/images/zenn-use-cases-v0/generated/waitline-flow.png)
*書かせる側は、行が無ければ1度だけ止めます。読む側は、待ちを置き換え、外を待たないworkだけなら続きを置きます。*

本番では、同じ2つの依頼を、連絡の受付へ送るハンドラが受けます。`close_turn`は1字も変えません。本物はPythonの上で動くLispのHyで書き、doeffを使っています。

## 「work」で止まる会話に、人の代わりに「続けて」を送る

`work`を足した後も、止まったままの会話が残りました。2026年10月9日には、2つの会話が`WAIT: work — 次の単位を続ける`の形で止まりました。どちらも、見ていた人が「続けて」と送るまで動きませんでした。

`work`は、外から何かが届くのを待つ申告です。自分の次の作業しか無ければ、届く物はありません。誰も起こさないので、会話は止まったままになります。

そこで、同じ日に2段で直しました。

1. Stop hookの規則を1本足しました。外の相手を書かない`work`で止まろうとすると、1度止めて「その場で続けてください」と返します。
2. それでも止まった時は、仕組みが同じ会話へ「続けて — <理由>」を1通置きます。上の`PostContinue`です。

外の相手を書いたかは、印の表で見ます。`@名前`・会話のid・「〜からの」・利用者・CI・配備などです。表は閉じていて、足す時は表を直します。

## 何が変わったか

![待ちの申告2,644個を、最後に申告した日ごとに分ける](/images/zenn-use-cases-v0/generated/waitline-days.png)
*会話ごとに、最後のターンの1行だけが残ります。10月11日までの「見てほしい」は、1週間後も残っていました。*

| 測った物 | 値 | 測った日と範囲 |
| --- | --- | --- |
| 待ちを申告している会話 | 2,644個 | 2026年10月18日20時14分(日本時間)の時点の全部・最も古い申告は10月9日 |
| 直近24時間に止まった会話 | 520個 | 10月17日20時14分〜18日20時14分 |
| そのうち、筆者を待つ会話 | 16個(決めてほしい13・議論3) | 同上 |
| Stop hookが呼ばれた停止 | 9,467回 | 1つの実行環境の会話の記録2,854本・10月5日〜18日 |
| WAITの行が無く止めた | 8回(0.08%) | 同上 |
| 外の相手を書かない`work`で止めた | 353回(3.7%) | 同上・規則は10月9日から |
| 人の代わりに置いた「続けて」 | 15通 | 同上 |

- 直近24時間に止まった会話のうち、筆者を待つのは3%でした。残りの504個は、1行を読めば開かずに済みます。
- AIは、ほとんどの停止で行を書きます。行が無く止めたのは、9,467回のうち8回でした。
- 多かったのは、外を待たない`work`の方です。353回止め、それでも止まった会話に15通の「続けて」を置きました。
- 行の形は守れても、意味の取り違えは残ります。

## その後に足した物 — 状態の1行

2026年10月15日からは、WAITの行の直前に、もう1行を求めています。`status: done — 直しをmainに入れた`の形です。語は、done・decide・alert・blockedの4つです。会話の一覧の画面が、会話を1行にまとめて見せるためです。

この行も、無ければ同じStop hookが1度だけ止めます。10月15日から18日に、状態の行が無く止めたのは33回でした。

## うまくいかなかったこと

- 「見てほしい」の申告は溜まりました。申告は、その会話が次のターンを終えるまで残ります。筆者が見ても、返事をしなければ消えません。10月9日〜11日の119個が、18日にも残っていました。筆者は10月11日に、この申告をやめました。出来た物を見るのは後からの任意にし、開発を止めないためです。
- 印の表には、最初は「会話」の2字も入っていました。10月10日、ある会話が「〜をこの会話で続ける」で止まりました。「会話」が印に当たり、外を待つ申告と読まれました。続きは置かれず、18分止まりました。10月12日からは、前が「この」の「会話」を数えません。上の`PARTY`の`(?<!この)会話`が、その直しです。
- 書いた行が正しいかは、確かめていません。`WAIT: work — CIの結果`と書いても、本当にCIを待つかは見ません。
- 止めなければ何が起きたかは、分かりません。数えたのは、止めた回数だけです。

## まとめ

- 止まった会話が何を待つかは、画面からは分かりません。AIに最後の1行で書かせます。
- 種類は閉じた4語にし、行が無ければStop hookで1度だけ止めます。2度目は必ず通します。
- 読み方は例の表にし、書かせる側と読む側が、同じ表を試験で読みます。
- 読む側はdoeffのProgramにし、申告と続きを依頼にします。試験ではメモリの表が、本番では受付が答えます。
- 外を待たない`work`には、人の代わりに「続けて」を置きます。

[doeffとは？：メイン記事へ戻る](doeff-main.md)

## 参考

この記事のコードは、2026年10月18日に、PyPIのdoeff 0.7.0で確かめました。Pythonは3.12と3.14で試しました。GitHubにあるdoeffのmain(commit `2d92f63`)でも、同じく動きました。Pythonの枠は、上から順に前の枠の続きとして動きます。`doeff`のパッケージだけが要ります。

完全な例を`turn_wait.py`として保存し、次のコマンドで実行します。

```bash
uv run --no-project --with "doeff==0.7.0" python turn_wait.py  # PyPIのdoeffを入れて、完全な例の2つの試験を走らせる。
```

- [Claude Codeのhooksの説明(公式)](https://code.claude.com/docs/en/hooks)
- [doeff本体(この記事で確かめた版)](https://github.com/proboscis/doeff/tree/2d92f636b2acf457053ee4eb5c2a6ed6fe85c763)
- [この記事のコードをまとめた完全な例](examples/turn_wait.py)
- 関連する回:[CLAUDE.mdに決まり851本を書かず、hookで要る時だけ見せる](doeff-hooks.md)
- 関連する回:[1人でClaude Codeを最大50並べたら、詰まるのは人だった](doeff-fleet.md)
- 関連する回:[Claude Codeに仕事を配らせるのをやめ、関数で配る](doeff-dispatch.md)

数字は、非公開のリポジトリにある会話の待ちの記録と、1つの実行環境の会話の記録から数えました。読者は、数えた元を確かめられません。
