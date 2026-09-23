;;; 直接束縛 deftest: 実 substrate handler(DOE-004 C2)。
;;;
;;; substrate.hy は生 IO の唯一の家 — 純関数(禁止 env・paste 残留検出)は
;;; 決定的に、Fs / Env / Clock effect は tmpdir で、tmux effect は実 tmux
;;; server での smoke で検証する(tmux 不在時は skip)。oracle:
;;; agentd-rust-final:src/main.rs の ensure_no_forbidden_agent_env /
;;; output_has_unsubmitted_paste_input / tmux_* / fs 物理。

(require doeff-hy.macros [deftest defk deff <-])

(import errno)
(import glob)
(import mmap)
(import os)
(import select)
(import shutil)
(import signal)
(import threading)
(import tempfile)
(import time)
(import pytest)
(import doeff [EffectBase run])
(import sessionhost_launch_deftests [LaunchWorld fake-launch-substrate])

(import doeff_agents.sessionhost.effects [
  fs-canonical-path
  fs-file-mtime
  fs-read-text
  fs-write-text-atomic
  fs-ensure-symlink
  fs-make-dirs
  fs-link-artifact
  FsSymlinkOutcome
  FS-ENSURE-SYMLINK-STATES
  FS-LINK-ARTIFACT-STATES
  FS-SYMLINK-LINKED
  FS-SYMLINK-OCCUPIED
  FS-SYMLINK-REFUSED
  FS-SYMLINK-SAME-ENTITY
  FS-SYMLINK-SOURCE-MISSING
  FS-SYMLINK-TARGET-CONFLICT
  FS-SYMLINK-UNCHANGED
  env-get
  clock-now
  tmux-new-session
  tmux-has-session
  tmux-capture
  tmux-send-keys
  tmux-kill-session])
(import doeff_agents.sessionhost.substrate :as substrate)
(import doeff_agents.sessionhost.substrate [
  real-substrate
  ensure-symlink-outcome
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
                                  (! (compose-home-view-name (get fx "auth")
                                                             (get fx "profile"))))))
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
    (<- first ((real-substrate "tmux") (fs-ensure-symlink link old-target)))
    (assert (= (. first state) FS-SYMLINK-LINKED) first)
    (assert (= (os.path.realpath link) old-target))
    ;; 同じ先 → 触らない(inode も mtime も動かさない = 走っている席の見張りを起こさない)
    (setv before (os.lstat link))
    (<- second ((real-substrate "tmux") (fs-ensure-symlink link old-target)))
    (assert (= (. second state) FS-SYMLINK-UNCHANGED) second)
    (setv after (os.lstat link))
    (assert (= before.st-ino after.st-ino))
    ;; 別の先 → 張り替える
    (<- third ((real-substrate "tmux") (fs-ensure-symlink link new-target)))
    (assert (= (. third state) FS-SYMLINK-LINKED) third)
    (assert (= (os.path.realpath link) new-target))
    ;; symlink でない実体が居る → 触らない(erosion guard)
    (setv occupied (os.path.join d "home" "real"))
    (with [f (open occupied "w" :encoding "utf-8")]
      (.write f "someone's real file"))
    (<- fourth ((real-substrate "tmux") (fs-ensure-symlink occupied new-target)))
    (assert (= (. fourth state) FS-SYMLINK-OCCUPIED) fourth)
    (assert (not (os.path.islink occupied)))
    (with [f (open occupied :encoding "utf-8")]
      (assert (= (.read f) "someone's real file")))
    (finally
      (shutil.rmtree d :ignore-errors True))))


;; ---------------------------------------------------------------------------
;; D8 の同拍 — 本物の 2 process(card acp:kanban-issue:ki-62aa1f4e9c9c・計画段の実測
;; evidence/symlink_install_race.log)
;; ---------------------------------------------------------------------------
;;
;; ⚠ 逐次の 3 値の検(test-fs-ensure-symlink-three-outcomes)では競りは見えない。家は
;;   **資格ごと**に鋳られ、同じ資格の複数の席が 1 つの家を同時に読む(pod の実測: 1 つの家を
;;   3 会話が共有・手番の最中に新しい家が鋳られる)。pool の入れ替えの直後は排水中に溜まった
;;   郵便が一斉に手番になるので、空の家へ同じ拍で複数の席が降りる。
;; ⚠ 待ち合わせに lock を 1 つも取らない(匿名の共有 mmap へ自分の byte だけ書き、相手の byte を
;;   回して待つ)。fork の子の中で lock を取ると、親が fork した瞬間に別の thread が握っていた
;;   lock をそのまま引き継いで子が固まる。thread では拍が揃わず競りが再現しないので、
;;   process で撃つこと自体が検の本体。
;; ⚠ 子は raw の ensure-symlink-outcome を撃つ(物理の点そのもの)。effect の層はこの関数へ
;;   委ねる 1 行なので、効果の経路は引き続き 3 値の検が見張る。

