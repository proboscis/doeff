;;; coordinator 起動 memory の根治:
;;; - clocks-shifted は終わった切り離し task(held-row?)の lease-until-ms を動かさず、起動と止まりの直後の書き換え嵐を起こさない
;;; - load-state は変更のあった鍵だけを書き、読み直し後に WalStore の重複 KV 表を保持しない
;;; - snapshot.json / wal.jsonl の読み書きと compaction(durable-checkpoint)は chunk 単位の streaming で行い、
;;;   状態全体の巨大 JSON 文字列や二重コピーを作らず memory の峰が状態の量に比例して膨らまない
;;; - boot.sh の背後片付けは uv run を噛ませず venv の python -I を double-fork で直接起動する
(require doeff-hy.macros [deftest defk <- val var])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import gc)
(import os)
(import json)
(import tracemalloc)
(import pathlib [Path])
(import pytest)
(import doeff_hy._table [TableWrite table-of])
(import doeff_cluster [ClusterState WalStore WalCorrupted load-state])
(import doeff_cluster._coordinator.intent.cluster_model [TaskRecord WorkerInfo])
(import doeff_cluster._coordinator.intent.task_table [task-table-of])
(import doeff_cluster._coordinator.core.api_policy [clocks-shifted resume-after-downtime])
(import doeff_cluster._coordinator.protocol.durable_kv [full-kv])
(import doeff_cluster._coordinator.protocol.store [durable-load durable-persist durable-checkpoint])
(import doeff_cluster._coordinator.protocol.wal_format [SnapshotRead read-snapshot sealed])
(import tests.wal_rig [on-wal-files])

(val NOW 1795000000000)
(val GAP 60000)
(val BOOT-SH (/ (. (.resolve (Path __file__)) parent parent) "deploy" "boot.sh"))


