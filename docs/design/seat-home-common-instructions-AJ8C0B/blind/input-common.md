# 材料(固定済み)

対象 repo: doeff / 基準 commit `57641077306214b7b7da86708b04e9ed0524499b`
検査の実体: `make lint`(ruff / pyright / semgrep / doeff-linter)と `uv run pytest`。
semgrep の規則の正本 = `.semgrep.yaml`、実行 = `make lint-semgrep` と pre-commit。

⚠ 下の「設計」は**まだ 1 行も実装されていない提案**である。`実コード(現状・変更前)` と
註した抜粋だけが現に repo に在る code で、それ以外は提案。未提示の検査を推測しないこと。

---

# agentd が起こす claude の席の家へ、共通の CLAUDE.md と skills を運ぶ口(設計)

- 依頼: `lt-HZRYH9ST368YKXR2T1Z2W9ZKJ9`(class investigate・from `c-3JYBNJMC2RZTM1S8V43939MP42`)
- card: `acp:kanban-issue:ki-62aa1f4e9c9c`(盤 agora-redesign)
- 設計の会話: `c-AJ8C0BK9RF29HQ92ZQ986FXQVT`(claude-opus-5 / effort xhigh)
- 基準 commit: doeff `57641077306214b7b7da86708b04e9ed0524499b`(2026-09-21 11:40:52 +0900)
- **完了の範囲: 設計まで**。この会話は code を 1 行も変えない。本実装・CI への接続は「予定」と記す。

---

## 0. 結論(先に 1 段落)

**家へ運ぶのは「起動の拍ごとの据え付け」1 つ**にする。共通の CLAUDE.md は**実体 file**として
`<CLAUDE_CONFIG_DIR>/CLAUDE.md` へ書き、skills は `<CLAUDE_CONFIG_DIR>/skills` の
**whole-dir symlink** を正本へ向け直す。どちらも `claude-pre-launch`(PreLaunchSetup)の
1 か所で、席を起こす拍ごとに行う。正本の在り処は**機体の参加宣言の 2 鍵**が名指し、
`claude_settings_file` と同じ連鎖(宣言 → join → env → AgentdSettings → 起動の拍で読む →
params)で運ぶ。第 2 の読み口(dotfiles 側の provisioner・reconciler)は作らない。
Mac で起きる二重読みは、doeff が導く 3 つ目の settings の鍵 `claudeMdExcludes` で落とす。

この 3 つの選択は**どれも実射で測った**上で倒している(§2)。とくに「symlink にしない」は
記憶や doc からの判断ではなく、**symlink だと黙って 0 件になる条件が現に在る**ことを
撃って確かめた結果である(§2.3)。

---

## 1. 固定した受入条件・現状の障害・未確認事項

### 1.1 受入条件(依頼書 §8 の引き写し = 実装段の合否)

1. pod の席と Mac の席の両方で、共通の CLAUDE.md が **user 層として** context に載り、
   dotfiles の skill が使える一覧に在る。
2. 陽性対照: cwd を `$HOME` の外にした席でも届く。
3. もれなさの針: 宿を**列挙せず導く**(`cron_management/*.toml` のうち `[agentd-join.agentd]` を
   持つ file 全部が正本を名指す、という形)。3 台目の宿を足した日に書き忘れれば赤くなる。
4. 確かめるのは実際の agentd の席(node の行の名乗り + 席の context)。mock では受入にならない。
5. (この設計が足す)Mac の形で共通 CLAUDE.md が **1 度だけ**載る(二重読みが無い)。

### 1.2 現状の障害(依頼者の実測 + この会話の再現)

- agentd が起こす claude の席の家(`~/.local/state/doeff/agentd-homes/claude/<id>`)には
  CLAUDE.md も skills も無い。この pod の自分の家(`8df4f960-…`)を直に読んで確認:
  `.claude.json` / `backups` / `policy-limits.json` / `projects` / `remote-settings.json` /
  `session-env` / `sessions` / `shell-snapshots` ちょうど。
- 今日の倒れ方を fixture で再現した(`evidence/probe_user_layer.log` の G / H):
  - cwd が `$HOME` の下の席 … 共通 CLAUDE.md が **project 層**として偶然載る
    (`Contents of $HOME/.claude/CLAUDE.md (project instructions, checked into the codebase)`)。
    skills は **0 本**。
  - cwd が `$HOME` の外の席 … **CLAUDE.md も skills も 0**。

### 1.3 未確認事項(この設計が持ち越す前提)

- 実射は **pod の機体(linux-x64・本体 2.1.263)**で行った。依頼書が名指した会社 Mac は
  **2.1.278**(この会話からは読めない)。⇒ §4 の強制の 1 本目は「据わっている本体から
  逐語を読む計器」で、版が動いた日・宿が違う日に**赤くなる**形にする(dotfiles
  `check_native_worktree_contract.py` と同じ 3 値の作り)。この計器が入るまで、
  「Mac の 2.1.278 でも同じ逐語」は**未確認**である。
- この pod には managed settings(`/etc/claude-code/`)が無い。managed 層が
  `strictPluginOnlyCustomization` / `lockedCustomizationSources` を立てる宿では
  user 層の skills と記憶が本体の側で落ちる(逐語 `Or("userSettings")` の門)。
  会社 Mac に managed 層が在るかは**未確認** ⇒ §4 の計器が `Or("userSettings")` の
  門も読む。

---

## 2. 本体の読み口を実射で測る(依頼書 §4-1)

測り方は 2 本で、**どちらも据わっている本体そのもの**を読む。

- **(実射)** `ANTHROPIC_BASE_URL` を手元の捕捉 server へ向け、本体が実際に組んだ
  `/v1/messages` の request body を読む。**body に入っている = 席の context に載っている**。
  札は dummy 値で、本物の資格は 1 本も渡さない(API へは 1 byte も出ない)。
  台本 `evidence/probe_user_layer.sh` / 記録 `evidence/probe_user_layer.log`。
- **(逐語)** 実行体から契約の逐語を読む。
  台本 `evidence/read_body_contract.sh` / 記録 `evidence/body_contract.log`
  (本体 sha256 `26d020351e8112f4006790f3cfce43b4c9df0c1bb1d0e542364d64151b81d5ba`)。

### 2.1 user 層の CLAUDE.md は `CLAUDE_CONFIG_DIR` の下

逐語:

```
function s(){return process.env.CLAUDE_CONFIG_DIR}
var Se=os(()=>(s()??i(R(),".claude")).normalize("NFC"),s);
function dQ(e){let t=he();switch(e){
  case"User":return Ke(Se(),"CLAUDE.md");
  case"Local":return Ke(t,"CLAUDE.local.md");
  case"Project":return Ke(t,"CLAUDE.md");
  case"Managed":return Ke(HS(),"CLAUDE.md");…
```

実射(probe B): 家に実体 file を置くと body に
`Contents on <家>/CLAUDE.md (user's private global instructions for all projects)` の札で載る。
⇒ **`$HOME/.claude` ではなく `CLAUDE_CONFIG_DIR` の下**。依頼書 §2 の skills 側の測定と
同じ向きで、CLAUDE.md 側も確かめられた。

### 2.2 user 層の skills も `CLAUDE_CONFIG_DIR` の下(依頼者の測定の裏取り)

逐語 `let r=Ah(Se(),"skills"),o=Ah(HS(),".claude","skills"),…;
n(\`Loading skills from: managed=${o}, user=${r}, project=[${d.join(", ")}]\`)`。
実射の debug 行 `Loading skills from: managed=/etc/claude-code/.claude/skills,
user=/tmp/ccprobe/homeA/skills, project=[]` / `Loaded 1 unique skills (… user: 1 …)`。

