> **この文書は途中で止めた下書きです(2026-09-21 14:1x JST)。**
> 依頼 `lt-YB707M0QM8WD4WPDDGG91CJQ33` が二重投函のため取り下げになり、設計の検証
> (盲検の反例生成 A/B・反例の再現)は**一度も走らせていません**。レビューも受けていません。
> 確かなのは `measurements.md` の実測だけで、この文書の設計・採否・強制方法は未検証の案です。
> 生きている依頼 `lt-HZRYH9ST368YKXR2T1Z2W9ZKJ9`(担い手 c-AJ8C0BK9RF29HQ92ZQ986FXQVT)が
> 参考にする場合も、結論としては扱わないでください。

> **⚠ 2026-09-21 14:3x 追補 — この下書きの推し(案 A = 設定ホームへ symlink)は起動口によっては成り立ちません。**
> user 層の規則 file が symlink / hard link のときに黙って落ちる門が本体に在り、`CLAUDE_CODE_ENTRYPOINT=local-agent` の席で当たります(`@` の取り込みも同じ門で落ちます)。
> 綴りと読み方は `measurements.md` の **追補 M10**。案の比較(§の 3 案)はこの事実の前では未更新です。

# agentd が起こす claude セッションへ共通 CLAUDE.md と skills を届ける設計

- 対象: doeff `origin/main` 57641077306214b7b7da86708b04e9ed0524499b(2026-09-21)
- 計画段の成果物(コードは変更していない)。実装は別依頼。
- 追跡カード: `acp:kanban-issue:ki-62aa1f4e9c9c`(ボード agora-redesign)
- 設計した会話: c-EK11A9R4Y986YA8JQWTEQ3WNXZ / 2026-09-21 JST / 会社 Mac CA-20038667

## 1. 受入条件(依頼から固定)

1. pod のセッションと Mac のセッションの両方で、共通の CLAUDE.md が **user 層(利用者レベルの
   グローバル指示)** として context に載る。
2. 同じ両方で、dotfiles の skill が「使える skill の一覧」に在る。
3. 陽性対照: 作業ディレクトリを `$HOME` の外にしたセッションでも 1 と 2 が成り立つ。
4. もれなさの検査: 宿を列挙せずに導く(参加宣言を持つ宣言ファイル全部が正本を名指す形)。
   3 台目の宿を足した日に書き忘れれば赤くなること。
5. 確かめるのは実際の agentd のセッション。mock では受入にならない。

## 2. 実測(据わっている claude 2.1.278 から測った)

計測はすべて `/Users/s22625/.local/share/claude/versions/2.1.278`(`claude --version` = 2.1.278)に対して、
agentd と同じ形(`CLAUDE_CODE_OAUTH_TOKEN` + `CLAUDE_CONFIG_DIR`)で実行した。記録 = `measurements.md`。

