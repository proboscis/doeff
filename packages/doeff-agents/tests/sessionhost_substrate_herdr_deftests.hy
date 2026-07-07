;;; 直接束縛 deftest: herdr substrate handler(第二 substrate トライアル)。
;;;
;;; sessionhost_substrate_deftests.hy の鏡映。純関数(キー写像・チャンク分割)は
;;; 決定的に、herdr effect は実 herdr server での smoke で検証する
;;; (herdr 不在・server 停止時は skip)。物理の出典は Phase 0 実測
;;; (conformance/herdr-physics.md)。

(require doeff-hy.macros [deftest defk deff <-])

(import json)
(import os)
(import shutil)
(import socket :as socket-mod)
(import tempfile)
(import time)
(import doeff [EffectBase])

(import doeff_agents.sessionhost.effects [
  tmux-new-session
  tmux-has-session
  tmux-pane-current-command
  tmux-capture
  tmux-send-keys
  tmux-kill-session])
(import doeff_agents.sessionhost.substrate_herdr [
  DEFAULT-HERDR-SOCKET
  REQUEST-LINE-BYTE-LIMIT
  herdr-substrate
  herdr-call
  herdr-key-name
  herdr-request-line
  chunked-send-texts
  normalize-ansi-read])


(deff herdr-server-available? []
  {:pre [True]
   :post [(: % bool)]}
  "実 herdr server が socket で応答するか(deftest の skip 判定)。"
  (when (not (os.path.exists DEFAULT-HERDR-SOCKET))
    (return False))
  (setv sock (socket-mod.socket socket-mod.AF-UNIX socket-mod.SOCK-STREAM))
  (try
    (.settimeout sock 2.0)
    (.connect sock DEFAULT-HERDR-SOCKET)
    (.sendall sock b"{\"id\":\"probe\",\"method\":\"ping\",\"params\":{}}\n")
    (setv data (.recv sock 65536))
    (in b"pong" data)
    (except [Exception]
      False)
    (finally
      (.close sock))))

(setv HERDR-AVAILABLE (herdr-server-available?))


;; ---------------------------------------------------------------------------
;; 純関数(実測物理の写像)
;; ---------------------------------------------------------------------------

(deftest test-herdr-key-name-mapping
  ;; 実測: BSpace は backspace のみ受理、Home/End は非対応 → ctrl+a/ctrl+e。
  (assert (= (herdr-key-name "BSpace") "backspace"))
  (assert (= (herdr-key-name "Home") "ctrl+a"))
  (assert (= (herdr-key-name "End") "ctrl+e"))
  ;; whitelist の残り(policy.hy ALLOWED-UNBLOCK-KEY-NAMES)は素通し —
  ;; herdr が大文字小文字不問で受理することを実測済み。
  (for [key ["Up" "Down" "Left" "Right" "Enter" "Escape" "Tab" "Space" "y" "1"]]
    (assert (= (herdr-key-name key) key))))


(deftest test-chunked-send-texts-small-is-single-chunk
  (setv text "hello herdr")
  (assert (= (chunked-send-texts text) [text])))


(deftest test-chunked-send-texts-large-respects-request-line-limit
  ;; 5MB(U1 実測の byte-exact 上限確認と同サイズ)を分割し、
  ;; (1) 復元が byte-exact (2) 各チャンクの request line が上限以下、を確認。
  (setv text (* "x" (* 5 1024 1024)))
  (setv chunks (chunked-send-texts text))
  (assert (> (len chunks) 1))
  (assert (= (.join "" chunks) text))
  (for [chunk chunks]
    (assert (<= (len (herdr-request-line
                       "pane.send_text"
                       {"pane_id" "w000:p000" "text" chunk}))
                REQUEST-LINE-BYTE-LIMIT))))


