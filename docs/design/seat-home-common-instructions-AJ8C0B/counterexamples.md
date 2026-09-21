# 盲検 A・B の反例 — 再現・判定・修正・再検証

- 盲検の起動記録: `blind/runs.md` / 未加工の返答: `blind/A-raw.md` `blind/B-raw.md`
- 渡した入力: `blind/input-common.md`(sha256 `28affd52…`)
- 基準 commit: doeff `57641077306214b7b7da86708b04e9ed0524499b`
- 再現の記録: `counterexamples/repro_A_real_substrate.log`(**実物の substrate handler**)/
  `counterexamples/verify_B_semgrep.log`(**実物の `.semgrep.yaml`**)/
  `counterexamples/model_runs.log`(修正後の設計モデル)

**どちらの反例も成立した。設計を 3 点改める。**

---

## A — 「正本の置き場を移す」で、設計が unchanged と書いた責務が動く

### A が言ったこと

S1 の claim「正本が `/opt/agent-canon` へ移っても**変わるのは宣言の 2 鍵の値だけ・doeff 側は
1 行も変わらない**」は偽。**skills の「正本へ向け直す」動作が現行の substrate の effect 語彙で
書けない**ので、動くのは §5 の責務表に 1 行も無い `sessionhost/effects.hy` + `substrate.hy`
(+ 偽 substrate + `.pyi`)= substrate の語彙そのもの。

### 設計者の再現(逐語の転写ではなく**実物の handler**を回した)

`counterexamples/repro_A_real_substrate.py` — `real-substrate` を installer 形
(`((real-substrate "tmux") program)`)で回す。

```
## 反例 A-1 — 正本を移した日に whole-dir symlink が張り替わらない(実物の substrate)
  1st  outcome='linked'
  2nd  outcome='target-conflict'   (正本を canonB へ移した拍)
  家の link の先 = canonA
  席が読む skills = ['a-skill.md']
  ⇒ 張り替えは値 'target-conflict' で**黙って**落ち、席は旧い正本を読み続ける

## 反例 A-2 — 同じ家への同拍の書きが競う(実物の substrate)
  200 回 × 2 席: 例外 151 件 {'FileNotFoundError': 151}  torn 0 件
  ⇒ 同拍の 2 席で片方の launch が例外で落ちる(席が起きない)
```

repo の逐語も突き合わせた:

- `substrate.hy:451-470` — `(setv outcome (if same "same-entity" "target-conflict"))`。**触らない**。
- `effects.hy:700-710` — 契約も同じ(「別実体 = 触らず `target-conflict`(silent 置換はしない)」)。
- `substrate.hy:295-310` `_ensure-view-symlink` — 張り替えの意味論は**既に在る**が
  `FsComposeHomeView`(codex の profile view)の private で、effect として公開されていない。
- `substrate.hy:439-445` — `(setv tmp-path (+ path tmp-suffix))`。**書き手ごとに一意でない**。
- `substrate.hy:278-284` の view 単位の lock の註が「host は connection 毎 thread で launch を
  回すため、同一 binding の並行 launch が symlink の unlink/relink で race しないよう」と、
  この race を**既に認めている**。

### 判定 — **成立**。設計者の予想が外れた理由

「`Fs*` だけを使う」を §5 の表で**一様な能力**として書いたため、`Fs*` の**中の**政策
(`FsLinkArtifact` の「黙って置換しない」は会話 artifact の transplant 由来・
`_ensure-view-symlink` の erosion guard は codex の profile bundle 由来)が見えなかった。
これは「意図した契約の拡張」でも「配線」でもなく、**隠すはずの知識の漏洩**である:
据え付け層は自分と無関係な 2 つの責務が置いた置換 policy を知らないと書けない。

A-2 は設計の主張 S3「書きは冪等なので **lock を足さない**」を割る。しかも**今日から在る**:
`preseed-claude-trust` は共有の家の `<家>/.claude.json` を同じ固定 tmp で書いている。
この設計は起動の拍ごとに `<家>/CLAUDE.md` を足すので、**窓を広げる**側に働く。

### 修正

