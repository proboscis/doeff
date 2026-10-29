;;; 追いついた読み手の待たない変更の読み(ki-ca4b41612e2e — 呼び手の往復の内訳で、service の中の秒の 9 割が置き場の読みだった):
;;;   (1) 位置が置き場の頭そのもの(cursor.sequence = 頭)の変更の読みは、頭の 1 文だけを流し、変更の列の文を流さない — 読む範囲(位置, 頭] が
;;;       空なので答えは同じ空の変更で、次の位置は頭のまま。頭より後ろの位置(書きの後)は今までどおり変更の列の文も流して変更を返す
;;;   (2) 待つ秒が 0 の変更の読みは、置き場の通知の呼び鈴を掛けも外しもしない(待たないので、読みと掛けの間の書きを取りこぼさない)
;;; 使い捨ての PostgreSQL の置き場に、本番の置き場の handler(pg-records-handler)を据え、外側へ出る SQL の効果を数える答え手を挟んで確かめる。
;;; 直す前は (1) の頭の読みで文 2 本(頭・変更の列)、(2) で呼び鈴の掛けと外しが 1 つずつ出て赤。
(require doeff-hy.macros [deftest defhandler defeffect defk <- val var])
(import dataclasses [dataclass])  ; defeffect の展開が名指す
(import pytest)
(import doeff [EffectBase Program run with_handlers])
;; 束の読みの読み解きと綴りの Compute(ADR-DOE-RECORDS-002)を仮想の時計の上でその場で回す(本番は thread の pool)。
(import doeff_core_effects [inline-compute-handler])
(import doeff_core_effects [state])
(import doeff_core_effects [scheduled])
(import doeff_core_effects.sql_effects [SqlQuery SqlHangNotice SqlDropNotice])
(import doeff_core_effects.postgres_sql [postgres-sql-handler])
(import doeff_time [SimClock sim-time-handler])
(import doeff_hy.frozen [FrozenMap])
(import doeff_records [PutRow WatchChanges])
(import doeff_records [Changes ExpectAbsent Reset WatchCursor])
(import doeff_records._laws [MAKER])
(import doeff_records.pg [drop-records-tables pg-records-handler])
(import tests.interpreters [DATABASE ORIGIN-HOST fresh-prefix pg-skip-reason postgres-connections prepared-store run-sql])

(val pytestmark (pytest.mark.skipif (bool (pg-skip-reason)) :reason (pg-skip-reason)))


(defeffect TalliedSql
  "ここまでに置き場の handler が外へ出した SQL の効果の種(query・hang・drop)を出た順に読む — 検だけの問い(sql-tallied が答える)。"
  {:fields [] :answer (get tuple #(str ...)) :tags {:context "records" :role "intent"}})


(defhandler sql-tallied
  "置き場の handler の外側で、SQL の文(SqlQuery)・呼び鈴の掛け(SqlHangNotice)・外し(SqlDropNotice)を数えてから、外の答え手へ渡す。"
  {:tags {:context "records" :role "foundation"}}
  (session var kinds #())
  (SqlQuery []
    (:= kinds (+ kinds #("query")))
    (<- answer effect)
    (resume answer))
  (SqlHangNotice []
    (:= kinds (+ kinds #("hang")))
    (<- answer effect)
    (resume answer))
  (SqlDropNotice []
    (:= kinds (+ kinds #("drop")))
    (<- answer effect)
    (resume answer))
  (TalliedSql []
    (resume kinds)))


(defk tallied-during [program]
  {:pre [(: program (| Program EffectBase))] :post [(: % tuple)] :tags {:context "records" :role "foundation"}}
  "program を走らせ、答えと、その間に出た SQL の効果の種の列を組で返すため。"
  (<- before tuple (TalliedSql))
  (<- answer program)
  (<- after tuple (TalliedSql))
  #(answer (cut after (len before) None)))


(defk watch-at-head-and-behind []
  {:pre [] :post [(: % tuple)] :tags {:context "records" :role "program"}}
  "表 parts の頭の位置を読み(版の外れの Reset から床へ・床から頭へ)、頭での待たない読みと、1 行書いた後に同じ位置からの待たない読みを、
   それぞれ SQL の効果の種と組で返すため。"
  (<- reset Reset (WatchChanges #("parts") (WatchCursor 0 0)))
  (<- caught Changes (WatchChanges #("parts") (WatchCursor reset.epoch reset.floor)))
  (<- at-head tuple (tallied-during (WatchChanges #("parts") caught.cursor)))
  (<- _written (PutRow "parts" #("p1") (FrozenMap {"label" "a"}) (ExpectAbsent)))
  (<- behind tuple (tallied-during (WatchChanges #("parts") caught.cursor)))
  #(caught at-head behind))


(defk watched-on-postgres []
  {:pre [] :post [(: % tuple)] :tags {:context "records" :role "foundation"}}
  "使い捨ての PostgreSQL の置き場を用意し、本番の置き場の handler と数える答え手の下で watch-at-head-and-behind を走らせて、表を片付けるため。"
  (val connections (postgres-connections))
  (val store (prepared-store connections (fresh-prefix)))
  (try
    (run (scheduled (with_handlers [(sim-time-handler :clock (SimClock)) inline-compute-handler (postgres-sql-handler connections) (state) sql-tallied
                                    (pg-records-handler store MAKER ORIGIN-HOST)]
                                   (watch-at-head-and-behind))))
    (finally
      (run-sql connections (drop-records-tables store)))))


(deftest test-a-change-read-at-the-head-runs-only-the-head-statement-and-hangs-no-bell
  (<- seen tuple (watched-on-postgres))
  (val caught (get seen 0))
  (val at-head (get seen 1))
  (val behind (get seen 2))
  ;; (1)(2) 頭での待たない読み: 文は頭の 1 本だけ・呼び鈴は無い・答えは空の変更で位置は頭のまま。
  (assert (= (get at-head 1) #("query")) at-head)
  (assert (isinstance (get at-head 0) Changes) at-head)
  (assert (= (. (get at-head 0) items) #()) at-head)
  (assert (= (. (get at-head 0) cursor) caught.cursor) #(at-head caught))
  ;; 書きの後(頭より後ろの位置): 頭と変更の列の 2 本を流し、書いた 1 行の変わりを返す。待たないので呼び鈴は無い。
  (assert (= (get behind 1) #("query" "query")) behind)
  (assert (= (len (. (get behind 0) items)) 1) behind)
  (assert (> (. (get behind 0) cursor sequence) caught.cursor.sequence) behind))
