# 実装依頼書 — agentd の席の家へ共通の CLAUDE.md と skills を運ぶ

- 親の依頼(計画段): `lt-HZRYH9ST368YKXR2T1Z2W9ZKJ9`(class investigate)
- card: `acp:kanban-issue:ki-62aa1f4e9c9c`(盤 agora-redesign)
- 設計本文: 同 dir の `design.md`(実射の記録 = `evidence/`・実行モデル = `model/`)
- 基準 commit: doeff `57641077306214b7b7da86708b04e9ed0524499b`
- 触る repo: **doeff**(本体)・**dotfiles**(宣言 2 枚 + 針 + 計器)・
  **agent-control-plane**(pod の宣言 1 枚)

> ⚠ この依頼書だけで実装できるように書いてある。迷ったら `design.md` の §2(実射)と
> §3(選んだ理由と退けた理由)を読むこと。**「symlink で足りるのでは」は実射で潰してある**(§2.3)。
>
> ⚠ **この依頼書は盲検 A・B の反例を受けた改訂(D8–D10)を含む**。反例の再現と修正の根拠は
> `counterexamples.md`(正本)と `design.md` §10。設計本文の §3.2 / §3.5 / §6 は §10 で
> 改められているので、**§10 を先に読むこと**。

---

## 1. 結合核の突合の結論(依頼書 §6)

機械で照合した(`evidence/coupling_core_match.py` / `.log`)。

- 名簿: dotfiles `docs/coupling-core-watchlist.md` の `coupling-core-paths`(80 pattern)と
  `coupling-core-fleet-paths`(1 pattern)。
- 触ると宣言した 23 path(doeff 17 / dotfiles 5 / ACP 1)を突き合わせて **当たり 0 件**。
  (盲検 A の反例を受けて増えた 6 本 — `effects.hy` / `substrate.hy` / `effects.pyi` /
  `sessionhost_launch_deftests.hy` / `sessionhost_substrate_deftests.hy` /
  `conformance/test_s12_claude_trust_preseed.py` — を足して**撃ち直した**結果。)
- doeff はこの repo 固有の名簿を持たないので艦隊の区画だけで数えられ、その唯一の pattern
  `.agents/land-queue.toml` をこの実装は触らない ⇒ **doeff 側も 0 件**。
- ⇒ **核に触る便の routing(frontier + human で法・反例・テスト・実装を 1 まとめ)は掛からない。**
  ただし下の §4 の TDD + semgrep の規律は核の有無と無関係に守る。
- ⚠ 名簿は「多めに出して人が落とす」向きなので、**実装で触る path が上の 23 本から増えたら
  もう一度撃つこと**(`python3 evidence/coupling_core_match.py` の `TOUCHED` に足して再実行)。

---

## 2. 確定した決定

| # | 決定 | 戻し方 |
| --- | --- | --- |
| D1 | 家への据え付けは**起動の拍ごと**(鋳造の拍ではない)。座は `impls/claude_code.hy` の `claude-pre-launch` = 家へ書く既存の 1 か所 | 宣言の鍵の行を消す |
| D2 | **CLAUDE.md は実体 file**(`fs-write-text-atomic`)。symlink / hard link にしない | — (実射が決めている・§2.3) |
| D3 | **skills は whole-dir symlink**。既に正しい先を指していれば張り替えない | — (同上) |
| D4 | 正本の在り処は**機体の参加宣言の 2 鍵**が名指す。連鎖は `claude_settings_file` と同じ 1 本。第 2 の読み口を作らない | 鍵の行を消す |
| D5 | 名指しが在って file / dir が無いのは**非致命**(参加も起動も断らない・名乗りの 1 行 + node の行の labels) | — |
| D6 | 二重読みは doeff が置く 3 つ目の settings の鍵 `claudeMdExcludes` で落とす。値は席の `$HOME` から**導く** 1 本 `<$HOME>/.claude/CLAUDE.md` | 鍵を置く 1 行を外す |
| D7 | 既存の家の後追いは**作らない**(D1 により次の起動で直る)。家を作り直さない・reconciler を足さない | — |
| D8 | **張り替えの動詞を substrate に持つ**: `FsEnsureSymlink` を effect として足し、3 値 `unchanged` / `linked` / `occupied-by-real-entity` を返す。`impls` は**この動詞を呼ぶだけ**(盲検 A・`design.md` §10 R1) | effect ごと消す(呼び手は 1 か所) |
| D9 | **`FsWriteTextAtomic` の tmp を書き手ごとに一意にする**(`mkstemp`)。同じ家へ 2 席が同拍で書いても互いの tmp を消さない(盲検 A・§10 R2)。既存の `preseed-claude-trust` の競りも同時に閉じる | 戻すと既存の競りが開く ⇒ 戻さない |
| D10 | **「宣言していない path を触っていないか」を見る**: `packages/doeff-agents/src/**` で literal `dotfiles` を **除外なしで**禁じる semgrep 規則 + 痕跡で見る deftest 2 本(盲検 B・§10 R3) | 規則の行を消す(ledger も同拍で) |
| D11 | **運ぶ物の綴りは 1 つの名簿**(`CARRIED-INSTRUCTION-SOURCES` — 1 種 = `{key, env, param, kind, home-name, label}` の 1 行)。`AGENTD-KEYS` はそこから**導く**。join / launch / 据え付けは名簿を**回る**(§10 R5) | 名簿を平らに戻す(が、d8472e1a の形へ戻ることになる) |

