---
title: "DIも同じ@doで — doeffで依存を外側から渡す"
emoji: "🔁"
type: "tech"
topics: ["python", "doeff", "設計"]
published: false
---

依存性注入（DI）は、処理が使う実装を外側から渡す設計です。doeffでは、必要な依存を`Ask`で要求し、ハンドラが値や処理を供給します。取得した処理も`@do`なら、`yield`でそのまま合成できます。

## 同じ処理へ、異なる実装を渡す

記事の見出しを整える処理を差し替えます。`FormatTitleFn`は、文字列を受け取り、文字列を返す`Program`を作るという契約です。この型を`Ask`のキーにも使います。

以下の`run`と`assert`は動作を確認するための実行境界です。処理を組み立てる関数の内側では、子の`Program`を`yield`します。

```python
from typing import Protocol  # 整形処理に必要な呼び出し契約を表す。

from doeff import Program, do, run  # Programを合成し、確認の境界で実行する。
from doeff_core_effects import Ask  # 利用したい依存をハンドラへ要求する。
from doeff_core_effects.handlers import reader  # 外側で選んだ依存を供給する。


class FormatTitleFn(Protocol):  # 処理の種類を表す型をAskのキーにも使う。
    def __call__(self, text: str) -> Program[str]: ...  # 見出しを返すProgramを要求する。


@do  # 装飾しない実装も、呼び出すとProgramになる。
def plain_title(text: str):  # 加工対象の見出しは通常の引数で受け取る。
    return text  # 実行すると入力と同じ見出しを返す。


@do  # 装飾する実装を、同じProgramの契約で提供する。
def bracketed_title(text: str):  # 同じ入力型で実装を差し替えられる。
    return f"【{text}】"  # 実行すると括弧付きの見出しを返す。


@do  # 依存の取得と、取得した処理の実行を合成する。
def make_title(text: str):  # 呼び出し元は具体的な整形実装を渡さなくてよい。
    formatter: FormatTitleFn = yield Ask(FormatTitleFn)  # ハンドラから整形処理を受け取る。
    return (yield formatter(text))  # そのProgramを実行し、見出しの文字列を返す。


plain = reader(env={FormatTitleFn: plain_title})(make_title("新着記事"))  # 無装飾を供給する。
bracketed = reader(env={FormatTitleFn: bracketed_title})(make_title("新着記事"))  # 括弧版を供給する。
assert run(plain) == "新着記事"  # 同じ利用側から無装飾の見出しが得られる。
assert run(bracketed) == "【新着記事】"  # 実装だけを替えると装飾が変わる。
```

`make_title`の中に実装の選択はありません。入力の見出しは通常の引数で渡し、整形の実装はハンドラから取得します。この規模なら関数の引数で渡す方法でも十分ですが、依存が呼び出しの奥に増えても、中間の関数で一つずつ運ぶ必要を減らせます。

![依存を求め、受け取った処理をyieldでつなぐ](/images/zenn-use-cases-v0/generated/di-concept.png)

`Ask`で実装を受け取り、その実装が返す`Program`を`yield`します。外側の辞書を替えると、同じ利用側から異なる見出しが得られます。

## 遅延解決とログも、同じ@doに収まる

`reader`は辞書の値をそのまま渡します。一方、`lazy_ask`は辞書に入れた`Program`を、最初に要求されたときに実行して結果を再利用します。`Local(env, program)`は、その子の計算の範囲だけ環境を上書きします。ここでは`lazy_ask`が`Ask`と`Local`の両方を処理します。

次の例では挨拶を4回取得します。通常の挨拶を2回、`Local`の中で夕方の挨拶を1回、外に戻って通常の挨拶を1回です。作成するたびに`Tell`で接頭辞を記録し、結果の再利用をログから確認します。

```python
from doeff import do, run  # 挨拶のProgramを合成し、結果を確認する。
from doeff_core_effects import Ask, Local, Tell  # 依存の要求・局所変更・作成ログを表す。
from doeff_core_effects.handlers import lazy_ask, state, writer, writer_log  # 解決と記録を担当する。
from doeff_core_effects.scheduler import scheduled  # 遅延解決の排他制御を実行する。


@do  # 必要になるまで挨拶の組み立てを実行しない。
def build_greeting():  # 作成方針は、このProgramを解釈する環境で決める。
    prefix = yield Ask("prefix")  # 現在の範囲にある挨拶の接頭辞を取得する。
    yield Tell(prefix)  # 実際に作成した回数をwriterで観測できるようにする。
    return prefix + "読者のみなさん"  # 接頭辞に対応する挨拶を返す。


@do  # 依存の利用側を小さなProgramとして再利用する。
def read_greeting():  # 挨拶をどう作るかは利用側に埋め込まない。
    return (yield Ask("greeting"))  # 解決済み、または今解決した挨拶を返す。


@do  # 通常の取得と、局所的な変更を順番に合成する。
def localized_messages():  # 4回の取得結果から範囲の違いを確認する。
    first = yield read_greeting()  # 最初の取得では通常の挨拶を作る。
    second = yield read_greeting()  # 同じ範囲では作成済みの挨拶を再利用する。
    local = yield Local({"prefix": "こんばんは、"}, read_greeting())  # 中だけ夕方の挨拶にする。
    restored = yield read_greeting()  # Localを出ると通常の挨拶を再び受け取る。
    return first, second, local, restored  # 外・外・内・外の順で返す。


@do  # 結果と作成ログを同じハンドラの範囲から回収する。
def inspect_messages():  # テストに必要な観測値を戻り値へ含める。
    messages = yield localized_messages()  # 挨拶の取得を4回進める。
    built = yield writer_log()  # 作成時にだけ出たTellをリストで受け取る。
    return messages, built  # 表示内容と作成回数を別々に検証できる。


env = {"prefix": "こんにちは、", "greeting": build_greeting()}  # 値と未実行Programを渡す。
program = lazy_ask(env=env)(writer(inspect_messages()))  # 依存解決の内側にもログを届ける。
messages, built = run(scheduled(state()(program)))  # 排他制御とログ保存を外側に置いて実行する。
first, second, local, restored = messages  # 各範囲の結果を名前付きで確認する。
assert first == second == restored == "こんにちは、読者のみなさん"  # 外側の値は変わらない。
assert local == "こんばんは、読者のみなさん"  # Local内だけ変更した接頭辞が反映される。
assert built == ["こんにちは、", "こんばんは、"]  # 取得4回に対し作成は2回で済む。
```

