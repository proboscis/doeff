;;; Executable ADR: 関数語彙は defk のみ — deff(契約つき素関数)の新設を禁止し、
;;; 既存 227 箇所を凍結台帳(ratchet)で単調減少させ、台帳が空になったら
;;; deff macro 本体を doeff-hy から削除する(終端強制)。
;;;
;;; 出自 = operator 指示 2026-08-21(逐語):
;;;   "we need defadr to disallow all deff. only use defk"
;;;
;;; 2 語彙の併存は呼び出し規約の分裂(直接呼び vs <- bind)であり、file 内の
;;; 既存慣行を写すエージェント書き手は deff を再生産し続ける — 禁止の法と
;;; 機械の針が無い限り収束しない(W1b/W2 便自身が 4 つの deff を新設した実測)。

(require doeff-adr.macros [defadr rule law])
(import doeff-adr.macros [fact interpretation counterexample])
(require doeff-hy.macros [deftest defk <-])
(import doeff [run])
(import os)
(import re)
(import pathlib [Path])


;; ---------------------------------------------------------------------------
;; 生きた probe — 移行レシピの実演: 純粋な検証ロジックは defk の退化形
;; (bind ゼロ)でそのまま書け、handler ゼロの run で直接回る。deff にしか
;; 書けない形は存在しない(表現力の反例が無いことの実行可能な証拠)。
;; ---------------------------------------------------------------------------

(defk probe-pure-validator [value]
  {:pre [(: value str)]
   :post [(: % bool)]}
  (and (> (len value) 0) (not (.startswith value "/"))))


;; ---------------------------------------------------------------------------
;; ratchet 台帳 — 2026-08-21 時点の実測(23 file・227 定義)。file 単位で
;; 「現在数 <= 台帳数」を針が強制する: 新設は必ず赤、削減は緑(台帳は
;; 変換便が同便で削る — R3)。ここに無い file の deff は 0 でなければならない。
;; 計測は textual(regex)— コメント・文字列内の「開き括弧 + deff + 空白」も
;; 数える(台帳と針が同じ物差しである限り ratchet は一貫する)。
;; ---------------------------------------------------------------------------

(setv DEFF-ROSTER
  {"docs/adr/defadr_doeff_agents_007_koine_session_surface.hy" 4
   "docs/adr/defadr_doeff_hy_003_bang_evaluation_position.hy" 1
   "packages/doeff-agents/src/doeff_agents/sessionhost/adopt.hy" 1
   "packages/doeff-agents/src/doeff_agents/sessionhost/effects.hy" 38
   "packages/doeff-agents/src/doeff_agents/sessionhost/host.hy" 35
   "packages/doeff-agents/src/doeff_agents/sessionhost/impls/channel.hy" 1
   "packages/doeff-agents/src/doeff_agents/sessionhost/impls/claude_code.hy" 2
   "packages/doeff-agents/src/doeff_agents/sessionhost/impls/codex.hy" 5
   "packages/doeff-agents/src/doeff_agents/sessionhost/impls/markers.hy" 30
   "packages/doeff-agents/src/doeff_agents/sessionhost/launch.hy" 5
   "packages/doeff-agents/src/doeff_agents/sessionhost/policy.hy" 29
   "packages/doeff-agents/src/doeff_agents/sessionhost/schema.hy" 3
   "packages/doeff-agents/src/doeff_agents/sessionhost/store.hy" 35
   "packages/doeff-agents/src/doeff_agents/sessionhost/substrate.hy" 15
   "packages/doeff-agents/src/doeff_agents/sessionhost/substrate_herdr.hy" 13
   "packages/doeff-agents/src/doeff_agents/sessionhost/turn.hy" 3
   "packages/doeff-agents/tests/sessionhost_policy_deftests.hy" 1
   "packages/doeff-agents/tests/sessionhost_resume_cross_binding_deftests.hy" 1
   "packages/doeff-agents/tests/sessionhost_substrate_deftests.hy" 1
   "packages/doeff-agents/tests/sessionhost_substrate_herdr_deftests.hy" 1
   "tests/semgrep/fixtures/python/packages/doeff-agents/src/doeff_agents/sessionhost/impls/api_limit_possessive_verbatim_forbidden.hy" 1
   "tests/semgrep/fixtures/python/packages/doeff-agents/src/doeff_agents/sessionhost/impls/provider_failure_verbatim_forbidden.hy" 1
   "tests/semgrep/fixtures/python/packages/doeff-agents/src/doeff_agents/sessionhost/turn_touches_substrate_forbidden.hy" 1})