### 2.3 ⚠ 反例 — **user 層の記憶だけ** symlink / hard link を黙って捨てる条件が在る

逐語(記憶 file の読みの本体 `Q0`):

```
if(t==="User"&&!v)try{
  let q=await ae().lstat(e);
  if(d===0&&q.isSymbolicLink()||(q.nlink??1)>1&&q.isFile())return[]
}catch{}
```

`v` の定義は `let v=o&&(t!=="User"||wgr());`、`function wgr(){return CN()!=="local-agent"}`、
`function CN(){return o().entrypoint}`(= 環境変数 `CLAUDE_CODE_ENTRYPOINT`)。
user 層の呼び口は `includeExternal` を真で固定して渡すので
(`Or("userSettings")){let Pe=dQ("User");E.push(...await Q0(Pe,"User",v,!0,0,…))`)、
この門は **`CLAUDE_CODE_ENTRYPOINT=local-agent` の席でだけ**発火する。

実射で当てた(`probe_user_layer.log` §1・§2):

| 家の `CLAUDE.md` の形 | 既定の entrypoint | `CLAUDE_CODE_ENTRYPOINT=local-agent` |
| --- | --- | --- |
| symlink | 載る(1 件/手番) | **0 件(黙って落ちる)** |
| hard link(nlink 2) | 載る(1 件/手番) | **0 件(黙って落ちる)** |
| 実体 file(nlink 1) | 載る(1 件/手番) | 載る(1 件/手番) |

skills 側は同じ条件で **3 形すべて 2 件のまま**(symlink の base dir でも落ちない)。
逐語も対応する: skills の entry は `if(!N.isDirectory()&&!N.isSymbolicLink())return null;` と
**symlink を明示で受ける**。

⇒ **CLAUDE.md は実体 file・skills は symlink**、という非対称はこの測定が決めている。
「片方だけ symlink なのは気持ち悪い」という審美の話ではなく、**本体の契約が 2 面で違う**。

### 2.4 鍵・旗で置き場を指せるか(依頼書 §4-1 の残り) → **指せない**

| 候補 | 逐語 | 判定 |
| --- | --- | --- |
| settings `skillsDirs` | `skillsDirs:k(us().refine((e)=>e.split("/").at(-1)==="skills",…))` は `CLAUDE_MEMORY_STORES` の store の欄。実行時に `x(N.mount,"skillsDirs is team-store only")` | **不可**。team の mount 専用で、網越しの同期(`/v1/code/local/memory/mounts`)が前提。宿の file を指す鍵ではない |
| env `CLAUDE_CODE_ADDITIONAL_DIRECTORIES_CLAUDE_MD` | `E.push(...await Q0(Ie,"Project",v,D))` | **不可**。載るのは **Project 層**。受入 1 の「user 層として」を満たさない |
| env `CLAUDE_CODE_DISABLE_CLAUDE_MDS` | 返り `[]` | 逆向き(全部止める) |
| `--bare` | help 逐語「skip … CLAUDE.md auto-discovery」 | 逆向き |
| settings `claudeMdExcludes` | `function Sgr(e,t){if(t!=="User"&&t!=="Project"&&t!=="Local")return!1;…ze().claudeMdExcludes…}` | **落とす向きにだけ使える**(§3.4 で使う) |

⇒ **案 2(起動の argv / settings の鍵で置き場を指す)は退ける**。置き場を指す鍵は本体に無い。

---

## 3. 運ぶ手段を 1 つ選ぶ(依頼書 §4-2)

### 3.1 候補の比較

| | 案 A: 鋳造の拍に symlink | 案 B: 鍵・旗で指す | 案 C: charter が中身を運ぶ | **採用: 起動の拍ごとの据え付け** |
| --- | --- | --- | --- | --- |
| CLAUDE.md が user 層で載るか | △ entrypoint 次第で**黙って 0**(§2.3) | ✗ Project 層にしかならない(§2.4) | ○ 実体 file | ○ 実体 file |
| skills 117 本 | ○ 写さない | ✗ 指す鍵が無い | ✗ 3.4 MB を手番ごとに wire へ | ○ symlink なので写さない |
| 正本が動いた日 | ○ 即 | — | ○ 手番ごと | ○ 次の席の起動から |
| 既に在る 31 個の家(§4) | ✗ 後追いが要る | — | ○ 要らない | **○ 要らない(次の起動で自動で直る)** |
| 写しが腐る | ○ 腐らない | — | ○ 腐らない | ○ 腐らない(拍ごとに書き直す) |
| 宿ごとの path の差 | 宣言 | — | 起こす側が持つ | 宣言(§3.3) |

案 A を退ける理由は 2 つで、**1 つ目が決定的**:

1. **黙って落ちる枝が在る**(§2.3 の実射)。落ちても log は 1 行も出ない —
   「席は立っている・条文は届いていない」という、この card が直そうとしている障害と
   まったく同じ倒れ方を、別の入口で作り直すことになる。
2. 鋳造の拍にしか張らないので、既に在る 31 個の家が空のまま残る(依頼書 §4)。

案 C(`memory_files` と同じ形)を skills に使わないのは、正本が **3.4 MB / 203 file**
(この pod の checkout 実測。Mac は 117 skill でさらに大きい)で、手番ごとに wire を通す
量ではないから。CLAUDE.md 単体(62,696 byte — `~/dotfiles/claude/CLAUDE.md` 実測)なら
案 C でも運べるが、**正本が宿の checkout に在る**以上、中身を中央へ持ち上げて配り直すのは
経路が 1 本増えるだけで、`claude_settings_file` が既に開いている「宿の file を宿で読む」
連鎖を使えば足りる。

### 3.2 採用する形

`claude-pre-launch`(`impls/claude_code.hy` — PreLaunchSetup の claude 実体)に
2 つの据え付けを足す。この関数は**既に家へ書いている**(`preseed-claude-trust` が
`<家>/.claude.json` を書き、`memory_dir` へ冊を書く)ので、家への書き口は増えない。

1. **`<config-dir>/CLAUDE.md`** … 宣言が名指した正本を読み、**実体 file** として
   `fs-write-text-atomic`(temp + rename。既存の 2 か所と同じ動詞)で書く。
2. **`<config-dir>/skills`** … 宣言が名指した正本への **whole-dir symlink**。
   既に同じ先を指していれば**何もしない**(churn を作らない — §3.5)。

「起動の拍ごと」であることが、この設計の効く部分である:

- 既存の家の後追いが**設計の中に消える**(§4 の問いに「後追いの仕組みは要らない」と答える)。
- 写しが腐らない。dotfiles 側の実弾がまさにこれ:
  `agent/tests/test_provision_claude_profile.py::test_the_sweep_sees_a_profile_whose_only_gap_is_the_context_symlink`
  — 個人 Mac の profile `ca` の `~/.claude/CLAUDE.md` が **23 日古い実体の写し**で、
  その席は 23 日前の条文(既に偽と判じられた 1 文を含む)を常時 in-context に載せて走っていた。
  鋳造の拍に 1 度だけ書く設計は、この実弾を agentd の家で再演する。

### 3.3 正本の読み口 — 既存の連鎖 1 本に乗せる(依頼書 §5 の 1 点目)

`claude_settings_file` が開いた連鎖をそのまま使う。**第 2 の読み口を作らない**。

```
機体の参加宣言 [agentd].claude_memory_file / .claude_skills_dir
  → acp/join.hy  (AGENTD-KEYS に 2 鍵・claude-settings-file-of と同じ形の読み)
  → env DOEFF_AGENTD_CLAUDE_MEMORY_FILE / DOEFF_AGENTD_CLAUDE_SKILLS_DIR (acp/effects.py が綴りの家)
  → AgentdSettings
  → launch.hy / headless.hy が **起動の拍ごとに**読む(claude-settings-declaration と同じ use-site)
  → params
  → impls/claude_code.hy claude-pre-launch が家へ据える
```

