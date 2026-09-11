;;; agentd の純粋な判断(defk の退化形 — bind ゼロ・I/O の import なし)。
;;;
;;; agentd は判断を持たない(設計 第 12.10 節・bounded context F)。ここに在るのは
;;;   * 「自分に結ばれた job か」の純関数 1 点(bound-to-me)— job を選ぶ唯一の判定。
;;;     選択も優先も無い(該当する行は行の順にすべて受ける)。
;;;   * 行の欄の写し(charter・inputs・affinity → 起こし方 / status の書き換え / node の
;;;     lease の欄)— 判断ではなく契約の綴りの変換。
;;;   * transcript の行 → TurnDelta の frame と turn-record の entries(契約
;;;     docs/contracts/turn-delta.json / agora-kinds.json の欄へ写す)。
;;;   * capture の是非(購読者の数 → continue | stop・issue #1 の決定)と待ちの長さ。
;;; wire の綴り(kind 名・phase・route)は effects.py だけが持ち、ここは import する。
;;; I/O は 1 つも無い(handlers.py が持つ)— 法 (b)(針: この file に job を選ぶ第 2 の
;;; 判定が無い・binding を書かない)。

(require doeff-hy.macros [defk <-])

(import dataclasses [replace])
(import datetime [datetime timezone])
(import json)
(import re)

(import doeff_agents.sessionhost.acp.effects [
  AGENT-TYPE-LEASE-KIND
  AGENTD-PRINCIPAL
  AgentdSettings
  AgentdState
  AcpRow
  CLAUDE-OAUTH-TOKEN-ENV
  DeltaBatch
  InFlightJob
  JSONObject
  JobOutcome
  LaunchPlan
  NODE-GONE
  PHASE-BOUND
  PHASE-ENDED
  PHASE-RUNNING
  SESSION-TERMINAL-STATUSES
  SessionView
  TURN-RECORD-ENDED
  WatchAdvance])


;; ---------------------------------------------------------------------------
;; 自分に結ばれた job か — agentd が持つ唯一の判定
;; ---------------------------------------------------------------------------

(defk bound-to-me [row node-name]
  {:pre [(: row AcpRow) (: node-name str)]
   :post [(: % bool)]}
  "phase == Bound かつ status.binding.node == 自分。これ以外の条件で job を選ばない
   (法 agentd-holds-no-placement-judgment)。"
  (setv status row.status)
  (setv binding (if (isinstance status dict) (.get status "binding") None))
  (and (isinstance status dict)
       (= (.get status "phase") PHASE-BOUND)
       (isinstance binding dict)
       (= (.get binding "node") node-name)))


(defk job-rows-bound-to [rows node-name]
  {:pre [(: rows tuple) (: node-name str)]
   :post [(: % tuple)]}
  "list の行のうち自分に結ばれた行を、行の順のまま(優先も選択も無し)。"
  (setv out [])
  (for [row rows]
    (<- mine bool (bound-to-me row node-name))
    (when mine
      (.append out row)))
  (tuple out))


;; ---------------------------------------------------------------------------
;; 行の欄の写し(agent-job)
;; ---------------------------------------------------------------------------

(defk status-object-of [row]
  {:pre [(: row AcpRow)]
   :post [(: % dict)]}
  "行の status(無ければ空 — 生まれの状態は engine が刻むので agentd が発明しない)。"
  (if (isinstance row.status dict) (dict row.status) {}))


(defk running-status-of [row session-id principal]
  {:pre [(: row AcpRow) (: session-id str) (: principal str)]
   :post [(: % dict)]}
  "受けた job の status: committed の欄を写し、phase = Running と sessionHandle
   {sessionId, stream{owner, name}} だけ書く(binding は触らない — 書き手は scheduling)。"
  (<- next dict (status-object-of row))
  (setv (get next "phase") PHASE-RUNNING)
  (setv (get next "sessionHandle")
        {"sessionId" session-id
         "stream" {"owner" principal "name" session-id}})
  next)


(defk condition-of [condition-type reason]
  {:pre [(: condition-type str) (: reason str)]
   :post [(: % dict)]}
  "conditions の 1 項(改訂 R1-a の {type, status, reason})。"
  {"type" condition-type "status" "True" "reason" reason})


(defk ended-status-of [status result conditions]
  {:pre [(: status dict) (: result (| dict list str int float bool None)) (: conditions tuple)]
   :post [(: % dict)]}
  "手番の終わりの status: phase = Ended、result(あれば)、conditions は既存に足す。"
  (setv next (dict status))
  (setv (get next "phase") PHASE-ENDED)
  (when (is-not result None)
    (setv (get next "result") result))
  (when conditions
    (setv existing (.get status "conditions"))
    (setv (get next "conditions")
          (+ (if (isinstance existing list) (list existing) []) (list conditions))))
  next)


(defk launch-plan-of [row]
  {:pre [(: row AcpRow)]
   :post [(: % LaunchPlan)]}
  "Bound の行から起こし方を写す: charter(= session.launch の params そのもの)・
   affinity.predecessor(あれば resume)・binding.account(あれば預かり所から借りる —
   種類は charter の agent_type)・binding.profile と charter.model は turn-record の欄。
   欠けている欄は発明しない: profile は binding に無ければ \"unbound\"(結ばれた profile が
   無い事実の名)、model は charter に無ければ \"default\"(走行器の既定を使う事実の名)。"
  (setv spec row.spec)
  (setv charter (.get spec "charter"))
  (when (not (isinstance charter dict))
    (raise (ValueError f"agent-job {row.key}: spec.charter is not an object")))
  (setv affinity (.get spec "affinity"))
  (setv predecessor (if (isinstance affinity dict) (.get affinity "predecessor") None))
  (<- status dict (status-object-of row))
  (setv binding (.get status "binding"))
  (setv binding (if (isinstance binding dict) binding {}))
  (setv account (.get binding "account"))
  (setv agent-type (.get charter "agent_type"))
  (setv lease-kind (if (and (isinstance account str) account (isinstance agent-type str))
                       (.get AGENT-TYPE-LEASE-KIND agent-type)
                       None))
  (setv profile (.get binding "profile"))
  (setv model (.get charter "model"))
  (LaunchPlan
    :charter (dict charter)
    :predecessor (if (isinstance predecessor str) predecessor None)
    :lease-kind lease-kind
    :account (if (is lease-kind None) None account)
    :profile (if (and (isinstance profile str) profile) profile "unbound")
    :model (if (and (isinstance model str) model) model "default")))


