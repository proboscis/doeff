"""coroutineとの比較、解釈の差し替え、1要素ずつの非同期待機を検証する。"""

import asyncio  # 比較用coroutineと、通信しない非同期ストリームを動かす。
from collections.abc import AsyncIterator  # 文字列を1要素ずつ非同期に返す入力型を表す。
from dataclasses import dataclass  # 名前取得の依頼を不変のデータ型にする。

from doeff_core_effects import Await  # 非同期イテレータの次の1要素だけを待つ依頼を使う。
from doeff_core_effects.handlers import await_handler  # Awaitをasyncioとの橋渡しへ接続する。
from doeff_core_effects.scheduler import scheduled  # 橋渡しの完了を待ち、停止した計算を再開する。

from doeff import (  # 依頼を解釈し、Programを合成して検証できるようにする。
    Effect,  # ReadNameをハンドラへ配送する操作として宣言する。
    EffectGenerator,  # @do本体がyieldを通して値を受け取る型を表す。
    Pass,  # 担当しない操作を、その継続とともに外側へ渡す。
    Program,  # まだ実行していない合成可能な計算の型を表す。
    Resume,  # 名前を待つ続きへ、ハンドラが選んだ値を渡す。
    do,  # 関数を呼ぶと実行前のProgramを作るようにする。
    handler,  # 名前の供給方法をProgramの外側へ取り付ける。
    run,  # 構成したProgramを検証の境界で実行する。
)  # 依頼・継続・合成・実行のAPIを揃える。


async def read_name_async() -> str:  # asyncio側の比較教材として、非同期の名前取得を表す。
    await asyncio.sleep(0)  # 通信せず、いったん現在のTaskから実行を譲る。
    return "花子"  # 再開後、呼び出し元のawaitへ名前の文字列を返す。


async def greet_async() -> str:  # 名前取得と挨拶の組み立てをcoroutineとしてつなぐ。
    name = await read_name_async()  # 子coroutineを進め、返ってきた「花子」を受け取る。
    return f"こんにちは、{name}さん"  # 取得した名前を含む挨拶を返す。


@dataclass(frozen=True)  # 一度発行した依頼を書き換えないデータ型にする。
class ReadName(Effect):  # 取得方法を含めず、「名前を返してほしい」という操作を表す。
    pass  # この依頼には引数がないため、フィールドを追加しない。


@do  # 呼び出すと、名前取得と挨拶を合成した実行前のProgramを返す。
def greet() -> EffectGenerator[str]:  # coroutine版と同じ挨拶を、依頼とその結果で組み立てる。
    name = yield ReadName()  # 名前の取得をハンドラに委ね、渡された文字列を受け取る。
    return f"こんにちは、{name}さん"  # 選択したハンドラが返した名前で挨拶を作る。


@do  # 名前を求める依頼を受け、続きへ値を返せる計算にする。
def hanako(effect, k):  # この実行範囲でReadNameへの返答を「花子」にする。
    if isinstance(effect, ReadName):  # 名前の取得だけをこのハンドラの担当にする。
        return (yield Resume(k, "花子"))  # 停止中のyieldへ「花子」を渡し、挨拶の続きを再開する。
    return (yield Pass(effect, k))  # 別の依頼は元の継続を保って外側へ渡す。


@do  # 同じ名前取得の依頼に、別の解釈を取り付けられるようにする。
def taro(effect, k):  # この実行範囲でReadNameへの返答を「太郎」にする。
    if isinstance(effect, ReadName):  # 名前の取得だけをこのハンドラの担当にする。
        return (yield Resume(k, "太郎"))  # 同じ本体を「太郎」で再開し、異なる挨拶を得る。
    return (yield Pass(effect, k))  # 別の依頼は元の継続を保って外側へ渡す。


@do  # 子のProgramをyieldして結果を受け取る合成の例にする。
def twice() -> EffectGenerator[tuple[str, str]]:  # 挨拶を2回行い、順番を保った組で返す。
    first = yield greet()  # 子の計算を実行し、1回目の挨拶を受け取る。
    second = yield greet()  # 新しい子の計算を実行し、2回目の挨拶を受け取る。
    return first, second  # 2つの結果を、呼び出した順で返す。