- 宣言の値は絶対 path か `~` / `~/…`(cwd 相対は断る — `claude-settings-file-of` と同じ規律)。
- **不在は非致命**(`claude_settings_file` の R13 の訂正と同じ): 宿は「先端で揃えられない日は
  image の下限へ戻して立つ」正規の degrade を持ち、その日の checkout に file は無い。
  断ると degrade が **pool 全体の capacity 0** に化ける。⇒ 起こす。ただし黙らず、起動の拍ごとに
  名前つきの 1 行(`seat-memory-file-absent` / `seat-skills-dir-absent`)と
  node の行の labels(`seat-memory` / `seat-skills` = present|missing)で名乗る。
- 名指しが無い機体は今日どおり(家は 1 byte も変わらない)。

宿ごとの差は**参加宣言の 1 点**、中身は dotfiles、据え付けの書き口は doeff
(設計 card `ki-f10ffeb77cab` の決定)— この形を保つ。

### 3.4 二重読みを避ける(依頼書 §4-3)

**測った**: Mac の形(cwd が `$HOME` の下・`$HOME/.claude/CLAUDE.md` が在る)を fixture で
再現すると、新しい形では同じ中身が **2 度**載る(`probe_user_layer.log` の I:
`user_memory_occurrences=4`(2 手番 × 2)・`cited` が project 層と user 層の 2 本)。

**なぜ席でだけ起きるか**も測った(probe L): 有人の席(家 = `$HOME/.claude`)では
**1 件ちょうど**になる。本体は祖先を登って `<祖先>/.claude/CLAUDE.md` も Project 層に
積むが、有人の席ではそれが user 層と**同じ path** なので `processedPaths` が畳む。
agentd の席は家の path が違うので畳まれない ⇒ **席に固有の二重読み**である。

**落とし方**: doeff が `--settings` に **3 つ目の鍵** `claudeMdExcludes` を置く。
値は席の `$HOME` から導く 1 本 `"<$HOME>/.claude/CLAUDE.md"` ちょうど
(glob を使わない・宣言に書かせない — 宿ごとに書かせると 3 台目で漏れる)。
実射(probe K)で、project 層の写しだけが落ち user 層は残ることを確かめた。

⚠ **射程は測った分ちょうど**: 落とすのは「有人 profile の家の user 記憶が、祖先の歩きで
project 層として入ってくる」1 本。`$HOME/CLAUDE.md`(在れば同じく祖先で拾われる)は
**この宿では実在せず測っていない**ので、名簿に入れない。次にそれを測った便が判定する。

### 3.5 起動・停止・排水・同時性(依頼書 §5 の 2 点目)

家は**同じ資格の複数 session が共有する**。測った事実と、そこから引く契約:

- **書きの同時性**: 同じ node の全席が**同じ宣言**を読むので、書く中身は同一。
  `fs-write-text-atomic`(temp + rename)なので torn read が無く、同時に走っても最後の
  rename が勝って中身は同じ。⇒ **冪等**。lock を足さない。
- **走っている席への波及(skills)**: 本体は skills の dir を**見張っている**。実射の debug 行:
  `Watching for changes in skill/command directories: <家>/skills…` /
  `[skills] idle — switching poll interval to 30000ms`。
  ⇒ 走行中に張り替えると走っている席にも効く。だから**既に正しい先を指していれば張り替えない**
  (read-then-write)。正本の中身が動いた時は symlink 越しなので張り替え自体が起きない。
- **走っている席への波及(CLAUDE.md)**: 記憶の file の読みは host ごとに memoize される
  (`M_(e,t,r,o){let d=DZ(e),f=d.files.get(t);if(!f)f=Qgs(…)}`)。skills と違い
  `ConfigChange` hook の source 名簿(`["user_settings","project_settings","local_settings","policy_settings","skills"]`)にも
  記憶は無い。⇒ 走行中に書き直しても、走っている席はその手番で読んだ物を持ち続ける。
- **引く契約(これを実装依頼書に書く)**: **家の中身は席の起動の拍で決まる。
  走っている席は自分の起動の拍の中身を持ち続ける。** 正本を動かした日に全席へ行き渡らせる
  正規の動詞は「席を起こし直す」ちょうどで、走行中の席へ押し込む口は作らない。
- **agentd の描き直しは要らない**: 新しい 2 鍵は宣言 → env の既存の連鎖に乗るだけで、
  中断の枠(系に 1 つ)にも排水(`restart_drain_minutes`)にも触らない。
  ⚠ ただし **崖が 1 つ在る**(`claude_settings_file` と同じ形): この鍵を知らない agentd は
  「宣言に無い鍵」で参加を断る(`declared-values-of` の ValueError → capacity 0)。
  ⇒ **宣言の行の着地は、その版の doeff がその宿で現に走っていることを
  node の行の `spec.agentd.revision` で確かめた後**(§6 の手順)。

### 3.6 方策の定義点は 1 つ・旧側の値を運ぶ(依頼書 §5 の 3 点目)

- 綴りの家は doeff の 1 か所:
  `impls/claude_code.hy` に `CLAUDE-USER-MEMORY-FILE = "CLAUDE.md"` /
  `CLAUDE-USER-SKILLS-DIR = "skills"` / `CLAUDE-MD-EXCLUDES-SETTING = "claudeMdExcludes"`、
  最後の 1 本は `CLAUDE-SETTINGS-OWNED-KEYS` に入る(集合はこの 1 か所・`.semgrep.yaml` が
  第 2 の綴りを禁じる — 既存の `autoMemoryDirectory` の規則と同じ形)。
- **旧側の値を運ぶ**:
  - Mac で偶然届いていた CLAUDE.md と**同じ中身** = `~/dotfiles/claude/CLAUDE.md`
    (`$HOME/.claude/CLAUDE.md` はこれへの symlink — dotfiles `cc-multi-profile` skill の
    据え付け表 `~/.claude/CLAUDE.md → ~/dotfiles/claude/CLAUDE.md`)。宣言が名指すのは
    **symlink ではなく正本**の `${home}/dotfiles/claude/CLAUDE.md`。
  - skills の正本 = `${home}/dotfiles/agent/skills`(Mac 117 本 / この pod の checkout は 107 本)。
  - **pod では正本の path が違う**: pod の agentd の容器は dotfiles を
    `${home}/dotfiles` に持たない。⇒ pod の宣言
    (ACP `deploy/acp-control/agentd-pool-join.yaml`)は**その容器の中の綴り**を書く。
    値が宿ごとに違うことがまさに「宿ごとの差は参加宣言の 1 点」であり、
    `claude_settings_file` が既に同じ形を取っている(Mac は `${home}/dotfiles/…`)。
    ⚠ **pod の容器のどこに dotfiles が在るか(または据える口が在るか)は、この会話からは
    確かめられていない** — 実装段の最初の 1 手がこれ(§7 の手順 0)。

---

## 4. 既に在る家の後追い(依頼書 §4-4)

**後追いの仕組みを作らない。** 据え付けが起動の拍ごとなので、会社 Mac に既に在る 31 個の家は
**次にその家で席が起きた拍**に直る。家を作り直さない(`.claude.json` の trust・projects・
sessions を捨てない)。reconciler を足さない(= card が案 B として退けた「外から書く係」を
作らない)。

「直ったこと」の観測は 2 点:
- 起動の拍の 1 行(`seat-instructions-installed memory=<書いた byte 数> skills=<指した先>`)。
- node の行の labels(`seat-memory` / `seat-skills`)。

---

## 5. 責務と公開契約

