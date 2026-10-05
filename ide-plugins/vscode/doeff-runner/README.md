# doeff runner (VS Code)

Run `doeff` `Program` values directly from VS Code. The extension mirrors the PyCharm plugin: it detects annotated `Program[...]` bindings, looks up interpreters/kleisli/transformers via `doeff-indexer`, and launches `doeff run` under the Python debugger.

## 文章・用語を workspace 全体で検査する

0.7.0 以降は Rust の doc-linter 0.3.0 以降を使い、開いていない文書も含めて検査します。
「Doeff Hy」の「文章・用語（doc-linter）」パネルで、対象ファイル数、検査の完了数、
キャッシュ利用数、未測定数、用語と指摘を確認できます。文章の指摘は従来の
「違反（Linter）」と「問題」パネルにも診断元 doc-linter として表示します。

| 指摘 | 意味 |
|---|---|
| DOC001〜004 | 文章の説明不足。助言として表示 |
| DOC101〜104 | 用語の未定義・重複・循環・不正な宣言 |
| DOC000 | 検査できなかった理由 |

Rust の実行ファイルを ~/.local/bin、PATH、~/.cargo/bin、/opt/homebrew/bin から探します。
設定 doeff-runner.docLint.binary に絶対パスを指定することもできます。
Jev の接続設定と永続キャッシュは CLI と共有します。本文と参照した用語の説明を
設定済みの Jev に送信します。同じ入力は再推論せず、定義変更時は参照する本文だけを
再測定します。拡張は全体検査に fresh を指定しません。

0.7.1 は逐次結果を最大200ミリ秒ごとにまとめて反映し、進捗だけの変化で全診断を
再描画しません。doc-linter 0.3.1 では再読み込み時にキャッシュの結果を先に復元し、
未測定の文章への通信はその後に開始します。初回の全件検査には対象数に応じた時間が
かかりますが、保存済みの結果の表示はその通信を待ちません。

対象は .hy / .py / .pyi / .md / .markdown / .txt。Git の無視設定と依存物のディレクトリを
除く全対応ファイルを、信頼済み workspace の起動時に検査します。作成・変更・削除や
未保存の編集後は2秒待って再検査し、古いストリームを破棄します。Rust プロセスは
同時に1つ、内部問い合わせは最大4件です。doeff-runner.docLint.enabled で停止できます。
命令「doeff: 文章の説明を再検査する（doc-linter）」も workspace 全体を対象にします。

用語は Markdown の見出し \## 表示名 {#term:安定ID} とその本文、または Hy の
(defterm identifier "表示名" "説明") で定義します。Hy では doeff-hy.macros から defterm を
require します。参照は [表示名](term:安定ID) です。用語を定義しても関数の用途そのものは
コメントに書く必要があります。説明同士の循環は診断し、無限に展開しません。

通常のエディタでは参照にマウスを重ねると説明が出て、F12で定義、Shift+F12で使用箇所へ
移動できます。doeff viewer ではカードの「用語の説明」のボタンから定義と使用箇所を
開けます。文章・用語パネルの用語をクリックしても定義へ移動できます。

## 実行機能の要件

- VS Code Python extension
- `doeff` installed in the active Python environment (`pip install doeff`)

The extension bundles `doeff-indexer` binaries for common platforms (macOS, Linux, Windows). No additional configuration is needed in most cases.

### Binary Discovery Order

1. **Bundled binary** (platform-specific binary included with the extension)
2. `DOEFF_INDEXER_PATH` environment variable (if set)
3. Python environment bin directory (from the Python extension)
4. System paths (`/usr/local/bin/`, `~/.cargo/bin/`, `~/.local/bin/`, etc.)

## How it works

- CodeLens appears on lines annotated with `Program[...]`
- **Run**: runs `uv run doeff run --program <path>` (fallback: `python -m doeff run --program <path>`)
- **Run with options**: invokes `doeff-indexer` to:
  - resolve the program's qualified name for the current file
  - gather available interpreters, Kleisli programs, and transformers
- A quick-pick dialog lets you choose the interpreter and optional Kleisli/transformer, then starts `python -m doeff run ...` using the configured interpreter
- **➕ Playlist**: saves a worktree-aware execution unit (branch + optional commit pin); edit tools later in the Playlists view
- Appends/updates `.vscode/launch.json` so you can tweak the run config

## Playlists (Worktree-aware)

