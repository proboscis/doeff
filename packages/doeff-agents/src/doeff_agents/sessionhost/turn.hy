;;; turn 打刻の受け側(koine session surface v0 stage 1 — ADR-DOE-AGENTS-007 R5)。
;;;
;;; 席の hook が fire-and-forget(hard timeout ≤200ms・応答を読まない)で
;;; 書く打刻を受ける。受け側の義務 = hung を作らない: この経路は
;;; 「行引き + 3 列 UPDATE」のみで、substrate effect・subprocess・配送を
;;; 構造的に持たない(semgrep doeff-agents-turn-rpc-must-not-touch-substrate
;;; が禁止する)。書き込みは StoreActor で直列化される 1 op = 原子的。
;;;
;;; descriptor {pane_id, agent_name} の解決権威は sessionhost 側
;;; (turn-stamp-path 決定 3: 席は session id を知らない — id 配布機構は
;;; 作らない)。wait は opaque 保存 — 解釈権威(kind 語彙)は席側
;;; wait_protocol.py のみで、ここでは holder(= wait.who)以外を読まない。

(require doeff-hy.macros [deff])

(import json)
(import sqlite3)

(import doeff_agents.sessionhost.policy [ACTIVE-STATUSES])
(import doeff_agents.sessionhost.store [now-iso])


(setv TURN-HOLDER-AGENT "agent")
(setv TURN-HOLDER-DEFAULT-CLOSE "work")


(deff turn-close-holder [wait]
  {:pre [(: wait (| dict None))]
   :post [(: % str)]}
  "turn_close の holder = wait.who(あれば — user/work 等へ手番が渡る)/
   無ければ \"work\"(発注元確定 2026-07-21)。who 以外の wait field は
   読まない(再 parse 禁止 — turn-stamp-path 所見 2)。"
  (setv who (if (isinstance wait dict) (.get wait "who") None))
  (if (and (isinstance who str) (.strip who))
      who
      TURN-HOLDER-DEFAULT-CLOSE))


(deff db-resolve-turn-target [conn pane-id agent-name]
  {:pre [(: conn sqlite3.Connection) (: pane-id (| str None))
         (: agent-name (| str None))]
   :post [(: % (| str None))]}
  "descriptor → adopt 済み非終端行の session_id(pane_id 第一鍵・agent_name
   第二鍵 = session_name 照合)。複数一致は started_at DESC, session_id ASC
   の先頭(session.list と同じ全順序)。不在は None = 未 adopt(呼び手が
   正直 no-op + counter を返す — エラーにも黙殺にもしない)。"
  (setv actives (sorted ACTIVE-STATUSES))
  (setv placeholders (.join ", " (lfor _ actives "?")))
  (setv base (+ "SELECT session_id FROM agent_sessions "
                f"WHERE adopted = 1 AND status IN ({placeholders}) AND "))
  (setv order " ORDER BY started_at DESC, session_id ASC LIMIT 1")
  (when (is-not pane-id None)
    (setv row (.fetchone (.execute conn (+ base "pane_id = ?" order)
                                   (tuple (+ actives [pane-id])))))
    (when (is-not row None)
      (return (get row 0))))
  (when (is-not agent-name None)
    (setv row (.fetchone (.execute conn (+ base "session_name = ?" order)
                                   (tuple (+ actives [agent-name])))))
    (when (is-not row None)
      (return (get row 0))))
  None)


(deff db-turn-stamp [conn pane-id agent-name holder wait]
  {:pre [(: conn sqlite3.Connection) (: pane-id (| str None))
         (: agent-name (| str None)) (: holder str) (> (len holder) 0)
         (: wait (| dict None))]
   :post [(: % (| str None))]}
  "turn 打刻の実体(actor 内で 1 op として実行 = 解決と書き込みが原子的):
   descriptor 解決 → turn_holder / turn_since / turn_wait_json の 3 列
   UPDATE。turn_open は wait=None で通り turn_wait_json を NULL に戻す。
   戻り値 = 更新した session_id / None(未 adopt — 何も書かない)。
   status には触れない(stalled は wire 導出のみ — signal only、R4)。"
  (setv sid (db-resolve-turn-target conn pane-id agent-name))
  (when (is sid None)
    (return None))
  (setv wait-json (if (is wait None)
                      None
                      (json.dumps wait :sort-keys True :separators #("," ":")
                                  :ensure-ascii False)))
  (.execute conn
            (+ "UPDATE agent_sessions SET turn_holder = ?, turn_since = ?, "
               "turn_wait_json = ? WHERE session_id = ?")
            #(holder (now-iso) wait-json sid))
  sid)
