---
title: "AIエージェントの起動と結果待ちを、doeffの処理として書く"
emoji: "🔁"
type: "tech"
topics: ["python", "doeff", "ai", "agent", "testing"]
published: false
---

AIエージェント2つにレビューを並べて頼み、結果を合わせる処理を書いたとします。AIエージェントとは、Claude CodeやCodexのように自分で作業を進めるAIです。

この処理のテストは書きにくいです。

- 本物のエージェントを起こすと遅く、答えも毎回変わります。
- 入力待ちや時間切れの分岐は、狙って起こせません。
- 起動のコードが処理に散ると、テストで差し替える所が増えます。

doeffは、処理の読み書きや待ちを、外側の部品で差し替えるPythonのライブラリです。`doeff-agents`を使うと、エージェントの起動と結果待ちを、処理からの依頼として書けます。

```python
from doeff import do  # 依頼を出す処理を定義する。
from doeff_agents.effects import AgentSpec, AwaitResult, LaunchSession, ReleaseSession  # セッションへの依頼。

@do  # 呼んだ時点では起動せず、実行時にハンドラへ依頼を渡す。
def delegate_task(spec: AgentSpec):  # 依頼文と結果の形を持つ仕様を受け取る。
    session = yield LaunchSession(spec)  # 起動を頼み、セッションのハンドルを受け取る。
    outcome = yield AwaitResult(session)  # 結果を待ち、終わり方と結果を受け取る。
    yield ReleaseSession(session)  # 使い終えたセッションの解放を頼む。
    return outcome  # 成否は呼び出し側が確かめる。成功と決めつけない。
```

この3つの`yield`は、どれもまだ何もしません。依頼を外側へ出し、答えが来るまで止まるだけです。

![同じ処理に、外側でハンドラを1つ付ける](/images/zenn-use-cases-v0/generated/agents-concept.png)

図の3つのハンドラのどれを付けても、処理の本体は同じです。依頼にどう答えるかだけが替わります。

## 依頼と、答える部品を分ける

doeffの言葉を4つだけ押さえます。

- **エフェクト**: 処理が外へ出す「これをしてほしい」という依頼の値です。`LaunchSession(spec)`もその1つです。
- **ハンドラ**: 届いた依頼に、何をして何を返すかを決める部品です。
- **`@do`**: 付けた関数は呼んでもすぐには動かず、動かす手順を返します。この手順をProgramと呼びます。
- **`yield`**: 依頼を出して止まります。ハンドラの答えを受け取ると、続きが動きます。

セッションとは、エージェントを1回起こしてから解放するまでの作業の単位です。`AgentSpec`の`run_id`と`node_id`と`attempt`から、セッションIDが決まります。

本物のエージェントを起こさない、いちばん小さなハンドラを書いてみます。決めた答えを、待たずにすぐ返します。

