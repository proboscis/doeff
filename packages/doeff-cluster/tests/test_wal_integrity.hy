;;; WAL の整合(2026-09-25): 行と snapshot の checksum・壊れた末尾の扱い。
;;; 捨ててよいのは「最後の 1 行が読めない」時だけ(fsync の途中で落ちた = 返事をしていないまとまり)。途中の破損・checksum の不一致・
;;; seq の飛びは WalCorrupted で起動を断る(黙って切り詰めると、返事を済ませた書きが巻き戻った状態で起動する)。
(import json)
(import pytest)
(import pathlib [Path])
(import doeff_cluster.wal_store [WalStore WalCorrupted encode-line read-line-record scan-log sealed read-snapshot])


(defn #^ WalStore fresh [#^ Path tmp-path]
  (setv store (WalStore (str tmp-path) :max-log-bytes 10000000))
  (.load store)
  store)


(defn #^ bytes log-bytes [#^ Path tmp-path] (.read-bytes (/ tmp-path "wal.jsonl")))
(defn #^ None write-log [#^ Path tmp-path #^ bytes data] (.write-bytes (/ tmp-path "wal.jsonl") data))


(defn #^ None test-every-line-carries-a-checksum-that-is-checked [#^ Path tmp-path]
  (setv store (fresh tmp-path))
  (.persist store {"board/a" {"value" "日本語" "resourceVersion" 1}})
  (setv line (log-bytes tmp-path))
  (setv record (json.loads line))
  (assert (= (sorted record) ["crc" "delta" "seq"]))
  (setv #(back checked reason) (read-line-record line))
  (assert checked)
  (assert (= (get back "delta") {"board/a" {"value" "日本語" "resourceVersion" 1}})))


(defn #^ None test-a-flipped-byte-in-the-last-line-is-dropped-and-the-log-truncated [#^ Path tmp-path]
  ;; 最後の行が改行まで書けていても、中身が化けていれば(fsync の途中で落ちた page)捨てる。返事をしていないまとまり。
  (setv store (fresh tmp-path))
  (.persist store {"k/1" 1})
  (.persist store {"k/2" 2})
  (setv data (log-bytes tmp-path) first-len (+ (.index data b"\n") 1))
  (write-log tmp-path (+ (cut data 0 first-len) (.replace (cut data first-len None) b"\"k/2\":2" b"\"k/2\":3")))
  (setv again (WalStore (str tmp-path)))
  (assert (= (.load again) {"k/1" 1}))
  (assert (= again.seq 1))
  (assert (= (len (log-bytes tmp-path)) first-len))
  (assert (= (get again.recovered "reason") "checksum が合わない")))


(defn #^ None test-a-broken-line-in-the-middle-refuses-to-start-and-changes-nothing [#^ Path tmp-path]
  (setv store (fresh tmp-path))
  (for [i (range 3)] (.persist store {(+ "k/" (str i)) i}))
  (setv data (log-bytes tmp-path))
  (setv broken (.replace data b"\"k/1\":1" b"\"k/1\":9"))
  (write-log tmp-path broken)
  (with [raised (pytest.raises WalCorrupted)]
    (.load (WalStore (str tmp-path))))
  (assert (in "2 行目" (str raised.value)))
  ;; 何も書き換えない(人が調べられるように)
  (assert (= (log-bytes tmp-path) broken)))


(defn #^ None test-a-gap-in-seq-refuses-to-start [#^ Path tmp-path]
  (write-log tmp-path (+ (encode-line 1 {"a" 1}) (encode-line 3 {"b" 2}) (encode-line 4 {"c" 3})))
  (with [raised (pytest.raises WalCorrupted)]
    (.load (WalStore (str tmp-path))))
  (assert (in "続きでない" (str raised.value))))


(defn #^ None test-a-line-without-checksum-after-checked-lines-is-corruption [#^ Path tmp-path]
  (setv old (.encode (+ (json.dumps {"seq" 2 "delta" {"b" 2}}) "\n") "utf-8"))
  (write-log tmp-path (+ (encode-line 1 {"a" 1}) old (encode-line 3 {"c" 3})))
  (with [(pytest.raises WalCorrupted)]
    (.load (WalStore (str tmp-path)))))


(defn #^ None test-a-log-written-before-checksums-still-loads [#^ Path tmp-path]
  ;; 2026-09-25 より前の置き場(crc の無い行・snapshot)から起動できる。続きの書きは crc つき。
  (.write-text (/ tmp-path "snapshot.json") (json.dumps {"seq" 1 "kv" {"a" 1}}) :encoding "utf-8")
  (write-log tmp-path (.encode (+ (json.dumps {"seq" 2 "delta" {"b" 2}}) "\n") "utf-8"))
  (setv store (WalStore (str tmp-path)))
  (assert (= (.load store) {"a" 1 "b" 2}))
  (.persist store {"c" 3})
  (assert (= (.load (WalStore (str tmp-path))) {"a" 1 "b" 2 "c" 3})))


(defn #^ None test-a-snapshot-with-a-bad-checksum-refuses-to-start [#^ Path tmp-path]
  (setv store (fresh tmp-path))
  (.persist store {"a" 1})
  (.checkpoint store)
  (setv snap (/ tmp-path "snapshot.json"))
  (.write-bytes snap (.replace (.read-bytes snap) b"\"a\":1" b"\"a\":2"))
  (with [raised (pytest.raises WalCorrupted)]
    (.load (WalStore (str tmp-path))))
  (assert (in "checksum" (str raised.value))))


(defn #^ None test-lines-already-in-the-snapshot-are-not-applied-twice []
  ;; snapshot を書いた後・log を空にする前に落ちた形: log の行は snapshot の seq 以下なので当てない。
  (setv lines [(encode-line 1 {"a" 1}) (encode-line 2 {"a" None "b" 2}) (encode-line 3 {"c" 3})])
  (setv scan (scan-log lines 2 {"b" 2} "log"))
  (assert (= (get scan "kv") {"b" 2 "c" 3}))
  (assert (= (get scan "seq") 3))
  (assert (= (get scan "dropped") 0)))


(defn #^ None test-sealed-snapshot-round-trips []
  (assert (= (read-snapshot (sealed {"seq" 7 "kv" {"x" [1 2]}}) "s") #({"x" [1 2]} 7))))
