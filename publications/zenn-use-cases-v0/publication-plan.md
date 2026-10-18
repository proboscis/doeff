# 連載10本の公開計画

合意日：2026-09-16に「7日間、毎日1本」をユーザーが承認した。2026-10-16に、ユーザーが公開先をZenn、頻度を週1本、1本目を「doeffとは」の入門に決め直した。2026-10-17に、ユーザーが連載10本の順を案Mに決めた。

目的は、毎回一つの具体的な悩みからdoeffを試せる入口を作ること。1本目のメイン記事は、困りごとから各回へ案内する目次として育てる。各回は、前の回を読んでいなくても理解できる形にする。

公開の操作はユーザーが行い、AIは記事を公開しない。執筆担当は原稿の仕上げと計画までを受け持つ(2026-10-16のユーザーの答え「勝手に公開はしないこと。執筆と計画まで。」)。1本ずつの公開の可否は、依頼書で問わない(2026-10-16のユーザーの差し戻し「連載の概要すら議論してないのに、週一の単一記事を決済書で出されても困る」)。

## 公開順

週1本を公開する。開始日はまだ決めていない。この表は公開順の合意であり、Zennへの登録・予約の記録ではない。

案Mは、実践の技術の3本(2〜4本目)の後に、5本目から開発の記録を挟む順である。

| 週 | 原稿 | Zennのslug | 読者への入口 | 公開状態 |
| --- | --- | --- | --- | --- |
| 1 | [処理を書き換えずに、テストと本番で動かし方を替えるdoeffとは](doeff-main.md) | `doeff-introduction` | エフェクトと最小ハンドラを実行し、用途の全体像を見る | 公開予定 |
| 2 | [Claude CodeやCodexを呼ぶ処理を、起動せずにテストする](doeff-agents.md) | `doeff-ai-agents` | AIエージェントの仕事を、起動・待機・結果の契約で組み合わせる | 公開予定 |
| 3 | [20秒待つ処理を、20秒待たずにテストする](doeff-time.md) | `doeff-virtual-time` | 20秒待つ処理を、20秒待たずにテストする | 公開予定 |
| 4 | [DIコンテナなしで、依存を引数でバケツリレーしない](doeff-di.md) | `doeff-dependency-injection` | 必要な依存をその場で依頼し、外側から供給する | 公開予定 |
| 5 | [1人でClaude Codeを最大50並べたら、詰まるのは人だった](doeff-fleet.md) | `doeff-fifty-ai-conversations` | AIの会話を最大50並べて、1人でdoeffと周りの仕組みを開発した記録を読む | 公開予定 |
| 6 | [外部APIのテストを、通信せずに回す](doeff-replay.md) | `doeff-offline-api-tests` | 遅く落ちる外部APIのテストを、保存した応答で通信せずに回す | 公開予定 |
| 7 | [バックテストと本番で、処理を2つ書かない](doeff-backtest.md) | `doeff-backtest-design` | 時刻と相場の入力をハンドラで差し替え、過去のデータと本番で同じ処理を動かす | 公開予定 |
| 8 | [CLAUDE.mdに決まり851本を書かず、hookで要る時だけ見せる](doeff-hooks.md) | `doeff-ai-rule-hooks` | AIが決まりを読み落とす時に、要る場面でだけ規則を見せる | 公開予定 |
| 9 | [async/awaitがあるのに、なぜyieldで書くのか](doeff-coroutines.md) | `doeff-yield-vs-async-await` | `async def`・`await`と`@do`・`yield`で、依頼の渡し先と再開の仕方を比べる | 公開予定 |
| 10 | [ADRをpytestで走らせ、設計の約束を守らせる](doeff-adr.md) | `doeff-executable-adr` | 設計の決まりが崩れないよう、例と規則を書いた設計の記録をテストとして走らせる | 公開予定 |

公開状態は「公開済み <ZennのURL>」「公開予定」「下書き」のどれかで書く。下書きは、まだ仕上げ基準を満たしていない原稿で、満たしたら公開予定に変える。`render_release.py`がこの表を読み、公開する本文のリンクを作る。下書きの行と表に無い原稿は、候補として扱う。

