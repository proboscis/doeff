"""async/awaitとyieldの比較、ハンドラの差し替え、1断片ずつの待ちを、通信せずに確かめる。

記事 doeff-coroutines.md の Python の4枠と同じ行を持つ完全な例。記事の枠の行は、
checks/coroutines-check.py がこのファイルの行と一致することを確かめる。
"""

import asyncio  # 比べるためのcoroutineを、標準のイベントループで動かす。
from collections.abc import AsyncIterator  # 文字列を非同期に1つずつ返す入力の型。
from dataclasses import dataclass  # 依頼を、書き換えられないデータ型にする。

from doeff_core_effects import Await, Listen  # 完了を待つ依頼と、出た依頼を集める依頼。
from doeff_core_effects.handlers import (  # 待ちに答える役と、集める役。
    await_handler,  # Awaitを、裏のasyncioのループで進める。
    listen_handler,  # Listenに答え、包んだ範囲の依頼を集める。
)
from doeff_core_effects.scheduler import scheduled  # 待ちが終わったら、止まった処理を再開する。

from doeff import Effect, Pass, Resume, do, handler, run, with_handlers  # 依頼・ハンドラ・実行。

# --- 記事の1枠目: 同じ挨拶を、await版とyield版で書く ---


async def read_name_async():  # await版の部品。名前を取る実装そのもの。
    await asyncio.sleep(0)  # 通信の代わりに、いったんイベントループへ譲る。
    return "花子"  # 実装が決めた名前を返す。


async def greet_async():  # await版の挨拶。
    name = await read_name_async()  # 呼んだ実装を進め、その戻り値を受け取る。
    return f"こんにちは、{name}さん"  # 名前を入れた挨拶を返す。


@dataclass(frozen=True)  # 出した依頼を、後から書き換えない型にする。
class ReadName(Effect):  # yield版の部品。「名前を返して」という依頼で、取り方は書かない。
    pass  # 引数の無い依頼なので、欄は無い。


@do  # 呼んでもすぐには動かず、動かす手順を返す。
def greet():  # yield版の挨拶。
    name = yield ReadName()  # 依頼を出して止まり、外側から返った名前を受け取る。
    return f"こんにちは、{name}さん"  # 名前を入れた挨拶を返す。


@handler  # 手順に取り付けられるハンドラにする。
def hanako(effect, k):  # effectは届いた依頼、kは止まった処理の続き。
    if isinstance(effect, ReadName):  # 名前の依頼なら、
        return (yield Resume(k, "花子"))  # 続きに「花子」を返して再開する。
    return (yield Pass(effect, k))  # 担当しない依頼は、外側のハンドラへ渡す。


# --- 記事の2枠目: 同じProgramに、別の答えを付ける ---


@handler  # 同じ依頼へ、別の答え方をするハンドラにする。
def taro(effect, k):  # この範囲では、名前の依頼に「太郎」で答える。
    if isinstance(effect, ReadName):  # 名前の依頼だけを担当する。
        return (yield Resume(k, "太郎"))  # 続きに「太郎」を返して再開する。
    return (yield Pass(effect, k))  # 担当しない依頼は、外側へ渡す。


@do  # 子のProgramをyieldで呼ぶ処理にする。
def twice():  # 挨拶を2回作り、順に組で返す。
    first = yield greet()  # awaitの代わりにyieldで、子の結果を受け取る。
    second = yield greet()  # 2回目も、子の奥でReadNameの依頼が出る。
    return first, second  # 2つの挨拶を、呼んだ順に返す。


# --- 記事の3枠目: 非同期SDKとは、必要な1操作の所でつなぐ ---


@do  # 収集のループも、yieldで依頼を出す処理にする。
def collect_chunks(stream: AsyncIterator[str]):  # 断片を、届いた順につなぐ。
    iterator = aiter(stream)  # 入力から非同期イテレータを取り出す。
    text = ""  # まだ何も届いていない本文。
    while True:  # 終わりが来るまで、1断片ずつ待つ。
        chunk = yield Await(anext(iterator, None))  # 次の1断片を待つ。終わりならNone。
        if chunk is None:  # 断片の文字列と、終わりの合図を見分ける。
            return text  # つないだ本文を返す。
        text += chunk  # 空の文字列も正しい断片として、順につなぐ。


@do  # 収集と仕上げを、同じyieldでつなぐ。
def introduction(stream: AsyncIterator[str]):  # 断片を集め、前後の空白を除く。
    body = yield collect_chunks(stream)  # 子のProgramから、つないだ本文を受け取る。
    return body.strip()  # 全部そろってから、前後の空白を除く。


