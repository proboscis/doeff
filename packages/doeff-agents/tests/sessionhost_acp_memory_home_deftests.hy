;;; 自動記憶(auto-memory)の置き場は会話に従う(ADR-DOE-AGENTS-006 R11)の焦点の検。
;;;
;;; 直した欠陥(2026-09-20 に会社 Mac で読みだけで測定): Claude Code の自動記憶の置き場は
;;; `<CLAUDE_CONFIG_DIR>/projects/<潰した cwd>/memory/` で、CLAUDE_CONFIG_DIR は doeff が
;;; 預かり所の account から組んでいた(charter-with-grant)。⇒ 実効の鍵は「account × 作業ディレクトリ」
;;; という**誰も宣言していない組**で、profile を替えるたびに 1 つの会話が別の置き場へ割れ、同じ
;;; account に載った別々の会話が相席する。実測: 同じ会話の連続する 2 手番が交わり 0 件の 2 つの家を
;;; 読み、15 分前に書いた記憶が次の手番から消えていた。
;;;
;;; ここで撃つのは判断の 1 点(memory-home-of)と、その値が**起こし方(effects.NextArm)の defer を除く
;;; 全部** — 起こす 3 つ(launch / resume / rehydrate)と送り(send)の 4 つ — で席へ運ばれることちょうど。
;;; 腕は定義から反射で数える(手で並べた一覧は 2026-09-20 に 4 つ目の腕〔send〕を黙って落とした —
;;; card acp:kanban-issue:ki-a40292ed30d9)。腕ごとに落ちる形(名簿の漏れ)は 2026-09-15 の添付の実弾と
;;; 同じなので、resume の名簿も名指しで固定する。HTTP も subprocess も無い。

(require doeff-hy.macros [deftest])

(import json)
(import os)
(import shutil)
(import tempfile)
(import typing [get-args])

(import doeff [run])
(import doeff_agents.sessionhost.acp.effects [
  AgentdSettings
  ArmChoice
  LaunchPlan
  NEXT-ARM-DEFER
  NEXT-ARM-LAUNCH
  NEXT-ARM-REHYDRATE
  NEXT-ARM-RESUME
  NEXT-ARM-SEND
  MemoryTurnFiles
  NextArm])
(import doeff_agents.sessionhost.host [
  DEFAULT-PROMPT-JUDGE-CMD
  HostConfig
  build-launch-program-params
  dispatch-line
  parse-args])
(import doeff_agents.sessionhost.store [StoreActor])
(import doeff_agents.sessionhost.acp.judgment [
  charter-with-memory-home
  charter-with-memory-sweep
  incarnation-charter-of
  memory-home-of
  resume-params-of
  turn-charter-of])


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
;; V6 — 置き場を運ぶ腕は 4 つ(起こす launch / resume / rehydrate + 送り send)。数えるのは定義
;; (effects.NextArm)からで、手で並べない。どの腕でも同じ置き場が席へ渡り、resume の名簿でも落ちない
;; ---------------------------------------------------------------------------

(defn plan-of []
  (LaunchPlan :charter (charter-of CONVERSATION "/Users/x/repos/doeff")
              :predecessor None :lease-kind None :account None :profile "p" :model "claude-opus-5"))


