;;; effect の記録の置き場の答え手(record-files)の契約テスト — record-files は file system の effect を出すだけなので、本物
;;; (os-file-handler)と fake(memory-file-handler)の下で同じ deftest を通る。解釈器の組み立ては file_contract_handlers.hy。
;;;
;;;   * 追記は <根>/<service>/<run>/<区切り 6 桁>.jsonl の末尾に行ごと改行つきで足し、足した行の数を答える
;;;   * 読みは区切りの番号の順につなぐ(範囲で絞れる)・無い run は None・まだ無い根の一覧は空
;;;   * 一覧の行: 区切りの数・最後の区切り・disk の byte(file の大きさの和)・圧縮した区切りの数・頭の行(k = run の時だけ)と startedMs
;;;     (頭の行は区切りを丸ごと読まずに取る — 初めの読みの幅より長い行も・圧縮した区切りでも)
;;;   * 圧縮: 書き終わった .jsonl を .jsonl.gz にし .jsonl を消す・読みの答えは変わらない・圧縮の後に遅れて届いた行は .jsonl に足され、
;;;     次の圧縮で .gz の 2 つ目の member として続く
;;;   * 保持: 最後の書きが期限より古い run を丸ごと消し、消した [service run] を答える
;;; 契約の外(本物だけの性質): file の mtime(memory の置き場は 0 で答える)— 「まだ書いている区切りは圧縮しない」「期限の中の run を
;;; 残す」は mtime の読みの性質で、ここでは十分に先の時刻(全部が古い)と 0(全部が新しい)で比べる・fsync の効き目。
(require doeff-hy.macros [defk deftest <- val])
(import gzip)
(import json)
(import doeff [with_handlers])
(import doeff_core_effects.file_effects [ReadText ReadBytes ListDirectory])
(import doeff_cluster.record_store [AppendRecordLines ListRecordRuns ReadRecordRun CompactRecords PruneRecords])
(import doeff_cluster.record_store_handlers [record-files HEAD-READ-BYTES])
(import tests.file_contract_handlers [FilesRoot])

(val HEADER (json.dumps {"k" "run" "run" "r1" "startedMs" 1000} :ensure-ascii False))
(val FAR-FUTURE-MS (* 10 (** 10 15)))
(val LONG-HEADER (json.dumps {"k" "run" "startedMs" 7 "pad" (* "x" (* 3 HEAD-READ-BYTES))}))


(defk appends-and-reads []
  {:pre [] :post [(: % list)] :tags {:context "doeff-cluster-test" :role "program"}}
  "区切りを前後して追記し、全体・範囲・無い run を読む筋。"
  (<- a int (AppendRecordLines "svc" "r1" 2 ["{\"e\":3}"]))
  (<- b int (AppendRecordLines "svc" "r1" 0 [HEADER "{\"e\":1}"]))
  (<- c int (AppendRecordLines "svc" "r1" 0 ["{\"e\":2,\"t\":\"日本語\"}"]))
  (<- d int (AppendRecordLines "svc" "r1" 1 []))
  (<- whole (ReadRecordRun "svc" "r1" None None))
  (<- middle (ReadRecordRun "svc" "r1" 1 2))
  (<- missing (ReadRecordRun "svc" "none" None None))
  [a b c d whole middle missing])


(deftest test-appended-lines-land-in-the-chunk-file-and-read-back-in-chunk-order
  {:interpreters ["os-files" "memory-files"]}
  (<- root str (FilesRoot))
  (<- answers list (with_handlers [(record-files (+ root "/records"))] (appends-and-reads)))
  (assert (= (cut answers 0 4) [1 2 1 0]) answers)
  (assert (= (get answers 4) (+ HEADER "\n{\"e\":1}\n{\"e\":2,\"t\":\"日本語\"}\n{\"e\":3}\n")) answers)
  (assert (= (get answers 5) "{\"e\":3}\n") answers)
  (assert (is (get answers 6) None) answers)
  (<- chunk0 str (ReadText (+ root "/records/svc/r1/000000.jsonl")))
  (assert (= chunk0 (+ HEADER "\n{\"e\":1}\n{\"e\":2,\"t\":\"日本語\"}\n")) chunk0)
  (<- names tuple (ListDirectory (+ root "/records/svc/r1")))
  (assert (= (lfor e names e.name) ["000000.jsonl" "000001.jsonl" "000002.jsonl"]) names))