(deftest test-chunked-send-texts-escape-expansion-counted
  ;; チャンク判定は JSON escape 後の実バイト長 — 改行だらけのテキスト
  ;; (\n → \\n で 2 倍膨張)でも request line が上限を超えないこと。
  ;; 生バイト基準の分割はこのケースで上限を突き破る(回帰 pin)。
  (setv text (* "y\n" (* 1 1024 1024)))  ; 生 2MiB、escape 後 ~3MiB
  (setv chunks (chunked-send-texts text))
  (assert (= (.join "" chunks) text))
  (for [chunk chunks]
    (assert (<= (len (herdr-request-line
                       "pane.send_text"
                       {"pane_id" "w000:p000" "text" chunk}))
                REQUEST-LINE-BYTE-LIMIT))))


(deftest test-normalize-ansi-read-preserves-trailing-space
  ;; pane.read format=ansi の実測形(2026-07-07): SGR 込み grid 再構成、
  ;; 行区切り \r\n、trailing space は SGR の内側(`\x1b[1m› \x1b[0m`)。
  ;; strip 後に "\n› "(trailing space 込み)が残ることが codex idle prompt
  ;; 検出(impls/markers.hy has-idle-prompt)の前提 — S18 ハングの回帰 pin。
  (setv raw "\x1b[0m\x1b[38;5;1mRED\x1b[0m plain\r\n\r\n\x1b[0m\x1b[1m› \x1b[0m")
  (assert (= (normalize-ansi-read raw) "RED plain\n\n› "))
  ;; OSC(BEL / ST 終端)と 2 文字 ESC も剥がす(grid 再構成には現れない
  ;; はずだが防御的に被覆)。
  (assert (= (normalize-ansi-read "\x1b]0;title\x07text\x1b]2;t\x1b\\tail\x1b7")
             "texttail"))
  ;; plain テキストは素通し(冪等)。
  (assert (= (normalize-ansi-read "no escapes here ") "no escapes here ")))


;; ---------------------------------------------------------------------------
;; herdr smoke(実 herdr server — 不在時 skip)
;; ---------------------------------------------------------------------------

(deftest test-herdr-lifecycle-smoke
  {:skip-if (not HERDR-AVAILABLE)
   :skip-reason "herdr server not running"}
  (setv d (tempfile.mkdtemp))
  (setv session-name f"doeff-herdr-smoke-{(os.getpid)}")
  (try
    ;; new-session → has-session(True)→ capture → current-command →
    ;; kill → has-session(False)— tmux 版 deftest と同じ寿命 parity。
    (<- pane ((herdr-substrate DEFAULT-HERDR-SOCKET)
              (tmux-new-session session-name d {"DOEFF_PROBE" "deftest"})))
    (assert (in ":p" pane))
    (<- alive ((herdr-substrate DEFAULT-HERDR-SOCKET)
               (tmux-has-session session-name)))
    (assert alive)
    (<- captured ((herdr-substrate DEFAULT-HERDR-SOCKET)
                  (tmux-capture pane 10)))
    (assert (isinstance captured str))
    ;; 素の shell pane の foreground は idle shell(zombie 判定の観測面)。
    ;; ただし作成直後は shell rc の子(mkdir / scutil 等)が一時的に
    ;; foreground に立つ(2026-07-07 実測 flake)ので eventual assert —
    ;; monitor loop も周期観測なので一時 foreground を誤判定しない。
    (setv shells ["zsh" "bash" "sh" "dash" "fish" "ksh"])
    (setv deadline (+ (time.monotonic) 5.0))
    (setv cmd None)
    (setv probing True)
    (while probing
      (<- probed ((herdr-substrate DEFAULT-HERDR-SOCKET)
                  (tmux-pane-current-command pane)))
      (setv cmd probed)
      (if (or (in cmd shells) (> (time.monotonic) deadline))
          (setv probing False)
          (time.sleep 0.1)))
    (assert (in cmd shells))
    (<- _ ((herdr-substrate DEFAULT-HERDR-SOCKET)
           (tmux-kill-session session-name)))
    (<- gone ((herdr-substrate DEFAULT-HERDR-SOCKET)
              (tmux-has-session session-name)))
    (assert (not gone))
    (finally
      (shutil.rmtree d :ignore-errors True))))


