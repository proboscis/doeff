# 盲検 B の返答(未加工)

役: 静的検査を通過する責務違反の反例の生成 / 要求モデル `claude-opus-5`(effort は起動口が受け付けない)
起動: Agent 口・新規文脈 / 2026-09-21 / 所要 495 秒 / tool 36 回 / 108,225 token

---

調査を終えました。検査は推測せず、scratch harness を作って semgrep / ruff を実際に撃った上で報告します。

---

# 反例: 「宣言が読めない日の戻り先」を据え付け層が持つ

## 1. 現実的な機能要求と最小差分

### 要求(設計自身の 2 つの条文をそのまま繋ぐと出てくる)

- §3.3「**不在は非致命**。宿は『先端で揃えられない日は image の下限へ戻して立つ』正規の degrade を持ち、その日の checkout に file は無い」
- §3.6「**旧側の値を運ぶ** — Mac で偶然届いていた CLAUDE.md と同じ中身 = `~/dotfiles/claude/CLAUDE.md`。skills の正本 = `${home}/dotfiles/agent/skills`」

実装者の自然な読み: 「degrade の日に席を丸腰にしないため、宣言が読めなければ**旧側の値へ戻す**(= 今日と同じ挙動を保つ)」。§3.6 は戻り先の綴りを**逐語で 2 本**与えているので、それを既定値として code に写すのは一直線の一手です。実際に運用側の要求も立ちます — degrade した日に 10 席が条文ゼロで走るのは、この card が直そうとしている障害そのものに見えるので。

### 最小差分

**`packages/doeff-agents/src/doeff_agents/sessionhost/impls/claude_code.hy`**(据え付けの実体。§3.6 が「綴りの家」と定めた file)

```hy
;; --- 共通条文 / skills の綴り(設計どおり: この repo で 1 か所)---------------
(setv CLAUDE-USER-MEMORY-FILE "CLAUDE.md")
(setv CLAUDE-USER-SKILLS-DIR "skills")
(setv CLAUDE-MD-EXCLUDES-SETTING "claudeMdExcludes")
(setv CLAUDE-SETTINGS-OWNED-KEYS #{CLAUDE-DISABLE-ALL-HOOKS-SETTING
                                   CLAUDE-AUTO-MEMORY-DIR-SETTING
                                   CLAUDE-MD-EXCLUDES-SETTING})

;; --- ★ 違反の核: 宣言が読めない日の戻り先を doeff が持つ ---------------------
;; 設計 §3.6「旧側の値を運ぶ」。宿が degrade して宣言 file がその日の checkout に
;; 無いときに席を丸腰にしない。
(setv CLAUDE-COMMON-MEMORY-FALLBACKS ["{home}/dotfiles/claude/CLAUDE.md"
                                      "{home}/.claude/CLAUDE.md"])
(setv CLAUDE-COMMON-SKILLS-FALLBACKS ["{home}/dotfiles/agent/skills"
                                      "{home}/.claude/skills"])

(defk claude-instruction-candidates [declared shapes]
  {:pre [(: declared (| str None)) (: shapes list)]
   :post [(: % list)]}
  "宣言が名指した正本を第一候補に、旧側の値を後ろへ並べる。"
  (setv out [])
  (when (and (isinstance declared str) (.strip declared))
    (.append out (.strip declared)))
  (<- home (env-get "HOME"))
  (when (and (isinstance home str) (.strip home))
    (for [shape shapes]
      (.append out (.format shape :home home))))
  out)

(defk install-seat-instructions [config-dir params]
  {:pre [(: config-dir str) (: params dict)]
   :post [(: % dict)]}
  "共通の条文(実体 file)と skills(whole-dir symlink)を家へ据える。"
  (setv installed {"memory" 0 "memory_src" "none" "skills_src" "none"})
  (setv memory-candidates
        (! (claude-instruction-candidates (.get params "claude_memory_file")
                                          CLAUDE-COMMON-MEMORY-FALLBACKS)))
  (for [path memory-candidates]
    (<- text (fs-read-text path))
    (when (is-not text None)
      (<- _ (fs-write-text-atomic f"{config-dir}/{CLAUDE-USER-MEMORY-FILE}"
                                  text ".agentd-tmp"))
      (setv (get installed "memory") (len text))
      (setv (get installed "memory_src") path)
      (break)))
  (setv skills-candidates
        (! (claude-instruction-candidates (.get params "claude_skills_dir")
                                          CLAUDE-COMMON-SKILLS-FALLBACKS)))
  (setv target f"{config-dir}/{CLAUDE-USER-SKILLS-DIR}")
  (for [path skills-candidates]
    (<- there (fs-file-exists path))
    (when there
      (<- current (fs-canonical-path target))
      (<- wanted (fs-canonical-path path))
      (when (!= current wanted)
        (<- _ (fs-link-artifact path target)))
      (setv (get installed "skills_src") path)
      (break)))
  (<- _ (log-line
          (+ "session.launch: seat-instructions-installed "
             f"memory={(get installed "memory")} "
             f"memory_src={(get installed "memory_src")} "
             f"skills_src={(get installed "skills_src")}")))
  installed)
```

