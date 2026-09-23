"""yield・継続の再開・戻り順・one-shotを実VMで確認する。"""
from dataclasses import dataclass  # 引数を持たない不変の依頼型を定義するために読み込む。

from doeff_vm import PyVM  # PythonからRust VMを直接起動する入口を読み込む。

# 依頼・継続制御・計算の構築・実行に使うAPIを読み込む。
from doeff import Effect, Expand, Pass, Perform, Resume, Transfer, do, handler, run


@dataclass(frozen=True)  # 依頼の内容を後から変えられないデータ型にする。
class ReadName(Effect):  # 名前の取得を要求し、文字列を受け取る操作を定義する。
    pass  # この依頼には追加の引数がないため、フィールドを置かない。


@do  # 関数の呼び出しで、実行前の計算を組み立てられるようにする。
def greet():  # 名前を要求して挨拶文字列を返す計算を定義する。
    name = yield ReadName()  # 名前を要求して停止し、ハンドラから渡された文字列を受け取る。
    return f"こんにちは、{name}さん"  # 取得した名前を挨拶に埋め込み、計算の結果として返す。


@do  # 関数の呼び出しで、実行前の計算を組み立てられるようにする。
def supply_name(effect, k):  # 依頼と停止中の継続を受け取り、名前を供給するハンドラを定義する。
    if isinstance(effect, ReadName):  # 名前の取得依頼だけをこのハンドラで処理する。
        return (yield Resume(k, "花子"))  # 名前に「花子」を渡して継続を再開し、その結果を返す。
    return (yield Pass(effect, k))  # 担当しない依頼を、継続とともに外側のハンドラへ渡す。


@do  # 関数の呼び出しで、実行前の計算を組み立てられるようにする。
def explicit_greet():  # Performを明示した場合も同じ挨拶になる計算を定義する。
    name = yield Perform(ReadName())  # ReadNameの実行を明示し、再開時に名前の文字列を受け取る。
    return f"こんにちは、{name}さん"  # 取得した名前を挨拶に埋め込み、計算の結果として返す。


