"""4種類のLLMエフェクトをつなぎ、テスト用OpenAIハンドラで動かす。"""

from collections.abc import AsyncIterator  # SDKが返す非同期イテレータの型を使う。

from doeff_core_effects import Ask, Await, reader  # モデルの注入・SDKの待機・環境の取り付けを使う。
from doeff_core_effects.handlers import (  # SDKの待機を処理するハンドラを読み込む。
    await_handler,  # Awaitをスケジューラへ接続するハンドラを使う。
)
from doeff_core_effects.scheduler import scheduled  # 待機を進めるスケジューラを使う。
from doeff_llm.effects import (  # 4種類のLLMへの依頼を使う。
    LLMChat,  # 本文生成の依頼を表すエフェクトを使う。
    LLMEmbedding,  # 埋め込み生成の依頼を表すエフェクトを使う。
    LLMStreamingChat,  # チャンク列を受け取る依頼を表すエフェクトを使う。
    LLMStructuredQuery,  # 型を指定した構造化応答の依頼を使う。
)
from doeff_openai.handlers.testing import (  # 返答を固定し、依頼履歴を調べる。
    MockOpenAIConfig,  # 各依頼へ返す固定応答の設定型を使う。
    MockOpenAIState,  # 依頼の入力と回数を記録する状態型を使う。
    mock_handlers,  # 固定応答を返すハンドラの取り付け関数を使う。
)
from openai.types.chat import ChatCompletionChunk  # OpenAI SDKのチャンクの型を使う。
from pydantic import BaseModel  # 構造化応答の型と検証条件を定義する。

from doeff import EffectGenerator, Program, do, run  # 処理の定義・合成と、検証時の実行を使う。


class ArticlePlan(BaseModel):  # 記事構成の返り値の形を決める。
    title: str  # 構成には記事タイトルを含める。
    sections: list[str]  # 構成には見出しのリストを含める。


@do  # 呼び出し時に合成可能なProgramを作る。
def collect_openai_stream(  # チャンク収集を、合成できる処理として定義する。
    stream: AsyncIterator[ChatCompletionChunk],  # SDKが返す非同期チャンク列を受け取る。
) -> EffectGenerator[str]:  # チャンクを順に読み、完成した文字列を返す。
    iterator = aiter(stream)  # 渡されたストリームの反復を開始する。
    text = ""  # 本文の蓄積を空文字列から始める。
    while True:  # 反復終了を受け取るまで1チャンクずつ進める。
        # 非同期SDKとの境界は「次の1チャンクを待つ」操作だけ。
        chunk = yield Await(  # 次のチャンクだけを待機する依頼として渡す。
            anext(iterator, None)  # 反復を1回進め、終了時はNoneを受け取る。
        )  # SDKから次のチャンクを待ち、終了ならNoneを受け取る。
        if chunk is None:  # 本文のないチャンクと、反復の終了を区別する。
            return text  # 反復が終了したので、集めた本文を呼び出し元へ返す。
        if chunk.choices:  # usageだけの、choicesが空のチャンクを除く。
            content = chunk.choices[0].delta.content  # 最初の候補から本文の断片を取り出す。
            if content is not None:  # roleや終了理由だけのチャンクは本文へ加えない。
                text += content  # 本文の断片を受信順に追加する。


@do  # 呼び出し時に合成可能なProgramを作る。
def plan_article(  # 題材から記事構成を作る段階を定義する。
    topic: str, model: str  # 記事の題材と使用するモデル名を受け取る。
) -> EffectGenerator[ArticlePlan]:  # 題材からArticlePlanを作る段階を定義する。
    return (  # 次の依頼の結果を、この段階の結果として返す。
        yield LLMStructuredQuery(  # 指定型に合う構造化応答をハンドラへ依頼する。
            messages=[  # 構成生成に渡す入力メッセージ列を用意する。
                {"role": "user", "content": f"記事の構成を作ってください: {topic}"}  # 題材を埋め込み、構成の生成を依頼する。
            ],  # 構成を作る題材をメッセージへ入れる。
            model=model,  # 呼び出し元で選んだモデルへ依頼する。
            response_format=ArticlePlan,  # タイトルと見出しを持つArticlePlanを期待する。
        )
    )


