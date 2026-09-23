---
title: "完了した仕事をやり直さない — doeffと永続実行の入口"
emoji: "🔁"
type: "tech"
topics: ["python", "doeff", "設計"]
published: false
---

長い処理が途中で止まったとき、完了したステップまでやり直したくない。doeffでは、いつもの`@do`で処理を組み立て、結果の保存と再利用を外側に足せます。

![完了した計算の結果を、次の実行へ渡す](/images/zenn-use-cases-v0/generated/durable-concept.png)

`finish()`を先頭から実行し直しても、保存済みの解析結果を受け取って見出し抽出へ進めます。保存するのは完了した結果です。

## 解析と見出し抽出を、同じ`@do`でつなぐ

本文の解析と見出しの抽出を別のステップにします。`@cache()`は、関数名と引数から保存結果を探すためのMemoエフェクトを発行します。

```python
from doeff import do  # 各段階をyieldでつなぐProgramにする。
from doeff_core_effects.cache import cache  # 計算結果の保存と再利用をMemoに依頼する。

@cache()  # 同じ本文とparser_versionなら保存済みの解析結果を使う。
@do  # 本文解析を、ほかのProgramからyieldできる計算にする。
def parse_document(text: str, parser_version: str):  # 版もキャッシュの識別に含める。
    print("本文を解析しました")  # 保存結果がなく、本体が動いたときだけ表示する。
    return tuple(line.strip() for line in text.splitlines() if line.strip())  # 空行を除いた行の組を返す。

@cache()  # 同じ行の組とoutline_versionなら保存済みの見出しを使う。
@do  # 見出し抽出もyieldで合成できる計算にする。
def make_outline(lines: tuple[str, ...], outline_version: str):  # 抽出規則の版を識別に含める。
    print("見出しを抽出しました")  # 保存結果がない場合だけ抽出処理を実行する。
    return tuple(line for line in lines if line.startswith("#"))  # 見出しの2行を返す。

@do  # 初回に実行する解析段階をProgramにする。
def prepare():  # 本文と解析処理の版を固定した入口を作る。
    return (yield parse_document("# はじめに\n説明文\n# 遊び方", "parser-v1"))  # 解析済みの3行を返す。

@do  # 解析と見出し抽出をひとつのProgramへ合成する。
def finish():  # 次のプロセスでは、この全体を先頭から実行する。
    lines = yield prepare()  # 解析が保存済みなら、再解析せず3行を受け取る。
    return (yield make_outline(lines, "outline-v1"))  # 未保存なら抽出し、保存済みなら見出しを再利用する。
```

処理を組み合わせる書き方は、通常の`@do`と同じです。保存先は、計算を実行する側で決めます。次はSQLiteを使う検証用の構成です。

```python
from doeff import run  # 検証の外側で、組み立てたProgramを実行する。
from doeff_core_effects.handlers import await_handler, slog_discard_handler  # 保存の待機と内部ログを扱う。
from doeff_core_effects.memo_handlers import memo_handler  # Memo依頼を選んだ保存先で扱う。
from doeff_core_effects.scheduler import scheduled  # SQLiteが発行するAwaitの待機を進める。
from doeff_core_effects.storage import SQLiteStorage  # 完了した値をプロセスをまたいで保存する。

storage = SQLiteStorage("document-memo.sqlite")  # 保存先を作成するか、前回のファイルを開く。
program = memo_handler(storage)(finish())  # 解析と抽出のMemo依頼をSQLiteへ接続する。
try:  # 結果を検証し、失敗した場合も接続を解放する。
    result = run(scheduled(await_handler()(slog_discard_handler(program))))  # 保存結果か新しい計算結果を得る。
    assert result == ("# はじめに", "# 遊び方")  # どちらの経路でも同じ2見出しになる。
finally:  # 実行結果にかかわらず後始末する。
    storage.close()  # 呼出元スレッドのSQLite接続を閉じる。
    del program  # ハンドラが保持する保存先への参照を解放する。
    del storage  # 検証側の保存先への参照も解放する。
```

`Await`を発行するのはSQLiteの実装側です。利用者の解析・抽出処理を`async def`に移したり、その全体を`Await`で包んだりする必要はありません。

## 3つのプロセスで確かめる

[完全な例](examples/durable.py)は、保存先と実行する段階を受け取る検証スクリプトです。新しい保存先を指定し、別プロセスで順に実行します。

