;; 翻訳の handler checkout-reads(runtime_env.hy)を模擬の土台 — 台本の git(checkout_git_script.hy の git-command)と memory の置き場 —
;; の上で走らせる検(2026-09-27)。本物の土台(subprocess-handler・os-file-handler)で読む検は test_runtime_env_sender.hy。
;;
;;   - 翻訳を通した読みが checkout の世界と合う(head・URL・汚れ・remote に在るか・送り手の source の根・uv.lock の sha256・無い file)
;;   - 反例: 翻訳を誤る形(head を別の checkout から読む・sha256 でない digest)は同じ検め方で赤になる
;;   - 組み立て(runtime-env-of-checkouts)が模擬の土台の上で本物と同じ所で断る(汚れ・push していない)
;;   - 系の宣言の前の検め(checked-declaring-checkout)が版の違い・checkout の外・汚れ・push していない commit を断る
(require doeff-hy.macros [deftest defk defhandler <- val var])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])
(import hashlib)
(import pytest)
(import doeff [with_handlers])
(import doeff_core_effects.handlers [state])
(import doeff_core_effects.file_effects [MemoryFile MemoryFiles ReadBytes])
(import doeff_core_effects.memory_file [memory-file-handler])
(import doeff_core_effects.scripted_process [ProcessScript scripted-process-handler])
(import doeff_cluster.runtime_env [LocalCheckout ProjectOfCheckout CheckoutState ReadCheckout SenderSourceRoot SENDER-SOURCE-DIR
                                   runtime-env-of-checkouts checkout-reads checkout-state-at checked-declaring-checkout])
(import doeff_cluster.shared.intent.runtime_env_model [RepoCheckout RuntimeEnv RuntimeEnvInvalid InvalidKind])
(import doeff_cluster.env_prepare [FileSha256])
(import doeff_cluster.checkout_git_script [GitCheckout GitRemote GitRev git-command])
(import doeff_core_effects.process_effects [ProcessOutcome RunProcess])

(val LOCK "httpx==0.28.1\n")
(val APP-HEAD (* "a" 40))
(val LIB-HEAD (* "b" 40))
(val APP-URL "file:///remotes/app.git")
(val LIB-URL "file:///remotes/lib.git")


