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
  tmux-kill-session])
(import doeff_agents.sessionhost.substrate [
  real-substrate
  normalized-env-key
  ensure-no-forbidden-agent-env
  unsubmitted-paste-input?])


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
