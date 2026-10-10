;;; codex の app-server の stdout の 1 行(JSON-RPC の message 1 つ)を、上の層が読む型つきの記録へ分ける読み手。
;;;
;;; 分類(classify-line)は効果を持たない: 1 行 → CodexLine。JSON の境目はこの file の 1 か所 — 行は defwire の型で解いて確かめ、
;;; 上の層は記録の型だけを読む(生の dict を読み直す 2 か所目を作らない)。形は codex 0.162.1 の実物の行(tests/recorded/codex-0.162.1)
;;; と、同じ版の `codex app-server generate-json-schema` の定義に合わせた。wire の型は使う欄だけを持ち、知らない欄は読み捨てる
;;; (codex は版ごとに欄を足す — 足された欄で読めなくならないように)。使う欄が無い・型が違う行は Unparsed にし、訳を残す(黙って
;;; Other に混ぜない — 版の食い違いを上の層が数えられるように)。
;;;
;;; 行の 4 つの形(JSON-RPC): 要求への答え = id と result / 要求への誤りの答え = id と error / 通知 = method と params /
;;; codex からの要求 = id と method と params(道具の許可の問いなど — 上の層が同じ id で答える)。
(require doeff-hy.macros [val])
(val MODULE-TAGS {:context "codex" :role "foundation"})
(require doeff-hy.macros [defk <-])
(require doeff-hy.record [defenum defrecord defwire])
(import collections.abc [Callable])
(import dataclasses [dataclass])
(import enum [StrEnum])
(import doeff_hy.json_value [OpaqueJson])
(import doeff_hy.wire [parse parse-json Malformed])

;; ターンの状態(turn/started・turn/completed の turn.status の綴り — codex 0.162.1 の TurnStatus)。
(defenum TurnStatus COMPLETED INTERRUPTED FAILED (IN-PROGRESS "inProgress"))


;; --- 読む行の wire の型(使う欄だけ・知らない欄は読み捨てる) ---------------------------------------