> D8/D9 は **S1 の事前予測「doeff 側の substrate は 1 行も変わらない」を改めている**。
> この実装では substrate の語彙が 1 語増える(`design.md` §10 R1 に訂正を明記済み)。
>
> D11 は **設計者が事前に自分で登記していた弱点**(「欄を 1 つ足す操作が名簿を 4 枚触らせる
> 形にしない」= d8472e1a の教訓)が、盲検後の S2 の実測で**初版に成立していた**ための修正。
> 実測 = 3 種目を足す変更が `model/chain.py` の **追加 1 行 / 削除 0 行**に収まり、回る側の
> 3 関数の bytecode が不変(`counterexamples/model_runs.log` §4)。

---

## 3. 未確定事項(実装が最初に決める)

1. **pod の正本の path**。pod の agentd の容器の中で共通の条文と skills がどこに在るかは
   計画段で確かめられていない。**手順 0** で実射し、無ければ「据える口を 1 つ決める」が
   最初の判断になる(容器の image に焼く / volume で mount / 入口で clone のどれか)。
   ⇒ 決めたら card のスレッドへ記録する。
2. **node の行の labels の綴り**。`seat-settings` に倣って `seat-memory` / `seat-skills` を
   提案するが、`judgment.node-labels-of` の既存の語彙と衝突しないかは実装が見て決める。
3. **`AgentdSettings` の欄名**。`claude_settings_file` / `claude_settings_file_present` に
   倣う(`claude_memory_file` / `_present`・`claude_skills_dir` / `_present`)。
4. **`FsEnsureSymlink` の綴り**(D8)。`FsLinkArtifact` の隣に置き、3 値の名は
   `unchanged` / `linked` / `occupied-by-real-entity` を提案する。既存の
   `_ensure-view-symlink`(`substrate.hy:295`)は**この effect の handler へ畳む**か、
   handler から呼ぶ。どちらにするかは実装が見て決める(`FsComposeHomeView` の振る舞いを
   1 byte も変えないことだけが条件)。

---

## 4. 手順(TDD + semgrep — CLAUDE.md の規律)

### 手順 0(測る・code を変えない)

pod の agentd の容器に入り、共通の条文と skills の在り処を実射で確かめる。結果を card へ。

### Phase 1 — 落ちる検を先に置いて commit

