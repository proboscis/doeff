;;; 直接束縛 deftest: Hy session host の walking skeleton(DOE-004 C3)。
;;;
;;; host.hy の host 層物理 — CLI parse(oracle parse_args :600-693)・
;;; default path(:718-742)・wire 封筒(:210-235 の skip_serializing_if
;;; parity 込み)・dispatch(:1215-1301)・単一インスタンス拒否
;;; (prepare_socket_path :1079-1092)— を daemon 起動なしで検証する。
;;; serve loop 全体の検証は conformance suite(転送束縛)の領分。

(require doeff-hy.macros [deftest])

(import json)
(import os)
(import shutil)
(import signal)
(import socket)
(import sqlite3)
(import subprocess)
(import sys)
(import tempfile)
(import time)

(import doeff_agents.sessionhost.host [
  HostConfig
  DEFAULT-PROMPT-JUDGE-CMD
  RPC-ERR-ALREADY-TERMINAL
  RPC-ERR-RESULT-REJECTED
  RpcHostError
  build-launch-program-params
  parse-args
  default-db-path
  default-socket-path
  ok-response
  err-response
  dispatch-line
  prepare-socket-path
  report-result-op
  main])
(import doeff_agents.sessionhost.store [
  StoreActor
  db-acquire-lease
  open-conn
  db-migrate
  db-upsert-snapshot
  db-session-get])


;; ---------------------------------------------------------------------------
;; env 操作ヘルパ(save/restore — deftest は他 test と env を共有する)
;; ---------------------------------------------------------------------------

(defn with-env [overrides thunk]
  "overrides(value=None は削除)を適用して thunk を呼び、必ず復元する。"
  (setv saved {})
  (for [key (.keys overrides)]
    (setv (get saved key) (.get os.environ key)))
  (try
    (for [[key value] (.items overrides)]
      (if (is value None)
          (.pop os.environ key None)
          (setv (get os.environ key) value)))
    (thunk)
    (finally
      (for [[key value] (.items saved)]
        (if (is value None)
            (.pop os.environ key None)
            (setv (get os.environ key) value))))))


;; ---------------------------------------------------------------------------
;; CLI parse(oracle parse_args parity)
;; ---------------------------------------------------------------------------

(deftest test-parse-args-defaults
  (defn check []
    (setv config (parse-args []))
    (assert (= config.db-path (default-db-path)))
    (assert (= config.socket-path (default-socket-path)))
    (assert (= config.tmux-bin "tmux"))
    (assert (= config.monitor-interval-seconds 1.0))
    (assert (= config.max-running 10))
    (assert (= config.result-solicitation-limit 2))
    (assert (= config.prompt-stall-seconds 180))
    (assert (= config.prompt-unblock-limit 3))
    ;; 既定 judge は実 claude haiku(oracle :163-164)— 無効化は明示のみ
    (assert (= config.prompt-judge-cmd DEFAULT-PROMPT-JUDGE-CMD)))
  ;; env knob が漏れていると既定が変わるので、素の env で検証する
  (with-env {"DOEFF_AGENTD_RESULT_SOLICITATIONS" None
             "DOEFF_AGENTD_PROMPT_STALL_SECS" None
             "DOEFF_AGENTD_PROMPT_UNBLOCK_ATTEMPTS" None
             "DOEFF_AGENTD_PROMPT_JUDGE_CMD" None}
            check))


(deftest test-parse-args-harness-argv
  ;; conformance harness が渡す正確な argv(harness.py start())
  (setv config (parse-args ["--db" "/tmp/x.sqlite"
                            "--socket" "/tmp/x.sock"
                            "--monitor-interval-ms" "100"
                            "--max-running" "4"
                            "--prompt-judge-cmd" ""
                            "serve"]))
  (assert (= config.db-path "/tmp/x.sqlite"))
  (assert (= config.socket-path "/tmp/x.sock"))
  (assert (= config.monitor-interval-seconds 0.1))
  (assert (= config.max-running 4))
  ;; 空文字 = judge 無効(ハザード 1 — conformance が依存する意味論)
  (assert (is config.prompt-judge-cmd None)))


(deftest test-parse-args-max-running-unlimited
  ;; 「上限なし」を言う口(2026-08-19 operator 裁定: 同時走行の上限は ACP の
  ;; 都合であって sessionhost の性質ではない)。None = admission の check ごと
  ;; 飛ぶ = 無制限で、これは直接束縛(config を持たない使い方)と同じ形。
  (for [spelling ["none" "unlimited" "NONE" "Unlimited"]]
    (setv config (parse-args ["--max-running" spelling "serve"]))
    (assert (is config.max-running None) f"expected unlimited for {spelling}"))
  ;; 数値の綴りは従来どおり
  (assert (= (. (parse-args ["--max-running" "4" "serve"]) max-running) 4))
  ;; 旗を省略したら従来の既定 10 のまま — 省略へ無制限を割り当てると、省略
  ;; している既存の呼び手(適合 harness 等)の挙動まで一斉に変わる
  (assert (= (. (parse-args []) max-running) 10)))


