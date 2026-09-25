;;; effect の記録の置き場(effect-records)。coordinator の盤とは別の durable な置き場(Deployment 1 台 + longhorn-hot の PVC)に、
;;; 記録係(record_handlers.HttpSink)が送る行を run ごと・区切り(chunk)ごとの file に追記する。
;;;
;;;   POST /append {"service", "run", "chunk", "lines": [JSON の 1 行 …]}   追記して fsync してから返事をする
;;;   GET  /runs[?service=]                                             run の一覧(区切りの数・disk の byte・最初と最後の時刻)
;;;   GET  /runs/<service>/<run>[?fromChunk=&toChunk=]                  記録の行(JSONL の text。圧縮した区切りは戻して返す)
;;;   GET  /stats                                                       service ごとの byte と run の数
;;;
;;; 置き方: <root>/<service>/<run>/<区切り 6 桁>.jsonl。書き終わって idle-seconds 経った区切りは gzip にする(.jsonl.gz)。
;;; 保持: 最後の書きから retention-days を過ぎた run を丸ごと消す(再生は run の始まりから走らせるので、run の途中だけを残さない)。
;;;
;;; 入口 = record_store_main.hy(effect の class を __main__ に作らないため入口を分ける)。形は coordinator と同じ: HTTP の受付(別 thread)が要求を箱に並べ、1 本の Program(store-loop)が取り出して判断し、file の I/O は
;;; effect(AppendRecordLines 等)として handler(record_store_handlers.hy)が行う。
(require doeff-hy.macros [defk <-])
(import dataclasses [dataclass])
(import re)
(import sys)
(import doeff [EffectBase])
(import doeff_cluster.clock [now-epoch-ms])
(import doeff_cluster.cluster_model [Request NextRequests Reply PlainText CoordinatorStopRequested])


;; --- effect(file の I/O は handler だけ) ------------------------------------------------------

(defclass [(dataclass :frozen True)] AppendRecordLines [EffectBase]
  "結果は追記した行の数。fsync してから返る。"
  (#^ str service)
  (#^ str run)
  (#^ int chunk)
  (#^ list lines))

(defclass [(dataclass :frozen True)] ListRecordRuns [EffectBase]
  "結果は run の dict の list。service = None なら全部。"
  (#^ object service))

(defclass [(dataclass :frozen True)] ReadRecordRun [EffectBase]
  "結果は JSONL の text(区切りの順)。無ければ None。"
  (#^ str service)
  (#^ str run)
  (#^ object from-chunk)
  (#^ object to-chunk))

(defclass [(dataclass :frozen True)] CompactRecords [EffectBase]
  "結果は gzip にした区切りの数。idle-ms 書かれていない .jsonl を圧縮する。"
  (#^ int now-ms)
  (#^ int idle-ms))

(defclass [(dataclass :frozen True)] PruneRecords [EffectBase]
  "結果は消した run の #(service run) の list。最後の書きが now-ms - retention-ms より古い run を消す。"
  (#^ int now-ms)
  (#^ int retention-ms))


;; --- 純粋な判断 --------------------------------------------------------------------------------

(setv NAME-PATTERN (re.compile r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$"))
(setv MAINTENANCE-MS 300000)

(defn #^ bool safe-name? [name]
  "path の 1 段に使ってよい名(/ や .. を含まない)。"
  (and (isinstance name str) (is-not (.match NAME-PATTERN name) None) (not-in ".." name)))

(defn #^ tuple append-plan [body]
  "POST /append の本文 → #(None エラー文) か #(AppendRecordLines None)。"
  (when (not (isinstance body dict)) (return #(None "本文は JSON の object")))
  (setv service (.get body "service") run (.get body "run") chunk (.get body "chunk") lines (.get body "lines"))
  (cond
    (not (safe-name? service)) #(None "service の名が不正")
    (not (safe-name? run)) #(None "run の名が不正")
    (or (not (isinstance chunk int)) (isinstance chunk bool) (< chunk 0)) #(None "chunk は 0 以上の整数")
    (or (not (isinstance lines list)) (not (all (gfor l lines (and (isinstance l str) (not-in "\n" l))))))
      #(None "lines は改行を含まない文字列の list")
    True #((AppendRecordLines service run chunk lines) None)))

(defn optional-int [query key]
  (setv v (.get query key))
  (if (is v None) None (int v)))


(defk answer-request [request]
  {:pre [(: request Request)] :post [(: % tuple)]}
  ;; 要求 1 件 → #(status body)。
  (setv parts (lfor p (.split (.strip request.path "/") "/") :if p p))
  (cond
    (and (= request.method "POST") (= parts ["append"]))
      (do (setv #(effect error) (append-plan request.body))
          (if (is effect None)
              #(400 {"error" error})
              (do (<- n int (AppendRecordLines effect.service effect.run effect.chunk effect.lines))
                  #(200 {"appended" n}))))
    (and (= request.method "GET") (= parts ["runs"]))
      (do (<- runs list (ListRecordRuns (.get request.query "service")))
          #(200 {"runs" runs}))
    (and (= request.method "GET") (= (len parts) 3) (= (get parts 0) "runs"))
      (if (not (and (safe-name? (get parts 1)) (safe-name? (get parts 2))))
          #(400 {"error" "名が不正"})
          (do (<- text (| str None) (ReadRecordRun (get parts 1) (get parts 2)
                                                  (optional-int request.query "fromChunk") (optional-int request.query "toChunk")))
              (if (is text None) #(404 {"error" "その run は無い"}) #(200 (PlainText text "application/x-ndjson; charset=utf-8")))))
    (and (= request.method "GET") (= parts ["stats"]))
      (do (<- runs list (ListRecordRuns None))
          (setv by {})
          (for [r runs]
            (setv s (.setdefault by (get r "service") {"runs" 0 "bytes" 0}))
            (+= (get s "runs") 1)
            (+= (get s "bytes") (get r "bytes")))
          #(200 {"services" by}))
    (and (= request.method "GET") (= parts ["healthz"])) #(200 {"ok" True})
    True #(404 {"error" (.format "知らない口: {} {}" request.method request.path)})))


(defk store-loop [retention-ms idle-ms]
  {:pre [(: retention-ms int) (: idle-ms int)] :post [(: % int)]}
  ;; 要求を受けて答える。MAINTENANCE-MS ごとに、書き終わった区切りの圧縮と、保持を過ぎた run の削除。
  (setv last-maintenance 0 served 0)
  (while True
    (<- stopping bool (CoordinatorStopRequested))
    (when stopping (return served))
    (<- batch list (NextRequests 1.0))
    (for [request batch]
      (try
        (<- answered tuple (answer-request request))
        (except [e Exception]
          (setv answered #(500 {"error" (.format "{}: {}" (. (type e) __name__) e)}))))
      (<- (Reply request (get answered 0) (get answered 1)))
      (+= served 1))
    (<- now int (now-epoch-ms))
    (when (>= (- now last-maintenance) MAINTENANCE-MS)
      (setv last-maintenance now)
      (<- compacted int (CompactRecords now idle-ms))
      (<- pruned list (PruneRecords now retention-ms))
      (when (or compacted pruned)
        (print (.format "records: 圧縮 {} 区切り・保持を過ぎて消した run {}" compacted pruned) :file sys.stderr :flush True))))
  served)