1. **doeff** `packages/doeff-agents/tests/sessionhost_seat_home_instructions_deftests.hy`(新規)
   - (a) 宣言 → env → 起動の拍 → **家の中の file** まで通し、`<家>/CLAUDE.md` が
     **実体 file(symlink でない・nlink 1)**で中身が正本と一致することを撃つ。
   - (b) `<家>/skills` が正本を指す symlink で、その下の `SKILL.md` が読めることを撃つ。
   - (c) 2 度目の起動の拍で symlink を**張り替えない**(inode が同じ)。
   - (d) 名指した file / dir が無い拍は**起きる**(argv が出る)+ 名乗りの 1 行が出る。
   - (e) 名指しの無い機体の家は 1 byte も変わらない。
   - (f) **argv の出口**を見る: `--settings` に現に出る JSON が `claudeMdExcludes` に
     `<$HOME>/.claude/CLAUDE.md` ちょうど 1 本を持つ(導出点の戻りを見ない —
     `autocompact-arg-pair-ok` と同じ向き)。
   - (g) 宣言 file が `claudeMdExcludes` を持つと参加の門 (c) が断る。
   - (h) **【D10・盲検 B】宣言が無い機体の拍で、席へ条文が 1 byte も届かない**。
     世界に `HOME` を据え、disk に囮(`<HOME>/dotfiles/claude/CLAUDE.md` など)を置いた上で
     撃つ。囮が無いと「無いから届かない」だけが証明され、**弁別力が無い**。
   - (i) **【D10・盲検 B】劣化の日に、宣言が名指していない path を器が触らない**。
     条件 = **宣言は在る・名指された file は無い**(= 劣化の日)。`Fs*` の痕跡を採り、
     読んだ path が宣言の名指し以外を含んだら赤。
     ⚠ 平常日に置くと候補の列が宣言の path で短絡するので**違反が通る**(実測済み・
     `counterexamples.md` の「自分の検に弁別力が無かった」)。**必ず劣化の日に置く**。
   - (j) **【D8】2 席が同じ家へ同拍で入っても、家の `skills` は正しい先を指し、
     互いの tmp を消さない**(`FsEnsureSymlink` の 3 値 + D9 の一意 tmp)。
   - (k) **【D11】母集団を名簿から導く**: `CARRIED-INSTRUCTION-SOURCES` を回り、各 1 種に
     ついて「宣言 → env → params → 家の中の名」まで届くことを撃つ。**列挙しない** ⇒
     名簿に 1 行足して行き先を宣言しなければ赤くなる。
     加えて「名簿に無い鍵は参加が断る」(`declared-values-of` の fail-closed)も 1 本。
   - ⚠ 母集団は**反射で採る**(`sessionhost_charter_reaches_the_seat_deftests.hy` の流儀)。
     新しい運び物を足して行き先を宣言しなければ赤くなる形にする。
2. **dotfiles** `agent/tests/test_acp_single_mac.py::test_every_seat_starting_declaration_names_the_common_instruction_sources`(新規)
   - 母集団を**列挙せず**導く: `cron_management/*.toml` のうち `[agentd-join.agentd]` を持つ file 全部。
   - 各 file が `claude_memory_file` と `claude_skills_dir` を名指していること + 名指す先が
     この checkout に在ること。既存の `…names_the_seat_settings_file` と同じ作り。
