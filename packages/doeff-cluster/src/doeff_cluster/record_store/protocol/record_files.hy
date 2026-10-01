;;; effect の記録の置き場の file の I/O の言い換え(record_store.intent.record_store_model の effect の handler — #2030 で層 protocol へ)。
;;; 置き方: <root>/<service>/<run>/<区切り 6 桁>.jsonl(書いている区切り)・.jsonl.gz(書き終わって圧縮した区切り)。
;;; 追記は 1 要求ごとに fsync してから返す(返事を済ませた行は Pod が落ちても残る)。圧縮した後に遅れて届いた行は .jsonl に
;;; 追記され、読みは .jsonl.gz → .jsonl の順につなぐ(同じ区切りの中の順は行の番号 e が持つ — 読む側が並べ直す)。
;;;
;;; record-files は自分で os を呼ばない: 置き場の判断(区切りの並び・圧縮と保持の選び・一覧の形)だけを持ち、file の I/O は汎用の file system の
;;; effect(doeff_core_effects.file_effects)で出す。答え手は外側に被せる — 本番 = os-file-handler(record_store.entry.main)・検 = memory-file-handler。
;;; 本物と fake が同じ判断の関数を通るので、同じ契約テスト(tests/test_record_files_contract.hy)を両方で回せる。
;;; file の effect の断り(FileFailed)は OSError で上げる(前の形の os の呼び出しと同じ — store-loop が 500 で答える)。
(require doeff-hy.macros [defhandler defk <- val var])
(val MODULE-TAGS {:context "record-store" :role "protocol"})
(import gzip)
(import io)
(import json)
(import os)
(import zlib)
(import doeff_core_effects.file_effects [PathKind PathStat StatPath ReadBytes AppendText WriteBytes MakeDirectory ListDirectory
                                         RemoveTree file-done])
(import doeff_cluster.record_store.intent.record_store_model [AppendRecordLines ListRecordRuns ReadRecordRun CompactRecords PruneRecords])

;; 区切りの頭の 1 行を探す読みの初めの byte 数(行の終わりが見つからなければ倍にして読み直す — 区切りを丸ごと読まない)。
(val HEAD-READ-BYTES 65536)
;; 圧縮した区切りの mode(前の形の gzip.open が umask 022 の下で作った物と同じ — 置き換えの書きの一時 file は 0600 なので明示する)。
(val RECORD-FILE-MODE 0o644)


(defk chunk-stem [chunk]
  {:pre [(: chunk int)] :post [(: % str)] :tags {:context "doeff-cluster" :role "judgment"}}
  "区切りの番号を file の名の頭(6 桁)にするため。"
  (.format "{:06d}" chunk))


(defk chunk-files [run-dir]
  {:pre [(: run-dir str)] :post [(: % dict)] :tags {:context "doeff-cluster" :role "judgment"}}
  "run の dir の中身を、区切りの番号 → その区切りの file の path の list(.jsonl.gz を先に)にするため。"
  (<- entries tuple (file-done (ListDirectory run-dir)))
  (val out {})
  (for [entry entries]
    (val name entry.name)
    (val path (os.path.join run-dir name))
    (cond
      (.endswith name ".jsonl.gz") (.insert (.setdefault out (int (cut name 0 6)) []) 0 path)
      (.endswith name ".jsonl") (.append (.setdefault out (int (cut name 0 6)) []) path)))
  out)


(defk chunk-text [path]
  {:pre [(: path str)] :post [(: % str)] :tags {:context "doeff-cluster" :role "foundation"}}
  "区切りの file 1 つの中身を text で読むため(.gz は戻す — 遅れて足した member も続けて 1 つとして)。"
  (<- content bytes (file-done (ReadBytes path)))
  (.decode (if (.endswith path ".gz") (gzip.decompress content) content) "utf-8"))


(defk head-line [path]
  {:pre [(: path str)] :post [(: % (| str None))] :tags {:context "doeff-cluster" :role "foundation"}}
  "区切りの file の先頭の 1 行(行の終わりを含む・無ければ file の全部)を、file を丸ごと読まずに取るため(区切りは数百 MB に
   なりうる)。.gz は読んだ頭だけを戻す。読めない・戻せない file は None(一覧の頭の行が無いだけ — 一覧は止めない)。"
  (var limit HEAD-READ-BYTES)
  (while True
    (<- raw (ReadBytes path :limit limit))
    (when (not (isinstance raw bytes))
      (return None))
    (try
      (val data (if (.endswith path ".gz") (.decompress (zlib.decompressobj (+ 16 zlib.MAX-WBITS)) raw) raw))
      (val end (.find data b"\n"))
      (cond
        (>= end 0) (return (.decode (cut data 0 (+ end 1)) "utf-8"))
        (< (len raw) limit) (return (.decode data "utf-8")))
      (except [Exception]
        (return None)))
    (:= limit (* 2 limit))))


