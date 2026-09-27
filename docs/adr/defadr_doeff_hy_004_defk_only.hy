;;; Executable ADR: 関数語彙は defk のみ — defn を禁止し(Python との境界も含む・
;;; 例外は macro の展開の時に呼ぶ関数だけ)、deff は「defk にできない」理由の註と
;;; :tags つきの逃げ道としてだけ許す。理由の註の無い deff は凍結台帳(ratchet)で
;;; 単調減少させる。defk / deff の契約の辞書には :tags を必須にする。
;;;
;;; 出自 = operator 指示 2026-08-21(逐語):
;;;   "we need defadr to disallow all deff. only use defk"
;;; 改訂 = operator 指示 2026-09-27(Claude Code の会話・agora-redesign #798・逐語):
;;;   "and we want to forbid the use of defn and only allow defk, and in inevitable case allow deff, to force the use of tag on definitions"
;;;   (R4「台帳が空になったら deff の macro を消す」は取り下げ — deff は避けられない所の逃げ道として残る)
;;;
;;; 追記 = operator 裁定 2026-09-27(同じ会話・逐語は :problem の fact): 設定を読むこと・検の補助・framework と
;;;   process の入口は deff の理由にしない方向。deff の理由の受け入れは doeff-linter の DOEFF203(Jev)が判じる(R6〜R9)。
;;;
;;; 戻し方: この改訂の commit を revert する(ADR の条・針・台帳の数え方が 2026-08-21 版へ戻る)。
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
   :post [(: % bool)]
   :tags {:context "doeff-hy-adr" :role "judgment"}}
  (and (> (len value) 0) (not (.startswith value "/"))))


;; ---------------------------------------------------------------------------
;; ratchet 台帳 — 2026-08-21 時点の実測(23 file・227 定義)。file 単位で
;; 「現在数 <= 台帳数」を針が強制する: 新設は必ず赤、削減は緑(台帳は
;; 変換便が同便で削る — R3)。ここに無い file の deff は 0 でなければならない。
;; 計測は textual(regex)— コメント・文字列内の「開き括弧 + deff + 空白」も
;; 数える(台帳と針が同じ物差しである限り ratchet は一貫する)。
;; 2026-09-27 の改訂から、同じ行に「; defk にできない: <理由>」の註がある deff は
;; 数えない(R1 の逃げ道 — 理由を名乗った deff は台帳の外で許す)。台帳の数は
;; 改訂の時点で理由の註を持つ deff が 0 だったので変わらない。
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
   ;; 29 → 25(agora-redesign #708): 境界の env の語彙の 4 本を agent_env.hy へ defn で出した(R1 の Python との境界 — deff の焼却ではない)。
   "packages/doeff-agents/src/doeff_agents/sessionhost/policy.hy" 25
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
;; 自身のテスト。deff の macro は R4 の改訂で残るので、ここの字面は正当)。
(setv SCAN-SKIP-PARTS
  #{".git" ".venv" ".claude" ".worktrees" "__pycache__" "node_modules"
    "dist" ".mypy_cache" ".pytest_cache" "scratchpad"})

(setv DEFF-REASON-MARK "defk にできない")
(setv DEFF-LINE-PATTERN (re.compile r"\(deff\s[^\n]*"))

(defk count-unexcused-deff [text]
  {:pre [(: text str)]
   :post [(: % int)]
   :tags {:context "doeff-hy-adr" :role "judgment"}}
  "理由の註(同じ行の「defk にできない」)を持たない deff の定義の数 — 台帳の物差し。"
  (len (lfor line (.findall DEFF-LINE-PATTERN text)
             :if (not-in DEFF-REASON-MARK line)
             line)))

(defk scan-deff-counts [repo-root]
  {:pre [(: repo-root Path)]
   :post [(: % dict)]
   :tags {:context "doeff-hy-adr" :role "foundation"}}
  "repo 内 .hy の、理由の註の無い deff の定義数を file 別に数える(針と台帳の共通物差し)。"
  (setv counts {})
  (for [p (sorted (.rglob repo-root "*.hy"))]
    (setv rel (str (.relative-to p repo-root)))
    (when (or (& (set (. (.relative-to p repo-root) parts)) SCAN-SKIP-PARTS)
              (.startswith rel "packages/doeff-hy/"))
      (continue))
    (<- n (count-unexcused-deff
            (.read-text p :encoding "utf-8" :errors "replace")))
    (when (> n 0)
      (setv (get counts rel) n)))
  counts)


