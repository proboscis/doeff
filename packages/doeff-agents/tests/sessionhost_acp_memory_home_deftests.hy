;;; 自動記憶(auto-memory)の置き場は会話に従う(ADR-DOE-AGENTS-006 R11)の焦点の検。
;;;
;;; 直した欠陥(2026-09-20 に会社 Mac で読みだけで測定): Claude Code の自動記憶の置き場は
;;; `<CLAUDE_CONFIG_DIR>/projects/<潰した cwd>/memory/` で、CLAUDE_CONFIG_DIR は doeff が
;;; 預かり所の account から組んでいた(charter-with-grant)。⇒ 実効の鍵は「account × 作業ディレクトリ」
;;; という**誰も宣言していない組**で、profile を替えるたびに 1 つの会話が別の置き場へ割れ、同じ
;;; account に載った別々の会話が相席する。実測: 同じ会話の連続する 2 手番が交わり 0 件の 2 つの家を
;;; 読み、15 分前に書いた記憶が次の手番から消えていた。
;;;
;;; ここで撃つのは判断の 1 点(memory-home-of)と、その値が**会話を起こすあらゆる拍**で argv の
;;; 導出点まで届くことちょうど。腕ごとに落ちる形(名簿の漏れ)は 2026-09-15 の添付の実弾と同じなので、
;;; resume の名簿も名指しで固定する。HTTP も subprocess も無い。
;;;
;;; 2 度目の欠陥(card acp:kanban-issue:ki-a068efe8f6d9・2026-09-21): この検が起こし方を手書きで
;;; 3 つ(launch / resume / rehydrate)並べていたので、**4 つ目**(send の腕 = 降りた process を
;;; `--resume` で起こし直す拍 — claude は 1 手番 1 process なので普段の手番はすべてこれ)が
;;; 1 度も数えられず、その腕だけ**置き場も冊も両方**落ちていた。⇒ 起こし方は閉語彙 effects.NextArm を
;;; 回して数え、継続の経路(headless.continue-headless-process)は argv と置き場の中身を直に撃つ。
;;;
;;; ⚠ 直し方の形も固定する: 手番の荷(policy.TURN-CARRIED-KEYS)を**行に写して**直すのは禁じ手
;;; (正本は ACP の行・法 ACP 575b1e — 行へ写すと第 2 の正本が腐る)。継続も他の 3 腕と同じく
;;; 手番ごとに名乗り直す。行が置き場を指せないことを test-the-row-cannot-point-the-turn-at-a-memory-home
;;; が撃つ。

(require doeff-hy.macros [deftest defk <- defhandler])

(import json)
(import os)
(import shutil)
(import tempfile)
(import typing [get-args])

(import doeff [run])
(import doeff_agents.sessionhost.acp.effects [
  AgentdSettings
  ArmChoice
  BACKEND-HEADLESS
  LaunchPlan
  MEMORY-BASE-FILE
  MEMORY-INDEX-FILE
  NEXT-ARM-DEFER
  NEXT-ARM-SEND
  NextArm])
(import doeff_agents.sessionhost.policy [LAUNCH-FLAG-KEYS TURN-CARRIED-KEYS])
(import doeff_agents.sessionhost.host [
  DEFAULT-PROMPT-JUDGE-CMD
  HostConfig
  build-launch-program-params
  dispatch-line
  parse-args])
(import doeff_agents.sessionhost.store [StoreActor])
(import doeff_agents.sessionhost.acp.judgment [
  charter-with-memory-home
  first-turn-carries-inputs
  incarnation-charter-of
  memory-home-of
  resume-params-of
  turn-memory-home-of])
(import doeff_agents.sessionhost.effects [
  EnvGet
  FsMakeDirs
  FsReadText
  FsWriteTextAtomic
  HeadlessSpawn
  LogLine
  SessionRow])
