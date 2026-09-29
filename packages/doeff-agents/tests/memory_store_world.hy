;;; 直接束縛の検(launch・monitor の policy・未送信の prompt の掃き取り)の fake の世界が、session の行の置き場に package の fake
;;; (memory-session-store)を使うための口。置き場は走りの間だけ memory-session-store の session の値に在るので、世界は走りの合間の
;;; 中身(store)と、検が読む写し(rows = session id → SessionRow・events = #(session id 種類) の列)を持つ。
;;; 置き場への書きは走りの中でも外でも package の書きの判断(with-row — SQLite の store と同じ重ね方)を通す。
(require doeff-hy.macros [defk <- var])
(import dataclasses [replace])
(import doeff [Program])
(import doeff_agents.sessionhost.effects [SessionRow])
(import doeff_agents.sessionhost.store_memory [MemorySessionRows ReadMemorySessionRows stored-row stored-rows with-row])


(defclass StoreWorld []
  "置き場を memory-session-store に置く fake の世界の型(__init__ で store・rows・events を置く — 下の defk の読み手)。"
  #^ MemorySessionRows store
  #^ dict rows
  #^ list events)


(defk remember-store [world store]
  {:pre [(: world StoreWorld) (: store MemorySessionRows)] :post [(: % None)] :tags {:context "session-store-test" :role "foundation"}}
  "置き場の今の中身を世界へ写すため: 次の走りの初めの中身(store)と、検が読む行と出来事の写し(rows・events)。"
  (<- rows dict (stored-rows store))
  (setattr world "store" store)
  (.clear world.rows)
  (.update world.rows rows)
  (.clear world.events)
  (.extend world.events (lfor event store.events #(event.session-id event.event-type)))
  None)


(defk kept-in [world program]
  {:pre [(: world StoreWorld) (: program Program)] :post [(: % "program の答え(型は program ごと)")]
   :tags {:context "session-store-test" :role "foundation"}}
  "memory-session-store の内側で program を走らせ、終わった時(落ちた時も)の置き場を世界へ写すため。"
  (var answer None)
  (try
    (<- ran program)
    (:= answer ran)
    (finally
      (<- store MemorySessionRows (ReadMemorySessionRows))
      (<- (remember-store world store))))
  answer)


(defk seeded [world row]
  {:pre [(: world StoreWorld) (: row SessionRow)] :post [(: % None)] :tags {:context "session-store-test" :role "foundation"}}
  "検の下地の行を置き場へ書くため(SessionStoreUpsert と同じ書きの判断 — 読まずに書いた行)。"
  (<- store MemorySessionRows (with-row world.store row))
  (<- (remember-store world store))
  None)


(defk reported [world session-id payload]
  {:pre [(: world StoreWorld) (: session-id str) (: payload str)] :post [(: % None)]
   :tags {:context "session-store-test" :role "foundation"}}
  "結果の報告が行の結果の欄に着いた状態を作るため(SQLite の store では report_result の db-report-result-guarded-update が同じ列へ
   書く — 結果の欄は最初の書きが勝つ)。"
  (<- row (stored-row world.store session-id))
  (assert (is-not row None) session-id)
  (<- (seeded world (replace row :result-payload payload)))
  None)