`claude-pre-launch` 内で `(<- _ (install-seat-instructions config-dir params))` を 1 行呼ぶ。`launch.hy` 側(`claude-instruction-sources` → params の 2 欄)は**設計どおり正しく**書きます — 違反は据え付け層の 1 か所に閉じています。ここが反例として厄介な点です。

この差分は設計の「形」を全部守っています: CLAUDE.md は実体 file(§2.3)、skills は whole-dir symlink で既に正しければ張り替えない(§3.5)、`Fs*` / `EnvGet` だけ(substrate-clean)、綴りは 1 か所、`claudeMdExcludes` の導出は無改変、起動の拍ごと、消す動詞なし。

---

## 2. 本来の責務所有者と、破る契約

| | |
|---|---|
| **本来の所有者** | `host-declaration`(機体の参加宣言)— §5「その宿で正本が**どこに在るか**(2 鍵)」/ 不変量「**宿ごとの差はここ 1 点**」 |
| **奪った側** | `claude-home-installer`(`impls/claude_code.hy`)— §5 hides「宣言・行・ACP」/ 「**運ばれてきた path を実体化するだけ**」 |

### 移る知識・判断

1. **「その宿で正本がどこに在るか」が宣言から code へ移る。** `{home}/dotfiles/claude/CLAUDE.md` は §3.6 が「Mac の宿の宣言に書く値」と定めたもので、doeff が知ってよい値ではありません。据え付け層が宿の file layout を知った時点で、§5 の「hides: 宣言」が崩れます。
2. **「どの正本を据えるか」の決定権が移る。** 宣言は「第一候補の提案」に格下げされ、最終決定は installer の候補列の順序が持ちます。§3.3 が明示した「**第 2 の読み口を作らない**」に第 2 の読み口ができます(env `HOME` → 固定 path、という宣言を経由しない経路)。
3. **S1 の claim が偽になる。** 「正本が `/opt/agent-canon` へ移っても**変わるのは宣言の 2 鍵の値だけ・doeff 側は 1 行も変わらない**」— 新しい置き場を宣言しても、その日その path が読めなければ doeff は黙って古い `~/dotfiles/…` を据えます。移行が「完了したのに完了していない」状態を作り、しかもそれが log 上は成功に見えます。
4. **受入条件 3(もれなさの針)の不変量が無効化される。** 針は「宣言が名指していること」を撃つだけで(実測: dotfiles `agent/tests/test_acp_single_mac.py:389` の既存同型 `test_every_seat_starting_declaration_names_the_seat_settings_file` は `agentd.get(...) == "${home}/dotfiles/..."` と「その file がこの checkout に在る」しか見ない)、**席が現に何を読んだか**は見ません。fallback があると、宣言を書き忘れた宿でも席には条文が届くので、「3 台目の宿を足した日に書き忘れれば赤くなる」という受入 3 の設計意図が、赤くならないまま成立しなくなります。§6 が自分で挙げた限界(「pod の宣言は ACP repo に在るので母集団に入らない」)が、そのまま穴になります。
5. **障害の形が変わるだけで消えない。** §1.2 の「席は立っている・条文は届いていない」が「席は立っている・**誰も宣言していない条文が**届いている」に化けます。§4 の観測点 2 つ(`seat-instructions-installed memory=<byte>` と node の行の labels `seat-memory=present`)は**どちらも緑**です。とくに Mac では `~/.claude/CLAUDE.md` が §3.6 の表どおり `~/dotfiles/claude/CLAUDE.md` への symlink なので、fallback が発火しても**席の context は成功時と 1 byte も違いません** — 受入 1 の実射(§7 手順 5)でも成功と区別できません。
6. **資格境界の面**(副次): skills の最終 fallback `{home}/.claude/skills` は有人 profile の置き場で、無人席が有人 profile の持ち物を読むことになります。P2「家は doeff だけが書く」の読み側の対です。

