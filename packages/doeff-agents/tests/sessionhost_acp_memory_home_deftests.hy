;;; 自動記憶(auto-memory)の置き場は会話に従う(ADR-DOE-AGENTS-006 R11)の焦点の検。
;;;
;;; 直した欠陥(2026-09-20 に会社 Mac で読みだけで測定): Claude Code の自動記憶の置き場は
;;; `<CLAUDE_CONFIG_DIR>/projects/<潰した cwd>/memory/` で、CLAUDE_CONFIG_DIR は doeff が
;;; 預かり所の account から組んでいた(charter-with-grant)。⇒ 実効の鍵は「account × 作業ディレクトリ」
;;; という**誰も宣言していない組**で、profile を替えるたびに 1 つの会話が別の置き場へ割れ、同じ
;;; account に載った別々の会話が相席する。実測: 同じ会話の連続する 2 手番が交わり 0 件の 2 つの家を
;;; 読み、15 分前に書いた記憶が次の手番から消えていた。
;;;
;;; ここで撃つのは判断の 1 点(memory-home-of)と、その値が**起こす 3 つの腕すべて**で charter に
;;; 乗ることちょうど。腕ごとに落ちる形(名簿の漏れ)は 2026-09-15 の添付の実弾と同じなので、
;;; resume の名簿も名指しで固定する。HTTP も subprocess も無い。

(require doeff-hy.macros [deftest])

(import json)
(import os)
(import shutil)
(import tempfile)

(import doeff [run])
(import doeff_agents.sessionhost.acp.effects [
  AgentdSettings
  ArmChoice
  LaunchPlan
  NEXT-ARM-LAUNCH
  NEXT-ARM-REHYDRATE
  NEXT-ARM-RESUME])
(import doeff_agents.sessionhost.host [
  DEFAULT-PROMPT-JUDGE-CMD
  HostConfig
  build-launch-program-params
  dispatch-line
  parse-args])
(import doeff_agents.sessionhost.store [StoreActor])
(import doeff_agents.sessionhost.acp.judgment [
  charter-with-memory-home
  incarnation-charter-of
  memory-home-of
  resume-params-of])


(setv MEMORY-ROOT "/state/doeff/agent-memory")
(setv CONVERSATION "c-01ARZ3NDEKTSV4RRFFQ69G5FAV")
(setv OTHER "c-01ARZ3NDEKTSV4RRFFQ69G5FAW")


