# 11本目からの候補6「ライブラリのcommitの68%を、Claude Codeと書いた1年」の下書きを足す

- 決定日時：2026-10-18（JST）
- 置き場：記事ごとの決定を記事ごとの文書に置く決まり（2026-10-17）に従う。公開順の週がまだ無いので、名は`zenn-week<N>`でなく`zenn-candidate`にした。
- 決定担当：11本目からの見込みの強い4本の下書きを先に揃える仕事のうち、候補6を受け持ったAIの会話（Opus 5.5）。
- 根拠：11本目からの順は、10本への読者の反応を見て決める（連載の概要の合意・案M）。候補の報告（2026-10-17）で、候補6は見込みが強く、公開の障りが弱い唯一の候補だった。草稿は無く、新しく書いた。
- 採用：原稿は`doeff-commits.md`。公開順の表には足さず、公開計画の「候補」の節に1行を足す。案のZennのslugは`doeff-claude-code-commits`。何番目に出すかは決めない。
- 採用：題は候補の報告の題「Python の library の commit の 68% を Claude Code と書いた 1 年」から「Python」を外し、「ライブラリのcommitの68%を、Claude Codeと書いた1年」（35字）にした。傾向の調べで題の語「Python」は当たり4.3%と弱く、「Claude Code」は7.7%。区切り記号は使わず、番号も付けない。「Python」は冒頭の1文目で出す。
- 採用：5本目（AIの会話を最大50並べる）と材料が重なるので、ライブラリの品質の側（文書のコード・型・設計の約束・PyPIからの入れ方）に絞る。会話を並べる仕組みは5本目へ案内する。
- 採用：数は2026-10-18に、doeffのmainのcommit `c0dfc3f24`までで数え直した（5,566個のうち3,811個・68.5%）。候補の報告の数（`2b32efb9e`で5,513個のうち3,770個）は使わない。月は、author dateをUTC+9に直して分けた（作業の機体に時刻帯の表が無く、`TZ=Asia/Tokyo`はUTCのまま分ける）。
- 採用：Codexで書いたcommitは共著の行を残さないので数えられないと本文に書き、68%を「AIを通した割合の下限」とした。ほかの道具の名の共著の行13個は数に入れない。
- 採用：掲載コードは、共著の行を数える`claude_share`をdoeffのProgramにし、テスト用（決めた3つの本文）と本番用（`git log`）のハンドラで動かす形にした。本番用はPyPIのdoeff 0.7.0の核だけで動くよう、`RunProcess`（Hyで書かれ`doeff[hy]`が要る）を使わず、ハンドラの中で`subprocess.run`を呼ぶ。
- 採用：型の検査の枠は`tests/type_probes/p17_forgot_yield.py`を縮めた形にし、pyrightとmypyの厳しい設定で印の行だけが誤りになる事を確かめのスクリプトで照らす。
- 採用：概念図では、4つの網をmainの前の門として描かない。mainへの門は衝突の印だけを見る（5本目）。
- 採用：取引の研究には触れない。`docs/design-checks`の記録の番号（依頼のid）は本文に載せない。
- 戻す手順：本記事・`examples/commit_share.py`・図3枚とSVG・`visuals/commits_months_svg.py`・台帳（`visuals/captions/commits.json`・`imagegen/manifest/commits.json`・`imagegen/records/commits.md`・`imagegen/commits-svg-source.txt`・`reviews/commits-visuals.json`・`reviews/commits-months.csv`）・`reviews/commits.md`と`commits-check.py`、メインとREADMEの表の1行、`reviews/main-check.py`の記事の名の1行、公開計画の「候補」の節の行を、この変更のcommitで戻す。製品コードには触れていない。