(defwire RpcErrorWire
  "要求への誤りの答えの error(code と文 — JSON-RPC ではどちらも必ず在る。欠けた答えは形の合わない行)。"
  {:tags {:context "codex" :role "type"} :names :camel :unknown :ignore}
  (#^ int code)
  (#^ str message))

(defwire EnvelopeWire
  "JSON-RPC の message 1 つの外枠。params と result は method ごとに形が違うので、中を読まずに運び、method で選んだ型で解き直す。"
  {:tags {:context "codex" :role "type"} :names :camel :unknown :ignore}
  (setv #^ (| int str None) id None)
  (setv #^ (| str None) method None)
  (setv #^ (| OpaqueJson None) params None)
  (setv #^ (| OpaqueJson None) result None)
  (setv #^ (| RpcErrorWire None) error None))

(defwire IdWire
  "id だけを読む入れ子の欄(thread・turn)。"
  {:tags {:context "codex" :role "type"} :names :camel :unknown :ignore}
  (#^ str id))

(defwire ResultWire
  "要求への答えの result のうち、上の層が使う欄(thread/start・thread/resume の thread、turn/start の turn)。どちらも無い答えも在る。"
  {:tags {:context "codex" :role "type"} :names :camel :unknown :ignore}
  (setv #^ (| IdWire None) thread None)
  (setv #^ (| IdWire None) turn None))

(defwire ThreadParamsWire
  "thread/started の params。"
  {:tags {:context "codex" :role "type"} :names :camel :unknown :ignore}
  (#^ IdWire thread))

(defwire HttpFailureWire
  "誤りの種類のうち HTTP の失敗を名乗る物の中身(status は無いこともある)。"
  {:tags {:context "codex" :role "type"} :names :camel :unknown :ignore}
  (setv #^ (| int None) http-status-code None))

(defwire ErrorInfoWire
  "codexErrorInfo の object の形(codex 0.162.1 の CodexErrorInfo — 鍵ちょうど 1 つが誤りの種類を名乗る)。知らない鍵(版で増えた
   種類)と鍵の無い object はこの型に解けず、TurnErrorWire の欄の OpaqueJson の側へ落ちて UnknownValue として運ばれる — 知らない
   種類を「種類なし」に読み替えず、行も落とさない(ターンの終わりを失わない)。"
  {:tags {:context "codex" :role "type"} :names :camel :unknown :reject
   :check [(= 1 (sum (gfor kind [http-connection-failed response-stream-connection-failed response-stream-disconnected
                                 response-too-many-failed-attempts active-turn-not-steerable]
                           (is-not kind None))))]}
  (setv #^ (| HttpFailureWire None) http-connection-failed None)
  (setv #^ (| HttpFailureWire None) response-stream-connection-failed None)
  (setv #^ (| HttpFailureWire None) response-stream-disconnected None)
  (setv #^ (| HttpFailureWire None) response-too-many-failed-attempts None)
  (setv #^ (| OpaqueJson None) active-turn-not-steerable None))

(defwire TurnErrorWire
  "ターンの誤り(error の通知の error と、turn/completed の turn.error)。codexErrorInfo は種類の名の文字列か ErrorInfoWire の object で、
   版で増えた種類も在るので、ここでは中を読まずに OpaqueJson で受け、error-facts-of が順に解く(union に OpaqueJson を混ぜると、何でも
   受ける OpaqueJson の側が既知の値まで取る — pydantic の union の選び方・2026-10-10 の検で実測)。"
  {:tags {:context "codex" :role "type"} :names :camel :unknown :ignore}
  (#^ str message)
  (setv #^ (| OpaqueJson None) codex-error-info None))

(defwire ErrorKindNameWire
  "codexErrorInfo が種類の名の文字列で来た時に、その文字列を解くための包み(error-facts-of が {\"kind\": 値} に包んで解く)。"
  {:tags {:context "codex" :role "type"} :names :camel :unknown :reject}
  (#^ str kind))

(defwire TurnWire
  "turn/started・turn/completed の turn。status は綴りのまま読み、TurnStatus に無い綴り(版で増えた状態)は記録の側で UnknownValue に
   する — 知らない状態でターンの終わりの行を落とさない。"
  {:tags {:context "codex" :role "type"} :names :camel :unknown :ignore}
  (#^ str id)
  (#^ str status)
  (setv #^ (| TurnErrorWire None) error None))

(defwire TurnParamsWire
  "turn/started・turn/completed の params。"
  {:tags {:context "codex" :role "type"} :names :camel :unknown :ignore}
  (#^ str thread-id)
  (#^ TurnWire turn))

(defwire DeltaParamsWire
  "差分の通知(item/agentMessage/delta・item/reasoning/textDelta・item/reasoning/summaryTextDelta)の params。"
  {:tags {:context "codex" :role "type"} :names :camel :unknown :ignore}
  (#^ str thread-id)
  (#^ str turn-id)
  (#^ str item-id)
  (#^ str delta))

(defwire ItemWire
  "item/started・item/completed の item のうち使う欄(種類と id と、答えの全文 — agentMessage だけが text を持つ)。"
  {:tags {:context "codex" :role "type"} :names :camel :unknown :ignore}
  (#^ str type)
  (#^ str id)
  (setv #^ (| str None) text None))

(defwire ItemParamsWire
  "item/started・item/completed の params。"
  {:tags {:context "codex" :role "type"} :names :camel :unknown :ignore}
  (#^ str thread-id)
  (#^ str turn-id)
  (#^ ItemWire item))

(defwire ErrorParamsWire
  "error の通知の params(ターンの誤り — willRetry が偽なら codex はこのターンを繰り返さない)。"
  {:tags {:context "codex" :role "type"} :names :camel :unknown :ignore}
  (#^ str thread-id)
  (#^ str turn-id)
  (#^ bool will-retry)
  (#^ TurnErrorWire error))

(defwire TokenCountWire
  "token の数の組(名乗らない欄は None — 0 を発明しない)。"
  {:tags {:context "codex" :role "type"} :names :camel :unknown :ignore}
  (setv #^ (| int None) total-tokens None)
  (setv #^ (| int None) input-tokens None)
  (setv #^ (| int None) cached-input-tokens None)
  (setv #^ (| int None) output-tokens None)
  (setv #^ (| int None) reasoning-output-tokens None))

(defwire TokenUsageWire
  "thread/tokenUsage/updated の tokenUsage。"
  {:tags {:context "codex" :role "type"} :names :camel :unknown :ignore}
  (#^ TokenCountWire total)
  (#^ TokenCountWire last)
  (setv #^ (| int None) model-context-window None))

(defwire TokenUsageParamsWire
  "thread/tokenUsage/updated の params。"
  {:tags {:context "codex" :role "type"} :names :camel :unknown :ignore}
  (#^ str thread-id)
  (#^ str turn-id)
  (#^ TokenUsageWire token-usage))


;; --- 記録の型(上の層が読む) ---------------------------------------------------------------------

(defrecord UnknownValue
  "この版の語彙に無い値(版で増えた状態・誤りの種類)を、中を読まずに運ぶ: value = codex が出した値そのまま。知らない値を既知の値や
   None に読み替えず、行も落とさない — 上の層が名指して数えられるように。"
  {:tags {:context "codex" :role "type"}}
  (#^ OpaqueJson value))

(defrecord Response
  "要求への答え: id = 要求の id / thread-id = 答えが名乗る thread の id(thread/start・thread/resume)/ turn-id = 答えが名乗るターンの
   id(turn/start)。名乗らない答え(initialize・turn/interrupt)は None。"
  {:tags {:context "codex" :role "type"}}
  (#^ (| int str) id)
  (setv #^ (| str None) thread-id None)
  (setv #^ (| str None) turn-id None))

(defrecord ErrorResponse
  "要求への誤りの答え: id = 要求の id / code = JSON-RPC の誤りの code / message = 誤りの文。"
  {:tags {:context "codex" :role "type"}}
  (#^ (| int str) id)
  (#^ int code)
  (#^ str message))

(defrecord ServerRequest
  "codex からの要求(道具の許可の問いなど): id = 答えに使う id / method = 要求の名 / params = 要求の中身(method ごとに形が違うので
   中を読まずに運ぶ — 答える側が method で選んだ型で解く。無ければ None)。"
  {:tags {:context "codex" :role "type"}}
  (#^ (| int str) id)
  (#^ str method)
  (setv #^ (| OpaqueJson None) params None))

(defrecord ThreadStarted
  "thread が開いた(thread/started)。"
  {:tags {:context "codex" :role "type"}}
  (#^ str thread-id))

(defrecord TurnStarted
  "ターンが始まった(turn/started)。"
  {:tags {:context "codex" :role "type"}}
  (#^ str thread-id)
  (#^ str turn-id))

(defrecord TextDelta
  "答えの文字の途中(item/agentMessage/delta): text = 前の差分の後ろに続く文字。同じ item の差分を順に連ねると、その item の
   AgentMessageDone の text になる。"
  {:tags {:context "codex" :role "type"}}
  (#^ str thread-id)
  (#^ str turn-id)
  (#^ str item-id)
  (#^ str text))

(defrecord ReasoningDelta
  "考えている間の差分: summary = 要約の差分(item/reasoning/summaryTextDelta)なら真、本文の差分(item/reasoning/textDelta)なら偽。"
  {:tags {:context "codex" :role "type"}}
  (#^ str thread-id)
  (#^ str turn-id)
  (#^ str item-id)
  (#^ str text)
  (#^ bool summary))

(defrecord AgentMessageDone
  "答えの全文(item/completed の agentMessage)。"
  {:tags {:context "codex" :role "type"}}
  (#^ str thread-id)
  (#^ str turn-id)
  (#^ str item-id)
  (#^ str text))

(defrecord ItemStarted
  "ターンの中の item が始まった(item/started): item-type = codex の item の種類の名(userMessage・agentMessage・reasoning・
   commandExecution など — 版で増えるので名のまま)。上の層が「考えている」「道具を呼んでいる」を出す材料。"
  {:tags {:context "codex" :role "type"}}
  (#^ str thread-id)
  (#^ str turn-id)
  (#^ str item-id)
  (#^ str item-type))

(defrecord ItemDone
  "答えの全文でない item が終わった(item/completed の agentMessage 以外): item-type は ItemStarted と同じ。"
  {:tags {:context "codex" :role "type"}}
  (#^ str thread-id)
  (#^ str turn-id)
  (#^ str item-id)
  (#^ str item-type))

(defrecord TokenCount
  "token の数の組(名乗らない欄は None)。"
  {:tags {:context "codex" :role "type"}}
  (setv #^ (| int None) total-tokens None)
  (setv #^ (| int None) input-tokens None)
  (setv #^ (| int None) cached-input-tokens None)
  (setv #^ (| int None) output-tokens None)
  (setv #^ (| int None) reasoning-output-tokens None))

(defrecord TokenUsage
  "使った token の数(thread/tokenUsage/updated): last = この呼びの分 / total = thread の累積 / model-context-window = model の窓の
   大きさ(名乗らなければ None)。"
  {:tags {:context "codex" :role "type"}}
  (#^ str thread-id)
  (#^ str turn-id)
  (#^ TokenCount last)
  (#^ TokenCount total)
  (setv #^ (| int None) model-context-window None))

(defrecord TurnError
  "ターンの誤り(error の通知): message = 誤りの文 / error-kind = codex の誤りの種類の名(codexErrorInfo — 文字列の値か object の鍵・
   この版に無い形は UnknownValue・名乗らなければ None)/ http-status = 上流の HTTP の status(名乗らなければ None)/ will-retry =
   codex がこのターンを繰り返すか。"
  {:tags {:context "codex" :role "type"}}
  (#^ str thread-id)
  (#^ str turn-id)
  (#^ str message)
  (#^ (| str UnknownValue None) error-kind)
  (#^ (| int None) http-status)
  (#^ bool will-retry))

(defrecord TurnEnded
  "ターンの終わり(turn/completed — ターンごとにちょうど 1 つ): status = 終わりの状態(この版に無い綴りは UnknownValue — 知らない状態でも
   終わりは届く)/ error-message・error-kind・http-status = 失敗の終わりの誤り(turn.error — 無ければ None・種類は TurnError と同じ)。"
  {:tags {:context "codex" :role "type"}}
  (#^ str thread-id)
  (#^ str turn-id)
  (#^ (| TurnStatus UnknownValue) status)
  (setv #^ (| str None) error-message None)
  (setv #^ (| str UnknownValue None) error-kind None)
  (setv #^ (| int None) http-status None))

(defrecord Other
  "語彙の外の通知: method = 通知の名(中身は持たない — 発明しない)。"
  {:tags {:context "codex" :role "type"}}
  (#^ str method))

(defrecord Unparsed
  "読めない行: method = 読めた method の名(JSON として読めない行・外枠の形の違う行は空)/ reason = 読めない訳(どの型の・どの欄が)。"
  {:tags {:context "codex" :role "type"}}
  (#^ str method)
  (#^ str reason))

(val CodexLine (| Response ErrorResponse ServerRequest ThreadStarted TurnStarted TextDelta ReasoningDelta AgentMessageDone
                   ItemStarted ItemDone TokenUsage TurnError TurnEnded Other Unparsed))

(defrecord ErrorFacts
  "codexErrorInfo から読んだ誤りの種類の名(この版に無い形は UnknownValue)と HTTP の status(どちらも名乗らなければ None)。"
  {:tags {:context "codex" :role "type"}}
  (#^ (| str UnknownValue None) kind)
  (#^ (| int None) http-status))


;; --- 分類 -------------------------------------------------------------------------------------

(defk reason-of [#^ Malformed malformed]
  {:pre [(: malformed Malformed)] :post [(: % str)] :tags {:context "codex" :role "foundation"}}
  "形の合わない所を Unparsed の訳の 1 文にするため(型の名と、欄ごとの場所と訳)。"
  (.format "{}: {}" malformed.wire-type
           (.join "; " (gfor problem malformed.fields (.format "{} {}" (or problem.field "(値の全体)") problem.reason)))))


(defk error-facts-of [info]
  {:pre [(: info (| OpaqueJson None))] :post [(: % ErrorFacts)] :tags {:context "codex" :role "foundation"}}
  "codexErrorInfo(種類の名の文字列か、鍵ちょうど 1 つの object)から、誤りの種類の名と HTTP の status を読むため。順に解く: この版の
   object の形 → 種類の名の文字列 → どちらでもない形(版で増えた object)は UnknownValue。"
  (when (is info None)
    (return (ErrorFacts :kind None :http-status None)))
  (<- known (parse ErrorInfoWire info))
  (when (isinstance known ErrorInfoWire)
    (<- facts (known-error-facts-of known))
    (return facts))
  ;; 文字列かどうかは、{"kind": 値} に包んで解いて確かめる(OpaqueJson の text は最小の直列化の JSON の値そのもの)。
  (<- named (parse ErrorKindNameWire (OpaqueJson.from-text (.format "{{\"kind\":{}}}" info.text))))
  (if (isinstance named ErrorKindNameWire)
      (ErrorFacts :kind named.kind :http-status None)
      (ErrorFacts :kind (UnknownValue :value info) :http-status None)))


(defk known-error-facts-of [#^ ErrorInfoWire info]
  {:pre [(: info ErrorInfoWire)] :post [(: % ErrorFacts)] :tags {:context "codex" :role "foundation"}}
  "この版の codexErrorInfo の object(鍵ちょうど 1 つ)から、鍵の名を種類に、中の httpStatusCode を HTTP の status にするため。"
  (cond
    (is-not info.response-too-many-failed-attempts None)
    (ErrorFacts :kind "responseTooManyFailedAttempts" :http-status info.response-too-many-failed-attempts.http-status-code)
    (is-not info.http-connection-failed None)
    (ErrorFacts :kind "httpConnectionFailed" :http-status info.http-connection-failed.http-status-code)
    (is-not info.response-stream-connection-failed None)
    (ErrorFacts :kind "responseStreamConnectionFailed" :http-status info.response-stream-connection-failed.http-status-code)
    (is-not info.response-stream-disconnected None)
    (ErrorFacts :kind "responseStreamDisconnected" :http-status info.response-stream-disconnected.http-status-code)
    ;; 残りは activeTurnNotSteerable だけ(ErrorInfoWire の :check が鍵ちょうど 1 つを確かめている)。
    True (ErrorFacts :kind "activeTurnNotSteerable" :http-status None)))


(defk token-count-of [#^ TokenCountWire wire]
  {:pre [(: wire TokenCountWire)] :post [(: % TokenCount)] :tags {:context "codex" :role "foundation"}}
  "wire の token の数の組を、上の層が読む記録の型へ写すため。"
  (TokenCount :total-tokens wire.total-tokens :input-tokens wire.input-tokens :cached-input-tokens wire.cached-input-tokens
              :output-tokens wire.output-tokens :reasoning-output-tokens wire.reasoning-output-tokens))


(defk turn-status-of [#^ str spelled]
  {:pre [(: spelled str)] :post [(: % (| TurnStatus UnknownValue))] :tags {:context "codex" :role "foundation"}}
  "turn.status の綴りを TurnStatus にするため。この版に無い綴りは UnknownValue(既知の状態に読み替えない)。"
  (if (in spelled (frozenset (gfor known TurnStatus known.value)))
      (TurnStatus spelled)
      (UnknownValue :value (OpaqueJson.of spelled))))


(defk turn-ended-of [#^ TurnParamsWire params]
  {:pre [(: params TurnParamsWire)] :post [(: % TurnEnded)] :tags {:context "codex" :role "foundation"}}
  "turn/completed を、状態と失敗の誤りを持つターンの終わりにするため(知らない状態・誤りの種類でも終わりは作る)。"
  (val failure params.turn.error)
  (<- facts (error-facts-of (if (is failure None) None failure.codex-error-info)))
  (<- status (turn-status-of params.turn.status))
  (TurnEnded :thread-id params.thread-id :turn-id params.turn.id :status status
             :error-message (if (is failure None) None failure.message) :error-kind facts.kind :http-status facts.http-status))


(defk turn-error-of [#^ ErrorParamsWire params]
  {:pre [(: params ErrorParamsWire)] :post [(: % TurnError)] :tags {:context "codex" :role "foundation"}}
  "error の通知を、誤りの文・種類・HTTP の status を持つターンの誤りにするため。"
  (<- facts (error-facts-of params.error.codex-error-info))
  (TurnError :thread-id params.thread-id :turn-id params.turn-id :message params.error.message :error-kind facts.kind
             :http-status facts.http-status :will-retry params.will-retry))


(defk token-usage-of [#^ TokenUsageParamsWire params]
  {:pre [(: params TokenUsageParamsWire)] :post [(: % TokenUsage)] :tags {:context "codex" :role "foundation"}}
  "thread/tokenUsage/updated を、この呼びの分と累積を分けた使用量にするため。"
  (<- this-call (token-count-of params.token-usage.last))
  (<- whole-thread (token-count-of params.token-usage.total))
  (TokenUsage :thread-id params.thread-id :turn-id params.turn-id :last this-call :total whole-thread
              :model-context-window params.token-usage.model-context-window))


(defk item-started-of [#^ ItemParamsWire params]
  {:pre [(: params ItemParamsWire)] :post [(: % ItemStarted)] :tags {:context "codex" :role "foundation"}}
  "item/started を、上の層が「考えている」「道具を呼んでいる」を出す材料(item の種類と id)にするため。"
  (ItemStarted :thread-id params.thread-id :turn-id params.turn-id :item-id params.item.id :item-type params.item.type))


(defk item-done-of [#^ ItemParamsWire params]
  {:pre [(: params ItemParamsWire)] :post [(: % (| AgentMessageDone ItemDone Unparsed))] :tags {:context "codex" :role "foundation"}}
  "item/completed のうち答えの全文(agentMessage)を AgentMessageDone に、ほかの種類の item を ItemDone にするため。全文の無い
   agentMessage は読めない行(答えの全文を空と読み替えない)。"
  (match params.item.type
    "agentMessage" (if (is params.item.text None)
                       (Unparsed :method "item/completed" :reason (.format "agentMessage の item {} に text が無い" params.item.id))
                       (AgentMessageDone :thread-id params.thread-id :turn-id params.turn-id :item-id params.item.id
                                         :text params.item.text))
    _ (ItemDone :thread-id params.thread-id :turn-id params.turn-id :item-id params.item.id :item-type params.item.type)))


(defk text-delta-of [#^ DeltaParamsWire params]
  {:pre [(: params DeltaParamsWire)] :post [(: % TextDelta)] :tags {:context "codex" :role "foundation"}}
  "item/agentMessage/delta を答えの文字の途中にするため。"
  (TextDelta :thread-id params.thread-id :turn-id params.turn-id :item-id params.item-id :text params.delta))


(defk reasoning-text-of [#^ DeltaParamsWire params]
  {:pre [(: params DeltaParamsWire)] :post [(: % ReasoningDelta)] :tags {:context "codex" :role "foundation"}}
  "item/reasoning/textDelta を考えの本文の差分にするため。"
  (ReasoningDelta :thread-id params.thread-id :turn-id params.turn-id :item-id params.item-id :text params.delta :summary False))


(defk reasoning-summary-of [#^ DeltaParamsWire params]
  {:pre [(: params DeltaParamsWire)] :post [(: % ReasoningDelta)] :tags {:context "codex" :role "foundation"}}
  "item/reasoning/summaryTextDelta を考えの要約の差分にするため。"
  (ReasoningDelta :thread-id params.thread-id :turn-id params.turn-id :item-id params.item-id :text params.delta :summary True))


(defk thread-started-of [#^ ThreadParamsWire params]
  {:pre [(: params ThreadParamsWire)] :post [(: % ThreadStarted)] :tags {:context "codex" :role "foundation"}}
  "thread/started を thread の id の記録にするため。"
  (ThreadStarted :thread-id params.thread.id))


(defk turn-started-of [#^ TurnParamsWire params]
  {:pre [(: params TurnParamsWire)] :post [(: % TurnStarted)] :tags {:context "codex" :role "foundation"}}
  "turn/started をターンの始まりの記録にするため。"
  (TurnStarted :thread-id params.thread-id :turn-id params.turn.id))


(defk parsed-into [#^ str method #^ type wire-type convert params]
  {:pre [(: method str) (: wire-type type) (: convert Callable) (: params (| OpaqueJson None))] :post [(: % CodexLine)]
   :tags {:context "codex" :role "foundation"}}
  "語彙の中の通知の params を、その method の wire の型で解いて記録へ写すため(convert = 写す defk)。params が無い・形が合わない
   通知は、method を名乗る Unparsed にする。"
  (when (is params None)
    (return (Unparsed :method method :reason "params が無い")))
  (<- parsed (parse wire-type params))
  (when (isinstance parsed Malformed)
    (<- reason (reason-of parsed))
    (return (Unparsed :method method :reason reason)))
  (<- record (convert parsed))
  record)


(defk notification-of [#^ str method params]
  {:pre [(: method str) (: params (| OpaqueJson None))] :post [(: % CodexLine)] :tags {:context "codex" :role "foundation"}}
  "通知を method で選んだ型で解き、記録にするため。語彙の外は Other(差分の 3 つは wire の型が同じで、写し方だけが違う)。"
  (match method
    "thread/started" (do (<- record (parsed-into method ThreadParamsWire thread-started-of params)) record)
    "turn/started" (do (<- record (parsed-into method TurnParamsWire turn-started-of params)) record)
    "turn/completed" (do (<- record (parsed-into method TurnParamsWire turn-ended-of params)) record)
    "item/agentMessage/delta" (do (<- record (parsed-into method DeltaParamsWire text-delta-of params)) record)
    "item/reasoning/textDelta" (do (<- record (parsed-into method DeltaParamsWire reasoning-text-of params)) record)
    "item/reasoning/summaryTextDelta" (do (<- record (parsed-into method DeltaParamsWire reasoning-summary-of params)) record)
    "item/started" (do (<- record (parsed-into method ItemParamsWire item-started-of params)) record)
    "item/completed" (do (<- record (parsed-into method ItemParamsWire item-done-of params)) record)
    "error" (do (<- record (parsed-into method ErrorParamsWire turn-error-of params)) record)
    "thread/tokenUsage/updated" (do (<- record (parsed-into method TokenUsageParamsWire token-usage-of params)) record)
    _ (Other :method method)))


(defk response-of [#^ EnvelopeWire envelope]
  {:pre [(: envelope EnvelopeWire)] :post [(: % (| Response Unparsed))] :tags {:context "codex" :role "foundation"}}
  "要求への答えを、要求の id と答えが名乗る thread・ターンの id の記録にするため。result の無い答え(error も無い)は形の合わない行。"
  (when (is envelope.result None)
    (return (Unparsed :method "" :reason (.format "id {} の答えに result も error も無い" envelope.id))))
  (<- parsed (parse ResultWire envelope.result))
  (when (isinstance parsed Malformed)
    (<- reason (reason-of parsed))
    (return (Unparsed :method "" :reason reason)))
  (Response :id envelope.id
            :thread-id (if (is parsed.thread None) None parsed.thread.id)
            :turn-id (if (is parsed.turn None) None parsed.turn.id)))


(defk classify-line [#^ str line]
  {:pre [(: line str)] :post [(: % CodexLine)] :tags {:context "codex" :role "foundation"}}
  "app-server の stdout の 1 行を、上の層が読む記録にするため(JSON の境目の 1 か所)。"
  (<- envelope (parse-json EnvelopeWire line))
  (when (isinstance envelope Malformed)
    (<- reason (reason-of envelope))
    (return (Unparsed :method "" :reason reason)))
  (cond
    (and (is-not envelope.id None) (is-not envelope.method None))
    (ServerRequest :id envelope.id :method envelope.method :params envelope.params)
    (and (is-not envelope.id None) (is-not envelope.error None))
    (ErrorResponse :id envelope.id :code envelope.error.code :message envelope.error.message)
    (is-not envelope.id None)
    (do (<- answer (response-of envelope)) answer)
    (is-not envelope.method None)
    (do (<- record (notification-of envelope.method envelope.params)) record)
    True (Unparsed :method "" :reason "id も method も無い")))
