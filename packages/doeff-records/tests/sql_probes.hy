;;; 検の代役の SQL の答え手 probe-sql-handler — 本物の PostgreSQL に流しつつ、driver の手前で文を数え・止め・落とす(旧い版の検の
;;; 代役の host LocklessHost・FailingMidBatchHost・CountingConnection と同じ段の差し替え)。postgres-sql-handler と同じ部品
;;; (postgres_sql.hy の文の手順と sql_transaction.hy の run-in-transaction)で答え、違うのは QueryProbe が決める 3 点だけ:
;;;   keep-lock = False なら SqlTransaction の lock-key の錠を取らない(錠の検の反例)
;;;   barrier   = 行の錠(FOR UPDATE)の読みの後で待ち合わせる(2 本の書きが両方「行が無い」を読んでから書く反例)
;;;   fail-at   = state_rows への n 本目の INSERT を流さずに SqlUnreachable を答える(書きの途中で接続が落ちる代役)
;;; counts = 流れた CREATE の文の数と、DB へ流れた文の綴りの全部(thread の間で共有)。transaction の区切り(BEGIN・錠の文・COMMIT・ROLLBACK)と
;;; 書きの合図(SqlNotify の文)も 1 文に数える — どれも DB への往復 1 回なので、効果 1 回の往復の数を texts の長さの差で測れる(#3605 の D)。
;;; 書きの合図は同じ接続で流す(呼び鈴は鳴らさない — 待ちの検は、この代役の外側に postgres-sql-handler を置いて呼び鈴をそちらへ渡す)。
(require doeff-hy.macros [defhandler defk <- val var])
(import dataclasses [dataclass field])
(import threading)
(import doeff [EffectBase])
(import doeff_core_effects.sql_effects [SqlQuery SqlParam SqlTransaction SqlNotify SqlUnreachable])
(import doeff_core_effects.sql_transaction [run-in-transaction])
(import doeff_core_effects.postgres_sql [PostgresConnections postgres-query postgres-insert postgres-begin postgres-control NOTICE-STATEMENT])

;; 数えの綴り: postgres-begin が BEGIN の後に流す錠の文(lock-key が在る時だけ)。
(val LOCK-STATEMENT "SELECT pg_advisory_xact_lock(hashtext(:key))")


(defclass StatementCounts []
  "流れた文の数え(thread の間で共有): creates = CREATE の文の数 / upserts = state_rows への INSERT の数 / texts = 流れた文の綴り(流れた順の
   tuple — 効果 1 回が流した文を、前後の長さの差で切り出すため・#3561)。"
  (defn __init__ [self]  ; defk にできない: 検の資源の class の初期化
    (setv self.lock (threading.Lock) self.creates 0 self.upserts 0 self.texts #()))
  (defn record [self #^ str statement]  ; defk にできない: 複数の thread の run から同期に数える
    "文 1 つを数え、それが state_rows への何本目の INSERT か(INSERT でなければ 0)を返す。"
    (with [self.lock]
      (setv self.texts (+ self.texts #(statement)))
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


(defk probed-begin [probe leased lock-key]
  {:pre [(: probe QueryProbe) (: leased "psycopg の接続") (: lock-key (| str None))] :post [(: % "SqlFailed | SqlUnreachable | None")]
   :tags {:context "records" :role "foundation"}}
  "transaction の始まり(BEGIN と、錠を取るなら錠の文 — 往復 2 回)を数えて流すため(keep-lock = False なら錠を取らず、錠の文も数えない)。"
  (val held (if probe.keep-lock lock-key None))
  (.record probe.counts "BEGIN")
  (when (is-not held None)
    (.record probe.counts LOCK-STATEMENT))
  (<- began (postgres-begin leased held))
  began)


(defk probed-control [probe leased statement]
  {:pre [(: probe QueryProbe) (: leased "psycopg の接続") (: statement str)] :post [(: % "SqlFailed | SqlUnreachable | None")]
   :tags {:context "records" :role "foundation"}}
  "transaction の区切りの文(COMMIT・ROLLBACK)を数えて流すため。"
  (.record probe.counts statement)
  (<- answer (postgres-control leased statement))
  answer)


(defk probed-notice [probe leased database request]
  {:pre [(: probe QueryProbe) (: leased "psycopg の接続") (: database str) (: request SqlNotify)] :post [(: % "SqlRows | SqlFailed | SqlUnreachable")]
   :tags {:context "records" :role "foundation"}}
  "書きの合図(SqlNotify)の文を数えて同じ接続で流すため。"
  (.record probe.counts NOTICE-STATEMENT)
  (<- answer (postgres-query leased (SqlQuery database NOTICE-STATEMENT #((SqlParam :name "channel" :value request.channel)))))
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
                                     (fn [] (probed-begin probe leased lock-key))
                                     (fn [] (probed-control probe leased "COMMIT"))
                                     (fn [] (probed-control probe leased "ROLLBACK"))
                                     :execute-notify (fn [request] (probed-notice probe leased database request))))
      (finally (.release connections database leased)))
    (resume answer)))


(defk effect-statements [counts ask]
  {:pre [(: counts StatementCounts) (: ask EffectBase)] :post [(: % tuple)]
   :tags {:context "records" :role "program"}}
  "効果 ask を 1 回撃ち、その間に DB へ流れた文(往復)を切り出すため。答え = #(答え 流れた文の綴りの tuple)。"
  (val mark (len counts.texts))
  (<- answer ask)
  #(answer (tuple (cut counts.texts mark None))))
