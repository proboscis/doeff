;;; 汎用の SQL の effect(sql_effects.hy)の、既存の DB の file を開く sqlite の答え手 sqlite-file-sql-handler(agora-redesign #2237 の前提 —
;;; 預かり所の本体を doeff-cluster へ移す。今の本体(Haskell の StoreSqlite.hs)が持つ file の DB を、そのまま開いて読み書きするため)。
;;; 業務を知らない: 表は呼び手が SqlEnsureTables で宣言するか、file に既に在る。
;;;   - 組み立ての側(composition root)が database の宣言(SqliteFile — 名と file の path)の tuple から open-sqlite-files で接続を開き
;;;     (SqliteFiles)、答え手へ渡し、止める時に close-sqlite-files で閉じる。閉じた後の effect は SqlUnreachable。
;;;   - 既存の file を開くだけ: URI の mode=rw で開き、file が無ければ作らずに開く所で sqlite3.OperationalError を上げる(作り直さない・
;;;     コピーしない — 空の DB を黙って作ると、預かり所が空の台帳で動き出す)。
;;;   - 本体と同じく WAL(`PRAGMA journal_mode=WAL` — 答えが wal でなければ開く所で断る)で、database ごとに接続 1 本・書きは直列
;;;     (scheduler の thread で順に流す)。
;;;   - 答え方は memory の DB の答え手(sqlite_sql.hy)と同じ道(sqlite-answer-query・-insert・-tables・-transaction)を通る: `:name` の引数の書き換え・失敗は SqlFailed(SQLSTATE の類の
;;;     表)/ SqlUnreachable の値(例外を呼び手へ抜かない)・BOOLEAN の読み戻し・SqlTransaction は BEGIN IMMEDIATE … COMMIT / ROLLBACK。
;;;     文の中で時計を読まない(刻は呼び手が引数で渡す)。SqlTransaction の lock-key は使わない(接続 1 本で直列)。
;;;   - SetSqlOutage で database を不達にでき、その間は全部 SqlUnreachable(不達の印は session の値 — 置き場の state が外側に要る)。
(require doeff-hy.macros [defhandler defk <- val var])
(require doeff-hy.record [defrecord])
(import sqlite3)
(import pathlib [Path])
(import dataclasses [dataclass])
(import doeff_core_effects.sql_effects [SqlQuery SqlInsertRows SqlTransaction SqlEnsureTables SetSqlOutage])
(import doeff_core_effects.sqlite_sql [SqliteConnection sqlite-connection connection-of outage-marked
                                       sqlite-answer-query sqlite-answer-insert sqlite-answer-tables sqlite-answer-transaction])

;; 開いた接続の journal_mode に求める答え(本体の StoreSqlite.hs と同じ)。
(val JOURNAL-MODE "wal")


(defrecord SqliteFile
  "答える database の宣言: 名(effect の database)と、既存の DB の file の path。"
  (#^ str name)
  (#^ str path))


(defrecord SqliteFiles
  "開いた file の DB の接続(database ごとに 1 本)。open-sqlite-files で作り、close-sqlite-files で閉じる。"
  (#^ (get tuple #(SqliteConnection ...)) connections))


(defk sqlite-file-connection [file]
  {:pre [(: file SqliteFile)] :post [(: % SqliteConnection)]
   :tags {:context "sql" :role "foundation"}}
  "既存の DB の file 1 つを WAL で開くため(頭の註)。file が無い・WAL にならない時は開く所で例外。"
  (val connection (! (sqlite-connection (+ (.as-uri (.resolve (Path file.path))) "?mode=rw") True)))
  (val mode (get (.fetchone (.execute connection "PRAGMA journal_mode=WAL")) 0))
  (when (!= mode JOURNAL-MODE)
    (.close connection)
    (raise (sqlite3.OperationalError (.format "{} を WAL で開けない(journal_mode = {})" file.path mode))))
  (SqliteConnection :name file.name :connection connection))


(defk open-sqlite-files [files]
  {:pre [(: files (of tuple SqliteFile ...)) (all (gfor f files (isinstance f SqliteFile)))
         (= (len files) (len (set (gfor f files f.name))))]
   :post [(: % SqliteFiles)]
   :tags {:context "sql" :role "foundation"}}
  "database の宣言の並びから接続を開くため(名は重ねない)。途中で開けなければ、開いた分を閉じてから例外を通す。"
  (var opened #())
  (try
    (for [f files]
      (<- connection (sqlite-file-connection f))
      (:= opened (+ opened #(connection))))
    (except [Exception]
      (for [c opened]
        (.close c.connection))
      (raise)))
  (SqliteFiles :connections opened))


(defk close-sqlite-files [files]
  {:pre [(: files SqliteFiles)] :post [(: % None)]
   :tags {:context "sql" :role "foundation"}}
  "開いた接続を全部閉じるため(組み立ての側が止める時に呼ぶ)。閉じた後の effect は SqlUnreachable を答える。"
  (for [c files.connections]
    (.close c.connection))
  None)


(defhandler sqlite-file-sql-handler [#^ SqliteFiles files]
  ;; 引数に残す理由: 開いた接続は組み立ての側が持つ資源で、答え手の外で閉じる(Ask では閉じる側と同じ物を指せない)。
  "既存の DB の file に答える SQL の答え手(頭の註)。files に名の無い database の effect は外側へ回す。"
  {:tags {:context "sql" :role "foundation"}}
  (session var unreachable #())
  (SetSqlOutage [database down] :when (any (gfor c files.connections (= c.name database)))
    (<- marked (outage-marked unreachable database down))
    (:= unreachable marked)
    (resume None))
  (SqlQuery [database statement params] :when (any (gfor c files.connections (= c.name database)))
    (<- connection (connection-of files.connections database))
    (<- answer (sqlite-answer-query connection unreachable (SqlQuery database statement params)))
    (resume answer))
  (SqlInsertRows [database table columns rows] :when (any (gfor c files.connections (= c.name database)))
    (<- connection (connection-of files.connections database))
    (<- answer (sqlite-answer-insert connection unreachable (SqlInsertRows database table columns rows)))
    (resume answer))
  (SqlEnsureTables [database tables] :when (any (gfor c files.connections (= c.name database)))
    (<- connection (connection-of files.connections database))
    (<- answer (sqlite-answer-tables connection unreachable database tables))
    (resume answer))
  (SqlTransaction [database program lock-key] :when (any (gfor c files.connections (= c.name database)))
    (<- connection (connection-of files.connections database))
    (<- answer (sqlite-answer-transaction connection unreachable database program))
    (resume answer)))
