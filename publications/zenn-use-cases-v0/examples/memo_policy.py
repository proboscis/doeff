"""多段Memoの補充・コスト経路・削除・保持方針を、外部通信なしで確かめる。"""

from pathlib import Path  # SQLiteの保存先を、検証用ディレクトリの下へ組み立てる。
from tempfile import TemporaryDirectory  # 検証で作ったSQLiteだけを終了時に片付ける。

import pytest  # 全層で見つからないときのKeyErrorを検査する。
from doeff_core_effects import HttpRequest  # 通信方法から独立したHTTP依頼の型を使う。
from doeff_core_effects.handlers import (  # 保存層の待機と診断ログを受け持つ。
    await_handler,  # InMemoryStorageやSQLiteStorageが出すAwaitを進める。
    slog_discard_handler,  # 検証中のMemo診断ログは表示しない。
)
from doeff_core_effects.http_handlers import http_production_handler  # HTTP取得だけの担当を選ぶ。
from doeff_core_effects.memo_effects import (  # 保存先に依存しない4種類の操作を使う。
    MemoDelete,  # 読み出し対象になる層から同じキーを削除する。
    MemoExists,  # 値を取得・補充せず、存在だけを問い合わせる。
    MemoGet,  # 見つかった値を受け取り、手前の空の層へ補充する。
    MemoPut,  # 同じコストを扱う全層へ値を書き込む。
)
from doeff_core_effects.memo_handlers import (  # 操作の再利用と保存先を別々に組み立てる。
    make_memo_rewriter,  # 対象の依頼を保存済み判定・取得・保存へ分解する。
    memo_handler,  # 指定した保存先をMemo操作の担当にする。
)
from doeff_core_effects.memo_policy import (  # 経路選択と保持方針を明示する型を使う。
    Lifecycle,  # 保持の意図を示す列挙値であり、標準層の期限処理ではない。
    MemoPolicy,  # 再計算コスト・保持の意図・TTL・メタデータを依頼に添える。
    RecomputeCost,  # 安価・高価・再現困難のどれを扱うかを選ぶ。
)
from doeff_core_effects.scheduler import scheduled  # 保存層のAwaitを進めるスケジューラを付ける。
from doeff_core_effects.storage import (  # 同じMemoハンドラに異なる保存先を渡せる。
    InMemoryStorage,  # プロセス内だけで保持する、検証用の保存先を使う。
    SQLiteStorage,  # ファイルへ保存し、別のインスタンスでも結果を再利用する。
)

from doeff import do, run  # @doで処理を合成し、runは検証境界だけで呼ぶ。


@do  # 本文からもyieldして使える、階層動作の検証Programにする。
def inspect_layers(l1, l2, l3):  # 3層は検証の観測対象として明示的に受け取る。
    yield l3.put("answer", 42)  # テストの準備として、一番外側だけに42を置く。
    assert (yield MemoExists("answer")) is True  # L1とL2を通過し、L3で存在を確認する。
    assert list(l1.keys()) == list(l2.keys()) == []  # 存在確認だけでは値を補充しない。
    value = yield MemoGet("answer")  # L3の42が、L2・L1へ戻りながら保存される。
    copied = [(yield l1.get("answer")), (yield l2.get("answer"))]  # 補充後の2層を直接観測する。
    assert value == 42  # L3に用意した値が、処理本体まで戻る。
    assert copied == [42, 42]  # 戻り値だけでなく、手前の2層への補充も確認する。
    yield l3.put("answer", 99)  # 外側だけを書き換え、どの層の値を読むかを区別する。
    assert (yield MemoGet("answer")) == 42  # L1に値があればそこで確定し、L3の99は読まない。
    assert (yield MemoPut("answer", 100)) is None  # 明示的な保存は対象全層へ伝播し、Noneを返す。
    assert all(dict(layer.items()) == {"answer": 100} for layer in (l1, l2, l3))  # 3層が100になる。
    assert (yield MemoDelete("answer")) is None  # 削除も対象全層へ伝播し、Noneを返す。
    assert all(list(layer.keys()) == [] for layer in (l1, l2, l3))  # 古い値を残さず全層から消す。
    assert (yield MemoExists("answer")) is False  # 全層の不在を、Falseとして受け取る。
    return value, copied  # 最初の取得と補充の結果として、(42, [42, 42])を返す。