(deftest test-parse-args-rejects-max-running-zero
  ;; 0 は「上限なし」の綴りではない。admission は `owned-count >= max-running`
  ;; なので 0 は常に真 = 全 launch を恒久 100% 拒否する。無音で全拒否する
  ;; daemon を作らないため、起動時に loud に落とす。
  (setv raised None)
  (try
    (parse-args ["--max-running" "0" "serve"])
    (except [e ValueError] (setv raised e)))
  (assert (is-not raised None) "expected reject for --max-running 0")
  (assert (in "rejects every launch" (str raised)))
  (assert (in "--max-running none" (str raised))))


(deftest test-parse-args-rejects
  (for [[args fragment]
        [[["--frobnicate"] "unknown argument"]
         [["--tmux"] "--tmux requires a value"]
         [["--prompt-stall-secs" "0"] "must be positive"]
         [["--max-running" "0"] "rejects every launch"]
         [["status"] "unknown argument"]]]
    (setv raised None)
    (try
      (parse-args args)
      (except [e ValueError] (setv raised e)))
    (assert (is-not raised None) f"expected reject for {args}")
    (assert (in fragment (str raised)))))


(deftest test-parse-args-env-knobs
  ;; 有効値は既定を置換、parse 不能値は黙って既定へ fallback(oracle env_u32)
  (defn check-valid []
    (setv config (parse-args []))
    (assert (= config.result-solicitation-limit 5))
    (assert (= config.prompt-stall-seconds 7))
    (assert (= config.prompt-unblock-limit 9)))
  (with-env {"DOEFF_AGENTD_RESULT_SOLICITATIONS" "5"
             "DOEFF_AGENTD_PROMPT_STALL_SECS" "7"
             "DOEFF_AGENTD_PROMPT_UNBLOCK_ATTEMPTS" "9"}
            check-valid)
  (defn check-invalid []
    (setv config (parse-args []))
    (assert (= config.result-solicitation-limit 2))
    (assert (= config.prompt-stall-seconds 180)))
  (with-env {"DOEFF_AGENTD_RESULT_SOLICITATIONS" "banana"
             "DOEFF_AGENTD_PROMPT_STALL_SECS" "-3"
             "DOEFF_AGENTD_PROMPT_UNBLOCK_ATTEMPTS" None}
            check-invalid))


(deftest test-default-socket-path
  (defn check-runtime-dir []
    (assert (= (default-socket-path) "/run/user/501/doeff/agentd.sock")))
  (with-env {"XDG_RUNTIME_DIR" "/run/user/501"} check-runtime-dir)
  (defn check-user-fallback []
    (assert (= (default-socket-path) "/tmp/doeff-agentd-conftest.sock")))
  (with-env {"XDG_RUNTIME_DIR" None "USER" "conftest" "LOGNAME" None}
            check-user-fallback))


;; ---------------------------------------------------------------------------
;; wire 封筒(RpcResponse の skip_serializing_if parity)
;; ---------------------------------------------------------------------------

(deftest test-wire-envelope
  ;; 成功: result は JSON null でも field として残る(session.get の不在)
  (assert (= (ok-response 1 None) "{\"id\":1,\"ok\":true,\"result\":null}"))
  (assert (= (ok-response "a" {"x" 1})
             "{\"id\":\"a\",\"ok\":true,\"result\":{\"x\":1}}"))
  ;; 失敗: error_code は None のとき field ごと省略、構造化エラーのみ載る
  (assert (= (err-response None "boom" None)
             "{\"id\":null,\"ok\":false,\"error\":\"boom\"}"))
  (assert (= (err-response 2 "timeout" -32000)
             "{\"id\":2,\"ok\":false,\"error\":\"timeout\",\"error_code\":-32000}")))


;; ---------------------------------------------------------------------------
;; dispatch(1 行 → 1 行)
;; ---------------------------------------------------------------------------

(defn with-skeleton [thunk]
  "tmpdir の StoreActor + config で thunk(config actor)を回し、必ず閉じる。"
  (setv d (tempfile.mkdtemp))
  (try
    (setv config (parse-args ["--db" (os.path.join d "agentd.sqlite")
                              "--socket" (os.path.join d "agentd.sock")
                              "--prompt-judge-cmd" ""
                              "serve"]))
    (setv actor (StoreActor config.db-path))
    (try
      (thunk config actor)
      (finally (.close actor)))
    (finally (shutil.rmtree d :ignore-errors True))))


