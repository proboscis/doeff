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
| D8 | **張り替えの動詞を substrate に持つ**: `FsEnsureSymlink` を effect として足し、3 値 `unchanged` / `linked` / `occupied-by-real-entity` を返す。`impls` は**この動詞を呼ぶだけ**(盲検 A・`design.md` §10 R1)。**⚠ 2026-09-21T10:1xZ 追補 — 張る・張り替えるの物理は原子的に**: 同じ dir に書き手ごとに一意な名の仮の symlink を張り(D9 の `mkstemp` と同じ考え)、`os.replace(仮, link)` で被せる。`unlink` → `symlink` の 2 手も、空の家への素の `symlink` も使わない。実測(`evidence/symlink_install_race.{py,log}`・着地済みの `ensure-symlink-outcome` を 1:1 で写した): 空の家へ 2 席が同拍で張ると **200 回中 200 回**で片方が `FileExistsError`(= 席が起きない)、張り替えの間は読み手の **52 %** が根の無い瞬間を見る。lock は足さない(D9 と同じ)。**⚠ 2026-09-21T10:5xZ 追補 — 現物での実測で 3 つ目の壊れ方**: 写しではなく出荷されている `ensure-symlink-outcome` を個人 Mac で 2 process の同拍に掛けると、負けた側は `FileExistsError` が 82.5 %・**偽の `occupied-by-real-entity` が 8.5 %**(`islink` が False → 相手が張る → `exists` が symlink を辿って True になる)。偽の occupied の帰結は座で違う — `_ensure-view-symlink` は**居ない実 file を指して** `RuntimeError`(「手で直せ」)で席を落とし、`impls/claude_code.hy:532` は語のまま 1 行 log ちょうどで**黙って skills を入れない**(= この card が直している欠陥そのものが、直した後に再発する)。**⚠ 2026-09-21T12:1xZ 訂正 — 欠陥は独立に 2 つで、原子性だけでは 3 つ目が残る**(依頼者 c-3JYBNJMC… の Darwin の実測)。(1) **書きが原子でない**(`unlink` → `symlink` の谷)⇒ 一意な仮 + `rename` で閉じる。(2) **判定が symlink を辿る**(`os.path.exists` は symlink を辿って正本の dir に当たるので、相手が張った symlink を実体と読む)⇒ これは原子性では閉じない。閉じるのは**辿らない読みで枝を決める** = `os.lstat` **1 回**。⇒ 実装は **「判定は `lstat` 1 回(辿らない)」と「書きは一意な仮 + `rename`」を 2 つとも**満たすこと。片方だけでは 3 つ目(偽の `occupied-by-real-entity`)が残る — 原子化だけした形が 400 回とも緑に見えたのは窓が計器の分解能の下に入っただけで、窓が閉じた証拠ではない。実測 = `evidence/symlink_install_race_real_fn.{py,log}`・`evidence/symlink_install_darwin.{py,log}` | effect ごと消す(呼び手は 1 か所) |
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
     ⚠ 追補(10:1xZ): **同拍は本当に 2 process(barrier)で撃つ** — 空の家へ同時に張って例外 0・link は正しい先、
     別の先へ張り替える間にもう 1 つの process が `os.path.lexists(link)` を読み続けて根の無い瞬間 0。逐次の 3 値の検
     (`test-fs-ensure-symlink-three-outcomes`)では競りは見えない(実測 `evidence/symlink_install_race.log`)。
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
   - ⚠ **ただし本体の探し方と棄権の rc は写さない**(2026-09-21T08:4xZ 追補・依頼者 c-3JYBNJMC2RZTM1S8V43939MP42 が
     会社 Mac で見つけ、発注者が個人 Mac で再現): 雛形の既定の path `DEFAULT_BODY` は **pod の置場**
     (`/usr/local/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe`)ちょうどで、Mac(native の installer・
     `~/.local/share/claude/versions/<版>`)には無い。引数なしで撃つと `abstain` になり、雛形は
     `sys.exit(0 if verdict in ("green","abstain") else 1)` なので **exit 0**。⇒ そのまま写すと、守るはずの Mac で
     計器が**一度も発火せず、契約が動いても緑で通る**(本体を持たない宿での正直な棄権と見分けが付かない)。
     - 直し方は雛形自身が挙げている先例の再利用: dotfiles `agent/tests/check_native_worktree_contract.py` の
       `candidates()`(`shutil.which("claude")`・`~/.local/share/claude/versions` の新しい 3 本・`ClaudeCode.app`・
       npm の置場、実 path で重複を畳む)と「候補のうち最初に目印の綴りを持つ物を本体とする」判定。
       会社 Mac では `~/.local/bin/claude` の指す 2.1.278 が選ばれ、同居する古い版には引かれない(依頼者の実測)。
     - rc は dotfiles `agent/tests/checker_outcome.py` の語彙(緑 0・赤 1・**棄権 2**)を import して使う。
       棄権を 0 にしない。
     - ⚠ 先例の候補に pod の置場 `…/claude-code/bin/claude.exe` は名指しで入っていない(npm の旧い `cli.js` だけ)。
       pod では `which claude` がそこへ解決されることを**pod で撃って**確かめ、されなければ候補へ足す。
     - 記録: `evidence/shape_pins_company_mac.log` §3。
   - ⚠ **追補(2026-09-21T07:01Z 着地・依頼者 c-3JYBNJMC2RZTM1S8V43939MP42 が入れた。出典 = 発注者
     c-AJ8C0BK9RF29HQ92ZQ986FXQVT の郵便 lt-C22T2AV9VGX520WYFCEB0Q8X8B の自認。07:15Z に発注者が
     4 つの版の実測で書き直した — 別の注記は足していない)**: 上の逐語 6 本は
     **版で変わる縮めた識別子を含む**ので、綴りのまま写すと別の版の機体で**偽の赤**になる。
     雛形が pin しているのは 2.1.263(pod)で、実装の席は会社 Mac(**2.1.278**)。
     - 依頼者の実測(pod 2.1.263・2026-09-21T07:01Z): 雛形をそのまま撃って **green** ⇒ 陽性対照は生きている。
       赤は本来「契約が動いた」の合図だが、この pin では**版が違うだけ**でも出る。
     - 射程(依頼者が数えた): **6 本すべて**が縮めた名に依る(`Ke`/`Se`・`ae`・`wgr`/`CN`・
       `Ah`/`Se`/`HS`・`N`・`Sgr`)。**局所変数の名も動く**。本体の実物の byte
       (`evidence/shape_pins_across_versions.log`)で、落とす分岐は 2.1.263 の
       `if(t==="User"&&!v)try{let q=await ae().lstat(e);if(d===0&&q.isSymbolicLink()…`
       が 2.1.278 で `if(n==="User"&&!O)try{let ve=await le().lstat(e);if(g===0&&ve.isSymbolicLink()…`、
       判定は `wgr`/`CN` が `KRt`/`mB`。(07:01Z の版のこの注記にあった `st` と裸の `lstat(e)` は、
       別の担い手が整形して写した綴りで、本体の実物ではなかった。)
     - **直し方 = 3 つの規則**(雛形の逐語〔2.1.263〕と本体 2.1.265・2.1.276・2.1.278 で実測済み —
       `evidence/shape_pins_across_versions.py` / `.log`):
       1. 束縛の名(関数名・局所変数・引数)は **`[\w$]+`** で受ける。**`\w+` では足りない** —
          JS の識別子は `$` を含み、2.1.278 では関数名の約 2%(51,622 中 1,118)が `$` を持つ。
       2. 綴りで固定してよいのは、縮める道具が付け替えない物ちょうど — 文字列の literal
          (`"User"` / `"Project"` / `"Local"` / `"CLAUDE.md"` / `"skills"` / `".claude"` /
          `"local-agent"`)・`.` の後ろの property / method の名(`lstat` / `isSymbolicLink` /
          `isFile` / `isDirectory` / `nlink` / `entrypoint`)・構文。同じ束縛が何度も出る所は
          名前付きの group で「同じ名であること」を pin する(例: `lstat` の結果と
          `isSymbolicLink` / `nlink` / `isFile` の受け手が同じ変数)。
       3. **契約を担う断片だけを pin する**。関数の頭まで含めると版で落ちる — 2.1.278 は除外の関数が
          引数 2 つから 3 つに組み替わり(`zRt(e,n,r)`)、雛形の 6 本目
          (`function Sgr(e,t){…`)は `[\w$]+` にしても**偽の赤**になる。層の判定
          `if(<t>!=="User"&&<t>!=="Project"&&<t>!=="Local")return!1;` だけを pin すれば 4 つの版で緑。
     - 実測の結果: 形 6 本はどの版でも緑、契約を担う語を 1 つ動かすと 6 本とも赤。さらに**結び付き 2 本** —
       落とす分岐の `!<v>` が起動口の判定から作られていること(`<v>=<x>&&(<t>!=="User"||<判定>())`)・
       判定が読むのが `.entrypoint` であること — も 4 つの本体で緑。この 2 本は「判定が在る」ではなく
       「判定が落とす分岐を実際に制御している」を pin するので、計器に入れる価値がある(採否は担い手が決める。
       依頼者も入れることを推している)。
     - **pod の 2.1.263 の本体でも実測済み**(07:15Z の版のここにあった「pod の本体には撃っていない」は閉じた):
       依頼者 c-3JYBNJMC2RZTM1S8V43939MP42 が 2026-09-21T07:4xZ に pod `agentd-pool-6dd96ccd58-hhm5p` の
       `/usr/local/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe` へ、main `bbcdc552` の script を
       変えずに撃って **14/14 緑**(形 6・結び付き 2・弁別 6)。`$` を含む関数名は 975 / 45,301(2.15%)で、
       版の偶然ではない。6 本目は 2.1.263 では関数の頭ごとの逐語も在る ⇒ 頭ごとの pin が赤になるのは
       2.1.278 だけで、「頭は pin しない」の根拠は版の差(`evidence/shape_pins_pod_2.1.263.log`)。
     - **skills の entry は 2 つの形に分ける**: 1 つの形のままだと 2 か所(dir の走査と `SKILL.md` の読み込み)に
       当たり、片方が動いても他方で緑のまま残る。依頼者の案(`re.S` で組む)で割ると、4 つの版すべてで
       各 1 件・別の場所・弁別は赤:
       - 走査の側: `if\(!(?P<n>@)\.isDirectory\(\)&&!(?P=n)\.isSymbolicLink\(\)\)return null;if\((?P<r>@)==="skills"&&@\((?P=n)\.name\)\)`
       - 読み込みの側: `if\(!(?P<e>@)\.isDirectory\(\)&&!(?P=e)\.isSymbolicLink\(\)\)return null;.{0,200}?@\(@,(?P=e)\.name,"SKILL\.md"\)`
         (実物の幅は 151〜152 byte で、窓 200 の余りは約 50 byte。版で間の処理が増えれば窓を広げる)
       - ⚠ 2.1.276 では走査の側の判定の関数名が **`k$`**(`if(r==="skills"&&k$(U.name))`)。`\w+` で組むと
         この版で**現に偽の赤**になる — 規則 1 の実例。
     - **会社 Mac でも実測済み**(依頼者 c-3JYBNJMC2RZTM1S8V43939MP42・2026-09-21T08:2xZ・`CA-20038667`):
       同居する 2.1.276 / 2.1.277 / 2.1.278 の 3 版で、雛形の逐語は 3 版とも赤(欠け 6/6)、形の script は
       57 項目すべて緑(2.1.277 は誰も測っていなかった版)。2.1.278 の `$` を含む関数名の数(1,118 / 51,622)が
       個人 Mac の値と完全に一致 ⇒ 2 つの機体の 2.1.278 は同じ build で、機体をまたぐ限界は消えた
       (`evidence/shape_pins_company_mac.log`)。
     - 弁別(逐語を 1 つ動かした複製で **red**)は、計器を書いたら**据わる機体の版で撃ち直して**から緑を名乗る。
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
| `sessionhost/policy.hy` | **【D11】** `CarriedSource` と名簿 `CARRIED-INSTRUCTION-SOURCES`(= 綴りの定義点。env 鍵・宣言の鍵・kind・家の中の名・名乗りの語・不在の語を **1 行**に持つ)+ params の欄名 `CARRIED-INSTRUCTION-SOURCES-PARAM`。⚠ **置き場は `acp/effects.py` ではない** — 名簿を読むのは器(`impls/claude_code.hy`)と両腕(`launch.hy` / `headless.hy`)で、この 3 つは既存の規則 `doeff-agents-memory-baseline-spelling-has-one-home`(「substrate-clean な器は acp から import しない」)の側に在る。⇒ 名簿は両側から引ける `policy.hy` に 1 つ置き、`acp/join.hy` がそれを import する(2026-09-21 の実装で確定・依頼者の裁定 lt-46ADR4MBWB91X6QHR6A6X9K0J8 の 4) |
| `acp/effects.py` | **【D11】** `AgentdSettings` の **2 欄**(`instruction_sources` = (鍵, path) の対の tuple / `instruction_sources_present` = 実在した鍵の tuple)+ `JoinSpec` の **1 欄**(`instruction_sources`)。⚠ 1 種ごとに path と present の 2 欄を足す形(旧稿の「4 欄」)にしない — それが D11 が禁じた「1 種足すと欄が増える」形そのもの(依頼者の裁定 lt-0WRQ0387E1PWFJ43C5HDWD19BB の 3) |
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

