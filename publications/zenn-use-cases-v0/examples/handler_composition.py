"""ハンドラの順序・別操作の合成・適用範囲を検証する。"""

from dataclasses import dataclass  # 引数を持たない依頼もデータ型として宣言できるようにする

from doeff_core_effects import (  # 価格を状態から読む依頼と、その値を供給するハンドラを使う
    Get,  # 状態に保存した価格を要求する
    state,  # この計算の価格をメモリ内に保持して供給する
)  # 状態を読む側と、値を供給する側の両方を揃える
from doeff_core_effects.scheduler import (  # 時間待ちの再開を支える実行基盤を選ぶ
    scheduled,  # 時間ハンドラが使う待機・再開をスケジューラで扱う
)  # スケジューラで包めるようにする
from doeff_time import (  # 待機と時計を仮想時間で解釈し、実時間の待機を省く
    Delay,  # 指定秒数の待機を時間ハンドラに依頼する
    GetTime,  # 処理の前後で仮想時刻を取得する
    sim_time_handler,  # 時計を進めて待機を処理する解釈を供給する
)  # 仮想時間での経過秒数を検証するためのAPIを揃える

from doeff import (  # 依頼と続きを扱い、ハンドラの入れ子を構築・検証する
    Effect,  # 価格や通貨の依頼に専用の型を与える
    Pass,  # 担当外の依頼を元の続きとともに外側へ渡す
    Resume,  # 依頼への値を渡して元の続きを再開する
    do,  # 依頼と補助計算をyieldで合成できるようにする
    handler,  # 計算を担当ハンドラで包む
    run,  # 検証の境界で構成済みの計算を実行する
    with_handlers,  # 外側から順に並べたリストを入れ子へ変換する
)  # 依頼・解釈・実行を分けて検証できるようにする


@dataclass(frozen=True)  # 発行した依頼の属性を書き換えない型にする
class ReadPrice(Effect):  # 整数の価格を要求する専用の操作を定義する
    pass  # この依頼には追加の引数がないため、属性を増やさない


@dataclass(frozen=True)  # 発行した依頼の属性を書き換えない型にする
class ReadCurrency(Effect):  # 通貨コードを要求する専用の操作を定義する
    pass  # この依頼には追加の引数がないため、属性を増やさない


@do  # 依頼を受けて続きへ値を返せる価格ハンドラにする
def base_price(effect, k):  # 価格の依頼に100を返し、呼び出し元を再開する
    if isinstance(effect, ReadPrice):  # 価格の依頼だけを、このハンドラの処理対象にする
        return (yield Resume(k, 100))  # 価格を待つ元の続きへ100を渡し、その実行結果を返す
    return (yield Pass(effect, k))  # 担当外の依頼と元の続きを外側へ渡し、ここでは再開しない


@do  # 外側への再依頼と手数料加算後の再開を合成するハンドラにする
def add_fee(effect, k):  # 外側が返す価格へ手数料10を足して返す
    if isinstance(effect, ReadPrice):  # 価格の依頼だけを、このハンドラの処理対象にする
        price = yield effect  # 同じ価格の依頼を外側へ発行し、加工前の値を受け取る
        return (yield Resume(k, price + 10))  # 手数料10を足し、100なら110、80なら90で元の続きを再開する
    return (yield Pass(effect, k))  # 担当外の依頼と元の続きを外側へ渡し、ここでは再開しない