(deftest test-dispatch-invalid-json
  (defn check [config actor]
    (setv response (json.loads (dispatch-line "not json" config actor)))
    (assert (is (get response "id") None))
    (assert (= (get response "ok") False))
    (assert (.startswith (get response "error") "invalid request:"))
    (assert (not-in "error_code" response))
    ;; id 欠落も invalid request(serde の必須 field parity)
    (setv response (json.loads (dispatch-line "{\"method\":\"daemon.status\"}"
                                              config actor)))
    (assert (= (get response "ok") False))
    (assert (in "invalid request" (get response "error"))))
  (with-skeleton check))


(deftest test-dispatch-daemon-status
  (defn check [config actor]
    ;; lease 取得後の daemon.status は lease 行を返す(oracle :1252-1263)
    (.submit actor (fn [conn] (db-acquire-lease conn (os.getpid))))
    (setv response (json.loads (dispatch-line
                                 "{\"id\":7,\"method\":\"daemon.status\"}"
                                 config actor)))
    (assert (= (get response "id") 7))
    (assert (= (get response "ok") True))
    (setv result (get response "result"))
    (assert (= (get result "state") "running"))
    (assert (= (get result "pid") (os.getpid)))
    (assert (= (get result "db_path") config.db-path))
    (assert (= (get result "socket_path") config.socket-path))
    (assert (= (get result "max_running") 10))
    (assert (= (get result "active_sessions") 0))
    (setv lease (get result "lease"))
    (assert (= (get lease "lease_name") "doeff-agentd"))
    (assert (= (get lease "owner_pid") (os.getpid))))
  (with-skeleton check))


(deftest test-dispatch-kinds-list
  ;; DOE-004 R5(縮小版): kinds.list は policy.hy の kind 表(単一ソース)
  ;; から導出した広告を返す — control plane の定期照合の読み口。表と広告が
  ;; 乖離したらここが red(スキーマの家は 1 つ)。#15: codex は v2(受理形の
  ;; 拡張 — {codex_home} XOR {auth_file, profile_dir})、claude-code は v1 の
  ;; まま。required_field は人間可読ラベル(照合の機械面は kind+api_version)。
  (defn check [config actor]
    (setv response (json.loads (dispatch-line
                                 "{\"id\":9,\"method\":\"kinds.list\"}"
                                 config actor)))
    (assert (= (get response "id") 9))
    (assert (= (get response "ok") True))
    (setv kinds (get (get response "result") "kinds"))
    ;; ADR-006 R5: resumable / forkable は capability の additive field。
    ;; api_version は binding 受理形の契約版なので据え置き(受理形は不変)。
    (assert (= kinds
               [{"kind" "claude-code"
                 "agent_type" "claude"
                 "required_field" "config_dir"
                 "api_version" "acp.dev/agent-binding/v1"
                 "resumable" True
                 "forkable" True}
                {"kind" "codex"
                 "agent_type" "codex"
                 "required_field" "codex_home | auth_file+profile_dir"
                 "api_version" "acp.dev/agent-binding/v2"
                 "resumable" True
                 "forkable" True}])))
  (with-skeleton check))


(deftest test-dispatch-skeleton-loud
  (defn check [config actor]
    ;; await: 不在 session は -32001 を error_code 付きで返す(oracle
    ;; :2071-2082 — timeout を待たず即答)
    (setv response (json.loads (dispatch-line
                                 "{\"id\":1,\"method\":\"session.await_result\",\"params\":{\"session_id\":\"nope\"}}"
                                 config actor)))
    (assert (= (get response "ok") False))
    (assert (= (get response "error_code") -32001))
    (assert (= (get response "error") "no session with id 'nope'"))
    ;; report: 不在 session は require-session の文言
    (setv response (json.loads (dispatch-line
                                 "{\"id\":1,\"method\":\"session.report_result\",\"params\":{\"session_id\":\"nope\",\"payload\":{}}}"
                                 config actor)))
    (assert (= (get response "ok") False))
    (assert (= (get response "error") "session is not registered: nope"))
    ;; 実装済み method の params 検証も loud(missing field)
    (setv response (json.loads (dispatch-line
                                 "{\"id\":1,\"method\":\"session.launch\",\"params\":{}}"
                                 config actor)))
    (assert (= (get response "ok") False))
    (assert (in "missing field" (get response "error")))
    ;; 存在しない session への get は result null(oracle parity)
    (setv response (json.loads (dispatch-line
                                 "{\"id\":2,\"method\":\"session.get\",\"params\":{\"session_id\":\"nope\"}}"
                                 config actor)))
    (assert (= (get response "ok") True))
    (assert (is (get response "result") None))
    ;; 存在しない session への cancel は require-session の文言(oracle :2257)
    (setv response (json.loads (dispatch-line
                                 "{\"id\":3,\"method\":\"session.cancel\",\"params\":{\"session_id\":\"nope\"}}"
                                 config actor)))
    (assert (= (get response "ok") False))
    (assert (= (get response "error") "session is not registered: nope"))
    ;; 契約外 method は oracle と同文言
    (setv response (json.loads (dispatch-line
                                 "{\"id\":4,\"method\":\"no.such\"}"
                                 config actor)))
    (assert (= (get response "ok") False))
    (assert (= (get response "error") "unknown method: no.such")))
  (with-skeleton check))