```python
from pathlib import Path  # 作業場所の型。

from doeff import Pass, Resume, handler, run, with_handlers  # ハンドラの定義と実行に使う。
from doeff_agents import AgentType  # 起動するCLIの種類。
from doeff_agents.effects import (  # 依頼の型と、答えに使う値の型。
    AwaitOutcome, AwaitResultEffect, AwaitStatus,  # 結果待ちの依頼と、その答え。
    LaunchSessionEffect, ReleaseSessionEffect, SessionHandle,  # 起動と解放の依頼、ハンドル。
)

RESULT_SCHEMA = {  # 欲しい結果の形をJSON Schemaで決める。
    "type": "object",  # 結果は辞書。
    "required": ["summary", "ok"],  # 要約と可否を必ず持つ。
    "properties": {"summary": {"type": "string"}, "ok": {"type": "boolean"}},  # 値の型。
    "additionalProperties": False,  # ほかの項目は許さない。
}

@do  # 仕様の準備も、yieldで合成できる処理にする。
def review_spec(node: str, prompt: str):  # 作業名と依頼文から1件分の仕様を作る。
    return AgentSpec(  # 仕様を返すだけで、エージェントはまだ起こさない。
        run_id="article-example", node_id=node, attempt=0,  # この3つからセッションIDが決まる。
        agent_type=AgentType.CODEX, work_dir=Path("/example/docs"),  # 使うCLIと架空の作業場所。
        prompt=prompt, result_schema=RESULT_SCHEMA,  # 依頼文と、結果の形の約束。
    )

@handler  # Programに取り付けられるハンドラにする。
@do  # ハンドラもyieldで再開と委譲を指示する。
def fixed_agent(effect, k):  # effectは届いた依頼、kは止まった処理の続き。
    if isinstance(effect, LaunchSessionEffect):  # 起動の依頼なら、
        return (yield Resume(k, SessionHandle(effect.spec.session_id)))  # 起こさずハンドルだけ返す。
    if isinstance(effect, AwaitResultEffect):  # 結果待ちの依頼なら、
        outcome = AwaitOutcome(AwaitStatus.EXITED, result={"summary": "読みやすい", "ok": True})  # 決めた答え。
        return (yield Resume(k, outcome))  # 待たずに、その答えで続きを動かす。
    if isinstance(effect, ReleaseSessionEffect):  # 解放の依頼なら、
        return (yield Resume(k, None))  # 解放する物が無いのでNoneを返す。
    return (yield Pass(effect, k))  # 担当しない依頼は外側のハンドラへ渡す。

@do  # 仕様の準備と依頼を1つの処理にまとめる。
def first_review():  # 冒頭のdelegate_taskを1件だけ動かす。
    spec = yield review_spec("language", "文書の読みやすさを確認")  # 仕様を受け取る。
    return (yield delegate_task(spec))  # 冒頭の処理をそのまま呼ぶ。

outcome = run(with_handlers([fixed_agent], first_review()))  # 自作のハンドラを付けて動かす。
assert outcome.status is AwaitStatus.EXITED  # 終わったという答えが届いた。
assert outcome.result == {"summary": "読みやすい", "ok": True}  # 決めた結果がそのまま届いた。
```

ハンドラの中では、次の3つの書き方を使います。

- `isinstance`で、届いた依頼の種類を見分けます。
- `Resume(k, 値)`は、止まった処理へ値を返して続きを動かします。
- 担当しない依頼は、`Pass(effect, k)`で外側のハンドラへ渡します。

`with_handlers`でハンドラを付け、`run`で動かします。冒頭の`delegate_task`は、1文字も変えていません。

## 結果はAwaitOutcomeで受け取る

`AwaitResult`への答えは`AwaitOutcome`です。結果だけでなく、どう終わったかも一緒に届きます。

| 欄 | 中身 |
| --- | --- |
| `status` | `EXITED`(終わった)、`AWAITING_INPUT`(入力待ち)、`TIMED_OUT`(時間切れ)の3つ |
| `result` | `result_schema`に合った結果です。無い時は`None`です |
| `validation_error` | 結果がschemaに合わなかった時の説明です |
| `exit_code` | 終わりのコードです。分かる時だけ入ります |
| `continuable` | 追加の指示を受け付けられるかです |

- schemaの検査と、合わない時の再試行は、本番のハンドラが受け持ちます。
- 結果をファイルに書かせて探す処理は要りません。`result`だけを読みます。
- schemaに合わない結果は、`status`ではなく`validation_error`で分かります。

## 2件のレビューを並べて進め、結果を合わせる

文章とコード例を、別々のエージェントに確かめてもらいます。`checked_review`が1件分の判断で、`review_document`が2件を並べます。

