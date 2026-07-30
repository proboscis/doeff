;;; Executable ADR: readiness/idle 判定物理の単一の家 — ADR-004
;;; protocol-physics-has-one-home 法の readiness への具体化と enforcement 供給。

(require doeff-adr.macros [defadr defsemgrep rule law])
(require doeff-hy.macros [deftest])
(import doeff-adr.macros [fact interpretation counterexample])


(defadr ADR-DOE-AGENTS-008
  :title "readiness/idle 判定の物理事実(pattern 文字列・trust-prompt 述語・repl-idle 予算既定値)は定義箇所を 1 箇所に限る — gate 形式は doeff-free leaf の sessionhost/impls/ready_physics.hy、observation 形式は impls/markers.hy、予算既定値は effects.hy MonitorKnobs 語彙。消費者(adapters / session.py / launch.hy / host.hy)は import 参照のみ"
  :status "accepted"
  :scope ["packages/doeff-agents/src/doeff_agents/adapters"
          "packages/doeff-agents/src/doeff_agents/session.py"
          "packages/doeff-agents/src/doeff_agents/sessionhost"]
  :problem
    [(fact
       "claude の readiness が 2 つの生きた家に fork していた: adapters/claude.py の CLAUDE_READY_PATTERN(screen-reader mode の footer 「shift+tab to cycle」)と markers.hy の has-idle-prompt(通常 TUI mode の行頭 `❯`)。両 stack は起動 argv 自体も fork している(legacy = --ax-screen-reader 付き / sessionhost impls/claude_code.hy = なし)— 同一 kind の物理が 2 実装に散り『見ていた方だけ直る』ADR-004 の反例そのもの(2026-07-05 trust-dialog 永久ハングの構造)。"
       :evidence "adapters/claude.py CLAUDE_READY_PATTERN(移動前 :20)/ sessionhost/impls/markers.hy has-idle-prompt / impls/claude_code.hy build-claude-argv")
     (fact
       "codex の readiness は同一 TUI mode の物理が 2 形式で二重定義されていた: adapters/codex.py の CODEX_READY_PATTERN(単発 gate 用 regex — MCP-boot 排他 \\A(?!...) + menu 選択 marker 排他 (?!\\d+\\.) 込み)と markers.hy の述語群(has-idle-prompt + is-starting-mcp-servers + detect-dialog 優先順)。片方だけの修正で無音 drift する。"
       :evidence "adapters/codex.py CODEX_READY_PATTERN(移動前 :23)/ markers.hy has-idle-prompt・is-starting-mcp-servers・detect-dialog")
     (fact
       "repl-idle 予算 120s の literal が 2 箇所に定義されていた: launch.hy(コメントは退役 Rust の Duration::from_secs(120) を『oracle 定数』と呼び複製を正当化 — ADR-004 R7/U1『退役 Rust は正しさの基準ではない』と矛盾する言語構造)と effects.hy MonitorKnobs 既定値。boot watchdog 予算(launch timeout + repl-idle 予算)は両者が同値であることに依存しており、drift は watchdog の誤裁定になる。"
       :evidence "sessionhost/launch.hy(移動前 :79-81)/ sessionhost/effects.hy MonitorKnobs repl-idle-max-wait-seconds / sessionhost/host.hy boot watchdog 予算材料")
     (fact
       "screen-reader trust prompt の物理(version-stable な y/n 行 3 点一致)が session.py に孤立していた — markers.hy の R9 trust dialog(通常 mode)の sibling が別の家に居る形。一本化の検定作図で第 2 の divergence も発見(登記のみ・修正は follow-up): verbatim first-paint capture(claude_screen_reader_trust_dialog.txt)は『Please answer y or n.』を含まず、この述語は初回描画では match しない — 実運用の初回 dismissal は generic onboarding pattern(『Yes, I trust this folder』)側が拾っており、本述語が効くのは入力拒否後の re-prompt のみ。"
       :evidence "session.py _screen_reader_trust_prompt_visible(移動前 :467)/ tests/data/ready_screens/claude_screen_reader_trust_dialog.txt(first-paint に『Please answer y or n.』非在)")
     (fact
       "一本化の作図中に実 divergence を発見(本 ADR では登記のみ・修正は別 issue): codex trust-dialog frame(`› 1. Yes, continue`)を gate regex は menu 先読みで拒否するが、has-idle-prompt 単独は composer と誤認して受理する。markers.hy の detect-dialog に codex trust 検出は無い(sessionhost は per-kind pre-launch が workspace を事前 trust するため通常この frame に到達しない)が、skip_trust_setup 経路と trust 失敗時に wait-for-repl-idle が prompt を trust menu へ paste しうる潜在穴。fail-open 挙動を expected に固定する歴史ピンは置かない(ADR-004 R8 と同判断)。"
       :evidence "tests/data/ready_screens/codex_trust_dialog.txt(verbatim: `› 1. Yes, continue`)/ launch.hy wait-for-repl-idle / impls/codex.hy codex-pre-launch")]
  :context
    [(interpretation
       "本 ADR は新法ではなく、受理済み ADR-DOE-AGENTS-004 の law protocol-physics-has-one-home の readiness 物理への具体化 + enforcement 供給である。『家が 1 つ』とは各物理事実の定義箇所が 1 箇所という意味であり、形式(gate 用 regex と observation 用述語)の複数性そのものは禁じない — 形式間の一致は parity 検定で機械執行する。")
     (interpretation
       "gate 形式の家を markers.hy でなく doeff-free leaf(ready_physics.hy)に分けるのは、package root が明文で保つ『命令型 session transport API は doeff VM を import せずに動く』性質のため: markers.hy は effects.hy(→ doeff)を引き込むので、adapters から直接 import すると transport の import graph に VM が入る。leaf は import ゼロ(hy 言語ランタイムのみ)。")
     (interpretation
       "claude の 2 mode fork(screen-reader vs 通常 TUI)は『物理が 2 つある』のではなく『同一 kind の 2 つの描画 mode の物理』— 家が 1 つなら両事実は並んで文書化され drift が構造的に不可能になる。mode の統一(= legacy 命令型 stack の退役・ADR-004 R2 の in-process defhandler 束縛への合流)は別 issue の follow-up。gemini は sessionhost impl を持たない legacy-only kind で物理の定義は既に 1 箇所(gemini.py)のみ — 重複が無いため移動しない(sessionhost に偽の家を作らない)。")]
  :decision
    [(rule R1 "readiness のテキスト物理は定義 1 箇所: gate 形式(ready pattern 文字列・screen-reader trust prompt 述語)= sessionhost/impls/ready_physics.hy(doeff-free leaf)、observation 形式(has-idle-prompt / dialog 検出等)= sessionhost/impls/markers.hy。adapters / session.py は import 参照のみで、pattern literal の再定義は installed semgrep rule doeff-agents-ready-pattern-literal-outside-physics-home が ban する。")
     (rule R2 "repl-idle 予算既定値(120s)の literal は sessionhost/effects.hy(MonitorKnobs knob 語彙)にのみ置く。launch.hy(ready gate 予算 fallback)と host.hy(boot watchdog 予算材料)は import 参照 — installed semgrep rule doeff-agents-repl-idle-budget-literal-single-home が sessionhost 内の再定義を ban する。")
     (rule R3 "codex の gate regex と observation 述語は同一物理の 2 形式であり、両 gate が定義される全 fixture frame(ready / mcp-boot / login / update-dialog)で判定が一致すること(parity)。一致検定は tests/test_ready_physics_single_home.py が verbatim fixture で執行する。既知 divergence(trust-dialog frame)は problem fact 5 の別 issue で解消するまで parity 対象外 — expected に固定しない。")
     (rule R4 "退役 Rust(rollback tag agentd-rust-final に保存)を readiness 物理の『oracle』と呼ぶ文言は本 ADR が触った物理帯(ready_physics.hy / markers.hy header / launch.hy 予算コメント / adapters)から除去した。出自表記は『退役 Rust 移植出典(rollback 専用・正しさの基準ではない — ADR-004 R7/U1)』とし、物理の正当性根拠は conformance fixture(tests/data/ready_screens/ の verbatim capture)と conformance suite に置く。残余モジュール(policy.hy / host.hy / store.hy / substrate.hy 等)の同種文言の一掃は follow-up。")]
  :laws
    [(law readiness-facts-have-one-defining-module
       :statement "readiness_fact(kind, mode) => exactly_one_defining_module; consumers_import_never_redefine"
       :counterexamples
         [(counterexample "adapter に ready pattern literal を再定義し、markers/ready_physics 側だけ修正されて無音 drift する(claude trust-hang 2026-07-05 の再演構造)")
          (counterexample "repl-idle 予算 120 を新モジュールに literal でコピーし、『退役 Rust の定数と同じ値だから』とコメントで正当化する")
          (counterexample "gemini など単一の家しか無い kind の物理を sessionhost へ複製して『統一』と称する(偽の家 — 重複の新設)")])
     (law gate-and-observation-formalisms-must-agree
       :statement "same_fact_in_two_formalisms => machine_checked_parity_on_shared_frames; divergence_is_red_not_documentation"
       :counterexamples
         [(counterexample "codex gate regex だけ修正して markers 述語を放置する(またはその逆)— parity 検定が red になるべき変更")
          (counterexample "発見済み divergence を expected 値としてテストに固定し、修正 issue を立てない(fail-open の歴史ピン — ADR-004 R8 違反)")])]
  :enforcement
    [(deftest test-adr-doe-agents-008-adapters-import-canonical-patterns
       ;; 家の identity: adapter property が返すのは leaf の定数オブジェクト
       ;; そのもの(is 一致)。literal 再定義では同一オブジェクトにならない。
       (import doeff_agents.adapters.claude [ClaudeAdapter])
       (import doeff_agents.adapters.codex [CodexAdapter])
       (import doeff_agents.sessionhost.impls.ready-physics :as ready-physics)
       (assert (is (. (ClaudeAdapter) ready-pattern)
                   ready-physics.CLAUDE-SCREEN-READER-READY-PATTERN))
       (assert (is (. (CodexAdapter) ready-pattern)
                   ready-physics.CODEX-READY-PATTERN)))
     (deftest test-adr-doe-agents-008-budget-single-home
       ;; 予算の一貫性: effects(家)・launch(import 先)・MonitorKnobs 既定が
       ;; 同値。literal の不在は semgrep rule + test_ready_physics_single_home
       ;; の source 検査が担う。
       (import doeff_agents.sessionhost.effects :as fx)
       (import doeff_agents.sessionhost.launch :as launch)
       (assert (= fx.REPL-IDLE-MAX-WAIT-SECONDS 120))
       (assert (= launch.REPL-IDLE-MAX-WAIT-SECONDS fx.REPL-IDLE-MAX-WAIT-SECONDS))
       (assert (= (. (fx.MonitorKnobs) repl-idle-max-wait-seconds)
                  fx.REPL-IDLE-MAX-WAIT-SECONDS)))
     (defsemgrep ready-pattern-literal-outside-home
       "doeff-agents-ready-pattern-literal-outside-physics-home"
       [{"relative-path" "packages/doeff-agents/src/doeff_agents/adapters/claude.py"
         "source" "CLAUDE_READY_PATTERN = r\"shift\\+tab to cycle\"\n"}
        {"relative-path" "packages/doeff-agents/src/doeff_agents/adapters/codex.py"
         "source" "CODEX_READY_PATTERN = r\"(?ims)composer\"\n"}]
       [{"relative-path" "packages/doeff-agents/src/doeff_agents/adapters/claude.py"
         "source" "from doeff_agents.sessionhost.impls.ready_physics import CLAUDE_SCREEN_READER_READY_PATTERN\n"}])
     (defsemgrep repl-idle-budget-literal-outside-home
       "doeff-agents-repl-idle-budget-literal-single-home"
       [{"relative-path" "packages/doeff-agents/src/doeff_agents/sessionhost/launch.hy"
         "source" ";; 予算の再定義(旧形)\n(setv REPL-IDLE-MAX-WAIT-SECONDS 120)\n"}]
       [{"relative-path" "packages/doeff-agents/src/doeff_agents/sessionhost/launch.hy"
         "source" "(import doeff_agents.sessionhost.effects [REPL-IDLE-MAX-WAIT-SECONDS])\n"}])]
  :plans ["packages/doeff-agents/src/doeff_agents/sessionhost/impls/ready_physics.hy(gate 形式の家 — doeff-free leaf)"
          "packages/doeff-agents/src/doeff_agents/sessionhost/impls/markers.hy(observation 形式の家)"
          "packages/doeff-agents/src/doeff_agents/sessionhost/effects.hy(予算既定値の家)"
          "packages/doeff-agents/tests/test_ready_physics_single_home.py(identity + parity + source 検査)"
          "follow-up issue: codex trust-frame の fail-closed 化(problem fact 5)"
          "follow-up issue: legacy 命令型 stack の退役 / claude 起動 mode の統一 / 残余 oracle 文言の一掃"])