- **R1 — 張り替えの動詞を持つ**。`_ensure-view-symlink` の意味論を substrate の effect へ
  昇格する(仮称 `FsEnsureSymlink`)。結末は 3 値:
  `unchanged`(既に正しい・張り替えない = watcher を叩かない)/ `linked`(張った・張り替えた)/
  `occupied-by-real-entity`(実体が居る — **黙って置換しない**。呼び手が loud に落ちる)。
  置換してよいかの判断は**呼び手の持ち物**になり、`FsLinkArtifact` の artifact 由来の政策を
  据え付け層が知らずに済む。
  ⇒ **S1 の claim を訂正する**: この実装では substrate の語彙が**動詞 1 本ぶん増える**
  (宣言した契約の拡張)。それ以後の「正本の置き場を移す」は宣言の値だけで済む。
  盲検前の claim は `claims-before-blind.md` に**そのまま残す**(書き換えない)。
- **R1b — 計器は結末を名乗る**。`skills=<指した先>` は「起きなかった張り替え」を肯定する。
  `skills=<結末> target=<先>` に改める。
- **R2 — 共有の家への書きを壊れないようにする**。`FsWriteTextAtomic` の tmp を
  **書き手ごとに一意**にする(同じ dir の `mkstemp`。`tmp-suffix` は印として前置に残す)。
  ⚠ 既存の `preseed-claude-trust` の race も同時に閉じる。
  ⚠ `packages/doeff-agents/conformance/test_s12_claude_trust_preseed.py:57` は残骸の不在を
  **逐語の 1 名**で見ているので、glob へ広げる(実装の便で同時に直す)。

### 副次の所見(A が挙げ、設計者が逐語で確かめた)

`seat_settings_present` は `runtime.py:159-160` で **agentd の起動時に 1 度だけ**
`os.path.isfile` され、`node-labels-of` はその凍った値を名乗る ⇒ node の行の label は
**起動の拍の真実ではない**。新しい label を同じ形で足すなら、そう**書く**:
per-beat の真実は起動の拍の 1 行ちょうど。**既存 `seat-settings` label の凍結は
この設計の射程外の欠陥**として依頼者へ報告する(黙って直さない)。

---

## B — 静的検査を通る責務違反: 「宣言が読めない日の戻り先」を据え付け層が持つ

### B が言ったこと

設計自身の 2 条(§3.3「不在は非致命・degrade は正規の道」+ §3.6「旧側の値を運ぶ」が戻り先の
綴りを**逐語で 2 本**与えている)を素直に繋ぐと、実装者は「宣言が読めなければ旧側の値へ戻す」
候補列を書く。すると:

- 「その宿で正本がどこに在るか」が**宣言から code へ移る**(§5 の hides「宣言」が崩れる)。
- 「第 2 の読み口を作らない」に、env `HOME` → 固定 path という**宣言を経由しない経路**ができる。
- **宣言を 1 鍵も持たない宿**へ条文が届き、計器も node の label も**緑**。Mac では
  `~/.claude/CLAUDE.md` が正本への symlink なので、席の context は成功時と 1 byte も違わない。
- 受入 3(もれなさの針)は「宣言が名指しているか」しか見ないので、**赤くならない**。

### 設計者の再現(実物の `.semgrep.yaml` を scratch の git tree へ複写して撃った)

`counterexamples/verify_B_semgrep.sh` / `.log`:

```
## (1) 現行の全規則 → 違反例            Ran 18 rules on 1 file: 0 findings.
## (2) 当初 §6 が足すと宣言した規則      Ran 1 rule on 0 files: 0 findings.
## (3) 陰性対照(綴りの家の外に置く)     Ran 1 rule on 1 file: 1 finding.   ← 規則は生きている
```

(2) が 0 file なのが要点: 「`autoMemoryDirectory` 規則と**同形**」は
`exclude: "**/sessionhost/impls/claude_code.hy"` を含む(綴りの家を除外しないと正規の定義が
赤くなる)ので、**違反をその file の中に置けば静的検査は原理的に届かない**。

B の「`.hy` に掛かる静的検査は実質 semgrep だけ」も逐語で確かめた:

| 検査 | 逐語 | 届くか |
| --- | --- | --- |
| ruff | `Makefile:87` `ruff check doeff/ tests/ packages/` | `.hy` を target にしない ⇒ **届かない** |
| pyright | `pyrightconfig.json` の `include` は `doeff` / `packages/doeff-adr/{src,tests}`・`Makefile:92` は `pyright doeff/` | `packages/doeff-agents` は **対象外** |
| doeff-linter | `packages/doeff-linter/src/lib.rs:186` `extension().map_or(false, |e| e == "py")` | `.py` だけ ⇒ **届かない** |
| semgrep | `.semgrep.yaml` の generic 規則 | 届くが**綴りの家を除外する** |

