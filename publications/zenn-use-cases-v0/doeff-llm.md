---
title: "LLMへの依頼をエフェクトにする — @doで文書制作を組み立てる"
emoji: "🔁"
type: "tech"
topics: ["python", "doeff", "設計"]
published: false
---

LLMを使う処理には、構造化された返答、文章の生成、ストリームの収集、埋め込みなどが登場します。doeffでは、これらをエフェクトとして依頼し、処理全体を`@do`の関数で組み立てられます。

```python
from doeff import do  # 関数からProgramを作るデコレータを使う。
from doeff_llm.effects import LLMChat  # チャット応答を依頼するエフェクトを使う。

@do  # 呼び出し時に合成可能なProgramを作る。
def summarize(text: str, model: str):  # 文章とモデルを受け取る要約用Programを定義する。
    return (yield LLMChat(  # チャットの応答を受け取り、この段階の結果として返す。
        messages=[{"role": "user", "content": f"次の文章を要約してください。\n{text}"}],  # 要約対象の文章をユーザーメッセージとして渡す。
        model=model,  # 呼び出し元で選んだモデルへ依頼する。
    ))  # 指定した値の組み立てをここで閉じる。
```

この関数は`Program`を作ります。実行時にハンドラが`LLMChat`を受け取り、SDKを呼ぶか、テスト用の返答を返すかを決めます。

![LLMへの依頼を、ハンドラで解釈する](/images/zenn-use-cases-v0/generated/llm-concept.png)

共通のLLMChatを、取り付けたハンドラが解釈します。対応機能と返り値はハンドラごとに確認します。

## 共通なのは、LLMへ何を依頼するか

`doeff-llm`は次の4種類の依頼を提供しています。

| エフェクト | 依頼すること |
|---|---|
| `LLMChat` | メッセージから応答を作る |
| `LLMStructuredQuery` | 指定した型の構造化データを作る |
| `LLMStreamingChat` | ストリーミング形式の応答を求める |
| `LLMEmbedding` | 入力を埋め込みベクトルへ変換する |

依頼の型を共通にしても、対応機能と返り値はハンドラの実装次第です。この開発版の標準ハンドラには、次の差があります。

| プロバイダ | 通常のチャット | ストリーミング依頼の現状 | 埋め込み |
|---|---|---|---|
| OpenAI | SDKの`ChatCompletion` | SDKの非同期イテレータを返す | SDKの`CreateEmbeddingResponse` |
| Gemini | `str` | 現実装は生成した`str`を返す。チャンクを受け取るAPIではない | `list[float]`または`list[list[float]]` |
| OpenRouter | `dict` | 実処理は`NotImplementedError`を送出する | 標準ハンドラは`Pass`し、外側へ委ねる |

以下の文書制作は**OpenAI形式の応答を扱う例**です。ハンドラだけをGeminiやOpenRouterへ差し替えて、そのまま4段階全部が動く例ではありません。必要な対応機能と返り値の契約を先に確認します。

## ストリームを読む処理も、@doの関数にする

ストリームの収集自体を`@do`で書きます。`Await`に渡すのは、SDKの非同期イテレータから**次の1チャンクを取得する操作**です。

```python
from collections.abc import AsyncIterator  # SDKが返す非同期イテレータの型を使う。
from openai.types.chat import ChatCompletionChunk  # OpenAI SDKのチャンクの型を使う。

from doeff import EffectGenerator, do  # Programの定義と、返り値の型を記述する。
from doeff_core_effects import Await  # SDKの非同期待機をエフェクトとして外へ出す。

@do  # 呼び出し時に合成可能なProgramを作る。
def collect_openai_stream(stream: AsyncIterator[ChatCompletionChunk]) -> EffectGenerator[str]:  # チャンクを順に読み、完成した文字列を返す。
    iterator = aiter(stream)  # 渡されたストリームの反復を開始する。
    text = ""  # 本文の蓄積を空文字列から始める。
    while True:  # 反復終了を受け取るまで1チャンクずつ進める。
        chunk = yield Await(anext(iterator, None))  # SDKから次のチャンクを待ち、終了ならNoneを受け取る。
        if chunk is None:  # 本文のないチャンクと、反復の終了を区別する。
            return text  # 反復が終了したので、集めた本文を呼び出し元へ返す。
        if chunk.choices:  # usageだけの、choicesが空のチャンクを除く。
            content = chunk.choices[0].delta.content  # 最初の候補から本文の断片を取り出す。
            if content is not None:  # roleや終了理由だけのチャンクは本文へ加えない。
                text += content  # 本文の断片を受信順に追加する。
```

