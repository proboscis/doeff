# imagegenによる全23記事・47枚の画像

2026-09-16、ユーザー指定のimagegenスキルの組み込みツールで、概念図23枚と処理図23枚を制作・差し替え、メイン冒頭のキービジュアル1枚を追加した。編集も同じツールを使用した。独立の画像生成APIやCLIは使っていない。

## 現在の制作基準

不透明な白背景、平面的な淡色の枠、紺文字、青緑の矢印。写実的な物体、3D、金属・ガラスの装置を使わない。短いコードと日本語コメントを置き、入力・処理・返る値を図へ対応させる。全文は記事のコード例で読める。

全47枚を個別に表示し、コード、括弧・辞書の記号、値、依頼と結果の矢印を確認した。誤った矢印や文字はimagegenで局所編集した。採用PNGはすべて不透明で、各3MB未満。

## 正本と採用記録

- 採用PNG：リポジトリ直下の `images/zenn-use-cases-v0/generated/`。
- [manifest.json](manifest.json)：採用パス、生成・編集プロンプト、寸法、容量、SHA-256、目視確認記録。
- `*-flat-code*.txt`：今回の生成・編集プロンプト。メインの概念図・処理図は`-v3`、冒頭画像は`main-hero-flat-code-v1.txt`を採用。
- `../reviews/*-visuals.json`：実装精査後のコードと概念・矢印・説明文の仕様。
- `../visuals/captions.json`：記事に掲載するタイトルと説明文。

旧PNGは`images/zenn-use-cases-v0/archive/`に保持。旧DOT・SVG・PNGと旧プロンプトも比較用であり、現在の掲載画像の生成元ではない。

## 更新手順

対象PNGを表示してからimagegenで編集する。採用画像をリポジトリへ保存し、実際に目視確認を終えた記事slugだけを`reviews/sync_review_metadata.py`へ渡す。台帳更新後に`verify_publication.py`とプレビューで記事との整合を確認する。生成は確率的なので、同じプロンプトから同じ画素を得る保証はない。

## メインの最小ハンドラを図へ反映（2026-09-16追記）

main-conceptとmain-flowをv3へ更新した。組み込みimagegenで生成・編集し、@handler・@do・handle_ask_effect、Resumeによる再開、with_handlersによる設置を本文と揃えた。旧図はarchiveへ保持。採用プロンプトはmain-*-flat-code-v3.txt、編集はmain-*-flat-code-v3-edit1.txt。実行結果はrunで得られることも図へ明記した。

## メイン冒頭のキービジュアル

[main-hero.png](/images/zenn-use-cases-v0/generated/main-hero.png)を追加した。[生成プロンプト](main-hero-flat-code-v1.txt)を組み込みimagegenへ渡して制作し、コードと往復矢印を目視確認した。タイトル直下に配置し、定義と最小コードへ続く。画像47枚の寸法・容量・採用ハッシュはmanifest.jsonを参照。
