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
;;; 置き場 = packages/doeff-agents/src/doeff_agents/sessionhost/acp/(effects.py = 要求と値の
;;; 型・judgment.hy = 純粋な判断・agentd.hy = program・handlers.py = 実 I/O・fake.py = test の
;;; handler・valve.py = 弁・runtime.py = composition root・entry.py = console script の入口)。
;;; agentd は host の socket の client。段 2 の初版では host.hy / hostmain.py / impls / policy を
;;; 1 行も変えなかったが、R10(温かい session)は host の公開 RPC の最小の追補を要した —
;;; lifecycle の閉語彙に multi_turn(launch.hy)・turn-end の連言の結果を行に刻む turn_ended_at
;;; (policy.hy の monitor 1 点・store の列・wire)・session.send の awaiting(host.hy)。弁と
;;; agentd の腕は引き続き host の内側に無い(針 test-adr-doe-agents-012-valve-defaults-off)。

(require doeff-adr.macros [defadr rule law])
(require doeff-hy.macros [deftest])
(import doeff-adr.macros [fact interpretation counterexample])
(import re)
(import pathlib [Path])
(import doeff [run])
(import doeff_agents.sessionhost.acp.effects
        [AGENT-JOB-KIND AGENT-JOB-NAMESPACE AGORA-KINDS-NAMESPACE AcpRow AgentdSettings
         AgentdState CaptureGone JSONObject MESSAGE-KIND NODE-KIND PHASE-BOUND PHASE-ENDED
         PHASE-RUNNING TURN-RECORD-KIND])
