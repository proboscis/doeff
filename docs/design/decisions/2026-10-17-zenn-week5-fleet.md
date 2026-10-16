# 5週目に開発の記録「AIの会話を最大50並べて、1人で開発する」を足す

- 決定日時：2026-10-17（JST）
- 置き場：はじめ[シリーズ全体の決定記録](2026-09-15-zenn-use-cases.md)の末尾へ書き足した節を、記事ごとの決定を記事ごとの文書に置く決まり（2026-10-17）に合わせ、文を変えずにこの文書へ移した。
- 決定担当：連載の並べ方（案M）を受け、5週目の下書きを受け持ったAIの会話（Opus 5.5）。
- 根拠：ユーザーが連載10本の並べ方を案Mに決め、5本目を開発の記録の1本目にした。草稿は無く、新しく書いた。
- 採用：原稿は`doeff-fleet.md`、Zennのslugは`doeff-fifty-ai-conversations`。公開計画の表は別の仕事が書くので、この変更では触れない。
- 採用：中心の話を「配る判断をdoeffのProgramにし、本番と模擬でハンドラだけを替える」にする。掲載コードは本物の配り役を縮めたPythonで、本物はHyで書いてある事を本文に書く。
- 採用：非公開のリポジトリ（agora・agora-controllers）からは、作業の流れ・数字・考え方だけを書く。機体の名・会話の番号・個人の情報は載せない。数えた元を読者が確かめられない事も本文に書く。
- 採用：図は概念図・処理図に、35日のcommitの数の図を足して3枚にする。台帳の`extra_kinds`で3枚目も照らす。
- 採用：取引の研究には触れない。
- 戻す手順：本記事・`examples/fleet_dispatch.py`・図3枚とSVG・`visuals/fleet_commits_svg.py`・台帳（captions.json・manifest.json・fleet-visuals.json・fleet-svg-source.txt・fleet-commits.csv）・`reviews/fleet.md`と`fleet-check.py`、メインとREADMEの表の1行、照合の道具の記事の数を、この変更のcommitで戻す。製品コードには触れていない。