`built`が2件なのは、2回目と4回目の通常の挨拶が保存済みの結果を使うためです。夕方の挨拶は、依存している`prefix`が`Local`内で変わるので、その範囲で別に解決されます。外へ出ると通常の結果を使えます。

この再利用は、作成した`lazy_ask`ハンドラが持つメモリ上のキャッシュです。プロセスを越える保存は[Memoとdurable executionの記事](doeff-durable.md)で扱います。また、`lazy_ask`が解決を調整するために使うセマフォは、外側の`scheduled`が処理します。`Tell`の記録には`writer`と、その保存先になる`state`を組み合わせています。

![同じ範囲では再利用し、Localの中だけ作り直す](/images/zenn-use-cases-v0/generated/di-flow.png)

取得は4回、作成は2回です。`Local`の内側で依存する値を変更しても、外側の挨拶は通常のまま使えます。

## 依存の供給元も、ハンドラで組み合わせる

実行環境との接続が必要なら、`env_var_ask`を外側へ取り付けられます。本文の処理は`os.environ`を直接読みません。前節の`read_greeting`に対して、テスト中だけ架空の環境変数を供給します。

```python
from unittest.mock import patch  # 架空の環境変数をテスト中だけ設定する。

from doeff_core_effects.handlers import env_var_ask  # 環境変数を依存の供給元にする。


with patch.dict("os.environ", {"ARTICLE_DEMO_greeting": "環境からの挨拶"}):  # 終了時に元へ戻す。
    program = lazy_ask(env={})(read_greeting())  # 未登録のAskを外側へ渡す。
    sourced = env_var_ask(prefix="ARTICLE_DEMO_")(program)  # 外側で架空の環境変数から解決する。
    assert run(scheduled(sourced)) == "環境からの挨拶"  # 供給元を替えても利用側は同じ。
```

この構成では、`lazy_ask`の空の辞書で見つからなかったキーが外側へ渡り、`env_var_ask`が`ARTICLE_DEMO_greeting`を読みます。通常の文字列は文字列のまま返り、数値などへ自動変換されません。供給元を変更しても、`read_greeting`のコードは同じです。

## DIで得た処理と、エフェクトの境界

冒頭の`Ask(FormatTitleFn)`は、整形処理を取得するエフェクトです。続く`yield formatter(text)`は、取得した`@do`のProgramを合成しています。`bracketed_title`の文字列加工そのものは、普通のPythonの計算です。

同様に、`Ask`でAPIクライアントを取得しても、そのクライアントの通信が自動で記録・再生可能になるわけではありません。通信を差し替えたい場合は、操作を`HttpRequest`などのエフェクトで表し、HTTPハンドラへ渡します。実際のコードは[HTTPとMemoを合成する例](doeff-replay.md)を参照してください。

現行のリポジトリでは`doeff-pinjected`パッケージは削除済みです。この記事では、現行の`Ask`・`reader`・`lazy_ask`を使います。古いpinjected連携のサンプルを、そのまま現行APIとして扱わないようにしています。

[全体を実行できる例](examples/dependencies.py)もリポジトリに保存しています。依存の供給から、[状態や失敗の合成](doeff-composition.md)、[ハンドラの合成](doeff-handlers.md)まで、同じ`@do`の書き方でつなげられます。

---

[doeffとは？：メイン記事へ戻る](doeff-main.md)

## 参考資料・検証版

導入方法は[公式README](https://github.com/proboscis/doeff#installation)を参照してください。以下はこの記事で確認した開発版へのリンクです。公開パッケージの最新版との一致は別途確認が必要です。

- [Ask・Localの定義](https://github.com/proboscis/doeff/blob/d4705914e39740aee98a9f57a4535c463d9479cc/packages/doeff-core-effects/doeff_core_effects/effects.py)
- [reader・lazy_ask・env_var_ask・writerの実装](https://github.com/proboscis/doeff/blob/d4705914e39740aee98a9f57a4535c463d9479cc/packages/doeff-core-effects/doeff_core_effects/handlers.py)
- [遅延解決とLocalの検証](https://github.com/proboscis/doeff/blob/d4705914e39740aee98a9f57a4535c463d9479cc/tests/effects/test_lazy_ask.py)
- [pinjected連携の削除契約](https://github.com/proboscis/doeff/blob/d4705914e39740aee98a9f57a4535c463d9479cc/tests/core/test_pinjected_removal.py)