5〜10本目は、各回の担当が下の仕上げ基準を1項目ずつ照らし、掲載したコードを走らせた。その結果を受けて、2026-10-17に公開予定へ変えた。公開予定はZennへの登録ではない。

原稿の欄の題は、各原稿のfront matterの`title`と同じにする。2026-10-17に、Zennで読まれる記事の傾向と手本の連載の調べを元に、10本の題と冒頭の3文を直した。題は区切り記号(—)で2つに分けず、30字前後の1つの主張にする。直した訳は[決定記録](../../docs/design/decisions/2026-10-17-zenn-titles-and-openings.md)にある。

これから書く原稿は、ファイルが出来るまでリンクにせず、決めたファイル名を書く。ファイル名は`doeff-<英小文字>.md`の形にする(`render_release.py`が記事間のリンクと見なす形)。原稿が出来たら、原稿の欄を、題からその原稿へのリンクに変える。

Zennのslugは、Zennの`articles/<slug>.md`のファイル名で、公開後のURLの末尾になる。半角の小文字・数字・`-`・`_`の12〜50字で書く。公開した後は変えない。

11本目からは、10本への読者の反応を見て決める(下の「11本目から」)。

## 各回の仕上げ基準

連載全体では、ユーザーが決めた次の条件を守る。

- 1本目は「doeffとは」の入門にする。困りごとから各回へ案内する目次を持ち、回が出るたびにリンクを足す。
- 取引の研究は載せない。backtestの回は、設計と実装までを書く。実際に試した戦略・損益・相場を記録する仕組み・動かしている機械の構成には触れない。
- 公開の操作はAIがしない。1本ずつの公開の可否を、依頼書で問わない。
- 始める日はまだ決めていない。決まったら、この計画に書く。

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
```

| 命令の決まり | 中身 |
| --- | --- |
| `published` | 変えない。branchに在る記事はbranchの値のままでなければ止まり、branchに無い記事は`false`でなければ止まる。公開済みの記事を非公開に戻さず、公開予定の記事を公開しない |
| 変わった記事が無い時 | pushしない |
| 原稿か画像に未commitの変更がある時 | 止まる。branchのcommitは、書き出したcommitを名乗る |
| `--resync` | Zennは連携の後のpushで変わった`.md`だけを同期する。記事を外すcommitと置き直すcommitを、別々のpushで送る。Zennは記事の削除をダッシュボードからしか受けないので、外すpushでZennの記事は消えない |
| pushの資格 | 呼んだ人のgitの資格で`origin`へpushする。agoraの会話では`GIT_CONFIG_COUNT=2 GIT_CONFIG_KEY_0=credential.helper GIT_CONFIG_VALUE_0=agora-lease GIT_CONFIG_KEY_1=credential.useHttpPath GIT_CONFIG_VALUE_1=true`を付けて走らせる |

4. 公開開始日・時刻を確定してから、ユーザーがbranchの`articles/<slug>.md`の`published`を`true`にするcommitで公開する(GitHubの画面でファイルを直してもよい)。AIは公開しない。
5. Zennの公開状態とURLを確認し、上の公開順の表の公開状態を「公開済み <URL>」へ更新する。更新するまで、`push_zenn_branch.py`はその記事を非公開に戻すとみて止まる。
6. 読者からの質問や分かりにくかった点を記録し、次の回と11本目からの題材へ反映する。

Zennは1記事を1つのMarkdownで管理し、`published_at`による予約公開に対応している。設定と配置の仕様は[公式ガイド](https://zenn.dev/zenn/articles/zenn-cli-guide)を参照する。

## 11本目から

11本目からの題材と順は、10本への読者の反応(閲覧の数・質問・分かりにくかった点)を見て決める。表に無い原稿(Memo・カードゲーム・永続実行・ハンドラ合成・開発支援・Rust VM・Hyなど)を候補とする。全23記事の初稿を持ち、公開ごとに説明とリンクを整える。

### 候補

読まれる見込みの強い候補の下書きを、何番目に出すかを決めずに先に用意する。下書きは仕上げ基準を照らしてあり、順を決めたら公開順の表へ移す。

- 候補1:[マージキューでテストを走らせるのをやめた](doeff-mergequeue.md)。Zennのslugの案は`doeff-merge-queue-without-tests`。2026-10-18に下書きした([確かめの記録](reviews/mergequeue.md))。公開の前に、記事の数(全体の検証の結末・`stable`の位置)を数え直す。
- 候補6:[ライブラリのcommitの68%を、Claude Codeと書いた1年](doeff-commits.md)。Zennのslugの案は`doeff-claude-code-commits`。2026-10-18に下書きした([確かめの記録](reviews/commits.md)・[決定記録](../../docs/design/decisions/2026-10-18-zenn-candidate-commits.md))。
- 候補2:[Claude Codeに仕事を配らせるのをやめ、関数で配る](doeff-dispatch.md)。Zennのslugの案は`doeff-dispatch-by-function`。5本目と材料が重なり、その続きに置ける。2026-10-18に下書きした([確かめの記録](reviews/dispatch.md))。
- 候補7:[公開APIの型の穴をテストで塞ぎ、黙って開かせない](doeff-types.md)。Zennのslugの案は`doeff-public-api-types`。候補6の4つの網のうち、型の網を詳しく書く回。2026-10-18に下書きした([確かめの記録](reviews/types.md)・[決定記録](../../docs/design/decisions/2026-10-18-zenn-candidate-types.md))。

## 1冊の本にまとめる下書き

連載10本を、1冊の無料のZennの本にまとめる下書きを持つ(候補14・2026-10-18)。本を出すかと出す時期は、連載の始め方を決めた後に決める。記事のbranch `zenn-content`には置かず、公開もしない。

| 項目 | 中身 |
| --- | --- |
| 本の正本 | `books/doeff-effects-and-handlers/`。Zennの本と同じ形で、`config.yaml`(章立て)・章の雛形10個・`rewrites.json`・表紙を置く |
| 題 | doeffで、処理を書き換えずにテストと本番の動かし方を替える |
| 値段と公開 | `price: 0`(無料)・`published: false`。`export_zenn_book.py`は、どちらかが違うと止まる |
| 章の本文 | 書き出すたびに原稿から作る。本のために原稿を写して直さない |
| 章のつなぎ | 章の雛形の、印の行「`<!-- 原稿の本文 -->`」の前と後に書く。前後の章は`[章](doeff-<slug>.md)`のリンクで書き、書き出しが「第N章」に直す |
| 連載の言葉 | 「3本目の」「前の回:」「この記事」などを、決まった規則と`rewrites.json`で直す。直す前の文が原稿から消えると止まる |
| 章の順 | 1 doeffとは・2 DI・3 時間・4 通信・5 AIエージェント・6 バックテスト・7 yieldとasync・8 AIの会話を50並べる・9 hook・10 ADR |

章の順は、連載の順(案M)と違い、差し替える物が1つずつ増える順にした。訳は[決定記録](../../docs/design/decisions/2026-10-18-zenn-book-draft.md)にある。書き出しと確かめは、記事と同じ道具で行う。`check.mjs`と`screenshot.mjs`は、出力先に`books/`があれば本も読む。

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
- [ ] ZennのダッシュボードでこのリポジトリとZennを連携し、同期するbranchを`zenn-content`にする(ユーザーの操作)。その後に`push_zenn_branch.py --resync`を1度走らせる。
- [x] 最初の4本を上の仕上げ基準で最終確認する。週1は2026-10-16に確認した。1〜5本目を通し読みで照らし、用語・前後の回・導入の案内を直した(2026-10-17・[記録](reviews/readthrough-1-5.md))。
- [ ] 開始日を決める。
- [ ] 各回の公開後に、公開状態を更新する。

編集判断と変更を戻す手順は[決定記録](../../docs/design/decisions/2026-09-15-zenn-use-cases.md)へ残す。
