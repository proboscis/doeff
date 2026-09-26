;;; session host の headless の backend の effect 語彙(agora-redesign #37・段 2 lane 2d)— sessionhost/effects.hy から
;;; agora-redesign #624(E8)で移した。中身は 1 語も変えていない。
;;;
;;; 移した理由: この語彙は旧い headless の器(headless_protocol.py の Dialogue・headless_process.py の器・substrate_headless.hy)の
;;; 持ち物で、共有の effect 語彙(effects.hy)に置くと、effects.hy を読むだけの module(policy.hy → shell.py → doeff-agents の
;;; headless の adapter)まで旧い器の状態機械を import する。agent の手番を層 2(doeff-claude-code)で回す経路が、退役する
;;; この backend に依存しないように、語彙を backend の側へ寄せる。backend を退役させる時(削除計画 (b) の手順 5-1・#668)は、
;;; この file を backend の file と一緒に消せばよく、effects.hy は触らない。
;;; 検 = agora-controllers scripts/check_headless_route_imports.hy(経路の import の閉包にこの file と headless_protocol が無い)。

(require doeff-hy.macros [defk <-])

(import dataclasses [dataclass])
(import typing [Any])
(import doeff [EffectBase])
(import doeff_agents.sessionhost.effects [ProcResult])
(import doeff_agents.sessionhost.headless_protocol [
  BackendLiveness
  ClaudeDialogue
  CodexDialogue
  HeadlessObservation])


;; --- headless backend(agora-redesign #37・段 2 lane 2d): tui の pane を持たない
;; 子 process(claude の print mode(-p)の stream-json / codex app-server)の substrate。判断(stdin の
;; 綴り・手番の終わり・割り込みの伝え方)は headless_protocol.py の Dialogue(純粋)、
;; 器は headless_process.py、handler は substrate_headless.hy。argv は per-kind impl
;; (interface effect BuildHeadlessLaunch → impls/headless_argv.hy)。

(defclass [(dataclass :frozen True :kw-only True)] BuildHeadlessLaunch [EffectBase]
  "kind の headless の起動(argv と stdin / stdout の作法)を組み立てる。戻り値:
   {\"argv\": list[str], \"dialogue\": Dialogue}。params は launch params 相当
   (model / effort / mcp_servers / result_channel / session_hooks / work_dir)+
   \"conversation\"(kind 判別 union)+ \"resume_mode\"(\"resume\" = 続きの
   手番・None = 会話の最初の手番)。prompt は argv に載せない(stdin の作法 =
   Dialogue.turn)。物理: claude = print mode(-p)の `--output-format stream-json
   --include-partial-messages --verbose`(--session-id / --resume)、codex =
   `codex app-server --listen stdio://`(thread/start / thread/resume は Dialogue)。"
  #^ str agent-type
  #^ dict params)

(defclass [(dataclass :frozen True :kw-only True)] HeadlessSpawn [EffectBase]
  "headless の子 process を起こす(stdin / stdout は pipe・stdout の行は events-path へ
   1 行 1 event で追記)。禁止 env(ANTHROPIC_API_KEY*)の hard reject は substrate 所有
   (TmuxNewSession と同じ)。同じ名の生きた process が在れば raise(duplicate parity)。
   戻り値: pid(int)。"
  #^ str session-name
  #^ str work-dir
  #^ dict env
  #^ list argv
  #^ str events-path
  #^ Any dialogue)

(defclass [(dataclass :frozen True :kw-only True)] HeadlessRunOnce [EffectBase]
  "headless の 1 回きりの子 process を、HeadlessSpawn と同じ実効 env(禁止 env の hard reject も
   同じ 1 点)で完了まで走らせる。用途 = 冷えた再開の前の圧縮(print mode の prompt `/compact
   fast-jev-if-cold` を同じ会話の続きとして 1 回 — 圧縮 plugin が自分の状態で温冷を決め、温ければ
   何もしない)。stdout / stderr は捕り、wall-clock の上限は substrate 所有。
   戻り値: ProcResult(exit-code != 0 も値 — 圧縮は最適化で、手番を止める理由にならない)。"
  #^ str session-name
  #^ str work-dir
  #^ dict env
  #^ list argv)

(defclass [(dataclass :frozen True :kw-only True)] HeadlessDeliver [EffectBase]
  "次の手番の本文を process の stdin へ(綴りは Dialogue.turn — claude は stream-json の
   user の行・codex は turn/start)。戻り値: bool(process が生きていて書けたか)。"
  #^ str session-name
  #^ str text
  ;; 段 10 lane 10o(agora-redesign #96): 手番の添付(型つき — 綴りは Dialogue が組む)。
  #^ tuple attachments)

(defclass [(dataclass :frozen True :kw-only True)] HeadlessInject [EffectBase]
  "割り込みの本文を走っている手番へ(段 8 lane 4x・綴りは Dialogue.inject — claude は user の
   行を CLI が次の tool の境界で注入・codex は turn/interrupt → 同じ thread へ turn/start)。
   ref = 注入の行の名(段 10 lane 10n — claude は user の行の uuid・CLI の command_lifecycle がこの
   綴りで運命を名乗る。空 = Dialogue が鋳造)。
   戻り値: bool(走っている手番が在って器が引き受けたか — 偽なら呼び手が queued へ倒す)。"
  #^ str session-name
  #^ str text
  #^ str ref
  ;; 段 10 lane 10o: 割り込みの添付(型つき — 綴りは Dialogue が組む)。
  #^ tuple attachments)

(defclass [(dataclass :frozen True :kw-only True)] HeadlessEscalate [EffectBase]
  "停止の合図(段 10 lane 10n・判断は Dialogue.escalate): 走っている手番に model がまだ読んでいない
   注入(queued)が在れば claude の control_request interrupt を stdin へ(codex は注入の段が無いので
   出す物が無い)。戻り値: bool(合図を出したか)。"
  #^ str session-name)

(defclass [(dataclass :frozen True :kw-only True)] HeadlessPoll [EffectBase]
  "monitor の拍: 前の拍から読んだ事実(行・手番の終わり・会話の id・型付きの失敗)と
   process の生死。戻り値: HeadlessObservation | None(名の登記が無い)。"
  #^ str session-name)

(defclass [(dataclass :frozen True :kw-only True)] HeadlessInterrupt [EffectBase]
  "走っている手番を止める合図(claude = SIGINT・codex = turn/interrupt)。session は
   残す。戻り値: bool(合図を出せたか)。"
  #^ str session-name)

(defclass [(dataclass :frozen True :kw-only True)] HeadlessKill [EffectBase]
  "process を降ろして登記を消す(stdin の EOF → SIGTERM → SIGKILL の順・猶予つき)。
   戻り値: bool(登記が在ったか)。"
  #^ str session-name)

(defclass [(dataclass :frozen True :kw-only True)] HeadlessHasSession [EffectBase]
  "名の process が登記されて生きているか。戻り値: bool。"
  #^ str session-name)

(defclass [(dataclass :frozen True :kw-only True)] HeadlessKillAll [EffectBase]
  "登記の全 process を段ごとに並列で降ろして忘れる(host の停止 — 段 10 lane 10h 便 2: stdin の EOF → SIGTERM →
   SIGKILL の猶予を process の数だけ直列に積まない)。戻り値: int(降ろした登記の数)。")

(defclass [(dataclass :frozen True :kw-only True)] HeadlessLiveness [EffectBase]
  "行の backend(子 process)の生死の観測(段 10 lane 10h・agora-redesign #84): pid の存在(kill 0)と
   所有(この host の registry が同じ pid の生きた process を持つ)。判断は持たない — 戻り値:
   headless_protocol.BackendLiveness。pid が None の行(backend_ref に pid が無い)は存在も所有も偽。"
  #^ str session-name
  #^ (| int None) pid)


;; --- 構築子(署名 = 契約面)

(defk build-headless-launch [agent-type params]
  {:pre [(: agent-type str) (: params dict)]
   :post [(: % dict)]}
  "BuildHeadlessLaunch を実行する(headless の argv と作法は per-kind impl 所有)。
   戻り = impl が組んだ {argv, dialogue}。"
  (<- built (BuildHeadlessLaunch :agent-type agent-type :params params))
  built)

(defk headless-spawn [session-name work-dir env argv events-path dialogue]
  {:pre [(: session-name str) (: work-dir str) (: env dict) (: argv list)
         (: events-path str) (: dialogue (| ClaudeDialogue CodexDialogue))]
   :post [(: % int)]}
  "HeadlessSpawn を実行する。戻り = 起こした子 process の pid。"
  (<- pid (HeadlessSpawn :session-name session-name :work-dir work-dir :env env :argv argv
                         :events-path events-path :dialogue dialogue))
  pid)

(defk headless-run-once [session-name work-dir env argv]
  {:pre [(: session-name str) (: work-dir str) (: env dict) (: argv list) (> (len argv) 0)]
   :post [(: % ProcResult)]}
  "HeadlessRunOnce を実行する。戻り = 子 process の完了結果(ProcResult)。"
  (<- res (HeadlessRunOnce :session-name session-name :work-dir work-dir :env env :argv argv))
  res)

(defk headless-deliver [session-name text [attachments #()]]
  {:pre [(: session-name str) (: text str) (: attachments tuple)]
   :post [(: % bool)]}
  "HeadlessDeliver を実行する(添付は段 10 lane 10o — 型つきのまま器へ)。
   戻り = 器が本文を受け取ったか。"
  (<- delivered (HeadlessDeliver :session-name session-name :text text :attachments attachments))
  delivered)

(defk headless-poll [session-name]
  {:pre [(: session-name str)]
   :post [(: % (| HeadlessObservation None))]}
  "HeadlessPoll を実行する。戻り = 器の観測(登記に process が無ければ None)。"
  (<- observed (HeadlessPoll :session-name session-name))
  observed)

(defk headless-interrupt [session-name]
  {:pre [(: session-name str)]
   :post [(: % bool)]}
  "HeadlessInterrupt を実行する。戻り = 合図を出せたか。"
  (<- signalled (HeadlessInterrupt :session-name session-name))
  signalled)

(defk headless-inject [session-name text ref [attachments #()]]
  {:pre [(: session-name str) (: text str) (: ref str) (: attachments tuple)]
   :post [(: % bool)]}
  "HeadlessInject を実行する(段 8 lane 4x・ref は段 10 lane 10n・添付は段 10 lane 10o)。
   戻り = 走っている手番へ注入できたか。"
  (<- accepted (HeadlessInject :session-name session-name :text text :ref ref :attachments attachments))
  accepted)

(defk headless-escalate [session-name]
  {:pre [(: session-name str)]
   :post [(: % bool)]}
  "HeadlessEscalate を実行する(段 10 lane 10n)。戻り = 停止の合図を出したか。"
  (<- signalled (HeadlessEscalate :session-name session-name))
  signalled)

(defk headless-kill [session-name]
  {:pre [(: session-name str)]
   :post [(: % bool)]}
  "HeadlessKill を実行する。戻り = 登記が在ったか(降ろした)。"
  (<- killed (HeadlessKill :session-name session-name))
  killed)

(defk headless-has-session [session-name]
  {:pre [(: session-name str)]
   :post [(: % bool)]}
  "HeadlessHasSession を実行する。戻り = 同じ名の生きた process が在るか。"
  (<- exists (HeadlessHasSession :session-name session-name))
  exists)

(defk headless-kill-all []
  {:pre []
   :post [(: % int)]}
  "HeadlessKillAll を実行する(段 10 lane 10h 便 2)。戻り = 降ろした数。"
  (<- killed (HeadlessKillAll))
  killed)

(defk headless-liveness [session-name pid]
  {:pre [(: session-name str) (: pid (| int None))]
   :post [(: % BackendLiveness)]}
  "HeadlessLiveness を実行する(段 10 lane 10h)。戻り = backend の生死の観測。"
  (<- liveness (HeadlessLiveness :session-name session-name :pid pid))
  liveness)