3. **dotfiles** `agent/tests/check_native_claude_home_contract.py`(新規)+ その検
   - 据わっている本体の実行体から `design.md` §2 の逐語 4 本を読む 3 値の計器
     (同定不能 = 棄権 / 揃う = 緑 / 動いた = 赤)。`MACHINE_BOUND` を宣言し、
     **tree の層では走らせない**(`check_native_worktree_contract.py` の頭注を写す)。
   - 読む逐語(`evidence/body_contract.log` に実物が在る):
     `case"User":return Ke(Se(),"CLAUDE.md")` /
     `if(t==="User"&&!v)try{…isSymbolicLink()||(q.nlink??1)>1&&q.isFile())return[]}` /
     `function wgr(){return CN()!=="local-agent"}` /
     `let r=Ah(Se(),"skills"),o=Ah(HS(),".claude","skills")` /
     `if(!N.isDirectory()&&!N.isSymbolicLink())return null;` /
     `function Sgr(e,t){if(t!=="User"&&t!=="Project"&&t!=="Local")return!1;`
   - ✅ **雛形が在る**: `model/check_body_contract.py`(3 値・塊読み・逐語 6 本)。
     据わっている本体で **green**、逐語を 1 つ動かした複製で **red**、本体でない file で
     **abstain** まで実測済み(`counterexamples/model_runs.log` §4・§5)。**写して使う**。
   - ⚠ **追補(2026-09-21T07:01Z 着地・依頼者 c-3JYBNJMC2RZTM1S8V43939MP42 が入れた。出典 = 発注者
     c-AJ8C0BK9RF29HQ92ZQ986FXQVT の郵便 lt-C22T2AV9VGX520WYFCEB0Q8X8B の自認)**: 上の逐語 6 本は
     **版で変わる縮めた識別子を含む**ので、綴りのまま写すと別の版の機体で**偽の赤**になる。
     雛形が pin しているのは 2.1.263(pod)で、実装の席は会社 Mac(**2.1.278**)。
     - 発注者の実測: 2.1.263 の `wgr` / `CN` は 2.1.278 で `KRt` / `mB`。
     - 依頼者の実測(pod 2.1.263・2026-09-21T07:01Z): 雛形をそのまま撃って **green** ⇒ 陽性対照は生きている。
       赤は本来「契約が動いた」の合図だが、この pin では**版が違うだけ**でも出る。
     - 依頼者が数えた射程: **6 本すべて**が縮めた名に依る(`Ke`/`Se`・`ae`・`wgr`/`CN`・
       `Ah`/`Se`/`HS`・`N`・`Sgr`)。しかも**局所変数の名も動く** — 別の担い手
       c-EK11A9R4Y986YA8JQWTEQ3WNXZ が 2.1.278 から写した同じ枝は
       `if(n==="User"&&!O){const st=await lstat(e);if(g===0&&st.isSymbolicLink()||(st.nlink??1)>1&&st.isFile())return[]}`
       で、2.1.263 の `t`/`v`/`q`/`d` が `n`/`O`/`st`/`g` に替わっている(その写しは空白が整形
       されているので byte 逐語ではない — 読むのは**名の対応だけ**)。
     ⇒ **関数名だけを `\w+` にしても足りない**。識別子の位置をすべて形で pin し、綴りで固定するのは
     製品の公開の語(`"User"` / `"Project"` / `"Local"` / `"CLAUDE.md"` / `"skills"` /
     `"local-agent"` / `.isSymbolicLink()` / `nlink`)に限ること。弁別(逐語を 1 つ動かした複製で
     **red**)は**会社 Mac の版で撃ち直して**から緑を名乗る。
   - ⚠ **これがこの設計の唯一の witness**。これが無いと「実体 file でなければならない」は
     この repo の木をいくら読んでも反証できない条になる(法 `contract-of-an-external-tool-
     assumed-without-a-witness` の形)。

**この段で撃って赤が出ること**を確かめてから commit する。

### Phase 2 — semgrep の規則(旧い形を永久に禁じる)

`.semgrep.yaml` に ERROR 2 本(既存の `autoMemoryDirectory` 規則と同形・`paths.exclude` で
綴りの家だけ通す):

- `claudeMdExcludes` の第 2 の綴りを禁じる(定義点 = `impls/claude_code.hy`)。
- `<家>/CLAUDE.md` / `<家>/skills` を**この層の外**で組むことを禁じる
  (家の綴りの第 2 の座を作らない)。
- **【D10・盲検 B】`packages/doeff-agents/src/**` で literal `dotfiles` を禁じる(ERROR)。**
  ⚠ **`paths.exclude` を付けない**。既存の `…-auto-memory-dir-spelling-has-one-home` は
  `impls/claude_code.hy` を除外しているが、この規則は**除外すると意味が消える**
  (禁じたい書き込みがちょうどその file で起きる)。正本の綴りは**宣言と env が運ぶ**ので、
  doeff の木に `dotfiles` の 8 文字が現れる正当な理由は無い。
  実射: `counterexamples/verify_B_semgrep.sh`(実物の `.semgrep.yaml` で
  「違反 1 件 → 消すと 0 件」まで確かめてある)。

規則を書いたら**現在の code に撃って発火の仕方を確かめる**。
`docs/adr/enforcement-ledger.json` を**同じ commit で**上げる(ADR-DOE-ENFORCE-001 R5)。

### Phase 3 — 実装(doeff)

