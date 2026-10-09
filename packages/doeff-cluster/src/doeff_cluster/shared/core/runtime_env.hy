;;; 送り手の側の実行環境の宣言の組み立て(2026-09-26)。
;;;
;;; 送り手は手元の checkout(repo ごとの作業の dir)から宣言(runtime_env_model の RuntimeEnv)を組み立てる。worker は commit を
;;; remote から取りに行くので、worker が取れない物は送る前に断る(例外 RuntimeEnvInvalid):
;;;   local-remote          remote の url が手元の path か file://(別の機体の worker はその url から取れない — #3167)
;;;   dirty-tree            commit していない変更がある(送れるのは commit した物だけ)
;;;   commit-not-on-remote  その commit が remote の branch に無い(push していない)
;;;   sender-source-differs 送り手自身が動いている source(このパッケージの checkout)が、宣言の同じ repo の commit と違う
;;;                         (子で黙って版の不一致になる路を残さない)
;;;
;;; 系の宣言(declare — 2026-09-28・計画 2.2 の E)も同じ読みを通す: 系の関数の source の在る checkout が汚れておらず push 済みで
;;; (checked-repo と同じ断り)、HEAD が宣言の版そのものの時だけ宣言する(checked-declaring-checkout)。
;;;   not-in-checkout       系の関数の source が git の checkout の中に無い(宣言の版と同じ code かを確かめられない)
;;;   revision-differs      checkout の HEAD が宣言の版と違う(詰める Program の参照する code と、実行先が版で展開する code がずれる)
;;;
;;;   (<- env (runtime-env-of-checkouts #((LocalCheckout :name "app" :path "/src/app")
;;;                                       (LocalCheckout :name "lib" :path "/src/lib"))
;;;                                     (ProjectOfCheckout :repo "app" :path "." :python "3.14")
;;;                                     #("app/." "app/vendor")
;;;                                     :sender-repo "lib"))
;;;
;;; checkout の読みは effect(ReadCheckout・CheckoutRoot・SenderSourceRoot・FileSha256)。答えるのは下の翻訳の handler checkout-reads 1 つで、doeff の汎用の
;;; 子 process の effect(RunProcess — git を起こす)と file の effect(StatPath・ReadBytes)へ訳す。I/O を持たない(sha256 は計算だけ)。
;;; 環境で差し替えるのはその汎用の effect に答える土台の handler だけ(2026-09-27):
;;;   本物   [subprocess-handler os-file-handler checkout-reads](外側が先)
;;;   模擬   [(state) (memory-file-handler …) (scripted-process-handler (ProcessScript :commands #((git-command checkouts)))) checkout-reads]
;;;          — git の台本は checkout_git_script.hy の git-command(checkout の読みに要る git の問いだけに答える)
;;;
;;; 訳し方(git は `git -C <path> …` の 1 回ずつ・0 でない終わりは読めない checkout として RuntimeError — 前の本物の check=True と同じ):
;;;   ReadCheckout      rev-parse HEAD → config --get remote.<remote>.url(insteadOf で書き換える前の URL)→ status --porcelain --untracked-files=no(空でなければ dirty)→
;;;                     branch -r --contains <head> --list <remote>/*(空でなければ on-remote — 知識は手元の追跡の ref・最後の fetch による)
;;;   CheckoutRoot      <path> で rev-parse --show-toplevel。0 でなければ None(checkout の外)
;;;   SenderSourceRoot  CheckoutRoot と同じ問いを SENDER-SOURCE-DIR(この module の dir)で
;;;   FileSha256        StatPath が file なら ReadBytes の sha256・file でなければ None
;;;
;;; 置き場(#2110): 組み立ての Program はここ(shared/core)・型と effect は doeff_cluster.shared.intent.checkout_model・
;;; 翻訳の handler は doeff_cluster.shared.protocol.checkout_reads。
(require doeff-hy.macros [defk <- val var])
(val MODULE-TAGS {:context "doeff-cluster" :role "program"})
(import doeff_cluster.shared.intent.runtime_env_model [RepoCheckout PythonProject EnvVar ToolRequirement RuntimeEnv RuntimeEnvInvalid InvalidKind
                                                       LocalPath RemoteRepo RepoLocation])
(import doeff_cluster.shared.intent.checkout_model [LocalCheckout ProjectOfCheckout CheckoutState ReadCheckout CheckoutRoot SenderSourceRoot])
(import doeff_cluster.shared.intent.env_marker_model [FileSha256])
(import doeff_cluster.shared.core.runtime_env_rules [url-location])


;; --- 組み立て -----------------------------------------------------------------------------

(defk checked-repo [checkout]
  {:pre [(: checkout LocalCheckout)] :post [(: % RepoCheckout)]}
  "checkout 1 つを宣言の repo にする。worker が取れない物(remote が手元の path・汚れたツリー・push していない commit)はここで断る。
   手元の path かの読み分けは url-location(git の url_is_local_not_ssh と同じ)の 1 つ — 宣言の url は別の機体の worker が clone する元
   なので、手元の path と file:// は取れない。https://・ssh://・scp の形(git@github.com:owner/repo.git)は網の repo。"
  (<- seen CheckoutState (ReadCheckout checkout.path checkout.remote))
  (<- location RepoLocation (url-location seen.url))
  (match location
    (LocalPath)
    (raise (RuntimeEnvInvalid InvalidKind.LOCAL-REMOTE
                              (.format "{} の remote {} は手元の path — 別の機体の worker は取れない。remote を GitHub の url(git@github.com:… か https://…)にした clone から宣言する"
                                       checkout.name seen.url)))
    (RemoteRepo)
    None)
  (when seen.dirty
    (raise (RuntimeEnvInvalid InvalidKind.DIRTY-TREE
                              (.format "{}({})に commit していない変更がある(送れるのは commit した物だけ)"
                                       checkout.name checkout.path))))
  (when (not seen.on-remote)
    (raise (RuntimeEnvInvalid InvalidKind.COMMIT-NOT-ON-REMOTE
                              (.format "{} の commit {} が remote {} の branch に無い(push していない)"
                                       checkout.name seen.head checkout.remote))))
  (RepoCheckout :name checkout.name :url seen.url :commit seen.head))


(defk checked-declaring-checkout [path revision]
  {:pre [(: path str) (: revision str)] :post [(: % RepoCheckout)] :tags {:context "runtime-env" :role "judgment"}}
  "系の宣言(declare)の前に、系の関数の source(path = その module の file の dir)が、宣言の版 revision の commit そのものの
   汚れていない・push 済みの checkout に在ることを確かめるため — declare が詰める Program の参照する code と、実行先が revision で
   展開する code を一致させる(計画 2.2 の E)。汚れ・push していない commit は checked-repo と同じ理由で断り、checkout の外は
   NOT-IN-CHECKOUT、HEAD が revision と違えば REVISION-DIFFERS で断る。"
  (<- root (| str None) (CheckoutRoot path))
  (when (is root None)
    (raise (RuntimeEnvInvalid InvalidKind.NOT-IN-CHECKOUT
                              (.format "系の関数の source({})が git の checkout の中に無い(宣言の版と同じ code かを確かめられない)" path))))
  (<- repo RepoCheckout (checked-repo (LocalCheckout :name "system-source" :path root)))
  (when (!= repo.commit revision)
    (raise (RuntimeEnvInvalid InvalidKind.REVISION-DIFFERS
                              (.format "系の関数の checkout({})の HEAD {} が宣言の版 {} と違う — 詰める Program の code と、実行先が版で展開する code がずれる(HEAD を版にするか、その版を checkout してから宣言する)"
                                       root repo.commit revision))))
  repo)


(defk check-sender-source [checkouts repos sender-repo]
  {:pre [(: checkouts tuple) (: repos tuple) (: sender-repo str)] :post [(: % bool)]}
  "送り手自身の source が、宣言の sender-repo と同じ commit の汚れていない checkout であることを確かめる。"
  (<- root (| str None) (SenderSourceRoot))
  (val declared (next (gfor r repos :if (= r.name sender-repo) r) None))
  (when (is declared None)
    (raise (RuntimeEnvInvalid InvalidKind.UNKNOWN-REPO (.format "送り手の repo {} が宣言に無い" sender-repo))))
  (when (is root None)
    (raise (RuntimeEnvInvalid InvalidKind.SENDER-SOURCE-DIFFERS
                              "送り手の source が git の checkout の中に無い(宣言の commit と同じかを確かめられない)")))
  (<- seen CheckoutState (ReadCheckout root "origin"))
  (when (or seen.dirty (!= seen.head declared.commit))
    (raise (RuntimeEnvInvalid InvalidKind.SENDER-SOURCE-DIFFERS
                              (.format "送り手の source({}・commit {}{})が宣言の {} の commit {} と違う"
                                       root seen.head (if seen.dirty "・変更あり" "") sender-repo declared.commit))))
  True)


(defk runtime-env-of-checkouts [checkouts project import-roots [env-vars #()] [tools #()] [sender-repo None]]
  {:pre [(: checkouts tuple) (: project ProjectOfCheckout) (: import-roots tuple) (: env-vars tuple) (: tools tuple)
         (: sender-repo (| str None))]
   :post [(: % RuntimeEnv)]}
  "手元の checkout から宣言を組み立てる(送る前の唯一の口)。uv.lock の sha256 は checkout から計算する。
   sender-repo = 送り手自身の source を持つ宣言の repo の名(送り手が env の外で動く時に、その source が宣言と同じかを確かめる)。"
  (var repos [])
  (for [c checkouts]
    (<- repo RepoCheckout (checked-repo c))
    (.append repos repo))
  (val by-name (dfor c checkouts c.name c.path))
  (when (not-in project.repo by-name)
    (raise (RuntimeEnvInvalid InvalidKind.UNKNOWN-REPO (.format "project の repo {} の checkout が無い" project.repo))))
  (val lock-path (if (= project.path ".")
                     (.format "{}/uv.lock" (get by-name project.repo))
                     (.format "{}/{}/uv.lock" (get by-name project.repo) project.path)))
  (<- lock-hash (| str None) (FileSha256 lock-path))
  (when (is lock-hash None)
    (raise (RuntimeEnvInvalid InvalidKind.BAD-PATH (.format "project の uv.lock が無い: {}" lock-path))))
  (when (is-not sender-repo None)
    (<- (check-sender-source checkouts (tuple repos) sender-repo)))
  (RuntimeEnv :repos (tuple repos)
              :project (PythonProject :repo project.repo :path project.path :lock-sha256 lock-hash :python project.python
                                      :groups project.groups :native project.native)
              :import-roots import-roots :env-vars env-vars :tools tools))


;; venv を持つ project の dir — worker の準備(worker/core/env_prepare)と入口の検め(runtime_identity)が同じ path を使う
;; (#2025 の 3 本目で env_prepare から移した)。
(defk project-dir [env root]
  {:pre [(: env RuntimeEnv) (: root str)] :post [(: % str)]}
  "venv を持つ project の dir(uv の --project に渡す path)。"
  (if (= env.project.path ".")
      (.format "{}/{}" root env.project.repo)
      (.format "{}/{}/{}" root env.project.repo env.project.path)))
