# 連載の公開計画

合意日：2026-09-16に「7日間、毎日1本」をユーザーが承認した。2026-10-16に、ユーザーが公開先をZenn、頻度を週1本、1本目を「doeffとは」の入門に決め直した。2026-10-17に、ユーザーが連載10本の順を案Mに決めた。2026-10-20に、ユーザーが連載をdoeffの連載とAIエージェントで開発した記録の連載の2つに分け、題を「〜のをやめる」の形にすると決めた。同じ日に、ユーザーが2つの連載を交互に毎日1本、その日から出し、公開の操作はAIがすると決めた(連載の始め方の依頼書の答え)。

目的は、毎回一つの具体的な悩みからdoeffを試せる入口を作ること。doeffの連載の1本目のメイン記事は、困りごとから各回へ案内する目次として育てる。AIエージェントの連載の1本目は、その連載の入口にする。各回は、前の回を読んでいなくても理解できる形にする。

公開の操作はAIが行う。決めた日時に出す回は、記事のbranchでその回を`published: true`と`published_at`(公開の刻)の組にするcommitをpushし、Zennの予約公開で刻に出す(2026-10-20のユーザーの答え「AI が表の刻ごとに、その回を公開にして push する」)。刻を下の表の公開状態に書き、`push_zenn_branch.py`が予約を置く。刻ちょうどにpushする仕組みを置かず、Zennの予約に任せる(訳は[決定記録](../../docs/design/decisions/2026-10-21-zenn-reserved-publish.md))。始め方の答えの前は、AIは公開しない決まりだった(2026-10-16のユーザーの答え「勝手に公開はしないこと。執筆と計画まで。」)。1本ずつの公開の可否は、依頼書で問わない(2026-10-16のユーザーの差し戻し「連載の概要すら議論してないのに、週一の単一記事を決済書で出されても困る」)。

## 公開順

2026-10-20から、doeffの連載とAIエージェントの連載を交互に毎日1本公開する(それぞれ2日に1回)。doeffの連載の1本目から始める。毎日、読者の反応を確かめて戦略を見直す(2026-10-20のユーザーの答え「並列で2日に1回出してみるか」「今日から」「2つを交互に毎日出して、毎日反応を確認して戦略を再検討する」)。公開の刻は毎日8:00 JSTにした。答えに時刻が無かったので、戻せる決めとして置いた(個人の記事は時刻の差がほぼ無いという調べによる)。下の2つの表は連載ごとの公開順の合意で、公開状態の刻がZennへの予約になる。AIエージェントの連載が10月31日の6本目で終わった後は、doeffの連載の7〜10本目を毎日1本出し、11月4日に16本が出揃う。順や刻を変える時は、表の刻を直してmainに入れ、`push_zenn_branch.py`を走らせる。予約を変えられるのは刻の前だけで、出た回の刻は変えない。

2026-10-20に、10本と候補6本を回ごとに数え、doeffが主題の回とAIエージェントが主題の回に分けた。訳は[決定記録](../../docs/design/decisions/2026-10-20-zenn-series-split.md)にある。閲覧の目標(12月31日までに合計10,000)は、2つの連載の合計で測る。

### doeffの連載

案Mの層を保つ。入門の1本目、実践の技術の2〜4本目の後に、5本目と8本目でdoeffそのものの開発の記録を挟む。