(defk inputs-of [row]
  {:pre [(: row AcpRow)]
   :post [(: % tuple)]}
  "spec.inputs(Message の id の列)。無ければ空。"
  (setv inputs (.get row.spec "inputs"))
  (if (isinstance inputs list)
      (tuple (lfor item inputs :if (isinstance item str) item))
      #()))


(defk resume-params-of [predecessor charter]
  {:pre [(: predecessor str) (: charter dict)]
   :post [(: % dict)]}
  "affinity.predecessor が在る job の session.resume の params: 前の incarnation の
   session_id を名指し、新しい session_id と launch の意図(charter)を運ぶ
   (host.hy の session.resume の受理形 — resume 専用の欄はそのまま素通し)。"
  (setv params {"session_id" predecessor
                "new_session_id" (.get charter "session_id")})
  (for [key ["prompt" "model" "effort" "mcp_servers" "session_env" "binding"
             "expected_result" "context_file" "launch_attribution"]]
    (when (in key charter)
      (setv (get params key) (get charter key))))
  params)


(defk charter-with-grant [charter lease-kind account grant-token auth-json homes-root]
  {:pre [(: charter dict) (: lease-kind str) (: account str)
         (: grant-token (| str None)) (: auth-json (| str None)) (: homes-root str)]
   :post [(: % tuple)]}
  "借りた札で charter を組み直す。戻り = #(charter' auth-file-path)。
   claude: 札は env CLAUDE_CODE_OAUTH_TOKEN(custodian の契約 — 資格 file は書かない)、
   家 = <homes-root>/claude/<account> を binding {kind claude-code, config_dir} で渡す
   (transcript の家 = この config_dir の projects/ 配下)。
   codex: 札 = auth.json の中身。<homes-root>/codex/<account>/auth.json へ置き(家の中の
   auth file — 法 (e) の唯一の許された平文)、binding {kind codex, auth_file, profile_dir}
   の二軸で host の fs-compose-home-view に家を組ませる。profile_dir は charter の binding
   の profile_dir(無ければ codex_home)— どちらも無ければ charter は変えない(借りた札を
   使う家が無い)。"
  (setv next (dict charter))
  (setv safe-account (re.sub r"[^A-Za-z0-9._-]" "_" account))
  (cond
    (= lease-kind "claude")
    (do
      (setv env (dict (or (.get charter "session_env") {})))
      (when (is-not grant-token None)
        (setv (get env CLAUDE-OAUTH-TOKEN-ENV) grant-token))
      (setv (get next "session_env") env)
      (setv (get next "binding")
            {"kind" "claude-code" "config_dir" f"{homes-root}/claude/{safe-account}"})
      #(next None))
    (= lease-kind "codex")
    (do
      (setv binding (.get charter "binding"))
      (setv binding (if (isinstance binding dict) binding {}))
      (setv profile-dir (or (.get binding "profile_dir") (.get binding "codex_home")))
      (if (or (is auth-json None) (not (isinstance profile-dir str)) (not profile-dir))
          #(next None)
          (do
            (setv auth-file f"{homes-root}/codex/{safe-account}/auth.json")
            (setv (get next "binding")
                  {"kind" "codex" "auth_file" auth-file "profile_dir" profile-dir})
            #(next auth-file))))
    True
    #(next None)))


(defk message-bodies-of [rows inputs]
  {:pre [(: rows tuple) (: inputs tuple)]
   :post [(: % tuple)]}
  "inputs の id に対応する Message の本文(spec.body)を inputs の順に。戻り =
   #(bodies missing-ids)。鍵は契約の identityKey(spec.id)、無ければ行の resourceId。"
  (setv by-id {})
  (for [row rows]
    (setv spec-id (.get row.spec "id"))
    (setv (get by-id (if (isinstance spec-id str) spec-id row.resource-id)) row))
  (setv bodies [])
  (setv missing [])
  (for [input-id inputs]
    (setv row (.get by-id input-id))
    (setv body (if (is row None) None (.get row.spec "body")))
    (if (isinstance body str)
        (.append bodies body)
        (.append missing input-id)))
  #((tuple bodies) (tuple missing)))


;; ---------------------------------------------------------------------------
;; 行の欄の写し(node の lease と観測)
;; ---------------------------------------------------------------------------

(defk node-row-named [rows name]
  {:pre [(: rows tuple) (: name str)]
   :post [(: % (| AcpRow None))]}
  "自分の名の生きた Node の行(gone は同じ名の生きた行ではない)。無ければ None。"
  (setv found None)
  (for [row rows]
    (setv state (if (isinstance row.status dict) (.get row.status "state") None))
    (when (and (is found None)
               (= (.get row.spec "name") name)
               (!= state NODE-GONE))
      (setv found row)))
  found)


(defk node-status-with-lease [row settings now-ms sessions]
  {:pre [(: row AcpRow) (: settings AgentdSettings) (: now-ms int) (: sessions int)]
   :post [(: % dict)]}
  "agentd が書く欄だけを更新した node の status: lease{owner, heartbeatAt, expiresAt} と
   observations{streamCapability, sessions}。state(scheduling の欄)は写すだけ。"
  (<- next dict (status-object-of row))
  (setv (get next "lease")
        {"owner" settings.principal
         "heartbeatAt" now-ms
         "expiresAt" (+ now-ms (* 1000 settings.node-lease-ttl-seconds))})
  (setv (get next "observations")
        {"streamCapability" settings.stream-capability
         "sessions" sessions})
  next)


;; ---------------------------------------------------------------------------
;; 手番の記録(turn-record)
;; ---------------------------------------------------------------------------

(defk turn-record-spec-of [job]
  {:pre [(: job InFlightJob)]
   :post [(: % dict)]}
  "契約 turn-record の spec(conversationId・agentJobId・node・profile・model)。"
  {"conversationId" job.subject
   "agentJobId" job.job-id
   "node" job.node
   "profile" job.profile
   "model" job.model})


(defk turn-record-ended-status [status usage entries]
  {:pre [(: status dict) (: usage (| dict None)) (: entries tuple)]
   :post [(: % dict)]}
  "手番の終わりの turn-record の status: state = ended・usage(素材があれば)・entries。"
  (setv next (dict status))
  (setv (get next "state") TURN-RECORD-ENDED)
  (when (is-not usage None)
    (setv (get next "usage") usage))
  (setv (get next "entries") (list entries))
  next)


;; ---------------------------------------------------------------------------
;; transcript → TurnDelta / entries
;; ---------------------------------------------------------------------------

(defk transcript-path-of [view canonical-work-dir]
  {:pre [(: view SessionView) (: canonical-work-dir str)]
   :post [(: % (| str None))]}
  "器の眺めから transcript の file を引く。claude =
   <CLAUDE_CONFIG_DIR>/projects/<mangled canonical work_dir>/<sid>.jsonl(mangle = 非英数字を
   '-' に — impls/claude_code.hy の会話 identity の家と同じ物理)、codex = conversation の
   rollout_path。材料が欠ければ None(実況は無し・記録は空 — 発明しない)。"
  (setv conv (or view.conversation {}))
  (setv identity (or view.effective-identity {}))
  (cond
    (= view.agent-type "codex")
    (do (setv rollout (.get conv "rollout_path"))
        (if (isinstance rollout str) rollout None))
    (= view.agent-type "claude")
    (do (setv config-dir (.get identity "CLAUDE_CONFIG_DIR"))
        (setv sid (.get conv "session_id"))
        (if (and (isinstance config-dir str) (isinstance sid str) canonical-work-dir)
            (do (setv mangled (re.sub "[^A-Za-z0-9]" "-" canonical-work-dir))
                f"{config-dir}/projects/{mangled}/{sid}.jsonl")
            None))
    True None))


(defk usage-of-claude-message [usage model]
  {:pre [(: usage dict) (: model (| str None))]
   :post [(: % dict)]}
  "claude の message.usage → 契約 usage(token の 4 欄 + 内訳)。欄の欠落は 0 を発明せず
   落とす … ただし必須 4 欄は素材が無ければ 0(素材の無い message は呼び手が渡さない)。"
  (setv cache (or (.get usage "cache_creation") {}))
  (setv #^ JSONObject out {"input" (int (.get usage "input_tokens" 0))
                           "output" (int (.get usage "output_tokens" 0))
                           "cacheWrite" (int (.get usage "cache_creation_input_tokens" 0))
                           "cacheRead" (int (.get usage "cache_read_input_tokens" 0))})
  (when (isinstance cache dict)
    (when (in "ephemeral_5m_input_tokens" cache)
      (setv (get out "cacheWrite5m") (int (get cache "ephemeral_5m_input_tokens"))))
    (when (in "ephemeral_1h_input_tokens" cache)
      (setv (get out "cacheWrite1h") (int (get cache "ephemeral_1h_input_tokens")))))
  (when (isinstance model str)
    (setv (get out "model") model))
  out)


(defk add-usage [total part]
  {:pre [(: total (| dict None)) (: part dict)]
   :post [(: % dict)]}
  "usage の和(model は最後に見た綴り)。"
  (setv out (if (is total None) {} (dict total)))
  (for [key ["input" "output" "cacheWrite" "cacheRead" "cacheWrite5m" "cacheWrite1h"]]
    (when (in key part)
      (setv (get out key) (+ (int (.get out key 0)) (int (get part key))))))
  (when (in "model" part)
    (setv (get out "model") (get part "model")))
  out)


(defk parse-json-lines [text]
  {:pre [(: text str)]
   :post [(: % tuple)]}
  "JSON 行の列 → dict の列(壊れた行・object でない行は飛ばす)。"
  (setv out [])
  (for [line (.splitlines text)]
    (setv stripped (.strip line))
    (when stripped
      (try
        (setv value (json.loads stripped))
        (except [ValueError]
          (setv value None)))
      (when (isinstance value dict)
        (.append out value))))
  (tuple out))


(defk summary-of [value limit]
  {:pre [(: value (| dict list str int float bool None)) (: limit int)]
   :post [(: % str)]}
  "表示用の要約(JSON の compact な綴りを limit 字で切る)。"
  (setv text (if (isinstance value str) value (json.dumps value :ensure-ascii False)))
  (if (> (len text) limit) (cut text 0 limit) text))


(defk delta-frame [job-id seq at kind payload]
  {:pre [(: job-id str) (: seq int) (: at int) (: kind str) (: payload dict)]
   :post [(: % dict)]}
  "契約 turn-delta.json の frame 1 つ。"
  {"agentJobId" job-id "seq" seq "at" at "kind" kind "payload" payload})


(defk claude-deltas-of [records job-id seq-start at]
  {:pre [(: records tuple) (: job-id str) (: seq-start int) (: at int)]
   :post [(: % DeltaBatch)]}
  "claude の transcript の行(assistant の content block・user の tool_result)→ frame と
   entries。usage は message.id ごとに 1 度だけ数える(1 message が block ごとの行に割れる)。"
  (setv frames [])
  (setv entries [])
  (setv usage None)
  (setv seen-messages (set))
  (setv model None)
  (setv seq seq-start)
  (for [record records]
    (setv kind (.get record "type"))
    (setv message (.get record "message"))
    (when (and (in kind #{"assistant" "user"}) (isinstance message dict))
      (setv content (.get message "content"))
      (setv message-model (.get message "model"))
      (when (isinstance message-model str)
        (setv model message-model))
      (when (= kind "assistant")
        (setv message-id (.get message "id"))
        (setv message-usage (.get message "usage"))
        (when (and (isinstance message-usage dict) (not-in message-id seen-messages))
          (.add seen-messages message-id)
          (<- part dict (usage-of-claude-message message-usage message-model))
          (<- usage dict (add-usage usage part))
          (<- usage-frame dict (delta-frame job-id seq at "usage" part))
          (.append frames usage-frame)
          (setv seq (+ seq 1))))
      (when (isinstance content list)
        (for [block content]
          (when (isinstance block dict)
            (setv block-type (.get block "type"))
            (cond
              (and (= kind "assistant") (= block-type "text") (isinstance (.get block "text") str))
              (do
                (setv payload {"text" (get block "text")})
                (when (isinstance message-model str)
                  (setv (get payload "model") message-model))
                (<- text-frame dict (delta-frame job-id seq at "text" payload))
                (.append frames text-frame)
                (setv entry {"seq" seq "at" at "kind" "text" "text" (get block "text")})
                (when (isinstance message-model str)
                  (setv (get entry "model") message-model))
                (.append entries entry)
                (setv seq (+ seq 1)))
              (and (= kind "assistant") (= block-type "tool_use"))
              (do
                (setv tool-id (str (.get block "id" "")))
                (setv name (str (.get block "name" "")))
                (<- summary str (summary-of (.get block "input") 4000))
                (<- use-frame dict (delta-frame job-id seq at "tool_use"
                                                {"toolUseId" tool-id "name" name "summary" summary}))
                (.append frames use-frame)
                (.append entries {"seq" seq "at" at "kind" "tool_use"
                                  "toolName" name "summary" summary})
                (setv seq (+ seq 1)))
              (and (= kind "user") (= block-type "tool_result"))
              (do
                (setv tool-id (str (.get block "tool_use_id" "")))
                (setv result (.get block "content"))
                (<- summary str (summary-of result 4000))
                (setv size (len (.encode (json.dumps result :ensure-ascii False) "utf-8")))
                (<- result-frame dict (delta-frame job-id seq at "tool_result"
                                                   {"toolUseId" tool-id "summary" summary
                                                    "bytes" size
                                                    "isError" (bool (.get block "is_error" False))}))
                (.append frames result-frame)
                (.append entries {"seq" seq "at" at "kind" "tool_result" "summary" summary})
                (setv seq (+ seq 1)))
              True None))))))
  (DeltaBatch :frames (tuple frames) :entries (tuple entries) :usage usage
              :next-seq seq :model model))


(defk codex-deltas-of [records job-id seq-start at]
  {:pre [(: records tuple) (: job-id str) (: seq-start int) (: at int)]
   :post [(: % DeltaBatch)]}
  "codex の rollout の行(response_item の message / function_call /
   function_call_output・event_msg の token_count)→ frame と entries。読めない形は飛ばす。"
  (setv frames [])
  (setv entries [])
  (setv usage None)
  (setv seq seq-start)
  (for [record records]
    (setv kind (.get record "type"))
    (setv payload (.get record "payload"))
    (when (isinstance payload dict)
      (setv ptype (.get payload "type"))
      (cond
        (and (= kind "response_item") (= ptype "message") (= (.get payload "role") "assistant"))
        (for [block (or (.get payload "content") [])]
          (when (and (isinstance block dict) (isinstance (.get block "text") str))
            (<- text-frame dict (delta-frame job-id seq at "text" {"text" (get block "text")}))
            (.append frames text-frame)
            (.append entries {"seq" seq "at" at "kind" "text" "text" (get block "text")})
            (setv seq (+ seq 1))))
        (and (= kind "response_item") (= ptype "function_call"))
        (do
          (setv name (str (.get payload "name" "")))
          (<- summary str (summary-of (.get payload "arguments") 4000))
          (<- use-frame dict (delta-frame job-id seq at "tool_use"
                                          {"toolUseId" (str (.get payload "call_id" ""))
                                           "name" name "summary" summary}))
          (.append frames use-frame)
          (.append entries {"seq" seq "at" at "kind" "tool_use" "toolName" name "summary" summary})
          (setv seq (+ seq 1)))
        (and (= kind "response_item") (= ptype "function_call_output"))
        (do
          (setv output (.get payload "output"))
          (<- summary str (summary-of output 4000))
          (<- whole str (summary-of output 1000000000))
          (<- result-frame dict (delta-frame job-id seq at "tool_result"
                                             {"toolUseId" (str (.get payload "call_id" ""))
                                              "summary" summary
                                              "bytes" (len (.encode whole "utf-8"))}))
          (.append frames result-frame)
          (.append entries {"seq" seq "at" at "kind" "tool_result" "summary" summary})
          (setv seq (+ seq 1)))
        (and (= kind "event_msg") (= ptype "token_count"))
        (do
          (setv info (.get payload "info"))
          (setv total (if (isinstance info dict) (.get info "total_token_usage") None))
          (when (isinstance total dict)
            (setv #^ JSONObject part {"input" (int (.get total "input_tokens" 0))
                                      "output" (int (.get total "output_tokens" 0))
                                      "cacheWrite" 0
                                      "cacheRead" (int (.get total "cached_input_tokens" 0))})
            ;; token_count は累計なので和ではなく最新で置き換える
            (setv usage part)
            (<- usage-frame dict (delta-frame job-id seq at "usage" part))
            (.append frames usage-frame)
            (setv seq (+ seq 1))))
        True None)))
  (DeltaBatch :frames (tuple frames) :entries (tuple entries) :usage usage
              :next-seq seq :model None))


(defk deltas-of [agent-type text job-id seq-start at]
  {:pre [(: agent-type str) (: text str) (: job-id str) (: seq-start int) (: at int)]
   :post [(: % DeltaBatch)]}
  "transcript の追記(text)→ kind 別の TurnDelta の frame と entries。未知の kind は空。"
  (<- records tuple (parse-json-lines text))
  (cond
    (= agent-type "claude")
    (do (<- claude-batch DeltaBatch (claude-deltas-of records job-id seq-start at))
        claude-batch)
    (= agent-type "codex")
    (do (<- codex-batch DeltaBatch (codex-deltas-of records job-id seq-start at))
        codex-batch)
    True (DeltaBatch :frames #() :entries #() :usage None :next-seq seq-start :model None)))


(defk pane-frame [job-id seq at lines]
  {:pre [(: job-id str) (: seq int) (: at int) (: lines tuple)]
   :post [(: % dict)]}
  "pane の断面の frame(mode = full)。"
  (<- frame dict (delta-frame job-id seq at "frame"
                              {"mode" "full" "rows" (len lines) "lines" (list lines)}))
  frame)


(defk status-frame [job-id seq at phase]
  {:pre [(: job-id str) (: seq int) (: at int) (: phase str)]
   :post [(: % dict)]}
  "手番の進みの frame(running / waiting / ended)。"
  (<- frame dict (delta-frame job-id seq at "status" {"phase" phase}))
  frame)


(defk frame-lines-of [text]
  {:pre [(: text str)]
   :post [(: % tuple)]}
  "capture の text → 行の列(末尾の空行は落とす)。"
  (setv lines (list (.splitlines text)))
  (while (and lines (not (.strip (get lines -1))))
    (.pop lines))
  (tuple lines))


;; ---------------------------------------------------------------------------
;; capture の是非・待ちの長さ・周期(判断ではなく規則の写し)
;; ---------------------------------------------------------------------------

(defk capture-verdict [subscribers]
  {:pre [(: subscribers (| int None))]
   :post [(: % str)]}
  "購読者が居る時だけ capture する(issue #1 の決定 1 と 4)。0 = stop、正 = continue。
   None(中継が数を返さない)は「居る」と読まない = stop。"
  (if (and (isinstance subscribers int) (> subscribers 0)) "continue" "stop"))


(defk wait-seconds-for [state settings]
  {:pre [(: state AgentdState) (: settings AgentdSettings)]
   :post [(: % float)]}
  "次の watch の待ちの上限: capture 中は frame の間隔、手番が走っていれば transcript の
   周期、何も無ければ idle の上限。"
  (cond
    (any (gfor job state.jobs job.capturing)) (float settings.frame-interval-seconds)
    state.jobs (float settings.transcript-poll-seconds)
    True (float settings.idle-wait-seconds)))


(defk due [last-ms now-ms period-seconds]
  {:pre [(: last-ms (| int None)) (: now-ms int) (: period-seconds (| int float))]
   :post [(: % bool)]}
  "周期の拍か(last が None = まだ 1 度も = 今)。"
  (or (is last-ms None)
      (>= (- now-ms last-ms) (* 1000 period-seconds))))


(defk resync-due [signal state now-ms settings]
  {:pre [(: signal WatchAdvance) (: state AgentdState) (: now-ms int) (: settings AgentdSettings)]
   :post [(: % bool)]}
  "list を読み直す拍: sequence が進んだ・gap・接続の張り直し・周期の保険。"
  (<- periodic bool (due state.last-resync-ms now-ms settings.watch-resync-seconds))
  (or (in signal.kind #{"changed" "gap" "closed"}) periodic))


(defk lease-renew-due [job now-ms settings]
  {:pre [(: job InFlightJob) (: now-ms int) (: settings AgentdSettings)]
   :post [(: % bool)]}
  "借りた札の錠の期限の margin 秒前に借り直す。"
  (and (is-not job.lease-hold-ms None)
       (>= (+ now-ms (* 1000 settings.lease-renew-margin-seconds)) job.lease-hold-ms)))


(defk job-outcome-of [view]
  {:pre [(: view SessionView)]
   :post [(: % JobOutcome)]}
  "器の眺めから手番の結末を読む(既存の turn-end の意味論 = 行の status が終端 —
   policy.hy の monitor が turn-end で done へ倒す。語彙は effects.SESSION-TERMINAL-STATUSES)。done 以外の終端は SessionFailed の
   condition(理由 = terminal_cause の category と reason)。"
  (if (not-in view.status SESSION-TERMINAL-STATUSES)
      (JobOutcome :ended False :result None :conditions #())
      (do
        (setv conditions [])
        (when (!= view.status "done")
          (setv cause (or view.terminal-cause {}))
          (setv category (.get cause "category"))
          (setv reason (.get cause "reason"))
          (<- failed dict (condition-of "SessionFailed"
                                        (+ f"session {view.status}"
                                           (if (isinstance category str) f": {category}" "")
                                           (if (isinstance reason str) f" ({reason})" ""))))
          (.append conditions failed))
        (JobOutcome :ended True :result view.result-payload :conditions (tuple conditions)))))


(defk without-job [state job-id]
  {:pre [(: state AgentdState) (: job-id str)]
   :post [(: % AgentdState)]}
  (replace state :jobs (tuple (lfor job state.jobs :if (!= job.job-id job-id) job))))


(defk with-job [state job]
  {:pre [(: state AgentdState) (: job InFlightJob)]
   :post [(: % AgentdState)]}
  "同じ job_id の行を置き換える(無ければ足す)。"
  (setv kept (lfor existing state.jobs :if (!= existing.job-id job.job-id) existing))
  (replace state :jobs (tuple (+ kept [job]))))


(defk in-flight-ids [state]
  {:pre [(: state AgentdState)]
   :post [(: % set)]}
  (set (gfor job state.jobs job.job-id)))