さらに B の逃げ道の逐語も確かめた: `sessionhost_launch_deftests.hy:75-100` の `LaunchWorld` は
`(setv self.env {})` / `(setv self.fs {})` で始まり、`EnvGet` は `(.get world.env name)`
(同 file 324-326)⇒ **決定的世界では `HOME` が None なので戻り先の枝が 1 度も実行されない**。
本番の `EnvGet` は `os.environ`(`substrate.hy:511`)。⇒ 戻り先は**本番でだけ生きて、検の
世界では構造的に観測できない**。

### 判定 — **成立**。設計者の網が届いていなかった理由

§6 の強制は「綴りが 1 か所か」を見ていて、「**宣言していない path を読んでいないか**」を
見ていなかった。前者は綴りの家を除外しないと書けず、後者は除外の内側でも効く。

### 修正

- **R3a(lint)** — `packages/doeff-agents/src/**` に**宿の checkout の layout の綴り**
  (`dotfiles`)を禁じる規則を足す。**除外を持たない**(綴りの家の中でも発火する)。
  実測(`verify_B_semgrep.log`):
  ```
  ## (4) 修正 R3a の規則 → 違反例   Ran 1 rule on 2 files: 1 finding.
      packages/doeff-agents/src/doeff_agents/sessionhost/impls/claude_code.hy
        5┆ (setv CLAUDE-COMMON-MEMORY-FALLBACKS ["{home}/dotfiles/claude/CLAUDE.md"
  ## (5) 陽性対照(違反の 2 行を消す)  Ran 1 rule on 2 files: 0 findings.
  ```
  ⚠ 限界: `{home}/.claude/…` 側は `dotfiles` を含まないのでこの規則では落ちない。
  だから下の R3b が本体で、R3a はその補強。
- **R3b(検 — これが本体)** — **「据え付けが触る正本は宣言が名指した path ちょうど」**を
  効果の跡(`world.trace`)で撃つ検を足す。2 本:
  1. **宣言が 1 鍵も無い宿**の家は 1 byte も変わらない。
     ⚠ **世界に `HOME` を据え、戻り先の綴りに実 file を置いて**撃つ(B の逃げ道を塞ぐ)。
  2. **degrade の日**(宣言は在るが名指した file がその日の checkout に無い)に、
     触った path の集合が**宣言が名指した集合ちょうど**であること。
     ⚠ 正常な日に撃つと候補列も 1 本目で break するので**弁別できない** —
     設計者の初版はそこで弁別力が無く、違反例が素通りした
     (`model_runs.log` の再検証で判明し、条件を degrade の日へ移した)。
- **R3c** — §6 の deftest の名簿に「**宣言が無いときに届かない**」を明示で足す
  (初版は「宣言 → 家まで届く」しか持っていなかった)。

---

## 修正後の再検証(`counterexamples/model_runs.log`)

設計モデル(`model/chain.py` — 責務の割りを写した最小の実行モデル)に R1・R1b・R2・R3b を入れ、
3 方向から撃った。

| 走行 | 結果 |
| --- | --- |
| 正常例(設計の公開契約 7 本)`model/test_chain.py` | **7/7 ok** |
| 反例(修正後に成り立つべき 6 本)`model/test_counterexamples.py` | **6/6 ok** |
| 違反例が**狙った理由で**拒否される 4 本 `model/test_violations_are_rejected.py` | **4/4 ok** |

違反例の側の逐語(拒否の理由):

```
## 修正前の A-1(FsLinkArtifact の意味をそのまま使う)
  狙った理由で拒否 — 張り替わっていない: …/home/dotfiles/agent/skills
## 修正前の A-2(tmp が `path + suffix` 固定)
  狙った理由で拒否 — 同拍の書きが落ちた: 200 件 FileNotFoundError
## 盲検 B の実装(据え付け層が宿の file layout の戻り先を持つ)
  狙った理由で拒否 — 宣言が無いのに条文が届いた
  狙った理由で拒否 — 宣言が名指していない path を触った: ['…/.claude/CLAUDE.md', '…/.claude/skills', …]
```

⚠ **設計段の `passed` はここまで**(実行モデルと実物の substrate / semgrep で撃った最小実験)。
本実装の effect・検・規則は**まだ 1 行も存在しない** — `implementation-request.md` の
Phase 1〜3 が予定。

## 残る限界(正直登記)

- `model/chain.py` は責務の割りを写した**模型**で、本実装(Hy の sessionhost)ではない。
  R1 の effect が substrate の実 handler で意図どおり動くことは**実装段で測る**。
