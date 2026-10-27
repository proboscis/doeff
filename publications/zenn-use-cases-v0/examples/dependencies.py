"""依存の差し替え・自作ハンドラ・遅延解決・Local・環境変数を外部接続なしで確認する。"""

from typing import Protocol  # 型をAskのキーにする例で、呼び出し契約を表す。
from unittest.mock import patch  # 架空の環境変数をテスト中だけ設定する。

from doeff_core_effects import Ask, Local, ReadWriterLog, Tell  # 依存要求・局所変更・ログを使う。
from doeff_core_effects.handlers import (  # 依存供給とログ保存の担当を組み合わせる。
    env_var_ask,  # Askを接頭辞付きの環境変数へ接続する。
    lazy_ask,  # Programで渡した依存を必要時に解決する。
    reader,  # 辞書に入れた値をそのまま供給する。
    state,  # writerがログを保存する状態を提供する。
    writer,  # Tellの内容を記録する。
)
from doeff_core_effects.scheduler import scheduled  # 遅延解決の排他制御を実行する。

from doeff import Pass, Program, Resume, do, handler, run, with_handlers  # 定義・委譲・実行を使う。


@do  # 呼ぶと、まだ動いていない処理(Program)ができる。
def make_title(text: str):  # 見出しの整え方は、この関数の中で決めない。
    formatter = yield Ask("formatter")  # 整形の実装を外側へ依頼する。
    return (yield formatter(text))  # 受け取った実装を動かし、見出しの文字列を返す。


@do  # テスト用の実装も、呼ぶとProgramになる。
def plain_title(text: str):  # 加工する見出しは通常の引数で受け取る。
    return text  # 入力と同じ見出しを返す。


@do  # 本番用の実装を、同じ呼び方で提供する。
def bracketed_title(text: str):  # 入力の型はテスト用と同じにする。
    return f"【{text}】"  # 括弧で囲んだ見出しを返す。


@handler  # この関数を、Programに取り付けられるハンドラにする。
def test_formatter(effect, k):  # effectは届いた依頼、kは止まった処理の続き。
    if isinstance(effect, Ask) and effect.key == "formatter":  # 整形の依頼だけを担当する。
        return (yield Resume(k, plain_title))  # テスト用の実装を渡し、続きを動かす。
    return (yield Pass(effect, k))  # 担当外の依頼は、外側のハンドラへ渡す。


@do  # 見出しとサイト名を組み合わせる処理。
def make_heading(text: str):  # 依存の渡し方は、この関数も知らない。
    title = yield make_title(text)  # 前の例の処理を、yieldでそのまま合成する。
    site = yield Ask("site")  # サイト名も外側へ依頼する。
    return f"{title} | {site}"  # 2つの答えを1行の見出しにまとめる。


class FormatTitleFn(Protocol):  # 処理の種類を表す型をAskのキーにも使う。
    def __call__(self, text: str) -> Program[str]: ...  # 見出しを返すProgramを要求する。


@do  # 型をキーにしても、依頼の書き方は文字列のキーと同じ。
def make_typed_title(text: str):  # 受け取る値に型の注釈を付けられる。
    formatter: FormatTitleFn = yield Ask(FormatTitleFn)  # 型をキーに整形の実装を受け取る。
    return (yield formatter(text))  # そのProgramを実行し、見出しの文字列を返す。


@do  # 必要になるまで挨拶の組み立てを実行しない。
def build_greeting():  # 作成方針は、このProgramを解釈する環境で決める。
    prefix = yield Ask("prefix")  # 現在の範囲にある挨拶の接頭辞を取得する。
    yield Tell(prefix)  # 実際に作成した回数をwriterで観測できるようにする。
    return prefix + "読者のみなさん"  # 接頭辞に対応する挨拶を返す。


@do  # 依存の利用側を小さなProgramとして再利用する。
def read_greeting():  # 挨拶をどう作るかは利用側に埋め込まない。
    return (yield Ask("greeting"))  # 解決済み、または今解決した挨拶を返す。


@do  # 通常の取得と、局所的な変更を順番に合成する。
def localized_messages():  # 4回の取得結果から範囲の違いを確認する。
    first = yield read_greeting()  # 最初の取得では通常の挨拶を作る。
    second = yield read_greeting()  # 同じ範囲では作成済みの挨拶を再利用する。
    local = yield Local({"prefix": "こんばんは、"}, read_greeting())  # 中だけ夕方の挨拶にする。
    restored = yield read_greeting()  # Localを出ると通常の挨拶を再び受け取る。
    return first, second, local, restored  # 外・外・内・外の順で返す。


@do  # 結果と作成ログを同じハンドラの範囲から回収する。
def inspect_messages():  # テストに必要な観測値を戻り値へ含める。
    messages = yield localized_messages()  # 挨拶の取得を4回進める。
    built = yield ReadWriterLog()  # 作成時にだけ出たTellをリストで受け取る。
    return messages, built  # 表示内容と作成回数を別々に検証できる。


def verify() -> None:  # runを使う場所は、このオフライン確認の境界にまとめる。
    test_run = reader(env={"formatter": plain_title})(make_title("新着記事"))  # テスト用を渡す。
    prod_run = reader(env={"formatter": bracketed_title})(make_title("新着記事"))  # 本番用を渡す。
    assert run(test_run) == "新着記事"  # テスト用の実装では、見出しがそのまま返る。
    assert run(prod_run) == "【新着記事】"  # 外側を替えるだけで、括弧付きの見出しになる。

    inner = with_handlers([test_formatter], make_heading("新着記事"))  # 内側に自作のハンドラを付ける。
    outer = reader(env={"site": "技術ブログ"})(inner)  # サイト名は外側のreaderが答える。
    assert run(outer) == "新着記事 | 技術ブログ"  # 2つの依頼に、別々のハンドラが答える。

    typed = reader(env={FormatTitleFn: bracketed_title})(make_typed_title("新着記事"))  # 型のキーで渡す。
    assert run(typed) == "【新着記事】"  # ハッシュできる値なら、型もキーに使える。

    env = {"prefix": "こんにちは、", "greeting": build_greeting()}  # 値と未実行Programを渡す。
    program = lazy_ask(env=env)(writer(inspect_messages()))  # 依存解決の内側にもログを届ける。
    messages, built = run(scheduled(state()(program)))  # 排他制御とログ保存を外側に置いて実行する。
    first, second, local, restored = messages  # 各範囲の結果を名前付きで確認する。
    assert first == second == restored == "こんにちは、読者のみなさん"  # 外側の値は変わらない。
    assert local == "こんばんは、読者のみなさん"  # Local内だけ変更した接頭辞が反映される。
    assert built == ["こんにちは、", "こんばんは、"]  # 取得4回に対し作成は2回で済む。

    with patch.dict("os.environ", {"ARTICLE_DEMO_greeting": "環境からの挨拶"}):  # 終了時に元へ戻す。
        program = lazy_ask(env={})(read_greeting())  # 未登録のAskを外側へ渡す。
        sourced = env_var_ask(prefix="ARTICLE_DEMO_")(program)  # 外側で架空の環境変数から解決する。
        assert run(scheduled(sourced)) == "環境からの挨拶"  # 供給元を替えても利用側は同じ。


if __name__ == "__main__":  # import時にはテストを実行しない。
    verify()  # 依存の差し替えと、解決結果の再利用を確認する。
    print("依存の差し替え・自作ハンドラ・型のキー・遅延解決・Localの分離・環境変数: OK")  # 全assertの成功を伝える。
