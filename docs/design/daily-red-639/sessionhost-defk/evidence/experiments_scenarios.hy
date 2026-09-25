;;; 事前の主張の S1・S2・S4 を設計者が実際に試す最小の実験(共有の source は変えない)。
;;; store の本体(M2)と transaction の型(M1)は作業樹の module をそのまま import する。
;;; S1 の「開き方の変更」は M1 の写しを実験の中に置いて試す(変えるのは M1 の 1 定義だけ、を確かめる)。
;;; 使い方: uv run hy .scratch/design-check/<依頼>/experiments_scenarios.hy
;;; 出力は「観測: …」の行だけを見る。

(require doeff-hy.macros [defk <- defhandler])
(import doeff [run Program UnhandledEffect])
(import os sqlite3 tempfile shutil sys)
(import pathlib [Path])
(import datetime [datetime timedelta timezone])
(import types [NoneType])
(import doeff_agents.sessionhost.store [open-conn db-migrate db-read-lease db-upsert-lease
                                        db-immediate-transaction
                                        db-acquire-lease-body db-heartbeat-lease-body
                                        db-release-lease-body])
(import doeff_agents.sessionhost.policy [parse-iso])
(import doeff_agents.sessionhost.effects [ClockNow clock-now])

(defn fresh-db []
  (setv d (tempfile.mkdtemp))
  (setv conn (open-conn (os.path.join d "agentd.sqlite")))
  (db-migrate conn)
  #(d conn))

(defn owner-of [conn]
  (setv row (db-read-lease conn))
  (if (is row None) None (get row "owner_pid")))

;; ---------------------------------------------------------------- S1(storage)
;; M1 の写し: BEGIN EXCLUSIVE で開き、本体か COMMIT が SQLITE_BUSY で落ちたら同じ Program を
;; もう 1 度束ねてやり直す。M2(lease の本体)と M3(入口)は 1 文字も変えずにこれへ渡す。
(defk exclusive-transaction-with-retry [conn body]
  {:pre [(: conn sqlite3.Connection) (: body Program)]
   :post [(: % "body の値")]}
  (setv attempt 0)
  (while True
    (setv attempt (+ attempt 1))
    (.execute conn "BEGIN EXCLUSIVE")
    (try
      (<- value body)
      (.execute conn "COMMIT")
      (return value)
      (except [e sqlite3.OperationalError]
        (when conn.in-transaction
          (.execute conn "ROLLBACK"))
        (when (not (and (< attempt 2) (in "locked" (str e))))
          (raise))))))

(defk flaky-body [conn runs]
  {:pre [(: conn sqlite3.Connection) (: runs list)]
   :post [(: % NoneType)]}
  "1 回目は SQLITE_BUSY 相当で落ち、2 回目は書く。束ねるたびに最初から走るかを数える。"
  (.append runs 1)
  (.execute conn "INSERT INTO agent_daemon_lease (lease_name, owner_pid, heartbeat_at, expires_at) VALUES ('probe', 7, 'x', 'y')")
  (when (= (len runs) 1)
    (raise (sqlite3.OperationalError "database is locked")))
  None)

(defn s1-positive []
  (setv #(d conn) (fresh-db))
  (try
    ;; 変えない M2 の本体を、開き方だけ変えた M1 の写しへ渡す。
    (run (exclusive-transaction-with-retry conn (db-acquire-lease-body conn 111)))
    (setv after-acquire (owner-of conn))
    (run (exclusive-transaction-with-retry conn (db-heartbeat-lease-body conn 111)))
    (setv released (run (exclusive-transaction-with-retry conn (db-release-lease-body conn 111))))
    (print f"観測: S1 正常例 acquire 後の owner = {after-acquire} / release の戻り = {released} / "
           f"release 後の lease = {(db-read-lease conn)} / in_transaction = {conn.in-transaction}")
    (setv runs [])
    (run (exclusive-transaction-with-retry conn (flaky-body conn runs)))
    (setv probe (.fetchone (.execute conn "SELECT COUNT(*) FROM agent_daemon_lease WHERE lease_name = 'probe'")))
    (print f"観測: S1 正常例 やり直し: 本体が走った回数 = {(len runs)} / probe の行 = {(get probe 0)}"
           "(1 回目の書きは巻き戻り、同じ Program の 2 度目の束ねで本体が最初から走った)")
    (finally
      (.close conn)
      (shutil.rmtree d))))

(defn s1-negative []
  ;; 開き方の変更を M1 の外(lease の本体の中)に書くと、R15 の走査の検が名指す。
  (setv here (Path (os.path.abspath __file__)))
  (setv tests (/ (. here parents [3]) "packages" "doeff-agents" "tests"))
  (sys.path.insert 0 (str tests))
  (import test_sessionhost_transaction_owner [transaction-control-outside-owners])
  (setv root (Path (tempfile.mkdtemp)))
  (try
    (.write-text (/ root "store.hy")
                 (+ "(defk db-immediate-transaction [conn body]\n"
                    "  (.execute conn \"BEGIN IMMEDIATE\"))\n"
                    "(defk db-acquire-lease-body [conn owner-pid]\n"
                    "  (.execute conn \"BEGIN EXCLUSIVE\")\n"
                    "  (db-upsert-lease conn owner-pid))\n")
                 :encoding "utf-8")
    (print f"観測: S1 反例 本体の中に BEGIN EXCLUSIVE を書いた写しの走査 = "
           f"{(transaction-control-outside-owners root)}")
    (finally
      (shutil.rmtree root))))

;; ---------------------------------------------------------------- S2(effects)
;; lease の本体が effect を出す(時刻を ClockNow で読む)。handler は M3 の run の位置に差す。
(defk acquire-with-clock-body [conn owner-pid]
  {:pre [(: conn sqlite3.Connection) (: owner-pid int)]
   :post [(: % NoneType)]}
  "db-acquire-lease-body と同じ判定を、時刻だけ ClockNow effect から読む形(S2・S4 の M2 の変更)。"
  (<- now datetime (clock-now))
  (setv existing (db-read-lease conn))
  (when (is-not existing None)
    (setv expires (parse-iso (get existing "expires_at")))
    (when (and (is-not expires None) (> expires now))
      (setv owner (get existing "owner_pid"))
      (raise (RuntimeError f"doeff-agentd lease is active: owner_pid={owner}"))))
  (db-upsert-lease conn owner-pid))

(defhandler fixed-clock [now]
  (ClockNow [] (resume now)))

(defn s2-positive []
  (setv #(d conn) (fresh-db))
  (try
    ;; M3 の入口が変わるのは「run の位置に handler を差す」ことだけ。M1 はそのまま。
    (run ((fixed-clock (datetime.now timezone.utc))
          (db-immediate-transaction conn (acquire-with-clock-body conn 222))))
    (print f"観測: S2 正常例 本体の ClockNow は M1 の <- を通って外の handler へ届いた — owner = {(owner-of conn)} / "
           f"in_transaction = {conn.in-transaction}")
    (finally
      (.close conn)
      (shutil.rmtree d))))

(defn s2-negative []
  (setv #(d conn) (fresh-db))
  (try
    (setv outcome None)
    (try
      (run (db-immediate-transaction conn (acquire-with-clock-body conn 222)))
      (except [e BaseException]
        (setv outcome (. (type e) __name__))))
    (print f"観測: S2 反例 handler が無い: 出た例外 = {outcome} / in_transaction = {conn.in-transaction} / "
           f"lease = {(db-read-lease conn)}(巻き戻って何も残らない)")
    (finally
      (.close conn)
      (shutil.rmtree d))))

;; ---------------------------------------------------------------- S4(simulation)
;; 失効の判定を固定の時刻で決定的に再現する。既存の lease(owner 111)の expires_at を基準に
;; 1 秒後(失効)と 1 秒前(未失効)の時刻を handler で差す。3 回撃って同じ答えかも見る。
(defn s4-once [offset-seconds]
  (setv #(d conn) (fresh-db))
  (try
    (run (db-immediate-transaction conn (db-acquire-lease-body conn 111)))
    (setv expires (parse-iso (get (db-read-lease conn) "expires_at")))
    (setv now (+ expires (timedelta :seconds offset-seconds)))
    (setv outcome "returned")
    (try
      (run ((fixed-clock now) (db-immediate-transaction conn (acquire-with-clock-body conn 222))))
      (except [e Exception]
        (setv outcome f"{(. (type e) __name__)}: {e}")))
    #(outcome (owner-of conn) conn.in-transaction)
    (finally
      (.close conn)
      (shutil.rmtree d))))

(defn s4 []
  (setv expired (lfor _ (range 3) (s4-once 1)))
  (setv active (lfor _ (range 3) (s4-once -1)))
  (print f"観測: S4 正常例 失効の 1 秒後(3 回) = {expired}")
  (print f"観測: S4 反例 失効の 1 秒前(3 回) = {active}"))

(s1-positive)
(s1-negative)
(s2-positive)
(s2-negative)
(s4)
