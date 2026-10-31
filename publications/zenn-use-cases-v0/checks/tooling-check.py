"""tooling本文と専用例を、外部CLIやサービスへ接続せずに検証する。"""

import os  # 子プロセスのimport先を区切り文字でつなぐ。
import re  # 本文中のコードブロックを取り出す。
import sys  # 現在と同じPython環境を検証にも使う。
from pathlib import Path  # リポジトリ内の教材を見つける。
from tempfile import TemporaryDirectory  # 本文のHy断片だけを一時ファイルで実行する。

from doeff_core_effects.os_process import subprocess_handler  # 子を起こす答え手。
from doeff_core_effects.process_effects import EnvEntry, EnvMode, RunProcess  # 依頼の型。

from doeff import run, with_handlers  # 依頼を答え手のもとで1回実行する。

base = Path(__file__).resolve().parents[1]  # 記事シリーズのディレクトリを求める。
root = base.parents[1]  # packagesを持つリポジトリのルートを求める。
paths = [root, base / "examples"]  # doeff本体と記事の例をimport対象にする。
paths += [  # ワークスペースの各パッケージを同じソース版で読み込む。
    p / "src" if (p / "src").is_dir() else p  # 各パッケージの配置形式に合わせる。
    for p in sorted((root / "packages").iterdir()) if p.is_dir()  # パッケージのディレクトリだけを選ぶ。
]
env = (  # 子プロセスだけに重ねる検証用の環境(PATHなど既存の実行環境の継承は答え手が行う)。
    EnvEntry(name="PYTHONPATH", value=os.pathsep.join(map(str, paths))),  # ワークスペースのimport先を指定する。
    EnvEntry(name="SEMGREP_SEND_METRICS", value="off"),  # Semgrepの外部メトリクス送信を無効にする。
)
source = (base / "doeff-tooling.md").read_text()  # 編集後の本文を検証対象にする。


def execute(args):  # 子プロセスの失敗を検証失敗として伝える。
    request = RunProcess(argv=tuple(args), cwd=str(root), env=env, env_mode=EnvMode.EXTEND, timeout=60.0)  # 1回分の依頼。
    outcome = run(with_handlers([subprocess_handler], request))  # 子プロセスを起こし、終わりを待つ。
    print(outcome.stdout, end="")  # 子プロセスの出力を、これまでどおり画面へ出す。
    print(outcome.stderr, end="", file=sys.stderr)  # 子プロセスの診断も画面へ出す。
    assert outcome.exit_code == 0, args  # 成功時だけ次の検査へ進む(時間切れは終了コード124)。


execute([sys.executable, str(base / "examples/domain_check.py")])  # 対応・指定漏れ・所属を確認する。
blocks = re.findall(r"```python\n(.*?)```", source, re.S)  # 本文のPythonを取り出す。
assert len(blocks) == 4  # 検査対象のブロックが追加・削除されていないことを確認する。
code = "\n".join([blocks[1], blocks[0], blocks[2], blocks[3]])  # 完全な定義を先に置いて抜粋を実行する。
execute([sys.executable, "-c", code])  # 本文の全Python例が期待結果まで動くことを確認する。

for index, block in enumerate(re.findall(r"```hy\n(.*?)```", source, re.S)):  # Hyの全3ブロックを検証する。
    if index == 0:  # ADRブロックは生成されたテストも呼ぶ。
        block = "(import doeff [run])\n" + block  # 純粋なテストProgram用のrunを読み込む。
        block += "\n(test-title-contract (fn [program #** kwargs] (run program)))"  # 正常例と反例を検証する。
        block += "\n(test-ADR-ARTICLE-TITLE-adr-contract)"  # ADRの構造契約も検証する。
    if index == 1:  # 静的検査は生成されたSemgrepテストも呼ぶ。
        block += "\n(test-no-empty-title-defsemgrep)"  # badが検出されgoodが通ることを確認する。
    with TemporaryDirectory() as directory:  # 教材の正本を変更せず実行用Hyファイルを用意する。
        path = Path(directory) / f"tooling_block_{index}.hy"  # ブロックごとに独立した実行対象を作る。
        path.write_text(block)  # 本文そのものと、明示的な検証呼び出しを保存する。
        execute([sys.executable, "-m", "hy", str(path)])  # Hyのマクロ展開と実行結果を確認する。

for block in re.findall(r"```bash\n(.*?)```", source, re.S):  # 掲載した呼び出し用のコードを取り出す。
    if "<<'PYTHON'" in block:  # 実行するのはオフラインのADR/Semgrepテストに限定する。
        python = block.split("\n", 1)[1].rsplit("\nPYTHON", 1)[0]  # Python部分だけを取り出す。
        execute([sys.executable, "-c", python])  # uvによる環境更新をせず、同じPythonで呼び方を確認する。

print("tooling: Python 4 / Hy 3 / 呼び出し例 2 / 専用例 OK")  # 確認した範囲を明示して終了する。
