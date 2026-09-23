"""依存の差し替え・遅延解決・Local・環境変数を外部接続なしで確認する。"""

from typing import Protocol  # 注入する処理の呼び出し契約を型で表す。
from unittest.mock import patch  # 架空の環境変数をテスト中だけ設定する。

from doeff_core_effects import Ask, Local, Tell  # 依存要求・局所変更・観測ログを使う。
from doeff_core_effects.handlers import (  # 依存供給とログ保存の担当を組み合わせる。
    env_var_ask,  # Askを接頭辞付きの環境変数へ接続する。
    lazy_ask,  # Programで渡した依存を必要時に解決する。
    reader,  # 辞書に入れた値をそのまま供給する。
    state,  # writerがログを保存する状態を提供する。
    writer,  # Tellの内容を記録する。
    writer_log,  # 記録した内容をProgramとして読み出す。
)
from doeff_core_effects.scheduler import scheduled  # 遅延解決の排他制御を実行する。

from doeff import Program, do, run  # Programを組み立て、確認用の境界で実行する。


class FormatTitleFn(Protocol):  # 処理の種類を表す型をAskのキーにも使う。
    def __call__(self, text: str) -> Program[str]: ...  # 見出しを返すProgramを要求する。


@do  # 装飾しない実装も、呼び出すとProgramになる。
def plain_title(text: str):  # 加工対象の見出しは通常の引数で受け取る。
    return text  # 実行すると入力と同じ見出しを返す。


@do  # 装飾する実装を、同じProgramの契約で提供する。
def bracketed_title(text: str):  # 同じ入力型で実装を差し替えられる。
    return f"【{text}】"  # 実行すると括弧付きの見出しを返す。


@do  # 依存の取得と、取得した処理の実行を合成する。
def make_title(text: str):  # 呼び出し元は具体的な整形実装を渡さなくてよい。
    formatter: FormatTitleFn = yield Ask(FormatTitleFn)  # ハンドラから整形処理を受け取る。
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
    built = yield writer_log()  # 作成時にだけ出たTellをリストで受け取る。
    return messages, built  # 表示内容と作成回数を別々に検証できる。


def verify() -> None:  # runを使う場所は、このオフライン確認の境界にまとめる。
    plain = reader(env={FormatTitleFn: plain_title})(make_title("新着記事"))  # 無装飾を供給する。
    bracketed = reader(env={FormatTitleFn: bracketed_title})(make_title("新着記事"))  # 括弧版を供給する。
    assert run(plain) == "新着記事"  # 同じ利用側から無装飾の見出しが得られる。
    assert run(bracketed) == "【新着記事】"  # 実装だけを替えると装飾が変わる。

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
    print("依存の差し替え・遅延解決・Localの分離・環境変数: OK")  # 全assertの成功を伝える。
