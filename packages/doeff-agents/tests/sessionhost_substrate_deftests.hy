;;; 直接束縛 deftest: 実 substrate handler(DOE-004 C2)。
;;;
;;; substrate.hy は生 IO の唯一の家 — 純関数(禁止 env・paste 残留検出)は
;;; 決定的に、Fs / Env / Clock effect は tmpdir で、tmux effect は実 tmux
;;; server での smoke で検証する(tmux 不在時は skip)。oracle:
;;; packages/doeff-agentd/src/main.rs の ensure_no_forbidden_agent_env /
;;; output_has_unsubmitted_paste_input / tmux_* / fs 物理。

(require doeff-hy.macros [deftest defk deff <-])

(import os)
(import shutil)
(import tempfile)
(import pytest)
(import doeff [EffectBase])

(import doeff_agents.sessionhost.effects [
  fs-canonical-path
  fs-read-text
  fs-write-text-atomic
  fs-make-dirs
  env-get
  clock-now
  tmux-new-session
  tmux-has-session
  tmux-capture
  tmux-send-keys
  tmux-kill-session])
(import doeff_agents.sessionhost.substrate [
  real-substrate
  normalized-env-key
  ensure-no-forbidden-agent-env
  unsubmitted-paste-input?
  compose-home-view
  compose-home-view-name])


;; ---------------------------------------------------------------------------
;; 純関数(oracle verbatim)
;; ---------------------------------------------------------------------------

(deftest test-normalized-env-key
  (assert (= (normalized-env-key "anthropic-api-key") "ANTHROPIC_API_KEY"))
  (assert (= (normalized-env-key "CODEX_HOME") "CODEX_HOME")))


(deftest test-forbidden-env-reject
  ;; alias 3 種すべて reject(oracle FORBIDDEN_AGENT_ENV_KEYS)
  (for [key ["ANTHROPIC_API_KEY" "anthropic_api_key_personal"
             "ANTHROPIC-API-KEY--PERSONAL"]]
    (setv raised None)
    (try
      (ensure-no-forbidden-agent-env {key "secret" "CODEX_HOME" "/x"})
      (except [e RuntimeError] (setv raised e)))
    (assert (is-not raised None) f"expected reject for {key}")
    (assert (in "Anthropic API keys" (str raised))))
  ;; 通常 env は通す
  (assert (is None (ensure-no-forbidden-agent-env {"CODEX_HOME" "/x" "PATH" "/bin"}))))


(deftest test-unsubmitted-paste-collapsed-marker
  ;; collapsed paste marker(sent-text 無し = monitor 面)
  (assert (unsubmitted-paste-input? "❯ [Pasted text +40 lines]" None))
  (assert (unsubmitted-paste-input? "› [Pasted Content 2KB]" None))
  (assert (not (unsubmitted-paste-input? "❯ " None)))
  ;; idle prompt に普通のテキストは残留ではない
  (assert (not (unsubmitted-paste-input? "❯ hello" None))))


(deftest test-unsubmitted-paste-visible-fragment
  ;; 送出テキストの断片が prompt 領域に可視のまま(confirm 面 = sent-text あり)
  (setv sent "please refactor the authentication module thoroughly")
  (assert (unsubmitted-paste-input?
            "❯ please refactor the authentication module thoroughly" sent))
  ;; 送信済みで prompt がクリアなら残留ではない
  (assert (not (unsubmitted-paste-input? "⏺ working\n❯ " sent))))


;; ---------------------------------------------------------------------------
;; Fs / Env / Clock(実 IO、tmpdir)
;; ---------------------------------------------------------------------------

(defk drive [op]
  {:pre [(: op EffectBase)]
   :post [(: % "effect の実 substrate 解釈結果")]}
  "real-substrate で 1 effect を回す最小ドライバ。"
  (<- result ((real-substrate "tmux") op))
  result)


(deftest test-fs-write-atomic-and-read
  (setv d (tempfile.mkdtemp))
  (try
    (setv nested (os.path.join d "a" "b"))
    (<- _ (drive (fs-make-dirs nested)))
    (assert (os.path.isdir nested))
    (setv target (os.path.join nested "state.json"))
    ;; 不存在は None
    (<- missing (drive (fs-read-text target)))
    (assert (is None missing))
    ;; atomic write → read round-trip・tmp 残骸不在
    (<- _ (drive (fs-write-text-atomic target "{\"ok\": true}" ".agentd-tmp")))
    (<- content (drive (fs-read-text target)))
    (assert (= content "{\"ok\": true}"))
    (assert (not (os.path.exists (+ target ".agentd-tmp"))))
    (finally
      (shutil.rmtree d :ignore-errors True))))


