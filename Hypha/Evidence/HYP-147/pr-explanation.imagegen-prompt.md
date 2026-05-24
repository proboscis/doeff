Tool: imagegen

Prompt:

Use case: infographic-diagram
Asset type: GitHub PR 説明図として使う横長 PNG
Primary request: HYP-147 の実装内容を説明する、霞ヶ関風の情報密度が高い業務ブリーフ図を作成してください。全ての見出し、説明、ラベルは日本語にしてください。英語の文章は禁止です。技術識別子だけはそのまま使えます。

Canvas: 16:9 横長、白背景、細い罫線、淡いグレーの区画、濃紺と深緑をアクセントにした官公庁資料風。文字は読みやすい太めの日本語ゴシック。小さな pictogram と矢印を使い、表形式と流れ図を組み合わせる。

Visible title: 「HYP-147 生成物を追跡対象から外す」
Subtitle: 「標準 lint と doeff-flow example が git 差分を汚さない状態へ」

Main layout:
1. 左カラム「変更前の問題」
   - 「make lint-doeff が .doeff-lint.jsonl を更新」
   - 「example 実行で SQLite DB と trace.jsonl が残る」
   - 「None / Untitled など用途不明の出力も追跡」
   Use warning icon, dirty git diff icon, file icons.
2. 中央カラム「今回の変更」
   - 「lint-doeff は --no-log を使用」
   - 「.gitignore に log / trace / DB / 一時出力を追加」
   - 「doeff-flow の DB は .doeff-flow/durable_workflow.db へ」
   - 「既存の生成物ファイルを削除」
   Use wrench icon, folder icon, database icon, arrow flow.
3. 右カラム「再発防止」
   - 「make check-repo-hygiene を追加」
   - 「git ls-files で DB / SQLite / JSONL / None / Untitled を検査」
   - 「fixture 化する場合は明示的な配置と許可リストへ」
   Use shield icon, checklist icon.
4. 下段「レビュアー確認点」
   - 「意図した fixture を削除していないか」
   - 「標準 lint 経路が追跡ファイルを変更しないか」
   - 「doeff-flow README と example の説明が一致しているか」

Avoid: pseudo-code, invented APIs, English headings, English callouts, raw command dumps, screenshots, decorative blobs, gradients, handwritten style.
