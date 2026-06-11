Tool: imagegen

Prompt:
横長の霞ヶ関風インフォグラフィック。HYP-203 の PR 説明図。白背景、濃紺と深緑、細い罫線、番号付き区画、矢印、文書・歯車・警告・盾・チェックのアイコン。日付欄、作成者欄、PR番号欄は描かない。

文字は日本語中心。英字は識別子だけ: HYP-203, WithHandler, WithHandlerType, defhandler, handler(program), README.md, docs, examples, HYP-168, HYP-158, HYP-197, pytest。

タイトル: HYP-203 PR説明図: handler合成案内を現行契約へ統一
中央矢印: 互換shim案内 → 直接合成

区画1「課題」
- WithHandler は互換shim
- README.md と docs が推奨手順として案内
- 新規利用者が警告の出る例を書く

区画2「採用した契約」
- handler factory は Program -> Program installer
- 新規例は handler(program) を直接呼ぶ
- 低レベル構築は WithHandlerType
- Hy は defhandler を直接呼ぶ

区画3「変更範囲」
- README.md と docs を更新
- examples の合成例を更新
- core handler factory を直接合成対応
- docs guard を追加

区画4「確認ポイント」
- WithHandler 呼び出し例は現行docsとexamplesから除去
- 標準例は警告なし
- HYP-168 は default_handlers と async_run の別課題
- HYP-158 と HYP-197 との境界維持

下部の検証帯:
- pytest 全体通過
- docs guard 通過
- 型検査は既存の import エラーで停止

整列した行政説明資料風。文字は大きめで読みやすく、架空APIや日付を描かない。