(deftest test-fs-canonical-and-env
  ;; realpath は symlink / .. を解決(tmpdir 自体が macOS では /var→/private/var
  ;; の symlink なので基準も realpath 済みにする)
  (setv d (os.path.realpath (tempfile.mkdtemp)))
  (try
    (<- canonical (drive (fs-canonical-path (os.path.join d "x" ".." "y"))))
    (assert (= canonical (os.path.join d "y")))
    (finally
      (shutil.rmtree d :ignore-errors True)))
  ;; env 読み(存在キー・不存在キー)
  (setv key "DOEFF_SESSIONHOST_TEST_ENV")
  (setv (get os.environ key) "present")
  (try
    (<- got (drive (env-get key)))
    (assert (= got "present"))
    (<- absent (drive (env-get "DOEFF_SESSIONHOST_ABSENT_XYZ")))
    (assert (is None absent))
    (finally
      (del (get os.environ key)))))


(deftest test-clock-now-is-utc-aware
  (<- now (drive (clock-now)))
  (assert (is-not now.tzinfo None))
  (assert (= (.total-seconds (.utcoffset now)) 0.0)))


;; ---------------------------------------------------------------------------
;; home view の合成(#15 FsComposeHomeView — apps ensure-agent-home の後継)
;; ---------------------------------------------------------------------------

(deff _compose-fixture []
  {:pre []
   :post [(: % dict)]}
  "tmpdir に profile bundle(config.toml + legacy auth.json + mcp.json)と
   auth file を敷く。"
  (setv d (os.path.realpath (tempfile.mkdtemp)))
  (setv profile (os.path.join d "bundle"))
  (os.makedirs profile)
  (with [f (open (os.path.join profile "config.toml") "w")]
    (.write f "model = \"gpt\"\n"))
  (with [f (open (os.path.join profile "mcp.json") "w")]
    (.write f "{}"))
  ;; bundle 内の legacy auth.json は合成で無視される(宣言 auth_file が勝つ)
  (with [f (open (os.path.join profile "auth.json") "w")]
    (.write f "{\"legacy\": true}"))
  (setv auth (os.path.join d "company-auth.json"))
  (with [f (open auth "w")]
    (.write f "{}"))
  {"root" d "profile" profile "auth" auth
   "view-root" (os.path.join d "agent-homes")})


(deftest test-compose-home-view-materializes-and-is-idempotent
  ;; 全 symlink の view・決定的命名(basename--hash8)・sessions は bundle 側・
  ;; 2 回目も同一 view に収束(level-triggered 再 ensure)。
  (setv fx (_compose-fixture))
  (try
    (setv view (compose-home-view (get fx "auth") (get fx "profile")
                                  (get fx "view-root")))
    (assert (= view (os.path.join (get fx "view-root")
                                  (compose-home-view-name (get fx "auth")
                                                          (get fx "profile")))))
    (assert (.startswith (os.path.basename view) "bundle--"))
    ;; auth.json は宣言 auth_file へ(bundle の legacy auth.json ではない)
    (assert (os.path.islink (os.path.join view "auth.json")))
    (assert (= (os.readlink (os.path.join view "auth.json")) (get fx "auth")))
    ;; config.toml は copy でなく symlink のまま(trust は canonicalize 書きで
    ;; bundle に届く — per-view trust という意味論変更を持ち込まない)
    (assert (os.path.islink (os.path.join view "config.toml")))
    (assert (= (os.readlink (os.path.join view "config.toml"))
               (os.path.join (get fx "profile") "config.toml")))
    ;; sessions は bundle 側に掘られ view から symlink(profile 単位で共有)
    (assert (os.path.isdir (os.path.join (get fx "profile") "sessions")))
    (assert (os.path.islink (os.path.join view "sessions")))
    ;; 冪等
    (setv again (compose-home-view (get fx "auth") (get fx "profile")
                                    (get fx "view-root")))
    (assert (= again view))
    (finally
      (shutil.rmtree (get fx "root") :ignore-errors True))))


(deftest test-compose-home-view-fails-loud
  ;; 実在検証の単一の家(登録時検証の launch-time 移設、ACP 0040 R2 改訂):
  ;; auth_file / profile_dir の不在は typed fail。erosion guard: symlink で
  ;; あるべき場所の実ファイルは黙って置換しない。
  (setv fx (_compose-fixture))
  (try
    ;; auth 不在
    (setv raised None)
    (try
      (compose-home-view (os.path.join (get fx "root") "nope.json")
                         (get fx "profile") (get fx "view-root"))
      (except [e RuntimeError] (setv raised e)))
    (assert (in "auth_file does not resolve" (str raised)))
    ;; profile 不在
    (setv raised None)
    (try
      (compose-home-view (get fx "auth")
                         (os.path.join (get fx "root") "nodir")
                         (get fx "view-root"))
      (except [e RuntimeError] (setv raised e)))
    (assert (in "profile_dir does not resolve" (str raised)))
    ;; erosion guard: view 内の config.toml を実ファイル化してから再合成
    (setv view (compose-home-view (get fx "auth") (get fx "profile")
                                  (get fx "view-root")))
    (setv link (os.path.join view "config.toml"))
    (os.unlink link)
    (with [f (open link "w")]
      (.write f "forked = true\n"))
    (setv raised None)
    (try
      (compose-home-view (get fx "auth") (get fx "profile")
                         (get fx "view-root"))
      (except [e RuntimeError] (setv raised e)))
    (assert (in "refusing to overwrite" (str raised)))
    (finally
      (shutil.rmtree (get fx "root") :ignore-errors True))))


;; ---------------------------------------------------------------------------
;; tmux smoke(実 tmux server — 不在時 skip)
;; ---------------------------------------------------------------------------

(deftest test-tmux-lifecycle-smoke
  {:skip-if (is None (shutil.which "tmux"))
   :skip-reason "tmux not installed"}
  (setv tmux (shutil.which "tmux"))
  (setv d (tempfile.mkdtemp))
  (setv session-name f"doeff-sessionhost-smoke-{(os.getpid)}")
  (try
    ;; new-session → has-session(True)→ capture → kill → has-session(False)
    (<- pane ((real-substrate tmux)
              (tmux-new-session session-name d {"CODEX_HOME" "/x"})))
    (assert (.startswith pane "%"))
    (<- alive ((real-substrate tmux) (tmux-has-session session-name)))
    (assert alive)
    (<- captured ((real-substrate tmux) (tmux-capture pane 10)))
    (assert (isinstance captured str))
    (<- _ ((real-substrate tmux) (tmux-kill-session session-name)))
    (<- gone ((real-substrate tmux) (tmux-has-session session-name)))
    (assert (not gone))
    (finally
      ;; 念のため後始末(kill 済みでも冪等に)
      (os.system f"{tmux} kill-session -t {session-name} 2>/dev/null")
      (shutil.rmtree d :ignore-errors True))))


(deftest test-tmux-new-session-rejects-forbidden-env
  {:skip-if (is None (shutil.which "tmux"))
   :skip-reason "tmux not installed"}
  (setv tmux (shutil.which "tmux"))
  (setv raised None)
  (try
    (<- _ ((real-substrate tmux)
           (tmux-new-session "doeff-forbidden" "/tmp"
                             {"ANTHROPIC_API_KEY" "leak"})))
    (except [e RuntimeError] (setv raised e)))
  (assert (is-not raised None))
  (assert (in "Anthropic API keys" (str raised))))


(deftest test-tmux-paste-survives-imsg-command-limit
  {:skip-if (is None (shutil.which "tmux"))
   :skip-reason "tmux not installed"}
  ;; tmux の client-server protocol は 1 コマンド ~16KB(imsg framing)。
  ;; set-buffer の argv 渡しは 16KB 超の paste で "command too long" になり
  ;; launch が必ず落ちる(argus attend prompt の成長で live 実測、oracle
  ;; 33ab4bae と同修正)。pin は「20KB literal paste が例外なく完了する」
  ;; こと(旧実装は RuntimeError で即死)。pane 内容の assert はしない —
  ;; 表示は受け手の line-editor / canonical-mode 物理(agent 所有)で、
  ;; 実配送は live(17KB attend prompt の全文受領)で実証済み。
  (setv tmux (shutil.which "tmux"))
  (setv d (tempfile.mkdtemp))
  (setv session-name f"doeff-sessionhost-bigpaste-{(os.getpid)}")
  (setv message (+ (* "x" 20000) " BIGPASTE-END-MARKER"))
  (try
    (<- pane ((real-substrate tmux)
              (tmux-new-session session-name d {})))
    ;; submit=False: paste のみ(confirm ループの Enter 物理はここでは無関係)。
    ;; 旧 set-buffer 実装ではこの行が "tmux set-buffer failed" で raise する。
    (<- _ ((real-substrate tmux) (tmux-send-keys pane message True False)))
    (finally
      (os.system f"{tmux} kill-session -t {session-name} 2>/dev/null")
      (shutil.rmtree d :ignore-errors True))))
