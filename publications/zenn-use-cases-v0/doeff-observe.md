---
title: "処理の進捗を知りたい — doeffでログの表示と収集を組み合わせる"
emoji: "🔎"
type: "tech"
topics: ["python", "doeff", "設計"]
published: false
---

長い処理では「3ページまで終わった」という情報を画面に出したくなります。テストでは、その同じ情報を値として受け取り、ページ数まで確かめたい。doeffなら、処理の中で出す依頼と、その表示・収集を分けられます。

```python
from doeff import do, run  # 処理をProgramとして定義し、下の確認で実行する。
from doeff_core_effects import slog  # メッセージと属性を持つ構造化ログを作る。
from doeff_core_effects.handlers import slog_handler  # 構造化ログを標準エラーへ表示する。

@do  # 進捗の記録と完了判定を、呼び出し側からyieldできる処理にする。
def summarize_progress(completed: int, total: int):  # 完了件数と対象件数を受け取る。
    yield slog("処理の進捗", completed=completed, total=total)  # 件数を属性として渡す。
    return completed == total  # 全件完了ならTrue、途中ならFalseを返す。

assert run(slog_handler(summarize_progress(3, 3))) is True  # 件数を表示し、完了を確認する。
```

`slog_handler`を取り付けると、この例は標準エラーへ`INFO 処理の進捗 completed=3 total=3`を出します。`run`はここでは動作確認のために使っています。別の業務処理からつなぐときは、`@do`の中で`yield summarize_progress(...)`と書きます。

![ログの表示と収集を、独立して組み合わせる](/images/zenn-use-cases-v0/generated/observe-concept.png)

本体はログの依頼を出します。Listenは依頼を収集して外側へ渡し、Slogのハンドラは表示するか捨てるかを選びます。

## Tellとslogは、用途も型も異なる

`Tell`はWriterへ値を渡す操作です。`writer`が蓄積し、`yield writer_log()`で一覧を受け取れます。現在の実装では、その蓄積先として外側に`state()`が必要です。

`slog`は観測用の構造化ログを出します。型は`SlogEffect`で、`Slog`はその別名です。`slog_handler`の仕事は標準エラーへの表示で、蓄積はしません。こちらには`state()`が不要です。

そして`Listen`は、子Programが出す指定のエフェクトを**依頼オブジェクトのまま**収集し、`(本体の戻り値, 収集した依頼のリスト)`を返します。元の依頼も外側へ渡すため、収集と表示を一緒にできます。

## メッセージだけでなく、属性と実行順も検査する

次は、3ページの変換処理を題材にした例です。`Listen`の前後にも`Tell`を置いて、観測する範囲とWriterの蓄積範囲の違いを確かめます。

```python
from contextlib import redirect_stderr  # 実際の表示内容をテストで捕まえる。
from io import StringIO  # 表示先をメモリに置き、文字列として検査する。
from doeff import do, run  # 本体と観測を@doで合成し、テストで実行する。
from doeff_core_effects import Listen, SlogEffect, Tell, WriterTellEffect, slog  # 2種類の依頼を使う。
from doeff_core_effects.handlers import listen_handler, slog_handler  # 収集と表示の処理先を使う。
from doeff_core_effects.handlers import state, writer, writer_log  # Tellの蓄積先と取得操作を使う。

@do  # 変換処理を定義し、ログの表示先はここでは決めない。
def convert():  # サンプルの3ページの変換結果を返す。
    yield Tell("変換を開始")  # Writerへ文字列を渡し、処理を続ける。
    yield slog("変換が完了", pages=3)  # 観測用ログに処理済みページ数3を添える。
    return "完了"  # ログとは別に、後続が使う処理結果を返す。

@do  # 観測する範囲を、convertという子Programに限定する。
def observe():  # 結果・収集した依頼・Writerの蓄積を返す。
    yield Tell("観測前")  # Listenの対象外なので、Writerにだけ蓄積される。
    result, captured = yield Listen(convert(), types=(WriterTellEffect, SlogEffect))  # 2件を収集する。
    yield Tell("観測後")  # このTellもListenの対象外だが、Writerには蓄積される。
    written = yield writer_log()  # Tellだけの3件を、独立した一覧として受け取る。
    return result, captured, written  # 結果は「完了」、収集は2件、Writerは3件となる。

output = StringIO()  # 標準エラーへの表示を受け取る入れ物を作る。
with redirect_stderr(output):  # 表示ハンドラを実際に動かし、その出力を捕まえる。
    program = listen_handler(observe())  # Listenの対象Programへ観測ハンドラを取り付ける。
    result, captured, written = run(state()(writer(slog_handler(program))))  # 表示と蓄積も実行する。
assert result == "完了"  # 本体の戻り値が保たれている。
assert len(captured) == 2  # convertの依頼だけを収集している。
assert isinstance(captured[0], WriterTellEffect)  # 先頭はTellの依頼オブジェクトになる。
assert captured[0].msg == "変換を開始"  # メッセージを依頼から読み取れる。
assert isinstance(captured[1], SlogEffect)  # 次は構造化ログの依頼オブジェクトになる。
assert captured[1].kwargs == {"pages": 3}  # 属性名と数値をそのまま検査できる。
assert written == ["観測前", "変換を開始", "観測後"]  # Writerには対象外のTellも届く。
assert output.getvalue().strip() == "INFO 変換が完了 pages=3"  # 収集しても画面表示は残る。
```