| module | 責務 / owns | hides | 公開契約 | effects | lifetime | 不変量 |
| --- | --- | --- | --- | --- | --- | --- |
| **dotfiles(正本)** | 共通 CLAUDE.md と skills の**中身** | doeff の据え付けの手順・家の綴り | file が在る/読める(不在も正規) | なし | checkout の寿命 | 中身の正本はここ 1 つ |
| **機体の参加宣言** | その宿で正本が**どこに在るか**(2 鍵) | 中身・据え付けの手順 | toml / yaml の 2 鍵(絶対 path か `~/…`) | なし | 宿の設営の寿命 | 宿ごとの差はここ 1 点 |
| **`acp/join.hy`**(判断) | 宣言の**読みと門** | file の I/O・家 | `claude-memory-file-of` / `claude-skills-dir-of`(純関数・形の誤りだけを断る) | なし(純) | 参加の拍 | 不在で参加を断らない |
| **`acp/effects.py`** | env 鍵の**綴り** | 値 | `CLAUDE_MEMORY_FILE_ENV` / `CLAUDE_SKILLS_DIR_ENV` + `AgentdSettings` の 2 欄 | なし | process | 綴りの定義点はここ 1 つ |
| **`launch.hy` / `headless.hy`** | **起動の拍ごとの読み**と不在の名乗り | 家の綴り・書き方 | `claude-instruction-sources` → params 2 欄 | `EnvGet` / `FsReadText` / `LogLine` | 起動の拍 | 拍ごとに読む(memoize しない) |
| **`impls/claude_code.hy`** | 家の**綴り**と据え付け(書く/張る)と `claudeMdExcludes` の導出 | 宣言・行・ACP | `claude-pre-launch` の戻り(identity)+ `build-claude-argv` の `--settings` | `Fs*` / `EnvGet` のみ(substrate-clean) | 起動の拍 | 家への書き口はこの 1 か所 |

**隠す知識の線**:
- `join.hy` は家を知らない(path の**形**だけを見る)。
- `impls/claude_code.hy` は宣言も行も ACP も知らない(運ばれてきた path を実体化するだけ —
  `memory_files` の層と同じ規律で、この module は substrate-clean = 生 IO 禁止)。
- **どちらの層も「中身」を判断しない**(dotfiles の条文を doeff が読んで分岐しない)。

**停止・解放の所有者**: 据え付けは冪等で状態を持たないので解放は無い。家の掃除
(`<家>/skills` を外す・`CLAUDE.md` を消す)**の動詞はこの層に置かない** —
`memory_files` の層と同じ判断(「消す動詞をこの層に置かない」)。宣言から鍵を外した日の
家は、外した拍の中身を持ったまま残る(戻し方は §8)。

---

## 6. 強制方法(どこで・何が・違反をどう止めるか)

| 守る責務 | 強制方法 | 実装箇所(予定) | 実行経路 | 限界 |
| --- | --- | --- | --- | --- |
| 綴りの定義点が 1 つ | `.semgrep.yaml` に `claudeMdExcludes` / `"CLAUDE.md"` / `"skills"` の第 2 の綴りを禁じる規則(既存 `autoMemoryDirectory` 規則と同形・severity ERROR) | doeff `.semgrep.yaml` + `docs/adr/enforcement-ledger.json`(R5: 同じ便で台帳を上げる) | `make lint-semgrep` / pre-commit | 文字列連結で組めば逃げられる。逃げ道は下の deftest が家の中の file を数えて塞ぐ |
| 宣言が doeff の鍵を持たない | 既存の参加の門 (c)(`CLAUDE-SETTINGS-OWNED-KEYS` に新しい鍵を足すだけ) | `acp/join.hy` + `impls/claude_code.hy` | join の拍(fail-loud) | 集合に足し忘れると門が緩む ⇒ 下の deftest が集合を反射で読む |
| 宣言 → 家まで届く | deftest(`sessionhost_charter_reaches_the_seat_deftests.hy` と同じ流儀): env を据え、launch / headless の**両方の腕**を通し、最後に**家の中の file を数える** | doeff `packages/doeff-agents/tests/sessionhost_seat_home_instructions_deftests.hy`(新規) | `uv run pytest` の焦点走行 + 日次 | 実際の claude を起こさない(本体の読みは下の計器が受け持つ) |
| **本体が実体 file を要求する契約** | **据わっている本体から逐語を読む計器**(3 値: 同定不能 = 棄権 / 逐語が揃う = 緑 / 動いた = 赤)。読む逐語 = §2.1・§2.3・§2.2・§2.4 の 4 本 | dotfiles `agent/tests/check_native_claude_home_contract.py`(新規・`MACHINE_BOUND` 宣言つき) | `AI_CHECK_LAYER=all`(commit 経路)+ 機体の全走。⚠ tree の層では走らせない | 版が上がった日に**赤で知る**形で、先回りはしない |
| 宿の名簿のもれなさ | 母集団を**列挙せず**導く針(`[agentd-join.agentd]` を持つ `cron_management/*.toml` 全部) | dotfiles `agent/tests/test_acp_single_mac.py::test_every_seat_starting_declaration_names_the_common_instruction_sources` | dotfiles の fast 層 | pod の宣言は ACP repo に在るので母集団に入らない(隣 repo を読まない)⇒ pod 側は §7 の手順 4 の実射で受ける |
| 二重読みが戻らない | argv の**出口**を見る deftest(`autocompact-arg-pair-ok` と同じ向き: 導出点の戻りではなく `--settings` に**現に出る** JSON を見る) | 同上 deftest 内 | 同上 | `$HOME` が無い宿では鍵を置かない(その分岐も検で撃つ) |

**既存の compiler / 型で表せる分はそちらを使う**: 2 鍵は `AgentdSettings` の
`str` 欄(`.pyi` に載る)・`params` の欄は既存の dict 契約のまま(`claude_settings` と同格)。
`defk` の `:pre` / `:post` で形を縛る。行数制限は使わない。

---

## 7. 実装段の手順(実装依頼書に写す)

0. **測る(pod)**: pod の agentd の容器の中で dotfiles(または共通の条文と skills)がどこに在るか
   実射で確かめる。**無ければ**「据える口を 1 つ決める」が実装の最初の判断になる
   (この会話では確かめられていない — §3.6 の ⚠)。
1. **赤い検を先に置く**(TDD): §6 の deftest と dotfiles の針・計器を、赤いまま commit。
2. **semgrep の規則**を置き、既存 code に対して**撃って赤が出ること**を確かめる。
   `docs/adr/enforcement-ledger.json` を同じ便で上げる(ADR-DOE-ENFORCE-001 R5)。
3. **doeff 側の実装**(§3.3 の連鎖の 5 file)。
4. **宿の宣言**は doeff が据わった後に当てる(§3.5 の崖)。順は
   会社 Mac → pod → 個人 MacBook。各宿で `node の行の spec.agentd.revision` を先に読む。
5. **受入の実射**: 実際の agentd の席を 1 本ずつ起こし、node の行の labels と
   **席の context**(user 層の札で共通 CLAUDE.md が載っている・dotfiles の skill が
   一覧に在る)を読む。mock では受入にならない。

**テストの制約(依頼書 §7 の引き写し)**: 開発中に撃つのは**触った file の焦点の検だけ・1 分以内**。
全数テストと全体検証は 1 日 1 回の日次便に任せ、周回ごとに回さない。型検査は日次と手での名指しだけ。

---

## 8. 戻せる決定と戻し方(two-way door)

| 決定 | 戻し方 |
| --- | --- |
| CLAUDE.md を実体 file で書く | 宣言の `claude_memory_file` の行を消す(家は据えた拍の中身を持ったまま残る) |
| skills を whole-dir symlink で張る | 宣言の `claude_skills_dir` の行を消す |
| `claudeMdExcludes` を doeff が置く | `impls/claude_code.hy` の鍵を置く 1 行を外す + `CLAUDE-SETTINGS-OWNED-KEYS` から外す(argv は二重読みの形に戻る) |
| 据え付けを起動の拍ごとにする | 鋳造の拍へ移すのは**一方通行に近い**(31 個の家の後追いが必要になる)⇒ 戻さない前提で採る |