def verify() -> None:  # 実VMで各動作を検証し、不一致ならassertで失敗させる。
    assert run(handler(supply_name)(greet())) == "こんにちは、花子さん"  # 名前を供給して実行し、挨拶の内容を確認する。
    assert PyVM().run(handler(supply_name)(greet())) == "こんにちは、花子さん"  # VMを直接呼んでも同じ挨拶が返ることを確認する。
    # 明示的なPerformでも同じ結果になることを確認する。
    assert run(handler(supply_name)(explicit_greet())) == "こんにちは、花子さん"
    started: list[str] = []  # 構築時と実行時を区別するため、観測記録を空で用意する。

    @do  # 関数の呼び出しで、実行前の計算を組み立てられるようにする。
    def deferred():  # 実行開始を記録してからgreetへ進む計算を定義する。
        started.append("実行開始")  # 本体が実際に実行された時点で、観測用の記録を1件残す。
        return (yield greet())  # 子の計算もyieldで実行し、その挨拶を結果として返す。

    program = deferred()  # 計算を構築する。この時点では本体の記録処理を実行しない。
    assert isinstance(program, Expand)  # 呼び出し結果が実行前のExpand型の値であることを確認する。
    assert started == []  # 計算の構築だけでは本体が動いていないことを確認する。
    assert run(handler(supply_name)(program)) == "こんにちは、花子さん"  # 実行境界で計算を動かし、子の計算の結果を確認する。
    assert started == ["実行開始"]  # 実行後に、開始がちょうど1回記録されたことを確認する。

    forwarded: list[str] = []  # 内側のハンドラを通過した依頼の型を観測する記録を用意する。

    @do  # 関数の呼び出しで、実行前の計算を組み立てられるようにする。
    def forward(effect, k):  # 依頼を観測して外側へ渡すハンドラを定義する。
        forwarded.append(type(effect).__name__)  # 配送先を確認するため、受け取った型名を記録する。
        return (yield Pass(effect, k))  # 担当しない依頼を、継続とともに外側のハンドラへ渡す。

    # 内側で通過し、外側で供給された名前を確認する。
    assert run(handler(supply_name)(handler(forward)(greet()))) == "こんにちは、花子さん"
    assert forwarded == ["ReadName"]  # 内側のハンドラが名前の依頼を1回受け取ったことを確認する。

    trace: list[str] = []  # 本体とハンドラの進行順を観測する記録を用意する。

    @do  # 関数の呼び出しで、実行前の計算を組み立てられるようにする。
    def body():  # 名前の依頼前と再開後を記録する計算を定義する。
        trace.append("本体: 依頼前")  # 本体が名前を要求する直前であることを記録する。
        name = yield ReadName()  # 名前を要求して停止し、ハンドラから渡された文字列を受け取る。
        trace.append(f"本体: {name}で再開")  # 渡された名前で本体が再開したことを記録する。
        return name  # 受け取った名前を、この計算の完了結果として返す。

    @do(non_tail=True)  # 継続の完了後もハンドラで処理を続けることを明示する。
    def traced(effect, k):  # 継続を再開し、その完了後にも記録するハンドラを定義する。
        if isinstance(effect, ReadName):  # 名前の取得依頼だけをこのハンドラで処理する。
            trace.append("ハンドラ: 再開前")  # 依頼を受け取り、まだ本体を再開していない時点を記録する。
            result = yield Resume(k, "花子")  # 本体を再開し、継続が完了して返した結果を受け取る。
            trace.append(f"ハンドラ: 本体の結果={result}")  # 本体の完了後、ハンドラに戻ったことと結果を記録する。
            return result  # 本体から受け取った完了結果を、さらに外側へ返す。
        return (yield Pass(effect, k))  # 担当しない依頼を、継続とともに外側のハンドラへ渡す。

    assert run(handler(traced)(body())) == "花子"  # 本体とハンドラを通って最終的に名前が返ることを確認する。
    assert trace == [  # 本体、ハンドラ、再開した本体、戻ったハンドラの順序を確認する。
        "本体: 依頼前", "ハンドラ: 再開前",  # 先に本体が依頼し、次にハンドラが受け取る。
        "本体: 花子で再開", "ハンドラ: 本体の結果=花子",  # 再開した本体の完了後、ハンドラへ結果が戻る。
    ]  # 期待する4件の順序の定義を閉じ、観測結果との一致を確かめる。

    transferred: list[str] = []  # Transferを使うハンドラの呼び出し回数を観測する記録を用意する。

    @do  # 関数の呼び出しで、実行前の計算を組み立てられるようにする。
    def tail_name(effect, k):  # 名前を供給した後は自分へ戻らず継続へ移るハンドラを定義する。
        if isinstance(effect, ReadName):  # 名前の取得依頼だけをこのハンドラで処理する。
            transferred.append("名前を渡す")  # 名前の供給1回につき、観測用の記録を1件残す。
            return (yield Transfer(k, "花子"))  # 現在のハンドラ呼び出しを終え、名前を渡して本体へ移る。
        return (yield Pass(effect, k))  # 担当しない依頼を、継続とともに外側のハンドラへ渡す。

    @do  # 関数の呼び出しで、実行前の計算を組み立てられるようにする。
    def two_names():  # 同じハンドラ境界で2回の依頼を扱えるか確認する計算を定義する。
        first = yield ReadName()  # 1回目の名前を要求し、供給された文字列を受け取る。
        second = yield ReadName()  # 再開後にも名前を要求し、同じ境界のハンドラから値を受け取る。
        return first, second  # 2回の依頼の結果を組として返す。

    # Transferの後でも2回とも同じ境界で処理できることを確認する。
    assert run(handler(tail_name)(two_names())) == ("花子", "花子")
    assert transferred == ["名前を渡す", "名前を渡す"]  # ハンドラが2回呼ばれたことを記録でも確認する。

    @do(non_tail=True)  # 継続の完了後もハンドラで処理を続けることを明示する。
    def resume_twice(effect, k):  # 二重再開の拒否を検証するため、意図的に誤ったハンドラを定義する。
        if isinstance(effect, ReadName):  # 名前の取得依頼だけをこのハンドラで処理する。
            yield Resume(k, "花子")  # 最初の再開を行い、同じkをここで消費する。
            return (yield Resume(k, "太郎"))  # 消費済みのkを再利用する誤り。VMが例外で拒否することを期待する。
        return (yield Pass(effect, k))  # 担当しない依頼を、継続とともに外側のハンドラへ渡す。

    try:  # 二重再開が実行時エラーになることを検証する範囲を始める。
        PyVM().run(handler(resume_twice)(greet()))  # 意図的に誤ったハンドラを実VMで動かし、二重再開を試す。
    except RuntimeError as error:  # 期待する実行時エラーだけを受け取り、内容の確認へ進む。
        failure = str(error)  # 継続の消費を理由に失敗したか、後で確かめるために文字列を得る。
    else:  # 例外なしで終わった場合は、one-shotの制約が守られていない。
        raise AssertionError("同じ継続の二重再開を受理しました")  # 二重再開を受理した場合、検証自体を失敗させる。
    assert "consumed" in failure or "one-shot" in failure  # 失敗理由が継続の再利用であることを確認する。
    print("基本実行・遅延実行・Pass・戻り順・Transfer後の境界・二重再開の拒否: OK")  # すべてのassertを通過した検証結果を表示する。
    print(trace)  # 確認済みの4段階の実行順を表示する。


if __name__ == "__main__":  # このファイルを直接実行した場合だけ検証を起動する。
    verify()  # 外部サービスを使わず、Rust VMの各動作を検証する。