---

## 3. 提示された各検査が拒否しない理由(印: 実測 / 実読)

再現 harness を `/tmp/ce-AJ8C0B` に作り、`.semgrep.yaml` を repo からそのまま複写して撃ちました。harness の忠実さは陽性対照で先に確認しています(`impls/` 配下に `(import subprocess)` を置くと `doeff-agents-substrate-clean-impls` が `.hy` / `.py` 両方で発火)。

| 検査 | 結果 | 理由 |
|---|---|---|
| **semgrep(現行 261 規則全部)** | **実測 0 findings**(該当 19 規則・2 target) | `doeff-agents-substrate-clean-impls`(`.semgrep.yaml:455-473`)が禁じるのは `subprocess` / `sqlite3` / `os.system` / `open(` の 4 本だけ。反例は `env-get` / `fs-read-text` / `fs-write-text-atomic` / `fs-file-exists` / `fs-link-artifact` しか使わず、substrate-clean の定義を**完全に**満たします。`env-get "HOME"` から path を組む前例は既存 `resolve-claude-config-dir` に在り、今日も緑です |
| **semgrep(§6 が足すと宣言した規則)** | **実測 0 findings**。陰性対照として `launch.hy` に `"CLAUDE.md"` を 1 行足すと**発火する**ことも実測 | §6 は「既存 `autoMemoryDirectory` 規則と**同形**」と明記。同形なら `exclude: "**/sessionhost/impls/claude_code.hy"` を持ちます(綴りの家そのものを除外しないと正規の定義が赤くなるため)。反例は**その除外された file の中**に在ります。加えて fallback は `"{home}/dotfiles/claude/CLAUDE.md"` という path 形で、`"CLAUDE.md"` という**引用符つき literal**にすら一致しません |
| **ruff** | **実測「No Python files found」** | `make lint-ruff` = `ruff check doeff/ tests/ packages/`。sessionhost の実装層は全部 `.hy` で、ruff は `.hy` を target にしません |
| **pyright** | **実読** | `pyrightconfig.json` の `include` は `doeff` / `packages/doeff-adr/{src,tests}` のみ。`packages/doeff-agents/src` は `extraPaths`(解決用)にしか無く、**検査対象ではありません**。`make lint-pyright` も `uv run pyright doeff/` |
| **doeff-linter** | **実読** | `packages/doeff-linter/src/lib.rs:183-207` の `collect_python_files_with_options` は `extension() == "py"` だけを採る。`.hy` は 1 file も見ません |
| **pre-commit** | **実読** | `.pre-commit-config.yaml` の `semgrep-hy` は `.hy` を generic 規則へ渡すだけ、`doeff-linter` は `types: [python]`。上 2 行と同じ結論 |
| **§6 の deftest(「家の中の file を数える」)** | **実読・未実行**(未実装のため) | 器は `LaunchWorld`(`packages/doeff-agents/tests/sessionhost_launch_deftests.hy:75-119`)。**`self.env = {}` / `self.fs = {}` で始まり**、`EnvGet` は `(.get world.env name)`、`FsReadText` は `(.get world.fs path)`(同 file 296-324)。⇒ 決定的世界では `HOME` が **None** なので候補列は宣言 1 本だけになり、**fallback の枝は 1 度も実行されません**。宣言を据えた検は緑、宣言を据えない検(「家は 1 byte も変わらない」)も緑。一方、本番の `EnvGet` は `(.get os.environ name)`(`substrate.hy:511`)で `HOME` は必ず在る ⇒ **fallback は本番でだけ生きて、検の世界では構造的に観測できません** |
| **§6 の二重読み deftest(argv の `--settings` を見る)** | 該当なし | 反例は `claudeMdExcludes` の導出を一切触りません |
| **§6 の本体逐語の計器(dotfiles `check_native_claude_home_contract.py`)** | 該当なし | 読むのは据わっている Claude Code 実行体の逐語(§2.1/2.2/2.3/2.4)。doeff の code を 1 行も見ません |
| **§6 の宿の名簿の針** | 該当なし(上の 4 参照) | 宣言 toml しか読まず、席が何を読んだかを見ません |
| **参加の門 (c)(`CLAUDE-SETTINGS-OWNED-KEYS`)** | 該当なし | 反例は集合に `claudeMdExcludes` を正しく足しています |
| **`doeff-agents-charter-carried-key-hand-written-in-a-roster`** | 該当なし | 2 鍵は charter 経由ではなく env → launch 経由(§3.3 の連鎖)。regex は `"(auto_compact_window|memory_dir|memory_files)"\s+\(` のみ |

