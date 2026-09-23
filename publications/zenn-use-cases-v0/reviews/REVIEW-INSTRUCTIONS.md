# 記事別レビュー担当への共通指示

担当slugに対応する `doeff-<slug>.md` と、そこで直接使う `examples/` の専用例だけを編集する。他記事から共有される例は親担当へ連絡する。共通README/TODO/manifest/coverage/検査スクリプトは親担当が編集する。

## 必須の修正基準

- doeff-patternsスキルと必要なruntime/Hyスキルを読み、現行実装に照合する。APIのimport/引数/戻り値、Programをyieldする場所、handler設置を確認。READMEだけでなく実装を読む。
- 合成する関数は @do を付け、yield helper(...) でつなぐ。async defへループ全体を逃がしてAwaitで包まない。Awaitは本物の非同期SDK primitive等に限る。比較教材のasync defと必要なSDKテストスタブは目的を明記して可。
- 全コード例の各実質行に日本語コメントを付ける。各行の目的と期待する値・動作を説明する。空行は不要、多行引数や閉括弧は前行の説明で意味が追えればよい。Python以外(Hy/Rust/shell)もその言語のコメントを使う。例ファイルも同様。単なる「定義する」「実行する」の反復では不足。
- 紹介する全機能にコードを残す。架空のAPIや誇大な互換性を取り除く。廃止済/未実装は明記。オフラインで意味のある確認。実LLM/実agent/API/Docker/SSH/有料処理や外部送信はしない。
- 製品runtimeやpackagesのコードは変更しない。commit/pushしない。

## 出力

1. 記事と専用コード例の修正、実行確認。
2. `reviews/<slug>.md`: 担当、確認対象、指摘/修正、実装根拠、検証コマンドと範囲、未実行の外部接続、各行コメント要件の確認を日本語で記録。
3. `reviews/<slug>-visuals.json`: `{ "concept": {"title":..., "code":..., "flow":..., "caption":...}, "flow": {...} }`。図はrootがimagegenで生成する。コードは2〜5行程度の正確な抜粋で各行短いコメント付き。flowはコード行の役割/値と図のノード・矢印を指定。白背景の平面的ダイアグラム、写真/3D不可。既存のalt/title/captionに齟齬があれば記事側も修正し、JSONを正本としてrootへ通知する。

メインからの導線は維持。新記事 doeff-vm.md / doeff-handlers.md / doeff-coroutines.md は必要に応じ関連リンク可。各記事published:false。

追加方針: client_factory等の下位クライアント注入を利用者へ要求する例を、handler差し替えの利点として扱わない。記録/再生はHTTP取得とMemo保存のハンドラを分ける方向でreplay担当が修正中。関連例も責務の分離を確認する。