| # | 測ったこと | 結果 |
|---|---|---|
| M1 | `$CLAUDE_CONFIG_DIR/CLAUDE.md` はどの層で読まれるか(cwd は `$HOME` の外) | **user 層**。context のラベルは逐語 `Contents of /tmp/ccx/cfg/CLAUDE.md (user's private global instructions for all projects):` |
| M2 | `$CLAUDE_CONFIG_DIR/skills/<名>/SKILL.md` は使える skill の一覧に入るか | **入る**(probe skill が一覧の先頭に出た) |
| M3 | 設定ホーム配下の **symlink** は効くか(ファイルとディレクトリの両方) | **効く**。`CLAUDE.md` → dotfiles の正本ファイル、`skills` → `~/dotfiles/agent/skills` のディレクトリ symlink で、一覧は 129 件(組み込み 12 + dotfiles 117)、`agora-artifact` / `coupling-core` / `model-routing` すべて在り |
| M4 | 二重読みは起きるか(cwd が `$HOME` の下) | **symlink なら起きない / 実体コピーなら起きる**。symlink 版は user 層 1 件だけ。コピー版は user 層 `/tmp/ccx/cfg3/CLAUDE.md` と project 層 `/Users/s22625/.claude/CLAUDE.md` の **2 件**が載り、本文が逐語で重複した(64KB × 2)。⇒ 重複排除は **解決後のファイル実体**で行われている |
| M7 | 走っているセッションの途中でホームの中身を変えたら効くか | **効かない**。1 つのプロセスを保ったまま CLAUDE.md と skill を足しても、その後のターンでは user 層なし・skill 数 12 のまま。⇒ 読みは**プロセス起動時の 1 回**。次の起動(launch / resume)から効く |
| M8 | `--plugin-dir ~/dotfiles/agent/skills` で skills を渡せるか | **渡せない**(一覧 13 = 組み込みのみ)。そのディレクトリ自身はプラグインの形ではないため |
| M8b | `--plugin-dir ~/dotfiles/agent`(`skills/` を含む親)なら | **渡せる**(一覧 129・dotfiles の skill 在り)。ただし user 層の CLAUDE.md を指す引数・設定は存在しない |
| M9 | 壊れた symlink(リンク先が無い)を置いたセッションは立つか | **立つ**。異常終了せず、一覧は組み込み 12 件のまま。⇒ pod が古い checkout に戻って立つ日も、セッションは止まらない |

補足(M7 の誤検出を 1 度作ってから直した): 最初の計測では質問文の中に目印の文字列を書いてしまい、
モデルが「context に在る」と答えた。目印を質問文から外して測り直した結果が上表の M7。

### 実測から決まること

- **設定ホーム(`CLAUDE_CONFIG_DIR`)に置けば、CLAUDE.md は user 層で載り、skills は一覧に入る。**
  これが今日ちょうど欠けているもので、`$HOME/.claude` は読まれない(設定ホームが別の場所を指すため)。
- **symlink が最良**: 117 本を写さずに済み、Mac で今日偶然届いている project 層の読みと同じ実体を指すので
  重複して載らない。実体コピーは Mac で 64KB を 2 度載せる。
- **引数で渡す道は skills にしか使えない。** user 層の CLAUDE.md を指す引数・設定キーは本体に無い
  (`--add-dir` は project 層・`--append-system-prompt` は CLAUDE.md ではない)。
- **既に在るホームの後追いは要らない**: 読みは起動時の 1 回なので、起動のたびにホームを整えれば
  既存の 31 ホームは次に使われた拍で直る。走っているセッションには触れない(排水も再描画も不要)。

## 3. 設計

### 3.1 責務の分担(今日の hook の届け方と同じ形)

| 責務 | 所有者 | 持つ知識 | 隠す知識 |
|---|---|---|---|
| 中身(共通 CLAUDE.md と skills の本文) | dotfiles | 文書と skill の内容 | どのホストがどこに置くか・claude の読み口 |
| 宿ごとの差(どの絶対パスが正本か) | 各ホストの参加宣言(dotfiles の `cron_management/*.toml` と ACP の ConfigMap) | そのホストでの絶対パス | claude の設定ホームの構造 |
| 据え付け(設定ホームへ置く) | doeff sessionhost | 設定ホームの構造・どのエントリ名を自分が所有するか | 中身・ホストごとのパス |
| 読み口(user 層 / skills 一覧) | claude 本体 | — | — |

これは設計カード `ki-f10ffeb77cab` が hook について決めた分担そのままで、第 2 の読み口を作らない。
dotfiles 側からホームへ外から書く仕組み(プロビジョナ・定期の調停)は、カードが案 B として既に退けている。

### 3.2 宣言 → 参加 → 環境変数 → 起動 → 据え付け の連鎖

`claude_settings_file` と同じ 1 本の連鎖に乗せる。新しい経路を作らない。