**要点**: この repo では `packages/doeff-agents` の `.hy` に掛かる静的検査は**実質 semgrep の generic regex 規則だけ**で(ruff / pyright / doeff-linter はいずれも `.hy` または当該 path を見ない — 3 つとも実測または実読で確認)、その semgrep は「綴りの家」を除外しなければ成立しません。責務違反を**綴りの家の中に**置けば、静的検査は原理的に届きません。

---

## 4. 同じ設定で「検査通過」と「責務違反」の両方を確かめる最小手順

### A) 検査通過(実測済み・そのまま再実行可能)

```bash
CE=/tmp/ce-AJ8C0B                      # 既に作成済み。repo は一切触らない
cd $CE && git add -A
# 現行の全規則
semgrep --metrics=off --disable-version-check --config .semgrep.yaml packages/ --error
#   → 実測: Ran 19 rules on 2 files: 0 findings.
# §6 が足すと宣言した規則(autoMemoryDirectory 規則と同形で再構成)
semgrep --metrics=off --disable-version-check --config proposed.yaml packages/ --error
#   → 実測: Ran 1 rule on 1 file: 0 findings.
# 陰性対照(規則が死んでいないことの確認)
printf '\n(setv X "CLAUDE.md")\n' >> packages/doeff-agents/src/doeff_agents/sessionhost/launch.hy
git add -A && semgrep --config proposed.yaml packages/ --error
#   → 実測: Findings: 1 (1 blocking) at launch.hy
```

ruff の盲点も実測済み: `uv run ruff check $CE/packages/` → `warning: No Python files found under the given path(s) / All checks passed!`

### B) 責務違反(§6 の deftest の器で撃てる最小入力 — **未実行**)

`LaunchWorld` に対して:

```hy
(setv world (LaunchWorld))
(setv (get world.env "HOME") "/Users/masui")
(setv (get world.fs "/Users/masui/dotfiles/claude/CLAUDE.md") "COMMON-CANON")
;; ★ 宣言 env(DOEFF_AGENTD_CLAUDE_MEMORY_FILE)は **据えない** = 名指しが無い機体
(<- row (run-launch world (launch-params :agent_type "claude")))
```

