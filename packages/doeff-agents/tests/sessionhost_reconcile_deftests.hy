;;; adopted 行 reconciler の直接束縛 deftest(ADR-DOE-AGENTS-007 R8/R9)。
;;;
;;; daemon 不要 — 実 SQLite(in-memory・db-migrate 済み)+ 台本生存一覧
;;; (TmuxListSessions)+ 固定 Clock の合成検体で reconcile-cycle を回す。
;;; 実台帳・実 substrate には一切触れない(無副作用・残置ゼロ)。
;;;
;;; 検体 3 態(発注の受入条件):
;;;   ① 消滅   — 持続不在(観測 K 回 + 時間窓)のみが vanished 終端に届く
;;;   ② 改名   — 同 pane 改名 = 名前追随 / 改名復活 = superseded + 後継 ID
;;;   ③ 一過性不在 — 復帰で streak クリア・決して終端しない(D566 の pin)
;;; 追加の負例: 供給断 skip(integration-lead 条件①)/ 起動猶予
;;; (同 条件②)/ 同一性不確かの保留 / 会話 live 痕跡の vanish 阻止。

(require doeff-hy.macros [deftest defk deff <- defhandler])

(import datetime [datetime timezone timedelta])
(import json)
(import sqlite3)

(import doeff_agents.sessionhost.effects [
  SessionRow
  SessionStoreGet
  SessionStoreListActive
  SessionStoreRecordEvent
  SessionStoreReconcileClearAbsence
  SessionStoreReconcileFollowRename
  SessionStoreReconcileMarkAbsent
  SessionStoreReconcileVanish
  SessionStoreReconcileSupersede
  TmuxListSessions
  ClockNow])
(import doeff_agents.sessionhost.policy [ACTIVE-STATUSES iso-format])
(import doeff_agents.sessionhost.store [
  db-migrate
  db-session-get
  db-session-list
  db-record-event
  db-reconcile-clear-absence
  db-reconcile-follow-rename
  db-reconcile-mark-absent
  db-reconcile-vanish
  db-reconcile-supersede
  snapshot-to-policy-row])
(import doeff_agents.sessionhost.reconcile [
  reconcile-cycle
  classify-adopted-row
  build-live-index
  find-successor-id])
(import doeff_agents.sessionhost.host [
  AdoptedSessionNotOwned
  cancel-program
  cleanup-program])


;; ---------------------------------------------------------------------------
;; 合成検体の世界: 実 SQLite + 台本生存一覧 + 固定 clock
;; ---------------------------------------------------------------------------

(setv T0 (datetime 2026 8 17 12 0 0 :tzinfo timezone.utc))

(defclass ReconcileWorld []
  (defn __init__ [self]
    (setv self.conn (sqlite3.connect ":memory:"))
    (db-migrate self.conn)
    (setv self.live [])                ;; TmuxListSessions の台本(list[dict])
    (setv self.live-raises False)      ;; True = 供給断(socket 例外の合成)
    (setv self.now T0)
    (setv self.events [])))            ;; [(session-id, event-type)]

(defn advance [world seconds]
  (setv world.now (+ world.now (timedelta :seconds seconds))))

(defn seed-row [world sid name pane conv #** kw]
  "adopted running 行を実 SQLite へ直接植える(既定 adopted=1)。"
  (.execute world.conn
    (+ "INSERT INTO agent_sessions (session_id, session_name, pane_id, "
       "agent_type, work_dir, status, backend_kind, backend_ref_json, "
       "started_at, adopted, conversation_json) "
       "VALUES (?, ?, ?, 'claude', '', ?, 'tmux', '{}', ?, ?, ?)")
    #(sid name pane
      (.get kw "status" "running")
      (.get kw "started_at" (iso-format world.now))
      (.get kw "adopted" 1)
      (if (is conv None) None (json.dumps {"session_id" conv})))))

(defn live-entry [name pane conv]
  {"session_name" name "pane_id" pane "conversation_id" conv})

(defn row-of [world sid]
  (db-session-get world.conn sid))

