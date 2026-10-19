# 連載を2つに分け、題を直した後の照らしの記録(2026-10-20)

決定は[決定記録](../../../docs/design/decisions/2026-10-20-zenn-series-split.md)にある。この記録は、直した後の16本と本1冊を照らした結果である。

## 直した物

| 物 | 直した事 |
| --- | --- |
| 公開計画 | 公開順を、doeffの連載10本の表とAIエージェントの連載6本の表に分けた。候補6本を表へ移し、候補の節を「表の後の回」と「数え直す回」にした |
| 題 | 2・3・4・6・7・10本目とhook・待ちの1行の回の8本を、「〜のをやめる」「〜のをやめた」の形にした。原稿のfront matter・記事間のリンクの文・本の直しの表・例のコメントも同じ題にした |
| doeffの連載の1本目 | 10本の一覧を新しい順と題にした。表「AIと開発する時の悩み」を外し、AIエージェントの連載の1本目へのリンク1行にした。commitの68%の行は「処理の把握・設計・検査の悩み」の表の先頭へ移した |
| AIエージェントの連載の1本目 | その連載の6本の順の一覧を置いた。冒頭の案内を「doeffが初めての方は、doeffの連載の1本目から」にした |
| 各回の参考 | 前の回・次の回のリンクを、連載の中でだけつないだ |
| 道具 | `render_release.py`の表の行が属する連載を持つ。`verify_publication.py`が前後のリンクを連載ごとに照らし、連載の端に前後のリンクが無い事と、別の連載の回がその連載の1本目から辿れる事を照らす。`reviews/main-check.py`はdoeffの連載の表だけと比べる |
| 本 | 8章と9章を、commitの68%と型の穴の章に替えた |

## 書き方の決まりの数

`render_release.py`で測った(コードの枠・画像・リンク先・記号・空白を除いた字数)。16本とも、60字を越える文は0個、です・ます以外で終わる文は0個。

| 連載 | 回 | 原稿 | 字数 | 枠 | 図 |
| --- | --- | --- | --- | --- | --- |
| doeff | 1 | doeff-main.md | 5,181 | 3 | 3 |
| doeff | 2 | doeff-agents.md | 3,664 | 6 | 2 |
| doeff | 3 | doeff-time.md | 3,310 | 6 | 2 |
| doeff | 4 | doeff-di.md | 3,387 | 5 | 2 |
| doeff | 5 | doeff-commits.md | 5,364 | 4 | 3 |
| doeff | 6 | doeff-replay.md | 4,413 | 6 | 2 |
| doeff | 7 | doeff-backtest.md | 3,582 | 6 | 2 |
| doeff | 8 | doeff-types.md | 4,295 | 6 | 2 |
| doeff | 9 | doeff-coroutines.md | 4,438 | 5 | 2 |
| doeff | 10 | doeff-adr.md | 5,221 | 6 | 2 |
| AIエージェント | 1 | doeff-fleet.md | 5,667 | 6 | 3 |
| AIエージェント | 2 | doeff-dispatch.md | 5,954 | 6 | 3 |
| AIエージェント | 3 | doeff-hooks.md | 4,698 | 6 | 2 |
| AIエージェント | 4 | doeff-waitline.md | 4,782 | 6 | 3 |
| AIエージェント | 5 | doeff-reports.md | 5,445 | 5 | 4 |
| AIエージェント | 6 | doeff-mergequeue.md | 4,993 | 4 | 3 |

## 確かめ

| 確かめ | 結果 |
| --- | --- |
| `verify_pypi.py`(16本・PyPIのdoeff 0.7.0・Python 3.12・回ごとの新しい環境) | 16本ともOK |
| `verify_publication.py` | OK。33記事・公開順の16回の前後のリンク・本1冊(10章) |
| 連載の端の回に前の回を置いた写し | `verify_publication.py`が「連載の端の回に前の回がある」で落ちた |
| `reviews/main-check.py` | OK。doeffの連載の表と同じ10本の一覧 |
| `verify_comments.py`・`ruff check`(直した道具) | OK |
| `export_zenn.py`と`export_zenn_book.py` | 記事16本(`published: true`は0本)・本1冊10章を書き出した |
| `check.mjs --online`(zenn-cli 0.5.6) | 記事16本で崩れ0個、本1冊10章で崩れ0個(記事と本の組を分けて走らせた) |
| `screenshot.mjs`(chromium 151) | 記事16本・本1冊の27画面で崩れ0個 |

注: `uv run --project <リポジトリ>`の中から`verify_examples.py`を走らせると、`reviews/types-check.py`の「0.6.0で4 errors」が0 errorsになって落ちた。同じ命令を外の環境の無い一時のdirで走らせると4 errorsだったので、外の環境が中の`uv run --with`に混ざった物とみて、読者と同じ形の`verify_pypi.py`で確かめた。

記事は公開していない。始める日・曜日と時刻・公開の操作・閲覧の知らせ方・本の時期・2つの連載の出す順は問うていない。