⚠ **前提(2026-09-21T10:1xZ・依頼者 c-3JYBNJMC… の pod の観測〔郵便 lt-6P2WV98TDN84JQHC2SCF04XMN8〕で裏取り): 席の家は会話ごとではなく、
借りた資格(account)ごと**。家 = `<homes-root>/claude/<account の安全な綴り>`(`acp/judgment.hy` charter-with-grant)で、同じ資格の
会話は同じ家を**同時に**読む(実測: 1 つの pod で 8 席 → 家 3 つ・1 つの家を 3 会話が共有・手番の最中に 3 つ目の家が生まれた)。
`design.md` §3.5「家は同じ資格の複数 session が共有する」・D1「据え付けは起動の拍ごと」・D9 の一意 tmp はこの前提から出ている。帰結:
(1) 家へ置くのは全会話で同じ bytes の共通の指示だけ — 会話固有の物は自動記憶と同じ会話 id の側へ(Phase 4 の 2 鍵が名指すのは宿ごとに
1 つの在処なので、このまま整合する)。(2) 家は pod ごとに**空で鋳直される**(名は資格から決まるので使い回されるが、中身は運ばれない)—
一度据えて終わりにはできず、D1 の起動の拍ごとの据え付けが要る。**受入 1 / 3 / 4 は入れ替わった直後の pod で撃つ**(温まった pod の緑は
次の rollout で赤になる筋を残す)。(3) 据え付けは競り合う — D8 の追補(原子的な張り)と D9。家の名の形(pod = UUID・Mac = `claude_<16 hex>`)
は資格の綴りの形で、実装は名の形に依らない(`CLAUDE_CONFIG_DIR` の値をそのまま使う)。

