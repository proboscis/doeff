---
title: "ライブラリのcommitの68%を、Claude Codeと書いた1年"
emoji: "📈"
type: "tech"
topics: ["python", "doeff", "claudecode", "oss", "test"]
published: false
---

筆者は、PythonのライブラリdoeffをClaude Codeと書いています。最初のcommitから約13か月で、mainの5,566個のうち3,811個(68%)がClaudeとの共著です。人が差分を読み切れない量になっても、使う人との約束を崩さないためにした事を書きます。

AIに書かせると、commitは速く増えます。2026年10月は、18日間で2,114個のcommitが入りました。1日に100個を越え、人が全部の差分を読むことはできません。

それでも、公開するライブラリには、使う人との約束があります。

- 公開した名前と型が、書いた通りに使えること
- 文書のコードが、書いてある通りに動くこと
- `pip install`で入る物が、書いた通りであること

doeffは、処理の中の「読む」「待つ」といった依頼を、外側の部品で差し替えられるライブラリです。連載が初めての方は[1本目の入門](doeff-main.md)から、コードは[GitHub](https://github.com/proboscis/doeff)から読めます。

## まず短いコード — 共著のcommitを数える

Claude Codeは既定の設定で、作ったcommitの本文の末尾に、共著者を示す次の行を足します。

`Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`

この行を持つcommitを数える処理を、doeffで書きます。履歴を読む所は、外側への依頼にしておきます。

```python
import re  # 共著の行を探す。
from dataclasses import dataclass  # 依頼を、書き換えないデータ型で表す。

from doeff import Effect, Pass, Resume, do, handler, run, with_handlers  # 依頼・再開・委譲と実行を使う。

CLAUDE = re.compile(r"^Co-Authored-By: Claude", re.I | re.M)  # 本文の行頭の、Claudeとの共著の行。


@dataclass(frozen=True)  # 発行した依頼を書き換えない型にする。
class ReadMessages(Effect):  # 「commitの本文を全部読みたい」という依頼。
    rev: str  # どのcommitまでの履歴を読むか。


@do  # 呼ぶと、数える手順(Program)ができる。
def claude_share(rev):  # 共著の行を持つcommitの数と、全体の数を返す。
    messages = yield ReadMessages(rev)  # commitの本文の一覧を、外側に頼む。
    marked = sum(1 for message in messages if CLAUDE.search(message))  # 共著の行を持つ本文を数える。
    return marked, len(messages)  # 共著の数と、全体の数の組。


@handler  # Programに取り付けられるハンドラにする。
@do  # ハンドラもyieldで、再開や委譲を指示する。
def fixed_log(effect, k):  # テスト用に、決めた3つの本文を答える。
    if isinstance(effect, ReadMessages):  # 本文の依頼には、
        return (yield Resume(k, ["Fix typo",  # 共著の行が無い本文と、
                                 "Add Gather\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>",  # 共著の本文と、
                                 "Refactor\n\nCo-authored-by: Claude Fable 5 <noreply@anthropic.com>"]))  # 小文字の行の本文。
    return (yield Pass(effect, k))  # 担当外の依頼は、外側のハンドラへ渡す。


print(run(with_handlers([fixed_log], claude_share("main"))))  # (2, 3)と表示される。
```

`claude_share`は、commitの本文を外側へ依頼し、数えるだけです。テストでは`fixed_log`が、決めた3つの本文を答えます。大文字と小文字の違う行も、共著と数えます。

コードの語を、先に整理します。

- `@do`を付けた関数は、呼ぶと実行の手順(`Program`)を返します。
- `yield ReadMessages(...)`は外側への依頼です。この依頼をエフェクトと呼びます。
- 依頼に答える部品をハンドラと呼びます。`Resume(k, 値)`で値を渡し、止まった続き`k`を動かします。
- 担当外の依頼は、`Pass(effect, k)`で外側のハンドラへ渡します。

次に、本物のgitの履歴を読むハンドラに替えます。`claude_share`は書き換えません。

```python
import subprocess  # 本番のハンドラで、gitを走らせる。


@handler  # Programに取り付けられるハンドラにする。
@do  # ハンドラもyieldで、再開や委譲を指示する。
def git_log(effect, k):  # 本番用に、今いるリポジトリのgitの履歴から本文を読む。
    if isinstance(effect, ReadMessages):  # 本文の依頼には、
        argv = ["git", "log", "-z", "--format=%B", effect.rev]  # 本文を、NUL文字で区切って出させる。
        out = subprocess.run(argv, capture_output=True, text=True, check=True).stdout  # gitを走らせて読む。
        return (yield Resume(k, out.split("\0")[:-1]))  # 末尾の区切りの後の空を除いた、本文の一覧を返す。
    return (yield Pass(effect, k))  # 担当外の依頼は、外側のハンドラへ渡す。


print(run(with_handlers([git_log], claude_share("c0dfc3f24"))))  # (3811, 5566)と表示される。
```

doeffのリポジトリの中で走らせると、`(3811, 5566)`と表示されます。2026年10月18日のmain(commit `c0dfc3f24`)までの数です。mergeのcommitも1個と数えます。

![同じclaude_shareを、テスト用と本番用のハンドラで動かす](/images/zenn-use-cases-v0/generated/commits-flow.png)
*claude_shareはReadMessagesを依頼するだけです。fixed_logは決めた3つの本文で(2, 3)を、git_logは本物の履歴で(3811, 5566)を返します。*

## 全体の図 — 読めない量を、4つの網で受け止める

![AIが書く変更を、4つの網で受け止める](/images/zenn-use-cases-v0/generated/commits-concept.png)
*人は仕事を頼み、AIの会話が変更を書きます。約束は人が読む代わりに、4つの網が機械で確かめます。*

差分を全部読む代わりに、約束を機械が確かめる形にしました。網は次の4つです。

1. 文書のコードを走らせ、表示を注記と比べます。
2. 型の検査を、使う人と同じ入れ方で走らせます。
3. 設計の約束をテストにし、別のAIに破らせます。
4. 出した版を、PyPIから毎日入れ直します。

PyPIは、Pythonのパッケージを公開する置き場です。13か月の数字の後に、網を1つずつ書きます。

## 13か月の数字

![月ごとのcommitの数と、Claudeとの共著の行があるcommit](/images/zenn-use-cases-v0/generated/commits-months.png)
*doeffのmainのcommitを、書いた月(日本時間)で数えました。範囲は2025年9月16日〜2026年10月18日で、10月は18日までです。*

| 項目 | 数 | 測った日と範囲 |
| --- | --- | --- |
| mainのcommit | 5,566個 | 2025年9月16日〜2026年10月18日(`c0dfc3f24`まで) |
| そのうち共著の行があるcommit | 3,811個(68.5%) | 同じ範囲 |
| 2026年10月の共著の割合 | 2,114個のうち2,070個(97.9%) | 10月1日〜18日 |
| mergeのcommitを除いた割合 | 5,106個のうち3,799個(74.4%) | 同じ範囲 |
| 共著の行で最も多いモデル | Opus 5.5の2,549行(全3,841行) | 同じ範囲・1つのcommitに2行の物もある |
| PyPIに出したdoeffの版 | 7つ(0.2.0〜0.7.0) | 2026年10月18日にPyPIの公開の情報を読んだ |

- 2025年11月は、44個のcommitに共著の行が1つもありません。
- 2026年1〜2月は、949個のうち117個(12%)だけでした。
- 2026年9月からは、ほぼ全部のcommitに共著の行があります。

共著の行が無いcommitを、誰が書いたかは分かりません。筆者が手で書いた物と、Codexで書いた物が混ざっています。CodexはClaude Codeと同じ行を残さないので、その分は数えられません。

共著の行は、Claude Codeを通したことを示すだけです。誰が頼んだ変更かも、変更の良さも表しません。

## 網1: 文書のコードを走らせ、表示を注記と比べる

AIは、それらしく見えるコードの例を速く書きます。ところが、書いた時から動かない例や、後の変更で黙って壊れる例が混ざります。

doeffのREADMEでは、`print`の行の末尾に、出るはずの表示を注記で書きます。たとえば`print(...)  # 14 chars`の形です。テストは例を新しいPythonで走らせ、表示の行と注記を比べます。注記の無い例は、何も確かめていないとして失敗にします。

終了コードだけを見ないのは、0で終わっても動いたとは限らないからです。後で書く0.5.0の不具合も、終了コードは0でした。

READMEと入門の章の外の文書は、どのテストも走らせていませんでした。0.5.0の時点で、定義を読むだけで通っていた137個の枠を走らせると、25個が落ちました。同期の`httpx.get`の返り値を、待つための依頼に渡す例もありました。

「Why Effects?」の章も、16個の例が、PyPIに無い部品と古いAPIを使う下書きでした。0.7.0で、`pip install doeff`だけで動く例に書き直しました。走る形に直した章から順に、このテストの対象に足しています。

この連載の掲載コードも、記事ごとの確かめのスクリプトで走らせています。

## 網2: 型の検査を、使う人と同じ入れ方で走らせる

開発の環境では、リポジトリを直に読み込み、型の設定もリポジトリの物を使います。ここで型の検査が通っても、使う人の環境で通るとは限りません。

そこで、新しい環境に核の4つのパッケージだけを入れて検査する道具を作りました。当てるのは、誤りが出るべき行に印を付けた25個のファイルです。1つを縮めると、次の形です。

```python
from collections.abc import Generator  # Programの型を書く。
from typing import Any  # 答えの型は問わない。

from doeff import Ask, do  # 設定の値を依頼する。


@do  # 呼ぶと、手順(Program)ができる。
def user_name() -> Generator[Ask, Any, str]:  # 名前の文字列を返す手順。
    yield Ask("greeting")  # 1つ目の依頼には、yieldを付けた。
    name = Ask("name")  # 2つ目の依頼で、yieldを付け忘れた。
    return name  # expect-error(文字列の代わりに、依頼を返している)
```

`yield`を付け忘れると、`name`は答えではなく、依頼そのものになります。型の検査の道具のpyrightとmypyは、厳しい設定で返す行を誤りとします。印の行に誤りが出なくても、印の無い行に誤りが出ても、道具は失敗します。

ただし、値を使わずに捨てる付け忘れは、この形では捕まりません。pyrightの別の設定で捕まえる例も、25個の中にあります。

型の分からない公開の名も数えました。2026年10月17日に、doeffの公開の名381個のうち、69個の型が分かりませんでした。直した後は2個で、pyrightのverifytypesの点は81.9%から99.5%になりました。verifytypesは、公開の名のうち型の分かる割合を測る機能です。

## 網3: 設計の約束をテストにし、別のAIに破らせる

「業務のコードは、今の時刻を直に読まない」のような約束は、文書に書くだけでは崩れます。doeffでは、設計の判断の記録(ADR)に規則と反例を持たせ、pytestで走らせます。

2026年10月18日に数えると、`docs/adr`にそうしたファイルが44本ありました。コードの形を探すsemgrepの規則も、54個あります。詳しくは「[ADRを書いて終わりにするのをやめる](doeff-adr.md)」に書きました。

ただ、規則は、書いた人が思いつく破り方しか止めません。そこで変更の一部では、設計を書いた会話の文脈を持たない2つのAIに、失敗例を探させます。

- 1つ目は、ありそうな将来の変更で、責務が複数の部品へ漏れる例を探します。
- 2つ目は、今の検査を全部通るのに、約束を破る実装を探します。
- どちらも、gitの履歴やcommitの文を読みません。見つからなくても、安全とは結論しません。

この記録は、リポジトリの`docs/design-checks`に50件あります。2026年9月25日から10月17日までの物です。49件には、2つのAIの返答をそのまま残しています。

## 網4: 出した版を、PyPIから毎日入れ直す

0.5.0は、2026年10月17日の3時37分(日本時間)にPyPIへ出しました。使う人の目で見ると、困る所が2つありました。

- `pip install doeff`で、19個・19.5MBのパッケージが入りました。
- `async def`の関数に`@do`を付けると、何も動かずに終了コード0で終わりました。

約14時間後の0.6.0で、入る物を核の4個・3.5MBにしました。`async def`には、`@do`を付けた所で誤りを出します。0.7.0までの3つの版を、約29時間で出しました。

今は、PyPIのdoeffを毎朝、新しい環境へ入れ直しています。Linux・macOS・Windowsと、Pythonの3.11〜3.14の12の組です。入った物がちょうど核の4個であることと、READMEの例が注記の通りに表示することを確かめます。

核の4個は、doeff・doeff-vm・doeff-core-effects・frozendictです。開発の環境ではなく、今日初めて来た人と同じ手順で入れるのが要です。

## 4つの網が守る約束

| 網 | 守る約束 | 要る物 |
| --- | --- | --- |
| 文書のコードと注記 | 文書のコードが、書いた通りに表示する | 枠を走らせ、表示と注記を比べる道具 |
| 使う人と同じ入れ方の型の検査 | 誤りが出るべき行にだけ、誤りが出る | 核だけの新しい環境と、印を付けた25個のファイル |
| 設計の約束のテストと、別のAI | 変更の後も、設計の約束が破られていない | ADRの規則・semgrepの規則・別のAIの会話2つ |
| PyPIからの入れ直し | 今日入れた人の環境で、READMEが動く | 毎朝のCIの仕事(12の組) |

どの網も、人が差分を読まなくても、約束が破れた時に赤くなります。1つ目と4つ目は、使う人が最初に触る所を守ります。

## うまくいかなかったこと

- 共著の行が無いcommitは、人とCodexを分けられません。68%は、AIを通した割合の下限です。
- 2026年1〜2月は、pull requestをmergeする形が多い時期でした。mergeのcommit 258個が数に入り、割合を下げています。
- 19.5MBの入れ方も、走らない文書の例も、使う人と同じ手順で試すまで気づきませんでした。網の多くは、困った後に足した物です。

## まだ分からないこと

- 共著の行が無い1,755個のうち、Codexで書いた物の数は分かりません。
- 4つの網が、それぞれ何件の誤りを止めたかは数えていません。
- 共著の割合と、使う人が出会う不具合の数の関係も分かりません。

## まとめ

- doeffのmainのcommitは、13か月で68%、2026年10月は98%がClaude Codeとの共著でした。
- 差分を人が読み切れないので、約束を機械が確かめる網を4つ置きました。
- 文書のコードは走らせて注記と比べ、型は使う人と同じ入れ方で検査します。
- 設計の約束はテストにし、別のAIに破り方を探させます。
- 出した版は、PyPIから毎日入れ直して確かめます。

AIの会話を並べる仕組みは、AIエージェントで開発した記録の連載の1本目に書きました。題は「[1人でClaude Codeを最大50並べたら、詰まるのは人だった](doeff-fleet.md)」です。

[doeffとは？：メイン記事へ戻る](doeff-main.md)

## 参考

この記事のコードは、2026年10月18日に確かめました。版は、doeffのmain(commit `c0dfc3f24`)とPyPIのdoeff 0.7.0です。Pythonは3.12です。型の検査の枠は、pyright 1.1.411とmypy 2.4.0にかけました。

完全な例は、リポジトリの`examples/commit_share.py`にあります。gitの履歴を数えるので、doeffのリポジトリを取ってきて、その根で実行します。

```bash
git clone https://github.com/proboscis/doeff && cd doeff  # 履歴を全部持つ形で取ってくる。
uv run --no-project --with "doeff==0.7.0" python publications/zenn-use-cases-v0/examples/commit_share.py  # (2, 3)と(3811, 5566)を表示する。
```

- [doeff本体(この記事で確かめた版)](https://github.com/proboscis/doeff/tree/c0dfc3f242b20d5dce25324cbc03ead7f532726d)
- [変更の記録(CHANGELOG.md)](https://github.com/proboscis/doeff/blob/c0dfc3f242b20d5dce25324cbc03ead7f532726d/CHANGELOG.md)
- [型の検査を当てる25個のファイル](https://github.com/proboscis/doeff/tree/c0dfc3f242b20d5dce25324cbc03ead7f532726d/tests/type_probes)
- [設計の検証の記録](https://github.com/proboscis/doeff/tree/c0dfc3f242b20d5dce25324cbc03ead7f532726d/docs/design-checks)
- [この記事のコードをまとめた完全な例](examples/commit_share.py)
- 前の回:[依存を引数で引き回すのをやめる](doeff-di.md)
- 次の回:[外部APIのテストで通信するのをやめる](doeff-replay.md)

数字は、2026年10月18日に数えました。元は、doeffのmainの`git log`・`CHANGELOG.md`・テストのファイルと、PyPIの公開の情報です。どれも公開されていて、読者も同じ手順で数えられます。
