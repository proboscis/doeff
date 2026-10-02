;;; WAL の整合(2026-09-25): 行と snapshot の checksum・壊れた末尾の扱い。
;;; 捨ててよいのは「最後の 1 行が読めない」時だけ(fsync の途中で落ちた = 返事をしていないまとまり)。途中の破損・checksum の不一致・
;;; seq の飛びは WalCorrupted で起動を断る(黙って切り詰めると、返事を済ませた書きが巻き戻った状態で起動する)。
;;; 行と写しの形は coordinator/protocol/wal_format.hy、file の I/O は foundation/wal_store.hy の WalStore、両方を組むのは
;;; coordinator/protocol/store.hy の置き場の口(durable-load・durable-persist・durable-checkpoint — #2785)。
(require doeff-hy.macros [deftest defk <- val])
(import json)
(import pytest)
(import pathlib [Path])
(import doeff_cluster.foundation.wal_store [WalStore])
(import doeff_cluster.coordinator.protocol.wal_format [WalCorrupted GoodLine DroppedTail LogScan SnapshotRead encode-line read-line-record scan-log sealed read-snapshot])
(import doeff_cluster.coordinator.protocol.store [durable-load durable-persist durable-checkpoint])


(defk fresh [tmp-path]
  {:pre [(: tmp-path Path)] :post [(: % WalStore)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "空の dir に読み直し済みの置き場を作る — 検が書きから始めるため。"
  (val store (WalStore (str tmp-path) :max-log-bytes 10000000))
  (<- (durable-load store))
  store)


(defk log-bytes [tmp-path]
  {:pre [(: tmp-path Path)] :post [(: % bytes)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "置き場の log の byte — 検が書いた行を読むため。"
  (.read-bytes (/ tmp-path "wal.jsonl")))


(defk write-log [tmp-path data]
  {:pre [(: tmp-path Path) (: data bytes)] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "置き場の log を data で置き換える — 壊れた log・旧い形の log を作るため。"
  (.write-bytes (/ tmp-path "wal.jsonl") data)
  None)


(deftest test-every-line-carries-a-checksum-that-is-checked [tmp-path]
  (<- store WalStore (fresh tmp-path))
  (<- (durable-persist store {"board/a" {"value" "日本語" "resourceVersion" 1}}))
  (<- line bytes (log-bytes tmp-path))
  (val record (json.loads line))
  (assert (= (sorted record) ["crc" "delta" "seq"]))
  (<- back GoodLine (read-line-record line))
  (assert back.checked)
  (assert (= back.delta {"board/a" {"value" "日本語" "resourceVersion" 1}})))


(deftest test-a-flipped-byte-in-the-last-line-is-dropped-and-the-log-truncated [tmp-path]
  ;; 最後の行が改行まで書けていても、中身が化けていれば(fsync の途中で落ちた page)捨てる。返事をしていないまとまり。
  (<- store WalStore (fresh tmp-path))
  (<- (durable-persist store {"k/1" 1}))
  (<- (durable-persist store {"k/2" 2}))
  (<- data bytes (log-bytes tmp-path))
  (val first-len (+ (.index data b"\n") 1))
  (<- (write-log tmp-path (+ (cut data 0 first-len) (.replace (cut data first-len None) b"\"k/2\":2" b"\"k/2\":3"))))
  (val again (WalStore (str tmp-path)))
  (assert (= (! (durable-load again)) {"k/1" 1}))
  (assert (= again.seq 1))
  (assert (= (len (! (log-bytes tmp-path))) first-len))
  (assert (is-not again.recovered None) "壊れた行を捨てて読み直した記録がある")
  (assert (= (get again.recovered "reason") "checksum が合わない")))


(deftest test-a-broken-line-in-the-middle-refuses-to-start-and-changes-nothing [tmp-path]
  (<- store WalStore (fresh tmp-path))
  (for [i (range 3)] (<- (durable-persist store {(+ "k/" (str i)) i})))
  (<- data bytes (log-bytes tmp-path))
  (val broken (.replace data b"\"k/1\":1" b"\"k/1\":9"))
  (<- (write-log tmp-path broken))
  (with [raised (pytest.raises WalCorrupted)]
    (<- (durable-load (WalStore (str tmp-path)))))
  (assert (in "2 行目" (str raised.value)))
  ;; 何も書き換えない(人が調べられるように)
  (assert (= (! (log-bytes tmp-path)) broken)))


(deftest test-a-gap-in-seq-refuses-to-start [tmp-path]
  (<- (write-log tmp-path (+ (! (encode-line 1 {"a" 1})) (! (encode-line 3 {"b" 2})) (! (encode-line 4 {"c" 3})))))
  (with [raised (pytest.raises WalCorrupted)]
    (<- (durable-load (WalStore (str tmp-path)))))
  (assert (in "続きでない" (str raised.value))))


(deftest test-a-line-without-checksum-after-checked-lines-is-corruption [tmp-path]
  (val old (.encode (+ (json.dumps {"seq" 2 "delta" {"b" 2}}) "\n") "utf-8"))
  (<- (write-log tmp-path (+ (! (encode-line 1 {"a" 1})) old (! (encode-line 3 {"c" 3})))))
  (with [(pytest.raises WalCorrupted)]
    (<- (durable-load (WalStore (str tmp-path))))))


(deftest test-a-log-written-before-checksums-still-loads [tmp-path]
  ;; 2026-09-25 より前の置き場(crc の無い行・snapshot)から起動できる。続きの書きは crc つき。
  (.write-text (/ tmp-path "snapshot.json") (json.dumps {"seq" 1 "kv" {"a" 1}}) :encoding "utf-8")
  (<- (write-log tmp-path (.encode (+ (json.dumps {"seq" 2 "delta" {"b" 2}}) "\n") "utf-8")))
  (val store (WalStore (str tmp-path)))
  (assert (= (! (durable-load store)) {"a" 1 "b" 2}))
  (<- (durable-persist store {"c" 3}))
  (assert (= (! (durable-load (WalStore (str tmp-path)))) {"a" 1 "b" 2 "c" 3})))


(deftest test-a-snapshot-with-a-bad-checksum-refuses-to-start [tmp-path]
  (<- store WalStore (fresh tmp-path))
  (<- (durable-persist store {"a" 1}))
  (<- (durable-checkpoint store))
  (val snap (/ tmp-path "snapshot.json"))
  (.write-bytes snap (.replace (.read-bytes snap) b"\"a\":1" b"\"a\":2"))
  (with [raised (pytest.raises WalCorrupted)]
    (<- (durable-load (WalStore (str tmp-path)))))
  (assert (in "checksum" (str raised.value))))


(deftest test-lines-already-in-the-snapshot-are-not-applied-twice
  ;; snapshot を書いた後・log を空にする前に落ちた形: log の行は snapshot の seq 以下なので当てない。
  (val lines [(! (encode-line 1 {"a" 1})) (! (encode-line 2 {"a" None "b" 2})) (! (encode-line 3 {"c" 3}))])
  (<- scan LogScan (scan-log lines 2 {"b" 2} "log"))
  (assert (= scan.kv {"b" 2 "c" 3}))
  (assert (= scan.seq 3))
  (assert (is scan.dropped None)))



(deftest test-a-cut-last-line-is-reported-as-the-dropped-tail
  ;; 改行まで書けなかった最後の行: 当てずに、残す byte 数・捨てる byte 数・理由を返す(切り詰めるのは置き場の口)。
  (val lines [(! (encode-line 1 {"a" 1})) (cut (! (encode-line 2 {"b" 2})) 0 -3)])
  (<- scan LogScan (scan-log lines 0 {} "log"))
  (assert (= scan.kv {"a" 1}))
  (assert (= scan.seq 1))
  (assert (= scan.dropped (DroppedTail :kept (len (get lines 0)) :size (len (get lines 1)) :reason "改行が無い(途中で切れた)"))))

(deftest test-sealed-snapshot-round-trips
  (<- back SnapshotRead (read-snapshot (! (sealed {"seq" 7 "kv" {"x" [1 2]}})) "s"))
  (assert (= back (SnapshotRead :kv {"x" [1 2]} :seq 7))))
