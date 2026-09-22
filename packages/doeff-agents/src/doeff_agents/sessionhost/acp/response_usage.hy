;;; 手番の応答(API の応答 1 つ = message 1 つ)ごとの消費 — card acp:kanban-issue:ki-c3ac5832a0bd。
;;; 材料は claude の行(headless の stream-json も tui の transcript も同じ形)。判断は純関数だけで、書くのは
;;; 手番の終わりの 1 点(agentd.end-turn-record → turn-record の status.responses)。
;;;
;;; ⚠ assistant の行の usage.output_tokens は**途中の値**(stream-json では block ごとの行が出た拍の数 — 実測 2026-09-22:
;;; 行は 5、同じ message の message_delta は 1244)。応答の出力の最終値は stream_event の message_delta が運ぶ。
;;; ⇒ 同じ message の観測を全部畳み、欄ごとに大きい方を採る(token の数は message の中で減らない)。
(require doeff-hy.macros [defk <-])
(import json)
(import doeff_agents.sessionhost.acp.cache_observation [response-time-ms])
(import doeff_agents.sessionhost.acp.effects [TURN-RECORD-RESPONSES-BYTE-BUDGET])

;; 応答の欄(契約の items の token の欄ちょうど)。cacheWrite5m / cacheWrite1h は材料に在る時だけ。
(setv RESPONSE-TOKEN-FIELDS #("input" "output" "cacheWrite" "cacheRead" "cacheWrite5m" "cacheWrite1h"))
(setv RESPONSE-REQUIRED-TOKENS #("input" "output" "cacheWrite" "cacheRead"))
;; 課金の無い合成の応答(CLI が自分で組む message — usage は 0)。
(setv SYNTHETIC-MODEL "<synthetic>")


(defk response-tokens-of [usage]
  {:pre [(: usage dict)] :post [(: % dict)]}
  "claude の usage(assistant の message.usage / message_delta の usage)→ 応答の token の欄。数でない欄は落とす(0 を発明しない)。"
  (setv out {})
  (for [[field raw] [#("input" (.get usage "input_tokens")) #("output" (.get usage "output_tokens"))
                     #("cacheWrite" (.get usage "cache_creation_input_tokens"))
                     #("cacheRead" (.get usage "cache_read_input_tokens"))]]
    (when (and (= (type raw) int) (>= raw 0))
      (setv (get out field) raw)))
  (setv cache (.get usage "cache_creation"))
  (when (isinstance cache dict)
    (for [[field raw] [#("cacheWrite5m" (.get cache "ephemeral_5m_input_tokens"))
                       #("cacheWrite1h" (.get cache "ephemeral_1h_input_tokens"))]]
      (when (and (= (type raw) int) (>= raw 0))
        (setv (get out field) raw))))
  out)


(defk merged-response [known tokens]
  {:pre [(: known dict) (: tokens dict)] :post [(: % dict)]}
  "同じ応答の 2 つ目の観測を畳む — 欄ごとに大きい方(出力は message_delta で伸びる・他の欄は変わらない)。"
  (setv out (dict known))
  (for [[field value] (.items tokens)]
    (setv (get out field) (max value (.get out field 0))))
  out)


(defk response-usages-of [records]
  {:pre [(: records tuple)] :post [(: % tuple)]}
  "材料の行の列 → 応答ごとの消費の列(最初に見た順)。1 項 = {at, model?, subagent?, input, output, cacheWrite, cacheRead,
   cacheWrite5m?, cacheWrite1h?}。畳む観測は 3 つ: assistant の行(message.id・usage・時刻)・stream_event の message_start
   (message.id・usage)・message_delta(usage — 名指しはその親〔parent_tool_use_id〕で直前に始まった message)。
   時刻(at)は assistant の行の timestamp の最初の値で、受信処理の時計は使わない。時刻を 1 度も持たなかった応答と
   token の必須 4 欄が揃わない応答は落とす(発明しない)。合成の応答(model <synthetic>)は数えない。"
  (setv found {} order [] current {})
  (for [record records]
    (when (not (isinstance record dict)) (continue))
    (setv kind (.get record "type")
          parent (.get record "parent_tool_use_id"))
    (setv parent-key (if (isinstance parent str) parent ""))
    (setv message None usage None at None)
    (cond
      (= kind "assistant")
      (do (setv message (.get record "message"))
          (setv timestamp (.get record "timestamp"))
          (<- at (| int None) (response-time-ms (if (isinstance timestamp str) timestamp None))))
      (= kind "stream_event")
      (do (setv event (.get record "event"))
          (when (not (isinstance event dict)) (continue))
          (setv event-type (.get event "type"))
          (cond
            (= event-type "message_start") (setv message (.get event "message"))
            (= event-type "message_delta")
            (do (setv response-id (.get current parent-key)
                      usage (.get event "usage"))
                (when (and (in response-id found) (isinstance usage dict))
                  (<- tokens dict (response-tokens-of usage))
                  (<- merged dict (merged-response (get found response-id) tokens))
                  (setv (get found response-id) merged))
                (continue))
            True (continue)))
      True (continue))
    (when (not (isinstance message dict)) (continue))
    (setv response-id (.get message "id") model (.get message "model") usage (.get message "usage"))
    (when (or (not (isinstance response-id str)) (not response-id) (= model SYNTHETIC-MODEL)
              (not (isinstance usage dict)))
      (continue))
    (when (= kind "stream_event")
      (setv (get current parent-key) response-id))
    (<- tokens dict (response-tokens-of usage))
    (if (in response-id found)
        (do (<- merged dict (merged-response (get found response-id) tokens))
            (setv (get found response-id) merged))
        (do (setv fresh (dict tokens))
            (when (isinstance model str) (setv (get fresh "model") model))
            (when parent-key (setv (get fresh "subagent") True))
            (setv (get found response-id) fresh)
            (.append order response-id)))
    (when (and (is-not at None) (not-in "at" (get found response-id)))
      (setv (get (get found response-id) "at") at)))
  (tuple (lfor response-id order
               :setv response (get found response-id)
               :if (and (in "at" response) (all (gfor field RESPONSE-REQUIRED-TOKENS (in field response))))
               response)))


(defk compact-bytes [value]
  {:pre [(: value dict)] :post [(: % int)]}
  "engine が statusByteBudget を測るのと同じ物差し — compact JSON の UTF-8 の byte 数。"
  (len (.encode (json.dumps value :separators #("," ":") :ensure-ascii False) "utf-8")))


(defk responses-status-of [responses]
  {:pre [(: responses tuple)] :post [(: % dict)]}
  "応答の列 → turn-record の status.responses(契約 agora-kinds.json)。items = 古い順に先頭から、欄の compact JSON が
   TURN-RECORD-RESPONSES-BYTE-BUDGET(契約 conventions.turnRecordResponses.byteBudget)に収まるまで(n = 1 始まりの番号)。
   超えた分は末尾を切り dropped が数を名乗る(手番の 1 本目は必ず残る — 日次の冷えの判定の材料)。欄の順は契約の並び。
   ⚠ 測るのは dropped を載せた形(切った時の欄の全体)— 載せる前の形で測ると印の分だけ上限を越える。"
  (setv items [])
  (for [[index response] (enumerate responses)]
    (setv item {"n" (+ index 1) "at" (get response "at")})
    (when (in "model" response) (setv (get item "model") (get response "model")))
    (for [field RESPONSE-TOKEN-FIELDS]
      (when (in field response) (setv (get item field) (get response field))))
    (when (.get response "subagent") (setv (get item "subagent") True))
    (setv trial {"count" (len responses) "items" (+ items [item])})
    (when (< (+ index 1) (len responses))
      (setv (get trial "dropped") (- (len responses) (+ index 1))))
    (<- size int (compact-bytes trial))
    (when (and items (> size TURN-RECORD-RESPONSES-BYTE-BUDGET))
      (break))
    (.append items item))
  (setv out {"count" (len responses) "items" items})
  (when (> (len responses) (len items))
    (setv (get out "dropped") (- (len responses) (len items))))
  out)


(defk usage-of-responses [responses]
  {:pre [(: responses tuple)] :post [(: % (| dict None))]}
  "応答の列 → 手番の合計(turn-record の status.usage — 契約の 6 欄ちょうど・model は運ばない)。応答が無ければ None。
   内訳(cacheWrite5m / cacheWrite1h)は 1 つでも応答が持っていれば足す。"
  (when (not responses) (return None))
  (setv total {"input" 0 "output" 0 "cacheWrite" 0 "cacheRead" 0})
  (for [response responses]
    (for [field RESPONSE-TOKEN-FIELDS]
      (when (in field response)
        (setv (get total field) (+ (.get total field 0) (get response field))))))
  total)