(defn race-spin-barrier [shared index total deadline]
  "同拍の待ち合わせ: 自分の byte だけ書き、全員の byte が立つまで回る(lock を取らない)。"
  (setv (get shared index) 1)
  (while (any (gfor i (range total) (= (get shared i) 0)))
    (when (> (time.monotonic) deadline)
      (return False)))
  True)


(defn race-child-installs [shared index link target write-fd]
  "子: 同拍で 1 回だけ張り、結果を 1 行で親へ返して即座に落ちる(**戻らない**)。"
  (try
    (race-spin-barrier shared index 2 (+ (time.monotonic) 30))
    ;; str(outcome) は state(理由が在れば state (detail))— 旧来の 1 行の綴りのまま。
    (os.write write-fd (.encode f"ok:{(ensure-symlink-outcome link target)}" "utf-8"))
    (except [error BaseException]
      (try
        (os.write write-fd (.encode f"err:{(. (type error) __name__)}" "utf-8"))
        (except [OSError] None)))
    (finally
      (os._exit 0))))


(defn race-child-reads [link stop started write-fd]
  "子: 根が在るかを回して読み続け、親が stop を立てたら #(読んだ回数 根の無かった回数) を返す
   (**戻らない**)。"
  (try
    (setv reads 0)
    (setv missing 0)
    (setv deadline (+ (time.monotonic) 60))
    (setv (get started 0) 1)
    (while (and (= (get stop 0) 0) (< (time.monotonic) deadline))
      (setv reads (+ reads 1))
      (when (not (os.path.lexists link))
        (setv missing (+ missing 1))))
    (os.write write-fd (.encode f"{reads}:{missing}" "utf-8"))
    (except [error BaseException]
      (try
        (os.write write-fd (.encode f"err:{(. (type error) __name__)}" "utf-8"))
        (except [OSError] None)))
    (finally
      (os._exit 0))))


(defn race-read-line [read-fd deadline]
  "子の 1 行を締切つきで受け取る(子が固まっても suite を止めない)。"
  (setv chunks [])
  (while True
    (setv remaining (- deadline (time.monotonic)))
    (when (<= remaining 0)
      (return "timeout:"))
    (setv ready (get (select.select [read-fd] [] [] remaining) 0))
    (when (not ready)
      (return "timeout:"))
    (setv chunk (os.read read-fd 256))
    (when (not chunk)
      (return (.join "" chunks)))
    (.append chunks (.decode chunk "utf-8"))))