```bash
uv run --no-sync python publications/zenn-use-cases-v0/examples/durable.py document-memo.sqlite prepare  # 解析3行を計算して保存する。
uv run --no-sync python publications/zenn-use-cases-v0/examples/durable.py document-memo.sqlite finish  # 解析結果を再利用し、見出し2行を計算して保存する。
uv run --no-sync python publications/zenn-use-cases-v0/examples/durable.py document-memo.sqlite finish  # 両方の保存結果を使い、同じ見出し2行を返す。
```

| プロセス | 解析 | 見出し抽出 | 最後に返す値 |
| --- | --- | --- | --- |
| 1: `prepare` | 本体を実行して保存 | 実行しない | 解析した3行 |
| 2: `finish` | 保存結果を取得 | 本体を実行して保存 | 見出し2行 |
| 3: `finish` | 保存結果を取得 | 保存結果を取得 | 同じ見出し2行 |

初回だけ「本文を解析しました」、2回目だけ「見出しを抽出しました」が出ます。3回目はどちらも表示せず、見出しを返します。この動きを、実際に3つのPythonプロセスとSQLiteで確認しています。

`parser_version`と`outline_version`は、処理内容を選ぶ分岐には使っていません。**処理の版を保存結果の識別に含めるための引数**です。既定のキーは関数の完全修飾名と引数で、関数本体の変更を自動検出するものではありません。関数名や引数の変化でも再利用範囲が変わります。

## 保存しているのは、何か

停止や再起動をまたいで仕事を進める仕組みを永続実行（durable execution）と呼びます。ここで示したのは、その構成要素になる**完了結果の永続化と再利用**です。

プロセス2は`finish()`の途中の命令から再開するのではなく、`finish()`を先頭から実行します。そこで`prepare()`を呼ぶと、`parse_document(...)`の保存結果が返り、後続へ進みます。Pythonのスタックや、実行中の継続をSQLiteへ保存しているわけではありません。継続自体の扱いは[Rust VMの記事](doeff-vm.md)で説明します。

また、結果を保存するのは計算が返った後です。外部操作の成功直後、Memoへの保存前に停止すると、その操作が再実行される可能性があります。外部操作を一度だけにしたい場合には、操作先の冪等キーなど、重複を防ぐ仕組みも必要になります。

この記事の3プロセスの例は、各段階を正常終了して結果を再利用する検証です。未完了の外部操作、強制終了からの復旧、永続タイマーを検証した例ではありません。

## Conductorでは、実行IDに結び付けた履歴も使う

`doeff-conductor`には、ワークフローの時刻・乱数の依頼を実行IDに対応する履歴へ記録する仕組みもあります。次は開始時刻と乱数値を保存するHyのワークフローです。エージェントは起動しません。

```hy
(require doeff-hy.conductor [defworkflow time! random! <-])  ;; ワークフローと時刻・乱数の依頼を使う。
(import doeff_conductor.dsl [artifact ref])  ;; 記録した値を参照して最終結果へまとめる。

(defworkflow document-run  ;; 開始時刻と乱数を取得するワークフローを定義する。
  :params {}  ;; この例は外部パラメータを受け取らない。
  :roles {}  ;; エージェントを使う役割を設けない。
  (<- started (time!))  ;; 初回は現在時刻、再生時は記録済みの時刻を受け取る。
  (<- seed (random!))  ;; 初回は乱数値、再生時は記録済みの値を受け取る。
  (artifact {"started" (ref "started") "seed" (ref "seed")}))  ;; 2つの値を辞書として返す。

(setv WORKFLOW document-run)  ;; Conductorが読み込むワークフローの入口を公開する。
```

この原稿の[workflow_journal.hy](examples/workflow_journal.hy)を指定して実行します。

```python
from pathlib import Path  # ワークフローと保存先のパスを指定する。
from tempfile import TemporaryDirectory  # 検証ごとに独立した履歴の保存先を用意する。
from doeff_conductor.api import ConductorAPI  # ワークフローを読み込み、履歴付きで実行する。
from doeff_conductor.types import WorkflowStatus  # 完了状態の定数で結果を検証する。

workflow = Path("publications/zenn-use-cases-v0/examples/workflow_journal.hy").resolve()  # 記事のHy定義を使う。
with TemporaryDirectory() as directory:  # この検証の2回の実行で保存先を共有する。
    api = ConductorAPI(state_dir=Path(directory))  # 履歴をこのディレクトリへ保存する。
    first = api.run_workflow(str(workflow), run_id="article-journal")  # 時刻と乱数を取得して記録する。
    replayed = api.run_workflow(str(workflow), run_id="article-journal")  # 同じ定義と実行IDで履歴を使う。
    assert first.status == replayed.status == WorkflowStatus.DONE  # 両方とも最後まで完了する。
    assert first.result_payload == replayed.result_payload  # 開始時刻と乱数値が両方一致する。
```

