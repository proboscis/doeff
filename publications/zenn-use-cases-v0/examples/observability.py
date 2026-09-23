"""ログの表示・Writerの蓄積・Listenの収集を、別々に確認する。"""

from contextlib import redirect_stderr  # テスト中の標準エラーを捕まえ、表示内容を検査する。
from io import StringIO  # ネットワークもファイルも使わず、表示先をメモリに置く。

from doeff_core_effects import (  # 観測対象と、ログを発行する公開APIを使う。
    Listen,  # 子Programの指定エフェクトを、戻り値と一緒に受け取る。
    SlogEffect,  # 構造化ログを表す型で、WriterTellEffectとは別の型になる。
    Tell,  # Writerへ渡す値をWriterTellEffectとして作る。
    WriterTellEffect,  # ListenでTellの依頼だけを選ぶために使う。
    slog,  # メッセージと名前付き属性を持つSlogEffectを作る。
)
from doeff_core_effects.handlers import (  # 用途の異なるハンドラを外側で組み合わせる。
    listen_handler,  # Listenの対象Programへ観測ハンドラを取り付ける。
    slog_discard_handler,  # 構造化ログを受け取り、画面には表示しない。
    slog_handler,  # 構造化ログを標準エラーへ表示する。
    state,  # Writerの蓄積先を保持し、実行ごとに新しく作る。
    writer,  # TellのメッセージをStateへ蓄積する。
    writer_log,  # 蓄積したTellのメッセージ一覧をコピーして返す。
)
from doeff_vm import UnhandledEffect  # ログを処理するハンドラがない場合の失敗を確かめる。

from doeff import do, run  # 計算は@doで合成し、このファイルのテストだけでrunする。


@do  # 進捗の記録と完了判定を、呼び出し側からyieldできる処理にする。
def summarize_progress(completed: int, total: int):  # 完了件数と対象件数を受け取る。
    yield slog("処理の進捗", completed=completed, total=total)  # 例では3件中3件を記録する。
    return completed == total  # 3と3ならTrue、1と3ならFalseを返す。


@do  # 変換処理を定義し、ログの表示先はここでは決めない。
def convert():  # サンプルの3ページの変換結果を返す。
    yield Tell("変換を開始")  # Writerへ文字列を渡し、処理を続ける。
    yield slog("変換が完了", pages=3)  # 観測用ログに処理済みページ数3を添える。
    return "完了"  # ログとは別に、後続が使う処理結果を返す。


@do  # 観測する範囲を、convertという子Programに限定する。
def observe():  # 結果・収集した依頼・Writerの蓄積を返す。
    yield Tell("観測前")  # Listenの対象外なので、Writerにだけ蓄積される。
    result, captured = yield Listen(  # 子Programの戻り値と、依頼のリストを受け取る。
        convert(), types=(WriterTellEffect, SlogEffect)  # convertから出る2種類の依頼を選ぶ。
    )  # 2件を収集する。
    yield Tell("観測後")  # このTellもListenの対象外だが、Writerには蓄積される。
    written = yield writer_log()  # Tellだけの3件を、独立した一覧として受け取る。
    return result, captured, written  # 結果は「完了」、収集は2件、Writerは3件となる。


@do  # 既定のListenが何を収集するか、別のProgramとして示す。
def observe_writer_only():  # typesを省いたときの結果を検査するために使う。
    return (yield Listen(convert()))  # WriterTellEffectだけの1件と「完了」を返す。