- **Programs (All Worktrees)** view indexes all `git worktree` checkouts and lets you add any Program to a playlist.
- **Playlists** are stored in `.git/doeff/playlists.json` (shared across all worktrees).
- Running a pinned item can create a temporary detached worktree at the pinned commit when needed.
- Playlist item: click to **Go to Definition**; use the inline ▶ action to Run/Debug.

## Hy(doeff-hy)のコードを行き来する

`.hy` / `.hyk` / `.hyp`(languageId `hy`)の file で、次の機能が使えます。

- **定義へ移動**(F12): カーソルの名前を mangle(`-` → `_`)して照合し、次の順で探します。
  1. 同じ file の定義(`Color.RED` のような入れ物の member も)
  2. その file の import(名前・`:as` の別名・module の別名 + dotted の `alias.fn`・相対 import)から決めた module の Hy の定義
  3. import 先が Python の module(索引に無い物)なら、workspace の中の `a/b.py` か `a/b/__init__.py`(root 直下 → `src/` → workspace 全体)の `def` / `class` / `name =` の行
  4. それでも無い module(uv の git / path 依存の package など workspace の外の物)は、workspace の Python 環境に `uv run --no-sync --project <workspace の root> python -c …` で置き場所を聞きます(`hy` が入っていれば `.hy` の module も引けます)。`.hy` ならその 1 file を `hy-index --file` で索引して定義へ、`.py` なら 3 と同じ探し方で飛びます。聞いた結果は workspace の root ごとに持ち、`uv.lock` か `pyproject.toml` が変わると聞き直します。外の file の索引は別に持ち、workspace の記号の検索と参照の一覧には混ぜません。uv が無い・時間切れ等は Output に理由を出して、この段を飛ばします。
  5. どれでも見つからなければ workspace 全体の同名の定義を全部
- **参照の一覧**(Shift+F12): 全 file の参照と定義から同じ名前の位置を集めます。定義の module が 1 つに定まる時は、import と dotted の修飾で別の module の同名を除き、修飾を解けない参照(`self.x` など)は名前だけで数えます。
- **ファイルの目次**(Outline・パンくず): 定義を入れ物で入れ子にします(class の method と field、enum の member、handler の effect 節)。横に kind と引数を出します。
- **workspace の記号の検索**(Cmd/Ctrl+T): 全 file の定義を名前で絞ります。
- **hover**: 定義の kind・引数・module・docstring を出します。effect には扱う handler の数、defk / deff / defp には中で撃つ effect、handler には扱う effect を足します。

### effect・handler・defk を行き来する

名前が effect であるとは、`defclass` / `defrecord` の基底に `EffectBase`(`doeff.EffectBase` のような dotted も最後の区切りで比べます)か effect のクラスがある(推移的に)か、どこかの `defhandler` にその名前の節があることです。workspace の外の package の effect は、定義へ移動で一度開いて索引を取った物まで数えます。

- **Cmd+クリック**: effect の名前の上では、クラスの定義とその effect を扱う全 handler の節を返します(複数なら peek で選べます)。普通の関数・defk は今までどおりです。
- **実装へ移動**(Cmd+F12): effect の名前の上(クラスの定義・`(PutRow …)` の呼び出し・import・handler の節の頭)ではその effect を扱う全 handler の節へ、handler の名前の上ではその handler の節の一覧へ移動します。
- **呼び出し階層**(Shift+Alt+H): defk / deff / defp / defn / defhandler / effect の節 / effect のクラスが項目です。出ていく呼び出しは行き先ごとにまとめ、effect の生成には「effect」と出します。effect のクラスから出ていくと、その effect を扱う handler の節へ降ります。入ってくる呼び出しは、名前で絞ってから定義へ移動と同じ解決で行き先を確かめます。handler の節へは、その effect を撃つ場所から入ってきます。
- **コード上の注記**: effect のクラスの上に「handler N 個」「撃つ場所 M 箇所」、defhandler の上に「扱う effect: …」、defk / deff / defp の上に「撃つ effect N 個」「呼び出し元 M 箇所」を出します。押すと 1 件なら直接移動し、複数なら peek で一覧を出します。
- **ナビゲーションパネル**(activity bar の「doeff Hy」): Effects(module ごとの effect → Handlers と Performed by)、Handlers(handler → 扱う節 → 同じ effect の他の handler)、Programs(defk / deff / defp → Performs・Calls・Called by。effect からは Handlers へ降りられます)、Current file(今開いている file の分だけ)。項目を押すとその位置へ移動し、右クリックで「参照を表示」「呼び出し階層を表示」を選べます。view の上に絞り込みと更新のボタンがあります。子は展開した時に作り、既に開いた経路に戻る項目は「循環」として止めます。