(import doeff_agents.sessionhost.acp.fake [Birth FakeAcp FakeCustody FakeLocal FakeSessions])
(import doeff_agents.sessionhost.acp.judgment [capture-verdict job-step-of stream-capability-of-backend])
(import doeff_agents.sessionhost.acp.runtime [initial-state run-tick])
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
  :title "sessionhost の agentd の腕: 出口は ACP と custody だけ・判断は『自分に結ばれた job か』の純関数 1 点だけ(binding は書かない)・弁の既定は off・購読 0 で capture が止まる・借りた札は家の中の auth file 以外の平文で disk に残さない・job の進みは行から導く(自分の Running は再起動後も拾い、次の 1 手は job-step-of の 1 点)・capture の gone は終端の合図で例外ではない・tick の縁は互いの失敗で止まらない・session は会話の資源で job は手番(同じ会話の次の手番は launch せず send・判断は next-arm-for-job の 1 点・idle の寿命は値の宣言 1 点)・headless backend(print mode の家は 1 つ・events の写像は純関数・withdraw は中断の合図・watch の拍は差分の読み)"
  :status "accepted"
  :scope ["packages/doeff-agents/src/doeff_agents/sessionhost/acp/effects.py"
          "packages/doeff-agents/src/doeff_agents/sessionhost/acp/judgment.hy"
          "packages/doeff-agents/src/doeff_agents/sessionhost/acp/agentd.hy"
          "packages/doeff-agents/src/doeff_agents/sessionhost/acp/handlers.py"
          "packages/doeff-agents/src/doeff_agents/sessionhost/acp/fake.py"
          "packages/doeff-agents/src/doeff_agents/sessionhost/acp/valve.py"
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
       :evidence "計画の会話の追補 2026-09-12・ACP `GET /api/event-window?after=5215004&limit=1` の実測・src/Acp/App/Server.hs の event-window(cursor-only・postDeltas の post-image)")]
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
       "差分の読みと計器の始点(R14): watch で起きた拍は agent-job の全量 list ではなく ACP の event-window(cursor-only・(after, through] の post-image)で変わった行だけを読み、知っている行の cache(AgentdState.rows)に差し替える。全量 list は最初の拍・周期の保険(watch_resync_seconds)・gap・接続の張り直し・窓が retention の床の下(409)の時だけ。郵便の本文は鍵で 1 行ずつ読む。計器 agent-job-to-send の始点は行の生まれの着地(generation 1 の image の resourceLandedAt・ns 精度)で、欄が無ければ今日の値(秒の粒度の createdAt)。")]
  :decision
    [(rule R1 "agentd の出口は ACP(GET /api/resources・POST /api/events・GET /api/watch/stream・POST /api/streams)と custody(POST /lease/*)だけ。agora の台帳 API(/api/state・turn-jobs・seat-*・headless・agmsg)の語を sessionhost の source に置かない。")
     (rule R2 "job を選ぶ判定は judgment.hy の bound-to-me(phase == Bound ∧ binding.node == 自分 — 受け)と running-on-me(phase == Running ∧ binding.node == 自分 ∧ sessionHandle.stream.owner == 自分 — 再起動後の拾い直し)の 2 つの述語だけで、どちらも binding.node == 自分の行に閉じる。それ以外に job を選ぶ・優先する code を置かない。agent-job の status.binding を agentd は書かない(写して返すだけ)。")
     (rule R3 "弁の既定は off(valve.py の ACP_VALVE_DEFAULT = False)。on は flag --acp か env DOEFF_AGENTD_ACP=on だけで、語彙の外の値は黙って off に倒さず断る。")
     (rule R4 "frame の capture は購読者が居る時だけ: push の応答の subscribers が 0(か不明)なら capture を止め、周期の status frame で読み直して再開する。判定は judgment.hy の capture-verdict の 1 点。")
     (rule R5 "借りた札は disk の平文に残さない — 例外は家の中の auth file(codex の <homes>/codex/<account>/auth.json・0600)だけ。claude の札は env CLAUDE_CODE_OAUTH_TOKEN で渡し、log と計器には載せない。")
     (rule R6 "値の宣言は 1 点: lease の TTL と周期・watch の resync・frame の rate・購読の読み直しの周期は effects.AgentdSettings の既定値、URL と札の env の綴りは handlers.py / valve.py。")
     (rule R7 "job の進みは行から導く: 自分の Running(running-on-me)は memory に無くても resync の拍に拾い、次の 1 手は judgment.hy の job-step-of(器の現況 → observe | record-end | fail-missing・閉語彙 effects.JobStep)の 1 点で決める — memory に在る job の拍も同じ 1 点を通る。record-end は記録の腕(turn-record ended・result・phase Ended)だけを撃ち launch も send もし直さない。fail-missing は記録が在れば ended にし condition SessionFailed で Ended。終端の語彙(SESSION_TERMINAL_STATUSES)を読むのは judgment.hy だけ。")
     (rule R8 "capture の gone は終端の合図で例外ではない: SessionCapture の答えは閉語彙 CaptureFrame | CaptureGone、実 handler は host の断り(AgentdClientError)を CaptureGone に写す(host.hy / substrate は触らない)。gone の job は capturing = False・stream_gone = True で、以後 capture も購読の読み直しもせず、器の終端(同じ拍に読み直す)で記録の腕へ。器が終端の拍は capture を撃たない(job-step-of を実況より先に読む)。")
     (rule R9 "tick の縁: heartbeat・受け・job ごとの観測は互いの I/O の失敗(effects.IO_FAILURES = RuntimeError | OSError)で止まらない — program の agentd-tick が 3 つの腕をそれぞれ捕まえ、log して次の周期 / 次の拍へ持ち越す(condition には写さない — 一時の失敗を job の結末にしない)。I/O より広い例外は捕まえない(runtime.run_loop の縁)。")
     (rule R11 "headless backend: host の backend の閉語彙は tmux | herdr | headless。headless の器は専用の program(sessionhost/headless.hy)と substrate(effects.hy の Headless* → substrate_headless.hy → headless_process.py)で、host.hy は backend の分岐だけ(RPC の語彙は同じ意味・session.interrupt を足す)。stdin / stdout の作法と手番の判断は headless_protocol.py の Dialogue / turn_verdict の純関数 1 点。claude の print mode の argv の家は impls/headless_argv.hy ちょうどで、semgrep doeff-agents-no-claude-print-mode の除外もその家と headless の substrate / program / 検だけ。admission と identity の準備は tui の launch と共有する(launch.hy admit-launch / prepare-launch-workspace)。")
     (rule R12 "events の実況: agentd は headless の器の実況を events file(wire の backend_ref.events_path)から offset で読み(SessionEvents)、純関数 judgment.events-to-deltas(claude = stream-json・codex = app-server の通知)で契約の種類の閉語彙(text / tool_use / tool_result / usage)の TurnDelta に写す。text の delta は 1 行ずつ frame、完成した本文は entries だけ。headless の器に pane の capture は撃たない。node の observations.streamCapability は host の backend から導く(judgment.stream-capability-of-backend の 1 点: headless = events・それ以外 = frames)。")
     (rule R13 "withdraw は中断の合図: 自分の走っている job の行が Withdrawn(書き手 = 作った側)になったら、judgment.interrupt-arm-for の 1 点で手番の途中なら session.interrupt(headless = SIGINT / turn/interrupt・tmux = Escape・session は残す)を撃ち、turn-record を ended(ここまでの entries と usage)、agent-job の conditions に Interrupted(phase は書かない)。session.cleanup は撃たない(温かい session は残す — 寿命は sessions-to-retire)。")
     (rule R14 "watch の拍は差分の読み・計器の始点は生まれの着地: 行の読み直しの様式は judgment.list-mode-for の 1 点(full = 最初の拍・周期の保険・gap・接続の張り直し / window = watch で起きた拍 = GET /api/event-window の post-image で AgentdState.rows を差し替え・窓が読めなければ full に落ちる / none = idle)。郵便の本文は鍵で 1 行ずつ読む(全量 list しない)。計器 agent-job-to-send の createdAtMs は judgment.birth-ms-of の 1 点(生まれの表 → generation 1 の image の landed_at_ms → 今日の値 created_at_ms)。")
     (rule R15 "session の id は agentd が鋳造する: 起こす session の id(session_id と session_name・sessionHandle.sessionId・stream の name)は effect MintId(ULID・時刻と乱数は handler)の答えで、charter(Messaging が組む launch の params)の session_id / session_name は読まない(judgment.launch-plan-of が落とす・据えるのは charter-with-session-id の 1 点)。実弾 2026-09-12: 温かい session が idle TTL で片付いた後、charter の固定の id の launch が `session is already registered`(host は片付いた行を登記のまま残す)に落ちて LaunchFailed で Ended した。")
     (rule R10 "session は会話の資源・job は手番(温かい session・設計 17.4): 会話 → 生きている session の対応は行(自分が claim した同じ subject の agent-job の sessionHandle)と器の現況から導き、Bound の job の起こし方は judgment.hy の next-arm-for-job(閉語彙 effects.NextArm = launch | send | resume | defer)の 1 点で決める — 同じ会話の生きて idle な session が在れば launch せず session.send(awaiting)だけ、sessionHandle はその session を指し、turn-record は手番ごと。手番の終わりは器の lifecycle multi_turn(launch.hy の閉語彙に足した語)で policy.hy の monitor が既存の turn-end の連言から行の turn_ended_at に刻み、agentd は job-step-of の turn-end(turn_ended_at > 手番の始まりの下限 ∧ 記録の進み)で読む — status は倒さず session は生かす。idle の寿命は AgentdSettings.session_idle_ttl_seconds の 1 点で、超過・Withdrawn・node の退役で session.cleanup。計器 agent-job-to-send は create → send のまま(温かい path で p99 < 2 秒)。")]
  :laws
    [(law agentd-exits-only-to-acp-and-custody
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
       :statement "for_all Bound job j of conversation c on node n: exists session s of c alive ∧ idle (lifecycle = multi_turn ∧ turn_ended_at ≠ None) ⇒ claim(j) issues no session.launch / session.resume and exactly session.send(inputs(j)) to s ∧ sessionHandle(j) = s ∧ turn-record(j) is its own row; no such s ⇒ launch (or resume when affinity.predecessor); s mid-turn ⇒ j stays Bound (defer)"
       :counterexamples
         [(counterexample "同じ会話の次の手番を毎回 cold に launch する — tmux で claude の tui を起こす約 10 秒が create → send に毎手番乗り、段 2 の計器 p99 < 2 秒を構造的に満たせない(実測 p50 11.0 秒 / p99 16.9 秒)")
          (counterexample "会話 → session の対応を process の memory にだけ持つ — 再起動で温かい session を見失い、生きている session を残したまま同じ会話をもう 1 つ起こす")
          (counterexample "手番の途中の session に次の手番の本文を send で積む — 前の手番の終わりの turn_ended_at を次の手番の終わりと読み違え、turn-record の境界が壊れる")])
     (law warm-send-is-decided-at-one-point
       :statement "the only decision launch | send | resume | defer for a Bound job is judgment.next-arm-for-job; the only reading of a warm turn's end is judgment.job-step-of (turn-end ⇔ lifecycle = multi_turn ∧ turn_ended_at > floor ∧ progressed); agentd.hy neither compares lifecycle words nor reads turn_ended_at"
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
     (law withdraw-is-an-interrupt-signal-not-a-cleanup
       :statement "for_all withdrawn agent-job row r I am observing: interrupt-arm-for(job, session.get) = interrupt ⇒ exactly one session.interrupt(r.sessionHandle) and no session.cleanup; turn-record(r) = ended; conditions(r) ∋ Interrupted; phase(r) stays Withdrawn; the session stays alive for the next send"
       :counterexamples
         [(counterexample "取り下げで session.cleanup を撃つ — 温かい session が死に、次の手番が cold の launch(tmux の tui で約 10 秒)に戻る")
          (counterexample "agentd が Withdrawn の行の phase を書く — 書き手は作った側(withdraw の権限)で、agentd の書きは断られるか二重の終端になる")
          (counterexample "手番が既に終わっている job に割り込む — 次の手番(別の job)の途中の session に Escape / SIGINT が飛ぶ")])
     (law watch-wake-reads-changed-rows-and-latency-starts-at-birth
       :statement "for_all watch wake with kind = changed: agentd issues AcpEventWindow(after = last_window_seq) and no AcpGet(agent-job) unless the window is incomplete; message bodies are read by key (AcpGetRow) and never by AcpGet(message); the metric agent-job-to-send.createdAtMs = birth-ms-of(row, births) = the generation-1 landed_at_ms when known, else the row's created_at_ms"
       :counterexamples
         [(counterexample "watch で起きるたびに agent-job と message を全量 list する — loadCurrentState の全 state を 2 度読み、温かい path の p99 が 2 秒を超える(実測 p50 4.7 s)")
          (counterexample "createdAtMs を秒の粒度の resourceCreatedAt から取る — 計器が最大 1 秒ずれ、2 秒の受入を測れない")])
     (law session-id-is-minted-by-agentd
       :statement "for_all claim that launches or resumes: session_id(launch params) = MintId() ∧ session_id ∉ {charter.session_id, charter.session_name, agent-job id}; sessionHandle.sessionId = stream.name = that id; after a session was cleaned up (its row stays registered in the host) the next job of the conversation launches with a fresh id and is not refused"
       :counterexamples
         [(counterexample "charter の固定の session_id で launch する — idle TTL で片付いた行が host に登記のまま残り、次の launch が `session is already registered` で LaunchFailed(実弾 2026-09-12 aj 031〜033)")
          (counterexample "agentd.hy が id を自分で組む(時刻や job の id から)— 純関数の外で id が生まれ、fake で反例を撃てない")])
     (law idle-session-ttl-is-declared-once
       :statement "the idle lifetime of a warm session is AgentdSettings.session_idle_ttl_seconds and nothing else; idle(s) ∧ now ≥ turn_ended_at(s) + ttl ⇒ session.cleanup(s) at the next heartbeat; the choice is judgment.sessions-to-retire (pure) and the clock is an effect"
       :counterexamples
         [(counterexample "TTL を agentd.hy や handlers.py の literal に散らす — 値を変えた時に片方だけ残り、片付けの拍と観測の拍で寿命が食い違う")
          (counterexample "idle の session を永遠に生かす — 会話ごとの tmux の pane が増え続け、node の容量(capacity)が温かい session で埋まる")])]
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
       ;; 断り(AgentdClientError)を CaptureGone に写す・R9 の縁は agentd-tick に 3 つ。
       (setv agentd-lines (code-lines (/ ACP-DIR "agentd.hy")))
       (setv handler-lines (code-lines (/ ACP-DIR "handlers.py")))
       (assert (any (gfor line agentd-lines (in "(<- outcome (| CaptureFrame CaptureGone)" line))))
       (assert (any (gfor line handler-lines (in "except AgentdClientError" line))))
       (assert (any (gfor line handler-lines (in "return CaptureGone(" line))))
       (assert (= (len (lfor line agentd-lines :if (in "(except [e IO-FAILURES]" line) line)) 3)
               "tick の縁は heartbeat・受け・job ごとの 3 つ(ADR-DOE-AGENTS-012 R9)")
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
       ;; 追補 2 の反例: 片付いた後の次の job は新しい id で launch に成功する(charter の固定の
       ;; id で `session is already registered` に落ちない)。
       (run-warm-turn world "t-4" "conv-a" "again")
       (assert (= (len world.sessions.launches) 3))
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
       ;; R11 の針: print mode の argv の綴り(`"-p" "--output-format"` / `"--print"`)は
       ;; impls/headless_argv.hy だけ(tmux の -p は capture-pane / paste-buffer の旗で別物)。host.hy の backend の
       ;; 閉語彙と分岐の述語は 1 点。semgrep の除外は headless の家だけ。
       (setv hits [])
       (for [path (source-files)]
         (when (= path.suffix ".hy")
           (for [line (code-lines path)]
             (when (re.search r"\"-p\"\s+\"--output-format\"|\"--print\"" line)
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
       (setv world.settings (AgentdSettings :node-name "mac-1" :homes-root "/homes" :stream-capability "events"))
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
       (assert (= (get first-entry "text") "abcd")))
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
       (assert (= (get metric "ms") (- 2500 1437))))]
  :plans ["docs/impl-requests/stage2-lane-prompts/lane-2b-agentd.md(agora-redesign)"
          "docs/impl-requests/stage2-lane-prompts/lane-2b2-agentd-fix.md(agora-redesign・改訂 R7〜R9)"
          "docs/impl-requests/stage2-lane-prompts/lane-2b3-warm-session.md(agora-redesign・改訂 R10)"
          "docs/impl-requests/stage2-lane-prompts/lane-2d-headless-backend.md(agora-redesign・改訂 R11〜R14)"])
