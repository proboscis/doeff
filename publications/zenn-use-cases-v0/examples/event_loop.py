"""イベントの反復受信を@doで組み、完了と期限切れを仮想時間で確認する。"""

from dataclasses import dataclass  # イベントの値を、不変のデータとして表す。

from doeff_core_effects.scheduler import (  # 待機中の処理を同じスケジューラで調整する。
    Cancel,  # この例が起動したタスクを、不要になった時点で止める。
    Race,  # 消費側と期限のうち、先に完了した値を受け取る。
    Spawn,  # @doのProgramを子タスクとして開始し、Taskを受け取る。
    scheduled,  # Taskの開始・待機・取り消しを解釈する実行境界。
)
from doeff_events import (  # 発行と型を指定した受信を、エフェクトで表す。
    Publish,  # 現在待っている購読者へイベントを渡す。
    WaitForEvent,  # 指定した型の次のイベントまで続きを止める。
    event_handler,  # メモリ上の購読状態を持つハンドラを作る。
)
from doeff_time import Delay, sim_time_handler  # 待ち時間を、実時間を使わず解釈する。

from doeff import do, run  # @doで計算を組み立て、検証の境界でだけ実行する。


@dataclass(frozen=True)  # 受信後に値を書き換えないイベントにする。
class PageReady:  # 読み取り対象の文書が用意できたことを表す。
    title: str  # 消費側が結果へ蓄積するタイトル。


@dataclass(frozen=True)  # 終了の通知も、独立したイベント型で表す。
class PagesFinished:  # この通知を受け取ったら反復受信を終える。
    pass  # 終了を表す型だけで十分なので、追加の値は持たない。


@do  # 反復受信の全体を、他の@doからyieldできるProgramにする。
def read_until_end():  # 終了通知まで集めたタイトルのタプルを返す。
    titles = ()  # 初期値は空。最終的には2件のタイトルになる。
    while True:  # 1件受け取るたびに、次の待ち受けを登録する。
        event = yield WaitForEvent(PageReady, PagesFinished)  # どちらかの型が届くまで停止。
        if isinstance(event, PagesFinished):  # 終了通知なら、もう次の受信は始めない。
            return titles  # 正常完了時は("はじめに", "遊び方")を返す。
        titles = (*titles, event.title)  # 届いたタイトルを順番どおりに加える。


@do  # イベントの発行と間隔も、同じProgramの中に記述する。
def produce_pages():  # 仮想時刻1・2秒でタイトル、3秒で終了を通知する。
    for title in ("はじめに", "遊び方"):  # この順番で2件を発行する。
        yield Delay(1)  # 消費側が待ち受けを登録してから、1秒後に進む。
        yield Publish(PageReady(title))  # 現在の購読者へ、このタイトルを配る。
    yield Delay(1)  # 最後のタイトルの受信後、終了通知を待てる間隔を置く。
    yield Publish(PagesFinished())  # 消費側にタイトル収集の終了を知らせる。


@do  # 期限を、結果を返す小さなProgramとして合成する。
def deadline(seconds: float):  # 指定秒数後に、期限切れを表す値を返す。
    yield Delay(seconds)  # 時間の進め方は、設置した時間ハンドラに委ねる。
    return "期限切れ"  # Raceで期限側が先に終わった場合の結果になる。


@do  # 受信・発行・期限を、async defへ移さずに合成する。
def collect_with_deadline(seconds: float):  # 正常なタイトル列か期限切れの値を返す。
    consumer = yield Spawn(read_until_end())  # 先に消費側の待ち受けを開始する。
    producer = yield Spawn(produce_pages())  # 最初の発行は1秒後。consumerのTaskとは別に進む。
    timer = yield Spawn(deadline(seconds))  # 消費側と競わせる期限のTaskを作る。
    try:  # Raceの成功時も失敗時も、この関数が持つタスクを後始末する。
        return (yield Race(consumer, timer))  # 先に完了した側の値を、そのまま返す。
    finally:  # Race自体は、遅い側のタスクを自動で取り消さない。
        yield Cancel(producer)  # 残った発行を止める。完了済みなら何もしない。
        yield Cancel(consumer)  # 期限切れなら、残ったイベント待機を止める。
        yield Cancel(timer)  # 正常完了なら、不要になった期限待機を止める。


def verify() -> None:  # 実時間や外部サービスを使わず、両方の結果を確認する。
    cases = [(5, ("はじめに", "遊び方")), (0.5, "期限切れ")]  # 3秒で完了する場合と期限が先の場合。
    for seconds, expected in cases:  # 毎回ハンドラを新しく作り、購読状態を共有しない。
        program = event_handler()(collect_with_deadline(seconds))  # 発行・購読を同じハンドラで解釈。
        result = run(scheduled(sim_time_handler()(program)))  # スケジューラ上で仮想時間を進める。
        assert result == expected  # 2件のタイトル、または0.5秒で期限切れになることを確かめる。


if __name__ == "__main__":  # import時には動かさず、検証として実行した場合だけ起動する。
    verify()  # 正常完了と期限切れの両経路を実行する。
    print("イベント待ちループ・終了イベント・Race・残ったタスクの終了: OK")  # 検証成功を表示する。