(deftest test-herdr-new-session-rejects-forbidden-env
  {:skip-if (not HERDR-AVAILABLE)
   :skip-reason "herdr server not running"}
  ;; 禁止 env reject は substrate 所有 — herdr 束縛でも同じ guard が効く
  ;; (substrate.hy の ensure-no-forbidden-agent-env を共有)。
  (setv raised None)
  (try
    (<- _ ((herdr-substrate DEFAULT-HERDR-SOCKET)
           (tmux-new-session "doeff-herdr-forbidden" "/tmp"
                             {"ANTHROPIC_API_KEY" "leak"})))
    (except [e RuntimeError] (setv raised e)))
  (assert (is-not raised None))
  (assert (in "Anthropic API keys" (str raised))))


(deftest test-herdr-duplicate-session-rejected
  {:skip-if (not HERDR-AVAILABLE)
   :skip-reason "herdr server not running"}
  ;; tmux new-session の duplicate 拒否 parity — herdr は agent_name_taken を
  ;; ネイティブに返す(Phase 0 実測)。
  (setv d (tempfile.mkdtemp))
  (setv session-name f"doeff-herdr-dup-{(os.getpid)}")
  (try
    (<- _ ((herdr-substrate DEFAULT-HERDR-SOCKET)
           (tmux-new-session session-name d {})))
    (setv raised None)
    (try
      (<- _ ((herdr-substrate DEFAULT-HERDR-SOCKET)
             (tmux-new-session session-name d {})))
      (except [e RuntimeError] (setv raised e)))
    (assert (is-not raised None))
    (assert (in "agent_name_taken" (str raised)))
    (finally
      (try
        (<- _ ((herdr-substrate DEFAULT-HERDR-SOCKET)
               (tmux-kill-session session-name)))
        (except [Exception]))
      (shutil.rmtree d :ignore-errors True))))


(deftest test-herdr-new-session-owns-sole-pane-workspace
  {:skip-if (not HERDR-AVAILABLE)
   :skip-reason "herdr server not running"}
  ;; 幾何学 parity pin(実測 2026-07-07): herdr agent.start の既定配置は
  ;; 「現在 workspace の active tab への split」で、pane 幅が既存 pane 数に
  ;; 反比例して劣化する。幅 ~10 桁の pane では claude bypass-permissions
  ;; dialog が単語の途中で折返され("Bypass\n  Permi\n  ssions\n  mode")、
  ;; markers.hy の部分文字列一致 oracle(has-claude-bypass-dialog 等)が
  ;; 構造的に全滅 → wait-for-repl-idle が 120s 上限まで縮退し session が
  ;; 死ぬ(実 claude E2E で実測)。tmux new-session は常に独立 window
  ;; (フル幅 grid)なので、herdr 束縛は「専用 workspace の唯一 pane」で
  ;; 同じ幾何学を保証する義務を負う。
  (setv d (tempfile.mkdtemp))
  (setv session-name f"doeff-herdr-geom-{(os.getpid)}")
  (try
    (<- pane ((herdr-substrate DEFAULT-HERDR-SOCKET)
              (tmux-new-session session-name d {})))
    (setv ws-id (get (.split pane ":") 0))
    (setv listing (herdr-call DEFAULT-HERDR-SOCKET "pane.list"
                              {"workspace_id" ws-id}))
    (setv panes (get listing "panes"))
    (assert (= (len panes) 1)
            f"session pane must be the sole pane of its workspace (full width), got {(len panes)}: {panes}")
    (assert (= (get (get panes 0) "pane_id") pane))
    (finally
      (try
        (<- _ ((herdr-substrate DEFAULT-HERDR-SOCKET)
               (tmux-kill-session session-name)))
        (except [Exception]))
      (shutil.rmtree d :ignore-errors True))))


