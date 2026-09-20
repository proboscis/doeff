;;; claude-code per-kind defhandler(ADR-DOE-AGENTS-004 R2、C2)。
;;;
;;; protocol 物理の単一の家(protocol-physics-has-one-home): oracle =
;;; agentd-rust-final:src/main.rs build_claude_argv / trust_claude_workspace。
;;; conformance 凍結: S12(trust pre-seed: canonicalized work_dir key・
;;; temp+rename)/ S13(--settings disableAllHooks は既定のみ —
;;; DOEFF_AGENTD_SESSION_HOOKS=inherit で外れる。+ --mcp-config stdio +
;;; --strict-mcp-config・prompt は argv に載らない)/ DOE-003 R3
;;; (CLAUDE_CONFIG_DIR 無しは warning のみの staged enforcement)。
;;;
;;; substrate-clean 領域: 生 IO 禁止(defsemgrep 執行)。FS / env は
;;; Fs* / EnvGet substrate effect の yield のみ。

(require doeff-hy.macros [defk deff <- defhandler])

(import doeff [run])

(import json)
(import re)
(import uuid)

(import doeff_agents.sessionhost.effects [
  BuildLaunch
  BuildResume
  DiscoverConversation
  PreLaunchSetup
  ClassifyPane
  DeliverMessage
  ProbeConversationActivity
  RESUME-ERR-TRANSCRIPT-NOT-DISCOVERABLE
  TransplantConversation
  WireResultChannel
  fs-canonical-path
  fs-file-exists
  fs-file-mtime
  fs-link-artifact
  fs-list-dir
  fs-read-text
  fs-write-text-atomic
  fs-make-dirs
  env-get
  tmux-send-keys])
(import doeff_agents.sessionhost.policy [
  AUTOCOMPACT-PARAM-KEY
  BILLING-METERED
  BILLING-SUBSCRIPTION
  CLAUDE-SETTINGS-API-KEY-HELPER
  CLAUDE-SETTINGS-VERTEX-ENV
  CLAUDE-SETTINGS-VERTEX-PROJECT-ENV
  HOME-READING-ABSENT
  HOME-READING-MALFORMED
  binding-billing-class
  claude-home-metered-reading])
(import doeff_agents.sessionhost.impls.channel [
  REPORT-RESULT-MCP-SERVER
  result-channel-spec])
(import doeff_agents.sessionhost.impls.markers [classify-output])


;; ---------------------------------------------------------------------------
;; argv 物理(oracle build_claude_argv — S13 で oracle green 済みの凍結配線)
;; ---------------------------------------------------------------------------

