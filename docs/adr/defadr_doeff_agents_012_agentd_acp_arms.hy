;;; Executable ADR: sessionhost の agentd の腕(段 2・agora-redesign #19 / #20)—
;;; ACP の cluster に参加して agent-job を受け、手番の記録(turn-record)と実況(TurnDelta)を
;;; ACP へ書き、資格は custody から借りる。判断は持たない。
;;;
;;; 出自 = agora-redesign の設計 docs/design/design-entities-and-boundaries-2026-09-11.md
;;; 第 12.10 節(main node = ACP + custody + scheduling operator・各 node = 仕事を受けるだけの
;;; agentd)・第 11.13 節(資格は custodian・agentd が借りる)・第 17 節(TurnDelta)・第 16 節
;;; 段 2、bounded context F(docs/design/bounded-contexts-2026-09-11.md)、issue #1(capture は
;;; 購読者が居る時だけ・購読 0 で止める — operator 承認 2026-09-11)、実装依頼書
;;; docs/impl-requests/stage2-lane-prompts/lane-2b-agentd.md 7.(法 (a)〜(e))。
;;; 改訂 2026-09-12(lane 2b-2・同 lane-2b2-agentd-fix.md): 段 2 の受入の e2e の実弾 2 つ —
;;; job aj-stage2-e2e-003(片付いた session の capture が例外で tick ごと落ち Running のまま)と
;;; aj-stage2-e2e-002(ACP の list が落ちて Running のまま孤児・Bound しか拾わないので戻らない)—
;;; から R7(job の進みは行から導く)・R8(capture の gone は終端の合図)・R9(tick の縁)を足す。
;;;
;;; 改訂 2026-09-12(lane 2b-3・同 lane-2b3-warm-session.md): 段 2 の受入の計器「郵便から agent の
;;; stdin まで p99 < 2 秒」に対し本番の実測は create → send の p50 11.0 秒 / p99 16.9 秒 — 内訳は
;;; tmux で claude の tui を毎手番 cold に起こす約 10 秒(watch → claim → send は 1 秒台)。設計
;;; 第 17.4 節の「温かい session」(会話の session を手番の間も生かし、次の手番は send だけ)を
;;; R10 として足す。
;;;
;;; 改訂 2026-09-12(lane 2d・同 lane-2d-headless-backend.md・agora-redesign #37): operator 指示
;;; 逐語 "make sure to have claude/codex headless mode support with streaming and interrupt support"。
;;; sessionhost の backend は tmux | herdr(tui の pane)だけで、headless(claude -p stream-json /
;;; codex app-server)が無く、実況は pane の frame と transcript 由来、取り下げ(Withdrawn)は
;;; session の片付けだった。R11(headless backend・print mode の唯一の家)・R12(events の実況は
;;; 純関数の写像・streamCapability は backend から)・R13(withdraw は中断の合図・session は残す)・
;;; R14(追補: watch の拍は差分の読み・計器の始点は生まれの着地 — 本番の温かい path の実測
;;; create → send p50 4.7 s / p99 5.3 s と、agent-job の resourceCreatedAt が秒の粒度である実測)を足す。
;;;
;;; 改訂 2026-09-12(lane 2d-2・同 lane-2d2-codex-headless-shim.md の追補): 本番の e2e で headless の
;;; claude の launch の腕が charter の prompt で 1 手番目の process を起こした直後に、after-start が
;;; inputs の郵便を session.send し、host が同じ session の名で --resume の process を spawn して
;;; `headless session already exists` で落ちた(1 手番 1 process — 走っている手番の途中に次を
;;; 起こせない)。根 = launch(charter の prompt)と send(郵便)を 2 手番として撃つこと。R16
;;; (headless の起こす手番は郵便を 1 手番目の本文に畳む・send は撃たない)を足す。tui は今日どおり。
;;;
;;; 置き場 = packages/doeff-agents/src/doeff_agents/sessionhost/acp/(effects.py = 要求と値の
;;; 型・judgment.hy = 純粋な判断・agentd.hy = program・handlers.py = 実 I/O・fake.py = test の
;;; handler・valve.py = 弁・runtime.py = composition root・entry.py = console script の入口)。
;;; agentd は host の socket の client。段 2 の初版では host.hy / hostmain.py / impls / policy を
;;; 1 行も変えなかったが、R10(温かい session)は host の公開 RPC の最小の追補を要した —
;;; lifecycle の閉語彙に multi_turn(launch.hy)・turn-end の連言の結果を行に刻む turn_ended_at
;;; (policy.hy の monitor 1 点・store の列・wire)・session.send の awaiting(host.hy)。弁と
;;; agentd の腕は引き続き host の内側に無い(針 test-adr-doe-agents-012-valve-defaults-off)。
;;;
;;; 段 6 lane 6f(agora-redesign #26・設計 第 12.6 節 決定 23)の追補 = R17: 機体を足す手順は
;;; 1 命令 `doeff-sessionhost join --server <URL> --token-file <札>`(宣言 file `--config` は k3s の
;;; config と同じ規律 — flag と同名の鍵・flag が優先)。判断は join.hy の 1 点(join-spec-of →
;;; join-plan-of)で、今日の serve --acp の起動が読む env の束と host の argv を宣言から導く —
;;; 読み手(runtime / valve / host.hy)は増やさない。所有の等級(ownership)は検の方法(proof)と対で
;;; 宣言し、thread を起こす前に ownership-preflight(gce-project = metadata server の project-id・
;;; declared = 検なし)で突合、不一致は参加しない(会社 profile の API 呼び出しは会社所有の機体だけ)。
;;; 検めた等級は node の observations.ownership に名乗る(E の spec.labels.boundary との突合の材料)。
;;;
;;; 段 7 lane 7d-3(agora-redesign 7d の登記済み間隙 2)の追補 = R18: 契約 profile の status.observed
;;; (window・remaining・resetAt・observedAt・node)の書き手は agentd(writers.status.observed = ["agentd"])
;;; なのに本番の profile 35 行に観測が 1 つも無く、予算の controller(agora-budget・lane 7d)が
;;; ProfileExhausted を判じられなかった。既知の形 = kubelet の node status(観測は runner が書き、判断は
;;; controller)。agentd は遅い周期(AgentdSettings.profile_observe_seconds)で、この機体が持つ資格の
;;; profile ごとに残量を読み(ReadProfileUsage — 読み口は dotfiles agentcli の usage の 1 点 `ai usage --json`・
;;; その前に器の家の在否 ListProfileHomes = 登録簿 `agentcli profiles list --json` × dir の実在で観測する行を絞り、
;;; 家の在る profile が無い機体〔pool の pod〕は usage を撃たず 1 度だけ名乗る — 段 8e lane 4j・
;;; 会社境界の判定はその葉)、judgment.profile-observed-of の 1 点で観測を決め、committed の行から組んだ
;;; post-image を ifGeneration で変わった時だけ書く。断られた / 単位の違う profile は書かず理由を log に 1 行。
;;;
;;; 段 8 lane 4u(agora-redesign #49)の追補 = R19: 手番の出来事の耐久化。本番の turn-record 97 行の entries は
;;; 「最後の本文 1 行・at は全部同じ」で、headless の stream(assistant の本文・tool_use / tool_result・system)は
;;; 実況の中継(tail)で流れるだけで耐久化されず、会話の面(郵便 + entries を時刻順)に agent の出力の全史が
;;; 出なかった(operator 逐語 "a conversation view is supposed to show all history of agent outputs with user
;;; inputs and system inputs like a chat")。既知の形 = event-sourced の出来事の列(append-only・durable):
;;; runner(agentd)が出来事の書き手、control plane(行)が正本、画面は read model。agentd は実況の材料を
;;; 読む拍ごとに、その拍の出来事(text / tool_use / tool_result / system / error)を turn-record の
;;; status.entries へ**追記**する(append-entries — informer と同じ CAS・Conflict は読み直して積み直す・
;;; 断られた出来事は持ち越す)。行の上限(契約 conventions.turnRecordEntries の写し = effects.py の
;;; TURN_RECORD_ENTRIES_BYTE_BUDGET)は judgment の純関数。
;;; 手番の終わりは最後の材料を同じ拍で読んで追記した上に ended・usage を書く(entries を置換しない)。
;;;
;;; 段 9f lane 9f-2 / 9f-4(agora-redesign #59・設計 conversation-record-service §2.2 / §2.4)の改訂 = R19 / R20 の追補:
;;; 本文は会話の記録の service へ(spool → appendEvents・冪等)、ACP の turn-record の entry は**見出しの閉じた欄**
;;; (effects.TurnEntryHeadline — seq・at・kind・toolName・toolUseId・bytes・sha256・isError。本文の欄 text / summary /
;;; input / output / model は型に無い)。既知の形 = claim check(control plane には参照と見出し・本文は記録の service)。
;;; 見出しを導く点は judgment.headline-of-body の 1 つ・JSON への写しは entry-json-of の 1 つ・bytes / sha256 は service の
;;; 冪等の判断と同じ計算(record-body-bytes-of)。受理の答え(highestProducerSeq)は status.recordRef / recordedSeq に写す
;;; (agentd.mark-recorded・judgment.turn-record-recorded-status)。履歴からの再開は service の before=latest から後向きに
;;; 読み(agentd.record-turns-for・effect RecordRead)、届かない時は ACP の見出しで薄く再開すると名乗る(HeadlineTurns —
;;; 本文の無い行を本文として扱わない・型で分ける)。
;;; 段 9f lane 9f-6(同 #59)の改訂 = R17 の追補: 本文の行き先(record の宛先)を持たない agentd は参加を断る — 判断は
;;; join.record-sink-of の純関数 1 点・読みは runtime.settings_from_env の 1 点・断りは AgentdPreflightError(理由 = 宣言の
;;; 置き場)。既知の形 = runner の参加の門(宣言された依存先の検・推測せず宣言で断る)。宛先が在って届かないのは spool。
;;; 段 9o lane 9o-3(agora-redesign #75)の改訂 = R20 の追補: 家の鍵(judgment.session-affinity-key-of)は account・binding・**model** の組。
;;; 走っている CLI の session は起こした時の model のまま手番を回す(session.send に model の欄は無く、headless の続きの process も
;;; 器の行の model で起きる)ので、会話の宣言の model だけを変えた手番も温かい session へ送らず、片付けて履歴から再開する(新しい
;;; session は charter.model で起き、本文は記録の service の before=latest から)。片付いた session も model が違えば --resume しない。
;;; 判断は next-arm-for-job の 1 点のまま(第 2 の判定点を作らない)。既知の形 = virtual actor の器の再利用の鍵に宣言の欄を含める。
;;; この版より前に起こした session の刻みには model が無い = 違う家と読み、次の手番で 1 度だけ履歴から再開する(互換の枝を持たない)。
;;; 段 10 lane 10c 便 2(agora-redesign #80)の改訂 = R23: 手番の資格の出所は judgment.credential-source-of の 1 点(lease / missing /
;;; home)。預かり所を宣言した node(AgentdSettings.custody_declared — runtime.settings_from_env が CUSTODY_URL_ENV の在否から導く 1 点)
;;; は status.binding.account の無い Bound の job を起こさず、条件 CredentialSourceMissing で閉じる。charter の binding(機体の profile の
;;; 家)で起こす経路は宣言の無い node だけ。session を使い回す鍵は session-affinity-key-of(旧名 home-key-of — 資格ではないことを名で
;;; 分かるように)。ACP の側の半分(配置が会話の profile から預かり所の account を解いて結ぶ)は ACP の法 c744ca(L769)。
;;; 段 10 lane 10e 便 2(agora-redesign #53・設計 agent-settings-c4-context-map 第 9 節 問 3 / 問 5)の改訂 = R24: node は能力の表
;;; (status.capabilities = agent の種類ごとの settings / restartOn — effects.AGENT-CAPABILITIES の写し・judgment.capabilities-of)を
;;; lease と同じ拍に名乗り、restartOn は session-affinity-key-of の鍵の欄(model・profile)ちょうど。effort は鍵に入れず、同じ家で
;;; effort だけ違う温かい session は片付けて同じ session を新しい旗で --resume(next-arm-for-job の effort の腕・cache は保つ)。
;;; 効かない宣言の欄(受けない種類・温かい send で違う workDir)は条件 AgentSettingIgnored(judgment.ignored-settings-of の 1 点)。
;;; ACP の側(会話の宣言の effort・許可名簿の投影・charter.effort)は ACP の法 eda1e8(L771)。
;;; 段 10 lane 10h 便 1(agora-redesign #84・実弾 2026-09-14 14:35〜18:5x: agentd の kickstart -k で headless の子 process が
;;; 道連れになり、行は running のまま・agentd は observe / defer を返し続け、会話が永久に「動いている」・次の郵便が Pending)の
;;; 改訂 = R25: backend の生死は host の観測で決め、status の語から推測しない。host = 起動時の復帰(headless.hy
;;; recover-headless-rows・判断 headless_protocol.recovery_verdict の 1 点: 手番の途中 ∧ backend が死んでいる → exited + vanished)と
;;; wire の backend_alive(session.get / session.list が毎回観測)。agentd = judgment.backend-alive(観測の無い眺めは生きていると読む —
;;; 観測断 ≠ 死亡)を job-step-of(session-lost → 記録の腕と条件 SessionLost)と next-arm-for-job(手番の途中でも backend が死んで
;;; いれば待たず、候補を片付けて resume / rehydrate)が読む。resume の腕の KeyError 2 つ(store.hy の cause の decode・launch.hy の
;;; events_root)も同じ便で直した(既知の形 = kubelet が node の再起動の後に container の生死を観測して pod の状態を直す)。
;;; 便 2 = R26: 停止(TERM)で子を黙って道連れにしない — host は TERM の 1 度目に accept を生かしたまま別 thread で停止の hook
;;; (entry.py が登録する agentd の close_for_stop = 走っている job を turn-record ended・Ended〔AgentdRestart〕)→ headless の行の停止
;;; (stop-headless-rows: 手番の途中の行を stopped + cancelled・全 process を並列の猶予で降ろす・判断 headless_protocol.stop_verdict の
;;; 1 点)を走らせ、自分に同じ信号を撃ち直して 2 度目で SystemExit。headless の子を pipe から切り離して拾い直す形(detach)は取らない —
;;; stdin / stdout の pipe の親を失った process は器として使えない(events file の書き手も Dialogue も host に在る)。ACP の宛先
;;; (実況の push を含む)は宣言 ACP_DAEMON_URL ちょうどで、127.0.0.1:8868 の既定値は消した(宣言の無い agentd は参加しない)。
;;; 段 10f 便 2(agora-redesign #82・operator 2026-09-14 逐語 "that routing agent should compact itself with some threshold")の
;;; 改訂 = R27: 会話の宣言 status.agent.compactAt(文脈の使用率 % の閾値・任意・書き手 agora-conversation)を、直前の手番の
;;; 文脈の使用率が超えていたら、次の手番を履歴からの再開(rehydrate — R20 の腕そのまま)で起こす。turn-record の usage に同等の
;;; 欄が無い(token の和は文脈の大きさではない)ので実測は agentd: 手番の終わりに材料の末尾(claude = 最後の assistant の usage の
;;; 入力側 + 出力と result の modelUsage[model].contextWindow / codex = last_token_usage と model_context_window)から測り
;;; (judgment.context-percent-of)、session ごとに memory(AgentdState.context_by_session)に持つ。判断は next-arm-for-job の
;;; 1 点に条件 compact(judgment.compaction-due = 宣言あり ∧ 実測あり ∧ 実測 ≥ 閾値)を足す形。計器 agentd_compactions_total{conversation}。

(require doeff-adr.macros [defadr rule law])
(require doeff-hy.macros [deftest])
(import doeff-adr.macros [fact interpretation counterexample])
(import json)
(import re)
(import dataclasses [replace])
(import pathlib [Path])
(import doeff [run])
(import doeff_agents.sessionhost.acp.effects
        [AGENT-JOB-KIND AGENT-JOB-NAMESPACE AGORA-KINDS-NAMESPACE AcpRow AgentdSettings
         AgentdState CaptureGone InFlightJob JSONObject JoinArgv JoinDeclaration JoinPlan JoinSpec MESSAGE-KIND
         NODE-KIND Ownership PHASE-BOUND PHASE-ENDED PHASE-RUNNING PROFILE-KIND PROFILE-USAGE-KIND
         ProfileHome ProfileUsage ProfileUsageUnavailable TURN-RECORD-KIND UsageWindow])
(import doeff_agents.sessionhost.acp.fake [Birth FakeAcp FakeCustody FakeLocal FakeSessions])
(import doeff_agents.sessionhost.acp.join [join-plan-of join-spec-of ownership-preflight])
(import doeff_agents.sessionhost.acp.judgment [capture-verdict job-step-of record-due stream-capability-of-backend
                                               wait-seconds-for])
(import doeff_agents.sessionhost.acp.runtime [AgentdPreflightError initial-state install run-tick settings-from-env])
(import doeff_agents.sessionhost.acp.valve [ACP-VALVE-DEFAULT ACP-VALVE-ENV acp-valve])


;; ---------------------------------------------------------------------------
;; 針の共通部品: sessionhost の source の code 行(註釈を除く)
;; ---------------------------------------------------------------------------

(setv SESSIONHOST-DIR
      (/ (. (Path __file__) parent parent parent)
         "packages" "doeff-agents" "src" "doeff_agents" "sessionhost"))
(setv ACP-DIR (/ SESSIONHOST-DIR "acp"))

;; agora の台帳 API の語(agentd の出口は ACP と custody だけ — herdr-hud daemon の
;; 状態台帳・手番の配車・headless の席の口を sessionhost は 1 語も知らない)。
(setv AGORA-LEDGER-WORDS
      ["/api/state" "turn-jobs" "seat-turn-jobs" "seat-open-workers" "/api/headless"
       "/api/turn-jobs" "agmsg" "kickAgmsgWake"])


(defn #^ list code-lines [#^ Path path]
  "註釈(;; / #)と空行を除いた code 行の列。"
  (setv out [])
  (for [line (.splitlines (.read-text path :encoding "utf-8"))]
    (setv stripped (.lstrip line))
    (when (and stripped
               (not (.startswith stripped ";"))
               (not (.startswith stripped "#")))
      (.append out line)))
  out)


(defn #^ list source-files []
  (sorted (+ (list (.rglob SESSIONHOST-DIR "*.hy"))
             (list (.rglob SESSIONHOST-DIR "*.py")))))


(defn #^ list defk-body [#^ list lines #^ str name]
  "code 行の列から `(defk <name> ` の頭から次の頂点の form(行頭が `(`)の手前までを切り出す(針が腕の中身を読む)。"
  (setv out [])
  (setv inside False)
  (for [line lines]
    (cond
      (.startswith line f"(defk {name} ") (do (setv inside True) (.append out line))
      (and inside (.startswith line "(")) (break)
      inside (.append out line)))
  out)


(defclass World []
  "fake の 4 handler + 値の宣言 + Node の行(法の反例を撃つ最小の世界)。"
  (defn #^ None __init__ [self]
    (setv self.settings (AgentdSettings :node-name "mac-1" :homes-root "/homes"))
    (setv self.acp (FakeAcp :births {TURN-RECORD-KIND (Birth "state" "running")}))
    (.put-row self.acp (AcpRow :namespace AGORA-KINDS-NAMESPACE
                               :key f"{AGORA-KINDS-NAMESPACE}:{NODE-KIND}:mac-1"
                               :kind NODE-KIND :resource-id "mac-1" :version "v1"
                               :generation 1 :created-at-ms 0 :labels {} :payload {}
                               :spec {"name" "mac-1" "labels" {} "capacity" 1 "streamCapability" "frames"}
                               :status {"state" "joined"}))
    (setv self.custody (FakeCustody :tokens {"acct" "sk-ant-oat01-secret"}
                                    :auth-jsons {"acct" "{\"tokens\": {}}"}))
    (setv self.sessions (FakeSessions))
    (setv self.local (FakeLocal :now-ms 1000))
    (setv self.state (initial-state)))

  (defn #^ None tick [self #^ int advance-ms]
    (setv self.local.now-ms (+ self.local.now-ms advance-ms))
    (setv self.state
          (run-tick self.settings self.state
                    [self.acp.dispatch self.custody.dispatch
                     self.sessions.dispatch self.local.dispatch]))
    None))


(defn #^ AcpRow bound-row [#^ str job-id #^ str node #^ (| str None) account #^ str agent-type
                           #^ str phase]
  (setv #^ JSONObject binding {"node" node "profile" "personal"})
  (when (is-not account None)
    (setv (get binding "account") account))
  ;; charter の id は agentd が読まない(鋳造する — 追補 2)。読んだら針が割れる綴りにする。
  (setv #^ JSONObject charter {"session_id" f"charter-{job-id}" "session_name" f"charter-{job-id}"
                               "agent_type" agent-type
                               "work_dir" "/work" "prompt" "go"
                               "binding" {"kind" "codex" "codex_home" "/bundle"}})
  (setv #^ JSONObject spec {"subject" job-id "inputs" [] "charter" charter})
  (setv #^ JSONObject status {"phase" phase "binding" binding})
  (AcpRow :namespace AGENT-JOB-NAMESPACE
          :key f"{AGENT-JOB-NAMESPACE}:{AGENT-JOB-KIND}:{job-id}"
          :kind AGENT-JOB-KIND :resource-id job-id :version "v1" :generation 1
          :created-at-ms 500 :labels {} :payload {}
          :spec spec
          :status status))


(defn #^ AcpRow turn-row [#^ str job-id #^ str subject #^ str message-id #^ int created-at-ms]
  "会話 subject の 1 手番の Bound の行(inputs = message-id・charter に lifecycle は無い =
   agentd の既定 multi_turn)。"
  (setv base (bound-row job-id "mac-1" None "claude" PHASE-BOUND))
  (setv #^ JSONObject spec (dict base.spec))
  (setv (get spec "subject") subject)
  (setv (get spec "inputs") [message-id])
  (AcpRow :namespace base.namespace :key base.key :kind base.kind :resource-id base.resource-id
          :version base.version :generation base.generation :created-at-ms created-at-ms
          :labels base.labels :payload base.payload :spec spec :status base.status))


(defn #^ AcpRow message-row [#^ str message-id #^ str body]
  (AcpRow :namespace AGORA-KINDS-NAMESPACE
          :key f"{AGORA-KINDS-NAMESPACE}:{MESSAGE-KIND}:{message-id}"
          :kind MESSAGE-KIND :resource-id message-id :version "v1" :generation 1
          :created-at-ms 0 :labels {} :payload {}
          :spec {"id" message-id "body" body}
          :status {"state" "inbox"}))


(defn #^ None run-warm-turn [#^ World world #^ str job-id #^ str subject #^ str body]
  "1 手番を温かい session で回す: Bound を置く → 受け(launch か send)→ 記録が進む → host が
   手番の終わりを刻む → Ended。"
  (.put-row world.acp (message-row f"m-{job-id}" body))
  (.put-row world.acp (turn-row job-id subject f"m-{job-id}" (- world.local.now-ms 300)))
  (.tick world 1000)
  (setv job (status-of (get world.acp.rows f"acp-system:agent-job:{job-id}")))
  (setv session-id (str (get (object-at job "sessionHandle") "sessionId")))
  (setv path f"/homes/claude/acct/projects/-work/{session-id}.jsonl")
  (setv (get world.local.transcripts path)
        (+ (.get world.local.transcripts path "")
           "{\"type\": \"assistant\", \"message\": {\"role\": \"assistant\", \"id\": \"m\", \"content\": [{\"type\": \"text\", \"text\": \"ok\"}]}}\n"))
  (.tick world 1000)
  (.finish-turn world.sessions session-id (+ world.local.now-ms 100))
  (.tick world 1000)
  None)


(defn #^ AcpRow running-row [#^ str job-id #^ str node #^ str owner]
  "agentd が claim した後の行(phase Running + sessionHandle{stream.owner})— 再起動後に
   list で映る形。"
  (setv base (bound-row job-id node None "claude" PHASE-RUNNING))
  (setv #^ JSONObject status (dict (status-of base)))
  (setv (get status "sessionHandle")
        {"sessionId" job-id "stream" {"owner" owner "name" job-id}})
  (AcpRow :namespace base.namespace :key base.key :kind base.kind :resource-id base.resource-id
          :version base.version :generation base.generation :created-at-ms base.created-at-ms
          :labels base.labels :payload base.payload :spec base.spec :status status))


(defn #^ str sid-of [#^ World world #^ str job-id]
  "job が使っている session の id(行の sessionHandle — agentd が鋳造した綴り。charter の id ではない)。"
  (setv status (status-of (get world.acp.rows f"acp-system:agent-job:{job-id}")))
  (str (get (object-at status "sessionHandle") "sessionId")))


(defn #^ JSONObject status-of [#^ AcpRow row]
  "行の status(test の読み — 無い行は空)。"
  (setv status row.status)
  (if (isinstance status dict) status {}))


(defn #^ JSONObject object-at [#^ JSONObject value #^ str key]
  "JSON の object の欄を object として読む(test の読み — object でなければ空)。"
  (setv item (get value key))
  (if (isinstance item dict) item {}))


(defn #^ str last-condition-type [#^ JSONObject status]
  "status.conditions の末尾の type(test の読み — 無ければ空文字)。"
  (setv conditions (.get status "conditions"))
  (when (not (isinstance conditions list))
    (return ""))
  (when (not conditions)
    (return ""))
  (setv last (get conditions -1))
  (if (isinstance last dict) (str (.get last "type" "")) ""))


(defadr ADR-DOE-AGENTS-012
  :title "sessionhost の agentd の腕: 出口は ACP と custody だけ・判断は『自分に結ばれた job か』の純関数 1 点だけ(binding は書かない)・弁の既定は off・購読 0 で capture が止まる・借りた札は家の中の auth file 以外の平文で disk に残さない・job の進みは行から導く(自分の Running は再起動後も拾い、次の 1 手は job-step-of の 1 点)・capture の gone は終端の合図で例外ではない・tick の縁は互いの失敗で止まらない・session は会話の資源で job は手番(同じ会話の次の手番は launch せず send・判断は next-arm-for-job の 1 点・idle の寿命は値の宣言 1 点)・headless backend(print mode の家は 1 つ・events の写像は純関数・withdraw は中断の合図・watch の拍は差分の読み)・機体を足す手順は 1 命令 join(宣言 → env の束の座は 1 つ・所有の等級は検と対で名乗る)"
  :status "accepted"
  :scope ["packages/doeff-agents/src/doeff_agents/sessionhost/acp/effects.py"
          "packages/doeff-agents/src/doeff_agents/sessionhost/acp/judgment.hy"
          "packages/doeff-agents/src/doeff_agents/sessionhost/acp/agentd.hy"
          "packages/doeff-agents/src/doeff_agents/sessionhost/acp/handlers.py"
          "packages/doeff-agents/src/doeff_agents/sessionhost/acp/fake.py"
          "packages/doeff-agents/src/doeff_agents/sessionhost/acp/valve.py"
          "packages/doeff-agents/src/doeff_agents/sessionhost/acp/join.hy"
          "packages/doeff-agents/src/doeff_agents/sessionhost/acp/runtime.py"
          "packages/doeff-agents/src/doeff_agents/sessionhost/acp/entry.py"
          "packages/doeff-agents/tests/test_sessionhost_acp.py"
          "packages/doeff-agents/src/doeff_agents/sessionhost/launch.hy"
          "packages/doeff-agents/src/doeff_agents/sessionhost/policy.hy"
          "packages/doeff-agents/src/doeff_agents/sessionhost/effects.hy"
          "packages/doeff-agents/src/doeff_agents/sessionhost/store.hy"
          "packages/doeff-agents/src/doeff_agents/sessionhost/host.hy"
          "packages/doeff-agents/src/doeff_agents/sessionhost/headless.hy"
          "packages/doeff-agents/src/doeff_agents/sessionhost/headless_protocol.py"
          "packages/doeff-agents/src/doeff_agents/sessionhost/headless_process.py"
          "packages/doeff-agents/src/doeff_agents/sessionhost/substrate_headless.hy"
          "packages/doeff-agents/src/doeff_agents/sessionhost/impls/headless_argv.hy"
          "packages/doeff-agents/tests/test_sessionhost_headless.py"
          ".semgrep.yaml"]
  :problem
    [(fact
       "今日の手番の配車は agora(herdr-hud daemon)の turn-jobs / turn-dispatcher / headless fleet が持ち、sessionhost は観測されるだけ(sessionhost-client.ts:56)で、agent-job を受ける腕も TurnRecord / TurnDelta を書く腕も custody から借りる腕も無い。"
       :evidence "agora-redesign docs/integration/stage2-grounding.md(計画に効く穴 3 つ)")
     (fact
       "採用形は main node = ACP + custody の app + scheduling operator、各 node = 仕事を受けるだけの agentd(判断を持たない)。共有状態は ACP の store 1 つで、agentd は cluster の状態を持たず自分に結ばれた job を pull で受ける(NAT / tailscale の向こうの Mac に inbound は要らない)。"
       :evidence "設計 第 12.10 節・operator 逐語 2026-09-11 \"agentd can be kept simple agent job runner\"")
     (fact
       "資格の所有は ACP に行かない: 保管と回転は custodian、agentd は session を起こす時に借りる(/lease/claude は env CLAUDE_CODE_OAUTH_TOKEN に入れる access token ちょうど・資格 file は書かない、/lease/codex は $CODEX_HOME/auth.json の中身ちょうど)。"
       :evidence "設計 第 11.13 節・herdr-hud packages/custodian/src/server.ts handleClaudeLease / handleCodexLease の応答")
     (fact
       "実況の frame(pane の capture・2〜5 Hz)は購読者が居る時だけ取り、購読 0 で止める(rate を落とすだけでは 50 session × 5 Hz が残る)。中継の push の応答が subscribers を返す(段 2 lane 2a)。"
       :evidence "agora-redesign issue #1(operator 逐語 2026-09-11 \"推奨通りで\")・lane-2a 3.")
     (fact
       "共通の品質検査(dotfiles agent/quality)の Hy の投影は defk / deftest / <- だけを扱い、deff / defhandler を持つ既存 file は欠測(incomplete)になる — host.hy に弁を足すと段 2 の受入(passed)が構造的に満たせない。"
       :evidence "dotfiles agent/quality/hy_projection.py Lowering.declaration_import(require の macro は未対応)")
     (fact
       "段 2 の受入の e2e(2026-09-12 01:3x・~/.cache/acp-stage2-e2e/logs/agentd.log): job 003 は claim → launch → 実況 → turn-record まで成功した後、手番の終わりに sessionhost が session を片付け(run_to_completion の cleanup で pane が消え、唯一の window だったので tmux の server も exit)、agentd の frame の capture が RuntimeError(tmux capture-pane failed: no server running)を上げて tick ごと落ち、job は Running・turn-record は running・result 無しのまま残った。observe の順序が transcript → capture → 終端の判定だったので、器が既に done でも capture を先に撃っていた。"
       :evidence "agentd.log: `agentd: tick failed: AgentdClientError: tmux capture-pane failed: no server running` × 13 拍・lane-2b2-agentd-fix.md 実弾")
     (fact
       "job 002 は本番の pod の入れ替え中に ACP への list が Connection reset by peer で落ち、同じく tick ごと落ちて Running のまま孤児になった。agentd は bound-to-me(phase == Bound)しか拾わず、job の進みを process の memory(InFlightJob)にだけ持っていたので、再起動しても二度と戻らなかった。"
       :evidence "agentd.log: `agentd: tick failed: RuntimeError: agentd: ACP list of agent-job failed: [Errno 54] Connection reset by peer`・旧 agentd.hy receive-bound-jobs(Bound のみ)")
     (fact
       "段 2 の受入の計器「郵便から agent の stdin まで p99 < 2 秒」に対し、本番の e2e(この Mac・2026-09-12 02:1x)の実測は create → send の p50 11.0 秒 / p99 16.9 秒。内訳は tmux で claude の tui を毎手番 cold に起こす時間(約 10 秒)で、watch → claim → send そのものは 1 秒台。1 job = 1 session(run_to_completion)で手番の終わりに sessionhost が session を片付け、affinity.predecessor があっても session.resume(cold)で起こし直していた。"
       :evidence "~/.cache/acp-stage2-e2e/logs/agentd-2.log の計器 agent-job-to-send(ms 16880 / 11802 …)・lane-2b3-warm-session.md")
     (fact
       "sessionhost に『手番の終わりで片付けず、かつ手番の終わりを観測する』lifecycle は無かった: run_to_completion は turn-end で done へ倒れ cleanup で pane が消える、interactive は monitor の最初の腕(reap-exempt)で観測ごと素通りされ turn-end の連言が評価されない。turn_open / turn_close の hook の打刻は adopted の行にしか落ちない(turn.hy db-resolve-turn-target)ので agentd が起こす session には使えない。"
       :evidence "policy.hy monitor-session-once(reap-exempt の腕・turn-ended の連言・is-run-to-completion の分岐)・turn.hy")
     (fact
       "sessionhost の backend の閉語彙は tmux | herdr(host.hy)で headless が無い。段 2 の agentd は tmux で claude の tui を起こし、frames(pane の断面)と transcript 由来の text / usage を実況にしていた。agora の headless fleet(k8s Job + dotfiles agentcli/headless.py: claude -p --output-format stream-json・codex app-server)は sessionhost を通らない別系。取り下げ(Withdrawn)は agentd が session.cleanup で片付けていた(手番の中断ではない)。"
       :evidence "agora-redesign issue #37・host.hy parse-args の backend の閉語彙・旧 agentd.hy withdraw-sessions")
     (fact
       "本番の温かい path の実測(2026-09-12): arm=send で create → send が p50 4.7 s / p99 5.3 s。agent-job の行の resourceCreatedAt は秒の粒度(`2026-09-11T18:42:10Z`)で、生まれの event(SpecApplied)の post-image の resourceLandedAt / envelope の eventObservedAt は ns 精度(`18:42:10.91213204Z`)。watch で起きた拍に agentd は agent-job と message を全量 list していた(GET /api/resources?kind= = loadCurrentState の全 state)。"
       :evidence "計画の会話の追補 2026-09-12・ACP `GET /api/event-window?after=5215004&limit=1` の実測・src/Acp/App/Server.hs の event-window(cursor-only・postDeltas の post-image)")
     (fact
       "今日の agentd の起動は env の束(DOEFF_AGENTD_ACP・ACP_DAEMON_URL・ACP_AGENTD_TOKEN_FILE・DOEFF_AGENTD_NODE_NAME・DOEFF_SESSIONHOST_BACKEND・DOEFF_SESSIONHOST_HEADLESS_DIR・DOEFF_AGENTD_SESSION_HOOKS・AGORA_CUSTODY_URL・AGORA_BORROWER_KEY_PATH)と host の argv(--db / --socket / --max-running / serve)を宿(dotfiles の launchd の宣言 cron_management/acp-single-mac.toml)が 1 つずつ写す形で、宿ごとに同じ束を書き直す。設計 第 12.6 節の決定 23 は『機体を足す手順は 1 命令 `agentd --server --token`(k3s agent と同じ体験・Mac / Linux / GCP node / runner pod のどれでも同じ)』。会社 GCP node(herdr-hud deploy/company-node・k3s の node として参加する道具一式)には agentd を起こす宣言が無く、node の契約(agora-kinds.json)の observations に所有の等級を名乗る欄も無い。"
       :evidence "agora-redesign docs/impl-requests/stage6-lane-prompts/lane-6f-gcp-node-join.md・dotfiles cron_management/acp-single-mac.toml [unit.agentd]・herdr-hud deploy/company-node/startup-script.sh(k3s agent の参加のみ)")]
  :context
    [(interpretation
       "agentd は sessionhost の隣の名前空間 acp/ に住み、host の socket の client として参加する。host.hy / hostmain.py / impls / policy は 1 行も変えない: 器の口(session.launch / send / capture / get)は公開の RPC で足りるので、腕を host の内側に生やす理由が無い。弁は console script の入口(acp/entry.py — 今日の hostmain.main を包む薄い殻)が持つ。")
     (interpretation
       "判断と I/O の分離: 要求と値の型は effects.py(data だけ)、純粋な判断は judgment.hy(defk の退化形・bind ゼロ)、program は agentd.hy(effect の列)、実 I/O は handlers.py、fake は fake.py、handler の選択は runtime.py の 1 点。test は同じ program を fake で回す。")
     (interpretation
       "agentd が持つ唯一の判定は『自分に結ばれた job か』(phase == Bound ∧ status.binding.node == 自分)。選択も優先も無く、該当する行は行の順にすべて受ける。binding は scheduling の欄なので読むだけ、Node の行も作らない(writers: create = acp-scheduling)。")
     (interpretation
       "借りた札の置き場: claude は env(custodian の契約 — 資格 file を書かない)、codex は家の中の auth.json ちょうど(fs-compose-home-view の auth_file の軸)。log・計器・簿に札を出さない。")
     (interpretation
       "job の進みの正本は行(agent-job の phase・sessionHandle・conditions と turn-record の state)と器の現況(session.get)で、memory の InFlightJob は cache。自分が claim した Running(phase == Running ∧ binding.node == 自分 ∧ sessionHandle.stream.owner == 自分)は memory に無くても resync の拍に拾い、次の 1 手は judgment.job-step-of(器が無い → fail-missing / 終端 → record-end / 走っている → observe)の 1 点で決める。memory に在る job の拍も同じ 1 点を通る。行と器に無い欄(transcript の offset・frame の seq・capture の可否・札の id)は始まりの値で組み直し、発明しない(resume の手番の始まりの offset は今の file の大きさ — 前の手番の行を混ぜない)。")
     (interpretation
       "capture の gone は終端の合図: 片付いた session の pane は無く(唯一の window なら tmux の server も無い)、host は session.capture を RPC の error で断る。これは agentd にとって『実況の終わり』で、例外にして tick を落とす理由ではない。SessionCapture の答えは閉語彙 CaptureFrame | CaptureGone で、実 handler が host の断り(AgentdClientError)を gone に写す(host.hy は触らない)。gone の後は capture も購読の読み直しもせず、器の終端で記録の腕へ進む。器が終端の拍はそもそも capture を撃たない(判定を実況より先に読む)。")
     (interpretation
       "tick の縁: heartbeat(参加の lease)・受け(list)・job ごとの観測は互いの I/O の失敗(RuntimeError | OSError = effects.IO_FAILURES)で止まらない。失敗は log して次の周期 / 次の拍へ持ち越す(heartbeat と受けは周期の刻印を進めて洪水を避ける)。lease の heartbeat が止まると段 3 の GC が node を gone と読むので、heartbeat は job の腕と独立に走る。I/O より広い例外(bug)は program では捕まえず runtime.run_loop の縁(log + 有界の backoff)へ。")
     (interpretation
       "温かい session(R10): session は会話に紐づく資源で、job は手番。会話 → 生きている session の対応は行から導く — 自分が claim した同じ subject の agent-job の行(sessionHandle.sessionId)と器の現況(session.get)で、memory は要らない。Bound の job の起こし方は judgment.next-arm-for-job の 1 点: 候補(affinity.predecessor か会話の最後の手番の session)が生きて idle なら send(predecessor が生きていれば温かい resume)、手番の途中なら defer(claim せず次の list で読み直す — 走っている手番に本文を積まない)、predecessor が在るが生きていなければ session.resume(cold)、それ以外は launch。charter に lifecycle が無ければ agentd の既定は multi_turn(名指しは尊重 — run_to_completion の charter は今日どおり 1 手番で片付く)。")
     (interpretation
       "手番の終わりの検出は session の生死と切り離す: 器の lifecycle に multi_turn を足し、policy.hy の monitor が既存の turn-end の連言(idle ∧ ¬active ∧ stable ∧ 会話記録の静止 ∧ ¬awaiting)の結果を行の turn_ended_at に刻む(level-triggered・最初の観測時刻を保ち、次の手番が走ると None・writer は monitor だけ)。agentd は job-step-of の 1 点で『turn_ended_at がこの手番の始まりの下限(本文を送った時刻)より後 ∧ 記録が進んだ(送った本文が届いた証拠)』を turn-end と読み、turn-record を ended・job を Ended にして session は生かす。send は host の awaiting latch を立てる(送った本文は owed)ので、見かけの turn-end は正の作業証拠が出るまで評価されない — 第 2 の判定は作らない。")
     (interpretation
       "session の寿命: idle が AgentdSettings.session_idle_ttl_seconds(既定 600)を過ぎた温かい session は heartbeat の拍に sessions-to-retire(純関数・時計は effect)で選び session.cleanup で片付ける。node の退役(行が無い)でも片付ける。multi_turn の器が終端(awaiting の期限で failed 等)になった手番は、host の掃き取り(run_to_completion の cleanup)の対象外なので agentd が記録の後に片付ける。GC の全体は段 3。")
     (interpretation
       "headless backend(R11): tui の launch(ready gate・paste・pane の marker)は headless に当てはまらないので、sessionhost に専用の program(headless.hy)と substrate(Headless* effect → headless_process.py の子 process)を足し、host.hy は backend の分岐だけ(RPC の語彙 launch / send / get / capture / cancel / cleanup は同じ意味)。stdin / stdout の作法(claude = prompt を stdin に書いて閉じ result の行で終わり・codex = app-server の JSON-RPC)と「手番の途中か」の判断は headless_protocol.py の Dialogue と turn_verdict の純関数 1 点。claude の print mode(-p)は tui の adapter に禁じられた形(semgrep doeff-agents-no-claude-print-mode)なので、その唯一の家を impls/headless_argv.hy に閉じ、semgrep の除外もその家と headless の substrate / program / 検だけ。claude は 1 手番 1 process(次の手番は --resume の process を同じ session の名で起こし直す — 温かい = 会話の資源としての行と events file が続く)、codex は app-server の process を生かす。")
     (interpretation
       "割り込み(R13): cancel(終端)と「手番だけ止めて session は残す」は 1 つの動詞に同居できないので、新しい動詞 session.interrupt(headless = SIGINT / turn/interrupt・tmux = Escape)を両 backend に足す。agentd は Withdrawn(書き手 = 作った側)を watch で受け、自分の走っている job なら interrupt-arm-for の 1 点で session.interrupt を撃ち、turn-record を ended(ここまでの entries と usage)、agent-job に condition Interrupted(phase は書かない)、session は片付けない(idle の寿命は sessions-to-retire)。取り下げは片付けの合図ではなく中断の合図。")
     (interpretation
       "events の実況(R12): headless の器は stdout の行を events file(backend_ref.events_path・1 行 1 event)に追記し、agentd はそこから offset で読んで純関数 events-to-deltas で TurnDelta に写す(text の delta は 1 行ずつ frame に、完成した本文は entries に — 同じ本文を frame で二度流さない)。pane は無いので frame の capture は撃たない。node の observations.streamCapability は host の backend から導く(headless = events・tmux / herdr = frames)。")
     (interpretation
       "差分の読みと計器の始点(R14): watch で起きた拍は agent-job の全量 list ではなく ACP の event-window(cursor-only・(after, through] の post-image)で変わった行だけを読み、知っている行の cache(AgentdState.rows)に差し替える。全量 list は最初の拍・周期の保険(watch_resync_seconds)・gap・接続の張り直し・窓が retention の床の下(409)の時だけ。郵便の本文は鍵で 1 行ずつ読む。計器 agent-job-to-send の始点は行の生まれの着地(generation 1 の image の resourceLandedAt・ns 精度)で、欄が無ければ今日の値(秒の粒度の createdAt)。")
     (interpretation
       "headless の 1 手番目(R16): headless の器は 1 手番 = 1 prompt(claude は 1 手番 1 process・codex は turn/start が手番)で、走っている手番の途中に次の本文を積めない。tui の launch → send(pane の paste は手番の途中でも積める)をそのまま撃つと、launch(charter の prompt)が 1 手番目を起こした直後の send が同じ名の --resume の process を起こそうとして落ちる(実弾 2026-09-12 agentd-4.log)。⇒ 起こす手番(launch / resume)の本文は charter の prompt(前置き)と inputs の郵便の本文を空行で 1 つに畳んだ文(judgment.first-turn-prompt-of)で、after-start は send を撃たない。畳むかの判定は judgment.first-turn-carries-inputs(host の backend が headless ∧ 腕が launch / resume)の 1 点で、backend は AgentdSettings.backend_kind(composition root が host の argv / env から導く — streamCapability と同じ源)。send の腕(温かい session)は今日どおり郵便の本文だけ。tui は今日どおり launch の後に send。")
     (interpretation
       "1 命令の参加(R17): 既知の形は runner(kubelet / CI runner)の参加 — k3s の `k3s agent --server --token`(config.yaml は flag と同名の鍵・flag が優先)。`doeff-sessionhost join` は宣言(flag > toml `--config` > 既定)から JoinSpec を組み(join-spec-of)、そこから今日の serve --acp の起動が読む env の束と host の argv を導く(join-plan-of)。座は join.hy の 1 点で、読み手(runtime.settings_from_env / real_dispatchers・valve.acp_valve・host.hy parse-args)は増やさず変えない — env の名の綴りは effects.py が唯一持つ。宿(launchd / systemd)の宣言は『join を起こす 1 行』に縮み、3 つの宿で同じ宣言 file(schema doeff.agentd-join.v1)を読む。所有の等級(grade = company | personal)は検の方法(proof = gce-project:<project-id> | declared)と対で宣言し、thread を起こす前に ownership-preflight(gce-project = OwnershipProbe で GCE の metadata server の project-id を読み一致だけ通す・declared = 撃たない)で突合、不一致は AgentdPreflightError(参加しない — fail-closed)。検めた等級は node-status-with-lease の 1 点で observations.ownership{grade, proof} に名乗り(宣言が無ければ欄ごと書かない = 未観測)、配置の側(E)が spec.labels.boundary の宣言と突合する材料にする。CI runner の規律(登録 token と heartbeat・label で targeting)に反しない — 登録 token = 名簿の agentd の札(今日の 1 札・node ごとの札は別便)、label は E の宣言のまま、F は観測だけを報告する。")
     (interpretation
       "profile の残量の観測(R18): 契約 profile(agora-kinds.json)の status.observed の書き手は agentd で、予算の controller(agora-budget)は observed の鮮度と残量から ProfileExhausted を導き、Scheduling はその condition だけを読む。既知の形 = kubelet の node status: 観測は runner が書き、判断は controller。agentd は heartbeat とは別の遅い周期(AgentdSettings.profile_observe_seconds・既定 300 秒・値の宣言は 1 点)で生きている profile の行を読み、この機体が持つ資格の残量を 1 度読む(effect ReadProfileUsage — 実 handler は dotfiles agentcli の usage の 1 点 `ai usage --json --cache-ttl <周期>` を subprocess で撃つ。agentcli は doeff の tool env に無く doeff は dotfiles の上流なので import しない)。会社境界(会社 profile の API 呼び出しは会社機体だけ・unknown は不許可)はその葉が判定し、断りは record の error に載って ProfileUsageUnavailable に写る — agentd は第 2 の判定を持たず、断られた profile は書かない(理由を log に 1 行)。judgment.profile-observed-of の 1 点が閉語彙 ProfileVerdict(ProfileObservation | ProfileUnobserved | ProfileNotHeld)を返す: 窓は spec.reset.everySeconds と周期が一致する provider の窓(無ければ 5h)、remaining = 100 - used(percent・budget.unit が percent でなければ書かない — 契約に unit の欄は無い)、resetAt = 窓の戻る時刻(窓が空なら観測の時刻)、observedAt = 断面の時刻、node = 自分。post-image は committed の status(state・conditions = 他の書き手の欄)を写して observed を据え、committed と同じなら書かず、ifGeneration の競合(Conflict)は 1 拍見送って次の周期に読み直す。この機体に無い profile は黙って書かない。")
     (interpretation
       "会話の引き継ぎ(R20・段 8q・agora-redesign #51・operator 決定 #54): profile / 機体を変えた手番でも会話は続く。cache(温かい session と transcript)を保つのは同じ機体 ∧ 同じ家の時だけ(operator 逐語 2026-09-13 \"i want cache kept when both machine and a profile is not changed. in other cases, i think i need to accept the fact that cache gets invalidated\")で、それ以外は正本 = ACP の会話の記録(郵便 + turn-record の entries)を読み込んで履歴から再開する。session の会話・手番・家は起こす時に launch_attribution へ刻み(回収される agent-job の行に頼らない)、turn-record の spec に sessionId を書いて Messaging が次の手番の predecessor を名指せるようにし、node の観測は sessions に account・transcripts に終端の session を載せて Scheduling が (node, account) で親和を決める(ACP 法 cd258b)。既知の形 = virtual actor の状態の移送。文脈の圧縮は別 issue #55。")]
  :decision
    [(rule R1 "agentd の出口は ACP(GET /api/resources・POST /api/events・GET /api/watch/stream・POST /api/streams)と custody(POST /lease/*)だけ。agora の台帳 API(/api/state・turn-jobs・seat-*・headless・agmsg)の語を sessionhost の source に置かない。")
     (rule R2 "job を選ぶ判定は judgment.hy の bound-to-me(phase == Bound ∧ binding.node == 自分 — 受け)と running-on-me(phase == Running ∧ binding.node == 自分 ∧ sessionHandle.stream.owner == 自分 — 再起動後の拾い直し)の 2 つの述語だけで、どちらも binding.node == 自分の行に閉じる。それ以外に job を選ぶ・優先する code を置かない。agent-job の status.binding を agentd は書かない(写して返すだけ)。")
     (rule R3 "弁の既定は off(valve.py の ACP_VALVE_DEFAULT = False)。on は flag --acp か env DOEFF_AGENTD_ACP=on だけで、語彙の外の値は黙って off に倒さず断る。")
     (rule R4 "frame の capture は購読者が居る時だけ: push の応答の subscribers が 0(か不明)なら capture を止め、周期の status frame で読み直して再開する。判定は judgment.hy の capture-verdict の 1 点。")
     (rule R5 "借りた札は disk の平文に残さない — 例外は家の中の auth file(codex の <homes>/codex/<account>/auth.json・0600)だけ。claude の札は env CLAUDE_CODE_OAUTH_TOKEN で渡し、log と計器には載せない。")
     (rule R6 "値の宣言は 1 点: lease の TTL と周期・watch の resync・frame の rate・購読の読み直しの周期は effects.AgentdSettings の既定値、env の名の綴り(URL・札・node の名・backend・所有)と host の argv の綴りは effects.py(R17 の join が同じ綴りを組む — 2026-09-12 改訂・以前は handlers.py / valve.py)、URL の既定値は handlers.py。")
     (rule R7 "job の進みは行から導く: 自分の Running(running-on-me)は memory に無くても resync の拍に拾い、次の 1 手は judgment.hy の job-step-of(器の現況 → observe | record-end | fail-missing・閉語彙 effects.JobStep)の 1 点で決める — memory に在る job の拍も同じ 1 点を通る。record-end は記録の腕(turn-record ended・result・phase Ended)だけを撃ち launch も send もし直さない。fail-missing は記録が在れば ended にし condition SessionFailed で Ended。終端の語彙(SESSION_TERMINAL_STATUSES)を読むのは judgment.hy だけ。")
     (rule R8 "capture の gone は終端の合図で例外ではない: SessionCapture の答えは閉語彙 CaptureFrame | CaptureGone、実 handler は host の断り(AgentdClientError)を CaptureGone に写す(host.hy / substrate は触らない)。gone の job は capturing = False・stream_gone = True で、以後 capture も購読の読み直しもせず、器の終端(同じ拍に読み直す)で記録の腕へ。器が終端の拍は capture を撃たない(job-step-of を実況より先に読む)。")
     (rule R9 "tick の縁: heartbeat・profile の残量の観測(R18)・受け・割り込みの配達(R21)・job ごとの観測は互いの I/O の失敗(effects.IO_FAILURES = RuntimeError | OSError)で止まらない — program の agentd-tick が 5 つの腕をそれぞれ捕まえ、log して次の周期 / 次の拍へ持ち越す(condition には写さない — 一時の失敗を job の結末にしない)。I/O より広い例外は捕まえない(runtime.run_loop の縁)。")
     (rule R11 "headless backend: host の backend の閉語彙は tmux | herdr | headless。headless の器は専用の program(sessionhost/headless.hy)と substrate(effects.hy の Headless* → substrate_headless.hy → headless_process.py)で、host.hy は backend の分岐だけ(RPC の語彙は同じ意味・session.interrupt を足す)。stdin / stdout の作法と手番の判断は headless_protocol.py の Dialogue / turn_verdict の純関数 1 点。claude の print mode の argv の家は impls/headless_argv.hy ちょうどで、semgrep doeff-agents-no-claude-print-mode の除外もその家と headless の substrate / program / 検だけ。admission と identity の準備は tui の launch と共有する(launch.hy admit-launch / prepare-launch-workspace)。")
     (rule R12 "events の実況: agentd は headless の器の実況を events file(wire の backend_ref.events_path)から offset で読み(SessionEvents)、純関数 judgment.events-to-deltas(claude = stream-json・codex = app-server の通知)で契約の種類の閉語彙(text / tool_use / tool_result / usage)の TurnDelta に写す。text の delta は 1 行ずつ frame、完成した本文は entries だけ。headless の器に pane の capture は撃たない。node の observations.streamCapability は host の backend から導く(judgment.stream-capability-of-backend の 1 点: headless = events・それ以外 = frames)。")
     (rule R13 "withdraw は中断の合図: 自分の走っている job の行が Withdrawn(書き手 = 作った側)になったら、judgment.interrupt-arm-for の 1 点で手番の途中なら session.interrupt(headless = SIGINT / turn/interrupt・tmux = Escape・session は残す)を撃ち、turn-record を ended(ここまでの entries と usage)、agent-job の conditions に Interrupted(phase は書かない)。session.cleanup は撃たない(温かい session は残す — 寿命は sessions-to-retire)。")
     (rule R14 "watch の拍は差分の読み・計器の始点は生まれの着地: 行の読み直しの様式は judgment.list-mode-for の 1 点(full = 最初の拍・周期の保険・gap・接続の張り直し / window = watch で起きた拍 = GET /api/event-window の post-image で AgentdState.rows を差し替え・窓が読めなければ full に落ちる / none = idle)。郵便の本文は鍵で 1 行ずつ読む(全量 list しない)。計器 agent-job-to-send の createdAtMs は judgment.birth-ms-of の 1 点(生まれの表 → generation 1 の image の landed_at_ms → 今日の値 created_at_ms)。")
     (rule R15 "session の id は agentd が鋳造する: 起こす session の id(session_id と session_name・sessionHandle.sessionId・stream の name)は effect MintId(ULID・時刻と乱数は handler)の答えで、charter(Messaging が組む launch の params)の session_id / session_name は読まない(judgment.launch-plan-of が落とす・据えるのは charter-with-session-id の 1 点)。実弾 2026-09-12: 温かい session が idle TTL で片付いた後、charter の固定の id の launch が `session is already registered`(host は片付いた行を登記のまま残す)に落ちて LaunchFailed で Ended した。")
     (rule R16 "headless の起こす手番は郵便を 1 手番目の本文に畳む: host の backend が headless(AgentdSettings.backend_kind — runtime.settings_from_env が valve.backend_of から導く 1 点・streamCapability と同じ源)なら、launch / resume の腕は charter の prompt(前置き)と inputs の郵便の本文を judgment.first-turn-prompt-of(空行区切り・郵便が無ければ charter だけ)で 1 つに畳んで起こし、after-start は session.send を撃たない。判定は judgment.first-turn-carries-inputs(backend ∧ 腕)の 1 点。send の腕(温かい session)は郵便の本文だけを send。tui(tmux / herdr)は今日どおり launch の後に send。turn-record の create・計器 agent-job-to-send・in-flight の登記は腕に依らず同じ。実弾 2026-09-12: launch の直後の send が `headless session already exists` で tick ごと落ち、turn-record が作られず job は拾い直しの腕へ。")
     (rule R17 "機体を足す手順は 1 命令 join: `doeff-sessionhost join --server <URL> --token-file <札> [--config <toml>] [--node-name] [--state-dir] [--backend] [--session-hooks] [--custody] [--borrower-key-file] [--ownership --ownership-proof]` の宣言は join.hy の join-spec-of(flag > toml(schema doeff.agentd-join.v1・flag と同名の鍵)> 既定)の 1 点で JoinSpec に組み、join-plan-of の 1 点で今日の起動が読む env の束(effects.py の *_ENV の綴り)と host の argv(--db / --socket / --max-running none / --backend / serve)に写す。entry.py は plan を process の env に据えて serve --acp と同じ経路を走る — env の名を entry / runtime が自分で組まない・読み手を増やさない。所有の等級 ownership(company | personal)は proof(gce-project:<project-id> | declared)と対でだけ宣言でき(片方だけは断る)、runtime.start_agentd_thread は thread を起こす前に join.ownership-preflight を撃ち(gce-project = OwnershipProbe の答え = metadata の project-id が一致する時だけ通す・declared = 撃たない)、不一致は AgentdPreflightError で参加しない。検めた等級は judgment.node-status-with-lease の 1 点で observations.ownership{grade, proof} に書く(宣言が無ければ欄ごと無い)。agentd.hy は ownership の語を比較しない。本文の行き先(段 9f lane 9f-6・agora-redesign #59): 会話の記録の service の宛先(宣言 file の [record].url / flag --record → env RECORD_SERVICE_URL)を持たない agentd は参加を断る — 判断は join.record-sink-of の純関数 1 点(宣言 → 参加可否・宣言の検で届くかは検めない)、読みは runtime.settings_from_env の 1 点(join の経路も serve --acp の経路も同じ門)、断りは AgentdPreflightError(理由 = 宣言の置き場)で entry.py が stderr に書いて exit 2(宿が再起動する — process の中で再試行しない・宣言が直るまで参加しない)。宛先が在って届かないのは spool が受ける(参加は断らない)。本文の行き先を持たないまま見出しだけを書く agentd は存在しない。")
     (rule R18 "profile の残量の観測は agentd が書く(段 7 lane 7d-3): agentd-tick の 1 つの腕 observe-profiles が AgentdSettings.profile_observe_seconds(既定 300・値の宣言は 1 点・同じ値を読み口の cache の寿命に渡す)の周期で生きている profile の行(state ≠ retired)を読み、この機体の profile の家の在否を effect ListProfileHomes(段 8e lane 4j — 実 handler = handlers.list_profile_homes = 登録簿の 1 点 handlers.PROFILES_COMMAND = `agentcli profiles list --json` の subprocess + dir の実在)で読み、judgment.profile-rows-held の 1 点で観測する行を絞る: 家の在る行が 1 つも無い機体(pool の pod — personal の資格は預かり所が観測し、会社 profile は会社機体だけ)は usage を撃たず、「観測する profile なし」を AgentdState.no_profile_homes_logged で 1 度だけ log し(家が現れたら戻る)、計器 profile-observed(homes 0)は出す。家の在る行が在れば、この機体が持つ資格の残量を effect ReadProfileUsage(kind = effects.PROFILE_USAGE_KIND = claude・契約の行は資格の種類を運ばない)で 1 度読む。実 handler = handlers.read_profile_usage = dotfiles agentcli の console script(handlers.USAGE_COMMAND = `ai usage --json`)の subprocess ちょうど — sessionhost は agentcli を import しない・会社境界(company_boundary)の判定を持たない(断りは record の error → ProfileUsageUnavailable)・読み口の落ち方(profiles.gen.json の不在)で器の profile の有無を判じない。書く観測は judgment.profile-observed-of の 1 点(閉語彙 effects.ProfileVerdict): 窓 = observed-window-of(spec.reset.everySeconds と一致する窓・無ければ 5h)、remaining = 100 - used(percent・budget.unit ≠ percent は書かない)、resetAt = 窓の戻る時刻(無ければ observedAt)、observedAt = 断面の時刻、node = 自分。post-image は profile-status-with-observed(committed の state・conditions を写す)、committed と同じ observed は書かず(profile-observed-changed)、Conflict は log して次の周期、Refused / 書かない理由は log に 1 行、この機体に無い profile(ProfileNotHeld)は書かず log もしない。agentd.hy は窓の名・単位・境界の語を比較しない。")
     (rule R19 "手番の出来事は拍ごとに turn-record へ追記する(段 8 lane 4u・agora-redesign #49): stream-records は実況の材料の追記を読むたびに、その拍の出来事(judgment.deltas-of の entries = 契約 agora-kinds.json の turn-record の status.entries の item — kind は effects.EntryKind の閉語彙 text / tool_use / tool_result / frame / system / error・at = 読んだ拍・seq = frame と共有の採番)を agentd.append-entries の 1 点で行の status.entries へ追記する(耐久化は手番の終わりを待たない)。書きは行の最後の image(InFlightJob.record — 無ければ鍵で読む)に対する CAS(AcpPutStatus の ifGeneration)で、Conflict は行を読み直して同じ出来事を 1 度だけ積み直し、Refused / 行の不在は出来事を InFlightJob.pending_entries に持ち越して次の拍か手番の終わりに乗せる(落とさない)。拾い直した job の採番(seq 0 から)が行の seq と衝突すれば judgment.next-seq-after / renumbered-entries で行の次から振り直す。entry の形は見出しの閉じた型 effects.TurnEntryHeadline(段 9f lane 9f-4・設計 §2.2: seq・at・kind・toolName・toolUseId・bytes・sha256・isError — 本文の欄 text / summary / input / output / model は型に無く、写さない・切らない〔切り詰めは会話の記録の service の責務〕)で、導く点は judgment.headline-of-body の 1 つ・JSON への写しは entry-json-of の 1 つ・bytes / sha256 は service の冪等の判断と同じ計算(record-body-bytes-of = text / summary / input / output の在る欄だけの compact・鍵 sort・UTF-8)。toolUseId は呼び出しと結果を結ぶ鍵。行の上限(TURN_RECORD_ENTRIES_BYTE_BUDGET = 262144 byte)は judgment.entries-within-budget が古い見出しから落とし先頭に印(TurnEntryDropMarker → kind system・truncated・dropped — 本文も bytes / sha256 も無い)を残す。service が本文を受理した答え(highestProducerSeq)は agentd.mark-recorded が status.recordRef(`record:<cid>/<streamId>`)/ recordedSeq(後ろへ戻さない)に写す。claude の system の行(init / API の retry / hook の失敗)は kind system に、result の誤りは kind error に、codex の turn/completed の誤りも kind error に写す(手番の終わりの判定は host のまま — ここは記録だけ)。手番の終わり(finalize-job / interrupt-job)は drain-stream で最後の材料を同じ拍で読んで追記し、turn-record-ended-status は残りの出来事を**追記**した上で ended・usage を据える(entries を置換しない — 旧の形は最後の本文 1 行だった)。usage は手番の全材料の読み直し(turn-batch-of)から数える(message ごとの重複を跨がない)。")
     (rule R20 "会話の cache を保つのは同じ機体 ∧ 同じ家の時だけ・それ以外は 履歴からの再開(ACP の記録から)(段 8q・agora-redesign #51・operator 決定 #54): Bound の job の起こし方は judgment.next-arm-for-job(candidate view home)の 1 点 — candidate = affinity.predecessor か会話の最後の手番の session(warm-candidate-of)、home = judgment.session-affinity-key-of(binding.account・charter の binding・charter.model〔plan.model〕の組 — model は段 9o lane 9o-3)、session の家は起こす時に刻んだ launch_attribution の agentd の欄(session-attribution-of / attribution-of-view — 回収される agent-job の行から導かない)。候補なし → launch / 生きて idle ∧ 同じ家 → send / 生きて idle ∧ 家が違う → 候補を session.cleanup して rehydrate / 生きていて idle でない → defer / 器に登記されて終端 ∧ 同じ家 → session.resume(cache を保つ)/ それ以外(器に無い = 別の機体・終端だが家が違う・帰属が無く家が分からない)→ rehydrate。rehydrate = session.launch で、最初の本文 = charter の prompt + judgment.rehydrate-history-of(会話の郵便〔ACP の kind message〕と手番の本文を時刻順・kind ごとに畳み、この手番の inputs と frame は除き、AgentdSettings.rehydrate_history_byte_budget〔既定 65536 byte〕を超えたら古い手番から要約せず落として落とした数と全文の在処を名乗る)(+ headless は郵便の本文)。手番の本文の材料は型で 2 つ(段 9f lane 9f-4・設計 §2.4): RecordedTurns = 会話の記録の service を before=latest から後向きに読んだ本文(agentd.record-turns-for — effect RecordRead を 1 頁 RECORD_PAGE_MAX_LIMIT ずつ、judgment.record-history-satisfied〔読めた bytes ≥ 上限〕か会話の最初まで)/ HeadlineTurns = service が配線されていない・届かない時の ACP の turn-record の見出しだけ(**薄い再開** — 手番ごとの出来事の数と道具の名だけを畳み、prompt の頭と log が薄い再開と理由を名乗る。本文の無い行を本文として扱わない)。材料の読みは名指しの順(段 9q・agora-redesign #77): 本文 = RecordRead(会話 1 つ)→ 郵便 = effect AcpConversationMail(kind message・手番を起こし直す時の 1 回だけ)→ 見出し = effect AcpTurnHeadlines(kind turn-record の全量 — 実測 2026-09-14: 29,913 行・172 MB・頭の応答 59 秒)は service が答えなかった薄い再開の拍にだけ(agentd.headline-turns-for の 1 点)。claim(AcpPutStatus)は宣言の照合だけで即時(実測 0.3 秒)、器の準備(再開の読み・畳み)は claim の後の腕 — node の lease(TTL 90 秒)より長く tick を塞がない。resume が器に断られたら judgment.fallback-arm-of で同じ鋳造 id の rehydrate。家またぎの transcript の写し(sessionhost の transplant)には頼らない。turn-record の spec に sessionId(= sessionHandle.sessionId)を書く。node の observations は sessions の各項に account(帰属の account・null = 借りていない)、transcripts に終端の session のうち transcript の file がこの機体に在る会話ごとの最新(judgment.transcript-candidates-of・上限 AgentdSettings.transcripts_observed_max)を載せる — Scheduling はそれを (node, account) で読む(ACP 法 cd258b)。")
     (rule R21 "割り込みの本文は走っている手番へ即座に渡す(段 8 lane 4x・agora-redesign #56・operator 逐語 2026-09-13 \"messaging supports both 'queued/interrupting' messages\"): Messaging(ACP)が走っている手番の agent-job の status.interrupts に載せた Message の id を、agentd は毎拍・行の cache から・自分が走らせている job(memory の InFlightJob)についてだけ読み(agentd.deliver-interrupts の 1 点)、渡していない id(judgment.pending-interrupts-of = 行の interrupts − 行の interruptsDelivered − memory の interrupts_sent・載せた順)ごとに Message の本文を鍵で 1 行読んで SessionInterject(session.send の mode = interrupt)で器へ渡す。渡せた id は鍵で読み直した行に CAS で記録する(judgment.interrupts-delivered-status-of — 同じ 1 回の書きで interrupts から消し interruptsDelivered へ足す・他の欄は写す・Conflict は 1 度読み直す)。器が断った id(走っている手番が無い)はそこで止めて行に残す(順を跨いで後の id を先に渡さない)— 手番が終わればその行は終端の phase で interrupts を持ち、Messaging が queued として積み直す。器の側(sessionhost の headless): claude は `--input-format stream-json` の温かい process(impls/headless_argv.hy の CLAUDE-HEADLESS-FLAGS・実測 conformance/interrupt-physics.md — 手番の途中に書いた user の行は CLI が次の tool の境界で手番に注入し、result の後も process は生きて次の行が次の手番)で、割り込みの本文 = 同じ user の行(headless_protocol.ClaudeDialogue.inject — 走っている手番が無ければ accepted = False)、codex = turn/interrupt を送り interrupted の turn/completed を手番の終わりとして報告せず同じ thread へ本文の turn/start(CodexDialogue.inject — host から見て手番は 1 つのまま)。host は器が引き受けなかった時に型付きに断る(headless-inject-program — 誰の job でもない手番を起こさない)。agentd は器の作法(stdin の綴り・turn/interrupt)を 1 語も持たない。")
     (rule R22 "実況の push の周期は購読者が居る間 ≤ 50 ms(段 8 lane 4aa・agora-redesign #63): headless の器の実況は events file の行の増分で、file の追記は合図を持たない —— agentd が offset から読んで中継へ押す拍の周期がそのまま push の間隔になる。購読者が居る(InFlightJob.capturing)間の watch の待ちの上限は、この器の実況が events(AgentdSettings.stream_capability = events)なら AgentdSettings.events_poll_seconds(既定 0.05 = 出来事ごとの push に最も近い有界の拍)、frames(tui の pane の断面)なら frame_interval_seconds(2〜5 Hz・issue #1 の決定 4 のまま)。購読者が居なければ transcript_poll_seconds(記録の追記だけ)、job が無ければ idle_wait_seconds。判断は judgment.wait-seconds-for の 1 点、値の宣言は AgentdSettings の 1 点(handlers / agentd.hy に周期の literal を置かない)。本番 2026-09-13 17:1x: 実況の最初の tail が attach の後 247〜258 ms、割り込みの反映 219 ms — 画面の糊の側の根(会話簿の毎拍の組み直し)は agora-controllers 741e67d で直し、agentd の側の残りがこの周期(購読ありで 0.4 s・無しで 1.0 s の tick)だった。⚠ 記録(turn-record)への追記の拍は push の周期に**追随しない**(judgment.record-due — transcript_poll_seconds のまま・InFlightJob.last_record_ms): 追記は CAS の書き = ACP の event 1 つで、50 ms の拍ごとに書くと走っている手番 1 つで毎秒 10〜20 の event が journal に並び、画面の糊の watch の拍(1 event = 1 拍)が飽和する(実弾 2026-09-13 18:3x: 糊の占有 367 拍中 359 が 200〜500 ms・hello 15 s)。書かない拍の出来事は pending_entries に持ち越す(落とさない)。")
     (rule R23 "手番の資格の出所は judgment.credential-source-of の 1 点(段 10 lane 10c・agora-redesign #80・operator 決定 2026-09-14 \"access token is to be fetched from k3s\"): launch-plan-of が据えた plan.account(binding.account ∧ charter の agent_type に貸与の種類)が在れば lease(預かり所から借り、借りた家で起こす)、無ければ node が預かり所を宣言している(AgentdSettings.custody_declared — runtime.settings_from_env が CUSTODY_URL_ENV = join の [custody].url の在否から導く 1 点)時 missing、宣言していなければ home。agentd.claim-job は plan を読んだ直後にこの答えを読み、missing の job は起こさず Running も sessionHandle も書かず、end-job-now で条件 CredentialSourceMissing つきの Ended に閉じる(黙って charter の binding = 機体の profile の家へ落ちない)。home(charter の binding で起こす)の経路は預かり所を宣言していない node(移行前の機体)だけに残る。session を使い回す鍵は judgment.session-affinity-key-of(旧名 home-key-of — 鍵の中身は account・binding・model のまま不変で、資格ではない)。ACP の側の半分(配置が会話の profile → profile の行 → spec.account を解いて status.binding.account に置く)は ACP の法 defadr_20260914_turn_credential_is_the_custody_lease_c744ca。")
     (rule R24 "node の能力の表と、効かない宣言の欄の条件(段 10 lane 10e・agora-redesign #53・operator 決定 2026-09-14 \"all lgtm\"): agentd は node の status.capabilities に agent の種類(charter.agent_type の語 claude / codex)ごとの {settings: 受ける欄, restartOn: 変えたら session を作り直す欄} を lease と同じ拍に名乗る(judgment.capabilities-of — 値は effects.AGENT-CAPABILITIES の 1 点・契約 agora-kinds.json conventions.agentSettings.settings の綴り)。restartOn は session-affinity-key-of の鍵の欄(model・profile = account と binding の家)ちょうどで、effort と workDir は受けるが鍵に入れない。effort は claude の --effort / codex の -c model_reasoning_effort(process の旗)なので、同じ家で effort だけ違う温かい session は片付けて同じ session を新しい旗で --resume する(next-arm-for-job の 4 つ目の引数・帰属の effort の欄と比べる — session は作り直さず cache を保つ)。この手番で効かない宣言の欄(能力の表に無い種類・受けない欄・温かい session への send で charter.work_dir が session の cwd と違う)は、judgment.ignored-settings-of の 1 点が条件 AgentSettingIgnored(1 欄 1 行・reason = <欄>=<値>: <理由>)にして手番の終わりに刻む(黙って落とさない)。")
     (rule R25 "backend の生死は host の観測で決め、status の語から推測しない(段 10 lane 10h・agora-redesign #84・既知の形 = kubelet の node 再起動後の container の生死の観測): sessionhost は headless の行の backend(子 process)の生死を観測で決める — pid の存在(kill 0)+ 所有(この host の registry が同じ pid の生きた process を持つ・effect HeadlessLiveness・値 headless_protocol.BackendLiveness)。host の起動時(accept より前・awaiting latch の clear より前)に headless.hy recover-headless-rows が非終端の headless 行を観測し、判断 headless_protocol.recovery_verdict(status_terminal, in_flight, liveness)の 1 点で『手番の途中(awaiting)∧ backend が死んでいる』行だけを exited + cause vanished(ADR-DOE-AGENTS-009 の証拠つき死亡の語彙・reason に pid と観測の文)にして session_exited を刻む。idle の温かい行は触らない(次の send が --resume で同じ session を起こし直す)。awaiting latch の起動時の全 clear(store.hy db-clear-awaiting-latches)は headless の行を対象にしない(headless の latch は『手番の途中』の事実そのもの)。wire の session.get / session.list は backend_alive(headless = 上の観測・tmux / herdr = 行の pane が session の pane の集合に在る・終端の行は観測せず false)を毎回載せる。agentd は判断 judgment.backend-alive の 1 点(器に無い → 偽・明示の False → 偽・観測の無い眺め〔launch / resume の応答の backend_alive = None〕→ 真: 観測の無さは死亡の証拠ではない)を job-step-of(非終端 ∧ 手番の終わりでない ∧ backend が死 → session-lost = 記録の腕と条件 SessionLost〔reason に session・backend の種類・pid・観測の時刻 — judgment.session-lost-condition-of〕で Ended・session は host の monitor に任せて片付けない)と next-arm-for-job(候補が生きて idle でない ∧ backend が生 → defer / ∧ backend が死 → 同じ家なら候補を片付けて resume・違う家なら片付けて rehydrate)で読む。agentd.hy は backend_alive の欄も終端の語も直に読まない。headless の session.resume の腕は launch-params に events_root を運ぶ(launch.hy resume-session — 運ばないと headless-launch-session が KeyError で断り、全部 rehydrate に落ちる)。店の cause の decode(store.hy terminal-cause-from-dict)は契約の欄(category / observed_at)を持たない persisted cause を None(typed には cause なし)と読み、行ごと KeyError で読めなくしない(wire は raw を運ぶ・DB の COALESCE が raw を消さない)。")
     (rule R26 "停止で子を黙って道連れにしない(段 10 lane 10h 便 2・agora-redesign #84): launchd の bootout / kickstart は process group ごと殺すので headless の子 process(pipe の子)は host と共に死ぬ — pipe から切り離して拾い直す形(detach)は取らない(親を失った process は器として使えない: events file の書き手も Dialogue の状態も host に在る)。代わりに host は TERM の 1 度目に accept loop を生かしたまま別 thread(host.hy graceful-stop)で (1) 登録された停止の hook(host.register-shutdown-hook — entry.py が agentd の AgentdRun.close_for_stop を登録する: loop を止めて今の拍を有界に待ち、memory の走っている job を agentd.close-jobs-for-stop で 1 つずつ記録の腕〔残りの材料・turn-record ended〕と条件 AgentdRestart〔judgment.restart-condition-of の 1 点 — node・理由・session・時刻〕で Ended・status frame ended・札の返却。session は片付けない)(2) headless の行の停止の腕(headless.hy stop-headless-rows: 判断 headless_protocol.stop_verdict の 1 点で手番の途中の非終端の行だけ stopped + cause cancelled〔reason = host の停止と信号〕+ session_cancelled・idle の温かい行は触らない・登記の全 process を HeadlessKillAll で段ごとに並列の猶予〔EOF → TERM → KILL〕で降ろす)を走らせ、stderr に数を 1 行ずつ書き、自分に同じ信号を撃ち直す(実の信号 — _thread.interrupt_main は accept の syscall を起こさない)。2 度目の TERM は SystemExit(0) で finally(lease の釈放)へ。SIGKILL には手が無い — 次の起動の復帰(R25)が拾う。Mac の agentd の入れ替えは走っている job が 0 の拍に launchctl の bootout → bootstrap(TERM の経路)で行い、kickstart -k(即時)は使わない。ACP の宛先(実況の push・行の読み書き・watch の全部)は宣言(join の --server / [agentd].server → ACP_DAEMON_URL)ちょうどで、handlers.py に 127.0.0.1:8868 の既定値は無い(runtime.real_dispatchers は宣言の無い env を AgentdPreflightError で断る)。")
     (rule R27 "会話は自分で圧縮する — 閾値は会話の宣言・実測は agentd・腕は履歴からの再開(段 10f 便 2・agora-redesign #82・operator 2026-09-14 逐語 \"that routing agent should compact itself with some threshold\"): 会話の行の status.agent.compactAt(0〜100 の整数・任意・書き手 agora-conversation・契約 agora-kinds.json)は文脈の使用率の閾値。agentd は手番の終わり(settle-record と interrupt-job — 記録の腕)に材料の末尾から文脈の大きさを測り(judgment の deltas-of が DeltaBatch.context = {tokens, window} を組む: claude = 最後の assistant の message の usage の input + cacheRead + cacheWrite + output と result の modelUsage[その model].contextWindow / codex = token_count の last_token_usage〔app-server は tokenUsage.last〕の input + output と model_context_window〔modelContextWindow〕)、judgment.context-percent-of で %(切り捨て・上限 100・窓が無ければ None = 測れない)にして session ごとに AgentdState.context_by_session へ置く(with-context-percent — memory の cache・再起動で消え次の手番の終わりに測り直す)。claim の腕は候補の session が在る時だけ会話の行を鍵で 1 回読み(conversation-key-of・AcpGetRow)、compact-at-of と context-percent-for から judgment.compaction-due(宣言あり ∧ 実測あり ∧ 実測 ≥ 閾値)を求め、next-arm-for-job の 5 つ目の引数 compact に渡す。腕: compact ∧ 候補あり ∧ 手番の途中でない → rehydrate(ArmChoice.compacts = True・生きている候補は片付ける — 温かい cache を捨てて記録の service の履歴を縮めて畳むのが圧縮の意味)/ 手番の途中(backend が生)は defer が先 / 候補なしは launch。compacts の拍に計器 agentd_compactions_total{conversation, agentJobId, sessionId} と log 1 行(retire-reason-of が理由を名乗る)。turn-record の usage には書かない(契約に欄が無い — 耐久にする時は契約の便で contextTokens / contextWindow を足してから)。")
     (rule R10 "session は会話の資源・job は手番(温かい session・設計 17.4): 会話 → 生きている session の対応は行(自分が claim した同じ subject の agent-job の sessionHandle)と器の現況から導き、Bound の job の起こし方は judgment.hy の next-arm-for-job(閉語彙 effects.NextArm = launch | send | resume | rehydrate | defer — 家と機体の扱いは R20)の 1 点で決める — 同じ会話の生きて idle な session が在れば launch せず session.send(awaiting)だけ、sessionHandle はその session を指し、turn-record は手番ごと。手番の終わりは器の lifecycle multi_turn(launch.hy の閉語彙に足した語)で policy.hy の monitor が既存の turn-end の連言から行の turn_ended_at に刻み、agentd は job-step-of の turn-end(turn_ended_at > 手番の始まりの下限 ∧ 記録の進み)で読む — status は倒さず session は生かす。idle の寿命は AgentdSettings.session_idle_ttl_seconds の 1 点で、超過・Withdrawn・node の退役で session.cleanup。計器 agent-job-to-send は create → send のまま(温かい path で p99 < 2 秒)。")]
  :laws
    [(law interrupts-ride-the-running-turn-and-are-recorded-on-the-row
       :statement "for_all Running job j run by this agentd with status.interrupts = [m1..mn]: each mi not in status.interruptsDelivered ∪ memory.interrupts_sent is handed to the session by SessionInterject(body(mi)) in placement order, and every accepted mi is written back by one CAS that removes it from interrupts and appends it to interruptsDelivered; a refused mi stops the order and stays on the row; agentd never starts a turn for an interrupt and never removes an id it did not hand over"
       :counterexamples
         [(counterexample "agentd が割り込みの id を行から消すだけの形(interruptsDelivered に足さない): Messaging の『載せた』と agentd の『渡した』の間で片方が再起動すると『渡した』と『まだ載せていない』が区別できず、同じ Message が二度 agent に届く。渡した id は行に残す(append-only)")
          (counterexample "器が断った割り込み(走っている手番が無い)を agentd が interruptsDelivered に足す形: 本文は誰にも届いていないのに『渡した』と嘘をつき、Messaging が queued へ積み直せない。断られた id は行に残す")
          (counterexample "割り込みの本文を agentd が session.send(mode = turn)で送る形: claude の温かい process では手番の外に書いた user の行が**次の手番**になり(job の無い手番・turn-record も result も無い)、手番の中でも『割り込み』の印が host に無い。mode = interrupt の 1 語で器の作法(注入 / turn/interrupt)を選ぶ")
          (counterexample "codex の Dialogue が interrupted の turn/completed を手番の終わりとして報告してから turn/start する形: host が turn_ended_at を刻み、agentd が job を Ended にし、次の turn が job の無い手番になる。完了は飲んで同じ thread へ積む(手番は 1 つ)")
          (counterexample "claude の headless を 1 手番 1 process(本文 + EOF)のまま割り込む形: stdin が閉じていて書けず、SIGINT は手番を止めるだけで本文を渡せない。--input-format stream-json の温かい process が割り込みの口(実測 2026-09-13)")]
       :enforcement ["docs/adr/defadr_doeff_agents_012_agentd_acp_arms.hy::test-adr-doe-agents-012-interrupts-ride-the-running-turn"
                     "packages/doeff-agents/tests/test_sessionhost_acp.py::test_interrupt_on_a_running_job_is_handed_to_the_session_and_recorded_on_the_row"
                     "packages/doeff-agents/tests/test_sessionhost_acp.py::test_interrupt_refused_by_the_session_stays_on_the_row_and_is_not_recorded_as_delivered"
                     "packages/doeff-agents/tests/test_sessionhost_headless.py::test_headless_process_claude_inject_reaches_the_running_turn"
                     "packages/doeff-agents/tests/test_sessionhost_headless.py::test_codex_dialogue_inject_interrupts_then_starts_the_next_turn_as_one_turn"
                     "packages/doeff-agents/tests/test_sessionhost_headless.py::test_host_headless_claude_interrupt_mode_reaches_the_running_turn"])
     (law agentd-exits-only-to-acp-and-custody
       :statement "for_all source_file f in sessionhost/: agora_ledger_words(code_lines(f)) = ∅ — agentd(sessionhost)が話す相手は ACP と custody だけ"
       :counterexamples
         [(counterexample "sessionhost の handler が /api/state や turn-jobs の台帳を直に叩く — 共有状態が 2 つの store に割れ、段 4 で退役する agora の turn 系 API に新しい依存が生える")])
     (law agentd-holds-no-placement-judgment
       :statement "job を選ぶ判定は bound-to-me(phase == Bound ∧ binding.node == self)の 1 点で、agentd は status.binding を書かない"
       :counterexamples
         [(counterexample "agentd が空いている job を自分で拾う(binding を自分に書く)— scheduling operator の配置の判断(会社境界・容量・予算)を迂回し、2 台の agentd が同じ job を取り合う")
          (counterexample "別の node に結ばれた Bound の job を launch する — 結びの権限(改訂 R1-b)の欠落そのもの")])
     (law agentd-valve-defaults-off
       :statement "acp_valve(argv, env).enabled = False when '--acp' ∉ argv ∧ env[DOEFF_AGENTD_ACP] ∉ {on} — 弁の既定は今日のまま"
       :counterexamples
         [(counterexample "既定で参加する sessionhost — 段 2 の会社 Mac 1 台の切替が全機体に黙って広がり、dogfooding の旧の経路が止まる")])
     (law capture-stops-at-zero-subscribers
       :statement "subscribers = 0 ⇒ capture-verdict = stop ⇒ SessionCapture は呼ばれない; subscribers > 0 ⇒ continue"
       :counterexamples
         [(counterexample "購読者の無い session を capture し続ける(rate を落とすだけ)— 50 session × 5 Hz の tmux capture が残る(issue #1 の推奨が退けた形)")])
     (law borrowed-credentials-never-rest-on-disk-in-plain
       :statement "for_all written_file w: token ∉ w unless w = <homes>/codex/<account>/auth.json — 借りた札は家の中の auth file 以外の平文に残らず、log / 計器にも出ない"
       :counterexamples
         [(counterexample "claude の access token を CLAUDE_CONFIG_DIR の中の file や log に書く — custodian の契約(env 注入・資格 file を書かない)に反し、家の写しが札の写しになる")])
     (law job-progress-is-derived-from-rows
       :statement "for_all agent-job row r: phase(r) = Running ∧ binding.node(r) = self ∧ sessionHandle.stream.owner(r) = self ∧ r ∉ memory ⇒ the next resync settles r by job-step-of(session.get(r)) ∈ {observe, record-end, fail-missing} without a second launch or send — 再起動後の孤児は残らず、判断は judgment.hy の job-step-of の 1 点"
       :counterexamples
         [(counterexample "job の進みを process の memory(InFlightJob)にだけ持ち、list では Bound しか拾わない — agentd が落ちた / ACP が一時切れた拍に Running の job が二度と戻らない孤児になる(実弾 002)")
          (counterexample "拾い直した Running を Bound と同じに扱って launch し直す — 走っている session が 2 つになり、turn-record が二重になる")
          (counterexample "agentd.hy が終端の語彙を直に読んで record-end を決める — 次の 1 手の判定点が 2 つになり、memory の有無で結末が食い違う")])
     (law capture-gone-is-a-terminal-signal-not-an-error
       :statement "SessionCapture ∈ {CaptureFrame, CaptureGone}; CaptureGone ⇒ no exception escapes the job's tick ∧ capturing = False ∧ stream_gone = True ∧ no further SessionCapture ∧ the job ends by the record arm (turn-record ended・phase Ended) once the session is terminal; session terminal at the tick ⇒ SessionCapture is not issued at all"
       :counterexamples
         [(counterexample "片付いた session の capture を例外のまま tick に上げる — 器が done で result も在るのに tick ごと落ち、job は Running・turn-record は running のまま(実弾 003)")
          (counterexample "gone の後も frame の capture や購読の読み直しを続ける — 無い pane への tmux capture の連打")])
     (law session-is-a-conversation-resource-and-a-job-is-a-turn
       :statement "for_all Bound job j of conversation c on node n: exists session s of c alive ∧ idle (lifecycle = multi_turn ∧ turn_ended_at ≠ None) ⇒ claim(j) issues no session.launch / session.resume and exactly session.send(inputs(j)) to s ∧ sessionHandle(j) = s ∧ turn-record(j) is its own row; no such s ⇒ launch, or resume / rehydrate by R20 when the conversation has a previous session; s mid-turn ⇒ j stays Bound (defer)"
       :counterexamples
         [(counterexample "同じ会話の次の手番を毎回 cold に launch する — tmux で claude の tui を起こす約 10 秒が create → send に毎手番乗り、段 2 の計器 p99 < 2 秒を構造的に満たせない(実測 p50 11.0 秒 / p99 16.9 秒)")
          (counterexample "会話 → session の対応を process の memory にだけ持つ — 再起動で温かい session を見失い、生きている session を残したまま同じ会話をもう 1 つ起こす")
          (counterexample "手番の途中の session に次の手番の本文を send で積む — 前の手番の終わりの turn_ended_at を次の手番の終わりと読み違え、turn-record の境界が壊れる")])
     (law warm-send-is-decided-at-one-point
       :statement "the only decision launch | send | resume | rehydrate | defer for a Bound job is judgment.next-arm-for-job; the only reading of a warm turn's end is judgment.job-step-of (turn-end ⇔ lifecycle = multi_turn ∧ turn_ended_at > floor ∧ progressed); agentd.hy neither compares lifecycle words nor reads turn_ended_at"
       :counterexamples
         [(counterexample "agentd.hy が『予め resume か launch か』を自分で分岐し、judgment にも同じ分岐を持つ — 判定点が 2 つになり memory の有無で起こし方が食い違う")
          (counterexample "agentd が transcript の落ち着きを自分で数えて手番の終わりを宣言する — policy.hy の turn-end の連言(会話記録の鮮度窓・queued messages・awaiting)を持たない第 2 の判定で、走行中の手番を終わりと読む")])
     (law print-mode-has-one-home-the-headless-backend
       :statement "the spelling of claude's print mode (`-p` in an argv) appears in sessionhost/ exactly in impls/headless_argv.hy; host.hy's backend vocabulary is {tmux, herdr, headless} and every RPC arm chooses the headless program by the one predicate headless-backend?; the semgrep rule doeff-agents-no-claude-print-mode excludes only the headless home (argv / protocol / process / program / substrate / tests)"
       :counterexamples
         [(counterexample "tui の adapter(impls/claude_code.hy)に -p を足す — 1 手番で process が死に、monitor が result を validate / 再促できない(ADR-DOE-AGENTS-002 の禁止そのもの)")
          (counterexample "semgrep の除外を packages/** に広げる — print mode の禁止が死に、次の one-shot の launch site が黙って通る")])
     (law headless-events-are-mapped-by-one-pure-function
       :statement "for_all headless session s (backend_kind = headless): the live stream of s is read from backend_ref.events_path (SessionEvents) and mapped to TurnDelta by judgment.events-to-deltas; text deltas become text frames one per line and completed text becomes entries only; SessionCapture is never issued for s; AgentdSettings.stream_capability = events iff the host backend is headless"
       :counterexamples
         [(counterexample "headless の器に pane の capture を撃つ — 無い pane への capture が gone で毎拍落ちる(実弾 003 の headless 版)")
          (counterexample "完成した assistant の本文を text frame でも流す — 画面に同じ本文が delta と全文で二度出る")
          (counterexample "streamCapability を値の宣言の literal に固定する — headless の node が frames を名乗り、画面が端末の眺めで chat の block を描けない")])
     (law headless-first-turn-carries-the-mail
       :statement "for_all Bound job j claimed by launch or resume on a host whose backend is headless (AgentdSettings.backend_kind = headless): the prompt of session.launch / session.resume = first-turn-prompt-of(charter.prompt, bodies(inputs(j))) (blank-line joined・charter only when inputs are empty) ∧ no SessionSend is issued for j; on a tui host the launch prompt = charter.prompt ∧ SessionSend(bodies) follows; the send arm sends bodies only on every host; the decision is judgment.first-turn-carries-inputs alone"
       :counterexamples
         [(counterexample "headless の launch の後に郵便を session.send する — claude は 1 手番 1 process なので host が同じ名で --resume を spawn し `headless session already exists` で tick が落ちる(実弾 2026-09-12 agentd-4.log)・codex は走っている turn に turn/start を積む")
          (counterexample "agentd.hy が backend の語を自分で比較して畳む / 畳まないを分ける — 判定点が judgment と 2 つになり、backend の語彙が増えた日に片方だけ直る")
          (counterexample "tui でも郵便を charter に畳む — 温かい send の path と cold の launch で本文の届き方が変わり、ready gate の paste 物理(context_file・composer)の前提が崩れる")])
     (law withdraw-is-an-interrupt-signal-not-a-cleanup
       :statement "for_all withdrawn agent-job row r I am observing: interrupt-arm-for(job, session.get) = interrupt ⇒ exactly one session.interrupt(r.sessionHandle) and no session.cleanup; turn-record(r) = ended; conditions(r) ∋ Interrupted; phase(r) stays Withdrawn; the session stays alive for the next send"
       :counterexamples
         [(counterexample "取り下げで session.cleanup を撃つ — 温かい session が死に、次の手番が cold の launch(tmux の tui で約 10 秒)に戻る")
          (counterexample "agentd が Withdrawn の行の phase を書く — 書き手は作った側(withdraw の権限)で、agentd の書きは断られるか二重の終端になる")
          (counterexample "手番が既に終わっている job に割り込む — 次の手番(別の job)の途中の session に Escape / SIGINT が飛ぶ")])
     (law watch-wake-reads-changed-rows-and-latency-starts-at-birth
       :statement "for_all watch wake with kind = changed: agentd issues AcpEventWindow(after = last_window_seq) and no AcpGet(agent-job) unless the window is incomplete; message bodies are read by key (AcpGetRow) and never by AcpGet(message); the one read of all messages is AcpConversationMail when a claim rehydrates (R20) and AcpTurnHeadlines (all turn-records) is issued only when the record service did not answer; the metric agent-job-to-send.createdAtMs = birth-ms-of(row, births) = the generation-1 landed_at_ms when known, else the row's created_at_ms"
       :counterexamples
         [(counterexample "watch で起きるたびに agent-job と message を全量 list する — loadCurrentState の全 state を 2 度読み、温かい path の p99 が 2 秒を超える(実測 p50 4.7 s)")
          (counterexample "createdAtMs を秒の粒度の resourceCreatedAt から取る — 計器が最大 1 秒ずれ、2 秒の受入を測れない")])
     (law join-is-one-command-and-one-decision-point
       :statement "for_all argv a and declaration d: the env bundle and host argv of `doeff-sessionhost join a` = join-plan-of(join-spec-of(a, d)) ∧ settings_from_env(env) / acp_valve(argv, env) read that bundle unchanged (the readers gain no second spelling); ownership declared ⇒ proof declared (grade without proof is refused) ∧ ownership-preflight(gce-project:p) admits iff OwnershipProbe answers p ∧ observations.ownership = {grade, proof} iff ownership is declared; settings_from_env(env) admits iff record-sink-of(env[RECORD_SERVICE_URL]) answers a sink (no record sink ⇒ AgentdPreflightError naming the declaration site; a declared but unreachable sink is admitted and the spool absorbs it)"
       :counterexamples
         [(counterexample "宿(launchd の plist / systemd の unit)が env の束を 1 つずつ写す — 名が 1 つ増えた日に Mac・Linux・GCP node の 3 つの宿で片方だけ直り、機体を足す手順が宿ごとに違う(決定 23 の反対)")
          (counterexample "entry.py / runtime.py が env の名を自分で組む — 宣言 → env の写像点が join.hy と 2 つになり、既定(headless・inherit・置き場)が食い違う")
          (counterexample "ownership=company を proof なしで名乗れる — 会社 profile の API 呼び出しの境界(CLAUDE.md)が自己申告だけになり、GCE の外の機体が会社 node を名乗る")
          (counterexample "metadata の project-id が違っても参加する(log だけ)— 借りた GCP node や別 project の VM に会社 profile の job が結ばれる")
          (counterexample "所有の等級を spec.labels に agentd が書く — labels の書き手は E(acp-scheduling)で 403 になるか、宣言と観測の区別(突合の材料)が消える")
          (counterexample "会話の記録の service の宛先を持たない agentd が参加する(弁 off で走る)— ACP の turn-record には見出し(bytes / sha256)だけが並び本文がどこにも残らない(段 9f lane 9f-4 の残上流 3・pool の agentd の実弾 2026-09-14: 宣言 file に [record] が無いまま新しい code を上げると本文を失う)。宛先の無い宣言は理由つきで断り、宿の再起動が宣言の修正を待つ")
          (counterexample "参加の門が宛先へ接続して届くかを検める — service の一時の不達で agentd が起動できず、spool(届かない時の受け皿)の意味が消える。門は宣言の検ちょうど")])
     (law session-id-is-minted-by-agentd
       :statement "for_all claim that launches or resumes: session_id(launch params) = MintId() ∧ session_id ∉ {charter.session_id, charter.session_name, agent-job id}; sessionHandle.sessionId = stream.name = that id; after a session was cleaned up (its row stays registered in the host) the next job of the conversation launches with a fresh id and is not refused"
       :counterexamples
         [(counterexample "charter の固定の session_id で launch する — idle TTL で片付いた行が host に登記のまま残り、次の launch が `session is already registered` で LaunchFailed(実弾 2026-09-12 aj 031〜033)")
          (counterexample "agentd.hy が id を自分で組む(時刻や job の id から)— 純関数の外で id が生まれ、fake で反例を撃てない")])
     (law idle-session-ttl-is-declared-once
       :statement "the idle lifetime of a warm session is AgentdSettings.session_idle_ttl_seconds and nothing else; idle(s) ∧ now ≥ turn_ended_at(s) + ttl ⇒ session.cleanup(s) at the next heartbeat; the choice is judgment.sessions-to-retire (pure) and the clock is an effect"
       :counterexamples
         [(counterexample "TTL を agentd.hy や handlers.py の literal に散らす — 値を変えた時に片方だけ残り、片付けの拍と観測の拍で寿命が食い違う")
          (counterexample "idle の session を永遠に生かす — 会話ごとの tmux の pane が増え続け、node の容量(capacity)が温かい session で埋まる")])
     (law profile-remaining-is-observed-by-agentd-from-one-usage-point
       :statement "at each period profile_observe_seconds agentd reads the homes of this node once (ListProfileHomes = the agentcli registry × the existence of each profile's dir) and keeps only the live profile rows p (state ≠ retired) whose home is present (judgment.profile-rows-held); when that set is empty it issues NO ReadProfileUsage, logs 'no profile has a home' exactly once (AgentdState.no_profile_homes_logged, reset when a home appears) and still emits the profile-observed metric with homes = 0; otherwise, for_all such p: agentd issues exactly one ReadProfileUsage and, for p held by this node with a readable usage u and budget.unit = percent, writes status.observed(p) = {window = observed-window-of(p), remaining = 100 - used(u, window), resetAt, observedAt = captured(u), node = self} over the committed status (state and conditions preserved) with ifGeneration, only when it differs from the committed observed; refused / failed usage (ProfileUsageUnavailable — decided by the agentcli leaf, never by agentd) or a non-percent unit ⇒ no write and one log line; a profile not held ⇒ no write and no log; Conflict ⇒ no write this period and a re-read next period; the usage reader is handlers.read_profile_usage over USAGE_COMMAND, the registry reader is handlers.list_profile_homes over PROFILES_COMMAND, and sessionhost imports nothing from agentcli"
       :counterexamples
         [(counterexample "agentd が profile の残量を書かない — controller は observed の不在を unobserved(Unknown)としか読めず、盤の health が『profile 35 本の残量の観測がどれも窓より古いか無い』のまま、Scheduling は枯渇を判じられない(実弾 2026-09-12 23:4x・本番の profile 35 行)")
          (counterexample "agentd が会社境界を自分で判定する(handlers / judgment に company の述語を置く)— 判定点が agentcli の葉と 2 つになり、名簿の改訂で片方だけ残る(operator の規則 2026-09-09 の否定)")
          (counterexample "断られた profile に cached の値を書く — 非会社機体が会社 profile の残量を名乗り、controller が会社 profile の観測の由来(observed.node の ownership)を読み違える")
          (counterexample "post-image を observed だけで組む(conditions を落とす)— agentd は conditions の書き手でないので engine が断り、観測が 1 行も着地しない")
          (counterexample "断面が同じ拍にも書く — 35 行 × 周期ごとの status_synced が event journal を埋め、watch の拍が起き続ける")
          (counterexample "sessionhost が agentcli を import する — doeff(上流)が dotfiles(下流)に依存し、tool env(uv tool)では import が落ちて agentd が参加しない")
          (counterexample "profile を 1 つも持たない器(pool の pod)で周期ごとに `ai usage` を撃つ — 登録簿の生成物(profiles.gen.json)の無い器では読み口が毎周 exit 1 で落ち、log が『profile observation failed: FileNotFoundError』で埋まる(実弾 2026-09-13 zeus の agentd-pool・段 8e lane 4j)。器の profile の集合は家の在否で先に読み、空なら撃たない")
          (counterexample "読み口の落ち方(FileNotFoundError の文言)で『profile が無い』を判じる — 判定が dotfiles の内部の綴りに結ばれ、名簿の改訂で偽陰性・偽陽性になる")])
     (law conversation-cache-is-kept-only-on-the-same-node-and-home
       :statement "for_all Bound job j of conversation c claimed on node n with home h = session-affinity-key-of(plan(j)) and candidate session s (affinity.predecessor, else the last session of c on n): the arm of j is judgment.next-arm-for-job(s, session.get(s), h) alone; s alive ∧ idle ∧ attribution.home(s) = h ⇒ send; s alive ∧ idle ∧ attribution.home(s) ≠ h ⇒ session.cleanup(s) ∧ rehydrate; s alive ∧ ¬idle ⇒ defer; s registered ∧ terminal ∧ attribution.home(s) = h ⇒ session.resume(s); otherwise ⇒ rehydrate = session.launch whose prompt = charter.prompt ++ rehydrate-history-of(c, messages of c, source, inputs(j), rehydrate_history_byte_budget) (++ bodies on headless) where source = RecordedTurns(the record service's events of c read backwards from before=latest until record-history-satisfied or the first page) when the record service answers, else HeadlineTurns(the turn-record rows of c) and the prompt's head and the log name the thin rehydrate and its reason; a refused resume ⇒ rehydrate with the same minted id; turn-record(j).spec.sessionId = sessionHandle(j).sessionId; observations.sessions[*].account = attribution.account and observations.transcripts = the newest ended session per conversation whose transcript file exists, at most transcripts_observed_max"
       :counterexamples
         [(counterexample "profile を変えた手番を同じ機体の温かい session へ send する — 前の家の資格と cache で走り、binding の account が効かない(2026-09-13 の code の読み: next-arm-for-job は家を見ていなかった)")
          (counterexample "会話の宣言の model だけを変えた手番を同じ家の温かい session へ send する — CLI の session は起こした時の model のまま走り、charter.model が効かない(実射 2026-09-14 段 9o lane 9o-2: charter と turn-record は claude-opus-5 なのに『session started: model claude-sonnet-5』・返事も sonnet。家の鍵に model が無かった)")
          (counterexample "家をまたいで --resume する(transcript を新しい家へ写す前提)— operator 決定 #54 は cache の失効を受け入れると決めた。別の家の --resume は transcript を見つけられず SessionRefused → LaunchFailed で会話が止まる")
          (counterexample "器に行の無い predecessor を resume する — 別の機体で走った会話が `session is not registered` で LaunchFailed(Rehydrate の腕が無かった)")
          (counterexample "turn-record の spec に sessionId を書かない — Messaging が predecessor を名指せず、温かい session が片付いた(idle TTL・agentd の再起動・agent-job の行の回収)次の手番は同じ家でも文脈なしで起きる(本番 2026-09-13: 起こし方は send 39・launch 21・resume 0)")
          (counterexample "session の会話を回収される agent-job の行から導く — 終端の後に行が回収されると node の観測から会話が消え、Scheduling が cache を持つ node を選べない")
          (counterexample "「これまでの会話」を上限なしに畳む / 古い手番を黙って落とす — prompt が器の上限を超えるか、agent は落ちた事実も全文の在処も知らずに答える")
          (counterexample "履歴からの再開を ACP の見出しから畳む(text の無い entry を本文として読む)— 手番の本文が全部空の『これまでの会話』を agent に渡し、agent は文脈が無いことを知らずに答える。本文は記録の service の before=latest から読み、届かない時は薄い再開と名乗る")
          (counterexample "記録の service が答える拍にも ACP の turn-record の全量を読む(見出しを本文より先に・無条件に読む)— kind の全量は 29,913 行 / 172 MB / 頭の応答 59 秒(2026-09-14)で、claim は 0.3 秒で着地しているのに送るまで 127〜134 秒、単一の tick が塞がって node の lease(TTL 90 秒)が切れ、Scheduling が Running の行を Pending → Unschedulable(no-node-capacity)→ attempt 2 で Bound し直す(実弾 aj-HV9TMD3D… 21:44Z・aj-E61AWHDW… 22:01Z・agora-redesign #77)。見出しは service が答えなかった拍にだけ読む")])
     (law turn-credential-is-the-custody-lease-on-a-node-that-declares-it
       :statement "for_all Bound job j claimed on node n: source(j) = judgment.credential-source-of(plan(j), n.custody_declared) alone; source = lease ⇒ the session is launched in the home the custody lease was written to; source = missing ⇒ no session is launched, no Running and no sessionHandle is written, and j is Ended with condition CredentialSourceMissing; source = home only when n does not declare the custody service"
       :counterexamples
         [(counterexample "預かり所を宣言した node が account の無い job を charter の binding(Mac の ~/.config/claude-kento)で起こす — 手番が預かり所を 1 度も通らず機体の家の資格で走り、会社 / 個人の資格の置き場の不変条件が守られない(本番 2026-09-14: 直近 400 手番 account 0・agora-redesign #80)")
          (counterexample "account の無い job を Bound のまま claim せずに放置する — Scheduling の監督は node が生きている限り戻さず、その会話の手番が 1 本走っている扱いのまま永久に止まる。起こさない時は理由の条件つきで閉じる")
          (counterexample "資格の出所を claim の腕ごとに判じる(launch / resume / rehydrate / recover の各所で account を見る)— 判定点が増え、1 つの腕だけが家へ落ちる形が生える。出所は credential-source-of の 1 点で claim の頭に 1 度")
          (counterexample "預かり所の宣言の有無を URL の既定値(CUSTODY_URL_DEFAULT)の在否で判じる — 既定の URL は常に在るので全 node が宣言した扱いになり、移行前の機体の手番が全部断られる。宣言は join の env の在否 1 点")
          (counterexample "session を使い回す鍵を『家の鍵』と呼ぶ — 資格の選択と cache の同一性が同じ語で語られ、鍵の一致を資格の一致と読み違える(operator 逐語 2026-09-14 \"oh my god, there's home key???\")。鍵の名は session-affinity-key-of")])
     (law node-names-its-capability-table-and-never-drops-a-setting-silently
       :statement "for_all node n joined by agentd: n.status.capabilities = judgment.capabilities-of alone, one entry per agent kind agentd can launch with settings ⊇ restartOn and restartOn = the fields of session-affinity-key-of; for_all Bound job j whose declared effort differs from the warm session's launched effort in the same home: the session is cleaned up and the same session is resumed with the new effort; for_all declared field f of j's charter that this node cannot apply on this turn: j's ended conditions carry AgentSettingIgnored naming f, its value and why"
       :counterexamples
         [(counterexample "能力の表を名乗らない(段 10 lane 10e より前の agentd)— ACP の許可名簿の投影は『受ける欄なし』と読み、この node しか無い時は operator が model も effort も選べない。名乗りは lease と同じ拍で必ず書く")
          (counterexample "effort を session-affinity-key-of の鍵に入れる — effort を変えるたびに履歴からの再開(cache の失効)になる。effort は process の旗で、同じ session を --resume すれば足りる")
          (counterexample "effort が違う温かい session にそのまま send する — process の旗は起動時のものなので前の effort で走る(黙って落とす)。帰属の effort と比べて起こし直す")
          (counterexample "温かい send で違う work_dir を黙って前の cwd で走らせる — operator は宣言が効いたと思う。条件 AgentSettingIgnored で名指す(session は作り直さない・次に起こす時に効く)")
          (counterexample "能力の表の値を judgment.hy と effects.py と agentd.hy に別々に書く — 名乗りと判断が食い違う。表は effects.AGENT-CAPABILITIES の 1 点で、名乗りも判断もそこから読む")
          (counterexample "restartOn に settings に無い欄を書く — ACP の読み手(nodeViewOf)が行の誤りとして node を落とし、投影から消える")])
     (law backend-liveness-is-observed-not-inferred-from-the-status-word
       :statement "for_all headless session row r of a sessionhost host h at h's startup: in_flight(r) ∧ ¬(pid(r) exists ∧ owned_by(h, pid(r))) ⇒ status(r) = exited ∧ cause(r).category = vanished ∧ cause(r).reason names pid(r), before h accepts its first RPC; ¬in_flight(r) ∨ terminal(r) ⇒ r is untouched; the decision is headless_protocol.recovery_verdict alone; for_all session.get / session.list answer v of a non-terminal row: v.backend_alive = the host's observation of that row's backend (headless: pid exists ∧ owned; tmux / herdr: pane ∈ panes(session)); for_all Running job j of agentd with view v: v ≠ None ∧ ¬terminal(v) ∧ ¬turn-ended(v) ∧ v.backend_alive = False ⇒ job-step-of = session-lost ⇒ turn-record(j).state = ended ∧ phase(j) = Ended ∧ conditions(j) ∋ SessionLost naming session, pid and the observed time, without session.cleanup; v.backend_alive = None ⇒ judged as alive; for_all Bound job with a busy candidate s: s.backend_alive = False ⇒ next-arm-for-job ∈ {resume, rehydrate} with retire = s (never defer)"
       :counterexamples
         [(counterexample "再起動の後の復帰が『器の行が在る = 走っている』と読んで observe を返す(2026-09-14 14:35 実弾: kickstart -k で子 process が道連れ・行は running のまま・agentd は observe / defer を返し続け、会話が永久に「動いている」・次の郵便が Unschedulable: conversation-turn-in-flight で永久 Pending)")
          (counterexample "起動時の awaiting latch の全 clear を headless の行にも掛ける — 『手番の途中』の事実が消えて monitor の gone の腕も復帰も判断できず、死んだ手番が running のまま残る")
          (counterexample "backend の生死を wire の status の語(running / finished)で判じる — status は host が書いた語で、host が観測していない(再起動で registry を失った)拍には現実を映さない。生死は pid と所有の観測から")
          (counterexample "観測の無い眺め(launch の応答の backend_alive = None)を死と読む — 起こした直後の job が全部 SessionLost で閉じる。観測断 ≠ 死亡(ADR-DOE-AGENTS-009)")
          (counterexample "idle の温かい行を process が降りているという理由で復帰が終端に倒す — 温かい session の設計(send が --resume で同じ session を起こし直す)が壊れ、再起動のたびに会話の cache を捨てる")
          (counterexample "session-lost の job の session を agentd が session.cleanup で片付ける — host の monitor が観測した終端(exit code つきの failed / vanished)より粗い cancelled で上書きする。session は host に任せる")
          (counterexample "新しい cause の category(backend_process_dead)を凍結表に足す — 証拠つき死亡の語彙は vanished の 1 つ(ADR-DOE-AGENTS-009)で、同じ事実に 2 つ目の概念が生える")
          (counterexample "resume の腕が persisted cause の欄を get で読む(store.hy)— 手で書かれた行 1 つで session.get も resume も読めず、会話が rehydrate に落ちる(2026-09-14 18:5x 実弾 KeyError 'category')")
          (counterexample "headless の session.resume の launch-params に events_root を運ばない — headless-launch-session の (get params \"events_root\") で KeyError、本番の --resume が全部 rehydrate に落ちる(2026-09-14 実弾 2 件)")])
     (law stop-closes-running-turns-instead-of-taking-the-children-down-silently
       :statement "for_all sessionhost host h receiving SIGTERM with the accept loop alive: before h exits, every registered shutdown hook runs (agentd: for_all running job j in memory: turn-record(j).state = ended ∧ phase(j) = Ended ∧ conditions(j) ∋ AgentdRestart naming the node, the reason, the session and the time, with the lease revoked and the status frame ended), then for_all non-terminal headless row r of h: in_flight(r) ⇒ status(r) = stopped ∧ cause(r).category = cancelled ∧ cause(r).reason names the host stop; ¬in_flight(r) ⇒ r is untouched; every registered headless process is terminated with one parallel grace (EOF → TERM → KILL) instead of one grace per process; the decision is headless_protocol.stop_verdict alone; h then re-raises the same signal to itself and exits through its finally (lease released); and the ACP address of agentd (stream push included) is the declared ACP_DAEMON_URL with no localhost default"
       :counterexamples
         [(counterexample "TERM で SystemExit だけ投げて子を process group の kill に任せる — 手番の途中の行と job が黙って残り、次の起動の復帰が『死んだ』と観測するまで会話が「動いている」のまま(2026-09-14 14:35〜18:5x 実弾の入口)")
          (counterexample "headless の子を setsid で切り離して復帰で拾おうとする — stdin / stdout の pipe の親が死ぬと子は EPIPE で降りるか、生きても events file の書き手が居ない。器として使えない process を『生きている』と数える")
          (counterexample "停止の hook を SystemExit の後の finally で走らせる — accept loop が死んだ後は agentd の器の RPC(session.get)が応えず、hook が hang するか眺めを読めない。hook は 1 度目の TERM の別 thread で、accept を生かしたまま")
          (counterexample "撃ち直しを _thread.interrupt_main で行う — main thread は accept の blocking syscall の中で、次の接続が来るまで handler に来ない(実測 2026-09-14: 30 s 待っても exit しない)。実の os.kill で撃ち直す")
          (counterexample "process を 1 つずつ kill() で降ろす — EOF 5 s + TERM 5 s の猶予が process の数だけ直列に積み、launchd の ExitTimeOut(20 s)を越えて SIGKILL され、残りの行が黙って残る")
          (counterexample "停止の腕が idle の温かい行も stopped にする — 再起動のたびに会話の cache を捨てる(次の send が --resume で同じ session を起こし直す設計を壊す)")
          (counterexample "実況の push の宛先に 127.0.0.1:8868 の既定値を残す — 宣言の無い agentd が黙って退役した Mac の中継へ押し続ける。宛先は宣言ちょうど・無ければ参加しない")])
     (law conversations-compact-themselves-at-their-declared-threshold
       :statement "for_all conversation c with a row whose status.agent.compactAt = k (an integer 0..100) and for_all Bound job j of c claimed by agentd with a candidate session s that is not mid-turn: percent(s) = judgment.context-percent-of(context measured at the end of s's last turn) and (percent(s) ≠ None ∧ percent(s) ≥ k) ⇒ next-arm-for-job(s, view(s), home, effort, compact = True) = rehydrate with compacts = True and retire = s when s is alive, and agentd emits one agentd_compactions_total line naming c; (k absent ∨ percent(s) = None ∨ percent(s) < k) ⇒ compact = False and the arm is R20's; the measurement is judgment.deltas-of (DeltaBatch.context from the last assistant usage and the result's modelUsage[model].contextWindow for claude, the last token usage and the model context window for codex) and agentd writes it to no ACP row"
       :counterexamples
         [(counterexample "受付の会話(永続・郵便が絶えない)を温かい session へ送り続ける — 文脈が窓を埋め、agent が古い郵便を忘れるか CLI が自分で要約して振り分けの根拠が消える(operator 2026-09-14「that routing agent should compact itself」の実弾)")
          (counterexample "閾値を agentd の値の宣言(AgentdSettings)に置く — 会話ごとに違う閾値(受付 60・議論 90)を表せず、宣言の座が会話の行(agora-conversation が書く)と agentd の 2 つになる")
          (counterexample "文脈の大きさを turn-record の usage の和(input + cacheRead + …)から導く — 和は手番の全 message の入力の合計で、最後の prompt の大きさではない(3 message の手番は 3 倍に見える)")
          (counterexample "窓の大きさを model の名から agentd が推測する(claude は 200k と決め打つ)— [1m] の model や codex の窓が違い、実測の無い値で圧縮の拍を決める。窓は器が名乗った値(result の modelUsage / model_context_window)だけ")
          (counterexample "圧縮の手番を手番の途中の候補にも撃つ(defer より先に rehydrate)— 走っている手番の session を片付け、その手番の結末が消える")
          (counterexample "圧縮の判断を claim の腕(agentd.hy)で行い next-arm-for-job にも家の判断を残す — 起こし方の判定点が 2 つになり、fake で反例を撃てない(R10 の反例と同じ)")
          (counterexample "実測を turn-record の status.usage に書く(契約に無い欄)— 読み手の zod / Hy の写しが行を落とすか、engine の statusByteBudget の外で書き手が第 2 の定義点を作る。耐久にするなら契約の便が先")])
     (law turn-events-are-appended-to-the-record-per-tick
       :statement "for_all running job j observed by agentd and for_all tick t at which stream-records reads new material of j: the events e_1..e_n that judgment.deltas-of derives from that material are appended (not replaced) to the status.entries of turn-record(j) within the same tick by agentd.append-entries, each with at = t and a seq strictly greater than every seq already on the row, via one CAS write on the last known image of the row (Conflict ⇒ one re-read and one retry; Refused or missing row ⇒ the events stay in InFlightJob.pending_entries and ride the next write); the row's entries JSON never exceeds TURN_RECORD_ENTRIES_BYTE_BUDGET (the oldest events are dropped first and a single leading kind=system marker with truncated=true and dropped=k replaces them); every appended entry is the JSON of a TurnEntryHeadline (seq, at, kind, toolName?, toolUseId?, bytes, sha256, isError?) derived by judgment.headline-of-body from the body sent to the record service — it carries no text / summary / input / output / model, and its sha256 = sha256 of record-body-bytes-of(body) (the service's identity of the same event); the record service's appendAnswer.highestProducerSeq for the stream of j lands as status.recordedSeq (never decreasing) with status.recordRef = record:<cid>/<streamId>; and the end of the turn drains the remaining material through the same point, then writes state=ended and usage over the appended entries without replacing them"
       :counterexamples
         [(counterexample "出来事を手番の終わりにだけ書く(旧の形・実弾 2026-09-13: 本番の turn-record 97 行の entries が最後の本文 1 行・at は全部同じ・途中で落ちた手番は空)— 会話の面に agent の出力の全史が無く、途中で落ちた手番は何も残らない")
          (counterexample "終わりの書きが entries を置換する(手番の全材料を読み直した列で上書き)— 拍ごとの at が消え、追記で残った印(dropped)も消え、同じ出来事が seq を変えて二度並ぶ")
          (counterexample "Refused を捨てる — ACP が一時的に断った拍の出来事が永久に消える。出来事は持ち越して次の書きに乗せる")
          (counterexample "拾い直した job が seq 0 から書く — 行の seq と衝突し、画面の行の鍵(<agentJobId>#<seq>)が同じになって別の出来事が 1 行に畳まれる")
          (counterexample "行の上限を持たない — 長い手番(道具 100 回 × 4 KB)で 1 行が数 MB になり、watch の差分と画面の全量の置換が拍ごとに膨れる。上限は書き手が守り、読み手は印で知る(推定しない)")
          (counterexample "画面の糊や webapp が切り詰めを推定する(『entries が 1 件だから途中は無い』)— 落とした出来事と読めていない出来事を同じ顔で描く。印(dropped)が在る時だけ『落とした』と言える")
          (counterexample "ACP の entry に本文(text / summary / input)を写す(段 9f より前の形・本番 2026-09-13: 30k 行の entries が頭脳の live の大半)— control plane が本文の器になり、行の上限で本文が切れ、画面も再開も『切れた写し』を読む。見出し(bytes / sha256)だけを写し、本文は記録の service から取り寄せる(claim check)")
          (counterexample "見出しを dict で組む(text を持てる型)— 1 箇所の書き手が本文を混ぜても検が落ちない。TurnEntryHeadline に本文の欄が無いので、本文を持つ entry は型で落ちる")])
     (law live-events-are-pushed-within-50ms-while-watched
       :statement "for_all agentd with stream_capability = events and for_all tick at which some InFlightJob is capturing (the last push answered subscribers > 0): the wait bound of AcpWatchSse is AgentdSettings.events_poll_seconds ≤ 0.05 and nothing else, so new lines of the events file reach the relay (AcpStreamPush) within one such tick; with stream_capability = frames the bound stays frame_interval_seconds (2–5 Hz capture); with no capturing job the bound is transcript_poll_seconds, with no job idle_wait_seconds; the choice is judgment.wait-seconds-for (pure) and every period is declared once on AgentdSettings. The turn-record append (agentd.append-entries, one ACP event per write) does NOT follow the poll: it happens only when judgment.record-due (last_record_ms + transcript_poll_seconds ≤ now, or never written) holds, and the events of the other ticks ride InFlightJob.pending_entries to the next write or the end of the turn"
       :counterexamples
         [(counterexample "headless の器でも frame の間隔(0.4 s)で events を読む — 画面が attach していても agent の出力が 400 ms 刻みでしか中継へ届かず、実況の最初の tail と割り込みの反映が 200 ms を超える(本番 2026-09-13 17:1x: 247〜258 ms / 219 ms の agentd 側の根)")
          (counterexample "tui の器の capture の周期まで 50 ms にする — pane の断面を 20 Hz で撮り、tmux と中継の ring(2000 frame)が数分で埋まる(issue #1 の決定 4 の否定)")
          (counterexample "購読者が居ない間も 50 ms で読む — 誰も見ていない手番のために agentd が 20 Hz で file を読み、記録の追記(CAS)の拍も細かくなって event journal を埋める")
          (counterexample "周期を handlers.py や agentd.hy の literal に置く — 値を変えた時に片方だけ残り、判断(wait-seconds-for)の検が本番の周期を撃てない")
          (counterexample "記録の追記を push と同じ 50 ms の拍で書く — 走っている手番 1 つで毎秒 10〜20 の CAS の書きが ACP の event journal に並び、画面の糊の watch の拍が飽和して hello まで 15 s 待った(実弾 2026-09-13 18:3x・便 2 の初版)")])]
  :enforcement
    [(deftest test-adr-doe-agents-012-no-agora-ledger-words-in-sessionhost
       ;; R1 の針: sessionhost の全 source(acp/ を含む)の code 行に agora の台帳 API の語が無い。
       (setv hits [])
       (for [path (source-files)]
         (for [line (code-lines path)]
           (for [word AGORA-LEDGER-WORDS]
             (when (in word line)
               (.append hits f"{(.relative-to path SESSIONHOST-DIR)}: {word}")))))
       (assert (= hits []) f"agentd の出口は ACP と custody だけ(ADR-DOE-AGENTS-012 R1): {hits}"))
     (deftest test-adr-doe-agents-012-bound-to-me-is-the-only-job-selection
       ;; R2 の針: Bound の語を判定に使うのは judgment.hy の bound-to-me だけ、
       ;; status の "binding" 欄を書く形(setv (get … "binding"))が acp/ に無い。
       (setv judgment (/ ACP-DIR "judgment.hy"))
       (setv agentd (/ ACP-DIR "agentd.hy"))
       ;; import の一覧の項(行が語そのもの)は使用ではない。
       (setv bound-uses (lfor line (+ (code-lines judgment) (code-lines agentd))
                              :if (and (in "PHASE-BOUND" line)
                                       (!= (.strip line) "PHASE-BOUND"))
                              line))
       (assert (= (len bound-uses) 1)
               f"Bound の判定は bound-to-me の 1 点(ADR-DOE-AGENTS-012 R2): {bound-uses}")
       (assert (in "(= (.get status \"phase\") PHASE-BOUND)" (get bound-uses 0)))
       ;; status の "binding" 欄を書く形は無い。同じ語が charter(session.launch の typed
       ;; auth binding)にも在るので、charter を組む charter-with-grant の中だけを許す。
       (for [path [judgment agentd]]
         (setv block "")
         (for [line (code-lines path)]
           (when (.startswith line "(defk ")
             (setv block (get (.split line) 1)))
           (when (re.search r"\(setv\s+\(get\s+\S+\s+\"binding\"" line)
             (assert (= block "charter-with-grant")
                     f"agentd は agent-job の binding を書かない(ADR-DOE-AGENTS-012 R2): {path.name} {block}: {line}"))))
       ;; 反例(挙動): 別の node に結ばれた job と Pending の job は受けない。
       (setv world (World))
       (.put-row world.acp (bound-row "s-other" "someone-else" None "claude" PHASE-BOUND))
       (.put-row world.acp (bound-row "s-pending" "mac-1" None "claude" "Pending"))
       (.tick world 0)
       (assert (= world.sessions.launches []))
       ;; 自分に結ばれた job は受け、binding は写して返すだけ(欄は不変)。
       (.put-row world.acp (bound-row "s-mine" "mac-1" None "claude" PHASE-BOUND))
       (.tick world 100)
       (assert (= (len world.sessions.launches) 1))
       (setv mine (status-of (get world.acp.rows "acp-system:agent-job:s-mine")))
       (assert (= (get mine "phase") "Running"))
       (assert (= (get mine "binding") {"node" "mac-1" "profile" "personal"})))
     (deftest test-adr-doe-agents-012-valve-defaults-off
       (assert (is ACP-VALVE-DEFAULT False))
       (assert (is (. (acp-valve ["serve"] {}) enabled) False))
       (assert (is (. (acp-valve ["serve"] {ACP-VALVE-ENV "off"}) enabled) False))
       (assert (is (. (acp-valve ["serve" "--acp"] {}) enabled) True))
       (assert (is (. (acp-valve ["serve"] {ACP-VALVE-ENV "on"}) enabled) True))
       ;; 弁は console script の入口(acp/entry.py)が持ち、host.hy / hostmain.py は agentd を知らない。
       (setv entry (.read-text (/ ACP-DIR "entry.py") :encoding "utf-8"))
       (assert (in "acp_valve(" entry))
       (for [name ["host.hy" "hostmain.py"]]
         (for [line (code-lines (/ SESSIONHOST-DIR name))]
           (assert (not (or (in "sessionhost.acp" line)
                            (in "acp_valve" line)
                            (in "start_agentd_thread" line)))
                   f"{name} は agentd の弁を持たない(弁は acp/entry.py の 1 点): {line}"))))
     (deftest test-adr-doe-agents-012-capture-stops-at-zero-subscribers
       (assert (= (run (capture-verdict 0)) "stop"))
       (assert (= (run (capture-verdict None)) "stop"))
       (assert (= (run (capture-verdict 2)) "continue"))
       ;; 反例(挙動): 購読 0 の間は何拍回しても SessionCapture が呼ばれず、購読者が
       ;; 現れると status frame の読み直しの後に capture が始まる。
       (setv world (World))
       (.put-row world.acp (bound-row "s-cap" "mac-1" None "claude" PHASE-BOUND))
       (.tick world 0)
       (.tick world 500)
       (.tick world 500)
       (assert (= world.sessions.captures []))
       (setv (get world.acp.subscribers (sid-of world "s-cap")) 1)
       (.tick world 5000)
       (.tick world 500)
       (assert (= world.sessions.captures [#((sid-of world "s-cap") 60)])))
     (deftest test-adr-doe-agents-012-borrowed-credentials-not-on-disk-in-plain
       ;; claude: 札は env に乗り、file には 1 つも書かれない。
       (setv world (World))
       (.put-row world.acp (bound-row "s-cl" "mac-1" "acct" "claude" PHASE-BOUND))
       (.tick world 0)
       (setv launch (get world.sessions.launches 0))
       (assert (= (get (object-at launch "session_env") "CLAUDE_CODE_OAUTH_TOKEN") "sk-ant-oat01-secret"))
       (assert (= world.local.files {}))
       (for [line world.local.logs]
         (assert (not-in "sk-ant-oat01-secret" line)))
       (for [metric world.local.metrics]
         (assert (not-in "sk-ant-oat01-secret" (str metric))))
       ;; codex: 唯一の平文は家の中の auth.json(<homes>/codex/<account>/auth.json)。
       (setv world2 (World))
       (setv world2.sessions.agent-type "codex")
       (.put-row world2.acp (bound-row "s-cx" "mac-1" "acct" "codex" PHASE-BOUND))
       (.tick world2 0)
       (assert (= (list (.keys world2.local.files)) ["/homes/codex/acct/auth.json"]))
       (setv launch2 (get world2.sessions.launches 0))
       (assert (= (get (object-at launch2 "binding") "auth_file") "/homes/codex/acct/auth.json"))
       ;; 針: program は札を LogLine / MetricLine に載せる形を持たない。
       (for [line (code-lines (/ ACP-DIR "agentd.hy"))]
         (when (or (in "LogLine" line) (in "MetricLine" line))
           (assert (not-in "access-token" line))
           (assert (not-in "auth-json" line)))))
     (deftest test-adr-doe-agents-012-running-jobs-settle-from-rows
       ;; R7 の針: 次の 1 手の判定は judgment.hy の job-step-of の 1 点。agentd.hy は終端の語彙
       ;; (SESSION-TERMINAL-STATUSES)も Running の述語も直に読まず、job-step-of を呼ぶ。
       (setv judgment-lines (code-lines (/ ACP-DIR "judgment.hy")))
       (setv agentd-lines (code-lines (/ ACP-DIR "agentd.hy")))
       (assert (= (len (lfor line judgment-lines :if (.startswith line "(defk job-step-of ") line)) 1))
       (assert (= (len (lfor line judgment-lines :if (.startswith line "(defk running-on-me ") line)) 1))
       (assert (= (len (lfor line judgment-lines
                             :if (re.search r"\((not-)?in view\.status SESSION-TERMINAL-STATUSES\)" line)
                             line))
                  3)
               "終端の語彙を読む述語は judgment.hy の job-outcome-of・job-step-of・session-alive ちょうど")
       (for [line agentd-lines]
         (assert (not-in "SESSION-TERMINAL-STATUSES" line)
                 f"agentd.hy は終端の語彙を直に読まない(ADR-DOE-AGENTS-012 R7): {line}")
         (assert (not (and (in "PHASE-RUNNING" line) (in "(= " line)))
                 f"Running の述語は judgment.hy の running-on-me の 1 点(R2 / R7): {line}"))
       (assert (>= (len (lfor line agentd-lines :if (in "(job-step-of " line) line)) 2)
               "observe-job と recover-job は同じ job-step-of を通る")
       ;; 反例(挙動): 再起動(memory を捨てる)後の最初の tick で自分の Running を行から拾い、
       ;; 器が終端なら記録の腕だけで閉じる(launch は増えない)。他人の Running は触らない。
       (setv world (World))
       (.put-row world.acp (bound-row "s-mine" "mac-1" None "claude" PHASE-BOUND))
       (.tick world 0)
       (assert (= (len world.sessions.launches) 1))
       (setv world.state (initial-state))
       (.finish world.sessions (sid-of world "s-mine") "done" {"ok" True})
       (.put-row world.acp (running-row "s-theirs" "someone-else" "agentd"))
       (.put-row world.acp (running-row "s-not-mine" "mac-1" "other-principal"))
       (.tick world 1000)
       (assert (= (len world.sessions.launches) 1))
       (setv mine (status-of (get world.acp.rows "acp-system:agent-job:s-mine")))
       (assert (= (get mine "phase") PHASE-ENDED))
       (assert (= (get mine "result") {"ok" True}))
       (setv record (status-of (get world.acp.rows "default:turn-record:s-mine")))
       (assert (= (get record "state") "ended"))
       (for [job-id ["s-theirs" "s-not-mine"]]
         (assert (= (get (status-of (get world.acp.rows f"acp-system:agent-job:{job-id}")) "phase")
                    PHASE-RUNNING)))
       (assert (= world.state.jobs #()))
       ;; 器に session が無い Running(実弾 002 の孤児)は SessionFailed で Ended。
       (setv orphan (World))
       (.put-row orphan.acp (running-row "s-orphan" "mac-1" "agentd"))
       (.tick orphan 0)
       (setv gone (status-of (get orphan.acp.rows "acp-system:agent-job:s-orphan")))
       (assert (= (get gone "phase") PHASE-ENDED))
       (assert (= (last-condition-type gone) "SessionFailed"))
       ;; 純関数の閉語彙。
       (assert (= (run (job-step-of None 0 True)) "fail-missing")))
     (deftest test-adr-doe-agents-012-capture-gone-is-terminal-and-ticks-do-not-share-failure
       ;; R8 の針: SessionCapture の答えは閉語彙(agentd.hy の bind の型)・実 handler は host の
       ;; 断り(AgentdClientError)を CaptureGone に写す・R9 の縁は agentd.hy に 7 つ(agentd-tick に heartbeat・profile の
       ;; 観測〔R18〕・受け・割り込みの配達〔R21〕・job ごと・spool の送り〔段 9f lane 9f-2〕の 6 つ + 拍の途中の spool の
       ;; 書き spool-record-bodies の 1 つ)。
       (setv agentd-lines (code-lines (/ ACP-DIR "agentd.hy")))
       (setv handler-lines (code-lines (/ ACP-DIR "handlers.py")))
       (assert (any (gfor line agentd-lines (in "(<- outcome (| CaptureFrame CaptureGone)" line))))
       (assert (any (gfor line handler-lines (in "except AgentdClientError" line))))
       (assert (any (gfor line handler-lines (in "return CaptureGone(" line))))
       (assert (= (len (lfor line agentd-lines :if (in "(except [e IO-FAILURES]" line) line)) 7)
               "縁は heartbeat・profile の観測・受け・割り込みの配達・job ごと・spool の送り・spool の書きの 7 つ(ADR-DOE-AGENTS-012 R9・R18・R21・段 9f lane 9f-2)")
       ;; 反例(挙動): gone は例外にならず、capture を止め、器の終端で Ended と ended。
       (setv world (World))
       (.put-row world.acp (bound-row "s-gone" "mac-1" None "claude" PHASE-BOUND))
       (.tick world 0)
       (setv gone-sid (sid-of world "s-gone"))
       (setv (get world.acp.subscribers gone-sid) 1)
       (.tick world 5000)
       (setv world.sessions.capture-gone "tmux capture-pane failed: no server running")
       (.tick world 500)
       (assert (= world.sessions.captures [#(gone-sid 60)]))
       (assert (= (lfor line world.local.logs :if (in "tick failed" line) line) []))
       (assert (is (. (get world.state.jobs 0) stream-gone) True))
       (.tick world 5000)
       (assert (= world.sessions.captures [#(gone-sid 60)]))
       (.finish world.sessions gone-sid "done" {"ok" True})
       (.tick world 500)
       (assert (= (get (status-of (get world.acp.rows "acp-system:agent-job:s-gone")) "phase") PHASE-ENDED))
       (assert (= (get (status-of (get world.acp.rows "default:turn-record:s-gone")) "state") "ended"))
       ;; 器が終端の拍は capture を撃たない。
       (setv quiet (World))
       (.put-row quiet.acp (bound-row "s-quiet" "mac-1" None "claude" PHASE-BOUND))
       (.tick quiet 0)
       (setv (get quiet.acp.subscribers (sid-of quiet "s-quiet")) 1)
       (.tick quiet 5000)
       (.finish quiet.sessions (sid-of quiet "s-quiet") "done" None)
       (.tick quiet 500)
       (assert (= quiet.sessions.captures []))
       (assert (= (get (status-of (get quiet.acp.rows "acp-system:agent-job:s-quiet")) "phase") PHASE-ENDED))
       ;; R9 の反例(挙動): 1 job の器の RPC が落ちても heartbeat と他の job は進む。
       (setv shared (World))
       (.put-row shared.acp (bound-row "s-a" "mac-1" None "claude" PHASE-BOUND))
       (.put-row shared.acp (bound-row "s-b" "mac-1" None "claude" PHASE-BOUND))
       (.tick shared 0)
       (setv (get shared.sessions.failures (sid-of shared "s-a")) (RuntimeError "socket reset"))
       (.finish shared.sessions (sid-of shared "s-b") "done" None)
       (.tick shared 30000)
       (setv node (status-of (get shared.acp.rows "default:node:mac-1")))
       (assert (= (get (object-at node "lease") "heartbeatAt") 31000))
       (assert (= (get (status-of (get shared.acp.rows "acp-system:agent-job:s-b")) "phase") PHASE-ENDED))
       (assert (= (get (status-of (get shared.acp.rows "acp-system:agent-job:s-a")) "phase") PHASE-RUNNING))
       (assert (in "agentd: job s-a tick failed: RuntimeError: socket reset" shared.local.logs)))
     (deftest test-adr-doe-agents-012-warm-session-send-instead-of-launch
       ;; R10 の針: 起こし方の判定は judgment.hy の next-arm-for-job の 1 点、手番の終わりの読みは
       ;; job-step-of の 1 点。agentd.hy は lifecycle の語を比較せず turn-ended-at を読まない
       ;; (SessionList の絞りの引数だけ)。TTL の値は AgentdSettings の 1 点(既定 600)。
       ;; host 側: lifecycle の閉語彙に multi_turn・turn_ended_at の書き点は policy.hy の 1 つ。
       (setv judgment-lines (code-lines (/ ACP-DIR "judgment.hy")))
       (setv agentd-lines (code-lines (/ ACP-DIR "agentd.hy")))
       (assert (= (len (lfor line judgment-lines :if (.startswith line "(defk next-arm-for-job ") line)) 1))
       (assert (= (len (lfor line judgment-lines :if (.startswith line "(defk sessions-to-retire ") line)) 1))
       (assert (= (len (lfor line judgment-lines :if (in "JOB-STEP-TURN-END" line) line)) 2)
               "turn-end を返す点は job-step-of ちょうど(import の項 + 1)")
       (for [line agentd-lines]
         (assert (not-in "turn-ended-at" line)
                 f"agentd.hy は turn_ended_at を直に読まない(R10): {line}")
         (assert (not (and (in "LIFECYCLE-MULTI-TURN" line) (in "(= " line)))
                 f"agentd.hy は lifecycle の語を比較しない(R10): {line}")
         (assert (not-in "NEXT-ARM-LAUNCH" line)
                 f"agentd.hy は launch を自分で決めない(R10): {line}"))
       (assert (= (. (AgentdSettings :node-name "x") session-idle-ttl-seconds) 600))
       (assert (= (len (lfor line (code-lines (/ SESSIONHOST-DIR "policy.hy"))
                             :if (in ":turn-ended-at" line) line))
                  1)
               "turn_ended_at の書き点は policy.hy の monitor の 1 つ")
       (assert (any (gfor line (code-lines (/ SESSIONHOST-DIR "launch.hy"))
                          (in "LIFECYCLE-MULTI-TURN \"multi_turn\"" line))))
       ;; policy.hy は deff / defhandler の Hy で共通の品質検査の投影が無いので、ここでは
       ;; import せず code 行で針を撃つ(挙動の反例は tests/sessionhost_policy_deftests.hy)。
       (setv policy-lines (code-lines (/ SESSIONHOST-DIR "policy.hy")))
       (assert (= (len (lfor line policy-lines :if (.startswith line "(deff is-multi-turn ") line)) 1))
       (assert (any (gfor line policy-lines (in "(is-multi-turn row.lifecycle)))))" line)))
               "reap-exempt は multi_turn を免除しない(監視される)")
       ;; 反例(挙動): 同じ会話の 2 手番目は launch を呼ばず send、別会話は launch、
       ;; TTL 超過で cleanup。
       (setv world (World))
       (run-warm-turn world "t-1" "conv-a" "first")
       (assert (= (len world.sessions.launches) 1))
       (assert (= world.sessions.cleanups []))
       (setv warm-sid (sid-of world "t-1"))
       (assert (!= warm-sid "t-1") "session の id は agentd が鋳造する(charter / job の id ではない)")
       (run-warm-turn world "t-2" "conv-a" "second")
       (assert (= (len world.sessions.launches) 1))
       (assert (= world.sessions.resumes []))
       (assert (= (get world.sessions.sends -1) #(warm-sid "second" True)))
       (setv second (status-of (get world.acp.rows "acp-system:agent-job:t-2")))
       (assert (= (get second "phase") PHASE-ENDED))
       (assert (= (get (object-at second "sessionHandle") "sessionId") warm-sid))
       (assert (= (get (status-of (get world.acp.rows "default:turn-record:t-2")) "state") "ended"))
       (setv warm-metrics (lfor m world.local.metrics
                                :if (and (= (get m "metric") "agent-job-to-send") (= (get m "agentJobId") "t-2"))
                                m))
       (setv warm-ms (get (get warm-metrics 0) "ms"))
       (assert (isinstance warm-ms int))
       (assert (< warm-ms 2000))
       (run-warm-turn world "t-3" "conv-b" "other")
       (assert (= (len world.sessions.launches) 2))
       (assert (= (get (get world.sessions.launches 1) "session_id") (sid-of world "t-3")))
       (.tick world 700000)
       (assert (= (sorted world.sessions.cleanups) (sorted [warm-sid (sid-of world "t-3")])))
       ;; 追補 2 の反例: 片付いた後の次の job は新しい id で起こし直しに成功する(charter の固定の
       ;; id で `session is already registered` に落ちない)。R20: 同じ機体 ∧ 同じ家なので片付いた
       ;; session から --resume(cache を保つ)。
       (run-warm-turn world "t-4" "conv-a" "again")
       (assert (= (len world.sessions.launches) 2))
       (assert (= (lfor resumed world.sessions.resumes (get resumed "session_id")) [warm-sid]))
       (assert (!= (sid-of world "t-4") warm-sid))
       (assert (= (get (status-of (get world.acp.rows "acp-system:agent-job:t-4")) "phase") PHASE-ENDED)))
     (deftest test-adr-doe-agents-012-session-id-is-minted-by-agentd
       ;; R15 の針: MintId を撃つ点は agentd.hy の claim-job の 1 つ、charter の id を落とすのは
       ;; launch-plan-of、据えるのは charter-with-session-id の 1 点。反例(挙動): launch の
       ;; session_id は charter の綴りでも job の id でもなく、鋳造の綴り。
       (setv agentd-lines (code-lines (/ ACP-DIR "agentd.hy")))
       (assert (= (len (lfor line agentd-lines :if (in "(MintId)" line) line)) 1))
       (setv judgment-lines (code-lines (/ ACP-DIR "judgment.hy")))
       (assert (= (len (lfor line judgment-lines :if (.startswith line "(defk charter-with-session-id ") line)) 1))
       (assert (any (gfor line judgment-lines (in "(.pop charter-out \"session_id\" None)" line))))
       (setv world (World))
       (.put-row world.acp (bound-row "m-1" "mac-1" None "claude" PHASE-BOUND))
       (.tick world 0)
       (setv launch (get world.sessions.launches 0))
       (setv minted (sid-of world "m-1"))
       (assert (= (get launch "session_id") minted))
       (assert (= (get launch "session_name") minted))
       (assert (not-in minted #{"m-1" "charter-m-1"}))
       (assert (not-in "charter-m-1" (str launch))))
     (deftest test-adr-doe-agents-012-print-mode-has-one-home
       ;; R11 の針: print mode の argv の綴り(`"-p" "--input-format"`〔段 8 lane 4x から stream-json の入力〕/
       ;; `"-p" "--output-format"` / `"--print"`)は
       ;; impls/headless_argv.hy だけ(tmux の -p は capture-pane / paste-buffer の旗で別物)。host.hy の backend の
       ;; 閉語彙と分岐の述語は 1 点。semgrep の除外は headless の家だけ。
       (setv hits [])
       (for [path (source-files)]
         (when (= path.suffix ".hy")
           (for [line (code-lines path)]
             (when (re.search r"\"-p\"\s+\"--(input|output)-format\"|\"--print\"" line)
               (.append hits (str (.relative-to path SESSIONHOST-DIR)))))))
       (assert (= (sorted (set hits)) ["impls/headless_argv.hy"])
               f"print mode の argv の家は impls/headless_argv.hy ちょうど: {hits}")
       (setv host-lines (code-lines (/ SESSIONHOST-DIR "host.hy")))
       (assert (any (gfor line host-lines (in "#{\"tmux\" \"herdr\" HEADLESS-BACKEND-KIND}" line))))
       (assert (= (len (lfor line host-lines :if (.startswith line "(deff headless-backend? ") line)) 1))
       (assert (>= (len (lfor line host-lines :if (in "(headless-backend? config)" line) line)) 7)
               "launch / capture / send / interrupt / cancel / cleanup / monitor の分岐は述語 1 点を読む")
       (setv semgrep (.read-text (/ (. (Path __file__) parent parent parent) ".semgrep.yaml") :encoding "utf-8"))
       (setv rule (get (.split semgrep "  - id: doeff-agents-no-claude-print-mode") 1))
       (setv rule (get (.split rule "  - id: ") 0))
       (for [home ["impls/headless_argv.hy" "headless_protocol.py" "headless_process.py"
                   "headless.hy" "substrate_headless.hy" "test_sessionhost_headless.py"]]
         (assert (in home rule) f"semgrep の除外は headless の家 {home} を名指す"))
       (assert (not-in "**/doeff_agents/**" rule) "除外を package 全体へ広げない"))
     (deftest test-adr-doe-agents-012-headless-events-are-the-live-stream
       ;; R12 の針: capability は backend から(純関数)。反例(挙動): headless の器では text の
       ;; delta が 1 行ずつ frame になり、完成した本文は entries だけ、capture は撃たない。
       (assert (= (run (stream-capability-of-backend "headless")) "events"))
       (assert (= (run (stream-capability-of-backend "tmux")) "frames"))
       (assert (= (run (stream-capability-of-backend "herdr")) "frames"))
       (setv world (World))
       (setv world.settings (AgentdSettings :node-name "mac-1" :homes-root "/homes" :backend-kind "headless" :stream-capability "events"))
       (setv world.sessions (FakeSessions :backend-kind "headless" :events-root "/events"))
       (.put-row world.acp (message-row "m-h" "hello"))
       (.put-row world.acp (turn-row "h-1" "conv-h" "m-h" 500))
       (.tick world 0)
       (setv h-sid (sid-of world "h-1"))
       (setv node (status-of (get world.acp.rows "default:node:mac-1")))
       (assert (= (get (object-at node "observations") "streamCapability") "events"))
       (setv (get world.local.transcripts f"/events/{h-sid}.events.jsonl")
             (+ "{\"type\": \"stream_event\", \"event\": {\"type\": \"content_block_delta\", \"index\": 0, \"delta\": {\"type\": \"text_delta\", \"text\": \"ab\"}}}\n"
                "{\"type\": \"stream_event\", \"event\": {\"type\": \"content_block_delta\", \"index\": 0, \"delta\": {\"type\": \"text_delta\", \"text\": \"cd\"}}}\n"
                "{\"type\": \"assistant\", \"message\": {\"role\": \"assistant\", \"id\": \"m\", \"content\": [{\"type\": \"text\", \"text\": \"abcd\"}]}}\n"))
       (setv (get world.acp.subscribers h-sid) 1)
       (.tick world 1000)
       (.tick world 500)
       (setv kinds (lfor [_o _n frames] world.acp.pushes frame frames (get frame "kind")))
       (assert (= (lfor k kinds :if (= k "text") k) ["text" "text"]))
       (assert (not-in "frame" kinds))
       (assert (= world.sessions.captures []))
       (.finish-turn world.sessions h-sid (+ world.local.now-ms 100))
       (.tick world 1000)
       (setv record (status-of (get world.acp.rows "default:turn-record:h-1")))
       (assert (= (get record "state") "ended"))
       (setv entries (get record "entries"))
       (assert (isinstance entries list))
       (assert (= (lfor e entries :if (isinstance e dict) (get e "kind")) ["text"]))
       (setv first-entry (get entries 0))
       (assert (isinstance first-entry dict))
       (assert (not-in "text" first-entry) "見出しに本文が在る(段 9f lane 9f-4)")
       (assert (= (get first-entry "bytes") (len (.encode "{\"text\":\"abcd\"}" "utf-8"))))
       (setv digest (get first-entry "sha256"))
       (assert (isinstance digest str))
       (assert (= (len digest) 64)))
     (deftest test-adr-doe-agents-012-conversation-cache-only-on-the-same-node-and-home
       ;; R20 の針(構造): 起こし方・家の比較・畳み・charter の組み立ては judgment.hy の 1 点ずつ。agentd.hy は家を
       ;; 比較せず、記録の材料の読みは history-for の 1 行、上限は AgentdSettings の 1 点、spec は sessionId を名乗る。
       (setv judgment-lines (code-lines (/ ACP-DIR "judgment.hy")))
       (for [name ["next-arm-for-job" "session-in-home" "rehydrate-history-of" "incarnation-charter-of"
                   "fallback-arm-of" "transcript-candidates-of"]]
         (assert (= (len (lfor line judgment-lines :if (.startswith line f"(defk {name} ") line)) 1) name))
       (assert (any (gfor line judgment-lines (in "\"sessionId\" job.session-id" line)))
               "turn-record の spec は sessionId を名乗る(R20)")
       (setv agentd-lines (code-lines (/ ACP-DIR "agentd.hy")))
       ;; 段 9f lane 9f-4: 手番の本文は service の before=latest から(record-turns-for の 1 腕・RecordRead)、届かなければ
       ;; HeadlineTurns で薄く再開すると名乗る。
       (assert (= (len (lfor line agentd-lines :if (.startswith line "(defk record-turns-for ") line)) 1) "record-turns-for が 1 点でない(R20)")
       (assert (= (len (lfor line agentd-lines :if (in "(RecordRead :conversation-id" line) line)) 1) "RecordRead を撃つ点は 1 つ(R20)")
       (assert (any (gfor line agentd-lines (in "rehydrates thinly from ACP headlines" line))) "薄い再開を名乗る log が無い(R20)")
       (assert (= (len (lfor line judgment-lines :if (.startswith line "(defk record-history-satisfied ") line)) 1))
       (for [line agentd-lines]
         (assert (not-in "session-in-home" line) f"agentd.hy は家を比較しない(R20): {line}")
         (assert (not-in "plan.predecessor" line) f"resume の元は choice.source(R20): {line}"))
       (assert (= (len (lfor line agentd-lines :if (in "(AcpConversationMail :conversation-id" line) line)) 1)
               "会話の郵便の読みは history-for の 1 点(R20)")
       ;; 段 9q(#77): 見出し(kind turn-record の全量)を読む点は headline-turns-for の 1 つで、history-for は読まない —
       ;; service が答えた拍に全量を読むと claim の後の準備が node の lease(90 秒)を超える。
       (assert (= (len (lfor line agentd-lines :if (.startswith line "(defk headline-turns-for ") line)) 1) "headline-turns-for が 1 点でない(R20・段 9q)")
       (assert (= (len (lfor line agentd-lines :if (in "(AcpTurnHeadlines :conversation-id" line) line)) 1)
               "見出しの読みは headline-turns-for の 1 点(R20・段 9q)")
       (setv history-body (defk-body agentd-lines "history-for"))
       (assert (not (any (gfor line history-body (in "AcpTurnHeadlines" line)))) "history-for が見出しを読む(段 9q)")
       (assert (not (any (gfor line history-body (in "(AcpGet " line)))) "history-for が全量 list を撃つ(段 9q)")
       (assert (= (len (lfor line (code-lines (/ ACP-DIR "effects.py"))
                             :if (.startswith line "    rehydrate_history_byte_budget: int = ") line))
                  1)
               "履歴からの再開の上限の宣言は AgentdSettings の 1 点(R20)")
       ;; 反例(挙動): 家(account)の違う手番は温かい session を片付けて 履歴から再開する(ACP の記録から)(送らない)。
       (setv world (World))
       (run-warm-turn world "t-1" "conv-a" "first")
       (setv warm (sid-of world "t-1"))
       (assert (= (get (. (get world.acp.rows "default:turn-record:t-1") spec) "sessionId") warm))
       (setv base (bound-row "t-2" "mac-1" "acct" "claude" PHASE-BOUND))
       (setv #^ JSONObject spec (dict base.spec))
       (setv (get spec "subject") "conv-a")
       (setv (get spec "inputs") ["m-t-2"])
       (setv (get spec "affinity") {"predecessor" warm})
       (.put-row world.acp (message-row "m-t-2" "second"))
       (.put-row world.acp (AcpRow :namespace base.namespace :key base.key :kind base.kind
                                   :resource-id base.resource-id :version base.version :generation 1
                                   :created-at-ms 500 :labels {} :payload {} :spec spec :status base.status))
       (.tick world 1000)
       (assert (= world.sessions.cleanups [warm]))
       (assert (= world.sessions.resumes []))
       (setv rehydrated-prompt (get (get world.sessions.launches -1) "prompt"))
       (assert (isinstance rehydrated-prompt str))
       (assert (in "これまでの会話" rehydrated-prompt))
       (assert (not-in #(warm "second" True) world.sessions.sends) "別の家の session に送らない(R20)")
       ;; 同じ家の片付いた session は --resume(cache を保つ)。
       (setv same (World))
       (run-warm-turn same "s-1" "conv-b" "first")
       (setv kept (sid-of same "s-1"))
       (.tick same 601000)
       (assert (= same.sessions.cleanups [kept]))
       (.put-row same.acp (message-row "m-s-2" "second"))
       (.put-row same.acp (turn-row "s-2" "conv-b" "m-s-2" (- same.local.now-ms 300)))
       (.tick same 1000)
       (assert (= (lfor resumed same.sessions.resumes (get resumed "session_id")) [kept]))
       (assert (= same.acp.history-reads []))
       (assert (= same.acp.headline-reads []))
       ;; 器に無い predecessor(別の機体)は履歴からの再開。
       (setv elsewhere (World))
       (setv lone (bound-row "e-1" "mac-1" None "claude" PHASE-BOUND))
       (setv #^ JSONObject lone-spec (dict lone.spec))
       (setv (get lone-spec "affinity") {"predecessor" "sid-on-another-node"})
       (.put-row elsewhere.acp (AcpRow :namespace lone.namespace :key lone.key :kind lone.kind
                                       :resource-id lone.resource-id :version lone.version :generation 1
                                       :created-at-ms 500 :labels {} :payload {} :spec lone-spec :status lone.status))
       (.tick elsewhere 0)
       (assert (= elsewhere.sessions.resumes []))
       (assert (= (len elsewhere.sessions.launches) 1))
       (assert (= elsewhere.acp.history-reads ["e-1"]))
       ;; この World は記録の service を配線していない(弁 off)ので薄い再開 — 見出しはその拍にだけ読む(段 9q)。
       (assert (= elsewhere.acp.headline-reads ["e-1"])))
     (deftest test-adr-doe-agents-012-turn-events-are-durable-mid-turn
       ;; R19 の針: 追記の座は agentd.hy の append-entries の 1 点(AcpPutStatus を turn-record へ撃つ腕は
       ;; append-entries と end-turn-record と、受理の答えを写す mark-recorded〔段 9f lane 9f-4〕だけ)。反例(挙動):
       ;; 出来事は手番の途中の拍に行に在り、at は読んだ拍、終わりの書きは追記の上に ended(置換しない)、見出しは本文を持たない。
       (setv agentd-src (.read-text (/ ACP-DIR "agentd.hy") :encoding "utf-8"))
       (assert (in "(defk append-entries [job entries]" agentd-src) "追記の腕 append-entries が無い(R19)")
       (assert (in "(defk drain-stream [" agentd-src) "手番の終わりの読み drain-stream が無い(R19)")
       (setv effects-src (.read-text (/ ACP-DIR "effects.py") :encoding "utf-8"))
       ;; 段 9f lane 9f-5 便 2b: 行の上限は見出しだけの行の 32,768(ACP の契約の締めより先に書き手が下げる)。
       (assert (in "TURN_RECORD_ENTRIES_BYTE_BUDGET = 32_768" effects-src) "行の上限の宣言が 1 点に無い(R19)")
       ;; 段 9f lane 9f-4: 見出しの型は本文の欄を持たない・導く点と写す点は 1 つずつ・切り詰めの規則は agentd に無い。
       (assert (in "class TurnEntryHeadline:" effects-src) "見出しの型が無い(R19)")
       (for [field ["    text:" "    summary:" "    input:" "    output:" "    model:"]]
         (setv block (get (.split (get (.split effects-src "class TurnEntryHeadline:") 1) "\n\n\n") 0))
         (assert (not-in field block) f"見出しの型に本文の欄 {field} が在る(R19)"))
       (assert (not-in "ENTRY_SUMMARY_MAX_CHARS" effects-src) "agentd の切り詰めの規則が残っている(R19)")
       (assert (not-in "ENTRY_TEXT_MAX_CHARS" effects-src) "agentd の切り詰めの規則が残っている(R19)")
       (setv judgment-lines (code-lines (/ ACP-DIR "judgment.hy")))
       (for [name ["headline-of-body" "entry-json-of" "record-body-bytes-of" "turn-record-recorded-status"]]
         (assert (= (len (lfor line judgment-lines :if (.startswith line f"(defk {name} ") line)) 1) f"{name} は 1 点(R19)"))
       (for [name ["entry-of-body" "text-entry" "tool-use-entry" "tool-result-entry" "note-entry"]]
         (assert (not (any (gfor line judgment-lines (.startswith line f"(defk {name} ")))) f"本文を写す {name} が残っている(R19)"))
       (assert (in "(defk mark-recorded [" agentd-src) "受理の写しの腕 mark-recorded が無い(R19)")
       (setv world (World))
       (setv world.settings (AgentdSettings :node-name "mac-1" :homes-root "/homes" :backend-kind "headless" :stream-capability "events"))
       (setv world.sessions (FakeSessions :backend-kind "headless" :events-root "/events"))
       (.put-row world.acp (message-row "m-e" "hello"))
       (.put-row world.acp (turn-row "e-1" "conv-e" "m-e" 500))
       (.tick world 0)
       (setv e-sid (sid-of world "e-1"))
       (setv (get world.local.transcripts f"/events/{e-sid}.events.jsonl")
             (+ "{\"type\": \"system\", \"subtype\": \"init\", \"model\": \"m\"}\n"
                "{\"type\": \"assistant\", \"message\": {\"role\": \"assistant\", \"id\": \"m1\", \"content\": [{\"type\": \"tool_use\", \"id\": \"t1\", \"name\": \"Read\", \"input\": {\"file_path\": \"/a\"}}]}}\n"))
       (.tick world 1000)
       (setv mid (status-of (get world.acp.rows "default:turn-record:e-1")))
       (assert (= (get mid "state") "running") "手番の途中の行が running でない")
       (setv mid-entries (get mid "entries"))
       (assert (isinstance mid-entries list))
       (assert (= (lfor e mid-entries :if (isinstance e dict) (get e "kind")) ["system" "tool_use"])
               "出来事が手番の途中で行に無い(R19)")
       (setv first-at world.local.now-ms)
       (assert (= (lfor e mid-entries :if (isinstance e dict) (get e "at")) [first-at first-at]) "at は読んだ拍(R19)")
       (setv (get world.local.transcripts f"/events/{e-sid}.events.jsonl")
             (+ (get world.local.transcripts f"/events/{e-sid}.events.jsonl")
                "{\"type\": \"user\", \"message\": {\"role\": \"user\", \"content\": [{\"type\": \"tool_result\", \"tool_use_id\": \"t1\", \"content\": \"ok\"}]}}\n"
                "{\"type\": \"assistant\", \"message\": {\"role\": \"assistant\", \"id\": \"m2\", \"content\": [{\"type\": \"text\", \"text\": \"done\"}]}}\n"))
       (.finish-turn world.sessions e-sid (+ world.local.now-ms 1100))
       (.tick world 1000)
       (setv ended (status-of (get world.acp.rows "default:turn-record:e-1")))
       (assert (= (get ended "state") "ended"))
       (setv ended-entries (get ended "entries"))
       (assert (isinstance ended-entries list))
       (assert (= (lfor e ended-entries :if (isinstance e dict) (get e "kind")) ["system" "tool_use" "tool_result" "text"])
               "終わりの書きは追記の上に ended(置換しない・R19)")
       (for [e ended-entries]
         (when (isinstance e dict)
           (assert (= (set.intersection (set (.keys e)) #{"text" "summary" "input" "output" "model"}) (set))
                   f"見出しに本文の欄が在る(R19・段 9f lane 9f-4): {e}")
           (assert (and (in "bytes" e) (in "sha256" e)) f"見出しに同一性が無い(R19): {e}")))
       (assert (= (cut ended-entries 0 2) mid-entries) "途中の出来事(at・seq)が終わりの書きで変わった(R19)")
       (setv seqs [])
       (for [e ended-entries]
         (when (isinstance e dict)
           (setv seq (get e "seq"))
           (assert (isinstance seq int))
           (.append seqs seq)))
       (assert (= seqs (sorted seqs)) "seq が単調でない(R19)")
       (assert (= (len (set seqs)) (len seqs)) "seq が衝突した(R19)"))
     (deftest test-adr-doe-agents-012-live-events-are-polled-within-50ms-while-watched
       ;; R22 の針(構造): 周期の宣言は AgentdSettings の 1 点(events_poll_seconds ≤ 0.05)・判断は judgment.wait-seconds-for の
       ;; 1 点(events-poll-seconds を読むのは judgment だけ — agentd.hy / handlers.py には無い)。
       (setv effects-lines (code-lines (/ ACP-DIR "effects.py")))
       (setv declared (lfor line effects-lines :if (.startswith line "    events_poll_seconds: float = ") line))
       (assert (= (len declared) 1) "events の周期の宣言は AgentdSettings の 1 点(R22)")
       (assert (<= (float (.strip (get (.split (get declared 0) "=") 1))) 0.05) "events の周期は 50 ms の中(R22)")
       (setv judgment-lines (code-lines (/ ACP-DIR "judgment.hy")))
       (assert (= (len (lfor line judgment-lines :if (.startswith line "(defk wait-seconds-for ") line)) 1))
       (assert (any (gfor line judgment-lines (in "settings.events-poll-seconds" line))) "判断が events の周期を読む(R22)")
       (for [name ["agentd.hy" "handlers.py"]]
         (for [line (code-lines (/ ACP-DIR name))]
           (assert (not-in "events-poll-seconds" line) f"{name} は周期を読まない(R22): {line}")
           (assert (not-in "events_poll_seconds" line) f"{name} は周期を読まない(R22): {line}")))
       ;; 反例(挙動): capturing の job が在る時、events の器は events の周期・frames の器は capture の間隔。無ければ記録の周期。
       (setv probe (InFlightJob :job-key "k" :job-namespace AGENT-JOB-NAMESPACE :job-id "j" :subject "c-1" :session-id "s"
                                :agent-type "claude" :node "n" :profile "p" :model "m" :started-ms 0 :turn-floor-ms 0
                                :start-offset 0 :transcript-offset 0 :delta-seq 0 :lease-id None :lease-kind None
                                :lease-account None :lease-hold-ms None :capturing True :stream-gone False
                                :last-frame-ms 0 :last-probe-ms 0 :pending-conditions #()))
       (setv watched (replace (initial-state) :jobs #(probe))
             unwatched (replace (initial-state) :jobs #((replace probe :capturing False)))
             events (AgentdSettings :node-name "n" :backend-kind "headless" :stream-capability "events")
             frames (AgentdSettings :node-name "n" :backend-kind "tmux" :stream-capability "frames"))
       (assert (= (run (wait-seconds-for watched events)) events.events-poll-seconds) "events の器は events の周期(R22)")
       (assert (= (run (wait-seconds-for watched frames)) frames.frame-interval-seconds) "frames の器は capture の間隔(R22)")
       (assert (= (run (wait-seconds-for unwatched events)) events.transcript-poll-seconds) "購読 0 は記録の周期(R22)")
       (assert (= (run (wait-seconds-for (initial-state) events)) events.idle-wait-seconds) "job なしは idle(R22)")
       ;; 記録の追記の拍は transcript の周期(push の周期に追随しない): まだ 1 度も → 今・周期の中 → 書かない・経った → 書く。
       (assert (run (record-due probe 1000 events)) "まだ 1 度も書いていなければ今(R22)")
       (setv written (replace probe :last-record-ms 1000))
       (assert (not (run (record-due written 1050 events))) "50 ms 後は書かない(R22)")
       (assert (run (record-due written (+ 1000 (int (* 1000 events.transcript-poll-seconds))) events)) "周期が経てば書く(R22)")
       (assert (any (gfor line (code-lines (/ ACP-DIR "agentd.hy")) (in "(record-due " line))) "追記の腕が record-due で拍を選ぶ(R22)"))
     (deftest test-adr-doe-agents-012-withdraw-is-an-interrupt-signal
       ;; R13 の針: 割り込みの判定は judgment.hy の interrupt-arm-for の 1 点、agentd.hy の
       ;; withdraw の腕に SessionCleanup は無い。反例(挙動): 取り下げ → interrupt 1 回・
       ;; cleanup 0・turn-record ended・Interrupted・phase は Withdrawn・session は生きたまま。
       (setv judgment-lines (code-lines (/ ACP-DIR "judgment.hy")))
       (assert (= (len (lfor line judgment-lines :if (.startswith line "(defk interrupt-arm-for ") line)) 1))
       (setv agentd-lines (code-lines (/ ACP-DIR "agentd.hy")))
       (setv block "")
       (for [line agentd-lines]
         (when (.startswith line "(defk ")
           (setv block (get (.split line) 1)))
         (when (in block #{"interrupt-job" "withdraw-jobs"})
           (assert (not-in "SessionCleanup" line) f"取り下げは片付けない(R13): {block}: {line}")
           (assert (not-in "retire-sessions" line) f"取り下げは片付けない(R13): {block}: {line}")))
       (setv world (World))
       (run-warm-turn world "w-1" "conv-w" "first")
       (.put-row world.acp (message-row "m-w2" "second"))
       (.put-row world.acp (turn-row "w-2" "conv-w" "m-w2" (- world.local.now-ms 300)))
       (.tick world 1000)
       (setv running (get world.acp.rows "acp-system:agent-job:w-2"))
       (setv withdrawn (dict (status-of running)))
       (setv (get withdrawn "phase") "Withdrawn")
       (.put-row world.acp (AcpRow :namespace running.namespace :key running.key :kind running.kind
                                   :resource-id running.resource-id :version running.version
                                   :generation running.generation :created-at-ms running.created-at-ms
                                   :labels running.labels :payload running.payload :spec running.spec
                                   :status withdrawn))
       (.tick world 1000)
       (setv w-sid (sid-of world "w-1"))
       (assert (= world.sessions.interrupts [w-sid]))
       (assert (= world.sessions.cleanups []))
       (assert (= (get (status-of (get world.acp.rows "default:turn-record:w-2")) "state") "ended"))
       (setv after (status-of (get world.acp.rows "acp-system:agent-job:w-2")))
       (assert (= (get after "phase") "Withdrawn"))
       (assert (= (last-condition-type after) "Interrupted"))
       (assert (= (. (get world.sessions.views w-sid) status) "running"))
       (.tick world 1000)
       (assert (= world.sessions.interrupts [w-sid])))
     (deftest test-adr-doe-agents-012-watch-wake-reads-the-window-and-birth-landing
       ;; R14 の針: 様式の判定は list-mode-for の 1 点・計器の始点は birth-ms-of の 1 点。
       ;; 反例(挙動): watch で起きた拍は agent-job も message も全量 list せず、計器は生まれの着地。
       (setv judgment-lines (code-lines (/ ACP-DIR "judgment.hy")))
       (assert (= (len (lfor line judgment-lines :if (.startswith line "(defk list-mode-for ") line)) 1))
       (assert (= (len (lfor line judgment-lines :if (.startswith line "(defk birth-ms-of ") line)) 1))
       (for [line (code-lines (/ ACP-DIR "agentd.hy"))]
         (assert (not-in "(AcpGet :kind MESSAGE-KIND)" line) "郵便は鍵で 1 行ずつ読む(R14)"))
       (setv world (World))
       (.tick world 0)
       (assert (= (.count world.acp.lists "agent-job") 1))
       (.put-row world.acp (message-row "m-b" "born"))
       (setv base (bound-row "b-1" "mac-1" None "claude" PHASE-BOUND))
       (.put-row world.acp (AcpRow :namespace base.namespace :key base.key :kind base.kind
                                   :resource-id base.resource-id :version base.version :generation 1
                                   :created-at-ms 1000 :labels {} :payload {} :spec base.spec
                                   :status {"phase" "Pending"} :landed-at-ms 1437))
       (.put-row world.acp (AcpRow :namespace base.namespace :key base.key :kind base.kind
                                   :resource-id base.resource-id :version base.version :generation 2
                                   :created-at-ms 1000 :labels {} :payload {} :spec base.spec
                                   :status base.status :landed-at-ms 1900))
       (.tick world 1500)
       (assert (= (.count world.acp.lists "agent-job") 1) "watch の拍は全量 list しない")
       (assert (not-in "message" world.acp.lists))
       (setv metric (get (lfor m world.local.metrics :if (= (get m "metric") "agent-job-to-send") m) -1))
       (assert (= (get metric "createdAtMs") 1437))
       (assert (= (get metric "ms") (- 2500 1437))))
     (deftest test-adr-doe-agents-012-headless-first-turn-carries-the-mail
       ;; R16 の針: 畳むかの判定は judgment.hy の first-turn-carries-inputs の 1 点・本文の畳みは
       ;; first-turn-prompt-of の 1 点。agentd.hy は backend の語(BACKEND-HEADLESS / "headless")を
       ;; 比較しない。反例(挙動): headless の器では launch の prompt に郵便が畳まれ send は 0、
       ;; tui の器では launch の prompt は charter のままで send に郵便が乗る。
       (setv judgment-lines (code-lines (/ ACP-DIR "judgment.hy")))
       (assert (= (len (lfor line judgment-lines :if (.startswith line "(defk first-turn-carries-inputs ") line)) 1))
       (assert (= (len (lfor line judgment-lines :if (.startswith line "(defk first-turn-prompt-of ") line)) 1))
       (for [line (code-lines (/ ACP-DIR "agentd.hy"))]
         (assert (not-in "BACKEND-HEADLESS" line) f"agentd.hy は backend の語を比較しない(R16): {line}")
         (assert (not-in "\"headless\"" line) f"agentd.hy は backend の語を比較しない(R16): {line}"))
       (setv headless (World))
       (setv headless.settings (AgentdSettings :node-name "mac-1" :homes-root "/homes" :backend-kind "headless" :stream-capability "events"))
       (setv headless.sessions (FakeSessions :backend-kind "headless" :events-root "/events"))
       (.put-row headless.acp (message-row "m-f" "hello"))
       (.put-row headless.acp (turn-row "f-1" "conv-f" "m-f" 500))
       (.tick headless 0)
       (assert (= (get (get headless.sessions.launches -1) "prompt") "go\n\nhello"))
       (assert (= headless.sessions.sends []))
       (assert (is-not (.get headless.acp.rows "default:turn-record:f-1") None))
       (assert (= (len headless.state.jobs) 1))
       (setv tui (World))
       (.put-row tui.acp (message-row "m-t" "hello"))
       (.put-row tui.acp (turn-row "t-1" "conv-t" "m-t" 500))
       (.tick tui 0)
       (assert (= (get (get tui.sessions.launches -1) "prompt") "go"))
       (assert (= tui.sessions.sends [#((sid-of tui "t-1") "hello" True)])))
     (deftest test-adr-doe-agents-012-join-is-one-command-and-one-decision-point
       ;; R17 の針: 宣言 → env の束の写像点は join.hy の join-plan-of ちょうど。entry.py / runtime.py
       ;; は env の名の綴り(ACP_DAEMON_URL 等)を自分で組まない(綴りは effects.py の *_ENV)。
       ;; agentd.hy は ownership の語を比較しない。observations の ownership を書く点は judgment の 1 つ。
       (setv join-lines (code-lines (/ ACP-DIR "join.hy")))
       (assert (= (len (lfor line join-lines :if (.startswith line "(defk join-spec-of ") line)) 1))
       (assert (= (len (lfor line join-lines :if (.startswith line "(defk join-plan-of ") line)) 1))
       (assert (= (len (lfor line join-lines :if (.startswith line "(defk ownership-preflight ") line)) 1))
       (for [line (+ (code-lines (/ ACP-DIR "entry.py")) (code-lines (/ ACP-DIR "runtime.py")))]
         (for [word ["\"ACP_DAEMON_URL\"" "\"ACP_AGENTD_TOKEN_FILE\"" "\"DOEFF_AGENTD_ACP\""
                     "\"DOEFF_SESSIONHOST_BACKEND\"" "\"DOEFF_AGENTD_OWNERSHIP\""]]
           (assert (not-in word line) f"entry / runtime は env の名を自分で綴らない(R17): {line}")))
       (for [line (code-lines (/ ACP-DIR "agentd.hy"))]
         (assert (not-in "ownership" line) f"agentd.hy は ownership の語を持たない(R17): {line}"))
       (assert (= (len (lfor line (code-lines (/ ACP-DIR "judgment.hy")) :if (in "\"ownership\"" line) line)) 1)
               "observations.ownership を書く点は node-status-with-lease の 1 つ(R17)")
       ;; 反例(挙動): 宣言 → plan → 今日の読み手が同じ束を読む(第 2 の綴りが無い)。
       (setv spec (run (join-spec-of
                         (JoinArgv :items #("--server" "http://acp:8868" "--token-file" "/t/agentd.token"
                                            "--ownership" "company" "--ownership-proof" "gce-project:p-1"))
                         (JoinDeclaration :tables {"schema" "doeff.agentd-join.v1"
                                                   "agentd" {"node_name" "gcp-0"}
                                                   "record" {"url" "http://record:8874"}})
                         "/state")))
       (assert (isinstance spec JoinSpec))
       (assert (= spec.node-name "gcp-0"))
       (setv plan (run (join-plan-of spec)))
       (assert (isinstance plan JoinPlan))
       (setv env (dict plan.env))
       (setv settings (settings-from-env env plan.host-argv))
       (assert (= settings.node-name "gcp-0"))
       (assert (= settings.backend-kind "headless"))
       (assert (= settings.ownership (Ownership :grade "company" :proof "gce-project:p-1")))
       (assert (is settings.record-enabled True))
       (assert (is (. (acp-valve (list plan.host-argv) env) enabled) True))
       ;; 本文の行き先(段 9f lane 9f-6): 宣言に [record] が無い束は同じ読み(settings-from-env)が参加を断る —
       ;; 判断は join.record-sink-of の 1 点(宣言の検・届くかは検めない)。焦点の検は tests/sessionhost_acp_record_deftests.hy。
       (assert (= (len (lfor line join-lines :if (.startswith line "(defk record-sink-of ") line)) 1))
       (setv unsinked (dict (. (run (join-plan-of (run (join-spec-of
                                                          (JoinArgv :items #("--server" "http://acp:8868" "--token-file" "/t"))
                                                          (JoinDeclaration :tables {}) "/state"))))
                               env)))
       (setv refused-for "")
       (try
         (settings-from-env (| unsinked {"DOEFF_AGENTD_NODE_NAME" "gcp-0"}) plan.host-argv)
         (except [error AgentdPreflightError]
           (setv refused-for (str error))))
       (assert (in "[record].url" refused-for) "宛先の無い agentd は参加を断る(段 9f lane 9f-6)")
       ;; 等級だけ(proof なし)は断る。
       (setv refused False)
       (try
         (run (join-spec-of (JoinArgv :items #("--server" "http://a" "--token-file" "/t" "--ownership" "company"))
                            (JoinDeclaration :tables {}) "/state"))
         (except [ValueError]
           (setv refused True)))
       (assert refused "ownership は proof と対でだけ宣言できる(R17)")
       ;; 検: metadata の project-id が一致する時だけ通し、違えば ValueError(runtime が参加を断る)。
       (setv local (FakeLocal :now-ms 0))
       (setv (get local.probe-answers "gce-project:p-1") "p-1")
       (assert (= (run (install (ownership-preflight (Ownership :grade "company" :proof "gce-project:p-1"))
                                [local.dispatch]))
                  (Ownership :grade "company" :proof "gce-project:p-1")))
       (setv (get local.probe-answers "gce-project:p-1") "p-2")
       (setv mismatched False)
       (try
         (run (install (ownership-preflight (Ownership :grade "company" :proof "gce-project:p-1"))
                       [local.dispatch]))
         (except [ValueError]
           (setv mismatched True)))
       (assert mismatched "project-id の不一致は参加しない(R17)")
       ;; 観測: 宣言が在る時だけ observations.ownership が載る。
       (setv owned (World))
       (setv owned.settings (AgentdSettings :node-name "mac-1" :homes-root "/homes"
                                            :ownership (Ownership :grade "company" :proof "declared")))
       (.tick owned 0)
       (setv observations (object-at (status-of (get owned.acp.rows "default:node:mac-1")) "observations"))
       (assert (= (get observations "ownership") {"grade" "company" "proof" "declared"}))
       (setv bare (World))
       (.tick bare 0)
       (assert (not-in "ownership" (object-at (status-of (get bare.acp.rows "default:node:mac-1")) "observations"))))
     (deftest test-adr-doe-agents-012-profile-observed-is-written-from-one-usage-point
       ;; R18 の針(構造): 読み口は handlers.py の USAGE_COMMAND の 1 点(agentcli の console script)で、
       ;; sessionhost は agentcli を import しない。観測を決める点は judgment.profile-observed-of の 1 つ、
       ;; post-image を組む点は profile-status-with-observed の 1 つ。agentd.hy は窓の名・単位・境界の語を比較しない。
       (setv handler-lines (code-lines (/ ACP-DIR "handlers.py")))
       (assert (= (len (lfor line handler-lines :if (.startswith line "USAGE_COMMAND") line)) 1)
               "usage の読み口の綴りは handlers.USAGE_COMMAND の 1 点(R18)")
       (assert (= (len (lfor line handler-lines :if (.startswith line "def read_profile_usage(") line)) 1))
       ;; 段 8e lane 4j: 器の profile の集合は登録簿の 1 点(PROFILES_COMMAND)× 家の実在で読み、
       ;; 判断(観測する行)は judgment.profile-rows-held の 1 つ。
       (assert (= (len (lfor line handler-lines :if (.startswith line "PROFILES_COMMAND") line)) 1)
               "登録簿の読み口の綴りは handlers.PROFILES_COMMAND の 1 点(R18・段 8e lane 4j)")
       (assert (= (len (lfor line handler-lines :if (.startswith line "def list_profile_homes(") line)) 1))
       (for [path (source-files)]
         (for [line (code-lines path)]
           (assert (not (or (.startswith (.lstrip line) "import agentcli")
                            (.startswith (.lstrip line) "from agentcli")
                            (in "(import agentcli" line)))
                   f"sessionhost は agentcli を import しない(R18): {(.relative-to path SESSIONHOST-DIR)}: {line}")))
       (setv judgment-lines (code-lines (/ ACP-DIR "judgment.hy")))
       (assert (= (len (lfor line judgment-lines :if (.startswith line "(defk profile-observed-of ") line)) 1))
       (assert (= (len (lfor line judgment-lines :if (.startswith line "(defk profile-rows-held ") line)) 1))
       (assert (= (len (lfor line judgment-lines :if (.startswith line "(defk profile-status-with-observed ") line)) 1))
       (for [line (code-lines (/ ACP-DIR "agentd.hy"))]
         (for [word ["\"5h\"" "\"7d\"" "\"percent\"" "company" "five_hour"]]
           (assert (not-in word line) f"agentd.hy は窓・単位・境界の語を比較しない(R18): {line}")))
       ;; 反例(挙動): 断られた profile は書かず log に 1 行、読めた profile は committed の conditions を
       ;; 残した post-image で observed が立つ、この機体に無い profile は黙る。
       (defn #^ AcpRow profile-row [#^ str name #^ str unit]
         (AcpRow :namespace AGORA-KINDS-NAMESPACE :key f"{AGORA-KINDS-NAMESPACE}:{PROFILE-KIND}:{name}"
                 :kind PROFILE-KIND :resource-id name :version "v1" :generation 2 :created-at-ms 0
                 :labels {} :payload {}
                 :spec {"name" name "boundary" "company" "budget" {"amount" 100 "unit" unit}
                        "reset" {"everySeconds" 18000} "seats" 2}
                 :status {"state" "active"
                          "conditions" [{"type" "ProfileExhausted" "status" "Unknown" "reason" "unobserved"}]}))
       (setv world (World))
       (.put-row world.acp (profile-row "ca" "percent"))
       (.put-row world.acp (profile-row "p1" "percent"))
       (.put-row world.acp (profile-row "t1" "tokens"))
       (.put-row world.acp (profile-row "gone" "percent"))
       (setv (get world.local.usage PROFILE-USAGE-KIND)
             #((ProfileUsageUnavailable :profile "ca" :reason "company-boundary: host unverified")
               (ProfileUsage :profile "p1" :captured-at-ms 5000
                             :windows #((UsageWindow :name "5h" :used-percent 25.0 :resets-at-ms 9000)))
               (ProfileUsage :profile "t1" :captured-at-ms 5000
                             :windows #((UsageWindow :name "5h" :used-percent 25.0 :resets-at-ms 9000)))))
       (.tick world 0)
       (assert (= world.local.usage-reads [#(PROFILE-USAGE-KIND world.settings.profile-observe-seconds)])
               "残量は周期に 1 度だけ読む(R18)")
       (setv written (status-of (get world.acp.rows f"{AGORA-KINDS-NAMESPACE}:{PROFILE-KIND}:p1")))
       (assert (= (get written "observed") {"window" "5h" "remaining" 75.0 "resetAt" 9000 "observedAt" 5000 "node" "mac-1"}))
       (assert (= (get written "state") "active"))
       (assert (= (get written "conditions") [{"type" "ProfileExhausted" "status" "Unknown" "reason" "unobserved"}])
               "post-image は committed の conditions を残す(R18)")
       (for [name ["ca" "t1" "gone"]]
         (assert (not-in "observed" (status-of (get world.acp.rows f"{AGORA-KINDS-NAMESPACE}:{PROFILE-KIND}:{name}")))
                 f"{name} は書かない(R18)"))
       (setv profile-logs (lfor line world.local.logs :if (in "agentd: profile " line) line))
       (assert (any (gfor line profile-logs (and (in "profile ca" line) (in "company-boundary" line)))))
       (assert (any (gfor line profile-logs (and (in "profile t1" line) (in "tokens" line)))))
       (assert (not (any (gfor line profile-logs (in "profile gone" line)))) "持たない profile は log しない(R18)")
       ;; 同じ断面の次の周期は書かない。
       (.tick world (* 1000 world.settings.profile-observe-seconds))
       (assert (= (len (lfor [key status] world.acp.writes :if (in ":profile:" key) key)) 1)
               "committed と同じ observed は書かない(R18)")
       ;; 反例(挙動・段 8e lane 4j): 家の在る profile が無い器は usage を撃たず、1 度だけ名乗る。
       (setv bare (World))
       (.put-row bare.acp (profile-row "p1" "percent"))
       (setv (get bare.local.usage PROFILE-USAGE-KIND)
             #((ProfileUsage :profile "p1" :captured-at-ms 5000
                             :windows #((UsageWindow :name "5h" :used-percent 25.0 :resets-at-ms 9000)))))
       (setv (get bare.local.homes PROFILE-USAGE-KIND) #((ProfileHome :name "p1" :home "/homes/p1" :present False)))
       (.tick bare 0)
       (.tick bare (* 1000 bare.settings.profile-observe-seconds))
       (assert (= bare.local.usage-reads []) "家の無い器は usage を撃たない(R18・段 8e lane 4j)")
       (assert (= (len bare.local.home-reads) 2) "家の在否は周期ごとに読み直す")
       (assert (= (len (lfor line bare.local.logs :if (in "no profile has a home" line) line)) 1)
               "『観測する profile なし』は 1 度だけ(R18・段 8e lane 4j)")
       (assert (not-in "observed" (status-of (get bare.acp.rows f"{AGORA-KINDS-NAMESPACE}:{PROFILE-KIND}:p1")))))
     (deftest test-adr-doe-agents-012-turn-credential-source-is-one-judgment
       ;; R23 の針(構造): 資格の出所は judgment.hy の credential-source-of の 1 点で、agentd.hy は claim の頭でその答えだけを読む。
       ;; 宣言の有無は runtime.py の settings_from_env が CUSTODY_URL_ENV から導く 1 点。session を使い回す鍵は
       ;; session-affinity-key-of で、旧名 home-key-of は acp の source に残らない。反例(挙動)は test_sessionhost_acp.py の 4 本。
       (setv judgment-lines (code-lines (/ ACP-DIR "judgment.hy")))
       (assert (= (len (lfor line judgment-lines :if (.startswith line "(defk credential-source-of ") line)) 1))
       (assert (= (len (lfor line judgment-lines :if (.startswith line "(defk session-affinity-key-of ") line)) 1))
       (for [path (sorted (.glob ACP-DIR "*"))]
         (when (in path.suffix #{".hy" ".py" ".pyi"})
           (for [line (code-lines path)]
             (assert (not-in "home-key-of" line) f"旧名 home-key-of が残る(R23): {path.name}: {line}")
             (assert (not-in "home_key_of" line) f"旧名 home_key_of が残る(R23): {path.name}: {line}"))))
       (setv agentd-lines (code-lines (/ ACP-DIR "agentd.hy")))
       (assert (any (gfor line agentd-lines (in "(credential-source-of plan settings.custody-declared)" line)))
               "agentd.claim-job が資格の出所を 1 点から読まない(R23)")
       ;; agentd.hy は宣言の有無(custody-declared)を資格の出所の呼び出しの 1 行でだけ読み、出所の語は missing だけを比べる。
       (assert (= (len (lfor line agentd-lines :if (in "custody-declared" line) line)) 1) "agentd.hy が宣言の有無を 2 か所で読む(R23)")
       (for [line agentd-lines]
         (assert (not-in "CREDENTIAL-SOURCE-HOME" line) f"agentd.hy が出所 home を自分で比べる(R23): {line}")
         (assert (not-in "CREDENTIAL-SOURCE-LEASE" line) f"agentd.hy が出所 lease を自分で比べる(R23): {line}"))
       (setv runtime-lines (code-lines (/ ACP-DIR "runtime.py")))
       (assert (= (len (lfor line runtime-lines :if (in "custody_declared=" line) line)) 1) "宣言の有無の読みが 1 点でない(R23)")
       (setv tests (.read-text (/ (. (Path __file__) parent parent parent) "packages" "doeff-agents" "tests" "test_sessionhost_acp.py") :encoding "utf-8"))
       (for [name ["test_custody_declared_node_does_not_launch_a_job_without_an_account"
                   "test_custody_declared_node_borrows_the_account_and_launches_in_the_borrowed_home"
                   "test_undeclared_node_keeps_the_charter_home_for_a_job_without_an_account"
                   "test_credential_source_is_one_judgment"]]
         (assert (in (+ "def " name "(") tests) f"R23 の反例の検が無い: {name}")))
     (deftest test-adr-doe-agents-012-capability-table-and-agent-setting-ignored-are-one-judgment
       ;; R24 の針(構造): 能力の表は effects.AGENT-CAPABILITIES の 1 点で、judgment.capabilities-of だけがそれを node の status に
       ;; 写し、node-status-with-lease がその 1 点を呼ぶ。効かない欄の条件は judgment.ignored-settings-of の 1 点で、agentd.hy は
       ;; after-start でその答えだけを pending に積む。effort の腕は next-arm-for-job の中(agentd.hy は effort-of-plan の答えを渡すだけ)。
       ;; 反例(挙動)は test_sessionhost_acp.py の 4 本。
       (setv judgment-lines (code-lines (/ ACP-DIR "judgment.hy")))
       (assert (= (len (lfor line judgment-lines :if (.startswith line "(defk capabilities-of ") line)) 1))
       (assert (= (len (lfor line judgment-lines :if (.startswith line "(defk ignored-settings-of ") line)) 1))
       (assert (= (len (lfor line judgment-lines :if (in "(.items AGENT-CAPABILITIES)" line) line)) 1) "能力の表を写す点が 1 つでない(R24)")
       (assert (any (gfor line judgment-lines (in "(setv (get next NODE-CAPABILITIES-KEY) table)" line))) "node-status-with-lease が能力の表を書かない(R24)")
       (assert (any (gfor line judgment-lines (in "(and idle same) (ArmChoice :arm NEXT-ARM-RESUME :source candidate :retire candidate)" line)))
               "effort だけ違う温かい session を片付けて resume する腕が無い(R24)")
       (setv agentd-lines (code-lines (/ ACP-DIR "agentd.hy")))
       (assert (= (len (lfor line agentd-lines :if (in "(ignored-settings-of plan view arm)" line) line)) 1) "agentd.hy が効かない欄を 1 点から読まない(R24)")
       (assert (= (len (lfor line agentd-lines :if (in "(next-arm-for-job candidate view home effort compact)" line) line)) 1) "agentd.hy が effort を腕へ渡さない(R24)")
       (for [line agentd-lines]
         (assert (not-in "AGENT-CAPABILITIES" line) f"agentd.hy が能力の表を自分で読む(R24): {line}")
         (assert (not-in "CONDITION-AGENT-SETTING-IGNORED" line) f"agentd.hy が条件を自分で組む(R24): {line}"))
       (setv effects-lines (code-lines (/ ACP-DIR "effects.py")))
       (assert (any (gfor line effects-lines (.startswith line "AGENT_CAPABILITIES: dict[str, dict[str, tuple[AgentSetting, ...]]] = {"))) "能力の表の定義点が effects.py に無い(R24)")
       (setv tests (.read-text (/ (. (Path __file__) parent parent parent) "packages" "doeff-agents" "tests" "test_sessionhost_acp.py") :encoding "utf-8"))
       (for [name ["test_node_status_names_the_capability_table"
                   "test_ignored_settings_of_is_the_one_decision"
                   "test_second_turn_with_another_effort_resumes_the_same_session_with_the_new_flag"
                   "test_warm_send_with_another_work_dir_records_agent_setting_ignored"]]
         (assert (in (+ "def " name "(") tests) f"R24 の反例の検が無い: {name}")))
     (deftest test-adr-doe-agents-012-backend-liveness-is-observed
       ;; R25 の針(構造): 復帰の判断は headless_protocol.recovery_verdict の 1 点で host.hy の main が accept より前・latch の
       ;; clear より前に recover-headless-rows を呼ぶ。latch の clear は headless を対象にしない。wire の backend_alive は
       ;; augment-wire-snapshot の 1 点。agentd の判断は judgment.backend-alive の 1 点で、job-step-of と next-arm-for-job が
       ;; それを読み、agentd.hy は backend-alive の欄を直に読まない。resume の腕は events_root を運ぶ。
       ;; 反例(挙動)は test_sessionhost_acp.py の 3 本と test_sessionhost_headless.py の 4 本。
       (setv protocol-lines (code-lines (/ SESSIONHOST-DIR "headless_protocol.py")))
       (assert (= (len (lfor line protocol-lines :if (.startswith line "def recovery_verdict(") line)) 1) "復帰の判断は recovery_verdict の 1 点(R25)")
       (assert (= (len (lfor line protocol-lines :if (.startswith line "def backend_alive(") line)) 1))
       (setv headless-lines (code-lines (/ SESSIONHOST-DIR "headless.hy")))
       (assert (= (len (lfor line headless-lines :if (.startswith line "(defk recover-headless-rows ") line)) 1))
       (assert (= (len (lfor line headless-lines :if (in "(recovery-verdict (is-terminal-status row.status) row.awaiting-response liveness)" line) line)) 1)
               "復帰の 1 行は行の事実と観測を判断の 1 点へ渡す(R25)")
       (assert (any (gfor line headless-lines (in "(make-cause \"vanished\" verdict.detail observed-at)" line))) "証拠つき死亡の語彙は vanished(R25 / ADR-009)")
       (setv host-lines (code-lines (/ SESSIONHOST-DIR "host.hy")))
       (setv recover-at (next (gfor [i line] (enumerate host-lines) :if (in "(recover-headless-rows)" line) i) None))
       (setv clear-at (next (gfor [i line] (enumerate host-lines) :if (in "(db-clear-awaiting-latches conn)" line) i) None))
       (setv serve-at (next (gfor [i line] (enumerate host-lines) :if (in "(serve config actor listener shutdown-event)" line) i) None))
       (assert (and (is-not recover-at None) (is-not clear-at None) (is-not serve-at None)))
       (assert (< recover-at clear-at serve-at) "復帰は latch の clear より前・accept より前(R25)")
       (assert (= (len (lfor line host-lines :if (in "(setv (get wire \"backend_alive\")" line) line)) 1) "wire の backend_alive は augment-wire-snapshot の 1 点(R25)")
       (setv store-lines (code-lines (/ SESSIONHOST-DIR "store.hy")))
       (assert (any (gfor line store-lines (in "\"AND backend_kind != 'headless' \"" line))) "latch の clear は headless を対象にしない(R25)")
       (assert (not (any (gfor line store-lines (in "(TerminalCause :category (get payload \"category\")" line)))) "cause の decode は get で読まない(R25)")
       (setv launch-lines (code-lines (/ SESSIONHOST-DIR "launch.hy")))
       (assert (any (gfor line launch-lines (in "\"events_root\" (.get params \"events_root\")" line))) "resume の腕は events_root を運ぶ(R25)")
       (setv judgment-lines (code-lines (/ ACP-DIR "judgment.hy")))
       (assert (= (len (lfor line judgment-lines :if (.startswith line "(defk backend-alive ") line)) 1) "agentd の生死の判断は backend-alive の 1 点(R25)")
       (assert (= (len (lfor line judgment-lines :if (in "view.backend-alive" line) line)) 1) "backend_alive の欄を読む点は backend-alive ちょうど(R25)")
       (assert (= (len (lfor line judgment-lines :if (in "JOB-STEP-SESSION-LOST" line) line)) 2) "session-lost を返す点は job-step-of ちょうど(import の項 + 1)(R25)")
       (assert (any (gfor line judgment-lines (in "(and alive (not idle) live-backend) (ArmChoice :arm NEXT-ARM-DEFER :source candidate :retire None)" line))) "defer は backend が生きている時だけ(R25)")
       (setv agentd-lines (code-lines (/ ACP-DIR "agentd.hy")))
       (for [line agentd-lines]
         (assert (not-in "backend-alive" line) f"agentd.hy は backend_alive を直に読まない(R25): {line}"))
       (assert (= (len (lfor line agentd-lines :if (in "(session-lost-condition-of view now-ms)" line) line)) 1) "SessionLost の条件は judgment の 1 点から(R25)")
       ;; 反例(挙動): 再起動後、行は running のままでも backend が死んでいれば SessionLost で閉じ、次の Bound は defer しない。
       (setv world (World))
       (.put-row world.acp (message-row "m-l" "first"))
       (.put-row world.acp (turn-row "t-lost" "conv-l" "m-l" (- world.local.now-ms 300)))
       (.tick world 1000)
       (setv sid (sid-of world "t-lost"))
       (setv world.state (initial-state))
       (.kill-backend world.sessions sid)
       (.tick world 1000)
       (setv lost (status-of (get world.acp.rows "acp-system:agent-job:t-lost")))
       (assert (= (get lost "phase") PHASE-ENDED))
       (assert (= (last-condition-type lost) "SessionLost"))
       (assert (= (get (status-of (get world.acp.rows "default:turn-record:t-lost")) "state") "ended"))
       (assert (= world.sessions.cleanups []) "session は host に任せる(R25)")
       (.put-row world.acp (message-row "m-l2" "second"))
       (.put-row world.acp (turn-row "t-next" "conv-l" "m-l2" world.local.now-ms))
       (.tick world 1000)
       (assert (= world.sessions.cleanups [sid]) "手番の途中で死んだ候補は片付けて resume(R25)")
       (assert (= (len world.sessions.resumes) 1))
       (assert (= (get (status-of (get world.acp.rows "acp-system:agent-job:t-next")) "phase") PHASE-RUNNING))
       (assert (any (gfor line world.local.logs (in "mid-turn with a dead backend process" line))) "片付けの理由は観測の語で(R25)")
       ;; 純関数: 観測の無い眺めは生きていると読む・死は明示の False だけ(片付けた後の行は終端なので running に戻して読む)。
       (setv view (replace (get world.sessions.views sid) :status "running" :turn-ended-at-ms None))
       (assert (= (run (job-step-of (replace view :backend-alive None) 0 True)) "observe"))
       (assert (= (run (job-step-of (replace view :backend-alive False) 0 True)) "session-lost"))
       (setv tests (.read-text (/ (. (Path __file__) parent parent parent) "packages" "doeff-agents" "tests" "test_sessionhost_acp.py") :encoding "utf-8"))
       (for [name ["test_dead_backend_of_a_running_job_ends_it_with_session_lost_and_the_next_turn_resumes"
                   "test_live_backend_of_a_recovered_job_is_observed_not_lost"
                   "test_backend_liveness_is_read_from_the_observation_not_the_status_word"]]
         (assert (in (+ "def " name "(") tests) f"R25 の反例の検が無い: {name}"))
       (setv host-tests (.read-text (/ (. (Path __file__) parent parent parent) "packages" "doeff-agents" "tests" "test_sessionhost_headless.py") :encoding "utf-8"))
       (for [name ["test_recovery_verdict_is_the_one_decision"
                   "test_terminal_cause_from_dict_is_total_over_the_store"
                   "test_host_headless_startup_recovery_ends_the_dead_mid_turn_row_and_keeps_the_idle_one"
                   "test_host_headless_resume_reads_a_row_whose_persisted_cause_lacks_the_contract_fields"]]
         (assert (in (+ "def " name "(") host-tests) f"R25 の host の反例の検が無い: {name}")))
     (deftest test-adr-doe-agents-012-stop-closes-running-turns
       ;; R26 の針(構造): 判断は headless_protocol.stop_verdict の 1 点・host の TERM の 1 度目は graceful-stop の thread で
       ;; hook → stop-headless-rows → 実の信号の撃ち直し・entry.py が agentd の close_for_stop を hook に登録・agentd の停止の腕は
       ;; close-jobs-for-stop の 1 点で条件は restart-condition-of から・process は kill_all の並列の猶予・ACP の宛先に既定値は無い。
       ;; 反例(挙動)は test_sessionhost_headless.py の 3 本(判断・program・実 binary の TERM)と test_sessionhost_acp.py の 2 本。
       (setv protocol-lines (code-lines (/ SESSIONHOST-DIR "headless_protocol.py")))
       (assert (= (len (lfor line protocol-lines :if (.startswith line "def stop_verdict(") line)) 1) "停止の判断は stop_verdict の 1 点(R26)")
       (setv headless-lines (code-lines (/ SESSIONHOST-DIR "headless.hy")))
       (assert (= (len (lfor line headless-lines :if (.startswith line "(defk stop-headless-rows ") line)) 1))
       (assert (= (len (lfor line headless-lines :if (in "(stop-verdict (is-terminal-status row.status) row.awaiting-response)" line) line)) 1))
       (assert (any (gfor line headless-lines (in "(headless-kill-all)" line))) "process は kill_all で並列に降ろす(R26)")
       (setv process-lines (code-lines (/ SESSIONHOST-DIR "headless_process.py")))
       (assert (= (len (lfor line process-lines :if (.startswith line "    def kill_all(self) -> int:") line)) 1))
       (assert (= (len (lfor line process-lines :if (.startswith line "def _wait_all(") line)) 1) "猶予は並列(R26)")
       (setv host-lines (code-lines (/ SESSIONHOST-DIR "host.hy")))
       (assert (= (len (lfor line host-lines :if (.startswith line "(defn graceful-stop ") line)) 1))
       (assert (= (len (lfor line host-lines :if (.startswith line "(defn register-shutdown-hook ") line)) 1))
       (assert (any (gfor line host-lines (in "(os.kill (os.getpid) signum)" line))) "撃ち直しは実の信号(R26)")
       (for [line host-lines]
         (assert (not-in "interrupt-main" line) f"_thread.interrupt_main は accept を起こさない(R26): {line}"))
       (setv install-at (next (gfor [i line] (enumerate host-lines) :if (in "(install-graceful-stop config actor)" line) i) None))
       (setv serve-at (next (gfor [i line] (enumerate host-lines) :if (in "(serve config actor listener shutdown-event)" line) i) None))
       (assert (and (is-not install-at None) (is-not serve-at None) (< install-at serve-at)) "graceful の handler は serve の前に据える(R26)")
       (setv entry-lines (code-lines (/ ACP-DIR "entry.py")))
       (assert (any (gfor line entry-lines (in "register_shutdown_hook(lambda: run.close_for_stop(" line))) "entry.py が agentd の停止の腕を hook に登録する(R26)")
       (setv runtime-lines (code-lines (/ ACP-DIR "runtime.py")))
       (assert (= (len (lfor line runtime-lines :if (.startswith line "    def close_for_stop(self, reason: str) -> int:") line)) 1))
       (for [line runtime-lines]
         (assert (not-in "ACP_URL_DEFAULT" line) f"ACP の宛先に既定値は無い(R26): {line}"))
       (setv handler-lines (code-lines (/ ACP-DIR "handlers.py")))
       (for [line handler-lines]
         (assert (not-in "127.0.0.1:8868" line) f"実況の push の宛先の literal(R26): {line}"))
       (setv agentd-lines (code-lines (/ ACP-DIR "agentd.hy")))
       (assert (= (len (lfor line agentd-lines :if (.startswith line "(defk close-jobs-for-stop ") line)) 1))
       (assert (= (len (lfor line agentd-lines :if (in "(restart-condition-of job settings.node-name reason now-ms)" line) line)) 1) "AgentdRestart の条件は judgment の 1 点から(R26)")
       (assert (= (len (lfor line agentd-lines :if (.startswith line "(defk settle-record ") line)) 1) "記録の腕の本体は finalize-job と停止の腕の共有(R26)")
       (setv judgment-lines (code-lines (/ ACP-DIR "judgment.hy")))
       (assert (= (len (lfor line judgment-lines :if (.startswith line "(defk restart-condition-of ") line)) 1))
       (setv tests (.read-text (/ (. (Path __file__) parent parent parent) "packages" "doeff-agents" "tests" "test_sessionhost_acp.py") :encoding "utf-8"))
       (for [name ["test_stop_closes_running_jobs_with_agentd_restart_and_leaves_the_session_to_the_host"
                   "test_acp_url_has_no_localhost_default_and_the_join_bundle_carries_it"]]
         (assert (in (+ "def " name "(") tests) f"R26 の反例の検が無い: {name}"))
       (setv host-tests (.read-text (/ (. (Path __file__) parent parent parent) "packages" "doeff-agents" "tests" "test_sessionhost_headless.py") :encoding "utf-8"))
       (for [name ["test_stop_verdict_cuts_only_the_mid_turn_rows"
                   "test_host_headless_stop_cuts_the_mid_turn_row_and_terminates_every_process"
                   "test_real_host_sigterm_closes_the_running_turn_before_exit"]]
         (assert (in (+ "def " name "(") host-tests) f"R26 の host の反例の検が無い: {name}")))
     (deftest test-adr-doe-agents-012-conversations-compact-at-their-threshold
       ;; R27 の針(構造): 判断は judgment の 1 点ずつ(compaction-due・context-percent-of・compact-at-of)・腕は next-arm-for-job の
       ;; compact の引数 1 つ・claim の腕は会話の行を鍵で読み compaction-due を呼ぶ・実測は記録の腕(settle-record・interrupt-job)の
       ;; 2 か所が同じ 1 点(with-context-percent)へ置く・turn-record の usage へは書かない・計器の名は effects の 1 点。
       ;; 反例(挙動)は sessionhost_acp_compact_deftests.hy(実測・判断・fake で一周)と test_sessionhost_acp.py の the-one-decision。
       (setv judgment-lines (code-lines (/ ACP-DIR "judgment.hy")))
       (for [name ["compaction-due" "context-percent-of" "compact-at-of" "conversation-key-of" "context-percent-for" "with-context-percent" "codex-context-of"]]
         (assert (= (len (lfor line judgment-lines :if (.startswith line f"(defk {name} ") line)) 1) f"判断は 1 点(R27): {name}"))
       (assert (any (gfor line judgment-lines (.startswith line "(defk next-arm-for-job [candidate view home effort compact]"))) "圧縮は next-arm-for-job の 5 つ目の引数(R27)")
       (assert (any (gfor line judgment-lines (in "compact (ArmChoice :arm NEXT-ARM-REHYDRATE :source None :retire (if alive candidate None) :compacts True)" line))) "圧縮の腕は rehydrate + compacts(R27)")
       (setv defer-at (next (gfor [i line] (enumerate judgment-lines) :if (in "(and alive (not idle) live-backend) (ArmChoice :arm NEXT-ARM-DEFER" line) i) None))
       (setv compact-at (next (gfor [i line] (enumerate judgment-lines) :if (in "compact (ArmChoice :arm NEXT-ARM-REHYDRATE" line) i) None))
       (assert (and (is-not defer-at None) (is-not compact-at None) (< defer-at compact-at)) "手番の途中は圧縮より defer が先(R27)")
       (assert (= (len (lfor line judgment-lines :if (in ":context (if (is context-tokens None) None {\"tokens\" context-tokens \"window\" context-window})" line) line)) 1) "claude の実測は deltas の 1 点(R27)")
       (assert (= (len (lfor line judgment-lines :if (in "(codex-context-of (if (isinstance last-usage dict) last-usage None)" line) line)) 2) "codex の実測は rollout と app-server の 2 か所が同じ 1 点を呼ぶ(R27)")
       (setv agentd-lines (code-lines (/ ACP-DIR "agentd.hy")))
       (assert (= (len (lfor line agentd-lines :if (in "(<- due bool (compaction-due compact-at percent))" line) line)) 1) "claim の腕は compaction-due を 1 度呼ぶ(R27)")
       (assert (= (len (lfor line agentd-lines :if (in "(next-arm-for-job candidate view home effort compact)" line) line)) 1) "腕の呼び手は 1 つ(R27)")
       (assert (= (len (lfor line agentd-lines :if (in "(<- measured AgentdState (with-context-percent state job.session-id percent))" line) line)) 2) "実測を置く点は記録の腕の 2 か所(settle-record・interrupt-job)(R27)")
       (assert (= (len (lfor line agentd-lines :if (in "(AcpGetRow :key conversation-key)" line) line)) 1) "会話の行は鍵で 1 回読む(R27)")
       (assert (= (len (lfor line agentd-lines :if (in "\"metric\" METRIC-COMPACTIONS-TOTAL" line) line)) 1) "計器は 1 点(R27)")
       (for [line agentd-lines]
         (assert (not-in "contextPercent" line) f"turn-record に文脈の欄を書かない(R27): {line}")
         (assert (not-in "\"compactAt\"" line) f"agentd.hy は compactAt の綴りを直に読まない(R27): {line}"))
       (setv effects-lines (code-lines (/ ACP-DIR "effects.py")))
       (assert (any (gfor line effects-lines (.startswith line "METRIC_COMPACTIONS_TOTAL = \"agentd_compactions_total\""))) "計器の名は effects の 1 点(R27)")
       (assert (any (gfor line effects-lines (.startswith line "    compacts: bool = False"))) "ArmChoice.compacts(R27)")
       (assert (any (gfor line effects-lines (.startswith line "    context_by_session: tuple[tuple[str, int], ...] = ()"))) "実測の cache は AgentdState の 1 欄(R27)")
       (setv reads (json.loads (.read-text (/ (. (Path __file__) parent parent) "contracts" "reads.json") :encoding "utf-8")))
       (assert (in "kinds.conversation.schema.properties.status.properties.agent.properties.compactAt" (get (get reads "reads") "agora-kinds")) "読む欄の宣言(R27)")
       (setv tests (.read-text (/ (. (Path __file__) parent parent parent) "packages" "doeff-agents" "tests" "sessionhost_acp_compact_deftests.hy") :encoding "utf-8"))
       (for [name ["test-a-turn-end-measures-the-context-and-the-next-turn-over-compact-at-rehydrates"
                   "test-below-the-threshold-or-without-a-declaration-the-warm-session-is-kept"
                   "test-claude-events-measure-the-last-message-against-the-result-context-window"
                   "test-codex-rollout-and-app-server-measure-the-last-response"]]
         (assert (in (+ "(deftest " name) tests) f"R27 の反例の検が無い: {name}")))
     (deftest test-adr-doe-agents-012-interrupts-ride-the-running-turn
       ;; R21 の針(構造): 判断は judgment.hy の 1 点ずつ・配達は agentd.deliver-interrupts の 1 点・agentd は器の作法の語を
       ;; 持たない・claude の headless は stream-json の入力・sessionhost の割り込みの口は mode = interrupt の 1 語。
       (setv judgment-lines (code-lines (/ ACP-DIR "judgment.hy")))
       (for [name ["pending-interrupts-of" "interrupts-delivered-status-of" "job-row-keyed"]]
         (assert (= (len (lfor line judgment-lines :if (.startswith line f"(defk {name} ") line)) 1) name))
       (setv agentd-lines (code-lines (/ ACP-DIR "agentd.hy")))
       (assert (= (len (lfor line agentd-lines :if (.startswith line "(defk deliver-interrupts ") line)) 1)
               "割り込みの配達は agentd.deliver-interrupts の 1 点(R21)")
       (assert (= (len (lfor line agentd-lines :if (in "(SessionInterject :session-id" line) line)) 1)
               "器へ渡す点は 1 つ(R21)")
       (for [line agentd-lines]
         (for [word ["claude-user-line" "claude_user_line" "--input-format" "REQ_TURN_INTERRUPT" "HeadlessInject"]]
           (assert (not-in word line) f"agentd.hy は器の作法の綴りを持たない(R21): {line}")))
       (setv effects-lines (code-lines (/ ACP-DIR "effects.py")))
       (assert (= (len (lfor line effects-lines :if (.startswith line "JOB_INTERRUPTS_KEY: str = \"interrupts\"") line)) 1))
       (assert (= (len (lfor line effects-lines :if (.startswith line "JOB_INTERRUPTS_DELIVERED_KEY: str = \"interruptsDelivered\"") line)) 1))
       (assert (= (len (lfor line effects-lines :if (.startswith line "    interrupts_sent: tuple[str, ...] = ()") line)) 1)
               "渡した id の memory は InFlightJob の 1 欄(R21)")
       (setv handler-lines (code-lines (/ ACP-DIR "handlers.py")))
       (assert (= (len (lfor line handler-lines :if (in "\"mode\": \"interrupt\"" line) line)) 1)
               "sessionhost への割り込みの口は session.send の mode = interrupt の 1 語(R21)")
       (setv argv-lines (code-lines (/ SESSIONHOST-DIR "impls/headless_argv.hy")))
       (assert (any (gfor line argv-lines (in "\"--input-format\" \"stream-json\"" line)))
               "claude の headless は stream-json の入力の温かい process(R21)")
       (setv protocol-lines (code-lines (/ SESSIONHOST-DIR "headless_protocol.py")))
       (assert (= (len (lfor line protocol-lines :if (.startswith line "    def inject(self, text: str) -> Injection:") line)) 2)
               "割り込みの本文の作法は Dialogue.inject の 2 腕(claude / codex)(R21)")
       (assert (any (gfor line protocol-lines (in "one_process_per_turn: bool = False" line))))
       (setv host-lines (code-lines (/ SESSIONHOST-DIR "host.hy")))
       (assert (any (gfor line host-lines (in "(headless-inject-program sid message)" line)))
               "host の mode = interrupt は inject の program へ(R21)")
       ;; 反例(挙動): 行の interrupts は載せた順に器へ渡り、同じ 1 回の書きで interruptsDelivered へ移る。断られた id は残る。
       (setv world (World))
       (.put-row world.acp (message-row "m-x" "first"))
       (.put-row world.acp (turn-row "t-2" "conv-i" "m-x" (- world.local.now-ms 300)))
       (.tick world 1000)
       (setv sid (sid-of world "t-2"))
       (.put-row world.acp (message-row "m-i1" "stop"))
       (.put-row world.acp (message-row "m-i2" "then continue"))
       (setv running (get world.acp.rows "acp-system:agent-job:t-2"))
       (setv #^ JSONObject placed (dict (status-of running)))
       (setv (get placed "interrupts") ["m-i1" "m-i2"])
       (.put-row world.acp (AcpRow :namespace running.namespace :key running.key :kind running.kind :resource-id running.resource-id
                                   :version running.version :generation (+ running.generation 1) :created-at-ms running.created-at-ms
                                   :labels running.labels :payload running.payload :spec running.spec :status placed))
       (.tick world 1000)
       (assert (= world.sessions.interjections [#(sid "stop") #(sid "then continue")]) "載せた順に器へ(R21)")
       (setv after (status-of (get world.acp.rows "acp-system:agent-job:t-2")))
       (assert (= (get after "interrupts") []) "渡した id は interrupts から消える(R21)")
       (assert (= (get after "interruptsDelivered") ["m-i1" "m-i2"]) "渡した id は interruptsDelivered に残る(R21)")
       (assert (= (get after "phase") PHASE-RUNNING) "他の欄は写す(R21)")
       (.tick world 1000)
       (assert (= (len world.sessions.interjections) 2) "二度渡さない(R21)"))]
  :plans ["docs/impl-requests/stage2-lane-prompts/lane-2b-agentd.md(agora-redesign)"
          "docs/impl-requests/stage2-lane-prompts/lane-2b2-agentd-fix.md(agora-redesign・改訂 R7〜R9)"
          "docs/impl-requests/stage2-lane-prompts/lane-2b3-warm-session.md(agora-redesign・改訂 R10)"
          "docs/impl-requests/stage2-lane-prompts/lane-2d-headless-backend.md(agora-redesign・改訂 R11〜R14)"
          "docs/impl-requests/stage2-lane-prompts/lane-2d2-codex-headless-shim.md(agora-redesign・追補 R16)"
          "docs/impl-requests/stage6-lane-prompts/lane-6f-gcp-node-join.md(agora-redesign・追補 R17)"
          "docs/impl-requests/stage7-lane-prompts/lane-7d3-agentd-profile-observed.md(agora-redesign・追補 R18)"
          "docs/impl-requests/stage8-lane-prompts/lane-4j-acp-debts.md(agora-redesign・R18 の追補: 器の profile の集合は家の在否で先に読む)"
          "docs/impl-requests/stage8-lane-prompts/lane-4u-turn-events-persisted.md(agora-redesign #49・追補 R19)"
          "docs/impl-requests/stage8-lane-prompts/lane-4w-rehydrate-across-profiles.md(agora-redesign #51・operator 決定 #54・追補 R20)"
          "docs/impl-requests/stage8-lane-prompts/lane-4x-messaging-queued-and-interrupt.md(agora-redesign #56・追補 R21)"
          "docs/impl-requests/stage8-lane-prompts/lane-4aa-live-tail-200ms.md(agora-redesign #63・追補 R22)"
          "docs/impl-requests/stage9-lane-prompts/lane-9f2-agentd-dual-write.md(agora-redesign #59・設計 §2.4・本文の二重書き)"
          "docs/impl-requests/stage9-lane-prompts/lane-9f4-agentd-headline-entries.md(agora-redesign #59・設計 §2.2 / §2.4・R19 / R20 の追補: 見出しだけ・recordRef / recordedSeq・再開は service から)"
          "docs/impl-requests/stage9-lane-prompts/lane-9o3-agentd-warm-session-honors-model.md(agora-redesign #75・R20 の追補: 家の鍵に model)"
          "docs/impl-requests/stage10-lane-prompts/lane-10e-agent-settings-catalog-and-chip.md(agora-redesign #53・追補 R24: 能力の表・effort の腕・AgentSettingIgnored)"
          "docs/impl-requests/stage10-lane-prompts/lane-10h-agentd-dead-backend-recovery.md(agora-redesign #84・追補 R25 / R26: backend の生死は観測で・復帰・session-lost・resume の KeyError・停止で子を黙って道連れにしない・ACP の宛先は宣言ちょうど)"])