def verify() -> None:  # テストの実行境界で、表示・収集・対象範囲を検査する。
    output = StringIO()  # slog_handlerの表示内容をメモリへ受け取る。
    with redirect_stderr(output):  # 実際の表示ハンドラを動かし、その出力を捕まえる。
        program = listen_handler(observe())  # 子Programから指定した依頼を収集する。
        result, captured, written = run(state()(writer(slog_handler(program))))  # 表示と蓄積も行う。
    assert result == "完了"  # 観測を足しても、本体の戻り値が保たれる。
    assert len(captured) == 2  # 対象のconvertから出た2件だけを収集している。
    assert isinstance(  # 収集された依頼の型を確かめる。
        captured[0], WriterTellEffect  # 最初の依頼はTellが作る型と一致する。
    )  # 先頭は文字列そのものではなくTellの依頼になる。
    assert captured[0].msg == "変換を開始"  # Tellの依頼から、渡したメッセージを読める。
    assert isinstance(captured[1], SlogEffect)  # 2件目は構造化ログの依頼になる。
    assert captured[1].msg == "変換が完了"  # 構造化ログのメッセージが保たれる。
    assert captured[1].kwargs == {"pages": 3}  # 属性名と数値が文字列化されずに保たれる。
    assert written == ["観測前", "変換を開始", "観測後"]  # Listenの収集後もTellはWriterへ届く。
    assert (  # 標準エラーへ出たメッセージと属性の整形結果を確かめる。
        output.getvalue().strip() == "INFO 変換が完了 pages=3"  # 期待する1行と一致する。
    )  # Listenの収集後もslogは表示される。

    silent_output = StringIO()  # 無表示のハンドラに交換した結果を調べる。
    with redirect_stderr(silent_output):  # テスト用ハンドラが何も表示しないことを観測する。
        program = listen_handler(observe())  # 本体とListenは同じ定義を使う。
        silent = run(state()(writer(slog_discard_handler(program))))  # 表示先だけを交換する。
    assert silent_output.getvalue() == ""  # 表示を捨てる選択が適用されている。
    assert silent[0] == result  # 本体の戻り値は変わらない。
    assert silent[2] == written  # Writerの蓄積も変わらない。
    assert [effect.msg for effect in silent[1]] == [  # 交換後の収集結果を元の結果と比較する。
        effect.msg for effect in captured  # 元の収集結果から同じ順序のメッセージを取り出す。
    ]  # 収集も残る。

    program = listen_handler(observe_writer_only())  # typesを省いたListenを取り付ける。
    default_result, default_captured = run(  # 新しいStateと無表示のハンドラで実行する。
        state()(writer(slog_discard_handler(program)))  # TellもSlogも処理先へ届く構成にする。
    )
    assert default_result == "完了"  # 収集対象の絞り込みは本体の戻り値を変えない。
    assert len(default_captured) == 1  # 既定ではslogを含めず、Tellだけを収集する。
    assert isinstance(default_captured[0], WriterTellEffect)  # 唯一の収集結果はTellの依頼になる。
    assert run(slog_discard_handler(summarize_progress(3, 3))) is True  # 全件完了を判定する。
    assert run(slog_discard_handler(summarize_progress(1, 3))) is False  # 途中の状態を判定する。

    failure_output = StringIO()  # VMが出す期待したエラー表示を捕まえる。
    with redirect_stderr(failure_output):  # 失敗を検査しながら、例の通常出力へ混ぜない。
        try:  # Listenがあっても、元の依頼の処理先は必要なことを確かめる。
            run(listen_handler(observe_writer_only()))  # Writerがないため、Tellで失敗する。
        except UnhandledEffect as error:  # 期待した未処理エフェクトだけを検査する。
            failure_message = str(error)  # 失敗内容を後で検査するために取り出す。
        else:  # 誤って成功した場合には、検証自体を失敗させる。
            raise AssertionError("ListenだけでTellが処理されました")  # 観測と処理の混同を検出する。
    assert "Tell" in failure_message  # 失敗した依頼がTellであることも確認する。


if __name__ == "__main__":  # スクリプトとして起動したときだけオフライン検証を行う。
    verify()  # 表示と収集の独立性、属性、順序、範囲、未処理時の失敗を確認する。
    print("ログの表示・収集・対象範囲: OK")  # すべての検査を通過したことを表示する。
