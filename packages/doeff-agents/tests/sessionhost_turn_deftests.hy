;;; 直接束縛 deftest: koine turn 打刻 + liveness 導出(ADR-DOE-AGENTS-007)。
;;;
;;; Verification 8(issue koine-session-surface-stage1)の level-triggered
;;; 一周: open turn で stalled 導出 → 復帰打刻で clear。close 済み
;;; (WAIT 待ち)は経過時間によらず非 stalled・open 打刻の欠落でも成立
;;; (turn-stamp-path 所見 3 — edge-triggered 実装の禁止面)。
;;; 加えて turn.hy の descriptor 解決と 3 列 UPDATE を実 SQLite で検証する。
;;;
;;; 波 1-S1(ADR-DOE-AGENTS-007 改訂・law stamp-never-crosses-identities)の
;;; 解決鍵: pane_id 第一鍵(descriptor が conversation_id を運ぶ時は別会話を
;;; 名乗る行に落ちない・会話未登記〔NULL〕行は可)→ conversation_id 第二鍵
;;; (identity 鍵)→ agent_name 第三鍵は descriptor が pane_id も
;;; conversation_id も運ばない時のみ(吸い込み実弾 2026-08-17 の構造的排除)。

(require doeff-hy.macros [deftest])

(import datetime [datetime timedelta timezone])
(import json)
(import os)
(import shutil)
(import tempfile)

(import doeff_agents.sessionhost.policy [turn-stalled])
(import doeff_agents.sessionhost.store [
  open-conn
  db-migrate
  db-upsert-snapshot
  db-session-get])
(import doeff_agents.sessionhost.turn [
  TURN-HOLDER-AGENT
  db-resolve-turn-target
  db-turn-stamp
  turn-close-holder])