@do  # 呼び出し時に合成可能なProgramを作る。
def draft_openai_article(  # 記事構成から本文を生成する段階を定義する。
    plan: ArticlePlan, model: str  # 前段の構成と使用するモデル名を受け取る。
) -> EffectGenerator[str]:  # 構成から本文を作り、OpenAI応答から文字列を取り出す。
    draft = yield LLMChat(  # 本文生成を依頼し、チャット応答を受け取る。
        messages=[  # 本文生成へ渡す入力メッセージ列を用意する。
            {"role": "user", "content": f"この構成で本文を書いてください: {plan}"}  # 前段の構成を埋め込み、本文の生成を依頼する。
        ],  # 前段で得た構成を本文の入力へ渡す。
        model=model,  # 呼び出し元で選んだモデルへ依頼する。
    )
    if (  # 本文の文字列を返せない応答を検出する。
        not draft.choices or draft.choices[0].message.content is None  # 候補がない場合と本文がNoneの場合を異常とする。
    ):  # 文字列の本文を返せる応答か検査する。
        raise ValueError(  # 本文を得られなかった失敗を呼び出し元へ伝える。
            "本文のテキストが返されませんでした"  # 失敗の理由を例外メッセージに残す。
        )  # 本文がない応答を正常な記事として扱わない。
    return draft.choices[0].message.content  # 検査した本文の文字列を次の段階へ渡す。


@do  # 呼び出し時に合成可能なProgramを作る。
def introduce_openai_article(  # 本文から紹介文を生成する段階を定義する。
    body: str, model: str  # 生成済みの本文と使用するモデル名を受け取る。
) -> EffectGenerator[str]:  # 本文から紹介文を作り、ストリームを収集する。
    stream = yield LLMStreamingChat(  # OpenAI形式の非同期イテレータを受け取る。
        messages=[  # 紹介文生成へ渡す入力メッセージ列を用意する。
            {"role": "user", "content": f"読者向けの紹介文を作ってください: {body}"}  # 前段の本文を埋め込み、紹介文を依頼する。
        ],  # 本文を紹介文生成の入力へ渡す。
        model=model,  # 呼び出し元で選んだモデルへ依頼する。
    )
    return (  # 収集し終えた紹介文を、この段階の結果として返す。
        yield collect_openai_stream(stream)  # 収集用Programをyieldし、文字列の紹介文を受け取る。
    )  # 収集用Programをyieldして、紹介文の文字列を受け取る。


@do  # 呼び出し時に合成可能なProgramを作る。
def embed_openai_article(  # 本文を埋め込みベクトルへ変換する段階を定義する。
    body: str, model: str  # 本文と埋め込み用のモデル名を受け取る。
) -> EffectGenerator[list[float]]:  # 本文1件の埋め込みベクトルを作る。
    embedding = yield LLMEmbedding(  # 埋め込み生成を依頼して、OpenAI形式の応答を受け取る。
        input=body, model=model  # 前段の本文と、選択した埋め込みモデルを渡す。
    )  # 本文の埋め込みを依頼し、OpenAI形式の応答を受け取る。
    if len(embedding.data) != 1:  # 本文1件に対して結果も1件か検査する。
        raise ValueError(  # 本文と結果の対応を保証できない失敗を伝える。
            "本文1件に対応する埋め込みを期待しています"  # 期待する結果の件数を例外メッセージに残す。
        )  # 件数が契約と違う結果をそのまま使わない。
    return embedding.data[0].embedding  # 応答から数値ベクトルを取り出して返す。


@do  # 呼び出し時に合成可能なProgramを作る。
def write_article(  # 記事制作の4段階を合成する処理を定義する。
    topic: str,  # 記事の題材を文字列で受け取る。
) -> EffectGenerator[  # エフェクトをyieldし、最後に成果を返す型にする。
    tuple[ArticlePlan, str, str, list[float]]  # 構成・本文・紹介文・ベクトルの順で返す型を指定する。
]:  # 4段階を合成し、制作したデータをまとめて返す。
    model = yield Ask("chat_model")  # 共有するチャットモデルの設定を受け取る。
    embedding_model = yield Ask("embedding_model")  # 埋め込み用のモデル設定を受け取る。
    plan = yield plan_article(topic, model)  # 構成用Programを実行し、ArticlePlanを受け取る。
    body = yield draft_openai_article(plan, model)  # 構成を渡し、本文の文字列を受け取る。
    introduction = yield introduce_openai_article(  # 紹介文を作るProgramをyieldして結果を受け取る。
        body, model  # 生成済み本文と共通のチャットモデルを次段へ渡す。
    )  # 本文を渡し、収集済みの紹介文を受け取る。
    vector = yield embed_openai_article(  # 埋め込みを作るProgramをyieldしてベクトルを受け取る。
        body, embedding_model  # 同じ本文と、埋め込み専用のモデル設定を渡す。
    )  # 同じ本文から検索用のベクトルを作る。
    return plan, body, introduction, vector  # 後続で利用できるよう4つの成果を返す。