@do  # コストごとの操作も、通常の@do処理として合成する。
def store_results():  # キーごとに一貫した再計算コストを指定する。
    yield MemoPut("preview", "短い結果", recompute_cost=RecomputeCost.CHEAP)  # L1と安価層へ置く。
    yield MemoPut("analysis", "高価な結果", recompute_cost=RecomputeCost.EXPENSIVE)  # L1と高価層へ置く。
    yield MemoPut("snapshot", "再取得不能", recompute_cost=RecomputeCost.IRREPRODUCIBLE)  # 高価層も扱う。
    return (yield MemoGet("analysis", recompute_cost=RecomputeCost.EXPENSIVE))  # 高価な結果を受け取る。


@do  # 誤った削除コストの影響を、再現できる検証Programとして示す。
def inspect_deletion(l1, expensive):  # 削除先と残存する値を直接観測する。
    yield MemoDelete("analysis")  # 既定はCHEAPなので、L1から消えても高価層には届かない。
    assert "analysis" not in list(l1.keys())  # L1からは消えたため、一見すると削除できている。
    assert "analysis" in list(expensive.keys())  # 高価層に古い値が残ることを確認する。
    restored = yield MemoGet("analysis", recompute_cost=RecomputeCost.EXPENSIVE)  # 残った値が戻る。
    assert restored == "高価な結果"  # 高価層に残っていた値が返される。
    assert "analysis" in list(l1.keys())  # 読み出しでL1にも再補充される。
    yield MemoDelete("analysis", recompute_cost=RecomputeCost.EXPENSIVE)  # 保存時と同じコストで消す。
    assert "analysis" not in list(l1.keys())  # 今度もL1から消える。
    assert "analysis" not in list(expensive.keys())  # 同じコストを扱う高価層からも消える。
    return (yield MemoExists("analysis", recompute_cost=RecomputeCost.EXPENSIVE))  # 全対象層でFalse。


@do  # 存在しない値への直接読み出しを、検証からyieldまたはrunできるようにする。
def read_missing():  # MemoGetは自動計算の依頼ではないことを確認する。
    return (yield MemoGet("missing"))  # 外側にも値がなければKeyErrorになり、このreturnは完了しない。


@do  # 保存先の生成自体を、必要になるまで実行しないProgramにする。
def open_expensive_store(opened):  # openedは検証専用の生成回数記録で、接続設定ではない。
    opened.append("高価層")  # この行へ到達したときだけ、保存先が必要になったと分かる。
    return InMemoryStorage()  # 検証では通信を伴わない保存先を返す。


@do  # 保持方針と実際の保存動作を同じProgram内で確認する。
def inspect_policy():  # 方針の指定だけで期限処理が動くと誤解しないための例にする。
    policy = MemoPolicy(  # 依頼に添付する方針を明示的に構築する。
        recompute_cost=RecomputeCost.EXPENSIVE,  # 標準memo_handlerが経路選択に使う値。
        lifecycle=Lifecycle.PERSISTENT,  # 永続保持の意図であり、メモリを永続化はしない。
        ttl=0,  # 即時期限切れを意図しても、標準memo_handlerはこの値を処理しない。
        metadata={"format": "article-outline-v1"},  # 形式の版を依頼に添える。
    )
    request = MemoPut("outline", ["準備", "遊び方"], policy=policy)  # 方針付きの保存依頼を作る。
    assert request.policy is policy  # 同じMemoPolicyが依頼に保持されることを確かめる。
    yield request  # 標準memo_handlerはキーと値を保存先へ渡し、TTLなどは渡さない。
    return (yield MemoGet("outline", recompute_cost=RecomputeCost.EXPENSIVE))  # ttl=0でも値が残る。


@do  # HTTPの処理本体は、取得方法と保存先のどちらからも独立させる。
def fetch_text(url: str):  # 実行方法を引数のモードやクライアントで切り替えない。
    response = yield HttpRequest("GET", url)  # HTTP担当かMemoの保存済み応答を受け取る。
    response.raise_for_status()  # 失敗したHTTP状態は例外として呼び出し元へ伝える。
    return response.text  # 成功した本文だけを返す。


p_request = make_memo_rewriter(HttpRequest)(fetch_text("https://example.invalid/article"))  # 再利用判定。
p_cached = memo_handler(InMemoryStorage())(p_request)  # 保存先はHTTP担当から独立に取り付ける。
p_http = http_production_handler()(p_cached)  # 保存ミス時のHTTP取得方法を選ぶ。ここでは実行しない。