;; ---------------------------------------------------------------------------
;; 単一インスタンス拒否(実 socket、tmpdir)
;; ---------------------------------------------------------------------------

(deftest test-prepare-socket-path
  (setv d (tempfile.mkdtemp))
  (try
    (setv path (os.path.join d "agentd.sock"))
    ;; 不存在 → no-op
    (assert (is None (prepare-socket-path path)))
    ;; stale(誰も listen していない socket file)→ unlink
    (setv stale (socket.socket socket.AF-UNIX socket.SOCK-STREAM))
    (.bind stale path)
    (.close stale)
    (assert (os.path.exists path))
    (assert (is None (prepare-socket-path path)))
    (assert (not (os.path.exists path)))
    ;; 生存(listen 中)→ 単一インスタンス拒否
    (setv live (socket.socket socket.AF-UNIX socket.SOCK-STREAM))
    (.bind live path)
    (.listen live 1)
    (setv raised None)
    (try
      (prepare-socket-path path)
      (except [e RuntimeError] (setv raised e)))
    (.close live)
    (assert (is-not raised None))
    (assert (in "already listening" (str raised)))
    (finally
      (shutil.rmtree d :ignore-errors True))))


(deftest test-main-loser-dies-at-bind-before-touching-store
  ;; 起動順の根治ピン(2026-07-07 ensure spawn スパイラル): socket bind =
  ;; 排他の実体に負けた競合者は store open / lease 取得 / latch clear の
  ;; どれにも触れずに死ぬ。旧順序(store→lease→bind)ではこの test の DB
  ;; file が作られ lease 行が競合者名義で書かれてから bind で死んでいた。
  (setv d (tempfile.mkdtemp))
  (try
    (setv sock-path (os.path.join d "agentd.sock"))
    (setv db-path (os.path.join d "loser.sqlite"))
    ;; 本物の代役: live listener が socket を保持している
    (setv live (socket.socket socket.AF-UNIX socket.SOCK-STREAM))
    (.bind live sock-path)
    (.listen live 1)
    (setv saved-argv (list sys.argv))
    (setv raised None)
    (try
      (setv sys.argv ["doeff-sessionhost" "--db" db-path "--socket" sock-path "serve"])
      (main)
      (except [e RuntimeError] (setv raised e))
      (finally (setv sys.argv saved-argv)))
    (.close live)
    (assert (is-not raised None))
    (assert (in "already listening" (str raised)))
    ;; 敗者は store に一切触れていない — DB file 不在が証拠
    (assert (not (os.path.exists db-path)))
    (finally
      (shutil.rmtree d :ignore-errors True))))


;; ---------------------------------------------------------------------------
;; wire → launch program params(R7 binding passthrough)
;; ---------------------------------------------------------------------------

(deftest test-launch-program-params-carries-binding-and-overlay
  ;; R7: typed binding は wire から program へそのまま渡り、session_env は
  ;; 非 auth overlay として素通しされる(auth 検査は launch program の
  ;; admission 所有 — ここは serde 既定値の再現のみ)。
  (setv config (HostConfig :db-path "/tmp/x.db" :socket-path "/tmp/x.sock"
                           :tmux-bin "tmux" :monitor-interval-seconds 1.0
                           :max-running 4 :result-solicitation-limit 3
                           :prompt-stall-seconds 90 :prompt-unblock-limit 3
                           :prompt-judge-cmd DEFAULT-PROMPT-JUDGE-CMD))
  (setv wire {"session_id" "s1" "session_name" "doeff-s1"
              "agent_type" "codex" "work_dir" "/w"
              "binding" {"kind" "codex" "codex_home" "/x/codex"}
              "session_env" {"PYTHONUNBUFFERED" "1"}})
  (setv params (build-launch-program-params wire config))
  (assert (= (get params "binding") {"kind" "codex" "codex_home" "/x/codex"}))
  (assert (= (get params "session_env") {"PYTHONUNBUFFERED" "1"}))
  ;; binding 省略は None(serde 既定値)
  (setv bare {"session_id" "s1" "session_name" "doeff-s1"
              "agent_type" "codex" "work_dir" "/w"})
  (setv params2 (build-launch-program-params bare config))
  (assert (is None (get params2 "binding")))
  (assert (= (get params2 "session_env") {})))