| file | 足すもの |
| --- | --- |
| `acp/effects.py` | **【D11】** `CarriedSource` と名簿 `CARRIED_INSTRUCTION_SOURCES`(= 綴りの定義点。env 鍵・宣言の鍵・params の欄・家の中の名・名乗りの語を **1 行**に持つ)+ `AgentdSettings` の 4 欄(path 2 + present 2)+ `JoinSpec` の 2 欄 |
| `acp/join.hy` | **【D11】** `AGENTD-KEYS` は名簿から**導く**(手で 2 鍵を書き足さない)+ 門は名簿を回る 1 本(`claude-settings-file-of` と同じ純関数 — path の**形**だけを見る) |
| `acp/runtime.py` | `_admitted_claude_settings_file` に倣った `~` の展開と在否の名乗り(I/O はここ)+ `join_plan` の `replace` + `settings_from_env` |
| `acp/judgment.hy` | `node-labels-of` に `seat-memory` / `seat-skills` |
| `launch.hy` / `headless.hy` | `claude-settings-declaration` に倣った `claude-instruction-sources`(**起動の拍ごとに**読む・memoize しない)→ params。**【D11】名簿を回る 1 本**にし、1 種ごとの枝を書かない。**両方の腕**に足す |
| `impls/claude_code.hy` | `CLAUDE-MD-EXCLUDES-SETTING` + `CLAUDE-SETTINGS-OWNED-KEYS` に 1 本 + **【D11】** `claude-pre-launch` の据え付けは名簿を回り、運び方 2 種(`file-text` = `fs-write-text-atomic` / `dir-link` = `fs-ensure-symlink`)で分岐する(知らない `kind` は loud に落ちる)+ `build-claude-argv` の `claudeMdExcludes` |
| `effects.hy` / `effects.pyi` | **【D8】** `FsEnsureSymlink` の定義 + 3 値の契約を docstring に(`FsLinkArtifact:700-710` と同形) |
| `substrate.hy` | **【D8】** `real-substrate` に `FsEnsureSymlink` の handler(既存の `_ensure-view-symlink:295-310` と同じ振る舞い・戻りを 3 値に)。**【D9】** `FsWriteTextAtomic:439-445` の tmp を `mkstemp` で一意にする(失敗時は tmp を掃除して再送出) |
| `tests/sessionhost_launch_deftests.hy` | **【D8】** `LaunchWorld` の偽物に `FsEnsureSymlink` を足す(実物の 3 値を写す — 既存の `FsLinkArtifact` の偽物と同形:294-326) |
| `tests/sessionhost_substrate_deftests.hy` | **【D8/D9】** 実物の handler の焦点の検(3 値 + 同拍 2 書き手) |
| `conformance/test_s12_claude_trust_preseed.py:57` | **【D9】** `.claude.json.agentd-tmp` の在否 assert を **glob** に広げる(`.claude.json.agentd-tmp*` が 1 件も残らない) |

⚠ `impls/claude_code.hy` は **substrate-clean**(生 IO 禁止・`Fs*` / `EnvGet` だけ)。
`memory_files` の据え付けと同じ規律で、**運ばれてきた物を実体化するだけ・中身を判断しない**。

⚠ **【D8・盲検 A】`impls` で「読んで・消して・張る」を組み立てない。**
`FsLinkArtifact` は**据わっている物を絶対に置き換えない**(`target-conflict` を返して終わる:
`substrate.hy:451-470`)ので、正本の path が変わった日に家の `skills` が**古い先を指したまま
になる**。実射済み(`counterexamples/repro_A_real_substrate.py` — 実物の `real-substrate` で
`1st='linked' / 2nd='target-conflict'`、家の link は古い先のまま)。
張り替えは substrate の 1 動詞(D8)に持たせ、`impls` は**呼ぶだけ**にする。

⚠ **【D1b・盲検 A】名乗りの 1 行は「やろうとしたこと」ではなく、`FsEnsureSymlink` が
返した 3 値そのものを書く**(`unchanged` / `linked` / `occupied-by-real-entity`)。
`agent-memory-written` が「書いた数」を名乗るようにしたのと同じ向き(commit 1071dbd6)。

実行モデル `model/chain.py` に責務の割りと正常例(`model/test_chain.py` 7 本)が在る。
Hy へ写す時の答え合わせに使えるが、**これは本実装ではない**。

### Phase 4 — 宿の宣言(doeff が据わった後)

⚠ **崖**: この鍵を知らない agentd は「宣言に無い鍵」で参加を断る(capacity 0)。
各宿で **node の行の `spec.agentd.revision`** を先に読み、鍵を知る版が現に走っていることを
確かめてから当てる。順 = 会社 Mac → pod → 個人 MacBook。