def verify() -> None:  # テスト境界でのみrunし、外部へ接続しない複数の構成を検査する。
    def execute(program):  # 共通の実行環境を、検証専用の関数にまとめる。
        wrapped = slog_discard_handler(program)  # メモ化の診断ログを受け取り、画面には出さない。
        wrapped = await_handler()(wrapped)  # 保存先のAwaitを実行し、結果を各yieldへ戻す。
        return run(scheduled(wrapped))  # スケジューラを付けて検証Programを最後まで進める。

    l1, l2, l3 = InMemoryStorage(), InMemoryStorage(), InMemoryStorage()  # 空の3層を個別に作る。
    program = memo_handler(l1, name="L1")(inspect_layers(l1, l2, l3))  # 本体に一番近いL1を付ける。
    program = memo_handler(l2, name="L2")(program)  # L1の外側に、次の問い合わせ先L2を付ける。
    program = memo_handler(l3, name="L3")(program)  # 最後の問い合わせ先L3を一番外側へ付ける。
    assert execute(program) == (42, [42, 42])  # L3からの取得・補充・全層への保存と削除を検査する。
    with pytest.raises(KeyError, match="missing"):  # 最後の層にも値がなければKeyErrorになる。
        execute(memo_handler(l1)(read_missing()))  # 自動計算や暗黙のNoneには置き換えない。

    l1, temporary, expensive = InMemoryStorage(), InMemoryStorage(), InMemoryStorage()  # 経路別の保存先。
    program = memo_handler(l1)(store_results())  # L1はcost=Noneなので、全コストを受け取る。
    program = memo_handler(temporary, cost=RecomputeCost.CHEAP)(program)  # 安価な結果だけを扱う。
    program = memo_handler(expensive, cost=RecomputeCost.EXPENSIVE)(program)  # 高価・再現困難を扱う。
    assert execute(program) == "高価な結果"  # 保存したanalysisを同じコストで取得できる。
    assert set(l1.keys()) == {"preview", "analysis", "snapshot"}  # L1は3種類とも保持する。
    assert set(temporary.keys()) == {"preview"}  # 安価層には高価・再現困難な結果が入らない。
    assert set(expensive.keys()) == {"analysis", "snapshot"}  # 高価層は再現困難な結果も保持する。
    program = memo_handler(l1)(inspect_deletion(l1, expensive))  # 同じ保存先を使って削除を比較する。
    program = memo_handler(temporary, cost=RecomputeCost.CHEAP)(program)  # 安価層の経路も維持する。
    program = memo_handler(expensive, cost=RecomputeCost.EXPENSIVE)(program)  # 高価層の経路を維持する。
    assert execute(program) is False  # 同じコストで削除した後には対象層のどこにも値がない。
    assert set(expensive.keys()) == {"snapshot"}  # 別のキーに保存した再現困難な値は消さない。

    opened: list[str] = []  # 遅延生成した保存先の回数だけを記録する。
    lazy_layer = memo_handler(open_expensive_store(opened), cost=RecomputeCost.EXPENSIVE)  # 未生成。
    assert opened == []  # ハンドラを組み立てただけでは保存先を生成しない。
    cheap_layer = memo_handler(InMemoryStorage(), cost=RecomputeCost.CHEAP)  # 安価な依頼の担当を付ける。
    assert execute(lazy_layer(cheap_layer(MemoPut("preview", 1)))) is None  # 安価な保存だけを行う。
    assert opened == []  # 通過するだけの高価層は、保存先を必要としない。
    execute(lazy_layer(cheap_layer(store_results())))  # 高価・再現困難な依頼が初めてこの層へ届く。
    assert opened == ["高価層"]  # 複数のMemo操作があっても、同じハンドラ内では生成は一度。

    assert execute(memo_handler(InMemoryStorage())(inspect_policy())) == ["準備", "遊び方"]  # TTL未適用。
    with TemporaryDirectory() as directory:  # 永続層の検証が所有するSQLiteファイルを用意する。
        database = Path(directory) / "memo.sqlite"  # 同じパスを開けば、ハンドラを作り直しても読める。
        first = memo_handler(SQLiteStorage(database))(MemoPut("saved", 42))  # 最初の層で保存する。
        assert execute(first) is None  # 保存が完了してから、別の層を作る。
        second = memo_handler(SQLiteStorage(database))(MemoGet("saved"))  # 新しい保存先インスタンスを使う。
        assert execute(second) == 42  # 同じファイルに記録された42を取得できる。


if __name__ == "__main__":  # 直接実行したときだけ、検証を開始する。
    verify()  # HTTP用のp_httpは実行せず、ローカルのメモリ・SQLiteだけを検査する。
    print("Memoの階層・コスト・削除・遅延生成・保持方針・SQLite: OK")  # 全確認を通過したときだけ表示。
