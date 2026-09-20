;;; providerの応答から読むキャッシュ観測。受信処理の時計・resultの合計・子agentは使わない。
(require doeff-hy.macros [defk <-])
(import datetime [datetime])

(defk response-time-ms [text]
  {:pre [(: text (| str None))] :post [(: % (| int None))]}
  (when (not text) (return None))
  (try
    (setv at (datetime.fromisoformat (.replace text "Z" "+00:00")))
    (except [ValueError] (return None)))
  ;; zoneのない時刻を機体のローカル時刻で推測しない。
  (when (is at.tzinfo None) (return None))
  (setv millis (int (* (at.timestamp) 1000)))
  (if (< millis 0) None millis))

(defk cache-observation-of [records]
  {:pre [(: records tuple)] :post [(: % (| dict None))]}
  (setv latest None seen #{} known-ttl None known-model None)
  (for [record records]
    (when (or (not (isinstance record dict)) (!= (.get record "type") "assistant")
              (is-not (.get record "parent_tool_use_id") None))
      (continue))
    (setv message (.get record "message"))
    (when (not (isinstance message dict)) (continue))
    (setv response-id (.get message "id") model (.get message "model") usage (.get message "usage"))
    (when (or (not (isinstance response-id str)) (not response-id) (in response-id seen)
              (not (isinstance model str)) (= model "<synthetic>") (not (isinstance usage dict)))
      (continue))
    (.add seen response-id)
    (setv timestamp (.get record "timestamp"))
    (<- at (| int None) (response-time-ms (if (isinstance timestamp str) timestamp None)))
    (when (or (is at None) (and (is-not latest None) (< at (get latest "at")))) (continue))
    (setv read (.get usage "cache_read_input_tokens" 0)
          written (.get usage "cache_creation_input_tokens" 0)
          cache (.get usage "cache_creation"))
    (when (or (not (= (type read) int)) (not (= (type written) int)) (< read 0) (< written 0))
      (continue))
    (when (!= model known-model) (setv known-ttl None known-model model))
    (when (isinstance cache dict)
      (setv short (.get cache "ephemeral_5m_input_tokens" 0)
            long (.get cache "ephemeral_1h_input_tokens" 0))
      (cond
        (and (= (type short) int) (> short 0)) (setv known-ttl 300)
        ;; 今回の1h書込みは、読み出したprefix全体のTTLを証明しない。
        (and (= (type long) int) (> long 0))
          (setv known-ttl (if (and (> read 0) (= known-ttl 300)) 300 3600))))
    (setv latest {"responseId" response-id "at" at "ttlSeconds" known-ttl
                 "model" model "cacheRead" read "cacheWrite" written}))
  latest)

(defk with-request-start-bound [observation before-start-ms covers-turn]
  {:pre [(: observation (| dict None)) (: before-start-ms (| int None)) (: covers-turn bool)]
   :post [(: % (| dict None))]}
  "応答時刻はTTLの起点ではない。手番の開始を確認できた場合だけ開始時刻の下限を添える。"
  (when (is observation None) (return None))
  (setv at (.get observation "at")
        bound (if (and covers-turn (= (type at) int) (= (type before-start-ms) int)
                       (<= 0 before-start-ms at))
                  before-start-ms None)
        result (dict observation))
  (setv (get result "requestStartedAtLowerBound") bound)
  result)

(defk cache-context-of [status account]
  {:pre [(: status (| dict None)) (: account (| str None))]
   :post [(: % (| dict None))]}
  "短命なagent-jobが回収されても、前ターンのnode世代・認証口座・宣言世代を失わない。"
  (when (or (not status) (not account)) (return None))
  (setv binding (.get status "binding"))
  (when (not (isinstance binding dict)) (return None))
  (setv node-row (.get binding "nodeRow") generation (.get binding "declarationGeneration"))
  (when (or (not (isinstance node-row str)) (not node-row)
            (!= (type generation) int) (< generation 0)) (return None))
  {"nodeRow" node-row "account" account "declarationGeneration" generation})
