;;; PostgreSQL の置き場の接続の貸し出し — HTTP の口が要求ごとに PgRecordsHost を 1 つ借りて返す(composition root の部品)。
;;;
;;; 1 本の psycopg の接続を thread の間で分けると、同時の要求の transaction が 1 つの接続の上で入れ子(savepoint)になり、
;;; 書きの lock と版の判断が混ざる。だから要求 1 つ = 接続 1 本。接続は size 本まで開き、返された物を使い回す。
;;; 切れた接続(broken / closed)は返された時に捨て、次の貸し出しで開き直す。
(import collections.abc [Callable Iterator])
(import contextlib [contextmanager])
(import queue [Queue Empty])
(import threading)
(import doeff_records.values [RecordsSchema])
(import doeff_records.pg [PgRecordsHost pg-records-handler])
(import doeff_records.pg_sql [DEFAULT-PREFIX])

(setv DEFAULT-POOL-SIZE 8)


(defclass PgHostPool []
  "接続の貸し出し: connect = () → 自動 commit の psycopg の接続 / schema・prefix = 置き場 / unreachable-errors = 接続の失敗の型 /
   size = 同時に貸す上限(超えた要求は待つ)。"
  (defn #^ None __init__ [self #^ Callable connect #^ RecordsSchema schema * #^ tuple unreachable-errors
                          #^ str [prefix DEFAULT-PREFIX] #^ int [size DEFAULT-POOL-SIZE]]
    (setv self.connect connect
          self.schema schema
          self.prefix prefix
          self.unreachable-errors unreachable-errors
          self.idle (Queue)
          self.permits (threading.BoundedSemaphore size)
          self.lock (threading.Lock)
          self.opened []))

  (defn #^ PgRecordsHost open-host [self]
    "接続を 1 本開いて置き場の host にする(表の用意は何度でも同じ)。"
    (setv host (PgRecordsHost (self.connect) self.schema :unreachable-errors self.unreachable-errors :prefix self.prefix))
    (with [self.lock] (.append self.opened host))
    host)

  (defn [contextmanager] #^ (get Iterator PgRecordsHost) lease [self]
    "host を 1 つ借りる(with の中だけ使う)。返す時に切れていれば捨てる。"
    (.acquire self.permits)
    (try
      (setv host (try (.get-nowait self.idle) (except [Empty] (self.open-host))))
      (try
        (yield host)
        (finally
          (if (or host.connection.closed host.connection.broken)
              (self.discard host)
              (.put self.idle host))))
      (finally (.release self.permits))))

  (defn #^ None discard [self #^ PgRecordsHost host]
    "切れた接続を閉じて貸し出しの列から外す。"
    (with [self.lock] (.remove self.opened host))
    (.close host.connection))

  (defn [contextmanager] #^ (get Iterator Callable) lease-handlers [self]
    "HTTP の口の lease-handlers: host を 1 つ借り、書き手の名 → その host の PostgreSQL の handler の関数を渡す。"
    (with [host (self.lease)]
      (yield (fn [writer] (pg-records-handler host writer)))))

  (defn #^ None close [self]
    "開いた接続を全部閉じる(口を止めた後に呼ぶ)。"
    (with [self.lock]
      (for [host self.opened] (.close host.connection))
      (setv self.opened []))))