| # | 受入 | 対応する検 / 実射 |
| --- | --- | --- |
| 1 | **pod の席**で共通の CLAUDE.md が **user 層として** context に載る | 実際の agentd の席の context(`Contents of <家>/CLAUDE.md (user's private global instructions for all projects)` の札)。mock 不可 |
| 2 | **Mac の席**で同じ | 同上(会社 Mac) |
| 3 | 両方で dotfiles の skill が**使える一覧に在る** | 席の skill 一覧(`ai`/`claude` の一覧か、席の手番が名指しで 1 本呼べること)。**Mac では、使った cwd が symlink を経ない綴りであることを札に pin するか、運んだ根を外した対照 1 席(同じ名前が 0 件)を並べる**(§5 の「偽の緑の筋」) |
| 4 | **陽性対照**: cwd を `$HOME` の外にした席でも届く | 同上(cwd を `$HOME` の外にして 1 席) |

⚠ **受入 1 / 2 / 3 / 4 の札には「どの機体で撃ったか」を書く**(12:1xZ 追補 — 依頼者の長命の宿の実測):家の名は資格ごとで**機体をまたいで同じ**(pod で使った名が Mac にも在る)が、**中身は機体ごとに別**。⇒「1 つの機体で据わった」は他の機体について何も言わない。なお長命の宿(会社 Mac / 個人 Mac)は**陰性対照として pool より強い** — 依頼者が数えた 11 個の家はどれにも `CLAUDE.md` も `skills` も無く、pod と違って「まだ据わっていないだけ」とは読めない。⇒ 受入 3 は pool と長命の宿の**両方**で撃てる。
| 5 | **もれなさの針**(宿を列挙せず導く) | dotfiles `test_every_seat_starting_declaration_names_the_common_instruction_sources` |
| 6 | **二重読みが無い**(Mac の形で user 層 1 件ちょうど) | 席の context + doeff の deftest (f) |
| 7 | 名指した file が無い日も席は起きる | doeff の deftest (d) + 起動の拍の 1 行 |
| 8 | 名指しの無い機体は今日どおり | doeff の deftest (e) |
| 9 | 本体の契約が動いたら赤くなる | dotfiles `check_native_claude_home_contract.py`(緑で出荷・逐語が動けば赤) |
| 10 | `make lint` 清浄(新しい semgrep 規則を含む) | `make lint` |
| 11 | **【D8】正本の path が変わった日に、家の `skills` が新しい先へ張り替わる**。**同拍の 2 席**(空の家へ同時に張る・張り替えの間に読む)で例外 0・根の無い瞬間 0・**偽の `occupied-by-real-entity` 0**(10:1xZ / 10:5xZ 追補)。⚠ **数えるのは「根が消えた読み」(`os.path.lexists` が false = ENOENT)ちょうどで、「読みの失敗」一般ではない**(12:1xZ 追補): macOS / APFS では張り替え中の `os.listdir` が一過性の `EINVAL` を 0.1 % 前後返すが、これは**古い形でも同率で出る**ので設計が防ぐ対象では無い。「失敗 0」と書くと Mac の枡だけ赤になる | doeff の deftest (j)(2 process の同拍を含む)+ `sessionhost_substrate_deftests.hy` の 3 値の検 + `evidence/symlink_install_race_real_fn.py`(**現物の関数**を叩く方)を直した実装に当てて A の例外 0・偽の occupied 0、`evidence/symlink_install_race.py` で B の根の無い瞬間 0 |
| 12 | **【D9】同じ家へ 2 席が同拍で書いても、片方の書きが落ちない** | `sessionhost_substrate_deftests.hy` の同拍 2 書き手 + `conformance/test_s12_claude_trust_preseed.py`(glob) |
| 13 | **【D10】宣言が無い機体へ条文が届かない / 劣化の日に宣言外の path を触らない** | doeff の deftest (h)(囮を置いた上で)と (i)(劣化の日) |
| 14 | **【D10】`packages/doeff-agents/src/**` に literal `dotfiles` が 1 件も無い** | 新しい semgrep 規則(除外なし)・`make lint-semgrep` |
| 15 | **【D11】運ぶ物の綴りの定義点が 1 つ**(名簿に 1 行足すだけで宣言 → 席まで通り、行き先を宣言しない足し方は赤) | doeff の deftest (k)(名簿を回る・列挙しない) |