**一方通行の決定は無い**(2 鍵はどちらも「名乗らなければ今日どおり」)。

---

## 9. 結合核の突合(依頼書 §6)

機械で照合した(`evidence/coupling_core_match.py` / `evidence/coupling_core_match.log`)。

- 読んだ名簿: dotfiles `docs/coupling-core-watchlist.md` の
  `coupling-core-paths`(80 pattern)と `coupling-core-fleet-paths`(1 pattern)。
- 突き合わせた path: この設計が触ると宣言した doeff 11 / dotfiles 5 /
  agent-control-plane 1 の計 17 本。
- **結果: 当たり 0 件。**
- doeff 側: この repo は `docs/coupling-core-watchlist.md` を**持たない**(自分の名簿を
  持たない repo)ので、艦隊の区画だけで数えられる。その区画の唯一の pattern
  `.agents/land-queue.toml` をこの設計は触らない。⇒ **doeff 側も当たり 0**。
- ⇒ 法・反例・テスト・実装を 1 まとめで出荷する拘束(核に触る便の routing)は**掛からない**。
  ただし §6 の強制は核の有無と無関係に出荷する(依頼書 §7 の TDD + semgrep の規律)。


---

# 設計者が固定した主張(変更シナリオと予想した波及範囲)

- 保存時刻: 2026-09-21T05:2xZ(盲検 A・B の起動より前)
- 基準 commit: doeff `57641077306214b7b7da86708b04e9ed0524499b`
- 対象の設計: `design.md`(同 dir)

⚠ この file は盲検の返答を受けても**書き換えない**。反例を受けた結論は
`counterexamples.md` に別に書く。

## 主張の前提(すべての claim が依る)

- P1. 席を起こすのは agentd(node の側)で、正本の file はその node の checkout に在る。
- P2. 家(`CLAUDE_CONFIG_DIR`)は同じ資格の複数 session が共有し、doeff だけが書く。
- P3. 本体(Claude Code)の読み口は §2 の逐語のとおり(版が動けば §6 の計器が赤くなる)。
- P4. 宿ごとの差は参加宣言の 1 点に在る(`claude_settings_file` が既に取っている形)。

## module id(`report.json` の `modules` と対応)

`dotfiles-canon` / `host-declaration` / `join-judgment` / `agentd-effects` /
`launch-readout` / `claude-home-installer`

## 変更シナリオ(6 軸)

### S1 `storage` — 正本の置き場が checkout から**別の置き場**へ移る(applicable)

- change: 宿の共通条文と skills を、dotfiles の checkout ではなく専用の volume / OCI layer /
  `/opt/agent-canon` のような機体の別の置き場から配る(pod で dotfiles を clone しない形)。
- claim: **変わるのは `host-declaration` の 2 鍵の値だけ**。doeff 側は 1 行も変わらない。
- expected_scope: `cron_management/*.toml` の 2 行(+ pod の yaml の 2 行)。
- unchanged: `join-judgment`(形しか見ない)・`agentd-effects`・`launch-readout`・
  `claude-home-installer`(運ばれてきた path を実体化するだけ)。

### S2 `effects` — 運ぶ物が **1 種類増える**(applicable)

- change: 共通の `agents/`(subagent 定義)や `commands/` も席へ配りたくなる。
- claim: 足すのは**鍵 1 本 + 据え付け 1 行**で、判断の層(`join-judgment`)は
  「path の形」の検めを共有し、家の綴りは `claude-home-installer` の 1 か所に閉じる。
  **`launch-readout` の 4 枚の名簿を手で触らせる形にはしない**
  (d8472e1a の教訓 = 欄を 1 つ足す操作が名簿を 4 枚触らせる形は 3 度壊れた)。
- expected_scope: `agentd-effects`(綴り 1 本)・`join-judgment`(鍵の集合 1 行)・
  `claude-home-installer`(据え付け 1 行)・宣言 1 行。
- risk(自分で挙げる): **`launch-readout` にも 1 行増える**。ここが増え続けるなら、
  2 鍵を「席へ据える正本の名簿」1 つの欄へ畳む改訂が要る(設計の弱点として登記)。

### S3 `concurrency` — 同じ家で席が同時に起きる / 走行中に正本が動く(applicable)

- change: 1 つの account の家で 10 席が同時に起き、途中で dotfiles が更新される。
- claim: 書きは冪等(同じ宣言 → 同じ中身)かつ atomic(temp+rename)なので、
  **どの席も自分の起動の拍の中身を持ち、torn read は起きない**。skills は
  「既に正しければ張り替えない」ので走っている席の watcher を叩かない。
- expected_scope: 変わる責務は無い(`claude-home-installer` の内部の判断だけ)。
- unchanged: 公開契約(「家の中身は席の起動の拍で決まる」)。

### S4 `distribution` — 宿が 3 台から N 台へ・pod が複数の image 版で並ぶ(applicable)

- change: 4 台目の宿が増える / pod の replica が古い image と新しい image で混ざる。
- claim: もれなさは**針**(母集団を宣言から導く)が受け、版の崖は
  **node の行の `spec.agentd.revision`** で受ける。doeff の code は台数を知らない。
- expected_scope: 宣言 1 枚(+ 針は自動で母集団に入る)。
- unchanged: doeff 側すべて。

### S5 `hardware` — 機体の OS / arch / 本体の版が変わる(applicable)

- change: 会社 Mac が 2.1.278 → 2.2.x へ上がり、user 層の記憶の門の逐語が変わる。
- claim: **黙っては壊れない**。§6 の計器(据わっている本体から逐語を読む)が赤くなり、
  設計の前提 P3 が偽になった日が名指しで分かる。
- expected_scope: 計器が赤 → `claude-home-installer` の据え付けの形(実体 file か symlink か)を
  測り直す便が 1 本。
- unchanged: 宣言・join・env・launch の連鎖。

### S6 `simulation` — 決定的に再現したい(applicable)

- change: 実 claude を起こさずに「宣言 → 家の中の file」を検で固定したい。
- claim: `Fs*` / `EnvGet` だけの substrate-clean な層なので、既存の deftest の器で
  **家の中の file を数える**ところまで決定的に回せる(`memory_files` の検と同じ形)。
  本体の読みは実行体の逐語の計器が別に受ける(2 段に割る)。
- expected_scope: 検 1 枚 + 計器 1 枚。
- unchanged: 実装。



---

## 付録: 実コードの抜粋(判断の材料)

### いまの `--settings` の合流点(doeff が置く鍵と宣言の鍵が合流する唯一の座)
`packages/doeff-agents/src/doeff_agents/sessionhost/impls/claude_code.hy:155-245` — 実コード(現状・変更前)