(defk untouched-store []
  {:pre [] :post [(: % list)] :tags {:context "doeff-cluster-test" :role "program"}}
  "1 行も書いていない置き場で、一覧・読み・圧縮・保持を出す筋。"
  (<- runs (ListRecordRuns None))
  (<- text (ReadRecordRun "svc" "r1" None None))
  (<- compacted (CompactRecords FAR-FUTURE-MS 0))
  (<- pruned (PruneRecords FAR-FUTURE-MS 0))
  [runs text compacted pruned])


(deftest test-a-store-without-a-root-lists-nothing-and-reads-none
  {:interpreters ["os-files" "memory-files"]}
  (<- root str (FilesRoot))
  (<- answers list (with_handlers [(record-files (+ root "/not-yet"))] (untouched-store)))
  (assert (= answers [[] None 0 []]) answers))


(defk several-runs []
  {:pre [] :post [(: % list)] :tags {:context "doeff-cluster-test" :role "program"}}
  "2 つの service に run を書き、全部と 1 つの service の一覧を読む筋。"
  (<- (AppendRecordLines "svc" "r1" 0 [HEADER "{\"e\":1}"]))
  (<- (AppendRecordLines "svc" "r1" 3 ["{\"e\":2}"]))
  (<- (AppendRecordLines "svc" "r2" 0 ["{\"k\":\"call\",\"startedMs\":5}"]))
  (<- (AppendRecordLines "svc" "r3" 0 [LONG-HEADER]))
  (<- (AppendRecordLines "other" "r9" 0 ["not json"]))
  (<- all-runs (ListRecordRuns None))
  (<- only (ListRecordRuns "other"))
  [all-runs only])