p_article: Program[tuple[ArticlePlan, str, str, list[float]]] = write_article(  # 題材を固定したProgramを組み立てる。まだ実行しない。
    "カードゲーム"  # この記事制作の題材をカードゲームに固定する。
)  # 題材を固定したProgramを作る。この時点ではAPIを呼ばない。


def verify() -> None:  # 固定応答で段階間のデータ受け渡しを検証する。
    config = MockOpenAIConfig(  # 各エフェクトへ返すテスト用の値を指定する。
        structured_responses=[  # 構造化応答の依頼へ返す固定値の列を用意する。
            {"title": "カードの遊び方", "sections": ["準備", "手順"]}  # 期待するタイトルと見出しを固定する。
        ],  # 構成の固定応答を用意する。
        chat_responses=["カードを配り、順に遊びます。"],  # 本文の固定応答を用意する。
        streaming_responses=["まずは 一局"],  # 複数のチャンクになる紹介文を用意する。
        embedding_vectors=[[0.1, 0.2, 0.3]],  # 結果の受け渡しを確認するテスト用ベクトルを用意する。
    )
    state = MockOpenAIState()  # 依頼の回数と入力を記録する場所を作る。
    program = mock_handlers(config=config, state=state)(  # 固定応答を返し、依頼履歴を残すハンドラを設置する。
        p_article  # 検証する記事制作Programをハンドラの内側へ置く。
    )  # APIに接続しないテスト用ハンドラを取り付ける。
    program = reader(  # 共有モデル設定をProgramの外側から与える。
        env={  # Askが参照するモデル設定を用意する。
            "chat_model": "gpt-example",  # チャット処理へ渡すテスト用モデル名を固定する。
            "embedding_model": "text-embedding-example",  # 埋め込み処理へ渡すテスト用モデル名を固定する。
        }  # テスト用ハンドラが扱える架空のモデル名を注入する。
    )(program)  # この環境を、上で組み立てたProgramへ適用する。
    plan, body, introduction, vector = run(  # 検証の境界で実行し、4つの成果を受け取る。
        scheduled(await_handler()(program))  # SDKの待機を処理するハンドラとスケジューラを設置する。
    )  # 待機とスケジューラを設置し、検証の境界で実行する。
    assert plan.sections == ["準備", "手順"]  # 指定した構成がArticlePlanとして返ることを確かめる。
    assert body == "カードを配り、順に遊びます。"  # 本文抽出でテキストが変わらないことを確かめる。
    assert introduction == "まずは 一局"  # 紹介文のチャンクが正しい順に結合されたことを確かめる。
    assert vector == [0.1, 0.2, 0.3]  # 応答から埋め込みベクトルを取り出せたことを確かめる。
    assert (  # 4種類の依頼の実行回数をまとめて検査する。
        state.structured_calls,  # 構造化応答の依頼回数を読む。
        state.chat_calls,  # 本文生成の依頼回数を読む。
        state.streaming_calls,  # ストリーム生成の依頼回数を読む。
        state.embedding_calls,  # 埋め込みの依頼回数を読む。
    ) == (1, 1, 1, 1)  # 各段階がちょうど1回ずつ実行されたことを確かめる。
    assert (  # 構成が本文生成の入力へ渡ったことを検査する。
        str(plan) in state.calls[1]["messages"][0]["content"]  # 記録された本文生成のメッセージに、構成が含まれると期待する。
    )  # 構成が本文生成の入力へ渡ったことを確かめる。
    assert (  # 本文が紹介文生成の入力へ渡ったことを検査する。
        body in state.calls[2]["messages"][0]["content"]  # 記録された紹介文生成のメッセージに、本文が含まれると期待する。
    )  # 本文が紹介文生成の入力へ渡ったことを確かめる。
    assert state.calls[3]["input"] == body  # 同じ本文が埋め込みの入力へ渡ったことを確かめる。


