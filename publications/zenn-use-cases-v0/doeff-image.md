---
title: "画像生成も編集も、同じ処理へ組み込む — doeff-image"
emoji: "🔁"
type: "tech"
topics: ["python", "doeff", "設計"]
published: false
---

**画像を作る。できた画像を次の編集へ渡す。** この順序は制作のロジックです。サービスとの通信はハンドラに任せ、生成も編集も`@do`の処理として組み合わせます。

```python
from doeff import do  # 依頼をyieldして進めるProgramを作る。
from doeff_image.effects import ImageGenerate  # 画像生成の依頼を表す。

@do  # 呼び出した時点では生成せず、実行可能なProgramを返す。
def cover_image(subject: str, model: str):  # 題材と利用するモデル名を受け取る。
    return (yield ImageGenerate(  # ハンドラへ依頼し、ImageResultを受け取って返す。
        prompt=f"{subject}を説明する、日本語の記事向けのイラスト",  # 作る画像の目的を伝える。
        model=model,  # ハンドラが扱うモデル名を指定する。
    ))
```

`yield ImageGenerate(...)`から戻る値は、画像を含む`ImageResult`です。この関数は、SDKのクライアントを作ったり、非同期処理のループを管理したりしません。

![生成と編集を、ひとつの制作処理へ](/images/zenn-use-cases-v0/generated/image-concept.png)

生成と編集を別の依頼として表し、生成結果の画像を次の編集へ渡します。通信方法は取り付けるハンドラが担当します。

## 生成結果を、そのまま次の編集へ渡す

`doeff-image`の共通契約は、`ImageGenerate`と`ImageEdit`、戻り値の`ImageResult`です。`ImageResult.images`は`list[PIL.Image.Image]`なので、次の編集依頼へそのまま渡せます。

```python
from doeff import do  # 生成と編集を小さなProgramに分け、yieldで合成する。
from doeff_image.effects import ImageEdit, ImageGenerate  # 生成依頼と編集依頼を使う。
from doeff_image.types import ImageResult  # 画像リストを保持する共通の戻り値型を使う。

@do  # 生成だけを依頼する再利用可能なProgramにする。
def generate_cover(model: str):  # 指定モデルに表紙を依頼する。
    return (yield ImageGenerate(  # 生成結果のImageResultを呼び出し元へ返す。
        prompt="カードゲームの説明書の表紙。文字を置く余白を確保する",  # 表紙の構図を伝える。
        model=model,  # 生成先のモデル名を依頼に含める。
    ))

@do  # 編集もProgramにし、生成以外から得た画像にも再利用できるようにする。
def brighten_cover(generated: ImageResult, model: str):  # 編集対象の画像入り結果を受け取る。
    return (yield ImageEdit(  # 画像と指示を渡し、編集後のImageResultを受け取る。
        prompt="中央のカードを残し、背景を明るくする",  # 変更箇所を指定する。
        model=model,  # 編集先のモデル名を指定する。
        images=generated.images,  # 生成結果のPIL画像リストをコピーせず引き継ぐ。
    ))

@do  # helperをyieldして一つの制作処理へ合成する。
def create_cover(model: str):  # 生成してから編集する、という順序を表す。
    generated = yield generate_cover(model)  # 生成ハンドラが返したImageResultを受け取る。
    edited = yield brighten_cover(generated, model)  # その画像を編集し、新しいImageResultを受け取る。
    return edited  # 後続の制作処理へ編集結果を渡す。
```

後続の処理も`yield create_cover(model)`で結果を受け取れます。ファイルの受け渡しや`async def`への変換は、この合成に必要ありません。並行に複数案を作るなら、[Traverseのページ](doeff-traverse.md)のように、このProgramを他のProgramと組み合わせます。

## ハンドラを差し替えて、外部生成なしで確かめる

GeminiとSeedreamには、共通の画像エフェクトを受け取る本番用・テスト用のハンドラがあります。まず、上で定義した`create_cover`へテスト用を取り付けます。

```python
from doeff import run  # ここではテストの境界としてProgramを実行する。
from doeff_gemini.handlers.testing import mock_handlers as gemini_testing  # Geminiの固定応答を使う。
from doeff_seedream.handlers.testing import mock_handlers as seedream_testing  # Seedreamの固定応答を使う。

for model, install in [  # 同じ制作処理を二つの固定応答実装で試す。
    ("gemini-example", gemini_testing()),  # テスト専用モデル名とGeminiのハンドラを組み合わせる。
    ("seedream-example", seedream_testing()),  # テスト専用モデル名とSeedreamのハンドラを組み合わせる。
]:
    result = run(install(create_cover(model)))  # 生成と編集の依頼を、このハンドラで解釈する。
    assert isinstance(result, ImageResult)  # どちらのハンドラでも共通の結果型が戻る。
    assert len(result.images) == 1  # 固定応答に含まれる画像は一枚。
    assert result.model == model  # 指定モデル名を結果から参照できる。
    assert result.to_pil_image().size == (16, 16)  # 先頭の画像を取り出すと16×16のPIL画像が得られる。
```