(defk run-header [line]
  {:pre [(: line (| str None))] :post [(: % (| dict None))] :tags {:context "doeff-cluster" :role "judgment"}}
  "区切りの頭の行を JSON の object として読むため(読めなければ None)。"
  (try
    (val parsed (if (is line None) None (json.loads line)))
    (if (isinstance parsed dict) parsed None)
    (except [ValueError]
      None)))


(defk run-view [run-dir service run]
  {:pre [(: run-dir str) (: service str) (: run str)] :post [(: % dict)] :tags {:context "doeff-cluster" :role "judgment"}}
  "run 1 つの一覧の行(区切りの数・disk の byte・圧縮した区切りの数・最後の書きの時刻・頭の行)を作るため。"
  (<- chunks dict (chunk-files run-dir))
  (val files (lfor fs (.values chunks) f fs f))
  (val stats [])
  (for [f files]
    (<- found PathStat (file-done (StatPath f)))
    (.append stats found))
  (var header None)
  (when chunks
    (<- line (head-line (get (get chunks (min chunks)) 0)))
    (<- parsed (run-header line))
    (:= header parsed))
  {"service" service "run" run "chunks" (len chunks) "lastChunk" (if chunks (max chunks) None)
   "bytes" (sum (gfor s stats s.size))
   "compressedChunks" (len (lfor fs (.values chunks) :if (.endswith (get fs 0) ".gz") fs))
   "lastWriteMs" (if stats (int (* 1000 (max (gfor s stats s.modified)))) None)
   "startedMs" (if (is header None) None (.get header "startedMs"))
   "header" (if (and (is-not header None) (= (.get header "k") "run")) header None)})


(defk dir-names [path]
  {:pre [(: path str)] :post [(: % list)] :tags {:context "doeff-cluster" :role "foundation"}}
  "dir の直下の dir の名を名の順に並べるため。"
  (<- entries tuple (file-done (ListDirectory path)))
  (lfor e entries :if (= e.kind PathKind.DIRECTORY) e.name))


(defk root-exists [root]
  {:pre [(: root str)] :post [(: % bool)] :tags {:context "doeff-cluster" :role "foundation"}}
  "置き場の根が在るかを読むため(まだ 1 行も書いていない置き場は空として答える)。"
  (<- found PathStat (file-done (StatPath root)))
  (!= found.kind PathKind.MISSING))


(defk append-lines [root service run chunk lines]
  {:pre [(: root str) (: service str) (: run str) (: chunk int) (: lines list)] :post [(: % int)] :tags {:context "doeff-cluster" :role "foundation"}}
  "行を区切りの .jsonl の末尾へ足し、disk へ落としてから足した行の数を返すため。"
  (val run-dir (os.path.join root service run))
  (<- (file-done (MakeDirectory run-dir)))
  (<- stem str (chunk-stem chunk))
  (<- (file-done (AppendText (os.path.join run-dir (+ stem ".jsonl")) (.join "" (gfor line lines (+ line "\n"))) :sync True)))
  (len lines))


(defk list-runs [root service]
  {:pre [(: root str) (: service (| str None))] :post [(: % list)] :tags {:context "doeff-cluster" :role "judgment"}}
  "置き場の run の一覧(service = None なら全部)を作るため。"
  (val out [])
  (<- exists bool (root-exists root))
  (when exists
    (<- services list (dir-names root))
    (for [name services]
      (when (or (is service None) (= name service))
        (<- runs list (dir-names (os.path.join root name)))
        (for [run runs]
          (<- view dict (run-view (os.path.join root name run) name run))
          (.append out view)))))
  out)