- 盲検は A・B 各 1 本ずつ。S2 / S4 / S5 / S6 の軸へ反例は撃たれていない。
- 本体(Claude Code)の読み口は pod の **2.1.263** でしか測っていない(§1.3)。
- `make lint` 全体の緑は確かめていない(この機体に doeff-linter が未導入)。
  撃ったのは semgrep ちょうど。

---

## 盲検の後に残っていた軸を撃った(S2 / S4 / S5)— 追加の修正 R5

盲検 A・B は S1 / S3 と S5(強制の網)に当たったが、`claims-before-blind.md` の
**S2 / S4 / S5 の claim は撃たれていなかった**。設計段の `passed` に未実行を混ぜないため、
3 軸それぞれに正常例と違反例を撃った(`model/test_scenario_checks.py` /
`counterexamples/model_runs.log` の §4・§5)。

### S2 — **設計者が自分で登記していた弱点が実測で成立した**

`claims-before-blind.md` の S2 には自分で risk を書いていた:
「**`launch-readout` の 4 枚の名簿を手で触らせる形にはしない**(d8472e1a の教訓)」。
初版のモデルは**まさにその形**だった — 運ぶ物を 1 種増やすと

1. `AGENTD_KEYS` の集合、2. `join_env_of` の組の列、3. `instruction_sources` の枝、
4. `install_into_home` の枝、5. 名乗りの語(`seat-<label>-<noun>-absent`)

の **5 か所**を手で触る。d8472e1a(`memory_files` が wire の受理形で落ちた便)が
「欄を 1 つ足す操作が名簿を 4 枚触らせる形は 3 度壊れた」と結論した形そのもの。

- **R5 — 運ぶ物の綴りを 1 行に畳む**。`CarriedSource{key, env, param, kind, home_name, label}`
  の名簿 `CARRIED_INSTRUCTION_SOURCES` を綴りの定義点にし、`AGENTD_KEYS` は名簿から**導く**。
  join / launch / 据え付けは名簿を**回る**。運び方は 2 種(`file-text` / `dir-link`)で、
  名簿に無い `kind` は loud に落ちる。
- 実測(`model_runs.log` §4):
  - 正常例 — 3 種目(`claude_agents_dir`)を足す変更は `chain.py` の **追加 1 行・削除 0 行**
    (`difflib` で数えた)+ 宣言 1 行。席の家に `agents` の symlink が据わり、
    `join_env_of` / `instruction_sources` / `install_into_home` の **bytecode が 1 byte も
    変わっていない**ことまで見た。
  - 違反例 — 名簿に無い運び物を宣言に書くと**参加が声を上げて断る**
    (`[agentd] に宣言に無い鍵: claude_agents_dir`)。d8472e1a の壊れ方(黙って落ちる)の逆。

### S4 — 針が母集団を導き、旧版は黙って起きない(claim は成立)

- 正常例 — 4 台目の宣言 file を置くだけで針が赤くなり(`claude_memory_file` /
  `claude_skills_dir` を名乗らせると緑)、節を持たない file は母集団の外。
  要求する鍵も**名簿から導く**ので、運ぶ物が増えた日に針が自動で厳しくなる。
- 違反例 — 鍵を知らない版の agentd は、席を**条文なしで起こさずに**参加を断る
  (`[agentd] に宣言に無い鍵: claude_memory_file, claude_skills_dir`)。
  ⇒ 版の崖は「静かな劣化」ではなく「参加の拒否」に出る(依頼書 Phase 4 の順の根拠)。

### S5 — 計器は 3 値を**弁別する**(claim は成立)

`model/check_body_contract.py`(dotfiles へ出す計器の雛形)を 3 方向へ撃った。

| 対象 | 結果 |
| --- | --- |
| 据わっている本体(2.1.263・sha256 `26d02035…`) | **green**(逐語 6 本が揃う) |
| 逐語を 1 つだけ動かした複製(`local-agent` → `local-agemt`・同長で上書き) | **red**(欠け = `the-gate-fires-only-for-local-agent`) |
| 本体でない file | **abstain**(棄権 — 赤と混ざらない) |

⇒ 「版が動いたら黙っては壊れない」は**実測で弁別された**(赤と棄権が別値であることまで見た)。

### この 3 軸で変わらなかったもの

`host-declaration` / `join-judgment` の公開契約(path の形しか見ない)・
`claude-home-installer` の「中身を判断しない」・二重読みの落とし方。
R5 は `agentd-effects` の**内部の綴りの置き方**の変更で、宣言の鍵も env の鍵も 1 つも動かない。