| 週 | 原稿 | Zennのslug | 読者への入口 | 公開状態 |
| --- | --- | --- | --- | --- |
| 1 | [処理を書き換えずに、テストと本番で動かし方を替えるdoeffとは](doeff-main.md) | `doeff-introduction` | エフェクトと最小ハンドラを実行し、用途の全体像を見る | 公開済み https://zenn.dev/proboscis/articles/doeff-introduction |
| 2 | [Claude Codeを起動してテストするのをやめる](doeff-agents.md) | `doeff-ai-agents` | AIエージェントの仕事を、起動・待機・結果の契約で組み合わせる | 公開予定 2026-10-22 08:00 JST |
| 3 | [テストで20秒待つのをやめる](doeff-time.md) | `doeff-virtual-time` | 20秒待つ処理を、20秒待たずにテストする | 公開予定 2026-10-24 08:00 JST |
| 4 | [依存を引数で引き回すのをやめる](doeff-di.md) | `doeff-dependency-injection` | 必要な依存をその場で依頼し、外側から供給する | 公開予定 2026-10-26 08:00 JST |
| 5 | [ライブラリのcommitの68%を、Claude Codeと書いた1年](doeff-commits.md) | `doeff-claude-code-commits` | AIと書くライブラリの約束を、4つの網で毎日確かめる | 公開予定 2026-10-28 08:00 JST |
| 6 | [外部APIのテストで通信するのをやめる](doeff-replay.md) | `doeff-offline-api-tests` | 遅く落ちる外部APIのテストを、保存した応答で通信せずに回す | 公開予定 2026-10-30 08:00 JST |
| 7 | [バックテストと本番で処理を2つ書くのをやめる](doeff-backtest.md) | `doeff-backtest-design` | 時刻と相場の入力をハンドラで差し替え、過去のデータと本番で同じ処理を動かす | 公開予定 2026-11-01 08:00 JST |
| 8 | [公開APIの型の穴をテストで塞ぎ、黙って開かせない](doeff-types.md) | `doeff-public-api-types` | 型の分からない公開の名を表にし、増えても減ってもテストを赤にする | 公開予定 2026-11-02 08:00 JST |
| 9 | [async/awaitがあるのに、なぜyieldで書くのか](doeff-coroutines.md) | `doeff-yield-vs-async-await` | `async def`・`await`と`@do`・`yield`で、依頼の渡し先と再開の仕方を比べる | 公開予定 2026-11-03 08:00 JST |
| 10 | [ADRを書いて終わりにするのをやめる](doeff-adr.md) | `doeff-executable-adr` | 設計の決まりが崩れないよう、例と規則を書いた設計の記録をテストとして走らせる | 公開予定 2026-11-04 08:00 JST |

### AIエージェントの連載

AIエージェントで開発した記録の連載。1本目の会話を最大50並べた記録を入口にし、その中の部品を1本ずつ書く。2本目からの順は、候補の調べの「5本目が読まれたら候補2、8本目が読まれたら候補4」の続き方に沿う。

| 週 | 原稿 | Zennのslug | 読者への入口 | 公開状態 |
| --- | --- | --- | --- | --- |
| 1 | [1人でClaude Codeを最大50並べたら、詰まるのは人だった](doeff-fleet.md) | `doeff-fifty-ai-conversations` | AIの会話を最大50並べて、1人でdoeffと周りの仕組みを開発した記録を読む | 公開予定 2026-10-21 08:00 JST |
| 2 | [Claude Codeに仕事を配らせるのをやめ、関数で配る](doeff-dispatch.md) | `doeff-dispatch-by-function` | 配る順を純粋な関数にし、記録した1日の負荷で比べてから選ぶ | 公開予定 2026-10-23 08:00 JST |
| 3 | [CLAUDE.mdに決まり851本を書き足すのをやめた](doeff-hooks.md) | `doeff-ai-rule-hooks` | AIが決まりを読み落とす時に、要る場面でだけ規則を見せる | 公開予定 2026-10-25 08:00 JST |
| 4 | [止まったClaude Codeを1つずつ開くのをやめた](doeff-waitline.md) | `doeff-claude-code-wait-line` | ターンの最後に待つ物を1行書かせ、止まった会話のどれが自分の番かを読む | 公開予定 2026-10-27 08:00 JST |
| 5 | [Claude Codeに報告のページを丸ごと作らせるのをやめた](doeff-reports.md) | `doeff-claude-code-report-pages` | 中身と図と組み立てを分け、図つきの報告を先に開く | 公開予定 2026-10-29 08:00 JST |
| 6 | [マージキューでテストを走らせるのをやめた](doeff-mergequeue.md) | `doeff-merge-queue-without-tests` | 門は衝突だけを見て、全体のテストは別の周期で回す | 公開予定 2026-10-31 08:00 JST |