### linter の結果(doeff-linter)

規則の判定の正本は linter で、拡張はその出力を表示するだけです(自分では判定しません)。operator の逐語は "linter must be the source of truth and editor must follow that" です。

- **呼び方**: 設定 `doeff-runner.hy.lintCommand`(workspace ごと)。既定は `doeff-linter --output-format editor-json` です。空にすると呼びません。
  - 起動時と workspace の変化、「再実行」のボタンでは、repo 全体を呼びます(引数なし)。
  - 保存時と編集の 0.8 秒後には、その file を `--stdin --path <path>` で呼びます。
  - 子 process は同時に 1 つで、時間切れがあります。終了コード 2 と、契約(`lint-contract-v1`)に合わない出力は、理由を Output に出します。
- **Jev の判定**(意味の規則): 決定的な実行とは別の子 process で、同時に 1 本だけ走らせます。
  - 保存した時(設定 `doeff-runner.hy.semanticOnSave`・既定 on): その file を `--semantic <path>` で呼びます。
  - 編集中(設定 `doeff-runner.hy.semanticOnChange`・既定 on): 打つのが止まったら(`doeff-runner.hy.semanticOnChangeDelaySeconds`・既定 2 秒)、
    その時の中身を `--stdin --path <path> --semantic --semantic-changed` で呼びます。問うのは、構文として読めて中身が変わった定義だけです。書きかけで読めない定義は問いません。
  - 問うている間にまた打つと、その答えは捨てて最新の中身の答えだけを出します。まだ走り始めていない古い中身の依頼は取り下げます。
  - repo の pyproject に `[tool.doeff-linter.semantic] proxy_url` があれば、Jev の呼び出しを覚える代理を経由します。同じ定義を 2 回目に問う時は、どの機体・worktree からでも Jev を呼びません。
- **波線**(問題の一覧): 重さは linter のとおりです(error = 新しい破れ、warning = 登録簿に載った既知の破れ、info)。文には、直し方と、規則の ID・ADR の law の名が付きます。
- **「違反(linter)」パネル**: 重大さの要約 → (重大さ, 規則) → file → 違反の順に並べます(詳しくは下の「拡張の中での使い方」)。押すとその位置へ移動します。
  - 上の `$(law)` で「規則の一覧」に切り替えます。針のつながっていない規則は灰色で、linter がまだ見ていない物です。
- **「層の地図(linter)」パネル**: linter の `modules` の層(core・intent・protocol・foundation・entry・層の外)→ dir → file の木です。色は違反の有無だけで付けます。

### タグで閲覧

定義(hy-index の definitions)を軸で並べ替えて見るパネルです。判定はせず、軸の値は hy-index と linter の出力を読むだけです。

- **軸**: service・層(linter の modules)、context・role(定義の `:tags` → 無ければ linter が読んだ module の頭の MODULE-TAGS)、`:tags` に書いた任意の鍵(`:owner` を書けば owner の軸が出る)、kind、生の副作用の分類(hy-index の `raw.direct`)、違反の規則(linter の違反で範囲が定義に入る物)。
  - 値が無い時は「(不明)」か「なし」です。複数の値を持つ定義(生の副作用の分類など)は、各値の下に出ます。
- **並べ方**: view の上のボタンで、軸の順を 1〜3 段選びます。各段の項目には件数と違反の数が出ます。末端は定義で、押すとその範囲を選んで移動します。
- **絞り込み**: 軸 = 値 の条件を AND で足します(値は件数つきで複数選べます)。今の条件は view の説明欄に出ます。解除のボタンもあります。
- **保存した見方**: 並べ方と絞り込みに名前を付けて、設定 `doeff-runner.hy.browseViews` に保存します(workspace の設定に置けば repo で共有できます)。同梱の見方は「service ▸ 層」「層 ▸ service」「生の副作用 ▸ service」の 3 つです。

### 層を見分ける表示(linter の layers から)

層とその説明、違反の理由は、すべて linter(doeff-linter の editor-json)が出します。拡張は文を持ちません。

- **エクスプローラーの印**: file に層の頭文字(C・I・P・F・E …)を付けます。層の外の file には付けません。
  - 文字は層を表し、色は違反の有無を優先します。違反のある file は問題の色(赤)、違反が無ければ linter の層の順に当てた色です。層は文字で、壊れているかは色で見分けます。
  - tooltip には、linter の層の一行の説明と、その file の層を何で決めたか(`layer_reason`)を出します。