⚠ **受入 3 は Mac でも pod でも運べた証拠になる — 今日の席は dotfiles の skills を 1 件も読んでいない**
(2026-09-21T09:1xZ 訂正・発注者が個人 Mac の 2.1.278 で実測 — `evidence/skills_roots_probe.{py,log}`。
この節の 08:4xZ の版は「Mac では受入 3 は証拠にならない・受入 4 で代える」と書いていた。その起点は依頼者
c-3JYBNJMC2RZTM1S8V43939MP42 の「会社 Mac の生きた席で、家が空でも skills が 117 件効いている」という報告だった。
依頼者はこれを撤回した(郵便 lt-7HQZS8PQMQ9YK2KEMR3PPKC0A4。生きた席の一覧に在るのは、本体に同梱の skill と
plugin の skill だけ)。発注者の席(個人 Mac)の一覧も同梱の 13 件だけで、同じ結果になった。08:4xZ の版の
結論は取り消し、この版で置き換える。受入の表は変えない):

- 本体は、家(`CLAUDE_CONFIG_DIR`)の `skills` を user 層として読む。ほかに、**作業ディレクトリから上へ
  たどった各 `.claude/skills` を project 層として読む**(本体の中の名は `getProjectDirsUpToHome`)。
  このたどりには止まる点が 2 つある。**git の root では、root 自身まで読んで止まる**。**ホームディレクトリでは、
  ホームに入る手前で止まる**。
  ⇒ `$HOME/.claude/skills` が読まれるのは、家を付け替えていない時(`CLAUDE_CONFIG_DIR` を設定せず、既定の
  `~/.claude` が家になる、人が手で開く会話)の user 層としてだけになる。project 層としては読まれない。
  agentd の席は家を付け替えるので、**運ばなければ dotfiles の skills は 1 件も載らない**。
  - 実測 1(実席と同じ起動引数・作業ディレクトリ = `~/repos/doeff`): 家が空なら、dotfiles の skill は 0 件。
    家の `skills` を `~/dotfiles/agent/skills` への dir symlink にした Phase 3 の形なら、一覧に載る。
    どちらも、API キーの dummy と OAuth の札の dummy で同じ結果。
    - 生きた席での裏取り(依頼者 c-3JYBNJMC… が会社 Mac の agentd の席で実施・郵便 lt-A9C1ZWKWMY8ZK0QRZYT1FXBDZ2・
      2026-09-21 18:1x JST): 家の `skills` を dir ごと 1 本の symlink にし、その中に実 dir の項目と symlink の
      項目を 1 つずつ置いた。次の手番の一覧に**両方とも載った**。したがって、根が symlink でも、項目が symlink
      でも読まれる(形の pin `skills-entries-accept-a-symlink` の実射の裏取り)。管理設定の file は、標準の
      3 つの置場のどれにも無い。
    - **pool の本体の版(2.1.263)での裏取り**(依頼者が pod `agentd-pool-d46558f69-c6p97` で同じ計器 1〜9 を 1 文字も
      変えずに撃った・郵便 lt-HH8KGE0FX6GP3WM7VVBXN5JF7E・`evidence/skills_roots_probe_pod_2.1.263.log`): Mac(2.1.278)の
      log と 10 行一致。行 7 / 7o = 2 なので、**運びの形は pool の版でも効く**。違うのは行 5 だけで、その理由は次項の
      取り消しにある。⚠ 形の pin の逐語は 2.1.263 から取った物で、挙動の測定はこれまで 2.1.278 だけだった —
      この裏取りで両方の版が揃った。
  - 実測 2(止まり方):
    - 読む: git でない作業ディレクトリの祖先。ホームより下の祖先。
    - 読まない: git の root より上。本物のホームの `.claude/skills`(⚠ cwd を symlink の綴りで渡した時は例外 — 次々項)。
  - ⚠ 08:4xZ の測定は見かけだった。その測定の「作業ディレクトリが `$HOME` の下なら載る」は、env の `HOME` を
    偽の dir に付け替えた測り方から出ていた。
    ⚠ **09:1xZ の版がここに書いた説明「本体は付け替えた `HOME` をホームとして扱わない・偽の dir はただの祖先として
    読まれる」は取り消す**(2026-09-21T09:5xZ)。依頼者が pod(2.1.263)で 1 変数ずつの対照 5a / 5b / 5c を撃ち
    (`evidence/skills_roots_probe_pod_2.1.263.log`)、発注者が会社 Mac(2.1.278)で偽の `HOME` の綴りだけを変えて追試した
    (`evidence/skills_roots_probe_ctl_home_spelling.log`)。結果は同じ: **本体は env の `HOME` を尊重してホームの手前で止まる**。
    行 5 が 2 だったのは、測定の作業 dir が macOS の `/tmp`(`/private/tmp` への symlink)の下にあり、`HOME` の綴りが
    symlink 経由・cwd 側の綴りが解決済みで、止まりの比較(綴り同士)が一度も当たらなかったから。同じ形を `$HOME` の下や
    `/private/tmp/…` の綴りで渡すと 0。pod(Linux・`/tmp` は実体)では最初から 0。版の差ではない(pod の同じ本体で
    symlink の綴りを渡す 5c が 2 を再現)。上の「止まる点が 2 つ」の結論は変わらない — 変わるのは止まりの**条件**で、
    ホームの止まりは「`HOME` の綴りと祖先の綴りが一致すること」に掛かっている。
  - ⚠ **受入 3 の偽の緑の筋**(上の取り消しから導かれる): 席の cwd が symlink の綴り(macOS では `/tmp`・`/var` の下)で
    与えられると、走査はホームで止まらず `$HOME/.claude/skills` とその上の祖先まで project 層として読む。両 Mac の
    `$HOME/.claude/skills` は dotfiles の skills を指しているので、その条件の席は**運んでいなくても** 107〜117 件を受け取る。
    ⇒ Mac で受入 3 を撃つ時は、使った cwd が symlink を経ない綴りであることを札に pin するか、運んだ根を外した対照 1 席
    (同じ名前が 0 件になる)を並べる。名前が一覧に出たことだけでは、運びの証拠にならない(受入の表の行 3 に書いた)。
    pod の席の家には `~/.claude` がそもそも無いので、pod ではこの筋は無い。
  - ⚠ 2 つ目の根に見える `…(<関数>(),".claude","skills")` の関数は**管理ポリシーの置場**
    (`getManagedFilePath`・ログの名は `managed=`)で、`$HOME` ではない。
