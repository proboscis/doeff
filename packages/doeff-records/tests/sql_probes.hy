;;; 検の代役の SQL の答え手 probe-sql-handler — 本物の PostgreSQL に流しつつ、driver の手前で DB への往復を数え・止め・落とす(旧い版の検の
;;; 代役の host LocklessHost・FailingMidBatchHost・CountingConnection と同じ段の差し替え)。postgres-sql-handler と同じ部品
;;; (postgres_sql.hy の文の手順・往復 1 回を pipeline 1 つで送る postgres-flush と、sql_transaction.hy の run-in-transaction /
;;; run-in-batched-transaction)で答え、違うのは QueryProbe が決める 3 点だけ:
;;;   keep-lock = False なら SqlTransaction の lock-key の錠を取らない(錠の検の反例)
;;;   barrier   = 行の錠(FOR UPDATE)の読みを含む往復の後で待ち合わせる(2 本の書きが両方「行が無い」を読んでから書く反例)
;;;   fail-at   = state_rows への n 本目の INSERT を含む往復を流さずに SqlUnreachable を答える(書きの途中で接続が落ちる代役)
;;; counts = DB へ流れた往復(往復ごとに、その往復で流した文の綴りの tuple — 流れた順・thread の間で共有)と、流れた CREATE の文の数。
;;; transaction の外の文 1 つと、既定の transaction(batched でない)の BEGIN・錠・文・合図・COMMIT はそれぞれ往復 1 回。batched の transaction
;;; は答え手が往復 1 回にまとめた物(BEGIN・錠・文・合図・COMMIT)が往復 1 回(#3605)。効果 1 回の往復の数と文の数を、前後の往復の列の差で
;;; 測る(effect-round-trips)。
;;; 書きの合図は同じ接続で流す(呼び鈴は鳴らさない — 待ちの検は、この代役の外側に postgres-sql-handler を置いて呼び鈴をそちらへ渡す)。
(require doeff-hy.macros [defhandler defk <- val var])
(import dataclasses [dataclass field])
(import threading)
(import doeff [EffectBase])
(import doeff_core_effects.sql_effects [SqlQuery SqlInsertRows SqlTransaction SqlNotify SqlParam SqlUnreachable])
(import doeff_core_effects.sql_transaction [TransactionFlush run-in-transaction run-in-batched-transaction])
(import doeff_core_effects.postgres_sql [PostgresConnections postgres-query postgres-insert postgres-insert-statement postgres-begin postgres-flush
                                         postgres-control LOCK-STATEMENT NOTICE-STATEMENT])


(defclass StatementCounts []
  "DB へ流れた往復の数え(thread の間で共有): creates = CREATE の文の数 / upserts = state_rows への INSERT の数 / flushes = 往復の列(流れた
   順 — 往復ごとにその往復で流した文の綴りの tuple。効果 1 回が流した往復を、前後の長さの差で切り出すため・#3561・#3605)。"
  (defn __init__ [self]  ; defk にできない: 検の資源の class の初期化
    (setv self.lock (threading.Lock) self.creates 0 self.upserts 0 self.flushes #()))
  (defn record [self #^ tuple statements]  ; defk にできない: 複数の thread の run から同期に数える
    "往復 1 回(流す文の綴りの tuple)を数え、その往復の state_rows への INSERT が何本目か(1 から数えた番号の tuple)を返す。"
    (with [self.lock]
      (setv self.flushes (+ self.flushes #(statements)))
      (setv self.creates (+ self.creates (sum (gfor s statements (.startswith (.lstrip s) "CREATE")))))
      (setv first self.upserts)
      (setv self.upserts (+ self.upserts (sum (gfor s statements (in "state_rows (ledger" s)))))
      (tuple (range (+ first 1) (+ self.upserts 1))))))


(defclass [(dataclass :frozen True)] QueryProbe []
  "代役の決め事(頭の註)。"
  (#^ StatementCounts counts)
  (setv #^ bool keep-lock True)
  (setv #^ (| threading.Barrier None) barrier None)
  (setv #^ (| int None) fail-at None))


(defk probed-query [probe leased request]
  {:pre [(: probe QueryProbe) (: leased "psycopg の接続") (: request SqlQuery)] :post [(: % "SqlRows | SqlFailed | SqlUnreachable")]
   :tags {:context "records" :role "foundation"}}
  "文 1 つ(往復 1 回 — transaction の外か、既定の transaction の中)を数え、決め事どおりに落とすか流して待ち合わせるため。"
  (val nth (.record probe.counts #(request.statement)))
  (when (and (is-not probe.fail-at None) (in probe.fail-at nth))
    (return (SqlUnreachable :reason "検の代役: 書きの途中で接続が落ちた")))
  (<- answer (postgres-query leased request))
  (when (and (is-not probe.barrier None) (.endswith (.rstrip request.statement) "FOR UPDATE"))
    (.wait probe.barrier))
  answer)


(defk probed-insert [probe leased request]
  {:pre [(: probe QueryProbe) (: leased "psycopg の接続") (: request SqlInsertRows)] :post [(: % "SqlRows | SqlFailed | SqlUnreachable")]
   :tags {:context "records" :role "foundation"}}
  "既定の transaction の中の SqlInsertRows(往復 1 回)を数えて流すため。"
  (<- text (postgres-insert-statement request.table request.columns))
  (.record probe.counts #(text))
  (<- answer (postgres-insert leased request))
  answer)


(defk probed-begin [probe leased lock-key]
  {:pre [(: probe QueryProbe) (: leased "psycopg の接続") (: lock-key (| str None))] :post [(: % "SqlFailed | SqlUnreachable | None")]
   :tags {:context "records" :role "foundation"}}
  "既定の transaction の始まり(BEGIN と、錠を取るなら錠の文 — 往復 2 回)を数えて流すため(keep-lock = False なら錠を取らず、錠の文も
   数えない)。"
  (val held (if probe.keep-lock lock-key None))
  (.record probe.counts #("BEGIN"))
  (when (is-not held None)
    (.record probe.counts #(LOCK-STATEMENT)))
  (<- began (postgres-begin leased held))
  began)


(defk probed-commit [probe leased]
  {:pre [(: probe QueryProbe) (: leased "psycopg の接続")] :post [(: % "SqlFailed | SqlUnreachable | None")]
   :tags {:context "records" :role "foundation"}}
  "既定の transaction の COMMIT(往復 1 回)を数えて流すため。"
  (.record probe.counts #("COMMIT"))
  (<- answer (postgres-control leased "COMMIT"))
  answer)


(defk probed-notice [probe leased database request]
  {:pre [(: probe QueryProbe) (: leased "psycopg の接続") (: database str) (: request SqlNotify)] :post [(: % "SqlRows | SqlFailed | SqlUnreachable")]
   :tags {:context "records" :role "foundation"}}
  "既定の transaction の中の書きの合図(SqlNotify — 往復 1 回)を数えて同じ接続で流すため。"
  (.record probe.counts #(NOTICE-STATEMENT))
  (<- answer (postgres-query leased (SqlQuery database NOTICE-STATEMENT #((SqlParam :name "channel" :value request.channel)))))
  answer)


(defk flush-texts [lock-key flush]
  {:pre [(: lock-key (| str None)) (: flush TransactionFlush)] :post [(: % tuple)]
   :tags {:context "records" :role "foundation"}}
  "transaction の往復 1 回で流れる文の綴りを、流れる順(BEGIN・錠・文・合図・COMMIT — postgres-flush と同じ順)に並べるため(数えの綴り)。"
  (var texts #())
  (when flush.opening
    (:= texts (+ texts #("BEGIN") (if (is lock-key None) #() #(LOCK-STATEMENT)))))
  (for [request flush.requests]
    (match request
      (SqlQuery :statement statement) (:= texts (+ texts #(statement)))
      (SqlInsertRows :table table :columns columns) (:= texts (+ texts #((! (postgres-insert-statement table columns)))))))
  (:= texts (+ texts (tuple (gfor _ flush.notices NOTICE-STATEMENT))))
  (when flush.closing
    (:= texts (+ texts #("COMMIT"))))
  texts)


(defk probed-flush [probe leased lock-key flush]
  {:pre [(: probe QueryProbe) (: leased "psycopg の接続") (: lock-key (| str None)) (: flush TransactionFlush)]
   :post [(: % "SqlRows の tuple | SqlFailed | SqlUnreachable")]
   :tags {:context "records" :role "foundation"}}
  "batched の transaction の往復 1 回を数え、本物の答え手と同じ postgres-flush で 1 つの pipeline として流すため(keep-lock = False なら錠を取らない・
   fail-at の INSERT を含む往復は流さずに SqlUnreachable・barrier は FOR UPDATE の読みを含む往復の後で待ち合わせる)。"
  (val held (if probe.keep-lock lock-key None))
  (<- texts (flush-texts held flush))
  (val nth (.record probe.counts texts))
  (when (and (is-not probe.fail-at None) (in probe.fail-at nth))
    (return (SqlUnreachable :reason "検の代役: 書きの途中で接続が落ちた")))
  (<- answer (postgres-flush leased held flush))
  (when (and (is-not probe.barrier None) (any (gfor text texts (.endswith (.rstrip text) "FOR UPDATE"))))
    (.wait probe.barrier))
  answer)


(defk probed-rollback [probe leased]
  {:pre [(: probe QueryProbe) (: leased "psycopg の接続")] :post [(: % "SqlFailed | SqlUnreachable | None")]
   :tags {:context "records" :role "foundation"}}
  "transaction の ROLLBACK(往復 1 回)を数えて流すため。"
  (.record probe.counts #("ROLLBACK"))
  (<- answer (postgres-control leased "ROLLBACK"))
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
  (SqlTransaction [database program lock-key batched] :when (= database answered)
    (val leased (.acquire connections database))
    (try
      (<- answer (if batched
                     (run-in-batched-transaction database program
                                                 (fn [flush] (probed-flush probe leased lock-key flush))
                                                 (fn [] (probed-rollback probe leased))
                                                 :accepts-notices True)
                     (run-in-transaction database program
                                         (fn [request] (probed-query probe leased request))
                                         (fn [request] (probed-insert probe leased request))
                                         (fn [] (probed-begin probe leased lock-key))
                                         (fn [] (probed-commit probe leased))
                                         (fn [] (probed-rollback probe leased))
                                         :execute-notify (fn [request] (probed-notice probe leased database request)))))
      (finally (.release connections database leased)))
    (resume answer)))


(defk effect-round-trips [counts ask]
  {:pre [(: counts StatementCounts) (: ask EffectBase)] :post [(: % tuple)]
   :tags {:context "records" :role "program"}}
  "効果 ask を 1 回撃ち、その間に DB へ流れた往復を切り出すため。答え = #(答え 往復の tuple)— 往復ごとに流した文の綴りの tuple(文の数は
   往復の文を並べた数)。"
  (val mark (len counts.flushes))
  (<- answer ask)
  #(answer (tuple (cut counts.flushes mark None))))