- **ステータスバー**: 今開いている file の「層: protocol — 相手の話し方へ訳す handler(context: …)」を出します。押すと linter の `layers` の表(知っていること・知らないこと・迷った時の問い)を開きます。
- **hover**: `MODULE-TAGS` の辞書と、定義の契約の辞書の `:tags` / `:role` の上で、書かれた role・linter の決めた層・層の決め方・層の説明を出します。
  - 違反のある行では、その違反の「これは何か」「なぜ違反か」「law の :statement」「直し方」を出します。問題の一覧の文にも同じものを出し、行末の注記は短い文のままです。
- **「層の地図(linter)」**: 層の項目の description に、linter の一行の説明を出します。
- linter が説明を出さない時(古い binary、設定に説明が無い)は、層の名前だけを出し、説明の欄は空にします。説明は doeff-linter の設定 `[tool.doeff-linter.layers.describe.<層>]` に書きます。

### 生の副作用に触る handler の印(事実の表示)

http・asyncio・時刻・乱数・file・process・環境変数・network・db・thread に直接触る定義に印を付けます。handler(defhandler と effect の節)を見分けるための機能です。同じ effect を実際の I/O で扱う本番の handler と、純粋な模擬の handler を並べて区別できます。

- **証拠を集めるのは doeff-indexer(`hy-index`・契約 版 3 の `raw`)** です。目録は doeff-indexer に同梱の `packages/doeff-indexer/data/raw_side_effects.json` の 1 か所にあり、拡張は証拠を表示するだけで目録を持ちません。
- これは事実の表示で、規則の違反の表示ではありません。何が違反かの判定の正本は linter です。
- **証拠の集め方**(hy-index の中): 索引の imports・references・calls と定義の範囲だけを使います(実行はしません)。参照を import で完全な名前に直して(`(import subprocess :as sp)` の `sp.run` は `subprocess.run`)、目録と比べます。
  - 強い根拠: import を通した名前と、呼び出しの頭の組み込み `open`。
  - 弱い根拠(印に「?」): method 名だけの一致(`(.read-text p)` など)。pathlib の method は、pathlib が見える時だけ数えます。
  - 数えない物: 例外の型(`httpx.ReadTimeout` など)と、純粋な module(`urllib.parse` など)。
- **経由**: 呼ぶ定義(同じ file か import で決まった物)が生に触るなら「経由で触る」とします。経路つきで深さ 4 まで辿ります。file をまたぐので、起動時の全体の索引で計算します。編集中の file には、直前の全体の結果を引き継ぎます。
- **印の出し方**:
  - Handlers パネル: 直接触る handler と節は `$(zap)` と「生: http, time」、経由だけなら `$(debug-stackframe)` と「経由: time」。Effects パネルの handler の節にも同じ印が付きます。view の上の `$(zap)` で「生の副作用に触る handler だけ表示」を切り替えます。
  - コード上の注記: defhandler と effect の節の上に「⚡ 生の副作用: http(httpx.post・42 行)」(経由だけなら「↳ 経由で生の副作用: …」)。押すと証拠の位置の一覧です。defk / deff / defp は直接の証拠だけを「⚡ 生の副作用に直接触る: …」と出します。
  - hover: 証拠の一覧(分類・名前・行・直接か経由か、経由なら経路)。
- **設定** `doeff-runner.hy.rawSideEffects`: 目録に足す名前(分類 → 名前の配列)です。hy-index に `--raw-catalog-extra` で渡します。
  - 名前の書き方: dotted の名前、`.name` は method 名、`builtin:name` は組み込み。
  - 読めない値の理由は Output に出ます。
- **見逃す形**: handler の引数で渡された client や関数(`[client]`・`[#^ Callable jev]`)の上の method 呼び出しは、型の注釈(`#^ httpx.Client client`)が無いと見分けられません。

### 索引の取り方

- 索引は `doeff-indexer hy-index` が出す JSON(版 3。版 3 だけを受け付けます)です。起動時に Hy の file を持つ workspace の folder ごとに `hy-index --root <folder>` を背景で実行し、編集中は 0.5 秒待ってから `--stdin --path <file>` でその file だけを取り直します。file の作成・削除・改名も反映します。
- `doeff-indexer` は上の「Binary Discovery Order」と同じ順で探します。見つかった binary が `hy-index` を知らない古い版なら、1 度だけ通知を出して Hy の機能を止めます(Python 向けの機能はそのまま動きます)。
- 子 process は同時に 1 つだけ走らせます。失敗・契約に合わない出力・file ごとの読み取りの問題は Output の `doeff-runner` に理由つきで出ます。