`anext(iterator, None)`は反復の終了時に`None`を返します。通常のチャンクでは本文の断片を取り出し、本文を持たないチャンクはそのまま次へ進みます。通信や解析の異常は、終了と見なさず呼び出し側へ伝わります。

呼び出す側も、普通の`@do`関数の合成です。

```python
from doeff_llm.effects import LLMStreamingChat  # ストリーミング応答を依頼するエフェクトを使う。

@do  # 呼び出し時に合成可能なProgramを作る。
def introduce_openai_article(body: str, model: str) -> EffectGenerator[str]:  # 本文から紹介文を作り、ストリームを収集する。
    stream = yield LLMStreamingChat(  # OpenAI形式の非同期イテレータを受け取る。
        messages=[{"role": "user", "content": f"読者向けの紹介文を作ってください: {body}"}],  # 本文を紹介文生成の入力へ渡す。
        model=model,  # 呼び出し元で選んだモデルへ依頼する。
    )  # 指定した値の組み立てをここで閉じる。
    return (yield collect_openai_stream(stream))  # 収集用Programをyieldして、紹介文の文字列を受け取る。
```

次のチャンクをいつ待つか、受け取った本文をどう処理するかは`@do`の中に残ります。各待機がエフェクトとして外へ出るため、待機の間にログや検査などを加えるときも、同じ処理の書き方で拡張できます。

## 構成・本文・紹介文・検索用データをつなぐ

残りの段階も小さな`@do`関数にします。上で定義した`collect_openai_stream`と`introduce_openai_article`を使い、4種類の依頼を文書制作として合成します。

```python
from pydantic import BaseModel  # 構造化応答の型と検証条件を定義する。

from doeff import Program  # 実行前の処理を表すProgramの型を使う。
from doeff_core_effects import Ask  # 共有モデルの設定を外側から受け取る。
from doeff_llm.effects import LLMChat, LLMEmbedding, LLMStructuredQuery  # 本文・埋め込み・構成の依頼を使う。

class ArticlePlan(BaseModel):  # 記事構成の返り値の形を決める。
    title: str  # 構成には記事タイトルを含める。
    sections: list[str]  # 構成には見出しのリストを含める。


@do  # 呼び出し時に合成可能なProgramを作る。
def plan_article(topic: str, model: str) -> EffectGenerator[ArticlePlan]:  # 題材からArticlePlanを作る段階を定義する。
    return (  # 次の依頼の結果を、この段階の結果として返す。
        yield LLMStructuredQuery(  # 指定型に合う構造化応答をハンドラへ依頼する。
            messages=[{"role": "user", "content": f"記事の構成を作ってください: {topic}"}],  # 構成を作る題材をメッセージへ入れる。
            model=model,  # 呼び出し元で選んだモデルへ依頼する。
            response_format=ArticlePlan,  # タイトルと見出しを持つArticlePlanを期待する。
        )  # 指定した値の組み立てをここで閉じる。
    )  # 指定した値の組み立てをここで閉じる。


@do  # 呼び出し時に合成可能なProgramを作る。
def draft_openai_article(plan: ArticlePlan, model: str) -> EffectGenerator[str]:  # 構成から本文を作り、OpenAI応答から文字列を取り出す。
    draft = yield LLMChat(  # 本文生成を依頼し、チャット応答を受け取る。
        messages=[{"role": "user", "content": f"この構成で本文を書いてください: {plan}"}],  # 前段で得た構成を本文の入力へ渡す。
        model=model,  # 呼び出し元で選んだモデルへ依頼する。
    )  # 指定した値の組み立てをここで閉じる。
    if not draft.choices or draft.choices[0].message.content is None:  # 文字列の本文を返せる応答か検査する。
        raise ValueError("本文のテキストが返されませんでした")  # 本文がない応答を正常な記事として扱わない。
    return draft.choices[0].message.content  # 検査した本文の文字列を次の段階へ渡す。


@do  # 呼び出し時に合成可能なProgramを作る。
def embed_openai_article(body: str, model: str) -> EffectGenerator[list[float]]:  # 本文1件の埋め込みベクトルを作る。
    embedding = yield LLMEmbedding(input=body, model=model)  # 本文の埋め込みを依頼し、OpenAI形式の応答を受け取る。
    if len(embedding.data) != 1:  # 本文1件に対して結果も1件か検査する。
        raise ValueError("本文1件に対応する埋め込みを期待しています")  # 件数が契約と違う結果をそのまま使わない。
    return embedding.data[0].embedding  # 応答から数値ベクトルを取り出して返す。


@do  # 呼び出し時に合成可能なProgramを作る。
def write_article(topic: str) -> EffectGenerator[tuple[ArticlePlan, str, str, list[float]]]:  # 4段階を合成し、制作したデータをまとめて返す。
    model = yield Ask("chat_model")  # 共有するチャットモデルの設定を受け取る。
    embedding_model = yield Ask("embedding_model")  # 埋め込み用のモデル設定を受け取る。
    plan = yield plan_article(topic, model)  # 構成用Programを実行し、ArticlePlanを受け取る。
    body = yield draft_openai_article(plan, model)  # 構成を渡し、本文の文字列を受け取る。
    introduction = yield introduce_openai_article(body, model)  # 本文を渡し、収集済みの紹介文を受け取る。
    vector = yield embed_openai_article(body, embedding_model)  # 同じ本文から検索用のベクトルを作る。
    return plan, body, introduction, vector  # 後続で利用できるよう4つの成果を返す。


p_article: Program[tuple[ArticlePlan, str, str, list[float]]] = write_article("カードゲーム")  # 題材を固定したProgramを作る。この時点ではAPIを呼ばない。
```

