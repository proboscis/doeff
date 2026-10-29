"""操作の領域を値として検査し、実際のハンドラの結果も確認する。"""

from dataclasses import dataclass  # 依頼の引数を変更不能な値としてまとめる。

from doeff_domain import (  # 領域の宣言と、検査の依頼とその答え手を使う。
    AssertDomainCovered,  # 宣言した操作が対応ハンドラで網羅されるかを検査する依頼。
    AssertNoOrphanEffects,  # 指定モジュールの操作に所属先があるかを検査する依頼。
    Domain,  # 操作と対応ハンドラを組にした宣言の型。
    DomainCoverageError,  # 対応ハンドラが足りないときの例外。
    Handles,  # ハンドラが扱う操作を領域の側で明示する宣言の型。
    domain_registry,  # 領域の表から検査の依頼に答えるハンドラを作る。
)

from doeff import (  # 依頼・合成・実行を使う。
    Effect,  # 独自の依頼ReadTitleの基底型を使う。
    EffectGenerator,  # yieldする計算の最終結果を型で示す。
    Pass,  # 対象外の依頼を外側へ渡す。
    Resume,  # 求められた値で続きkを再開する。
    do,  # 生成器を合成可能なProgramにする。
    handler,  # ハンドラをProgramへ設置する。
    run,  # テストでProgramをVMに渡して結果を得る。
)


@dataclass(frozen=True)  # 依頼を受け取った後で文書が書き換わらないようにする。
class ReadTitle(Effect):  # 文書の先頭行を求める操作を表す。
    document: str  # 先頭行と本文を含む文字列をハンドラへ渡す。


@handler  # @doの関数をハンドラにする。
def title_handler(effect, k):  # 依頼と、その結果を待つ続きkを受け取る。
    if isinstance(effect, ReadTitle):  # 見出しの依頼だけをここで解釈する。
        return (yield Resume(k, effect.document.splitlines()[0]))  # 先頭行で呼び出し元を再開する。
    return (yield Pass(effect, k))  # ほかの操作は外側のハンドラへ渡す。


@do  # 見出し取得を別の処理からyieldできるProgramにする。
def read_title(document: str) -> EffectGenerator[str]:  # 文字列を受け取り見出しを返す。 # doeff: kleisli
    return (yield ReadTitle(document))  # ハンドラが返した先頭行をそのまま結果にする。


document_domain = Domain(  # 登録表を触らず、領域の宣言を値として作る。
    name="article-document",  # 領域を識別する名前を付ける。
    title="記事の文書操作",  # 人が読む表示名を付ける。
    effects=(ReadTitle,),  # この領域が導入する操作を1つ指定する。
    handlers=(Handles(title_handler, ReadTitle),),  # title_handlerがReadTitleを扱うことを検査器へ伝える。
)


def verify() -> None:  # 通る宣言・落ちる宣言・実際の結果をオフラインで確認する。
    covered = domain_registry(domains=[document_domain])  # 正常例だけを載せた表から検査に答えるハンドラを作る。
    run(covered(AssertDomainCovered(document_domain)))  # ReadTitleの対応宣言があるので例外なく通る。
    program = read_title("見出し\n本文")  # 入力を固定した見出し取得のProgramを作る。
    assert run(handler(title_handler)(program)) == "見出し"  # 設置したハンドラが先頭行を返す。
    incomplete = Domain(  # 同じ操作から対応ハンドラだけを省いた反例を作る。
        name="article-incomplete",  # 正常例と区別できる名前を付ける。
        title="ハンドラの指定漏れ",  # 何が欠けた例なのかを示す。
        effects=(ReadTitle,),  # ReadTitleへの対応が必要であることは変えない。
        handlers=(),  # 対応するハンドラを宣言しない。
    )
    try:  # 宣言の不備が例外になることをテストする。
        run(domain_registry(domains=[incomplete])(AssertDomainCovered(incomplete)))  # 未対応なのでDomainCoverageErrorになる。
    except DomainCoverageError as error:  # 想定した検査エラーだけを受け取る。
        if "ReadTitle" not in str(error):  # 指定漏れの操作名が診断に含まれない場合を調べる。
            raise AssertionError("診断に操作名がありません") from error  # 説明不足の診断も検査失敗にする。
    else:  # 例外が出なければ検査の期待に反する。
        raise AssertionError("対応するハンドラがないのに検査が通りました")  # 誤った成功を拒否する。


if __name__ == "__main__":  # CLIから直接実行したときだけ検証と登録を行う。
    verify()  # 領域の対応関係と見出し取得の結果を検査する。
    registered = domain_registry(domains=[document_domain])  # ReadTitleの所属先を載せた表で答えるハンドラを作る。
    run(registered(AssertNoOrphanEffects([__name__])))  # このモジュールのReadTitleに所属漏れがないので通る。
    print("ドメインの対応検査・指定漏れ検出・所属検査: OK")  # 全確認の成功を表示する。