- ⇒ **受入 3 は Mac でも pod でも、そのまま見分けになる**。受入 4 で代えない(Mac では cwd の pin か対照つき — 上の偽の緑の筋)。
  確かめる時は **skill の名前で**見る。100 件を超えると一覧の文字数の予算で説明文が落ち、名前だけの行になる。
  手で開く会話が `~/.claude/skills` を読む時も同じ形なので、運び方の欠陥ではない。
- **運んだ skills は、repo が持つ project 層の skills と並んで載る**。これが意図した意味で、戻せる決定として
  発注者が決めた(前提を訂正したうえで維持):
  この設計の目標は「共通の指示がどの席にも届く」(下限)で、「席は宣言した一式だけを見る」(上限)ではない。
  上限にするには本体の project 層の読みを止めるしかなく、repo が持つ project の skills と CLAUDE.md まで消える。
  戻す時は、席の起動に設定の読み元の絞り(`--setting-sources`)を足す別の変更を出す。
- **名前がぶつかったら運んだ側が勝つ**(実測・2.1.278・実席の起動引数): 家に目印 B を置き、git の root の
  project 層に同じ名前で目印 P を置く。context に載るのは B だけ(P は 0 件)。対照として、家が空なら P は載る。
  ⇒ repo の `.claude/skills` と名簿が同じ名前を持っていても、席が使うのは運んだ版。実体が同じ file なら、
  本体は同じ file の重複として 1 件に畳む。

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

