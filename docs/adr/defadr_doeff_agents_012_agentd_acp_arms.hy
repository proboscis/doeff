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
;;; 段 11 lane 11v(agora-redesign #55 便 1・依頼者の裁定 2026-09-16)の改訂 = R34: R20 の畳みが上限で落とす古い手番を黙って捨てず、
;;; 落とした区間を見出し 1 行(期間・kind ごとの件数・道具の名・全文の在処 — judgment.history-dropped-headline・綴りは薄い再開の
;;; turn-record の見出しと同じ history-counts-note)に畳んで残した手番の前に置く。決定的(model を呼ばない)。model による要約は
;;; 便 2 の設計だけ(費用 = one-way door・operator の判断)。上限の宣言は rehydrate_history_byte_budget の 1 点のまま。
;;; 便 3(agora-redesign #225・依頼者の裁定 2026-09-16「案 B を便 3 として起票して L140 の後に実装してよい」)の改訂 = R35: 手番を
;;; 丸ごと落とす前に、古い手番から道具の項(tool_use の入力・tool_result の本文)だけを先頭 budget / HISTORY_THIN_DIVISOR byte に
;;; 薄くして元の byte を名乗る(judgment.history-event-thin-line / history-thin-body)。郵便・agent の text・user / system / error は
;;; 1 byte も変えない。決定的・費用 0・値の宣言は増やさない(比の定数は effects の 1 点)。
;;; 段 12 lane 12j(agora-redesign #233・#55 案 D・operator 2026-09-16 "lets see if 1 will work")の改訂 = R37: charter.kind = summarize の
;;; job(会話の履歴の段階つき要約)を、会話の profile の札を借りて claude -p を区間ごとに 1 回起こす腕(claim-summarize-job → observe-summarize)で
;;; 担い、要約の本文を記録の service の stream(streamKind summary)へ、claim check を agora の kind summary の行へ書く。session を起こさず・
;;; turn-record を書かず・郵便を読まない。prompt(残す情報・落とす情報)の定義点は judgment.summarize-prompt-of の 1 点。
;;; 同じ lane の便 3 = R38: 契機はこの agentd の手番の終わり(材料の末尾の文脈の大きさ > summarize_trigger_tokens)で、この手番より前の
;;; 区間(この手番の stream の最初の recordSeq − 1)を要約する summarize の job を create-only で 1 つ書く(judgment.summarize-due の 1 点)。
;;; 履歴からの再開は kind summary の行を先に読み(要約 = 原文の前・区間の順)、原文は要約の区間より新しい出来事だけ(record-turns-for の floor)。

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
(import doeff_agents.sessionhost.acp.judgment [capture-verdict job-step-of mail-turn-text-of record-due stream-capability-of-backend
                                               wait-seconds-for])
(import doeff_agents.sessionhost.acp.runtime [AgentdPreflightError initial-state install run-heartbeat run-tick settings-from-env])
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
    None)

  ;; 段 10 lane 10ba(agora-redesign #115): lease の書き手は tick と独立した heartbeat の 1 点。
  ;; 検も同じ入口(runtime.run_heartbeat)で撃つ — 器の中で thread を起こさない。
  (defn #^ str heartbeat [self]
    (run-heartbeat self.settings
                   [self.acp.dispatch self.custody.dispatch
                    self.sessions.dispatch self.local.dispatch])))


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


(defclass HeadlessWorld [World]
  "backend = headless の世界(events file が実況の正本 — R29 の反例は CLI の行を読む)。"
  (defn #^ None __init__ [self]
    (.__init__ (super))
    (setv self.settings (AgentdSettings :node-name "mac-1" :homes-root "/homes"
                                        :backend-kind "headless" :stream-capability "events"))
    (setv self.sessions (FakeSessions :agent-type "claude" :backend-kind "headless" :events-root "/events"))))


(defn #^ AcpRow turn-row-timed [#^ str job-id #^ str subject #^ str message-id #^ int created-at-ms
                                #^ int escalation-seconds]
  "turn-row + charter.interruptEscalationSeconds(R29 — 期限は charter の値ちょうど)。"
  (setv base (turn-row job-id subject message-id created-at-ms))
  (setv #^ JSONObject spec (dict base.spec))
  (setv raw-charter (get spec "charter"))
  (assert (isinstance raw-charter dict))
  (setv #^ JSONObject charter (dict raw-charter))
  (setv (get charter "interruptEscalationSeconds") escalation-seconds)
  (setv (get spec "charter") charter)
  (AcpRow :namespace base.namespace :key base.key :kind base.kind :resource-id base.resource-id
          :version base.version :generation base.generation :created-at-ms created-at-ms
          :labels base.labels :payload base.payload :spec spec :status base.status))


(defn #^ str mailed [#^ str message-id #^ str body]
  "段 10 lane 10r 追補(agora-redesign #99): 手番へ渡る郵便の文 = 見出し 1 行 + 本文(judgment.mail-turn-text-of の 1 点 —
   検体の郵便 message-row は id と body だけなので見出しの他の欄は「無し」)。"
  (run (mail-turn-text-of message-id {"id" message-id "body" body} body)))


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
     (rule R5 "借りた札は disk の平文に残さない — 例外は家の中の auth file(codex の <homes>/codex/<account>/auth.json・0600)だけ。codex の家(binding の profile_dir)は claude の config_dir と同じく <homes>/codex/<account> ちょうどで、charter の binding の家を読まない(段 10 lane 10r・agora-redesign #99 — ACP は charter に binding を書かない)。claude の札は env CLAUDE_CODE_OAUTH_TOKEN で渡し、log と計器には載せない。【註 2026-09(従量課金の便 lane A・条文は不変): **受け手自身の鍵が受け手の家(0600)に在るのは『借りた札』ではない** — この rule と下の law borrowed-credentials-never-rest-on-disk-in-plain の対象は預かり所が貸した札ちょうど。従量課金の kind(claude-code-metered の settings.json の apiKeyHelper / codex-metered の auth.json の OPENAI_API_KEY)の鍵は、その配備の持ち主が自分の CLI の道具で自分の家に入れた資格で、預かり所を通らず host も値を読まない(ADR-DOE-AGENTS-004 R9)。】")
     (rule R6 "値の宣言は 1 点: lease の TTL と周期・watch の resync・frame の rate・購読の読み直しの周期は effects.AgentdSettings の既定値、env の名の綴り(URL・札・node の名・backend・所有)と host の argv の綴りは effects.py(R17 の join が同じ綴りを組む — 2026-09-12 改訂・以前は handlers.py / valve.py)、URL の既定値は handlers.py。")
     (rule R7 "job の進みは行から導く: 自分の Running(running-on-me)は memory に無くても resync の拍に拾い、次の 1 手は judgment.hy の job-step-of(器の現況 → observe | record-end | fail-missing・閉語彙 effects.JobStep)の 1 点で決める — memory に在る job の拍も同じ 1 点を通る。record-end は記録の腕(turn-record ended・result・phase Ended)だけを撃ち launch も send もし直さない。fail-missing は記録が在れば ended にし condition SessionFailed で Ended。終端の語彙(SESSION_TERMINAL_STATUSES)を読むのは judgment.hy だけ。")
     (rule R8 "capture の gone は終端の合図で例外ではない: SessionCapture の答えは閉語彙 CaptureFrame | CaptureGone、実 handler は host の断り(AgentdClientError)を CaptureGone に写す(host.hy / substrate は触らない)。gone の job は capturing = False・stream_gone = True で、以後 capture も購読の読み直しもせず、器の終端(同じ拍に読み直す)で記録の腕へ。器が終端の拍は capture を撃たない(job-step-of を実況より先に読む)。")
     (rule R9 "tick の縁: heartbeat・profile の残量の観測(R18)・受け・割り込みの配達(R21)・job ごとの観測は互いの I/O の失敗(effects.IO_FAILURES = RuntimeError | OSError)で止まらない — program の agentd-tick が 5 つの腕をそれぞれ捕まえ、log して次の周期 / 次の拍へ持ち越す(condition には写さない — 一時の失敗を job の結末にしない)。I/O より広い例外は捕まえない(runtime.run_loop の縁)。")
     (rule R11 "headless backend: host の backend の閉語彙は tmux | herdr | headless。headless の器は専用の program(sessionhost/headless.hy)と substrate(effects.hy の Headless* → substrate_headless.hy → headless_process.py)で、host.hy は backend の分岐だけ(RPC の語彙は同じ意味・session.interrupt を足す)。stdin / stdout の作法と手番の判断は headless_protocol.py の Dialogue / turn_verdict の純関数 1 点。claude の print mode の argv の家は impls/headless_argv.hy ちょうどで、semgrep doeff-agents-no-claude-print-mode の除外もその家と headless の substrate / program / 検だけ。admission と identity の準備は tui の launch と共有する(launch.hy admit-launch / prepare-launch-workspace)。")
     (rule R12 "events の実況: agentd は headless の器の実況を events file(wire の backend_ref.events_path)から offset で読み(SessionEvents)、純関数 judgment.events-to-deltas(claude = stream-json・codex = app-server の通知)で契約の種類の閉語彙(text / tool_use / tool_result / usage)の TurnDelta に写す。text の delta は 1 行ずつ frame、完成した本文は entries だけ。headless の器に pane の capture は撃たない。node の observations.streamCapability は host の backend から導く(judgment.stream-capability-of-backend の 1 点: headless = events・それ以外 = frames)。")
     (rule R13 "withdraw は中断の合図: 自分の走っている job の行が Withdrawn(書き手 = 作った側)になったら、judgment.interrupt-arm-for の 1 点で手番の途中なら session.interrupt(headless = SIGINT / turn/interrupt・tmux = Escape・session は残す)を撃ち、turn-record を ended(ここまでの entries と usage)、agent-job の conditions に Interrupted(phase は書かない)。session.cleanup は撃たない(温かい session は残す — 寿命は sessions-to-retire)。")
     (rule R14 "watch の拍は差分の読み・計器の始点は生まれの着地: 行の読み直しの様式は judgment.list-mode-for の 1 点(full = 最初の拍・周期の保険・gap・接続の張り直し / window = watch で起きた拍 = GET /api/event-window の post-image で AgentdState.rows を差し替え・窓が読めなければ full に落ちる / none = idle)。郵便の本文は鍵で 1 行ずつ読む(全量 list しない)。計器 agent-job-to-send の createdAtMs は judgment.birth-ms-of の 1 点(生まれの表 → generation 1 の image の landed_at_ms → 今日の値 created_at_ms)。")
     (rule R15 "session の id は agentd が鋳造する: 起こす session の id(session_id と session_name・sessionHandle.sessionId・stream の name)は effect MintId(ULID・時刻と乱数は handler)の答えで、charter(Messaging が組む launch の params)の session_id / session_name は読まない(judgment.launch-plan-of が落とす・据えるのは charter-with-session-id の 1 点)。実弾 2026-09-12: 温かい session が idle TTL で片付いた後、charter の固定の id の launch が `session is already registered`(host は片付いた行を登記のまま残す)に落ちて LaunchFailed で Ended した。")
     (rule R16 "headless の起こす手番は郵便を 1 手番目の本文に畳む: host の backend が headless(AgentdSettings.backend_kind — runtime.settings_from_env が valve.backend_of から導く 1 点・streamCapability と同じ源)なら、launch / resume の腕は charter の prompt(前置き)と inputs の郵便の本文を judgment.first-turn-prompt-of(空行区切り・郵便が無ければ charter だけ)で 1 つに畳んで起こし、after-start は session.send を撃たない。判定は judgment.first-turn-carries-inputs(backend ∧ 腕)の 1 点。send の腕(温かい session)は郵便の本文だけを send。tui(tmux / herdr)は今日どおり launch の後に send。郵便の文は見出し 1 行 + 本文(段 10 lane 10r 追補・agora-redesign #99・依頼者の裁定 2026-09-15 案 A — `[郵便 <id>・kind=・class=・from=・parent=・at=<JST>]`・綴りは judgment.mail-heading-of の 1 点・1 手番目の畳み・温かい send・割り込みの注入の 3 路が judgment.mail-turn-text-of を通る。手番の agent は郵便を id で名指せる — 受付の ai forward は id が要る)。turn-record の create・計器 agent-job-to-send・in-flight の登記は腕に依らず同じ。実弾 2026-09-12: launch の直後の send が `headless session already exists` で tick ごと落ち、turn-record が作られず job は拾い直しの腕へ。")
     (rule R17 "機体を足す手順は 1 命令 join: `doeff-sessionhost join --server <URL> --token-file <札> [--config <toml>] [--node-name] [--state-dir] [--backend] [--session-hooks] [--custody] [--borrower-key-file] [--service-account-token-file] [--ownership --ownership-proof]` の宣言は join.hy の join-spec-of(flag > toml(schema doeff.agentd-join.v1・flag と同名の鍵)> 既定)の 1 点で JoinSpec に組み、join-plan-of の 1 点で今日の起動が読む env の束(effects.py の *_ENV の綴り)と host の argv(--db / --socket / --max-running none / --backend / serve)に写す。entry.py は plan を process の env に据えて serve --acp と同じ経路を走る — env の名を entry / runtime が自分で組まない・読み手を増やさない。所有の等級 ownership(company | personal)は proof(gce-project:<project-id> | declared)と対でだけ宣言でき(片方だけは断る)、runtime.start_agentd_thread は thread を起こす前に join.ownership-preflight を撃ち(gce-project = OwnershipProbe の答え = metadata の project-id が一致する時だけ通す・declared = 撃たない)、不一致は AgentdPreflightError で参加しない。検めた等級は judgment.node-status-with-lease の 1 点で observations.ownership{grade, proof} に書く(宣言が無ければ欄ごと無い)。agentd.hy は ownership の語を比較しない。預かり所への身元(段 10 lane 10y・agora-redesign #110): 借り手札(X-Borrower-Key・Mac)に加え、宣言 file の [custody].service_account_token_file(flag --service-account-token-file → env AGORA_CUSTODY_SA_TOKEN_PATH)を持つ agentd(k8s の pod)は、貸与・引換券の redeem・返却の要求に Authorization: Bearer <その file の中身> で名乗る(預かり所の k3s の backend が TokenReview で解く・借り手名 = ns/sa)。file は要求ごとに読み、宣言したのに無い・空の拍は撃たずに宣言の置き場を名指して断る(handlers.CustodyHttp の 1 点)。本文の行き先(段 9f lane 9f-6・agora-redesign #59): 会話の記録の service の宛先(宣言 file の [record].url / flag --record → env RECORD_SERVICE_URL)を持たない agentd は参加を断る — 判断は join.record-sink-of の純関数 1 点(宣言 → 参加可否・宣言の検で届くかは検めない)、読みは runtime.settings_from_env の 1 点(join の経路も serve --acp の経路も同じ門)、断りは AgentdPreflightError(理由 = 宣言の置き場)で entry.py が stderr に書いて exit 2(宿が再起動する — process の中で再試行しない・宣言が直るまで参加しない)。宛先が在って届かないのは spool が受ける(参加は断らない)。本文の行き先を持たないまま見出しだけを書く agentd は存在しない。")
     (rule R18 "profile の残量の観測は agentd が書く(段 7 lane 7d-3): agentd-tick の 1 つの腕 observe-profiles が AgentdSettings.profile_observe_seconds(既定 300・値の宣言は 1 点・同じ値を読み口の cache の寿命に渡す)の周期で生きている profile の行(state ≠ retired)を読み、この機体の profile の家の在否を effect ListProfileHomes(段 8e lane 4j — 実 handler = handlers.list_profile_homes = 登録簿の 1 点 handlers.PROFILES_COMMAND = `agentcli profiles list --json` の subprocess + dir の実在)で読み、judgment.profile-rows-held の 1 点で観測する行を絞る(段 10 lane 10y・agora-redesign #110: 宣言の所有の等級〔join で検めた ownership〕が company でない機体〔personal・未宣言〕は spec.boundary = company の行を家が在っても持たない — 軸は機体の所有で置き場〔place〕ではない・provider を呼んでよいかの最後の判定は agentcli の葉のまま): 家の在る行が 1 つも無い機体(pool の pod — personal の資格は預かり所が観測し、会社 profile は会社機体だけ)は usage を撃たず、「観測する profile なし」を AgentdState.no_profile_homes_logged で 1 度だけ log し(家が現れたら戻る)、計器 profile-observed(homes 0)は出す。家の在る行が在れば、この機体が持つ資格の残量を effect ReadProfileUsage(kind = effects.PROFILE_USAGE_KIND = claude・契約の行は資格の種類を運ばない)で 1 度読む。実 handler = handlers.read_profile_usage = dotfiles agentcli の console script(handlers.USAGE_COMMAND = `ai usage --json`)の subprocess ちょうど — sessionhost は agentcli を import しない・会社境界(company_boundary)の判定を持たない(断りは record の error → ProfileUsageUnavailable)・読み口の落ち方(profiles.gen.json の不在)で器の profile の有無を判じない。書く観測は judgment.profile-observed-of の 1 点(閉語彙 effects.ProfileVerdict): 窓 = observed-window-of(spec.reset.everySeconds と一致する窓・無ければ 5h)、remaining = 100 - used(percent・budget.unit ≠ percent は書かない)、resetAt = 窓の戻る時刻(無ければ observedAt)、observedAt = 断面の時刻、node = 自分。post-image は profile-status-with-observed(committed の state・conditions を写す)、committed と同じ observed は書かず(profile-observed-changed)、Conflict は log して次の周期、Refused / 書かない理由は log に 1 行、この機体に無い profile(ProfileNotHeld)は書かず log もしない。agentd.hy は窓の名・単位・境界の語を比較しない。")
     (rule R19 "手番の出来事は拍ごとに turn-record へ追記する(段 8 lane 4u・agora-redesign #49): stream-records は実況の材料の追記を読むたびに、その拍の出来事(judgment.deltas-of の entries = 契約 agora-kinds.json の turn-record の status.entries の item — kind は effects.EntryKind の閉語彙 text / tool_use / tool_result / frame / system / error・at = 読んだ拍・seq = frame と共有の採番)を agentd.append-entries の 1 点で行の status.entries へ追記する(耐久化は手番の終わりを待たない)。書きは行の最後の image(InFlightJob.record — 無ければ鍵で読む)に対する CAS(AcpPutStatus の ifGeneration)で、Conflict は行を読み直して同じ出来事を 1 度だけ積み直し、Refused / 行の不在は出来事を InFlightJob.pending_entries に持ち越して次の拍か手番の終わりに乗せる(落とさない)。拾い直した job の採番(seq 0 から)が行の seq と衝突すれば judgment.next-seq-after / renumbered-entries で行の次から振り直す。entry の形は見出しの閉じた型 effects.TurnEntryHeadline(段 9f lane 9f-4・設計 §2.2: seq・at・kind・toolName・toolUseId・bytes・sha256・isError — 本文の欄 text / summary / input / output / model は型に無く、写さない・切らない〔切り詰めは会話の記録の service の責務〕)で、導く点は judgment.headline-of-body の 1 つ・JSON への写しは entry-json-of の 1 つ・bytes / sha256 は service の冪等の判断と同じ計算(record-body-bytes-of = text / summary / input / output の在る欄だけの compact・鍵 sort・UTF-8)。toolUseId は呼び出しと結果を結ぶ鍵。行の上限(TURN_RECORD_ENTRIES_BYTE_BUDGET = 262144 byte)は judgment.entries-within-budget が古い見出しから落とし先頭に印(TurnEntryDropMarker → kind system・truncated・dropped — 本文も bytes / sha256 も無い)を残す。service が本文を受理した答え(highestProducerSeq)は agentd.mark-recorded が status.recordRef(`record:<cid>/<streamId>`)/ recordedSeq(後ろへ戻さない)に写す。claude の system の行(init / API の retry / hook の失敗)は kind system に、result の誤りは kind error に、codex の turn/completed の誤りも kind error に写す(手番の終わりの判定は host のまま — ここは記録だけ)。手番の終わり(finalize-job / interrupt-job)は drain-stream で最後の材料を同じ拍で読んで追記し、turn-record-ended-status は残りの出来事を**追記**した上で ended・usage を据える(entries を置換しない — 旧の形は最後の本文 1 行だった)。usage は手番の全材料の読み直し(turn-batch-of)から数える(message ごとの重複を跨がない)。")
     (rule R20 "会話の cache を保つのは同じ機体 ∧ 同じ家の時だけ・それ以外は 履歴からの再開(ACP の記録から)(段 8q・agora-redesign #51・operator 決定 #54): Bound の job の起こし方は judgment.next-arm-for-job(candidate view home)の 1 点 — candidate = affinity.predecessor か会話の最後の手番の session(warm-candidate-of)、home = judgment.session-affinity-key-of(binding.account・charter の binding・charter.model〔plan.model〕の組 — model は段 9o lane 9o-3)、session の家は起こす時に刻んだ launch_attribution の agentd の欄(session-attribution-of / attribution-of-view — 回収される agent-job の行から導かない)。候補なし ∧ 記録の service に会話の原文の出来事が在る → rehydrate(追補 4・段 12 lane 12j・agora-redesign #233 / #176: 候補の無さは新しい会話の証拠ではない — 宣言を変えた手番は Messaging の lineageFor〔段 12 lane 12k〕が predecessor を空にし、前の手番の agent-job の行は終了 300 s で回収される。問いは RecordReadSince since 0・limit 1・原文の kind の 1 読みで、候補が無く記録の service が配線されている拍だけ撃つ — 答えの読みは judgment.conversation-recorded-of の 1 点: 空の頁 = 無い / 読めない = 在る / service が無い = 問わない)/ 候補なし ∧ 記録に無い → launch / 生きて idle ∧ 同じ家 → send / 生きて idle ∧ 家が違う → 候補を session.cleanup して rehydrate / 生きていて idle でない → defer / 器に登記されて終端 ∧ 同じ家 → session.resume(cache を保つ)/ それ以外(器に無い = 別の機体・終端だが家が違う・帰属が無く家が分からない)→ rehydrate。rehydrate = session.launch で、最初の本文 = charter の prompt + judgment.rehydrate-history-of(会話の郵便〔ACP の kind message〕と手番の本文を時刻順・kind ごとに畳み、この手番の inputs と frame は除き、AgentdSettings.rehydrate_history_byte_budget〔既定 65536 byte〕を超えたら古い手番から要約せず落とし、落とした区間を見出し 1 行〔期間・件数・道具の名・全文の在処 — R34〕に畳んで残す)(+ headless は郵便の本文)。手番の本文の材料は型で 2 つ(段 9f lane 9f-4・設計 §2.4): RecordedTurns = 会話の記録の service を before=latest から後向きに読んだ本文(agentd.record-turns-for — effect RecordRead を 1 頁 RECORD_PAGE_MAX_LIMIT ずつ、judgment.record-history-satisfied〔読めた bytes ≥ 上限〕か会話の最初まで)/ HeadlineTurns = service が配線されていない・届かない時の ACP の turn-record の見出しだけ(**薄い再開** — 手番ごとの出来事の数と道具の名だけを畳み、prompt の頭と log が薄い再開と理由を名乗る。本文の無い行を本文として扱わない)。材料の読みは名指しの順(段 9q・agora-redesign #77): 本文 = RecordRead(会話 1 つ)→ 郵便 = effect AcpConversationMail(kind message・手番を起こし直す時の 1 回だけ)→ 見出し = effect AcpTurnHeadlines(kind turn-record の全量 — 実測 2026-09-14: 29,913 行・172 MB・頭の応答 59 秒)は service が答えなかった薄い再開の拍にだけ(agentd.headline-turns-for の 1 点)。claim(AcpPutStatus)は宣言の照合だけで即時(実測 0.3 秒)、器の準備(再開の読み・畳み)は claim の後の腕 — node の lease(TTL 90 秒)より長く tick を塞がない。resume が器に断られたら judgment.fallback-arm-of で同じ鋳造 id の rehydrate。家またぎの transcript の写し(sessionhost の transplant)には頼らない。turn-record の spec に sessionId(= sessionHandle.sessionId)を書く。node の observations は sessions の各項に account(帰属の account・null = 借りていない)、transcripts に終端の session のうち transcript の file がこの機体に在る会話ごとの最新(judgment.transcript-candidates-of・上限 AgentdSettings.transcripts_observed_max)を載せる — Scheduling はそれを (node, account) で読む(ACP 法 cd258b)。")
     (rule R21 "割り込みの本文は走っている手番へ即座に渡す(段 8 lane 4x・agora-redesign #56・operator 逐語 2026-09-13 \"messaging supports both 'queued/interrupting' messages\"): Messaging(ACP)が走っている手番の agent-job の status.interrupts に載せた Message の id を、agentd は毎拍・行の cache から・自分が走らせている job(memory の InFlightJob)についてだけ読み(agentd.deliver-interrupts の 1 点)、渡していない id(judgment.pending-interrupts-of = 行の interrupts − 行の interruptsDelivered − memory の interrupts_sent・載せた順)ごとに Message の本文を鍵で 1 行読んで SessionInterject(session.send の mode = interrupt)で器へ渡す。渡せた id は鍵で読み直した行に CAS で記録する(judgment.interrupts-delivered-status-of — 同じ 1 回の書きで interrupts から消し interruptsDelivered へ足す・他の欄は写す・Conflict は 1 度読み直す)。器が断った id(走っている手番が無い)はそこで止めて行に残す(順を跨いで後の id を先に渡さない)— 手番が終わればその行は終端の phase で interrupts を持ち、Messaging が queued として積み直す。器の側(sessionhost の headless): claude は `--input-format stream-json` の温かい process(impls/headless_argv.hy の CLAUDE-HEADLESS-FLAGS・実測 conformance/interrupt-physics.md — 手番の途中に書いた user の行は CLI が次の tool の境界で手番に注入し、result の後も process は生きて次の行が次の手番)で、割り込みの本文 = 同じ user の行(headless_protocol.ClaudeDialogue.inject — 走っている手番が無ければ accepted = False)、codex = turn/interrupt を送り interrupted の turn/completed を手番の終わりとして報告せず同じ thread へ本文の turn/start(CodexDialogue.inject — host から見て手番は 1 つのまま)。host は器が引き受けなかった時に型付きに断る(headless-inject-program — 誰の job でもない手番を起こさない)。agentd は器の作法(stdin の綴り・turn/interrupt)を 1 語も持たない。")
     (rule R22 "実況の push の周期は購読者が居る間 ≤ 50 ms(段 8 lane 4aa・agora-redesign #63): headless の器の実況は events file の行の増分で、file の追記は合図を持たない —— agentd が offset から読んで中継へ押す拍の周期がそのまま push の間隔になる。購読者が居る(InFlightJob.capturing)間の watch の待ちの上限は、この器の実況が events(AgentdSettings.stream_capability = events)なら AgentdSettings.events_poll_seconds(既定 0.05 = 出来事ごとの push に最も近い有界の拍)、frames(tui の pane の断面)なら frame_interval_seconds(2〜5 Hz・issue #1 の決定 4 のまま)。購読者が居なければ transcript_poll_seconds(記録の追記だけ)、job が無ければ idle_wait_seconds。判断は judgment.wait-seconds-for の 1 点、値の宣言は AgentdSettings の 1 点(handlers / agentd.hy に周期の literal を置かない)。本番 2026-09-13 17:1x: 実況の最初の tail が attach の後 247〜258 ms、割り込みの反映 219 ms — 画面の糊の側の根(会話簿の毎拍の組み直し)は agora-controllers 741e67d で直し、agentd の側の残りがこの周期(購読ありで 0.4 s・無しで 1.0 s の tick)だった。⚠ 記録(turn-record)への追記の拍は push の周期に**追随しない**(judgment.record-due — transcript_poll_seconds のまま・InFlightJob.last_record_ms): 追記は CAS の書き = ACP の event 1 つで、50 ms の拍ごとに書くと走っている手番 1 つで毎秒 10〜20 の event が journal に並び、画面の糊の watch の拍(1 event = 1 拍)が飽和する(実弾 2026-09-13 18:3x: 糊の占有 367 拍中 359 が 200〜500 ms・hello 15 s)。書かない拍の出来事は pending_entries に持ち越す(落とさない)。")
     (rule R23 "手番の資格の出所は judgment.credential-source-of の 1 点(段 10 lane 10c・agora-redesign #80・operator 決定 2026-09-14 \"access token is to be fetched from k3s\"): launch-plan-of が据えた plan.account(binding.account ∧ charter の agent_type に貸与の種類)が在れば lease(預かり所から借り、借りた家で起こす)、無ければ node が預かり所を宣言している(AgentdSettings.custody_declared — runtime.settings_from_env が CUSTODY_URL_ENV = join の [custody].url の在否から導く 1 点)時 missing、宣言していなければ home。agentd.claim-job は plan を読んだ直後にこの答えを読み、missing の job は起こさず Running も sessionHandle も書かず、end-job-now で条件 CredentialSourceMissing つきの Ended に閉じる(黙って charter の binding = 機体の profile の家へ落ちない)。home(charter の binding で起こす)の経路は預かり所を宣言していない node(移行前の機体)だけに残る。session を使い回す鍵は judgment.session-affinity-key-of(旧名 home-key-of — 鍵の中身は account・binding・model のまま不変で、資格ではない)。ACP の側の半分(配置が会話の profile → profile の行 → spec.account を解いて status.binding.account に置く)は ACP の法 defadr_20260914_turn_credential_is_the_custody_lease_c744ca。")
     (rule R24 "node の能力の表と、効かない宣言の欄の条件(段 10 lane 10e・agora-redesign #53・operator 決定 2026-09-14 \"all lgtm\"): agentd は node の status.capabilities に agent の種類(charter.agent_type の語 claude / codex)ごとの {settings: 受ける欄, restartOn: 変えたら session を作り直す欄} を lease と同じ拍に名乗る(judgment.capabilities-of — 値は effects.AGENT-CAPABILITIES の 1 点・契約 agora-kinds.json conventions.agentSettings.settings の綴り)。restartOn は session-affinity-key-of の鍵の欄(model・profile = account と binding の家)ちょうどで、effort と workDir は受けるが鍵に入れない。effort は claude の --effort / codex の -c model_reasoning_effort(process の旗)なので、同じ家で effort だけ違う温かい session は片付けて同じ session を新しい旗で --resume する(next-arm-for-job の 4 つ目の引数・帰属の effort の欄と比べる — session は作り直さず cache を保つ)。この手番で効かない宣言の欄(能力の表に無い種類・受けない欄・温かい session への send で charter.work_dir が session の cwd と違う)は、judgment.ignored-settings-of の 1 点が条件 AgentSettingIgnored(1 欄 1 行・reason = <欄>=<値>: <理由>)にして手番の終わりに刻む(黙って落とさない)。")
     (rule R25 "backend の生死は host の観測で決め、status の語から推測しない(段 10 lane 10h・agora-redesign #84・既知の形 = kubelet の node 再起動後の container の生死の観測): sessionhost は headless の行の backend(子 process)の生死を観測で決める — pid の存在(kill 0)+ 所有(この host の registry が同じ pid の生きた process を持つ・effect HeadlessLiveness・値 headless_protocol.BackendLiveness)。host の起動時(accept より前・awaiting latch の clear より前)に headless.hy recover-headless-rows が非終端の headless 行を観測し、判断 headless_protocol.recovery_verdict(status_terminal, in_flight, liveness)の 1 点で『手番の途中(awaiting)∧ backend が死んでいる』行だけを exited + cause vanished(ADR-DOE-AGENTS-009 の証拠つき死亡の語彙・reason に pid と観測の文)にして session_exited を刻む。idle の温かい行は触らない(次の send が --resume で同じ session を起こし直す)。awaiting latch の起動時の全 clear(store.hy db-clear-awaiting-latches)は headless の行を対象にしない(headless の latch は『手番の途中』の事実そのもの)。wire の session.get / session.list は backend_alive(headless = 上の観測・tmux / herdr = 行の pane が session の pane の集合に在る・終端の行は観測せず false)を毎回載せる。agentd は判断 judgment.backend-alive の 1 点(器に無い → 偽・明示の False → 偽・観測の無い眺め〔launch / resume の応答の backend_alive = None〕→ 真: 観測の無さは死亡の証拠ではない)を job-step-of(非終端 ∧ 手番の終わりでない ∧ backend が死 → session-lost = 記録の腕と条件 SessionLost〔reason に session・backend の種類・pid・観測の時刻 — judgment.session-lost-condition-of〕で Ended・session は host の monitor に任せて片付けない)と next-arm-for-job(候補が生きて idle でない ∧ backend が生 → defer / ∧ backend が死 → 同じ家なら候補を片付けて resume・違う家なら片付けて rehydrate)で読む。agentd.hy は backend_alive の欄も終端の語も直に読まない。headless の session.resume の腕は launch-params に events_root を運ぶ(launch.hy resume-session — 運ばないと headless-launch-session が KeyError で断り、全部 rehydrate に落ちる)。店の cause の decode(store.hy terminal-cause-from-dict)は契約の欄(category / observed_at)を持たない persisted cause を None(typed には cause なし)と読み、行ごと KeyError で読めなくしない(wire は raw を運ぶ・DB の COALESCE が raw を消さない)。")
     (rule R26 "停止で子を黙って道連れにしない(段 10 lane 10h 便 2・agora-redesign #84): launchd の bootout / kickstart は process group ごと殺すので headless の子 process(pipe の子)は host と共に死ぬ — pipe から切り離して拾い直す形(detach)は取らない(親を失った process は器として使えない: events file の書き手も Dialogue の状態も host に在る)。代わりに host は TERM の 1 度目に accept loop を生かしたまま別 thread(host.hy graceful-stop)で (1) 登録された停止の hook(host.register-shutdown-hook — entry.py が agentd の AgentdRun.close_for_stop を登録する: loop を止めて今の拍を有界に待ち、memory の走っている job を agentd.close-jobs-for-stop で 1 つずつ記録の腕〔残りの材料・turn-record ended〕と条件 AgentdRestart〔judgment.restart-condition-of の 1 点 — node・理由・session・時刻〕で Ended・status frame ended・札の返却。session は片付けない)(2) headless の行の停止の腕(headless.hy stop-headless-rows: 判断 headless_protocol.stop_verdict の 1 点で手番の途中の非終端の行だけ stopped + cause cancelled〔reason = host の停止と信号〕+ session_cancelled・idle の温かい行は触らない・登記の全 process を HeadlessKillAll で段ごとに並列の猶予〔EOF → TERM → KILL〕で降ろす)を走らせ、stderr に数を 1 行ずつ書き、自分に同じ信号を撃ち直す(実の信号 — _thread.interrupt_main は accept の syscall を起こさない)。2 度目の TERM は SystemExit(0) で finally(lease の釈放)へ。SIGKILL には手が無い — 次の起動の復帰(R25)が拾う。Mac の agentd の入れ替えは走っている job が 0 の拍に launchctl の bootout → bootstrap(TERM の経路)で行い、kickstart -k(即時)は使わない。ACP の宛先(実況の push・行の読み書き・watch の全部)は宣言(join の --server / [agentd].server → ACP_DAEMON_URL)ちょうどで、handlers.py に 127.0.0.1:8868 の既定値は無い(runtime.real_dispatchers は宣言の無い env を AgentdPreflightError で断る)。")
     (rule R27 "会話は自分で圧縮する — 閾値は会話の宣言・実測は agentd・腕は履歴からの再開(段 10f 便 2・agora-redesign #82・operator 2026-09-14 逐語 \"that routing agent should compact itself with some threshold\"): 会話の行の status.agent.compactAt(0〜100 の整数・任意・書き手 agora-conversation・契約 agora-kinds.json)は文脈の使用率の閾値。agentd は手番の終わり(settle-record と interrupt-job — 記録の腕)に材料の末尾から文脈の大きさを測り(judgment の deltas-of が DeltaBatch.context = {tokens, window} を組む: claude = 最後の assistant の message の usage の input + cacheRead + cacheWrite + output と result の modelUsage[その model].contextWindow / codex = token_count の last_token_usage〔app-server は tokenUsage.last〕の input + output と model_context_window〔modelContextWindow〕)、judgment.context-percent-of で %(切り捨て・上限 100・窓が無ければ None = 測れない)にして session ごとに AgentdState.context_by_session へ置く(with-context-percent — memory の cache・再起動で消え次の手番の終わりに測り直す)。claim の腕は候補の session が在る時だけ会話の行を鍵で 1 回読み(conversation-key-of・AcpGetRow)、compact-at-of と context-percent-for から judgment.compaction-due(宣言あり ∧ 実測あり ∧ 実測 ≥ 閾値)を求め、next-arm-for-job の 5 つ目の引数 compact に渡す。腕: compact ∧ 候補あり ∧ 手番の途中でない → rehydrate(ArmChoice.compacts = True・生きている候補は片付ける — 温かい cache を捨てて記録の service の履歴を縮めて畳むのが圧縮の意味)/ 手番の途中(backend が生)は defer が先 / 候補なしは launch。compacts の拍に計器 agentd_compactions_total{conversation, agentJobId, sessionId} と log 1 行(retire-reason-of が理由を名乗る)。turn-record の usage には書かない(契約に欄が無い — 耐久にする時は契約の便で contextTokens / contextWindow を足してから)。追補 3(依頼者 2026-09-14 17:1x・実測「agent が自分の会話 id を答えられず session の UUID を答えた」): 起こす手番(launch / resume / rehydrate)の charter.session_env(host の launch-spawn-env が非 auth の overlay として process の env に混ぜる)に AGORA_CONVERSATION_ID(会話の id = 帰属の conversationId)と AGORA_SEAT_OPENER(claim の腕が読んだ会話の行の spec.opener の逐語・読めなければ置かない)を置く — 1 点 judgment.charter-with-conversation-env(incarnation-charter-of が呼ぶ)。`ai tell` / `ai forward` / `ai artifact put` の差出人・著者はこの会話 id ちょうど(便 3 = CLI が読む側)。")
     (rule R28 "node の行は機体が自分で名乗る(段 10 lane 10d・agora-redesign #85・依頼者の回答 問 A = 案 1 — 既知の形 = k8s の kubelet が Node を自分で登記する): agentd は heartbeat の腕(agentd.join-tick)で、自分の名の生きた node の行が無ければ AcpCreate で作り、在れば spec を宣言へ揃える(AcpPutSpec — engine の SpecApplied は status の軸を触らない)。spec の形は judgment.node-spec-of(作る時: name・labels 空・capacity・streamCapability)/ node-spec-declared(揃える時: labels は行のまま — 宣言の外の名乗り〔会社境界の boundary 等〕を運ぶ)の 1 点。capacity は機体の宣言 file の [agentd].capacity(flag --capacity)ちょうどで join.capacity-of が読み、無い・読めない agentd は参加しない(runtime.settings_from_env)— 家(profile の置き場)の数から導かない(資格は預かり所の貸与)。作れない・揃えられない拍(書き手の断り等)は1 度だけ log して次の heartbeat で撃ち直し、揃えられなくても lease は書く(参加の生存を spec の書きの成否に結ばない)。契約 agora-kinds.json の node の writers.create / update = agentd(withdraw は acp-scheduling)。指紋(段 10 lane 10y・agora-redesign #110・依頼者の裁定 2026-09-15): node の capacity は ACP の kind node の declaredByFile(ACP 段 10 lane 10t 便 1b)で、誕生を含む書きは header x-declaration-sha256 が要る — agentd は読んだ宣言 file(描いた写し)の bytes の sha256 を composition root(runtime.read_join_declaration)で読み、JoinDeclaration.sha256 → JoinSpec → env DOEFF_AGENTD_DECLARATION_SHA256(形の検は join.declaration-sha256-of の 1 点)→ AgentdSettings.declaration_sha256 と運び、join-tick の AcpCreate / AcpPutSpec が header で運ぶ(handlers.AcpHttp の書きごとの header・status の書きは運ばない)。宣言 file の無い agentd は指紋を持たず、新しい行を作れない(ACP の 403 declaration-needs-fingerprint)。作業場の根(段 10 lane 10y・依頼者の裁定 2026-09-15 案 C・既知の形 = volume topology の先読み): 宣言 file の [agentd].work_roots(flag --work-roots・, 区切りの 1 つの文字列 — 形の検は join.work-roots-of の 1 点: 各根は ~/ か / で始まり / で終わる)を env DOEFF_AGENTD_WORK_ROOTS → AgentdSettings.work_roots と運び、judgment.node-work-roots-of の 1 点で spec.workRoots(宣言の順の list)に誕生と揃えの両方で名乗る。宣言の無い agentd の行に欄は書かない(読み方は配車の側 — ACP の契約と Scheduling)。2026-09-13 までは仮の道具 register-node(札 acp-scheduling)を人が撃ち、pod の名が変わるたびに手で作り直し、pool の capacity 0 は CR の註の写しだった(実測 #85)。")
     (rule R29 "割り込みの約束 = 期限までに model が読む(段 10 lane 10n・agora-redesign #93・operator 2026-09-14 逐語 \"メッセージについてはキューするか割り込みするかっていうオプションがあるはずなのに、キューしか実装されてないんじゃないかっていう疑いがあって、ちゃんと割り込みできるように設計してほしい\"・既知の形 = cooperative cancel → hard cancel の 2 段・actor への signal): (1) 注入の行の名 = Message の id — agentd は SessionInterject の ref に Message の id を渡し、host は session.send の params.ref を器へ運び、claude の Dialogue.inject は user の行の最上位の uuid に写す(実測 conformance/interrupt-physics.md 2026-09-14: CLI は command_lifecycle でその綴りの運命を名乗る — queued / started〔model が読む拍〕/ completed / cancelled / discarded / refused。uuid は UUID の形でなくてよい)。(2) 読んだ証拠 = 材料の中の command_lifecycle started(claude)/ 止めた後の turn/started(codex — 注入の段が無く名も無いので未読を全部)で、judgment.claude-deltas-of / codex-event-deltas-of が kind system の entry と DeltaBatch.interrupt-reads にし、agentd.stream-records が judgment.interrupt-reads-of の 1 点で memory に写す。『注入の後に assistant の出来事が在る』は証拠にしない(実測: 道具の無い生成の途中の注入は畳まれず、次の手番になる — 偽陽性)。(3) 期限 = job の charter.interruptEscalationSeconds ちょうど(judgment.escalation-seconds-of-charter の 1 点・依頼者の追補 2026-09-14: 方策の行の値を Messaging の Plan.charterFor が会話の宣言で重ねて写す)— agentd は方策の行も会話の行も読まず、code に既定の定数を置かない。無い job は注入だけにして、渡した印と同じ 1 回の書きで条件 InterruptEscalationUndeclared を行に足す。(4) 注入から期限が経って読んだ証拠も止めた印も無い id が在れば(judgment.interrupts-due-for-escalation の 1 点)agentd.settle-interrupts が SessionEscalate(session.escalate)を 1 度出し、未読の id 全部に止めた時刻の印。host の headless-escalate-program は Dialogue.escalate の 1 点で判断(queued の注入が無い・既に出して答え待ち・手番が走っていない → 型付きに断る)、claude = control_request interrupt・codex = 出す物が無い(inject が turn/interrupt で止めて渡す)・tui = 断る。(5) 停止の合図の後の result(is_error・error_during_execution — interrupted という subtype は無い)は止めた段の終わりで手番の終わりではない: ClaudeDialogue は control_response の still_queued に注入の uuid が名指されていれば result を飲み(in_flight のまま・CLI が注入の行を同じ session の次の手番として即座に走らせる — 実測 6 ms・codex の inject と同じ扱い)、無ければ interrupted として報告する。停止の合図を出していない result の時点で queued のままの注入も同じ(CLI が次の手番として走らせる)— 走らずに終わった(cancelled / discarded / refused)拍に手番の終わり。agentd の deltas-of は control_response(still_queued)を kind system の entry にし、同じ材料の続く is_error の result も kind system(誤りではない)。(6) 印は行の status.interruptsRead {id: 証拠の seq} / status.interruptsEscalated {id: ms}(書き手 agentd・additive・append-only の map — judgment.interrupt-marks-status-of の 1 点)へ agentd.record-interrupt-marks が CAS で写し、断られた拍は memory の dirty で持ち越す。拾い直し(recover-job)は行の interruptsDelivered − interruptsRead − interruptsEscalated を拾い直した時刻から数える(judgment.recovered-interrupts-of)。(7) 受け取りは watch: AcpWatchSse が changed で即座に拍を起こし、同じ拍の window の読み直しが interrupts を cache に載せ、deliver-interrupts が同じ拍で渡す(拍の周期は保険)。(8) node の status.capabilities[kind].interrupt = steer-then-stop(claude)| stop(codex)(effects.AGENT-INTERRUPT-CAPABILITY・契約 agora-kinds.json)— 面の文言はこれに従う。")
     (rule R30 "手番の資格は引換券で借り、置き場を名乗り、手番ごとに運ぶ(段 10 lane 10d 便 2・agora-redesign #85・依頼者の回答 問 1〜8 と追補 2 / 3・既知の形 = HashiCorp Boundary の controller〔口座の見出しと貸与〕と worker〔封じた資格〕の分離): (1) 借りは 2 段 — agentd は宣言された預かり所(join の [custody].url = **master**・既定の宿を発明しない)へ POST /lease/{kind} で頼み、答えの引換券(voucher)と口座の worker の基点(workerUrl)を受け、その worker へ POST /redeem で引換券を札に換える。札は master を通らず、引換券は一回限りで期限は貸与の hold ちょうど。どちらの段の断りも LeaseRefused でそのまま呼び手へ(409 の hold は master の答えの holdExpiresAt から)。判断は handlers.CustodyHttp._borrow の 1 点で、宣言の無い呼びは 503(_UNDECLARED)。(2) 機体は自分が仕える置き場の**集合**を宣言する(段 11 lane 11u・agora-redesign #224・依頼者の裁定 2026-09-16 = two-way door — 1 値の place は退役: 1 値では会社 Mac が会社と個人の両方の worker として寄与できず、company と名乗れば個人の手番が落ち〔実弾 2026-09-15 01:3x〕・personal と名乗れば会社の profile を配れる node が 0 だった)— join の [agentd].places / --places / DOEFF_AGENTD_PLACES(, 区切り・語は閉語彙 effects.AGENTD-PLACES = company | personal・ACP の契約 agora-kinds.json v4 の node.spec.places.items = profile.spec.boundary と同じ綴り)を join.places-of が読み(無い・空・語彙の外・重複は参加しない)、node の型つきの欄 spec.places(語の list・judgment.node-places-of)と写し labels.places(, 区切りの 1 文字列・deprecated・judgment.node-labels-of)に名乗る。退役した 1 値の spec.place / labels.place は書かず、旧い行を揃える写しで落とす(配車は place を持つ行を行の誤りとして断る)。1 値の宣言を 1 要素の集合と読み替える互換は置かない。claim の頭で、結ばれた口座の profile の行が名乗る置き場(spec.boundary)が判っていて自分の集合に**無い**時だけ job を CredentialPlaceMismatch で閉じる(judgment.credential-place-mismatch — 両向き・判らない側が在る拍は止めない。最後の門は預かり所の側に在る)。(3) 手番ごとの札は行に残さず、その手番の送りが運ぶ(追補 2・実弾 #92 = 預かり所が口座を更新した拍に、温かい session の再開の手番が誕生時の access token を使い回して 401 revoked): agentd は借りた札を SessionSend.session_env に載せ(judgment.turn-session-env-of の 1 点・claude は CLAUDE_CODE_OAUTH_TOKEN・codex は家の中の auth file が運ぶので空)、host の session.send は params.session_env を launch と同じ関所(policy.session-env-admission-error)に通してから headless-send-program へ渡し、降りた process の起こし直し(continue-headless-process)は行の誕生の env にこの手番の env を重ねて起こす。行へ永続化する launch の意図からは手番ごとの札を落とす(policy.overlay-without-turn-auth — 行にも log にも値を残さない)。手番ごとの env を運べない組み合わせ(tmux の器・mode = interrupt)は黙って落とさず型付きに断る。(4) 手番の CLI は agentd の env を継がない(追補 3・実弾 #95): 子 process が機体から継ぐ env は policy.inheritable-spawn-env の名簿(場所・家・地域・証明書・proxy・ssh の agent)だけで、会話ごとの値は charter(session_env と binding 由来の auth env)が運ぶ 1 点に閉じる — ACP_* / DOEFF_* / AGORA_BORROWER_KEY_PATH / AGORA_CUSTODY_URL / RECORD_SERVICE_URL は届かない。")
     (rule R31 "郵便の添付は型つきで器へ・CLI の綴りは Dialogue(段 10 lane 10o・agora-redesign #96・operator の問い 2026-09-14「もしかして upload_image も conversation で未実装か」・依頼者の追補 2026-09-14 21:5x): (1) 郵便の行は添付の**見出し**だけを運ぶ(message.spec.attachments = {ref{conversation, stream}, seq, mime, bytes, sha256, name?}・中身は記録の service — claim check)。agentd は本文と同じ 1 回の stream の読み(agentd.mail-bodies-by-ref)で添付の出来事(kind attachment・producerSeq = 見出しの seq・0 は本文)も拾い、judgment.attachment-of が見出しの mime / bytes / sha256 と食い違わない時だけ型つきの値(sessionhost.attachment.TurnAttachment)にする。⚠ **見出しの bytes / sha256 は画像の生の byte**(差出人の client の attachments-plan が測る材料・ACP の法 89ce1f)で、記録の service の出来事が名乗る bytes / sha256(本文の欄の compact JSON を測った物)とは**別の値** — base64 を解いた生の byte で比べる。実弾 2026-09-15 02:39(本番の e2e chat.send-image): 出来事の値と比べていたので必ず食い違い、log に『attachment 1 of message … could not be read from the record service』が出て、画像が 1 枚も CLI へ渡らないまま手番が条件なしで終わっていた(黙って画像だけが落ちた)。(食い違い・欠落は値を作らず、呼び手が条件 AttachmentIgnored)。本文と添付の並びは judgment.message-bodies-of の**同じ 1 つの述語**で作る(2 つの関数に分けると並びがずれる)。(2) agentd は添付を SessionSend / SessionInterject の型つきの欄で host へ渡すだけで、CLI の綴りを 1 語も持たない — acp/agentd.hy と acp/judgment.hy に \"image\" / \"media_type\" / \"source\" の綴りは無い。綴りの座は sessionhost/headless_protocol.py の kind ごとの Dialogue ちょうど(R21 と同じ形): claude = Messages API と同じ content の block(claude_image_block)・codex = turn/start の input の項(codex_input_items)。どちらも便 1 の実測(conformance/attachment-physics.md 2026-09-14)の綴りで、codex は data URL と localImage が API へ同じ input_image になるので一時 file を作らない。(3) 起こす腕(first-turn-carries-inputs)は郵便を 1 手番目に畳むので、その郵便の添付も同じ 1 手番に載る。⚠ charter は **RPC へ出る object** なので、載せるのは型つきの値ではなく**項の綴り**(sessionhost.attachment.attachment-wire / attachment-of-wire の 1 点 — agentd の wire の handler・起こす charter・host の解きの 3 か所が同じ座を使う)。実弾 2026-09-15 03:08: 型つきの値のまま charter に入れていたので、RPC へ出す拍に `TypeError: Object of type TurnAttachment is not JSON serializable` で tick ごと落ち、手番が SessionFailed(session is not registered in the host)で終わっていた — 画像だけでなく**本文も届かない**。偽の器(fake の _incarnate)も本物と同じく params を JSON にして受ける(検が本番と同じ拍で落ちる)。⚠ 郵便を畳む腕は **launch / resume / rehydrate の 3 つ**で、resume は charter の欄を**名簿で**写す(resume-params-of)ので、足した欄は名簿にも足す — host の session.resume と launch.hy の resume が組む launch-params も同じ。実弾 2026-09-15 09:5x(operator の会話 c-01M1XGMDHR35FBBC04W1JXM5KJ): attachments を 3 か所とも運んでいなかったので、**腕が resume の手番だけ**画像が黙って落ちていた(誤りも条件も出ず、手番は条件なしで終わる)。見落としの根は検が launch の腕しか通していなかったこと — 針は 3 つの腕を全部通す(judgment.first-turn-attachments-of → launch-charter-with-attachments → host の session.launch → headless-deliver → Dialogue.turn)。(4) 添付の段を持たない器(tui = tmux / herdr の pane へのキー配送)は host が断りを答えに名乗り(attachmentsIgnored)、agentd が条件 AttachmentIgnored(effects.CONDITION-ATTACHMENT-IGNORED)に写す — 黙って落とさない。本文そのものは届く。(5) node の status.capabilities[kind].attachments = 受ける添付の種類の語の列(effects.AGENT-ATTACHMENT-CAPABILITY・今日は claude も codex も image・欠落 = 何も受けない)。(6) 綴りは偽 CLI(tests/headless_stubs の claude / codex)が凍結する: 綴りの違う block / 項は RuntimeError で落とす(実物は黙って劣化して success を返すので、替え玉が誤綴りを検で赤にする役を持つ)。")
     (rule R32 "手番の作業場はその node の家で読み、無ければ起こさない(段 10 lane 10y・agora-redesign #110・依頼者の裁定 2026-09-15 案 A・既知の形 = k8s の volume の topology〔資材の在る node にだけ結ぶ〕): (1) charter.work_dir(綴りの定義点は契約 agora-kinds.json の delivery-policy.spec.charter)は絶対 path か家からの相対(`~` / `~/…`)で、agentd は claim の頭で judgment.plan-with-node-home の 1 点により `~` だけを AgentdSettings.home(composition root が env HOME から据える)で展開し、以降の判断と起こす params は展開した plan を読む。(2) 展開した work_dir を effect FsDirectoryExists で読み、段は judgment.work-dir-step-of の 1 点(閉語彙 effects.WorkDirStep = launch | create | missing): 在る(か宣言が無い)= 起こす / 無いが charter.work_dir_scratch が bool の true = FsMakeDirectories で作ってから起こす / 無い = 起こさず(claim も借りも launch もせず)条件 WorkDirMissing(reason に node の名と work_dir)で Ended に閉じる(agentd.work-dir-ready)。印の無い work_dir を作らない — repo を指す作業場を空の dir で偽装しない。(3) 配車の係はこの条件を会話 × node で読み、同じ会話の手番の候補からその node を外す(ACP 側・lane 10d)。sessionhost の launch の work_dir の検(ADR-DOE-AGENTS-006 R10)は最後の門として残る。実弾 2026-09-15 02:54: delivery-policy current の charter.work_dir = /Users/s22625/.cache/acp-stage2-e2e/work(会社 Mac の絶対 path)の手番が proboscis-mbp(user kento)に結ばれ、本物の会話の手番 2 本が LaunchFailed に落ちた。")
     (rule R10 "session は会話の資源・job は手番(温かい session・設計 17.4): 会話 → 生きている session の対応は行(自分が claim した同じ subject の agent-job の sessionHandle)と器の現況から導き、Bound の job の起こし方は judgment.hy の next-arm-for-job(閉語彙 effects.NextArm = launch | send | resume | rehydrate | defer — 家と機体の扱いは R20)の 1 点で決める — 同じ会話の生きて idle な session が在れば launch せず session.send(awaiting)だけ、sessionHandle はその session を指し、turn-record は手番ごと。手番の終わりは器の lifecycle multi_turn(launch.hy の閉語彙に足した語)で policy.hy の monitor が既存の turn-end の連言から行の turn_ended_at に刻み、agentd は job-step-of の turn-end(turn_ended_at > 手番の始まりの下限 ∧ 記録の進み)で読む — status は倒さず session は生かす。idle の寿命は AgentdSettings.session_idle_ttl_seconds の 1 点で、超過・Withdrawn・node の退役で session.cleanup。計器 agent-job-to-send は create → send のまま(温かい path で p99 < 2 秒)。")
     (rule R33 "provider の限度の断りは器の終端の cause で残し、制御面はその欄を読む(段 11 lane 11n 便 C・agora-redesign #179・依頼者の裁定 2026-09-15 案 c′・既知の形 = runner が結末を書き control plane が欄を読む): CLI が『限度に達した』と断った拍、器(session)は温かいまま status running で残り、agent-job は result も条件も無い Ended で終わっていた —— 実弾 2026-09-15 13:2x、会話 c-01M1XGMDHR35FBBC04W1JXM5KJ の手番が profile btc で Fable の限度に 5 回当たり、行には『どの model が枯れたか』が 1 bit も残らず、予算の判断へ戻る道が無かった。⇒ (1) **CLI が名乗った文を落とさない**: claude の Dialogue の result の行の手番の終わりの detail は result の本文ちょうど(subtype〔error_during_execution〕は文が無い時の名前)—— headless_protocol.ClaudeDialogue._on_result の 1 点。(2) **族の表を当てるのは器の側の 1 点**(headless.headless-turn-limit-cause): 手番の終わりの verdict(ok = False・detail = CLI の文)に impls/markers.hy の has-api-limit-marker を当てる。表の家は markers.hy のまま(ADR-DOE-AGENTS-008 R1・pane の路の policy.action-terminal-cause / failed-output-cause が PaneObservation 経由で引く**同じ表**)で、写しは作らない。(3) 当たった手番は **session ごと終える**: 行は status failed + cause {category: rate_limited(policy の TERMINAL-CAUSE-CATEGORIES の語)・reason: CLI の文}(make-cause / cause-if-absent の既存の口)。温かいまま残すと、限度は口座 × model のものなので同じ profile の次の手番も断られる(5 連敗の形)。配車は model 別の枯渇(段 11 lane 11m)で別の profile へ移り、profile が変われば器はどうせ作り直し(restartOn = model・profile)。(4) 制御面(agentd の ACP の腕)は **cause の欄だけ**を読み、CLI の文にも族の表にも触らない: judgment.provider-limit-condition-of の 1 点が SessionView.terminal-cause の category = rate_limited から agent-job の status.conditions の 1 項 {type: ProviderLimit, status: True, reason: rate-limited, model?, message} を作り、settle-record が Ended の書きに足す(SessionFailed 等の他の書き手の条件は置き換えない)。model は**手番が走らせようとした model**(InFlightJob.model = charter.model)ちょうど —— 限度の拍の usage.model は `<synthetic>` で材料が名乗らない(effects.MODEL_UNDECLARED の手番は欄を落とす)。until は書かない(窓を知るのは予算の controller)。**status.result には書かない** —— result が在ることは『手番が結果を報告した』の意味で、await の終端の別(Acp.App.Agent.AgentJob.awaitOutcomeOf)が反転する。(5) 綴りは effects の 1 点(CONDITION_PROVIDER_LIMIT / REASON_RATE_LIMITED / CAUSE_CATEGORY_RATE_LIMITED〔policy.hy の語の写し — SESSION_TERMINAL_STATUSES と同じ扱いで、投影できない Hy の語を agentd が読むために写す〕)で、ACP docs/contracts/scheduling.json はその写し。読み手は予算の controller(agora-budget)で、窓の観測より新しいこの印を『観測できない時の枯渇の証拠』として model 別の枯渇の判断に足す。")
     (rule R34 "上限で落とした古い手番は黙って捨てず、落とした区間を見出し 1 行に畳む(段 11 lane 11v・agora-redesign #55 便 1・依頼者の裁定 2026-09-16・既知の形 = 有界の文脈の圧縮の決定的な段〔model の要約の前に置く〕): 履歴からの再開(R20)の畳み judgment.rehydrate-history-of が AgentdSettings.rehydrate_history_byte_budget を超えて古い手番を落とす時、落とした区間(古い手番の連なり)を**見出し 1 行**(judgment.history-dropped-headline の 1 点: 期間〔区間の最初の項の時刻〜最後の項の時刻〕・落とした手番と項の数・kind ごとの件数〔郵便は kind 郵便 = effects.HISTORY_MAIL_KIND で数える〕・道具の名〔初出の順〕・全文の在処)に畳み、残した手番の**前**(時刻順の位置)に置く。件数と道具の綴りは薄い再開の turn-record の見出しと同じ 1 点(judgment.history-counts-note — 形は effects.HeadlineCounts・材料の 1 項は effects.HistoryItem)。見出しは落とした手番ごとではなく区間に 1 行(見出しが上限を食わない)。見出しも上限の中に数え、最新の手番 1 つ(と頭・見出し)だけでも超える時はその先頭を切って切った byte を名乗る(judgment.history-cut-notice・HistoryFold.cut_bytes)。答え HistoryFold は見出しを欄 dropped_headline で運び(落とさなければ None)、agentd は起こす拍の log に見出しを 1 行(受入 = 本番の履歴からの再開で最初の本文に見出しの行が入り byte が上限の中)。model による要約は置かない(要約の作り手・上限・費用は one-way door — #55 便 2 で設計だけ・operator の判断)。方策の定義点は上限の 1 点のまま(見出しの上限を別に宣言しない)。")
     (rule R35 "上限を超えたら、手番を落とす前に古い手番から道具の項を薄くする(段 11 lane 11v 便 3・agora-redesign #225・依頼者の裁定 2026-09-16・既知の形 = 有界の文脈の段階的圧縮〔捨てる前に薄く・要約の前に決定的〕): judgment.rehydrate-history-of の畳みは 3 段 — 段 1 = 古い手番から新しい手番へ(最新の手番は最後)、道具の項(tool_use の入力・tool_result の本文)だけを先頭 k byte に薄くして「(先頭 k byte だけ・元 N byte)」と名乗る(judgment.history-event-thin-line → history-thin-body・綴りは全文の項と同じ history-event-line-of)。k = budget / effects.HISTORY_THIN_DIVISOR(65,536 なら 256)— 上限の宣言からの比で、AgentdSettings に 2 つ目の欄は置かない。郵便・agent の text・user / system / error(会話の意図と結論・文脈)は 1 byte も変えない。薄くなる項の無い手番(短い本文・薄い再開の見出し)は薄くした数に数えない。段 2 = 全部を薄くしても超える時だけ古い手番から落とし、落とした区間を見出し 1 行に畳む(R34)。段 3 = 最新の手番の先頭を切る(R34)。答え HistoryFold.thinned_turns・agentd の log に `M thinned`。決定的で model を呼ばない・費用 0。model による要約(#55 案 D)は one-way door で operator の判断のまま。")
     (rule R36 "charter.kind = verify の job は会話の手番ではなく機体の script を 1 つ走らせる命令で、claude / codex を起こさず札も借りない(段 12 lane 12a・agora-redesign #230・依頼者の裁定 2026-09-16 = two-way door・既知の形 = runner が job の種類で腕を分け、control plane は結ぶ先だけを決める〔k8s の CronJob → Job → node の kubelet が pod を走らせる形の、pod の代わりに script〕): 会社 repo(mediagen / orch / proboscis-ema / vibe-video-platform)の日次の全体検証は会社の資格でしか clone できず、その資格は家の cluster に置けないので k3s の pod では撃てない。契機の k3s の CronJob が ACP に agent-job を 1 つ書き(charter.kind = verify・charter.place = company・ACP 契約 docs/contracts/scheduling.json charterKind)、配置が charter.place を spec.places に名乗る node(会社 Mac)に結び、その agentd が担う。(1) 種類の読みは judgment.job-kind-of の 1 点(charter.kind の語・無い = turn)で、agentd.claim-job は**作業場の門・資格の出所・置き場の照合・起こし方の判断より前**に種類で分岐し、verify は agentd.claim-verify-job へ(手番の腕は 1 行も歩かない)。(2) 走らせるのは機体の家(AgentdSettings.home)の effects.VERIFY_SCRIPTS_RELDIR(dotfiles/cron_management)/<charter.jobId>.sh ちょうど — **命令の文字列は行から運ばない**(herdr-hud D0626 決定 2: cluster 側から機体へ任意の命令を流せる口を新設しない)。jobId は effects.CHARTER_VERIFY_JOB_ID_PATTERN(小文字英数字と - ・path の要素にそのまま使える形)ちょうどで、綴りの外・script が機体に無い(FsFileExists)id は起こさず条件 VerifyScriptMissing で Ended(知らない id は loud に落とす・推測しない)。judgment.verify-plan-of の 1 点が読む。(3) 起こし方は judgment.verify-argv-of の 1 点 = sh の 1 行(`echo $$ > pid && script >> log 2>&1; echo $? > rc` — path は位置引数 $0〜$3 で運び文字列に埋めない)を effect CommandStart が**自分の session で**(start_new_session)起こして待たない。結末は 3 file(AgentdSettings.verify_runs_dir/<job id>.{log,rc,pid} — 置き場は join の state_dir の下・runtime.verify_runs_dir の 1 点)。Running の書きは sessionHandle{stream{owner, name = job id}, verify{jobId, runKey, startedAtMs, deadlineSeconds, scriptPath, logPath, rcPath, pidPath}}(judgment.verify-handle-of / verify-running-status-of・binding は触らない)。(4) 観測は行と file から毎拍(R7): effect CommandProbe(rc の file → Exited / pid の生死 → Running | Gone)→ judgment.verify-step-of の 1 点(閉語彙 effects.VerifyStep = observe | ended | lost | timed-out)。ended = rc を result{kind: verify, jobId, runKey, rc, startedAtMs, endedAtMs, durationMs, log}(judgment.verify-result-of)に写して Ended — **赤(rc != 0)も結末で条件ではない**(赤の判断と記帳は ai land verify が land-partition の spec.verify に書いている・repair の依頼は作らない)。lost = 条件 VerifyCommandLost(result なし)。timed-out = charter.deadlineSeconds(0 = 期限なし)を越えた命令を CommandStop(SIGTERM を process group へ)で止め条件 VerifyDeadlineExceeded。agentd の再起動: process は自分の session で残るので降ろさず(close-jobs-for-stop は log 1 行)、次の agentd が Running の行の sessionHandle.verify と pid の file から組み直す(agentd.recover-command・judgment.verify-plan-of-handle — 起こし直さない・組めない行は VerifyCommandLost)。取り下げ(Withdrawn)は judgment.withdrawn-command-rows-of(verify の行は sessionId を持たないので withdrawn-rows-of の外)→ CommandStop + 条件 Interrupted(phase は書かない)。(5) 綴りの定義点は effects の 1 点(CHARTER_KIND_KEY / CHARTER_KIND_VERIFY / CHARTER_VERIFY_* / VERIFY_SCRIPTS_RELDIR / VERIFY_RUNS_RELDIR / CONDITION_VERIFY_*)で、ACP docs/contracts/scheduling.json charterKind.verify.runnerCharter はその写し。turn-record は作らない(会話が無い)・中継へ frame は押さない・預かり所は撃たない(反例 = 検の launches == [] ∧ borrowed == [])。")
     (rule R37 "charter.kind = summarize の job は会話の履歴の段階つき要約 1 つで、会話の profile の札を借りて claude -p を区間ごとに 1 回起こし、要約は記録の service の stream と kind summary の行に書く(段 12 lane 12j・agora-redesign #233・#55 案 D・operator 2026-09-16 逐語 \"lets see if 1 will work\"・依頼者の裁定 = two-way door・既知の形 = virtual actor の履歴の snapshot〔会話〕を durable workflow の 1 種〔job〕が区間ごとに進めて記録する): 契機はこの agentd 自身(手番の終わりに測った文脈の大きさが AgentdSettings.summarize_trigger_tokens = 500,000 を超えた拍 — 便 3)。配置(ACP Scheduling)は手番と同じ資格の路で会話の profile の account を結ぶ(ACP 法 cd258b R-summarize-runs-beside-the-turn-under-the-conversation-credential-b410)。(1) 種類の読みは judgment.job-kind-of の 1 点(verify と同じ)で、agentd.claim-job は作業場の門・資格の出所・起こし方の判断より前に種類で分岐し、summarize は agentd.claim-summarize-job へ(手番の腕は 1 行も歩かない)。(2) 欄の写しは judgment.summarize-plan-of の 1 点: 会話 = spec.subject・上端 = charter.until(recordSeq・含む)・1 区間の上限 = charter.regionByteBudget(無ければ宣言 summarize_region_byte_budget)・model = charter.model(operator の決定 = claude-opus-5・契機が書く値は summarize_model の 1 点)・資格 = binding.profile / account。読めない行は条件 SummarizePlanInvalid で Ended。(3) 区間 = 要約済みの区間(kind summary の行の spec.to の最大 — judgment.summary-rows-covered-to・effect AcpConversationSummaries)の続きから、記録の service を**前向きに**(effect RecordReadSince・since = from − 1・kinds = effects.RECORD_RAW_EVENT_KINDS〔text / tool_use / tool_result / system / error / user — frame・message・attachment・summary は原文ではない〕)読み、judgment.summary-region-of の 1 点が bytes の和 ≥ 上限で閉じる閉区間 [from, to]。原文が無ければ結末 regions 0 で Ended(条件ではない — 作り手の冪等の答え)。(4) 起こし方は judgment.summarize-argv-of の 1 点 = sh の 1 行(pid → `claude -p --model … --output-format json --no-session-persistence --tools \"\" --disable-slash-commands < prompt > out 2>> log` → rc・binary / model / path は位置引数 $0〜$6)を effect CommandStart が自分の session で起こす(env に借りた札 CLAUDE_CODE_OAUTH_TOKEN と家 CLAUDE_CONFIG_DIR = judgment.claude-home-of〔charter-with-grant と同じ綴り〕— effects.CommandStart.env・値は log にも argv にも出さない)。**session は起こさない**(SessionLaunch を撃たない)・turn-record を作らない・中継へ押さない・郵便を読まない。札は区間ごとに借り直し(CustodyLeaseBorrow)、区間が進む時と終わりに返す(CustodyLeaseRevoke)。(5) prompt(残す = 決定・進行中の仕事・未解決の問い・道具の結果の要点・成果物の在処・失敗と回避 / 落とす = 道具の生の出力・繰り返しの経過・挨拶)の**定義点は judgment.summarize-prompt-of の 1 点**で、ACP agora-kinds.json conventions.stagedSummaries.keep はその写し。原文の綴りは履歴からの再開と同じ history-event-line。(6) 結末の読みは judgment.summarize-output-of の 1 点(result の JSON・subtype success・result の本文・usage の 4 欄・modelUsage の model)。本文は記録の service の stream(streamKind summary・id = summary#<from>-<to>・出来事 kind summary・producerSeq 0 — judgment.summary-batch-of)へ RecordAppend し、agora の kind summary の行(id = sum-<会話>-<to>・spec = 会話 / from / to / recordRef / bytes / sha256〔judgment.record-body-bytes-of — service と同じ計算〕/ sourceEvents / sourceBytes / agentJobId・status = current / model / at / usage)を AcpCreate + AcpPutStatus で書く。既在(identity = 会話 × to)は済んでいる扱い。(7) 区間が済むごとに次の区間へ進み(agentd.advance-summary-region — 行の sessionHandle.summarize をその区間に更新)、上端まで済んだら Ended{result: kind summarize / conversationId / until / regions / lastTo}。観測は行と file から毎拍(R7・閉語彙は verify-step-of の 4 語をそのまま使う): lost = SummarizeCommandLost / timed-out = CommandStop + SummarizeDeadlineExceeded / rc != 0・答えが読めない = SummarizeOutputUnreadable / 本文か行を書けない = SummaryUnwritable。再起動は Running の行の sessionHandle.summarize と pid の file から組み直す(agentd.recover-summarize・judgment.summarize-of-handle — 起こし直さない・札は借り直す)。取り下げ = CommandStop + 札を返す + Interrupted(judgment.withdrawn-summarize-rows-of)。(8) 綴りの定義点は effects の 1 点(CHARTER_KIND_SUMMARIZE / CHARTER_SUMMARIZE_* / SUMMARY_* / RECORD_RAW_EVENT_KINDS / SUMMARY_RUNS_RELDIR / CONDITION_SUMMARIZE_* / CONDITION_SUMMARY_UNWRITABLE)で、ACP scheduling.json charterKind.summarize.runnerCharter と agora-kinds.json kinds.summary はその写し。")
     (rule R38 "要約の契機は agentd の手番の終わりの 1 点で、履歴からの再開は要約を原文の前に畳む(段 12 lane 12j 便 3・agora-redesign #233・#55 案 D・operator 2026-09-16「50 % を超えていたら(0.5M)Opus で compact したい」・依頼者の裁定 = two-way door・既知の形 = virtual actor の履歴の snapshot を活性の終わりに積み、再活性は snapshot + その後の出来事を読む): (1) 契機 = judgment.summarize-due の 1 点 — 手番の終わり(agentd.settle-record・文脈の使用率を測る同じ拍)に、材料の末尾の文脈の大きさ(DeltaBatch.context.tokens — 窓は見ない)が AgentdSettings.summarize_trigger_tokens(500,000・0 = 契機なし)を**超えた**時だけ。会話の宣言(compactAt)には依らない — 要約は cache を保てないどの再開にも効く。(2) 区間の上端 until = この手番の stream(record-stream-id-of)の最初の出来事の recordSeq − 1(judgment.turn-floor-of — 記録の service を RecordReadStream で読む)。stream にまだ出来事が無い・記録が読めない拍は書かず次の手番の終わりに測り直す(黙って推測しない)。until < 1(この手番より前が無い)も書かない。(3) 既に kind summary の行が until まで覆っていれば書かない(judgment.summary-rows-covered-to — 作り手の側の冪等)。書くのは agent-job(namespace acp-system・id = effects.SUMMARIZE_JOB_ID_PREFIX + 会話 + until・spec = judgment.summarize-job-spec-of: subject = 会話・inputs = []・charter{kind summarize, agent_type claude, model = AgentdSettings.summarize_model, until}・reason summarize)を AcpCreate で 1 つ。engine の identity((subject, inputs=[]) — 生きた summarize は会話に 1 つ)が 2 本目を断れば log 1 行(条件は書かない — 手番の結末に要約の都合を混ぜない)。書けた拍は log 1 行 + 計器 agentd_summarize_triggers_total{conversationId, until, agentJobId}。(4) 履歴からの再開(agentd.history-for)は先に要約を読む(agentd.summaries-for の 1 点: kind summary の行 = effect AcpConversationSummaries を 1 回・本文 = spec.recordRef の stream を RecordReadStream で・state superseded / 読めない行は名乗って飛ばす = その区間は原文で)、原文の読み(agentd.record-turns-for)は要約が覆う区間の終わり(judgment.summary-floor-of = to の最大)より新しい出来事だけ(頁が floor に届いたら止め、floor 以下は落とす — 読まない = 要約が担う)。畳み(judgment.rehydrate-history-of)は要約(effects.HistorySummary)を**原文と郵便の前**に区間の順で 1 区間 1 段(judgment.history-summary-line — 区間の recordSeq と model を名乗る)として置き、頭が要約の数を名乗る。要約は道具の項ではないので薄くならず、原文と別の前置きで時刻の並びには入れない(recordSeq を時刻に読まない)。上限では**原文の手番を先に**落とし(最新の 1 手番は残す)、それでも超える時だけ古い要約から落として見出し(kind = effects.HISTORY_SUMMARY_KIND)に数え、頭の要約の数も減らす(追補 5 — 要約は既に圧縮された履歴で byte あたりの価値が原文より高い。便 4 の実射 2026-09-16 18:08 aj-88JXQX…: 旧の順では上限 65,536 byte・原文 1,500 出来事・要約 4 本のとき要約が先に全部落ちて本文に 1 本も残らず、見出しの期間は recordSeq 0 を時刻に読んで 1970 年から始まった)。見出しの期間は落とした原文の時刻だけで数え(judgment.dropped-headline-counts)、頭は judgment.history-header-of の 1 点。HistoryFold.dropped_summaries が落とした要約の数を運び、agentd の log に出る。(6) 追補 6(実射 2026-09-16 18:45 aj-X92PW3ZHGW36ZCQWR2ZZPJNACS: 落ちた 77 手番の大半が郵便 105 通で上限を食っていた): 要約が覆う記録の終わりの時刻 = recordSeq floor の出来事の at(agentd.history-for が RecordReadSince since floor−1・limit 1・kinds なしで 1 読み・読みは judgment.summary-floor-at-of の 1 点・欠番や不達は None)より古い郵便は畳まない(要約がその期間の郵便も読んで書いている)— HistoryFold.summarized_mails に数え、agentd の log に「N mails left to the summaries」。None は今日どおり全通。要約が無い会話は今日どおり(HistoryFold.summary_regions = 0)。agentd は起こす拍の log に要約の数を名乗る。(5) 綴りは effects の 1 点(METRIC_SUMMARIZE_TRIGGERS_TOTAL / HISTORY_SUMMARY_KIND / SUMMARIZE_JOB_ID_PREFIX)で、ACP agora-kinds.json conventions.stagedSummaries(triggerTokens・rehydrate)はその写し。")
     (rule R39 "停止(SIGTERM)の前の排水(段 12 lane 12j・agora-redesign #304 便 2・依頼者の裁定 2026-09-16 20:3x「(a)」・#216 案 B を覆す側の機構・既知の形 = kubelet の drain〔cordon → 走っている pod の終わりを待つ〕): node が宣言 file の [agentd].drain_seconds(> 0・join が DRAIN_SECONDS_ENV で運び、AgentdSettings.drain_seconds に据わる)を名乗っていれば、agentd の停止の腕(runtime.AgentdRun.close_for_stop)は走っている job が在る時、閉じる前に drain の合図を立て(loop は settings.draining = True で回り続ける)、走っている job が全部終わるか上限に届くまで待つ(runtime.drain_until の 1 点)。排水の最中は (1) 新しい claim を受けない — 結ばれた Bound の行は claim せず log 1 行(agentd.receive-bound-jobs)、(2) node の spec.capacity を 0 に名乗る(judgment.declared-capacity-of の 1 点 — node-spec-of / node-spec-declared が読む)ので配車は新しい手番をこの node に結ばない、(3) lease の heartbeat は続く(走っている手番は結ばれたまま観測される)。上限に届けば残りを今日どおり AgentdRestart で閉じる。宣言 0 / 無し = 今日どおり即座に閉じる(Mac の据え直しは変えない)。pod の terminationGracePeriodSeconds は drain_seconds より長く取る(k8s の SIGKILL が先に来ない)— 値の置き場は ACP の CR(agentPools[].pod.terminationGracePeriodSeconds)と ConfigMap agentd-pool-join。")]
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
     (law interrupts-are-read-within-the-deadline-or-the-turn-is-stopped
       :statement "for_all Running job j run by this agentd and interrupt mi handed by SessionInterject(body(mi), ref = mi): (a) the CLI names mi's fate by command_lifecycle(mi) and read(mi) := the seq of the first `started` (claude) or of the first turn/started after the hand-over (codex), written to status.interruptsRead[mi]; (b) if charter(j).interruptEscalationSeconds = T is declared and now − injected(mi) ≥ T·1000 with no read(mi) and no escalated(mi), exactly one SessionEscalate(session(j)) is sent and status.interruptsEscalated[m] := now for every unread m; (c) if T is undeclared, no signal is ever sent and conditions(j) ∋ InterruptEscalationUndeclared; (d) after the signal, the CLI's result that ends the stopped stage is not a turn end while still_queued ∋ mi — the injected line runs as the next CLI turn of the same session and j stays Running; (e) agentd reads T from the charter only and holds no default"
       :counterexamples
         [(counterexample "『注入の後に assistant の出来事が在る』を読んだ証拠にする形: 道具の無い生成の途中に注入した行は畳まれず、assistant は注入と無関係の本文を出してから result になる(実測 2026-09-14 第 1 走)— 読んでいないのに『読んだ』と印が付き、停止の合図が出ない。証拠は CLI が名乗る command_lifecycle started だけ")
          (counterexample "注入の行に uuid を付けない形(今日の inject): CLI は lifecycle を名乗らず、読んだ拍が判らない — 期限の判断が『時間が経った』しか持たず、境界で読まれた直後の手番を止めてしまう")
          (counterexample "期限の既定を agentd の code に置く形(既定 20): 方策の定義点が方策の行と code の 2 つになり、方策を変えても機体ごとに古い既定で止める。charter に無い job は注入だけにして条件で名乗る")
          (counterexample "agentd が方策の行(delivery-policy)や会話の行を読んで期限を決める形: 判断の点が Messaging の charterFor と agentd の 2 つに割れ、会話の宣言の重ね方が 2 か所で食い違う。運搬路は charter の 1 本")
          (counterexample "停止の合図の後の result(is_error)を手番の終わりとして報告する形: host が turn_ended_at を刻み agentd が job を Ended にし、注入の行の手番(INTERRUPTED-ACK)が誰の job でもない手番になる(実測: 停止から 6 ms で次の手番が走る)。still_queued に名指された注入が在れば result は飲む")
          (counterexample "still_queued を読まずに result を常に飲む形: abort の瞬間に畳みの途中だった注入は次の手番にならず(bundle の記述)、host は永久に手番の途中のまま。答えを読んで、生き残りが無ければ interrupted で終える")
          (counterexample "停止の合図を拍ごとに撃ち直す形: control_request は手番ごとに 1 度で足りる(queued の注入を全部次の手番に運ぶ)— 二度目は次の手番(注入の行の手番)を止めてしまう。合図は session に 1 つ・答え待ちの間は出さない")
          (counterexample "codex に停止の合図を出す形: 注入の段が無く inject が turn/interrupt で既に止めている — 二度目の turn/interrupt は注入の行の turn を止める。codex の能力は stop で、読んだ証拠は turn/started")
          (counterexample "interrupts を拍の周期(transcript の周期)で polling する形: 載せてから注入まで拍の分だけ遅れる。watch の changed で拍を起こし、同じ拍で渡す(目標 1 秒以内)")]
       :enforcement ["docs/adr/defadr_doeff_agents_012_agentd_acp_arms.hy::test-adr-doe-agents-012-interrupts-are-read-within-the-deadline"
                     "packages/doeff-agents/tests/test_sessionhost_acp.py::test_interrupt_is_injected_with_the_message_id_as_its_name_and_read_at_the_boundary_is_recorded"
                     "packages/doeff-agents/tests/test_sessionhost_acp.py::test_interrupt_unread_past_the_deadline_escalates_once_and_the_next_turn_carries_it"
                     "packages/doeff-agents/tests/test_sessionhost_acp.py::test_interrupt_without_a_declared_deadline_is_injected_only_and_names_the_condition"
                     "packages/doeff-agents/tests/test_sessionhost_acp.py::test_codex_interrupt_is_read_at_the_turn_started_after_the_stop"
                     "packages/doeff-agents/tests/test_sessionhost_acp.py::test_interrupt_judgments_are_pure"
                     "packages/doeff-agents/tests/test_sessionhost_headless.py::test_claude_dialogue_escalates_an_unread_injection_and_the_next_turn_carries_it"
                     "packages/doeff-agents/tests/test_sessionhost_headless.py::test_claude_dialogue_escalation_without_survivors_ends_the_turn_as_interrupted"
                     "packages/doeff-agents/tests/test_sessionhost_headless.py::test_claude_dialogue_keeps_the_turn_while_an_injection_is_still_queued_at_the_result"
                     "packages/doeff-agents/tests/test_sessionhost_headless.py::test_codex_dialogue_does_not_escalate"
                     "packages/doeff-agents/tests/test_sessionhost_headless.py::test_headless_process_claude_escalate_stops_the_tool_and_the_injection_runs_next"
                     "packages/doeff-agents/tests/test_sessionhost_headless.py::test_host_headless_escalate_stops_the_turn_and_keeps_awaiting"])
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
       :statement "for_all written_file w: token ∉ w unless w = <homes>/codex/<account>/auth.json — 借りた札は家の中の auth file 以外の平文に残らず、log / 計器にも出ない(対象は**預かり所が貸した札**ちょうど — 受け手自身が自分の家に置いた従量課金の資格は借り物ではないので対象外。註 2026-09・R5 参照)"
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
       :statement "at each period profile_observe_seconds agentd reads the homes of this node once (ListProfileHomes = the agentcli registry × the existence of each profile's dir) and keeps only the live profile rows p (state ≠ retired) whose home is present and, unless the node's verified ownership grade is company, whose spec.boundary is not company (judgment.profile-rows-held — a node owned personal or declaring no ownership never holds a company account row, so neither the usage read nor the log names one); when that set is empty it issues NO ReadProfileUsage, logs 'no profile has a home' exactly once (AgentdState.no_profile_homes_logged, reset when a home appears) and still emits the profile-observed metric with homes = 0; otherwise, for_all such p: agentd issues exactly one ReadProfileUsage and, for p held by this node with a readable usage u and budget.unit = percent, writes status.observed(p) = {window = observed-window-of(p), remaining = 100 - used(u, window), resetAt, observedAt = captured(u), node = self} over the committed status (state and conditions preserved) with ifGeneration, only when it differs from the committed observed; refused / failed usage (ProfileUsageUnavailable — decided by the agentcli leaf, never by agentd) or a non-percent unit ⇒ no write and one log line; a profile not held ⇒ no write and no log; Conflict ⇒ no write this period and a re-read next period; the usage reader is handlers.read_profile_usage over USAGE_COMMAND, the registry reader is handlers.list_profile_homes over PROFILES_COMMAND, and sessionhost imports nothing from agentcli"
       :counterexamples
         [(counterexample "agentd が profile の残量を書かない — controller は observed の不在を unobserved(Unknown)としか読めず、盤の health が『profile 35 本の残量の観測がどれも窓より古いか無い』のまま、Scheduling は枯渇を判じられない(実弾 2026-09-12 23:4x・本番の profile 35 行)")
          (counterexample "agentd が provider を呼んでよいかを自分で判定する(登録簿の階級・会社認証の宿を handlers / judgment に写す)— 判定点が agentcli の葉と 2 つになり、名簿の改訂で片方だけ残る(operator の規則 2026-09-09 の否定)。観測する行を宣言の所有と行の boundary で先に絞るのは別の軸(段 10 lane 10y)で、葉の判定は写さない")
          (counterexample "家の在否だけで観測する行を絞る — 所有 personal の機体(operator の個人の MacBook proboscis-mbp・会社 profile の家 ca / p10xxx が在る)で会社の口座の行が観測の列に入り、usage の読みと log(`agentd: profile ca not observed: …`)に会社 profile が現れる(2026-09-15 の実測・operator 指示 2026-09-09「会社 profile は会社機体だけ」)")
          (counterexample "断られた profile に cached の値を書く — 非会社機体が会社 profile の残量を名乗り、controller が会社 profile の観測の由来(observed.node の ownership)を読み違える")
          (counterexample "post-image を observed だけで組む(conditions を落とす)— agentd は conditions の書き手でないので engine が断り、観測が 1 行も着地しない")
          (counterexample "断面が同じ拍にも書く — 35 行 × 周期ごとの status_synced が event journal を埋め、watch の拍が起き続ける")
          (counterexample "sessionhost が agentcli を import する — doeff(上流)が dotfiles(下流)に依存し、tool env(uv tool)では import が落ちて agentd が参加しない")
          (counterexample "profile を 1 つも持たない器(pool の pod)で周期ごとに `ai usage` を撃つ — 登録簿の生成物(profiles.gen.json)の無い器では読み口が毎周 exit 1 で落ち、log が『profile observation failed: FileNotFoundError』で埋まる(実弾 2026-09-13 zeus の agentd-pool・段 8e lane 4j)。器の profile の集合は家の在否で先に読み、空なら撃たない")
          (counterexample "読み口の落ち方(FileNotFoundError の文言)で『profile が無い』を判じる — 判定が dotfiles の内部の綴りに結ばれ、名簿の改訂で偽陰性・偽陽性になる")])
     (law conversation-cache-is-kept-only-on-the-same-node-and-home
       :statement "for_all Bound job j of conversation c claimed on node n with home h = session-affinity-key-of(plan(j)) and candidate session s (affinity.predecessor, else the last session of c on n): the arm of j is judgment.next-arm-for-job(s, session.get(s), h, effort, compact, recorded) alone, where recorded = judgment.conversation-recorded-of(the answer of one RecordReadSince(c, since 0, limit 1, raw kinds) asked only when s is absent and the record service is configured; an unreadable answer counts as recorded, no service as not); s absent ∧ recorded ⇒ rehydrate; s absent ∧ ¬recorded ⇒ launch; s alive ∧ idle ∧ attribution.home(s) = h ⇒ send; s alive ∧ idle ∧ attribution.home(s) ≠ h ⇒ session.cleanup(s) ∧ rehydrate; s alive ∧ ¬idle ⇒ defer; s registered ∧ terminal ∧ attribution.home(s) = h ⇒ session.resume(s); otherwise ⇒ rehydrate = session.launch whose prompt = charter.prompt ++ rehydrate-history-of(c, messages of c, source, inputs(j), rehydrate_history_byte_budget) (++ bodies on headless) where source = RecordedTurns(the record service's events of c read backwards from before=latest until record-history-satisfied or the first page) when the record service answers, else HeadlineTurns(the turn-record rows of c) and the prompt's head and the log name the thin rehydrate and its reason; a refused resume ⇒ rehydrate with the same minted id; turn-record(j).spec.sessionId = sessionHandle(j).sessionId; observations.sessions[*].account = attribution.account and observations.transcripts = the newest ended session per conversation whose transcript file exists, at most transcripts_observed_max"
       :counterexamples
         [(counterexample "profile を変えた手番を同じ機体の温かい session へ send する — 前の家の資格と cache で走り、binding の account が効かない(2026-09-13 の code の読み: next-arm-for-job は家を見ていなかった)")
          (counterexample "会話の宣言の model だけを変えた手番を同じ家の温かい session へ send する — CLI の session は起こした時の model のまま走り、charter.model が効かない(実射 2026-09-14 段 9o lane 9o-2: charter と turn-record は claude-opus-5 なのに『session started: model claude-sonnet-5』・返事も sonnet。家の鍵に model が無かった)")
          (counterexample "家をまたいで --resume する(transcript を新しい家へ写す前提)— operator 決定 #54 は cache の失効を受け入れると決めた。別の家の --resume は transcript を見つけられず SessionRefused → LaunchFailed で会話が止まる")
          (counterexample "器に行の無い predecessor を resume する — 別の機体で走った会話が `session is not registered` で LaunchFailed(Rehydrate の腕が無かった)")
          (counterexample "turn-record の spec に sessionId を書かない — Messaging が predecessor を名指せず、温かい session が片付いた(idle TTL・agentd の再起動・agent-job の行の回収)次の手番は同じ家でも文脈なしで起きる(本番 2026-09-13: 起こし方は send 39・launch 21・resume 0)")
          (counterexample "session の会話を回収される agent-job の行から導く — 終端の後に行が回収されると node の観測から会話が消え、Scheduling が cache を持つ node を選べない")
          (counterexample "「これまでの会話」を上限なしに畳む / 古い手番を黙って落とす — prompt が器の上限を超えるか、agent は落ちた事実も全文の在処も知らずに答える")
          (counterexample "履歴からの再開を ACP の見出しから畳む(text の無い entry を本文として読む)— 手番の本文が全部空の『これまでの会話』を agent に渡し、agent は文脈が無いことを知らずに答える。本文は記録の service の before=latest から読み、届かない時は薄い再開と名乗る")
          (counterexample "候補の無さを『新しい会話』と読んで launch する — 宣言を変えた手番は Messaging の lineageFor(段 12 lane 12k)が predecessor を空にし、前の手番の agent-job の行は終了 300 s で回収されるので、記録の在る会話が全履歴を失って始まる(本番 2026-09-16 17:29 aj-545JP9E9ZMZHPM11ZW99KM51AC・operator の会話 c-01M1XGMD…: arm launch・2,100 出来事の記録も 4 本の要約も読まれなかった)。候補が無い拍は記録の service に在否を 1 読みで問い、在れば履歴から再開する(追補 4)")
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
         [(counterexample "pool の入れ替え(頭と同じ拍で roll — #304)で SIGTERM を受けた agentd が走っている個人の手番を即座に AgentdRestart で閉じる — 頭が 1 日 4 回入れ替わるたびに手番が切れる(#216 案 B が pool を別の寿命の pin にしていた理由)。宣言 drain_seconds の node は閉じる前に排水する(R39)")
          (counterexample "TERM で SystemExit だけ投げて子を process group の kill に任せる — 手番の途中の行と job が黙って残り、次の起動の復帰が『死んだ』と観測するまで会話が「動いている」のまま(2026-09-14 14:35〜18:5x 実弾の入口)")
          (counterexample "headless の子を setsid で切り離して復帰で拾おうとする — stdin / stdout の pipe の親が死ぬと子は EPIPE で降りるか、生きても events file の書き手が居ない。器として使えない process を『生きている』と数える")
          (counterexample "停止の hook を SystemExit の後の finally で走らせる — accept loop が死んだ後は agentd の器の RPC(session.get)が応えず、hook が hang するか眺めを読めない。hook は 1 度目の TERM の別 thread で、accept を生かしたまま")
          (counterexample "撃ち直しを _thread.interrupt_main で行う — main thread は accept の blocking syscall の中で、次の接続が来るまで handler に来ない(実測 2026-09-14: 30 s 待っても exit しない)。実の os.kill で撃ち直す")
          (counterexample "process を 1 つずつ kill() で降ろす — EOF 5 s + TERM 5 s の猶予が process の数だけ直列に積み、launchd の ExitTimeOut(20 s)を越えて SIGKILL され、残りの行が黙って残る")
          (counterexample "停止の腕が idle の温かい行も stopped にする — 再起動のたびに会話の cache を捨てる(次の send が --resume で同じ session を起こし直す設計を壊す)")
          (counterexample "実況の push の宛先に 127.0.0.1:8868 の既定値を残す — 宣言の無い agentd が黙って退役した Mac の中継へ押し続ける。宛先は宣言ちょうど・無ければ参加しない")])
     (law conversations-compact-themselves-at-their-declared-threshold
       :statement "for_all conversation c with a row whose status.agent.compactAt = k (an integer 0..100) and for_all Bound job j of c claimed by agentd with a candidate session s that is not mid-turn: percent(s) = judgment.context-percent-of(context measured at the end of s's last turn) and (percent(s) ≠ None ∧ percent(s) ≥ k) ⇒ next-arm-for-job(s, view(s), home, effort, compact = True) = rehydrate with compacts = True and retire = s when s is alive, and agentd emits one agentd_compactions_total line naming c; (k absent ∨ percent(s) = None ∨ percent(s) < k) ⇒ compact = False and the arm is R20's; the measurement is judgment.deltas-of (DeltaBatch.context from the last assistant usage and the result's modelUsage[model].contextWindow for claude, the last token usage and the model context window for codex) and agentd writes it to no ACP row; for_all incarnation (launch / resume / rehydrate) of a job of c: params.session_env[AGORA_CONVERSATION_ID] = c and params.session_env[AGORA_SEAT_OPENER] = spec.opener of c's row when that row was read (absent otherwise), composed by judgment.charter-with-conversation-env alone"
       :counterexamples
         [(counterexample "受付の会話(永続・郵便が絶えない)を温かい session へ送り続ける — 文脈が窓を埋め、agent が古い郵便を忘れるか CLI が自分で要約して振り分けの根拠が消える(operator 2026-09-14「that routing agent should compact itself」の実弾)")
          (counterexample "閾値を agentd の値の宣言(AgentdSettings)に置く — 会話ごとに違う閾値(受付 60・議論 90)を表せず、宣言の座が会話の行(agora-conversation が書く)と agentd の 2 つになる")
          (counterexample "文脈の大きさを turn-record の usage の和(input + cacheRead + …)から導く — 和は手番の全 message の入力の合計で、最後の prompt の大きさではない(3 message の手番は 3 倍に見える)")
          (counterexample "窓の大きさを model の名から agentd が推測する(claude は 200k と決め打つ)— [1m] の model や codex の窓が違い、実測の無い値で圧縮の拍を決める。窓は器が名乗った値(result の modelUsage / model_context_window)だけ")
          (counterexample "圧縮の手番を手番の途中の候補にも撃つ(defer より先に rehydrate)— 走っている手番の session を片付け、その手番の結末が消える")
          (counterexample "圧縮の判断を claim の腕(agentd.hy)で行い next-arm-for-job にも家の判断を残す — 起こし方の判定点が 2 つになり、fake で反例を撃てない(R10 の反例と同じ)")
          (counterexample "実測を turn-record の status.usage に書く(契約に無い欄)— 読み手の zod / Hy の写しが行を落とすか、engine の statusByteBudget の外で書き手が第 2 の定義点を作る。耐久にするなら契約の便が先")
          (counterexample "手番の process に会話の id を渡さない(env は agentd 自身の AGORA_CUSTODY_URL 等の継承だけ)— agent が自分の会話 id を答えられず session の UUID を答える(依頼者の実測 2026-09-14 17:1x)。`ai tell` の差出人が名乗れない")
          (counterexample "opener を agentd が推測して置く(system 以外は machine と決め打つ)— operator が自分で開いた会話の手番が machine を名乗り、決裁書の門(law decision-paper-gates-bind-only-agora-opened-conversations)が誤って立つ。読めなければ置かない")])
     (law turn-credential-is-borrowed-with-a-voucher-from-the-account-s-worker
       :statement "for_all borrow of agentd a with declared custody master m: a issues POST m/lease/{kind}{account, purpose} and, on 200 with leaseId and holdExpiresAt and voucher and workerUrl, exactly one POST workerUrl/redeem{voucher} whose 200 body alone carries the credential (accessToken for claude, authJson for codex); LeaseGrant.hold_expires_at_ms = holdExpiresAt of the master answer; a non-200 at either step => LeaseRefused(status, error, hold from the master answer when known) and no credential; a 200 grant missing any of the four fields => LeaseRefused(malformed grant); no declared custody URL => LeaseRefused(503) with no request and no invented host; the decision is handlers.CustodyHttp._borrow alone"
       :counterexamples
         [(counterexample "預かり所の URL に既定値(http://custodian…)を残す — 宣言していない機体が黙って誰かの宿へ札を求め、会社の資格が非会社の機体へ渡り得る。宿は宣言ちょうど・無ければ断る")
          (counterexample "master の答えの札(accessToken)をそのまま使う(引換券を換えない)— 封じた資格が master に居ることになり、master と worker の分割(会社の資格は会社の機体の中だけ)が消える")
          (counterexample "引換券を複数回換える(失敗の再試行で同じ券を撃ち直す)— 一回限りの CAS が 409 を返すか、二重に貸した札が生きる。再試行は貸与からやり直す")
          (counterexample "worker の断りを握って master の答えだけで LeaseGrant を組む(access_token = None のまま起こす)— 資格の無い process が起き、CLI が 401 で落ちるまで誰も気づかない")
          (counterexample "hold の期限を worker の redeem の答えから導く — 期限の定義点が master の引換券の行と worker の 2 つになり、更新の見回りと貸与が別の期限を見る")])
     (law the-machine-names-its-place-and-refuses-another-place-s-account
       :statement "for_all agentd a: a.settings.places = join.places-of([agentd].places / --places / DOEFF_AGENTD_PLACES) is a non-empty duplicate-free tuple over {company, personal} (else a does not join), node-spec-of(a) puts it in the typed field spec.places and its comma-joined copy in spec.labels.places, and writes neither the retired one-word spec.place nor labels.place (aligning an existing row drops them); for_all Bound job j claimed by a with credential source = lease and profile row p of j's account: places(a) non-empty and boundary(p) known and boundary(p) not in places(a) => j is Ended with condition CredentialPlaceMismatch and no session is launched (both directions — a company-only node refuses a personal account as a personal-only node refuses a company one; a node naming both takes both); boundary(p) unknown (row absent, field absent, outside the closed vocabulary) => the turn proceeds; the comparison is judgment.credential-place-mismatch alone; no reader translates a one-word declaration into a set"
       :counterexamples
         [(counterexample "置き場を宣言しない機体を参加させ、口座の置き場だけで配車する — 会社の口座の手番が個人の機体で起き、会社 profile の API 呼び出しが非会社機体から飛ぶ(operator 指示 2026-09-09 の禁止そのもの)")
          (counterexample "判らない置き場(profile の行が読めない・欄が無い)を食い違いと読んで閉じる — 預かり所へ移る途中の口座の手番が全部落ちる。前段の門は判らないもので止めない")
          (counterexample "置き場の語彙を機体ごとに決める(company-mac / mac-company)— ACP の契約の boundary と綴りが合わず、突合が常に偽になる。語彙は契約の閉語彙ちょうど")
          (counterexample "食い違いの判定を claim の腕と配車の両方に書く — 判定点が 2 つになり、片方だけ直る。比べる点は judgment の 1 つ")
          (counterexample "機体の置き場を 1 値(place)で持たせる(段 10 lane 10d 便 2〜4)— 会社 Mac が会社と個人の両方の worker として寄与できない: company と名乗れば個人の口座の手番が全部 CredentialPlaceMismatch で落ち(実弾 2026-09-15 01:3x・4 本)、personal と名乗れば会社の profile を配れる node が 0(agora-redesign #224)。置き場は集合で名乗る(段 11 lane 11u)")
          (counterexample "1 値の宣言([agentd].place / DOEFF_AGENTD_PLACE)を 1 要素の集合と読み替える互換を join に置く — 宣言の定義点が 2 つになり、機体ごとにどちらが正か違う。鍵を places へ移し、旧い鍵は宣言に無い鍵として断る(依頼者の裁定 2026-09-16)")
          (counterexample "旧い行の 1 値の spec.place / labels.place を揃えの写しに残す — 契約 v4 の配車は place を持つ行を行の誤りとして断るので、agentd を入れ替えても node が配車から消えたまま(誰も言わない)。揃えの写しで落とす")])
     (law the-turn-s-credential-rides-the-turn-and-the-cli-inherits-no-agentd-env
       :statement "for_all warm send of agentd a for job j with lease l: SessionSend(j).session_env = judgment.turn-session-env-of(l) (claude: {CLAUDE_CODE_OAUTH_TOKEN: l.access_token}; codex or no lease: {}); for_all session.send received by the host with session_env e: e passes policy.session-env-admission-error (binding-owned keys and metered credentials refused) and backend = headless and mode = turn, otherwise the call is refused; for_all resume of a headless row r for that send: the spawned process env = launch-spawn-env(identity(r), overlay(r) merged with e) and overlay(r) carries no key of policy.TURN-AUTH-ENV-KEYS (the row never stores the turn credential); for_all headless spawn: the machine env reaching the child = policy.inheritable-spawn-env(os.environ) alone, so no ACP_* / DOEFF_* key and no custody / record / borrower address is inherited"
       :counterexamples
         [(counterexample "再開の process を行の launch_overlay の誕生の token で起こす — 預かり所が口座を更新した拍(実弾 #92 2026-09-14)に revoke 済みの札で起き、温かい会話の次の手番が 401 で落ちる")
          (counterexample "手番ごとの札を行(launch_overlay)に上書きで残す — 秘密が sqlite の行と session.get の答えに載り、log と検分の眺めへ漏れる。行に残すのは非 auth の意図だけ")
          (counterexample "session.send の session_env を launch と別の関所に通す(素通しする)— binding 所有キーの裏口が送りの口に開き、auth の合成の 1 点(R7)が壊れる")
          (counterexample "運べない組み合わせ(tmux の器・mode = interrupt)で session_env を黙って落とす — 呼び手は新しい札で起きたと思い、実際は誕生の札の process が走る(#92 の形が別の口で再生する)")
          (counterexample "手番の CLI に agentd の process env を丸ごと継がせる — 会話の中の道具が ACP の札と口・預かり所の URL・借り手札の path を読め、agentd の名で系を撃てる(実弾 #95 2026-09-14)")
          (counterexample "継がせない名を否定の名簿(ACP_* / DOEFF_* を落とす)で書く — 新しい接頭の env が増えるたびに漏れ、名簿の改訂を忘れた拍に静かに破れる。名簿は許可の側で書く")])
     (law node-row-is-named-by-the-machine-from-its-declaration
       :statement "for_all heartbeat of agentd a with settings s: no live node row named s.node_name => AcpCreate(node, s.node_name, node-spec-of(s), declaration_sha256 = s.declaration_sha256); a live node row n with n.spec != node-spec-declared(n.spec, s) => AcpPutSpec(n, node-spec-declared(n.spec, s), declaration_sha256 = s.declaration_sha256) and the lease is written in the same heartbeat whether or not the spec write lands; s.node_capacity = int([agentd].capacity) and a declaration without it refuses to join; s.declaration_sha256 = sha256(bytes of the declaration file a read) and both writes carry it as the header x-declaration-sha256; s.work_roots declared ⇒ node-spec-of(s).workRoots = node-spec-declared(n.spec, s).workRoots = list(s.work_roots) and undeclared ⇒ neither carries workRoots"
       :counterexamples
         [(counterexample "行を作るのを人の道具(register-node)に残す — pod の名が変わるたびに行が無く、heartbeat が『not in ACP yet』を吐いて待ち続け、手で撃たれた値(capacity 0)が宣言と食い違ったまま配車の候補から外れる(実測 2026-09-13・#85)")
          (counterexample "capacity を機体の家(profile の置き場)の数から導く — 資格は預かり所の貸与なので pool の pod の家は 0 で、名乗る容量が常に 0 になる")
          (counterexample "spec を揃える時に labels を宣言の空で上書きする — 手で置かれた会社境界の boundary = company が消え、会社の会話が結べる node が黙って無くなる")
          (counterexample "spec の書きが断られた拍に lease も書かない — 契約の書き手の登録し直しの前後で node の lease が切れ、走っている手番が LostLeaseExpired になる")
          (counterexample "node の行の誕生と揃えに読んだ宣言 file の指紋を運ばない — ACP の kind node の capacity が declaredByFile になった後(2026-09-15 02:0x の kind の再登録)、新しい節(proboscis-mbp・名の変わった pool の pod)が 403 declaration-needs-fingerprint で 1 つも参加できない(実測 2026-09-15 02:39・agora-redesign #110)")
          (counterexample "指紋を宣言の元の file(値の表の `${}` を含む dotfiles の toml)や整形し直した木から計算する — agentd が実際に読んだ bytes と食い違い、日次の見張りの突合(同じ描き手で写しを描いてから比べる)が偽の不一致を名乗る")]
       :enforcement ["docs/adr/defadr_doeff_agents_012_agentd_acp_arms.hy::test-adr-doe-agents-012-node-row-is-named-by-the-machine"
                     "packages/doeff-agents/tests/test_sessionhost_acp.py::test_missing_node_row_is_registered_from_the_declaration_and_joined_on_the_next_heartbeat"
                     "packages/doeff-agents/tests/test_sessionhost_acp.py::test_node_registration_refused_is_logged_once_and_retried_each_heartbeat"
                     "packages/doeff-agents/tests/test_sessionhost_acp.py::test_node_spec_is_aligned_to_the_declaration_keeping_labels_and_the_lease_is_written_in_the_same_tick"
                     "packages/doeff-agents/tests/test_sessionhost_acp.py::test_node_row_writes_carry_the_fingerprint_of_the_declaration_file_the_agentd_read"
                     "packages/doeff-agents/tests/test_sessionhost_acp.py::test_join_reads_the_fingerprint_of_the_declaration_file_bytes_into_the_env_and_settings"
                     "packages/doeff-agents/tests/test_sessionhost_acp.py::test_acp_writes_put_the_fingerprint_header_only_on_the_writes_that_carry_it"
                     "packages/doeff-agents/tests/test_sessionhost_acp.py::test_node_row_names_its_work_roots_only_when_declared"
                     "packages/doeff-agents/tests/test_sessionhost_acp.py::test_join_reads_work_roots_into_the_env_and_settings_and_refuses_ambiguous_roots"
                     "packages/doeff-agents/tests/test_sessionhost_acp.py::test_node_spec_alignment_refused_still_writes_the_lease_and_logs_once"
                     "packages/doeff-agents/tests/test_sessionhost_acp.py::test_join_spec_refuses_missing_server_or_token_unknown_flags_and_bad_words"])
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
          (counterexample "記録の追記を push と同じ 50 ms の拍で書く — 走っている手番 1 つで毎秒 10〜20 の CAS の書きが ACP の event journal に並び、画面の糊の watch の拍が飽和して hello まで 15 s 待った(実弾 2026-09-13 18:3x・便 2 の初版)")])
     (law turn-work-dir-is-read-on-this-node-and-a-missing-one-is-not-launched
       :statement "for_all Bound job j claimed by agentd a on node n with home h: plan(j).charter.work_dir w' = plan-with-node-home(w, h) (only ~ and ~/… are replaced by h); if w' is declared and not a directory on n then: charter.work_dir_scratch = true => FsMakeDirectories(w') and j is launched only when it succeeds; otherwise j is not claimed, no lease is borrowed, no session is launched, and j is Ended with the condition WorkDirMissing whose reason names n and w'; the step is judgment.work-dir-step-of alone and the launch params carry w'"
       :counterexamples
         [(counterexample "work_dir の在否を見ずに起こす — 会社 Mac の絶対 path(/Users/s22625/…)の手番が別の機体(proboscis-mbp・pool の pod)で sessionhost の LaunchFailed に落ち、条件が起こし損ねの総称なので配車の係が会話 × node で外せず、同じ会話の次の手番も同じ node に結ばれうる(実弾 2026-09-15 02:54・本物の会話 2 本)")
          (counterexample "無い work_dir を黙って作る — repo を指す作業場(~/repos/x)が空の dir で起こされ、agent が空の場所で手番を走らせて成功を名乗る")
          (counterexample "`~` を agentd の process の家や宣言した機体の家で展開する(charter を組んだ側で絶対 path に焼く)— 機体に依らない綴りの意味が消え、会社 Mac の家がまた他の機体へ運ばれる")
          (counterexample "起こした後に work_dir の検の結果で閉じる(claim と借りの後)— 預かり所の貸与の hold と Running の書きが無駄に立ち、配車の係の attempt が 1 つ減る")]
       :enforcement ["docs/adr/defadr_doeff_agents_012_agentd_acp_arms.hy::test-adr-doe-agents-012-work-dir-is-one-judgment-on-this-node"
                     "packages/doeff-agents/tests/test_sessionhost_acp.py::test_work_dir_missing_on_this_node_ends_the_job_without_launching"
                     "packages/doeff-agents/tests/test_sessionhost_acp.py::test_work_dir_home_relative_is_expanded_with_this_node_home_and_a_scratch_mark_creates_it"
                     "packages/doeff-agents/tests/test_sessionhost_acp.py::test_plan_with_node_home_expands_only_tilde"
                     "packages/doeff-agents/tests/test_sessionhost_acp.py::test_work_dir_step_is_one_judgment"])
     (law dropped-history-turns-leave-a-headline-not-silence
       :statement "for_all fold f = rehydrate-history-of(c, messages, source, exclude, budget, fetched) with turns t_1 … t_n (oldest first) whose full text exceeds budget: f drops the oldest prefix t_1 … t_k (the smallest k < n whose remainder fits) and f.text = header ++ H ++ t_{k+1} … t_n where H = history-dropped-headline alone = one line naming the period [first at of t_1 〜 last at of t_k], k, the number of dropped mails and events, the per-kind counts (mails counted as HISTORY_MAIL_KIND) and the tool names in first-seen order (history-counts-note — the same spelling as a thin rehydrate's turn-record headline) and where the full record is; f.dropped_headline = H and H occurs in f.text exactly once, before every kept turn; k = 0 ⇒ f.dropped_headline = None and no headline in f.text; header ++ H ++ t_n alone exceeding budget ⇒ the head of t_n is cut, H is kept and f.cut_bytes names the bytes cut; f.size_bytes ≤ budget whenever budget ≥ len(header ++ H ++ notice); the fold calls no model and performs no I/O; the budget is AgentdSettings.rehydrate_history_byte_budget alone; agentd logs H once per rehydrate that dropped"
       :counterexamples
         [(counterexample "上限を超えた古い手番を黙って落とす(落とした数と在処だけの footer)— agent は落ちた区間に何が在ったか(手番の数・期間・どの道具を使ったか)を知らずに答え、受付の会話のように手番が多い会話ほど文脈の穴が大きい(2026-09-13 段 8q の実装: 『古い手番 N 件を要約せずに落としました』の 1 行だけ)")
          (counterexample "落とした手番ごとに見出しを 1 行ずつ残す — 見出しの行数が落とした手番の数に比例して上限を食い、残せる本文が減る(受付の会話の数百手番で見出しだけが数十 KB)。見出しは落とした区間(古い手番の連なり)に 1 行")
          (counterexample "落とした手番を model に要約させる(この便で)— 要約の作り手・上限・費用は one-way door(費用)で operator の判断(agora-redesign #55 便 2 の設計)。便 1 は決定的な見出しだけ")
          (counterexample "見出しの綴りを turn-record の見出し(薄い再開)と別に書く — 同じ『kind ごとの件数と道具の名』が 2 つの綴りになり、読み手(agent)が別の物と読む。綴りは history-counts-note の 1 点")
          (counterexample "見出しを末尾(footer)に置く — 時刻順に読む agent が『これまでの会話』の途中で落ちた区間に気づかない。見出しは落とした区間の位置(残した手番の前)に置く")
          (counterexample "見出しの分だけ上限を超えても構わないとする — 器の prompt の上限に当たる。見出しも上限の中に数え、最新の手番だけでも超える時はその先頭を切って切った byte を名乗る")
          (counterexample "落とした区間の見出しの上限を別の値の宣言(AgentdSettings に 2 つ目)にする — 方策の定義点が 2 つになる。上限は rehydrate_history_byte_budget の 1 点")]
       :enforcement ["docs/adr/defadr_doeff_agents_012_agentd_acp_arms.hy::test-adr-doe-agents-012-dropped-history-turns-leave-a-headline"
                     "packages/doeff-agents/tests/test_sessionhost_acp_rehydrate.py::test_dropped_turns_fold_into_one_headline_with_period_counts_and_tools"
                     "packages/doeff-agents/tests/test_sessionhost_acp_rehydrate.py::test_a_fold_within_the_budget_carries_no_headline"
                     "packages/doeff-agents/tests/test_sessionhost_acp_rehydrate.py::test_the_newest_turn_is_cut_after_the_headline_and_the_cut_bytes_are_named"
                     "packages/doeff-agents/tests/test_sessionhost_acp_rehydrate.py::test_a_thin_rehydrate_folds_dropped_record_headlines_into_the_range_headline"])
     (law history-thins-tool-items-oldest-first-before-any-turn-is-dropped
       :statement "for_all fold f = rehydrate-history-of(c, messages, source, exclude, budget, fetched) with turns t_1 … t_n (oldest first) whose full text exceeds budget: before any turn is dropped, f replaces the tool items (kind tool_use: the input; kind tool_result: the output) of t_1, then t_2, … with their first k = budget / HISTORY_THIN_DIVISOR bytes followed by a note naming k and the original byte count (history-event-thin-line → history-thin-body, spelled by the same history-event-line-of as the full line), stopping at the first prefix that fits; mail lines and items of kind text / user / system / error are byte-identical to the full fold; a turn without any thinnable item is not counted; f.thinned_turns = the number of turns actually thinned; only when every turn is thin and the text still exceeds budget does f drop turns (R34) and then cut the newest (R34); source = HeadlineTurns ⇒ thinned_turns = 0; the fold is deterministic, calls no model, performs no I/O, and the only declared value is AgentdSettings.rehydrate_history_byte_budget"
       :counterexamples
         [(counterexample "上限を超えたら手番を丸ごと落とす(便 1 の形)— 道具の結果 30 KB の手番 1 つが上限の半分を食い、残せる手番が数個(本番 2026-09-13〜16: 落とし 23 回・kept 1 の再開 87 回)。落とす前に薄くすれば会話の意図(郵便)と結論(agent の text)は全手番残る")
          (counterexample "郵便や agent の text を切り詰める — 会話の意図と結論が消え、要約より悪い。薄くするのは道具の項だけ")
          (counterexample "最新の手番から薄くする — いま続ける作業の材料(直近の道具の結果)が先に消える。古い手番から")
          (counterexample "切り詰めの長さを AgentdSettings の 2 つ目の欄にする — 方策の定義点が 2 つになる。上限からの比(effects の定数 1 点)")
          (counterexample "薄くしたことを名乗らない(黙って切る)— agent が全文と思って読む。先頭 k byte と元の byte を名乗る")
          (counterexample "薄い再開(HeadlineTurns)でも薄くした数を数える — 本文の無い材料で『薄くした』と名乗る(嘘)。0")
          (counterexample "落とした後に薄くする(順序を逆にする)— 落とした手番は戻らないので薄くする意味が無い。薄くする → 落とす → 切る の順")
          (counterexample "model に要約させる — one-way door(費用・鍵の置き場)。operator の判断(#55 案 D)のまま")]
       :enforcement ["docs/adr/defadr_doeff_agents_012_agentd_acp_arms.hy::test-adr-doe-agents-012-history-thins-tool-items-before-dropping-turns"
                     "packages/doeff-agents/tests/test_sessionhost_acp_rehydrate.py::test_tool_items_are_thinned_oldest_first_before_any_turn_is_dropped"
                     "packages/doeff-agents/tests/test_sessionhost_acp_rehydrate.py::test_when_thinning_everything_is_not_enough_turns_are_dropped_behind_the_headline_in_thin_form"
                     "packages/doeff-agents/tests/test_sessionhost_acp_rehydrate.py::test_a_thin_rehydrate_has_no_tool_bodies_to_thin_and_counts_none"
                     "packages/doeff-agents/tests/test_sessionhost_acp_rehydrate.py::test_thinned_items_keep_the_head_and_name_the_original_bytes_while_text_and_mail_stay_byte_identical"])]
  :enforcement
    [(deftest test-adr-doe-agents-012-history-thins-tool-items-before-dropping-turns
       ;; R35 の針(構造): 薄くする点は judgment の 1 点ずつ(history-event-body / history-event-line-of / history-thin-body /
       ;; history-event-thin-line)・薄くするのは道具の 2 kind だけ・畳みの中で薄くする段が落とす段より前・k は effects の比の 1 点・
       ;; 上限の宣言は 1 点のまま・答えは thinned_turns を欄で運ぶ・agentd の log に thinned。反例(挙動)は rehydrate deftests の 4 本。
       (setv judgment-lines (code-lines (/ ACP-DIR "judgment.hy")))
       (for [name ["history-event-body" "history-event-line-of" "history-event-line" "history-thin-body" "history-event-thin-line"]]
         (assert (= (len (lfor line judgment-lines :if (.startswith line f"(defk {name} ") line)) 1) f"薄くする点は 1 つ(R35): {name}"))
       (setv thin-body (defk-body judgment-lines "history-event-thin-line"))
       (assert (any (gfor line thin-body (in "(not-in event.kind #(\"tool_use\" \"tool_result\"))" line))) "薄くするのは道具の項だけ(R35)")
       (assert (any (gfor line thin-body (in "(history-event-line-of event thin)" line))) "薄い項の綴りは全文の項と同じ 1 点(R35)")
       (setv fold-body (defk-body judgment-lines "rehydrate-history-of"))
       (setv thin-at (next (gfor [idx line] (enumerate fold-body) :if (in "(setv (get blocks reach) (get thin-blocks reach))" line) idx) None))
       (setv drop-at (next (gfor [idx line] (enumerate fold-body) :if (in "(history-dropped-headline counts dropped-turns dropped-items budget where)" line) idx) None))
       (assert (and (is-not thin-at None) (is-not drop-at None) (< thin-at drop-at)) "薄くする段は落とす段より前(R35)")
       (assert (any (gfor line fold-body (in "(// budget HISTORY-THIN-DIVISOR)" line))) "k は上限からの比(R35)")
       (assert (any (gfor line fold-body (in "(history-event-thin-line event thin-k)" line))) "薄い項は出来事の畳みで 1 度組む(R35)")
       (setv effects-lines (code-lines (/ ACP-DIR "effects.py")))
       (assert (any (gfor line effects-lines (.startswith line "HISTORY_THIN_DIVISOR = "))) "比の定数は effects の 1 点(R35)")
       (assert (any (gfor line effects-lines (.startswith line "    thinned_turns: int"))) "HistoryFold は薄くした数を欄で運ぶ(R35)")
       (assert (any (gfor line effects-lines (.startswith line "    thin_line: str | None"))) "HistoryItem は薄い行を持つ(R35)")
       (assert (= (len (lfor line effects-lines :if (.startswith line "    rehydrate_history_") line)) 1)
               "履歴からの再開の値の宣言は上限の 1 点だけ(R35 — 切り詰めの長さの欄を足さない)")
       (setv agentd-lines (code-lines (/ ACP-DIR "agentd.hy")))
       (assert (= (len (lfor line agentd-lines :if (in "{fold.thinned-turns} thinned" line) line)) 1) "agentd の log に thinned(R35)")
       (setv tests (.read-text (/ (. (Path __file__) parent parent parent) "packages" "doeff-agents" "tests" "sessionhost_acp_rehydrate_deftests.hy") :encoding "utf-8"))
       (for [name ["test-tool-items-are-thinned-oldest-first-before-any-turn-is-dropped"
                   "test-when-thinning-everything-is-not-enough-turns-are-dropped-behind-the-headline-in-thin-form"
                   "test-a-thin-rehydrate-has-no-tool-bodies-to-thin-and-counts-none"
                   "test-thinned-items-keep-the-head-and-name-the-original-bytes-while-text-and-mail-stay-byte-identical"]]
         (assert (in (+ "(deftest " name) tests) f"R35 の反例の検が無い: {name}")))
     (deftest test-adr-doe-agents-012-dropped-history-turns-leave-a-headline
       ;; R34 の針(構造): 畳みは rehydrate-history-of の 1 点・落とした区間の見出しは history-dropped-headline の 1 点・数の綴りは
       ;; history-counts-note の 1 点(turn-record の見出しと同じ)・黙って落とす旧の断り(footer)が無い・答えは見出しと切った byte を
       ;; 欄で運ぶ・agentd は見出しを log に 1 行・上限の宣言は AgentdSettings の 1 点のまま(方策の定義点を増やさない)。
       ;; 反例(挙動)は sessionhost_acp_rehydrate_deftests.hy の 4 本(見出しの無い落としが赤)。
       (setv judgment-lines (code-lines (/ ACP-DIR "judgment.hy")))
       (for [name ["rehydrate-history-of" "history-dropped-headline" "history-counts-note" "headline-counts-of-items"
                   "headline-counts-of-entries" "history-headline-line" "history-cut-notice"]]
         (assert (= (len (lfor line judgment-lines :if (.startswith line f"(defk {name} ") line)) 1) f"畳みの点は 1 つ(R34): {name}"))
       (setv fold-body (defk-body judgment-lines "rehydrate-history-of"))
       (assert (= (len (lfor line fold-body :if (in "(history-dropped-headline counts dropped-turns dropped-items budget where)" line) line)) 1)
               "落とした区間の見出しは history-dropped-headline の 1 点(R34)")
       (assert (any (gfor line fold-body (in "(+ [header headline] kept-summaries (cut blocks dropped-turns None))" line))) "見出しは残した要約と手番の前(R34・追補 5)")
       (assert (not (any (gfor line fold-body (in "要約せずに落としました" line)))) "黙って落とす旧の断りが残っている(R34)")
       (assert (= (len (lfor line judgment-lines :if (in "(history-counts-note counts)" line) line)) 2)
               "数の綴りは turn-record の見出しと落とした区間の見出しが同じ 1 点を呼ぶ(R34)")
       (setv headline-body (defk-body judgment-lines "history-dropped-headline"))
       (for [needle ["counts.first-at" "counts.last-at" "古い手番 {turns} 件" "{note}" "{where}"]]
         (assert (any (gfor line headline-body (in needle line))) f"見出しが名乗らない(R34): {needle}"))
       (setv effects-lines (code-lines (/ ACP-DIR "effects.py")))
       (assert (any (gfor line effects-lines (.startswith line "    dropped_headline: str | None"))) "HistoryFold は見出しを欄で運ぶ(R34)")
       (assert (any (gfor line effects-lines (.startswith line "    cut_bytes: int"))) "HistoryFold は切った byte を欄で運ぶ(R34)")
       (assert (any (gfor line effects-lines (.startswith line "HISTORY_MAIL_KIND = "))) "郵便を数える kind の綴りは effects の 1 点(R34)")
       (assert (= (len (lfor line effects-lines :if (.startswith line "    rehydrate_history_byte_budget: int = ") line)) 1)
               "上限の宣言は AgentdSettings の 1 点のまま(R34 — 方策の定義点を増やさない)")
       (setv agentd-lines (code-lines (/ ACP-DIR "agentd.hy")))
       (assert (= (len (lfor line agentd-lines :if (in "rehydrate headline: {fold.dropped-headline}" line) line)) 1)
               "agentd は落とした区間の見出しを log に 1 行(R34)")
       (setv tests (.read-text (/ (. (Path __file__) parent parent parent) "packages" "doeff-agents" "tests" "sessionhost_acp_rehydrate_deftests.hy") :encoding "utf-8"))
       (for [name ["test-dropped-turns-fold-into-one-headline-with-period-counts-and-tools"
                   "test-a-fold-within-the-budget-carries-no-headline"
                   "test-the-newest-turn-is-cut-after-the-headline-and-the-cut-bytes-are-named"
                   "test-a-thin-rehydrate-folds-dropped-record-headlines-into-the-range-headline"]]
         (assert (in (+ "(deftest " name) tests) f"R34 の反例の検が無い: {name}")))
     (deftest test-adr-doe-agents-012-provider-limit-is-the-containers-cause-read-as-a-field
       ;; R33 の針(構造): CLI の文を落とさない・族の表を当てるのは器の側の 1 点(表は markers のまま)・
       ;; 制御面は cause の欄だけを読む(族の逐語も markers の import も持たない)・乗るのは conditions で
       ;; result ではない・綴りは effects の 1 点。反例(挙動)は host の検 1 本(限度の文 → 行が failed /
       ;; rate_limited)と ACP の検 2 本(cause → 条件・限度でない終端では条件なし)。
       (setv protocol-text (.join "\n" (code-lines (/ SESSIONHOST-DIR "headless_protocol.py"))))
       (assert (in "said = _text_at(record, \"result\") if is_error else None" protocol-text)
               "CLI が名乗った文を手番の終わりの detail に運んでいない(R33)")
       (assert (in "return self._end(not is_error, detail)" protocol-text) "detail が subtype のまま(R33)")
       (setv headless-lines (code-lines (/ SESSIONHOST-DIR "headless.hy")))
       (setv headless-text (.join "\n" headless-lines))
       (assert (= (len (lfor line headless-lines :if (.startswith line "(deff headless-turn-limit-cause ") line)) 1)
               "限度を当てるのは器の側の 1 点(R33)")
       (assert (in "(import doeff_agents.sessionhost.impls.markers [has-api-limit-marker])" headless-text)
               "族の表は markers の 1 点を呼ぶ(R33)")
       (assert (in "(has-api-limit-marker verdict.detail)" headless-text) "表を手番の終わりの文へ当てていない(R33)")
       (assert (in "(make-cause \"rate_limited\" verdict.detail observed-at)" headless-text)
               "cause の category が pane の路と同じ語でない(R33)")
       (assert (in "(cause-if-absent row limit)" headless-text) "cause を既存の口で書いていない(R33)")
       (setv judgment-lines (code-lines (/ ACP-DIR "judgment.hy")))
       (setv judgment-text (.join "\n" judgment-lines))
       (assert (= (len (lfor line judgment-lines :if (.startswith line "(defk provider-limit-condition-of ") line)) 1)
               "制御面の判断は judgment の 1 点(R33)")
       (assert (in "(!= (.get cause \"category\") CAUSE-CATEGORY-RATE-LIMITED)" judgment-text)
               "判断が cause の category を読んでいない(R33)")
       (assert (= (lfor line judgment-lines :if (and (.startswith line "(import") (in "markers" line)) line) [])
               "制御面が族の表の module を import している(R33 — 註で家を指すのは可・引くのは不可)")
       (assert (not-in "has-api-limit-marker (" judgment-text) "制御面が族の表を撃っている(R33)")
       (for [word ["you've hit" "you've reached" "rate limit" "usage limit" "quota exceeded"]]
         (assert (not-in word (.lower judgment-text)) f"族の表が制御面に写っている(R33): {word}"))
       (setv agentd-text (.join "\n" (code-lines (/ ACP-DIR "agentd.hy"))))
       (assert (in "(provider-limit-condition-of" agentd-text) "手番の終わりが判断を撃っていない(R33)")
       (assert (in "view.terminal-cause" agentd-text) "読む材料が器の終端の cause でない(R33)")
       (assert (in "(ended-status-of job-status outcome.result" agentd-text) "result を条件で置き換えている(R33)")
       (assert (in "(if (is limit None) #() #(limit))" agentd-text) "条件が conditions に足されていない(R33)")
       (setv effects-lines (code-lines (/ ACP-DIR "effects.py")))
       (for [needle ["CONDITION_PROVIDER_LIMIT: ConditionType = \"ProviderLimit\""
                     "REASON_RATE_LIMITED: str = \"rate-limited\""
                     "CAUSE_CATEGORY_RATE_LIMITED: str = \"rate_limited\""
                     "MODEL_UNDECLARED: str = \"default\""]]
         (assert (any (gfor line effects-lines (.startswith line needle))) f"綴りは effects の 1 点(R33): {needle}")))
     (deftest test-adr-doe-agents-012-work-dir-is-one-judgment-on-this-node
       ;; R32 の針(構造): `~` の展開は judgment.plan-with-node-home の 1 点・段は work-dir-step-of の 1 点・門は agentd.work-dir-ready で
       ;; claim-job がそれを claim の前に呼ぶ・charter の作業場の綴りは effects の定数 2 つ(agentd.hy は charter の鍵を直に読まない)・
       ;; 条件の語は effects の 1 点。反例(挙動)は test_sessionhost_acp.py の 4 本。
       (setv judgment-lines (code-lines (/ ACP-DIR "judgment.hy")))
       (for [name ["(defk plan-with-node-home " "(defk work-dir-of " "(defk work-dir-step-of "]]
         (assert (= (len (lfor line judgment-lines :if (.startswith line name) line)) 1) f"作業場の判断は judgment の 1 点(R32): {name}"))
       (setv agentd-lines (code-lines (/ ACP-DIR "agentd.hy")))
       (assert (= (len (lfor line agentd-lines :if (.startswith line "(defk work-dir-ready ") line)) 1) "作業場の門は agentd.work-dir-ready の 1 点(R32)")
       (setv claim-start (next (gfor [i line] (enumerate agentd-lines) :if (.startswith line "(defk claim-job ") i)))
       (setv claim-lines (cut agentd-lines claim-start (+ claim-start 80)))
       (setv gate-at (next (gfor [i line] (enumerate claim-lines) :if (in "(work-dir-ready settings row plan now-ms)" line) i) None))
       (setv arm-at (next (gfor [i line] (enumerate claim-lines) :if (in "(next-arm-for-job " line) i) None))
       (assert (is-not gate-at None) "claim-job が作業場の門を撃つ(R32)")
       (assert (or (is arm-at None) (< gate-at arm-at)) "作業場の門は起こし方を決める前(claim と借りの前)(R32)")
       (assert (any (gfor line agentd-lines (in "(plan-with-node-home declared-plan settings.home)" line))) "claim-job は node の家で展開した plan を読む(R32)")
       (for [line agentd-lines]
         (assert (not-in "\"work_dir" line) f"agentd.hy は charter の作業場の鍵を直に読まない(R32): {line}"))
       (setv effects-lines (code-lines (/ ACP-DIR "effects.py")))
       (for [needle ["CHARTER_WORK_DIR_KEY = \"work_dir\"" "CHARTER_WORK_DIR_SCRATCH_KEY = \"work_dir_scratch\""
                     "CONDITION_WORK_DIR_MISSING: ConditionType = \"WorkDirMissing\""]]
         (assert (any (gfor line effects-lines (.startswith line needle))) f"綴りは effects の 1 点(R32): {needle}")))
     (deftest test-adr-doe-agents-012-verify-command-runs-a-script-not-a-session
       ;; R36 の針(構造): 種類の読みは judgment.job-kind-of の 1 点・claim-job は作業場の門(と資格の出所・起こし方)より前に
       ;; 種類で分岐する・verify の腕(claim-verify-job → observe-command → end-command)は session を起こさず札を借りない・
       ;; 起こし方は verify-argv-of の 1 点で path を位置引数で運ぶ・綴りは effects の 1 点。反例(挙動)は
       ;; sessionhost_acp_verify_deftests.hy の 7 本(launches == [] ∧ borrowed == [] を含む)。
       (setv judgment-lines (code-lines (/ ACP-DIR "judgment.hy")))
       (for [name ["(defk job-kind-of " "(defk verify-plan-of " "(defk verify-argv-of " "(defk verify-step-of "
                   "(defk verify-plan-of-handle " "(defk withdrawn-command-rows-of "]]
         (assert (= (len (lfor line judgment-lines :if (.startswith line name) line)) 1) f"verify の判断は judgment の 1 点(R36): {name}"))
       (setv agentd-lines (code-lines (/ ACP-DIR "agentd.hy")))
       (for [name ["(defk claim-verify-job " "(defk observe-command " "(defk recover-command " "(defk withdraw-command " "(defk end-command "]]
         (assert (= (len (lfor line agentd-lines :if (.startswith line name) line)) 1) f"verify の腕は agentd の 1 点(R36): {name}"))
       ;; claim-job: 種類の分岐は作業場の門と起こし方の判断より前。
       (setv claim-start (next (gfor [i line] (enumerate agentd-lines) :if (.startswith line "(defk claim-job ") i)))
       (setv claim-lines (cut agentd-lines claim-start (+ claim-start 90)))
       (setv kind-at (next (gfor [i line] (enumerate claim-lines) :if (in "(job-kind-of row)" line) i) None))
       (setv verify-at (next (gfor [i line] (enumerate claim-lines) :if (in "(claim-verify-job settings state row now-ms)" line) i) None))
       (setv gate-at (next (gfor [i line] (enumerate claim-lines) :if (in "(work-dir-ready settings row plan now-ms)" line) i) None))
       (setv source-at (next (gfor [i line] (enumerate claim-lines) :if (in "(credential-source-of plan settings.custody-declared)" line) i) None))
       (assert (and (is-not kind-at None) (is-not verify-at None)) "claim-job が種類で分岐しない(R36)")
       (assert (and (is-not gate-at None) (< verify-at gate-at)) "verify の分岐が作業場の門より後(R36)")
       (assert (and (is-not source-at None) (< verify-at source-at)) "verify の分岐が資格の出所の判断より後(R36)")
       ;; verify の腕は session を起こさず札を借りない・turn-record を作らない・中継へ押さない。
       (setv verify-start (next (gfor [i line] (enumerate agentd-lines) :if (.startswith line "(defk claim-verify-job ") i)))
       (setv verify-end (next (gfor [i line] (enumerate agentd-lines) :if (.startswith line "(defk receive-bound-jobs ") i)))
       (setv verify-body (.join "\n" (cut agentd-lines verify-start verify-end)))
       (for [word ["SessionLaunch" "SessionResume" "SessionSend" "CustodyLeaseBorrow" "incarnate" "start-claimed" "TURN-RECORD-KIND" "AcpStreamPush" "push-frames"]]
         (assert (not-in word verify-body) f"verify の腕が手番の腕の語を持つ(R36): {word}"))
       (assert (in "(CommandStart :argv argv :cwd settings.home)" verify-body) "verify の腕が CommandStart で起こしていない(R36)")
       (assert (in "(FsFileExists :path planned.script-path)" verify-body) "verify の腕が script の在否を検めていない(R36)")
       ;; 起こし方: sh の 1 行・path は位置引数(文字列に埋めない)。
       (setv argv-start (next (gfor [i line] (enumerate judgment-lines) :if (.startswith line "(defk verify-argv-of ") i)))
       (setv argv-body (.join "\n" (cut judgment-lines argv-start (+ argv-start 12))))
       (assert (in "echo $$ > \\\"$0\\\" && \\\"$1\\\" >> \\\"$2\\\" 2>&1; echo $? > \\\"$3\\\"" argv-body) "起こし方の sh の 1 行が変わった(R36)")
       (assert (in "plan.pid-path plan.script-path plan.log-path plan.rc-path" argv-body) "path が位置引数で運ばれていない(R36)")
       ;; 綴りは effects の 1 点。
       (setv effects-lines (code-lines (/ ACP-DIR "effects.py")))
       (for [needle ["CHARTER_KIND_KEY: str = \"kind\"" "CHARTER_KIND_VERIFY: str = \"verify\"" "CHARTER_VERIFY_JOB_ID_KEY: str = \"jobId\""
                     "VERIFY_SCRIPTS_RELDIR: str = \"dotfiles/cron_management\"" "CHARTER_VERIFY_JOB_ID_PATTERN: str = r\"^[a-z0-9][a-z0-9-]{0,63}$\""
                     "CONDITION_VERIFY_SCRIPT_MISSING: ConditionType = \"VerifyScriptMissing\""
                     "CONDITION_VERIFY_COMMAND_LOST: ConditionType = \"VerifyCommandLost\""
                     "CONDITION_VERIFY_DEADLINE_EXCEEDED: ConditionType = \"VerifyDeadlineExceeded\""]]
         (assert (any (gfor line effects-lines (.startswith line needle))) f"綴りは effects の 1 点(R36): {needle}"))
       ;; verify の腕は行の spec(charter)を直に読まない — 読むのは judgment.verify-plan-of / verify-plan-of-handle の 1 点。
       (assert (not-in "row.spec" verify-body) "verify の腕が行の spec(charter)を直に読む(R36)")
       (assert (not-in "\"charter\"" verify-body) "verify の腕が charter の鍵を直に読む(R36)"))
     (deftest test-adr-doe-agents-012-summarize-runs-claude-print-under-the-conversation-lease
       ;; R37 の針(構造): 判断は judgment の 1 点ずつ・claim-job は種類で summarize へ分岐する・summarize の腕は session を起こさず
       ;; turn-record を作らず中継へ押さない(札は借りる — verify との違い)・起こし方は summarize-argv-of の 1 点(claude -p・道具なし・
       ;; 位置引数)・prompt の定義点は summarize-prompt-of の 1 点・綴りは effects の 1 点。反例(挙動)は
       ;; sessionhost_acp_summarize_deftests.hy(launches == [] ∧ borrowed != [] を含む)。
       (setv judgment-lines (code-lines (/ ACP-DIR "judgment.hy")))
       (for [name ["(defk summarize-plan-of " "(defk summary-rows-covered-to " "(defk summary-region-of " "(defk summarize-prompt-of "
                   "(defk summarize-argv-of " "(defk summarize-output-of " "(defk summarize-of-handle " "(defk withdrawn-summarize-rows-of "
                   "(defk claude-home-of "]]
         (assert (= (len (lfor line judgment-lines :if (.startswith line name) line)) 1) f"summarize の判断は judgment の 1 点(R37): {name}"))
       (setv agentd-lines (code-lines (/ ACP-DIR "agentd.hy")))
       (for [name ["(defk claim-summarize-job " "(defk observe-summarize " "(defk recover-summarize " "(defk withdraw-summarize "
                   "(defk settle-summary-region " "(defk advance-summary-region " "(defk start-summary-region " "(defk read-summary-region "]]
         (assert (= (len (lfor line agentd-lines :if (.startswith line name) line)) 1) f"summarize の腕は agentd の 1 点(R37): {name}"))
       ;; claim-job: summarize の分岐は verify の分岐の直後・作業場の門と資格の出所より前。
       (setv claim-start (next (gfor [i line] (enumerate agentd-lines) :if (.startswith line "(defk claim-job ") i)))
       (setv claim-lines (cut agentd-lines claim-start (+ claim-start 100)))
       (setv summarize-at (next (gfor [i line] (enumerate claim-lines) :if (in "(claim-summarize-job settings state row now-ms)" line) i) None))
       (setv gate-at (next (gfor [i line] (enumerate claim-lines) :if (in "(work-dir-ready settings row plan now-ms)" line) i) None))
       (setv source-at (next (gfor [i line] (enumerate claim-lines) :if (in "(credential-source-of plan settings.custody-declared)" line) i) None))
       (assert (is-not summarize-at None) "claim-job が summarize へ分岐しない(R37)")
       (assert (and (is-not gate-at None) (< summarize-at gate-at)) "summarize の分岐が作業場の門より後(R37)")
       (assert (and (is-not source-at None) (< summarize-at source-at)) "summarize の分岐が資格の出所の判断より後(R37)")
       ;; summarize の腕は session を起こさず・turn-record を作らず・中継へ押さない(札は借りる)。
       (setv sum-start (next (gfor [i line] (enumerate agentd-lines) :if (.startswith line "(defk read-summary-region ") i)))
       (setv sum-end (next (gfor [i line] (enumerate agentd-lines) :if (.startswith line "(defk claim-verify-job ") i)))
       (setv sum-body (.join "\n" (cut agentd-lines sum-start sum-end)))
       (for [word ["SessionLaunch" "SessionResume" "SessionSend" "incarnate" "start-claimed" "TURN-RECORD-KIND" "AcpStreamPush" "push-frames" "mail-of"]]
         (assert (not-in word sum-body) f"summarize の腕が手番の腕の語を持つ(R37): {word}"))
       (assert (in "(CustodyLeaseBorrow :kind \"claude\" :account plan.account" sum-body) "summarize の腕が会話の profile の札を借りていない(R37)")
       (assert (in "(CommandStart :argv argv :cwd settings.summarize-runs-dir :env env)" sum-body) "summarize の腕が CommandStart(env つき)で起こしていない(R37)")
       (assert (in "(RecordReadSince :conversation-id conversation-id :since since :limit RECORD-PAGE-MAX-LIMIT :kinds RECORD-RAW-EVENT-KINDS)" sum-body)
               "区間の原文を前向きに原文の kind だけで読んでいない(R37)")
       ;; 実弾 2026-09-16 16:04(便 4): 答えの JSON(数 KB)を rc / pid 用の既定 256 字で読むと切れて non-JSON になる — 答えは大きな器で読む。
       (assert (in "(FsReadText :path command.out-path :max-chars SUMMARY-ANSWER-MAX-CHARS)" sum-body) "summarize の答えを小さな file の既定の上限で読んでいる(R37)")
       (assert (not-in "row.spec" sum-body) "summarize の腕が行の spec(charter)を直に読む(R37)")
       ;; 起こし方: claude -p・道具なし・session を残さない・位置引数。
       (setv argv-start (next (gfor [i line] (enumerate judgment-lines) :if (.startswith line "(defk summarize-argv-of ") i)))
       (setv argv-body (.join "\n" (cut judgment-lines argv-start (+ argv-start 14))))
       (for [needle ["-p --model" "--output-format json" "--no-session-persistence" "--tools \\\"\\\"" "--disable-slash-commands"
                     "(get paths \"pid\") claude-binary model (get paths \"prompt\") (get paths \"out\") (get paths \"log\") (get paths \"rc\")"]]
         (assert (in needle argv-body) f"起こし方の sh の 1 行が変わった(R37): {needle}"))
       ;; prompt の定義点は 1 つで、残す / 落とすの語を持つ。
       (setv prompt-start (next (gfor [i line] (enumerate judgment-lines) :if (.startswith line "(defk summarize-prompt-of ") i)))
       (setv prompt-body (.join "\n" (cut judgment-lines prompt-start (+ prompt-start 20))))
       (for [needle ["残す情報" "落とす情報" "決定" "未解決の問い" "道具の生の出力"]]
         (assert (in needle prompt-body) f"prompt が残す / 落とすの規則を名乗らない(R37): {needle}"))
       ;; 綴りは effects の 1 点。
       (setv effects-lines (code-lines (/ ACP-DIR "effects.py")))
       (for [needle ["CHARTER_KIND_SUMMARIZE: str = \"summarize\"" "CHARTER_SUMMARIZE_UNTIL_KEY: str = \"until\""
                     "SUMMARY_KIND: str = \"summary\"" "SUMMARY_STREAM_KIND: str = \"summary\"" "SUMMARY_EVENT_KIND: str = \"summary\""
                     "RECORD_RAW_EVENT_KINDS: tuple[str, ...] = (\"text\", \"tool_use\", \"tool_result\", \"system\", \"error\", \"user\")"
                     "    summarize_trigger_tokens: int = 500_000" "    summarize_model: str = \"claude-opus-5\""
                     "CONDITION_SUMMARIZE_OUTPUT_UNREADABLE: ConditionType = \"SummarizeOutputUnreadable\""
                     "CONDITION_SUMMARY_UNWRITABLE: ConditionType = \"SummaryUnwritable\"" "SUMMARY_ANSWER_MAX_CHARS: int = 4_194_304"]]
         (assert (any (gfor line effects-lines (.startswith line needle))) f"綴りは effects の 1 点(R37): {needle}"))
       ;; 反例の検が在る。
       (setv tests (.read-text (/ (. (Path __file__) parent parent parent) "packages" "doeff-agents" "tests" "sessionhost_acp_summarize_deftests.hy") :encoding "utf-8"))
       (for [name ["test-summarize-job-borrows-the-conversation-lease-and-runs-claude-print-without-a-session"
                   "test-summarize-job-writes-the-summary-body-to-the-record-service-and-the-claim-check-row"
                   "test-summarize-job-advances-region-by-region-and-skips-what-is-already-summarized"
                   "test-summarize-job-with-nothing-left-ends-with-zero-regions-and-no-lease"
                   "test-summarize-output-that-cannot-be-read-ends-with-a-condition-and-returns-the-lease"
                   "test-running-summarize-is-recovered-from-its-row-after-a-restart"
                   "test-withdrawn-summarize-stops-the-process-returns-the-lease-and-marks-interrupted"]]
         (assert (in (+ "(deftest " name) tests) f"R37 の反例の検が無い: {name}")))
     (deftest test-adr-doe-agents-012-summaries-are-triggered-at-turn-end-and-folded-first-on-rehydrate
       ;; R38 の針(構造): 契機の判断は judgment.summarize-due の 1 点で、撃つのは settle-record(文脈を測る同じ拍)・上端は turn-floor-of・
       ;; 再開は summaries-for → summary-floor-of → record-turns-for の floor → rehydrate-history-of の summaries・畳みは要約を先に・綴りは effects。
       (setv judgment-lines (code-lines (/ ACP-DIR "judgment.hy")))
       (for [name ["(defk summarize-due " "(defk turn-floor-of " "(defk summarize-job-id-of " "(defk summarize-job-spec-of "
                   "(defk summary-stream-id-of-ref " "(defk history-summary-of " "(defk summary-floor-of " "(defk history-summary-line "
                   "(defk summary-floor-at-of "]]
         (assert (= (len (lfor line judgment-lines :if (.startswith line name) line)) 1) f"要約の契機と再開の判断は judgment の 1 点(R38): {name}"))
       (assert (any (gfor line judgment-lines (.startswith line "(defk rehydrate-history-of [conversation-id messages source exclude budget fetched summaries floor-at]")))
               "畳みが要約(summaries)を受けていない(R38)")
       (setv fold-body (defk-body judgment-lines "rehydrate-history-of"))
       (assert (any (gfor line fold-body (in "(history-summary-line summary)" line))) "畳みが要約を history-summary-line の 1 点で組んでいない(R38)")
       (assert (any (gfor line fold-body (in ":summary-regions (- (len summary-lines) dropped-summaries)" line))) "HistoryFold が残した要約の数を運んでいない(R38)")
       ;; 追補 5: 要約は原文の後にだけ落ちる(段 2 の原文の落としの後に段 2b)・区間の番号を時刻に読まない・頭と見出しの数は 1 点。
       (assert (any (gfor line fold-body (in ":dropped-summaries dropped-summaries" line))) "HistoryFold が落とした要約の数を運んでいない(追補 5)")
       (setv raw-drop-at (next (gfor [i line] (enumerate fold-body) :if (in "(if (> (- (len blocks) dropped-turns) 1)" line) i) None))
       (setv summary-drop-at (next (gfor [i line] (enumerate fold-body) :if (in "(setv dropped-summaries (+ dropped-summaries 1)))" line) i) None))
       (assert (and (is-not raw-drop-at None) (is-not summary-drop-at None) (= (+ raw-drop-at 2) summary-drop-at))
               "要約が落ちるのは原文の手番が最新の 1 つだけになった後(if の否定の枝)(追補 5)")
       (assert (not (any (gfor line fold-body (in ":at summary.from-seq" line)))) "要約の区間の番号を時刻に読んでいる(追補 5)")
       (for [name ["(defk history-header-of " "(defk dropped-headline-counts "]]
         (assert (= (len (lfor line judgment-lines :if (.startswith line name) line)) 1) f"頭と見出しの数は judgment の 1 点(追補 5): {name}"))
       (setv agentd-lines (code-lines (/ ACP-DIR "agentd.hy")))
       (for [name ["(defk trigger-summarize " "(defk summaries-for "]]
         (assert (= (len (lfor line agentd-lines :if (.startswith line name) line)) 1) f"要約の契機と読みの腕は agentd の 1 点(R38): {name}"))
       (assert (any (gfor line agentd-lines (.startswith line "(defk record-turns-for [settings subject floor]"))) "原文の読みが floor を受けていない(R38)")
       (setv settle-body (defk-body agentd-lines "settle-record"))
       (assert (any (gfor line settle-body (in "(trigger-summarize settings job batch now-ms)" line))) "契機は settle-record(文脈を測る同じ拍)で撃つ(R38)")
       (setv history-body (defk-body agentd-lines "history-for"))
       (for [needle ["(summaries-for settings subject)" "(summary-floor-of summaries)" "(record-turns-for settings subject floor)"
                     "(RecordReadSince :conversation-id subject :since (max 0 (- floor 1)) :limit 1 :kinds #())" "(summary-floor-at-of edge floor)"
                     "settings.rehydrate-history-byte-budget (get read 0) summaries floor-at))"]]
         (assert (any (gfor line history-body (in needle line))) f"再開の読みが要約を先に読んでいない / 郵便の下限を読んでいない(R38・追補 6): {needle}"))
       (assert (any (gfor line fold-body (in ":summarized-mails summarized-mails" line))) "HistoryFold が要約に任せた郵便の数を運んでいない(追補 6)")
       (assert (= (len (lfor line agentd-lines :if (in "{fold.summarized-mails} mails left to the summaries" line) line)) 1) "agentd の log に要約に任せた郵便の数(追補 6)")
       (setv trigger-body (defk-body agentd-lines "trigger-summarize"))
       (for [needle ["(summarize-due batch.context settings.summarize-trigger-tokens)" "(turn-floor-of page.events)" "(summary-rows-covered-to rows job.subject)"
                     "(AcpCreate :namespace AGENT-JOB-NAMESPACE :kind AGENT-JOB-KIND :resource-id summarize-id :spec spec)"]]
         (assert (any (gfor line trigger-body (in needle line))) f"契機の腕の形が違う(R38): {needle}"))
       (assert (not (any (gfor line trigger-body (in "condition-of" line)))) "契機は手番の結末に条件を混ぜない(R38)")
       (assert (= (len (lfor line agentd-lines :if (in "{fold.summary-regions} summaries" line) line)) 1) "agentd の log に要約の数(R38)")
       (setv effects-lines (code-lines (/ ACP-DIR "effects.py")))
       (for [needle ["METRIC_SUMMARIZE_TRIGGERS_TOTAL = \"agentd_summarize_triggers_total\"" "HISTORY_SUMMARY_KIND = \"要約\""
                     "SUMMARIZE_JOB_ID_PREFIX = \"aj-summary-\"" "    summary_regions: int = 0" "    dropped_summaries: int = 0" "    summarized_mails: int = 0"]]
         (assert (any (gfor line effects-lines (.startswith line needle))) f"綴りは effects の 1 点(R38): {needle}"))
       ;; 反例の検が在る。
       (setv tests-dir (/ (. (Path __file__) parent parent parent) "packages" "doeff-agents" "tests"))
       (setv rehydrate-tests (.read-text (/ tests-dir "sessionhost_acp_rehydrate_deftests.hy") :encoding "utf-8"))
       (for [name ["test-summaries-fold-first-in-region-order-and-drop-only-after-raw-under-the-budget"
                   "test-the-production-shape-keeps-all-summaries-and-drops-raw-turns-with-a-real-period"
                   "test-mail-covered-by-the-summaries-is-left-to-them-and-does-not-eat-the-budget"
                   "test-rehydrate-folds-existing-summaries-first-and-reads-raw-only-after-their-floor"]]
         (assert (in (+ "(deftest " name) rehydrate-tests) f"R38 の反例の検が無い: {name}"))
       (setv compact-tests (.read-text (/ tests-dir "sessionhost_acp_compact_deftests.hy") :encoding "utf-8"))
       (assert (in "(deftest test-a-turn-end-over-the-summarize-trigger-writes-one-summarize-job-for-the-record-before-the-turn" compact-tests)
               "R38 の契機の検が無い"))
     (deftest test-adr-doe-agents-012-no-agora-ledger-words-in-sessionhost
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
       ;; 家も借りた口座の家ちょうど(段 10 lane 10r・agora-redesign #99)— 検体の charter が名乗る機体の家
       ;; (binding の codex_home /bundle)を読まない。ACP は charter に binding を書かないので、読むと家が無く札が使われない。
       (assert (= (get (object-at launch2 "binding") "profile_dir") "/homes/codex/acct"))
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
       (assert (= (len (lfor line agentd-lines :if (in "(except [e IO-FAILURES]" line) line)) 9)
               "縁は heartbeat・profile の観測・受け・割り込みの配達・job ごと・verify の命令ごと(段 12 lane 12a R36)・summarize ごと(段 12 lane 12j R37)・spool の送り・spool の書きの 9 つ(ADR-DOE-AGENTS-012 R9・R18・R21・R36・R37・段 9f lane 9f-2)")
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
       ;; 段 10 lane 10ba(agora-redesign #115)の追随: lease の書き手は tick と**独立した heartbeat の 1 点**に
       ;; なった(tick の I/O が TTL を超えて塞がっても lease が切れないため)。⇒ この針は「誰が書くか」を撃つ —
       ;; tick は status.lease を書かず、heartbeat が書く。
       (setv node (status-of (get shared.acp.rows "default:node:mac-1")))
       (assert (not (in "lease" node))
               "tick が status.lease を書いている(lease の書き手は heartbeat の 1 点・段 10 lane 10ba)")
       (.heartbeat shared)
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
       (assert (= (get world.sessions.sends -1) #(warm-sid (mailed "m-t-2" "second") True)))
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
                   "fallback-arm-of" "transcript-candidates-of" "conversation-recorded-of"]]
         (assert (= (len (lfor line judgment-lines :if (.startswith line f"(defk {name} ") line)) 1) name))
       (assert (any (gfor line judgment-lines (in "\"sessionId\" job.session-id" line)))
               "turn-record の spec は sessionId を名乗る(R20)")
       ;; 追補 4(段 12 lane 12j・agora-redesign #233 / #176): 候補の無さで launch と決めない — 記録の service への 1 読みの答え(recorded)を腕へ渡す。
       (assert (any (gfor line judgment-lines (in "(is candidate None) (ArmChoice :arm (if recorded NEXT-ARM-REHYDRATE NEXT-ARM-LAUNCH) :source None :retire None)" line)))
               "候補なしの腕は recorded で rehydrate / launch に分かれる(追補 4)")
       (setv agentd-lines-recorded (code-lines (/ ACP-DIR "agentd.hy")))
       (assert (= (len (lfor line agentd-lines-recorded :if (in "(RecordReadSince :conversation-id subject :since 0 :limit 1 :kinds RECORD-RAW-EVENT-KINDS)" line) line)) 1)
               "記録の在否の問いは 1 読み(since 0・limit 1・原文の kind)(追補 4)")
       (assert (= (len (lfor line agentd-lines-recorded :if (in "(<- recorded bool (conversation-recorded-of probe))" line) line)) 1)
               "記録の在否の判断は judgment.conversation-recorded-of の 1 点(追補 4)")
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
       (assert (not-in #(warm (mailed "m-t-2" "second") True) world.sessions.sends) "別の家の session に送らない(R20)")
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
       ;; 段 10 lane 10r 追補: 郵便の見出しの綴りは mail-heading-of の 1 点で、手番の文を組むのは mail-turn-text-of —
       ;; 1 手番目の畳みと温かい send は message-bodies-of の同じ bodies を、割り込みの注入は agentd.hy の腕が同じ関数を呼ぶ。
       (assert (= (len (lfor line judgment-lines :if (.startswith line "(defk mail-heading-of ") line)) 1))
       (assert (= (len (lfor line judgment-lines :if (in "(<- text str (mail-turn-text-of input-id row.spec body))" line) line)) 1))
       (assert (= (len (lfor line (code-lines (/ ACP-DIR "agentd.hy")) :if (in "(<- text str (mail-turn-text-of message-id message.spec body))" line) line)) 1))
       (for [line (code-lines (/ ACP-DIR "agentd.hy"))]
         (assert (not-in "BACKEND-HEADLESS" line) f"agentd.hy は backend の語を比較しない(R16): {line}")
         (assert (not-in "\"headless\"" line) f"agentd.hy は backend の語を比較しない(R16): {line}"))
       (setv headless (World))
       (setv headless.settings (AgentdSettings :node-name "mac-1" :homes-root "/homes" :backend-kind "headless" :stream-capability "events"))
       (setv headless.sessions (FakeSessions :backend-kind "headless" :events-root "/events"))
       (.put-row headless.acp (message-row "m-f" "hello"))
       (.put-row headless.acp (turn-row "f-1" "conv-f" "m-f" 500))
       (.tick headless 0)
       (assert (= (get (get headless.sessions.launches -1) "prompt") (+ "go\n\n" (mailed "m-f" "hello"))))
       (assert (= headless.sessions.sends []))
       (assert (is-not (.get headless.acp.rows "default:turn-record:f-1") None))
       (assert (= (len headless.state.jobs) 1))
       (setv tui (World))
       (.put-row tui.acp (message-row "m-t" "hello"))
       (.put-row tui.acp (turn-row "t-1" "conv-t" "m-t" 500))
       (.tick tui 0)
       (assert (= (get (get tui.sessions.launches -1) "prompt") "go"))
       (assert (= tui.sessions.sends [#((sid-of tui "t-1") (mailed "m-t" "hello") True)])))
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
                                            "--ownership" "company" "--ownership-proof" "gce-project:p-1"
                                            "--capacity" "2" "--places" "company,personal"))
                         (JoinDeclaration :tables {"schema" "doeff.agentd-join.v1"
                                                   "agentd" {"node_name" "gcp-0"}
                                                   "record" {"url" "http://record:8874"}})
                         "/state")))
       (assert (isinstance spec JoinSpec))
       (assert (= spec.node-name "gcp-0"))
       ;; 段 10 lane 10d(R28 / R30): node の capacity と機体の置き場も同じ束で運ばれ、同じ読み(settings-from-env)が読む。
       (assert (= spec.capacity 2))
       (assert (= spec.places #("company" "personal")))
       (setv plan (run (join-plan-of spec)))
       (assert (isinstance plan JoinPlan))
       (setv env (dict plan.env))
       (setv settings (settings-from-env env plan.host-argv))
       (assert (= settings.node-name "gcp-0"))
       (assert (= settings.node-capacity 2))
       (assert (= settings.places #("company" "personal")))
       (assert (= settings.backend-kind "headless"))
       (assert (= settings.ownership (Ownership :grade "company" :proof "gce-project:p-1")))
       (assert (is settings.record-enabled True))
       (assert (is (. (acp-valve (list plan.host-argv) env) enabled) True))
       ;; 本文の行き先(段 9f lane 9f-6): 宣言に [record] が無い束は同じ読み(settings-from-env)が参加を断る —
       ;; 判断は join.record-sink-of の 1 点(宣言の検・届くかは検めない)。焦点の検は tests/sessionhost_acp_record_deftests.hy。
       (assert (= (len (lfor line join-lines :if (.startswith line "(defk record-sink-of ") line)) 1))
       (setv unsinked (dict (. (run (join-plan-of (run (join-spec-of
                                                          (JoinArgv :items #("--server" "http://acp:8868" "--token-file" "/t" "--capacity" "1" "--places" "personal"))
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
         (run (join-spec-of (JoinArgv :items #("--server" "http://a" "--token-file" "/t" "--ownership" "company" "--capacity" "1" "--places" "company"))
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
       ;; 会社の口座の行(boundary company)を観測の列に持つのは所有 company を宣言した機体だけ(段 10 lane 10y)。
       ;; 葉の断り(ca)は宣言の所有とは別に agentcli が判じる — ここでは会社所有の機体で葉が断る形を撃つ。
       (setv world (World))
       (setv world.settings (replace world.settings :ownership (Ownership :grade "company" :proof "declared")))
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
       (assert (= (len (lfor line agentd-lines :if (in "(next-arm-for-job candidate view home effort compact recorded)" line) line)) 1) "agentd.hy が effort を腕へ渡さない(R24)")
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
     (deftest test-adr-doe-agents-012-stop-drains-declared-nodes-before-closing
       ;; R39 の針(構造): 排水の待ちは runtime.drain_until の 1 点・停止の腕は閉じる前に drain_for_stop を呼ぶ・loop は drain が立てば
       ;; settings.draining で回す・capacity の判断は judgment.declared-capacity-of の 1 点(node-spec-of / node-spec-declared の両方が読む)・
       ;; 排水の最中の受けは claim しない・宣言の鍵と env と欄の綴りは 1 点ずつ・反例の検が在る。
       (setv runtime-lines (code-lines (/ ACP-DIR "runtime.py")))
       (assert (= (len (lfor line runtime-lines :if (.startswith line "def drain_until(") line)) 1) "排水の待ちは runtime.drain_until の 1 点(R39)")
       (assert (= (len (lfor line runtime-lines :if (.startswith line "    def drain_for_stop(self, reason: str) -> int:") line)) 1) "停止の腕の排水は drain_for_stop の 1 点(R39)")
       (setv close-body (lfor line runtime-lines :if (in "self.drain_for_stop(reason)" line) line))
       (assert (= (len close-body) 1) "close_for_stop は閉じる前に排水を呼ぶ(R39)")
       (setv drain-at (next (gfor [i line] (enumerate runtime-lines) :if (in "self.drain_for_stop(reason)" line) i) None))
       (setv stop-at (next (gfor [i line] (enumerate runtime-lines) :if (and (is-not drain-at None) (> i drain-at) (in "self.stop.set()" line)) i) None))
       (assert (and (is-not drain-at None) (is-not stop-at None) (< drain-at stop-at)) "排水は loop を止める前(R39)")
       (assert (any (gfor line runtime-lines (in "replace(settings, draining=True) if draining else settings" line))) "loop は drain の合図を settings.draining に写す(R39)")
       (assert (any (gfor line runtime-lines (in "drain_seconds=_drain_seconds_of_env(env)" line))) "宣言の排水の上限は settings の 1 欄へ(R39)")
       (setv judgment-lines (code-lines (/ ACP-DIR "judgment.hy")))
       (assert (= (len (lfor line judgment-lines :if (.startswith line "(defk declared-capacity-of ") line)) 1) "capacity の判断は 1 点(R39)")
       (assert (= (len (lfor line judgment-lines :if (in "(<- capacity int (declared-capacity-of settings))" line) line)) 2) "node-spec-of と node-spec-declared が同じ 1 点を読む(R39)")
       (assert (not (any (gfor line judgment-lines (in "\"capacity\" settings.node-capacity" line)))) "spec の capacity を宣言から直に写す第 2 の点が残っている(R39)")
       (setv agentd-lines (code-lines (/ ACP-DIR "agentd.hy")))
       (assert (= (len (lfor line agentd-lines :if (in "(if settings.draining" line) line)) 1) "排水の最中の受けは claim しない(R39)")
       (assert (any (gfor line agentd-lines (in "leaving Bound job {row.resource-id} unclaimed" line))) "claim しない行は log に名乗る(R39)")
       (setv effects-lines (code-lines (/ ACP-DIR "effects.py")))
       (for [needle ["DRAIN_SECONDS_ENV = \"DOEFF_AGENTD_DRAIN_SECONDS\"" "    drain_seconds: int = 0" "    draining: bool = False"]]
         (assert (any (gfor line effects-lines (.startswith line needle))) f"綴りは effects の 1 点(R39): {needle}"))
       (setv join-lines (code-lines (/ ACP-DIR "join.hy")))
       (assert (any (gfor line join-lines (.startswith line "(setv KEY-DRAIN-SECONDS \"drain_seconds\")"))) "宣言の鍵は join の 1 点(R39)")
       (assert (= (len (lfor line join-lines :if (.startswith line "(defk drain-seconds-of ") line)) 1) "読みは join.drain-seconds-of の 1 点(R39)")
       (setv tests (.read-text (/ (. (Path __file__) parent parent parent) "packages" "doeff-agents" "tests" "test_sessionhost_acp.py") :encoding "utf-8"))
       (for [name ["test_declared_capacity_is_zero_while_draining_and_the_declaration_otherwise"
                   "test_a_draining_agentd_leaves_bound_jobs_unclaimed_and_names_it"
                   "test_drain_until_returns_when_the_turns_end_or_the_deadline_passes"
                   "test_close_for_stop_drains_before_closing_when_the_node_declares_drain_seconds"
                   "test_join_spec_reads_drain_seconds_and_settings_carry_it"]]
         (assert (in (+ "def " name) tests) f"R39 の反例の検が無い: {name}")))
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
       (assert (any (gfor line judgment-lines (.startswith line "(defk next-arm-for-job [candidate view home effort compact recorded]"))) "圧縮は next-arm-for-job の 5 つ目の引数(R27)")
       (assert (any (gfor line judgment-lines (in "compact (ArmChoice :arm NEXT-ARM-REHYDRATE :source None :retire (if alive candidate None) :compacts True)" line))) "圧縮の腕は rehydrate + compacts(R27)")
       (setv defer-at (next (gfor [i line] (enumerate judgment-lines) :if (in "(and alive (not idle) live-backend) (ArmChoice :arm NEXT-ARM-DEFER" line) i) None))
       (setv compact-at (next (gfor [i line] (enumerate judgment-lines) :if (in "compact (ArmChoice :arm NEXT-ARM-REHYDRATE" line) i) None))
       (assert (and (is-not defer-at None) (is-not compact-at None) (< defer-at compact-at)) "手番の途中は圧縮より defer が先(R27)")
       (assert (= (len (lfor line judgment-lines :if (in ":context (if (is context-tokens None) None {\"tokens\" context-tokens \"window\" context-window})" line) line)) 1) "claude の実測は deltas の 1 点(R27)")
       (assert (= (len (lfor line judgment-lines :if (in "(codex-context-of (if (isinstance last-usage dict) last-usage None)" line) line)) 2) "codex の実測は rollout と app-server の 2 か所が同じ 1 点を呼ぶ(R27)")
       (setv agentd-lines (code-lines (/ ACP-DIR "agentd.hy")))
       (assert (= (len (lfor line agentd-lines :if (in "(<- due bool (compaction-due compact-at percent))" line) line)) 1) "claim の腕は compaction-due を 1 度呼ぶ(R27)")
       (assert (= (len (lfor line agentd-lines :if (in "(next-arm-for-job candidate view home effort compact recorded)" line) line)) 1) "腕の呼び手は 1 つ(R27)")
       (assert (= (len (lfor line agentd-lines :if (in "(<- measured AgentdState (with-context-percent state job.session-id percent))" line) line)) 2) "実測を置く点は記録の腕の 2 か所(settle-record・interrupt-job)(R27)")
       (assert (= (len (lfor line agentd-lines :if (in "(AcpGetRow :key conversation-key)" line) line)) 1) "会話の行は鍵で 1 回読む(R27)")
       (assert (= (len (lfor line agentd-lines :if (in "\"metric\" METRIC-COMPACTIONS-TOTAL" line) line)) 1) "計器は 1 点(R27)")
       ;; 追補 3 の針: 会話の身元の env は judgment.charter-with-conversation-env の 1 点で、incarnation-charter-of が呼ぶ。名は effects の 1 点。
       (assert (= (len (lfor line judgment-lines :if (.startswith line "(defk charter-with-conversation-env ") line)) 1) "会話の身元の env は 1 点(R27 追補 3)")
       (assert (= (len (lfor line judgment-lines :if (in "(charter-with-conversation-env with-id (str (get attribution \"conversationId\")) opener)" line) line)) 1) "incarnation-charter-of が呼ぶ(R27 追補 3)")
       (assert (= (len (lfor line agentd-lines :if (in "(<- opener (| str None) (conversation-opener-of conversation-row))" line) line)) 1) "opener は claim の腕が会話の行から読む(R27 追補 3)")
       (for [line agentd-lines]
         (assert (not-in "AGORA_CONVERSATION_ID" line) f"agentd.hy は env の名を直に持たない(R27 追補 3): {line}"))
       (for [line agentd-lines]
         (assert (not-in "contextPercent" line) f"turn-record に文脈の欄を書かない(R27): {line}")
         (assert (not-in "\"compactAt\"" line) f"agentd.hy は compactAt の綴りを直に読まない(R27): {line}"))
       (setv effects-lines (code-lines (/ ACP-DIR "effects.py")))
       (assert (any (gfor line effects-lines (.startswith line "METRIC_COMPACTIONS_TOTAL = \"agentd_compactions_total\""))) "計器の名は effects の 1 点(R27)")
       (assert (any (gfor line effects-lines (.startswith line "    compacts: bool = False"))) "ArmChoice.compacts(R27)")
       (assert (any (gfor line effects-lines (.startswith line "    context_by_session: tuple[tuple[str, int], ...] = ()"))) "実測の cache は AgentdState の 1 欄(R27)")
       (assert (any (gfor line effects-lines (.startswith line "CONVERSATION_ID_ENV = \"AGORA_CONVERSATION_ID\""))) "env の名は effects の 1 点(R27 追補 3)")
       (assert (any (gfor line effects-lines (.startswith line "SEAT_OPENER_ENV = \"AGORA_SEAT_OPENER\""))) "env の名は effects の 1 点(R27 追補 3)")
       (setv reads (json.loads (.read-text (/ (. (Path __file__) parent parent) "contracts" "reads.json") :encoding "utf-8")))
       (assert (in "kinds.conversation.schema.properties.status.properties.agent.properties.compactAt" (get (get reads "reads") "agora-kinds")) "読む欄の宣言(R27)")
       (setv tests (.read-text (/ (. (Path __file__) parent parent parent) "packages" "doeff-agents" "tests" "sessionhost_acp_compact_deftests.hy") :encoding "utf-8"))
       (for [name ["test-a-turn-end-measures-the-context-and-the-next-turn-over-compact-at-rehydrates"
                   "test-below-the-threshold-or-without-a-declaration-the-warm-session-is-kept"
                   "test-claude-events-measure-the-last-message-against-the-result-context-window"
                   "test-codex-rollout-and-app-server-measure-the-last-response"
                   "test-every-incarnation-arm-puts-the-conversation-identity-in-the-process-env"]]
         (assert (in (+ "(deftest " name) tests) f"R27 の反例の検が無い: {name}")))
     (deftest test-adr-doe-agents-012-node-row-is-named-by-the-machine
       ;; R28 の針(構造): node の行の spec の形は judgment の 2 点・join-tick が AcpCreate / AcpPutSpec で名乗る・capacity は
       ;; join.capacity-of の 1 点で runtime もそれを読む・register-node の文言が agentd の腕に残っていない・契約の写しの node の
       ;; writers(create / update = agentd・withdraw = acp-scheduling)。反例(挙動)は test_sessionhost_acp.py の 5 本。
       (setv judgment-lines (code-lines (/ ACP-DIR "judgment.hy")))
       (assert (= (len (lfor line judgment-lines :if (.startswith line "(defk node-spec-of ") line)) 1) "作る時の spec は node-spec-of の 1 点(R28)")
       (assert (= (len (lfor line judgment-lines :if (.startswith line "(defk node-spec-declared ") line)) 1) "揃える時の spec は node-spec-declared の 1 点(R28)")
       (setv agentd-lines (code-lines (/ ACP-DIR "agentd.hy")))
       (assert (any (gfor line agentd-lines (in "(AcpCreate :namespace AGORA-KINDS-NAMESPACE :kind NODE-KIND :resource-id resource-id :spec spec" line))) "行が無ければ agentd が作る(R28 — 鍵は node-resource-id-of の incarnation・段 10 lane 10d 便 4)")
       (assert (any (gfor line agentd-lines (in ":declaration-sha256 settings.declaration-sha256))" line))) "誕生は読んだ宣言 file の指紋を運ぶ(R28・段 10 lane 10y)")
       (assert (any (gfor line agentd-lines (in "(AcpPutSpec :row node :spec declared :declaration-sha256 settings.declaration-sha256)" line))) "宣言と違えば agentd が指紋つきで揃える(R28・段 10 lane 10y)")
       (setv join-lines (code-lines (/ ACP-DIR "join.hy")))
       (assert (= (len (lfor line join-lines :if (.startswith line "(defk declaration-sha256-of ") line)) 1) "指紋の形の検は join.declaration-sha256-of の 1 点(R28・段 10 lane 10y)")
       (assert (= (len (lfor line join-lines :if (.startswith line "(defk work-roots-of ") line)) 1) "作業場の根の形の検は join.work-roots-of の 1 点(R28・段 10 lane 10y 案 C)")
       (assert (= (len (lfor line (code-lines (/ ACP-DIR "judgment.hy")) :if (.startswith line "(defk node-work-roots-of ") line)) 1) "spec.workRoots を置くのは judgment.node-work-roots-of の 1 点(R28・段 10 lane 10y 案 C)")
       (assert (any (gfor line (code-lines (/ ACP-DIR "effects.py")) (.startswith line "DECLARATION_FINGERPRINT_HEADER = \"x-declaration-sha256\""))) "header の綴りは effects の 1 点(R28・段 10 lane 10y)")
       (for [line agentd-lines]
         (assert (not-in "register-node" line) f"node の行を作るのは人の道具ではない(R28): {line}"))
       (setv join-lines (code-lines (/ ACP-DIR "join.hy")))
       (assert (= (len (lfor line join-lines :if (.startswith line "(defk capacity-of ") line)) 1) "capacity の読みは capacity-of の 1 点(R28)")
       (setv runtime-lines (code-lines (/ ACP-DIR "runtime.py")))
       (assert (any (gfor line runtime-lines (in "join.capacity_of(env.get(CAPACITY_ENV))" line))) "runtime は capacity を join の 1 点で読む(R28)")
       (setv kinds (json.loads (.read-text (/ (. (Path __file__) parent parent) "contracts" "agora-kinds.json") :encoding "utf-8")))
       (setv writers (get kinds "kinds" "node" "declaration" "writers"))
       (assert (= (get writers "create") ["agentd"]) "契約の写しの node の create の書き手は agentd(R28)")
       (assert (= (get writers "update") ["agentd"]) "契約の写しの node の update の書き手は agentd(R28)")
       (assert (= (get writers "withdraw") ["acp-scheduling"]) "withdraw は配置 E のまま(R28)"))
     (deftest test-adr-doe-agents-012-interrupts-are-read-within-the-deadline
       ;; R29 の針(構造): 判断は judgment.hy の 1 点ずつ・期限は charter だけ(既定の定数なし・方策 / 会話の行を読まない)・
       ;; 停止の合図の腕は agentd.settle-interrupts の 1 点・器の作法(control_request・uuid)は headless_protocol.py だけ・
       ;; host の口は session.escalate・codex は出さない・能力の表に interrupt。
       (setv judgment-lines (code-lines (/ ACP-DIR "judgment.hy")))
       (for [name ["escalation-seconds-of-charter" "interrupt-reads-of" "interrupts-due-for-escalation"
                   "interrupt-marks-status-of" "recovered-interrupts-of" "with-injected-interrupts"]]
         (assert (= (len (lfor line judgment-lines :if (.startswith line f"(defk {name} ") line)) 1) name))
       (setv agentd-lines (code-lines (/ ACP-DIR "agentd.hy")))
       (assert (= (len (lfor line agentd-lines :if (.startswith line "(defk settle-interrupts ") line)) 1)
               "停止の合図の腕は agentd.settle-interrupts の 1 点(R29)")
       (assert (= (len (lfor line agentd-lines :if (in "(SessionEscalate :session-id" line) line)) 1)
               "合図を出す点は 1 つ(R29)")
       (assert (any (gfor line agentd-lines (in ":ref message-id" line)))
               "注入の行の名は Message の id(R29)")
       (for [line (+ agentd-lines judgment-lines)]
         (assert (not-in "delivery-policy" line) f"agentd は方策の行を読まない — 期限は charter だけ(R29): {line}")
         (assert (not-in "control_request" line) f"agentd / judgment は器の作法の綴り control_request を書かない(R29): {line}"))
       (setv effects-lines (code-lines (/ ACP-DIR "effects.py")))
       (assert (= (len (lfor line effects-lines :if (.startswith line "JOB_INTERRUPTS_READ_KEY: str = \"interruptsRead\"") line)) 1))
       (assert (= (len (lfor line effects-lines :if (.startswith line "JOB_INTERRUPTS_ESCALATED_KEY: str = \"interruptsEscalated\"") line)) 1))
       (assert (= (len (lfor line effects-lines :if (.startswith line "CHARTER_INTERRUPT_ESCALATION_KEY: str = \"interruptEscalationSeconds\"") line)) 1))
       (assert (= (len (lfor line effects-lines :if (.startswith line "    interrupt_escalation_seconds: int | None = None") line)) 1)
               "期限の memory は InFlightJob の 1 欄・既定は None(宣言なし)で数の既定を置かない(R29)")
       (for [line effects-lines]
         (assert (not (re.search r"ESCALATION_SECONDS\w*\s*(:\s*int)?\s*=\s*\d" line)) f"期限の既定の定数を置かない(R29): {line}"))
       (assert (any (gfor line effects-lines (in "\"claude\": \"steer-then-stop\"" line))))
       (assert (any (gfor line effects-lines (in "\"codex\": \"stop\"" line))))
       (setv handler-lines (code-lines (/ ACP-DIR "handlers.py")))
       (assert (= (len (lfor line handler-lines :if (in "\"session.escalate\"" line) line)) 1)
               "sessionhost への停止の合図の口は session.escalate の 1 語(R29)")
       (setv protocol-lines (code-lines (/ SESSIONHOST-DIR "headless_protocol.py")))
       (assert (= (len (lfor line protocol-lines :if (.startswith line "    def escalate(self) -> Escalation:") line)) 2)
               "停止の合図の作法は Dialogue.escalate の 2 腕(claude / codex)(R29)")
       (assert (any (gfor line protocol-lines (in "\"subtype\": \"interrupt\"" line)))
               "claude の停止の合図は control_request interrupt(R29)")
       (assert (any (gfor line protocol-lines (in "record[\"uuid\"] = ref" line)))
               "注入の行の名は user の行の uuid(R29)")
       (setv host-lines (code-lines (/ SESSIONHOST-DIR "host.hy")))
       (assert (= (len (lfor line host-lines :if (in "(when (= method \"session.escalate\")" line) line)) 1))
       (assert (any (gfor line host-lines (in "(headless-escalate-program sid)" line))))
       ;; 反例(挙動): 期限を越えた未読の割り込みは合図 1 つ・止めた印・読んだ印は started の seq。
       (setv world (HeadlessWorld))
       (.put-row world.acp (message-row "m-x" "first"))
       (.put-row world.acp (turn-row-timed "t-3" "conv-e" "m-x" (- world.local.now-ms 300) 20))
       (.tick world 1000)
       (setv sid (sid-of world "t-3"))
       (setv (get world.local.transcripts f"/events/{sid}.events.jsonl")
             (+ (json.dumps {"type" "system" "subtype" "init" "session_id" sid}) "\n"))
       (.tick world 1000)
       (.put-row world.acp (message-row "m-i1" "stop"))
       (setv running (get world.acp.rows "acp-system:agent-job:t-3"))
       (setv #^ JSONObject placed (dict (status-of running)))
       (setv (get placed "interrupts") ["m-i1"])
       (.put-row world.acp (AcpRow :namespace running.namespace :key running.key :kind running.kind :resource-id running.resource-id
                                   :version running.version :generation (+ running.generation 1) :created-at-ms running.created-at-ms
                                   :labels running.labels :payload running.payload :spec running.spec :status placed))
       (.tick world 1000)
       (assert (= world.sessions.interjection-refs [#(sid "m-i1")]) "注入の行の名は Message の id(R29)")
       (.tick world 19000)
       (assert (= world.sessions.escalations []) "期限の手前では出さない(R29)")
       (.tick world 2000)
       (assert (= world.sessions.escalations [sid]) "期限を越えた未読の割り込みは合図 1 つ(R29)")
       (setv after (status-of (get world.acp.rows "acp-system:agent-job:t-3")))
       (assert (= (get after "interruptsEscalated") {"m-i1" world.local.now-ms}) "止めた印は時刻(R29)")
       (.tick world 1000)
       (assert (= world.sessions.escalations [sid]) "二度出さない(R29)")
       (setv (get world.local.transcripts f"/events/{sid}.events.jsonl")
             (+ (get world.local.transcripts f"/events/{sid}.events.jsonl")
                (json.dumps {"type" "command_lifecycle" "command_uuid" "m-i1" "state" "started"}) "\n"))
       (.tick world 1000)
       (setv read-after (status-of (get world.acp.rows "acp-system:agent-job:t-3")))
       (setv read-marks (get read-after "interruptsRead"))
       (assert (isinstance read-marks dict))
       (assert (in "m-i1" read-marks) "読んだ印は started の seq(R29)")
       (assert (= (get read-after "phase") "Running") "手番は続く(R29)"))
     (deftest test-adr-doe-agents-012-turn-credential-rides-the-turn
       ;; R30 の針(構造): 借りの 2 段は handlers の 1 点・預かり所の URL の既定値は無い・置き場の語彙と読みは
       ;; 1 点ずつ・手番ごとの env の判断は judgment の 1 点・送りの関所は launch と同じ 1 点・行に札を残さない・
       ;; 継ぐ env は許可の名簿。反例(挙動)は test_sessionhost_acp.py と test_sessionhost_headless.py の 9 本。
       (setv handler-lines (code-lines (/ ACP-DIR "handlers.py")))
       (assert (= (len (lfor line handler-lines :if (.startswith line "    def _borrow(self, effect: CustodyLeaseBorrow) -> LeaseOutcome:") line)) 1)
               "借りの判断は CustodyHttp._borrow の 1 点(R30)")
       (assert (= (len (lfor line handler-lines :if (in "f\"{self._base_url}/lease/{effect.kind}\"" line) line)) 1)
               "貸与は master の /lease/{kind} へ 1 度(R30)")
       (assert (= (len (lfor line handler-lines :if (in "f\"{worker_url.rstrip('/')}/redeem\"" line) line)) 1)
               "引換券を換えるのは口座の worker の /redeem へ 1 度(R30)")
       (assert (any (gfor line handler-lines (in "_UNDECLARED" line)))
               "宣言の無い預かり所は型付きに断る(R30)")
       (for [path (sorted (.glob (/ ACP-DIR "..") "**/*.py"))]
         (for [line (code-lines path)]
           (assert (not-in "CUSTODY_URL_DEFAULT" line)
                   f"預かり所の URL に既定値を置かない(R30): {path.name}: {line}")))
       (setv effects-lines (code-lines (/ ACP-DIR "effects.py")))
       (assert (any (gfor line effects-lines (.startswith line "AGENTD_PLACES")))
               "置き場の閉語彙は effects の 1 点(R30)")
       (assert (any (gfor line effects-lines (.startswith line "PLACES_ENV = \"DOEFF_AGENTD_PLACES\"")))
               "置き場の集合の env の名は effects の 1 点(R30・段 11 lane 11u)")
       (assert (not (any (gfor line effects-lines (.startswith line "PLACE_ENV = ")))) "退役した 1 値の env の名が effects に残っている(段 11 lane 11u)")
       (assert (any (gfor line effects-lines (.startswith line "NODE_SPEC_PLACES = \"places\""))) "集合の欄の綴りは effects の 1 点(段 11 lane 11u)")
       (assert (any (gfor line effects-lines (in "CONDITION_CREDENTIAL_PLACE_MISMATCH" line)))
               "食い違いの条件の名は effects の 1 点(R30)")
       (setv join-lines (code-lines (/ ACP-DIR "join.hy")))
       (assert (= (len (lfor line join-lines :if (.startswith line "(defk places-of ") line)) 1)
               "置き場の集合の読みは join.places-of の 1 点(R30・段 11 lane 11u)")
       (assert (= (len (lfor line join-lines :if (.startswith line "(defk place-of ") line)) 0)
               "1 値の読み join.place-of が残っている(互換の読み替え — 段 11 lane 11u)")
       (setv judgment-lines (code-lines (/ ACP-DIR "judgment.hy")))
       (for [name ["credential-place-of" "credential-place-mismatch" "turn-session-env-of" "node-labels-of" "node-places-of"]]
         (assert (= (len (lfor line judgment-lines :if (.startswith line f"(defk {name} ") line)) 1) name))
       (setv agentd-lines (code-lines (/ ACP-DIR "agentd.hy")))
       (assert (= (len (lfor line agentd-lines :if (in "(credential-place-mismatch settings.places boundary)" line) line)) 1)
               "置き場の集合の突合は claim の腕で 1 度(R30・段 11 lane 11u)")
       ;; 段 11 lane 11u: 判定は集合の含有(両向き)— 1 値の不一致(!=)に戻さない。
       (assert (any (gfor line judgment-lines (in "(and (bool places) (is-not boundary None) (not-in boundary places))" line)))
               "置き場の突合が『boundary が集合に無い』の形でない(段 11 lane 11u)")
       (assert (= (len (lfor line agentd-lines :if (in "(turn-session-env-of lease)" line) line)) 1)
               "手番ごとの env を組む点は 1 つ(R30)")
       (for [line agentd-lines]
         (assert (not-in "CLAUDE_CODE_OAUTH_TOKEN" line)
                 f"agentd.hy は札の env の名を直に持たない(R30): {line}"))
       (setv policy-lines (code-lines (/ SESSIONHOST-DIR "policy.hy")))
       (for [name ["session-env-admission-error" "overlay-without-turn-auth" "inheritable-spawn-env" "spawn-env-inherited?"]]
         (assert (= (len (lfor line policy-lines :if (.startswith line f"(deff {name} ") line)) 1) name))
       (assert (any (gfor line policy-lines (.startswith line "(setv TURN-AUTH-ENV-KEYS")))
               "手番ごとの資格の env の名は policy の 1 点(R30)")
       (assert (any (gfor line policy-lines (.startswith line "(setv SPAWN-INHERITED-ENV-KEYS")))
               "継ぐ env は許可の名簿(R30)")
       (setv host-lines (code-lines (/ SESSIONHOST-DIR "host.hy")))
       (assert (= (len (lfor line host-lines :if (in "(session-env-admission-error session-env \"session.send\")" line) line)) 1)
               "送りの口の関所は launch と同じ 1 点(R30)")
       ;; 段 10 lane 10o(R31): 同じ腕が郵便の添付も型つきで運ぶ(綴りは器の Dialogue)。
       (assert (any (gfor line host-lines (in "(headless-send-program sid message awaiting session-env attachments)" line)))
               "手番ごとの env は送りの腕へ渡る(R30・添付は R31)")
       (setv launch-lines (code-lines (/ SESSIONHOST-DIR "launch.hy")))
       (assert (= (len (lfor line launch-lines :if (in "(session-env-admission-error session-env \"session.launch\")" line) line)) 1)
               "launch の関所も同じ 1 点(R30)")
       (setv headless-lines (code-lines (/ SESSIONHOST-DIR "headless.hy")))
       (for [lines [launch-lines headless-lines]]
         (assert (= (len (lfor line lines :if (in ":launch-overlay {\"session_env\" (overlay-without-turn-auth session-env)" line) line)) 1)
                 "行には手番ごとの札を残さない(R30)"))
       (assert (any (gfor line headless-lines (in "(| (dict (or (.get overlay \"session_env\") {}))" line)))
               "起こし直しは誕生の env にこの手番の env を重ねる(R30)")
       (setv substrate-lines (code-lines (/ SESSIONHOST-DIR "substrate_headless.hy")))
       (assert (= (len (lfor line substrate-lines :if (in "(inheritable-spawn-env (dict os.environ))" line) line)) 1)
               "機体から継ぐ env は名簿の 1 点を通る(R30)"))
     (deftest test-adr-doe-agents-012-attachment-spelling-lives-in-the-dialogue
      ;; R31 の針 1(構造): agentd は CLI の綴りを 1 語も持たず、綴りの座は Dialogue の 2 腕ちょうど。
      (for [name ["agentd.hy" "judgment.hy"]]
        (setv lines (code-lines (/ ACP-DIR name)))
        (for [line lines]
          (for [word ["\"image\"" "\"media_type\"" "\"source\"" "media_type" "base64," "data:"]]
            (assert (not-in word line)
                    f"{name} は画像の綴りを持たない(R30・法 012 R21 と同じ形): {line}"))))
      (setv protocol-lines (code-lines (/ SESSIONHOST-DIR "headless_protocol.py")))
      (assert (= (len (lfor line protocol-lines :if (.startswith line "def claude_image_block(") line)) 1)
              "claude の image の block の座は 1 点(R31)")
      (assert (= (len (lfor line protocol-lines :if (.startswith line "def codex_input_items(") line)) 1)
              "codex の input の項の座は 1 点(R31)")
      (for [word ["\"media_type\": attachment.mime" "\"type\": \"base64\"" "f\"data:{attachment.mime};base64,{attachment.data}\""]]
        (assert (any (gfor line protocol-lines (in word line)))
                f"便 1 の実測の綴りが Dialogue に在る(R31): {word}"))
      ;; 型つきの値は器に依らない module の 1 点(Dialogue も agentd もここから引く)。
      (setv value-lines (code-lines (/ SESSIONHOST-DIR "attachment.py")))
      (for [name ["class TurnAttachment" "class AttachmentRefused" "class TurnContent"]]
        (assert (= (len (lfor line value-lines :if (.startswith line name) line)) 1) name))
      ;; 型の module は値だけ — wire の綴り(block / 項の鍵)を組む code は持たない。
      (for [line value-lines]
        (for [word ["\"media_type\"" "\"source\"" "\"type\": \"image\"" "base64,"]]
          (assert (not-in word line) f"添付の型の module に綴りは無い(R31): {line}")))
      ;; 器へ渡す口は 2 つ(手番 / 割り込み)で、どちらも型つきの欄を持つ。
      (setv effects-lines (code-lines (/ ACP-DIR "effects.py")))
      (assert (= (len (lfor line effects-lines :if (.startswith line "    attachments: tuple[TurnAttachment, ...] = ()") line)) 2)
              "SessionSend と SessionInterject の 2 つが型つきの添付を運ぶ(R31)")
      (assert (= (len (lfor line effects-lines :if (.startswith line "CONDITION_ATTACHMENT_IGNORED: ConditionType = \"AttachmentIgnored\"") line)) 1))
      (assert (= (len (lfor line effects-lines :if (.startswith line "NODE_CAPABILITY_ATTACHMENTS_KEY: str = \"attachments\"") line)) 1))
      ;; 本文と添付の並びは 1 つの述語(2 つ目の並べ手を置かない)。
      (setv judgment-lines (code-lines (/ ACP-DIR "judgment.hy")))
      (assert (= (len (lfor line judgment-lines :if (.startswith line "(defk message-bodies-of ") line)) 1))
      ;; 実弾 2026-09-15: 見出しは**画像の生の byte**で検める(記録の出来事が名乗る値と比べない)。
      (assert (any (gfor line judgment-lines (in "(setv raw (try (base64.b64decode event.data :validate True)" line)))
              "添付の検めは base64 を解いた生の byte で(R31)")
      (assert (any (gfor line judgment-lines (in "(= (.hexdigest (hashlib.sha256 raw)) digest)" line)))
              "指紋も生の byte で比べる(R31)")
      ;; 実弾 2026-09-15 03:08: charter は RPC へ出る — 項の綴りの 1 点で書く(型つきの値のまま入れない)。
      (assert (any (gfor line judgment-lines (in "(lfor one attachments (attachment-wire one))" line)))
              "起こす charter は項の綴りの 1 点で書く(R31)")
      ;; 実弾 2026-09-15 09:5x: resume は charter の欄を名簿で写す — 添付が名簿から漏れると resume の腕だけ落ちる。
      (assert (any (gfor line judgment-lines (in "             MESSAGE-ATTACHMENTS-KEY]]" line)))
              "resume の params の名簿に添付が在る(R31)")
      (setv launch-lines (code-lines (/ SESSIONHOST-DIR "launch.hy")))
      (assert (any (gfor line launch-lines (in "\"attachments\" (.get params \"attachments\" #())" line)))
              "resume が組む launch-params が添付を運ぶ(R31)")
      (setv host-lines (code-lines (/ SESSIONHOST-DIR "host.hy")))
      (assert (= (len (lfor line host-lines :if (in "(turn-attachments-of " line) line)) 3)
              "添付を解くのは 3 つの口ちょうど(send / launch / resume)(R31)")
      (setv value-lines (code-lines (/ SESSIONHOST-DIR "attachment.py")))
      (for [name ["def attachment_wire(" "def attachment_of_wire("]]
        (assert (= (len (lfor line value-lines :if (.startswith line name) line)) 1) name))
      (setv fake-lines (code-lines (/ ACP-DIR "fake.py")))
      (assert (any (gfor line fake-lines (in "json.dumps(params)" line)))
              "偽の器も起こす params を JSON にして受ける(R31)")
      (assert (not (any (gfor line judgment-lines (.startswith line "(defk message-attachments-by-input-of "))))
              "本文と添付を別々に並べる第 2 の述語を置かない(R31)"))

     (deftest test-adr-doe-agents-012-fake-clis-freeze-the-attachment-spelling
      ;; R31 の針 2(構造): 偽 CLI は実測の綴りだけを読み、違う綴りは落とす(実物は黙って劣化するので替え玉が赤にする)。
      ;; 振る舞いそのものは test_sessionhost_headless.py の 2 本(替え玉を実 process として起こす)が撃つ。
      (setv stubs (/ (. (Path __file__) parent parent parent) "packages" "doeff-agents" "tests" "headless_stubs"))
      (setv claude-lines (code-lines (/ stubs "claude")))
      (assert (= (len (lfor line claude-lines :if (.startswith line "def _user_images(") line)) 1)
              "偽 claude の画像の読みは 1 点(R31)")
      (for [word ["part.get(\"type\") != \"image\""
                  "source.get(\"type\") != \"base64\""
                  "source.get(\"media_type\")"]]
        (assert (any (gfor line claude-lines (in word line)))
                f"偽 claude が実測の綴りを名指す(R31): {word}"))
      (assert (<= 3 (len (lfor line claude-lines :if (in "raise RuntimeError" line) line)))
              "誤った綴りは落とす(R31) — 黙って劣化させない")
      (setv codex-lines (code-lines (/ stubs "codex")))
      (assert (= (len (lfor line codex-lines :if (.startswith line "def _turn_input_text(") line)) 1)
              "偽 codex の入力の読みは 1 点(R31)")
      (for [word ["DATA_URL_PREFIX = \"data:\"" "\";base64,\" in url" "kind != \"image\""]]
        (assert (any (gfor line codex-lines (in word line)))
                f"偽 codex が実測の綴りを名指す(R31): {word}"))
      (assert (<= 3 (len (lfor line codex-lines :if (in "raise RuntimeError" line) line)))
              "誤った綴り(localImage の path・素の URL)は落とす(R31)"))

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
       ;; 段 10 lane 10o(agora-redesign #96): 本文と添付は 1 つの型つきの値(TurnContent)で渡る — 綴りは Dialogue が組む。
       (assert (= (len (lfor line protocol-lines :if (.startswith line "    def inject(self, content: TurnContent, ref: str = \"\") -> Injection:") line)) 2)
               "割り込みの本文の作法は Dialogue.inject の 2 腕(claude / codex)(R21・ref は R28・添付は R22)")
       (assert (any (gfor line protocol-lines (in "one_process_per_turn: bool = False" line))))
       (setv host-lines (code-lines (/ SESSIONHOST-DIR "host.hy")))
       (assert (any (gfor line host-lines (in "(headless-inject-program sid message ref attachments)" line)))
               "host の mode = interrupt は inject の program へ(R21・添付は R22)")
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
       (assert (= world.sessions.interjections [#(sid (mailed "m-i1" "stop")) #(sid (mailed "m-i2" "then continue"))]) "載せた順に器へ(R21)")
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
          "docs/impl-requests/stage10-lane-prompts/lane-10h-agentd-dead-backend-recovery.md(agora-redesign #84・追補 R25 / R26: backend の生死は観測で・復帰・session-lost・resume の KeyError・停止で子を黙って道連れにしない・ACP の宛先は宣言ちょうど)"
          "docs/impl-requests/stage11-lane-prompts/lane-11v-rehydrate-compaction.md(agora-redesign #55 便 1・追補 R34: 上限で落とした古い手番は区間の見出し 1 行に畳む・model の要約は便 2 の設計だけ / 便 3 = #225・追補 R35: 落とす前に古い手番から道具の項を薄くする)"
          "agora-redesign docs/impl-requests/stage11-lane-prompts/lane-12a-company-repo-verify.md(agora-redesign #230・追補 R36: charter.kind = verify の job は機体の script を 1 つ走らせる命令 — claude / codex を起こさず札も借りない・結末は Ended の result)"])