```
参加宣言 [agentd].claude_user_instructions_file / claude_skills_dir   (宿ごとの差はここだけ)
  → acp/join.hy  claude-home-links-of         (形の門: 絶対パスか ~/… ・相対は断る)
  → acp/effects.py  DOEFF_AGENTD_CLAUDE_USER_INSTRUCTIONS_FILE / DOEFF_AGENTD_CLAUDE_SKILLS_DIR
  → launch.hy  claude-home-links-declaration  (起動の拍ごとに環境変数から読む)
  → impls/claude_code.hy  claude-pre-launch-setup  (設定ホームへ symlink を張る)
```

- 判断(何を張るか・どの名前を所有するか)は **doeff の 1 か所**。
  `impls/claude_code.hy` に対応表を置く:

  ```hy
  ;; 設定ホームの中で doeff が所有するエントリ(宣言キー → ホームのエントリ名)。
  ;; 綴りの定義点はこの 1 か所。参加の門も起動の据え付けもここを読む。
  (setv CLAUDE-HOME-LINKED-ENTRIES
        {"claude_user_instructions_file" "CLAUDE.md"
         "claude_skills_dir"             "skills"})
  ```

  `CLAUDE-SETTINGS-OWNED-KEYS` が `--settings` の鍵について持っているのと同じ役目を、
  ホームのエントリ名について持つ。semgrep が第 2 の綴りを禁じる(3.5)。

- 参加の門は `claude_settings_file` と**同じ 3 つの断り方**を持つ:
  - 相対パス(cwd に依る)は断る = 参加しない。
  - リンク先が無い日は**断らない**。名乗って参加する(pod の正規の degrade を全席停止に化けさせない
    — R13 の訂正と同じ理由)。名乗りは起動ごとの 1 行と node の行のラベル。
  - 宣言しないホストは今日どおり(引数もホームも 1 バイトも変わらない)。

### 3.3 据え付けの意味論(新しい substrate effect 1 つ)

`impls/claude_code.hy` は生 IO 禁止(Fs* / EnvGet の effect だけ)なので、symlink を張る effect を 1 つ足す。
既存の `FsLinkArtifact` は会話成果物の移植用で、リンク先が無ければ触らない・別実体なら触らないという
別の契約(ADR-DOE-AGENTS-006 R7)なので、そちらの意味を広げない。

新 effect `FsLinkManagedEntry(source-path, target-path)` の閉じた戻り値:

| 戻り値 | 状況 | 動作 |
|---|---|---|
| `linked` | エントリが無い | 親を mkdir して symlink を張る |
| `same-target` | 既に symlink で、リンク先の綴りが同じ | 何もしない |
| `relinked` | 既に symlink だが、リンク先の綴りが違う | 一時名へ symlink → rename で置き換える(不可分) |
| `foreign-entry` | 既に在るが symlink ではない(実体のファイル/ディレクトリ) | **触らない**・呼び手が名乗る |

- リンク先の実在は張る条件にしない(壊れた symlink は M9 で無害と実測済み)。実在の観測は別に
  `fs-file-exists` / `FsDirExists` で 1 回行い、`seat-claude-md-target-absent` /
  `seat-skills-target-absent` の名前つき 1 行にする。`seat-settings-file-absent` と同じ流儀。
- `relinked` が要る理由: 宣言のパスが変わった日(pod のホームの綴りが変わる・checkout の置場を変える)に、
  既存ホームが古いリンクを抱えたまま黙って残るのを防ぐ。doeff が所有する形(symlink)のときだけ直し、
  他人が置いた実体は触らない。
- 冪等。同じホームを共有する複数セッションが同時に起動しても、rename が不可分なので壊れない。

### 3.4 据え付けの位置

`claude-pre-launch-setup`(`PreLaunchSetup` の claude 実装)の中、記憶の置き場を作る処理の隣。

- この処理は **fresh launch と resume / fork の両方が通る**(launch.hy の 1 点を再利用している)。
- 実測 M7 より、読みはプロセス起動時の 1 回なので、ここで張れば「そのプロセスが読む前」に間に合う。
- 既存の 31 ホームは次に使われた拍で直る。**後追いの仕組み・定期の調停・agentd の再描画は要らない。**
- 走っているセッションには影響しない(ファイルを足すだけ・置き換えは rename)。

