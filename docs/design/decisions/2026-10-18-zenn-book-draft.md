# 連載10本を、1冊の無料のZennの本にまとめる下書き(候補14)を足す

- 決定日時：2026-10-18（JST）
- 決定担当：連載の始め方の答えを待つ間に、11本目からと本の下書きを用意する仕事のうち、候補14を受け持ったAIの会話（Opus 5.5）。
- 根拠：候補の報告（2026-10-17）の候補14と、手本の連載の調べ（2026-10-17）。手本の連載では、本の形で出した物が続きの本まで読まれた（手本の本1,233・続きの本502）。
- 採用：本の正本は、Zennの本と同じ形で`publications/zenn-use-cases-v0/books/doeff-effects-and-handlers/`に置く。`config.yaml`（章立て）、章の雛形10個（頭と終わりのつなぎ）、`rewrites.json`（連載の中でだけ通じる文の直し）、`cover.svg`と`cover.png`（表紙）の組。
- 採用：章の本文は、書き出すたびに原稿から作る（`export_zenn_book.py`）。本のために原稿を写して直すことはしない。原稿の誤りを直せば、本も次の書き出しで直る。
- 採用：連載の中でだけ通じる文は、決まった規則と`rewrites.json`の組で直す。規則は3つで、参考の節の「前の回」「次の回」の行を外す・「この記事」を「この章」にする・本の章へのリンクを「第N章「章の題」」にする。原稿の文が変わって直す前の文が無くなると、書き出しは何も書かずに止まり、`verify_publication.py`も落ちる。直した後に連載の言葉（「N本目」「連載」「前の回:」「メイン記事」「この記事」）が残っても止まる。
- 採用：無料（`price: 0`）・非公開（`published: false`）。`export_zenn_book.py`は、どちらかが違うと止まる。記事のbranch `zenn-content`には置かない。本を出すかは、連載の始め方を決めた後に決める。
- 採用：本のslugは`doeff-effects-and-handlers`、題は「doeffで、処理を書き換えずにテストと本番の動かし方を替える」（31字）。章のslugは、記事のZennのslugから`doeff-`を外した名にした。
- 採用：章の順は、連載の順（案M）ではなく、差し替える物が1つずつ増える順にした。案Mは、週1本の連載で技術の回と開発の記録の回を交互に出すための順である。本は前から順に読まれる。

| 章 | 原稿 | 連載の週 | この順に置いた訳 |
| --- | --- | --- | --- |
| 1 | doeff-main.md | 1 | `Ask`と最小のハンドラで、書き方を知る |
| 2 | doeff-di.md | 4 | 同じ`Ask`で、値の代わりに実装を受け取る |
| 3 | doeff-time.md | 3 | 外へ出す物が、値から「待つ」操作になる |
| 4 | doeff-replay.md | 6 | 「通信する」を外へ出す。原稿が3本目と4本目を「扱いました」と書く |
| 5 | doeff-agents.md | 2 | もっと遅く、答えが毎回変わる相手を差し替える |
| 6 | doeff-backtest.md | 7 | 時刻・値段・注文を一度に差し替える、2〜5章の合わせ技 |
| 7 | doeff-coroutines.md | 9 | ここまでの`yield`の書き方を、`async`・`await`と比べて振り返る |
| 8 | doeff-fleet.md | 5 | AIと開発した記録の全体。原稿が「hookの話は後で書く」と書く |
| 9 | doeff-hooks.md | 8 | 8章の仕組みの1つの部品。原稿が「全体の話は5本目」と書く |
| 10 | doeff-adr.md | 10 | 決まりを見せる(9章)から、決まりを走らせるへ。原稿が連載の締めを書く |

- 採用：本だけの章（はじめに・おわりに）は置かない。Zennの本の画面は章に番号を振るので、本文の「第N章」と画面の番号を揃えるため。本の案内は第1章の頭のつなぎ、締めは第10章の終わりのつなぎに書いた。
- 採用：つなぎの中で前後の章を指す時も、原稿と同じく`[章](doeff-<slug>.md)`のリンクで書き、書き出しが「第N章」に直す。章の順を変えても番号は合う。ただし、つなぎの文の中身は順に依るので、順を変えたらつなぎも書き直す。
- 採用：リンクを「第N章「章の題」」に開くと60字を越える文が2つ出た（時間の回とAIの会話を50並べる回）。この2つは`rewrites.json`で、リンクの文字を「章」にして「第N章」だけにした。10章とも、60字を越える文は0個。
- 採用：表紙は手で書いたSVGを、図と同じ`visuals/render_svg.mjs`で幅1000・高さ1400のPNGに撮った（111,363バイト。Zennの上限は1MB・比は1:1.4）。
- 採用：`zenn-check/check.mjs`と`screenshot.mjs`は、書き出し先に`books/`があれば本も読む。本は`validateBook`、章は`validateBookChapter`で検査し、`config.yaml`の章の並びとプレビューに出る章の並びを比べる。記事の検査は前と同じ。
- 採用：`export_zenn.py`から、topicsの規則・本文の画像とリンクの確かめ・画像の写しを関数に分け、本の書き出しと共に使う。記事の書き出しは、変更の前の版と1字も変わらないことを確かめた。
- 採用：`verify_publication.py`は、2026-10-18に足された`packages/doeff-claude-judge`が網羅表に無いため、本の確かめより前で落ちていた。網羅表に1行を足し、数を39ディレクトリ・9パッケージに直した。READMEの冒頭とpyproject.tomlを読んだだけの静的な棚卸しで、記事なしとした。
- 確かめ：2026-10-18に、次の命令で崩れ0個を確かめた（zenn-cli 0.5.6・chromium 151）。記事10本と本1冊を同じ出力先に書き出して読んだ。

```bash
uv run --no-sync python publications/zenn-use-cases-v0/export_zenn.py <出力先>  # 記事10本を書き出す。
uv run --no-sync python publications/zenn-use-cases-v0/export_zenn_book.py <出力先>  # 本1冊・10章を書き出す。
node publications/zenn-use-cases-v0/zenn-check/check.mjs <出力先> --online  # 記事10本・本1冊(章10)・崩れ0個。
agora-browser node publications/zenn-use-cases-v0/zenn-check/screenshot.mjs <出力先> <写しの置き場>  # 記事10本・本の画面11・崩れ0個。
uv run --no-sync python publications/zenn-use-cases-v0/verify_publication.py  # 本1冊(10章)を含めてOK。
```

- 確かめ：検査が崩れを拾うことも、壊した写しで確かめた。表紙を消す・`price`を100にする・並びに無い章を足す・本文に閉じない太字を入れると、4つとも崩れとして数えて終了コード1になった。`rewrites.json`の直す前の文を1字変えると、書き出しは何も書かずに止まった。
- 戻す手順：`books/doeff-effects-and-handlers/`・`export_zenn_book.py`・この決定記録を消し、`export_zenn.py`・`verify_publication.py`・`zenn-check/check.mjs`・`zenn-check/screenshot.mjs`・公開計画とREADMEの本の行を、この変更のcommitで戻す。網羅表の`doeff-claude-judge`の行は、本と別に残してよい。原稿と製品コードには触れていない。
