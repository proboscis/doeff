"""Hy記事の全ブロックと、説明した評価順序・契約・pytest接続を検証する。"""

import os  # 子プロセスへ例のimportパスを引き継ぐ。
import re  # 記事からPythonとHyのコードブロックを取り出す。
import subprocess  # pytestが生成されたテストを実際に収集するか確認する。
import sys  # 例のディレクトリをimport対象へ追加する。
from pathlib import Path  # 記事と専用例を、リポジトリ内の位置から見つける。
from tempfile import TemporaryDirectory  # pytestの派生ファイルを検査後に除去する。

import hy  # Hyの読み込みとマクロ展開を有効にする。
import pytest  # 契約違反が明示的な例外になることを検査する。
from doeff_core_effects import Ask  # 問い合わせられたキーを型付きで検査する。

from doeff import Transfer, do, handler, run  # 問い合わせを記録するハンドラと実行境界を使う。

SOURCE = Path(__file__).resolve().parents[1]  # publicationの正本ディレクトリを求める。
sys.path.insert(0, str(SOURCE / "examples"))  # 完全な例を記事と同じ名前でimport可能にする。
article = (SOURCE / "doeff-hy.md").read_text()  # 実際に掲載する本文を読む。
blocks = re.findall(r"```(python|hy)\n(.*?)```", article, re.S)  # 全7ブロックを取得する。
namespaces = []  # 後で契約・評価順を調べるため、各例の定義を保持する。
fixture_code = ""  # pytest用ブロックを、後の収集検査にも使う。
for index, (language, code) in enumerate(blocks):  # 各ブロックを独立した名前空間で実行する。
    namespace = {"__name__": f"_hy_article_{index}"}  # 前のブロックの定義に依存させない。
    if language == "hy":  # Hyコードを現在のマクロ実装で評価する。
        hy.eval(hy.read_many(code), globals=namespace)  # ブロック末尾のassertまで実行する。
    else:  # Python比較例とpytest接続例を実行する。
        exec(compile(code, f"article:{index}", "exec"), namespace)  # Pythonとしてコンパイルし、その場で評価する。
        if "doeff_interpreter" in namespace:  # pytest接続例を見つける。
            fixture_code = code  # 同じ掲載コードをそのままpytestにも渡す。
            namespace["test_greeting"](namespace["doeff_interpreter"].__wrapped__())  # 生成テストへ実際のハンドラを渡す。
    namespaces.append(namespace)  # 単独実行できた定義を保存する。

assert len(blocks) == 7  # コードブロックの検証漏れを防ぐ。
assert fixture_code  # pytest接続例を取り出せたことを確認する。
for namespace in namespaces:  # Python・Hyのどちらのgreetも入力契約を調べる。
    if "greet" in namespace and "_hy_macros" in namespace:  # Hyブロックの契約付きgreetだけを対象にする。
        with pytest.raises(AssertionError, match="pre-condition"):  # 型違反がAskより前に検出されると期待する。
            run(namespace["greet"](123))  # 文字列の契約へ整数を渡す。

total_namespace = next(ns for ns in namespaces if "total" in ns)  # 価格と送料の例を取り出す。
keys = []  # ハンドラが受け取った問い合わせの順序を記録する。

@handler  # 下位クライアントを作らず、Askの解釈を差し替える。
@do  # ハンドラ本体もProgramとして継続操作をyieldする。
def record_ask(effect, continuation):  # 問い合わせと、それを待つ継続を受け取る。
    assert isinstance(effect, Ask)  # この検査にはAskだけが届くと確認する。
    keys.append(effect.key)  # 価格と送料の実際の依頼順を記録する。
    values = {"price": 1000, "shipping": 200}  # 固定値で評価順を検査する。
    return (yield Transfer(continuation, values[effect.key]))  # 値を返し、計算の続きを再開する。

assert run(record_ask(total_namespace["total"]())) == 1200  # 2つの返却値が合計されると確認する。
assert keys == ["price", "shipping"]  # 記載した順番に問い合わせられたことを確認する。
optional_namespace = next(ns for ns in namespaces if "optional_price" in ns)  # 条件分岐の例を取り出す。
assert run(optional_namespace["optional_price"](False)) == 0  # 未選択のAskはハンドラがなくても実行されない。

contract_namespace = {"__name__": "_hy_post_contract"}  # 結果の型違反を独立して確認する。
hy.eval(hy.read_many("""
(require doeff-hy.macros [defk]) ; 結果の契約を付けたProgramを作る。
(defk invalid-result [] ; 契約が破られた場合の検証専用関数。
  {:pre [] :post [(: % str)]} ; 結果を文字列と宣言する。
  123) ; 故意に整数を返し、事後条件違反を発生させる。
"""), globals=contract_namespace)  # 間違いを含む検証用Programを組み立てる。
with pytest.raises(AssertionError, match="post-condition"):  # 返り値の契約違反も検出されると期待する。
    run(contract_namespace["invalid_result"]())  # 実行時に事後条件を検査する。

with TemporaryDirectory(prefix="doeff-hy-article-") as directory:  # 派生したpytest用ファイルを一時保存する。
    test_path = Path(directory) / "test_hy_example.py"  # pytestの収集対象になる名前を選ぶ。
    test_path.write_text(fixture_code)  # 掲載ブロックを改変せずに保存する。
    environment = dict(os.environ)  # 既存の実行環境へ追加のimportパスだけ設定する。
    environment["PYTHONPATH"] = os.pathsep.join(map(str, sys.path))  # 完全な例を子プロセスからも読み込めるようにする。
    result = subprocess.run(  # 実際のpytest収集とfixture解決を確認する。
        [sys.executable, "-m", "pytest", "-q", str(test_path) + "::test_greeting"],
        env=environment, capture_output=True, text=True, timeout=60, check=True,
    )
    assert "1 passed" in result.stdout  # Hyが生成したテスト1件が成功したことを確認する。
    print(result.stdout.strip())  # 収集・実行結果を監査ログへ残す。

print(f"OK: 記事{len(blocks)}ブロック・完全な例・契約・評価順・pytest接続")  # 検証した範囲を明記する。
