"""永続実行の記事本文・3プロセス・履歴ハンドラをオフラインで検証する。"""

import contextlib  # 診断表示を捕捉し、本体の実行有無を調べる。
import importlib.util  # 既存の回帰シナリオを記事の実行入口で動かす。
import io  # 標準出力を文字列として保持する。
import os  # 子プロセスの検索パスと検証用の作業ディレクトリを設定する。
import re  # 記事のPythonブロックを抜き出す。
import subprocess  # メモリを共有しない3つのPythonプロセスを起動する。
import sys  # 現在と同じPython環境を子プロセスで使う。
from pathlib import Path  # リポジトリ内の原稿と実装を参照する。
from tempfile import TemporaryDirectory  # 検証結果の保存先を毎回分離する。

ROOT = Path(__file__).resolve().parents[3]  # 作業ディレクトリに依存せずリポジトリを見つける。
SOURCE = ROOT / "publications/zenn-use-cases-v0"  # 検証する記事と例の共通ディレクトリを得る。
paths = [ROOT, SOURCE / "examples"]  # 本体と記事例をimport可能にする。
for package in sorted((ROOT / "packages").iterdir()):  # 開発中の各公開パッケージを直接参照する。
    if package.is_dir():  # パッケージディレクトリだけを候補にする。
        paths.append(package / "src" if (package / "src").is_dir() else package)  # 配置形式に合わせる。
sys.path[:0] = list(map(str, paths))  # インストール済みの旧版より、このcheckoutを優先する。
env = dict(os.environ)  # 認証情報は参照・表示せず、通常の実行環境を継承する。
env["PYTHONPATH"] = os.pathsep.join(map(str, paths))  # 子プロセスにも同じ実装を選ばせる。

with TemporaryDirectory() as directory:  # 過去のキャッシュがない保存先で試す。
    db = str(Path(directory) / "document.sqlite")  # 3つのプロセスが共有するのはこのDBだけにする。
    outputs = []  # 各プロセスの標準出力を比較する。
    for stage in ("prepare", "finish", "finish"):  # 解析、全体、全体を順に起動する。
        completed = subprocess.run(  # 各段階を別のPythonプロセスで実行する。
            [sys.executable, str(SOURCE / "examples/durable.py"), db, stage],
            cwd=ROOT, env=env, text=True, capture_output=True, check=True, timeout=60,
        )
        assert not completed.stderr, completed.stderr  # 終了処理を含めて警告や例外が出ていないことを確認する。
        outputs.append(completed.stdout)  # 後で診断表示と結果を検査する。
    assert outputs[0] == "本文を解析しました\n('# はじめに', '説明文', '# 遊び方')\n"  # 初回は解析だけ動く。
    assert outputs[1] == "見出しを抽出しました\n('# はじめに', '# 遊び方')\n"  # 次は抽出だけ動く。
    assert outputs[2] == "('# はじめに', '# 遊び方')\n"  # 3回目は計算本体がどちらも動かない。
print("OK: SQLite 3プロセスの実行回数と返り値")  # 確認できた範囲を表示する。

article = (SOURCE / "doeff-durable.md").read_text()  # 公開用本文そのものを検証対象にする。
blocks = re.findall(r"```python\n(.*?)```", article, re.S)  # Pythonの4ブロックを取り出す。
assert len(blocks) == 4  # 未検証の追加ブロックがあれば検査を止める。
namespace = {"__name__": "durable_article_check"}  # 定義同士を記事の掲載順でつなぐ。
exec(compile(blocks[0], "durable:block-1", "exec"), namespace)  # 解析・抽出・合成を記事から定義する。
with TemporaryDirectory() as directory:  # 本文中の固定ファイル名を検証用ディレクトリへ閉じ込める。
    previous = Path.cwd()  # 検証後に元の作業ディレクトリへ戻す。
    try:  # キャッシュ検査が失敗しても作業ディレクトリを復元する。
        os.chdir(directory)  # document-memo.sqliteをリポジトリに残さない。
        outputs = []  # 本文の実行構成もキャッシュヒットを確認する。
        for _ in range(2):  # 新しいハンドラと接続で2回実行する。
            output = io.StringIO()  # 本体実行の診断だけを捕捉する。
            with contextlib.redirect_stdout(output):  # 本文のassertもそのまま実行する。
                exec(compile(blocks[1], "durable:block-2", "exec"), namespace)  # 見出し2行の一致を検証する。
            outputs.append(output.getvalue())  # 初回と2回目の本体実行を比較する。
        assert outputs == ["本文を解析しました\n見出しを抽出しました\n", ""]  # 2回目は両方とも保存結果を使う。
    finally:  # 後続のConductor例が相対パスを正しく解決できるようにする。
        os.chdir(previous)  # 元の作業ディレクトリを復元する。
os.chdir(ROOT)  # 記事の相対ワークフローパスをリポジトリから解決する。
exec(compile(blocks[2], "durable:block-3", "exec"), namespace)  # 時刻と乱数を履歴から再取得する。
exec(compile(blocks[3], "durable:block-4", "exec"), namespace)  # 記事のJournaledAgentHandler実行入口を定義する。
print("OK: 本文4ブロックとConductorの時刻・乱数履歴")  # 実際に通した本文の範囲を示す。

path = ROOT / "packages/doeff-conductor/tests/test_agent_journal_c3.py"  # 実エージェントを使わない回帰例を選ぶ。
spec = importlib.util.spec_from_file_location("durable_journal_scenarios", path)  # ファイルから検証シナリオを読み込む。
assert spec is not None  # モジュールの仕様を読み込めない場合は停止する。
assert spec.loader is not None  # 実行用のローダーがなければ停止する。
scenarios = importlib.util.module_from_spec(spec)  # 回帰シナリオの名前空間を用意する。
spec.loader.exec_module(scenarios)  # モックの準備と検証関数を読み込む。


def article_runner(program, *, runtime, state_dir):  # 回帰テストの呼出形式を記事の関数へ接続する。
    return namespace["run_recorded_agents"](program, runtime, state_dir)  # 実行・保存・再生は本文コードを使う。


scenarios._run_with_journal = article_runner  # テスト専用の差し替えで本文の呼出方法を直接確認する。
with TemporaryDirectory() as directory:  # この回帰シナリオの履歴を独立させる。
    scenarios.test_kill_and_resume_replays_longest_valid_prefix_under_stubs(Path(directory))  # 2件再利用、3件目だけ実行。
print("OK: 本文のハンドラ構成で例外後の完了2件を再利用")  # 強制終了試験とは区別して報告する。
