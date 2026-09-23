"""文書の依頼・HTTP取得・Memo保存・時計を分け、外部通信なしで組み合わせを確かめる。"""

from dataclasses import dataclass  # ページIDと版を持つ、変更しない依頼データを作る。
from datetime import datetime, timezone  # 仮想時計の開始点をUTCで固定する。
from pathlib import Path  # 検証専用のSQLite保存先を組み立てる。
from tempfile import TemporaryDirectory  # この検証が作った保存先だけを終了時に片付ける。

import pytest  # 未保存の版が黙って成功しないことを例外まで検証する。
from doeff_core_effects import HttpRequest, HttpResponse  # HTTPの公開の依頼型と応答型を使う。
from doeff_core_effects.handlers import (  # 保存の待機とログを処理。
    await_handler,  # SQLiteや実時間のハンドラが出すAwaitを処理する。
    slog_discard_handler,  # Memoや仮想時計の診断ログを受け取る。
)
from doeff_core_effects.memo_handlers import (  # 再利用の方針と保存先を別々に選ぶ。
    in_memory_memo_handler,  # 1回の検証ではメモリ内へ結果を保存する。
    make_memo_rewriter,  # ReadPageを保存済み判定・取得・保存へ展開する。
    sqlite_memo_handler,  # 別runでも再利用できるよう、Memoの結果をSQLiteへ保存する。
)
from doeff_core_effects.scheduler import (  # 並行取得と、待機後の処理再開を扱う。
    PRIORITY_HIGH,  # 優先して動かすタスクへ指定する、公開の優先度定数。
    Gather,  # 開始済みTaskを待ち、入力順の結果を受け取る。
    Spawn,  # 処理を開始し、待機に使うTaskを受け取る。
    Wait,  # Taskの完了まで保留し、その結果で再開する。
    scheduled,  # 並行処理・待機を進めるスケジューラを取り付ける。
)
from doeff_time import (  # 時計はHTTPやMemoと独立に差し替える。
    Delay,  # 固定HTTPハンドラの取得時間を、差し替え可能な依頼として表す。
    GetTime,  # workflowの開始・終了時刻を、選んだ時計から受け取る。
    async_time_handler,  # Delayをasyncioの実時間待機へ変換する。
    sim_time_handler,  # 実時間を待たず、予定に合わせて仮想時計を進める。
)

from doeff import (  # ドメイン依頼とハンドラ、検証用runを使う。
    Effect,  # 独自のReadPageが継承する、依頼の基底型。
    Pass,  # 担当外の依頼と続きを、別のハンドラへ渡す。
    Resume,  # 結果を渡し、依頼元のyieldを再開する。
    do,  # 処理手順をProgramとして合成できるようにする。
    handler,  # 依頼の解釈をProgramへ取り付けられる形にする。
    run,  # 検証の最外側でだけ、構成済みProgramを実行する。
)


@dataclass(frozen=True)  # 操作の識別に使う2つの値を、途中で変更できないようにする。
class ReadPage(Effect):  # 文書を読むという、通信手段から独立した操作。
    page_id: str  # どのページかを指定し、例ではintro・rules・endingを使う。
    revision: str  # 同じIDでも版が違えば、別の本文として扱う。


@do  # 読み取りと見出し抽出を、呼び出し元がyieldできるProgramへまとめる。
def read_title(page_id: str, revision: str):  # ページIDと版から先頭行の見出しを返す。
    page = yield ReadPage(page_id, revision)  # ハンドラから、その版の本文文字列を受け取る。
    return page.splitlines()[0]  # 本文の先頭行を取り出し、例えば「遊び方」を返す。


@do  # 索引作りを、read_titleのProgramを使って合成する。
def make_index(page_ids: tuple[str, ...]):  # 入力されたページ順に見出しを並べる。
    tasks = ()  # 開始済みのTaskを、入力されたページ順に保持する。
    for page_id in page_ids:  # 各ページの処理を開始し、まだ結果を待たず次へ進む。
        task = yield Spawn(read_title(page_id, "edition-1"))  # 子Programを開始してTaskを受け取る。
        tasks = (*tasks, task)  # 新しいTaskを末尾に加え、索引の順序を保つ。
    titles = yield Gather(*tasks)  # 全Taskを待ち、入力順の見出しリストを受け取る。
    return tuple(titles)  # 例えば「はじめに・遊び方」の変更しない組を返す。


@do  # 2つの索引作成と計時を、同じyieldの規約で組み合わせる。
def workflow():  # ページが重なる2つの索引と、選んだ時計での経過秒数を返す。
    start = yield GetTime()  # 最初の索引を作る直前の時刻を受け取る。
    first = yield make_index(("intro", "rules"))  # 2ページを並行に読み、最初の索引を得る。
    second = yield make_index(("rules", "ending"))  # rulesは同じ版なので保存値を再利用できる。
    end = yield GetTime()  # 2番目の索引が完成した時刻を受け取る。
    return first, second, (end - start).total_seconds()  # 2つの索引と経過秒数を返す。