(defn memory-home-carried-by-incarnation [#^ str arm]
  "起こす腕(launch / resume / rehydrate)が置き場を運ぶ口 = judgment.incarnation-charter-of(charter の欄)。"
  (setv built (run (incarnation-charter-of
                     (plan-of) (ArmChoice :arm arm :source None :retire None)
                     "s-new" #() "" {"conversationId" CONVERSATION} "headless" None "/homes"
                     MEMORY-ROOT None #())))
  (.get (get built 0) "memory_dir"))


(defn memory-home-carried-by-send [#^ str arm]
  "送りの腕(send)が置き場を運ぶ口 = judgment.turn-charter-of(温かい session への SessionSend の
   turn_charter — 器が降りた process を `--resume` で起こし直す拍に読む荷)。"
  (.get (run (turn-charter-of MEMORY-ROOT CONVERSATION (MemoryTurnFiles))) "memory_dir"))


;; 起こし方 → その腕が置き場を運ぶ口。**NextArm に語を足した人は、ここに口を名乗るまで緑にならない**
;; (defer だけは手番を起こさないので口を持たない)。
(setv MEMORY-HOME-CARRIER-OF
  {NEXT-ARM-LAUNCH memory-home-carried-by-incarnation
   NEXT-ARM-RESUME memory-home-carried-by-incarnation
   NEXT-ARM-REHYDRATE memory-home-carried-by-incarnation
   NEXT-ARM-SEND memory-home-carried-by-send})


(deftest test-every-arm-in-the-definition-carries-the-memory-home
  ;; 腕の一覧は `typing.get_args(NextArm)` から defer を除いたもの — 手で並べると、腕を 1 つ足した日に
  ;; その腕の手番だけ記憶が別の置き場に行き、誰も気づかない(誤りも条件も出ない)。2026-09-20 の欠陥は
  ;; まさにこの形(3 つ並べた一覧が 4 つ目の send を落とした・card acp:kanban-issue:ki-a40292ed30d9)。
  (setv arms (tuple (gfor arm (get-args NextArm) :if (!= arm NEXT-ARM-DEFER) arm)))
  (assert arms "NextArm から起こし方を 1 つも読めない(定義の形が変わった)")
  (for [arm arms]
    ;; 振り分けの表に無い語 = 新しい腕が置き場の口を名乗っていない ⇒ 赤。
    (assert (in arm MEMORY-HOME-CARRIER-OF)
            #(f"NextArm の起こし方 {arm !r} が置き場を運ぶ口を名乗っていない — MEMORY-HOME-CARRIER-OF へ足す"
              (sorted (.keys MEMORY-HOME-CARRIER-OF))))
    (setv home ((get MEMORY-HOME-CARRIER-OF arm) arm))
    (assert (= home f"{MEMORY-ROOT}/{CONVERSATION}") #(arm home)))
  ;; 逆向き: 表に在るのに定義に無い語は退役した腕の死んだ行 — こちらも残さない。
  (for [arm (.keys MEMORY-HOME-CARRIER-OF)]
    (assert (in arm arms) #(f"表の起こし方 {arm !r} は NextArm に無い(退役した腕の行)" arms)))
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


(deftest test-the-sweep-roster-reaches-the-seat-on-every-roster
  ;; 受入 6(card acp:kanban-issue:ki-6b5c4b270ca0): 掃除の荷(退役した行と同じ名前の file)も、置き場と
  ;; 同じ**全部の名簿**を渡る。渡らない名簿が 1 枚在ると、その腕の手番だけ退役した冊の file が置き場に
  ;; 残り、次の畳み戻しがそれを読んで退役を取り消す — 直している欠陥がその腕だけで再演する。
  ;; 形は上の memory_dir の門ちょうど(名簿 = judgment.resume-params-of と host.build-launch-program-params)。
  (setv swept #("gone.md" "stale.md"))
  ;; 起こす腕: charter に載る(3 つの起こす腕は同じ charter をそのまま運ぶ)。
  (setv charter (run (charter-with-memory-sweep
                       (run (charter-with-memory-home (charter-of CONVERSATION "/w")
                                                      MEMORY-ROOT CONVERSATION))
                       swept)))
  (assert (= (get charter "memory_retired_files") (list swept)) charter)
  ;; 蘇生の名簿(judgment.resume-params-of)— 2026-09-15 の添付が落ちた 1 枚目。
  (setv params (run (resume-params-of "s-old" charter)))
  (assert (in "memory_retired_files" params)
          #("resume の params が掃除の荷を運ばない(名簿の漏れ)" (sorted (.keys params))))
  (assert (= (get params "memory_retired_files") (list swept)) params)
  ;; wire の受理形(host.build-launch-program-params)— agentd と host が同じ process の検では
  ;; 緑のまま落ちる 1 枚。
  (setv wire {"session_id" "s1" "session_name" "doeff-s1" "agent_type" "claude" "work_dir" "/w"
              "memory_dir" f"{MEMORY-ROOT}/{CONVERSATION}"
              "memory_retired_files" (list swept)})
  (setv program (build-launch-program-params wire (host-config)))
  (assert (= (get program "memory_retired_files") (list swept)) program)
  ;; 送りの腕(4 つ目)も同じ荷を運ぶ — 荷は 1 つの型(MemoryTurnFiles)で運ばれる。
  (setv sent (run (turn-charter-of MEMORY-ROOT CONVERSATION (MemoryTurnFiles :swept swept))))
  (assert (= (get sent "memory_retired_files") (list swept)) sent)
  ;; 0 件の拍は**欄を立てない**(退役した行の無い会話の charter / wire は 1 byte も変わらない)。
  (setv plain (run (charter-with-memory-sweep {"session_id" "s-new"} #())))
  (assert (not-in "memory_retired_files" plain) plain)
  (setv bare (build-launch-program-params
               {"session_id" "s1" "session_name" "doeff-s1" "agent_type" "claude" "work_dir" "/w"}
               (host-config)))
  (assert (not-in "memory_retired_files" bare) bare)
  (setv quiet (run (turn-charter-of MEMORY-ROOT CONVERSATION (MemoryTurnFiles))))
  (assert (not-in "memory_retired_files" quiet) quiet))


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
      ;; card ki-6b5c4b270ca0: 掃除の荷(memory_retired_files)も同じ理由で resume 専用 — しかもこちらは
      ;; **消す**荷なので、親の退役した名を通すと新しい会話の置き場の file が消える。断りの名簿は
      ;; policy.TURN-CARRIED-KEYS の 1 点から採るので、族に欄を足す便は自動でこの門に入る。
      (setv swept-line (json.dumps {"id" 4 "method" "session.fork"
                                    "params" {"session_id" "s-parent"
                                              "memory_retired_files" ["a.md"]}}))
      (setv swept-answer (json.loads (dispatch-line swept-line config actor)))
      (assert (= (get swept-answer "ok") False) swept-answer)
      (assert (in "`memory_retired_files` is " (get swept-answer "error")) swept-answer)
      ;; resume は同じ会話の続きなので、同じ欄が断られない。
      (setv resume-line (json.dumps {"id" 2 "method" "session.resume"
                                     "params" {"session_id" "s-parent"
                                               "memory_dir" f"{MEMORY-ROOT}/{CONVERSATION}"
                                               "memory_files" [{"name" "a.md" "text" "A"}]
                                               "memory_retired_files" ["gone.md"]}}))
      (setv resumed (json.loads (dispatch-line resume-line config actor)))
      (assert (not-in "memory_dir" (str (.get resumed "error" ""))) resumed)
      (assert (not-in "memory_files" (str (.get resumed "error" ""))) resumed)
      (assert (not-in "memory_retired_files" (str (.get resumed "error" ""))) resumed)
      (finally (.close actor)))
    (finally (shutil.rmtree d :ignore-errors True))))