(defk make-ended-detached [index [result-bytes 4000]]
  {:pre [(: index int) (: result-bytes int)] :post [(: % TaskRecord)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "結果 blob を持つ終わった切り離し task の行 1 本。"
  (TaskRecord (.format "t-done-{}" index) "spot-job" None "0000000000000000000000000000000000000000"
              #() #("linux") 60000 (+ NOW 60000) (- NOW 7200000)
              :phase "finished" :worker "w1" :result (* "R" result-bytes)
              :detail (.format "完了した task {} の詳細" index)
              :started-ms (- NOW 7100000) :finished-ms (- NOW 7000000)
              :detached True :key (.format "k-done-{}" index) :retain-ms 86400000
              :reported True :runtime-env {"repo" "https://example.com/r.git" "note" "日本語の宣言"}
              :shape "shape-a" :peak-mib 256))


(defk make-live-task [task-id detached]
  {:pre [(: task-id str) (: detached bool)] :post [(: % TaskRecord)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "lease が生きている task の行 1 本。"
  (TaskRecord task-id "live-job" None "0000000000000000000000000000000000000000"
              #() #("linux") 60000 (+ NOW 30000) (- NOW 10000)
              :phase (if detached "assigned" "finished") :worker "w1"
              :detached detached :key (if detached (+ "k-" task-id) None) :retain-ms (if detached 86400000 0)))


(deftest test-clocks-shifted-skips-ended-detached-tasks
  ;; 終わった切り離し task(held-row?)は lease-until-ms を使わないので clocks-shifted で動かさない。
  ;; 生きている task と切り離していない task だけは lease-until-ms を gap 分ずらす。
  (var writes #())
  (for [i (range 200)]
    (<- t TaskRecord (make-ended-detached i :result-bytes 200))
    (:= writes (+ writes #((TableWrite t.id t)))))
  (<- live-detached TaskRecord (make-live-task "t-live" True))
  (<- non-detached TaskRecord (make-live-task "t-attached" False))
  (:= writes (+ writes #((TableWrite live-detached.id live-detached)
                         (TableWrite non-detached.id non-detached))))
  (val before (ClusterState :tasks (task-table-of writes)
                            :workers (table-of #((TableWrite "w1" (WorkerInfo "w1" #("linux") (- NOW 1000) :seen-mark (- NOW 1000)))))
                            :alive-ms (- NOW GAP)))
  (<- shifted ClusterState (clocks-shifted before NOW GAP))
  (assert (= (. (.row shifted.tasks "t-live") lease-until-ms) (+ live-detached.lease-until-ms GAP)))
  (assert (= (. (.row shifted.tasks "t-attached") lease-until-ms) (+ non-detached.lease-until-ms GAP)))
  (for [i (range 200)]
    (val tid (.format "t-done-{}" i))
    (assert (is (.row shifted.tasks tid) (.row before.tasks tid))
            "終わった切り離し task は clocks-shifted で書き換えない")))


(deftest test-load-state-writes-only-live-keys-and-releases-store-kv [#^ Path tmp-path]
  ;; 終わった切り離し task が 200 本(計 ~1.2 MiB)あっても、起動の load-state は終わった task を WAL へ書き直さず、
  ;; max-log-bytes より小さい追記だけで済み、読み込み後は WalStore に重複 KV 表を残さない。
  (var writes #())
  (for [i (range 200)]
    (<- t TaskRecord (make-ended-detached i :result-bytes 5000))
    (:= writes (+ writes #((TableWrite t.id t)))))
  (<- live TaskRecord (make-live-task "t-live" True))
  (:= writes (+ writes #((TableWrite live.id live))))
  (val seed (ClusterState :tasks (task-table-of writes)
                          :workers (table-of #((TableWrite "w1" (WorkerInfo "w1" #("linux") (- NOW 1000) :seen-mark (- NOW 1000)))))
                          :alive-ms (- NOW GAP) :revision 42 :next-task 300))
  (val wal-dir (str (/ tmp-path "wal")))
  (val writer (WalStore wal-dir :max-log-bytes 200000 :sync False))
  ;; 置き場の dir は読み直し(wal-check-place)が作る。
  (<- (on-wal-files (durable-load writer)))
  (<- kv dict (full-kv seed))
  (.replace-table writer kv)
  (<- (on-wal-files (durable-checkpoint writer)))

  (val store (WalStore wal-dir :max-log-bytes 200000 :sync False))
  (<- loaded ClusterState (on-wal-files (load-state (str (/ tmp-path "absent.json")) store NOW)))
  (assert (= (.size loaded.tasks) 201))
  ;; 起動で WAL へ書いたのは counter・worker・生きている task だけ(終わった task は 1 本も書かない)。
  (val log-bytes (.read-bytes (/ tmp-path "wal" "wal.jsonl")))
  (assert (< (len log-bytes) 10000) (.format "WAL write during startup too large: {} bytes" (len log-bytes)))
  (val lines (.splitlines log-bytes))
  (assert (= (len lines) 1))
  (val delta (get (json.loads (get lines 0)) "delta"))
  (assert (in "counter" delta))
  (assert (in "worker/w1" delta))
  (assert (in "task/t-live" delta))
  (assert (not (any (gfor k delta (.startswith k "task/t-done-")))) (sorted delta))
  ;; load-state 完了後、WalStore は重複する全量 KV dict を memory に持ち続けない。
  (assert (= (len (.table store)) 0) "WalStore must not retain duplicate full KV table after load-state")
  ;; 同じ runtime_env を持つ終わった task は dict を共有する。
  (assert (is (. (.row loaded.tasks "t-done-0") runtime-env) (. (.row loaded.tasks "t-done-1") runtime-env))))


(deftest test-streaming-compaction-roundtrips-inserts-updates-deletes-and-utf8 [#^ Path tmp-path]
  ;; WalStore が in-memory の KV 表を持たない状態でも、on-disk の 2-way merge で snapshot + WAL を
  ;; 整合した canonical JSON(crc32 つき)へまとめ直せる。日本語 UTF-8 が chunk 境界を跨ぐ大きさでも壊れない。
  (val wal-dir (str (/ tmp-path "wal")))
  (val store (WalStore wal-dir :max-log-bytes 10000000 :sync False))
  (<- (on-wal-files (durable-load store)))
  ;; chunk 境界を跨ぐ日本語入りレコードを複数書く。
  (val big-jp (* "日本語の状態データ_" 2000))
  (<- (on-wal-files (durable-persist store {"a/keep" {"v" big-jp "n" 1}
                                            "b/update" {"v" "old" "n" 2}
                                            "c/delete" {"v" "gone" "n" 3}
                                            "z/tail" {"v" "end" "n" 4}})))
  (<- (on-wal-files (durable-checkpoint store)))
  ;; 新しい store で読み直したあと(in-memory KV を持たない状態)、追加・更新・削除をして再度 checkpoint する。
  (val store2 (WalStore wal-dir :max-log-bytes 10000000 :sync False))
  (<- loaded1 dict (on-wal-files (durable-load store2)))
  (assert (= (len loaded1) 4))
  (<- (on-wal-files (durable-persist store2 {"b/update" {"v" "new-日本語" "n" 20}
                                             "c/delete" None
                                             "m/insert" {"v" big-jp "n" 5}})))
  (<- (on-wal-files (durable-checkpoint store2)))
  ;; 出力 snapshot.json は canonical 形式 + 正しい CRC で読める。
  (val snap-bytes (.read-bytes (/ tmp-path "wal" "snapshot.json")))
  (<- snap SnapshotRead (read-snapshot snap-bytes "snapshot.json"))
  (assert (= snap.seq 2))
  (val expected {"a/keep" {"v" big-jp "n" 1}
                 "b/update" {"v" "new-日本語" "n" 20}
                 "m/insert" {"v" big-jp "n" 5}
                 "z/tail" {"v" "end" "n" 4}})
  (assert (= (dfor r snap.rows r.key r.value) expected))
  ;; 別の WalStore から streaming load しても完全一致する。
  (val store3 (WalStore wal-dir :max-log-bytes 10000000 :sync False))
  (<- loaded2 dict (on-wal-files (durable-load store3)))
  (assert (= loaded2 expected))
  (assert (= store3.seq 2))
  ;; 1 byte 破損させた snapshot.json は streaming load でも WalCorrupted で起動を断る。
  (.write-bytes (/ tmp-path "wal" "snapshot.json") (.replace snap-bytes b"\"n\":20" b"\"n\":21"))
  (with [raised (pytest.raises WalCorrupted)]
    (<- (on-wal-files (durable-load (WalStore wal-dir :sync False)))))
  (assert (in "checksum" (str raised.value))))


(defk checkpoint-and-load-peaks [wal-dir rows]
  {:pre [(: wal-dir str) (: rows int)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "rows 本の日本語入りの行を持つ snapshot.json を置き、durable-checkpoint と durable-load の一時 memory の峰(tracemalloc)を測る。
   返す物 = #(snapshot の byte 数 checkpoint の峰の増分 load の峰の超過)。"
  (val store (WalStore wal-dir :max-log-bytes 1000000000 :sync False))
  (<- (on-wal-files (durable-load store)))
  (val payload (* "R" 18000))
  (.replace-table store (dfor i (range rows)
                              (.format "task/t{:05d}" i)
                              {"id" (.format "t{:05d}" i) "result" payload "detail" (.format "完了 {}" i)}))
  (<- (on-wal-files (durable-checkpoint store)))
  (val snap-size (. (os.stat (os.path.join wal-dir "snapshot.json")) st-size))
  ;; checkpoint: disk の snapshot と WAL 1 行の 2-way merge。
  (val cp-store (WalStore wal-dir :max-log-bytes 1000000000 :sync False))
  (<- (on-wal-files (durable-load cp-store)))
  (<- (on-wal-files (durable-persist cp-store {"counter" {"revision" 99}})))
  (gc.collect)
  (tracemalloc.start)
  (val cp-before (get (tracemalloc.get-traced-memory) 0))
  (<- (on-wal-files (durable-checkpoint cp-store)))
  (val cp-peak (get (tracemalloc.get-traced-memory) 1))
  (tracemalloc.stop)
  ;; load: 読み終えた表を持つのは当然なので、峰が読み終えた後の量をどれだけ越えたかを測る。
  (gc.collect)
  (tracemalloc.start)
  (val loader (WalStore wal-dir :max-log-bytes 1000000000 :sync False))
  (<- loaded dict (on-wal-files (durable-load loader)))
  (val traced (tracemalloc.get-traced-memory))
  (tracemalloc.stop)
  (assert (= (len loaded) (+ rows 1)))
  #(snap-size (- cp-peak cp-before) (- (get traced 1) (get traced 0))))


(deftest test-checkpoint-and-load-peak-memory-do-not-grow-with-the-snapshot [#^ Path tmp-path]
  ;; snapshot を 4 倍にしても、durable-checkpoint と durable-load の一時 memory の峰は 4 倍にならない(chunk の大きさで決まる)。
  ;; 直す前は全量の JSON 文字列と byte 列を作り、峰が snapshot の 11〜14 倍で伸びた。
  (<- small tuple (checkpoint-and-load-peaks (str (/ tmp-path "small")) 300))
  (<- large tuple (checkpoint-and-load-peaks (str (/ tmp-path "large")) 1200))
  (val small-size (get small 0))
  (val small-cp (get small 1))
  (val small-load (get small 2))
  (val large-size (get large 0))
  (val large-cp (get large 1))
  (val large-load (get large 2))
  (assert (> large-size (* 3.5 small-size)) #(small-size large-size))
  (val growth-limit (* 2 1024 1024))
  (assert (< (- large-cp small-cp) growth-limit)
          (.format "checkpoint peak grew {:.2f} → {:.2f} MiB while the snapshot grew {:.2f} → {:.2f} MiB"
                   (/ small-cp 1048576.0) (/ large-cp 1048576.0) (/ small-size 1048576.0) (/ large-size 1048576.0)))
  (assert (< (- large-load small-load) growth-limit)
          (.format "load peak overshoot grew {:.2f} → {:.2f} MiB while the snapshot grew {:.2f} → {:.2f} MiB"
                   (/ small-load 1048576.0) (/ large-load 1048576.0) (/ small-size 1048576.0) (/ large-size 1048576.0))))


(deftest test-boot-sh-background-cleanups-avoid-uv-wrapper-and-zombies
  ;; boot.sh の背後片付け(boot_roots.py・python_installs.py)は標準 library のみなので uv run を挟まず
  ;; venv の python -I を直接呼び、double-fork で PID 1(tini)に回収させて defunct zombie を残さない。
  (val text (.read-text BOOT-SH :encoding "utf-8"))
  (for [fn-name #("doeff_boot_roots_gc" "doeff_python_installs_gc")]
    (val body (get (.split (get (.split text (+ fn-name "() {") 1) 1) "\n}\n" 1) 0))
    (val commands (lfor line (.splitlines body) :if (not (.startswith (.strip line) "#")) line))
    (assert (not (any (gfor line commands (in "uv run" line)))) (.format "{} should invoke venv python -I directly without uv run" fn-name))
    (assert (in "\"$root/.venv/bin/python\" -I" body) fn-name)))