同じ実行IDのもとで、依頼の種類・ノード・仕様などの識別と順序が一致する部分の値を再利用します。実行IDが同じならどんな変更も無条件に再生する、という意味ではありません。実用では`TemporaryDirectory`の代わりに、保持する状態ディレクトリを指定します。

エージェントの結果にも`JournaledAgentHandler`があります。次はテスト用の実行基盤に、完了結果の記録・再利用を加える構成です。この関数は**検証用の実行入口**であり、処理を合成する関数ではありません。

```python
from doeff_conductor.effects import AgentEffect  # エージェントへの依頼を差し替え対象にする。
from doeff_conductor.handlers import run_sync  # テスト用の入口でProgramを実行し、OkかErrを得る。
from doeff_conductor.handlers.journaled_agent import JournaledAgentHandler  # 有効な保存結果を先に探す。
from doeff_conductor.handlers.testing import mock_handlers  # 実エージェントを起動しないハンドラを使う。

# runtimeは応答を設定済みのMockConductorRuntime、state_dirはテスト間でも保持する保存先。
def run_recorded_agents(program, runtime, state_dir):  # テスト対象のProgramを履歴付きで実行する。
    journal = JournaledAgentHandler(runtime.handle_agent, state_dir=state_dir)  # 未保存の依頼だけテスト実装へ渡す。
    handlers = mock_handlers(runtime=runtime, overrides={AgentEffect: journal.handle_agent})  # AgentEffectを記録対象にする。
    return run_sync(program, scheduled_handlers=handlers)  # 保存結果か新しい応答でProgramを進め、OkかErrを返す。
```

[既存の回帰テスト](https://github.com/proboscis/doeff/blob/d4705914e39740aee98a9f57a4535c463d9479cc/packages/doeff-conductor/tests/test_agent_journal_c3.py)も実行しました。テスト用のエージェントで2件の完了後に例外を発生させ、再実行ではその2件を再利用して3件目だけ動くことを確認しています。これは例外による中断のテストで、実エージェントのプロセスを強制終了する試験ではありません。

## 処理の流れ

![3つのプロセスで、完了結果を引き継ぐ](/images/zenn-use-cases-v0/generated/durable-flow.png)

初回は解析だけを保存し、2回目は解析結果を使って見出しを保存します。3回目は両方の結果を再利用して、同じ見出しを返します。

## 再利用する単位を、仕事に合わせて決める

入力、設定、処理の版が変われば、以前の結果を使ってよいとは限りません。「同じ依頼の結果を使う」メモ化と、「起きた操作の順序を再現する」履歴の再生は、識別する単位が異なります。

[カードゲームの記事](doeff-games.md)では、初期状態・入力順序・乱数の結果・ルールの版を保存して対局を再構成します。同じカードを使う行動でも、1ターン目と2ターン目では別の出来事です。単純な引数だけのメモ化が、そのままゲームの保存形式になるわけではありません。

どこを保存可能な仕事の単位として切るかを決め、その結果の扱いをハンドラで選ぶ。共通の`@do`を使うと、この設計を通常の処理の合成とつなげられます。[ハンドラの合成](doeff-handlers.md)や[記録・再生](doeff-replay.md)でも、依頼と結果の境界を使った組み合わせを紹介しています。

## 参考資料・検証版

導入方法は[公式README](https://github.com/proboscis/doeff#installation)を参照してください。以下はこの記事で確認した開発版へのリンクです。公開パッケージの最新版との一致は別途確認が必要です。

- [doeff本体と導入方法](https://github.com/proboscis/doeff/tree/d4705914e39740aee98a9f57a4535c463d9479cc)
- [関数名と引数をキーにするcache](https://github.com/proboscis/doeff/blob/d4705914e39740aee98a9f57a4535c463d9479cc/packages/doeff-core-effects/doeff_core_effects/cache.py)
- [結果を保存・再利用するハンドラ](https://github.com/proboscis/doeff/blob/d4705914e39740aee98a9f57a4535c463d9479cc/packages/doeff-core-effects/doeff_core_effects/memo_handlers.py)
- [時刻と乱数の履歴](https://github.com/proboscis/doeff/blob/d4705914e39740aee98a9f57a4535c463d9479cc/packages/doeff-conductor/src/doeff_conductor/workflow_effect_journal.py)

---

[doeffとは？：メイン記事へ戻る](doeff-main.md)
