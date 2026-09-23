# Hy記事の再精査

- 担当: 記事専任エージェント `review_hy`
- 対象: `doeff-hy.md`、`examples/hy_composition.hy`
- 検証版: `d4705914e39740aee98a9f57a4535c463d9479cc`
- 使用スキル: doeff-patterns、doeff-hy-macros、doeff-deftest。記法・実行意味は現行実装を正本とした。

## 指摘と修正

1. **`!`の現在の展開方式を明示した。** スキル中には一時変数へ巻き上げる古い説明があるが、現在は書いた位置の`yield`へ展開する。条件分岐の未選択側にある`Ask`は実行されない例を追加した。価格と送料の順番も実行観測した。
2. **契約のタイミングを明示した。** `defk`の引数と結果の契約はProgramを実行するときに検査される。入力の文字列契約へ整数を渡したケース、結果の文字列契約を破ったケースで、それぞれ事前・事後条件違反を検出した。
3. **`defhandler`の呼び出し方を説明した。** `[]`付き定義は引数なしの工場を作るため、`(greeting-source)`でハンドラを作り、それにProgramを渡す。処理の合成は`do!`と`<-`を使う。
4. **pytest接続例の架空のモジュールを除去した。** 実在の`hy_composition`を読み込み、生成された`test_greeting`をPythonのテストモジュールへ公開する形にした。pytestが実際に1件を収集して、`doeff_interpreter`フィクスチャを渡すことを確認した。
5. **`for/do`の戻り値を正確に説明した。** `Collection`は項目の履歴を保持し、`When`による除外は`skipped`として残る。`Reduce`は有効な項目だけを畳み込む。入力`[1 -1 3]`は8、全件除外の`[-3 0]`は初期値0となる。
6. **展開に必要なインポートを説明した。** `From`と`When`はその位置で認識される構文であり、それ自体のインポートは不要。展開先の`Traverse`・`Skip`の別名付きインポートと`_doeff-do`は明記した。
7. **全コード行に目的・期待値の日本語コメントを付けた。** 掲載7ブロック、専用Hyファイル、図の抜粋に適用。Python比較例でも`@do`を使い、Hy側の子Programは`<-`で合成する。`run`は検証・pytestの実行境界に限定した。

## 実装根拠

- `packages/doeff-hy/src/doeff_hy/macros.hy:400`: 契約付き関数の組み立て、返り値の検査。
- `packages/doeff-hy/src/doeff_hy/macros.hy:498`: `defk`、必須の事前・事後契約。
- `packages/doeff-hy/src/doeff_hy/macros.hy:646`: `do!`のProgram化。
- `packages/doeff-hy/src/doeff_hy/macros.hy:711`: `<-`の束縛とyieldへの展開。
- `packages/doeff-hy/src/doeff_hy/macros.hy:833`: `From`・`When`のTraverse・Skipへの展開。
- `packages/doeff-hy/src/doeff_hy/macros.hy:1017`: `!`の位置を維持する展開。
- `packages/doeff-hy/src/doeff_hy/macros.hy:1290`: `deftest`から生成する関数と`doeff_interpreter`の受け渡し。
- `packages/doeff-hy/src/doeff_hy/handle.hy:499`: `defhandler`の引数あり／なし、Programを包む公開形。
- `packages/doeff-traverse/doeff_traverse/handlers.py:18`: 順次Traverse、Skipの履歴、有効項目だけのReduce。

## 検証

```bash
.venv/bin/python publications/zenn-use-cases-v0/reviews/hy-check.py # 全7ブロック・完全例・契約・評価順・pytest1件を検証する。
.venv/bin/ruff check publications/zenn-use-cases-v0/reviews/hy-check.py # 監査用Pythonの静的な問題がないと確認する。
```

- Python 2ブロック、Hy 5ブロックをそれぞれ独立した名前空間で実行した。
- `hy_composition.hy`のimport時のassertと、生成された`deftest`を実行した。
- 実際のAskハンドラで`["price", "shipping"]`の順番を観測し、合計1200を確認した。
- Askハンドラを置かず、未選択の分岐を含むProgramが0を返すと確認した。
- 入力契約2件と結果契約1件の違反を期待する例外として検出した。検査中に表示されるdoeffのAssertionErrorトレースは、この意図的な負例の出力。
- 掲載したpytest接続コードを派生ファイルとして保存し、その`::test_greeting`だけを実際にpytestへ渡した。結果は **1 passed in 0.08s**。
- 掲載7ブロックと専用Hyファイルの空行以外には、対応する言語のコメントがあることも確認した。
- 初回の独立pytest検査は設定を`/dev/null`に向けた状態で60秒タイムアウトした。通常のリポジトリ設定とテスト関数1件の指定に直して成功した。検査の無効化設定や実行範囲の拡大は最終スクリプトにない。

## 外部接続と変更範囲

外部API、実エージェント、ネットワーク通信は実行していない。runtime・packages・共通管理ファイルは変更していない。

## 画像の仕様

`hy-visuals.json`を正本として、PythonとHyが同じハンドラへ接続する概念図、`!`の位置で順次結果を受け取る処理図を指定した。白背景の平面図とコメント付きコードを使う。画像生成・採用は親担当が行う。
