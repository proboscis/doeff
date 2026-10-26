---
title: "依存を引数で引き回すのをやめる"
emoji: "🔁"
type: "tech"
topics: ["python", "doeff", "設計"]
published: false
---

処理の奥で使う実装を、テストと本番で替えたいことがあります。引数で渡すと、自分では使わない途中の関数まで、依存をバケツリレーで運びます。DIのコンテナを入れると、今度は処理の書き方がコンテナに縛られます。

この記事では、Pythonのライブラリdoeffで、コンテナを使わずに依存を外側から渡します。処理の側は「この実装がほしい」と依頼を書くだけです。どの実装を渡すかは、外側に取り付けたハンドラが決めます。最後に、この書き方が効く所と、儀式になる所を整理します。連載が初めての方は[1本目の入門](doeff-main.md)から、コードは[GitHub](https://github.com/proboscis/doeff)から読めます。

| 言葉 | この記事での意味 |
| --- | --- |
| DI（依存性注入） | 処理が使う実装を、処理の外側から渡す設計です |
| エフェクト（effect） | 処理が外側へ出す依頼です。この記事では主に`Ask`（値をください）を使います |
| ハンドラ（handler） | 依頼を受け取り、答えを返して処理の続きを動かす部品です |
| `Program` | `@do`の関数を呼ぶとできる、まだ動いていない処理です |

## まず短いコード：実装を依頼し、外側から渡す

```python
from doeff import do, run  # 処理を定義し、確認の境界で実行する。
from doeff_core_effects import Ask  # 必要な実装を外側へ依頼する。
from doeff_core_effects.handlers import reader  # 辞書から値を渡すハンドラを使う。


@do  # 呼ぶと、まだ動いていない処理(Program)ができる。
def make_title(text: str):  # 見出しの整え方は、この関数の中で決めない。
    formatter = yield Ask("formatter")  # 整形の実装を外側へ依頼する。
    return (yield formatter(text))  # 受け取った実装を動かし、見出しの文字列を返す。


@do  # テスト用の実装も、呼ぶとProgramになる。
def plain_title(text: str):  # 加工する見出しは通常の引数で受け取る。
    return text  # 入力と同じ見出しを返す。


@do  # 本番用の実装を、同じ呼び方で提供する。
def bracketed_title(text: str):  # 入力の型はテスト用と同じにする。
    return f"【{text}】"  # 括弧で囲んだ見出しを返す。


test_run = reader(env={"formatter": plain_title})(make_title("新着記事"))  # テスト用を渡す。
prod_run = reader(env={"formatter": bracketed_title})(make_title("新着記事"))  # 本番用を渡す。
assert run(test_run) == "新着記事"  # テスト用の実装では、見出しがそのまま返る。
assert run(prod_run) == "【新着記事】"  # 外側を替えるだけで、括弧付きの見出しになる。
```

`make_title`の中には、どの実装を使うかが書かれていません。`yield Ask("formatter")`で、整形の実装を外側へ依頼するだけです。`reader`は、渡した辞書から値を返すハンドラです。辞書を替えると、同じ`make_title`から違う見出しが得られます。

渡す実装も`@do`の関数です。そのため`yield formatter(text)`で、受け取った処理をそのまま動かせます。実装の中で別の依頼を出しても、同じ外側のハンドラの組が答えます。

![処理は依頼を書き、外側のハンドラが実装を選ぶ](/images/zenn-use-cases-v0/generated/di-concept.png)

処理のコードは1つです。外側のハンドラだけを替えると、テスト用と本番用の実装が入れ替わります。

## ハンドラの中身：readerがしていること

`reader`は特別な仕組みではありません。同じ働きのハンドラを、自分で書けます。次の`test_formatter`は、整形の依頼にだけテスト用の実装を返します。前の例の`make_title`と`plain_title`をそのまま使います。

```python
from doeff import Pass, Resume, handler, with_handlers  # 依頼の再開・委譲と取り付けに使う。


@handler  # この関数を、Programに取り付けられるハンドラにする。
def test_formatter(effect, k):  # effectは届いた依頼、kは止まった処理の続き。
    if isinstance(effect, Ask) and effect.key == "formatter":  # 整形の依頼だけを担当する。
        return (yield Resume(k, plain_title))  # テスト用の実装を渡し、続きを動かす。
    return (yield Pass(effect, k))  # 担当外の依頼は、外側のハンドラへ渡す。


@do  # 見出しとサイト名を組み合わせる処理。
def make_heading(text: str):  # 依存の渡し方は、この関数も知らない。
    title = yield make_title(text)  # 前の例の処理を、yieldでそのまま合成する。
    site = yield Ask("site")  # サイト名も外側へ依頼する。
    return f"{title} | {site}"  # 2つの答えを1行の見出しにまとめる。


inner = with_handlers([test_formatter], make_heading("新着記事"))  # 内側に自作のハンドラを付ける。
outer = reader(env={"site": "技術ブログ"})(inner)  # サイト名は外側のreaderが答える。
assert run(outer) == "新着記事 | 技術ブログ"  # 2つの依頼に、別々のハンドラが答える。
```

ハンドラは、届いた依頼`effect`と、止まった処理の続き`k`を受け取ります。`isinstance`とキーで、自分が担当する依頼かを判別します。担当する依頼なら、`Resume(k, 値)`で値を渡し、続きを動かします。担当外の依頼は、`Pass(effect, k)`で外側のハンドラへ渡します。

この例では、内側の`test_formatter`が整形の依頼に答えます。サイト名の依頼は外側へ渡り、`reader`が答えます。ハンドラを重ねる順番が、どの依頼に誰が答えるかを決めます。

DIのコンテナと比べると、違いは処理の側の書き方に出ます。`make_heading`は、コンテナにも`test_formatter`にも依存していません。依存を求める書き方は、どの渡し方でも`yield Ask(...)`の1つです。キーには文字列のほか、型などハッシュできる値も使えます。

## 遅れて決まる依存とログも、同じ書き方に収まる

DIのコンテナには、依存を初めて使う時に作り、以後は使い回す機能があります。doeffでは`lazy_ask`ハンドラが、その役を担います。辞書に入れた`Program`を、最初に要求された時に実行します。その結果は、同じ範囲の次の要求で再利用します。

`Local(env, program)`は、子の処理の範囲だけ環境を上書きします。ここでは`lazy_ask`が、`Ask`と`Local`の両方を処理します。

次の例では、挨拶を4回取得します。通常の挨拶を2回取得し、`Local`の中で夕方の挨拶を1回取得します。最後に外へ戻り、通常の挨拶をもう1回取得します。挨拶を作るたびに`Tell`で接頭辞を記録し、作った回数をログで数えます。

```python
from doeff_core_effects import Local, Tell  # 依存の局所変更と、作成ログの依頼を使う。
from doeff_core_effects.handlers import lazy_ask, state, writer, writer_log  # 解決と記録を担う。
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

`built`が2件なのは、2回目と4回目の取得が作成済みの結果を使うためです。夕方の挨拶は、依存する`prefix`が`Local`の中で変わるので、別に作られます。`Local`を出ると、外側で作った通常の挨拶をまた使えます。

![同じ範囲では再利用し、Localの中だけ作り直す](/images/zenn-use-cases-v0/generated/di-flow.png)

取得は4回、作成は2回です。`Local`の内側で依存する値を変更しても、外側の挨拶は通常のまま使えます。

依存を作る処理の中のログも、同じ`yield`で書けます。`Tell`の依頼は`writer`が受け、記録の置き場は`state`が持ちます。`lazy_ask`は、同じ依存を同時に作らないようにセマフォを使います。そのセマフォの依頼は、外側の`scheduled`が処理します。

この再利用は、`lazy_ask`ハンドラが持つメモリ上のキャッシュです。プロセスを越える保存は、[Memoと永続実行の記事](doeff-durable.md)で扱います。

## 依存の供給元も、ハンドラで組み合わせる

設定を環境変数から読みたい時は、`env_var_ask`を外側に取り付けます。処理の側は、`os.environ`を直接読みません。前の節の`read_greeting`へ、テストの間だけ架空の環境変数を渡します。

```python
from unittest.mock import patch  # 架空の環境変数をテスト中だけ設定する。

from doeff_core_effects.handlers import env_var_ask  # 環境変数を依存の供給元にする。


with patch.dict("os.environ", {"ARTICLE_DEMO_greeting": "環境からの挨拶"}):  # 終了時に元へ戻す。
    program = lazy_ask(env={})(read_greeting())  # 未登録のAskを外側へ渡す。
    sourced = env_var_ask(prefix="ARTICLE_DEMO_")(program)  # 外側で架空の環境変数から解決する。
    assert run(scheduled(sourced)) == "環境からの挨拶"  # 供給元を替えても利用側は同じ。
```

`lazy_ask`の空の辞書で見つからないキーは、外側のハンドラへ渡ります。外側の`env_var_ask`が、接頭辞を付けた`ARTICLE_DEMO_greeting`を読んで答えます。値は文字列のまま返り、数値などへは自動で変換されません。供給元を替えても、`read_greeting`のコードは同じです。

## まとめ：DIとエフェクトの境目

### 依存の受け取りと、処理の中身は別のもの

冒頭の`Ask("formatter")`は、整形の実装を受け取る依頼です。続く`yield formatter(text)`は、受け取った`@do`の処理を合成しています。括弧で囲む文字列の加工そのものは、普通のPythonの計算です。

同じように、`Ask`でAPIのクライアントを受け取っても、通信は記録も再生もされません。通信を差し替えたい時は、操作を`HttpRequest`などの依頼で表します。例は[HTTPとMemoを合成する記事](doeff-replay.md)にあります。

### 効く所と、儀式になる所

| 場面 | 向き | 訳 |
| --- | --- | --- |
| 同じ処理を、テストと本番など2通り以上で動かす | 効く | 外側のハンドラの組だけを替えれば済みます |
| 依存が呼び出しの奥で要る | 効く | 途中の関数が依存を受け取って運ぶ必要がありません |
| 範囲を区切って依存を上書きしたい | 効く | `Local`で、子の処理だけ替えられます |
| 一通りにしか動かさない小さな処理 | 儀式 | 替える物が無く、依頼の種類が増えるだけです |
| 速さが要るループの内側 | 儀式 | 依頼のたびにハンドラを通る手間がかかります |

`Ask`で実装を選ぶだけなら、できる事はDIと同じです。違いは、選ぶ場所と届け方にあります。同じ処理を、実行ごとやテストごとに別のハンドラの組で動かせます。依存は途中の関数を通らず、外側の環境から届きます。

代わりに、依存が関数の引数として見えません。答えるハンドラが無いと、実行して初めて`UnhandledEffect`のエラーになります。doeffには、処理の依頼とハンドラの組を静的に突き合わせる道具もあります。[doeff-effect-analyzer](https://github.com/proboscis/doeff/blob/9adc4f8e657a552f5253d8a7b422363789c2608c/packages/doeff-effect-analyzer/README.md)ですが、この記事では試していません。

[全体を実行できる例](examples/dependencies.py)もリポジトリにあります。状態や失敗の合成は[抽象の合成の記事](doeff-composition.md)で扱います。ハンドラの重ね方は[ハンドラ合成の記事](doeff-handlers.md)で扱います。

---

[doeffとは？：メイン記事へ戻る](doeff-main.md)

## 参考

この記事のコードは、2026年10月17日に、PyPIのdoeff 0.6.0で確かめました。Pythonは3.12と3.14で試しました。GitHubにあるdoeffのmain（commit `9adc4f8`）でも、同じく動きました。どの例も固定のハンドラと架空の環境変数で動かし、外部のサービスには接続していません。

試す時は、[完全な例](examples/dependencies.py)を`dependencies.py`として保存し、次のコマンドで実行します。

```bash
uv run --with "doeff==0.6.0" python dependencies.py  # PyPIのdoeff 0.6.0を入れて、記事の4つの例をまとめて確かめる。
```

- [Ask・Local・Tellの定義](https://github.com/proboscis/doeff/blob/9adc4f8e657a552f5253d8a7b422363789c2608c/packages/doeff-core-effects/doeff_core_effects/effects.py)
- [reader・lazy_ask・env_var_ask・writerの実装](https://github.com/proboscis/doeff/blob/9adc4f8e657a552f5253d8a7b422363789c2608c/packages/doeff-core-effects/doeff_core_effects/handlers.py)
- [遅延解決とLocalの検証](https://github.com/proboscis/doeff/blob/9adc4f8e657a552f5253d8a7b422363789c2608c/tests/effects/test_lazy_ask.py)
- [ハッシュできるキーの検証](https://github.com/proboscis/doeff/blob/9adc4f8e657a552f5253d8a7b422363789c2608c/tests/misc/test_reader_hashable_keys.py)
- [エフェクトとDIの比較（英語）](https://github.com/proboscis/doeff/blob/9adc4f8e657a552f5253d8a7b422363789c2608c/docs/20-why-effects-over-di.md)
- 前の回:[テストで20秒待つのをやめる](doeff-time.md)
- 次の回:[ライブラリのcommitの68%を、Claude Codeと書いた1年](doeff-commits.md)