@do  # 外側への再依頼と割引後の再開を合成するハンドラにする
def discount(effect, k):  # 外側が返す価格を2割引きして、元の依頼へ返す
    if isinstance(effect, ReadPrice):  # 価格の依頼だけを、このハンドラの処理対象にする
        price = yield effect  # 同じ価格の依頼を外側へ発行し、加工前の値を受け取る
        return (yield Resume(k, price * 80 // 100))  # 整数で2割引きし、100なら80、110なら88で元の続きを再開する
    return (yield Pass(effect, k))  # 担当外の依頼と元の続きを外側へ渡し、ここでは再開しない


@do  # 依頼を受けて続きへ値を返せる通貨ハンドラにする
def yen(effect, k):  # 通貨の依頼にJPYを返し、呼び出し元を再開する
    if isinstance(effect, ReadCurrency):  # 通貨の依頼だけを、このハンドラの処理対象にする
        return (yield Resume(k, "JPY"))  # 通貨を待つ元の続きへJPYを渡し、その実行結果を返す
    return (yield Pass(effect, k))  # 担当外の依頼と元の続きを外側へ渡し、ここでは再開しない


@do  # 価格と通貨の依頼をyieldで合成する計算にする
def quote():  # 価格と通貨を組み合わせた見積もり文字列を作る
    price = yield ReadPrice()  # 取り付けたハンドラの構成で決まる価格を受け取る
    currency = yield ReadCurrency()  # 外側のyenハンドラからJPYを受け取る
    return f"{currency} {price}"  # 通貨と価格を結合し、JPY 100などの見積もりを返す


@do  # 異なる適用範囲の価格取得を順番に合成する計算にする
def scoped_prices():  # 割引の適用範囲が2回目の依頼だけであることを観測する
    normal = yield ReadPrice()  # 割引の外で依頼し、基準価格100を受け取る
    reduced = yield handler(discount)(ReadPrice())  # この依頼だけを割引で包み、価格80を受け取る
    restored = yield ReadPrice()  # 割引の範囲を出た次の依頼では、再び100を受け取る
    return normal, reduced, restored  # 適用範囲の違いを比較できる3価格の組を返す


@do  # 状態取得と時間待ちを、差し替え可能な依頼として合成する
def read_delayed_price():  # 状態の価格を読み、仮想時間で2秒後に返す手順を作る
    price = yield Get("unit_price")  # 外側のstateから、この例で初期値に指定した100を読む
    yield Delay(2)  # 時間ハンドラへ2秒の待機を依頼し、仮想時刻を進める
    return price  # 待機が終わったら、先ほど読んだ価格100を返す


@do  # 補助計算からの価格取得で依頼に応じるハンドラにする
def stored_price(effect, k):  # 価格の依頼を、状態取得と待機を行う手順へ翻訳する
    if isinstance(effect, ReadPrice):  # 価格の依頼だけを、このハンドラの処理対象にする
        price = yield read_delayed_price()  # 補助計算をyieldし、その状態取得と待機も外側の解釈へ委ねる
        return (yield Resume(k, price))  # 補助計算が返した価格で元の続きを再開する
    return (yield Pass(effect, k))  # 担当外の依頼と元の続きを外側へ渡し、ここでは再開しない


@do  # 時計と価格の依頼を順番に扱う計算にする
def timed_price():  # 解釈された価格と、その取得に経過した秒数を返す
    start = yield GetTime()  # 価格取得前の仮想時刻を記録し、経過時間の基準にする
    price = yield ReadPrice()  # 取り付けたハンドラの構成で決まる価格を受け取る
    end = yield GetTime()  # 価格取得後の仮想時刻を読み、2秒の進みを観測する
    return price, (end - start).total_seconds()  # 割引後の価格80と、仮想時間の経過2秒を返す


def verify() -> None:  # 実行境界で順序・範囲・状態と時間の期待値を確認する
    fee_then_discount = handler(yen)(  # 通貨の供給を、手数料を加えてから割り引く構成の外側へ置く
        handler(base_price)(handler(add_fee)(handler(discount)(quote())))  # 100に10を足してから2割引きする入れ子を作る
    )  # ここまでの範囲を包んだ計算を作り、この時点では実行しない
    discount_then_fee = handler(yen)(  # 通貨の供給を、割引後に手数料を加える構成の外側へ置く
        handler(base_price)(handler(discount)(handler(add_fee)(quote())))  # 100を2割引きしてから10を足す入れ子を作る
    )  # ここまでの範囲を包んだ計算を作り、この時点では実行しない
    assert run(fee_then_discount) == "JPY 88"  # 手数料のあとで割り引いた結果がJPY 88と確認する
    assert run(discount_then_fee) == "JPY 90"  # 割引のあとで手数料を足した結果がJPY 90と確認する
    assert run(with_handlers([yen, base_price, add_fee, discount], quote())) == "JPY 88"  # リスト形式が同じ入れ子と同じJPY 88を返すと確認する
    assert run(handler(base_price)(scoped_prices())) == (100, 80, 100)  # 割引が2回目だけに適用され、次の依頼へ漏れないと確認する
    timed = scheduled(  # 状態からの価格取得と仮想時間の待機を動かす構成を作る
        sim_time_handler()(  # DelayとGetTimeを仮想時間として解釈する
            state(initial={"unit_price": 100})(  # この範囲で読む基準価格を100として供給する
                handler(stored_price)(handler(discount)(timed_price()))  # 計時する価格取得を割引で包み、その外側で状態と待機へ翻訳する
            )  # ここまでの範囲を包んだ計算を作り、この時点では実行しない
        )  # ここまでの範囲を包んだ計算を作り、この時点では実行しない
    )  # ここまでの範囲を包んだ計算を作り、この時点では実行しない
    assert run(timed) == (80, 2.0)  # 状態を読む解釈でも割引が効き、仮想時間だけ2秒進むと確認する
    print("順序: JPY 88 / JPY 90、範囲: (100, 80, 100)、状態と時間: (80, 2.0)")  # 全assertの通過後に、検証できた数値を表示する


if __name__ == "__main__":  # 直接実行した場合だけ検証し、import時には実行しない
    verify()  # 外部サービスを呼ばず、記事の価格・範囲・時間の契約を確かめる