# --- 記事の4枠目: 何回待ったかを、外側から数える ---


async def chunks_for_test():  # SDKの代わりに、通信しない非同期イテレータを作る。
    for chunk in (" はじめに", "", "、", "doeff "):  # 空の断片も1つ混ぜる。
        await asyncio.sleep(0)  # 断片が届くまでの待ちを、イベントループで作る。
        yield chunk  # 断片を1つ返す。


@do  # 収集を包み、中で出たAwaitを集める処理にする。
def observed():  # 本文と、届いた待ちの依頼の列を返す。
    return (yield Listen(introduction(chunks_for_test()), types=(Await,)))  # 収集は変えずに包む。


# --- 本文の言葉を確かめる比べ(記事には枠として載せない) ---


async def collect_all(stream: AsyncIterator[str]) -> str:  # 収集を丸ごと1つのasync defにした形。
    text = ""  # まだ何も届いていない本文。
    async for chunk in stream:  # 断片の待ちは、このcoroutineの中だけで起きる。
        text += chunk  # 届いた順につなぐ。
    return text  # つないだ本文を返す。


@do  # 丸ごとのasync defを、1回のAwaitで包む処理にする。
def introduction_whole(stream: AsyncIterator[str]):  # 比べるための、包む範囲が広い形。
    body = yield Await(collect_all(stream))  # 収集の全体を1回だけ待つ。
    return body.strip()  # 前後の空白を除く。


@do  # 丸ごと包んだ形も、同じListenで包む。
def observed_whole():  # 本文と、届いた待ちの依頼の列を返す。
    return (yield Listen(introduction_whole(chunks_for_test()), types=(Await,)))  # 同じ数え方。


def verify() -> None:  # 記事の期待値を、通信せずに順に確かめる。
    assert asyncio.run(greet_async()) == "こんにちは、花子さん"  # await版は、呼んだ実装が名前を決める。
    assert run(with_handlers([hanako], greet())) == "こんにちは、花子さん"  # yield版は、ハンドラが決める。

    p_greet = greet()  # 動かす手順を1つ作る。まだ何も動かない。
    assert run(with_handlers([hanako], p_greet)) == "こんにちは、花子さん"  # 同じ手順を花子で動かす。
    assert run(with_handlers([taro], p_greet)) == "こんにちは、太郎さん"  # 同じ手順を太郎で動かす。
    assert run(with_handlers([taro], twice())) == ("こんにちは、太郎さん",) * 2  # 子の奥の依頼にも届く。

    coro = greet_async()  # coroutineオブジェクトを1つ作る。
    assert asyncio.run(coro) == "こんにちは、花子さん"  # 1回目は待てる。
    try:  # 同じcoroutineオブジェクトを、もう1度待ってみる。
        asyncio.run(coro)  # 2回目は待てないはず。
    except RuntimeError as error:  # 本文の「2回目はRuntimeError」を確かめる。
        reuse = str(error)  # 「cannot reuse already awaited coroutine」が入る。
    else:  # 2回目も待てたなら、本文の説明が誤っている。
        raise AssertionError("coroutineを2回待てた")  # 本文と食い違うので止める。

    stack = [await_handler(), listen_handler]  # 先頭が外側。Listenに答える役を内側に置く。
    text, waits = run(scheduled(with_handlers(stack, observed())))  # 本物の待ちで動かす。
    assert text == "はじめに、doeff"  # 空の断片で止まらず、順につないで空白を除いた。
    assert len(waits) == 5  # 4つの断片と終わりの確かめで、5回の待ちが届いた。

    whole_text, whole_waits = run(scheduled(with_handlers(stack, observed_whole())))  # 同じ組で動かす。
    assert whole_text == "はじめに、doeff"  # 丸ごと包んでも、本文は同じになる。
    assert len(whole_waits) == 1  # 本文の「届く待ちは1回だけ」を確かめる。

    print(  # 確かめた値を1行で表示する。
        f"挨拶: 花子 / 太郎・子の奥にも届く / coroutineの2回目: {reuse} / "  # 1〜2枠目の結果。
        f"本文: {text} / Await: 1断片ずつ{len(waits)}回・丸ごと{len(whole_waits)}回"  # 4枠目と比べ。
    )


if __name__ == "__main__":  # importした時には動かさず、直に起動した時だけ確かめる。
    verify()  # 通信・LLM・外部サービスを使わずに、すべての期待値を確かめる。
