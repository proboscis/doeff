;;; session の行の置き場(SessionStore* の effect)の契約テストの解釈器(composition root)— 同じ契約の Program を、置き場の答え手だけ
;;; 替えて走らせる。
;;;
;;;   sqlite-session-store  本物: sqlite-session-store(走るたびに新しい一時 dir の SQLite の file と、書きを直列にする StoreActor の thread)
;;;   memory-session-store  fake: memory-session-store(空の MemorySessionRows)+ state(session の値の置き場)
;;;
;;; 出来事の記録は SessionStore* の effect では読めないので、契約の Program は RecordedEvents で読む — 本物は agent_session_events の行、
;;; fake は置き場の events。使い手は conftest.py の doeff_interpreter(deftest の :interpreters の名 → INTERPRETERS)。
(require doeff-hy.macros [defk defhandler <- val var])
(import dataclasses [dataclass])
(import json)
(import os)
(import tempfile)
(import doeff [EffectBase Program with_handlers])
(import doeff_core_effects.handlers [state])
(import doeff_agents.sessionhost.store [StoreActor sqlite-session-store])
(import doeff_agents.sessionhost.store_memory [MemorySessionRows ReadMemorySessionRows memory-session-store])

(val SQLITE-SESSION-STORE "sqlite-session-store")
(val MEMORY-SESSION-STORE "memory-session-store")


(defclass [(dataclass :frozen True)] RecordedEvents [EffectBase]
  "置き場が記録した出来事を記録した順に読む効果(答え = #(session id 種類 載せた行の dict) の list)。")


(defhandler sqlite-events [actor]
  ;; 引数に残す理由: 読む先は解釈器が走るたびに作る actor。
  (RecordedEvents []
    (val rows (.submit actor (fn [conn]
                               (.fetchall (.execute conn (+ "SELECT session_id, event_type, payload_json "
                                                            "FROM agent_session_events ORDER BY id"))))))
    (resume (lfor #(session-id event-type payload) rows #(session-id event-type (json.loads payload))))))


(defhandler memory-events
  (RecordedEvents []
    (<- store MemorySessionRows (ReadMemorySessionRows))
    (resume (lfor event store.events #(event.session-id event.event-type event.payload)))))


(defk under-sqlite-session-store [program]
  {:pre [(: program Program)] :post [(: % "契約の Program の答え(型は Program ごと)")]
   :tags {:context "session-store-test" :role "foundation"}}
  "本物の sqlite-session-store の下で、新しい一時 dir の SQLite の file を置き場にして program を走らせるため(走った後に actor を閉じ、
   一時 dir を消す)。"
  (var answer None)
  (with [directory (tempfile.TemporaryDirectory)]
    (val actor (StoreActor (os.path.join directory "agentd.sqlite")))
    (try
      (<- ran (with_handlers [(sqlite-session-store actor) (sqlite-events actor)] program))
      (:= answer ran)
      (finally
        (.close actor))))
  answer)


(defk under-memory-session-store [program]
  {:pre [(: program Program)] :post [(: % "契約の Program の答え(型は Program ごと)")]
   :tags {:context "session-store-test" :role "foundation"}}
  "fake の memory-session-store の下で、空の置き場から program を走らせるため。"
  (<- answer (with_handlers [(state) (memory-session-store (MemorySessionRows)) memory-events] program))
  answer)


(val INTERPRETERS {SQLITE-SESSION-STORE under-sqlite-session-store
                   MEMORY-SESSION-STORE under-memory-session-store})
