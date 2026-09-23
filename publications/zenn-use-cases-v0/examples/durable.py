"""文書処理の完了結果をSQLiteへ保存し、3プロセスで再利用する検証例。"""

import argparse  # 検証コマンドから保存先と実行段階を受け取る。
from pathlib import Path  # SQLiteの保存先をパスとして扱う。

from doeff_core_effects.cache import cache  # 関数名と引数をキーに結果の再利用を依頼する。
from doeff_core_effects.handlers import (  # 保存の待機と内部ログを扱う。
    await_handler,  # SQLiteの待機が終わると処理を再開する。
    slog_discard_handler,  # Memo内部のログを捨て、本文の診断表示だけを残す。
)
from doeff_core_effects.memo_handlers import memo_handler  # Memoの依頼を選んだ保存先へ接続する。
from doeff_core_effects.scheduler import scheduled  # SQLite処理の非同期待機を進める。
from doeff_core_effects.storage import SQLiteStorage  # プロセス終了後も結果を保持する保存先を使う。

from doeff import do, run  # 計算をProgramにし、検証の外側で実行する。


@cache()  # 同じ本文とparser_versionなら保存済みの解析結果を返す。
@do  # 解析をほかのProgramからyieldできる計算にする。
def parse_document(text: str, parser_version: str):  # 版は計算内容でなくキャッシュ識別に使う。
    print("本文を解析しました")  # キャッシュにないときだけ、この診断表示が出る。
    return tuple(line.strip() for line in text.splitlines() if line.strip())  # 空行を除いた行の組を返す。


@cache()  # 同じ行の組とoutline_versionなら保存済みの見出しを返す。
@do  # 見出し抽出もyieldで合成するProgramにする。
def make_outline(lines: tuple[str, ...], outline_version: str):  # 版を変えると別の結果として保存する。
    print("見出しを抽出しました")  # このステップが実際に計算された場合だけ表示する。
    return tuple(line for line in lines if line.startswith("#"))  # 見出しの2行を返す。


@do  # 初回プロセスで実行する解析段階をProgramにする。
def prepare():  # 本文と処理の版を固定した計算を組み立てる。
    return (yield parse_document("# はじめに\n説明文\n# 遊び方", "parser-v1"))  # 解析済みの3行を返す。


@do  # 解析と見出し抽出をひとつのProgramへ合成する。
def finish():  # 後続プロセスではこの全体を先頭から実行する。
    lines = yield prepare()  # 解析が保存済みなら、本文を再解析せず3行を受け取る。
    return (yield make_outline(lines, "outline-v1"))  # 未保存なら見出しを計算し、保存済みなら再利用する。


if __name__ == "__main__":  # このファイルを起動した検証プロセスだけで実行する。
    cli = argparse.ArgumentParser(description=__doc__)  # 3プロセス実験用の引数を解析する。
    cli.add_argument("db", type=Path)  # 全プロセスで共有するSQLiteファイルを受け取る。
    cli.add_argument("stage", choices=["prepare", "finish"])  # 解析だけか全体かを検証時に選ぶ。
    args = cli.parse_args()  # 指定された保存先と段階を取り出す。
    programs = {"prepare": prepare, "finish": finish}  # それぞれの段階を専用Programへ対応付ける。
    storage = SQLiteStorage(args.db)  # 指定したSQLiteファイルを作成するか開く。
    program = memo_handler(storage)(programs[args.stage]())  # 選んだ段階のMemo依頼をSQLiteで扱う。
    try:  # 実行が失敗した場合も、下のfinallyで参照と接続を解放する。
        print(run(scheduled(await_handler()(slog_discard_handler(program)))))  # 解析3行か見出し2行を表示する。
    finally:  # 正常終了と例外終了のどちらでも後始末する。
        storage.close()  # 呼出元スレッドのSQLite接続を閉じる。
        del program  # ハンドラから保存先への参照を解放する。
        del storage  # この検証プロセスが保持する保存先の参照も解放する。
