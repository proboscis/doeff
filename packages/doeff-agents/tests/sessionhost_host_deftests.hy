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
(import socket)
(import tempfile)

(import doeff_agents.sessionhost.host [
  HostConfig
  DEFAULT-PROMPT-JUDGE-CMD
  build-launch-program-params
  parse-args
  default-db-path
  default-socket-path
  ok-response
  err-response
  dispatch-line
  prepare-socket-path])
(import doeff_agents.sessionhost.store [StoreActor db-acquire-lease])


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


(deftest test-parse-args-rejects
  (for [[args fragment]
        [[["--frobnicate"] "unknown argument"]
         [["--tmux"] "--tmux requires a value"]
         [["--prompt-stall-secs" "0"] "must be positive"]
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