(defk read-run [root service run from-chunk to-chunk]
  {:pre [(: root str) (: service str) (: run str) (: from-chunk (| int None)) (: to-chunk (| int None))] :post [(: % (| str None))]
   :tags {:context "doeff-cluster" :role "judgment"}}
  "run の記録の行を区切りの順につないだ JSONL の text を作るため(無い run は None)。"
  (val run-dir (os.path.join root service run))
  (<- found PathStat (file-done (StatPath run-dir)))
  (when (!= found.kind PathKind.DIRECTORY)
    (return None))
  (<- chunks dict (chunk-files run-dir))
  (val parts [])
  (for [#(n files) (sorted (.items chunks))]
    (when (and (or (is from-chunk None) (>= n from-chunk)) (or (is to-chunk None) (<= n to-chunk)))
      (for [f files]
        (<- text str (chunk-text f))
        (.append parts (if (and text (not (.endswith text "\n"))) (+ text "\n") text)))))
  (.join "" parts))


(defk gzip-member [gz-path content]
  {:pre [(: gz-path str) (: content bytes)] :post [(: % bytes)] :tags {:context "doeff-cluster" :role "judgment"}}
  "区切りの中身を、gzip.open(gz-path, \"ab\") が足すのと同じ 1 つの gzip の member(頭に元の file の名と今の時刻)にするため。"
  (val buffer (io.BytesIO))
  (with [out (gzip.GzipFile :filename gz-path :mode "wb" :fileobj buffer)]
    (.write out content))
  (.getvalue buffer))


(defk compact-chunk [path]
  {:pre [(: path str)] :post [(: % None)] :tags {:context "doeff-cluster" :role "foundation"}}
  "書き終わった .jsonl 1 つを .jsonl.gz の末尾の member にし(既に在れば足す — gzip は複数の member をつないで 1 つとして読める)、
   disk へ落としてから .jsonl を消すため。"
  (val gz (+ path ".gz"))
  (<- content bytes (file-done (ReadBytes path)))
  (<- member bytes (gzip-member gz content))
  (<- found PathStat (file-done (StatPath gz)))
  (var earlier b"")
  (when (= found.kind PathKind.FILE)
    (<- read bytes (file-done (ReadBytes gz)))
    (:= earlier read))
  (<- (file-done (WriteBytes gz (+ earlier member) :mode RECORD-FILE-MODE :replace True :sync True)))
  (<- (file-done (RemoveTree path)))
  None)


(defk compact [root now-ms idle-ms]
  {:pre [(: root str) (: now-ms int) (: idle-ms int)] :post [(: % int)] :tags {:context "doeff-cluster" :role "judgment"}}
  "idle-ms の間書かれていない区切りの .jsonl を圧縮し、圧縮した数を返すため。"
  (var n 0)
  (<- exists bool (root-exists root))
  (when exists
    (<- services list (dir-names root))
    (for [service services]
      (<- runs list (dir-names (os.path.join root service)))
      (for [run runs]
        (val run-dir (os.path.join root service run))
        (<- entries tuple (file-done (ListDirectory run-dir)))
        (for [entry entries]
          (when (and (.endswith entry.name ".jsonl") (= entry.kind PathKind.FILE))
            (val path (os.path.join run-dir entry.name))
            (<- found PathStat (file-done (StatPath path)))
            (when (< (* 1000 found.modified) (- now-ms idle-ms))
              (<- (compact-chunk path))
              (:= n (+ n 1))))))))
  n)


(defk prune [root now-ms retention-ms]
  {:pre [(: root str) (: now-ms int) (: retention-ms int)] :post [(: % list)] :tags {:context "doeff-cluster" :role "judgment"}}
  "最後の書きが now-ms - retention-ms より古い run を丸ごと消し、消した [service run] の list を返すため。"
  (val removed [])
  (<- exists bool (root-exists root))
  (when exists
    (<- services list (dir-names root))
    (for [service services]
      (<- runs list (dir-names (os.path.join root service)))
      (for [run runs]
        (val run-dir (os.path.join root service run))
        (<- entries tuple (file-done (ListDirectory run-dir)))
        (var last 0)
        (for [entry entries]
          (<- found PathStat (file-done (StatPath (os.path.join run-dir entry.name))))
          (:= last (max last (* 1000 found.modified))))
        (when (< last (- now-ms retention-ms))
          (<- (file-done (RemoveTree run-dir)))
          (.append removed [service run])))))
  removed)


(defhandler record-files [#^ str root]
  ;; 引数に残す理由: 置き場の根は process ごとの設定(--root)。file の I/O は外側の file system の答え手(頭の註)。
  (AppendRecordLines [service run chunk lines]
    (<- appended int (append-lines root service run chunk lines))
    (resume appended))
  (ListRecordRuns [service]
    (<- runs list (list-runs root service))
    (resume runs))
  (ReadRecordRun [service run from-chunk to-chunk]
    (<- text (read-run root service run from-chunk to-chunk))
    (resume text))
  (CompactRecords [now-ms idle-ms]
    (<- compacted int (compact root now-ms idle-ms))
    (resume compacted))
  (PruneRecords [now-ms retention-ms]
    (<- removed list (prune root now-ms retention-ms))
    (resume removed)))