```python
from doeff_agents.effects import FollowUp, StopSession  # 追加の指示と、止める依頼。
from doeff_core_effects.scheduler import Spawn, Wait  # 子の処理を始める依頼と、終わりを待つ依頼。

@do  # 起動から結果判断までを1つのProgramとして呼び出せるようにする。
def checked_review(spec: AgentSpec, timeout_seconds: float = 60):  # 1件のレビュー結果を返す。
    session = yield LaunchSession(spec)  # ハンドラが起動または再取得し、SessionHandleを返す。
    outcome = yield AwaitResult(session, timeout_seconds=timeout_seconds)  # 状態と結果を待つ。
    if outcome.status is AwaitStatus.AWAITING_INPUT and outcome.continuable:  # 追加指示が可能か判定。
        yield FollowUp(session, "与えた文書だけを対象に確認してください")  # 対象を補足して続行を促す。
        outcome = yield AwaitResult(session, timeout_seconds=timeout_seconds)  # 補足後の結果を待つ。
    if outcome.status is not AwaitStatus.EXITED:  # 入力待ちが続く場合とタイムアウトは未完了とする。
        yield StopSession(session, reason="レビューを完了できませんでした")  # 後続へ進まず停止する。
        yield ReleaseSession(session)  # 停止したセッションに対応する資源を解放する。
        raise RuntimeError(f"レビュー未完了: {outcome.status.value}")  # 呼び出し元へ失敗を伝える。
    if (  # 終了していても、結果の欠落・検証失敗・異常終了があれば利用しない。
        outcome.result is None  # 結果なしを成功扱いしない。
        or outcome.validation_error is not None  # ハンドラが報告した検証エラーを確認する。
        or outcome.exit_code not in (None, 0)  # 終了コードが提供された場合は0だけを認める。
    ):
        yield ReleaseSession(session)  # 終了済みの失敗でも資源を解放する。
        raise RuntimeError("レビュー結果を利用できません")  # 利用可能な結果がないことを伝える。
    yield ReleaseSession(session)  # 正常終了でも資源の利用を終える。
    return outcome.result  # summaryとokを持つ辞書を返す。ok=Falseも有効なレビュー結果。

@do  # 2件のレビューと最終判断を同じProgramに合成する。
def review_document(timeout_seconds: float = 60):  # 文章とコードの両方を確認する。
    language_spec = yield review_spec("language", "文書の読みやすさを確認")  # 文章用の仕様を取得。
    example_spec = yield review_spec("examples", "文書のコード例を確認")  # コード用の仕様を取得。
    language = yield Spawn(checked_review(language_spec, timeout_seconds))  # 文章の子タスクを開始。
    examples = yield Spawn(checked_review(example_spec, timeout_seconds))  # コードの子タスクも開始。
    language_result = yield Wait(language)  # 文章の結果が返るまで、この計算を待機させる。
    example_result = yield Wait(examples)  # コードの結果も取得する。起動は上の行で済んでいる。
    return {  # 両方の返り値から公開用の判断材料を組み立てる。
        "ready": language_result["ok"] and example_result["ok"],  # 両方Trueの場合だけ準備完了。
        "summaries": [language_result["summary"], example_result["summary"]],  # 判断理由を残す。
    }
```

`checked_review`は、届いた答えを次の順に見ます。

- 入力待ちで続けられる時だけ、追加の指示を1回送ります。
- 終わらなければ、止めて解放してから例外にします。
- 結果が無い時、schemaに合わない時、終わりのコードが0でない時は、結果を使いません。
- 上のどの道でも、最後にセッションを解放します。
- ハンドラ自身の例外や、親の処理の取り消しの時の解放は、この例では扱いません。

`review_document`は`Spawn`を2回出してから`Wait`するので、2件は並んで進みます。`ok=False`も正しい形の結果で、出せるかどうかは処理の側が決めます。

![2件のレビューを並べて進め、結果を合わせる](/images/zenn-use-cases-v0/generated/agents-flow.png)

2件を始めてから、それぞれの結果を待ちます。次の節のテストでは文章がok=True、コードがok=Falseなので、ready=Falseです。

## エージェントの操作だけをテスト用のハンドラへ替える

`doeff-agents`には、テスト用の`ScenarioAgentHandler`が付いています。セッションIDごとに、返す答えの列を決めておけます。