```
;; claude の settings の綴り(この repo で 1 か所 — 判断は judgment.memory-home-of、値は charter の
;; `memory_dir`、綴りはここ)。実射 2026-09-20 で確かめた事実:
;;   * 効く綴りは `autoMemoryDirectory`(`memoryDir` は CLI 内部の property 名で、設定の鍵ではない —
;;     その綴りを渡した手番は黙って既定の家の置き場を作った)
;;   * `--settings` の inline JSON は flagSettings として読まれ、userSettings より先に効く
;; 反例つきの実射の記録は ADR-DOE-AGENTS-006 R11。
(setv CLAUDE-AUTO-MEMORY-DIR-SETTING "autoMemoryDirectory")
;; 既定の hook の無効化(49b3549b 傷跡)の綴り。
(setv CLAUDE-DISABLE-ALL-HOOKS-SETTING "disableAllHooks")
;; doeff が `--settings` に自分で置く鍵の集合(card acp:kanban-issue:ki-7b52bb76aa6e・ADR-DOE-AGENTS-004 R13)。
;; 機体の参加の宣言が名指した席の settings file(dotfiles claude-hooks/seat-settings.json)がこの鍵を持つと、join の
;; 参加の門 (c) が断り(acp/join.hy claude-settings-declaration-of)、build-claude-argv の合流も fail-loud で断る —
;; 定義点はこの 1 つ(綴りの家はこの file — .semgrep.yaml doeff-agents の autoMemoryDirectory の規則)。
(setv CLAUDE-SETTINGS-OWNED-KEYS #{CLAUDE-DISABLE-ALL-HOOKS-SETTING CLAUDE-AUTO-MEMORY-DIR-SETTING})
;; charter が運ぶ記憶の冊の欄(card acp:kanban-issue:ki-9fc7d4bca4dc)。綴りの正本は
;; sessionhost/acp/effects.py の CHARTER_MEMORY_FILES_KEY で、ここはその写し(検が突き合わせる)。
;; この層は行を読まない — 運ばれてきた {name, text} を置き場へ書くだけ。
(setv CLAUDE-MEMORY-FILES-KEY "memory_files")
;: 索引の file 名(計器が冊と索引を分けて数えるための綴り)。綴りの家は
;: sessionhost/acp/effects.py の MEMORY_INDEX_FILE で、ここはその写し(検が突き合わせる)。
(setv CLAUDE-MEMORY-INDEX-FILE "MEMORY.md")


(defk build-claude-argv [params]
  {:pre [(: params dict)]
   :post [(: % list)]}
  "claude の起動 argv。凍結物理:
   - `--dangerously-skip-permissions` + 既定では
     `--settings {\"disableAllHooks\":true}`(49b3549b 傷跡: 無人 session が
     config-dir 所有者の Stop-hook 連鎖で turn を切られた実障害)。
     ただし params session_hooks = \"inherit\"(daemon env knob
     DOEFF_AGENTD_SESSION_HOOKS — launch.hy が読む)ではこの pair を出さない:
     全 hook 無効化は安全側の hook(破壊的 git / pattern kill / push 門)まで
     切り、ACP 起動会話 241 母集団で発火 0 の実測(2026-08-18
     route-c03fe34745)。inherit は config-dir 所有者の hook 層が
     AGENT_SESSION_CLASS=unattended(spawn env — launch.hy)で会話種別
     self-gate することを契約前提にする。
   - effort → model の順(oracle 順序、無指定はフラグ自体を出さない)
   - model の後ろに `--autocompact <auto|tokens>` を**必ず**載せる(上の節の理由。
     値は params の欄、無ければ床)。⚠ 旧 Rust 実装はこの旗を持たない
     が、parity の基準ではない — 2026-07-06 の裁定で「Rust = oracle」は破棄され、
     canonical gate は Hy の session host(conformance/README.md 冒頭)
   - caller mcp_servers(sse)+ result channel(stdio)を単一 --mcp-config に
     まとめ、非空なら --strict-mcp-config を付ける
   - params claude_settings(dict・card acp:kanban-issue:ki-7b52bb76aa6e・ADR-004 R13: 機体の
     参加の宣言が名指した席の settings file を launch.hy / headless.hy が起動の拍ごとに読んで
     載せる)は同じ 1 つの `--settings` に合流する。doeff が置く鍵との衝突は fail-loud・
     disableAllHooks(session_hooks ≠ inherit)との同居も fail-loud。欄の無い手番の argv は
     1 byte も変わらない。
   - prompt は決して argv に載せない(live terminal transport のみ)・
     print mode(-p / --print)不使用"
  (setv args ["claude" "--dangerously-skip-permissions"])
  ;; --settings は 1 つだけ出す(2 回出すと後勝ちで片方が黙って消える)。中身は宣言の合流点:
  ;; hook の無効化(既定)と自動記憶の置き場(charter が運んだ時だけ)。両方とも無い手番では
  ;; 旗自体を出さない — 欄の無い charter の argv は今日と 1 byte も変わらない。
  (setv settings {})
  (when (!= (.get params "session_hooks") "inherit")
    (setv (get settings CLAUDE-DISABLE-ALL-HOOKS-SETTING) True))
  (setv memory-dir (.get params "memory_dir"))
  (when (and (isinstance memory-dir str) (.strip memory-dir))
    (setv (get settings CLAUDE-AUTO-MEMORY-DIR-SETTING) memory-dir))
  ;; card acp:kanban-issue:ki-7b52bb76aa6e(ADR-DOE-AGENTS-004 R13): 機体の参加の宣言が名指した席の settings
  ;; (launch / headless が起動の拍ごとに読んで params へ)を**同じ 1 つの** `--settings` に合流する。doeff が置く鍵との
  ;; 衝突は fail-loud — 黙って後勝ちにすると「hook を配ったつもりで disableAllHooks が残る」か「記憶の置き場が消える」の
  ;; どちらかが無音で起きる。空の宣言({})は「何も足さない」(argv は今日と同じ)。
  (setv declared (.get params "claude_settings"))
  (when declared
    (when (not (isinstance declared dict))
      (raise (RuntimeError
               (+ "claude_settings(席の settings の宣言)は JSON の object であること: "
                  (. (type declared) __name__)))))
    (when (in CLAUDE-DISABLE-ALL-HOOKS-SETTING settings)
      (raise (RuntimeError
               (+ "claude_settings(席の settings の宣言)は session_hooks=inherit の手番にだけ合流する — "
                  f"{CLAUDE-DISABLE-ALL-HOOKS-SETTING} と同居させると宣言した hook が黙って死ぬ"
                  "(ADR-DOE-AGENTS-004 R13・参加の門 (d) が断るはずの形)"))))
    (for [[key value] (.items declared)]
      (when (or (in key settings) (in key CLAUDE-SETTINGS-OWNED-KEYS))
        (raise (RuntimeError
                 (+ f"claude_settings の鍵 {key !r} は doeff が置く鍵と衝突する(doeff の鍵: "
                    (.join ", " (sorted CLAUDE-SETTINGS-OWNED-KEYS))
                    ")— 席の settings file は hook の登録だけを持つ(ADR-DOE-AGENTS-004 R13・参加の門 (c))"))))
      (setv (get settings key) value)))
  (when settings
    (.extend args ["--settings" (json.dumps settings :separators #("," ":"))]))
  (setv effort (.get params "effort"))
  (when effort
    (.extend args ["--effort" effort]))
  (setv model (.get params "model"))
  (when model
    (.extend args ["--model" model]))
```

### いまの PreLaunchSetup(家へ書いている既存の 2 か所 = trust と記憶の冊)
`packages/doeff-agents/src/doeff_agents/sessionhost/impls/claude_code.hy:297-345` — 実コード(現状・変更前)

```
;; ---------------------------------------------------------------------------

(defk resolve-claude-config-dir [params]
  {:pre [(: params dict)]
   :post [(: % tuple)]}
  "実効 CLAUDE_CONFIG_DIR の解決(R7: typed binding → process env →
   HOME/.claude — session_env は非 auth overlay で運搬手段ではない)。
   戻り値 #(config-dir warnings) — 明示無しは DOE-003 R3 の
   staged enforcement で warning のみ(codex と違い reject しない)。"
  (setv binding (.get params "binding"))
  (setv warnings [])
  (setv config-dir (when (is-not binding None) (.get binding "config_dir")))
  (when (is None config-dir)
    (.append warnings
             (+ "claude session launched without an explicit CLAUDE_CONFIG_DIR "
                "auth profile (ADR-DOE-AGENTS-003 R3: enforcement follows once "
                "callers migrate)"))
    (<- from-env (env-get "CLAUDE_CONFIG_DIR"))
    (setv config-dir from-env))
  (when (is None config-dir)
    (<- home (env-get "HOME"))
    (when (is None home)
      (raise (RuntimeError
               "cannot resolve CLAUDE_CONFIG_DIR: no session_env entry, no process env, no HOME")))
    (setv config-dir f"{home}/.claude"))
  #(config-dir warnings))


(defk preseed-claude-trust [config-dir work-dir]
  {:pre [(: config-dir str) (: work-dir str)]
   :post [(: % "None — trust state の書き込みのみ")]}
  "`<CLAUDE_CONFIG_DIR>/.claude.json` へ per-workspace trust を pre-seed する
   (42fb28fa 傷跡: fresh workspace の trust ダイアログ永久ハング)。
   claude は projects を cwd の REALPATH でキーする(S12: /tmp →
   /private/tmp)ため canonicalize が先。temp+rename で torn read を防ぐ。"
  (<- _ (fs-make-dirs config-dir))
  (<- trusted-dir (fs-canonical-path work-dir))
  (setv state-path f"{config-dir}/.claude.json")
  (<- raw (fs-read-text state-path))
  (setv state (if (is None raw) {} (json.loads raw)))
  (when (not (isinstance state dict))
    (raise (RuntimeError f"claude state file is not a JSON object: {state-path}")))
  (setv projects (.setdefault state "projects" {}))
  (setv project (.setdefault projects trusted-dir {}))
  (setv (get project "hasTrustDialogAccepted") True)
  (setv (get project "hasCompletedProjectOnboarding") True)
  (<- _ (fs-write-text-atomic state-path (json.dumps state) ".agentd-tmp"))
  None)

```

### いまの記憶の冊の据え付け(運ばれてきた {name,text} を置き場へ書くだけの層)
`packages/doeff-agents/src/doeff_agents/sessionhost/impls/claude_code.hy:420-456` — 実コード(現状・変更前)

```
  ;; 自動記憶の置き場は起こす前に在らせる(CLI 側も作るが、無い dir を設定で指さない)。
  ;; 判断は judgment.memory-home-of の 1 点 — ここは charter が運んだ path を実体化するだけで、
  ;; path を組まない。codex は対象外: codex の作業状態は profile dir の側の話で、
  ;; claude の auto-memory に当たる置き場を持たない。
  (setv memory-dir (.get params "memory_dir"))
  (when (and (isinstance memory-dir str) (.strip memory-dir))
    (<- _ (fs-make-dirs memory-dir))
    ;; card acp:kanban-issue:ki-9fc7d4bca4dc(法 ACP 575b1e conversation-memory-lives-in-the-row):
    ;; 記憶の正本は ACP の行で、置き場は手番ごとの写し。charter が運んできた冊(起こす側が行から読んで
    ;; 載せた — history / first_turn と同じ形)をここで実体化する。索引 MEMORY.md も同じ列に入っていて、
    ;; 行から導いた本文で毎手番上書きされる(file としての正本を持たない)。
    ;; ⚠ **行を読むのはここではない**: この module は substrate-clean(生 IO 禁止・Fs* / EnvGet だけ)なので、
    ;; ACP も記録の service も import しない。運ぶのは charter の 1 欄ちょうど。
    ;; 置き場に残った余りの file は消さない(消す動詞をこの層に置かない)— 退役した冊を書き戻さないのは
    ;; 起こす側の判断(judgment.memory-row-retired?)。
    (setv declared (list (or (.get params CLAUDE-MEMORY-FILES-KEY) [])))
    (setv written [])
    (for [book declared]
      (setv book-name (if (isinstance book dict) (.get book "name") None))
      (setv book-text (if (isinstance book dict) (.get book "text") None))
      (when (and (isinstance book-name str) (isinstance book-text str)
                 (.strip book-name) (not (in "/" book-name)) (not (.startswith book-name ".")))
        (<- _ (fs-write-text-atomic f"{memory-dir}/{book-name}" book-text ".agentd-tmp"))
        (.append written book-name)))
    ;; 計器(card acp:kanban-issue:ki-a40292ed30d9 受入 3): **器が書いた数**を起動ごとに 1 行で名乗る。
    ;; agentd 側の agent-memory-hydrated が数えるのは**行から読んだ数**なので、この 2 行が割れている
    ;; 拍(読んだ 44・書いた 0)が今回の壊れ方そのものだった — 読みの数だけでは log が成功しか言わない。
    ;; 置き場を名乗った手番は 0 冊でも名乗る(黙る拍を作らない)。索引は冊と別に数える
    ;; (行から導いた索引が落ちると、冊は在るのに読み手が古い索引を読む)。
    (setv books (lfor name written :if (!= name CLAUDE-MEMORY-INDEX-FILE) name))
    (<- _ (log-line
            (+ f"session.launch: agent-memory-written dir={memory-dir} "
               f"books={(len books)} index={(if (in CLAUDE-MEMORY-INDEX-FILE written) 1 0)} "
               f"declared={(len declared)}"))))
  (when (not (.get params "skip_trust_setup" False))
    (<- _ (preseed-claude-trust config-dir (get params "work_dir"))))
  (setv identity {"CLAUDE_CONFIG_DIR" config-dir
```

### いまの「起動の拍ごとに宣言 file を読む」use-site
`packages/doeff-agents/src/doeff_agents/sessionhost/launch.hy:811-853` — 実コード(現状・変更前。新しい 2 鍵はこれと同じ形で足す)

```
(defk claude-settings-declaration [agent-type]
  {:pre [(: agent-type str)]
   :post [(: % (| dict None))]}
  "席の settings の宣言(card acp:kanban-issue:ki-7b52bb76aa6e・ADR-DOE-AGENTS-004 R13): 機体の参加の宣言
   [agentd].claude_settings_file が名指した file(join が門を通して据えた絶対 path = env
   DOEFF_AGENTD_CLAUDE_SETTINGS_FILE — 綴りの正本は acp/effects.py CLAUDE_SETTINGS_FILE_ENV)を**起動の拍ごとに**
   読み、claude の `--settings` に合流させる dict を返す(合流点は impls/claude_code.hy build-claude-argv・鍵の衝突は
   そこで fail-loud)。session-hooks-mode と同じ use-site の流儀で env から読む。
     名指しが無い = None(今日どおり — argv は 1 byte も変わらない)
     claude 以外の kind = None(file の名は claude の settings — codex の起動は読まない)
     **file が無い = None + 名乗りの 1 行**(R13 の訂正・依頼書 §10-2): 宿は「先端で揃えられない日は image の下限へ
       戻して立つ」正規の degrade を持ち、その日の checkout に file は無い。そこで起動を止めると degrade が
       **その機体の全席の停止**に化ける(今日の欠陥より悪い)。だから起こす — ただし黙っては起こさず、起動の拍ごとに
       名前つきの 1 行(seat-settings-file-absent)を log へ出す。node の行の側の名乗りは
       judgment.node-labels-of(labels.seat-settings)。
     JSON でない / object でない = fail-loud(RuntimeError — 在るのに壊れている = 宣言そのものの誤り。
       黙って hook 無しの席を起こさない: 49b3549b と同じ「安全 hook 全滅」を無言で作らない)"
  (when (!= agent-type "claude")
    (return None))
  (<- path (env-get "DOEFF_AGENTD_CLAUDE_SETTINGS_FILE"))
  (when (not (and (isinstance path str) (.strip path)))
    (return None))
  (<- text (fs-read-text path))
  (when (is text None)
    (<- _ (log-line
            (+ f"session.launch: seat-settings-file-absent DOEFF_AGENTD_CLAUDE_SETTINGS_FILE={path} — "
               "機体の参加の宣言が名指した席の settings file が無いので、この席は hook 無しで起こす"
               "(dotfiles claude-hooks/seat-settings.json が宿の checkout に入るまで・ADR-DOE-AGENTS-004 R13)")))
    (return None))
  (try
    (setv parsed (json.loads text))
    (except [error ValueError]
      (raise (RuntimeError
               f"session.launch: DOEFF_AGENTD_CLAUDE_SETTINGS_FILE={path} は JSON であること: {error}"))))
  (when (not (isinstance parsed dict))
    (raise (RuntimeError
             (+ f"session.launch: DOEFF_AGENTD_CLAUDE_SETTINGS_FILE={path} は JSON の object であること: "
                (. (type parsed) __name__)))))
  parsed)


(defk launch-spawn-env [identity session-env]
  {:pre [(: identity (| dict None)) (: session-env dict)]
```

### いまの参加の門(宣言の読みと 3 つの門)
`packages/doeff-agents/src/doeff_agents/sessionhost/acp/join.hy:766-832` — 実コード(現状・変更前)

```
(defk claude-settings-file-of [text]
  {:pre [(: text (| str None))]
   :post [(: % (| str None))]}
  "席の settings file の名指しの読み(card acp:kanban-issue:ki-7b52bb76aa6e): 宣言 file の [agentd].claude_settings_file
   の文字列 → 綴り(strip)。無い・空 = None(名乗らない = 今日どおり)。形は絶対 path か `~` / `~/…`(agentd の HOME で
   composition root が展開)— cwd に依る相対 path は断る(どの cwd で読むかを黙って決めない)。file の読みと 3 つの門は
   runtime.join_plan(I/O)+ claude-settings-declaration-of(判断)で、**読めない file は断らない**(不在は名乗って参加する
   — R13 の訂正・依頼書 §10-2)。"
  (setv word (if (is text None) "" (.strip text)))
  (when (not word)
    (return None))
  (when (not (or (.startswith word "/") (= word "~") (.startswith word "~/")))
    (raise (ValueError (+ f"[{TABLE-AGENTD}].{KEY-CLAUDE-SETTINGS-FILE} は絶対 path か ~/… であること"
                          f"(cwd 相対は断る): {word !r}"))))
  word)


(defk claude-settings-declaration-of [text session-hooks]
  {:pre [(: text (| str None)) (: session-hooks str)]
   :post [(: % (| dict None))]}
  "参加の門の 1 点(card acp:kanban-issue:ki-7b52bb76aa6e・ADR-DOE-AGENTS-004 R13): 宣言が名指した席の settings file の
   本文(読めたもの — **読めない = None**・読みは composition root)と宣言の session_hooks → 席へ渡す settings(dict)。
   外れは ValueError(参加しない — 宣言そのものの誤りだけを断る):
     (d) session_hooks が inherit であること — disabled の宣言に settings を足しても disableAllHooks が勝って hook は
         配られない(file を効かせる前提条件)。**file の在否に依らない**(宣言 file の 2 行の食い違いなので、
         degrade で file が消えた日にも同じく誤り)。だから先に検める。
     (b) JSON の object であること(配列・数・文字列は settings ではない)
     (c) doeff が置く鍵(impls/claude_code.hy CLAUDE-SETTINGS-OWNED-KEYS = disableAllHooks と記憶の置き場の鍵)を
         **含まない**こと — 含むと argv の合流で衝突し、黙って後勝ちにすれば hook か記憶の置き場のどちらかが無音で消える
   ⚠ **file が無い(text = None)は断らない**(R13 の訂正・依頼書 §10-2): 宿の入口は「先端で揃えられない日は image の
   下限へ戻して立つ」正規の degrade を持ち、その日の checkout に file は無い。そこで参加を断ると degrade が
   **pool 全体の capacity 0** に化ける(今日の欠陥より悪い)。不在は参加して名乗る = None を返し、名乗りは
   起動の拍の 1 行(launch.claude-settings-declaration)と node の行(judgment.node-labels-of)。
   宣言しない機体(名指し無し)はこの門を通らない(今日どおり)。"
  (setv where f"[{TABLE-AGENTD}].{KEY-CLAUDE-SETTINGS-FILE}")
  ;; (d) は宣言どうしの食い違いなので file を読む前に検める(不在の日も同じく誤り)。
  (when (!= session-hooks JOIN-SESSION-HOOKS-DEFAULT)
    (raise (ValueError (+ f"{where} は [{TABLE-AGENTD}].{KEY-SESSION-HOOKS} = {JOIN-SESSION-HOOKS-DEFAULT !r} の宣言にだけ"
                          f"効く(いま {session-hooks !r})— disableAllHooks が勝って名指した hook は 1 本も配られない。"
                          "hook を配らないなら名指しの行を消す"))))
  ;; 不在 = 非致命(依頼書 §10-2)。読めた時だけ中身を検める。
  (when (is text None)
    (return None))
  (try
    (setv parsed (json.loads text))
    (except [error ValueError]
      (raise (ValueError f"{where} が名指す file は JSON であること: {error}"))))
  (when (not (isinstance parsed dict))
    (raise (ValueError (+ f"{where} が名指す file は JSON の object(settings の表)であること: "
                          (. (type parsed) __name__)))))
  (setv owned (sorted (lfor key parsed :if (in key CLAUDE-SETTINGS-OWNED-KEYS) key)))
  (when owned
    (raise (ValueError (+ f"{where} が名指す file は doeff が置く鍵を持てない({(.join ", " owned)})— "
                          "hook の無効化と記憶の置き場は doeff が `--settings` の合流点で自分で置く"
                          "(ADR-DOE-AGENTS-004 R13・衝突は黙って後勝ちにしない)"))))
  parsed)


(defk join-spec-of [argv declaration state-home]
  {:pre [(: argv JoinArgv) (: declaration JoinDeclaration) (: state-home str)]
   :post [(: % JoinSpec)]}
  "join の宣言の 1 点: argv(join の後の列)と宣言 file の木(file が無ければ空)と state の根
   (composition root が渡す・既定の置き場の材料)→ JoinSpec。flag > toml > 既定。
   server と token_file は必須。"
  (<- flags dict (flag-values-of argv))
  (<- declared dict (declared-values-of declaration))
  (<- values dict (merged-values-of flags declared))
```

### いまの semgrep の規則(第 2 の綴りを禁じる形の先例)
`.semgrep.yaml:478-500`

```yaml
    severity: ERROR
    message: >
      ADR-DOE-AGENTS-006 R11 / law auto-memory-home-is-keyed-by-the-conversation:
      the auto-memory home is DECIDED in one place (judgment.memory-home-of —
      <memory root>/<conversation id>, with neither the custody account nor the
      working directory among its arguments) and SPELLED for the CLI in one
      place (CLAUDE-AUTO-MEMORY-DIR-SETTING in sessionhost/impls/claude_code.hy).
      A second site that spells `autoMemoryDirectory` builds a memory home
      nobody declared. That is the defect this law fixes: until 2026-09-20 the
      effective key was "custody account x working directory", so one
      conversation read two disjoint memory homes in consecutive turns (measured
      on the company Mac: 30 homes, intersection 0) and a memory written 15
      minutes earlier was gone by the next turn. Carry the charter's
      `memory_dir` field through instead of respelling the setting.
    patterns:
      - pattern-regex: 'autoMemoryDirectory'
    paths:
      include:
        - /packages/doeff-agents/**
      exclude:
        - "**/sessionhost/impls/claude_code.hy"
        - "**/tests/sessionhost_impls_deftests.hy"
        - "**/tests/sessionhost_resume_deftests.hy"
```