;; ---------------------------------------------------------------------------
;; 会話の圧縮の閾値(設計記録 docs/design/auto-compact-window — operator 指示 2026-09-18)
;; ---------------------------------------------------------------------------
;;
;; 根: 起こす argv が閾値を**何も名乗っていなかった**ので、claude は自分の窓
;; (Fable / Opus は 1M)いっぱいまで畳まずに伸びる。実測 2026-09-18(会社 Mac・
;; 直近 24 時間の全 profile の会話記録 15,783 手番): 1 手番の平均の文脈が 550k・
;; 最大 967k・600k を超える手番が費用の 59% を占めた。畳まないまま伸びる会話は
;; 1 手番の値段が普通(100〜150k)の 4〜5 倍になり、20 席が 24 時間刻むと週の枠を
;; 2 日で焼く(09-15 の 4,697u → 09-17 の 16,976u)。
;;
;; 方針は dotfiles agentcli(headless.py autocompact_value・ADR-DOTFILES-012
;; R-4484fd43 law headless-compaction-threshold-declared-by-the-runner)と同じ側へ
;; 倒す ── **起こす側が必ず名乗る**。「誰も選ばない」は effort と違って「系が
;; 選ばない」ではなく「窓の上限任せ」に落ちるからで、上限任せが直そうとしている
;; 欠陥そのもの。呼び手が params で名乗った値が第一で、名乗らない拍だけこの定数。
;;
;; ⚠ 縮退の向きは常に `auto`(= CLI 自身の窓に合わせた調整)。読めない値・幅の外の
;;   値を argv に載せることは**手番を殺す**(claude 2.1.274 実測: argv 解釈の段で
;;   死に、stream-json の行を 1 つも吐かない ── 逐語 "It must be 'auto', or between
;;   100k and 1M")。縮退したことは argv 自身が名乗る(`ps` に `--autocompact auto`)。

;: 綴り(claude 2.1.274 実測 — `--autocompact <auto|tokens>`)。
(setv AUTOCOMPACT-ARG "--autocompact")
;: CLI 自身の窓に合わせた調整を頼む値。閾値を名乗れない拍の縮退先でもある。
(setv AUTOCOMPACT-AUTO "auto")
;: CLI が受理する幅(実測 2.1.274)。外れる値は載せない。
(setv AUTOCOMPACT-MIN-TOKENS 100000)
(setv AUTOCOMPACT-MAX-TOKENS 1000000)
;: 会話が名乗らない拍の閾値(token)。1M の窓に対して 40% ≒ 実装の途中で畳んで、
;: 最終盤(最も文脈が要る時)に畳まれない位置。役ごとの値は会話の宣言が運ぶ
;: (呼び手が params の欄で運ぶ)ので、ここは床ちょうど。
(setv AUTOCOMPACT-DEFAULT-TOKENS 400000)
;: ⚠ 欄の綴り AUTOCOMPACT-PARAM-KEY は policy が正本(起こす腕の名簿が同じ語を写す
;: ── policy.LAUNCH-FLAG-KEYS)。ここでは import するだけで、第 2 の綴りを置かない。


(defk claude-autocompact-value [params]
  {:pre [(: params dict)]
   :post [(: % str)]}
  "argv に載せる圧縮の閾値の**唯一の導出点**(純粋 — params を読むだけ)。

   既定も会話の宣言も**同じ関門**を通す(既定を素通しさせない)— 定数を誰かが幅の
   外へ動かした日に、その値がそのまま argv へ乗って手番が死ぬ形を作らないため。
   JSON の数は int でも float でも来る(Haskell 側 declareNumber)ので、整数に
   なる float は受ける。bool は数として読まない(True は 1 ではない)。"
  (setv raw (.get params AUTOCOMPACT-PARAM-KEY))
  (when (or (is raw None) (and (isinstance raw str) (not (.strip raw))))
    (setv raw AUTOCOMPACT-DEFAULT-TOKENS))
  (setv tokens
        (cond
          (isinstance raw bool) None
          (isinstance raw int) raw
          (and (isinstance raw float) (.is-integer raw)) (int raw)
          (isinstance raw str) (try (int (.strip raw) 10) (except [ValueError] None))
          True None))
  (cond
    (and (isinstance raw str) (= (.lower (.strip raw)) AUTOCOMPACT-AUTO)) AUTOCOMPACT-AUTO
    (and (is-not tokens None)
         (<= AUTOCOMPACT-MIN-TOKENS tokens AUTOCOMPACT-MAX-TOKENS)) (str tokens)
    True AUTOCOMPACT-AUTO))


(defn autocompact-arg-pair-ok [pair]
  "argv に載せてよい並びか(幅の関門を**出口で**もう一度見る検査)。

   ⚠ これは重複ではない。盲検の反例 B(2026-09-18)が示したのは、関門を通した**後**に
   値を加工する変更(「モデルの窓の 40% で頭打ちにする」)が、関門の関数に 1 文字も
   触らずに幅の外の値(200000 × 0.4 = 80000)を argv へ載せ、単体の検も静的検査も
   素通しする形。契約の不変量は『導出点の戻り』ではなく『**argv に出る値**』に
   掛かっていないと守れない。"
  (and (isinstance pair list)
       (= (len pair) 2)
       (= (get pair 0) AUTOCOMPACT-ARG)
       (isinstance (get pair 1) str)
       (or (= (get pair 1) AUTOCOMPACT-AUTO)
           (and (.isdigit (get pair 1))
                (<= AUTOCOMPACT-MIN-TOKENS (int (get pair 1)) AUTOCOMPACT-MAX-TOKENS)))))


(defk claude-autocompact-args [params]
  {:pre [(: params dict)]
   :post [(: % list) (autocompact-arg-pair-ok %)]}
  "発射に載る圧縮の閾値の並び。**常に載る**(空の並びを返さない)。

   ⚠ effort(名乗らない席は旗そのものを出さない)とは向きが逆で、それが要点 —
   上の節の理由。後置条件が argv に出る値そのものを見る(上の述語の註)。"
  [AUTOCOMPACT-ARG (! (claude-autocompact-value params))])


;; claude の settings の綴り(この repo で 1 か所 — 判断は judgment.memory-home-of、値は charter の
;; `memory_dir`、綴りはここ)。実射 2026-09-20 で確かめた事実:
;;   * 効く綴りは `autoMemoryDirectory`(`memoryDir` は CLI 内部の property 名で、設定の鍵ではない —
;;     その綴りを渡した手番は黙って既定の家の置き場を作った)
;;   * `--settings` の inline JSON は flagSettings として読まれ、userSettings より先に効く
;; 反例つきの実射の記録は ADR-DOE-AGENTS-006 R11。
(setv CLAUDE-AUTO-MEMORY-DIR-SETTING "autoMemoryDirectory")
;; charter が運ぶ記憶の冊の欄(card acp:kanban-issue:ki-9fc7d4bca4dc)。綴りの正本は
;; sessionhost/acp/effects.py の CHARTER_MEMORY_FILES_KEY で、ここはその写し(検が突き合わせる)。
;; この層は行を読まない — 運ばれてきた {name, text} を置き場へ書くだけ。
(setv CLAUDE-MEMORY-FILES-KEY "memory_files")


(defk build-claude-argv [params]
  {:pre [(: params dict)]
   :post [(: % list)]}
  "claude の起動 argv。凍結物理:
   - `--dangerously-skip-permissions` + 既定では
     `--settings {\"disableAllHooks\":true}`(49b3549b 傷跡: 無人 session が
     config-dir 所有者の Stop-hook 連鎖で turn を切られた実障害)。
     ただし params session_hooks = \"inherit\"(daemon env knob
     DOEFF_AGENTD_SESSION_HOOKS — launch.hy が読む)ではこの pair を出さない:
     全 hook 無効化は安全側の hook(破壊的 git / pattern kill / push 門)まで
     切り、ACP 起動会話 241 母集団で発火 0 の実測(2026-08-18
     route-c03fe34745)。inherit は config-dir 所有者の hook 層が
     AGENT_SESSION_CLASS=unattended(spawn env — launch.hy)で会話種別
     self-gate することを契約前提にする。
   - effort → model の順(oracle 順序、無指定はフラグ自体を出さない)
   - model の後ろに `--autocompact <auto|tokens>` を**必ず**載せる(上の節の理由。
     値は params の欄、無ければ床)。⚠ 旧 Rust 実装はこの旗を持たない
     が、parity の基準ではない — 2026-07-06 の裁定で「Rust = oracle」は破棄され、
     canonical gate は Hy の session host(conformance/README.md 冒頭)
   - caller mcp_servers(sse)+ result channel(stdio)を単一 --mcp-config に
     まとめ、非空なら --strict-mcp-config を付ける
   - prompt は決して argv に載せない(live terminal transport のみ)・
     print mode(-p / --print)不使用"
  (setv args ["claude" "--dangerously-skip-permissions"])
  ;; --settings は 1 つだけ出す(2 回出すと後勝ちで片方が黙って消える)。中身は宣言の合流点:
  ;; hook の無効化(既定)と自動記憶の置き場(charter が運んだ時だけ)。両方とも無い手番では
  ;; 旗自体を出さない — 欄の無い charter の argv は今日と 1 byte も変わらない。
  (setv settings {})
  (when (!= (.get params "session_hooks") "inherit")
    (setv (get settings "disableAllHooks") True))
  (setv memory-dir (.get params "memory_dir"))
  (when (and (isinstance memory-dir str) (.strip memory-dir))
    (setv (get settings CLAUDE-AUTO-MEMORY-DIR-SETTING) memory-dir))
  (when settings
    (.extend args ["--settings" (json.dumps settings :separators #("," ":"))]))
  (setv effort (.get params "effort"))
  (when effort
    (.extend args ["--effort" effort]))
  (setv model (.get params "model"))
  (when model
    (.extend args ["--model" model]))
  ;; 圧縮の閾値は model の後ろ・mcp の前(凍結接頭と `--effort` の位置を動かさない)。
  (.extend args (! (claude-autocompact-args params)))
  (setv servers {})
  (for [[name url] (.items (.get params "mcp_servers" {}))]
    (setv (get servers name) {"type" "sse" "url" url}))
  (setv channel (.get params "result_channel"))
  (when channel
    (setv (get servers REPORT-RESULT-MCP-SERVER)
          {"type" "stdio"
           "command" (get channel "command")
           "args" (get channel "args")}))
  (when servers
    (.extend args ["--mcp-config"
                   (json.dumps {"mcpServers" servers} :separators #("," ":"))
                   "--strict-mcp-config"]))
  ;; ADR-006 R1: launch 時に鋳造した会話 identity を --session-id で注入する
  ;; (boot 前に identity が stored fact になる)。resume / fork の argv は
  ;; build-claude-resume-argv の所有 — ここは fresh launch のみ。
  (setv conversation (.get params "conversation"))
  (when (and (isinstance conversation dict)
             (is (.get params "resume_mode") None))
    (setv conv-id (.get conversation "session_id"))
    (when conv-id
      (.extend args ["--session-id" conv-id])))
  args)


(deff build-claude-resume-argv [params]
  {:pre [(: params dict)
         (in (.get params "resume_mode") #{"resume" "fork"})
         (: (.get params "conversation") dict)]
   :post [(: % list)]}
  "claude の resume / fork argv(ADR-DOE-AGENTS-006 R3)。凍結物理:
   - fresh launch と同じ基礎フラグ群(--dangerously-skip-permissions /
     --settings / effort / model / mcp-config)を共有し、並行実装を作らない
   - resume: `--resume <conversation session_id>`
   - fork: さらに `--fork-session`(claude が新 session ID を鋳造 — 新会話
     identity は事後発見: DiscoverConversation)
   - prompt は argv に載せない(BuildLaunch と同一の live-terminal 物理)"
  (setv base-params (dict params))
  (.pop base-params "conversation" None)
  (setv args (run (build-claude-argv base-params)))
  (setv conv-id (get (get params "conversation") "session_id"))
  (.extend args ["--resume" conv-id])
  (when (= (get params "resume_mode") "fork")
    (.append args "--fork-session"))
  args)


;; ---------------------------------------------------------------------------
;; trust 物理(oracle trust_claude_workspace — S12)
;; ---------------------------------------------------------------------------

(defk resolve-claude-config-dir [params]
  {:pre [(: params dict)]
   :post [(: % tuple)]}
  "実効 CLAUDE_CONFIG_DIR の解決(R7: typed binding → process env →
   HOME/.claude — session_env は非 auth overlay で運搬手段ではない)。
   戻り値 #(config-dir warnings) — 明示無しは DOE-003 R3 の
   staged enforcement で warning のみ(codex と違い reject しない)。"
  (setv binding (.get params "binding"))
  (setv warnings [])
  (setv config-dir (when (is-not binding None) (.get binding "config_dir")))
  (when (is None config-dir)
    (.append warnings
             (+ "claude session launched without an explicit CLAUDE_CONFIG_DIR "
                "auth profile (ADR-DOE-AGENTS-003 R3: enforcement follows once "
                "callers migrate)"))
    (<- from-env (env-get "CLAUDE_CONFIG_DIR"))
    (setv config-dir from-env))
  (when (is None config-dir)
    (<- home (env-get "HOME"))
    (when (is None home)
      (raise (RuntimeError
               "cannot resolve CLAUDE_CONFIG_DIR: no session_env entry, no process env, no HOME")))
    (setv config-dir f"{home}/.claude"))
  #(config-dir warnings))


(defk preseed-claude-trust [config-dir work-dir]
  {:pre [(: config-dir str) (: work-dir str)]
   :post [(: % "None — trust state の書き込みのみ")]}
  "`<CLAUDE_CONFIG_DIR>/.claude.json` へ per-workspace trust を pre-seed する
   (42fb28fa 傷跡: fresh workspace の trust ダイアログ永久ハング)。
   claude は projects を cwd の REALPATH でキーする(S12: /tmp →
   /private/tmp)ため canonicalize が先。temp+rename で torn read を防ぐ。"
  (<- _ (fs-make-dirs config-dir))
  (<- trusted-dir (fs-canonical-path work-dir))
  (setv state-path f"{config-dir}/.claude.json")
  (<- raw (fs-read-text state-path))
  (setv state (if (is None raw) {} (json.loads raw)))
  (when (not (isinstance state dict))
    (raise (RuntimeError f"claude state file is not a JSON object: {state-path}")))
  (setv projects (.setdefault state "projects" {}))
  (setv project (.setdefault projects trusted-dir {}))
  (setv (get project "hasTrustDialogAccepted") True)
  (setv (get project "hasCompletedProjectOnboarding") True)
  (<- _ (fs-write-text-atomic state-path (json.dumps state) ".agentd-tmp"))
  None)


(setv CLAUDE-SETTINGS-FILE "settings.json")

(deff claude-home-reading-label [config-dir status declarations]
  {:pre [(: config-dir str) (: status str) (: declarations list)]
   :post [(: % str)]}
  "家の中の読みの顛末を人間可読の 1 句にする(admission の文言の共有語彙)。
   宣言の**名**しか載せない — 値は決して文言に載らない。"
  (setv path f"{config-dir}/{CLAUDE-SETTINGS-FILE}")
  (cond
    (= status HOME-READING-ABSENT) f"{path} does not exist"
    (= status HOME-READING-MALFORMED) f"{path} is not a JSON object"
    declarations
      (+ f"{path} declares {(.join ", " declarations)}, which a metered kind "
         "does not accept")
    True f"{path} declares no metered credential"))


(defk claude-pre-launch [params]
  {:pre [(: params dict)]
   :post [(: % dict)]}
  "PreLaunchSetup の claude 実体: 実効 identity の解決(S14 の Hy positive 化 —
   launch program が session 行へ永続化する)+ 家の中の従量課金の宣言と binding の
   kind の一致の検め(2026-09・ADR-DOE-AGENTS-004 R9 改訂 — 全副作用より前)+
   trust pre-seed(skip_trust_setup で trust だけを飛ばす。oracle: gate は launch
   側で常時、trust は skip 可能 — claude に hard gate は無い)+ ADR-006 R1 の会話
   identity 鋳造(fresh launch では launch program がこの UUID を
   --session-id 注入と row.conversation の両方に使う — boot 前に identity が
   stored fact になる。resume / fork では捨てられる)。

   kind による分岐はこの 1 点だけ(並行実装を作らない — 本体は共有し、宣言の検めと
   identity の印だけが課金の階級で変わる)。鍵の**値**は読まない: 判定は policy の
   純関数が宣言の名だけを返し、host の memory にも log にも行にも残らない。"
  (<- resolved (resolve-claude-config-dir params))
  (setv [config-dir warnings] resolved)
  (setv billing (binding-billing-class (.get params "binding")))
  ;; 家の中の宣言の読み(全副作用より前)。`claude-code-metered` の受理形は
  ;; {config_dir} なので、admission を通った metered の launch には必ず binding が
  ;; 在り、config-dir は binding 由来(env / $HOME への fallback には届かない)。
  (<- settings-text (fs-read-text f"{config-dir}/{CLAUDE-SETTINGS-FILE}"))
  (setv [settings-status declarations usable]
        (! (claude-home-metered-reading settings-text)))
  ;; lane B の締め直し(ADR-DOE-AGENTS-003 R4 改訂): **定額**の kind の家に従量課金の
  ;; 宣言があれば拒否する。今日の検査は env の名しか見ないので、家の中に鍵を入れれば
  ;; 黙って通る道が開いていた — 黙って通る道は運用主の不変条件(従量課金を系に入れない)
  ;; を人の注意力だけで守る形で、課金の階級も型に現れない。宣言があるなら kind で
  ;; 名乗る(claude-code-metered)か、家から宣言を外す。
  ;; account(どの人の login か)は今も検めない — 検めるのは課金の階級の宣言だけ。
  ;; 破損・不在の settings.json は **通す**(今日と同じ。破損は claude 自身が loud に
  ;; 落ちる)— ただし破損は warning に 1 行残す。
  (when (and (= billing BILLING-SUBSCRIPTION) declarations)
    (raise (RuntimeError
             (+ f"session.launch: binding kind '{(.get (.get params "binding") "kind")}' is a "
                f"subscription kind, but {config-dir}/{CLAUDE-SETTINGS-FILE} declares metered "
                f"billing ({(.join ", " declarations)}). Either declare the home with the "
                "metered kind 'claude-code-metered', or remove the metered declaration from "
                "the home. The billing class must be visible in the binding kind — a "
                "subscription kind whose home bills per use is exactly the silent path this "
                "contract closes (ADR-DOE-AGENTS-003 R4 / -004 R9)."))))
  (when (and (= billing BILLING-SUBSCRIPTION) (= settings-status HOME-READING-MALFORMED))
    (.append warnings
             (+ f"{config-dir}/{CLAUDE-SETTINGS-FILE} is not a JSON object, so its billing "
                "declaration could not be checked (the CLI fails loud on a broken settings "
                "file; ADR-DOE-AGENTS-003 R4)")))
  (when (and (= billing BILLING-METERED) (is usable None))
    (raise (RuntimeError
             (+ "session.launch: binding kind 'claude-code-metered' declares metered "
                f"billing, but {(claude-home-reading-label config-dir settings-status declarations)}. "
                f"Write {{\"{CLAUDE-SETTINGS-API-KEY-HELPER}\": \"cat <path to a 0600 file "
                f"holding the API key>\"}} into {config-dir}/{CLAUDE-SETTINGS-FILE}, or declare "
                f"Vertex there with env {CLAUDE-SETTINGS-VERTEX-ENV}=1 and "
                f"{CLAUDE-SETTINGS-VERTEX-PROJECT-ENV}=<project>. The host never reads the "
                "credential value — it only checks that the home declares one "
                "(ADR-DOE-AGENTS-004 R9)."))))
  ;; 自動記憶の置き場は起こす前に在らせる(CLI 側も作るが、無い dir を設定で指さない)。
  ;; 判断は judgment.memory-home-of の 1 点 — ここは charter が運んだ path を実体化するだけで、
  ;; path を組まない。codex は対象外: codex の作業状態は profile dir の側の話で、
  ;; claude の auto-memory に当たる置き場を持たない。
  (setv memory-dir (.get params "memory_dir"))
  (when (and (isinstance memory-dir str) (.strip memory-dir))
    (<- _ (fs-make-dirs memory-dir))
    ;; card acp:kanban-issue:ki-9fc7d4bca4dc(法 ACP 575b1e conversation-memory-lives-in-the-row):
    ;; 記憶の正本は ACP の行で、置き場は手番ごとの写し。charter が運んできた冊(起こす側が行から読んで
    ;; 載せた — history / first_turn と同じ形)をここで実体化する。索引 MEMORY.md も同じ列に入っていて、
    ;; 行から導いた本文で毎手番上書きされる(file としての正本を持たない)。
    ;; ⚠ **行を読むのはここではない**: この module は substrate-clean(生 IO 禁止・Fs* / EnvGet だけ)なので、
    ;; ACP も記録の service も import しない。運ぶのは charter の 1 欄ちょうど。
    ;; 置き場に残った余りの file は消さない(消す動詞をこの層に置かない)— 退役した冊を書き戻さないのは
    ;; 起こす側の判断(judgment.memory-row-retired?)。
    (for [book (or (.get params CLAUDE-MEMORY-FILES-KEY) [])]
      (setv book-name (if (isinstance book dict) (.get book "name") None))
      (setv book-text (if (isinstance book dict) (.get book "text") None))
      (when (and (isinstance book-name str) (isinstance book-text str)
                 (.strip book-name) (not (in "/" book-name)) (not (.startswith book-name ".")))
        (<- _ (fs-write-text-atomic f"{memory-dir}/{book-name}" book-text ".agentd-tmp")))))
  (when (not (.get params "skip_trust_setup" False))
    (<- _ (preseed-claude-trust config-dir (get params "work_dir"))))
  (setv identity {"CLAUDE_CONFIG_DIR" config-dir
                  "warnings" warnings
                  "conversation" {"session_id" (str (uuid.uuid4))}})
  ;; 課金の階級の印(行の effective_identity に残る — resume はこの印で kind を
  ;; 選ぶ)。env には出ない: launch-spawn-env は binding 所有キーだけを拾う。
  (when (= billing BILLING-METERED)
    (setv (get identity "billing") BILLING-METERED))
  identity)


;; ---------------------------------------------------------------------------
;; 会話 identity の事後発見(ADR-006 R1 — claude では fork の新 ID 用)
;; ---------------------------------------------------------------------------

(defk claude-discover-conversation [params]
  {:pre [(: params dict)]
   :post [(: % (| dict None))]}
  "claude の会話 identity 発見。物理: transcripts は
   `<CLAUDE_CONFIG_DIR>/projects/<mangled canonical work_dir>/<uuid>.jsonl`
   (mangle = 非英数字を '-' に置換。project key は S12 と同じく canonicalize
   済み cwd)。exclude_session_ids(既知の全会話 + fork 親)を除いた候補が
   ちょうど 1 つのときだけ捕獲する — 複数は曖昧(同 cwd の他 session の
   可能性)なので None を返し、次 cycle に委ねる(level-triggered)。"
  (setv identity (or (.get params "effective_identity") {}))
  (setv config-dir (.get identity "CLAUDE_CONFIG_DIR"))
  (setv excludes (set (or (.get params "exclude_session_ids") [])))
  (if (is config-dir None)
      None
      (do
        (<- canon (fs-canonical-path (get params "work_dir")))
        (setv mangled (re.sub "[^A-Za-z0-9]" "-" canon))
        (setv project-dir f"{config-dir}/projects/{mangled}")
        (<- entries (fs-list-dir project-dir))
        (setv candidates
              (sorted (lfor name entries
                            :if (and (.endswith name ".jsonl")
                                     (not-in (cut name 0 -6) excludes))
                            (cut name 0 -6))))
        (if (= (len candidates) 1)
            {"session_id" (get candidates 0)}
            None))))


;; ---------------------------------------------------------------------------
;; 会話記録の鮮度観測(ADR-002 R-conversation-evidence — turn 生死のデータ層証拠)
;; ---------------------------------------------------------------------------

(defk claude-conversation-activity [params]
  {:pre [(: params dict)]
   :post [(: % (| float None))]}
  "claude の会話記録の最終更新時刻(epoch 秒)。物理: transcript は
   `<CLAUDE_CONFIG_DIR>/projects/<mangled canonical work_dir>/<sid>.jsonl`
   (claude-discover-conversation / transplant と同じ家)。材料不足
   (identity / conversation / work_dir の欠け)や実体不在は None —
   probe は反証面であって門ではなく、None は表示層の従来物理へ fallback
   する(退行ゼロ)。"
  (setv identity (or (.get params "effective_identity") {}))
  (setv config-dir (.get identity "CLAUDE_CONFIG_DIR"))
  (setv conv (or (.get params "conversation") {}))
  (setv conv-id (.get conv "session_id"))
  (setv work-dir (.get params "work_dir"))
  (if (or (is config-dir None) (is conv-id None) (not work-dir))
      None
      (do
        (<- canon (fs-canonical-path work-dir))
        (setv mangled (re.sub "[^A-Za-z0-9]" "-" canon))
        (<- mt (fs-file-mtime f"{config-dir}/projects/{mangled}/{conv-id}.jsonl"))
        mt)))


;; ---------------------------------------------------------------------------
;; cross-binding transplant(ADR-006 改訂 R7 — 別 auth home への会話の持ち出し)
;; ---------------------------------------------------------------------------

(defk claude-transplant-conversation [params]
  {:pre [(: params dict)]
   :post [(: % dict)]}
  "transcript の実在検査 + cross-binding transplant(ADR-DOE-AGENTS-006
   R7/R10)。物理 = dotfiles agentcli share.py の 4 対と同型: transcript
   (必須 — 不在は typed 値で返し、resume-session が
   transcript_not_discoverable の reject にする)+ sessions-index.json /
   session-env/<sid> / file-history/<sid>(best-effort: あれば張る・無ければ
   skip)。所有 profile は source 行の effective_identity で既知なので
   registry 走査は持ち込まない。同一 home(binding config_dir = source の
   CLAUDE_CONFIG_DIR)は敷設 no-op だが実在検査は同じ(R10 — 起動段で死んだ
   行〔identity 鋳造済み・transcript 未実体化〕の resume を実 CLI の
   『No conversation found』120s 死に落とさない)。transcript の家は
   projects/<mangled canonical work_dir>/(resume-physics.md 2026-08-11
   プローブ (b): symlink 越しの --resume が文脈を保ち、追記は解決先の実体へ
   届く)。"
  (setv binding (get params "binding"))
  (setv target-dir (get binding "config_dir"))
  (setv identity (or (.get params "source_identity") {}))
  (setv source-dir (.get identity "CLAUDE_CONFIG_DIR"))
  (setv conv-id (get (get params "conversation") "session_id"))
  (when (is source-dir None)
    (return {"ok" False
             "code" RESUME-ERR-TRANSCRIPT-NOT-DISCOVERABLE
             "message" (+ "session.resume: the source incarnation of "
                          f"conversation '{conv-id}' has no recorded "
                          "CLAUDE_CONFIG_DIR — its transcript cannot be "
                          "located for a cross-binding transplant "
                          "(transcript-not-discoverable)")}))
  (<- canon (fs-canonical-path (get params "work_dir")))
  (setv mangled (re.sub "[^A-Za-z0-9]" "-" canon))
  (setv source-project f"{source-dir}/projects/{mangled}")
  (setv target-project f"{target-dir}/projects/{mangled}")
  (setv source-transcript f"{source-project}/{conv-id}.jsonl")
  ;; R10: 実在検査は same-home / cross-home 共通(symlink は解決先で判定)。
  (<- transcript-present (fs-file-exists source-transcript))
  (when (not transcript-present)
    (return {"ok" False
             "code" RESUME-ERR-TRANSCRIPT-NOT-DISCOVERABLE
             "message" (+ f"session.resume: transcript "
                          f"'{source-transcript}' does not exist "
                          "(transcript-not-discoverable) — the real CLI "
                          "fails loud without it (resume-physics.md probe "
                          "(a)); rehosting the conversation requires the "
                          "source transcript")}))
  (when (= source-dir target-dir)
    (return {"ok" True "action" "same-home"}))
  (<- outcome (fs-link-artifact source-transcript
                                f"{target-project}/{conv-id}.jsonl"))
  (when (= outcome "source-missing")
    (return {"ok" False
             "code" RESUME-ERR-TRANSCRIPT-NOT-DISCOVERABLE
             "message" (+ f"session.resume: transcript "
                          f"'{source-transcript}' does not exist "
                          "(transcript-not-discoverable) — a cross-binding "
                          "transplant requires the source transcript "
                          "(resume-physics.md probe (a): the real CLI fails "
                          "loud without it)")}))
  ;; 周辺 artifact は best-effort(share.py の残り 3 対と同型)。
  (<- _ (fs-link-artifact f"{source-project}/sessions-index.json"
                          f"{target-project}/sessions-index.json"))
  (<- _ (fs-link-artifact f"{source-dir}/session-env/{conv-id}"
                          f"{target-dir}/session-env/{conv-id}"))
  (<- _ (fs-link-artifact f"{source-dir}/file-history/{conv-id}"
                          f"{target-dir}/file-history/{conv-id}"))
  {"ok" True "action" outcome})


;; ---------------------------------------------------------------------------
;; per-kind defhandler(R2: 直接束縛と host 束縛の両方で同一モジュール)
;; ---------------------------------------------------------------------------

(defhandler claude-code-impl [result-command]
  (BuildLaunch [agent-type params]
    :when (= agent-type "claude")
    (<- argv (build-claude-argv params))
    (resume argv))

  (BuildResume [agent-type params]
    :when (= agent-type "claude")
    (resume (build-claude-resume-argv params)))

  (DiscoverConversation [agent-type params]
    :when (= agent-type "claude")
    (<- found (claude-discover-conversation params))
    (resume found))

  (ProbeConversationActivity [agent-type params]
    :when (= agent-type "claude")
    (<- activity-at (claude-conversation-activity params))
    (resume activity-at))

  (TransplantConversation [agent-type params]
    :when (= agent-type "claude")
    (<- transplanted (claude-transplant-conversation params))
    (resume transplanted))

  (PreLaunchSetup [agent-type params]
    :when (= agent-type "claude")
    (<- identity (claude-pre-launch params))
    (resume identity))

  (ClassifyPane [agent-type output]
    :when (= agent-type "claude")
    (<- observation (classify-output output))
    (resume observation))

  (WireResultChannel [agent-type session-id socket-path]
    :when (= agent-type "claude")
    (resume (result-channel-spec result-command session-id socket-path)))

  (DeliverMessage [pane-id text]
    ;; live REPL への paste + submit。盲窓(paste→confirm ループ)の物理は
    ;; substrate の TmuxSendKeys(literal+submit)所有 — impl は転送のみ。
    (<- _ (tmux-send-keys pane-id text True True))
    (resume None)))