(deftest test-launch-program-params-context-file-admission
  ;; law context-file-rides-the-wire(ACP steward W1b 2026-08-20): 受理形は
  ;; {path, content}。path は裸のファイル名のみ — session host は work_dir の
  ;; 外へ 1 歩も書かない(separator / '..' は traversal の口)。合格形は
  ;; program へ素通し、省略は None。
  (setv config (HostConfig :db-path "/tmp/x.db" :socket-path "/tmp/x.sock"
                           :tmux-bin "tmux" :monitor-interval-seconds 1.0
                           :max-running 4 :result-solicitation-limit 3
                           :prompt-stall-seconds 90 :prompt-unblock-limit 3
                           :prompt-judge-cmd DEFAULT-PROMPT-JUDGE-CMD))
  (setv base {"session_id" "s1" "session_name" "doeff-s1"
              "agent_type" "codex" "work_dir" "/w"})
  ;; 合格: 素通し
  (setv ok (dict base))
  (setv (get ok "context_file")
        {"path" ".acp-context.json" "content" {"work_item_id" "wi-1"}})
  (setv params (build-launch-program-params ok config))
  (assert (= (get params "context_file")
             {"path" ".acp-context.json" "content" {"work_item_id" "wi-1"}}))
  ;; 省略: None
  (assert (is None (get (build-launch-program-params (dict base) config)
                        "context_file")))
  ;; reject: separator つき path(traversal)/ '..' / content 欠落 / 非 dict
  (for [bad [{"path" "sub/ctx.json" "content" {}}
             {"path" ".." "content" {}}
             {"path" ".acp-context.json"}
             "not-a-dict"]]
    (setv wire (dict base))
    (setv (get wire "context_file") bad)
    (setv raised None)
    (try
      (build-launch-program-params wire config)
      (except [e RuntimeError] (setv raised e)))
    (assert (is-not raised None) f"expected reject for {bad}")
    (assert (in "context_file" (str raised)))))


(deftest test-launch-program-params-workspace-seed-admission
  ;; ACP W2(law resolved-materialization): workspace_seed の fail-closed
  ;; admission — 絶対 path・mode 2 語彙・detached は sha 必須・branch は
  ;; branch 必須・sha は hex 形・marker name は裸のファイル名。合格形は素通し。
  (setv config (HostConfig :db-path "/tmp/x.db" :socket-path "/tmp/x.sock"
                           :tmux-bin "tmux" :monitor-interval-seconds 1.0
                           :max-running 4 :result-solicitation-limit 3
                           :prompt-stall-seconds 90 :prompt-unblock-limit 3
                           :prompt-judge-cmd DEFAULT-PROMPT-JUDGE-CMD))
  (setv base {"session_id" "s1" "session_name" "doeff-s1"
              "agent_type" "codex" "work_dir" "/w"})
  (setv good {"repo" "/repo/acp" "dir" "/root/ws/inv-1"
              "mode" "detached" "sha" "abc1234def"
              "owner_marker" {"name" ".acp-owner.json" "content_text" "{}"}})
  (setv ok (dict base))
  (setv (get ok "workspace_seed") good)
  (assert (= (get (build-launch-program-params ok config) "workspace_seed") good))
  ;; 省略は None
  (assert (is None (get (build-launch-program-params (dict base) config)
                        "workspace_seed")))
  (for [bad [{"repo" "rel/path" "dir" "/d" "mode" "detached" "sha" "abc1234def"}
             {"repo" "/r" "dir" "/d" "mode" "steal" "sha" "abc1234def"}
             {"repo" "/r" "dir" "/d" "mode" "detached"}
             {"repo" "/r" "dir" "/d" "mode" "branch"}
             {"repo" "/r" "dir" "/d" "mode" "detached" "sha" "NOT-HEX"}
             {"repo" "/r" "dir" "/d" "mode" "detached" "sha" "abc1234def"
              "owner_marker" {"name" "../evil" "content_text" "{}"}}
             "not-a-dict"]]
    (setv wire (dict base))
    (setv (get wire "workspace_seed") bad)
    (setv raised None)
    (try
      (build-launch-program-params wire config)
      (except [e RuntimeError] (setv raised e)))
    (assert (is-not raised None) f"expected reject for {bad}")
    (assert (in "workspace_seed" (str raised)))))


