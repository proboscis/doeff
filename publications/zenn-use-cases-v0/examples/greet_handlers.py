# ruff: noqa: I001  # importの並びを本文の枠と同じに保つ。
"""記事「doeffとは」の完全な例。同じgreetへ花子と太郎のハンドラを取り付けて実行する。

本文の2つのコードの枠を、この順のまま1つのファイルにした。reviews/main-check.pyが本文との一致を確かめる。
"""

from doeff import Pass, Resume, do, handler, run, with_handlers  # 依頼の委譲・再開・処理の定義と実行を使う。
from doeff_core_effects import Ask  # キーを指定して値を依頼するエフェクトを使う。

@do  # greet()を呼ぶと、実行する手順を表すProgramができる。
def greet():  # 名前の取得方法を決めずに、挨拶を組み立てる。
    name = yield Ask("name")  # 名前を依頼し、ハンドラから返された値を受け取る。
    return f"こんにちは、{name}さん"  # 受け取った名前を含む挨拶を返す。

@handler  # この処理をProgramに取り付けられるハンドラにする。
@do  # ハンドラ自身もyieldで再開や委譲を指示する。
def handle_ask_effect(effect, k):  # effectは届いた依頼、kは中断した処理の続き。
    if isinstance(effect, Ask) and effect.key == "name":  # 名前を求める依頼だけを担当する。
        return (yield Resume(k, "花子"))  # nameに「花子」を渡してgreetを再開する。
    return (yield Pass(effect, k))  # 担当外の依頼は、外側のハンドラへ渡す。

assert run(with_handlers([handle_ask_effect], greet())) == "こんにちは、花子さん"  # 取り付けて実行し、挨拶を確認する。

@handler  # 同じエフェクトを別の方法で扱うハンドラを作る。
@do  # 再開と委譲を、最初のハンドラと同じ書き方で指示する。
def handle_test_ask_effect(effect, k):  # 依頼の型と再開する値の契約を保つ。
    if isinstance(effect, Ask) and effect.key == "name":  # 同じ名前の依頼を受け取る。
        return (yield Resume(k, "太郎"))  # 今度はnameへ「太郎」を渡す。
    return (yield Pass(effect, k))  # 担当外の依頼は外側へ渡す。

assert run(with_handlers([handle_test_ask_effect], greet())) == "こんにちは、太郎さん"  # 本体を変えず、ハンドラ交換で結果が変わる。

print(run(with_handlers([handle_ask_effect], greet())))  # 「こんにちは、花子さん」と表示する。
print(run(with_handlers([handle_test_ask_effect], greet())))  # 「こんにちは、太郎さん」と表示する。