ここで確認できるのは、**依頼と戻り値が接続できること**です。付属のテスト用ハンドラは画像の内容を実際に編集しません。テスト専用モデル名も、本番で使えるモデル名ではありません。

[完全な例](examples/image_pipeline.py)では、さらに独自の固定応答ハンドラを取り付け、次を確認しています。

- 生成、編集の順で依頼が一回ずつ発行される。
- 編集依頼へ、生成ハンドラが返した画像リストそのものが届く。
- 制作処理の戻り値は、編集ハンドラが返した結果そのものである。

ハンドラが値を返して処理を再開する部分は、次の形です。`base`は生成用、`final`は編集用の固定した`ImageResult`で、完全な例の検証関数内で用意しています。

```python
from doeff import Pass, Resume, do, handler  # 結果を返して再開する操作と、他のハンドラへ委ねる操作を使う。

@handler  # 依頼処理を、Programへ取り付けられるハンドラにする。
@do  # ハンドラ自身もyieldで再開操作を依頼する。
def fixed_images(effect, k):  # effectは画像の依頼、kはその結果を待っている継続。
    if isinstance(effect, ImageGenerate):  # 生成依頼を受け取った場合だけ処理する。
        return (yield Resume(k, base))  # 固定の生成結果を渡し、生成を待つProgramを再開する。
    if isinstance(effect, ImageEdit):  # 続いて発行される編集依頼を受け取る。
        assert effect.images is base.images  # 生成時の画像リストがそのまま編集へ届くと確認する。
        return (yield Resume(k, final))  # 固定の編集結果を返し、制作処理を再開する。
    yield Pass()  # 画像以外の依頼を、外側のハンドラへ渡す。
```

## 本番ハンドラとの境界

本番用のハンドラもProgramへ取り付ける形は同じです。次は、その接続を**定義するだけ**のコードです。前の例の`create_cover`を使い、SDKを動かす外側の実行環境はまだ取り付けていません。

```python
from doeff_gemini.handlers.production import production_handlers as gemini_handlers  # Geminiへの変換を選ぶ。
from doeff_seedream.handlers.production import production_handlers as seedream_handlers  # Seedreamへの変換を選ぶ。

p_gemini_cover = gemini_handlers()(create_cover("gemini-3-pro-image"))  # Gemini用Programを組み立てる。
p_seedream_cover = seedream_handlers()(create_cover("seedream-4"))  # Seedream用Programを組み立てる。
```

モデル名は参照した実装が振り分け対象として扱う例です。現在のサービスでの提供や利用権限を確認したものではありません。本番では認証、SDKの非同期呼び出しを扱う`Await`、各実装が発行する状態・ログなどのエフェクトも、実行環境側で解釈する必要があります。

共通エフェクトに同じフィールドがあることと、各サービスが同じ機能を提供することは別です。たとえば現行のGemini・Seedreamの共通`ImageEdit`ハンドラは`mask`をSDK呼び出しへ渡していません。この記事では、確認できた生成・画像入力・テキストによる編集の接続を対象にしています。

ハンドラの取り付け順や、担当しない依頼を外側へ渡す仕組みは、[ハンドラの合成](doeff-handlers.md)で説明します。

## 処理の流れ

![画像を生成し、その結果を編集へ渡す](/images/zenn-use-cases-v0/generated/image-flow.png)

生成ハンドラがImageResultを返すと、生成を待っていた処理が再開します。そのimagesを編集依頼へ渡し、編集後のImageResultを後続へ返します。

## 実装・検証の範囲

このページの例は、固定ハンドラと付属のテスト用ハンドラを使ってオフライン実行しました。実画像生成APIの品質・認証・本番通信は検証していません。記事の図版制作とは別の検証です。

- [生成エフェクト](https://github.com/proboscis/doeff/blob/d4705914e39740aee98a9f57a4535c463d9479cc/packages/doeff-image/src/doeff_image/effects/generate.py)
- [編集エフェクト](https://github.com/proboscis/doeff/blob/d4705914e39740aee98a9f57a4535c463d9479cc/packages/doeff-image/src/doeff_image/effects/edit.py)
- [共通の結果型](https://github.com/proboscis/doeff/blob/d4705914e39740aee98a9f57a4535c463d9479cc/packages/doeff-image/src/doeff_image/types.py)
- [Geminiの本番ハンドラ](https://github.com/proboscis/doeff/blob/d4705914e39740aee98a9f57a4535c463d9479cc/packages/doeff-gemini/src/doeff_gemini/handlers/production.py)
- [Seedreamの本番ハンドラ](https://github.com/proboscis/doeff/blob/d4705914e39740aee98a9f57a4535c463d9479cc/packages/doeff-seedream/src/doeff_seedream/handlers/production.py)

[メイン記事へ戻る](doeff-main.md)