(defhandler reconcile-world-handler [world]
  "実 store(guarded UPDATE は本物の SQL)+ 台本生存一覧 + 固定 clock。"
  (TmuxListSessions []
    (when world.live-raises
      (raise (RuntimeError "synthetic supply cut: inventory unreachable")))
    (resume (list world.live)))

  (ClockNow []
    (resume world.now))

  (SessionStoreListActive []
    (resume (lfor snap (db-session-list world.conn
                                        {"status" (sorted ACTIVE-STATUSES)})
                  (snapshot-to-policy-row snap))))

  (SessionStoreRecordEvent [session-id event-type row]
    (.append world.events #(session-id event-type))
    (db-record-event world.conn session-id event-type {})
    (resume None))

  (SessionStoreReconcileClearAbsence [session-id]
    (resume (db-reconcile-clear-absence world.conn session-id)))

  (SessionStoreReconcileFollowRename [session-id new-name]
    (resume (db-reconcile-follow-rename world.conn session-id new-name)))

  (SessionStoreReconcileMarkAbsent [session-id observed-at]
    (resume (db-reconcile-mark-absent world.conn session-id observed-at)))

  (SessionStoreReconcileVanish [session-id observed-at cutoff-iso min-checks]
    (resume (db-reconcile-vanish world.conn session-id observed-at
                                 cutoff-iso min-checks)))

  (SessionStoreReconcileSupersede [session-id successor-session-id observed-at]
    (resume (db-reconcile-supersede world.conn session-id
                                    successor-session-id observed-at))))

(defhandler adopted-row-store [row]
  "R10 検証用の最小 handler: SessionStoreGet のみ。cancel / cleanup の
   adopted 拒否は require-session-row の直後 = substrate effect の解釈すら
   要らないこと自体が「substrate 接触前の拒否」の検証になっている。"
  (SessionStoreGet [session-id]
    (resume row)))

(defk run-reconcile [world #** kw]
  {:pre [(: world ReconcileWorld)]
   :post [(: % dict)]}
  (<- summary ((reconcile-world-handler world)
               (reconcile-cycle (.get kw "allow_vanish" True)
                                (.get kw "window_seconds" 900)
                                (.get kw "min_checks" 3))))
  summary)


;; ---------------------------------------------------------------------------
;; store 面(guarded UPDATE の SQL 前提条件)
;; ---------------------------------------------------------------------------

(deftest test-reconcile-absence-streak-accrues-and-presence-clears
  ;; since は first-write-wins(streak の起点)・checks は加算・presence で
  ;; 両方リセット。非 adopted / 終端行には書けない(guarded)。
  (setv world (ReconcileWorld))
  (seed-row world "r1" "seat-a" "%p1" None)
  (seed-row world "r2" "seat-b" "%p2" None :adopted 0)
  (seed-row world "r3" "seat-c" "%p3" None :status "exited")
  (assert (= (db-reconcile-mark-absent world.conn "r1" "2026-08-17T12:00:00+00:00") 1))
  (assert (= (db-reconcile-mark-absent world.conn "r1" "2026-08-17T12:01:00+00:00") 1))
  (setv snap (row-of world "r1"))
  (assert (= (get snap "substrate_absent_since") "2026-08-17T12:00:00+00:00"))
  (assert (= (get snap "substrate_absent_checks") 2))
  ;; guarded: 非 adopted / 終端は 0 行
  (assert (= (db-reconcile-mark-absent world.conn "r2" "2026-08-17T12:00:00+00:00") 0))
  (assert (= (db-reconcile-mark-absent world.conn "r3" "2026-08-17T12:00:00+00:00") 0))
  ;; presence でクリア(no-op 時は 0 行 = 書かない)
  (assert (= (db-reconcile-clear-absence world.conn "r1") 1))
  (setv snap (row-of world "r1"))
  (assert (is (get snap "substrate_absent_since") None))
  (assert (= (get snap "substrate_absent_checks") 0))
  (assert (= (db-reconcile-clear-absence world.conn "r1") 0)))

(deftest test-reconcile-vanish-gates-on-checks-window-and-successor
  ;; vanish の前提条件は SQL に彫られている: checks ≥ K ∧ since ≤ cutoff ∧
  ;; 同一会話の非終端の別行なし。どれが欠けても 0 行(= 終端しない)。
  (setv world (ReconcileWorld))
  (seed-row world "g1" "ghost" "%gone" "conv-g")
  ;; checks 不足(1 < 3)
  (db-reconcile-mark-absent world.conn "g1" "2026-08-17T12:00:00+00:00")
  (assert (= (db-reconcile-vanish world.conn "g1" "2026-08-17T13:00:00+00:00"
                                  "2026-08-17T12:30:00+00:00" 3) 0))
  ;; checks 充足・窓未達(since > cutoff)
  (db-reconcile-mark-absent world.conn "g1" "2026-08-17T12:01:00+00:00")
  (db-reconcile-mark-absent world.conn "g1" "2026-08-17T12:02:00+00:00")
  (assert (= (db-reconcile-vanish world.conn "g1" "2026-08-17T12:03:00+00:00"
                                  "2026-08-17T11:59:00+00:00" 3) 0))
  ;; 同一会話の非終端の別行(後継候補)が居る間は vanish 禁止
  (seed-row world "g2" "revived" "%new" "conv-g")
  (assert (= (db-reconcile-vanish world.conn "g1" "2026-08-17T13:00:00+00:00"
                                  "2026-08-17T12:30:00+00:00" 3) 0))
  (.execute world.conn "UPDATE agent_sessions SET status='exited' WHERE session_id='g2'")
  ;; 全条件充足 → vanished 終端(status=exited・cause=vanished・finished_at)
  (assert (= (db-reconcile-vanish world.conn "g1" "2026-08-17T13:00:00+00:00"
                                  "2026-08-17T12:30:00+00:00" 3) 1))
  (setv snap (row-of world "g1"))
  (assert (= (get snap "status") "exited"))
  (assert (= (get (get snap "terminal_cause") "category") "vanished"))
  (assert (is-not (get snap "finished_at") None))
  ;; 冪等: 終端済みへは 0 行
  (assert (= (db-reconcile-vanish world.conn "g1" "2026-08-17T13:01:00+00:00"
                                  "2026-08-17T12:30:00+00:00" 3) 0)))

(deftest test-reconcile-supersede-requires-live-same-conversation-successor
  ;; supersede は「後継行が実在し・非終端で・同一会話」のときのみ(SQL guard)。
  (setv world (ReconcileWorld))
  (seed-row world "old" "seat-old" "%dead" "conv-x"
            :started_at "2026-08-16T00:00:00+00:00")
  (seed-row world "succ" "s-newname" "%alive" "conv-x"
            :started_at "2026-08-17T00:00:00+00:00")
  (seed-row world "other" "seat-y" "%y" "conv-other")
  ;; 別会話の行を後継に指定 → 0 行(識別子越えの紐づけ禁止)
  (assert (= (db-reconcile-supersede world.conn "old" "other"
                                     "2026-08-17T12:00:00+00:00") 0))
  ;; 正しい後継 → 終端 + successor 記帳 + cause=superseded(retryable=false)
  (assert (= (db-reconcile-supersede world.conn "old" "succ"
                                     "2026-08-17T12:00:00+00:00") 1))
  (setv snap (row-of world "old"))
  (assert (= (get snap "status") "exited"))
  (assert (= (get snap "successor_session_id") "succ"))
  (assert (= (get (get snap "terminal_cause") "category") "superseded"))
  (assert (= (get (get snap "terminal_cause") "retryable") False))
  ;; 後継が終端していたら紐づけない(生きた後継のみ)
  (seed-row world "old2" "seat-old2" "%dead2" "conv-y"
            :started_at "2026-08-16T00:00:00+00:00")
  (seed-row world "succ2" "seat-new2" "%alive2" "conv-y"
            :status "exited" :started_at "2026-08-17T00:00:00+00:00")
  (assert (= (db-reconcile-supersede world.conn "old2" "succ2"
                                     "2026-08-17T12:00:00+00:00") 0)))

(deftest test-reconcile-follow-rename-guarded
  ;; 改名追随は adopted 非終端 かつ 名前が実際に変わる時のみ 1 行。
  (setv world (ReconcileWorld))
  (seed-row world "n1" "old-name" "%p" "conv-n")
  (assert (= (db-reconcile-follow-rename world.conn "n1" "old-name") 0))
  (assert (= (db-reconcile-follow-rename world.conn "n1" "s-minted") 1))
  (assert (= (get (row-of world "n1") "session_name") "s-minted"))
  (.execute world.conn "UPDATE agent_sessions SET status='exited' WHERE session_id='n1'")
  (assert (= (db-reconcile-follow-rename world.conn "n1" "s-again") 0)))


;; ---------------------------------------------------------------------------
;; program 面 — 合成検体 3 態
;; ---------------------------------------------------------------------------

(deftest test-reconcile-cycle-transient-absence-never-terminalizes
  ;; 検体③(D566 の pin・発注の必須負例): 一過性の不在は決して終端に
  ;; 潰れない。(a) 復帰で streak がクリアされる (b) 窓・回数未達では
  ;; どれだけ周期を回しても非終端のまま。
  (setv world (ReconcileWorld))
  (seed-row world "t1" "seat-t" "%pt" "conv-t")
  ;; 不在 2 周期(checks 未達)。生存一覧は非空(供給断ではない)。
  (setv world.live [(live-entry "unrelated" "%other" "conv-z")])
  (<- s1 (run-reconcile world))
  (advance world 60)
  (<- s2 (run-reconcile world))
  (assert (= (get (row-of world "t1") "status") "running"))
  (assert (= (get (row-of world "t1") "substrate_absent_checks") 2))
  ;; 復帰(fleet の数十秒不在からの回復)→ streak 全クリア・running のまま
  (setv world.live [(live-entry "seat-t" "%pt" "conv-t")])
  (advance world 60)
  (<- s3 (run-reconcile world))
  (setv snap (row-of world "t1"))
  (assert (= (get snap "status") "running"))
  (assert (is (get snap "substrate_absent_since") None))
  (assert (= (get snap "substrate_absent_checks") 0))
  ;; 再び不在 K 回 — ただし窓(900s)未達: 60s 間隔 3 回では終端しない
  (setv world.live [(live-entry "unrelated" "%other" "conv-z")])
  (advance world 60)
  (<- s4 (run-reconcile world))
  (advance world 60)
  (<- s5 (run-reconcile world))
  (advance world 60)
  (<- s6 (run-reconcile world))
  (setv snap (row-of world "t1"))
  (assert (= (get snap "status") "running"))
  (assert (= (get snap "substrate_absent_checks") 3))
  (assert (= (lfor e world.events :if (= (get e 1) "session_vanished") e) [])))

(deftest test-reconcile-cycle-vanishes-only-after-sustained-absence
  ;; 検体①(消滅): 観測 3 回 + 窓 900s の両方が満ちて初めて vanished。
  ;; 満ちる直前の周期では running のままであることも陽に見る。
  (setv world (ReconcileWorld))
  (seed-row world "v1" "seat-v" "%pv" "conv-v")
  (setv world.live [(live-entry "unrelated" "%other" None)])
  (<- s1 (run-reconcile world))
  (advance world 500)
  (<- s2 (run-reconcile world))
  (assert (= (get (row-of world "v1") "status") "running"))
  (advance world 500)  ;; since から 1000s 経過・3 回目の観測
  (<- s3 (run-reconcile world))
  (setv snap (row-of world "v1"))
  (assert (= (get snap "status") "exited"))
  (assert (= (get (get snap "terminal_cause") "category") "vanished"))
  (assert (= (get s3 "vanished") 1))
  (assert (in #("v1" "session_vanished") world.events))
  ;; 終端後の周期は対象外(active 一覧から消える)
  (advance world 60)
  (<- s4 (run-reconcile world))
  (assert (= (get s4 "vanished") 0)))

(deftest test-reconcile-cycle-rename-follows-same-pane
  ;; 検体②a(同 pane 改名 = R55 鋳造名化): 同じ pane で名前だけ変わった席は
  ;; 同一の宿り — session_name を追随更新し、行は running のまま。
  (setv world (ReconcileWorld))
  (seed-row world "rn1" "task-seat" "%pr" "conv-r")
  (setv world.live [(live-entry "s-minted99" "%pr" "conv-r")])
  (<- s1 (run-reconcile world))
  (setv snap (row-of world "rn1"))
  (assert (= (get snap "status") "running"))
  (assert (= (get snap "session_name") "s-minted99"))
  (assert (in #("rn1" "session_renamed") world.events))
  (assert (= (get s1 "renamed") 1))
  ;; 会話なし世代でも同 pane の改名は追随する(pane が宿りの物理)
  (seed-row world "rn2" "old-bulk" "%pb" None)
  (setv world.live [(live-entry "s-minted99" "%pr" "conv-r")
                    (live-entry "s-bulk-new" "%pb" None)])
  (<- s2 (run-reconcile world))
  (setv snap (row-of world "rn2"))
  (assert (= (get snap "status") "running"))
  (assert (= (get snap "session_name") "s-bulk-new")))

(deftest test-reconcile-cycle-supersedes-renamed-revival
  ;; 検体②b(改名復活): 旧 pane 消滅・同一会話が新行(別 pane)で登記済み →
  ;; 旧行は vanished でなく superseded + 後継 ID。会話は ended と読ませない
  ;; (終端印は宿りの終端 — R9)。
  (setv world (ReconcileWorld))
  (seed-row world "old" "coupling-core-review" "%w3R" "conv-c"
            :started_at "2026-08-16T00:00:00+00:00")
  (seed-row world "new" "s-c137bf" "%fresh" "conv-c"
            :started_at "2026-08-17T00:00:00+00:00")
  (setv world.live [(live-entry "s-c137bf" "%fresh" "conv-c")])
  (<- s1 (run-reconcile world))
  (setv old-snap (row-of world "old"))
  (assert (= (get old-snap "status") "exited"))
  (assert (= (get (get old-snap "terminal_cause") "category") "superseded"))
  (assert (= (get old-snap "successor_session_id") "new"))
  (assert (in #("old" "session_superseded") world.events))
  ;; 後継の行は無傷で running
  (assert (= (get (row-of world "new") "status") "running"))
  ;; vanished は 1 件も出ていない
  (assert (= (get s1 "vanished") 0)))

(deftest test-reconcile-cycle-conversation-live-elsewhere-blocks-vanish
  ;; 会話が別 pane に生存(改名復活の痕跡)・後継行は未登記 → 保留のまま。
  ;; どれだけ時間が経っても vanish しない(後継行の供給は S2 便の仕事)。
  (setv world (ReconcileWorld))
  (seed-row world "m1" "moved-seat" "%oldpane" "conv-m")
  (setv world.live [(live-entry "s-renamed" "%newpane" "conv-m")])
  (<- s1 (run-reconcile world))
  (advance world 3600)
  (<- s2 (run-reconcile world))
  (advance world 3600)
  (<- s3 (run-reconcile world))
  (setv snap (row-of world "m1"))
  (assert (= (get snap "status") "running"))
  (assert (= (get snap "substrate_absent_checks") 0))  ;; 不在にも数えない
  (assert (= (get s3 "hold") 1)))

(deftest test-reconcile-cycle-supply-cut-skips-absence
  ;; integration-lead 条件①: 生存一覧が空 = 供給断 — 不在を 1 回も記帳せず
  ;; 周期ごと skip。例外(socket 不達)は最初の effect で伝播し、書き込みは
  ;; 1 つも起きない(観測の不成立 ≠ 不在 — 波1席の精密化と同語)。
  (setv world (ReconcileWorld))
  (seed-row world "sc1" "seat-sc" "%psc" "conv-sc")
  ;; 空一覧 = 供給断
  (setv world.live [])
  (<- s1 (run-reconcile world))
  (assert (= (get s1 "skipped") "supply_cut"))
  (assert (= (get (row-of world "sc1") "substrate_absent_checks") 0))
  ;; 例外 = 供給断(書き込みゼロで伝播)
  (setv world.live-raises True)
  (setv raised False)
  (try
    (<- s2 (run-reconcile world))
    (except [RuntimeError]
      (setv raised True)))
  (assert raised)
  (assert (= (get (row-of world "sc1") "substrate_absent_checks") 0)))

(deftest test-reconcile-cycle-startup-grace-blocks-vanish
  ;; integration-lead 条件②: allow-vanish=False(起動猶予中)の間は、SQL の
  ;; 前提条件が満ちていても vanish を試みない。猶予明けの周期で終端する。
  (setv world (ReconcileWorld))
  (seed-row world "gr1" "seat-gr" "%pgr" "conv-gr")
  (setv world.live [(live-entry "unrelated" "%o" None)])
  (<- s1 (run-reconcile world :allow_vanish False))
  (advance world 1000)
  (<- s2 (run-reconcile world :allow_vanish False))
  (advance world 1000)
  (<- s3 (run-reconcile world :allow_vanish False))
  ;; 窓 2000s・checks 3 — 条件は満ちているが猶予中は running のまま
  (assert (= (get (row-of world "gr1") "status") "running"))
  (advance world 60)
  (<- s4 (run-reconcile world :allow_vanish True))
  (assert (= (get (row-of world "gr1") "status") "exited")))

(deftest test-reconcile-cycle-holds-when-identity-uncertain
  ;; 同一性不確か(倒れ先 = 不明側): (a) 会話あり行の pane に別の何かが
  ;; 生きている (b) 会話なし行の名前だけ別 pane に生存 (c) 同一会話が
  ;; 複数 live(異常)— いずれも書かない・不在にも数えない。
  (setv world (ReconcileWorld))
  (seed-row world "h1" "seat-h1" "%ph1" "conv-h1")
  (seed-row world "h2" "seat-h2" "%ph2" None)
  (seed-row world "h3" "seat-h3" "%ph3" "conv-h3")
  (setv world.live [(live-entry "someone-else" "%ph1" None)
                    (live-entry "seat-h2" "%elsewhere" None)
                    (live-entry "dup-a" "%pa" "conv-h3")
                    (live-entry "dup-b" "%pb" "conv-h3")])
  (<- s1 (run-reconcile world))
  (assert (= (get s1 "hold") 3))
  (for [sid ["h1" "h2" "h3"]]
    (setv snap (row-of world sid))
    (assert (= (get snap "status") "running"))
    (assert (= (get snap "substrate_absent_checks") 0))))

(deftest test-adopted-cancel-cleanup-typed-refusal
  ;; R10(fold 判定 2026-08-17): adopted 行への session.cancel /
  ;; session.cleanup は typed 拒否 — SessionHost が作っていない外部の実席を
  ;; kill で巻き添えにしない。拒否は substrate 接触より前。
  (setv row (SessionRow :session-id "a1" :session-name "seat-a" :pane-id "%pa"
                        :agent-type "claude" :lifecycle "interactive"
                        :status "running"
                        :started-at "2026-08-17T00:00:00+00:00" :adopted True))
  (setv cancel-raised False)
  (try
    (<- _ ((adopted-row-store row) (cancel-program "a1")))
    (except [AdoptedSessionNotOwned]
      (setv cancel-raised True)))
  (assert cancel-raised)
  (setv cleanup-raised False)
  (try
    (<- _ ((adopted-row-store row) (cleanup-program "a1")))
    (except [AdoptedSessionNotOwned]
      (setv cleanup-raised True)))
  (assert cleanup-raised))

(deftest test-reconcile-classify-and-successor-pure-faces
  ;; 純関数面の直接検証(分類と後継解決の全順序)。
  (setv index (build-live-index
                [(live-entry "a" "%p1" "c1")
                 (live-entry "b" "%p2" None)]))
  (defn mk [sid name pane conv started]
    (SessionRow :session-id sid :session-name name :pane-id pane
                :agent-type "claude" :lifecycle "interactive" :status "running"
                :started-at started :adopted True
                :conversation (if (is conv None) None {"session_id" conv})))
  ;; 会話一致・同 pane = alive(現在名を運ぶ)
  (assert (= (classify-adopted-row (mk "x" "old" "%p1" "c1" "2026-08-17T00:00:00+00:00") index)
             #("alive" "a")))
  ;; 会話一致・別 pane = alive_moved
  (assert (= (classify-adopted-row (mk "x" "a" "%px" "c1" "2026-08-17T00:00:00+00:00") index)
             #("alive_moved" None)))
  ;; 会話なし・同 pane 別名 = alive(改名追随)
  (assert (= (classify-adopted-row (mk "x" "old" "%p2" None "2026-08-17T00:00:00+00:00") index)
             #("alive" "b")))
  ;; 痕跡なし = absent
  (assert (= (classify-adopted-row (mk "x" "gone" "%pz" "cz" "2026-08-17T00:00:00+00:00") index)
             #("absent" None)))
  ;; 後継解決 = 非終端の最新(started_at DESC, session_id ASC)・自分が最新なら None
  (setv rows [(mk "r-old" "n1" "%1" "cc" "2026-08-16T00:00:00+00:00")
              (mk "r-new" "n2" "%2" "cc" "2026-08-17T00:00:00+00:00")])
  (assert (= (find-successor-id (get rows 0) rows) "r-new"))
  (assert (is (find-successor-id (get rows 1) rows) None)))
