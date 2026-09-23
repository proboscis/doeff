"""画像サービスを呼ばず、生成結果が次の編集へ渡ることを検証する。"""

from typing import Any  # 継続はハンドラから受け取り、具体的な実装型へ依存させない。

from doeff_gemini.handlers.testing import (  # Geminiの通信を固定応答へ交換する。
    mock_handlers as gemini_testing,  # Gemini用の固定応答を選ぶ。
)
from doeff_image.effects import ImageEdit, ImageGenerate  # 生成と編集を別の依頼として表す。
from doeff_image.types import ImageResult  # 戻り値を共通の画像コンテナで扱う。
from doeff_seedream.handlers.testing import (  # Seedreamの通信も同じ条件で交換する。
    mock_handlers as seedream_testing,  # Seedream用も確認する。
)
from PIL import Image  # 小さな検証用画像をメモリ内に用意する。

from doeff import (  # 依頼と再開を組み立てる。
    EffectBase,  # ハンドラが受け取る依頼の型を表す。
    EffectGenerator,  # yieldする関数の最終戻り値を注釈する。
    Pass,  # 担当しない依頼を外側へ渡す。
    Resume,  # 結果を渡して依頼元を再開する。
    do,  # 関数の呼び出しをProgramに変換する。
    handler,  # 生の依頼処理をProgramに取り付け可能にする。
    run,  # テスト境界でProgramを実行して値を受け取る。
)


@do  # 生成をProgramにして、呼び出し元からyieldで合成できるようにする。
def generate_cover(model: str) -> EffectGenerator[ImageResult]:  # 指定モデルで表紙を一枚依頼する。
    return (yield ImageGenerate(  # ハンドラの返すImageResultを、そのまま呼び出し元へ返す。
        prompt="カードゲームの説明書の表紙。文字を置く余白を確保する",  # 表紙の目的を伝える。
        model=model,  # 接続先で使うモデル名を依頼に含める。
    ))


@do  # 編集もProgramにして、別の制作処理から単独で再利用できるようにする。
def brighten_cover(generated: ImageResult, model: str) -> EffectGenerator[ImageResult]:  # 生成結果を受け取る。
    return (yield ImageEdit(  # 画像と指示をハンドラへ渡し、編集後のImageResultを待つ。
        prompt="中央のカードを残し、背景を明るくする",  # 維持する部分と変更する部分を指定する。
        model=model,  # 編集に使うモデル名も明示する。
        images=generated.images,  # 生成時に受け取ったPIL画像のリストを直接引き継ぐ。
    ))


@do  # 二つのProgramを同じyieldの規則で合成する。
def create_cover(model: str) -> EffectGenerator[ImageResult]:  # 表紙制作の順序だけを記述する。
    generated = yield generate_cover(model)  # 生成依頼が解釈され、画像入りの結果が戻る。
    edited = yield brighten_cover(generated, model)  # その画像を入力にして編集結果を受け取る。
    return edited  # 呼び出し元には編集後のImageResultが戻る。


p_cover_example = create_cover("fixture-image")  # 実行前のProgram。検証専用モデル名を固定する。


def verify() -> None:  # テスト境界でのみrunを呼び、外部APIへの依頼を発生させない。
    base = ImageResult(  # 生成ハンドラから返す結果を用意する。
        images=[Image.new("RGB", (8, 8), "navy")],  # 8×8の単色画像で受け渡しを判定する。
        model="fixture-image",  # Programへ渡した検証専用モデル名と一致させる。
        prompt="カードゲームの説明書の表紙。文字を置く余白を確保する",  # 生成指示を対応させる。
    )
    final = ImageResult(  # 編集ハンドラから返す、生成結果とは異なる結果を用意する。
        images=[Image.new("RGB", (8, 8), "white")],  # 別の画像で編集結果の返却を識別する。
        model="fixture-image",  # 同じ検証モデルを使う。
        prompt="中央のカードを残し、背景を明るくする",  # 編集指示を対応させる。
    )
    calls: list[str] = []  # 発行された依頼の順番をテストで観測する。

    @handler  # 生の依頼処理を、Programへ取り付けるハンドラに変換する。
    @do  # 固定画像を返すハンドラ自身もProgramとして動く。
    def fixed_images(effect: EffectBase, k: Any):  # kは依頼元へ結果を渡して再開するための継続。
        if isinstance(effect, ImageGenerate):  # 表紙の生成依頼を受け取る。
            assert effect.model == base.model  # 指定モデルが生成依頼に届くことを確認する。
            assert effect.prompt == base.prompt  # 指定した表紙の指示が保たれることを確認する。
            calls.append("generate")  # 編集より先に生成が起きたことを記録する。
            return (yield Resume(k, base))  # 生成結果を渡してgenerate_coverの続きを再開する。
        if isinstance(effect, ImageEdit):  # 生成に続く編集依頼を受け取る。
            assert effect.images is base.images  # 同じ画像リストが編集へ届くことを確認する。
            assert effect.model == final.model  # 編集側にも指定モデルが届くことを確認する。
            assert effect.prompt == final.prompt  # 編集指示が生成指示と混ざらないことを確認する。
            calls.append("edit")  # 編集依頼が発行されたことを記録する。
            return (yield Resume(k, final))  # 編集後の結果を返して制作処理を再開する。
        yield Pass()  # 画像以外の依頼は外側のハンドラへ委ねる。

    result = run(fixed_images(p_cover_example))  # 固定ハンドラだけで制作処理を実行する。
    assert result is final  # 制作処理の戻り値が、ハンドラの返した編集結果そのものであると確認する。
    assert calls == ["generate", "edit"]  # 二つの依頼が一回ずつ、この順番で発行されたと確認する。

    for model, install in [  # 付属のテスト用ハンドラにも同じ制作処理を接続する。
        ("gemini-example", gemini_testing()),  # Geminiの固定応答実装を取り付ける。
        ("seedream-example", seedream_testing()),  # Seedreamの固定応答実装へ差し替える。
    ]:
        mocked = run(install(create_cover(model)))  # 各固定応答で生成から編集まで実行する。
        assert isinstance(mocked, ImageResult)  # どちらも共通の戻り値型を返すと確認する。
        assert len(mocked.images) == 1  # 各テスト用ハンドラが画像を一枚返すと確認する。
        assert mocked.model == model  # モデル名が結果にも残ることを確認する。
        assert mocked.to_pil_image().size == (16, 16)  # 付属の固定応答は16×16のPIL画像である。


if __name__ == "__main__":  # 直接実行したときだけ、このオフライン検証を起動する。
    verify()  # 画像の受け渡し、依頼の順序、二つのテスト用ハンドラを確認する。
    print("生成画像の受け渡し・生成→編集の順序・2種類の固定ハンドラ: OK")  # 成功時だけ結果を表示する。