(deftest test-launch-program-params-workspace-seed-release-admission
  ;; ACP ADR 3d81bd: stale holder の解除の鍵(release_stale_holder)と保護集合
  ;; (protected_dirs)の fail-closed admission — 鍵は bool・真なら branch 形
  ;; 限定かつ protected_dirs(絶対 path の list・空可)必須。合格形は素通し
  ;; (engine の data を実行係が改変しない)。
  (setv config (HostConfig :db-path "/tmp/x.db" :socket-path "/tmp/x.sock"
                           :tmux-bin "tmux" :monitor-interval-seconds 1.0
                           :max-running 4 :result-solicitation-limit 3
                           :prompt-stall-seconds 90 :prompt-unblock-limit 3
                           :prompt-judge-cmd DEFAULT-PROMPT-JUDGE-CMD))
  (setv base {"session_id" "s1" "session_name" "doeff-s1"
              "agent_type" "codex" "work_dir" "/w"})
  (for [good [{"repo" "/repo/acp" "dir" "/root/ws/inv-2" "mode" "branch"
               "branch" "feat/impl-x" "release_stale_holder" True
               "protected_dirs" ["/root/ws/inv-1" "/root/ws/inv-0"]}
              ;; 空の保護集合も「集合を名指した」形 — 受理
              {"repo" "/repo/acp" "dir" "/root/ws/inv-2" "mode" "branch"
               "branch" "feat/impl-x" "release_stale_holder" True
               "protected_dirs" []}
              ;; 偽は鍵なしと同義(第 1 波の挙動)— 受理
              {"repo" "/repo/acp" "dir" "/root/ws/inv-2" "mode" "branch"
               "branch" "feat/impl-x" "release_stale_holder" False}]]
    (setv ok (dict base))
    (setv (get ok "workspace_seed") good)
    (assert (= (get (build-launch-program-params ok config) "workspace_seed") good)))
  (for [bad [;; 鍵が bool でない
             {"repo" "/r" "dir" "/d" "mode" "branch" "branch" "b"
              "release_stale_holder" "yes" "protected_dirs" []}
             ;; 真なのに保護集合が無い(fail-closed)
             {"repo" "/r" "dir" "/d" "mode" "branch" "branch" "b"
              "release_stale_holder" True}
             ;; 保護集合が list でない / 相対 path を含む
             {"repo" "/r" "dir" "/d" "mode" "branch" "branch" "b"
              "release_stale_holder" True "protected_dirs" "/d"}
             {"repo" "/r" "dir" "/d" "mode" "branch" "branch" "b"
              "release_stale_holder" True "protected_dirs" ["rel/path"]}
             ;; detached に解除の鍵(branch を持たない)
             {"repo" "/r" "dir" "/d" "mode" "detached" "sha" "abc1234def"
              "release_stale_holder" True "protected_dirs" []}]]
    (setv wire (dict base))
    (setv (get wire "workspace_seed") bad)
    (setv raised None)
    (try
      (build-launch-program-params wire config)
      (except [e RuntimeError] (setv raised e)))
    (assert (is-not raised None) f"expected reject for {bad}")
    (assert (in "workspace_seed" (str raised)))))