(deftest test-the-run-list-counts-chunks-bytes-and-reads-the-header-line
  {:interpreters ["os-files" "memory-files"]}
  (<- root str (FilesRoot))
  (<- runs list (with_handlers [(record-files (+ root "/records"))] (several-runs)))
  (val all-runs (get runs 0))
  (val only (get runs 1))
  (assert (= (lfor r all-runs #((get r "service") (get r "run"))) [#("other" "r9") #("svc" "r1") #("svc" "r2") #("svc" "r3")]) all-runs)
  (assert (= (lfor r only (get r "run")) ["r9"]) only)
  (val by (dfor r all-runs (get r "run") r))
  (val r1 (get by "r1"))
  (assert (= #((get r1 "chunks") (get r1 "lastChunk") (get r1 "compressedChunks")) #(2 3 0)) r1)
  (assert (= (get r1 "bytes") (len (.encode (+ HEADER "\n{\"e\":1}\n{\"e\":2}\n") "utf-8"))) r1)
  (assert (= (get r1 "header") (json.loads HEADER)) r1)
  (assert (= (get r1 "startedMs") 1000) r1)
  (assert (isinstance (get r1 "lastWriteMs") int) r1)
  ;; 頭の行が k = run でなければ header は None(startedMs は読めた object から)・JSON でなければ両方 None。
  (assert (= #((get (get by "r2") "header") (get (get by "r2") "startedMs")) #(None 5)) (get by "r2"))
  (assert (= #((get (get by "r9") "header") (get (get by "r9") "startedMs")) #(None None)) (get by "r9"))
  ;; 初めの読みの幅より長い頭の行も読む。
  (assert (= (get (get by "r3") "startedMs") 7) (get by "r3")))


(defk compacts-twice []
  {:pre [] :post [(: % list)] :tags {:context "doeff-cluster-test" :role "program"}}
  "区切りを圧縮し、遅れて届いた行を足してもう一度圧縮する筋(各段の読みと一覧を返す)。"
  (<- (AppendRecordLines "svc" "r1" 0 [HEADER "{\"e\":1}"]))
  (<- (AppendRecordLines "svc" "r1" 1 ["{\"e\":2}"]))
  (<- (AppendRecordLines "svc" "r3" 0 [LONG-HEADER]))
  (<- before (ReadRecordRun "svc" "r1" None None))
  (<- first (CompactRecords FAR-FUTURE-MS 0))
  (<- after (ReadRecordRun "svc" "r1" None None))
  (<- runs (ListRecordRuns "svc"))
  (<- (AppendRecordLines "svc" "r1" 0 ["{\"e\":1.5}"]))
  (<- late (ReadRecordRun "svc" "r1" None None))
  (<- second (CompactRecords FAR-FUTURE-MS 0))
  (<- again (ReadRecordRun "svc" "r1" None None))
  (<- none-left (CompactRecords FAR-FUTURE-MS 0))
  [before first after runs late second again none-left])


(deftest test-compaction-keeps-the-text-and-late-lines-follow-the-compressed-chunk
  {:interpreters ["os-files" "memory-files"]}
  (<- root str (FilesRoot))
  (<- answers list (with_handlers [(record-files (+ root "/records"))] (compacts-twice)))
  (val before (get answers 0))
  (val after (get answers 2))
  (val runs (get answers 3))
  (val late (get answers 4))
  (val again (get answers 6))
  (assert (= (get answers 1) 3) answers)
  (assert (= after before) after)
  ;; 圧縮した区切りの頭の行も読む(初めの読みの幅より長い行も)。
  (assert (= (lfor r runs #((get r "run") (get r "compressedChunks") (get r "startedMs"))) [#("r1" 2 1000) #("r3" 1 7)]) runs)
  (assert (= late (+ HEADER "\n{\"e\":1}\n{\"e\":1.5}\n{\"e\":2}\n")) late)
  (assert (= (get answers 5) 1) answers)
  (assert (= again late) again)
  (assert (= (get answers 7) 0) answers)
  (<- names tuple (ListDirectory (+ root "/records/svc/r1")))
  (assert (= (lfor e names e.name) ["000000.jsonl.gz" "000001.jsonl.gz"]) names)
  ;; 圧縮した区切りは gzip の member を 2 つつないだ物(遅れて届いた行が 2 つ目)で、つないで読むと元の行になる。
  (<- packed bytes (ReadBytes (+ root "/records/svc/r1/000000.jsonl.gz")))
  (assert (= (.decode (gzip.decompress packed) "utf-8") (+ HEADER "\n{\"e\":1}\n{\"e\":1.5}\n")) packed))


(defk prunes []
  {:pre [] :post [(: % list)] :tags {:context "doeff-cluster-test" :role "program"}}
  "期限の中(時刻 0)と期限の外(十分に先の時刻)で保持を出す筋。"
  (<- (AppendRecordLines "svc" "r1" 0 ["{\"e\":1}"]))
  (<- (AppendRecordLines "svc" "r2" 0 ["{\"e\":1}"]))
  (<- (AppendRecordLines "other" "r3" 0 ["{\"e\":1}"]))
  (<- kept (PruneRecords 0 1000))
  (<- after-kept (ListRecordRuns None))
  (<- removed (PruneRecords FAR-FUTURE-MS 0))
  (<- after-removed (ListRecordRuns None))
  (<- text (ReadRecordRun "svc" "r1" None None))
  [kept (len after-kept) removed after-removed text])


(deftest test-prune-removes-the-runs-past-retention-and-keeps-the-rest
  {:interpreters ["os-files" "memory-files"]}
  (<- root str (FilesRoot))
  (<- answers list (with_handlers [(record-files (+ root "/records"))] (prunes)))
  (assert (= answers [[] 3 [["other" "r3"] ["svc" "r1"] ["svc" "r2"]] [] None]) answers)
  (<- left tuple (ListDirectory (+ root "/records/svc")))
  (assert (= left #()) left))