(defk world-of [app-dirty app-pushed]
  {:pre [(: app-dirty bool) (: app-pushed bool)] :post [(: % tuple)]}
  "筋書きの checkout の世界を作るため: app(/src/app — uv.lock を持つ)と lib(/src/lib — 送り手の source の dir を持つ・push 済み)。"
  #((GitCheckout :path "/src/app" :head APP-HEAD :remotes #((GitRemote :name "origin" :url APP-URL)) :dirty app-dirty
                 :pushed (if app-pushed #("origin/main" "upstream/main") #("upstream/main")))
    (GitCheckout :path "/src/lib" :head LIB-HEAD :remotes #((GitRemote :name "origin" :url LIB-URL)) :pushed #("origin/main")
                 :members #(SENDER-SOURCE-DIR))))


(defn #^ list grounds [#^ tuple checkouts]  ; defk にできない: with_handlers へ渡す handler の列を組む(Program の外)
  "模擬の土台の組(外側が先): session の値の置き場 → memory の置き場(app の uv.lock)→ 台本の git。"
  [(state)
   (memory-file-handler (MemoryFiles :files #((MemoryFile :path "/src/app/uv.lock" :content (.encode LOCK "utf-8")))
                                     :dirs #("/src" "/src/app" "/src/lib")))
   (scripted-process-handler (ProcessScript :commands #((git-command checkouts))))])


(defrecord Reads
  "翻訳を通した読みの全部(検め方 agree-with-world が世界と突き合わせる)。"
  (#^ CheckoutState app)
  (#^ CheckoutState lib)
  (#^ (| str None) source-root)
  (#^ (| str None) lock-sha256)
  (#^ (| str None) missing-sha256))


(defk reads []
  {:pre [] :post [(: % Reads)]}
  "checkout の読みの effect を全部 1 度ずつ出すため。"
  (<- app CheckoutState (ReadCheckout "/src/app" "origin"))
  (<- lib CheckoutState (ReadCheckout "/src/lib" "origin"))
  (<- root (| str None) (SenderSourceRoot))
  (<- lock (| str None) (FileSha256 "/src/app/uv.lock"))
  (<- missing (| str None) (FileSha256 "/src/app/absent.lock"))
  (Reads :app app :lib lib :source-root root :lock-sha256 lock :missing-sha256 missing))


(defk agree-with-world [seen]
  {:pre [(: seen Reads)] :post [(: % bool)]}
  "読みが world-of(False False)の世界と合うことを検めるため(合わなければ AssertionError — 反例もこの 1 つで検める)。"
  (assert (= seen.app (CheckoutState :head APP-HEAD :url APP-URL :dirty False :on-remote False)) seen.app)
  (assert (= seen.lib (CheckoutState :head LIB-HEAD :url LIB-URL :dirty False :on-remote True)) seen.lib)
  (assert (= seen.source-root "/src/lib") seen.source-root)
  (assert (= seen.lock-sha256 (.hexdigest (hashlib.sha256 (.encode LOCK "utf-8")))) seen.lock-sha256)
  (assert (is seen.missing-sha256 None) seen.missing-sha256)
  True)


;; --- 反例: 翻訳を誤る形(checkout-reads の内側に置き、同じ土台で答える) ------------------------------------------

(defhandler head-from-the-other-checkout
  ;; 誤った翻訳: 名指された checkout でなく、もう一方の checkout で git を読む(sha の取り違え)。
  (ReadCheckout [path remote]
    (<- state CheckoutState (checkout-state-at (if (= path "/src/app") "/src/lib" "/src/app") remote))
    (resume state)))


(defhandler digest-not-sha256
  ;; 誤った翻訳: 中身を読むが sha256 でなく sha1 で digest を作る。
  (FileSha256 [path]
    (<- content (ReadBytes path))
    (resume (if (isinstance content bytes) (.hexdigest (hashlib.sha1 content)) None))))


(deftest test-the-translation-reads-agree-with-the-checkout-world []
  (<- world tuple (world-of False False))
  (<- seen Reads (with_handlers (+ (grounds world) [checkout-reads]) (reads)))
  (<- ok bool (agree-with-world seen))
  (assert ok))


(deftest test-a-mistranslated-head-is-caught []
  (<- world tuple (world-of False False))
  (<- seen Reads (with_handlers (+ (grounds world) [checkout-reads head-from-the-other-checkout]) (reads)))
  ;; 赤の理由が head の取り違えであること(app の読みに lib の head が載る)まで検める。
  (with [_ (pytest.raises AssertionError :match LIB-HEAD)]
    (<- (agree-with-world seen))))


(deftest test-a-mistranslated-digest-is-caught []
  (<- world tuple (world-of False False))
  (<- seen Reads (with_handlers (+ (grounds world) [checkout-reads digest-not-sha256]) (reads)))
  ;; 赤の理由が digest の取り違えであること(sha1 の値が載る)まで検める。
  (with [_ (pytest.raises AssertionError :match (.hexdigest (hashlib.sha1 (.encode LOCK "utf-8"))))]
    (<- (agree-with-world seen))))


(defk build [checkouts]
  {:pre [(: checkouts tuple)] :post [(: % (| RuntimeEnv InvalidKind))]}
  "app と lib の checkout から宣言を組むため(送り手の repo = lib)。断られたら断りの種類。"
  (try
    (<- env RuntimeEnv (with_handlers (+ (grounds checkouts) [checkout-reads])
                                      (runtime-env-of-checkouts #((LocalCheckout :name "app" :path "/src/app")
                                                                  (LocalCheckout :name "lib" :path "/src/lib"))
                                                                (ProjectOfCheckout :repo "app" :path "." :python "3.14")
                                                                #("app/.")
                                                                :sender-repo "lib")))
    (except [refused RuntimeEnvInvalid]
      (return refused.kind)))
  env)


(deftest test-the-declaration-is-built-and-refused-on-the-scripted-grounds []
  (<- clean tuple (world-of False True))
  (<- env (| RuntimeEnv InvalidKind) (build clean))
  (assert (isinstance env RuntimeEnv) env)
  (assert (= (tuple (gfor r env.repos #(r.name r.url r.commit))) #(#("app" APP-URL APP-HEAD) #("lib" LIB-URL LIB-HEAD))))
  (assert (= env.project.lock-sha256 (.hexdigest (hashlib.sha256 (.encode LOCK "utf-8")))))
  (<- dirty-world tuple (world-of True True))
  (<- dirty (| RuntimeEnv InvalidKind) (build dirty-world))
  (assert (= dirty InvalidKind.DIRTY-TREE) dirty)
  (<- unpushed-world tuple (world-of False False))
  (<- unpushed (| RuntimeEnv InvalidKind) (build unpushed-world))
  (assert (= unpushed InvalidKind.COMMIT-NOT-ON-REMOTE) unpushed))


(defk git-says [checkouts argv]
  {:pre [(: checkouts tuple) (: argv tuple)] :post [(: % ProcessOutcome)]}
  "台本の git に問い 1 つを出して答えを読むため(名指しの rev の解きの検)。"
  (<- outcome ProcessOutcome (with_handlers [(state) (scripted-process-handler (ProcessScript :commands #((git-command checkouts))))]
                                            (RunProcess :argv argv)))
  outcome)


(deftest test-named-revs-resolve-to-commits-and-carry-their-own-remote-branches []
  ;; rev-parse --verify --quiet <rev>^{commit}: HEAD・head の sha・revs の名は sha へ、知らない rev は exit 1 で出力なし(本物の --quiet と同じ)。
  ;; branch -r --contains は head なら checkout の pushed、revs の sha ならその rev の pushed。
  (val head (* "a" 40))
  (val side (* "b" 40))
  (val world #((GitCheckout :path "/src/hud" :head head :pushed #("origin/main")
                            :revs #((GitRev :name "main" :sha head :pushed #("origin/main"))
                                    (GitRev :name "local-only" :sha side)))))
  (<- by-name ProcessOutcome (git-says world #("git" "-C" "/src/hud" "rev-parse" "--verify" "--quiet" "main^{commit}")))
  (assert (= (.strip by-name.stdout) head))
  (<- by-head ProcessOutcome (git-says world #("git" "-C" "/src/hud" "rev-parse" "--verify" "--quiet" "HEAD^{commit}")))
  (assert (= (.strip by-head.stdout) head))
  (<- unknown ProcessOutcome (git-says world #("git" "-C" "/src/hud" "rev-parse" "--verify" "--quiet" "feature^{commit}")))
  (assert (= #(unknown.exit-code unknown.stdout unknown.stderr) #(1 "" "")))
  (<- unpeeled ProcessOutcome (git-says world #("git" "-C" "/src/hud" "rev-parse" "--verify" "--quiet" "main")))
  (assert (= (.strip unpeeled.stdout) head))
  (<- side-sha ProcessOutcome (git-says world #("git" "-C" "/src/hud" "rev-parse" "--verify" "--quiet" "local-only^{commit}")))
  (assert (= (.strip side-sha.stdout) side))
  (<- on-main ProcessOutcome (git-says world #("git" "-C" "/src/hud" "branch" "-r" "--contains" head "--list" "origin/*")))
  (assert (= (.strip on-main.stdout) "origin/main"))
  (<- nowhere ProcessOutcome (git-says world #("git" "-C" "/src/hud" "branch" "-r" "--contains" side "--list" "origin/*")))
  (assert (= nowhere.stdout "")))


;; --- 系の宣言の前の検め(declare — 2026-09-28・計画 2.2 の E) ------------------------------------------

(defk declaring-kind [checkouts path revision]
  {:pre [(: checkouts tuple) (: path str) (: revision str)] :post [(: % (| RepoCheckout InvalidKind))]}
  "系の関数の source の dir path を宣言の版 revision で検めるため(通れば checkout の repo の読み・断られたら断りの種類)。"
  (try
    (<- repo RepoCheckout (with_handlers (+ (grounds checkouts) [checkout-reads]) (checked-declaring-checkout path revision)))
    (except [refused RuntimeEnvInvalid]
      (return refused.kind)))
  repo)


(deftest test-the-declaring-checkout-must-be-clean-pushed-and-at-the-revision []
  ;; 系の関数の source の dir(checkout の根の下)を翻訳を通して読む: 汚れておらず push 済みで HEAD = 宣言の版なら通り、その checkout の
  ;; repo の読みを返す。版の違い・checkout の外・汚れ・push していない commit は、それぞれの種類で断る。
  (<- clean tuple (world-of False True))
  (<- passed (| RepoCheckout InvalidKind) (declaring-kind clean "/src/app/pkg" APP-HEAD))
  (assert (= passed (RepoCheckout :name "system-source" :url APP-URL :commit APP-HEAD)) passed)
  (<- other (| RepoCheckout InvalidKind) (declaring-kind clean "/src/app/pkg" LIB-HEAD))
  (assert (= other InvalidKind.REVISION-DIFFERS) other)
  (<- outside (| RepoCheckout InvalidKind) (declaring-kind clean "/elsewhere/pkg" APP-HEAD))
  (assert (= outside InvalidKind.NOT-IN-CHECKOUT) outside)
  (<- dirty-world tuple (world-of True True))
  (<- dirty (| RepoCheckout InvalidKind) (declaring-kind dirty-world "/src/app/pkg" APP-HEAD))
  (assert (= dirty InvalidKind.DIRTY-TREE) dirty)
  (<- unpushed-world tuple (world-of False False))
  (<- unpushed (| RepoCheckout InvalidKind) (declaring-kind unpushed-world "/src/app/pkg" APP-HEAD))
  (assert (= unpushed InvalidKind.COMMIT-NOT-ON-REMOTE) unpushed))
