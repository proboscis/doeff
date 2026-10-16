# 週2のagentsの記事を、記事の書き方の決まりへ揃える

- 決定日時：2026-10-16（JST）
- 決定担当：記事を月4本公開する取り組みで、週2の下書きを受け持ったAIの会話（Opus 5.5）。
- 根拠：記事の取り組みが決めた書き方の決まり9項目と、週2の5行の構成（悩み → 短いコード → 全体の図 → 本文 → まとめ → 参考）。
- 置き場：シリーズ全体の決定は[2026-09-15-zenn-use-cases.md](2026-09-15-zenn-use-cases.md)にある。週ごとの記事が同じ文書の末尾へ同時に書き足すと、取り込みの時に衝突するため、この回の記録は別の文書にした。

## 決定と理由

- コードの枠を8つから6つにし、ConductorとAgenticの例と、`doeff-agentic`のCLIの例を外す。
- 冒頭の短いコードの後に、自作の最小ハンドラ`fixed_agent`（`@handler`・`@do`・`Resume`・`Pass`）を置き、各回の仕上げ基準「ハンドラの中身を見せる」を満たす。
- テストの枠はシナリオの正常系だけを載せ、時間切れ・結果なし・schema違反は完全な例`examples/agents_workflow.py`で確かめる。本番の組み立てとMCPの道具は1つの枠にまとめる。
- 実装へのリンクを、リポジトリに無いcommit d4705914からmainのcommit 914ea5629へ付け替える。PyPIのdoeff-agents 0.1.1（2026-05-13）に記事のAPIが無いことと、GitHubから試す手順を書く。
- 社内の言い回し（機体・繋ぎ口・答え手・名札・筋書きなど）を、一般の技術の言葉（環境・接続先・ハンドラ・ハンドル・シナリオ）へ替える。
- 図2枚を、週3と同じ作り方（手で書いたSVG → `publications/zenn-use-cases-v0/visuals/render_svg.mjs`）で作り直す。作り方は`publications/zenn-use-cases-v0/imagegen/agents-svg-source.txt`に書く。
- 理由：エージェントを作る人が、1本で悩みから解き方まで辿れるようにするため。前の回を読まなくても分かるよう、エフェクト・ハンドラ・`@do`・`yield`を記事の中で説明した。

## 戻す手順

本記事・`examples/agents_workflow.py`・`reviews/agents-check.py`・図2枚とSVG・台帳（captions.json・manifest.json・agents-svg-source.txt）・`verify_examples.py`の1行・この文書を、この変更のcommitで戻す。他の記事と製品コードには触れていない。
