"""Traverseの戦略差、失敗履歴、集計を外部接続なしで確認する。"""

from datetime import datetime, timezone  # 仮想時計の開始をUTCの日時で固定する。

from doeff_core_effects.scheduler import scheduled  # 並行タスクのSpawn/Gatherを解釈する。
from doeff_time import Delay, GetTime, sim_time_handler  # 待機と時刻を仮想時間で扱う。
from doeff_traverse import Fail, Inspect, Reduce, SortBy, Take, Traverse, Zip  # 各操作の依頼型。
from doeff_traverse.handlers import fail_handler, parallel, parallel_fail_fast, sequential  # 戦略。

from doeff import do, run  # @doで計算を組み立て、検証時だけrunで結果を得る。


@do  # 呼び出すたびに、要素1件分の新しいProgramを作る。
def process(value: int):  # 1・2・3秒待つ仕事を同じ関数で表す。
    yield Delay(value)  # 待ち方を時刻ハンドラへ委ね、指定秒数の後に再開する。
    return value * 10  # 入力1・2・3に対して10・20・30を返す。


@do  # 待機する仕事の集合も、通常の@do関数として合成する。
def pipeline():  # 全件成功する固定入力の処理結果と所要時間を返す。
    start = yield GetTime()  # 処理開始時点の仮想時計を読む。
    collection = yield Traverse(process, [1, 2, 3])  # 各入力のProgramを戦略に従って進める。
    items = yield Inspect(collection)  # 入力順のItemResultを取り出し、値と成否を確認できる。
    end = yield GetTime()  # 全件の完了後の仮想時計を読む。
    return [item.value for item in items], (end - start).total_seconds()  # 値一覧と経過秒数。


@do  # 文書ごとに失敗を報告できるProgramを組み立てる。
def read_length(text: str):  # 文書の内容を受け取り、正常なら文字数を返す。
    if not text:  # 空文字だけを、この例の不正な入力とする。
        return (yield Fail(ValueError("本文がありません"), stage="文字数"))  # 対応方針を外へ渡す。
    return len(text)  # 「短文」は2、「もう少し長い文」は7になる。


@do  # Traverseへ渡す処理なので、純粋な文字列操作もProgramにする。
def label(text: str):  # 元の並びを保ったまま表示名を付ける。
    return f"文書:{text}"  # 「短文」なら「文書:短文」を返す。


@do  # Reduceは累積値と要素からProgramを作る関数を受け取る。
def add(total: int, length: int):  # 現在の合計と、次の成功した文字数を受け取る。
    return total + length  # 0→2→9の順で合計を更新する。


@do  # 各段階をyieldし、戦略を選ばずに文書処理を記述する。
def summarize_documents():  # 空文書を含む3件から、合計・上位・履歴を作る。
    texts = ["短文", "", "もう少し長い文"]  # 正常2件と不正1件を用意する。
    lengths = yield Traverse(read_length, texts, label="文字数")  # 2・失敗・7を履歴付きで得る。
    labels = yield Traverse(label, texts, label="表示名")  # 同じ3件に同じ順で表示名を付ける。
    joined = yield Zip(labels, lengths)  # 対応する位置を結び、空文書の失敗も引き継ぐ。
    ranked = yield SortBy(lambda value: value[1], joined, reverse=True)  # 成功分を文字数の降順へ。
    top = yield Take(1, ranked)  # 成功した先頭1件を取り、失敗の記録は残す。
    total = yield Reduce(add, 0, lengths)  # 失敗を除いた文字数2と7を足して9を得る。
    top_history = yield Inspect(top)  # 最大の文書と、保持された空文書の失敗を取り出す。
    length_history = yield Inspect(lengths)  # 元の3件それぞれの成否と処理履歴を取り出す。
    return total, top_history, length_history  # 集計だけで失敗が隠れないよう履歴も返す。


p_documents = summarize_documents()  # 実行方針をまだ持たない、固定入力のProgram。
p_timing = pipeline()  # 同じ仕事で逐次と並行を比較できるProgram。


def verify() -> None:  # テスト境界なのでrunを使い、値と失敗方針を直接確認する。
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)  # 両戦略の時計を同じ時刻にそろえる。
    for strategy, seconds in ((sequential(), 6.0), (parallel(concurrency=3), 3.0)):  # 期待秒数。
        program = sim_time_handler(start_time=start)(strategy(p_timing))  # 仕事は同じまま方針を付ける。
        values, elapsed = run(scheduled(program))  # 仮想時間と並行タスクを実際に解釈する。
        assert values == [10, 20, 30]  # 完了時刻が違っても結果は入力順で一致する。
        assert elapsed == seconds  # 逐次1+2+3=6秒、3並行では最大の3秒になる。

    program = parallel(concurrency=2)(fail_handler(p_documents))  # Failを例外へ変え、要素ごとに隔離。
    total, top, history = run(scheduled(program))  # 最大2並行で、失敗を含む入力を処理する。
    assert total == 9  # 成功した2件だけがReduceへ渡ったことを確かめる。
    assert [row.value for row in top if not row.failed] == [("文書:もう少し長い文", 7)]  # 最大の成功値。
    assert len(top) == 2  # Take(1)は成功1件に加え、失敗の履歴1件も保持する。
    assert sum(row.failed for row in history) == 1  # 空文書だけが失敗したことを確かめる。
    assert len(history) == 3  # 成功2件と失敗1件の入力履歴が残っている。
    assert history[1].history[-1].event == "failed"  # 失敗が履歴上もfailedとして識別できる。

    try:  # 同じ計算を、最初の例外を全体へ伝える戦略でも確認する。
        strict = parallel_fail_fast(concurrency=2)(fail_handler(p_documents))  # 要素単位の隔離を外す。
        run(scheduled(strict))  # 空文書でValueErrorが呼び出し元へ届くことを期待する。
    except ValueError as error:  # 想定した種類の失敗だけを検証対象にする。
        if str(error) != "本文がありません":  # 別の原因のValueErrorを成功扱いしない。
            raise AssertionError("想定外の例外です") from error  # 元の例外を原因として残す。
    else:  # 例外が届かず成功した場合は、検証自体を失敗させる。
        raise AssertionError("失敗時には全体が中断されるはずです")  # 方針差の欠落を明示する。


if __name__ == "__main__":  # import時は計算を組み立てるだけにし、直接起動時だけ検証する。
    verify()  # 時間・結果順・履歴・集計・例外伝播の期待値を確認する。
    print("逐次6秒・並行3秒、失敗履歴、Zip・SortBy・Take・Reduce: OK")  # 全検証が通った印。