;; 走査から除く木: 一時複製(worktree/scratchpad)・生成物・環境・
;; packages/doeff-hy(macro の所有者 — deff の定義と、その意味論を検証する
;; 自身のテスト。R4 の macro 削除までここだけは deff の字面が正当に残る)。
(setv SCAN-SKIP-PARTS
  #{".git" ".venv" ".claude" ".worktrees" "__pycache__" "node_modules"
    "dist" ".mypy_cache" ".pytest_cache" "scratchpad"})

(defk scan-deff-counts [repo-root]
  {:pre [(: repo-root Path)]
   :post [(: % dict)]}
  "repo 内 .hy の deff 定義数を file 別に数える(針と台帳の共通物差し)。"
  (setv counts {})
  (for [p (sorted (.rglob repo-root "*.hy"))]
    (setv rel (str (.relative-to p repo-root)))
    (when (or (& (set (. (.relative-to p repo-root) parts)) SCAN-SKIP-PARTS)
              (.startswith rel "packages/doeff-hy/"))
      (continue))
    (setv n (len (re.findall r"\(deff\s"
                             (.read-text p :encoding "utf-8" :errors "replace"))))
    (when (> n 0)
      (setv (get counts rel) n)))
  counts)


(defadr ADR-DOE-HY-004
  :title "関数語彙は defk のみ: deff の新設を全面禁止し(macro 所有者を除く repo 全域)、既存 227 定義は凍結台帳の ratchet で単調減少させる。変換は呼び出し規約(<- bind)込みの一括出荷で台帳を同便で削り、台帳が空になったら deff macro 本体を削除する — 2 語彙の併存は file 慣行の複製で自己増殖するため、法と針なしには収束しない"
  :status "accepted"
  :scope ["docs/adr/defadr_doeff_hy_004_defk_only.hy"
          "packages/doeff-agents"
          "docs/adr"
          "tests"]
  :problem
    [(fact
       "doeff-hy は契約つき関数の語彙を 2 つ持つ: deff(素関数・直接呼び)と defk(kleisli program・<- bind で合成)。同じ :pre/:post 契約面を持ちながら呼び出し規約だけが分裂している。"
       :evidence "packages/doeff-hy/src/doeff_hy/macros.hy:447(deff)/ :498(defk)")
     (fact
       "operator 指示 2026-08-21(逐語): we need defadr to disallow all deff. only use defk"
       :evidence "gecko 席 会話 04703743(2026-08-21 未明)")
     (fact
       "実測 2026-08-21: doeff repo の deff は 23 file・227 定義(packages 219 / docs-adr 5 / tests 3)。下流 agent-control-plane の apps は deff 0・defk 3,065 — 収束の終端状態は下流で既に本番実証済み。"
       :evidence "本 ADR の DEFF-ROSTER(走査条件同一の凍結断面)")
     (fact
       "2 語彙は放置で自己増殖する: 2026-08-20〜21 の W1b/W2 便自身が、file 内の既存慣行(admission 群・effect constructor 群が deff)を写して新しい deff を 4 つ追加した。書き手はエージェントであり、局所慣行の複製が既定動作である。"
       :evidence "doeff 92dc4fbb(admit-context-file)/ 84ada9b2(admit-workspace-seed・git-run ほか)")
     (fact
       "deff→defk の変換は定義の書き換えだけでは完結しない: 呼び出しが直接呼びから <- bind に変わるため、呼び手自身が program である必要があり、変換は呼び出し木を遡って連鎖する。機械的な一括置換は壊れる。"
       :evidence "defk 展開 = @do generator(macros.hy:498-)— 直接呼びは Program 値が返るだけで実行されない")]
  :context
    [(interpretation
       "語彙が 1 つなら呼び出し規約も 1 つで、エージェント書き手が誤る余地が構造的に消える。純粋ロジックは defk の退化形(bind ゼロ)でそのまま書け、handler ゼロの run で回る — deff にしか書けない形は無いので、統一のコストは移行だけで表現力の損失は無い。")
     (interpretation
       "big-bang 変換は呼び出し規約の連鎖ゆえに危険。ratchet(新設は針で即赤・既存は台帳で凍結・変換便が台帳を同便で削る)が、回帰ゼロと漸進燃焼を両立する唯一の形。")
     (interpretation
       "『disallow』の終端は macro の削除である。台帳が空になった時点で deff を doeff-hy から消すことだけが、将来の再導入を構造的に防ぐ。")]
  :decision
    [(rule R1 "新しい deff 定義は禁止(repo 全域 — production・tests・docs/adr。除外は macro 所有者 packages/doeff-hy のみ)。新しい関数は defk で書く。handler は defhandler、テスト本体は deftest、Python interop 境界の素の defn は本 ADR の対象外。")
     (rule R2 "既存 227 定義は DEFF-ROSTER に凍結する。針は file 単位で 現在数 <= 台帳数 を強制し、台帳外 file の deff は 0 を強制する — いかなる新設・移設も赤。")
     (rule R3 "変換(burn-down)は、定義の defk 化と全呼び出し site の <- bind 化と DEFF-ROSTER の該当行の削減を 1 便で一括出荷する。台帳の減少と実削除は常に同期する。")
     (rule R4 "DEFF-ROSTER が空になったら、deff macro 本体とその意味論テストを packages/doeff-hy から削除する(終端強制)。それまで macro は既存 227 の動作保証のため残す。")
     (rule R5 "台帳の増額・除外の新設は operator 裁定のみ。針の走査条件(SCAN-SKIP-PARTS・doeff-hy 除外)の変更も同様。")]
  :laws
    [(law defk-only-vocabulary
       :statement "for_all hy_file f in repo \\ {packages/doeff-hy}: count_deff(f) <= DEFF-ROSTER.get(f, 0) — 台帳は単調非増加であり、新しい deff は存在できない"
       :counterexamples
         [(counterexample "2026-08-20 W1b 便が admit-context-file を deff で新設 — file 慣行の複製が deff を再生産する(針が無ければ収束しない)実測")
          (counterexample "deff→defk の機械一括置換 — 直接呼びの site は Program 値を受け取るだけで実行されず、静かに no-op 化する(呼び出し規約の連鎖を無視した変換は壊れる)")])]
  :enforcement
    [(deftest test-adr-doe-hy-004-deff-ratchet
       ;; 針: 実測 = scan-deff-counts、法 = DEFF-ROSTER との file 単位比較。
       (setv repo-root (. (Path __file__) parent parent parent))
       (setv counts (run (scan-deff-counts repo-root)))
       (setv violations [])
       (for [[rel n] (sorted (.items counts))]
         (setv allowed (.get DEFF-ROSTER rel 0))
         (when (> n allowed)
           (.append violations f"{rel}: {n} > 台帳 {allowed}")))
       (assert (= violations [])
               (+ "新しい deff の定義は禁止(ADR-DOE-HY-004 R1 — defk で書く。"
                  "既存の変換は R3 の一括出荷で台帳を同便で削る): "
                  (str violations)))
       ;; 台帳の腐り検知: 台帳に居るのに実体が台帳より大きく減った file は
       ;; 台帳の削り忘れ(R3 の同期違反)— 警告でなく赤にする(腐った台帳は
       ;; 次の新設をその file 内で 1 つ隠す)。
       (setv stale [])
       (for [[rel allowed] (sorted (.items DEFF-ROSTER))]
         (setv n (.get counts rel 0))
         (when (< n allowed)
           (.append stale f"{rel}: 実体 {n} < 台帳 {allowed}")))
       (assert (= stale [])
               (+ "台帳の削り忘れ(ADR-DOE-HY-004 R3 — 変換便は DEFF-ROSTER を"
                  "同便で削る): " (str stale))))
     (deftest test-adr-doe-hy-004-pure-logic-lives-in-defk
       ;; 移行レシピの実演: 純粋検証は defk の退化形で書け、handler ゼロの
       ;; run で直接回る — deff にしか書けない形は無い。
       (assert (= (run (probe-pure-validator "abc")) True))
       (assert (= (run (probe-pure-validator "/abs")) False))
       (assert (= (run (probe-pure-validator "")) False)))]
  :plans [])