(defn race-reap [pids deadline]
  "子を締切つきで回収する(固まっていたら殺してから回収 — 孤児を残さない)。"
  (for [pid pids]
    (while True
      (setv [done #* _] (os.waitpid pid os.WNOHANG))
      (when (!= done 0)
        (break))
      (when (> (time.monotonic) deadline)
        (try
          (os.kill pid signal.SIGKILL)
          (except [OSError] None))
        (os.waitpid pid 0)
        (break))
      (time.sleep 0.001))))


(deftest test-fs-ensure-symlink-survives-two-seats-landing-on-one-empty-home
  ;; ⚑ 受入 11(D8 の同拍・A): 空の家へ 2 席が同拍で張ると、素の symlink / unlink→symlink の
  ;; 2 手は **片方が FileExistsError で落ちる**(直す前の実測: 会社 Mac 200/200・この pod
  ;; 187/200)。launch の効果は raise するので、その席は起きない。張りが rename 1 手なら
  ;; どちらも落ちず、根は常に正しい先を指す。
  (setv d (os.path.realpath (tempfile.mkdtemp)))
  (try
    (setv target (os.path.join d "skills-src"))
    (os.makedirs target)
    (setv failures [])
    (setv wrong [])
    (setv residue [])
    (for [round-index (range 200)]
      (setv home (os.path.join d f"home-{round-index}"))
      (os.makedirs home)
      (setv link (os.path.join home "skills"))
      (setv shared (mmap.mmap -1 2))
      (setv (get shared 0) 0)
      (setv (get shared 1) 0)
      (setv read-fds [])
      (setv pids [])
      (for [index (range 2)]
        (setv [read-fd write-fd] (os.pipe))
        (setv pid (os.fork))
        (when (= pid 0)
          (os.close read-fd)
          (race-child-installs shared index link target write-fd))
        (os.close write-fd)
        (.append read-fds read-fd)
        (.append pids pid))
      (setv deadline (+ (time.monotonic) 60))
      (for [read-fd read-fds]
        (setv line (race-read-line read-fd deadline))
        (os.close read-fd)
        (when (not-in line ["ok:linked" "ok:unchanged"])
          (.append failures #(round-index line))))
      (race-reap pids deadline)
      (.close shared)
      (when (not (and (os.path.islink link) (= (os.readlink link) target)))
        (.append wrong round-index))
      ;; 仮の名は rename で消えるので、家に残るのは根 1 つだけ(残骸を残さない)
      (when (!= (os.listdir home) ["skills"])
        (.append residue #(round-index (os.listdir home)))))
    (assert (= failures []) f"同拍で落ちた席 {(len failures)} 件: {(cut failures 0 8)}")
    (assert (= wrong []) f"根が正しい先を指さない回: {(cut wrong 0 8)}")
    (assert (= residue []) f"家に残骸が残った回: {(cut residue 0 8)}")
    (finally
      (shutil.rmtree d :ignore-errors True))))


(deftest test-fs-ensure-symlink-never-shows-a-reader-a-rootless-moment
  ;; ⚑ 受入 11(D8 の同拍・B): unlink→symlink の 2 手は、その間に読んだ席へ **根の無い瞬間**を
  ;; 見せる(直す前の実測: 会社 Mac 52 %・この pod 53.0 %)。本体の skills の discovery が
  ;; そこへ当たると、その席の user 層の skills は 0 件。rename なら読み手は常に古い先か
  ;; 新しい先のどちらかを見る。
  (setv d (os.path.realpath (tempfile.mkdtemp)))
  (try
    (setv first-target (os.path.join d "skills-v1"))
    (setv second-target (os.path.join d "skills-v2"))
    (os.makedirs first-target)
    (os.makedirs second-target)
    (setv link (os.path.join d "home" "skills"))
    (ensure-symlink-outcome link first-target)
    (setv stop (mmap.mmap -1 1))
    (setv started (mmap.mmap -1 1))
    (setv (get stop 0) 0)
    (setv (get started 0) 0)
    (setv [read-fd write-fd] (os.pipe))
    (setv pid (os.fork))
    (when (= pid 0)
      (os.close read-fd)
      (race-child-reads link stop started write-fd))
    (os.close write-fd)
    (setv deadline (+ (time.monotonic) 60))
    ;; 読み手が回り始めてから張り替える(読み 0 回のまま緑になる筋を塞ぐ)
    (while (and (= (get started 0) 0) (< (time.monotonic) deadline))
      (time.sleep 0.001))
    (setv writer-failures [])
    (for [flip (range 3000)]
      (try
        (ensure-symlink-outcome link (if (% flip 2) second-target first-target))
        (except [error Exception]
          (.append writer-failures (. (type error) __name__)))))
    (setv (get stop 0) 1)
    (setv line (race-read-line read-fd deadline))
    (os.close read-fd)
    (race-reap [pid] deadline)
    (.close stop)
    (.close started)
    (assert (= writer-failures []) f"張り替えで落ちた {(len writer-failures)} 件: {(cut writer-failures 0 8)}")
    (setv parts (.split line ":"))
    (assert (and (= (len parts) 2) (.isdigit (get parts 0)) (.isdigit (get parts 1)))
            f"読み手が数を返さない: {line}")
    (setv reads (int (get parts 0)))
    (setv missing (int (get parts 1)))
    ;; 針: 読みが少なければ競りの窓を張れていない(0 回の読みで緑にならない)
    (assert (> reads 500) f"読みが {reads} 回では競りの窓を張れていない")
    (assert (= missing 0) f"根の無い瞬間を {missing} / {reads} 回見た")
    (finally
      (shutil.rmtree d :ignore-errors True))))


;; ---------------------------------------------------------------------------
;; 失敗の語彙(2026-09-22 — 設計 docs/design/symlink-verbs-fail-vocabulary-ZCN5BD)
;; ---------------------------------------------------------------------------
;;
;; symlink を据える 2 動詞は「例外を投げない・決まった値のどれかを返す」と約束しているのに、
;; **器(file system)が断った拍の値**を持っていなかった。断りは必ず起きる(権限・容量・
;; 読み取り専用・同時実行)ので、語彙に無い断りは 2 つの出口しか持たない — 素の OSError が
;; effect の外まで抜けるか、正常系の値に化けるか。下の検はその 2 つを塞ぐ。

(defn race-child-links [shared index handler source target write-fd]
  "子: 同拍で 1 回だけ敷設し、結果を 1 行で親へ返して即座に落ちる(**戻らない**)。
   ⚠ ensure-symlink-outcome と違い、FsLinkArtifact の物理は handler の中に在るので
   effect を駆動する(raw の関数が無い)。"
  (try
    (race-spin-barrier shared index 2 (+ (time.monotonic) 30))
    (setv outcome (run (handler (fs-link-artifact source target))))
    (os.write write-fd (.encode f"ok:{(. outcome state)}" "utf-8"))
    (except [error BaseException]
      (try
        (os.write write-fd (.encode f"err:{(. (type error) __name__)}" "utf-8"))
        (except [OSError] None)))
    (finally
      (os._exit 0))))


(deftest test-fs-link-artifact-survives-two-seats-landing-on-one-target
  ;; ⚑ 受入 1: 同じ敷設先へ 2 process が同拍で降りても、**例外 0・語彙の外 0・敷設先が
  ;; 正しくない回 0**。直す前は「見てから張る」の 2 手だったので、見た後・張る前に相手が
  ;; 張った拍で FileExistsError が effect の外まで抜けた(計画段の実測: 200 ラウンド中
  ;; 184 / 196 = 92〜98 %)。家は資格ごとに鋳られ、同じ資格の複数の会話が 1 つの家へ同じ拍で
  ;; 降りるので、これは理論上の窓ではない(現に届く敷設先 = claude の sessions-index.json と
  ;; launch の workspaces-root/sibling)。
  ;; ⚠ lock は足さない。家は process をまたぐので process の中の lock は届かない —
  ;; だから thread ではなく **process** で撃つこと自体が検の本体。
  (setv d (os.path.realpath (tempfile.mkdtemp)))
  (try
    (setv source (os.path.join d "rollout.jsonl"))
    (with [f (open source "w" :encoding "utf-8")]
      (.write f "{}"))
    (setv handler (real-substrate "tmux"))
    (setv failures [])
    (setv outside [])
    (setv wrong [])
    (for [round-index (range 200)]
      (setv target (os.path.join d f"home-{round-index}" "sessions" "rollout.jsonl"))
      (setv shared (mmap.mmap -1 2))
      (setv (get shared 0) 0)
      (setv (get shared 1) 0)
      (setv read-fds [])
      (setv pids [])
      (for [index (range 2)]
        (setv [read-fd write-fd] (os.pipe))
        (setv pid (os.fork))
        (when (= pid 0)
          (os.close read-fd)
          (race-child-links shared index handler source target write-fd))
        (os.close write-fd)
        (.append read-fds read-fd)
        (.append pids pid))
      (setv deadline (+ (time.monotonic) 60))
      (for [read-fd read-fds]
        (setv line (race-read-line read-fd deadline))
        (os.close read-fd)
        (if (.startswith line "ok:")
            ;; 語彙の中か(閉語彙の外を返していないか)
            (when (not-in (cut line 3 None) FS-LINK-ARTIFACT-STATES)
              (.append outside #(round-index line)))
            ;; 例外が抜けた / 締切
            (.append failures #(round-index line))))
      (race-reap pids deadline)
      (.close shared)
      ;; 勝ち negative でも負けでも、据わるのは source を指す symlink 1 つだけ
      (when (not (and (os.path.islink target)
                      (= (os.path.realpath target) source)))
        (.append wrong round-index)))
    (assert (= failures []) f"同拍で落ちた敷設 {(len failures)} 件: {(cut failures 0 8)}")
    (assert (= outside []) f"語彙の外を返した回 {(len outside)} 件: {(cut outside 0 8)}")
    (assert (= wrong []) f"敷設先が正しくない回: {(cut wrong 0 8)}")
    (finally
      (shutil.rmtree d :ignore-errors True))))


(defn r-x-refuses-writes? []
  "この宿が r-x の dir への書きを現に断るか。uid を読むのではなく **1 バイト
   書いてみて測る**: root は素通りするが、権限を素通しする file system(container の
   一部の mount・CI の overlay)でも同じ結果になるので、uid の判定はその母集団を
   取りこぼす。
   ⚠ 返りは **形ごとの判断**に使う(検ごと skip しない — 2026-09-22 の検分)。
   器の断り 4 形のうち 2 形(親の位置に実体 file = ENOTDIR / 親そのものが実体 file
   = EEXIST)は権限ビットに依らず断るので、書けてしまう宿でも撃てる。検の頭で
   skip するとその 2 形の覆いまで黙って 0 になる — この語彙は借りた家(pod)で起きる
   事故を閉じるために入れたので、家が root だった日に覆いが消える形は残さない。"
  (setv probe (tempfile.mkdtemp))
  (setv home (os.path.join probe "r-x"))
  (setv wrote False)
  (try
    (os.makedirs home)
    (os.chmod home 0o555)
    (try
      (with [f (open (os.path.join home "probe") "w" :encoding "utf-8")]
        (.write f "x"))
      (setv wrote True)
      (except [OSError] None))
    (finally
      (try
        (os.chmod home 0o755)
        (except [OSError] None))
      (shutil.rmtree probe :ignore-errors True)))
  (not wrote))


(defn container-refusal-homes [d]
  "器の断りの 4 形を作り、#(名 link/target 権限ビット依りか) の list を返す
   (設計 2.2 の表 + 1)。
     親の位置に実体 file が居る / 家が書けない(r-x)/ 親 dir を作れない /
     **親そのものが実体 file**(= makedirs が EEXIST を出す形 — 依頼書の改訂が
     名指した反例。errno の表を syscall 間で共有すると、この 1 形が『実体の居座り』に
     化けて、運用者が在りもしない dir を片付けに行く)。
   ⚠ 実測(2026-09-22): 『親の位置に実体 file』の errno は宿で違う
     (Darwin/APFS では `blocker/child` は ENOTDIR・`blocker` そのものは EEXIST)。
     だから errno の綴りではなく **結末の状態**を検の対象にする。"
  (setv blocker (os.path.join d "blocker"))
  (with [f (open blocker "w" :encoding "utf-8")]
    (.write f "a real file where a directory is required"))
  (setv read-only (os.path.join d "read-only-home"))
  (os.makedirs read-only)
  (os.chmod read-only 0o555)
  (setv read-only-parent (os.path.join d "read-only-parent"))
  (os.makedirs read-only-parent)
  (os.chmod read-only-parent 0o555)
  ;; 3 つ目の欄 = **権限ビットを守る宿でしか撃てない形か**。前の 2 形は ENOTDIR /
  ;; EEXIST で断るので root でも bit を無視する mount でも撃てる(実測 2026-09-22)。
  [#("親の位置に実体 file" (os.path.join blocker "child" "seat") False)
   #("親そのものが実体 file(EEXIST)" (os.path.join blocker "seat") False)
   #("家が r-x" (os.path.join read-only "seat") True)
   #("親 dir を作れない" (os.path.join read-only-parent "sub" "seat") True)])


(defn restore-writable [d]
  "後片付けのために r-x を戻す(戻さないと rmtree が中身を落とせない)。"
  (for [name ["read-only-home" "read-only-parent"]]
    (try
      (os.chmod (os.path.join d name) 0o755)
      (except [OSError] None))))


(deftest test-fs-ensure-symlink-names-the-container-refusal
  ;; ⚑ 受入 2(据え付けの側): 器の断り 4 形で refused-by-container + errno を返し、例外 0。
  ;; 直す前は makedirs / symlink が try の外に在ったので、4 形とも素の OSError が
  ;; effect の外まで抜けた(この動詞は raise しない約束なのに、その約束ごと破れて席が起きない)。
  (setv bits-enforced (r-x-refuses-writes?))
  (setv ran 0)
  (setv d (os.path.realpath (tempfile.mkdtemp)))
  (try
    (setv target (os.path.join d "skills-src"))
    (os.makedirs target)
    (for [#(name link needs-bits) (container-refusal-homes d)]
      ;; 権限ビットを素通しする宿(root / 一部の mount)では r-x 依りの 2 形だけ外す
      (when (and needs-bits (not bits-enforced))
        (continue))
      (+= ran 1)
      (setv outcome (ensure-symlink-outcome link target))
      (assert (isinstance outcome FsSymlinkOutcome) #(name outcome))
      (assert (= (. outcome state) FS-SYMLINK-REFUSED) #(name (. outcome state)))
      (assert (in (. outcome state) FS-ENSURE-SYMLINK-STATES) #(name outcome))
      (assert (is-not (. outcome errno) None) #(name "errno が無い"))
      ;; detail は「どの syscall が何と言ったか」— 理由の無い断りは無益な文言にしかならない
      (assert (is-not (. outcome detail) None) #(name "detail が無い"))
      (assert (in (str (. outcome errno)) (str (. outcome errno))))
      ;; 名乗りには理由が載る(ログの 1 行がそのまま診断になる)
      (assert (.startswith (str outcome) f"{FS-SYMLINK-REFUSED} (") (str outcome)))
    ;; 権限ビットに依らない 2 形は宿を問わず走る(検ごと skip すると覆いが黙って 0 になる)
    (assert (>= ran 2) f"器の断りの形が {ran} 件しか走っていない(宿を問わず 2 形は撃てる)")
    (finally
      (restore-writable d)
      (shutil.rmtree d :ignore-errors True))))


(deftest test-fs-link-artifact-names-the-container-refusal
  ;; ⚑ 受入 2(敷設の側): **同じ形が FsLinkArtifact にも在る**(計画段の実測 2.2 —
  ;; 依頼者は据え付けの側だけの話と見ていたが、敷設の側にも全部在った)。
  ;; 同時実行の直しだけでは全形とも抜けたままだったことも実測済み。
  (setv bits-enforced (r-x-refuses-writes?))
  (setv ran 0)
  (setv d (os.path.realpath (tempfile.mkdtemp)))
  (try
    (setv source (os.path.join d "rollout.jsonl"))
    (with [f (open source "w" :encoding "utf-8")]
      (.write f "{}"))
    (setv handler (real-substrate "tmux"))
    (for [#(name target needs-bits) (container-refusal-homes d)]
      (when (and needs-bits (not bits-enforced))
        (continue))
      (+= ran 1)
      (setv outcome (run (handler (fs-link-artifact source target))))
      (assert (isinstance outcome FsSymlinkOutcome) #(name outcome))
      (assert (= (. outcome state) FS-SYMLINK-REFUSED) #(name (. outcome state)))
      (assert (in (. outcome state) FS-LINK-ARTIFACT-STATES) #(name outcome))
      (assert (is-not (. outcome errno) None) #(name "errno が無い"))
      (assert (is-not (. outcome detail) None) #(name "detail が無い")))
    (assert (>= ran 2) f"器の断りの形が {ran} 件しか走っていない(宿を問わず 2 形は撃てる)")
    (finally
      (restore-writable d)
      (shutil.rmtree d :ignore-errors True))))


(defn blinded-lstat [path]
  "その 1 つの path だけ『何も居ない』に見せる os.lstat を返す。

   rename の枝は、見分け(1 回の lstat)と rename の**間に**実体が現れた拍にだけ届く —
   実体が見分けの時点で据わっていれば erosion guard が先に当たる(そちらは
   test-fs-ensure-symlink-three-outcomes が押さえている)。残る窓は設計 docstring が
   名指している既知の 1 つで、自然には掴めないので syscall の境界で再現する。"
  (setv real-lstat os.lstat)
  (defn blinded [candidate #* args #** kwargs]
    (when (= candidate path)
      (raise (FileNotFoundError errno.ENOENT "No such file or directory" path)))
    (real-lstat candidate #* args #** kwargs))
  blinded)


(deftest test-fs-ensure-symlink-splits-the-rename-errno
  ;; ⚑ 受入 3: rename(2) の except を **errno で割る**。直す前は無型の except OSError が
  ;; すべてを「実体が居る」と名乗っていたので、権限や容量で断られた拍にも
  ;; occupied-by-real-entity が出て、運用者が**居もしない実体を手で片付けに**行った。
  ;; EISDIR / ENOTEMPTY / EEXIST は POSIX が rename に定めた「宛先に実体が据わっている」の
  ;; 綴りなので Darwin 固有ではない(実測 = 設計 2.3: 空の dir も中身入りの dir も EISDIR)。
  (setv d (os.path.realpath (tempfile.mkdtemp)))
  (setv real-lstat os.lstat)
  (setv real-replace os.replace)
  (try
    (setv target (os.path.join d "skills-src"))
    (os.makedirs target)
    ;; (a) 空の dir が rename に当たる → occupied(EISDIR)
    (setv empty-dir (os.path.join d "home" "empty"))
    (os.makedirs empty-dir)
    (try
      (setv os.lstat (blinded-lstat empty-dir))
      (setv first (ensure-symlink-outcome empty-dir target))
      (finally
        (setv os.lstat real-lstat)))
    (assert (= (. first state) FS-SYMLINK-OCCUPIED) first)
    (assert (= (. first errno) errno.EISDIR) first)
    (assert (in "rename" (. first detail)) first)
    (assert (os.path.isdir empty-dir))
    (assert (not (os.path.islink empty-dir)))
    ;; (b) 中身入りの dir が rename に当たる → occupied(EISDIR)
    (setv full-dir (os.path.join d "home" "full"))
    (os.makedirs full-dir)
    (with [f (open (os.path.join full-dir "inside.txt") "w" :encoding "utf-8")]
      (.write f "x"))
    (try
      (setv os.lstat (blinded-lstat full-dir))
      (setv second (ensure-symlink-outcome full-dir target))
      (finally
        (setv os.lstat real-lstat)))
    (assert (= (. second state) FS-SYMLINK-OCCUPIED) second)
    (assert (= (. second errno) errno.EISDIR) second)
    (assert (= (os.listdir full-dir) ["inside.txt"]) (os.listdir full-dir))
    ;; (c) それ以外の OSError は **refused**(実体を名乗らない)。自然には出ない errno なので
    ;; syscall の境界で注入する — 割り方そのものが検の対象。
    (setv link (os.path.join d "home" "seat"))
    (defn refusing-replace [src dst]
      (raise (OSError errno.EACCES "Permission denied")))
    (try
      (setv os.replace refusing-replace)
      (setv third (ensure-symlink-outcome link target))
      (finally
        (setv os.replace real-replace)))
    (assert (= (. third state) FS-SYMLINK-REFUSED) third)
    (assert (= (. third errno) errno.EACCES) third)
    (assert (in "rename" (. third detail)) third)
    ;; 仮は自分のぶんだけ掃除して名乗る — 家に残骸を残さない
    (assert (= (sorted (os.listdir (os.path.join d "home"))) ["empty" "full"])
            (os.listdir (os.path.join d "home")))
    ;; (d) 依頼書 §4-3 の骨どおり、errno を**注入して**割りを撃つ(実体を据える形では
    ;;     os.lstat が先に当たるので rename に 0 回しか届かない — 直す前の版でも緑になる)。
    (setv injected (os.path.join d "home" "injected"))
    (defn raising-replace [err]
      (defn f [src dst #** kwargs]
        (raise (OSError err (os.strerror err))))
      f)
    (try
      (setv os.replace (raising-replace errno.EISDIR))
      (setv fourth (ensure-symlink-outcome injected target))
      (finally
        (setv os.replace real-replace)))
    (assert (= (. fourth state) FS-SYMLINK-OCCUPIED) fourth)
    (assert (= (. fourth errno) errno.EISDIR) fourth)
    ;; 仮の残骸 0(except の枝の「自分の仮だけ掃除する」が現に走った証拠 —
    ;; 到達路が無かった間、この 1 行は一度も走っていない)
    (assert (= (sorted (os.listdir (os.path.join d "home"))) ["empty" "full"])
            (os.listdir (os.path.join d "home")))
    ;; 割りの正本は純関数 1 点(substrate.refusal-of)。表を syscall 間で共有すると
    ;; makedirs / symlink の EEXIST が「実体の居座り」に化けて受入 2 が赤くなる。
    (assert (= (! (substrate.refusal-of "rename" errno.EISDIR)) FS-SYMLINK-OCCUPIED))
    (for [#(syscall code) [#("rename" errno.EACCES)
                           #("rename" errno.EEXIST)
                           #("rename" errno.ENOTEMPTY)
                           #("rename" None)
                           #("makedirs" errno.EEXIST)
                           #("makedirs" errno.EACCES)
                           #("symlink" errno.EEXIST)]]
      (assert (= (! (substrate.refusal-of syscall code)) FS-SYMLINK-REFUSED)
              #(syscall code)))
    (finally
      (setv os.lstat real-lstat)
      (setv os.replace real-replace)
      (shutil.rmtree d :ignore-errors True))))


(deftest test-fs-symlink-outcome-keeps-the-old-log-word
  ;; ⚑ 受入 5: **旧側の値を運ぶ**。ログの f"outcome={outcome}" は理由が無い限り
  ;; 綴りそのもの(既存 6 語の綴りは 1 ビットも変えない)。理由が在る時だけ
  ;; "state (detail)" になる。
  (for [state [FS-SYMLINK-LINKED FS-SYMLINK-UNCHANGED FS-SYMLINK-OCCUPIED
               FS-SYMLINK-SOURCE-MISSING FS-SYMLINK-SAME-ENTITY
               FS-SYMLINK-TARGET-CONFLICT FS-SYMLINK-REFUSED]]
    (assert (= (str (FsSymlinkOutcome :state state)) state) state))
  (assert (= (str (FsSymlinkOutcome :state FS-SYMLINK-REFUSED
                                    :errno 13
                                    :detail "symlink: EACCES Permission denied"))
             "refused-by-container (symlink: EACCES Permission denied)"))
  ;; record と str の比較は **常に誤り** — 移行で黙って False になる代わりに声を出す
  (setv outcome (FsSymlinkOutcome :state FS-SYMLINK-LINKED))
  (with [(pytest.raises TypeError)]
    (= outcome "linked"))
  (with [(pytest.raises TypeError)]
    (= "linked" outcome)))


;; ---------------------------------------------------------------------------
;; 偽ハンドラの同型性(受入 6 — 2026-09-22)
;; ---------------------------------------------------------------------------
;;
;; 台本の substrate(sessionhost_launch_deftests の LaunchWorld)は、実物と**同じ
;; effect 契約の fake** でなければならない。片方が文字列・片方がレコードだと、
;; 台本で緑の code が本番で黙って False を踏む(移行の第 2 の契約)。
;; ⚠ 台本の世界では器は断らないので refused-by-container は出ない。出ないことも
;;    契約のうち(errno / detail は None)。

(deftest test-fake-substrate-returns-the-same-symlink-outcome-type
  (setv d (os.path.realpath (tempfile.mkdtemp)))
  (try
    (setv handler (real-substrate "tmux"))
    (setv world (LaunchWorld))
    (setv drive-fake (fake-launch-substrate world))
    (setv first-target (os.path.join d "skills-v1"))
    (setv second-target (os.path.join d "skills-v2"))
    (os.makedirs first-target)
    (os.makedirs second-target)
    (setv link (os.path.join d "home" "skills"))
    ;; 台本の世界にも同じ実体を置く(dir は listings、file は fs)
    (setv (get world.listings first-target) [])
    (setv (get world.listings second-target) [])
    (defn both [program-real program-fake]
      "実物と台本を同じ拍で撃ち、#(実物 台本) を返す。"
      #((run (handler program-real)) (run (drive-fake program-fake))))
    (defn same-shape [name real fake]
      (assert (isinstance real FsSymlinkOutcome) #(name "real" real))
      (assert (isinstance fake FsSymlinkOutcome) #(name "fake" fake))
      (assert (= (. real state) (. fake state)) #(name (. real state) (. fake state)))
      ;; 台本の器は断らない
      (assert (is (. fake errno) None) #(name (. fake errno)))
      (assert (is (. fake detail) None) #(name (. fake detail))))
    ;; 据え付け 4 値のうち、台本が表せる 3 つ
    (setv [r f] (both (fs-ensure-symlink link first-target)
                      (fs-ensure-symlink link first-target)))
    (same-shape "何も居ない" r f)
    (assert (= (. r state) FS-SYMLINK-LINKED) r)
    (setv [r f] (both (fs-ensure-symlink link first-target)
                      (fs-ensure-symlink link first-target)))
    (same-shape "同じ先" r f)
    (assert (= (. r state) FS-SYMLINK-UNCHANGED) r)
    (setv [r f] (both (fs-ensure-symlink link second-target)
                      (fs-ensure-symlink link second-target)))
    (same-shape "別の先" r f)
    (assert (= (. r state) FS-SYMLINK-LINKED) r)
    (setv occupied (os.path.join d "home" "real"))
    (with [file (open occupied "w" :encoding "utf-8")]
      (.write file "someone's real file"))
    (setv (get world.fs occupied) "someone's real file")
    (setv [r f] (both (fs-ensure-symlink occupied first-target)
                      (fs-ensure-symlink occupied first-target)))
    (same-shape "実体が居る" r f)
    (assert (= (. r state) FS-SYMLINK-OCCUPIED) r)
    ;; 敷設 5 値のうち、台本が表せる 4 つ
    (setv source (os.path.join d "rollout.jsonl"))
    (setv artifact (os.path.join d "target-home" "rollout.jsonl"))
    (setv [r f] (both (fs-link-artifact source artifact)
                      (fs-link-artifact source artifact)))
    (same-shape "source 不在" r f)
    (assert (= (. r state) FS-SYMLINK-SOURCE-MISSING) r)
    (with [file (open source "w" :encoding "utf-8")]
      (.write file "{}"))
    (setv (get world.fs source) "{}")
    (setv [r f] (both (fs-link-artifact source artifact)
                      (fs-link-artifact source artifact)))
    (same-shape "敷設先が空いている" r f)
    (assert (= (. r state) FS-SYMLINK-LINKED) r)
    (setv [r f] (both (fs-link-artifact source artifact)
                      (fs-link-artifact source artifact)))
    (same-shape "同一実体" r f)
    (assert (= (. r state) FS-SYMLINK-SAME-ENTITY) r)
    (setv other (os.path.join d "other.jsonl"))
    (with [file (open other "w" :encoding "utf-8")]
      (.write file "{}"))
    (setv (get world.fs other) "{}")
    (setv [r f] (both (fs-link-artifact other artifact)
                      (fs-link-artifact other artifact)))
    (same-shape "別実体が据わっている" r f)
    (assert (= (. r state) FS-SYMLINK-TARGET-CONFLICT) r)
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
