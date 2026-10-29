;; 借りた資格の家で起こす codex の状態の DB の検 — 本番の handler + 替え玉の app-server(本物の codex 0.162.1 で測った状態の DB の
;; 振る舞いを持つ — 替え玉の頭の註)。
;;
;; 守る事: 状態の DB は使い捨ての資格の家の外(元の CODEX_HOME)に 1 つ在り、起動は会話の記録(sessions)の全部の大きさに比例する
;; 読み(backfill)を DB が無い時の 1 度しかしない。家を消した後も、別の家から同じ thread を thread/resume で続けられる。DB が無い時に
;; 同時に起こしても、DB が壊れず、どれも起動する。これが崩れると、記録が育つほど全部の Codex の起動が遅くなり、起動の待ちの上限を
;; 越えて会話の turn が起動できなくなる(card ki-f316f508a6a5 — 10-19 に 5.0 GB を起動のたびに読み直した)。
(require doeff-hy.macros [deftest defk <- val])
(val MODULE-TAGS {:context "codex-test" :role "program"})
(import dataclasses [replace])
(import sqlite3)
(import sys)
(import threading)
(import doeff_codex._credential_home :as credential-home)
(import doeff_codex._handler :as handler)
(import pathlib [Path])
(import doeff [with_handlers])
(import doeff_core_effects [scheduled Spawn Gather])
(import doeff_time [sync-time-handler])
(import doeff_codex [FreshThread ResumeThread CodexInput CodexSessionSpec CodexHome])
(import doeff_codex [AgentMessageDone])
(import doeff_codex [CodexHost codex-handler])
(import doeff_codex._credential_home [STATE-DB])
(import doeff_codex [CodexStartTurn CodexCloseSession SessionClosed TurnStarted])
(import tests.interpreters [STUB-PATH CODEX-HOME-DIR settings-for])
(import tests.scenario_steps [read-to-end records-of])

(val TURN-WAIT-SECONDS 30.0)
;; 検が置く大きな会話の記録(rollout の数と 1 本の bytes)。
(val ROLLOUTS 16)
(val ROLLOUT-BYTES 262144)
;; 同時に起こす数と、替え玉が DB を作る間に待つ秒(DB を作る起動の最中に、ほかの起動が重なる長さ)。
(val TOGETHER 3)
(val BACKFILL-SECONDS "1.0")
;; 借りた口座の auth.json の中身(検の作り物)。
(val LENT-AUTH-JSON "{\"auth_mode\":\"chatgpt\",\"OPENAI_API_KEY\":null,\"tokens\":{\"id_token\":null,\"access_token\":\"test-access\",\"refresh_token\":\"NO.T.test-marker\",\"account_id\":\"acct-test\"},\"last_refresh\":null}")
(val STATE-PHRASE "Tell the state.")