```python
from doeff_agents.handlers.testing import ScenarioAgentHandler, ScenarioStep  # テスト用のハンドラ。
from doeff_core_effects.scheduler import scheduled  # SpawnとWaitに答えるスケジューラを付ける。

testing = ScenarioAgentHandler(scripts={  # セッションIDごとに、答えの列を決める。
    "article-example-language-0": [ScenarioStep.success({"summary": "文章を確認", "ok": True})],  # すぐ終わる。
    "article-example-examples-0": [  # こちらは入力待ちを挟む。
        ScenarioStep.awaiting_input("対象の確認が必要"),  # 1回目のAwaitResultは入力待ち。
        ScenarioStep.success({"summary": "コードを確認", "ok": False}),  # FollowUpの後に結果を返す。
    ],
})
result = run(scheduled(testing.wrap(review_document(timeout_seconds=0))))  # シナリオの答えで動かす。
assert result == {"ready": False, "summaries": ["文章を確認", "コードを確認"]}  # 片方がok=False。
assert len(testing.follow_up_messages("article-example-examples-0")) == 1  # 追加の指示は1回だけ。
assert len(testing.released_sessions) == 2  # 2つのセッションとも解放された。
```

このテストで本物として動く物と、シナリオで決めた物を分けておきます。

| 本物として動く物 | シナリオで決めた物 |
| --- | --- |
| `@do`の処理と、分岐のコード | エージェントの起動 |
| スケジューラ(`Spawn`と`Wait`に答える部品) | エージェントの答えと終わり方 |
| 追加の指示と解放の依頼の数 | 待ち時間(0秒にしてある) |

- `ScenarioAgentHandler`は決めた答えをそのまま返し、schemaの検査も再試行もしません。
- 時間切れ、結果なし、schema違反の3つの場合は、[完全な例](examples/agents_workflow.py)で確かめています。
- 待ち時間を0にしたのは、シナリオの答え1つごとに分岐を見るためです。実際の時間の流れは確かめていません。

## 本番への接続は外側に置く

本物のセッションには、公開のハンドラ`agent_effectful_handler()`を付けます。処理の本体は、テストの時と同じ`checked_review`です。

```python
from dataclasses import replace  # 仕様の一部だけを替えた写しを作る。

from doeff import McpToolDef  # @doの関数をMCPの道具として公開する定義。
from doeff_agents.handlers import agent_effectful_handler  # 本物のセッションを扱う公開のハンドラ。
from doeff_agents.session_backend import SessionBackend, default_session_backend  # 端末への接続先。
from doeff_core_effects import Ask, reader  # 値を頼む依頼と、それに答えるreader。
from doeff_core_effects.handlers import await_handler, slog_discard_handler  # 非同期の待ちとログ。
from doeff_time.handlers.async_time import async_time_handler  # 時間の依頼を実時間で答える。

@do  # 道具の中身も、yieldで依頼を出せる処理にする。
def read_document():  # エージェントが引数なしで呼ぶ道具。
    return (yield Ask("document_text"))  # 文書の中身を外側に頼む。

read_tool = McpToolDef(  # 名前・説明・引数・中身をまとめる。ここではサーバーを起こさない。
    name="read_document", description="レビューする文書を読む", params=(), handler=read_document,  # 定義。
)

@do  # 本番の組み立ても、yieldされるまで動かない。
def live_review(work_dir: Path, document_text: str):  # 実在する作業場所と文書を受け取る。
    spec = yield review_spec("document", "read_documentで文書を読み、確認してください")  # 仕様を作る。
    spec = replace(spec, work_dir=work_dir, mcp_tools=(*spec.mcp_tools, read_tool))  # 場所と道具を足す。
    program = agent_effectful_handler()(checked_review(spec))  # エージェントの依頼を本物へ繋ぐ。
    program = reader(env={  # 内側から来るAskに答える。
        SessionBackend: default_session_backend(),  # 端末への接続先を渡す。
        "document_text": document_text,  # read_documentへ返す文書。
    })(program)  # 内側のProgramへ環境を付ける。
    program = async_time_handler()(program)  # 待ちの間の時間の依頼を実時間で答える。
    program = await_handler()(slog_discard_handler(program))  # 非同期の待ちとログに答える。
    return (yield scheduled(program))  # スケジューラを付けて動かし、結果を返す。
```