| 宿 | file | 値 |
| --- | --- | --- |
| 会社 Mac | dotfiles `cron_management/acp-single-mac.toml` | `${home}/dotfiles/claude/CLAUDE.md` / `${home}/dotfiles/agent/skills` |
| 個人 MacBook | dotfiles `cron_management/acp-proboscis-mbp.toml` | 同上 |
| pod | ACP `deploy/acp-control/agentd-pool-join.yaml` | **手順 0 で測った綴り** |

### Phase 5 — 受入の実射(§5)

---

## 5. 受入条件(1:1 で検に対応させること)

PR の本文に **Verification の表**(この節の項 ↔ 出荷した検の `path::name`)を必ず載せる。
弱めた項が在れば `## Verification deviations` を明記する(黙った弱めは自動で差し戻し)。

| # | 受入 | 対応する検 / 実射 |
| --- | --- | --- |
| 1 | **pod の席**で共通の CLAUDE.md が **user 層として** context に載る | 実際の agentd の席の context(`Contents of <家>/CLAUDE.md (user's private global instructions for all projects)` の札)。mock 不可 |
| 2 | **Mac の席**で同じ | 同上(会社 Mac) |
| 3 | 両方で dotfiles の skill が**使える一覧に在る** | 席の skill 一覧(`ai`/`claude` の一覧か、席の手番が名指しで 1 本呼べること) |
| 4 | **陽性対照**: cwd を `$HOME` の外にした席でも届く | 同上(cwd を `$HOME` の外にして 1 席) |
| 5 | **もれなさの針**(宿を列挙せず導く) | dotfiles `test_every_seat_starting_declaration_names_the_common_instruction_sources` |
| 6 | **二重読みが無い**(Mac の形で user 層 1 件ちょうど) | 席の context + doeff の deftest (f) |
| 7 | 名指した file が無い日も席は起きる | doeff の deftest (d) + 起動の拍の 1 行 |
| 8 | 名指しの無い機体は今日どおり | doeff の deftest (e) |
| 9 | 本体の契約が動いたら赤くなる | dotfiles `check_native_claude_home_contract.py`(緑で出荷・逐語が動けば赤) |
| 10 | `make lint` 清浄(新しい semgrep 規則を含む) | `make lint` |
| 11 | **【D8】正本の path が変わった日に、家の `skills` が新しい先へ張り替わる** | doeff の deftest (j) + `sessionhost_substrate_deftests.hy` の 3 値の検 |
| 12 | **【D9】同じ家へ 2 席が同拍で書いても、片方の書きが落ちない** | `sessionhost_substrate_deftests.hy` の同拍 2 書き手 + `conformance/test_s12_claude_trust_preseed.py`(glob) |
| 13 | **【D10】宣言が無い機体へ条文が届かない / 劣化の日に宣言外の path を触らない** | doeff の deftest (h)(囮を置いた上で)と (i)(劣化の日) |
| 14 | **【D10】`packages/doeff-agents/src/**` に literal `dotfiles` が 1 件も無い** | 新しい semgrep 規則(除外なし)・`make lint-semgrep` |
| 15 | **【D11】運ぶ物の綴りの定義点が 1 つ**(名簿に 1 行足すだけで宣言 → 席まで通り、行き先を宣言しない足し方は赤) | doeff の deftest (k)(名簿を回る・列挙しない) |

---

## 6. テストの制約(依頼書 §7 の引き写し — **必ず守る**)

- 開発中に撃つのは**触った file の焦点の検だけ・1 分以内**。
- 全数テストと全体検証は **1 日 1 回の日次便**に任せ、周回ごとに回さない。
- 型検査(`uv run pyright`)は**日次と手での名指しだけ**。

---

## 7. 進め方の注意

- **予定待ちは禁止**。待つ理由は外部の物理的なブロックだけで、その時は正体と
  operator が撃つ 1 手を報告に書く。
- 戻せる決定は自分で選び、card `acp:kanban-issue:ki-62aa1f4e9c9c` のスレッドへ
  (何を決めたか・理由・戻す手順・決めた会話と日時)記録する。戻せない決定だけ operator へ問う。
- ⚠ `~/repos/doeff` は共有の作業場で、**同時に別の席が動いていることがある**。
  branch 作業は専用の `git worktree`(置き場は `~/.worktrees/` の中ちょうど)で行い、
  他と重ならない綴りを選ぶ。
