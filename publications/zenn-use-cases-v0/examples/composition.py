"""設定・状態・ログ・失敗・時間を合成し、結果と状態とログを検証する。"""

from datetime import datetime, timedelta, timezone  # 仮想時計の開始時刻と2秒後を比較する。

from doeff_core_effects import Ask, Get, Put, Tell, Try  # 設定・状態・ログ・失敗を依頼する。
from doeff_core_effects.handlers import (  # 各依頼の解釈と、ログの取り出しを用意する。
    reader,  # Askへ設定値を返すハンドラを作る。
    state,  # Get/Putを同じ状態辞書へ接続する。
    try_handler,  # Tryの対象をOkまたはErrに包む。
    writer,  # Tellを外側のstateへ蓄積する。
    writer_log,  # 蓄積済みのメッセージ一覧を返すProgramを作る。
)
from doeff_core_effects.scheduler import scheduled  # 仮想時間ハンドラが使う待機と再開を扱う。
from doeff_time import Delay, GetTime  # 経過時間の待機と現在時刻を依頼する。
from doeff_time.handlers.sim_time import sim_time_handler  # 実時間を待たず仮想時計を進める。

from doeff import Err, Ok, do, run  # Programの合成と、テスト境界での実行に使う。

START = datetime(2026, 1, 1, tzinfo=timezone.utc)  # 毎回同じUTC時刻から検証を始める。


@do  # 待機も時刻取得も、呼び出し元へ合成できるProgramにする。
def wait_before_conversion():  # 待機後の時刻を後続の処理へ返す。
    seconds = yield Ask("delay_seconds")  # readerから2秒という設定値を受け取る。
    yield Delay(seconds)  # 時間ハンドラに2秒後の再開を依頼する。
    return (yield GetTime())  # 仮想時計の開始から2秒後を返す。


@do  # 変換処理の状態・ログ・待機を、同じProgramにまとめる。
def convert_title(text: str):  # 正常な見出しと空文字の両方を入力データとして扱う。
    count = yield Get("attempts")  # 初期状態から試行回数0を読む。
    yield Put("attempts", count + 1)  # この試行を数え、状態を1へ更新する。
    yield Tell("見出しの変換を開始")  # 失敗した場合にも残す開始ログを発行する。
    finished_at = yield wait_before_conversion()  # 子Programを実行し、待機後の時刻を受け取る。
    title = text.strip()  # 前後の空白を除き、変換対象の文字列を得る。
    if not title:  # 空の見出しなら、変換結果を作れないと判断する。
        raise ValueError("見出しが空です")  # 呼び出し元のTryへ変換失敗を伝える。
    return title.upper(), finished_at  # 成功なら大文字の見出しと完了時刻を返す。


@do  # 結果だけでなく、同じ試行の状態とログも値として取り出す。
def inspect(text: str):  # 入力ごとに成功・失敗と、その後に残る情報を調べる。
    outcome = yield Try(convert_title(text))  # 成功ならOk、空文字ならErrを受け取る。
    count = yield Get("attempts")  # Tryの後も試行回数1が残っていることを観測する。
    messages = yield writer_log()  # Tellで蓄積したメッセージ一覧を受け取る。
    return outcome, count, messages  # 結果・回数・ログを検証側へ返す。


def execute_test(text: str):  # 各テストのために独立したハンドラ構成を作る。
    program = try_handler(inspect(text))  # Tryを解釈して例外をErrへ変換する。
    program = writer(program)  # Tellを受け取り、保存用のGet/Putを外側へ依頼する。
    program = state(initial={"attempts": 0})(program)  # 本文とwriterが同じ状態を参照する。
    program = reader(env={"delay_seconds": 2})(program)  # 子Programへ2秒の設定を供給する。
    program = sim_time_handler(start_time=START)(program)  # 実行ごとに仮想時計を初期化する。
    return run(scheduled(program))  # テスト境界で実行し、inspectの3要素を受け取る。


def verify() -> None:  # 外部接続なしで、成功・失敗・再実行の意味を確かめる。
    success, count, messages = execute_test(" doeff ")  # 正常な入力を最初から処理する。
    assert isinstance(success, Ok)  # Tryが成功をOkとして返したことを確認する。
    assert success.value == ("DOEFF", START + timedelta(seconds=2))  # 変換結果と仮想時刻が合う。
    assert count == 1  # 成功した変換が1回として数えられる。
    assert messages == ["見出しの変換を開始"]  # Writerのログを返り値として確認できる。
    failure, count, messages = execute_test("   ")  # 空の見出しを、新しい状態で処理する。
    assert isinstance(failure, Err)  # 例外が検証側へ漏れず、失敗の値として届く。
    assert isinstance(failure.error, ValueError)  # 元の例外型が保たれる。
    assert str(failure.error) == "見出しが空です"  # 変換失敗の理由が保たれる。
    assert count == 1  # Tryは失敗前に行った状態更新を巻き戻さない。
    assert messages == ["見出しの変換を開始"]  # 失敗前のTellも巻き戻されない。
    _, repeated_count, repeated_messages = execute_test("again")  # 3回目もハンドラを作り直す。
    assert repeated_count == 1  # 別の実行に試行回数が漏れない。
    assert repeated_messages == ["見出しの変換を開始"]  # 別の実行のログも混ざらない。


if __name__ == "__main__":  # このファイルを直接実行した場合に検証する。
    verify()  # 全検証が通れば出力せず終了する。
