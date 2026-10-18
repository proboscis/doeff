# 11本目からの候補2「Claude Codeに仕事を配らせるのをやめ、関数で配る」の下書きを足す

- 決定日時：2026-10-18（JST）
- 決定担当：記事の取り組みの、11本目からの見込みの強い下書きを揃える区切りで、候補2の下書きを受け持ったAIの会話（Opus 5.5）。
- 根拠：ユーザーが連載の概要（案M）で、11本目からの順は10本への読者の反応を見て決めるとした。この仕事は下書きを先に用意するだけで、何番目に出すかは決めない。
- 採用：原稿は`doeff-dispatch.md`。公開順の表には入れず、公開計画の「候補」の節（候補1が置いた節）に1行を書く。Zennのslugの案は`doeff-dispatch-by-function`で、書き出しの確かめもこの名で行った。
- 採用：題は「Claude Codeに仕事を配らせるのをやめ、関数で配る」（29字）。候補の報告の題の案「AI の dispatcher をやめ、仕事は機械の scheduler で dispatch する」を、題と冒頭の決定記録（2026-10-17）の形に揃えた。否定の形・区切り記号の無い1つの主張・頭に「Claude Code」・番号なし。
- 採用：中心の話を「配る順を純粋な関数にし、記録した1日の負荷で比べてから選ぶ。AIの会話は見張りに回す」にする。5本目は配る判断をProgramにした形を見せたので、この回はAIの配り役で起きた事・配り方の比べ・本番の24時間の実測に絞る。
- 採用：掲載コードは、本物のschedulerを縮めたPythonで、PyPIのdoeffだけで走る。本物はHyで書いてある事を本文に書く。本物の比べと本番の数は、非公開の記録から引いた事と、読者が確かめられない事を本文に書く。
- 採用：非公開のリポジトリからは、流れ・数字・考え方だけを書く。社内の機体の名・会話やcardのid・利用者の発言・口座の数は載せない。
- 採用：比べの図は、配った枚数と急ぎの最長の待ちの2つの指標を1つの軸に混ぜず、上下2段に分ける。
- 採用：取引の研究には触れない。記事は公開せず、公開の可否も問わない。
- 戻す手順：本記事・`examples/card_dispatch.py`・図3枚とSVG・`visuals/dispatch_compare_svg.py`・台帳（`visuals/captions/dispatch.json`・`imagegen/manifest/dispatch.json`・`imagegen/records/dispatch.md`・`imagegen/dispatch-svg-source.txt`・`reviews/dispatch-visuals.json`・`reviews/dispatch-compare.csv`）・`reviews/dispatch.md`と`dispatch-check.py`、メインとREADMEの表の1行、公開計画の「候補」の節の1行、`reviews/main-check.py`の1語、`reviews/README.md`の1行を、この変更のcommitで戻す。製品コードには触れていない。
