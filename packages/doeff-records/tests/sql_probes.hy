;;; 検の代役の SQL の答え手 probe-sql-handler — 本物の PostgreSQL に流しつつ、driver の手前で文を数え・止め・落とす(旧い版の検の
;;; 代役の host LocklessHost・FailingMidBatchHost・CountingConnection と同じ段の差し替え)。postgres-sql-handler と同じ部品
;;; (postgres_sql.hy の文の手順と sql_transaction.hy の run-in-transaction)で答え、違うのは QueryProbe が決める 3 点だけ:
;;;   keep-lock = False なら SqlTransaction の lock-key の錠を取らない(錠の検の反例)
;;;   barrier   = 行の錠(FOR UPDATE)の読みの後で待ち合わせる(2 本の書きが両方「行が無い」を読んでから書く反例)
;;;   fail-at   = state_rows への n 本目の INSERT を流さずに SqlUnreachable を答える(書きの途中で接続が落ちる代役)
;;; counts = 流れた CREATE の文の数(thread の間で共有)。書きの合図(SqlNotify)は同じ接続で流す(呼び鈴は鳴らさない — 待ちの検はこの代役を使わない)。
(require doeff-hy.macros [defhandler defk <- val var])
(import dataclasses [dataclass field])
(import threading)
(import doeff_core_effects.sql_effects [SqlQuery SqlTransaction SqlUnreachable])
(import doeff_core_effects.sql_transaction [run-in-transaction])
(import doeff_core_effects.postgres_sql [PostgresConnections postgres-query postgres-insert postgres-notify postgres-begin postgres-control])


(defclass StatementCounts []
  "流れた文の数え(thread の間で共有): creates = CREATE の文の数 / upserts = state_rows への INSERT の数。"
  (defn __init__ [self]  ; defk にできない: 検の資源の class の初期化
    (setv self.lock (threading.Lock) self.creates 0 self.upserts 0))
  (defn record [self #^ str statement]  ; defk にできない: 複数の thread の run から同期に数える
    "文 1 つを数え、それが state_rows への何本目の INSERT か(INSERT でなければ 0)を返す。"
    (with [self.lock]
      (when (.startswith (.lstrip statement) "CREATE") (+= self.creates 1))
      (if (in "state_rows (ledger" statement)
          (do (+= self.upserts 1) self.upserts)
          0))))


(defclass [(dataclass :frozen True)] QueryProbe []
  "代役の決め事(頭の註)。"
  (#^ StatementCounts counts)
  (setv #^ bool keep-lock True)
  (setv #^ (| threading.Barrier None) barrier None)
  (setv #^ (| int None) fail-at None))


(defk probed-query [probe leased request]
  {:pre [(: probe QueryProbe) (: leased "psycopg の接続") (: request SqlQuery)] :post [(: % "SqlRows | SqlFailed | SqlUnreachable")]
   :tags {:context "records" :role "foundation"}}
  "文 1 つを数え、決め事どおりに落とすか流して待ち合わせるため。"
  (val nth (.record probe.counts request.statement))
  (when (and (is-not probe.fail-at None) (= nth probe.fail-at))
    (return (SqlUnreachable :reason "検の代役: 書きの途中で接続が落ちた")))
  (<- answer (postgres-query leased request))
  (when (and (is-not probe.barrier None) (.endswith (.rstrip request.statement) "FOR UPDATE"))
    (.wait probe.barrier))
  answer)


(defhandler probe-sql-handler [#^ PostgresConnections connections #^ str answered #^ QueryProbe probe]
  ;; 引数に残す理由: 接続の貸し出しと代役の決め事は検ごとに作る資源。answered は答える database の名。
  "検の代役の SQL の答え手(頭の註)。database の SqlQuery と SqlTransaction にだけ答える。"
  {:tags {:context "records" :role "foundation"}}
  (SqlQuery [database statement params] :when (= database answered)
    (val leased (.acquire connections database))
    (try
      (<- answer (probed-query probe leased (SqlQuery database statement params)))
      (finally (.release connections database leased)))
    (resume answer))
  (SqlTransaction [database program lock-key] :when (= database answered)
    (val leased (.acquire connections database))
    (try
      (<- answer (run-in-transaction database program
                                     (fn [request] (probed-query probe leased request))
                                     (fn [request] (postgres-insert leased request))
                                     (fn [] (postgres-begin leased (if probe.keep-lock lock-key None)))
                                     (fn [] (postgres-control leased "COMMIT"))
                                     (fn [] (postgres-control leased "ROLLBACK"))
                                     :execute-notify (fn [request] (postgres-notify leased request.channel))))
      (finally (.release connections database leased)))
    (resume answer)))
