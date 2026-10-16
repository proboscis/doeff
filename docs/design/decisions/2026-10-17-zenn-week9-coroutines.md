# 連載の9本目「async/awaitがあるのに、なぜyieldか」を、記事の書き方の決まりへ揃える

- 決定日時：2026-10-17（JST）
- 決定担当：記事を月4本公開する取り組みで、連載の9本目の下書きを受け持ったAIの会話（Opus 5.5）。
- 根拠：記事の取り組みが決めた書き方の決まり9項目と、連載の並べ方（案M・2026-10-17に決まった10本の順）の9本目「async/awaitがあるのに、なぜyieldか」。
- 置き場：シリーズ全体の決定は[2026-09-15-zenn-use-cases.md](2026-09-15-zenn-use-cases.md)にある。週ごとの記事が同じ文書の末尾へ同時に書き足すと取り込みで衝突するため、この回の記録は別の文書にした（週2と同じ）。
- 触らない物：公開順の表（`publications/zenn-use-cases-v0/publication-plan.md`）と`render_release.py`は、連載10本の表へ書き換える別の仕事が持つ。この回では変えない。

## 決定と理由

- 読者を、async/awaitを知るPythonの開発者に絞る。悩みは1つ（awaitの先は呼んだ実装で決まり、替えるにはpatchか引数が要る）にする。
- 答えの芯を「awaitもyieldも止まって続きを待つ。違いは、止まった所へ誰が値を返すか」にし、似ている所（generatorとsend・PEP 492の歴史）から書く。
- コードの枠を8つから5つにする。比べる2つの形を1枠目に並べ、ハンドラの差し替え・子Program・同じProgramの再利用を2枠目にまとめる。
- 自作のハンドラを`@handler`・`@do`付きにし、`with_handlers`で付ける。週1・週2の記事と書き方を揃えるため。
- 待ちを数える所は、標準の`Listen`と`listen_handler`で書く。可変のlistへ足していく自作のハンドラを、読者の手本にしないため。
- 「doeff側のキャンセルが橋渡し先へ伝播しない」の記述を消す。今のmainでは取り消しが`CancelledError`として届く（#498）。取り消しは頼むだけで終わりを待たないこと、共有のループを止めると全ての`Await`が止まることを、制約として書く。
- 「yieldを選ばなくてよい所」の節を置き、関数を引数で渡すだけで足りる場面と、yieldの代償（呼ぶ側も`@do`になる・ハンドラの約束を守る責任）を書く。
- 実装へのリンクを、リポジトリに無いcommit d4705914から、mainのcommit 6ab1e37d6へ付け替える。PyPIの最新のdoeff 0.4.1（2026-05-13）に`handler`と`with_handlers`が無いことと、GitHubから試す手順を書く。
- 図2枚を、週2・週3と同じ作り方（手で書いたSVG → `publications/zenn-use-cases-v0/visuals/render_svg.mjs`）で作り直す。作り方は`publications/zenn-use-cases-v0/imagegen/coroutines-svg-source.txt`に書く。
- 連載の前後の回（8本目と10本目）へは、この回ではリンクしない。8本目の原稿はまだ無く、10本目の原稿のファイル名は公開順の表を書き換える仕事が決めるため。

## 戻す手順

本記事・`examples/coroutine_comparison.py`・`reviews/coroutines-check.py`・`reviews/coroutines.md`の追記・`reviews/coroutines-visuals.json`・図2枚とSVG・台帳（captions.json・manifest.json・coroutines-svg-source.txt）・`feature-examples.md`の1行・`verify_examples.py`の1行・この文書を、この変更のcommitで戻す。他の記事と製品コードには触れていない。