`Listen`が依頼を消費してしまうなら、最後の表示とWriterの蓄積は起こりません。実装は収集後に`Pass`で外側へ渡します。したがって、**Listenだけを取り付けても依頼の処理先はそろいません**。この例では`writer`と`slog_handler`が元の依頼を処理しています。

## テストでは表示先だけを交換する

直前の`observe()`をそのまま使い、`slog_handler`を`slog_discard_handler`へ交換します。収集した依頼は同じように受け取れます。

```python
from doeff_core_effects.handlers import slog_discard_handler  # 構造化ログを表示せずに処理する。

silent_output = StringIO()  # 何も表示されないことを検査するための受け取り先を作る。
with redirect_stderr(silent_output):  # 交換後の実際の出力を捕まえる。
    program = listen_handler(observe())  # 本体と観測する範囲は前の例を使う。
    result, captured, written = run(state()(writer(slog_discard_handler(program))))  # 表示先を交換する。
assert silent_output.getvalue() == ""  # 構造化ログが標準エラーへ出ていない。
assert captured[1].kwargs == {"pages": 3}  # 表示を捨てても、Listenの収集結果は残る。
assert result == "完了"  # 本体の戻り値も変わらない。
```

`types`を省いた`Listen`の既定の収集対象は`WriterTellEffect`だけです。構造化ログも欲しいときには、最初の例のように明示します。

```python
@do  # 既定の収集対象を、子Programを使って確かめる。
def observe_writer_only():  # typesを省いたListenの結果を返す。
    return (yield Listen(convert()))  # 「完了」とTellだけの1件を受け取る。

program = listen_handler(observe_writer_only())  # 既定のListenを処理できるようにする。
result, captured = run(state()(writer(slog_discard_handler(program))))  # 両種類の依頼は処理する。
assert result == "完了"  # 収集対象を変えても本体は最後まで進む。
assert len(captured) == 1  # slogを含めず、Tellだけが残る。
assert isinstance(captured[0], WriterTellEffect)  # 唯一の収集結果の種類を確かめる。
```

![Listenで収集してから、元のログ処理へ渡す](/images/zenn-use-cases-v0/generated/observe-flow.png)

ListenはconvertのTellとSlogEffectを順に収集し、元の依頼を外側へ渡します。本体の結果と収集した2件が戻り、Writerの蓄積とSlogの表示も残ります。

[完全な例](examples/observability.py)では、ここまでに加えて、処理ハンドラがない場合に`UnhandledEffect`になることも確認しています。この例の検証範囲は同じタスク内の子Programです。並行タスクや、より内側で依頼を消費するハンドラまで含めて、どこからでも観測できるという主張ではありません。

## 実行トレースは、別の観測情報

進捗ログは、処理自身が出した情報です。一方、実行トレースでは「どの操作を実行中か」「どこを通ったか」を扱います。`doeff-flow`には、そのための`LiveTrace`型、JSONLへの記録、監視CLI、明示的なトレース操作の実装があります。

**ただし、この草稿で確認したcheckoutでは`doeff-flow`を利用できません。** `trace.py`が現在のVMに存在しない`RunResult`をimportするためです。下のコードは、実装が定義している操作を読むための構成例です。実行確認済みの例として扱わないでください。