---

## 検収の記録(2026-09-21・計画段 c-AJ8C0BK9RF29HQ92ZQ986FXQVT)

実装の報告 `lt-1WW45MA6K6N0NNJVYVY85NNQW0` を **accept**(`lt-JT6S0S08F48BFP67E8F9PV19J3`)。
1 度差し戻し(`lt-VZ34EHCR0RTDMJN67F64421B8K`・受入 11 の 1 点)→ L259 で直り、以下を計画段が
**自分で撃って**確かめた(個人 Mac = BSD・本線 `57bc38cf`。実装の実測は pod = Linux なので、
0 が OS をまたいで立つ)。

| 撃ったもの | 結果 |
| --- | --- |
| `test_fs_ensure_symlink_survives_two_seats_landing_on_one_empty_home` ほか 3 本 | pass |
| 門の弁別 + 席の家の検(semgrep 2 file + deftest) | 18 本 pass |
| `evidence/symlink_install_race_real_fn.py` を着地形へ | **400/400 `ok:linked`**・例外 0・偽 occupied 0(直す前は 165 例外 + 17 偽 occupied) |
| 実体の file が居る家 | `occupied-by-real-entity`・**中身は無傷**・残骸 0 |
| 実体の dir が居る家へ 3 度起動 | 3 度とも `occupied`・家の entry は増えず・残骸 0 |

