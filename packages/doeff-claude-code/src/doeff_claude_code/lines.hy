;;; claude の print mode の stdout の 1 行の型と、手番の終わりの型(設計 4.2 — この package が持つ型)。
;;;
;;; 分類(classify-record)は純関数: stream-json の 1 行(JSON の object)→ ClaudeLineKind。語彙の外の行は捨てずに
;;; Other { type, subtype } へ名前だけ持つ(発明しない)。1 行の逐語は ClaudeStreamLine.raw に残る。
;;;
;;; JSON の境界はこの file の 1 か所(parse-record と classify-*、transcript の額の行を読む recorded-cost)。状態機械(dialogue.hy)も
;;; 上の層も、分類した型だけを読む — 生の dict を読み直す 2 か所目を作らない。
(require doeff-hy.macros [defk val <-])
(require doeff-hy.record [defenum defrecord])
(import dataclasses [dataclass field fields])
(import datetime [datetime])
(import enum [StrEnum])
(import json)
(import math)
(import doeff [run])
(import doeff_hy.frozen [FrozenMap freeze-json frozen-json-object])
(import doeff_claude_code.values [ClaudeTurn])


;; --- 消費の token ------------------------------------------------------------------------------

(defclass [(dataclass :frozen True)] Usage []
  "手番の終わり(result の行)の usage。CLI が名乗った数だけを持ち、名乗らない欄は None(0 を発明しない)。
   result の行の usage はその CLI の手番 1 回分で、累積ではない(実測 2.1.283・#883: 同じ process の 2 つ目の
   result の行は 2 回目の分だけを名乗る)。
   欄: input-tokens = input_tokens / output-tokens = output_tokens / cache-creation-input-tokens = cache_creation_input_tokens /
   cache-read-input-tokens = cache_read_input_tokens / cache-creation-5m-input-tokens・cache-creation-1h-input-tokens =
   cache_creation.ephemeral_5m_input_tokens・ephemeral_1h_input_tokens / web-search-requests = server_tool_use.web_search_requests /
   service-tier = service_tier。ここに無い欄(CLI の版で増える欄)は捨てる — 要る欄はここへ足す。"
  (setv #^ (| int None) input-tokens None)
  (setv #^ (| int None) output-tokens None)
  (setv #^ (| int None) cache-creation-input-tokens None)
  (setv #^ (| int None) cache-read-input-tokens None)
  (setv #^ (| int None) cache-creation-5m-input-tokens None)
  (setv #^ (| int None) cache-creation-1h-input-tokens None)
  (setv #^ (| int None) web-search-requests None)
  (setv #^ (| str None) service-tier None)
  (defn __add__ [self #^ "Usage" other]
    "2 つの result の行の usage の和。1 つの host の手番に result の行が 2 つ以上ある時(読まれていない注入を CLI が次の CLI の手番として
     走らせた・CLI が自分で起こした手番)に、手番の usage を額と同じ母集団(手番に読んだ全部の行)で数えるため(状態機械 dialogue.hy)。
     欄ごとに、どちらも名乗らなければ None、片方だけなら名乗った数(0 を発明しない)。service-tier は後の行の物(無ければ前の行の物)。"
    (when (not (isinstance other Usage)) (return NotImplemented))
    (Usage #** (dfor usage-field (fields self)
                     :setv before (getattr self usage-field.name)
                     :setv after (getattr other usage-field.name)
                     usage-field.name (cond
                                        (is after None) before
                                        (is before None) after
                                        (= usage-field.name "service_tier") after
                                        True (+ before after))))))


;; --- 行の種類 ---------------------------------------------------------------------------------

(defrecord TimedPhase
  "CLI が名乗った計時の区間 1 つ(環境変数 CLAUDE_CODE_EMIT_STARTUP_TIMING を付けて起こした process だけが出す — argv.hy の
   process-env)。name = 区間の名(CLI の鍵のまま — 例 node_boot_ms・input_hooks)/ ms = 区間の長さのミリ秒 / start-ms = 区間の
   始まり(起動の区間だけが名乗る — CLI の process の時刻の起点 startup-origin-ms からのミリ秒。要求までの区間は始まりを名乗らないので
   None)。送ってから最初の字までのうち、CLI の起動と要求を送るまでの秒を区間ごとに割るため(#3855)。"
  (#^ str name)
  (#^ int ms)
  (setv #^ (| int None) start-ms None))

(defclass [(dataclass :frozen True)] Init []
  "system/init: 会話の id・CLI の能力の宣言・model・許可のモード・MCP の server の名 /
   startup-phases = startup_timing.phases の起動の区間(始まりは startup_timing.phase_start_ms — 区間は入れ子になる)/
   startup-origin-ms = startup_timing.time_origin_ms(区間の始まりの起点の壁の時刻・epoch ミリ秒を床へ)。startup_timing は
   CLAUDE_CODE_EMIT_STARTUP_TIMING を付けた process の最初の init の行だけが持つ — 無ければ空と None(実測 2.1.292・#3855)。"
  (#^ str session-id)
  (setv #^ (get tuple #(str ...)) capabilities #())
  (setv #^ str model "")
  (setv #^ str permission-mode "")
  (setv #^ (get tuple #(str ...)) mcp-servers #())
  (setv #^ (get tuple #(TimedPhase ...)) startup-phases #())
  (setv #^ (| int None) startup-origin-ms None))

(defclass [(dataclass :frozen True)] ToolCall []
  "assistant の message の tool_use の block 1 つ: id = block の id(続く user の行の tool_result の tool_use_id が同じ id で
   結果を名指す)/ name = 道具の名。どちらも空でない文字列(空の欄は分類の段で Other として断る — 空文字で通さない)/
   input = 道具の呼びの命令(block の input の JSON の object のまま — 鍵の集合は道具ごとに開いているので、PermissionRequested.input と
   同じく深く凍らせた写像。input の無い・写像でない block は分類の段で Other として断る)。#3744。"
  (#^ str id)
  (#^ str name)
  (setv #^ FrozenMap input (field :default-factory FrozenMap))
  (defn __post_init__ [self]
    (when (not (and (isinstance self.id str) self.id))
      (raise (ValueError (.format "ToolCall.id は空でない文字列: {!r}" self.id))))
    (when (not (and (isinstance self.name str) self.name))
      (raise (ValueError (.format "ToolCall.name は空でない文字列: {!r}" self.name))))
    (object.__setattr__ self "input" (frozen-json-object self.input "ToolCall.input"))))

(defclass [(dataclass :frozen True)] AssistantMessage []
  "assistant の message: 本文の text の block を連ねたものと、呼んだ道具(tool_use の block の id と名の組)の列 /
   usage = message.usage(この行を出した API の呼び 1 回の消費 — 1 つの呼びは content の block ごとに行を出し、どの行も同じ usage を
   名乗る。出力の token は呼びの途中の値。usage の object が無ければ None)/ model = message.model(無ければ None)/
   parent-tool-use-id = subagent の行なら親の呼び(道具の呼び)の id、本体の会話の行は None(行の最上位の parent_tool_use_id)。#3744。"
  (setv #^ str text "")
  (setv #^ (get tuple #(ToolCall ...)) tool-calls #())
  (setv #^ (| Usage None) usage None)
  (setv #^ (| str None) model None)
  (setv #^ (| str None) parent-tool-use-id None)
  ;; error = 行の最上位の error(CLI が API の誤りを答えの代わりに出した時の語 — 例 rate_limit: 口座の限度に当たった・#3983。無ければ None)。
  (setv #^ (| str None) error None))

(defclass [(dataclass :frozen True)] ToolAnswer []
  "user の行の tool_result の block 1 つ(道具の結果 1 つ): id = 答えた呼びの id(tool_use_id — 前の ToolCall.id と同じ・空でない文字列)/
   text = 結果の中身の本文(content が文字列ならそのまま、block の列なら text の block の本文を改行で連ねた物、content が無ければ空)/
   is-error = 道具が誤りで終えたか(block の is_error が真の時だけ真)/ non-text-kinds = content の列にあった text でない block
   (画像など)の種類の名を出た順に(本文に入らない中身が在ったことを黙って消さない — type の無い block は空の名)。#3744。"
  (#^ str id)
  (#^ str text)
  (#^ bool is-error)
  (setv #^ (get tuple #(str ...)) non-text-kinds #())
  (defn __post_init__ [self]
    (when (not (and (isinstance self.id str) self.id))
      (raise (ValueError (.format "ToolAnswer.id は空でない文字列: {!r}" self.id))))
    (when (not (isinstance self.text str))
      (raise (TypeError (.format "ToolAnswer.text は文字列: {!r}" self.text))))
    (when (not (isinstance self.non-text-kinds tuple))
      (raise (TypeError (.format "ToolAnswer.non_text_kinds は tuple: {!r}" self.non-text-kinds))))))

(defclass [(dataclass :frozen True)] ToolResult []
  "user の行の tool_result(道具の結果が model へ返った): answers = 結果ごとの答え(block の順 — 答えた呼びの id は各答えの id)。"
  (#^ (get tuple #(ToolAnswer ...)) answers))

;; stream_event の delta の種類(#3746 (a) — 閉じた語彙): TEXT = text_delta(本文の差分)/ THINKING = thinking_delta(考えている間の
;; 差分)/ TOOL-INPUT = input_json_delta(道具の呼びの命令を書いている間の差分)/ OTHER = 種類の名の在るほかの delta(signature_delta・
;; citations_delta・CLI の版で増える物)/ NO-DELTA = 種類の名の在る delta を持たない stream_event(message_start・content_block_start /
;; stop・message_delta)。
(defenum DeltaKind TEXT THINKING TOOL-INPUT OTHER NO-DELTA)

(defclass [(dataclass :frozen True)] PartialMessage []
  "stream_event(--include-partial-messages)。text-delta = text_delta なら本文の差分(ほかは空)/ delta = delta の種類(DeltaKind —
   考えている間や道具の命令を書いている間の行を、本文の空の行と分けて数えるため)/ thinking-delta = thinking_delta なら考えの差分の
   文字列(ほかは空 — 上の層が本文の前に「考えている」と分かる表示を出すため・#3789)/ tool-input-delta = input_json_delta なら道具の
   命令の切れ端(partial_json — JSON の途中で単独では読めない文字列・ほかは空)/ tool-start = content_block_start の tool_use なら呼びの
   id と道具の名(ToolCall — 命令はまだ無い・ほかは None)。後の 2 つは上の層が「担当は <道具> の命令を書いている」と出すため
   (#3974 の 3)。本文の差分は種類 TEXT の行だけ、考えの差分は種類 THINKING の行だけ、命令の切れ端は種類 TOOL-INPUT の
   行だけが持つ — 作り手が種類を名乗り忘れた行を作る時に断る。ttft-ms = 行の最上位の ttft_ms(message_start の行だけが名乗る —
   CLI が API へ要求を送ってから message_start を受けるまでのミリ秒。実測 2.1.292 で result の ttft_stream_ms − time_to_request_ms と
   1 ms 以内で同じ・名乗らなければ None — #3855)。"
  (setv #^ str text-delta "")
  (setv #^ DeltaKind delta DeltaKind.NO-DELTA)
  (setv #^ str thinking-delta "")
  (setv #^ str tool-input-delta "")
  (setv #^ (| ToolCall None) tool-start None)
  (setv #^ (| int None) ttft-ms None)
  (defn __post_init__ [self]
    (when (and self.tool-input-delta (!= self.delta DeltaKind.TOOL-INPUT))
      (raise (ValueError (.format "PartialMessage の命令の切れ端は種類 TOOL-INPUT の行だけ: delta {!r}" self.delta))))
    (when (and self.text-delta (!= self.delta DeltaKind.TEXT))
      (raise (ValueError (.format "PartialMessage の本文の差分は種類 TEXT の行だけ: delta {!r}" self.delta))))
    (when (and self.thinking-delta (!= self.delta DeltaKind.THINKING))
      (raise (ValueError (.format "PartialMessage の考えの差分は種類 THINKING の行だけ: delta {!r}" self.delta))))))

(defclass [(dataclass :frozen True)] ThinkingTokens []
  (setv #^ int estimated 0))

(setv INPUT-FATES #("queued" "started" "completed" "cancelled" "discarded" "refused"))
(setv INPUT-FATE-TERMINAL #("completed" "cancelled" "discarded" "refused"))

(defclass [(dataclass :frozen True)] InputFate []
  "入力の行の運命(command_lifecycle)。state は閉語彙 INPUT-FATES。"
  (#^ str ref)
  (#^ str state))

(defclass [(dataclass :frozen True)] PermissionRequested []
  "道具の前の許可の問い(control_request can_use_tool)。答えは ClaudeAnswerPermission。
   input = 道具の入力(鍵の集合は道具ごとに開いているので凍らせた写像)/ suggestions = CLI の提案の JSON の列(凍らせる)。"
  (#^ str request-id)
  (#^ str tool-name)
  (setv #^ FrozenMap input (field :default-factory FrozenMap))
  (setv #^ (get tuple #(object ...)) suggestions #())
  (defn __post_init__ [self]
    (object.__setattr__ self "input" (frozen-json-object self.input "PermissionRequested.input"))
    (when (not (isinstance self.suggestions tuple))
      (raise (TypeError (.format "PermissionRequested.suggestions は tuple: {!r}" self.suggestions))))
    (object.__setattr__ self "suggestions" (freeze-json self.suggestions))))

(defclass [(dataclass :frozen True)] ControlResponse []
  "control_request への CLI の答え(control_response)。request-id = 結ぶ求めの id / subtype = success | error /
   still-queued = interrupt の答えが名指した、生き残る注入の ref(名乗らなければ空)。"
  (#^ str request-id)
  (#^ str subtype)
  (setv #^ (get tuple #(str ...)) still-queued #()))

;; hook の行の段階(閉じた語彙): STARTED = system/hook_started(hook が動き始めた)/ RESPONSE = system/hook_response(hook が応答した)。
;; 途中経過の行(hook_progress)はこの語彙に入れない(Other のまま)。
(defenum HookPhase STARTED RESPONSE)

(defrecord HookNotice
  "CLI が hook を実行した通知の行(system/hook_started・system/hook_response): event = hook のイベント名(hook_event — この欄の無い版は
   hook_name の「:」の前)/ phase = 段階(HookPhase)/ name = hook の名前(hook_name — 「イベント名:matcher」・無ければ空)。入力を
   書かずに事前起動した process が最初の入力の前に出してよい行を、イベント名で絞るため(dialogue.hy の quiet-before-first-input)。"
  (#^ str event)
  (#^ HookPhase phase)
  (#^ str name))

(defclass [(dataclass :frozen True)] TaskEvent []
  "CLI の道具の task の開始と報せ(system/task_started・task_notification)。"
  (#^ str task-id)
  (#^ str status))

(defclass [(dataclass :frozen True)] RateLimit []
  "rate_limit_event の行: window = rateLimitType / utilization = その窓の使った割合 / resets-at = resetsAt(epoch 秒)/ status = 限度の
   答えの語(allowed・allowed_warning・rejected — 名乗らなければ空。rejected = 口座の限度に当たって要求が拒まれた・#3983)。"
  (#^ str window)
  (setv #^ (| float None) utilization None)
  (setv #^ (| int None) resets-at None)
  (setv #^ str status ""))

;; 限度の答えの語のうち、口座の限度に当たって要求が拒まれた事を名乗る語(#3983)。
(setv RATE-LIMIT-REJECTED "rejected")
;; assistant の行の error のうち、口座の限度に当たった事を名乗る語(#3983)。
(setv ASSISTANT-ERROR-RATE-LIMIT "rate_limit")

(defclass [(dataclass :frozen True)] AccountLimitHit []
  "手番が口座の限度に当たった事実(#3983 — 上の層が「この口座の枠が尽きた」と知って、枠の残る口座へ付け替えるため)。
   window = 尽きた限度の種類(rateLimitType — 例 five_hour。名乗らなければ None)/ resets-at = 枠が戻る刻(epoch 秒・名乗らなければ None)/
   text = CLI が答えの代わりに出した限度の文(例「You've hit your session limit · resets 7am (UTC)」— 読めなければ空)。
   どの口座かは CLI の行に無い — 手番を起こした上の層が知っている。"
  (setv #^ (| str None) window None)
  (setv #^ (| int None) resets-at None)
  (setv #^ str text ""))

;; assistant の行の error のうち、口座の側が要求を断った事を名乗る語(閉じた集まり — ここ 1 か所)。oauth_org_not_allowed = 口座の組織が
;; Claude Code での subscription の利用を止めている(本番 2026-10-08 22:32 の口座 cryptic-2・apiErrorStatus 403)/ authentication_failed =
;; 口座の資格が通らない / billing_error = 口座の支払いの側の断り。語は CLI の型 SDKAssistantMessageError の語のまま。rate_limit(口座の
;; 限度)はこの集まりに入れない — 限度は AccountLimitHit の道。
(val ACCOUNT-REFUSAL-ERRORS #("oauth_org_not_allowed" "authentication_failed" "billing_error"))

(defrecord AccountRefusalHit
  "手番の要求を口座の側が断った事実(上の層が「この口座は使えない」と知って、ほかの口座へ付け替えるため — 限度で尽きた
   AccountLimitHit とは別の事実)。error = CLI が本体の assistant の行の最上位の error で名乗った語そのまま(ACCOUNT-REFUSAL-ERRORS の
   語だけ — 外の語は作る時に断る)/ text = CLI が答えの代わりに出した断りの文(例「Your organization has disabled Claude subscription
   access for Claude Code · …」— 読めなければ空)。どの口座かは CLI の行に無い — 手番を起こした上の層が知っている。"
  {:check [(in error ACCOUNT-REFUSAL-ERRORS)]}
  (#^ str error)
  (#^ str text))

(defclass [(dataclass :frozen True)] ModelWindow []
  "result の行の modelUsage の model 1 つの窓(会話の context の大きさを上限と比べるため — #3744): model = model の名(modelUsage の鍵)/
   context-window = contextWindow / max-output-tokens = maxOutputTokens(名乗らない欄は None — 0 を発明しない)。"
  (#^ str model)
  (setv #^ (| int None) context-window None)
  (setv #^ (| int None) max-output-tokens None))

(defk merged-windows [#^ tuple earlier #^ tuple later]
  {:pre [(: earlier tuple) (: later tuple)] :post [(: % (get tuple #(ModelWindow ...)))] :tags {:context "claude-code" :role "foundation"}}
  "1 つの host の手番に読んだ result の行の窓を 1 つの列にするため(状態機械と fake が同じ規則で数える): model ごとに 1 つ、並びは最初に
   名乗った順、値は後の行が名乗った物。"
  (val by-model (dict (gfor window (+ earlier later) #(window.model window))))
  (tuple (.values by-model)))

(defrecord RequestTiming
  "result の行の計時の欄。どれも CLI の手番の時計が動き始めた刻(入力を読んだ刻)からのミリ秒で、名乗らない欄は None(0 を発明しない)。
   time-to-request-ms = time_to_request_ms(model へ要求を送るまで — 入力ごとの hook を含む)/ ttft-stream-ms = ttft_stream_ms
   (message_start を受けるまで)/ first-content-frame-ms = first_content_frame_ms / ttft-ms = ttft_ms / duration-ms = duration_ms /
   duration-api-ms = duration_api_ms(API の呼びの合計)/ request-phases = time_to_request_phases_ms の区間(和が time-to-request-ms
   ちょうど — 例 input_hooks・system_prompt。CLAUDE_CODE_EMIT_STARTUP_TIMING を付けた process だけが出す)。欄の名は CLI のまま
   (実測 2.1.292・#3855)。"
  (setv #^ (| int None) time-to-request-ms None)
  (setv #^ (| int None) ttft-stream-ms None)
  (setv #^ (| int None) first-content-frame-ms None)
  (setv #^ (| int None) ttft-ms None)
  (setv #^ (| int None) duration-ms None)
  (setv #^ (| int None) duration-api-ms None)
  (setv #^ (get tuple #(TimedPhase ...)) request-phases #()))

(defclass [(dataclass :frozen True)] TurnResult []
  "result の行(CLI の手番の終わり)。host の手番の終わりかどうかは状態機械(dialogue.hy)が決める。
   origin-kind = origin.kind(CLI が自分で起こした手番の印)/ result-text = result の本文 /
   usage = 消費の token(この CLI の手番の分)/
   cost-usd = total_cost_usd — 会話の累積の額(USD)で、この行の手番だけの額ではない(実測 2.1.283・#883: 同じ process の
   2 つ目の result の行は 1 つ目の額との和を名乗り、--resume で起こした process は、前の process が降りる時に transcript へ記した額から
   数え続ける — --fork-session の枝も親の transcript の額から数える)。手番の額へ直すのは状態機械(dialogue.hy)/
   api-error-status = API の誤りの HTTP status / input-refs = user_message_uuids(名乗らなければ空)/
   model-windows = modelUsage の model ごとの窓(ModelWindow の列・object の鍵の順 — 無ければ空)/
   timing = 計時の欄(RequestTiming)。"
  (#^ str subtype)
  (#^ bool is-error)
  (setv #^ str terminal-reason "")
  (setv #^ str origin-kind "")
  (setv #^ str result-text "")
  (setv #^ Usage usage (field :default-factory Usage))
  (setv #^ (| float None) cost-usd None)
  (setv #^ (| int None) api-error-status None)
  (setv #^ (get tuple #(str ...)) input-refs #())
  (setv #^ (get tuple #(ModelWindow ...)) model-windows #())
  (setv #^ RequestTiming timing (field :default-factory RequestTiming)))

;; Stop hook が答えを差し戻した時に CLI が手番へ注入する行の本文の頭(実測 CLI 2.1.292・#4020)。
(val STOP-HOOK-FEEDBACK-HEAD "Stop hook feedback:")

(defrecord StopHookFeedback
  "Stop hook が手番の答えを差し戻した事実(#4020): CLI は差し戻しの理由を手番へ注入して答え直させる — isSynthetic の user の行で、本文の
   text の block が STOP-HOOK-FEEDBACK-HEAD で始まる(実測 CLI 2.1.292・旗なしの既定の出力。同じ時に出る system/notification の行
   key stop-hook-error は印に使わない — 差し戻し以外の hook の誤りでも出るかが分からない)。reason = 頭の後の理由(前後の空白を外す)。
   この行より前で、手番の最後の道具の結果より後の本文は差し戻された答え(Stop hook は道具を呼ばずに終えた応答の後にだけ走る)。"
  (#^ str reason))

(defclass [(dataclass :frozen True)] Other []
  "語彙の外の行(名前だけ持つ)。"
  (#^ str type)
  (setv #^ str subtype ""))

(setv ClaudeLineKind (| Init AssistantMessage ToolResult PartialMessage ThinkingTokens InputFate
                        PermissionRequested ControlResponse TaskEvent HookNotice RateLimit StopHookFeedback TurnResult Other))

(defclass [(dataclass :frozen True)] ClaudeStreamLine []
  "stdout の 1 行。seq は会話の中で単調増加・at は読んだ時刻(doeff-time の時計)・raw は 1 行の逐語。"
  (#^ int seq)
  (#^ datetime at)
  (#^ ClaudeLineKind kind)
  (#^ str raw))


;; --- 手番の終わり ---------------------------------------------------------------------------------
;;
;; どの終わりも、会話の今の context の大きさを出すための 3 欄を持つ(#3744 — 状態機械 dialogue.hy の ended が 1 か所で載せる):
;; last-call-usage = この手番の本体の会話(parent_tool_use_id が null)の最後の assistant の行の usage(最後の API の呼び 1 回の消費 —
;; 今の context の大きさは入力の側 input・cache_read・cache_creation の和。出力の token は呼びの途中の値)/ last-call-model = その行の
;; model / model-windows = この手番に読んだ result の行の modelUsage の窓(model ごとに 1 つ)。手番の途中で終わった時は、それまでに
;; 読んだ値(本体の assistant の行が無ければ None・result の行が無ければ空 — 0 を発明しない)。

(defclass [(dataclass :frozen True)] Completed []
  "CLI が誤りなく終えた手番。usage = この手番に読んだ result の行の消費の token の和(Usage)/
   cost-usd = この手番の額(USD)= CLI が名乗った累積の額(total_cost_usd)の、手番の始まりから終わりまでの差(状態機械 dialogue.hy が
   数える)。始まりか終わりの額が分からなければ None(0 を発明しない)/ last-call-usage・last-call-model・model-windows は節の頭の註 /
   account-limit = この手番が口座の限度に当たった事実(AccountLimitHit — 当たっていなければ None・#3983)/
   account-refusal = この手番の要求を口座の側が断った事実(AccountRefusalHit — 断られていなければ None)。"
  (setv #^ str result-text "")
  (setv #^ Usage usage (field :default-factory Usage))
  (setv #^ (| float None) cost-usd None)
  (setv #^ (get tuple #(str ...)) input-refs #())
  (setv #^ (| Usage None) last-call-usage None)
  (setv #^ (| str None) last-call-model None)
  (setv #^ (get tuple #(ModelWindow ...)) model-windows #())
  (setv #^ (| AccountLimitHit None) account-limit None)
  (setv #^ (| AccountRefusalHit None) account-refusal None))

(defclass [(dataclass :frozen True)] Failed []
  "CLI が誤りで終えた手番。detail = CLI が名乗った文(無ければ subtype)・api-error-status = API の誤りの HTTP status・
   usage = 誤りの前に消費した token(result の行が名乗った物 — 注入の断りのように result の行が無い終わりは空の Usage)・
   cost-usd = 誤りの前に使った額(USD — 数え方は Completed と同じ。result の行が無い終わり・額が分からない時は None)/
   last-call-usage・last-call-model・model-windows は節の頭の註 / account-limit(#3983)・account-refusal は Completed と同じ。"
  (#^ str detail)
  (setv #^ (| int None) api-error-status None)
  (setv #^ str terminal-reason "")
  (setv #^ Usage usage (field :default-factory Usage))
  (setv #^ (| float None) cost-usd None)
  (setv #^ (get tuple #(str ...)) input-refs #())
  (setv #^ (| Usage None) last-call-usage None)
  (setv #^ (| str None) last-call-model None)
  (setv #^ (get tuple #(ModelWindow ...)) model-windows #())
  (setv #^ (| AccountLimitHit None) account-limit None)
  (setv #^ (| AccountRefusalHit None) account-refusal None))

(defclass [(dataclass :frozen True)] Interrupted []
  "止めた手番の終わり。process-kept = 同じ CLI の process が会話に残り次の手番も使うか(control の止め — 真)、止めと一緒に
   process が降りたか(SIGINT の形・会話を閉じる止め・止めの途中で process が消えた — 偽。#3672 の決め 6)— 既定値を置かない
   (作り手が必ず名乗る)・surviving-refs = CLI が次の手番として走らせる入力(continued-by がその手番)・dropped-refs = 読まれずに
   捨てられた入力 / last-call-usage・last-call-model・model-windows は節の頭の註。"
  (#^ bool process-kept)
  (setv #^ (get tuple #(str ...)) surviving-refs #())
  (setv #^ (get tuple #(str ...)) dropped-refs #())
  (setv #^ (| ClaudeTurn None) continued-by None)
  (setv #^ (| Usage None) last-call-usage None)
  (setv #^ (| str None) last-call-model None)
  (setv #^ (get tuple #(ModelWindow ...)) model-windows #()))

(defclass [(dataclass :frozen True)] BackendLost []
  "終わりの行を読む前に process が消えた(OOM・kill・host の再起動)。次の手番は同じ ResumeSession で頼めばよい /
   last-call-usage・last-call-model・model-windows は節の頭の註(消える前に読んだ値)。"
  (#^ str detail)
  (setv #^ (| Usage None) last-call-usage None)
  (setv #^ (| str None) last-call-model None)
  (setv #^ (get tuple #(ModelWindow ...)) model-windows #()))

(setv ClaudeTurnEnd (| Completed Failed Interrupted BackendLost))


;; --- 分類(純関数) ------------------------------------------------------------------------------

(defn #^ dict object-at [value #^ str key]
  (setv inner (if (isinstance value dict) (.get value key) None))
  (if (isinstance inner dict) inner {}))

(defn #^ str text-at [value #^ str key]
  (setv inner (if (isinstance value dict) (.get value key) None))
  (if (isinstance inner str) inner ""))

(defn #^ tuple strings-at [value #^ str key]
  (setv inner (if (isinstance value dict) (.get value key) None))
  (if (isinstance inner list) (tuple (gfor item inner :if (isinstance item str) item)) #()))

(defn #^ (| int None) int-at [value #^ str key]
  "整数ちょうど(bool・文字列・小数は数えない)。"
  (setv inner (if (isinstance value dict) (.get value key) None))
  (if (and (isinstance inner int) (not (isinstance inner bool))) inner None))

(defn #^ (| float None) number-at [value #^ str key]
  "数(整数か小数 — bool は数えない)を float で。無ければ None。"
  (setv inner (if (isinstance value dict) (.get value key) None))
  (if (and (isinstance inner #(int float)) (not (isinstance inner bool))) (float inner) None))

(defn #^ Usage usage-of [#^ dict usage]
  "result の行の usage の object → Usage(名乗らない欄は None)。"
  (setv cache (object-at usage "cache_creation"))
  (setv tier (.get usage "service_tier"))
  (Usage :input-tokens (int-at usage "input_tokens")
         :output-tokens (int-at usage "output_tokens")
         :cache-creation-input-tokens (int-at usage "cache_creation_input_tokens")
         :cache-read-input-tokens (int-at usage "cache_read_input_tokens")
         :cache-creation-5m-input-tokens (int-at cache "ephemeral_5m_input_tokens")
         :cache-creation-1h-input-tokens (int-at cache "ephemeral_1h_input_tokens")
         :web-search-requests (int-at (object-at usage "server_tool_use") "web_search_requests")
         :service-tier (if (isinstance tier str) tier None)))

(defn #^ (| dict None) parse-record [#^ str line]
  "stdout の 1 行 → JSON の object(壊れた行・object でない行は None)。"
  (setv stripped (.strip line))
  (when (not stripped) (return None))
  (try
    (setv value (json.loads stripped))
    (except [ValueError] (return None)))
  (if (isinstance value dict) value None))

(defn #^ tuple content-blocks [#^ dict record]
  (setv content (.get (object-at record "message") "content"))
  (if (isinstance content list) (tuple (gfor block content :if (isinstance block dict) block)) #()))

(defn classify-assistant [#^ dict record]
  "assistant の行 → AssistantMessage。tool_use の block は id・name・input を読んで ToolCall にする。id か name が無い・空の、または
   input が無い・JSON の object でない tool_use の block を 1 つでも持つ行は、名指しの Other(type = assistant・subtype =
   tool_use_without_id / tool_use_without_name / tool_use_without_input)で断る — 空の id の ToolCall を作らない(結果の tool_use_id と
   突き合わせられない呼びを通さない)・命令の無い呼びを空の命令として通さない。
   message.usage(object の時だけ — usage-of で読む)・message.model・行の最上位の parent_tool_use_id も読む(無い・空なら None)。"
  (setv blocks (content-blocks record))
  (setv uses (tuple (gfor block blocks :if (= (.get block "type") "tool_use") block)))
  (setv message (object-at record "message"))
  (setv usage (.get message "usage"))
  (cond
    (any (gfor block uses (not (text-at block "id")))) (Other :type "assistant" :subtype "tool_use_without_id")
    (any (gfor block uses (not (text-at block "name")))) (Other :type "assistant" :subtype "tool_use_without_name")
    (any (gfor block uses (not (isinstance (.get block "input") dict)))) (Other :type "assistant" :subtype "tool_use_without_input")
    True (AssistantMessage
           :text (.join "" (gfor block blocks :if (= (.get block "type") "text") (text-at block "text")))
           :tool-calls (tuple (gfor block uses (ToolCall :id (text-at block "id") :name (text-at block "name")
                                                         :input (frozen-json-object (get block "input") "tool_use の block の input"))))
           :usage (if (isinstance usage dict) (usage-of usage) None)
           :model (or (text-at message "model") None)
           :parent-tool-use-id (or (text-at record "parent_tool_use_id") None)
           :error (or (text-at record "error") None))))

(defk tool-answer-of [#^ dict block]
  {:pre [(: block dict)] :post [(: % ToolAnswer)] :tags {:context "claude-code" :role "foundation"}}
  "tool_result の block 1 つ → ToolAnswer。content は文字列(本文そのまま)・block の列(text の block の本文を改行で連ね、text でない
   block は種類の名を non-text-kinds へ)・無い(空の本文)のどれか。それ以外の値(写像・数)は 1 つの text でない中身として種類の名を残す。"
  (val content (.get block "content"))
  (val parts (cond
               (is content None) #()
               (isinstance content str) #({"type" "text" "text" content})
               (isinstance content list) (tuple content)
               True #(content)))
  (ToolAnswer :id (text-at block "tool_use_id")
              :text (.join "\n" (gfor part parts :if (= (text-at part "type") "text") (text-at part "text")))
              :is-error (is (.get block "is_error") True)
              :non-text-kinds (tuple (gfor part parts :if (!= (text-at part "type") "text") (text-at part "type")))))

(defn classify-user [#^ dict record]
  "user の行 → ToolResult(tool_result の block ごとに ToolAnswer)。isSynthetic の行で本文が STOP-HOOK-FEEDBACK-HEAD で始まる行は
   StopHookFeedback(#4020)。どちらでもない行は Other(type = user)。tool_use_id が無い・空の tool_result を 1 つでも持つ行は、
   名指しの Other(subtype = tool_result_without_id)で断る — どの呼びへの答えか分からない結果を通さない。"
  (setv results (tuple (gfor block (content-blocks record) :if (= (.get block "type") "tool_result") block))
        said (.join "\n" (gfor block (content-blocks record) :if (= (.get block "type") "text") (text-at block "text"))))
  (cond
    (and (not results) (is (.get record "isSynthetic") True) (.startswith said STOP-HOOK-FEEDBACK-HEAD))
    (StopHookFeedback :reason (.strip (cut said (len STOP-HOOK-FEEDBACK-HEAD) None)))
    (not results) (Other :type "user")
    (any (gfor block results (not (text-at block "tool_use_id")))) (Other :type "user" :subtype "tool_result_without_id")
    True (ToolResult :answers (tuple (gfor block results (run (tool-answer-of block)))))))

(defk hook-notice-of [#^ dict record #^ HookPhase phase]
  {:pre [(: record dict) (: phase HookPhase)] :post [(: % HookNotice)] :tags {:context "claude-code" :role "foundation"}}
  "hook の開始と応答の行を、hook のイベント名で読めるようにするため(最初の入力の前に許す行を名前で絞る — dialogue.hy)。イベント名は
   hook_event、この欄の無い版は hook_name(「イベント名:matcher」)の「:」の前。"
  (val name (text-at record "hook_name"))
  (HookNotice :event (or (text-at record "hook_event") (get (.partition name ":") 0)) :phase phase :name name))

(defk whole-ms-of [number]
  {:pre [(: number (| float None))] :post [(: % (| int None))] :tags {:context "claude-code" :role "foundation"}}
  "CLI が小数で名乗る刻(epoch ミリ秒)を、計時の行の他の刻と同じく床へ丸めた整数にするため(無ければ None)。"
  (if (is number None) None (math.floor number)))

(defk timed-phases-of [#^ dict phases #^ dict starts]
  {:pre [(: phases dict) (: starts dict)] :post [(: % (get tuple #(TimedPhase ...)))] :tags {:context "claude-code" :role "foundation"}}
  "CLI の区間の写像(名 → ミリ秒の整数)を TimedPhase の列(object の鍵の順)にするため。starts = 名 → 始まりのミリ秒(名乗らない名は
   None)。空の名・長さが整数でない名は飛ばす(0 を発明しない)。"
  (tuple (gfor #(name _) (.items phases)
               :if (and (isinstance name str) name (is-not (int-at phases name) None))
               (TimedPhase :name name :ms (int-at phases name) :start-ms (int-at starts name)))))

(defk request-timing-of [#^ dict record]
  {:pre [(: record dict)] :post [(: % RequestTiming)] :tags {:context "claude-code" :role "foundation"}}
  "result の行の計時の欄を RequestTiming にするため(名乗らない欄は None・区間は空)。"
  (<- phases (timed-phases-of (object-at record "time_to_request_phases_ms") {}))
  (RequestTiming :time-to-request-ms (int-at record "time_to_request_ms")
                 :ttft-stream-ms (int-at record "ttft_stream_ms")
                 :first-content-frame-ms (int-at record "first_content_frame_ms")
                 :ttft-ms (int-at record "ttft_ms")
                 :duration-ms (int-at record "duration_ms")
                 :duration-api-ms (int-at record "duration_api_ms")
                 :request-phases phases))

(defn classify-system [#^ dict record]
  (setv subtype (text-at record "subtype"))
  (setv startup (object-at record "startup_timing"))
  (cond
    (= subtype "init")
      (Init :session-id (text-at record "session_id")
            :capabilities (strings-at record "capabilities")
            :model (text-at record "model")
            :permission-mode (text-at record "permissionMode")
            :mcp-servers (tuple (gfor server (or (.get record "mcp_servers") [])
                                      :if (isinstance server dict) (text-at server "name")))
            :startup-phases (run (timed-phases-of (object-at startup "phases") (object-at startup "phase_start_ms")))
            :startup-origin-ms (run (whole-ms-of (number-at startup "time_origin_ms"))))
    (= subtype "thinking_tokens")
      (ThinkingTokens :estimated (or (int-at record "estimated_tokens") 0))
    (= subtype "task_started")
      (TaskEvent :task-id (text-at record "task_id") :status "started")
    (= subtype "task_notification")
      (TaskEvent :task-id (text-at record "task_id") :status (or (text-at record "status") "notified"))
    (= subtype "hook_started") (run (hook-notice-of record HookPhase.STARTED))
    (= subtype "hook_response") (run (hook-notice-of record HookPhase.RESPONSE))
    True (Other :type "system" :subtype subtype)))

(defn classify-rate-limit [#^ dict record]
  (setv info (object-at record "rate_limit_info"))
  (setv window (text-at info "rateLimitType"))
  (setv utilization (.get (object-at (object-at info "unifiedWindows") window) "utilization"))
  (RateLimit :window window
             :utilization (if (and (isinstance utilization #(int float)) (not (isinstance utilization bool)))
                              (float utilization) None)
             :resets-at (int-at info "resetsAt")
             :status (or (text-at info "status") "")))

(defn classify-control-request [#^ dict record]
  (setv request (object-at record "request"))
  (if (= (text-at request "subtype") "can_use_tool")
      (PermissionRequested :request-id (text-at record "request_id")
                           :tool-name (text-at request "tool_name")
                           :input (frozen-json-object (object-at request "input") "control_request の request.input")
                           :suggestions (tuple (or (.get request "permission_suggestions") [])))
      (Other :type "control_request" :subtype (text-at request "subtype"))))

(defn classify-control-response [#^ dict record]
  (setv response (object-at record "response"))
  (ControlResponse :request-id (text-at response "request_id")
                   :subtype (text-at response "subtype")
                   :still-queued (strings-at (object-at response "response") "still_queued")))

(defk model-windows-of [#^ dict model-usage]
  {:pre [(: model-usage dict)] :post [(: % (get tuple #(ModelWindow ...)))] :tags {:context "claude-code" :role "foundation"}}
  "result の行の modelUsage(model の名 → その model の数の object)→ ModelWindow の列(object の鍵の順)— 会話の context の大きさを
   model の窓と比べるため。値が object でない model・空の名は飛ばす。"
  (tuple (gfor #(model numbers) (.items model-usage) :if (and (isinstance model str) model (isinstance numbers dict))
               (ModelWindow :model model
                            :context-window (int-at numbers "contextWindow")
                            :max-output-tokens (int-at numbers "maxOutputTokens")))))

(defn classify-result [#^ dict record]
  (TurnResult :model-windows (run (model-windows-of (object-at record "modelUsage")))
              :subtype (text-at record "subtype")
              :is-error (is (.get record "is_error") True)
              :terminal-reason (text-at record "terminal_reason")
              :origin-kind (text-at (object-at record "origin") "kind")
              :result-text (text-at record "result")
              :usage (usage-of (object-at record "usage"))
              :cost-usd (number-at record "total_cost_usd")
              :api-error-status (int-at record "api_error_status")
              :input-refs (strings-at record "user_message_uuids")
              :timing (run (request-timing-of record))))

(defn classify-lifecycle [#^ dict record]
  (setv state (text-at record "state"))
  (if (in state INPUT-FATES)
      (InputFate :ref (text-at record "command_uuid") :state state)
      (Other :type "command_lifecycle" :subtype state)))

(defk delta-kind-of [#^ str delta-type]
  {:pre [(: delta-type str)] :post [(: % DeltaKind)] :tags {:context "claude-code" :role "foundation"}}
  "stream_event の delta の type の名 → DeltaKind(手番の終わりの計時の行が差分の種類ごとに数えるため — 名の無い delta は NO-DELTA・
   知らない名は OTHER)。"
  (match delta-type
    "" DeltaKind.NO-DELTA
    "text_delta" DeltaKind.TEXT
    "thinking_delta" DeltaKind.THINKING
    "input_json_delta" DeltaKind.TOOL-INPUT
    _ DeltaKind.OTHER))

(defn tool-start-of [#^ dict event]
  "stream_event の event が tool_use の block の始まり(content_block_start)なら、その呼びの id と道具の名(命令はまだ無い)を読むため。
   ほかの event・id か名の欠けた block は None(空の欄で ToolCall を作らない)。"
  (setv block (object-at event "content_block"))
  (if (and (= (text-at event "type") "content_block_start") (= (text-at block "type") "tool_use")
           (text-at block "id") (text-at block "name"))
      (ToolCall (text-at block "id") (text-at block "name"))
      None))

(defn classify-stream-event [#^ dict record]
  (setv event (object-at record "event"))
  (setv delta (object-at event "delta"))
  (setv kind (run (delta-kind-of (text-at delta "type"))))
  (PartialMessage :text-delta (if (= kind DeltaKind.TEXT) (text-at delta "text") "") :delta kind
                  :thinking-delta (if (= kind DeltaKind.THINKING) (text-at delta "thinking") "")
                  :tool-input-delta (if (= kind DeltaKind.TOOL-INPUT) (text-at delta "partial_json") "")
                  :tool-start (tool-start-of event)
                  :ttft-ms (int-at record "ttft_ms")))

;; --- transcript の額の行(純関数) ---------------------------------------------------------------------

(defk recorded-cost [#^ str transcript-text]
  {:pre [(: transcript-text str)] :post [(: % (| float None))] :tags {:context "claude-code" :role "foundation"}}
  "--resume(--fork-session を含む)で起こした CLI が数え始める額を、handler がその会話の transcript(jsonl の本文)から知るため:
   CLI は降りる時に会話の累積の額を {\"type\":\"cost-state\", \"totalCostUSD\": 数} の行で記し、続きの process と枝の process は
   最後のその行の額から数え続ける(実測 2.1.283・#883)。読むのは最後の cost-state の行の totalCostUSD の 1 欄だけ。
   行が無い・最後の行の値が数でない(bool は数えない)なら None(0 を発明しない — CLI の版で形が変わっても誤った額を出さない)。"
  (val found (next (gfor line (reversed (.splitlines transcript-text))
                         :if (in "\"cost-state\"" line)
                         :setv record (parse-record line)
                         :if (and (is-not record None) (= (.get record "type") "cost-state"))
                         record)
                   None))
  (if (is found None) None (number-at found "totalCostUSD")))

(defn classify-record [#^ dict record]
  "stream-json の 1 行 → ClaudeLineKind(純関数・語彙の外は Other)。"
  (setv kind (text-at record "type"))
  (cond
    (= kind "system") (classify-system record)
    (= kind "assistant") (classify-assistant record)
    (= kind "user") (classify-user record)
    (= kind "stream_event") (classify-stream-event record)
    (= kind "command_lifecycle") (classify-lifecycle record)
    (= kind "control_request") (classify-control-request record)
    (= kind "control_response") (classify-control-response record)
    (= kind "rate_limit_event") (classify-rate-limit record)
    (= kind "result") (classify-result record)
    True (Other :type kind :subtype (text-at record "subtype"))))
