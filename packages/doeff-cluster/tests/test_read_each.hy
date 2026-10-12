;;; 起動の読み直しが保存の行の列を読む関数 read-each の契約の検(#2812)。
;;;
;;; read-each は doeff-traverse の sequence で行を順に読む(tuple の継ぎ足しは行の数の 2 乗で伸び、3 万行で 2.8 秒かかった)。確かめる事は 3 つ。
;;; - 答え: 鍵と並びは渡した順のまま・None の答え(旧い形の温める表の行 — 呼び手が捨てる)も保つ。
;;; - 止まり方: 読めない行が在れば最初の 1 行で止まり、以降の行は読まない・黙って落とさない(sequence でなく for/do の sequential で
;;;   読むと、失敗した行は記録されて残りを読み続け、valid_values だけを読む呼び手には行が無かったように見える)。
;;; - 線形: 行の数が 10 倍になっても費用は約 10 倍(2 乗なら約 100 倍)。
(require doeff-hy.macros [deftest defk <- val var])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import shutil)
(import time)
(import pathlib [Path])
(import pytest)
(import doeff [with-handlers])
(import doeff_core_effects.handlers [slog-handler])
(import doeff_core_effects.os_file [os-file-handler])
(import doeff_cluster.foundation.wal_store [WalStore])
(import doeff_cluster.coordinator.protocol.store [durable-load])
(import doeff_cluster.coordinator.protocol.durable_kv [state-from-kv])
(import doeff_cluster.coordinator.protocol.state_json [read-each])

(val FIXTURE (/ (. (Path __file__) (resolve) parent) "fixtures" "coordinator_state_ec727f20" "wal"))
(val NOW 1767229200000)
;; 読んだ行の鍵を記録する(止まった後に読んだ行が無い事を確かめる)。
(val READ-LOG [])


(defk tenfold [value]
  {:pre [(: value int)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "行の読み(読む物は整数・答えは 10 倍)。"
  (* value 10))


(defk noting [value]
  {:pre [(: value int)] :post [(: % (| int None))] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "読んだ行を READ-LOG に残し、値 2 は読めない行(ValueError)、値 3 は None(旧い形の行)にする行の読み。"
  (.append READ-LOG value)
  (when (= value 2)
    (raise (ValueError "読めない行")))
  (when (= value 3)
    (return None))
  value)


(deftest test-read-each-keeps-the-keys-and-the-order-it-was-given
  (<- rows tuple (read-each tenfold #(#("b" 2) #("a" 1) #("c" 3) #("a" 4))))
  (assert (= rows #(#("b" 20) #("a" 10) #("c" 30) #("a" 40))) rows))


(deftest test-read-each-of-no-rows-is-empty
  (<- rows tuple (read-each tenfold #()))
  (assert (= rows #()) rows))


(deftest test-a-none-answer-is-kept-for-the-caller-to-drop
  ;; warm-entry-from-json は旧い形の行に None を返し、呼び手(state-from-kv・state-from-json)が捨てる。
  (READ-LOG.clear)
  (<- rows tuple (read-each noting #(#("x" 1) #("y" 3) #("z" 4))))
  (assert (= rows #(#("x" 1) #("y" None) #("z" 4))) rows))


(deftest test-the-first-unreadable-row-stops-the-read-and-no-later-row-is-read
  (READ-LOG.clear)
  (with [(pytest.raises ValueError :match "読めない行")]
    (<- rows tuple (read-each noting #(#("x" 1) #("y" 2) #("z" 4))))
    (assert False rows))
  ;; 3 つ目の行(値 4)は読まれない
  (assert (= READ-LOG [1 2]) READ-LOG))


(deftest test-a-stored-state-with-one-unreadable-task-row-does-not-start
  ;; 起動の読み直しは、読めない task の行が 1 つでも在れば止まる。黙って落として task の足りない状態で始めない。
  (import tempfile)
  (val directory (tempfile.mkdtemp))
  (shutil.copytree FIXTURE (/ (Path directory) "wal"))
  (<- stored dict (with-handlers [slog-handler os-file-handler] (durable-load (WalStore (str (/ (Path directory) "wal"))))))
  (val key (next (gfor k stored :if (.startswith k "task/") k)))
  ;; 直さなければ読める(task 1 つ)
  (<- good (state-from-kv stored NOW))
  (assert (= (len good.tasks) 1) (sorted good.tasks))
  ;; 欄 lease_ms を欠いた行は読めない
  (val broken (dict stored))
  (setv (get broken key) (dfor #(k v) (.items (get stored key)) :if (!= k "lease_ms") k v))
  (with [(pytest.raises ValueError :match "lease_ms")]
    (<- state (state-from-kv broken NOW))
    (assert False state)))


(defk timed-read [count]
  {:pre [(: count int)] :post [(: % float)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "count 行(鍵 = 値 = 連番)を read-each で 1 回読むのにかかった CPU 時間。"
  (val rows (tuple (gfor i (range count) #(i i))))
  (val started (time.process_time))
  (<- read tuple (read-each tenfold rows))
  (assert (= (len read) count))
  (- (time.process_time) started))


(defk fastest-read [count]
  {:pre [(: count int)] :post [(: % float)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "count 行を 3 回読んだ CPU 時間の最小(負荷の揺れを小さくする)。"
  (<- first float (timed-read count))
  (<- second float (timed-read count))
  (<- third float (timed-read count))
  (min first second third))


(deftest test-the-cost-grows-in-proportion-to-the-rows
  ;; 3 千行 → 3 万行(10 倍)で、費用は約 10 倍。tuple の継ぎ足しなら約 100 倍(3 千行 35 ms → 3 万行 3.5 秒)。測りは負荷で揺れるので、
  ;; 線形と 2 乗の間の 30 倍を境にする。
  (<- (timed-read 300))
  (<- small float (fastest-read 3000))
  (<- large float (fastest-read 30000))
  (assert (< large (* small 30)) #(small large)))