@handler  # ドメインからHTTPへの翻訳を、Programに取り付けられる形にする。
@do  # HTTP応答を待つ部分も、外側のハンドラへyieldする。
def pages_over_http(effect, k):  # ReadPageだけを本文取得の手順へ翻訳する。
    if not isinstance(effect, ReadPage):  # Memoや時間などの別の依頼は担当しない。
        return (yield Pass(effect, k))  # 別の担当へ元の依頼と続きを渡す。
    url = f"https://example.invalid/{effect.revision}/{effect.page_id}"  # 版とIDから取得先を決める。
    response = yield HttpRequest("GET", url)  # HTTP担当から公開型HttpResponseを受け取る。
    response.raise_for_status()  # HTTPエラーなら本文を成功値にせず、その例外を伝える。
    return (yield Resume(k, response.text))  # 本文文字列でReadPageを待つ処理を再開する。


def fixed_http(pages: dict[str, str], calls: list[str], seconds: float):  # テスト用HTTP担当を作る。
    @handler  # 固定応答を、本番HTTPと同じ位置へ取り付けられるようにする。
    @do  # 取得待機と結果返却を、DelayとResumeで表す。
    def interpret(effect, k):  # クライアントを作らず、HttpRequestを直接受け取る。
        if not isinstance(effect, HttpRequest):  # HTTP以外は外側の担当に委ねる。
            return (yield Pass(effect, k))  # Memoや時計の処理に干渉しない。
        if effect.method != "GET":  # この固定データではGET以外を提供していない。
            raise ValueError(effect.method)  # 未対応の操作を成功させず、検証を失敗させる。
        text = pages[effect.url]  # 未定義のURLならKeyErrorとし、実際のネットワークへ流さない。
        calls.append(effect.url)  # HTTP担当まで届いた依頼だけを記録する。
        yield Delay(seconds)  # 選んだ時計の意味で取得時間を待つ。
        response = HttpResponse(  # 本番HTTP担当と同じ公開の応答型を作る。
            status=200,  # 成功扱いなのでraise_for_statusを通過する。
            headers={},  # この例では応答ヘッダの情報を使わない。
            content=text.encode(),  # 生の応答本文も、文字列と同じUTF-8データにする。
            text=text,  # ReadPageが最終的に返す本文を指定する。
            url=effect.url,  # 対応する依頼のURLを応答に残す。
            elapsed_seconds=seconds,  # 固定した取得時間を応答情報にも残す。
        )
        return (yield Resume(k, response))  # HTTP応答を戻し、ReadPageの翻訳処理を再開する。

    return interpret  # 呼び出し元はこのハンドラをProgramへ取り付けられる。


@handler  # 保存済みのデータだけで動くことを確認する、別のHTTP担当。
@do  # 未保存のHTTP依頼を検出したら、Programの失敗として伝える。
def reject_http(effect, k):  # HTTPが呼ばれないはずの再実行に取り付ける。
    if not isinstance(effect, HttpRequest):  # HTTP以外の処理は引き続き使える。
        return (yield Pass(effect, k))  # Memoの読み取りや時計を妨げない。
    raise LookupError(effect.url)  # 未保存の依頼を、本物のHTTPへ接続せず拒否する。


def assemble(program, http, cache, clock):  # 外部との境界で、各責務の担当を選んで組む。
    wrapped = make_memo_rewriter(  # 文書単位の再利用を、取得方法から独立して追加する。
        ReadPage,  # HTTPの細部ではなく、版付きページ本文を保存の単位にする。
        key_fn=lambda e: f"page:{e.revision}:{e.page_id}",  # 版が変われば別の保存キーになる。
    )(program)  # 元の文書処理を包み、ReadPageが保存の判定を通るようにする。
    wrapped = pages_over_http(wrapped)  # 保存ミスのReadPageをHttpRequestへ翻訳する。
    wrapped = http(wrapped)  # 本番取得・固定応答・拒否のいずれかを担当させる。
    wrapped = cache(wrapped)  # Memo系の依頼を、メモリやSQLiteへ割り当てる。
    wrapped = clock(wrapped)  # DelayとGetTimeを、選んだ時計で解釈する。
    wrapped = slog_discard_handler(wrapped)  # Memoと仮想時計の診断ログを受け取る。
    return scheduled(await_handler()(wrapped))  # 保存や実時計のAwaitと、並行処理を進める。


@do  # 優先度を付けた開始と、完了待ちを同じ処理として表す。
def urgent_title():  # 優先度付きの読み取りでも、ドメイン操作の戻り値は見出し文字列。
    task = yield Spawn(read_title("rules", "edition-1"), priority=PRIORITY_HIGH)  # 高優先度で開始。
    title = yield Wait(task)  # 取得が完了すると、その見出しで待機を再開する。
    return title  # 呼び出し元へ「遊び方」を返す。