### 3.5 静的な強制

| 守る責務 | 強制方法 | 実装箇所 |
|---|---|---|
| ホームのエントリ名の綴りは 1 か所 | semgrep 規則: `impls/claude_code.hy` の外で文字列 `"skills"` をホームのパス組み立てに使うこと、および `CLAUDE-HOME-LINKED-ENTRIES` の外での `CLAUDE.md` のホーム結合を禁じる(既存の `autoMemoryDirectory` 規則と同じ形) | `.semgrep.yaml` + `docs/adr/enforcement-ledger.json` |
| 判断と IO の分離 | `impls/claude_code.hy` は substrate-clean(生 IO 禁止・既存 defsemgrep が執行)。新 effect も Fs* として足す | 既存規則をそのまま適用 |
| 参加宣言のもれなさ | dotfiles の検査を宿を列挙せず `[agentd-join.agentd]` を持つ宣言から導く(既存 `test_every_seat_starting_declaration_names_the_seat_settings_file` と同じ作り) | `agent/tests/test_acp_single_mac.py` |
| 宣言しないホストの引数不変 | 既存の「欄の無い手番の引数は 1 バイトも変わらない」pin を、ホームの据え付けにも拡張(宣言が無ければ `FsLinkManagedEntry` を 1 度も出さない)という単体検査 | doeff の deftests |
| 断り方の閉じた語彙 | `FsLinkManagedEntry` の戻り値を閉じた集合にし、呼び手側で網羅的に分岐する | doeff の deftests |

### 3.6 比べた案と採否

| 案 | 採否 | 理由 |
|---|---|---|
| **A. 設定ホームへ symlink を張る(採用)** | 採用 | M1/M2 で読み口が設定ホームだと測れている。M3 で symlink が効く。M4 で Mac の二重読みを避けられるのは symlink だけ。117 本を写さない。正本が動けば次に起動するセッションから効く。M7 より既存ホームの後追いが要らない |
| B. 起動の引数 / settings のキーで置き場を指す | 退ける | skills は `--plugin-dir <skills/ を含む親>` で渡せる(M8b)が、**user 層の CLAUDE.md を指す口が本体に無い**(M8 / help の全数確認)。CLAUDE.md がホーム経由になる以上、skills だけ引数にすると定義点が 2 つに割れる。引数はセッションごとに増える一方、ホームは 1 度整えれば済む |
| C. charter が中身を運んで doeff がホームへ書く(`memory_files` と同じ形) | 退ける | skills 117 本(数 MB)を毎起動で行に載せるのは重すぎる。CLAUDE.md だけでも 64KB を毎起動運ぶ。さらに M4 より Mac では実体コピーが project 層の読みと重複して 2 度載る。`memory_files` が正しいのは「会話ごとに違う・行が正本」の記憶で、この件は「全席で同じ・dotfiles が正本」なので形が違う |
| D. dotfiles 側からホームへ外から書く(プロビジョナ・定期の調停) | 退ける(カードが案 B として既に退けている) | ホームは doeff が作る。外から書くと据え付けの口が 2 つになり、どちらが正しいかが決まらない |

**採用案は戻せる**(two-way door): 宣言の 2 行を消して宿の宣言を描き直せば、引数もホームの新規エントリも
増えない状態に戻る。既に張った symlink が残る場合は、その 2 エントリを消すだけで今日の姿に戻る。

### 3.7 宿ごとの値(旧側の値を運ぶ)