(import doeff_agents.sessionhost.headless [continue-headless-process])
(import doeff_agents.sessionhost.impls.claude_code [CLAUDE-AUTO-MEMORY-DIR-SETTING claude-code-impl])
(import doeff_agents.sessionhost.impls.headless_argv [headless-argv-impl])


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
;; V6 — 起こし方は閉語彙 NextArm ちょうど。どの腕でも運ばれ、resume の名簿でも落ちない
;; ---------------------------------------------------------------------------

(defn plan-of []
  (LaunchPlan :charter (charter-of CONVERSATION "/Users/x/repos/doeff")
              :predecessor None :lease-kind None :account None :profile "p" :model "claude-opus-5"))


(deftest test-every-arm-that-wakes-a-conversation-carries-the-memory-home
  ;; 起こし方は**閉語彙 effects.NextArm ちょうど**。手書きで 3 つ並べない — 並べると語彙が
  ;; 増えた日に新しい腕が 1 度も数えられないまま緑になる。実弾 card
  ;; acp:kanban-issue:ki-a068efe8f6d9: この検が launch / resume / rehydrate を手で並べていたので、
  ;; 4 つ目の起こし方(send の腕 = 降りた process を `--resume` で起こし直す拍)が数えられず、
  ;; 置き場はその腕だけ落ちていた(会社 Mac で 6 時間・冊 18 件が既定の置き場へ)。
  ;;
  ;; 腕の分類は**production の述語**で行う(検が第 2 の表を持たない):
  ;;   charter を組む腕(judgment.first-turn-carries-inputs)= charter が置き場を運ぶ
  ;;   send                                                = 手番が名乗る(turn-memory-home-of)
  ;;   defer                                               = 何も起こさない
  ;; 語彙に腕が 1 つ増えたら、どれにも当たらず**この検が落ちる**。
  (setv attribution {"conversationId" CONVERSATION})
  (setv home f"{MEMORY-ROOT}/{CONVERSATION}")
  (setv counted [])
  (for [arm (get-args NextArm)]
    (setv wakes-with-charter (run (first-turn-carries-inputs BACKEND-HEADLESS arm)))
    (cond
      wakes-with-charter
      (do
        (setv built (run (incarnation-charter-of
                           (plan-of) (ArmChoice :arm arm :source None :retire None)
                           "s-new" #() "" attribution "headless" None "/homes" MEMORY-ROOT None #())))
        (setv charter (get built 0))
        (assert (= (.get charter "memory_dir") home) #(arm (sorted (.keys charter))))
        (.append counted arm))

      (= arm NEXT-ARM-SEND)
      (do
        ;; 送る腕は charter を組まない(incarnate が早戻りする)。それでも headless の器は
        ;; 降りた process を起こし直すので、手番そのものが置き場を名乗る。
        (assert (= (run (turn-memory-home-of MEMORY-ROOT BACKEND-HEADLESS CONVERSATION)) home))
        ;; tui(tmux / herdr)の器には起こし直しの拍が無いので名乗らない。
        (assert (is (run (turn-memory-home-of MEMORY-ROOT "tmux" CONVERSATION)) None))
        (.append counted arm))

      (= arm NEXT-ARM-DEFER)
      ;; 会話を次の拍へ回すだけの腕 — process を起こさないので運ぶ物が無い。
      (.append counted arm)

      True
      (assert False
              (+ f"起こし方の語彙(effects.NextArm)に新しい腕 {arm !r} が増えている — "
                 "この検でその腕が自動記憶の置き場をどう運ぶか数えること"
                 "(手書きの列挙を足さない)"))))
  (assert (= (sorted counted) (sorted (list (get-args NextArm)))) counted)
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



;; ---------------------------------------------------------------------------
;; 継続の経路(降りた process の `--resume`)— 会話を起こす 4 つ目の腕
;; ---------------------------------------------------------------------------
;;
;; card acp:kanban-issue:ki-a068efe8f6d9 の実害はここで起きた: claude は 1 手番 1 process なので
;; 次の手番は必ず `--resume` の起こし直し(sessionhost.headless.continue-headless-process)で、
;; その argv は **行の launch_overlay だけ**から組まれていた。手番の荷(policy.TURN-CARRIED-KEYS =
;; 置き場と冊)は行に残らないので、この腕だけ**置き場も冊も両方**落ちていた。
;;
;; ⇒ 直し方は「行に写す」ではない(写した瞬間に第 2 の正本が生まれる — 正本は ACP の行・法 ACP
;;   575b1e)。継続も他の 3 腕と同じく**手番ごとに名乗り直す**: 送りの params で運び、
;;   置き場も冊もその手番の値だけが決める。
;;
;; ⚠ 受け入れ条件は**鍵の側**で書く(依頼書の罠): 「`--settings` が在る」では緑にならない —
;;   disableAllHooks も席の settings の宣言も同じ 1 つの `--settings` に載るので、宿の宣言次第で
;;   旗は残り、置き場だけが消えた状態が緑に見える(pod では全行が緑になる)。

(defclass ContinueWorld []
  (defn __init__ [self]
    (setv self.env {})       ;; EnvGet 台本(空 = 宿は何も宣言していない)
    (setv self.fs {})        ;; FsReadText 台本 + 器が書いた file(path → 本文)
    (setv self.dirs [])      ;; 器が作った置き場
    (setv self.log [])       ;; 器の log の行
    (setv self.spawns [])))  ;; 起こした process の argv の記録


(defhandler fake-continue-substrate [world]
  (EnvGet [name]
    (resume (.get world.env name)))
  (FsReadText [path]
    (resume (.get world.fs path)))
  (FsMakeDirs [path]
    (.append world.dirs path)
    (resume None))
  (FsWriteTextAtomic [path text tmp-suffix]
    (setv (get world.fs path) text)
    (resume None))
  (LogLine [text]
    (.append world.log text)
    (resume None))
  (HeadlessSpawn [session-name work-dir env argv events-path dialogue]
    (.append world.spawns (list argv))
    (resume 4242)))


(defn headless-row [overlay]
  "温かい claude の headless の行(次の手番は `--resume` の起こし直し)。"
  (SessionRow :session-id "s1" :session-name "doeff-s1" :pane-id ""
              :agent-type "claude" :lifecycle "multi_turn" :status "running"
              :started-at "2026-09-21T00:00:00Z"
              :work-dir "/Users/x/repos/doeff"
              :backend-kind "headless"
              :backend-ref {"session_name" "doeff-s1"
                            "events_path" "/ev/s1.jsonl"
                            "socket_path" "/tmp/agentd.sock"}
              :conversation {"session_id" "cli-session-1"}
              :launch-overlay overlay))


(defk continue-turn [world row [memory-dir ""] [memory-files #()]]
  {:pre [(: world ContinueWorld) (: row SessionRow) (: memory-dir str) (: memory-files tuple)]
   :post [(: % list)]}
  "継続の腕を 1 回回して、起こし直しの argv を返す(生 IO ゼロ)。
   器(impls/claude_code)は本物 — 置き場に書く物も**器が書いた物**を数える。"
  (<- _ ((fake-continue-substrate world)
         ((claude-code-impl "agentd")
          ((headless-argv-impl) (continue-headless-process row None memory-dir memory-files)))))
  (get world.spawns 0))


(defn settings-of-argv [argv]
  "argv の `--settings` の中身(JSON の object)。旗が無ければ {} — 読み手が
   **鍵の側**で書けるように(旗の有無で書くと宿の宣言に依って緑になる)。"
  (if (in "--settings" argv)
      (json.loads (get argv (+ 1 (.index argv "--settings"))))
      {}))


(defn files-in [world home]
  "置き場に器が書いた file の名。"
  (sorted (gfor path (.keys world.fs)
                :if (.startswith path f"{home}/")
                (.removeprefix path f"{home}/"))))


(setv HOME-OF-CONVERSATION f"{MEMORY-ROOT}/{CONVERSATION}")
;; 鍵の綴りは argv の導出点そのものから引く(検が第 2 の綴りを持たない —
;; 法 auto-memory-home-is-keyed-by-the-conversation / semgrep
;; doeff-agents-auto-memory-dir-spelling-has-one-home)。
(setv AUTO-MEMORY-SETTING CLAUDE-AUTO-MEMORY-DIR-SETTING)
;; 行に無い普通の行(置き場も冊も 1 欄も持たない — 継続の腕の行はこれしか在り得ない)。
(setv PLAIN-OVERLAY {"session_env" {"AGORA_CONVERSATION_ID" CONVERSATION}})


(defn sample-books [n]
  "N 冊 + 索引 + 基準(器へ渡す memory_files — 起こす側の memory-files-of と同じ形・行から読んだ体)。
   冊数は本文に焼かない。畳み戻しの基準(card acp:kanban-issue:ki-9fc7d4bca4dc)は名簿の**最後**。"
  (tuple (+ (lfor i (range n) {"name" f"book-{i}.md" "text" f"body {i}\n"})
            [{"name" MEMORY-INDEX-FILE "text" "# MEMORY\n"}
             {"name" MEMORY-BASE-FILE
              "text" (+ (json.dumps {"books" (dfor i (range n) f"book-{i}"
                                                  {"recordSeq" (+ i 1) "sha256" (* "a" 64) "version" 1})})
                        "\n")}])))


(deftest test-the-resumed-turn-points-at-the-conversations-memory-home
  ;; 受け入れ条件 1: `--resume` を持つ起動すべてで、settings の置き場の鍵
  ;; (AUTO-MEMORY-SETTING = 導出点の綴りそのもの)が**その会話の**置き場を指す。
  ;; 行は置き場を持たない ⇒ 指せるのは**この手番が名乗った値**だけ。
  (setv world (ContinueWorld))
  (setv argv (run (continue-turn world (headless-row PLAIN-OVERLAY) HOME-OF-CONVERSATION)))
  (assert (in "--resume" argv) argv)
  (assert (= (get argv (+ 1 (.index argv "--resume"))) "cli-session-1") argv)
  (assert (= (.get (settings-of-argv argv) AUTO-MEMORY-SETTING) HOME-OF-CONVERSATION)
          #((settings-of-argv argv) argv))
  ;; 別の会話の手番は別の置き場(相席しない — 混入は実際に発火した)。
  (setv other-world (ContinueWorld))
  (setv other (run (continue-turn other-world (headless-row PLAIN-OVERLAY)
                                  f"{MEMORY-ROOT}/{OTHER}")))
  (assert (!= (get (settings-of-argv other) AUTO-MEMORY-SETTING)
              (get (settings-of-argv argv) AUTO-MEMORY-SETTING))
          #(argv other)))


(deftest test-the-row-cannot-point-the-turn-at-a-memory-home
  ;; 手番の荷は**行に残さない**(正本は ACP の行・法 ACP 575b1e)。⇒ 行に置き場が写っていても
  ;; argv には出ない = 行は第 2 の正本になれない。ここが緑でないと、継続は行の古い値で起き続ける。
  (assert (not-in "memory_dir" LAUNCH-FLAG-KEYS) LAUNCH-FLAG-KEYS)
  (assert (in "memory_dir" TURN-CARRIED-KEYS) TURN-CARRIED-KEYS)
  (assert (in "memory_files" TURN-CARRIED-KEYS) TURN-CARRIED-KEYS)
  (setv stale (headless-row (| PLAIN-OVERLAY {"memory_dir" "/stale/home"
                                              "memory_files" [{"name" "old.md" "text" "OLD"}]})))
  ;; 名乗らない手番: 行に写っていても argv にも置き場にも 1 byte も出ない。
  (setv world (ContinueWorld))
  (setv argv (run (continue-turn world stale)))
  (assert (not-in AUTO-MEMORY-SETTING (settings-of-argv argv)) argv)
  (assert (= world.fs {}) world.fs)
  ;; 名乗った手番: その会話の置き場ちょうど(行の古い値は混じらない)。
  (setv fresh (ContinueWorld))
  (setv healed (run (continue-turn fresh stale HOME-OF-CONVERSATION)))
  (assert (= (get (settings-of-argv healed) AUTO-MEMORY-SETTING) HOME-OF-CONVERSATION) healed)
  (assert (= (files-in fresh "/stale/home") []) fresh.fs))


(deftest test-the-resumed-turn-hydrates-the-books-into-that-home
  ;; 落ちていたのは置き場だけではない(依頼者の訂正): 冊を書く口も継続の腕を通らなかった。
  ;; ⇒ 手番が名乗った冊が、名乗った置き場へ**器の手で**書かれること。N 冊 → N + 2 file
  ;; (冊 N + 索引 + 畳み戻しの基準)。冊数は本文に焼かない。
  ;; ⚠ 基準(card ki-9fc7d4bca4dc)が**この腕だけ**落ちると、次の畳み戻しは規則 3b(基準が無いのに
  ;; 行が在る)で 1 冊も書き戻さない — 普段の手番はすべてこの腕なので、基準はここで数える。
  (for [n #(0 1 3)]
    (setv world (ContinueWorld))
    (setv books (sample-books n))
    (run (continue-turn world (headless-row PLAIN-OVERLAY) HOME-OF-CONVERSATION books))
    (setv written (files-in world HOME-OF-CONVERSATION))
    (assert (= (len written) (+ n 2)) #("N 冊 → 置き場に N + 2 file(冊 + 索引 + 基準)" n written))
    (assert (in MEMORY-INDEX-FILE written) written)
    (assert (in MEMORY-BASE-FILE written) written)
    (assert (in HOME-OF-CONVERSATION world.dirs) world.dirs)
    ;; 本文は byte で届く(名だけ在って中身が空の file を数えない)。基準だけは器が書けた冊へ絞って
    ;; 置き直すので byte ではなく名で数える。
    (for [book books]
      (when (!= (get book "name") MEMORY-BASE-FILE)
        (assert (= (get world.fs f"{HOME-OF-CONVERSATION}/{(get book "name")}") (get book "text"))
                #("置き場の本文が器へ渡した本文と違う" (get book "name")))))
    (setv landed (json.loads (get world.fs f"{HOME-OF-CONVERSATION}/{MEMORY-BASE-FILE}")))
    (assert (= (sorted (.keys (get landed "books"))) (lfor i (range n) f"book-{i}"))
            #("継続の腕で基準が落ちた / 絞りが冊を落とした" landed))
    ;; 器が**書いた数**は起こす拍ごとに 1 行(起こす腕と同じ計器 — 継続の拍だけ黙らない)。
    (setv lines (lfor line world.log :if (in "agent-memory-written" line) line))
    (assert (= (len lines) 1) #("継続の拍も書いた数を 1 行で名乗る" world.log))
    (assert (in f"books={n}" (get lines 0)) (get lines 0))
    (assert (in "base=1" (get lines 0)) (get lines 0))
    (assert (in HOME-OF-CONVERSATION (get lines 0)) (get lines 0))))


(deftest test-a-turn-that-names-no-home-keeps-todays-argv
  ;; 置き場を誰も名乗らない行(根を宣言していない機体・綴りの組めない会話 id)は今日と同じ挙動 —
  ;; **欄を作らない**。⚠ ここが「`--settings` が在る」で書けない理由そのもの: 旗は
  ;; disableAllHooks で残るのに、置き場の鍵だけが無い(pod ではこの形が全行で緑に見えた)。
  (setv world (ContinueWorld))
  (setv argv (run (continue-turn world (headless-row PLAIN-OVERLAY))))
  (setv settings (settings-of-argv argv))
  (assert (in "--settings" argv) argv)
  (assert settings settings)
  (assert (not-in AUTO-MEMORY-SETTING settings) settings)
  ;; 置き場に手を付けない(空の置き場を正として残さない)。
  (assert (= world.dirs []) world.dirs)
  (assert (= world.fs {}) world.fs))