(deftest test-herdr-capture-preserves-trailing-space
  {:skip-if (not HERDR-AVAILABLE)
   :skip-reason "herdr server not running"}
  ;; S18-herdr ハングの根本原因 pin: herdr pane.read format=text は行末
  ;; スペースをトリムする(実測 2026-07-07)が、tmux capture-pane -J は保持
  ;; する(man tmux)。codex idle prompt marker は trailing space 込みの
  ;; "\n› " 部分文字列一致(impls/markers.hy has-idle-prompt)なので、
  ;; トリムされると wait-for-repl-idle が構造的に idle を検出できず
  ;; launch が 120s 上限まで poll し続ける。substrate は format=ansi 読み +
  ;; SGR strip で tmux parity(trailing space 保持)を復元する義務を負う。
  ;; printf 後に sleep で前景プロセスを保持するのは、shell prompt が同じ
  ;; 行に続いてスペースが「行中」になり誤 green になるのを防ぐため
  ;; (codex idle frame と同じ「前景が trailing-space 行を描いて待つ」物理)。
  (import time)
  (setv d (tempfile.mkdtemp))
  (setv session-name f"doeff-herdr-tspace-{(os.getpid)}")
  (try
    (<- pane ((herdr-substrate DEFAULT-HERDR-SOCKET)
              (tmux-new-session session-name d {})))
    (time.sleep 1.0)
    (<- _ ((herdr-substrate DEFAULT-HERDR-SOCKET)
           (tmux-send-keys
             pane
             "printf '\\033[31mRED\\033[0m\\nPROMPTMARK '; sleep 30"
             True True)))
    (setv captured "")
    (setv found False)
    (for [_ (range 10)]
      (time.sleep 0.5)
      (<- got ((herdr-substrate DEFAULT-HERDR-SOCKET)
               (tmux-capture pane 50)))
      (setv captured got)
      (when (in "\nPROMPTMARK " captured)
        (setv found True)
        (break)))
    (assert found
            f"trailing space not preserved in capture tail: {(repr (cut captured -80 None))}")
    ;; ansi 読みの strip 義務: 生 CSI/SGR が capture に漏れないこと。
    (assert (not-in "\x1b[" captured))
    (assert (in "RED" captured))
    (finally
      (try
        (<- _ ((herdr-substrate DEFAULT-HERDR-SOCKET)
               (tmux-kill-session session-name)))
        (except [Exception]))
      (shutil.rmtree d :ignore-errors True))))


(deftest test-herdr-send-and-capture-roundtrip
  {:skip-if (not HERDR-AVAILABLE)
   :skip-reason "herdr server not running"}
  ;; literal paste + submit → capture に出力が現れる(Enter submit の実効と
  ;; capture の scrollback-empty fallback(visible)の両方を通る)。
  (import time)
  (setv d (tempfile.mkdtemp))
  (setv session-name f"doeff-herdr-echo-{(os.getpid)}")
  (setv marker f"HERDR-DEFTEST-{(os.getpid)}")
  (try
    (<- pane ((herdr-substrate DEFAULT-HERDR-SOCKET)
              (tmux-new-session session-name d {})))
    ;; shell 起動を待ってから送る(fresh pane は prompt 描画前がある)。
    (time.sleep 1.0)
    (<- _ ((herdr-substrate DEFAULT-HERDR-SOCKET)
           (tmux-send-keys pane f"echo {marker}" True True)))
    (setv found False)
    (for [_ (range 10)]
      (time.sleep 0.5)
      (<- captured ((herdr-substrate DEFAULT-HERDR-SOCKET)
                    (tmux-capture pane 50)))
      (when (in marker captured)
        (setv found True)
        (break)))
    (assert found "echo marker did not appear in herdr capture")
    (finally
      (try
        (<- _ ((herdr-substrate DEFAULT-HERDR-SOCKET)
               (tmux-kill-session session-name)))
        (except [Exception]))
      (shutil.rmtree d :ignore-errors True))))