(defn charter-of [#^ str conversation-id #^ str work-dir]
  "この会話・この作業ディレクトリで起こす charter(置き場に効くはずの無い欄を動かすため)。"
  {"session_id" "s-new" "prompt" "start" "model" "claude-opus-5" "work_dir" work-dir
   "launch_attribution" {"agentd" {"conversationId" conversation-id}}})


;; ---------------------------------------------------------------------------
;; V1 / V2 / V3 — 鍵は会話 id ちょうど(account も作業ディレクトリも鍵に入らない)
;; ---------------------------------------------------------------------------

(deftest test-the-memory-home-is-keyed-by-the-conversation-and-nothing-else
  ;; V1: 同じ会話なら、預かり所がどの account を貸しても同じ置き場を指す。account は memory-home-of の
  ;; 引数に無い — 「入れ忘れ」ではなく**入れられない**形にしてある(判断の引数が鍵の全部)。
  (setv home (run (memory-home-of MEMORY-ROOT CONVERSATION)))
  (assert (= home f"{MEMORY-ROOT}/{CONVERSATION}") home)
  ;; V2: 同じ account に載った別の会話は別の置き場(相席しない)。
  (setv other (run (memory-home-of MEMORY-ROOT OTHER)))
  (assert (!= home other) #(home other))
  ;; V3: 作業ディレクトリは鍵に入らない — 隔離作業ツリーと主 checkout を行き来しても同じ置き場。
  ;; (charter の work_dir を動かして、charter に乗る値が動かないことで撃つ。)
  (setv in-worktree (run (charter-with-memory-home
                           (charter-of CONVERSATION "/Users/x/.worktrees/doeff-wt-a")
                           MEMORY-ROOT CONVERSATION)))
  (setv in-checkout (run (charter-with-memory-home
                           (charter-of CONVERSATION "/Users/x/repos/doeff")
                           MEMORY-ROOT CONVERSATION)))
  (assert (= (get in-worktree "memory_dir") (get in-checkout "memory_dir"))
          #(in-worktree in-checkout))
  (assert (= (get in-worktree "memory_dir") home) in-worktree))


(deftest test-the-memory-home-never-escapes-its-root
  ;; 会話 id は path の 1 節になる。account の家と同じ規約で潰し、潰した結果が根の外を指す綴りは
  ;; 置き場を持たない(None — 発明しない)。
  (assert (= (run (memory-home-of MEMORY-ROOT "c-a/b")) f"{MEMORY-ROOT}/c-a_b"))
  (assert (= (run (memory-home-of MEMORY-ROOT "../../etc")) f"{MEMORY-ROOT}/.._.._etc"))
  (assert (= (run (memory-home-of MEMORY-ROOT "/")) f"{MEMORY-ROOT}/_"))
  ;; 潰した結果が根の外を指す綴り(空・`.`・`..`)だけは置き場を持たない。
  (for [bad ["" "." ".."]]
    (assert (is (run (memory-home-of MEMORY-ROOT bad)) None) bad))
  ;; 根の末尾の `/` は値を変えない(宣言の綴りの揺れで別の置き場を作らない)。
  (assert (= (run (memory-home-of (+ MEMORY-ROOT "/") CONVERSATION))
             (run (memory-home-of MEMORY-ROOT CONVERSATION))))
  ;; 根を宣言していない機体は置き場を持たない(欄を立てず、今日の挙動に落ちる)。
  (assert (is (run (memory-home-of "" CONVERSATION)) None))
  (setv plain (run (charter-with-memory-home (charter-of CONVERSATION "/w") "" CONVERSATION)))
  (assert (not-in "memory_dir" plain) plain))


;; ---------------------------------------------------------------------------
;; V6 — 起こす腕は 3 つ。どの腕でも charter に乗り、resume の名簿でも落ちない
;; ---------------------------------------------------------------------------

(defn plan-of []
  (LaunchPlan :charter (charter-of CONVERSATION "/Users/x/repos/doeff")
              :predecessor None :lease-kind None :account None :profile "p" :model "claude-opus-5"))


(deftest test-every-arm-that-wakes-a-conversation-carries-the-memory-home
  ;; 起こす腕は launch / resume / rehydrate の 3 つ。どれか 1 つで落ちると、その腕の手番だけ
  ;; 記憶が別の置き場に行き、誰も気づかない(誤りも条件も出ない)。
  (setv attribution {"conversationId" CONVERSATION})
  (for [arm [NEXT-ARM-LAUNCH NEXT-ARM-RESUME NEXT-ARM-REHYDRATE]]
    (setv built (run (incarnation-charter-of
                       (plan-of) (ArmChoice :arm arm :source None :retire None)
                       "s-new" #() "" attribution "headless" None "/homes" MEMORY-ROOT None #())))
    (setv charter (get built 0))
    (assert (= (.get charter "memory_dir") f"{MEMORY-ROOT}/{CONVERSATION}")
            #(arm (sorted (.keys charter)))))
  ;; resume の params は charter の欄を**名簿で**写す。名簿から漏れると、腕が resume の手番だけ
  ;; 黙って落ちる(実弾 2026-09-15 の添付と同じ形)。
  (setv charter (run (charter-with-memory-home (charter-of CONVERSATION "/w")
                                               MEMORY-ROOT CONVERSATION)))
  (setv params (run (resume-params-of "s-old" charter)))
  (assert (in "memory_dir" params) #("resume の params が置き場を運ばない(名簿の漏れ)"
                                      (sorted (.keys params))))
  (assert (= (get params "memory_dir") (get charter "memory_dir")) params)
  ;; 欄の無い charter の resume params は 1 byte も変わらない(欄を作らない)。
  (setv plain (run (resume-params-of "s-old" {"session_id" "s-new" "prompt" "start"})))
  (assert (not-in "memory_dir" plain) plain))


(deftest test-the-memory-root-is-declared-once-and-sits-outside-the-credential-homes
  ;; 値の宣言は 1 点(AgentdSettings)。既定は資格の家(homes-root)の**外** — 家の中に置くと
  ;; 預かり所が別の account を貸した拍に置き場が変わり、会話から剥がれる(直している欠陥そのもの)。
  (import doeff_agents.sessionhost.acp.runtime [settings_from_env])
  (setv settings (settings_from_env {"DOEFF_AGENTD_NODE_NAME" "n1" "HOME" "/home/u"
                                     "DOEFF_AGENTD_CAPACITY" "1" "DOEFF_AGENTD_PLACES" "company"
                                     "RECORD_SERVICE_URL" "http://127.0.0.1:1"}))
  (assert (isinstance settings AgentdSettings))
  (assert (= settings.memory-root "/home/u/.local/state/doeff/agent-memory") settings.memory-root)
  (assert (not (.startswith settings.memory-root settings.homes-root))
          #(settings.memory-root settings.homes-root))
  ;; 宣言の上書きは env 1 つ。
  (setv overridden (settings_from_env {"DOEFF_AGENTD_NODE_NAME" "n1" "HOME" "/home/u"
                                       "DOEFF_AGENTD_CAPACITY" "1" "DOEFF_AGENTD_PLACES" "company"
                                       "RECORD_SERVICE_URL" "http://127.0.0.1:1"
                                       "DOEFF_AGENTD_MEMORY_ROOT" "/elsewhere/mem"}))
  (assert (= overridden.memory-root "/elsewhere/mem") overridden.memory-root))


;; ---------------------------------------------------------------------------
;; 名簿は 4 枚(実弾 2026-09-15 の添付は 3 枚で落ちた)— 置き場を落とす枚を 1 つも残さない
;; ---------------------------------------------------------------------------

(defn host-config []
  (HostConfig :db-path "/tmp/x.db" :socket-path "/tmp/x.sock"
              :tmux-bin "tmux" :monitor-interval-seconds 1.0
              :max-running 4 :result-solicitation-limit 3
              :prompt-stall-seconds 90 :prompt-unblock-limit 3
              :prompt-judge-cmd DEFAULT-PROMPT-JUDGE-CMD))


(deftest test-the-wire-carries-the-memory-home-to-the-launch-program
  ;; 名簿の 4 枚目(host.build-launch-program-params: RPC の params → launch program)。
  ;; ここで落とすと、**RPC 越しの手番だけ**記憶が既定の置き場へ行く(agentd と host が同じ
  ;; process の検では緑のまま — 添付が 3 枚の名簿で落ちた時と同じ見落とし方)。
  (setv wire {"session_id" "s1" "session_name" "doeff-s1" "agent_type" "claude" "work_dir" "/w"
              "memory_dir" f"{MEMORY-ROOT}/{CONVERSATION}"})
  (setv params (build-launch-program-params wire (host-config)))
  (assert (= (get params "memory_dir") f"{MEMORY-ROOT}/{CONVERSATION}") params)
  ;; 欄の無い呼びは**欄を作らない**(card acp:kanban-issue:ki-a40292ed30d9 — 席へ運ぶ欄は
  ;; policy.CHARTER-CARRIED-KEYS の 1 点から写されるので、旗と同じ「欄の有無 = 会話が名乗ったか」
  ;; の印に揃う)。読み手は全員 `.get` なので、None の欄と欄の不在は同じ振る舞い。
  (setv bare (build-launch-program-params
               {"session_id" "s1" "session_name" "doeff-s1"
                "agent_type" "claude" "work_dir" "/w"}
               (host-config)))
  (assert (not-in "memory_dir" bare) bare))


(deftest test-fork-refuses-to-inherit-the-parent-conversations-memory-home
  ;; fork は**新しい会話**で、その id は CLI が鋳造するまで判らない。親の置き場を通せば
  ;; 新しい会話に親の記憶が黙って付く — 直している誤帰属そのもの。だから resume 専用として
  ;; 黙殺せず断る(binding / new_session_id / … と同じ fail-closed の扱い)。
  (setv d (tempfile.mkdtemp))
  (try
    (setv config (parse-args ["--db" (os.path.join d "agentd.sqlite")
                              "--socket" (os.path.join d "agentd.sock")
                              "--prompt-judge-cmd" "" "serve"]))
    (setv actor (StoreActor config.db-path))
    (try
      (setv line (json.dumps {"id" 1 "method" "session.fork"
                              "params" {"session_id" "s-parent"
                                        "memory_dir" f"{MEMORY-ROOT}/{CONVERSATION}"}}))
      (setv response (json.loads (dispatch-line line config actor)))
      (assert (= (get response "ok") False) response)
      (assert (in "`memory_dir` is " (get response "error")) response)
      ;; card ki-a40292ed30d9: 冊そのもの(memory_files)も同じ理由で resume 専用。置き場の名だけを
      ;; 断って中身を通したら、誤帰属は置き場ではなく**新しい会話の置き場に書かれた親の本文**で起きる。
      (setv books-line (json.dumps {"id" 3 "method" "session.fork"
                                    "params" {"session_id" "s-parent"
                                              "memory_files" [{"name" "a.md" "text" "A"}]}}))
      (setv books-answer (json.loads (dispatch-line books-line config actor)))
      (assert (= (get books-answer "ok") False) books-answer)
      (assert (in "`memory_files` is " (get books-answer "error")) books-answer)
      ;; resume は同じ会話の続きなので、同じ欄が断られない。
      (setv resume-line (json.dumps {"id" 2 "method" "session.resume"
                                     "params" {"session_id" "s-parent"
                                               "memory_dir" f"{MEMORY-ROOT}/{CONVERSATION}"
                                               "memory_files" [{"name" "a.md" "text" "A"}]}}))
      (setv resumed (json.loads (dispatch-line resume-line config actor)))
      (assert (not-in "memory_dir" (str (.get resumed "error" ""))) resumed)
      (assert (not-in "memory_files" (str (.get resumed "error" ""))) resumed)
      (finally (.close actor)))
    (finally (shutil.rmtree d :ignore-errors True))))