def verify() -> None:  # テスト境界でだけrunし、値・取得回数・保存・時計の組み合わせを確かめる。
    base = "https://example.invalid/edition-1"  # URLは例示用で、外部へ通信しない。
    pages = {  # HTTP担当が返す固定本文を、URLごとに定義する。
        f"{base}/intro": "はじめに\n本文",  # 最初の索引だけで必要なページ。
        f"{base}/rules": "遊び方\n本文",  # 両方の索引が読む、再利用の対象ページ。
        f"{base}/ending": "おわりに\n本文",  # 2番目の索引で初めて必要になるページ。
    }
    expected = (("はじめに", "遊び方"), ("遊び方", "おわりに"))  # 取得方法に依存しない索引の値。
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)  # 仮想時間を同じ開始点にそろえる。
    calls: list[str] = []  # 初回取得の回数を、HTTP担当側で観測する。
    first = assemble(  # 固定HTTP・メモリ保存・仮想時計を、それぞれ選んで組み合わせる。
        workflow(), fixed_http(pages, calls, 2),  # 各取得に仮想時間で2秒かかる設定。
        in_memory_memo_handler(), sim_time_handler(start_time=start),  # 保存と時計は独立に設置。
    )
    result = run(first)  # テストとして全文書処理を完了させる。
    assert result[:2] == expected  # 各索引が入力順の見出しを返すことを確認する。
    assert result[2] == 4.0  # 最初の2ページが並行で2秒、次の新規1ページで2秒だけ進む。
    assert sorted(calls) == sorted(pages)  # 同じrulesを2度取得せず、合計3ページだけ取得する。

    with TemporaryDirectory() as directory:  # この検証が所有するSQLiteだけを作る。
        database = Path(directory) / "pages.sqlite"  # 別のrunでも同じ保存先を使う。
        recorded_calls: list[str] = []  # 保存対象になった実取得の回数を記録する。
        record = assemble(  # 同じProgramを、SQLite保存に取り付け直す。
            workflow(), fixed_http(pages, recorded_calls, 2),  # 取得方法と文書処理は変更しない。
            sqlite_memo_handler(database), sim_time_handler(start_time=start),  # 保存先だけ変更。
        )
        assert run(record)[:2] == expected  # SQLite保存でも、索引の値は変わらない。
        assert sorted(recorded_calls) == sorted(pages)  # 保存されるページは3件だけ。
        replay = assemble(  # 新しいProgramとハンドラで、保存済みの本文を読み直す。
            workflow(), reject_http,  # HTTPが呼ばれたら失敗する担当へ交換する。
            sqlite_memo_handler(database), sim_time_handler(start_time=start),  # 同じSQLiteを開く。
        )
        assert run(replay) == (*expected, 0.0)  # 全件保存ヒットなのでHTTP待機なしで索引ができる。
        missing = assemble(  # ページIDが同じでも版を変えれば、保存ミスにする。
            read_title("rules", "edition-2"), reject_http,  # 未保存の版を取得しようとして拒否される。
            sqlite_memo_handler(database), sim_time_handler(start_time=start),  # 保存先は同じ。
        )
        with pytest.raises(LookupError) as failure:  # 保存にない版が成功しないことを確かめる。
            run(missing)  # HTTP担当まで届くため、LookupErrorになるはず。
        assert failure.value.args == ("https://example.invalid/edition-2/rules",)  # 版の差が残る。

    for clock in (sim_time_handler(start_time=start), async_time_handler()):  # 時計だけを交換する。
        clock_calls: list[str] = []  # 各時計でのHTTP到達回数を独立に数える。
        timed = assemble(  # HTTPと文書処理の契約を維持して、実時間でも動かす。
            workflow(), fixed_http(pages, clock_calls, 0.01),  # 各取得の待機は10ミリ秒だけにする。
            in_memory_memo_handler(), clock,  # 保存先は同じ種類の新しいメモリ領域。
        )
        actual = run(timed)  # 仮想時間では瞬時に進み、実時間では実際に短く待機する。
        assert actual[:2] == expected  # 時計の変更後も、索引の意味と値は同じ。
        assert actual[2] >= 0.02  # 2段階の取得には、各時計で合計20ミリ秒以上かかる。
        assert sorted(clock_calls) == sorted(pages)  # 時計に関係なく、取得は3ページ分だけ。

    priority_calls: list[str] = []  # 優先度付きの読み取りが担当へ届くことを確かめる。
    urgent = assemble(  # 優先度指定とドメイン・Memo・時計のハンドラを組み合わせる。
        urgent_title(), fixed_http(pages, priority_calls, 2),  # 高優先度でrulesを読むProgram。
        in_memory_memo_handler(), sim_time_handler(start_time=start),  # 2秒を仮想時間で待つ。
    )
    assert run(urgent) == "遊び方"  # SpawnしたProgramの見出しをWaitで受け取れる。
    assert priority_calls == [f"{base}/rules"]  # 指定したページが一度だけ取得される。


if __name__ == "__main__":  # 直接実行時だけ、外部通信なしの検証を開始する。
    verify()  # 異なる取得・保存・時計を組み合わせた全assertを実行する。
    print("ReadPage・HTTP・Memo・時計の合成、3件取得/仮想4秒・保存済み再利用: OK")  # 成功時だけ表示。
