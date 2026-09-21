;;; 直接束縛 deftest: 実 substrate handler(DOE-004 C2)。
;;;
;;; substrate.hy は生 IO の唯一の家 — 純関数(禁止 env・paste 残留検出)は
;;; 決定的に、Fs / Env / Clock effect は tmpdir で、tmux effect は実 tmux
;;; server での smoke で検証する(tmux 不在時は skip)。oracle:
;;; agentd-rust-final:src/main.rs の ensure_no_forbidden_agent_env /
;;; output_has_unsubmitted_paste_input / tmux_* / fs 物理。

(require doeff-hy.macros [deftest defk deff <-])

(import glob)
(import os)
(import shutil)
(import threading)
(import tempfile)
(import pytest)
(import doeff [EffectBase run])

(import doeff_agents.sessionhost.effects [
  fs-canonical-path
  fs-file-mtime
  fs-read-text
  fs-write-text-atomic
  fs-ensure-symlink
  fs-make-dirs
  FS-ENSURE-SYMLINK-LINKED
  FS-ENSURE-SYMLINK-OCCUPIED
  FS-ENSURE-SYMLINK-UNCHANGED
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


(deftest test-unsubmitted-paste-attachment-chip-below-prompt
  ;; issue #568(ADR-DOE-AGENTS-010 R1): 添付チップ([Image #N] / paste チップ)
  ;; は prompt 行の直下に落ちる形がある — composer 領域(最終 prompt 行以降)で
  ;; 検出する。confirm ループも monitor と同じ盲点を持っていた。
  (assert (unsubmitted-paste-input?
            "❯\n  [Image #150]\n\n  ⏵⏵ bypass permissions on (shift+tab to cycle)"
            None))
  (assert (unsubmitted-paste-input? "❯\n  [Pasted text #1 +12 lines]" None))
  ;; 送信済み履歴(最終 prompt 行より上)は対象外のまま
  (assert (not (unsubmitted-paste-input? "❯ [Image #3]\n⏺ done\n❯ " None))))


;; ---------------------------------------------------------------------------
;; Fs / Env / Clock(実 IO、tmpdir)
;; ---------------------------------------------------------------------------

(defk drive [op]
  {:pre [(: op EffectBase)]
   :post [(: % "effect の実 substrate 解釈結果")]}
  "real-substrate で 1 effect を回す最小ドライバ。"
  (<- result ((real-substrate "tmux") op))
  result)


(defn drive-sync [op]
  "thread の中から 1 effect を回す(deftest の <- は手番の外へ持ち出せないので素の run で回す)。"
  (run ((real-substrate "tmux") op)))


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
    ;; card acp:kanban-issue:ki-62aa1f4e9c9c D9: tmp の名は書き手ごとに一意なので、綴り 1 つでなく
    ;; glob で数える(残骸ゼロの観測面は変えない)。
    (assert (= (glob.glob (+ target ".*agentd-tmp")) []))
    (assert (not (os.path.exists (+ target ".agentd-tmp"))))
    (finally
      (shutil.rmtree d :ignore-errors True))))


(deftest test-fs-file-mtime
  ;; ADR-002 R-conversation-evidence: 実在ファイルは epoch 秒(getmtime と
  ;; 一致)・不在は None(raise しない — probe は反証面であって門ではない)。
  (setv d (tempfile.mkdtemp))
  (try
    (setv target (os.path.join d "conv.jsonl"))
    ;; 不在は None
    (<- missing (drive (fs-file-mtime target)))
    (assert (is None missing))
    (with [f (open target "w")]
      (.write f "{}\n"))
    (<- mt (drive (fs-file-mtime target)))
    (assert (isinstance mt float))
    (assert (= mt (os.path.getmtime target)))
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


;; ---------------------------------------------------------------------------
;; D8 / D9(card acp:kanban-issue:ki-62aa1f4e9c9c・盲検 A の反例)
;; ---------------------------------------------------------------------------

(deftest test-fs-ensure-symlink-three-outcomes
  ;; ⚑ 受入 11: 張り替えの動詞は 3 値を返し、**別の先を指す symlink は張り替える**。
  ;; FsLinkArtifact はここで "target-conflict" を返して終わるので、それで組むと正本の path が
  ;; 変わった日に家の skills が古い先を指したまま残る(実射 = 設計の repro_A_real_substrate.py)。
  (setv d (os.path.realpath (tempfile.mkdtemp)))
  (try
    (setv old-target (os.path.join d "skills-v1"))
    (setv new-target (os.path.join d "skills-v2"))
    (os.makedirs old-target)
    (os.makedirs new-target)
    (setv link (os.path.join d "home" "skills"))
    ;; 何も居ない → 張る(親 dir も作る)
    (<- first (drive (fs-ensure-symlink link old-target)))
    (assert (= first FS-ENSURE-SYMLINK-LINKED) first)
    (assert (= (os.path.realpath link) old-target))
    ;; 同じ先 → 触らない(inode も mtime も動かさない = 走っている席の見張りを起こさない)
    (setv before (os.lstat link))
    (<- second (drive (fs-ensure-symlink link old-target)))
    (assert (= second FS-ENSURE-SYMLINK-UNCHANGED) second)
    (setv after (os.lstat link))
    (assert (= before.st-ino after.st-ino))
    ;; 別の先 → 張り替える
    (<- third (drive (fs-ensure-symlink link new-target)))
    (assert (= third FS-ENSURE-SYMLINK-LINKED) third)
    (assert (= (os.path.realpath link) new-target))
    ;; symlink でない実体が居る → 触らない(erosion guard)
    (setv occupied (os.path.join d "home" "real"))
    (with [f (open occupied "w" :encoding "utf-8")]
      (.write f "someone's real file"))
    (<- fourth (drive (fs-ensure-symlink occupied new-target)))
    (assert (= fourth FS-ENSURE-SYMLINK-OCCUPIED) fourth)
    (assert (not (os.path.islink occupied)))
    (with [f (open occupied :encoding "utf-8")]
      (assert (= (.read f) "someone's real file")))
    (finally
      (shutil.rmtree d :ignore-errors True))))


(deftest test-fs-write-text-atomic-survives-two-writers-on-one-home
  ;; ⚑ 受入 12(D9・盲検 A): 家は**同じ資格の複数 session が共有する**。固定名の tmp
  ;; (<path><suffix>)だと 2 席が同拍で書いた時に互いの書きかけを上書きし、先に rename した側の
  ;; tmp を後の側が消して FileNotFoundError で片方の書きが落ちる。tmp が書き手ごとに一意なら、
  ;; どちらも落ちず・残骸も残らず・中身は**どちらかの全文**(混ざらない)。
  (setv d (os.path.realpath (tempfile.mkdtemp)))
  (try
    (setv target (os.path.join d "CLAUDE.md"))
    (setv texts {"a" (* "α" 4096) "b" (* "β" 4096)})
    (setv failures [])
    (defn writer [name]
      (for [_ (range 120)]
        (try
          (drive-sync (fs-write-text-atomic target (get texts name) ".agentd-tmp"))
          (except [error Exception]
            (.append failures #(name (repr error)))))))
    (setv threads (lfor name ["a" "b"] (threading.Thread :target writer :args #(name))))
    (for [t threads] (.start t))
    (for [t threads] (.join t))
    (assert (= failures []) failures)
    (with [f (open target :encoding "utf-8")]
      (setv content (.read f)))
    (assert (in content (list (.values texts))) (cut content 0 32))
    (assert (= (glob.glob (+ target ".*agentd-tmp")) []) (glob.glob (+ target ".*")))
    (finally
      (shutil.rmtree d :ignore-errors True))))