;; ---------------------------------------------------------------------------
;; graceful shutdown の lease 釈放(issue #565)
;; ---------------------------------------------------------------------------

(defn read-lease-row [db-path]
  "外部読者として lease 行を読む(不在・未 migrate は None)。"
  (try
    (setv conn (sqlite3.connect db-path))
    (try
      (setv row (.fetchone (.execute conn
                                     (+ "SELECT owner_pid, expires_at "
                                        "FROM agent_daemon_lease "
                                        "WHERE lease_name = 'doeff-agentd'"))))
      (if (is row None)
          None
          {"owner_pid" (int (get row 0)) "expires_at" (get row 1)})
      (finally (.close conn)))
    (except [e sqlite3.OperationalError]
      None)))


(defn spawn-serve [db-path sock-path log-path]
  "実プロセスの serve を spawn する(hostmain 経由 — console script と同経路)。
   judge は明示無効(実 claude subprocess を焼かない)。"
  (setv code (+ "import sys; "
                f"sys.argv = ['doeff-sessionhost', '--db', {(json.dumps db-path)}, "
                f"'--socket', {(json.dumps sock-path)}, "
                "'--prompt-judge-cmd', '', 'serve']; "
                "from doeff_agents.sessionhost.hostmain import main; main()"))
  (setv log (open log-path "ab"))
  (try
    (subprocess.Popen [sys.executable "-c" code]
                      :stdout log :stderr subprocess.STDOUT)
    (finally (.close log))))


(defn read-log [log-path]
  (if (os.path.exists log-path)
      (with [f (open log-path "rb")]
        (.decode (.read f) "utf-8" "replace"))
      ""))


(defn wait-for-lease-owner [proc db-path deadline-seconds what log-path]
  "lease 行が proc.pid 名義になるまで poll。proc の敗死は timeout を待たず
   log を添えて即 fail(lease-conflict exit 1 の検出面)。"
  (setv deadline (+ (time.monotonic) deadline-seconds))
  (while True
    (setv rc (.poll proc))
    (when (is-not rc None)
      (raise (AssertionError
               f"{what}: daemon died rc={rc}\n--- daemon log ---\n{(read-log log-path)}")))
    (setv lease (read-lease-row db-path))
    (when (and (is-not lease None) (= (get lease "owner_pid") proc.pid))
      (return))
    (when (>= (time.monotonic) deadline)
      (raise (AssertionError
               f"timeout waiting for {what}\n--- daemon log ---\n{(read-log log-path)}")))
    (time.sleep 0.1)))


(deftest test-sigterm-releases-lease-and-successor-binds-immediately
  ;; issue #565: SIGTERM(launchctl bootout)での graceful shutdown は自
  ;; owner_pid の lease 行を釈放する。launchd KeepAlive が即 spawn する後継は
  ;; lease-conflict("doeff-agentd lease is active" exit 1)で敗死せず、TTL
  ;; 失効を待たずに 1 spawn 目で定着する。socket path は AF_UNIX 104B 上限の
  ;; ため /tmp 直下に置く。
  (setv d (tempfile.mkdtemp :dir "/tmp"))
  (setv proc None)
  (setv successor None)
  (try
    (setv db-path (os.path.join d "agentd.sqlite"))
    (setv sock-path (os.path.join d "agentd.sock"))
    (setv log-path (os.path.join d "serve.log"))
    (setv proc (spawn-serve db-path sock-path log-path))
    ;; ready = lease 行が自 pid 名義で出現(Hy import 連鎖があるので寛めに待つ)
    (wait-for-lease-owner proc db-path 30 "initial daemon lease" log-path)
    ;; SIGTERM → graceful exit(rc 0)+ lease 行の釈放
    (.send-signal proc signal.SIGTERM)
    (setv rc (.wait proc :timeout 10))
    (assert (= rc 0) f"graceful shutdown must exit 0, got {rc}")
    (assert (is (read-lease-row db-path) None)
            "lease row must be released on graceful shutdown")
    ;; 後継の即 spawn(launchd KeepAlive 相当)— 敗死せず定着する
    (setv successor (spawn-serve db-path sock-path log-path))
    (wait-for-lease-owner successor db-path 30 "successor daemon lease" log-path)
    (assert (is (.poll successor) None)
            "successor must stay alive (no lease-conflict death)")
    (finally
      (for [p [proc successor]]
        (when (and (is-not p None) (is (.poll p) None))
          (.kill p)
          (.wait p :timeout 5)))
      (shutil.rmtree d :ignore-errors True))))


;; ---------------------------------------------------------------------------
;; 遅延 result 受理(ADR-DOE-AGENTS-009 R4 — false-lost 後の救済経路)
;; ---------------------------------------------------------------------------

(defn late-result-snap [session-id #** overrides]
  "report-result-op 検証用の store-of-record 行。"
  (setv base {"session_id" session-id
              "session_name" f"doeff-{session-id}"
              "pane_id" "%1"
              "agent_type" "codex"
              "work_dir" "/tmp/w"
              "lifecycle" "run_to_completion"
              "status" "running"
              "backend_kind" "tmux"
              "backend_ref" {"session_name" f"doeff-{session-id}"
                             "pane_id" "%1"
                             "command" "codex"}
              "started_at" "2026-07-27T19:27:00+00:00"
              "last_observed_at" "2026-07-27T20:10:00+00:00"
              "finished_at" None
              "cleaned_at" None
              "pr_url" None
              "output_snippet" None
              "terminal_cause" None
              "expected_result" {"payload_schema" {"type" "object"}}
              "retries_used" 0
              "last_validation_error" None
              "awaiting_response" False
              "observed_active_at" "2026-07-27T19:27:30+00:00"
              "result_payload" None
              "result_solicitations_used" 0
              "prompt_unblock_attempts" 0
              "last_output_change_at" None})
  (.update base overrides)
  base)


(defn with-late-conn [thunk]
  (setv d (tempfile.mkdtemp))
  (try
    (setv conn (open-conn (os.path.join d "late.sqlite")))
    (db-migrate conn)
    (thunk conn)
    (finally
      (shutil.rmtree d :ignore-errors True))))


(deftest test-report-result-accepted-after-death-verdict
  ;; ADR-DOE-AGENTS-009 R4: 死亡裁定クラス(exited)+ result 未永続 + contract
  ;; 有りへの遅延 report_result は schema 検証の上で受理される — status=done・
  ;; payload 永続・terminal_cause / last_validation_error クリア。2026-07-27
  ;; wedge で実装結果が -32003 拒否により result channel 断になった実 incident
  ;; (wi_ee07c6a71fa945a5)の救済経路。
  (defn check [conn]
    (db-upsert-snapshot conn (late-result-snap "s1"
      :status "exited"
      :finished_at "2026-07-27T20:15:00+00:00"
      :last_validation_error "stale"
      :terminal_cause {"category" "vanished"
                       "reason" "tmux session disappeared"
                       "retryable" True
                       "observed_at" "2026-07-27T20:15:00+00:00"}))
    (setv response (report-result-op conn "s1" {"ok" True}))
    (assert (= (get response "accepted") True))
    (assert (= (get response "late_result") True))
    (setv snap (db-session-get conn "s1"))
    (assert (= (get snap "status") "done"))
    (assert (= (get snap "result_payload") "{\"ok\":true}"))
    (assert (is (get snap "terminal_cause") None))
    (assert (is (get snap "last_validation_error") None))
    ;; finished_at は受理時刻へ前進する(死亡刻印時刻は誤裁定の時刻)
    (assert (!= (get snap "finished_at") "2026-07-27T20:15:00+00:00")))
  (with-late-conn check))


(deftest test-late-result-schema-still-validated
  ;; R4 は受理の門を開くだけで検証は緩めない: schema 不適合は従来どおり
  ;; -32002 のまま(exited の行でも同じ)。
  (defn check [conn]
    (db-upsert-snapshot conn (late-result-snap "s1"
      :status "exited"
      :expected_result {"payload_schema" {"type" "object"
                                          "required" ["summary"]
                                          "properties" {"summary" {"type" "string"}}}}
      :terminal_cause {"category" "vanished"
                       "reason" "tmux session disappeared"
                       "retryable" True
                       "observed_at" "2026-07-27T20:15:00+00:00"}))
    (setv raised None)
    (try
      (report-result-op conn "s1" {"nope" 1})
      (except [e RpcHostError] (setv raised e)))
    (assert (is-not raised None))
    (assert (= raised.code RPC-ERR-RESULT-REJECTED))
    (setv snap (db-session-get conn "s1"))
    (assert (= (get snap "status") "exited"))
    (assert (is (get snap "result_payload") None)))
  (with-late-conn check))


(deftest test-late-result-rejected-for-judged-and-operator-verdicts
  ;; R4 の範囲限定: judged failure(failed)と操作者裁定(cancelled/stopped)
  ;; は裁定の書き換えになるため従来どおり -32003 で拒否する。
  (defn check [conn]
    (for [status ["failed" "cancelled" "stopped"]]
      (setv sid f"s-{status}")
      (db-upsert-snapshot conn (late-result-snap sid :status status))
      (setv raised None)
      (try
        (report-result-op conn sid {"ok" True})
        (except [e RpcHostError] (setv raised e)))
      (assert (is-not raised None) f"expected -32003 for {status}")
      (assert (= raised.code RPC-ERR-ALREADY-TERMINAL))))
  (with-late-conn check))


(deftest test-late-result-idempotent-when-already-reported
  ;; 終端 + payload 既永続は従来どおり idempotent already_reported(exited でも)。
  (defn check [conn]
    (db-upsert-snapshot conn (late-result-snap "s1"
      :status "exited"
      :result_payload "{\"ok\":true}"))
    (setv response (report-result-op conn "s1" {"ok" True}))
    (assert (= response {"accepted" True "already_reported" True})))
  (with-late-conn check))


;; ---------------------------------------------------------------------------
;; context_file の resume 面(law context-file-rides-the-wire — dispatch admission)
;; ---------------------------------------------------------------------------


(deftest test-dispatch-fork-rejects-context-file
  ;; fork は新会話 — 前会話の invocation 簿記(context_file)の持ち込みは
  ;; resume-only の他拡張と同じ fail-closed(黙殺は誤配線を隠す)。
  (defn check [config actor]
    (setv req {"id" 1 "method" "session.fork"
               "params" {"session_id" "s1"
                         "context_file" {"path" "x.json" "content" {}}}})
    (setv response (json.loads (dispatch-line (json.dumps req) config actor)))
    (assert (= (get response "ok") False))
    (assert (in "resume-only" (get response "error"))))
  (with-skeleton check))


(deftest test-dispatch-resume-admits-context-file-shape
  ;; resume の context_file は launch と同じ admission(裸ファイル名 +
  ;; content 必須)を通る — 語彙外は store へ触る前に loud。
  (defn check [config actor]
    (for [[bad expected] [[{"path" "a/b.json" "content" {}} "bare file name"]
                          [{"path" ".acp-context.json"} "content is required"]
                          ["not-an-object" "must be an object"]]]
      (setv req {"id" 1 "method" "session.resume"
                 "params" {"session_id" "s1" "context_file" bad}})
      (setv response (json.loads (dispatch-line (json.dumps req) config actor)))
      (setv err (get response "error"))
      (assert (= (get response "ok") False))
      (assert (in expected err) f"admission 文言が想定外: {err}")))
  (with-skeleton check))