(defadr ADR-DOE-HY-004
  :title "関数語彙は defk のみ: defn は禁止(Python との境界も含む・例外は macro の展開の時に呼ぶ関数だけ)、deff は defk にできない所の逃げ道として同じ行の理由の註と :tags つきでだけ許し、defk / deff の契約の辞書には :tags を必須にする。理由の註の無い既存の deff は凍結台帳の ratchet で単調減少させ、変換は呼び出し規約(<- bind)込みの一括出荷で台帳を同便で削る — 語彙の併存は file 慣行の複製で自己増殖し、タグを書けない定義は並べて閲覧できないため、法と針なしには収束しない"
  :status "accepted"
  :scope ["docs/adr/defadr_doeff_hy_004_defk_only.hy"
          "packages/doeff-agents"
          "docs/adr"
          "tests"
          "packages/doeff-linter"]
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
       :evidence "defk 展開 = @do generator(macros.hy:498-)— 直接呼びは Program 値が返るだけで実行されない")
     (fact
       "operator 指示 2026-09-27(逐語): and we want to forbid the use of defn and only allow defk, and in inevitable case allow deff, to force the use of tag on definitions — 同じ会話の直前の文脈(逐語): yes so we structure the dir by service->layer, but have vscode navigator be able to structure components via tags"
       :evidence "Claude Code の会話(2026-09-27・agora-redesign #798)")
     (fact
       "defk / deff / defp / defhandler の契約の辞書は :tags {:context … :role …} を受け、定義の属性 __doeff_tags__ に残す(agora-redesign #800)。defn は契約の辞書を持たないので :tags を書けず、タグから定義を並べる閲覧(VS Code のパネル)に現れない。"
       :evidence "packages/doeff-hy/src/doeff_hy/declarations.hy(CONTRACT-KEYS・TAG-KEYS・ROLES)")
     (fact
       "実測 2026-09-27: doeff repo の packages・docs・tests の .hy に defn / defn/a が 269 file・2,135 定義ある(packages/doeff-hy の macro の実装を含む)。2026-08-21 版の R1 は Python との境界の defn を対象外にしていた。"
       :evidence "grep -rEc '\\(defn(/a)?\\s' --include='*.hy' packages docs tests")
     (fact
       "operator 裁定 2026-09-27(逐語 4 つ): \"yeah reading config, that's exactly where effects like Ask comes in, no excuse\" / \"yeah non-deftest must be forbidden\" / \"process entrypoint,,, of doeff-cluster? i dont think that doeff-cluster should require a non-defk func or something, we need to discuss further around doeff-cluster job api\" / \"about the reason text, we want jev to tell if it's acceptable right?\""
       :evidence "Claude Code の会話(2026-09-27・agora-redesign #798)— coordinator 経由")
     (fact
       "agora-controllers の本線 d99194b4 で『defk にできない』の註は 682 件あり、推定の内訳は 検の補助 166・組み立て 127・その他 112・process の入口 82・同上 81・外の library の callback 71・framework の入口 43。本当に素の callable が要る場面と、呼び手を Program にすれば済む場面(組み立て = handlers-of・検 = deftest)が区別されていなかった。"
       :evidence "doeff 683dd0e8(wt/hy-reason-kinds・doeff-linter の DOEFF111 / DOEFF203)の commit message")]
  :context
    [(interpretation
       "語彙が 1 つなら呼び出し規約も 1 つで、エージェント書き手が誤る余地が構造的に消える。純粋ロジックは defk の退化形(bind ゼロ)でそのまま書け、handler ゼロの run で回る — deff にしか書けない形は無いので、統一のコストは移行だけで表現力の損失は無い。")
     (interpretation
       "big-bang 変換は呼び出し規約の連鎖ゆえに危険。ratchet(新設は針で即赤・既存は台帳で凍結・変換便が台帳を同便で削る)が、回帰ゼロと漸進燃焼を両立する唯一の形。")
     (interpretation
       "2026-08-21 版は『disallow』の終端を deff の macro の削除と読み、R4 に置いた。2026-09-27 の決定はこれを改める: 外の library の callback・Python から同期で呼ばれる境界・pytest の fixture のような framework の規約は、Program ではなく素の callable を要求するので defk にできない。そこで defn を使うとタグを書けない定義が残る。deff は素の callable でありながら契約の辞書(:tags を含む)を持てるので、避けられない所の逃げ道として deff を残し、defn の方を消す。禁止の焦点は『deff という語彙』から『タグを書けない定義(defn)と、理由を名乗らない deff』へ移る。")
     (interpretation
       "判定の正本は doeff-linter とする(defn の禁止・deff の理由の註・:tags の必須)。この ADR の針(DEFF-ROSTER の ratchet)は理由の註の無い deff の数だけを持ち、linter の規則と二重に数えない。linter の規則が本線に着地するまで、該当の law は未配線(enforcement なし)と明示する。")]
  :decision
    [(rule R1 "関数は defk で書く(repo 全域 — production・tests・docs/adr。除外は macro 所有者 packages/doeff-hy のみ)。handler は defhandler、テスト本体は deftest、入口は defp。defn / defn/a は禁止し、Python との境界(interop)も対象に含める。例外は macro の展開の時に呼ぶ関数(eval-and-compile / eval-when-compile の中)だけ。素の callable が要る所(外の library の callback・Python から同期で呼ばれる境界・pytest の fixture のような framework の規約)は deff を逃げ道として使い、同じ行に『; defk にできない: <理由>』を書く。2026-08-21 版の『Python interop 境界の素の defn は対象外』は取り下げる(2026-09-27 改訂)。")
     (rule R1b "defk と deff の契約の辞書には :tags {:context <文脈> :role <役>} を必須にする。鍵と役の一覧は doeff-hy の declarations(TAG-KEYS・ROLES)と各 repo の doeff-linter の設定に従う(2026-09-27 新設)。")
     (rule R2 "理由の註の無い既存の deff(2026-08-21 時点で 227 定義)は DEFF-ROSTER に凍結する。針は file 単位で 現在数 <= 台帳数 を強制し、台帳外 file の理由の註の無い deff は 0 を強制する — いかなる新設・移設も赤。台帳は『defk にできる物を減らす』向きにだけ動く。")
     (rule R3 "変換(burn-down)は、定義の defk 化と全呼び出し site の <- bind 化と DEFF-ROSTER の該当行の削減を 1 便で一括出荷する。台帳の減少と実削除は常に同期する。")
     (rule R4 "【2026-09-27 取り下げ】旧文: DEFF-ROSTER が空になったら deff macro 本体とその意味論テストを packages/doeff-hy から削除する。改訂後: deff の macro は R1 の逃げ道として残す。DEFF-ROSTER が空になっても macro は消さない(台帳が空 = 理由を名乗らない deff が 0 になった状態)。")
     (rule R5 "台帳の増額・除外の新設は operator 裁定のみ。針の走査条件(SCAN-SKIP-PARTS・doeff-hy 除外)の変更も同様。")
     (rule R6 "設定・環境(env の値・設定の file)を読むことは deff の理由にならない。読みは Ask などの effect で書き、値は handler が答える(2026-09-27 追記)。")
     (rule R7 "検(テスト)は deftest だけで書く。pytest の素の test 関数・検の補助の素の関数(値を組む口・fixture の代わり)は禁止し、補助は defk にして deftest の本体から <- で受ける。R1 の『pytest の fixture のような framework の規約』は deff の理由の例から外す(2026-09-27 追記)。")
     (rule R8 "deff の理由(同じ行の『; defk にできない: <理由>』)は自由な文で書く。受け入れるかは doeff-linter の DOEFF203(Jev)が、その repo の architecture.hy に宣言した受け入れ可の理由と照らして決める。註の有無・形は DOEFF111 が検める(2026-09-27 追記)。")
     (rule R9 "framework・process の入口(doeff-cluster の job_entry の env の関数など)は deff の理由にしない方向とする。doeff-cluster の job API の議論(agora-redesign #829)が決まるまで、既存の入口の素の関数は登録簿で持ち、新設はしない(2026-09-27 追記)。")]
  :laws
    [(law defk-only-vocabulary
       :statement "for_all hy_file f in repo \\ {packages/doeff-hy}: count_deff(f) <= DEFF-ROSTER.get(f, 0) — 台帳は単調非増加であり、新しい deff は存在できない"
       :counterexamples
         [(counterexample "2026-08-20 W1b 便が admit-context-file を deff で新設 — file 慣行の複製が deff を再生産する(針が無ければ収束しない)実測")
          (counterexample "deff→defk の機械一括置換 — 直接呼びの site は Program 値を受け取るだけで実行されず、静かに no-op 化する(呼び出し規約の連鎖を無視した変換は壊れる)")]
       :enforced-by ["test-adr-doe-hy-004-deff-ratchet" "test-adr-doe-hy-004-reasoned-deff-is-outside-the-roster"])
     (law defn-is-forbidden
       :statement "for_all hy_file f in repo \\ {packages/doeff-hy}: defn / defn/a の定義は 0 — ただし eval-and-compile / eval-when-compile の中(macro の展開の時に呼ぶ関数)を除く"
       :counterexamples
         [(counterexample "Python から同期で呼ばれる境界の関数を defn で書く — 素の callable が要るなら deff に理由の註と :tags を付けて書く(defn は契約の辞書を持てずタグから閲覧できない)")
          (counterexample "小さな純粋関数を defn で書く — defk の退化形(bind ゼロ)で書ける")]
       :enforced-by ["doeff-linter DOEFF110"]
       :wiring "未配線(2026-09-27)— DOEFF110 は doeff-linter へ別の担当が足している最中で本線に未着地。着地までこの law を機械で検める針は無い")
     (law deff-names-its-reason
       :statement "for_all deff 定義 d: 同じ行に『; defk にできない: <理由>』がある、または d が DEFF-ROSTER の凍結分に数えられている"
       :counterexamples
         [(counterexample "外の library の callback を理由の註なしの deff で新設する — 理由を名乗らない deff は defk にできる物と見分けられない")]
       :enforced-by ["doeff-linter DOEFF111" "test-adr-doe-hy-004-deff-ratchet"]
       :wiring "一部配線(2026-09-27)— 台帳の ratchet(この ADR の deftest)は理由の註の無い deff の増加を赤にする。定義ごとの判定 DOEFF111 は doeff-linter に未着地")
     (law definitions-carry-tags
       :statement "for_all defk / deff 定義 d: d の契約の辞書に :tags {:context … :role …} がある"
       :counterexamples
         [(counterexample "契約の辞書に :pre / :post だけを書いた defk — タグから並べる閲覧に現れない")]
       :enforced-by ["doeff-linter DOEFF112"]
       :wiring "未配線(2026-09-27)— DOEFF112 は doeff-linter に未着地。既存の defk の大半は :tags をまだ持たない")
     (law deff-reason-is-accepted-by-jev
       :statement "for_all deff 定義 d: d の理由の註がある ∧ DOEFF203(Jev)が d の理由を architecture.hy の受け入れ可の理由のどれかと判じる。設定・環境の読み・検の補助・組み立ては受け入れ可の理由に入らない"
       :counterexamples
         [(counterexample "env の値を読む関数を『; defk にできない: 設定を読むので』の deff で書く — 読みは Ask の effect で書ける(operator 逐語 no excuse)")
          (counterexample "deftest の値を組む補助を『; defk にできない: 検の補助』の deff で書く — 補助は defk にして deftest から <- で受ける")
          (counterexample "handler の組を並べる組み立てを deff で書く — 組み立ては defk(handlers-of)で書ける")]
       :enforced-by ["doeff-linter DOEFF111" "doeff-linter DOEFF203"]
       :wiring "未配線(2026-09-27)— DOEFF111 の新しい形と DOEFF203 は doeff-linter の wt/hy-reason-kinds にあり本線に未着地")
     (law checks-are-deftest-only
       :statement "for_all 検 t: t は deftest で書かれている — 素の test 関数・検の補助の素の関数は 0"
       :counterexamples
         [(counterexample "pytest の素の test 関数(def test_… / defn test-…)で doeff の Program を検める")]
       :enforced-by ["doeff-linter DOEFF118"]
       :wiring "未配線(2026-09-27)— DOEFF118 は doeff-linter に未着地(番号は coordinator の指定・規則は別の担当が足す)")]
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
     (deftest test-adr-doe-hy-004-reasoned-deff-is-outside-the-roster
       ;; R1 の逃げ道: 同じ行に理由の註を持つ deff は台帳の物差しに数えない。
       ;; 註の無い deff と、別の行に註がある deff は数える。
       (setv reasoned (+ "(" "deff on-sort-key [row]  ; defk にできない: sorted の key は Program を実行しない\n"))
       (setv bare (+ "(" "deff on-sort-key [row]\n"))
       (setv detached (+ "; defk にできない: 別の行の註\n(" "deff on-sort-key [row]\n"))
       (assert (= (run (count-unexcused-deff reasoned)) 0))
       (assert (= (run (count-unexcused-deff bare)) 1))
       (assert (= (run (count-unexcused-deff detached)) 1)))
     (deftest test-adr-doe-hy-004-pure-logic-lives-in-defk
       ;; 移行レシピの実演: 純粋検証は defk の退化形で書け、handler ゼロの
       ;; run で直接回る — deff にしか書けない形は無い。
       (assert (= (run (probe-pure-validator "abc")) True))
       (assert (= (run (probe-pure-validator "/abs")) False))
       (assert (= (run (probe-pure-validator "")) False)))]
  :plans [])
