;;; 盲検 A・B の反例を設計者が再現する検体(共有の source は変えない・作業樹の module を import するだけ)。
;;; 使い方: uv run hy .scratch/design-check/<依頼>/repro_counterexamples.hy
;;; 出力は「観測: …」の行だけを見る。

(require doeff-hy.macros [defk <-])
(import doeff [run])
(import os sqlite3 tempfile shutil)
(import types [NoneType])
(import doeff_agents.sessionhost.store [StoreActor open-conn db-migrate db-read-lease
                                        db-immediate-transaction db-acquire-lease-body])
(import doeff_agents.sessionhost.cache_host_model [HostCacheRecord])
(import doeff_agents.sessionhost.cache_host_store [cache-receipt-put cache-receipt-get])

(defk fence-body [conn owner-pid record fail]
  {:pre [(: conn sqlite3.Connection) (: owner-pid int) (: record HostCacheRecord) (: fail bool)]
   :post [(: % NoneType)]}
  "盲検 A の反例の形: lease の持ち主を確かめてから、既存の helper で専用操作の記録を書く。"
  (setv current (db-read-lease conn))
  (when (or (is current None) (!= (get current "owner_pid") owner-pid))
    (raise (RuntimeError "not the lease holder")))
  (cache-receipt-put conn record)
  (when fail
    (raise (RuntimeError "body failed after the helper")))
  None)

(defn fresh-db []
  (setv d (tempfile.mkdtemp))
  (setv conn (open-conn (os.path.join d "agentd.sqlite")))
  (db-migrate conn)
  #(d conn))

(defn repro-a [fail]
  (setv #(d conn) (fresh-db))
  (try
    (run (db-immediate-transaction conn (db-acquire-lease-body conn 111)))
    (setv record (HostCacheRecord "op-f" "session" 10000 "process" "events"))
    (setv outcome "returned")
    (try
      (run (db-immediate-transaction conn (fence-body conn 111 record fail)))
      (except [e Exception]
        (setv outcome f"{(. (type e) __name__)}: {e}")))
    (setv kept (is-not (cache-receipt-get conn "op-f") None))
    (print f"観測: A(fail={fail}) 結果 = {outcome} / in_transaction = {conn.in-transaction} / "
           f"記録が残ったか = {kept}")
    (finally
      (.close conn)
      (shutil.rmtree d))))

(defn repro-b []
  (setv d (tempfile.mkdtemp))
  (setv actor (StoreActor (os.path.join d "agentd.sqlite")))
  (try
    (setv outcome None)
    (try
      (setv value (.submit actor (fn [conn] (db-immediate-transaction conn (db-acquire-lease-body conn 111)))))
      (setv outcome f"op の戻り = {(. (type value) __name__)}")
      (setv lease-before-caller-run (.submit actor db-read-lease))
      (print f"観測: B actor の op の直後の lease = {lease-before-caller-run}(actor の thread では何も書かれていない)")
      ;; 呼び手の thread で Program を実行する(B の反例の actor-handover-lease の `<-` と同じ位置)。
      (run value)
      (print f"観測: B 呼び手の thread で実行した後の lease = {(.submit actor db-read-lease)} / "
             f"書き込みの健康 = {actor.write-health.consecutive-failures}")
      (except [e Exception]
        (setv outcome f"{(. (type e) __name__)}: {e}")))
    (print f"観測: B 結果 = {outcome}")
    (finally
      (.close actor)
      (shutil.rmtree d))))

(repro-a False)
(repro-a True)
(repro-b)
