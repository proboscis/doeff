# 連載7本目「バックテストの設計」を新しく書く

- 決定日時：2026-10-17（JST）
- 決定担当：記事を月4本公開する取り組みで、7本目の下書きを受け持ったAIの会話（Opus 5.5）。
- 根拠：連載10本の並べ方（案M・利用者が2026-10-17に決定）の7本目。利用者の条件「取引の研究は入れない。バックテストの設計と実装まで。実際に試した戦略・損益・相場を記録する仕組み・機械の構成には触れない」。記事の書き方の決まり9項目。
- 置き場：シリーズ全体の決定は[2026-09-15-zenn-use-cases.md](2026-09-15-zenn-use-cases.md)にある。週ごとの記事が同じ文書へ同時に書き足すと取り込みで衝突するため、この回の記録は別の文書にした（週2の[2026-10-16-zenn-week2-agents.md](2026-10-16-zenn-week2-agents.md)と同じ扱い）。

## 決定と理由

- 原稿の名を`doeff-backtest.md`、Zennのslugを`doeff-backtest-design`にする。名前の案を公開計画の担当（card ki-27af1817335f）へ知らせ、公開順の表もこの名で決まった。表の7本目の原稿の欄は、公開計画の約束「原稿が出来たら、題からその原稿へのリンクに変える」に従ってリンクにした。公開状態は下書きのまま変えていない。
- 使うパッケージは`doeff`・`doeff-core-effects`・`doeff-time`の3つにする。時刻と待ちは`GetTime`・`Delay`、値段と注文は記事の中で定義する依頼`GetPrice`・`PlaceOrder`、各回の控えは`Tell`で書き残す。
- 判断の処理`decide`は、時刻も値段も見ずに毎回同じ注文を返す置き物にする。値段はすべて架空の数（100.0・101.0・102.0・999.0）にする。理由：利用者の条件で、実際の判断の中身を書かないため。
- 各回の控えは、`list.append`でも`tuple`の組み立てでもなく、`Tell`と`writer`で集める。`tuple`の組み立ては回数の2乗で重くなり、43,200回で26.5秒かかった（2026-10-17に測った）。`doeff-traverse`の`sequential()`は1回分の失敗を隔離して先へ進むので、値段が無い時に止まらず、時刻もずれる。バックテストの手本には、失敗で止まる素直な`for`の形がよいと判断した。
- 見せ場は「未来の値段を読ませない守りを、値段のハンドラの1か所に置く」とする。バックテストの設計の要で、取引の研究に踏み込まずに書ける。
- 本番のハンドラは、取引所のクライアントの形（`Broker`の2つの操作）だけを示す。実際の取引所には繋がず、固定の値を返す偽物のクライアントで確かめる。
- 図2枚を、週3・週4と同じ作り方（手で書いたSVG → `publications/zenn-use-cases-v0/visuals/render_svg.mjs`）で作る。作り方は`publications/zenn-use-cases-v0/imagegen/backtest-svg-source.txt`に書く。

## まだ揃っていないこと

- `verify_publication.py`の記事の数を、5本目・8本目と同じく1つ上げて26にした。READMEの記事の数と、記事別レビューの一覧も合わせた。
- メインの記事の「時間・依存・状態・保存の悩み」の表に、7本目の行を1つ足した。5本目と同じ扱いで、`reviews/main-check.py`の一覧にも足した。7本目は下書きなので、公開する本文では題だけが出てリンクは付かない。
- `verify_publication.py`は、他の記事の図が揃っていないため、mainと同じ画像の検査で止まる。

## 戻す手順

本記事・`examples/backtest.py`・`reviews/backtest-check.py`・`reviews/backtest.md`・`reviews/backtest-visuals.json`・図2枚とSVG・台帳（captions.json・manifest.json・backtest-svg-source.txt）・`verify_examples.py`の2行・`verify_publication.py`の記事の数・メインの表の1行と`reviews/main-check.py`の一覧・READMEの1行と記事の数・記事別レビューの一覧の1行・公開計画の7本目の原稿の欄・この文書を、この変更のcommitで戻す。メインの表の1行の他は、他の記事と製品コードに触れていない。