(defn make-adopted-snap [session-id #** overrides]
  "adopt 行の snapshot dict(store-of-record 形)。"
  (setv base {"session_id" session-id
              "session_name" f"seat-{session-id}"
              "pane_id" f"%{session-id}"
              "agent_type" "claude"
              "work_dir" ""
              "lifecycle" "interactive"
              "status" "running"
              "backend_kind" "tmux"
              "backend_ref" {"kind" "tmux" "ref" f"%{session-id}"}
              "started_at" "2026-07-21T00:00:00+00:00"
              "last_observed_at" None
              "finished_at" None
              "cleaned_at" None
              "pr_url" None
              "output_snippet" None
              "terminal_cause" None
              "expected_result" None
              "retries_used" 0
              "last_validation_error" None
              "awaiting_response" False
              "observed_active_at" None
              "result_payload" None
              "result_solicitations_used" 0
              "prompt_unblock_attempts" 0
              "last_output_change_at" None
              "effective_identity" None
              "adopted" True})
  (.update base overrides)
  base)


(defn with-tmp-conn [thunk]
  (setv d (tempfile.mkdtemp))
  (try
    (setv conn (open-conn (os.path.join d "agentd.sqlite")))
    (try
      (db-migrate conn)
      (thunk conn)
      (finally (.close conn)))
    (finally (shutil.rmtree d :ignore-errors True))))


;; ---------------------------------------------------------------------------
;; stalled 導出(純関数 — wire 出力時に毎回呼ばれる level-triggered 面)
;; ---------------------------------------------------------------------------

(deftest test-turn-stalled-derivation
  (setv now (datetime.now timezone.utc))
  (setv old (.isoformat (- now (timedelta :seconds 2000))))
  (setv fresh (.isoformat (- now (timedelta :seconds 10))))
  ;; open turn(holder=agent)が閾値超過 → stalled
  (assert (is (turn-stalled "agent" old now 1800) True))
  ;; open turn だが閾値内 → 非 stalled
  (assert (is (turn-stalled "agent" fresh now 1800) False))
  ;; close 済み(WAIT 待ち)は経過時間によらず非 stalled — 待つのは正常状態
  (assert (is (turn-stalled "user" old now 1800) False))
  (assert (is (turn-stalled "work" old now 1800) False))
  ;; open 打刻の欠落(holder 無し)でも成立 — edge-triggered 前提の禁止面
  (assert (is (turn-stalled None None now 1800) False))
  (assert (is (turn-stalled None old now 1800) False))
  ;; turn_since 欠落の open は非 stalled(導出不能を stalled と裁定しない)
  (assert (is (turn-stalled "agent" None now 1800) False)))


;; ---------------------------------------------------------------------------
;; 打刻 → 導出 → 復帰打刻 → clear の level-triggered 一周(実 SQLite)
;; ---------------------------------------------------------------------------

(deftest test-turn-stamp-liveness-cycle
  (defn check [conn]
    (db-upsert-snapshot conn (make-adopted-snap "s1"))
    ;; turn-open 打刻(pane_id 第一鍵)
    (setv sid (db-turn-stamp conn "%s1" None None TURN-HOLDER-AGENT None))
    (assert (= sid "s1"))
    (setv snap (db-session-get conn "s1"))
    (assert (= (get snap "turn_holder") "agent"))
    (assert (is-not (get snap "turn_since") None))
    (assert (is (get snap "turn_wait") None))
    ;; 放置をシミュレート(turn_since を過去へ)→ stalled 導出 true
    (setv now (datetime.now timezone.utc))
    (setv old (.isoformat (- now (timedelta :seconds 2000))))
    (.execute conn "UPDATE agent_sessions SET turn_since = ? WHERE session_id = 's1'"
              #(old))
    (setv snap (db-session-get conn "s1"))
    (assert (is (turn-stalled (get snap "turn_holder") (get snap "turn_since")
                              now 1800)
                True))
    ;; 復帰打刻(再 open)で clear — level-triggered: 新しい turn_since から
    ;; 再導出されるだけで、どこにも stalled は保存されていない
    (db-turn-stamp conn "%s1" None None TURN-HOLDER-AGENT None)
    (setv snap (db-session-get conn "s1"))
    (assert (is (turn-stalled (get snap "turn_holder") (get snap "turn_since")
                              now 1800)
                False))
    ;; close 打刻(agent_name 鍵 — 素の descriptor 限定・wait は opaque 保存)
    ;; → 放置しても非 stalled(WAIT 待ちは正常状態)
    (setv wait {"who" "user" "kind" "decide" "reason" "レビュー待ち"})
    (setv sid2 (db-turn-stamp conn None "seat-s1" None "user" wait))
    (assert (= sid2 "s1"))
    (setv snap (db-session-get conn "s1"))
    (assert (= (get snap "turn_holder") "user"))
    (assert (= (get snap "turn_wait") wait))
    (.execute conn "UPDATE agent_sessions SET turn_since = ? WHERE session_id = 's1'"
              #(old))
    (setv snap (db-session-get conn "s1"))
    (assert (is (turn-stalled (get snap "turn_holder") (get snap "turn_since")
                              now 1800)
                False)))
  (with-tmp-conn check))


;; ---------------------------------------------------------------------------
;; descriptor 解決の境界(adopt 済み非終端行のみ・未 adopt は None)
;; ---------------------------------------------------------------------------

(deftest test-turn-resolution-only-adopted-nonterminal
  (defn check [conn]
    ;; 未 adopt(launch 起点)行は打刻対象にならない
    (db-upsert-snapshot conn (make-adopted-snap "launch1" :adopted False))
    (assert (is (db-resolve-turn-target conn "%launch1" "seat-launch1" None) None))
    (assert (is (db-turn-stamp conn "%launch1" "seat-launch1" None "agent" None)
                None))
    ;; terminal な adopt 行も対象外(打刻が行を蘇生しない)
    (db-upsert-snapshot conn (make-adopted-snap "dead" :status "exited"))
    (assert (is (db-resolve-turn-target conn "%dead" "seat-dead" None) None))
    ;; 生きた adopt 行は pane_id / 素の agent_name のどちらでも解決できる
    (db-upsert-snapshot conn (make-adopted-snap "live"))
    (assert (= (db-resolve-turn-target conn "%live" None None) "live"))
    (assert (= (db-resolve-turn-target conn None "seat-live" None) "live"))
    ;; holder 既定(wait 無し close)= work / wait.who があればそれ
    (assert (= (turn-close-holder None) "work"))
    (assert (= (turn-close-holder {"kind" "decide"}) "work"))
    (assert (= (turn-close-holder {"who" "user" "kind" "decide"}) "user")))
  (with-tmp-conn check))


;; ---------------------------------------------------------------------------
;; 波 1-S1: identity 鍵と名前鍵の適用条件(law stamp-never-crosses-identities)
;; ---------------------------------------------------------------------------

(deftest test-turn-resolution-identity-keys
  (defn check [conn]
    ;; 会話つき行(c1・pane %old)と会話未登記行(NULL・pane %bare)
    (db-upsert-snapshot conn (make-adopted-snap "old"
                                                :conversation {"session_id" "c1"}))
    (db-upsert-snapshot conn (make-adopted-snap "bare"))
    ;; conversation 第二鍵: pane 未登記でも会話の行へ届く
    (assert (= (db-resolve-turn-target conn "%moved" None "c1") "old"))
    (assert (= (db-resolve-turn-target conn None None "c1") "old"))
    ;; pane 鍵 × 会話合意: 別会話を名乗る stamp は pane 一致でも落ちない
    (assert (is (db-resolve-turn-target conn "%old" None "c2") None))
    (assert (= (db-resolve-turn-target conn "%old" None "c1") "old"))
    ;; NULL 会話行は会話つき stamp を受ける(pre-S1 登記の継続受理)
    (assert (= (db-resolve-turn-target conn "%bare" None "c9") "bare"))
    ;; 同一 pane に会話一致行と NULL 行が並ぶ時は一致行が勝つ(鮮度より同一性)
    (db-upsert-snapshot conn (make-adopted-snap "twin-null"
                                                :pane_id "%twin"
                                                :started_at "2026-08-17T02:00:00+00:00"))
    (db-upsert-snapshot conn (make-adopted-snap "twin-conv"
                                                :pane_id "%twin"
                                                :started_at "2026-08-17T01:00:00+00:00"
                                                :conversation {"session_id" "c3"}))
    (assert (= (db-resolve-turn-target conn "%twin" None "c3") "twin-conv"))
    ;; 名前鍵は素の descriptor(pane も conversation も無し)限定 —
    ;; 識別子を運ぶ打刻の名前 fallback は吸い込み(実弾 2026-08-17)の形
    (assert (is (db-resolve-turn-target conn "%fresh-pane" "seat-old" None) None))
    (assert (is (db-resolve-turn-target conn "%fresh-pane" "seat-bare" "c9") None))
    (assert (= (db-resolve-turn-target conn None "seat-bare" None) "bare")))
  (with-tmp-conn check))