@do  # 収集ループ全体を、他のProgramからyieldできる計算にする。
def collect_chunks(stream: AsyncIterator[str]) -> EffectGenerator[str]:  # 文字列の断片を受信順に結合する。
    iterator = aiter(stream)  # 入力の非同期イテレータを取得し、最初の断片から読む。
    text = ""  # まだ何も受け取っていない本文を空文字列で表す。
    while True:  # 反復の終了を検出するまで、1要素ずつ依頼を発行する。
        chunk = yield Await(anext(iterator, None))  # 次の1要素だけを待ち、終了時にはNoneを受け取る。
        if chunk is None:  # str型の要素と、反復が終了した合図を区別する。
            return text  # 終了したら、結合済みの全文を呼び出し元へ返す。
        text += chunk  # 今回届いた文字列を、受信順に本文の末尾へ加える。


@do  # 収集用Programを、本文を仕上げる次の処理へつなぐ。
def introduction(stream: AsyncIterator[str]) -> EffectGenerator[str]:  # 断片を集め、前後の空白を除いた本文を返す。
    body = yield collect_chunks(stream)  # 子Programから結合済みの文字列を受け取る。
    return body.strip()  # 全文が揃ってから前後の空白を除き、紹介文の結果にする。


async def chunks_for_test() -> AsyncIterator[str]:  # 非同期SDKの反復プロトコルを再現するテスト専用入力にする。
    for chunk in (" はじめに", "", "、", "doeff "):  # 空の断片も含め、結合順と終了判定を検査する。
        await asyncio.sleep(0)  # ネットワークを使わず、次の要素が非同期に到着する状況を作る。
        yield chunk  # テスト用の非同期イテレータから、今回の断片だけを返す。


p_greet: Program[str] = greet()  # 名前供給の方法をまだ選ばず、同じ処理本体を再利用できる値にする。
p_twice: Program[tuple[str, str]] = twice()  # 挨拶2回という固定の手順を、実行前の値として保持する。


def verify() -> None:  # 比較結果・ハンドラ差し替え・橋渡しの粒度をオフラインで検証する。
    assert asyncio.run(greet_async()) == "こんにちは、花子さん"  # coroutine版が名前をawaitして挨拶を返すと確認する。
    assert run(handler(hanako)(p_greet)) == "こんにちは、花子さん"  # 同じ挨拶をdoeff版でも得ると確認する。
    assert run(handler(taro)(p_greet)) == "こんにちは、太郎さん"  # 本体を変えず、別ハンドラで返答だけを替える。
    assert run(handler(hanako)(p_twice)) == (  # 子Programの結果をyieldで2回受け取れたか確認する。
        "こんにちは、花子さん",  # 1回目の依頼から作られる挨拶を期待する。
        "こんにちは、花子さん",  # 2回目も同じハンドラが処理することを期待する。
    )  # 2回分が欠けず、順番を保つことを確認する。
    requested = []  # Awaitがループ全体でなく1要素ごとに届くことを観測する記録を用意する。

    @do  # 待機依頼を観測し、実際の橋渡しへ通過させるテスト用ハンドラにする。
    def count_awaits(effect, k):  # Awaitの発行回数を、収集関数を書き換えず確認する。
        if isinstance(effect, Await):  # ストリームの1要素を待つ依頼を記録対象にする。
            requested.append("Await")  # 1つの待機依頼が届いたことを記録する。
        return (yield Pass(effect, k))  # 待機の実処理は外側のawait_handlerに委ねる。

    collecting = handler(count_awaits)(introduction(chunks_for_test()))  # 4断片と終了の待機を観測する構成を作る。
    result = run(scheduled(await_handler()(collecting)))  # Awaitの橋渡しとスケジューラを付けて検証する。
    assert result == "はじめに、doeff"  # 空の断片で終了せず、受信順の結合と空白除去ができたか確認する。
    assert requested == ["Await"] * 5  # 4断片の取得と反復終了の確認で、計5回の境界を通ると確認する。
    print("挨拶: 花子 / 太郎、合成: 2回、本文: はじめに、doeff、Await: 5回")  # 検証を通過した結果をまとめて表示する。


if __name__ == "__main__":  # import時には実行せず、このファイルを直接起動したときだけ検証する。
    verify()  # 通信・LLM・外部サービスを使わず、すべての期待値を確かめる。