(defk lent-spec [#^ Path tmp-path #^ str prefix #^ dict extra-env]
  {:pre [(: tmp-path Path) (: prefix str) (: extra-env dict)] :post [(: % CodexSessionSpec)] :tags {:context "codex-test" :role "program"}}
  "借りた口座で起こす会話の宣言を作るため(替え玉の id の接頭辞 prefix — 起こすたびに id が 1 から数え直されて衝突しないように)。"
  (<- settings (settings-for "stub" tmp-path))
  (val env (| settings.spec.home.env {"STUB_ID_PREFIX" prefix} extra-env))
  (replace settings.spec :home (CodexHome :env env :auth-json LENT-AUTH-JSON)))

(defk base-of [#^ Path tmp-path]
  {:pre [(: tmp-path Path)] :post [(: % Path)] :tags {:context "codex-test" :role "program"}}
  "検の元の CODEX_HOME を作って返すため。"
  (val base (/ tmp-path CODEX-HOME-DIR))
  (.mkdir base :exist-ok True)
  base)

(defk put-rollouts [#^ Path base]
  {:pre [(: base Path)] :post [(: % int)] :tags {:context "codex-test" :role "program"}}
  "元の sessions に大きな会話の記録を置くため。答え = 置いた bytes の合計。"
  (val day (/ base "sessions" "2026" "10" "18"))
  (.mkdir day :parents True :exist-ok True)
  (for [index (range ROLLOUTS)]
    (.write-bytes (/ day (.format "rollout-2026-10-18T00-00-00-old-{:02d}.jsonl" index)) (* b"x" ROLLOUT-BYTES)))
  (* ROLLOUTS ROLLOUT-BYTES))

(defk said-state [#^ CodexSessionSpec spec origin]
  {:pre [(: spec CodexSessionSpec) (: origin (| FreshThread ResumeThread))] :post [(: % tuple)] :tags {:context "codex-test" :role "program"}}
  "会話の turn で替え玉に状態の DB の様子を言わせて閉じるため。答え = (thread の id, 答えの文)。"
  (<- started (CodexStartTurn origin spec (CodexInput :text STATE-PHRASE)))
  (assert (isinstance started TurnStarted) started)
  (<- so-far (read-to-end started.turn TURN-WAIT-SECONDS))
  (<- answers (records-of so-far AgentMessageDone))
  (assert (= (len answers) 1) answers)
  (<- closed (CodexCloseSession started.turn.thread-id "検の終わり"))
  (assert (isinstance closed SessionClosed) closed)
  #(started.turn.thread-id (. (get answers 0) text)))

(defk stub-handlers []
  {:pre [] :post [(: % list)] :tags {:context "codex-test" :role "program"}}
  "本番の handler + 替え玉の app-server の組を作るため。"
  [(sync-time-handler) (codex-handler (CodexHost #(sys.executable STUB-PATH) :launch-timeout 30.0))])

(defk sequence-of-launches [#^ Path tmp-path]
  {:pre [(: tmp-path Path)] :post [(: % tuple)] :tags {:context "codex-test" :role "program"}}
  "借りた口座で 3 回順に起こすため(1 回目・2 回目は新しい会話、3 回目は 1 回目の会話の続き — 1 回目と 2 回目の家は消えている)。"
  (<- spec-a (lent-spec tmp-path "a" {}))
  (<- spec-b (lent-spec tmp-path "b" {}))
  (<- spec-c (lent-spec tmp-path "c" {}))
  (<- first (said-state spec-a (FreshThread)))
  (<- second (said-state spec-b (FreshThread)))
  (<- third (said-state spec-c (ResumeThread :thread-id (get first 0))))
  #(first second third))

(defk spawned [#^ Path tmp-path #^ int index]
  {:pre [(: tmp-path Path) (: index int)] :post [(: % tuple)] :tags {:context "codex-test" :role "program"}}
  "index 番目から TOGETHER 番目の前までの会話を、DB を作る間を延ばした替え玉で同時に起こすため。答え = 起こした task の組。"
  (when (>= index TOGETHER) (return #()))
  (<- spec (lent-spec tmp-path (.format "t{}" index) {"STUB_BACKFILL_SECONDS" BACKFILL-SECONDS}))
  (<- task (Spawn (said-state spec (FreshThread))))
  (<- rest (spawned tmp-path (+ index 1)))
  (+ #(task) rest))

(defk launches-together [#^ Path tmp-path]
  {:pre [(: tmp-path Path)] :post [(: % list)] :tags {:context "codex-test" :role "program"}}
  "DB が無い所へ、借りた口座の会話を TOGETHER 本同時に起こすため。"
  (<- tasks (spawned tmp-path 0))
  (<- answers (Gather #* tasks))
  answers)


(defclass RepointGate []
  "DB の path の書き直しを、ほかの会話のターンが終わるまで(上限 GATE-SECONDS)止める門(disk が混んで書き直しが遅い間の模型)。
   書き直しが門で待った結末(開けられた = 真・上限で切れた = 偽・まだ待っていない = None)と、続きのターンが終わった時に門が
   開いていたか(ほかの会話のターンの後に終わったか)を記す。"
  (defn __init__ [self real]
    (setv self.real real
          self.opened (threading.Event)
          self.mut-waited None
          self.mut-resumed-after-other None))

  (defn repoint [self base]
    "門が開くまで待ってから本物の書き直しをする Program を作るため(handler が repoint-state-db の代わりに呼ぶ)。"
    (setv self.mut-waited (.wait self.opened GATE-SECONDS))
    (self.real base)))

;; 書き直しを止める上限(秒)。手で書き直す handler では turn-host の手がこの秒だけ止まり、門は上限で切れる。
(val GATE-SECONDS 15.0)

(defk plain-spec [#^ Path tmp-path #^ str prefix]
  {:pre [(: tmp-path Path) (: prefix str)] :post [(: % CodexSessionSpec)] :tags {:context "codex-test" :role "program"}}
  "借りた資格を持たない(家を作らない)会話の宣言を作るため。"
  (<- settings (settings-for "stub" tmp-path))
  (replace settings.spec :home (CodexHome :env (| settings.spec.home.env {"STUB_ID_PREFIX" prefix}))))

(defk resumed-then-noted [#^ CodexSessionSpec spec #^ str thread-id #^ RepointGate gate]
  {:pre [(: spec CodexSessionSpec) (: thread-id str) (: gate RepointGate)] :post [(: % tuple)] :tags {:context "codex-test" :role "program"}}
  "借りた口座の家から会話を続け、終わった事を門の順に記すため。"
  (<- said (said-state spec (ResumeThread :thread-id thread-id)))
  (setv gate.mut-resumed-after-other (.is-set gate.opened))
  said)

(defk other-then-open [#^ CodexSessionSpec spec #^ RepointGate gate]
  {:pre [(: spec CodexSessionSpec) (: gate RepointGate)] :post [(: % tuple)] :tags {:context "codex-test" :role "program"}}
  "ほかの会話のターンを 1 つ回し、終わったら門を開けるため(書き直しの間にこのターンが進めば門は上限より前に開く)。"
  (<- said (said-state spec (FreshThread)))
  (.set gate.opened)
  said)

(defk resume-beside-other [#^ Path tmp-path #^ str thread-id #^ RepointGate gate]
  {:pre [(: tmp-path Path) (: thread-id str) (: gate RepointGate)] :post [(: % list)] :tags {:context "codex-test" :role "program"}}
  "書き直しが門で止まる続きの起動と、ほかの会話のターンを同時に回すため。"
  (<- spec-a (lent-spec tmp-path "r" {}))
  (<- spec-b (plain-spec tmp-path "o"))
  (<- resumed (Spawn (resumed-then-noted spec-a thread-id gate)))
  (<- other (Spawn (other-then-open spec-b gate)))
  (<- answers (Gather resumed other))
  answers)


(deftest test-a-second-launch-from-a-lent-home-reads-no-session-records-and-resumes-across-homes [tmp-path]
  ;; 1 回目は DB が無いので記録の全部を読む。2 回目は、1 回目の家が消えた後の別の家から起こしても記録を 1 byte も読まず、DB の行が
  ;; 残っている(1 回目の会話の行と置いた記録の行)。3 回目は、1 回目と 2 回目の家が消えた後に 1 回目の会話を続けられる(DB の行の
  ;; rollout の path が、消えた家ではなく元の sessions を指す)。
  (<- base (base-of tmp-path))
  (<- written (put-rollouts base))
  (<- stack (stub-handlers))
  (<- said (scheduled (with_handlers stack (sequence-of-launches tmp-path))))
  (val first (get said 0))
  (val second (get said 1))
  (val third (get said 2))
  (assert (= (get first 1) (.format "STATE backfilled={} rows={}" written (+ ROLLOUTS 1))) first)
  (assert (= (get second 1) (.format "STATE backfilled=0 rows={}" (+ ROLLOUTS 2))) second)
  (assert (= (get third 0) (get first 0)) third)
  (assert (= (get third 1) (.format "STATE backfilled=0 rows={}" (+ ROLLOUTS 2))) third)
  ;; DB は家の外(元の CODEX_HOME)に 1 つ在り、家は残らない。
  (assert (.is-file (/ base STATE-DB)) (list (.iterdir base)))
  (assert (= (list (.iterdir (/ base ".credential-homes"))) []) (list (.iterdir (/ base ".credential-homes"))))
  (val connection (sqlite3.connect (str (/ base STATE-DB))))
  (val paths (lfor row (.execute connection "SELECT rollout_path FROM threads") (get row 0)))
  (.close connection)
  (assert (all (gfor path paths (not-in ".credential-homes" path))) paths))


(deftest test-lent-homes-launched-together-before-the-state-db-exists-all-start-and-keep-it-whole [tmp-path]
  ;; DB が無い所へ同時に起こすと、本物の codex は DB を作る最中の別の起動を降ろす(替え玉も同じ)。DB を作る起動を 1 本ずつにすれば、
  ;; どれも起動し、記録を読むのは 1 本だけで、DB は壊れない。
  (<- base (base-of tmp-path))
  (<- written (put-rollouts base))
  (<- stack (stub-handlers))
  (<- said (scheduled (with_handlers stack (launches-together tmp-path))))
  (val texts (sorted (gfor one said (get one 1))))
  (assert (= (len texts) TOGETHER) texts)
  (val backfilled (lfor text texts (int (get (.split (get (.split text "backfilled=") 1) " ") 0))))
  (assert (= (sorted backfilled) (+ (* [0] (- TOGETHER 1)) [written])) texts)
  (val connection (sqlite3.connect (str (/ base STATE-DB))))
  (assert (= (. (.execute connection "PRAGMA integrity_check") (fetchone)) #("ok")))
  (assert (= (get (.fetchone (.execute connection "SELECT count(*) FROM threads")) 0) (+ ROLLOUTS TOGETHER)))
  (.close connection))


(deftest test-a-slow-state-db-repoint-holds-only-the-resuming-launch-and-other-turns-go-on [tmp-path monkeypatch]
  ;; 家で起こすたびの DB の path の書き直しが遅い間(disk が混む・codex の書き込みと重なる — 配備の後の最初の起動で 59 秒)も、ほかの
  ;; 会話のターンは進む。書き直しを止める門は、ほかの会話のターンが終わって開ける。書き直しを handler の手で書くと、手が門で止まり、
  ;; ほかの会話のターンも進まず、門は上限で切れる。続きの起動は書き直しの終わりを待つので、消えた家を指していた行で断られない。
  (<- base (base-of tmp-path))
  (<- written (put-rollouts base))
  (<- spec-x (lent-spec tmp-path "x" {}))
  (<- stack (stub-handlers))
  (<- first (scheduled (with_handlers stack (said-state spec-x (FreshThread)))))
  (val gate (RepointGate credential-home.repoint-state-db))
  (.setattr monkeypatch credential-home "repoint_state_db" gate.repoint)
  (.setattr monkeypatch handler "repoint_state_db" gate.repoint :raising False)
  (<- stack-again (stub-handlers))
  (<- answers (scheduled (with_handlers stack-again (resume-beside-other tmp-path (get first 0) gate))))
  (val resumed (get answers 0))
  (val other (get answers 1))
  (assert (is gate.mut-waited True) gate.mut-waited)
  (assert (is gate.mut-resumed-after-other True) gate.mut-resumed-after-other)
  (assert (= (get resumed 0) (get first 0)) resumed)
  (assert (= (get resumed 1) (.format "STATE backfilled=0 rows={}" (+ ROLLOUTS 2))) resumed)
  (assert (.startswith (get other 1) "STATE backfilled=0") other)
  (assert (= (get first 1) (.format "STATE backfilled={} rows={}" written (+ ROLLOUTS 1))) first))
