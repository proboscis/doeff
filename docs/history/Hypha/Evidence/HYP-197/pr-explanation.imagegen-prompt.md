Tool: imagegen

Prompt:
Use case: infographic-diagram
Asset type: GitHub PR説明図, 16:9 PNG
Primary request: HYP-197 の実装内容を説明する、霞ヶ関スタイルの情報密度が高い日本語インフォグラフィックを作成する。
Visual style: 白地に濃紺と深緑の罫線、行政資料のような整然とした区画、細い矢印、チェック印、コードファイルの小さなピクトグラム、盾の検査アイコン。装飾は控えめ、文字は読みやすい太めのゴシック体。
Layout: 上部に大見出し「HYP-197: 旧 handler 署名を defhandler へ移行」。左から右へ 3 カラム。
Left column heading: 「問題」。内容: 「production Hy source に旧 defk [effect k] が残存」「doeff-hy macro guard が import / pytest collection を停止」。
Middle column heading: 「変更」。内容: 「doeff-docker: Dockerfile / Docker build / push を defhandler 化」「doeff-ml-nexus: file / docker / rsync / resolve を defhandler 化」「Dockerfile 生成 Program に契約を追加」。中央に大きな矢印「defk 署名禁止 → defhandler 節」。
Right column heading: 「確認」。内容: 「handler module import 通過」「Dockerfile 生成 4 tests 通過」「package tests 27 tests 通過」「Semgrep guard: no-legacy-defk-handler-signatures」。
Bottom band heading: 「レビュアーが見る点」。内容: 「未対応 effect は defhandler の既定転送」「Tell / Ask / slog の effect delegation を保持」「旧署名の再混入は architecture test と Semgrep が検出」。
Constraints: すべての見出し、ラベル、説明文は日本語。技術識別子は必要なものだけそのまま使う。Program.resolve や lazy_ask や resolved["tdnet"] のような、このPRに存在しない疑似APIや処理は一切描かない。Markdown、HTML、SVG、Mermaid風ではなく、生成されたビットマップの完成図にする。