def verify_sdk_stream() -> None:  # 通信せず、本物のSDKデコーダで収集処理を検証する。
    """通信せず、OpenAI SDKの実際のSSEデコーダとチャンク型を通す。"""
    import json  # SDKへ与えるイベントをJSON文字列にする。

    import httpx  # 通信を伴わないHTTP応答オブジェクトを作る。
    from doeff_core_effects import Try  # 失敗の検証結果をErrとして受け取る。
    from doeff_core_effects.handlers import try_handler  # Tryの成功・失敗を処理するハンドラを使う。
    from openai import (  # SDKのストリームデコーダと実際の例外型を使う。
        APIError,  # ストリーム内のエラーを表すSDKの例外型を使う。
        AsyncOpenAI,  # デコーダに必要なSDKクライアントの型を使う。
        AsyncStream,  # SSEをSDKのチャンク型へ変換するストリームを使う。
    )

    from doeff import Pass, handler  # Awaitを観測し、実処理は外側のハンドラへ渡す。

    # roleだけのチャンク、本文、choicesが空のusage、終了を含める。
    deltas = [  # 本文あり・本文なし・終了のチャンクを用意する。
        [  # role通知を1候補だけ含むチャンクにする。
            {"index": 0, "delta": {"role": "assistant", "content": None}}  # 本文のないrole通知を、収集から除外するために用意する。
        ],  # roleだけのチャンクを用意する。
        [{"index": 0, "delta": {"content": "まずは "}}],  # 本文の前半を用意する。
        [],  # choicesが空のチャンクを用意する。
        [{"index": 0, "delta": {"content": "一局"}}],  # 本文の後半を用意する。
        [  # 本文を持たない終了通知を1候補だけ含むチャンクにする。
            {"index": 0, "delta": {}, "finish_reason": "stop"}  # 本文のない終了通知を、収集から除外するために用意する。
        ],  # 本文を持たない終了チャンクを用意する。
    ]
    events = [  # 各チャンクへSDKが期待するメタデータを付ける。
        {  # チャンク1件のデータを作る。
            "id": "fixture",  # テスト応答を識別するIDを付ける。
            "object": "chat.completion.chunk",  # チャットのチャンクとしてSDKに解析させる。
            "created": 0,  # 時刻を固定してテストデータを一定にする。
            "model": "gpt-example",  # 本番呼び出しに使わないテスト用モデル名を付ける。
            "choices": choices,  # 現在のチャンクの候補を入れる。
        }
        for choices in deltas  # 用意したすべてのチャンクを同じ形式へ変換する。
    ]
    payload = "".join(  # 全イベントをつなぎ、SSE形式の本文を作る。
        f"data: {json.dumps(event)}\n\n" for event in events  # 各JSONへSSEの接頭辞と区切りを付ける。
    )  # JSONをSSEのイベント列へ組み立てる。
    payload += "data: [DONE]\n\n"  # SDKが認識するストリーム終了を付ける。

    @do  # 呼び出し時に合成可能なProgramを作る。
    def read_fixture(  # ローカルのSSEをSDKで解析し、本文を収集する処理を定義する。
        content: str,  # 通信せずに解析するSSEの本文を文字列で受け取る。
    ) -> EffectGenerator[str]:  # ローカルデータをSDKのストリームとして読む。
        # SDKのデコーダを使うだけ。HTTPリクエストは1件も送信しない。
        client = AsyncOpenAI(  # SSEデコーダ用のSDKクライアントを作る。通信はしない。
            api_key="offline-fixture"  # 認証には使わない、ローカル検証用の固定文字列を渡す。
        )  # デコーダ用のSDKクライアントを作る。通信はしない。
        response = httpx.Response(  # SDKへ渡す応答をメモリ内だけで作る。
            200,  # 成功したHTTP応答の形式を用意する。
            headers={"content-type": "text/event-stream"},  # 本文がSSE形式であることを示す。
            content=content.encode(),  # ローカルの文字列を応答のバイト列にする。
            request=httpx.Request(  # SDKの例外に付ける要求情報を作る。送信はしない。
                "GET", "https://fixture.invalid/stream"  # ローカル検証用の要求方式と架空のURLを指定する。
            ),  # 例外に付ける要求情報を作る。送信はしない。
        )
        stream = AsyncStream(  # SDKで応答をチャンク列へ解析するストリームを作る。
            cast_to=ChatCompletionChunk, response=response, client=client  # チャンク型、ローカル応答、デコーダ用クライアントを渡す。
        )  # SDKで実際のChatCompletionChunkへ解析させる。
        try:  # 収集が失敗してもクライアントを片付けられるようにする。
            return (  # SDKから収集した本文を呼び出し元へ返す。
                yield collect_openai_stream(stream)  # 収集用Programをyieldし、本文の文字列を受け取る。
            )  # 収集用Programをyieldして、紹介文の文字列を受け取る。
        finally:  # 成功時と失敗時の両方で後処理する。
            yield Await(client.close())  # SDKクライアントの非同期終了を待つ。

    waits: list[str] = []  # 観測した待機の一覧を空で用意する。

    @do  # 呼び出し時に合成可能なProgramを作る。
    def count_waits(effect, k):  # 各Awaitの発生を観測するハンドラを定義する。
        if isinstance(effect, Await):  # 今回の検証対象である待機だけを数える。
            waits.append(type(effect.coroutine).__name__)  # 待機を観測した記録を1件追加する。
        return (yield Pass(effect, k))  # エフェクトと継続を外側へ渡し、通常の処理を続ける。

    program = handler(count_waits)(  # 待機回数を観測するハンドラを取り付ける。
        read_fixture(payload)  # ローカルSSEから本文を収集するProgramを観測対象にする。
    )  # ローカルの収集処理へ待機を数えるハンドラを設置する。
    assert (  # 実際のSDKデコーダを通した本文の結果を検査する。
        run(scheduled(await_handler()(program))) == "まずは 一局"  # 待機を進め、2つの本文断片が正しく結合されると期待する。
    )  # 実際のSDKチャンクから紹介文を収集できることを確かめる。
    # 5チャンク + 反復の終了確認 + client.close。収集全体を1回のAwaitにしない。
    assert len(waits) == len(events) + 2, (  # チャンク数に終了確認と終了処理の2回を足した待機回数を期待する。
        waits  # 回数が違う場合は、観測した待機の一覧を表示する。
    )  # 各チャンク・終了確認・終了処理が別の待機になったことを確かめる。

    failed_payload = 'data: {"error": {"message": "fixture stream failure"}}\n\n'  # SDKがAPIErrorを送出するイベントを用意する。

    @do  # 呼び出し時に合成可能なProgramを作る。
    def inspect_failure():  # 異常系の結果を、検査できるErrとして受け取る。
        return (  # 収集の成功・失敗を検査できる結果として返す。
            yield Try(read_fixture(failed_payload))  # SDKの失敗をTryで受け、成功に置き換わらないか調べる。
        )  # SDKの失敗をTryで捕まえ、収集が成功扱いしないか調べる。

    program = try_handler(inspect_failure())  # 異常を結果の値にするハンドラを設置する。
    result = run(scheduled(await_handler()(program)))  # 失敗するストリームの検証を実行する。
    assert result.is_err(), (  # SDKの異常が収集の失敗として残ることを検査する。
        "ストリームの異常を成功として扱ってはいけません"  # 異常が成功へ変わった場合に、検証の失敗理由を表示する。
    )  # SDKの異常が収集の成功へ置き換わっていないことを確かめる。
    assert isinstance(result.error, APIError)  # SDKの例外型が保たれたことを確かめる。
    assert (  # SDKの例外メッセージが保たれることを検査する。
        str(result.error) == "fixture stream failure"  # 元のSSEエラーに含めたメッセージがそのまま残ると期待する。
    )  # SDKの例外メッセージが保たれたことを確かめる。


if __name__ == "__main__":  # このファイルを直接実行したときだけ検証を始める。
    verify()  # 4段階のデータの受け渡しを検証する。
    verify_sdk_stream()  # SDKの解析・待機の粒度・異常の伝播を検証する。
    print(  # 記事制作の検証が完了したことを表示する。
        "構造化応答 → 本文 → ストリーミング紹介文 → 埋め込み: OK"  # 4段階の受け渡しが成功した検証結果を示す。
    )  # 文書制作の検証が完了したことを表示する。
    print(  # 通信なしのSDK検証が完了したことを表示する。
        "SDKのSSE解析・チャンクごとのAwait・異常の伝播(通信なし): OK"  # SSE解析・待機の粒度・例外の伝播が成功したと示す。
    )  # 通信なしのSDK検証が完了したことを表示する。