`ArticlePlan`を本文生成へ渡し、その本文を紹介文と埋め込みの入力に使っています。本文が返されなかった場合や、埋め込みの件数が契約と違う場合はエラーにします。処理対象は引数で渡し、共有するモデル設定は`Ask`で外側から受け取ります。

## 同じ処理を、テスト用ハンドラで動かす

外部APIを使わず、用意した返答を返すハンドラで組み合わせを確認できます。`run()`はこの検証の境界にだけ置き、各段階の中からは呼びません。

```python
from doeff import run  # テストの境界でProgramを実行する。
from doeff_core_effects import reader  # Askに返す環境を外側から取り付ける。
from doeff_core_effects.handlers import await_handler  # Awaitをスケジューラへ接続するハンドラを使う。
from doeff_core_effects.scheduler import scheduled  # 待機を進めるスケジューラを使う。
from doeff_openai.handlers.testing import MockOpenAIConfig, MockOpenAIState, mock_handlers  # 返答を固定し、依頼履歴を調べる。

config = MockOpenAIConfig(  # 各エフェクトへ返すテスト用の値を指定する。
    structured_responses=[{"title": "カードの遊び方", "sections": ["準備", "手順"]}],  # 構成の固定応答を用意する。
    chat_responses=["カードを配り、順に遊びます。"],  # 本文の固定応答を用意する。
    streaming_responses=["まずは 一局"],  # 複数のチャンクになる紹介文を用意する。
    embedding_vectors=[[0.1, 0.2, 0.3]],  # 結果の受け渡しを確認するテスト用ベクトルを用意する。
)  # 指定した値の組み立てをここで閉じる。
state = MockOpenAIState()  # 依頼の回数と入力を記録する場所を作る。
program = mock_handlers(config=config, state=state)(p_article)  # APIに接続しないテスト用ハンドラを取り付ける。
program = reader(env={"chat_model": "gpt-example", "embedding_model": "text-embedding-example"})(program)  # テスト用の共有モデル設定を注入する。
plan, body, introduction, vector = run(scheduled(await_handler()(program)))  # 待機とスケジューラを設置し、検証の境界で実行する。
assert plan.sections == ["準備", "手順"]  # 指定した構成がArticlePlanとして返ることを確かめる。
assert body == "カードを配り、順に遊びます。"  # 本文抽出でテキストが変わらないことを確かめる。
assert introduction == "まずは 一局"  # 紹介文のチャンクが正しい順に結合されたことを確かめる。
assert vector == [0.1, 0.2, 0.3]  # 応答から埋め込みベクトルを取り出せたことを確かめる。
assert str(plan) in state.calls[1]["messages"][0]["content"]  # 構成が本文生成の入力へ渡ったことを確かめる。
assert body in state.calls[2]["messages"][0]["content"]  # 本文が紹介文生成の入力へ渡ったことを確かめる。
assert state.calls[3]["input"] == body  # 同じ本文が埋め込みの入力へ渡ったことを確かめる。
```

