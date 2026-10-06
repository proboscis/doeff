;;; agent の境界で運ばせない env の語彙(card acp:kanban-issue:ki-2a061da56ca9・agora-redesign #708)。
;;;
;;; 綴りの家はここ 1 点。層(受理 = sessionhost/policy.hy session-env-admission-error・
;;; spawn = sessionhost/substrate.hy・shell の起動 = shell.py)は「どの集合を禁じるか」を
;;; 名指すだけで、literal の名簿を持たない — 2026-09-19 の実測では 3 つの写しが
;;; すべて違う中身で、個人鍵の別名 3 綴りは受理を素通りしていた。
;;;
;;; ⚠ この module は session host(doeff_agents.sessionhost.*)を import しない。
;;; agent の手番の headless の経路(shell → ここ)が session host の語彙を引きずらないため
;;; (#624 の静的な検査で shell → sessionhost/policy → sessionhost/effects の鎖が見つかった)。
;;; sessionhost/policy.hy はここから読んで再輸出する(既存の読み手の名指しは変わらない)。
;;;
;;; ⚠ 3 集合を素朴に合併してはいけない。TURN-AUTH-ENV-KEYS(:403 手番の札)は
;;; shell の層だけが禁じ、受理と spawn は**わざと運ぶ**(ADR 012 R5・R30)。
;;; 和を取ると手番の札の経路が死ぬ。だから「どの層が何を禁じるか」は下の表の
;;; とおり名指しで、集合の和ではない:
;;;
;;;   受理  BINDING-OWNED ∪ metered(形 + 別名)∪ PROVIDER-AUTH
;;;   spawn PROVIDER-AUTH
;;;   shell PROVIDER-AUTH ∪ PROVIDER-ROUTING ∪ TURN-AUTH

;; 関数は素の defn(ADR-DOE-HY-004 R1 の「Python との境界の素の defn は対象外」)— shell.py(Python)が
;; 同期で呼ぶ判定で、sessionhost の deff の呼び手からも直に呼ばれる。deff は台帳の外の file に移せない(R2)。


;; binding が所有する auth の家の env(session_env = 非 auth の overlay には置けない・ADR-DOE-AGENTS-004 R9)。
(setv BINDING-OWNED-ENV-KEYS #{"CODEX_HOME" "CLAUDE_CONFIG_DIR"})

;; 手番ごとの資格の env(段 10 lane 10d 便 2 の追補 2・実弾 #92 = 預かり所が口座を更新した後、
;; 誕生の札で再開した手番が 401 を食った)。預かり所の貸与の札はこの名で運ぶ。判定点はここ 1 つ。
;; claude の手番の資格の env の名(headless の adapter が借りた access token を置く・agentd の貸与の札も同じ名)。
(setv CLAUDE-TURN-CREDENTIAL-ENV "CLAUDE_CODE_OAUTH_TOKEN")
(setv TURN-AUTH-ENV-KEYS #{CLAUDE-TURN-CREDENTIAL-ENV})

;; provider の鍵・札の綴り(= どの層でも agent process へ運ばせない)。
;; 形(`*_API_KEY`)の判定と重なる名も在るが、重なりは無害 — 形だけでは拾えない
;; 別名(*_API_KEY で終わらない個人鍵・AUTH_TOKEN 系)を綴りで塞ぐのがこの名簿の役。
(setv PROVIDER-AUTH-ENV-KEYS
      #{"ANTHROPIC_API_KEY"
        "ANTHROPIC_API_KEY_PERSONAL"
        "ANTHROPIC_API_KEY__PERSONAL"
        "ANTHROPIC_AUTH_TOKEN"
        "CLAUDE_API_KEY"
        "OPENAI_API_KEY"
        "OPENROUTER_API_KEY"})

;; 鍵ではないが provider を差し替える綴り(宛先のすり替え)。shell の層だけが禁じる
;; — 受理に足すと挙動が反例の分を越える(2026-09-19 の便の範囲外)。
(setv PROVIDER-ROUTING-ENV-KEYS #{"ANTHROPIC_BASE_URL" "ANTHROPIC_MODEL"})


(defn #^ str policy-normalized-env-key [#^ str key]  ; defk にできない: Python(shell.py)が同期で呼ぶ境界(ADR-DOE-HY-004 R1)
  "env key の正規化(substrate normalized-env-key と同規約: `-`→`_`・大文字化)。"
  (.upper (.replace key "-" "_")))

(defn #^ list env-offenders-against [#^ dict env #^ (| set frozenset) names]  ; defk にできない: Python(shell.py)が同期で呼ぶ境界(ADR-DOE-HY-004 R1)
  "env のうち names(正規化済みの綴りの集合)に当たるキーの列挙(判定の 1 点)。

   agent の境界で「運ばせない env」を判じる層は 3 つ在る — 受理
   (session-env-admission-error)・spawn(substrate ensure-no-forbidden-agent-env)・
   shell の起動(shell.assert-no-forbidden-agent-env)。層ごとに違うのは
   **どの集合を禁じるか**だけで、正規化と突合はこの 1 つを通る
   (card acp:kanban-issue:ki-2a061da56ca9: 3 写しの正規化が別々に古びるのを止める)。
   返るのは呼び手が書いた綴りのまま(正規化後の名ではない — 断りの文が
   『あなたが載せた名』を指せるように)。"
  (sorted (lfor key (.keys env)
                :if (in (policy-normalized-env-key key) names)
                key)))


(defn #^ list overlay-env-offenders [#^ dict session-env]  ; defk にできない: Python(shell.py)が同期で呼ぶ境界(ADR-DOE-HY-004 R1)
  "session_env(非 auth overlay)に居てはならない binding 所有キーの列挙。"
  (env-offenders-against session-env BINDING-OWNED-ENV-KEYS))


(defn #^ list provider-auth-env-offenders [#^ dict session-env]  ; defk にできない: Python(shell.py)が同期で呼ぶ境界(ADR-DOE-HY-004 R1)
  "session_env に居てはならない provider の鍵・札の綴りの列挙(純粋の 1 点)。"
  (env-offenders-against session-env PROVIDER-AUTH-ENV-KEYS))