```python
from doeff import do  # トレース操作を合成するProgramを定義する。
from doeff_flow.effects import TraceAnnotate, TraceCapture, TracePush, TraceSnapshot  # 4操作の型を使う。
# 現在はこのimportがRunResultのImportErrorで止まる。以下は実装上の呼び出し形を示す。
from doeff_flow.handlers.production import production_handlers  # 明示的なトレース操作の処理先を作る。

@do  # 工程名・属性・区切りを順に記録する処理を表す。
def traced_document():  # トレースのJSONL文字列を後続へ渡す構成にする。
    yield TracePush(name="文書処理", metadata={"document": "example"})  # 工程名と文書名を記録する。
    yield TraceAnnotate(key="stage", value="解析")  # 現在の記録へstage属性を加える。
    yield TraceSnapshot(label="解析が完了")  # この時点の状態にラベルを付けて記録する。
    return (yield TraceCapture(format="jsonl"))  # ここまでの記録をJSONL文字列として受け取る形になる。

trace_handler = production_handlers(  # 記録先を指定するfactoryの呼び出し形。現状は実行できない。
    workflow_id="article-document", trace_dir="./traces"  # 文書処理の識別子と記録先を渡す。
)
p_traced_document = trace_handler(traced_document())  # Programへ取り付ける形だけを示し、ここでは実行しない。
```

`TracePush`・`TraceAnnotate`・`TraceSnapshot`の処理は、ソース上では`LiveTrace`をJSONLへ追記します。`TraceCapture`は、それまでの記録を指定の形式で返す操作です。この明示的なハンドラだけでは、ワークフローの終了状態を自動記録しません。

監視CLIとデータ型の使用形も次のとおりです。これらも同じimport問題のため、現在のcheckoutでは動作確認済みの手順ではありません。

```bash
doeff-flow ps --trace-dir ./traces  # 記録済みのワークフローの状態を一覧にする使用形。
doeff-flow watch article-document --trace-dir ./traces  # 指定した記録の更新を監視する使用形。
```

```python
from doeff import do  # 記録から表示用データを取り出す処理も、Programとして合成する。
from doeff_flow.trace import LiveTrace  # 現在はimportで失敗する、実装上の記録データ型。

@do  # 後続からyield progress(trace)で使う表示用の変換を定義する。
def progress(trace: LiveTrace):  # 読み取った1件の記録を受け取る使用形。
    return {  # CLIなどで表示する4つの情報を返す。
        "workflow": trace.workflow_id,  # どのワークフローの記録かを示す。
        "step": trace.step,  # 記録されているステップ数を示す。
        "status": trace.status,  # runningなど、その記録の状態を示す。
        "effect": trace.current_effect,  # 記録対象の操作の文字列表現を示す。
    }
```

また、`trace_observer`でコールバックを作るだけではVMの実行を観測できません。実際の実行経路から呼ばれる接続が必要です。これも、上の明示的なトレース操作とは区別して読む必要があります。

実行前に定義を探す`doeff-indexer`や、開発途中の静的解析ツール`doeff-effect-analyzer`については、コード例とともに[開発を支える道具の記事](doeff-tooling.md)で扱います。保存結果からの再開は[durable executionの記事](doeff-durable.md)を参照してください。

## 実装・実例を読む

- [ログとListenの型](https://github.com/proboscis/doeff/blob/d4705914e39740aee98a9f57a4535c463d9479cc/packages/doeff-core-effects/doeff_core_effects/effects.py)
- [表示・蓄積・収集のハンドラ](https://github.com/proboscis/doeff/blob/d4705914e39740aee98a9f57a4535c463d9479cc/packages/doeff-core-effects/doeff_core_effects/handlers.py)
- [SlogとTellの分離を確認するテスト](https://github.com/proboscis/doeff/blob/d4705914e39740aee98a9f57a4535c463d9479cc/tests/test_slog_semantics.py)
- [実行トレースのデータ型](https://github.com/proboscis/doeff/blob/d4705914e39740aee98a9f57a4535c463d9479cc/packages/doeff-flow/src/doeff_flow/trace.py)
- [明示的なトレース操作のハンドラ](https://github.com/proboscis/doeff/blob/d4705914e39740aee98a9f57a4535c463d9479cc/packages/doeff-flow/src/doeff_flow/handlers/production.py)

この草稿は上記の開発版を参照しています。コアのログ例はオフラインで実行確認し、`doeff-flow`の例はimport互換性の問題を残した使用形として記載しています。

[ハンドラの合成を読む](doeff-handlers.md) / [メイン記事へ戻る](doeff-main.md)
