;;; Hy session host(ADR-DOE-AGENTS-004 C3)— 寿命の外部性の唯一の家。
;;;
;;; oracle = agentd-rust-final:src/main.rs。ここが verbatim 移植するのは
;;; host 層の凍結物理: wire 封筒(独自 JSON-lines、main.rs:210-235)・
;;; CLI(parse_args :600-693)・default path(:718-742)・単一インスタンス
;;; 拒否(prepare_socket_path :1079-1092)・serve/dispatch(:1159-1301)。
;;;
;;; C3 実行設計(ACP plan)の実装順 4 まで: wire 封筒 + store-of-record
;;; (store.hy の StoreActor / lease / latch clear)+ RPC→program 写像
;;; (launch-session / monitor-cycle / capture / send / cancel / cleanup)+
;;; monitor / heartbeat thread + result 経路(await_result の blocking poll /
;;; report_result の first-write-wins)。全 10 契約 method 実装済み。
;;;
;;; report-result-mcp stdio relay は relaymain.py(stdlib-only 純 Python)に
;;; 住む: relay の boot レイテンシは report-vs-turn-end race の凍結物理で、
;;; Hy import 連鎖を払うと golden path で solicitation を焼く(S1 実測)。
;;; subcommand dispatch も hostmain.py が Hy import より先に行う。

(require doeff-hy.macros [deff defk <-])

(import dataclasses [dataclass replace asdict])
(import datetime [datetime timedelta timezone])
(import json)
(import os)
(import signal)
(import socket)
(import re)
(import sqlite3)
(import sys)
(import threading)
(import doeff_agents.sessionhost.attachment [TurnAttachment attachment-of-wire])
(import time)

(import doeff [run])

(import doeff_agents.sessionhost.effects [
  AWAITING-RESPONSE-TIMEOUT-SECONDS
  CONVERSATION-QUIESCENCE-SECONDS
  MonitorKnobs
  PASTE-RESUBMIT-LIMIT
  REPL-IDLE-MAX-WAIT-SECONDS
  SessionRow
  clock-now
  session-store-get
  session-store-record-event
  session-store-upsert
  tmux-capture
  tmux-has-session
  tmux-kill-session
  tmux-send-keys
  tmux-session-pane-ids])
(import doeff_agents.sessionhost.adopt [AdoptTargetNotFound adopt-program])
(import doeff_agents.sessionhost.impls.claude_code [claude-code-impl])
(import doeff_agents.sessionhost.impls.codex [codex-impl])
(import doeff_agents.sessionhost.turn [TURN-HOLDER-AGENT
                                       db-turn-stamp
                                       turn-close-holder])
(import doeff_agents.sessionhost.launch [launch-session
                                         resume-session
                                         ResumeRejected])
(import doeff_agents.sessionhost.policy [
  ACTIVE-STATUSES
  binding-kind-advertisement
  carry-charter-fields
  cause-if-absent
  is-run-to-completion
  is-terminal-status
  iso-format
  make-cause
  monitor-cycle
  session-env-admission-error
  tail-chars
  TURN-CARRIED-KEYS
  turn-stalled])
(import doeff_agents.sessionhost.schema [validate-against-schema schema-admission-error])
(import doeff_agents.sessionhost.substrate [real-substrate])
(import doeff_agents.sessionhost.substrate_herdr [DEFAULT-HERDR-SOCKET herdr-substrate])
(import doeff_agents.sessionhost.substrate_headless [HEADLESS-REGISTRY headless-spawn-env headless-substrate])
(import doeff_agents.sessionhost.drivers [driver-listing])
(import doeff_agents.sessionhost.impls.headless_argv [headless-argv-impl])
(import doeff_agents.sessionhost.effects [headless-kill headless-liveness])
(import doeff_agents.sessionhost.headless_protocol [backend-alive])
(import doeff_agents.sessionhost.cache_host [cache-host-ping cache-host-probe cache-host-guard-normal-send cache-host-cancel cache-last-success-at])
(import doeff_agents.sessionhost.cache_host_model [CacheMaintenanceActiveError CACHE-MAINTENANCE-ACTIVE])
(import doeff_agents.sessionhost.cache_host_model [HostCacheRead])
(import doeff_agents.sessionhost.cache_host_store [session-mutation-lock])
(import doeff_agents.sessionhost.headless [
  HEADLESS-BACKEND-KIND
  EVENT-SESSION-INTERRUPTED
  headless-cancel-program
  headless-capture-program
  headless-cleanup-program
  SEND-MODE-INTERRUPT
  SEND-MODE-TURN
  SEND-MODES
  headless-escalate-program
  headless-inject-program
  headless-interrupt-program
  headless-launch-session
  headless-monitor-cycle
  headless-send-program
  recover-headless-rows
  stop-headless-rows])
(import doeff_agents.sessionhost.store [
  HISTORY-PRUNE-BATCH-ROWS
  LEASE-TTL-SECONDS
  StoreActor
  actor-prune-history
  db-acquire-lease
  db-clear-awaiting-latches
  db-count-active
  db-heartbeat-once
  db-read-lease
  db-late-result-accept
  db-mark-cleaned
  db-record-command
  db-record-event
  db-release-lease
  db-report-result-guarded-update
  db-session-get
  db-session-list
  db-session-by-conversation
  db-vacuum-if-bloated
  snapshot-to-wire-dict
  sqlite-session-store])


;; ---------------------------------------------------------------------------
;; 凍結定数(oracle main.rs:18-169)
;; ---------------------------------------------------------------------------

(setv DEFAULT-MONITOR-INTERVAL-MS 1000)
;; session.wait_events(出来事の journal の long-poll・段 12 lane 12b・agora-redesign #207 根 1)の
;; 1 回の待ちの上限(秒)。呼び手(agentd)はこれより短い上限を名乗って張り直す。
(setv WAIT-EVENTS-MAX-SECONDS 60.0)
(setv DEFAULT-MAX-RUNNING-SESSIONS 10)
(setv LAUNCH-TIMEOUT-SECONDS 60)
(setv STALE-OBSERVATION-THRESHOLD-SECONDS 300)
(setv DEFAULT-RESULT-SOLICITATION-LIMIT 2)
;; 監査履歴(events / commands)の retention(oracle に無い追加 — 2026-07-27
;; wedge 根治)。境界より古い行は起動時 + 毎時の prune が刈る。env knob
;; DOEFF_AGENTD_HISTORY_RETENTION_DAYS(use-site 読み — 他 knob と同じ)。
(setv HISTORY-RETENTION-DAYS-DEFAULT 14)
(setv HISTORY-PRUNE-INTERVAL-SECONDS 3600)
(setv DEFAULT-PROMPT-STALL-SECONDS 180)
(setv DEFAULT-PROMPT-UNBLOCK-LIMIT 3)
;; sh -c 越しに走るので settings JSON は single-quote で word splitting を
;; 生き延びる(oracle DEFAULT_PROMPT_JUDGE_CMD :163-164 verbatim)。judge は
;; agent session ではなく裁定 subprocess — one-shot 実行が oracle 凍結物理
;; そのものなので live-transport rule の対象外(inline nosemgrep。同定数の
;; もう 1 箇所は oracle main.rs:164)。
(setv DEFAULT-PROMPT-JUDGE-CMD
      "claude -p --settings '{\"disableAllHooks\":true}' --model haiku") ; nosemgrep: doeff-agents-no-claude-print-mode

;; out-of-band 寿命境界(orphan boundary)の poll / backstop。oracle に無い
;; opt-in 追加(意味論不変 — env knob 未設定なら経路ごと不在)。詳細は
;; orphan-watch-loop の docstring。
(setv ORPHAN-POLL-INTERVAL-SECONDS 1.0)
(setv ORPHAN-EXIT-BACKSTOP-SECONDS 10.0)

;; 構造化 wire エラーコード(oracle :107-120)。
(setv RPC-ERR-AWAIT-TIMEOUT -32000)
(setv RPC-ERR-NO-SUCH-SESSION -32001)
(setv RPC-ERR-RESULT-REJECTED -32002)
(setv RPC-ERR-ALREADY-TERMINAL -32003)
;; koine session surface v0(ADR-DOE-AGENTS-007 R2): koine 由来の新契約は
;; 数値でなく typed 文字列 error_code を使う(数値表は oracle 凍結語彙 —
;; 新語彙をそこへ足さない)。
(setv RPC-ERR-ADOPT-TARGET-NOT-FOUND "adopt_target_not_found")

;; turn 打刻 counters(ADR-007 §4: daemon.status へ additive)。in-memory で
;; 開始 — 永続化は実測需要が出てから。unadopted は adopt 網羅の計器を兼ねる
;; (turn-stamp-path 決定 3: 未 adopt 打刻は正直 no-op + 可視 counter)。
(setv TURN-COUNTERS {"turn_stamp_unadopted" 0 "turn_stamp_resolved" 0})

;; koine 条項 4 の stalled 導出閾値(既定 1800 — 発注元確定 2026-07-21)。
(setv DEFAULT-TURN-STALL-SECONDS 1800)

;; session.await_result の timeout 物理(oracle :89-103)。
(setv DEFAULT-AWAIT-TIMEOUT-SECONDS 600.0)
(setv MIN-AWAIT-TIMEOUT-SECONDS 1.0)
(setv MAX-AWAIT-TIMEOUT-SECONDS 3600.0)
(setv AWAIT-POLL-INTERVAL-SECONDS 0.5)

;; await が「終端」と見なす status(oracle is_await_terminal_status
;; :2925-2930 — is_terminal_status の 5 つ + lost)。
(setv AWAIT-TERMINAL-STATUSES
      #{"done" "failed" "cancelled" "exited" "stopped" "lost"})

;; wire 上の任意 JSON 値(serde_json::Value 相当)。id / params / result の
;; contract 型 — json.loads が返し得る全形。
(setv JsonValue (| dict list str int float bool None))


(defclass RpcHostError [Exception]
  "構造化 wire エラー(oracle RpcError :241-253)— dispatch が code を
   error_code として封筒に載せる。"
  (defn __init__ [self code message]
    (.__init__ Exception self message)
    (setv self.code code)
    (setv self.message message)))


(defclass [(dataclass :frozen True :kw-only True)] HostConfig []
  "daemon 設定(oracle Config)。monitor-interval は秒(float)で持つ。
   backend は mux substrate の選択(tmux | herdr — herdr トライアル、
   既定 tmux)。herdr-socket は backend=herdr のときのみ使う。"
  #^ str db-path
  #^ str socket-path
  #^ str tmux-bin
  #^ float monitor-interval-seconds
  ;; 同時走行の上限。None = 上限なし(admission の check ごと飛ぶ —
  ;; launch.hy の `(when (is-not max-running None) …)`)。無制限は設計として
  ;; 正規の形で、抜け道ではない: 直接束縛(host を通さない使い方)では
  ;; そもそも config が無く、params に key が載らない = 無制限。
  ;; ⚠ 0 は無制限ではなく全拒否(判定が `owned-count >= max-running` なので
  ;; 0 は常に真)。CLI は 0 を typed に拒む。
  #^ (| int None) max-running
  ;; 従量課金の binding kind を受けるか(2026-09・ADR-DOE-AGENTS-004 R9 改訂)。
  ;; 既定 False = fail-closed: 旗を立てていない配備では metered の kind は
  ;; 1 件も起動しない(判定は launch.hy の admission の 1 点)。起動時に固定で
  ;; 再読込は無い — 変えるには host の再起動(所有者は supervisor・R10(d))。
  #^ bool allow-metered-billing
  (setv allow-metered-billing False)
  #^ int result-solicitation-limit
  #^ int prompt-stall-seconds
  #^ int prompt-unblock-limit
  #^ (| str None) prompt-judge-cmd
  #^ str backend
  (setv backend "tmux")
  #^ str herdr-socket
  (setv herdr-socket DEFAULT-HERDR-SOCKET)
  ;; backend=headless の実況の正本(events file)の置き場。既定は
  ;; $XDG_STATE_HOME/doeff/headless(store DB と同じ解決系)。
  #^ str headless-events-root
  (setv headless-events-root "")
  ;; out-of-band 寿命境界(opt-in): spawn 元の死で自己終了 + launch 済み
  ;; session の reap。conformance harness が常時立てる(S28)。
  #^ bool exit-when-orphaned
  (setv exit-when-orphaned False))


;; ---------------------------------------------------------------------------
;; env knob / default path(oracle :695-742)
;; ---------------------------------------------------------------------------

(deff env-u32 [name]
  {:pre [(: name str)]
   :post [(: % (| int None))]}
  "非負 int の env 読み(oracle env_u32: parse 失敗は None = 既定へ fallback)。"
  (setv raw (.get os.environ name))
  (when (is raw None) (return None))
  (try
    (setv value (int (.strip raw)))
    (except [ValueError] (return None)))
  (if (>= value 0) value None))

(deff env-positive-i64 [name]
  {:pre [(: name str)]
   :post [(: % (| int None))]}
  "正 int の env 読み(oracle env_positive_i64: 0 以下・parse 失敗は None)。"
  (setv raw (.get os.environ name))
  (when (is raw None) (return None))
  (try
    (setv value (int (.strip raw)))
    (except [ValueError] (return None)))
  (if (> value 0) value None))

(defk normalize-prompt-judge-cmd [raw]
  {:pre [(: raw str)]
   :post [(: % (| str None))]}
  "空白のみの judge cmd は None = judge 無効(oracle normalize_prompt_judge_cmd)。
   conformance は『空文字 = 無効』のこの意味論に依存する(ハザード 1)。"
  (setv trimmed (.strip raw))
  (if (= trimmed "") None trimmed))

(deff home-dir []
  {:pre [True]
   :post [(: % str)]}
  (.get os.environ "HOME" "."))

(deff xdg-state-home []
  {:pre [True]
   :post [(: % str)]}
  (setv explicit (.get os.environ "XDG_STATE_HOME"))
  (if (is-not explicit None)
      explicit
      (os.path.join (home-dir) ".local" "state")))

(deff default-db-path []
  {:pre [True]
   :post [(: % str)]}
  "$XDG_STATE_HOME/doeff/agentd.sqlite(oracle default_db_path :718-720)。"
  (os.path.join (xdg-state-home) "doeff" "agentd.sqlite"))

(defk default-headless-events-root []
  {:pre [True]
   :post [(: % str)]}
  "$XDG_STATE_HOME/doeff/headless(headless backend の events file の置き場 —
   env knob DOEFF_SESSIONHOST_HEADLESS_DIR で上書き)。"
  (os.path.join (xdg-state-home) "doeff" "headless"))

(deff default-socket-path []
  {:pre [True]
   :post [(: % str)]}
  "$XDG_RUNTIME_DIR/doeff/agentd.sock、無ければ /tmp/doeff-agentd-$USER.sock
   (oracle default_socket_path :722-730。USER → LOGNAME → \"unknown\")。"
  (setv runtime-dir (.get os.environ "XDG_RUNTIME_DIR"))
  (when (is-not runtime-dir None)
    (return (os.path.join runtime-dir "doeff" "agentd.sock")))
  (setv user (or (.get os.environ "USER") (.get os.environ "LOGNAME") "unknown"))
  (os.path.join "/tmp" f"doeff-agentd-{user}.sock"))


;; ---------------------------------------------------------------------------
;; CLI(oracle parse_args :600-693)
;; ---------------------------------------------------------------------------

;; flag・command・env knob の綴りの座(この 1 点)。parse-args の分岐も
;; `--help` の usage の組み立て(sessionhost/usage.py)も同じ値を読む —
;; 綴りを手で写した一覧をどこにも作らない(一覧が実装から静かに乖離する形を
;; 作らない)。
(setv CMD-SERVE "serve")
(setv FLAG-DB "--db")
(setv FLAG-SOCKET "--socket")
(setv FLAG-TMUX "--tmux")
(setv FLAG-MONITOR-INTERVAL-MS "--monitor-interval-ms")
(setv FLAG-MAX-RUNNING "--max-running")
(setv FLAG-ALLOW-METERED-BILLING "--allow-metered-billing")
(setv FLAG-RESULT-SOLICITATIONS "--result-solicitations")
(setv FLAG-PROMPT-STALL-SECS "--prompt-stall-secs")
(setv FLAG-PROMPT-UNBLOCK-ATTEMPTS "--prompt-unblock-attempts")
(setv FLAG-PROMPT-JUDGE-CMD "--prompt-judge-cmd")
(setv FLAG-BACKEND "--backend")
(setv FLAG-HERDR-SOCKET "--herdr-socket")
(setv ENV-RESULT-SOLICITATIONS "DOEFF_AGENTD_RESULT_SOLICITATIONS")
(setv ENV-PROMPT-STALL-SECS "DOEFF_AGENTD_PROMPT_STALL_SECS")
(setv ENV-PROMPT-UNBLOCK-ATTEMPTS "DOEFF_AGENTD_PROMPT_UNBLOCK_ATTEMPTS")
(setv ENV-PROMPT-JUDGE-CMD "DOEFF_AGENTD_PROMPT_JUDGE_CMD")
(setv ENV-BACKEND "DOEFF_SESSIONHOST_BACKEND")
(setv ENV-HERDR-SOCKET "DOEFF_SESSIONHOST_HERDR_SOCKET")
(setv ENV-HEADLESS-DIR "DOEFF_SESSIONHOST_HEADLESS_DIR")
(setv ENV-EXIT-WHEN-ORPHANED "DOEFF_SESSIONHOST_EXIT_WHEN_ORPHANED")

;; serve の flag の一覧 = usage の生成元(並びがそのまま help の並び)。
;;   #(flag 値の見出し env の名 説明)
;; 値を取らない旗は見出しが None、env knob を持たない flag は env が None。
;; parse-args に flag を足す時はこの表にも 1 行足す — 表と parse-args の
;; 一致は deftest test-serve-flag-specs-cover-parse-args が守る。
(setv SERVE-FLAG-SPECS
  [#(FLAG-DB "<path>" None
     "Session ledger (SQLite). Default: $XDG_STATE_HOME/doeff/agentd.sqlite")
   #(FLAG-SOCKET "<path>" None
     (+ "RPC socket to bind. Default: $XDG_RUNTIME_DIR/doeff/agentd.sock, "
        "else /tmp/doeff-agentd-$USER.sock"))
   #(FLAG-BACKEND "<tmux|herdr|headless>" ENV-BACKEND
     "Substrate that carries sessions. Default: tmux")
   #(FLAG-TMUX "<bin>" None
     "tmux binary (tmux backend only). Default: tmux")
   #(FLAG-HERDR-SOCKET "<path>" ENV-HERDR-SOCKET
     f"herdr control socket (herdr backend only). Default: {DEFAULT-HERDR-SOCKET}")
   #(FLAG-MAX-RUNNING "<n|none|unlimited>" None
     (+ f"Launch admission ceiling. Default: {DEFAULT-MAX-RUNNING-SESSIONS}. "
        "`none` / `unlimited` lift it; 0 is refused at startup because it "
        "would reject every launch."))
   #(FLAG-ALLOW-METERED-BILLING None None
     (+ "Admit metered binding kinds (claude-code-metered / codex-metered). "
        "Off by default; no environment variable turns it on."))
   #(FLAG-MONITOR-INTERVAL-MS "<ms>" None
     f"Reconciler interval in milliseconds. Default: {DEFAULT-MONITOR-INTERVAL-MS}")
   #(FLAG-RESULT-SOLICITATIONS "<n>" ENV-RESULT-SOLICITATIONS
     (+ "How many times a missing agent result is solicited. Default: "
        f"{DEFAULT-RESULT-SOLICITATION-LIMIT}"))
   #(FLAG-PROMPT-STALL-SECS "<n>" ENV-PROMPT-STALL-SECS
     (+ "Seconds before an undelivered prompt counts as stalled. Default: "
        f"{DEFAULT-PROMPT-STALL-SECONDS}"))
   #(FLAG-PROMPT-UNBLOCK-ATTEMPTS "<n>" ENV-PROMPT-UNBLOCK-ATTEMPTS
     (+ "How many unblock attempts a stalled prompt gets. Default: "
        f"{DEFAULT-PROMPT-UNBLOCK-LIMIT}"))
   #(FLAG-PROMPT-JUDGE-CMD "<cmd>" ENV-PROMPT-JUDGE-CMD
     "Adjudicator subprocess consulted before an unblock attempt.")])