- `read_document`はMCPの道具です。MCPは、エージェントが外の道具を呼ぶための約束です。
- 道具の中身も`@do`の処理なので、文書の出どころは外側で選べます。ここでは`Ask`に答える`reader`が渡します。
- 本番のハンドラは端末の中でCLIを起こすので、tmuxが要ります。tmuxは、端末を分けて動かす道具です。
- 認証にはClaude CodeやCodex自身のログインを使います。LLMのAPIキーを仕様へ渡しません。

テストと本番で違うのは、外側に並べたハンドラだけです。`checked_review`と`review_document`は、どちらでも同じコードです。

## まとめ

- エージェントの起動、結果待ち、解放を、`yield`で出す依頼として書きました。
- 依頼への答え方は、外側のハンドラで選びます。自作、テスト用、本番の3つを見ました。
- 結果は`AwaitOutcome`で受け取り、終わり方と検査の結果を見てから使います。
- MCPの道具も、同じ`@do`の処理としてエージェントへ渡せます。

CLIを処理の中から直に呼ぶ書き方と比べると、違いは次の表のとおりです。

| 比べる所 | CLIを処理の中から直に呼ぶ | 依頼として書く |
| --- | --- | --- |
| テストで替える所 | 起動する関数ごとに、mockを当てます | 外側のハンドラを1つ替えます |
| 入力待ちと時間切れ | 本物で起きるのを待ちます | シナリオに1行書きます |
| 結果の受け取り | 出力やファイルを、自分で探します | `AwaitOutcome`の`result`を読みます |
| 本番への接続 | 処理のあちこちに散ります | 外側の1か所にまとまります |

## 参考

この記事のコードは、2026年10月17日に、PyPIのdoeff 0.6.0で確かめました。Pythonは3.12と3.14で試しました。GitHubにあるdoeffのmain（commit `9adc4f8`）でも、同じく動きました。

doeff-agentsと、それが使う3つの部品は、0.6.0と組める版がPyPIにありません。そこで、この4つだけをGitHubから入れます。`--no-sources`を付けないと、doeff本体もGitHubから入り、Rustのビルドが要ります。完全な例を`agents_workflow.py`として保存し、次のコマンドで実行します。

```bash
G="git+https://github.com/proboscis/doeff@9adc4f8#subdirectory=packages"  # PyPIに無い4つの部品の在りか。
uv run --no-sources --with "doeff==0.6.0" --with "doeff-time @ $G/doeff-time" --with "doeff-codex @ $G/doeff-codex" --with "doeff-claude-code @ $G/doeff-claude-code" --with "doeff-agents @ $G/doeff-agents" python agents_workflow.py  # doeff本体はPyPI、4つの部品だけGitHubから入れて、完全な例を実行する。
```

- 掲載したPythonのコード5つと完全な例を、上のコマンドの版で走らせました。
- 動かしたのは、自作のハンドラとテスト用のハンドラだけです。本物のエージェントは起こしていません。
- 本番の組み立ては、tmuxの無い環境で、端末への接続を作る所で止まることだけを確かめました。
- PyPIのdoeff-agentsの0.1.1と0.1.2はdoeff 0.4向けで、この記事のAPIがありません。
- 完全な例は、失敗の分岐を確かめる途中で、doeffのtracebackを3つ表示します。最後にOKが出れば成功です。

- [完全な例(自作ハンドラ、テスト、本番の組み立て)](examples/agents_workflow.py)
- [セッションの仕様と、結果の型](https://github.com/proboscis/doeff/blob/9adc4f8e657a552f5253d8a7b422363789c2608c/packages/doeff-agents/src/doeff_agents/effects/agent.py)
- [テスト用のハンドラの実装](https://github.com/proboscis/doeff/blob/9adc4f8e657a552f5253d8a7b422363789c2608c/packages/doeff-agents/src/doeff_agents/handlers/testing.hy)
- [本番のハンドラの入口](https://github.com/proboscis/doeff/blob/9adc4f8e657a552f5253d8a7b422363789c2608c/packages/doeff-agents/src/doeff_agents/handlers/__init__.py)
- 前の回:[doeffとは？(エフェクトとハンドラの入門)](doeff-main.md)
- 次の回:[20秒待つ処理を、20秒待たずにテストする](doeff-time.md)