検が空振りしない作りであることも確認: `os.fork` の本物の 2 process・共有 memory の spin barrier・
子の語が `ok:linked` / `ok:unchanged` 以外なら赤・読み手側は「読み 500 回超」の針つき。

### 決定 1(戻せる決定): 見分けと `rename` の間の窓は**最善努力**として受け入れる

`lstat` で枝を決めてから `rename` で被せる対は原子ではないので、その隙に**実体の file** が
現れた拍にはそれを置き換える(実体の dir なら `rename` が `OSError` になり `occupied` に落ちる)。
採る理由: 「symlink か不在の時だけ被せる」を原子的に言える syscall は移植できる形では無く、
erosion guard が守る形は長く据わっている実体なので見分けが先に当たる。**戻す時の手順**:
Linux 限定の `renameat2`(張り替えを弾くので使えない)ではなく、据え付けの前に家を占有の有無で
分ける形から検討する。決めた席 = 計画段 c-AJ8C0BK9RF29HQ92ZQ986FXQVT / 2026-09-21T11:2xZ。
出自 = 依頼者 c-3JYBNJMC… の pod の実測(`rename` 単体は実体の file を黙って潰す)と、
実装 c-EWR4R2XYWYEPDCMYVBEHRNF1MB の註。

### 決定 2(戻せる決定): 受入 1〜4 は宣言つき deviation のまま accept する

pod の agentd が `ec654440`(実装より前)を走らせている限り、生きた席の実射は原理的に撃てない。
担い手の未了ではないので報告を止めない。**pool が実装入りの image に載った拍に撃つ**を card
`acp:kanban-issue:ki-62aa1f4e9c9c` の追跡に残す。戻す時 = その拍に受入 1 / 3 / 4 を入れ替わった
直後の pod で撃ち直すだけ。

### 射程外の所見(この依頼では直さない)

- `os.replace` が dir 以外の理由(権限・容量)で `OSError` になった拍も `occupied-by-real-entity` を
  名乗るので、log は「実体が居た」と言い、追う人は在りもしない物を探す。3 値の語彙は変えずに
  log へ errno を添える形が後の便で価値がある。
- pod に tmux が無く、tmux の検 3 本が skip ではなく `assert` で赤になる(素の `cc58379a` でも同じ)。
  計画段から受付へ起票した。

### 検収の後に判った 3 つ(2026-09-21T12:1xZ・計画段)

1. **計画段の実測はどの木か** — すべて隔離 worktree(`~/.worktrees/doeff-wt-accept-seat-home-AJ8C0B`・
   HEAD `c668fa7d`)で、共有 checkout(この機体は本線から 199 commit 遅れ)ではない。import の実測で
   `substrate.hy` と `doeff` は worktree 側に解け、`doeff_hy` だけ共有 checkout 側に解けたが、
   `git diff de9b8cda origin/main -- packages/doeff-hy/src` が**空**なので測定に効く差は無い。
2. **BSD `ln -sfn` は原子ではない**(依頼者の Darwin の実測: 読み 80,733 のうち `ENOENT` 4,531)。
   GNU coreutils 9.4 では 0 だったので、これは実装の話ではなく**綴りを残さない理由**。文書・hook・shell の
   手順のどこにも `ln -sfn` と書かない(綴りは「一意の仮 symlink + `rename`」)。
3. **同じ file の `FsLinkArtifact` に同じ形の隙が残っている**(実装 c-EWR4R2XY… の申し送り + 計画段の到達性の確認)。
   `exists` / `islink` で見てから素の `os.symlink` の 2 手で、負けた側は `FileExistsError` を投げる。
   敷設先 `<target-project>/sessions-index.json`(`impls/claude_code.hy:740`)は**会話 id で割れていない**ので、
   同じ資格の 2 会話が同じ家へ transplant する拍に競る。⇒ 受付へ class investigate で起票した。
   ⚠ 新しい門 `doeff-agents-symlink-install-has-one-home` は **file 単位の exclude** で出荷の 1 点を通すため、
   同じ file の中に居るこの動詞は原理的に覆わない(門が緑でも「`substrate.hy` の中は自由」の意味)。

### 検収の後に残っていた扉 3 つ(2026-09-22・計画段が会社 Mac で実測)

依頼者 c-3JYBNJMC… が会社 Mac(Darwin 25.5.0 / APFS)で 3 点を挙げた。計画段が**動詞そのものを呼んで**
撃ち直した結果、2 つは現物どおり・1 つは結論が過大だった。計器と生の出力 =
`evidence/symlink_install_residual_doors.{py,log}`(検体 = `ef0eaa55` の `substrate.hy`・隔離 worktree)。

**扉 1 ⭐⭐ 「raise しない」の約束が、try の外の 2 つの syscall で現に破れる**(3 形とも実測)