| 宿 | `claude_user_instructions_file` | `claude_skills_dir` |
|---|---|---|
| 会社 Mac(`cron_management/acp-single-mac.toml`) | `${home}/dotfiles/claude/CLAUDE.md` | `${home}/dotfiles/agent/skills` |
| 個人 MacBook(`cron_management/acp-proboscis-mbp.toml`) | 同上 | 同上 |
| pod(ACP `deploy/acp-control/agentd-pool-join.yaml`) | `/home/kento/dotfiles/claude/CLAUDE.md` | `/home/kento/dotfiles/agent/skills` |

- Mac の `${home}/dotfiles/claude/CLAUDE.md` は、今日 `$HOME/.claude/CLAUDE.md` が指している実体そのもの。
  ⇒ 旧側の値(Mac で偶然届いていた中身)がそのまま新しい経路で届き、M4 の重複排除も効く。
- skills の正本は 117 本(`~/dotfiles/agent/skills`)。pod は同じ構造の別パス(init が clone する checkout)。
- **pod の宣言は ACP リポジトリに在る。** 受入 1 が pod を含むので、実装は
  doeff / dotfiles / agent-control-plane の 3 リポジトリにまたがる(依頼のタグは 2 つだったが、
  pod を外すと受入が満たせないため実装依頼には 3 つ目を足す)。

## 4. 変更シナリオ(6 軸)

| 軸 | 適用 | シナリオ | 変わる責務 | 変わらない公開契約 |
|---|---|---|---|---|
| hardware | 適用 | 4 台目の宿(別アーキテクチャの機体)を足す | その宿の参加宣言 1 ファイルだけ | doeff の据え付け・dotfiles の中身。もれなさの検査が書き忘れを赤くする |
| storage | 適用 | 正本の置場を変える(`~/dotfiles` → 別の checkout) | 各宿の宣言の値 | doeff は綴りを知らない。既存ホームは `relinked` で追随する |
| effects | 適用 | ホームへ据えるエントリを増やす(`commands/` など) | `CLAUDE-HOME-LINKED-ENTRIES` の 1 行 + 宣言キー 1 つ | effect の契約・門・据え付けの位置 |
| distribution | 適用 | pod を複数プール・複数 namespace へ広げる | ConfigMap の値 | doeff・dotfiles は不変 |
| concurrency | 適用 | 同じホームを共有する複数セッションが同時に起動する | なし(rename が不可分・冪等) | 走っているセッションは起動時に読んだ内容のまま(M7) |
| simulation | 適用 | 決定的な検査で据え付けを再現する | なし。effect なので fake handler で観測できる | 判断(何を張るか)は純関数側に在る |

## 5. 受入(実装段に書き下したもの)

1. 会社 Mac の実 agentd セッション・pod の実 agentd セッションの両方で、
   context に共通 CLAUDE.md が **`user's private global instructions for all projects`** の
   ラベルで載る(ラベルの逐語を証拠に含める)。
2. 同じ両方で、使える skill の一覧に dotfiles の skill(`agora-artifact` / `model-routing` /
   `coupling-core` の 3 本を名指しで)が在る。
3. 陽性対照: 作業ディレクトリが `$HOME` の外のセッションでも 1 と 2 が成り立つ。
4. Mac の陰性対照: 同じ本文が user 層と project 層で 2 度載っていないこと。
5. node の行に据え付けの名乗りが出る(在り / 不在 の 2 語)。不在の日も席は立つ。
6. dotfiles の検査が、`[agentd-join.agentd]` を持つ宣言ファイルを**列挙せず**走査して、
   2 つの新キーを要求する。キーを書かない宣言ファイルを足すと赤くなる。
7. 宣言しないホストでは起動の引数もホームの中身も今日と同一。
8. 触ったファイルの焦点の検査だけを 1 分以内で回す。全数・全体検証は日次に任せる。

## 6. 未確認

- pod の実セッションでの実測は、実装が pod へ配られた後でしか測れない(この計画段では Mac の
  claude 2.1.278 で測った。pod の claude も同じ版であることは実装段が node の行で確かめる)。
- `relinked` が実際に必要になるのは宣言のパスが変わった日だけで、今日その日は来ていない。