公開状態は「公開済み <ZennのURL>」「公開予定」「下書き」のどれかで書く。公開予定と公開済みの後ろには、公開の刻を「YYYY-MM-DD hh:mm JST」の形で書ける(例「公開予定 2026-10-21 08:00 JST」「公開済み https://zenn.dev/proboscis/articles/<slug> 2026-10-21 08:00 JST」)。刻を書いた公開予定は、Zennの予約公開になる。刻の無い公開予定は予約せず、Zennの下書きのまま置く。刻を過ぎた回は、`record_published.py`がZennで開けることを確かめてから公開済みに直す。公開済みの刻は、Zennの公開日時を変えないために残す。下書きは、まだ仕上げ基準を満たしていない原稿で、満たしたら公開予定に変える。`render_release.py`がこの2つの表を読み、公開する本文のリンクを作る。下書きの行と表に無い原稿は、候補として扱う。

5〜10本目は、各回の担当が下の仕上げ基準を1項目ずつ照らし、掲載したコードを走らせた。その結果を受けて、2026-10-17に公開予定へ変えた。候補6本も2026-10-18に仕上げ基準を照らして下書きし、2026-10-20に2つの表へ移して公開予定にした。公開予定はZennへの登録ではない。AIエージェントの連載の4〜6本目は、公開の前に記事の数を数え直す(下の「数え直す回」)。

doeffの連載の1本目の「困りごとから選ぶ入口」は、doeffの連載の表の10本の題を同じ順の一覧で持つ。AIエージェントの連載の回は、表から外し、その連載の1本目へのリンク1行で案内する。表の順や題を変えたら、1本目の一覧も同じに直す。`reviews/main-check.py`が照らす。

原稿の欄の題は、各原稿のfront matterの`title`と同じにする。2026-10-17に、Zennで読まれる記事の傾向と手本の連載の調べを元に、10本の題と冒頭の3文を直した。題は区切り記号(—)で2つに分けず、30字前後の1つの主張にする。直した訳は[決定記録](../../docs/design/decisions/2026-10-17-zenn-titles-and-openings.md)にある。2026-10-20に、実践の技術の回を「〜のをやめる」、開発の記録の回を「〜のをやめた」の形に直した。入門の1本目・数字の記録の回・問いの形の回は、そのままにした。

これから書く原稿は、ファイルが出来るまでリンクにせず、決めたファイル名を書く。ファイル名は`doeff-<英小文字>.md`の形にする(`render_release.py`が記事間のリンクと見なす形)。原稿が出来たら、原稿の欄を、題からその原稿へのリンクに変える。

Zennのslugは、Zennの`articles/<slug>.md`のファイル名で、公開後のURLの末尾になる。半角の小文字・数字・`-`・`_`の12〜50字で書く。公開した後は変えない。

次の回は、2つの表の後に、読者の反応を見て決める(下の「表の後の回」)。

## 各回の仕上げ基準

連載全体では、ユーザーが決めた次の条件を守る。

- 1本目は「doeffとは」の入門にする。困りごとから各回へ案内する目次を持ち、回が出るたびにリンクを足す。
- 取引の研究は載せない。backtestの回は、設計と実装までを書く。実際に試した戦略・損益・相場を記録する仕組み・動かしている機械の構成には触れない。
- 公開の操作はAIがする。1本ずつの公開の可否を、依頼書で問わない(2026-10-20に、始め方の依頼書の答えで決めた)。
- 2026-10-20に始めた。2つの連載を交互に、毎日8:00 JSTに1本出す。

各回の原稿は、次を満たす。

- 最初に具体的な悩みを一つ示し、短いコード・ハンドラ・期待結果から説明する。
- ハンドラの中身を見せる。独自ハンドラは`@handler`・`@do`付きの定義とし、依頼の判別、`Resume`、担当外の`Pass`を説明する。
- 補助処理を`@do`と`yield`で合成する。非同期SDKとの接点以外を丸ごと`async def`へ移さない。
- 実質コード行に目的と期待する値・動作の日本語コメントを付ける。完全な例と実行方法へ案内する。
- 白背景の平面図と、掲載コードの名前・値・処理順を揃える。
- 一つの悩みに必要な説明はその記事内に置く。前の回は補足としてリンクする。
- 固定ハンドラでの検証、実SDKの解析器での検証、実サービスでの検証を区別し、未確認の機能を実証済みとして扱わない。
- 公開する本文は、冒頭の悩み1つ→短いコード→全体の図→本文→まとめ→参考の順にする。
- 公開する本文は3,000〜6,000字(コードの枠は数えない)、コードの枠は6つまで、図は2〜4枚、1文は60字まで、です・ます調にする。
- 測った数字には、測った日と範囲を添える。APIキー・トークン・個人の情報・社外に出せない機械やプロジェクトの名前を載せない。
- 記事の台帳は、記事ごとのファイルを足して書く(図の題と説明は`visuals/captions/<slug>.json`、採用画像は`imagegen/manifest/<slug>.json`、制作の経緯は`imagegen/records/<slug>.md`、決定は`docs/design/decisions/<日付>-zenn-week<N>-<slug>.md`。`<slug>`は原稿`doeff-<slug>.md`の名)。共有の台帳の末尾へ書き足さず、記事の数も書かない。`verify_publication.py`が原稿と台帳の組を照らす。
- 参考の節に、公開順の前の回と次の回へのリンクを`- 前の回:[題](doeff-<slug>.md)`・`- 次の回:[題](doeff-<slug>.md)`の形で置く。`verify_publication.py`が、公開予定・公開済みの回どうしで照らす。
- 掲載したコードの確かめは`reviews/<slug>-check.py`に置く。`verify_examples.py`が、公開予定・公開済みの回の確かめを表の順に走らせる。
- 確かめの頭に、PyPIで入れる物をPEP 723の宣言(`# /// script`)で書く。doeffは版を書かず、GitHubから入れる部品は記事と同じ版を書く。`verify_pypi.py`が毎日と版を出した後に、公開順の表と候補の回の確かめを、PyPIの最新のdoeffと宣言の物だけを入れた新しい環境で走らせる(`.github/workflows/article-code-from-pypi.yml`)。

字数・枠・図・文の長さは`render_release.py`で測る。字数は、コードの枠・画像・リンク先・Markdownの記号・空白を除いて数える。

```bash
uv run --no-sync python publications/zenn-use-cases-v0/render_release.py doeff-main.md --output /tmp/doeff-main.release.md  # 公開する本文を作り、決まりの数を表示する。
```

## 記事間のリンク

リポジトリの原稿・プレビューでは、編集と確認のため全記事の相互リンクを保つ。公開する本文は`render_release.py`が正本から作り、記事間のリンクを公開状態に合わせて直す。

| 状態 | 公開する本文の案内 |
| --- | --- |
| 公開済み | 悩み・解法の表に、その記事の実際のZenn URLを付ける |
| 公開予定 | 題に「(公開予定)」を添え、未公開URLへリンクしない |
| 下書き・候補(表に無い原稿) | 切り口の説明と題を残し、公開日やURLを確約しない |

`examples/`などへの相対リンクは、GitHubのmainのURLへ直す。各個別記事が公開されたら、実際のURLを確認して公開順の表に書く。個別記事にもメインへのリンクを付ける。

## 公開の準備と手順

1. `export_zenn.py`で、公開順の表の公開予定・公開済みの記事を、Zennの記事を置くリポジトリの根へ書き出す。下書きの行は書き出さない。`articles/<slug>.md`と、本文が参照する`images/`の画像が出来る。編集用の原稿は引き続きこのディレクトリを正本とする。
2. 各記事のコード・ハンドラ・期待結果を確認し、`zenn-check/check.mjs`で書き出しを読み、`zenn-check/screenshot.mjs`でプレビューの画面を記事ごとに写して、どちらも崩れが0個であることを確かめる。

書き出しと確かめは次の5行で行う。`<出力先>`は記事を置くリポジトリの根、`<写しの置き場>`は記事ごとの写し(`<slug>.png`)を書くディレクトリである。

```bash
uv run --no-sync python publications/zenn-use-cases-v0/export_zenn.py <出力先>  # 公開順の表の記事を、Zennのarticles/とimages/へ書き出す。
npm ci --prefix publications/zenn-use-cases-v0/zenn-check  # 版を固定したzenn-cliとzenn-modelを入れる。
node publications/zenn-use-cases-v0/zenn-check/check.mjs <出力先> --online  # zenn-cliのプレビューで読み、崩れを数える。0個なら終了コード0。
npx --prefix publications/zenn-use-cases-v0/zenn-check playwright install chromium  # 写しを撮るchromiumを入れる(agoraの会話では要らない)。
node publications/zenn-use-cases-v0/zenn-check/screenshot.mjs <出力先> <写しの置き場>  # プレビューの画面を写し、画面の崩れを数える。agoraの会話では`agora-browser node …`で走らせる。
```

| 書き出しの項目 | 書き出す値 |
| --- | --- |
| ファイル名 | 公開順の表のZennのslug(`articles/<slug>.md`) |
| `title`・`emoji`・`type`・`topics` | 正本のfront matterの値。Zennの規則に合わなければ、何も書かずに止まる |
| `published` | 公開済みの記事だけ`true`、公開予定の記事は`false` |
| 記事間のリンク | `render_release.py`と同じく、公開状態に合わせて直す |
| 画像 | 本文の`/images/...`を、同じパスへ写す。3MBを越える画像やZennで置けない種類は止める |

| 確かめる崩れ | 確かめ方 |
| --- | --- |
| front matterと本文の誤り | プレビューのサーバーが返す記事を、プレビュー画面と同じzenn-modelの検査に通す |
| 画像の欠け | Markdownの画像の数と描いた画像の数を比べ、各画像をプレビューのサーバーから読む |
| リンク切れ | 記事内の`#…`は見出しを探す。相対パスは切れと数える。`--online`では外のURLを開き、GitHubの`#…`は見出しを探す |
| Markdownの崩れ | コードの外に`](`・`![`・`:::`・`**`・`` ` ``が文字のまま残っていないかを見る。`**`は、日本語の括弧に接した太字(例 `**「語」**です`)をZennが太字にしない時に残る |
| 秘密の形 | zenn-cliと同じsecretlintの推奨の規則で、記事のMarkdownを読む |
| 画面の崩れ | `screenshot.mjs`がプレビューの画面をchromiumで開き、検査の警告の行・本文の赤字のエラー(無い画像はこれになる)・読み終えても幅が0の画像(壊れた画像のファイル)を数える |
3. `push_zenn_branch.py`で、書き出しをこのリポジトリの記事だけのbranch `zenn-content`へpushする。ZennのGitHub連携は、このbranchを読む。branchはmainと履歴を共有しないorphan branchで、根に`articles/`と`images/`だけを置く。mainの木は変えない。1本を足す時は、原稿と公開順の表の行をmainに入れてから、同じ命令を走らせる。

```bash
uv run --no-sync python publications/zenn-use-cases-v0/push_zenn_branch.py --dry-run  # commitを作って表示し、pushしない。
uv run --no-sync python publications/zenn-use-cases-v0/push_zenn_branch.py  # 変わった記事があれば、zenn-contentへ1つのcommitでpushする。
uv run --no-sync python publications/zenn-use-cases-v0/push_zenn_branch.py --resync  # Zennと連携した直後に1度だけ走らせ、全記事を同期させる。
uv run --no-sync python publications/zenn-use-cases-v0/push_zenn_branch.py --dry-run --now 2026-10-22T09:00+09:00  # 刻を決めて、その刻に走らせた時の決まりの判じを確かめる。
```

| 命令の決まり | 中身 |
| --- | --- |
| 公開の刻を書いた回 | `published: true`と`published_at: <刻>`の組で置く。Zennはこの組を予約公開として受け、刻を過ぎると自分で公開する |
| 読者に見えている記事 | branchで`published`が`true`で、`published_at`が無いか過ぎた記事。`published`と`published_at`がbranchの値のままでなければ止まる。公開済みの記事を非公開に戻さず、公開日時も変えない |
| 刻の前の記事 | branchで`published: false`か、未来の`published_at`の記事。`false`か、未来の刻の予約にだけ変えられる。予約の刻を変える・予約を解くのは、刻の前にだけできる |
| その場の公開 | しない。`published: true`で刻が無いか過ぎた書き出しが、刻の前の記事を変える時は止まる。branchに無い記事も、`false`か未来の刻の予約で置く |
| 表から外した記事 | branchで公開済みか予約の在る記事が表に無ければ止まる。branchから外しても、Zennの公開と予約は残る |
| `--now` | 公開の刻と比べる今。既定は今の刻で、確かめのために過去や未来を渡せる |
| 変わった記事が無い時 | pushしない |
| 原稿か画像に未commitの変更がある時 | 止まる。branchのcommitは、書き出したcommitを名乗る |
| `--resync` | Zennは連携の後のpushで変わった`.md`だけを同期する。記事を外すcommitと置き直すcommitを、別々のpushで送る。Zennは記事の削除をダッシュボードからしか受けないので、外すpushでZennの記事は消えない |
| pushの資格 | 呼んだ人のgitの資格で`origin`へpushする。agoraの会話では`GIT_CONFIG_COUNT=2 GIT_CONFIG_KEY_0=credential.helper GIT_CONFIG_VALUE_0=agora-lease GIT_CONFIG_KEY_1=credential.useHttpPath GIT_CONFIG_VALUE_1=true`を付けて走らせる |

4. 決めた日時は、公開順の表の公開状態に刻で書く。表をmainに入れて手順3の命令を走らせると、branchの`articles/<slug>.md`が`published: true`と`published_at`の組になり、Zennが刻に公開する。
5. 刻の後に`record_published.py`を走らせる。刻を過ぎた回のZennのURLが開けることを確かめ、公開順の表の公開状態を「公開済み <URL> <刻>」へ直す。表をmainに入れてから手順3の命令を走らせると、他の回の本文のその回へのリンクが、ZennのURLへのリンクになる。

```bash
uv run --no-sync python publications/zenn-use-cases-v0/record_published.py --dry-run  # 刻を過ぎた回と、Zennで開けるかを表示し、表を書かない。
uv run --no-sync python publications/zenn-use-cases-v0/record_published.py  # 刻を過ぎて開ける回を、公開済みに直す。開けない回があれば終わりのcodeは1。
```

6. 読者からの質問や分かりにくかった点を記録し、次の回と11本目からの題材へ反映する。

Zennは1記事を1つのMarkdownで管理し、`published_at`による予約公開に対応している。`published: true`で未来の`published_at`を置いてpushすると、Zennでは公開予約の状態になり、刻を過ぎると自動で公開される。`published_at`は日本時間の`YYYY-MM-DD hh:mm`で書く。設定と配置の仕様は[公式ガイド](https://zenn.dev/zenn/articles/zenn-cli-guide)を参照する。

## 表の後の回

2つの表の後の題材と順は、読者の反応(閲覧の数・質問・分かりにくかった点)を見て決める。表に無い原稿(Memo・カードゲーム・永続実行・ハンドラ合成・開発支援・Rust VM・Hyなど)を候補とする。全23記事の初稿を持ち、公開ごとに説明とリンクを整える。

### 候補

読まれる見込みの強い候補の下書きを、何番目に出すかを決めずに先に用意する。下書きは仕上げ基準を照らしてあり、順を決めたら公開順の表へ移す。2026-10-18に下書きした候補6本は、2026-10-20に2つの表へ移した。今、表に移していない候補の下書きは無い。

### 数え直す回

表へ移した候補のうち次の3本は、記事の数が下書きの日の値なので、公開の前に数え直す。3本とも公開の刻(10月27日・29日・31日の8:00 JST)で予約してあるので、刻の前に数え直して原稿を直し、mainに入れて`push_zenn_branch.py`を走らせる。予約は残るので、直した本文が刻に出る。

- AIエージェントの連載の4本目[止まったClaude Codeを1つずつ開くのをやめた](doeff-waitline.md)(元の候補4)。待ちの申告の内訳と、止めた回数を数え直す([確かめの記録](reviews/waitline.md)・[決定記録](../../docs/design/decisions/2026-10-18-zenn-candidate-waitline.md))。
- AIエージェントの連載の5本目[Claude Codeに報告のページを丸ごと作らせるのをやめた](doeff-reports.md)(元の候補5)。毎日の所要の数を数え直す([確かめの記録](reviews/reports.md)・[決定記録](../../docs/design/decisions/2026-10-18-zenn-candidate-reports.md))。
- AIエージェントの連載の6本目[マージキューでテストを走らせるのをやめた](doeff-mergequeue.md)(元の候補1)。全体の検証の結末と、`stable`の位置を数え直す([確かめの記録](reviews/mergequeue.md))。

ほかの元の候補の記録は、doeffの連載の5本目(元の候補6・[確かめの記録](reviews/commits.md)・[決定記録](../../docs/design/decisions/2026-10-18-zenn-candidate-commits.md))、8本目(元の候補7・[確かめの記録](reviews/types.md)・[決定記録](../../docs/design/decisions/2026-10-18-zenn-candidate-types.md))、AIエージェントの連載の2本目(元の候補2・[確かめの記録](reviews/dispatch.md))にある。

## 1冊の本にまとめる下書き

doeffの連載10本を、1冊の無料のZennの本にまとめる下書きを持つ(候補14・2026-10-18)。AIエージェントの連載は本に入れない。本を出すかと出す時期は、連載の始め方を決めた後に決める。記事のbranch `zenn-content`には置かず、公開もしない。

| 項目 | 中身 |
| --- | --- |
| 本の正本 | `books/doeff-effects-and-handlers/`。Zennの本と同じ形で、`config.yaml`(章立て)・章の雛形10個・`rewrites.json`・表紙を置く |
| 題 | doeffで、処理を書き換えずにテストと本番の動かし方を替える |
| 値段と公開 | `price: 0`(無料)・`published: false`。`export_zenn_book.py`は、どちらかが違うと止まる |
| 章の本文 | 書き出すたびに原稿から作る。本のために原稿を写して直さない |
| 章のつなぎ | 章の雛形の、印の行「`<!-- 原稿の本文 -->`」の前と後に書く。前後の章は`[章](doeff-<slug>.md)`のリンクで書き、書き出しが「第N章」に直す |
| 連載の言葉 | 「3本目の」「前の回:」「この記事」などを、決まった規則と`rewrites.json`で直す。直す前の文が原稿から消えると止まる |
| 章の順 | 1 doeffとは・2 DI・3 時間・4 通信・5 AIエージェント・6 バックテスト・7 yieldとasync・8 commitの68%・9 型の穴・10 ADR |

章の順は、連載の順(案M)と違い、差し替える物が1つずつ増える順にした。訳は[決定記録](../../docs/design/decisions/2026-10-18-zenn-book-draft.md)にある。連載を2つに分けた2026-10-20に、8章と9章をAIの会話を50並べる記録とhookから、commitの68%と型の穴に替えた。8〜10章は、doeffそのものの約束を機械で守る3章になる([決定記録](../../docs/design/decisions/2026-10-20-zenn-series-split.md))。書き出しと確かめは、記事と同じ道具で行う。`check.mjs`と`screenshot.mjs`は、出力先に`books/`があれば本も読む。

```bash
uv run --no-sync python publications/zenn-use-cases-v0/export_zenn_book.py <出力先>  # 本の正本と原稿から、books/<本のslug>/とimages/を書き出す。
node publications/zenn-use-cases-v0/zenn-check/check.mjs <出力先> --online  # 本はvalidateBook、章はvalidateBookChapterで読み、崩れを数える。
agora-browser node publications/zenn-use-cases-v0/zenn-check/screenshot.mjs <出力先> <写しの置き場>  # 本と章の画面を写し、画面の崩れを数える。
```

`verify_publication.py`も、本の正本が今の原稿から崩れずに書き出せることを照らす。原稿を直して本の書き出しが止まったら、`rewrites.json`の直す前の文を、直した原稿の文に合わせる。

## 準備状況

- [x] 7日間の題材と順序を合意した(2026-09-16)。
- [x] 公開先をZenn、最初の4本を週1本の順に決めた(2026-10-16)。
- [x] 連載10本の順を案Mに決めた(2026-10-17)。
- [x] 最初の4本に対応する原稿・コード・レビュー記録がある。
- [x] 5〜10本目の下書きを仕上げ基準に揃え、公開状態を公開予定にした。5・7・8・10本目は新しいファイルに書いた(2026-10-17)。
- [x] 10本の各回の参考に、前の回と次の回へのリンクを置いた。各回の確かめのスクリプト(`reviews/<slug>-check.py`)を`verify_examples.py`から呼ぶ(2026-10-17)。
- [x] 10本の題と冒頭の3文を、Zennで読まれる記事の傾向と手本の連載の調べで直した。2〜10本目の冒頭に、1本目とGitHubへ案内する1文を置いた(2026-10-17・[決定記録](../../docs/design/decisions/2026-10-17-zenn-titles-and-openings.md))。
- [ ] 各回の図を揃える。`images/zenn-use-cases-v0/generated/`のPNG 47枚はリポジトリに入っておらず、週1の3枚、週3と週4の各2枚を作り直した(2026-10-16)。
- [x] Zennの形への書き出しと確かめの道具を作った。最初の4本を書き出し、崩れ0個を確かめた(2026-10-16・zenn-cli 0.5.6)。連載10本を書き出し、`check.mjs`と`screenshot.mjs`で崩れ0個を確かめた。10本の図22枚はリポジトリにある(2026-10-17)。
- [x] 記事を置く場所を、このリポジトリの記事だけのbranch `zenn-content`に決め、10本を`published: false`で置いた(2026-10-18・[決定記録](../../docs/design/decisions/2026-09-15-zenn-use-cases.md))。
- [x] 連載10本を1冊の無料のZennの本にまとめる下書きを作り、Zennの本の形で崩れ0個を確かめた(2026-10-18・[決定記録](../../docs/design/decisions/2026-10-18-zenn-book-draft.md))。本は公開しておらず、`zenn-content`にも置いていない。
- [x] 1本目を確定の形にした。目次に連載10本の順の一覧と候補の扱いの1文を置き、AIの表を2つに分け、各表で10本の回を先に並べた。PyPIのdoeff 0.6.0と0.7.0で確かめ直し、記事と本の書き出しで崩れ0個を確かめた(2026-10-19・[記録](reviews/main-final.md)・[決定記録](../../docs/design/decisions/2026-10-19-zenn-week1-main.md))。
- [x] ZennのダッシュボードでこのリポジトリとZennを連携し、同期するbranchを`zenn-content`にした(2026-10-19・ユーザーの頼みでブラウザを操作するChatGPT workが設定した)。その後に`push_zenn_branch.py --resync`を1度走らせ、Zennの下書きの一覧に10本が並び、デプロイが3回とも成功した事を確かめた(2026-10-20)。
- [x] 連載をdoeffの連載10本とAIエージェントの連載6本に分け、題を「〜のをやめる」の形に直した。候補6本を2つの表へ移し、各回の前後のリンク・1本目の目次・本の章立てを揃えた。1本目を確定の形にし直し、記事と本の書き出しで崩れ0個を確かめた(2026-10-20・[記録](reviews/series-split.md)・[決定記録](../../docs/design/decisions/2026-10-20-zenn-series-split.md))。
- [x] 最初の4本を上の仕上げ基準で最終確認する。週1は2026-10-16に確認した。1〜5本目を通し読みで照らし、用語・前後の回・導入の案内を直した(2026-10-17・[記録](reviews/readthrough-1-5.md))。
- [x] 開始日と出し方を決めた。2026-10-20から2つの連載を交互に毎日1本、AIが公開する。doeffの連載の1本目を2026-10-20 14:08 JSTに公開した。
- [x] 決めた日時に出す仕組みを作った。表の公開状態に刻を書き、`push_zenn_branch.py`がZennの予約公開として置く。残る15本を10月21日から11月4日まで毎日8:00 JSTで予約する(2026-10-21・[決定記録](../../docs/design/decisions/2026-10-21-zenn-reserved-publish.md))。
- [ ] 各回の公開後に、`record_published.py`で公開状態を更新する。

編集判断と変更を戻す手順は[決定記録](../../docs/design/decisions/2026-09-15-zenn-use-cases.md)へ残す。