`await_handler()`はSDKの非同期待機をスケジューラへ接続し、`scheduled`がその待機を処理します。このモデル名とベクトルはテスト用です。

[完全な例と検証](examples/llm_pipeline.py)には、OpenAI SDKの実際のストリームデコーダへローカルのイベントデータを渡す検証もあります。チャンクごとに`Await`が発生すること、本文を持たないチャンクを扱えること、SDKのエラーが呼び出し側へ伝わることを確認しています。HTTP通信は行いません。

## プロバイダのハンドラは外側に取り付ける

ハンドラを選ぶコードは処理本体から分離できます。この開発版では、OpenAIとOpenRouterのファクトリは生のハンドラ関数、Geminiのファクトリは`Program`へ取り付ける関数を返します。

```python
from doeff import handler  # 生のハンドラ関数をProgramへ取り付ける。
from doeff_openai.handlers.production import production_handlers as openai_handlers  # OpenAI用の生のハンドラ関数を得る。
from doeff_gemini.handlers.production import production_handlers as gemini_handlers  # Gemini用の取り付け関数を得る。
from doeff_openrouter.handlers.production import production_handlers as openrouter_handlers  # OpenRouter用の生のハンドラ関数を得る。

with_openai = handler(openai_handlers())  # OpenAI用ハンドラを取り付け関数へ変換する。
with_gemini = gemini_handlers()  # Geminiのファクトリは取り付け関数を直接返す。
with_openrouter = handler(openrouter_handlers())  # OpenRouter用ハンドラを取り付け関数へ変換する。

# 上で定義したsummarizeのProgramへ、OpenAI用ハンドラを取り付ける。
# この行はProgramを作るだけで、APIを実行しない。
p_openai_summary = with_openai(summarize("カードゲームのルールを説明する文章", "gpt-example"))  # 要約のProgramへOpenAI用ハンドラを設置する。実行はしない。
```

これは取り付け方のコードです。本番実行には実在する対応モデル、認証、SDKクライアント、ログ・状態・コスト・非同期待機を処理する外側の構成が必要です。上の`gpt-example`はテスト用の名前で、そのまま本番APIへ渡す設定ではありません。本稿では本番APIを実行していません。

たとえば通常チャットの応答の取り出し方だけでも、OpenAIの`response.choices[0].message.content`、Geminiの`response`、OpenRouterの`response["choices"][0]["message"]["content"]`という差があります。共通エフェクトはこの差を自動で消す仕組みではありません。用途に合わせて返り値を揃える境界を設計する話は、[エフェクトの切り方](doeff-boundaries.md)で扱います。

## 処理の流れ

![ストリームの収集も、@doで合成する](/images/zenn-use-cases-v0/generated/llm-flow.png)

OpenAIのストリームを1チャンクずつAwaitし、反復と本文の収集を@doの中で進めます。

複数件を処理する例は[コレクション処理](doeff-traverse.md)、完了した結果を再利用する例は[永続実行](doeff-durable.md)へ進めます。

## 実装・実例を読む

- [共通のエフェクト](https://github.com/proboscis/doeff/tree/d4705914e39740aee98a9f57a4535c463d9479cc/packages/doeff-llm/src/doeff_llm/effects)
- [OpenAIのハンドラ](https://github.com/proboscis/doeff/blob/d4705914e39740aee98a9f57a4535c463d9479cc/packages/doeff-openai/src/doeff_openai/handlers/production.py)
- [Geminiのハンドラ](https://github.com/proboscis/doeff/blob/d4705914e39740aee98a9f57a4535c463d9479cc/packages/doeff-gemini/src/doeff_gemini/handlers/production.py)
- [OpenRouterのチャット実装](https://github.com/proboscis/doeff/blob/d4705914e39740aee98a9f57a4535c463d9479cc/packages/doeff-openrouter/src/doeff_openrouter/chat.py)

この草稿は上記の開発版を参照しています。

[メイン記事へ戻る](doeff-main.md)