;; flag を持たない env knob(usage は別枠で出す — 読みは parse-args の同じ 1 点)。
(setv SERVE-ENV-ONLY-SPECS
  [#(ENV-HEADLESS-DIR
     (+ "Directory holding the headless backend's event files. Default: "
        "$XDG_STATE_HOME/doeff/headless"))
   #(ENV-EXIT-WHEN-ORPHANED
     (+ "Set to 1 to make the host exit when its parent goes away "
        "(opt-in lifetime boundary)."))])

(deff arg-at [args index]
  {:pre [(: args list) (: index int)]
   :post [(: % (| str None))]}
  "args.get(index) 同等(範囲外は None — oracle は --db/--socket の値欠落を
   黙って default へ fallback する。この quirk も parity)。"
  (if (< index (len args)) (get args index) None))

(deff required-arg [args index flag]
  {:pre [(: args list) (: index int) (: flag str)]
   :post [(: % str)]}
  (setv value (arg-at args index))
  (when (is value None)
    (raise (ValueError f"{flag} requires a value")))
  value)

(deff parse-args [args]
  {:pre [(: args list)]
   :post [(: % HostConfig)]}
  "oracle parse_args の verbatim 移植: flag 網羅・env knob の parse 時読み・
   unknown argument 拒否・command は serve のみ。"
  (setv db-path None)
  (setv socket-path None)
  (setv tmux-bin "tmux")
  (setv monitor-interval-seconds (/ DEFAULT-MONITOR-INTERVAL-MS 1000))
  (setv max-running DEFAULT-MAX-RUNNING-SESSIONS)
  ;; 値を取らない旗(既定 off)。env knob は作らない — 課金の方針を env の 1 語で
  ;; 変えられる形は ADR-DOE-AGENTS-004 R10(d) が退けた形と同じ(監督の穴を env
  ;; 宣言は塞げない)。配備は argv(launchd / systemd の ExecStart)で名乗る。
  (setv allow-metered-billing False)
  (setv result-solicitation-limit
        (or (env-u32 ENV-RESULT-SOLICITATIONS)
            DEFAULT-RESULT-SOLICITATION-LIMIT))
  (setv prompt-stall-seconds
        (or (env-positive-i64 ENV-PROMPT-STALL-SECS)
            DEFAULT-PROMPT-STALL-SECONDS))
  (setv prompt-unblock-limit
        (or (env-u32 ENV-PROMPT-UNBLOCK-ATTEMPTS)
            DEFAULT-PROMPT-UNBLOCK-LIMIT))
  (setv prompt-judge-cmd
        (run (normalize-prompt-judge-cmd
               (.get os.environ ENV-PROMPT-JUDGE-CMD
                     DEFAULT-PROMPT-JUDGE-CMD))))
  ;; herdr トライアルの transfer gate: conformance harness は daemon の argv を
  ;; 組み替えないため、env knob(flag が優先)で backend を切り替えられるように
  ;; する(CONFORMANCE_AGENTD_BIN seam と組で使う)。
  (setv backend (.get os.environ ENV-BACKEND "tmux"))
  (setv herdr-socket (.get os.environ ENV-HERDR-SOCKET
                           DEFAULT-HERDR-SOCKET))
  ;; headless backend(agora-redesign #37): events file の置き場も env knob。
  (setv headless-events-root (.get os.environ ENV-HEADLESS-DIR
                                   (run (default-headless-events-root))))
  ;; out-of-band 寿命境界(opt-in、env-only — CLI 語彙は oracle parse_args の
  ;; 凍結物理なので足さない。backend knob と同じ搬送経路)。
  (setv exit-when-orphaned
        (= (.get os.environ ENV-EXIT-WHEN-ORPHANED "") "1"))
  (setv command CMD-SERVE)
  (setv index 0)
  (while (< index (len args))
    (setv arg (get args index))
    (cond
      (= arg FLAG-DB)
      (do (+= index 1)
          (setv db-path (arg-at args index)))
      (= arg FLAG-SOCKET)
      (do (+= index 1)
          (setv socket-path (arg-at args index)))
      (= arg FLAG-TMUX)
      (do (+= index 1)
          (setv tmux-bin (required-arg args index FLAG-TMUX)))
      (= arg FLAG-MONITOR-INTERVAL-MS)
      (do (+= index 1)
          (setv raw (required-arg args index FLAG-MONITOR-INTERVAL-MS))
          (setv millis (int raw))
          (when (< millis 0)
            (raise (ValueError f"{FLAG-MONITOR-INTERVAL-MS} must be non-negative")))
          (setv monitor-interval-seconds (/ millis 1000)))
      (= arg FLAG-MAX-RUNNING)
      (do (+= index 1)
          (setv raw (required-arg args index FLAG-MAX-RUNNING))
          ;; 「上限なし」を言う口(2026-08-19 operator 裁定: 上限は ACP の
          ;; 都合であって sessionhost の性質ではない)。運用側は plist の
          ;; 1 語で無制限を選べる必要があり、旗の省略に無制限を割り当てると
          ;; 省略している既存の呼び手(適合 harness 等)の挙動まで変わる —
          ;; だから明示の綴りを足す形にする。
          (if (in (.lower raw) #{"none" "unlimited"})
              (setv max-running None)
              (do
                (setv max-running (int raw))
                (when (< max-running 0)
                  (raise (ValueError f"{FLAG-MAX-RUNNING} must be non-negative")))
                ;; 0 は「上限なし」の綴りではない。素通しすると全 launch が
                ;; 恒久 100% 拒否になる(`owned-count >= 0` は常に真)ので、
                ;; 起動時に loud に落とす — 無音で全拒否する daemon を作らない。
                (when (= max-running 0)
                  (raise (ValueError
                           (+ f"{FLAG-MAX-RUNNING} 0 rejects every launch "
                              "(admission is `owned >= max`); use "
                              f"`{FLAG-MAX-RUNNING} none` for unlimited")))))))
      (= arg FLAG-ALLOW-METERED-BILLING)
      (setv allow-metered-billing True)
      (= arg FLAG-RESULT-SOLICITATIONS)
      (do (+= index 1)
          (setv raw (required-arg args index FLAG-RESULT-SOLICITATIONS))
          (setv result-solicitation-limit (int raw))
          (when (< result-solicitation-limit 0)
            (raise (ValueError f"{FLAG-RESULT-SOLICITATIONS} must be non-negative"))))
      (= arg FLAG-PROMPT-STALL-SECS)
      (do (+= index 1)
          (setv raw (required-arg args index FLAG-PROMPT-STALL-SECS))
          (setv prompt-stall-seconds (int raw))
          (when (<= prompt-stall-seconds 0)
            (raise (ValueError f"{FLAG-PROMPT-STALL-SECS} must be positive"))))
      (= arg FLAG-PROMPT-UNBLOCK-ATTEMPTS)
      (do (+= index 1)
          (setv raw (required-arg args index FLAG-PROMPT-UNBLOCK-ATTEMPTS))
          (setv prompt-unblock-limit (int raw))
          (when (< prompt-unblock-limit 0)
            (raise (ValueError f"{FLAG-PROMPT-UNBLOCK-ATTEMPTS} must be non-negative"))))
      (= arg FLAG-PROMPT-JUDGE-CMD)
      (do (+= index 1)
          (setv raw (required-arg args index FLAG-PROMPT-JUDGE-CMD))
          (setv prompt-judge-cmd (run (normalize-prompt-judge-cmd raw))))
      (= arg FLAG-BACKEND)
      (do (+= index 1)
          (setv backend (required-arg args index FLAG-BACKEND)))
      (= arg FLAG-HERDR-SOCKET)
      (do (+= index 1)
          (setv herdr-socket (required-arg args index FLAG-HERDR-SOCKET)))
      (= arg CMD-SERVE)
      (setv command arg)
      True
      (raise (ValueError f"unknown argument: {arg}")))
    (+= index 1))
  (when (!= command CMD-SERVE)
    (raise (ValueError f"unsupported command: {command}")))
  (when (not-in backend #{"tmux" "herdr" HEADLESS-BACKEND-KIND})
    (raise (ValueError f"unsupported backend: {backend} (expected tmux|herdr|headless)")))
  (HostConfig
    :db-path (or db-path (default-db-path))
    :socket-path (or socket-path (default-socket-path))
    :tmux-bin tmux-bin
    :monitor-interval-seconds monitor-interval-seconds
    :max-running max-running
    :allow-metered-billing allow-metered-billing
    :result-solicitation-limit result-solicitation-limit
    :prompt-stall-seconds prompt-stall-seconds
    :prompt-unblock-limit prompt-unblock-limit
    :prompt-judge-cmd prompt-judge-cmd
    :backend backend
    :herdr-socket herdr-socket
    :headless-events-root headless-events-root
    :exit-when-orphaned exit-when-orphaned))


;; ---------------------------------------------------------------------------
;; wire 封筒(oracle RpcRequest/RpcResponse :210-235、dispatch_request :1215)
;; ---------------------------------------------------------------------------

(deff ok-response [id result]
  {:pre [(: id JsonValue) (: result JsonValue)]
   :post [(: % str)]}
  "成功封筒: result は常に載る(JSON null も値 — session.get の不在は
   `\"result\":null`)。serde の compact 出力と同じ separators。"
  (json.dumps {"id" id "ok" True "result" result} :separators #("," ":")))

(deff err-response [id message code]
  {:pre [(: id JsonValue) (: message str) (: code (| int str None))]
   :post [(: % str)]}
  "失敗封筒: error は常に載り、error_code は構造化エラーのみ
   (skip_serializing_if parity — None のとき field ごと省略)。
   koine 由来の新契約(ADR-007)は typed 文字列 code も使う。"
  (setv payload {"id" id "ok" False "error" message})
  (when (is-not code None)
    (setv (get payload "error_code") code))
  (json.dumps payload :separators #("," ":")))


;; ---------------------------------------------------------------------------
;; RPC→program 写像(plan「C3 実行設計」の host 骨格)
;;
;; 各 RPC は C1/C2 の共有 program(launch-session / monitor-cycle)か、下の
;; 小 program(capture / send / cancel / cleanup)を handler stack
;; (sqlite-session-store ∘ real-substrate ∘ codex-impl ∘ claude-code-impl)
;; で実行する。純 read(get / list / daemon.status)は program 化せず store
;; 直読。record_command(監査)は host 所有 — program は event を書く。
;; oracle は record_command → upsert → event の順だが、Hy は program 完了後に
;; command を記録する(commands 表は wire 契約外の監査ログで、conformance が
;; assert するのは events 表のみ)。
;; ---------------------------------------------------------------------------

(deff host-binary-path []
  {:pre [True]
   :post [(: % str)]}
  "result channel に配線する自分自身の実行 path(oracle agentd_binary_path
   :747-752 = current_exe。console script `doeff-sessionhost` の絶対 path)。"
  (setv candidate (get sys.argv 0))
  (if candidate (os.path.abspath candidate) "doeff-sessionhost"))

(defn run-hosted [config actor program]
  "handler stack で program を実行する(RPC 写像と monitor tick の共通経路)。
   substrate(生 IO)と store(SQLite actor)が外側、per-kind impl が内側。
   backend=herdr のときは herdr-substrate を real-substrate の内側に挿す —
   Tmux* effect は herdr が先取りし、非 mux substrate(Clock / Proc / Fs /
   Env)は素通しで real-substrate に残る(mux 差し替えの blast radius を
   多重化 effect 6 個に限定する)。"
  (setv result-command (host-binary-path))
  (setv inner ((codex-impl result-command)
               ((claude-code-impl result-command)
                program)))
  (when (= config.backend "herdr")
    (setv inner ((herdr-substrate config.herdr-socket) inner)))
  ;; backend=headless(agora-redesign #37): headless の substrate(子 process)と
  ;; kind 別の headless argv(print mode の唯一の家)を real-substrate の内側に挿す。
  ;; Tmux* effect は headless の program からは出ない(非 headless の substrate は素通し)。
  (when (= config.backend HEADLESS-BACKEND-KIND)
    (setv inner ((headless-substrate HEADLESS-REGISTRY)
                 ((headless-argv-impl) inner))))
  (run ((sqlite-session-store actor)
        ((real-substrate config.tmux-bin)
         inner))))


(deff headless-backend? [config]
  {:pre [(: config HostConfig)]
   :post [(: % bool)]}
  "この host が headless backend を話すか(RPC の program の選択の 1 点)。"
  (= config.backend HEADLESS-BACKEND-KIND))


(defk require-session-row [session-id]
  {:pre [(: session-id str)]
   :post [(: % SessionRow)]}
  "session 行の必須読み(oracle require_session :2257 — 文言 verbatim)。"
  (<- row (session-store-get session-id))
  (when (is row None)
    (raise (RuntimeError f"session is not registered: {session-id}")))
  row)

(defk capture-program [session-id lines]
  {:pre [(: session-id str) (: lines int) (> lines 0)]
   :post [(: % str)]}
  "session.capture(oracle session_capture :1943-1953): live capture +
   snippet(tail 500)/ last_observed_at の書き戻し + session_captured。"
  (<- row (require-session-row session-id))
  (<- text (tmux-capture row.pane-id lines))
  (<- now (clock-now))
  (setv updated (replace row
                         :output-snippet (tail-chars (or text " ") 500)
                         :last-observed-at (iso-format now)))
  (<- _ (session-store-upsert updated))
  (<- _ (session-store-record-event session-id "session_captured" updated))
  text)

(defk send-program [session-id message literal submit awaiting]
  {:pre [(: session-id str) (: message str) (: literal bool) (: submit bool)
         (: awaiting bool)]
   :post [(: % SessionRow)]}
  "session.send(oracle session_send :1955-1974): live pane へのキー配送 +
   session_sent event。awaiting = true(agentd の温かい手番 — ADR-DOE-AGENTS-012
   R10)は「送った本文は agent への prompt で owed」を launch の prompt 配送・催促と
   同じ awaiting latch で立てる: 正の作業証拠が出るまで見かけの turn-end を評価せず、
   期限(ADR-DOE-AGENTS-010 R3)は配送から数える。既定 false は今日どおりキー配送
   だけ(operator の郵便・救援キーは prompt とは限らない)。"
  (<- row (require-session-row session-id))
  (<- _ (tmux-send-keys row.pane-id message literal submit))
  (when awaiting
    (<- now (clock-now))
    (setv row (replace row :awaiting-response True
                           :awaiting-response-since (iso-format now)))
    (<- _ (session-store-upsert row)))
  (<- _ (session-store-record-event session-id "session_sent" row))
  row)

(defk cancel-program [session-id]
  {:pre [(: session-id str)]
   :post [(: % SessionRow)]}
  "session.cancel(oracle session_cancel :1976-2003): tmux kill(生存時)→
   stopped + cause cancelled(first-write-wins)+ session_cancelled。"
  (<- row (require-session-row session-id))
  (<- exists (tmux-has-session row.session-name))
  (when exists
    (<- _ (tmux-kill-session row.session-name)))
  (<- now (clock-now))
  (setv now-str (iso-format now))
  (setv updated (replace row :status "stopped"
                             :finished-at now-str
                             :last-observed-at now-str))
  (setv updated (cause-if-absent
                  updated (make-cause "cancelled" "session.cancel requested"
                                      now-str)))
  (<- _ (session-store-upsert updated))
  (<- _ (session-store-record-event session-id "session_cancelled" updated))
  updated)

(defk interrupt-program [session-id]
  {:pre [(: session-id str)]
   :post [(: % SessionRow)]}
  "session.interrupt(tmux / herdr): 走っている手番だけを止める(Escape の送出 — claude /
   codex の tui は Escape で今の手番を中断して idle に戻る)。session は残す: 手番の終わりは
   monitor の turn-end の連言が turn_ended_at に刻む(温かい session — ADR-DOE-AGENTS-012
   R10)。session.cancel(終端)とは別の動詞(agora-redesign #37: withdraw = 中断の合図)。"
  (<- row (require-session-row session-id))
  (<- exists (tmux-has-session row.session-name))
  (when exists
    (<- _ (tmux-send-keys row.pane-id "Escape" False False)))
  (<- now (clock-now))
  (setv updated (replace row :last-observed-at (iso-format now)))
  (<- _ (session-store-upsert updated))
  (<- _ (session-store-record-event session-id EVENT-SESSION-INTERRUPTED updated))
  updated)

(defk cleanup-program [session-id]
  {:pre [(: session-id str)]
   :post [(: % SessionRow)]}
  "session.cleanup(oracle session_cleanup :2005-2039): tmux kill(生存時)、
   非終端なら stopped + cause cancelled、finished_at は既存優先、cleaned_at
   刻印 + session_cleaned。"
  (<- row (require-session-row session-id))
  (<- exists (tmux-has-session row.session-name))
  (when exists
    (<- _ (tmux-kill-session row.session-name)))
  (<- now (clock-now))
  (setv now-str (iso-format now))
  (setv updated row)
  (when (not (is-terminal-status updated.status))
    (setv updated (replace updated :status "stopped"))
    (setv updated (cause-if-absent
                    updated
                    (make-cause "cancelled"
                                "session.cleanup stopped a non-terminal session"
                                now-str))))
  (when (is updated.finished-at None)
    (setv updated (replace updated :finished-at now-str)))
  (setv updated (replace updated :cleaned-at now-str
                                 :last-observed-at now-str))
  (<- _ (session-store-upsert updated))
  (<- _ (session-store-record-event session-id "session_cleaned" updated))
  updated)


;; ---------------------------------------------------------------------------
;; wire params(serde 既定値の再現)
;; ---------------------------------------------------------------------------

(deff params-object [params method]
  {:pre [(: params JsonValue) (: method str)]
   :post [(: % dict)]}
  "params は object 必須(serde: from_value(Null) は struct へ deserialize
   できない — 全 field optional でも同じ)。"
  (when (not (isinstance params dict))
    (raise (RuntimeError f"invalid params for {method}: expected an object")))
  params)

(deff required-str-param [params key method]
  {:pre [(: params dict) (: key str) (: method str)]
   :post [(: % str)]}
  (setv value (.get params key))
  (when (not (isinstance value str))
    (raise (RuntimeError f"invalid params for {method}: missing field `{key}`")))
  value)

(defk page-int-param [params key minimum]
  {:pre [(: params dict) (: key str) (: minimum int)]
   :post [(: % (| int None))]}
  "session.list の頁の欄(limit / offset): 無ければ None、在れば minimum 以上の整数(bool は整数と読まない)。"
  (setv value (.get params key))
  (when (and (is-not value None)
             (or (isinstance value bool) (not (isinstance value int)) (< value minimum)))
    (raise (RuntimeError
             f"invalid params for session.list: `{key}` must be an integer >= {minimum}")))
  value)

;; 段 10 lane 10o(agora-redesign #96): 添付の段を持たない器(tui = tmux / herdr の pane)の断りの理由。
;; 呼び手(agentd)はこれを条件 AttachmentIgnored の reason に写す(黙って落とさない)。
(setv ATTACHMENTS-UNSUPPORTED-REASON
      "the session substrate delivers keystrokes to a pane and cannot carry attachments")


(defk turn-attachments-of [params method]
  {:pre [(: params dict) (: method str)]
   :post [(: % tuple)]}
  "RPC の params の attachments(呼び手 = agentd)→ 型つきの添付の並び。欄が無ければ空。
   形(mime と data が文字列)の合わない項は断る — 黙って落とさない・発明しない。
   ⚠ 画像の綴り(block / input の項)はここに無い: 組むのは kind ごとの Dialogue(法 012 R21)。"
  (setv raw (.get params "attachments" []))
  (when (not (isinstance raw list))
    (raise (RuntimeError f"invalid params for {method}: attachments must be a list")))
  (setv built [])
  (for [item raw]
    ;; 項の綴りの読みは 1 点(sessionhost.attachment.attachment-of-wire)— agentd の書き手と同じ座。
    (setv one (attachment-of-wire item))
    (when (is one None)
      (raise (RuntimeError
               f"invalid params for {method}: each attachment needs a non-empty `mime` and `data`")))
    (.append built one))
  (tuple built))


(deff admit-expected-result [params method]
  {:pre [(: params dict) (: method str)]
   :post [(: % "None — 不適合は raise")]}
  "doeff#482: 契約 schema の fail-closed admission — meta-schema 違反の
   payload_schema で session を作らない(検証されない契約を存在させない)。
   session.launch と session.resume の共有面(ADR-006 改訂 R4:
   expected_result の受理形は verb 間で複製しない)。"
  (setv expected (.get params "expected_result"))
  (when (isinstance expected dict)
    (setv admission-error (schema-admission-error
                            (.get expected "payload_schema")))
    (when (is-not admission-error None)
      (raise (RuntimeError
               f"invalid params for {method}: {admission-error}"))))
  None)

(deff admit-context-file [params method]
  {:pre [(: params dict) (: method str)]
   :post [(: % "None — 不適合は raise")]}
  "context_file(law context-file-rides-the-wire — ACP steward W1b
   2026-08-20)の fail-closed admission。受理形 = {path, content}:
   path は裸のファイル名のみ(separator / '..' / 空は reject — session host は
   work_dir の外へ 1 歩も書かない)、content は必須(中身は launcher 所有の
   任意 JSON — host は schema を知らない)。"
  (setv ctx (.get params "context_file"))
  (when (is ctx None)
    (return None))
  (when (not (isinstance ctx dict))
    (raise (RuntimeError
             f"invalid params for {method}: context_file must be an object")))
  (setv path (.get ctx "path"))
  (when (not (and (isinstance path str) (> (len path) 0)))
    (raise (RuntimeError
             (+ f"invalid params for {method}: context_file.path must be a "
                "non-empty string"))))
  (when (or (in "/" path) (in "\\" path) (in path #{"." ".."}))
    (raise (RuntimeError
             (+ f"invalid params for {method}: context_file.path must be a "
                f"bare file name inside work_dir (got: {path})"))))
  (when (not-in "content" ctx)
    (raise (RuntimeError
             f"invalid params for {method}: context_file.content is required")))
  None)

(defk admit-launch-attribution [params method]
  {:pre [(: params dict) (: method str)]
   :post [(: % "None — 不適合は raise")]}
  "launch_attribution(発注者 = ACP scheduler が申告する帰属 metadata)の
   admission。器だけ検める(JSON object であること)— 中身は launcher 所有の
   opaque な id 群で、host は解釈しない(素通し verbatim 保存)。器の検査を
   落とすと綴り違いの scalar が黙って列に入り、json_extract の読み口が
   静かに空を返す。ADR-DOE-HY-004 R1(関数語彙は defk のみ)により program
   として綴るため、ふつうの関数から呼ぶ側は run で実行する — 呼びっぱなしは
   program を作るだけで検査が走らない。"
  (setv attribution (.get params "launch_attribution"))
  (when (is attribution None)
    (return None))
  (when (not (isinstance attribution dict))
    (raise (RuntimeError
             f"invalid params for {method}: launch_attribution must be an object")))
  None)

(deff admit-workspace-seed [params method]
  {:pre [(: params dict) (: method str)]
   :post [(: % "None — 不適合は raise")]}
  "workspace_seed(ACP W2 — law resolved-materialization)の fail-closed
   admission。受理形 = {repo, dir, mode, sha?, branch?, owner_marker?,
   link_siblings?, release_stale_holder?, protected_dirs?}: repo/dir は絶対
   path、mode は {detached, branch} の 2 語彙(detached は sha 必須・branch は
   branch 必須)、sha は hex 形、owner_marker は {name(裸のファイル名),
   content_text(str)}。release_stale_holder(ACP ADR 3d81bd — 同名 branch の
   古い worktree の解除を engine が許可する鍵)は bool で、真なら
   protected_dirs(engine が生きていると見なす作業場 = 絶対 path の list・
   空可)が必須 — 保護集合の無い解除は受理しない(fail-closed)。branch 形
   以外での真は契約違反として拒否(detached は branch を持たない)。"
  (setv seed (.get params "workspace_seed"))
  (when (is seed None)
    (return None))
  (when (not (isinstance seed dict))
    (raise (RuntimeError
             f"invalid params for {method}: workspace_seed must be an object")))
  (for [key ["repo" "dir"]]
    (setv value (.get seed key))
    (when (not (and (isinstance value str) (.startswith value "/")))
      (raise (RuntimeError
               (+ f"invalid params for {method}: workspace_seed.{key} must be "
                  "an absolute path")))))
  (setv mode (.get seed "mode"))
  (when (not-in mode #{"detached" "branch"})
    (raise (RuntimeError
             (+ f"invalid params for {method}: workspace_seed.mode must be "
                f"'detached' or 'branch' (got: {mode})"))))
  (setv sha (.get seed "sha"))
  (when (is-not sha None)
    (when (not (and (isinstance sha str) (re.fullmatch r"[0-9a-f]{7,64}" sha)))
      (raise (RuntimeError
               (+ f"invalid params for {method}: workspace_seed.sha must be "
                  "a hex commit id")))))
  (when (and (= mode "detached") (is sha None))
    (raise (RuntimeError
             f"invalid params for {method}: detached workspace_seed requires sha")))
  (when (= mode "branch")
    (setv branch (.get seed "branch"))
    (when (not (and (isinstance branch str) (> (len branch) 0)))
      (raise (RuntimeError
               (+ f"invalid params for {method}: branch workspace_seed requires "
                  "a non-empty branch")))))
  (setv marker (.get seed "owner_marker"))
  (when (is-not marker None)
    (when (not (isinstance marker dict))
      (raise (RuntimeError
               (+ f"invalid params for {method}: workspace_seed.owner_marker "
                  "must be an object"))))
    (setv name (.get marker "name"))
    (when (or (not (isinstance name str)) (= name "")
              (in "/" name) (in "\\" name) (in name #{"." ".."}))
      (raise (RuntimeError
               (+ f"invalid params for {method}: workspace_seed.owner_marker.name "
                  "must be a bare file name"))))
    (when (not (isinstance (.get marker "content_text") str))
      (raise (RuntimeError
               (+ f"invalid params for {method}: workspace_seed.owner_marker"
                  ".content_text must be a string")))))
  ;; ACP ADR 3d81bd: stale holder の解除の鍵と保護集合。
  (setv release (.get seed "release_stale_holder"))
  (when (is-not release None)
    (when (not (isinstance release bool))
      (raise (RuntimeError
               (+ f"invalid params for {method}: workspace_seed"
                  ".release_stale_holder must be a boolean"))))
    (when (and release (!= mode "branch"))
      (raise (RuntimeError
               (+ f"invalid params for {method}: workspace_seed"
                  ".release_stale_holder is only valid for mode 'branch' "
                  "(a detached checkout owns no branch to release)"))))
    (when (and release (not-in "protected_dirs" seed))
      (raise (RuntimeError
               (+ f"invalid params for {method}: workspace_seed"
                  ".release_stale_holder requires protected_dirs "
                  "(the launcher's live set — a release without it is "
                  "refused, fail-closed)")))))
  (when (in "protected_dirs" seed)
    (setv protected (get seed "protected_dirs"))
    (when (not (isinstance protected list))
      (raise (RuntimeError
               (+ f"invalid params for {method}: workspace_seed"
                  ".protected_dirs must be a list of absolute paths"))))
    (for [entry protected]
      (when (not (and (isinstance entry str) (.startswith entry "/")))
        (setv shown (repr entry))
        (raise (RuntimeError
                 (+ f"invalid params for {method}: workspace_seed"
                    ".protected_dirs must be a list of absolute paths "
                    f"(got: {shown})"))))))
  None)

(deff build-launch-program-params [params config]
  {:pre [(: params dict) (: config HostConfig)]
   :post [(: % dict)]}
  "wire LaunchParams(oracle :429-463)→ launch program params。serde 既定値
   (mcp_servers {} / skip_trust_setup False / lifecycle run_to_completion /
   session_env {})を再現し、host 所有値(socket_path / max_running)を注入。"
  (for [key ["session_id" "session_name" "agent_type" "work_dir"]]
    (when (not (isinstance (.get params key) str))
      (raise (RuntimeError
               f"invalid params for session.launch: missing field `{key}`"))))
  (admit-expected-result params "session.launch")
  (admit-context-file params "session.launch")
  (admit-workspace-seed params "session.launch")
  (run (admit-launch-attribution params "session.launch"))
  (carry-charter-fields
    params
    {"session_id" (get params "session_id")
   "session_name" (get params "session_name")
   "agent_type" (get params "agent_type")
   "work_dir" (get params "work_dir")
   "command" (.get params "command")
   "prompt" (.get params "prompt")
   "model" (.get params "model")
   "effort" (.get params "effort")
   "mcp_servers" (or (.get params "mcp_servers") {})
   "skip_trust_setup" (bool (.get params "skip_trust_setup" False))
   "lifecycle" (or (.get params "lifecycle") "run_to_completion")
   "binding" (.get params "binding")
   "session_env" (or (.get params "session_env") {})
   "expected_result" (.get params "expected_result")
   ;; ⚠ 自動記憶の置き場(memory_dir)と手番の頭に置き場へ書き出す冊(memory_files)は
   ;; **ここに書かない** — policy.TURN-CARRIED-KEYS の 1 点から carry-charter-fields が写す
   ;; (card ki-a40292ed30d9: 置き場だけをこの座へ手で足した便の次に、同じ座で本文が落ちた)。
   ;; law context-file-rides-the-wire(上の admit-context-file 参照): 実体化は
   ;; launch program(spawn 前・work_dir 検査後)が fs-write-text-atomic で行う
   "context_file" (.get params "context_file")
   ;; ACP W2(law resolved-materialization): 実体化は launch program の
   ;; materialize-workspace-seed(work_dir 検証の前)
   "workspace_seed" (.get params "workspace_seed")
   ;; 帰属 metadata(opaque — 上の admit-launch-attribution 参照): 保存は
   ;; launch program が SessionRow に載せ、store が verbatim 永続化する
   "launch_attribution" (.get params "launch_attribution")
   "socket_path" config.socket-path
   "max_running" config.max-running
   ;; 課金の階級の方針(host 所有値 — launch.hy の admission が読む 1 点)。
   "allow_metered_billing" config.allow-metered-billing
   ;; repl-idle 予算の env-only knob(S19 watchdog knob と同じ use-site 読み。
   ;; 未設定なら None → launch.hy が oracle 定数 120s に fallback)。
   "repl_idle_max_wait_seconds" (env-positive-i64
                                  "DOEFF_AGENTD_REPL_IDLE_MAX_WAIT_SECS")
   "backend_kind" config.backend
   ;; headless backend の実況の正本の置き場(headless.hy が events file を作る)。
   "events_root" config.headless-events-root}
  ;; 席へ運ぶ charter の欄(policy.CHARTER-CARRIED-KEYS = 旗 + 手番の荷)を素通しする。
  ;; ⚠ ここは wire の受理形 = **閉じた名簿**なので、欄を名簿に書き忘れると会話が名乗った
  ;; 値が席の導出点に 1 度も届かない(盲検の反例 A で閾値・2026-09-21 で記憶の本文を実測)。
  ;; 数え直すのは policy の集合 1 つだけ。
  ))


(defk build-resume-program-params [p config source-sid mode attachments]
  {:pre [(: p dict) (: config HostConfig) (: source-sid str) (: mode str) (: attachments tuple)]
   :post [(: % dict)]}
  "wire(session.resume / session.fork の params)→ resume program params。

   ⚠ ここは launch の受理形(build-launch-program-params)と同じ **閉じた名簿**で、
   charter → resume params(judgment.resume-params-of)→ ここ → 蘇生の名簿
   (launch.resume-session)と続く 4 枚のうちの 3 枚目。席へ運ぶ欄は数え直さず
   policy.CHARTER-CARRIED-KEYS を carry-charter-fields で写す
   (card acp:kanban-issue:ki-a40292ed30d9 — 置き場だけを 4 枚に手で足した便の次に、
    同じ 4 枚で本文が落ちた)。"
  (carry-charter-fields
    p
    {"session_id" source-sid
     "mode" mode
     "attachments" attachments
     "context_file" (.get p "context_file")
     "launch_attribution" (.get p "launch_attribution")
     "prompt" (.get p "prompt")
     "model" (.get p "model")
     "effort" (.get p "effort")
     "mcp_servers" (or (.get p "mcp_servers") {})
     "session_env" (or (.get p "session_env") {})
     "binding" (.get p "binding")
     ;; ⚠ 置き場(memory_dir)と手番の冊(memory_files)は**ここに書かない** —
     ;; policy.TURN-CARRIED-KEYS の 1 点から carry-charter-fields が写す。
     "new_session_id" (.get p "new_session_id")
     "expected_result" (.get p "expected_result")
     "expected_result_specified" (in "expected_result" p)
     "socket_path" config.socket-path
     "max_running" config.max-running
     "allow_metered_billing" config.allow-metered-billing
     "repl_idle_max_wait_seconds" (env-positive-i64
                                    "DOEFF_AGENTD_REPL_IDLE_MAX_WAIT_SECS")
     "backend_kind" config.backend
     "events_root" config.headless-events-root}))


(deff wire-snapshot [actor session-id]
  {:pre [(: actor StoreActor) (: session-id str)]
   :post [(: % dict)]}
  "行の fresh read → wire 形(program 完了後の応答用)。"
  (setv snap (.submit actor (fn [conn] (db-session-get conn session-id))))
  (when (is snap None)
    (raise (RuntimeError f"session is not registered: {session-id}")))
  (snapshot-to-wire-dict snap))


;; ---------------------------------------------------------------------------
;; koine wire 導出 field(ADR-DOE-AGENTS-007 R4/R6 — session.get / session.list /
;; session.adopt の応答にのみ載る。store には決して書かない)
;; ---------------------------------------------------------------------------

(deff effective-turn-stall-seconds []
  {:pre [True]
   :post [(: % int)]}
  "stalled 導出閾値(use-site の env 読み — 他の watchdog knob と同じ調整口)。"
  (or (env-positive-i64 "DOEFF_AGENTD_TURN_STALL_SECS")
      DEFAULT-TURN-STALL-SECONDS))

(defk substrate-present-program [session-name]
  {:pre [(: session-name str)]
   :post [(: % bool)]}
  "鏡原則(koine 条項 3)の突合 probe: 免除行の pane が今も実在するか。
   台帳は判定を保存せず、読まれるたびに現実を見る(現実が正・台帳は鏡)。"
  (<- present (tmux-has-session session-name))
  (bool present))

(defk backend-alive-program [backend-kind session-name pane-id backend-ref]
  {:pre [(: backend-kind str) (: session-name str) (: pane-id str) (: backend-ref (| dict None))]
   :post [(: % bool)]}
  "wire の backend_alive(段 10 lane 10h・agora-redesign #84): 行の backend が今この host で生きて
   いるかの観測 — headless = 子 process の pid の存在 ∧ registry の所有(HeadlessLiveness・判断は
   headless_protocol.backend_alive)、tmux / herdr = 行の pane が session の pane の集合に在る
   (TmuxSessionPaneIds — monitor の帰属検証と同じ観測)。status の語からは導かない(推測しない)。"
  (if (= backend-kind HEADLESS-BACKEND-KIND)
      (do
        (setv pid (.get (or backend-ref {}) "pid"))
        (<- liveness (headless-liveness session-name
                                        (if (and (isinstance pid int) (not (isinstance pid bool)))
                                            pid
                                            None)))
        (backend-alive liveness))
      (do
        (<- pane-ids (tmux-session-pane-ids session-name))
        (in pane-id pane-ids))))

(deff wire-with-stalled [wire]
  {:pre [(: wire dict)]
   :post [(: % dict)]}
  "stalled 導出のみを重ねる(store 読みだけで決まる level-triggered 面 —
   R4)。session.by_conversation(R7)はこれだけを使う: substrate_present /
   substrate_checked_at は意図的に載せない(law
   conversation-lookup-never-probes — 不在は field 不在で正直に)。"
  (setv now (datetime.now timezone.utc))
  (setv (get wire "stalled")
        (turn-stalled (.get wire "turn_holder") (.get wire "turn_since")
                      now (effective-turn-stall-seconds)))
  wire)

(deff augment-wire-snapshot [config actor wire]
  {:pre [(: config HostConfig) (: actor StoreActor) (: wire dict)]
   :post [(: % dict)]}
  "wire 導出 field を重ねる(読み出し面の毎回導出 — level-triggered):
   - stalled: open turn(turn_holder='agent')のまま閾値超過のみ true。
     close 済み(WAIT 待ち)は経過によらず false。signal only(R4)。
   - substrate_present / substrate_checked_at: 免除行(adopted または
     interactive)かつ非終端の行だけに載る突合表示(条項 3 — 消滅 pane を
     exited と裁定せず、乖離として見せる)。
   - backend_alive: 行の backend(headless の子 process / tmux の pane)が今この host で
     生きているかの観測(段 10 lane 10h — agentd の recover-job / next-arm-for-job が
     status の語ではなくこれで判断する)。終端の行は観測せず false(host が既に終端と
     裁定した行の backend は片付けの対象で、次の手番を受ける器ではない)。"
  (setv wire (wire-with-stalled wire))
  ;; card acp:kanban-issue:ki-567f2dd6140f §3.1e: host が載せるのは**観測した事実**ちょうど
  ;; (最後に成功した専用操作の完了時刻)。「いつまで保持するか」は ACP 側の判断
  ;; (judgment.cache-resident-retention-of)で、host はその式も予算も 1 つも持たない。
  (setv (get wire "cache_last_success_at_ms")
        (run-hosted config actor (cache-last-success-at (get wire "session_id"))))
  (setv now (datetime.now timezone.utc))
  (setv (get wire "backend_alive")
        (if (is-terminal-status (get wire "status"))
            False
            (bool (run-hosted config actor
                              (backend-alive-program
                                (get wire "backend_kind")
                                (get wire "session_name")
                                (get wire "pane_id")
                                (.get wire "backend_ref"))))))
  (setv exempt (or (bool (.get wire "adopted"))
                   (= (get wire "lifecycle") "interactive")))
  (when (and exempt (not (is-terminal-status (get wire "status"))))
    (setv (get wire "substrate_present")
          (bool (run-hosted config actor
                            (substrate-present-program
                              (get wire "session_name")))))
    (setv (get wire "substrate_checked_at") (.isoformat now)))
  wire)

(deff record-command [actor session-id command-type payload]
  {:pre [(: actor StoreActor) (: session-id str) (: command-type str)
         (: payload (| dict str))]
   :post [(: % "None")]}
  (.submit actor
           (fn [conn]
             (db-record-command conn session-id command-type "completed"
                                None payload)))
  None)


;; ---------------------------------------------------------------------------
;; result 経路(oracle session_await_result :2052-2111 /
;; build_await_response :2209-2255 / session_report_result :2136-2207)
;; ---------------------------------------------------------------------------

(deff build-await-response [snap]
  {:pre [(: snap dict)]
   :post [(: % dict)]}
  "await の成功応答: result は done かつ contract の永続 payload がある時だけ
   {\"payload\": …}(ADR 0035: 結果源はデータチャネル経由の payload のみ —
   transcript fallback は存在しない)。それ以外は result null +
   validation_error に monitor の記録した reason。"
  (setv response {"session" (snapshot-to-wire-dict snap)})
  (setv result-value None)
  (setv validation-error (get snap "last_validation_error"))
  (when (and (= (get snap "status") "done")
             (is-not (get snap "expected_result") None))
    (setv parse-ok False)
    (setv parsed None)
    (setv raw (get snap "result_payload"))
    (when (is-not raw None)
      (try
        (setv parsed (json.loads raw))
        (setv parse-ok True)
        (except [Exception])))
    (if parse-ok
        (do
          (setv result-value {"payload" parsed})
          (setv validation-error None))
        (when (is validation-error None)
          (setv validation-error
                "session reached 'done' without a reported result payload"))))
  (setv (get response "result") result-value)
  (when (is-not validation-error None)
    (setv (get response "validation_error") validation-error))
  response)


(deff report-result-op [conn session-id payload]
  {:pre [(: conn sqlite3.Connection) (: session-id str) (: payload JsonValue)]
   :post [(: % dict)]}
  "session.report_result の実体(actor 内で 1 op として実行 = 原子的)。
   終端 + payload 有り = idempotent already_reported / 死亡裁定クラス
   (exited)+ 無し = 遅延受理(ADR-DOE-AGENTS-009 R4: result 到着は死亡推定
   の反証 — status=done・cause / validation error クリア・finished_at 前進 +
   session_late_result_accepted event。2026-07-27 wedge で生存 agent の結果が
   -32003 で失われた実 incident の救済経路)/ その他の終端(judged failure・
   操作者裁定)+ 無し = -32003 / contract 無し = error / schema 不適合 =
   -32002 + session_result_rejected event、payload は永続しない・再検証しない
   (ADR 0035 R4)/ 非終端の適合 = first-write-wins guarded UPDATE(status は
   書かない — done 化は monitor)。"
  (setv snap (db-session-get conn session-id))
  (when (is snap None)
    (raise (RuntimeError f"session is not registered: {session-id}")))
  (setv status (get snap "status"))
  (setv late-accept False)
  (when (is-terminal-status status)
    (when (is-not (get snap "result_payload") None)
      (return {"accepted" True "already_reported" True}))
    (if (= status "exited")
        ;; 死亡裁定クラスのみ受理へ fall-through(contract / schema 検査は
        ;; 通常経路と共有 — 受理の門を開くだけで検証は緩めない)
        (setv late-accept True)
        (raise (RpcHostError RPC-ERR-ALREADY-TERMINAL
                 (+ f"session '{session-id}' already reached terminal status "
                    f"'{status}' without a result")))))
  (setv spec (get snap "expected_result"))
  (when (or (is spec None) (not (isinstance spec dict)))
    (raise (RuntimeError
             (+ f"session '{session-id}' has no result contract; "
                "report_result is not applicable"))))
  (setv reason (validate-against-schema payload (.get spec "payload_schema")
                                        "payload"))
  (when (is-not reason None)
    (db-record-event conn session-id "session_result_rejected"
                     {"session_id" session-id "reason" reason})
    (raise (RpcHostError RPC-ERR-RESULT-REJECTED
             f"reported result does not satisfy its schema: {reason}")))
  ;; byte-faithful 永続化 = serde to_string parity: compact・非 ASCII 素通し・
  ;; **key はソート**(serde_json::Value の Map は BTreeMap — oracle は挿入順
  ;; でなく辞書順で書く。S17 の raw column 突き合わせが検出した実測物理)。
  (setv payload-json (json.dumps payload :sort-keys True
                                 :separators #("," ":")
                                 :ensure-ascii False))
  (when late-accept
    (setv accepted-at (.isoformat (datetime.now timezone.utc)))
    (setv affected (db-late-result-accept conn session-id payload-json accepted-at))
    (when (= affected 0)
      (setv fresh (db-session-get conn session-id))
      (when (and (is-not fresh None) (is-not (get fresh "result_payload") None))
        (return {"accepted" True "already_reported" True}))
      (raise (RpcHostError RPC-ERR-ALREADY-TERMINAL
               (+ f"session '{session-id}' left the death-verdict class before "
                  "the late result could be recorded"))))
    (db-record-event conn session-id "session_late_result_accepted"
                     {"session_id" session-id
                      "previous_status" status
                      "previous_terminal_cause" (get snap "terminal_cause")})
    (return {"accepted" True "late_result" True}))
  (setv affected (db-report-result-guarded-update conn session-id payload-json))
  (when (= affected 0)
    (setv fresh (db-session-get conn session-id))
    (when (and (is-not fresh None) (is-not (get fresh "result_payload") None))
      (return {"accepted" True "already_reported" True}))
    (raise (RpcHostError RPC-ERR-ALREADY-TERMINAL
             (+ f"session '{session-id}' finished before the result could be "
                "recorded"))))
  (db-record-event conn session-id "session_result_reported"
                   {"session_id" session-id})
  {"accepted" True})


(defk late-result-cleanup-program [session-id]
  {:pre [(: session-id str)]
   :post [(: % bool)]}
  "遅延 result 受理後の substrate cleanup(ADR-DOE-AGENTS-009 R4)。受理は
   terminal 行への上書きなので monitor はもう触らない — finalize の RTC 終端
   cleanup と同義を RPC 層で行う(IO は actor op の外 — store actor を tmux で
   塞がない)。false-lost 救済の行は pane が実際に生きていることがある(それが
   まさに誤裁定だった)ため、生存確認してから kill する。戻り値 = kill 実行。"
  (<- row (require-session-row session-id))
  (when (not (is-run-to-completion row.lifecycle))
    (return False))
  (when (= row.backend-kind HEADLESS-BACKEND-KIND)
    (<- killed (headless-kill row.session-name))
    (return (bool killed)))
  (<- exists (tmux-has-session row.session-name))
  (when exists
    (<- _ (tmux-kill-session row.session-name)))
  (bool exists))


(deff await-result-blocking [actor session-id timeout-seconds]
  {:pre [(: actor StoreActor) (: session-id str)
         (: timeout-seconds (| int float))]
   :post [(: % dict)]}
  "終端 status まで 500ms poll で block(oracle
   session_await_result_with_interval — deadline は connection thread の
   stack にのみ生きる transient。daemon 再起動で in-flight await は
   socket 切断として落ちる = oracle と同じ)。"
  (setv started (time.monotonic))
  (setv snap (.submit actor (fn [conn] (db-session-get conn session-id))))
  (when (is snap None)
    (raise (RpcHostError RPC-ERR-NO-SUCH-SESSION
             f"no session with id '{session-id}'")))
  (while True
    (when (in (get snap "status") AWAIT-TERMINAL-STATUSES)
      (return (build-await-response snap)))
    (when (>= (- (time.monotonic) started) timeout-seconds)
      (setv secs (int timeout-seconds))
      (raise (RpcHostError RPC-ERR-AWAIT-TIMEOUT
               (+ f"session.await_result timed out after {secs}s "
                  f"for session '{session-id}'"))))
    (time.sleep AWAIT-POLL-INTERVAL-SECONDS)
    (setv snap (.submit actor (fn [conn] (db-session-get conn session-id))))
    (when (is snap None)
      (raise (RpcHostError RPC-ERR-NO-SUCH-SESSION
               f"no session with id '{session-id}'")))))


(deff dispatch-method [method params config actor]
  {:pre [(: method str) (: params JsonValue) (: config HostConfig)
         (: actor StoreActor)]
   :post [(: % JsonValue)]}
  "dispatch_request_result(:1247-1301)。session.await_result /
   session.report_result は C3-impl-4 — それまで not-implemented で loud。
   契約外 method は oracle と同文言の unknown method。"
  (when (= method "daemon.status")
    (return {"state" "running"
             "pid" (os.getpid)
             "db_path" config.db-path
             "socket_path" config.socket-path
             "max_running" config.max-running
             ;; host の方針は読めること(配備が従量課金を許しているかを argv を
             ;; 覗かずに確かめる読み口。additive field)。
             "allow_metered_billing" config.allow-metered-billing
             "active_sessions" (.submit actor db-count-active)
             "lease" (.submit actor db-read-lease)
             ;; ADR-007 §4: turn 打刻 counters(additive・in-memory)。
             "counters" (dict TURN-COUNTERS)}))

  ;; DOE-004 R5(縮小版、2026-07-08): kind 語彙の広告。純粋(store 非依存)
  ;; — control plane の reconciler が登録済み binding と定期照合する読み口。
  (when (= method "kinds.list")
    (return {"kinds" (binding-kind-advertisement)}))

  ;; ADR-DOE-AGENTS-012 R61(card acp:kanban-issue:ki-f250d67a7157): 種類ごとの実行ファイルが、この host が
  ;; 子 process を起こす実効 env(headless-spawn-env — 呼び手の重ねる env を機体の継承の名簿の上に重ねた 1 点)で
  ;; 見つかるか。params.env = 呼び手が重ねる env(無ければ {})。問われるたびに探す(host は結果を持たない)・
  ;; 起動の試しはしない。kinds.list(binding の種類の広告)には混ぜない。
  (when (= method "drivers.list")
    (setv overlay (if (is params None) {} (.get (params-object params "drivers.list") "env" {})))
    (when (not (and (isinstance overlay dict)
                    (all (gfor [key value] (.items overlay) (and (isinstance key str) (isinstance value str))))))
      (raise (RuntimeError "invalid params for drivers.list: `env` must be an object of strings")))
    (return {"drivers" (driver-listing (headless-spawn-env overlay))}))

  (when (= method "session.launch")
    (setv wire-params (params-object params "session.launch"))
    (setv program-params (build-launch-program-params wire-params config))
    ;; 段 10 lane 10o(agora-redesign #96): 起こす腕は郵便を 1 手番目に畳むので、その郵便の添付も
    ;; この launch に載る。型つきに解いて器へ渡す(綴りは器の Dialogue)— 添付の段を持たない器
    ;; (tui)には渡さず、断りを答えに名乗る。
    (setv launch-attachments (run (turn-attachments-of wire-params "session.launch")))
    (setv launch-ignored (if (and launch-attachments (not (headless-backend? config)))
                             ATTACHMENTS-UNSUPPORTED-REASON
                             ""))
    (setv (get program-params "attachments")
          (if launch-ignored #() launch-attachments))
    ;; DOE-003 R3 staged enforcement の warning(oracle :1721-1730 verbatim —
    ;; 運用ログは host の外部性。S11b が daemon log でこの文言を assert する)。
    ;; R7 後の運搬手段は typed binding — overlay session_env は admission で
    ;; auth キーを拒否するため、明示の有無は binding の有無そのもの。
    (when (and (= (get program-params "agent_type") "claude")
               (is (.get program-params "binding") None))
      (setv sid-for-warning (get program-params "session_id"))
      (print (+ "doeff-agentd WARNING: claude session "
                f"{sid-for-warning} launched without an explicit "
                "CLAUDE_CONFIG_DIR auth profile (ADR-DOE-AGENTS-003 R3: "
                "enforcement follows once callers migrate)")
             :file sys.stderr)
      (.flush sys.stderr))
    (setv row (run-hosted config actor
                          (if (headless-backend? config)
                              (headless-launch-session program-params)
                              (launch-session program-params))))
    (setv sid row.session-id)
    (setv wire (wire-snapshot actor sid))
    (record-command actor sid "session.launch" wire)
    (when launch-ignored
      (setv wire (dict wire #** {"attachmentsIgnored" launch-ignored})))
    (return wire))

  ;; ADR-DOE-AGENTS-006 R4: 会話の新 incarnation。program(resume-session)が
  ;; admission(実在 / capability / identity-unknown / one-live-incarnation /
  ;; binding / new_session_id 重複)と系譜・世代・cross-binding transplant
  ;; (R7)を所有し、宿しは launch-session を再利用する。typed reject
  ;; (ResumeRejected)は wire の error_code へ写す(R9 — 機械消費者は
  ;; message substring でなく code を照合する)。
  (when (in method #("session.resume" "session.fork"))
    (setv mode (if (= method "session.resume") "resume" "fork"))
    (setv p (params-object params method))
    (setv source-sid (required-str-param p "session_id" method))
    ;; cross-binding 拡張 3 param は resume 専用(ADR-006 改訂 R4)— fork への
    ;; 指定は fail-closed(黙殺は誤配線を隠す)。
    (when (= method "session.fork")
      ;; memory_dir が resume 専用なのは、fork が**新しい会話**だから(ADR-DOE-AGENTS-006 R11):
      ;; 置き場は会話 id から導くが、fork の新 identity は CLI が鋳造するまで判らない。親の値を
      ;; 通せば新しい会話に親の記憶が黙って付く — 直している誤帰属そのもの。黙殺せず断る。
      ;; memory_files(置き場へ書き出す冊そのもの)も同じ理由で resume 専用 — 親の記憶を
      ;; 新しい会話の置き場へ書き出したら、誤帰属は置き場の名ではなく**中身**で起きる。
      ;; ⚠ 記憶の族は**名簿から**採る(policy.TURN-CARRIED-KEYS の 1 点・card ki-6b5c4b270ca0):
      ;; 手で並べると、族に欄を足した便がこの断りを落とし、fork が親の値をそのまま持って行く
      ;; (memory_retired_files ならば、親の退役した名で**新しい会話の置き場の file を消す**)。
      (for [banned (+ #("binding" "new_session_id" "expected_result"
                        "context_file" "launch_attribution")
                      TURN-CARRIED-KEYS)]
        (when (in banned p)
          (raise (RuntimeError
                   (+ f"invalid params for session.fork: `{banned}` is "
                      "resume-only (ADR-DOE-AGENTS-006 R4)"))))))
    (when (and (in "new_session_id" p)
               (not (isinstance (.get p "new_session_id") str)))
      (raise (RuntimeError
               "invalid params for session.resume: `new_session_id` must be a string")))
    ;; expected_result の schema admission は launch と共有(R4 — 明示 null は
    ;; dict でないため素通り = 契約なしの指定)。
    (when (= method "session.resume")
      (admit-expected-result p "session.resume")
      ;; law context-file-rides-the-wire の resume 面 — 形の admission は
      ;; launch と共有(裸ファイル名 + content 必須)。fork は上の banned で
      ;; fail-closed(新会話に前会話の invocation 簿記を持ち込まない)。
      (admit-context-file p "session.resume")
      ;; 帰属 metadata の resume 面(one law, both faces)— 形の admission は
      ;; launch と共有。fork は banned(帰属も invocation 簿記)。
      (run (admit-launch-attribution p "session.resume")))
    ;; 段 10 lane 10o(実弾 2026-09-15 09:5x): 起こす腕は launch だけではない — resume も 1 手番目に
    ;; 郵便を畳むので、添付は同じ 1 点で型つきに解いて運ぶ(添付の段を持たない器は断りを名乗る)。
    (setv resume-attachments (run (turn-attachments-of p method)))
    (setv resume-ignored (if (and resume-attachments (not (headless-backend? config)))
                             ATTACHMENTS-UNSUPPORTED-REASON
                             ""))
    (setv program-params
          (run (build-resume-program-params
                 p config source-sid mode
                 (if resume-ignored #() resume-attachments))))
    (setv row None)
    (try
      (setv row (run-hosted config actor (resume-session program-params)))
      (except [e ResumeRejected]
        ;; R9: typed reject を構造化封筒へ(message は後方互換で不変)。
        (raise (RpcHostError e.code (str e)))))
    (setv new-sid row.session-id)
    (setv wire (wire-snapshot actor new-sid))
    (record-command actor new-sid method wire)
    (when resume-ignored
      (setv wire (dict wire #** {"attachmentsIgnored" resume-ignored})))
    (return wire))

  (when (= method "session.get")
    (setv p (params-object params "session.get"))
    (setv sid (required-str-param p "session_id" "session.get"))
    (setv snap (.submit actor (fn [conn] (db-session-get conn sid))))
    (return (if (is snap None)
                None
                (augment-wire-snapshot config actor (snapshot-to-wire-dict snap)))))

  ;; 段 12 lane 12b(agora-redesign #207 根 1): 出来事の journal(agent_session_events)の long-poll。
  ;; after = 呼び手が最後に知った先端(int ≥ 0)・wait_seconds = 待ちの上限(0 = 今の先端を返す・
  ;; 上限は WAIT-EVENTS-MAX-SECONDS に畳む)。答え = {"seq": 先端}。進んだ出来事の中身は運ばない —
  ;; 呼び手(agentd)は自分の眺め(session.get)を読み直して判じる(level-triggered の観測の合図)。
  ;; 待ちは actor の条件変数(接続ごとの thread が眠る — actor thread も他の RPC も塞がない)。
  (when (= method "session.wait_events")
    (setv p (params-object params "session.wait_events"))
    (setv after (.get p "after" 0))
    (when (or (isinstance after bool) (not (isinstance after int)) (< after 0))
      (raise (RuntimeError
               f"invalid params for session.wait_events: `after` must be a non-negative integer (got: {after !r})")))
    (setv wait-raw (.get p "wait_seconds" 0))
    (when (or (isinstance wait-raw bool) (not (isinstance wait-raw #(int float))) (< wait-raw 0))
      (raise (RuntimeError
               f"invalid params for session.wait_events: `wait_seconds` must be a non-negative number (got: {wait-raw !r})")))
    (setv wait (min (float wait-raw) WAIT-EVENTS-MAX-SECONDS))
    (return {"seq" (.wait-journal actor after wait)}))

  ;; 波 1-S1(ADR-007 R7): 会話 ID → 行の probe なし行引き口。応答 = wire
  ;; snapshot + stalled 導出のみ(substrate_present は意図的に不在 — law
  ;; conversation-lookup-never-probes。読み手は自分の substrate 観測と合成
  ;; する)。解決則 = 非終端の最新行 → 最新の terminal 行 → null。
  (when (= method "session.by_conversation")
    (setv p (params-object params "session.by_conversation"))
    (setv cid (required-str-param p "conversation_id" "session.by_conversation"))
    (when (not (.strip cid))
      (raise (RuntimeError
               (+ "invalid params for session.by_conversation: "
                  "conversation_id must be a non-empty string"))))
    (setv snap (.submit actor (fn [conn] (db-session-by-conversation conn cid))))
    (return (if (is snap None)
                None
                (wire-with-stalled (snapshot-to-wire-dict snap)))))

  (when (= method "session.list")
    (setv p (params-object params "session.list"))
    (setv filters {"status" (.get p "status")
                   "agent_type" (.get p "agent_type")
                   "backend_kind" (.get p "backend_kind")
                   "lifecycle" (.get p "lifecycle")
                   ;; ADR-007 §8: adopted filter(bool)— 対話席一覧の主 filter。
                   "adopted" (.get p "adopted")
                   ;; 頁(2026-09-23): 新しい順の一致行から offset 件飛ばして limit 件(無し = 全件)。
                   "limit" (run (page-int-param p "limit" 1))
                   "offset" (or (run (page-int-param p "offset" 0)) 0)})
    (setv snaps (.submit actor (fn [conn] (db-session-list conn filters))))
    (return (lfor s snaps
                  (augment-wire-snapshot config actor
                                         (snapshot-to-wire-dict s)))))

  ;; --- koine session surface v0(ADR-DOE-AGENTS-007)-------------------------

  (when (= method "session.adopt")
    (setv p (params-object params "session.adopt"))
    (setv session-name (required-str-param p "session_name" "session.adopt"))
    (setv agent-kind (required-str-param p "agent_kind" "session.adopt"))
    (setv substrate (.get p "substrate"))
    (when (not (isinstance substrate dict))
      (raise (RuntimeError
               "invalid params for session.adopt: missing field `substrate`")))
    (setv sub-kind (.get substrate "kind"))
    (setv sub-ref (.get substrate "ref"))
    (when (or (not (isinstance sub-kind str)) (not (.strip sub-kind)))
      (raise (RuntimeError
               "invalid params for session.adopt: substrate.kind must be a non-empty string")))
    (when (or (not (isinstance sub-ref str)) (not (.strip sub-ref)))
      (raise (RuntimeError
               "invalid params for session.adopt: substrate.ref must be a non-empty string")))
    ;; substrate binding の照合: この host が話す substrate だけを登記できる
    ;; (koine binding 宣言 — kind 不一致は typed reject、黙って読み替えない)。
    (when (!= sub-kind config.backend)
      (raise (RuntimeError
               (+ f"session.adopt substrate kind {sub-kind !r} does not match "
                  f"this host's backend {config.backend !r}"))))
    ;; 波 1-S1(ADR-007 R2): 任意の conversation_id(非空文字列)。
    (setv conversation-id (.get p "conversation_id"))
    (when (and (is-not conversation-id None)
               (or (not (isinstance conversation-id str))
                   (not (.strip conversation-id))))
      (raise (RuntimeError
               (+ "invalid params for session.adopt: conversation_id must be "
                  "a non-empty string"))))
    (setv program-params
          {"session_name" session-name
           "substrate_ref" sub-ref
           "agent_kind" agent-kind
           "lifecycle" (or (.get p "lifecycle") "interactive")
           "backend_kind" sub-kind
           "name" (.get p "name")
           "work_dir" (.get p "work_dir")
           "conversation_id" conversation-id})
    (setv row None)
    (try
      (setv row (run-hosted config actor (adopt-program program-params)))
      (except [e AdoptTargetNotFound]
        ;; 順序義務の失敗側: 行は作られていない(S23)。
        (raise (RpcHostError RPC-ERR-ADOPT-TARGET-NOT-FOUND (str e)))))
    (setv wire (wire-snapshot actor row.session-id))
    (record-command actor row.session-id "session.adopt" wire)
    (return (augment-wire-snapshot config actor wire)))

  (when (in method #("session.turn_open" "session.turn_close"))
    (setv p (params-object params method))
    (setv descriptor (.get p "descriptor"))
    (when (not (isinstance descriptor dict))
      (raise (RuntimeError
               f"invalid params for {method}: missing field `descriptor`")))
    (setv pane-id (.get descriptor "pane_id"))
    (setv agent-name (.get descriptor "agent_name"))
    (setv conversation-id (.get descriptor "conversation_id"))
    (setv pane-id (if (isinstance pane-id str) pane-id None))
    (setv agent-name (if (isinstance agent-name str) agent-name None))
    (setv conversation-id (if (isinstance conversation-id str)
                              conversation-id
                              None))
    (when (and (is pane-id None) (is agent-name None)
               (is conversation-id None))
      (raise (RuntimeError
               (+ f"invalid params for {method}: descriptor requires "
                  "pane_id, conversation_id or agent_name"))))
    ;; holder が open/closed を兼ねる契約(発注元確定 2026-07-21):
    ;; open = 'agent'(自走中・wait は NULL に戻る)/ close = wait.who
    ;; (無ければ 'work')。wait は opaque のまま保存(再 parse しない)。
    (setv wait (if (= method "session.turn_close") (.get p "wait") None))
    (setv wait-payload (if (isinstance wait dict) wait None))
    (setv holder (if (= method "session.turn_open")
                     TURN-HOLDER-AGENT
                     (turn-close-holder wait-payload)))
    ;; 行引き + UPDATE の 1 actor op のみ — substrate 不接触(hung を作らない
    ;; 受け側義務。≤200ms fire-and-forget の hook hot path が相手)。
    (setv sid (.submit actor
                       (fn [conn]
                         (db-turn-stamp conn pane-id agent-name
                                        conversation-id holder
                                        wait-payload))))
    (if (is sid None)
        (setv (get TURN-COUNTERS "turn_stamp_unadopted")
              (+ (get TURN-COUNTERS "turn_stamp_unadopted") 1))
        (setv (get TURN-COUNTERS "turn_stamp_resolved")
              (+ (get TURN-COUNTERS "turn_stamp_resolved") 1)))
    (return {"adopted" (is-not sid None) "session_id" sid}))

  (when (= method "session.capture")
    (setv p (params-object params "session.capture"))
    (setv sid (required-str-param p "session_id" "session.capture"))
    (setv lines (int (.get p "lines" 100)))
    (setv text (run-hosted config actor
                           (if (headless-backend? config)
                               (headless-capture-program sid lines)
                               (capture-program sid lines))))
    (return {"text" text}))

  (when (in method #("session.cache-ping" "session.cache-ping-status"))
    (setv p (params-object params method))
    (setv sid (required-str-param p "session_id" method))
    (setv operation-id (required-str-param p "operation_id" method))
    (when (not (headless-backend? config))
      (raise (RuntimeError "cache ping requires the headless backend")))
    (when (= method "session.cache-ping-status")
      (setv receipt (run-hosted config actor (HostCacheRead operation-id)))
      (when (is receipt None) (return None))
      (when (!= receipt.session-id sid)
        (raise (RuntimeError "cache operation session mismatch")))
      (return (asdict (run-hosted config actor (cache-host-probe receipt)))))
    (when (- (set p) #{"session_id" "operation_id" "expires_at" "session_env"})
      (raise (RuntimeError "cache ping accepts no prompt, priority or turn parameters")))
    (setv expires-at (.get p "expires_at"))
    (when (not (and (= (type expires-at) int) (>= expires-at 0)))
      (raise (RuntimeError "cache ping expires_at must be a nonnegative integer")))
    (setv session-env (.get p "session_env" {}))
    (when (not (isinstance session-env dict))
      (raise (RuntimeError "cache ping session_env must be an object")))
    (setv env-error (session-env-admission-error session-env method))
    (when env-error (raise (RuntimeError env-error)))
    (return (asdict (run-hosted config actor
      (cache-host-ping sid operation-id expires-at session-env)))))

  (when (= method "session.send")
    (setv p (params-object params "session.send"))
    (setv sid (required-str-param p "session_id" "session.send"))
    (setv message (required-str-param p "message" "session.send"))
    (setv enter (bool (.get p "enter" True)))
    (setv literal (bool (.get p "literal" True)))
    (setv awaiting (bool (.get p "awaiting" False)))
    ;; 段 8 lane 4x(agora-redesign #56): mode = turn(既定・次の手番の本文)| interrupt(割り込みの
    ;; 本文 — 走っている手番へ即座に。headless は器へ注入し、走っている手番が無ければ型付きに
    ;; 断る。tmux の tui は同じキー配送 — Claude Code / codex の tui は作業中の入力を自分で
    ;; 走っている手番に読ませる)。語彙の外は断る(黙って turn にしない)。
    (setv mode (.get p "mode" SEND-MODE-TURN))
    (when (not-in mode SEND-MODES)
      (raise (RuntimeError (+ "invalid params for session.send: mode must be one of "
                            (.join " / " (sorted SEND-MODES)) f" (got: {mode !r})"))))
    ;; 段 10 lane 10n: ref = 注入の行の名(headless の claude は user の行の uuid — CLI の
    ;; command_lifecycle がこの綴りで運命を名乗る)。無ければ器が鋳造。
    (setv ref (.get p "ref" ""))
    (when (not (isinstance ref str))
      (raise (RuntimeError f"invalid params for session.send: ref must be a string (got: {ref !r})")))
    ;; 段 10 lane 10d 便 2 追補 2(実弾 #92): session_env = **この手番の** env(預かり所の貸与の札は
    ;; 手番ごとに回る)。降りた process を起こし直す時、行に残った誕生の env ではなくこの値を重ねる。
    ;; admission は launch と同じ 1 点(binding 所有キー・従量課金 credential は送りの口でも受けない)。
    (setv session-env (or (.get p "session_env") {}))
    (when (not (isinstance session-env dict))
      (raise (RuntimeError
               f"invalid params for session.send: session_env must be an object (got: {session-env !r})")))
    (setv send-env-error (session-env-admission-error session-env "session.send"))
    (when (is-not send-env-error None)
      (raise (RuntimeError send-env-error)))
    ;; 手番ごとの env を運べない組み合わせは黙って落とさず断る(落とすと誕生の札で手番が走る =
    ;; まさに #92 の形): tmux の器には手番ごとの env が無く、mode = interrupt は走っている手番へ
    ;; 本文を注ぐだけで process を起こさない。
    (when session-env
      (when (not (headless-backend? config))
        (raise (RuntimeError
                 (+ "session.send: session_env is only carried by the headless backend "
                    f"(backend: {config.backend}). The tmux container has no per-turn env."))))
      (when (= mode SEND-MODE-INTERRUPT)
        (raise (RuntimeError
                 (+ "session.send: session_env belongs to mode = " SEND-MODE-TURN
                    " — an interrupt is poured into the turn already in flight and starts no process.")))))
    ;; card acp:kanban-issue:ki-a40292ed30d9(4 つ目の腕): turn_charter = **この手番の荷**
    ;; (policy.TURN-CARRIED-KEYS = 記憶の置き場と冊)。降りた process を起こし直す腕は行の
    ;; launch_overlay しか読まないので、行に残さない欄はこの口で来る。session_env とは別の口:
    ;; あちらは資格(秘密)の袋で、こちらは file に落ちる値 — 同じ袋に入れると、冊の本文が
    ;; 「log にも argv にも出さない」規律の側へ紛れる。
    (setv turn-charter (or (.get p "turn_charter") {}))
    (when (not (isinstance turn-charter dict))
      (raise (RuntimeError
               f"invalid params for session.send: turn_charter must be an object (got: {turn-charter !r})")))
    ;; 段 10 lane 10o(agora-redesign #96・依頼者の追補): 郵便の添付は**型つき**で受け取り、そのまま
    ;; 器へ渡す(CLI の綴りは kind ごとの Dialogue が組む — この module に画像の綴りは無い)。
    ;; 添付の段を持たない器(tui = tmux / herdr)は断りを名乗る — 本文は届く・添付だけ落ちる。
    (setv attachments (run (turn-attachments-of p "session.send")))
    (setv ignored (if (and attachments (not (headless-backend? config)))
                      ATTACHMENTS-UNSUPPORTED-REASON
                      ""))
    (when ignored
      (setv attachments #()))
    (when (headless-backend? config)
      (try
        (run-hosted config actor (cache-host-guard-normal-send sid))
        (except [error CacheMaintenanceActiveError]
          (raise (RpcHostError CACHE-MAINTENANCE-ACTIVE (str error))))))
    (run-hosted config actor
                (cond
                  (and (headless-backend? config) (= mode SEND-MODE-INTERRUPT))
                  (headless-inject-program sid message ref attachments)
                  (headless-backend? config)
                  (headless-send-program sid message awaiting session-env turn-charter attachments)
                  True
                  (send-program sid message literal enter awaiting)))
    (record-command actor sid "session.send" message)
    (if ignored
        (return {"sent" True "attachmentsIgnored" ignored})
        (return {"sent" True})))

  ;; agora-redesign #37: 走っている手番だけを止め、session は残す(withdraw = 中断の
  ;; 合図)。cancel(終端)とは別の動詞 — 1 つの動詞に「終端」と「残す」を同居させない。
  (when (= method "session.interrupt")
    (setv p (params-object params "session.interrupt"))
    (setv sid (required-str-param p "session_id" "session.interrupt"))
    (run-hosted config actor
                (if (headless-backend? config)
                    (headless-interrupt-program sid)
                    (interrupt-program sid)))
    (setv wire (wire-snapshot actor sid))
    (record-command actor sid "session.interrupt" wire)
    (return wire))

  ;; 段 10 lane 10n(agora-redesign #93): 注入した本文を model が期限まで読まなかった時の停止の合図
  ;; (headless の claude = control_request interrupt・codex は出す物が無い)。tui の backend には
  ;; 注入の段が無い(キー配送は作業中の入力を model が自分で読む)ので断る。
  (when (= method "session.escalate")
    (setv p (params-object params "session.escalate"))
    (setv sid (required-str-param p "session_id" "session.escalate"))
    (when (not (headless-backend? config))
      (raise (RuntimeError "session.escalate is only supported by the headless backend")))
    (run-hosted config actor (headless-escalate-program sid))
    (setv wire (wire-snapshot actor sid))
    (record-command actor sid "session.escalate" wire)
    (return wire))

  (when (= method "session.cancel")
    (setv p (params-object params "session.cancel"))
    (setv sid (required-str-param p "session_id" "session.cancel"))
    (run-hosted config actor
                (if (headless-backend? config)
                    (headless-cancel-program sid)
                    (cancel-program sid)))
    (setv wire (wire-snapshot actor sid))
    (record-command actor sid "session.cancel" wire)
    (return wire))

  (when (= method "session.cleanup")
    (setv p (params-object params "session.cleanup"))
    (setv sid (required-str-param p "session_id" "session.cleanup"))
    (run-hosted config actor
                (if (headless-backend? config)
                    (headless-cleanup-program sid)
                    (cleanup-program sid)))
    (setv wire (wire-snapshot actor sid))
    (record-command actor sid "session.cleanup" wire)
    (return wire))

  (when (= method "session.await_result")
    (setv p (params-object params "session.await_result"))
    (setv sid (required-str-param p "session_id" "session.await_result"))
    (setv timeout-raw (.get p "timeout_seconds"))
    (setv timeout-seconds
          (if (is timeout-raw None)
              DEFAULT-AWAIT-TIMEOUT-SECONDS
              (float timeout-raw)))
    (setv timeout-seconds (max MIN-AWAIT-TIMEOUT-SECONDS
                               (min MAX-AWAIT-TIMEOUT-SECONDS timeout-seconds)))
    (return (await-result-blocking actor sid timeout-seconds)))

  (when (= method "session.report_result")
    (setv p (params-object params "session.report_result"))
    (setv sid (required-str-param p "session_id" "session.report_result"))
    (when (not-in "payload" p)
      (raise (RuntimeError
               "invalid params for session.report_result: missing field `payload`")))
    (setv payload (get p "payload"))
    (setv response (.submit actor (fn [conn] (report-result-op conn sid payload))))
    ;; ADR-DOE-AGENTS-009 R4: 遅延受理した行は terminal — monitor は監視外の
    ;; ため、finalize と同義の RTC substrate cleanup をここで行い記帳する。
    (when (and (isinstance response dict) (.get response "late_result"))
      (setv killed (run-hosted config actor (late-result-cleanup-program sid)))
      (when killed
        (setv cleaned-at (.isoformat (datetime.now timezone.utc)))
        (.submit actor (fn [conn] (db-mark-cleaned conn sid cleaned-at)))))
    (return response))

  (raise (RuntimeError f"unknown method: {method}")))

(deff dispatch-line [line config actor]
  {:pre [(: line str) (: config HostConfig) (: actor StoreActor)]
   :post [(: % str)]}
  "1 リクエスト行 → 1 レスポンス行。parse 失敗は id=null の invalid request
   (:1197-1205)。RpcRequest は id + method 必須、params default null。"
  (try
    (setv request (json.loads line))
    (except [e Exception]
      (return (err-response None f"invalid request: {e}" None))))
  (when (not (isinstance request dict))
    (return (err-response None "invalid request: expected an object" None)))
  (when (not-in "id" request)
    (return (err-response None "invalid request: missing field `id`" None)))
  (setv id (get request "id"))
  (setv method (.get request "method"))
  (when (not (isinstance method str))
    (return (err-response None "invalid request: missing field `method`" None)))
  (setv params (.get request "params"))
  (try
    ;; 一つのsessionの通常sendと専用pingを同時に開始させない。API完了までの短い区間だけ。
    ;; await_result等の長い待ちはこのlockを取得しない。
    (if (and (in method #("session.send" "session.cache-ping" "session.cache-ping-status"
                          "session.cancel" "session.cleanup" "session.interrupt" "session.escalate"))
             (isinstance params dict) (isinstance (.get params "session_id") str))
      (with [(session-mutation-lock (get params "session_id"))]
        (when (and (headless-backend? config) (in method #("session.cancel" "session.cleanup")))
          (run-hosted config actor (cache-host-cancel (get params "session_id"))))
        (setv value (dispatch-method method params config actor)))
      (setv value (dispatch-method method params config actor)))
    (ok-response id value)
    (except [e RpcHostError]
      (err-response id e.message e.code))
    (except [e Exception]
      (err-response id (str e) None))))


;; ---------------------------------------------------------------------------
;; serve(oracle prepare_socket_path :1079 / serve :1159 / handle_stream :1182)
;; ---------------------------------------------------------------------------

(deff prepare-socket-path [socket-path]
  {:pre [(: socket-path str)]
   :post [(: % "None — 生存 socket は raise")]}
  "既存 socket の connect probe: 生きていれば単一インスタンス拒否、
   死んでいれば stale unlink(oracle prepare_socket_path)。"
  (when (not (os.path.exists socket-path))
    (return None))
  (setv probe (socket.socket socket.AF-UNIX socket.SOCK-STREAM))
  (setv alive False)
  (try
    (.connect probe socket-path)
    (setv alive True)
    (except [OSError])
    (finally (.close probe)))
  (when alive
    (raise (RuntimeError
             f"doeff-sessionhost is already listening on {socket-path}")))
  (os.remove socket-path)
  None)

(deff handle-stream [conn config actor]
  {:pre [(: conn socket.socket) (: config HostConfig) (: actor StoreActor)]
   :post [(: % "None")]}
  "connection 毎の JSON-lines ループ(oracle handle_stream)。EOF で終了、
   空行は skip、エラーは stderr へ(接続は落とすが daemon は落とさない)。"
  (try
    (with [stream (.makefile conn "rw" :encoding "utf-8" :newline "\n")]
      (while True
        (setv line (.readline stream))
        (when (= line "")
          (break))
        (when (= (.strip line) "")
          (continue))
        (setv response (dispatch-line line config actor))
        (.write stream (+ response "\n"))
        (.flush stream)))
    (except [e Exception]
      (print f"doeff-sessionhost client error: {e}" :file sys.stderr))
    (finally
      (.close conn)))
  None)


(defn run-worker-tick [worker thunk]
  "1 tick の隔離実行(oracle run_worker_tick :3433-3445 — worker thread は
   例外で死なず log して次 tick へ。disk-full storm が両 worker を黙殺した
   傷跡)。"
  (try
    (thunk)
    (except [e Exception]
      (print f"doeff-sessionhost {worker} error: {e}" :file sys.stderr))))


(defn heartbeat-loop [actor owner-pid shutdown-event]
  "lease 更新 loop(oracle heartbeat_loop :3454-3460、interval = TTL/3)。
   shutdown-event が立ったら打刻をやめる(issue #565): main の finally が
   釈放した lease を queue 残留 tick が張り直したり disappeared で吠えたり
   しない。release は event set の**後**に submit されるので、actor 直列化と
   合わせて「release より後に実行される heartbeat op は必ず event set 済み」
   が成立する(thunk 内の再検査がその実装点)。"
  (setv interval (max (// LEASE-TTL-SECONDS 3) 1))
  (while (not (.is-set shutdown-event))
    (run-worker-tick
      "heartbeat"
      (fn [] (.submit actor (fn [conn]
                              (when (not (.is-set shutdown-event))
                                (db-heartbeat-once conn owner-pid))))))
    (.wait shutdown-event interval)))


(defn reap-launched-sessions [config actor]
  "orphan 境界の巻き添え回収: この daemon が launch した active session
   (非 adopted 行)を cleanup 意味論(cleanup-program)で刈る — mux session
   ごと殺すので pane 内の agent プロセス(conformance_agent 等)が対で消える。
   adopted 席は daemon が作っていない(鏡原則 — ADR-DOE-AGENTS-007 条項 3)
   ので触らない。per-session 隔離は run-worker-tick(1 席の失敗で残りを
   見捨てない)。graceful SIGTERM(restart 耐久 S10/S15 が前提にする
   「daemon 死 ≠ session 死」)からは呼ばれない — orphan 検出時のみ。"
  (setv snaps (.submit actor
                       (fn [conn]
                         (db-session-list conn
                                          {"status" (sorted ACTIVE-STATUSES)}))))
  (for [snap snaps]
    (when (not (.get snap "adopted" False))
      (setv sid (get snap "session_id"))
      (run-worker-tick f"orphan-reap[{sid}]"
                       (fn [] (run-hosted config actor
                                          (if (headless-backend? config)
                                              (headless-cleanup-program sid)
                                              (cleanup-program sid))))))))


(defn orphan-watch-loop [config actor initial-ppid]
  "out-of-band 寿命境界(env knob DOEFF_SESSIONHOST_EXIT_WHEN_ORPHANED=1 の
   opt-in — conformance S28)。fixture teardown(__exit__)は正しいが pytest
   ごと SIGKILL されると走らない — その穴を daemon 自身が塞ぐ: spawn 元 =
   supervisor の死を getppid の変化(orphan は init/launchd へ reparent
   される)で検出し、(1) launch 済み active session を reap(上)、
   (2) SIGTERM を自送 — main の SIGTERM handler が SystemExit(0) に変換し、
   finally の lease 釈放を通る(issue #565 の graceful 経路と同一)。SIGTERM
   経路が万一 wedge しても backstop の os._exit(1) で有界に退場する(lease は
   TTL 失効が回収)。launchd 直下の production host は ppid が最初から 1 なの
   で、この knob を立てて起動してはならない(立てれば即時退場 — それが契約)。"
  (while True
    (setv ppid (os.getppid))
    (when (or (!= ppid initial-ppid) (= ppid 1))
      (print (+ "doeff-sessionhost supervisor vanished "
                f"(ppid {initial-ppid} -> {ppid}); reaping launched sessions "
                "and exiting")
             :file sys.stderr)
      (.flush sys.stderr)
      (run-worker-tick "orphan-reap" (fn [] (reap-launched-sessions config actor)))
      (os.kill (os.getpid) signal.SIGTERM)
      (time.sleep ORPHAN-EXIT-BACKSTOP-SECONDS)
      (os._exit 1))
    (time.sleep ORPHAN-POLL-INTERVAL-SECONDS)))


(deff history-retention-cutoff-iso []
  {:pre [True]
   :post [(: % str)]}
  "監査履歴の retention 境界(now - N days)。knob は use-site 読み
   (DOEFF_AGENTD_HISTORY_RETENTION_DAYS、他 knob と同じ流儀)。境界は
   店じまい記録(now-iso)と同じ isoformat なので文字列比較で整合する。"
  (setv days (or (env-positive-i64 "DOEFF_AGENTD_HISTORY_RETENTION_DAYS")
                 HISTORY-RETENTION-DAYS-DEFAULT))
  (.isoformat (- (datetime.now timezone.utc) (timedelta :days days))))


(defn prune-history-tick [actor]
  "監査履歴 prune の 1 pass(起動時 + 毎時)。削除があったときだけ log。"
  (setv cutoff (history-retention-cutoff-iso))
  (setv deleted (actor-prune-history actor cutoff HISTORY-PRUNE-BATCH-ROWS))
  (when (> deleted 0)
    (print (+ f"doeff-sessionhost history prune: deleted {deleted} audit "
              f"rows older than {cutoff}")
           :file sys.stderr))
  deleted)


(deff build-monitor-knobs [config]
  {:pre [(: config HostConfig)]
   :post [(: % MonitorKnobs)]}
  "MonitorKnobs の組み立て。stale / launch timeout は oracle と同じく
   **use-site で env を読む**(:47-50 / :79-85 — conformance が rebuild 無しで
   watchdog を秒単位へ圧縮する調整口。tick 毎に評価される)。"
  (MonitorKnobs
    :prompt-stall-seconds config.prompt-stall-seconds
    :result-solicitation-limit config.result-solicitation-limit
    :prompt-unblock-limit config.prompt-unblock-limit
    :launch-timeout-seconds (or (env-positive-i64 "DOEFF_AGENTD_LAUNCH_TIMEOUT_SECS")
                                LAUNCH-TIMEOUT-SECONDS)
    :stale-observation-seconds (or (env-positive-i64 "DOEFF_AGENTD_STALE_OBSERVATION_SECS")
                                   STALE-OBSERVATION-THRESHOLD-SECONDS)
    ;; booting 所有権 arm の boot watchdog 予算材料 — launch 側 ready gate と
    ;; 同じ env knob(issue agentd-session-registration-after-ready-gate)。
    :repl-idle-max-wait-seconds (or (env-positive-i64
                                      "DOEFF_AGENTD_REPL_IDLE_MAX_WAIT_SECS")
                                    REPL-IDLE-MAX-WAIT-SECONDS)
    ;; koine 条項 4(ADR-007 R4): stalled 導出閾値。wire 導出側
    ;; (augment-wire-snapshot)と同じ env knob を使う。
    :turn-stall-seconds (effective-turn-stall-seconds)
    ;; issue #568(ADR-DOE-AGENTS-010 R2/R3)— conformance 調整口も他 knob と
    ;; 同じ use-site env 読みの流儀。
    :paste-resubmit-limit (or (env-positive-i64 "DOEFF_AGENTD_PASTE_RESUBMIT_LIMIT")
                              PASTE-RESUBMIT-LIMIT)
    :awaiting-response-timeout-seconds
      (or (env-positive-i64 "DOEFF_AGENTD_AWAITING_RESPONSE_TIMEOUT_SECS")
          AWAITING-RESPONSE-TIMEOUT-SECONDS)
    ;; ADR-002 R-conversation-evidence — conformance / 運用の調整口も他 knob と
    ;; 同じ use-site env 読みの流儀(margin は調整口を持たない — 配送物理の
    ;; 余白であって運用パラメータではない)。
    :conversation-quiescence-seconds
      (or (env-positive-i64 "DOEFF_AGENTD_CONVERSATION_QUIESCENCE_SECS")
          CONVERSATION-QUIESCENCE-SECONDS)
    :judge-cmd config.prompt-judge-cmd))


(defn monitor-loop [config actor]
  "monitor loop(oracle monitor_loop :3447-3452)。tick = monitor-cycle
   program の実行 — per-session 隔離は program 所有(policy.hy:622、oracle の
   tick 隔離より強い)。run-worker-tick は backstop。監査履歴の毎時 prune も
   ここが持つ(retention 反故 = 無限成長は 2026-07-27 wedge の根)。"
  (setv next-prune (+ (time.monotonic) HISTORY-PRUNE-INTERVAL-SECONDS))
  ;; 段 12 lane 12b(agora-redesign #207 根 1): headless の器は手番の終わりを読み手が読んだ拍に
  ;; 登記簿へ合図する(HeadlessRegistry.wait-turn-end)。拍の合間はその合図を待つ — 上限が
  ;; monitor の周期(周期は保険に退く)。合図が来れば即座に次の拍が観測して turn_ended_at を刻む。
  ;; tui の器(tmux / herdr)に合図は無く、今日どおり周期で眠る。
  (setv seen-turn-ends 0)
  (while True
    (run-worker-tick
      "monitor"
      (fn [] (run-hosted config actor
                         (if (headless-backend? config)
                             (headless-monitor-cycle)
                             (monitor-cycle (build-monitor-knobs config))))))
    (when (>= (time.monotonic) next-prune)
      (run-worker-tick "history-prune" (fn [] (prune-history-tick actor)))
      (setv next-prune (+ (time.monotonic) HISTORY-PRUNE-INTERVAL-SECONDS)))
    (if (headless-backend? config)
        (setv seen-turn-ends (.wait-turn-end HEADLESS-REGISTRY seen-turn-ends
                                             config.monitor-interval-seconds))
        (time.sleep config.monitor-interval-seconds))))


(deff bind-listener [socket-path]
  {:pre [(: socket-path str)]
   :post [(: % socket.socket)]}
  "単一インスタンス排他の実体 = socket bind(lease はその影)。生存 listener は
   prepare-socket-path が loud に拒否するので、bind に勝った 1 プロセスだけが
   ここを通過する。main はこれを store open / lease より**先**に呼ぶ —
   敗者が lease や latch clear に触れてから死ぬ 2026-07-07 の
   ensure spawn スパイラル(競合 child が lease を盗んで socket 衝突で死亡、
   lease が死 pid 名義で腐る)の根治点。"
  (prepare-socket-path socket-path)
  (setv listener (socket.socket socket.AF-UNIX socket.SOCK-STREAM))
  (.bind listener socket-path)
  (.listen listener 64)
  listener)


(deff serve [config actor listener shutdown-event]
  {:pre [(: config HostConfig) (: actor StoreActor) (: listener socket.socket)
         (: shutdown-event threading.Event)]
   :post [(: % "戻らない(accept loop — 脱出は SystemExit / KeyboardInterrupt)")]}
  "monitor / heartbeat thread → accept loop(connection 毎 thread、
   oracle serve :1159-1180)。listener は bind-listener が起動順の先頭で
   確保済みのものを受け取る。shutdown-event は heartbeat の停止合図
   (main の finally が lease 釈放前に立てる — issue #565)。"
  (setv monitor (threading.Thread :target monitor-loop
                                  :args #(config actor)
                                  :daemon True
                                  :name "sessionhost-monitor"))
  (.start monitor)
  (setv heartbeat (threading.Thread :target heartbeat-loop
                                    :args #(actor (os.getpid) shutdown-event)
                                    :daemon True
                                    :name "sessionhost-heartbeat"))
  (.start heartbeat)
  ;; out-of-band 寿命境界(opt-in)— knob 未設定なら thread ごと不在。
  (when config.exit-when-orphaned
    (setv orphan-watch (threading.Thread :target orphan-watch-loop
                                         :args #(config actor (os.getppid))
                                         :daemon True
                                         :name "sessionhost-orphan-watch"))
    (.start orphan-watch))
  (while True
    (try
      (setv #(conn _addr) (.accept listener))
      (except [e OSError]
        (print f"doeff-sessionhost accept error: {e}" :file sys.stderr)
        (continue)))
    (setv worker (threading.Thread :target handle-stream
                                   :args #(conn config actor)
                                   :daemon True))
    (.start worker)))


;; ---------------------------------------------------------------------------
;; entry(oracle main :566-598)
;; ---------------------------------------------------------------------------

;; ---------------------------------------------------------------------------
;; 停止の作法(段 10 lane 10h 便 2・agora-redesign #84): TERM で子を黙って道連れにしない
;; ---------------------------------------------------------------------------
;;
;; launchd の bootout / kickstart は process group ごと殺すので、headless の子 process(pipe の子)は
;; host と共に死ぬ。手番の途中の行と agent-job をそのまま残すと、次の起動の復帰(recover-headless-rows・
;; agentd の recover-job)が「死んだ」と観測して閉じるまで会話が「動いている」のままになる。TERM の
;; 1 度目は accept loop を生かしたまま別 thread で (1) 登録された停止の hook(entry.py が agentd の
;; close_for_stop を登録する — 器の眺めを host の RPC で読むので accept が要る)(2) headless の行の
;; 停止の腕(stop-headless-rows — 手番の途中の行を stopped に・全 process を並列の猶予で降ろす)を
;; 走らせ、済んだら自分に同じ信号を撃ち直す = 2 度目は SystemExit(0) で finally(lease の
;; 釈放)へ。判断は headless_protocol.stop_verdict の 1 点。SIGKILL(kickstart -k の即時)には手が
;; 無い — その時は次の起動の復帰が拾う(便 1)。

;; entry.py が登録する停止の hook(agentd の close_for_stop)— host は中身を知らない。
(setv SHUTDOWN-HOOKS [])

(defn register-shutdown-hook [hook]
  "host の停止(TERM)の前に走らせる hook を登録する(agentd 等 — 引数なし・戻り値は読まない)。"
  (.append SHUTDOWN-HOOKS hook)
  None)

(defn graceful-stop [config actor signum]
  "TERM の 1 度目の腕(別 thread): hook → headless の行の停止 → 自分に同じ信号を撃ち直す。
   hook / 腕の例外は log して次へ(停止を止めない)。"
  (setv name (. (signal.Signals signum) name))
  (print f"doeff-sessionhost stop ({name}): closing running turns before exit" :file sys.stderr)
  (for [hook (list SHUTDOWN-HOOKS)]
    (try
      (hook)
      (except [e Exception]
        (print f"doeff-sessionhost stop: hook failed: {(. (type e) __name__)}: {e}" :file sys.stderr))))
  (when (headless-backend? config)
    (try
      (setv outcomes (run-hosted config actor (stop-headless-rows name)))
      (setv cut (lfor [sid status] (.items outcomes) :if (and (!= sid "killed") (= status "stopped")) sid))
      (print (+ f"doeff-sessionhost stop ({name}): {(.get outcomes "killed" 0)} headless process(es) terminated, "
                f"{(len cut)} mid-turn row(s) ended as stopped/cancelled"
                (if cut f": {(.join ", " cut)}" ""))
             :file sys.stderr)
      (except [e Exception]
        (print f"doeff-sessionhost stop: headless rows not settled: {(. (type e) __name__)}: {e}" :file sys.stderr))))
  (.flush sys.stderr)
  ;; 実の信号で撃ち直す(_thread.interrupt_main は accept の blocking syscall を起こさない — main thread は
  ;; 次の接続まで handler に来ない。実の TERM は EINTR で accept を抜け、2 度目の handler が SystemExit を投げる)。
  (os.kill (os.getpid) signum)
  None)

(defn install-graceful-stop [config actor]
  "TERM の handler を据える(lease を取った後・serve の前): 1 度目は graceful-stop の thread、2 度目
   (撃ち直し・停止中の再送)は SystemExit(0)。"
  (setv stopping (threading.Event))
  (defn on-term [signum frame]
    (if (.is-set stopping)
        (raise (SystemExit 0))
        (do
          (.set stopping)
          (setv worker (threading.Thread :target graceful-stop
                                         :args #(config actor signum)
                                         :daemon True
                                         :name "sessionhost-graceful-stop"))
          (.start worker))))
  (signal.signal signal.SIGTERM on-term)
  None)


(defn main []
  "serve entry(console script doeff-sessionhost の serve 経路 — subcommand
   dispatch は hostmain.py 所有で、report-result-mcp は relaymain.py へ
   Hy import より先に分岐済み)。SIGTERM(launchctl bootout の標準経路)は
   SystemExit(0) に変換し、finally の lease 釈放へ落とす(issue #565)。"
  (setv raw (list (cut sys.argv 1 None)))
  (setv config (parse-args raw))
  (for [parent [(os.path.dirname config.db-path)
                (os.path.dirname config.socket-path)]]
    (when parent
      (os.makedirs parent :exist-ok True)))
  ;; SIGTERM handler は main thread で SystemExit(0) を raise する — accept
  ;; loop(下の serve)の except 節は OSError のみなので素通りして finally
  ;; まで届く。bind 前に登録するので、bind 敗死する競合者も clean に死ぬ
  ;; (lease 未取得 = 釈放対象なし)。
  (signal.signal signal.SIGTERM (fn [signum frame] (raise (SystemExit 0))))
  ;; 起動順は bind が先(oracle main :581-596 からの意図的乖離、2026-07-07
  ;; ensure spawn スパイラルの根治): socket bind = 排他の実体に負けた競合者は
  ;; store open / lease 取得 / latch clear のどれにも触れずに死ぬ。
  ;; lease 取得(未失効 lease は loud に拒否)→ awaiting_response latch 全
  ;; clear は bind 通過後にのみ走る。
  (setv listener (bind-listener config.socket-path))
  (setv actor (StoreActor config.db-path))
  (setv owner-pid (os.getpid))
  (.submit actor (fn [conn] (db-acquire-lease conn owner-pid)))
  ;; ここから先は lease の持ち主 — graceful 終了(SIGTERM→SystemExit /
  ;; SIGINT / 予期せぬ例外)は finally が自 lease を釈放し、launchd
  ;; KeepAlive の即 spawn 後継が lease-conflict で敗死しない(issue #565)。
  ;; SIGKILL / crash は従来どおり TTL 失効がバックストップ。
  (setv shutdown-event (threading.Event))
  ;; 段 10 lane 10h 便 2: lease を取った後は TERM を graceful に(走っている手番を閉じてから finally へ)。
  (install-graceful-stop config actor)
  (try
    ;; 段 10 lane 10h(agora-redesign #84): headless の host は latch の clear より前・accept より前に
    ;; 復帰を 1 度走らせる — 手番の途中のまま残った行の backend を観測し、死んでいれば exited +
    ;; vanished に倒す(判断は headless_protocol.recovery_verdict の 1 点)。log に数を 1 行。
    (when (headless-backend? config)
      (setv recovered (run-hosted config actor (recover-headless-rows)))
      (setv dead (lfor [sid status] (.items recovered) :if (= status "exited") sid))
      (print (+ f"doeff-sessionhost startup recovery: {(len recovered)} headless rows observed, "
                f"{(len dead)} ended as exited/vanished (backend process dead)"
                (if dead f": {(.join ", " dead)}" ""))
             :file sys.stderr))
    (.submit actor (fn [conn] (db-clear-awaiting-latches conn)))
    ;; 監査履歴の retention + 物理回収(2026-07-27 wedge 根治)。VACUUM は
    ;; 全書き換えなので accept 開始前のここでだけ走る(serve 中は禁止 —
    ;; actor を数秒占有して client probe を飢えさせない)。
    (prune-history-tick actor)
    (when (.submit actor db-vacuum-if-bloated)
      (print "doeff-sessionhost store vacuum: reclaimed free pages"
             :file sys.stderr))
    (serve config actor listener shutdown-event)
    (finally
      ;; event set → release submit の順が要(heartbeat-loop の docstring):
      ;; release より後に actor で実行される heartbeat op は必ず skip する。
      (.set shutdown-event)
      (when (.submit actor (fn [conn] (db-release-lease conn owner-pid)))
        (print "doeff-sessionhost lease released on shutdown"
               :file sys.stderr))))
  None)