- **設計どおりの期待結果**(§3.3「名指しが無い機体は今日どおり — 家は 1 byte も変わらない」):
  `world.fs` に `<config-dir>/CLAUDE.md` の鍵が**現れない**。log に `seat-instructions-installed` が出ない。
- **反例の実際**:
  `(get world.fs f"{config-dir}/CLAUDE.md") == "COMMON-CANON"`、log に
  `seat-instructions-installed memory=12 memory_src=/Users/masui/dotfiles/claude/CLAUDE.md`。
  = 宣言が 1 鍵も無い宿へ条文が届き、計器は成功を名乗る。

この B の検は **§6 の名簿に無い**(「宣言 → 家まで届く」を撃つ検だけが在り、「**宣言が無いときに届かない**」を撃つ検が無い)ため、通常の実装なら誰も書きません。§6 に足すべき 1 本を名指すなら、これです。

---

## 5. 未確認の前提・不足(推測と実測の区別)

**実測したもの**
- semgrep が `.hy` の generic 規則を反例に撃って 0 findings(現行 261 規則 / §6 の再構成規則)、および陰性対照の発火。
- ruff が `.hy` を target にしないこと。
- `git init` 済み scratch でないと `paths.include: /packages/...` の anchor が効かないこと(harness 構築上の注意)。

**実読(実行していない)**
- `pyrightconfig.json` の include に `packages/doeff-agents/src` が無いこと。pyright 自体は走らせていません。
- `doeff-linter` が `.py` のみを収集すること(`lib.rs:183-207`)。**この環境に doeff-linter は未導入**で、`make lint-doeff` はここでは「成功として扱いません」と出て**失敗します** ⇒ **`make lint` 全体が緑であることは私は確かめていません**。
- `LaunchWorld` の `env`/`fs` 既定が空であること、本番 `EnvGet` が `os.environ` を読むこと。
- dotfiles の既存 針 `test_every_seat_starting_declaration_names_the_seat_settings_file` の中身。

**未確認・不足**
- `uv run pytest` は**一切走らせていません**。反例が既存の検を 1 本も割らないことは未確認です(反例は既存の code path に条件分岐を足すだけなので割らないと**推測**しますが、実測ではありません)。
- §6 の deftest / 計器 / 針は**まだ 1 行も実装されていません**。私が撃ったのは「§6 の記述(『既存 `autoMemoryDirectory` 規則と同形』)から再構成した規則」であって、実装段が書く実物ではありません。実物が `exclude` を持たない形になれば反例は赤くなりますが、その場合は正規の定義(`CLAUDE-USER-MEMORY-FILE "CLAUDE.md"`)自身も赤くなるので、同形である限り除外は要ります。
- 本体(Claude Code)の読み口(§2 の逐語・P3)はこの会話では一切検証していません。§1.3 の未確認事項(Mac 2.1.278・managed 層)はそのままです。
- `~/.claude/skills` が会社 Mac に実在するか、`fs-canonical-path` が不在 target に何を返すかは未確認です(反例の skills 側の細部はこれに依存しますが、CLAUDE.md 側の違反は依存しません)。

**結論として、私はこの反例 1 本しか具体化しておらず、他の箇所が安全であるとは一切言えません。** 探索した範囲は `.semgrep.yaml` の全規則・`Makefile` の lint 系 target・`pyrightconfig.json`・`.pre-commit-config.yaml`・`doeff-linter` の収集経路・`packages/doeff-agents/tests/` の deftest 器(`LaunchWorld` と charter-reaches-the-seat)・dotfiles の既存針 1 本です。`packages/doeff-agents/tests/` の全数、ADR の法(`docs/adr/defadr_*.hy` の `law`/`deftest`)、および ACP repo 側の検査は**見ていません** — とくに ADR の `law` 群は未探索で、そこに責務を縛る条文が在る可能性があります。

**共有 source は 1 file も変更していません**(作業は `/tmp/ce-AJ8C0B` の scratch のみ)。他会話への連絡も行っていません。