## 新しい版が入った時の報せ(vsix の追随)

手元の機体では、dotfiles の vsix の追随(`agentcli` の `vsix_follow`)が、本線に入った拡張を組んで VS Code に入れます。VS Code は Reload Window まで動作中の拡張を替えないので、この拡張が次の 2 つを報せます。

- **新しい版が入った**: 追随が書く状態 file `~/.local/state/ai/vsix-follow/status.json` の入った commit と、いま動いている拡張の入った dir の印 `out/vsix-follow-build.json` の commit が違えば、「新しい版が入った — Reload Window で有効」の通知を Reload のボタンつきで 1 回出します(この拡張と python-semantic-highlighter の両方)。押すと Reload Window が走ります。Reload は押した時だけで、追随は自分では Reload しません。
- **組み立てが落ちた**: 状態 file に直近の失敗があれば、理由の 1 行つきの警告を 1 回出します(同じ失敗は繰り返しません)。

状態 file が無い・読めない・知らない版の時は何も出しません。同じ文は Output の `doeff-runner` にも `[vsix の追随]` の行で出ます(agora-redesign #1043)。

## pixel art の icon

doeff の語(`defk`・`<-`・`Absent` など)・層・linter の違反と規則・service を、同じ絵で見分けるための icon の組です。絵柄は工業の生活感のある機械の sprite(端末・貨物の木箱・データのカートリッジ・ドローン・錨・エアロック・警報灯 — 継ぎはぎの板・ラベル・通気口・配線・ボルト・錆の染み)で、枠は付けません。色と絵柄は operator がくれた見本の絵に寄せています(版 3・2026-09-28)。

- **元の定義は `resources/pixel/glyphs.json` の 1 か所**です。icon ごとに名前・家族・一言の説明と、32×32 と 16×16 の点の格子(1 文字 = 1 点)を持ちます。画面に出す大きさは 16 css px と 8 css px で、1 css px に縦横 2 点ずつを詰めます(Retina の画面では 1 点 = 1 画素)。版 2(2026-09-28)で、版 1 の 16×16 と 8×8 の絵を拡大ではなく 2 倍の点で描き直しました。
  - 文字の意味: `.` は透明、`0`〜`9`・`a`〜`v` は色の組(`palette`)の番号、`L` は物に付いた小さな灯(消えている時は `lamps.off` の色、違反の重さで `lamps.error` / `warning` / `info` の色に灯る)、`A` / `B` は service の旗の模様の色です。
  - **色は `palette` の表 1 か所だけ**です(1 行 = 番号の文字・`#RRGGBB`・役の一言。今は見本の絵から採った 26 色 — からし色と黄土が主役、くすんだ青、錆と木、生成り、鋼の灰、地と影は黒でなく藍と炭)。格子は色を番号で参照するので、表を差し替えると全部の sprite・旗・拡張の icon の色が変わります。表に無い番号は読み込みで断ります。外周は `outline` の先頭の色(最も暗い藍)で縁取ります。
  - 拡張の一覧の icon(128×128)は、32×32 を拡大せず `extension.icon` の格子(今は 64×64 を 2 倍)を別に描きます。小さく出る所(16・32)は同じ題材の 32×32 と 16×16 の sprite(`extension.glyph`)です。
  - 絵は `"picture": "<名前>"` で別の icon の絵を使えます(違反の欄の規則の家族が、語の sprite を写さずに使うため)。参照の参照と、格子と参照の両方を書くことは断ります。
- **家族**(差し色と物の種類でそろえます):

  | 家族 | 語 |
  |---|---|
  | 宣言 | defk(端末)・defhandler(光の遮断の棒のゲート)・defeffect(アンテナ付きの封筒)・defrecord(貨物の木箱)・defwire(データのカートリッジ)・defsystem(宇宙港の星図)・deftest(検査のスキャナー)・law(天秤)・契約(`:pre` / `:post`)・`:tags`(荷札) |
  | 値と流れ | Program(テープ)・`<-`(括弧の受け口と矢印)・resume(再生のボタン)・finish(ゴールの旗)・Ask(問いの吹き出し)・effect の呼び出し(飛ぶ封筒) |
  | 失敗の語彙(赤と桃色の差し色) | Absent(空の貨物室)・Raise(信号弾)・Unreachable(外れた接続)・Refused(止まれの手)・Conflict(ぶつかる矢印)・Malformed(壊れたカートリッジ) |
  | 層 | core(ドームの家)・intent(立て札)・protocol(通訳のロボット)・foundation(係留の錨)・entry(エアロックの扉) |
  | linter の印 | error(赤い警報灯)・warning(琥珀の灯)・info(青い灯)・登録済み(工事中の三角コーン)・Jev の未判定(霧)・Jev(アンテナ付きのロボットのふくろう) |
  | linter の規則 | 違反の欄の行 — 置き場所(星図)・定義の書き方(判子を持ったロボット)・class(木箱)・JSON(カートリッジ)・臭い(匂いを嗅ぐドローン)・Jev(ふくろう)・層(積んだ床)・タグ(荷札)・生の副作用(錨)・名前(名札)・決まりと Python の規則(天秤)。Jev が判定した臭いと class は組の sprite |
  | service | 同じ形の旗 — 色 2 つと模様を service の名前の hash から選ぶ |
  | 拡張 | doe(手紙をくわえた雌鹿)と、状態バー用の doe の表情 |

- **違反の重さは灯で出します**: 定義の kind・層・規則の家族・service の旗の sprite は灯(`L`)を持ち、違反の最も重い重さの色(赤 = error・琥珀 = warning・青 = info)に灯します。違反が無ければ消えた灯(暗い灰)です。どの規則がどの家族か・規則の短い名は doeff-linter の出力(`rules` の `family`・`title`、契約の更新 6)が決め、拡張は写しを持ちません。
- **service の旗**: `serviceFlags.services` に並べた service の間で、同じ模様・同じ色の組が 0 組であることを生成の時に検算します。重なったら `serviceFlags.salt` を変えます。2 色は明るさの差が `minContrast` 以上の組だけから選びます。並べていない service の名前でも、拡張は同じ関数で同じ旗を作ります。
- **生成物**(元の定義から作り、commit します): `resources/pixel/svg/{32,16}/<名前>.svg`、`resources/pixel/png/{32,16}/<名前>.png`(2 倍)、icon 字体 `resources/pixel/doeff-icons.woff`(単色は黒と紺の点)、`package.json` の `contributes.icons`(`$(doeff-<名前>)` で書けます)、拡張の icon `icon.png`(128×128)と `icon.svg`、activity bar の単色の輪郭 `resources/pixel/activitybar.svg`(48×48 の点を 24 css px で)。
  - 生成物は手で書き換えません。`glyphs.json` を直して `npm run pixel` を実行します。
  - `npm run pixel:check` は、commit した生成物が元の定義と食い違えば終了コード 1 を返します。単体テストも同じ食い違いを赤にします。
  - `npm run pixel:preview` は、全 icon を 32×32・16×16・拡大で並べた見本の HTML を `out/pixel/preview.html` に書きます。`node scripts/build-pixel.js --preview <file> --lint-json <editor-json の file> --compare <前の glyphs.json>` で、違反の欄の見本(linter の実際の出力から)と前の版との並べ比べも足せます。

### 拡張の中での使い方

- **「違反(linter)」の欄**: 一番上に重大さ(CRITICAL・MAJOR・MINOR・INFO)ごとの件数の要約を出し、それぞれを「新しい分(登録簿に無い)・既知の分(登録簿に載った)・照合中」に分けます。重大さは repo が pyproject の `[tool.doeff-linter.rules.<ID>] level` で規則ごとに宣言し(無い規則は規則の重さから — error = major・warning = minor・info = info)、登録簿で波線の色を下げても重大さは下げません。その下に (重大さ, 規則) ごとの行を critical から並べ、見出しは「重大さ 件数 · 規則の番号 + 短い名」(例 `CRITICAL 3 · DOEFF126 defk を素で呼んで答えに使う`)、説明に新しい分と既知の分の数。欄の見出しのボタンで、重大さ(全部 → critical だけ → major 以上)と「新しい分だけ」を切り替えます。要約の行と状態バーには、前に VS Code を開いていた時の最後の数からの新しい分の増減も出ます。状態バーの `CRITICAL n(新しい m)` を押すと critical だけに絞った欄を開きます。law の名・ADR・規則の文は hover に出します。行の icon は規則の家族の sprite で、灯の色が重大さ(critical = 赤・major = 琥珀・minor = 消えた灯・info = 青)です。
- **「タグで閲覧」「層の地図」**: kind・層・service の sprite に、違反の最も重い重さの灯。
- **gutter(行の左端)**: 定義の行に kind の sprite(灯 = 定義の範囲の違反の重さ)、定義の外の違反の行に印の sprite。設定 `doeff-runner.pixel.gutter` で「層と service」(層の建物の右下に service の旗)・「出さない」に切り替えられます。
- **状態バー**: 今の file の違反で表情の変わる doe。
- 設定 `doeff-runner.pixel.treeIcons`・`doeff-runner.pixel.statusBar` で木と状態バーの pixel art を切れます(codicon に戻ります)。

### 決まった語の文字の置き換え

Hy の file の決まった語を、**表示の上でだけ** 小さい sprite(16×16 の点)に置き換えます。file の文字は変えないので、保存・検索・コピー・画面読み上げは元の文字のままです(画面読み上げは本文を読み、飾りの画は読みません)。

- 置き換える語(種類): def* の頭(`definition`)・`<-`(`bind`)・`:tags` の辞書(`tags` — 1 行に収まる辞書は丸ごと 1 つの荷札に畳む)・`:pre` / `:post`(`contract`)・effect の頭(`effect` — `Ask` は吹き出しに置き換え、宣言した effect は名前を残して前に手紙の印)・`defhandler` / `handle` の中の `resume` / `finish`(`handler`)・失敗の語彙(`failure`)。文字列・註・`import` の並びの中は置き換えません。
- カーソルの行と選んだ範囲の行は元の文字で見せます。見えている範囲だけに付けます。
- 置き換えた sprite の上の hover は、置き換える前の文字を一字一句そのまま(畳んだ `:tags` は辞書の全文)コピーできる code block で出し、その下に語の sprite と一言の説明を出します。
- 入り切り: 設定 `doeff-runner.pixel.replaceText`(全体)・`doeff-runner.pixel.replaceKinds`(種類ごと)、命令「doeff: 決まった語の icon の置き換えを入り切り」「doeff: icon に置き換える語の種類を選ぶ」。何を置き換えるかの決まりは `src/pixel/replace.ts` の 1 か所です。

### defk の見出しと束縛の型

defk / deff の型・effect・tags を、**読むだけの表示**として editor に描きます(file の文字は変えません)。材料は doeff-linter の editor-json の `signatures` と `bindings`(契約 版 2)で、型の読み方は linter の 1 か所にあり、拡張は描くだけです。

- 見出し: 頭の行 `(defk 名 [引数]` の名を太字にし、行に薄い帯と下の線を引き、行の末尾に tags の小さな丸い札を置きます。契約の辞書 `{:pre … :post … :effects … :tags …}` の文字は隠し、1 行目に型の行 `(dict, str) -> JsonAnswer`(Absent を起こしうる答えは `Maybe[B]`・引数の名は hover)を、2 行目に effect の行(装置の絵と名の札・`Raise X` の札)を描きます。linter の知らせ(宣言と推論の食い違いなど)は見出しに出さず、linter が違反の場所に出します。
- 定義へ飛ぶ: 型の名・effect の札・束縛の型の札を Cmd+クリック(と、その位置の F12)すると、その定義へ飛びます。組み込みの型(`dict`・`str` など)は飛びません。部品は隠した辞書の空白と括弧の上に 1 つずつ付けてあり、押された位置から部品を引きます。
- 束縛: `(<- x T e)` → `T x <- e`・`(val x e)` → `T x = e`・`(var x e)` → `var T x = e`・`(:= x v)` → `x := v`。型は editor の文字で、型ごとの色と薄い枠で描きます。型が分からない束縛は `?` です。
- hover に型の行の文・引数の名と型・型と effect の定義へ飛ぶ link・effect の答えの分け方(値 / Absent / Raise)を出します。カーソルが定義に入ると元の lisp を見せます(設定 `doeff-runner.defk.revealOnCursor`)。
- 入り切り: 設定 `doeff-runner.defk.header`・`doeff-runner.defk.bindingTypes`、命令「doeff: defk の見出し(型・effect・tags)の入り切り」。
- linter の出力に拡張の知らない語があっても出力は捨てず、その項目だけ一般の見た目にして、状態バーに「doeff: 拡張が古い」を出します。

### 呼びを f(a, b) の形で見せる表示

defk / deff の本体の呼びを Python に近い形で見せます(読むだけの表示・file の文字は変えません)。材料は editor-json の `rewrites` で、式の形を読むのは linter の 1 か所です。

- `(f a b)` → `f(a, b)`・`(f a :key v)` → `f(a, key=v)`・`(.get row "k")` → `row.get("k")`・`(get row "k")` → `row["k"]`・`(+ a b)` → `a + b`(優先順位が変わる所だけ括弧)。
- effect は `!` の印を残し、その前に effect の装置の小さな絵を添えます: `(val x (+ (! (f 0)) 1))` → `T x = !f(0) + 1`。effect の値を作る呼び `(Effect a)` も `Effect(a)` の前に絵。
- 制御の形(`when`・`if`・`match`・`for`)と知らない macro は lisp のまま。字下げは作り直しません。
- カーソルの行と選んだ範囲の行は元の lisp。置き換えた式の上の hover で元の lisp と、呼びの頭の定義への link・答えの型を見せます。名・引数は元の文字のまま残るので、定義へ飛ぶ機能もそのまま効きます。
- 入り切り: 設定 `doeff-runner.defk.callSyntax`、命令「doeff: 呼びを f(a, b) の形で見せる表示の入り切り」。

## Agentic Workflows

The extension integrates with `doeff-agentic` CLI for monitoring and managing agent-based workflows.

### Workflows Tree View

The **Workflows** view (in the doeff sidebar) displays:

```
DOEFF WORKFLOWS
├─ ● a3f8b2c: pr-review-main [blocked]
│   └─ review-agent (blocked)
├─ ○ b7e1d4f: pr-review-feat-x [running]
│   └─ fix-agent (running)
└─ ✓ c9a2e6d: data-pipeline [done]
```

- **Status indicators**: ○ running, ● blocked, ✓ completed, ✗ failed, ◻ stopped
- **Auto-refresh**: Tree updates every 5 seconds
- **Status bar**: Shows active workflow count (click to list workflows)

### Workflow Commands

- `Doeff: List Workflows` - Show workflow picker with actions
- `Doeff: Attach to Workflow` - Open terminal and attach to agent's tmux session
- `Doeff: Watch Workflow` - Open terminal with live status updates
- `Doeff: Stop Workflow` - Stop workflow and kill agent sessions

### Requirements

Workflow features require the `doeff-agentic` CLI. Install with:

```bash
cargo install doeff-agentic
```

## Commands

- `doeff-runner.runDefault`: Quick run with defaults
- `doeff-runner.runOptions`: Run with interpreter/Kleisli/transformer selection
- `doeff-runner.runConfig`: Launch a prepared selection payload (used internally from the quick pick)
- `doeff-runner.addToPlaylist`: Add a Program to a playlist
- `doeff-runner.pickProgram`: Pick a Program across all worktrees
- `doeff-runner.pickAndRun`: Pick and run a Program across all worktrees
- `doeff-runner.pickAndAddToPlaylist`: Pick a Program and add it to a playlist
- `doeff-runner.pickPlaylistItem`: Pick a playlist item (reveal)
- `doeff-runner.pickAndRunPlaylistItem`: Pick and run a playlist item
- `doeff-runner.listWorkflows`: Show workflow picker
- `doeff-runner.attachWorkflow`: Attach to workflow's agent tmux session
- `doeff-runner.watchWorkflow`: Watch workflow updates
- `doeff-runner.stopWorkflow`: Stop workflow and kill agents

## Development

```bash
npm install
npm run watch
```

Package with `npm run vscode:prepublish`.

VS Code を起動せずに走る単体テスト(Hy の解決の論理・playlist・worktree)は次で実行します。

```bash
npm run test:unit
```

compile の後、`out/test/**/*.test.js` を mocha の API で実行します(`scripts/run-unit-tests.js`。同梱の mocha の CLI は Node 22 以降で起動できないため)。fixture は `test-fixtures/` にあります。
# 文書検査の差分更新（0.8.0）

文書検査は初回と明示的な再実行だけ workspace 全体を読みます。編集・作成・削除では、
変更したファイルと用語定義の影響先だけを再検査します。本文が同じ再通知では linter を
起動しません。進行中の検査は編集で中断せず、変更をまとめて次に処理します。
Rust 製の `doc-linter` 0.4.0 以降が必要です。

文書検査パネルは既存の違反パネルと同じ「ルール → ファイル → 指摘」の表示です。
進捗だけが変わった時には指摘の木を再生成せず、展開状態を維持します。
進捗に「差分」と表示される場合、件数は今回の変更対象だけです。未変更の指摘は保持します。