| 家の形 | 抜けた例外 | 抜ける座 |
| --- | --- | --- |
| 親の位置に実体 file が居る | `FileExistsError` | `os.makedirs` |
| 家が書けない(`r-x`) | `PermissionError` | `os.symlink` |
| 親 dir を作れない(祖父が `r-x`) | `PermissionError` | `os.makedirs` |

`os.lstat` と `os.replace` は `try` に包まれているが、`os.makedirs` と `os.symlink` は包まれていない。
⇒ 容量切れ(`ENOSPC`)・権限の事故・読み取り専用の家で、席が 3 値の名乗りではなく素の `OSError` で落ちる。
これは D8 が直したばかりの `FileExistsError` と**同じ壊れ方**(docstring の逐語「この動詞は raise しない
約束なのに、その約束ごと破れて席が起きない」)が、別の扉から残っている形。

**扉 2 ⚠ `except OSError` が広すぎる — ただし動詞の中に到達路は見つからなかった**

`os.replace` の except は無型なので、実体が 1 つも居ない失敗(仮が消えた = `ENOENT` 等)も
`occupied-by-real-entity` を名乗り、`_ensure-view-symlink` が「在りもしない実 file を手で片付けろ」
(`is a real file where a symlink … is required (erosion guard) — reconcile it manually`)と人に言う。
段を単独で撃つとそのとおりになる(実測)。⚠ **ただし動詞の中では到達路が無い**: 実体 dir(空 / 中身入り)と
実体 file は 3 形とも正しく `occupied` を名乗り、仮を消す物は木に 1 つも居ない
(`.agentd-tmp` を掃く座 = 0・`glob` の掃引 = 0)。容量・権限は扉 1 の側で先に落ちる。
⇒ **潜在**(語彙の誤りであって live な誤診断ではない)。扉 1 と同じ直しで一緒に閉じる。

**扉 3 ❌ `unchanged` は綴りの一致だが、張り替えは「毎起動」ではなく「綴りが変わった 1 度きり」**

| 宣言の綴り | 1 回目 | 2 回目 | 3 回目 | 指す先 |
| --- | --- | --- | --- | --- |
| 据わっている綴りと同じ | `unchanged` | `unchanged` | `unchanged` | 正しい |
| 末尾 `/` が付いた | `linked` | `unchanged` | `unchanged` | 正しい |
| `./` が挟まった | `linked` | `unchanged` | `unchanged` | 正しい |

前提は依頼者の指摘どおり(`instruction-sources-of` は `.strip` と形の門だけ・
`_with_admitted_instruction_sources` は `~` を展開するだけ・`normpath` も `realpath` も無い)。
だが `os.symlink` は綴りを**逐語で**格納するので、張り替えた次の拍からは新しい綴りどうしが一致して
`unchanged` に落ちる。⇒ 「起動のたびに張り替えが起きる」は成り立たない。残るのは
**綴りが変わった日の 1 度だけの余計な張り替え**で、同じ家へ**異なる綴り**を宣言する機体が 2 つ在る時だけ
往復する(今日そうなっている機体は見ていない)。⇒ 欠陥としては小さい。門の 1 点(`join` が据える拍の
`normpath`)は安いので入れてよいが、扉 1 / 2 とは別の重さ。

**針の射程 ⭐ semgrep `doeff-agents-symlink-install-has-one-home` は Python の綴りしか見ていない**

`pattern-regex: 'os\.symlink\b'` / `'\.symlink_to\b'`・`languages: generic`・
include は `/packages/doeff-agents/src/**`。⇒ shell の `ln -sfn` はどこに書いても当たらない。
BSD の `ln -sfn` は読みの 5.64 %(4,531 / 80,733)が `ENOENT`(依頼者の Darwin の実測)= 規則の
message が禁じている物理の中で最悪。いま木に `ln -s` は 0 件なので**再侵入**の話で、
`pattern-either` に shell の綴りを 1 行足すのが安い(include を広げるのは、検体側が意図的に
symlink を張るので勧めない)。

⚠ 門を**動詞の単位**へ動かす時の註(実装 c-EWR4R2XY… から): 許す綴りは 1 つではなく **2 つ**になる。
「置き換える据え付け」(`ensure-symlink-outcome` — `rename` で被せる)と「置き換えない敷設」
(`FsLinkArtifact` — `FileExistsError` を合図に読み直す)は物理が逆で、1 つの正しい形へは畳めない。

**根は 1 つ**: この file の symlink の動詞は、失敗の語彙を happy path の値しか持たない。
⇒ 呼び出し座の guard ではなく**動詞の戻り値の型**を直す(「器が断った」を表す値 + errno を足し、
物理を全部 `try` の中へ入れて約束を守らせ、`_ensure-view-symlink` は `occupied` だけを erosion guard の
typed fail に写す)。`FsLinkArtifact` の隙(上の 3)と同じ根なので、**一括出荷**が素直。
