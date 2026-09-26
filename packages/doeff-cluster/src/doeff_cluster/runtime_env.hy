;;; 送り手の側の実行環境の宣言の組み立て(2026-09-26)。
;;;
;;; 送り手は手元の checkout(repo ごとの作業の dir)から宣言(runtime_env_model の RuntimeEnv)を組み立てる。worker は commit を
;;; remote から取りに行くので、worker が取れない物は送る前に断る(例外 RuntimeEnvInvalid):
;;;   dirty-tree            commit していない変更がある(送れるのは commit した物だけ)
;;;   commit-not-on-remote  その commit が remote の branch に無い(push していない)
;;;   sender-source-differs 送り手自身が動いている source(このパッケージの checkout)が、宣言の同じ repo の commit と違う
;;;                         (子で黙って版の不一致になる路を残さない)
;;;
;;;   (<- env (runtime-env-of-checkouts #((LocalCheckout :name "app" :path "/src/app")
;;;                                       (LocalCheckout :name "lib" :path "/src/lib"))
;;;                                     (ProjectOfCheckout :repo "app" :path "." :python "3.14")
;;;                                     #("app/." "app/vendor")
;;;                                     :sender-repo "lib"))
;;;
;;; checkout の読みは effect(ReadCheckout・SenderSourceRoot・FileSha256)。本物の handler は下の local-checkouts(git を呼ぶ)。
(require doeff-hy.macros [defk defhandler <- val var])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])
(import hashlib)
(import pathlib [Path])
(import subprocess)
(import doeff [EffectBase])
(import .runtime_env_model [RepoCheckout PythonProject EnvVar ToolRequirement RuntimeEnv RuntimeEnvInvalid InvalidKind])
(import .env_prepare [FileSha256])


;; --- 入力と答え ---------------------------------------------------------------------------

(defrecord LocalCheckout
  "送り手の手元の checkout 1 つ。name = 宣言の repo の名・path = 作業の dir・remote = worker が取りに行く remote の名。"
  (#^ str name)
  (#^ str path)
  (setv #^ str remote "origin"))


(defrecord ProjectOfCheckout
  "宣言の project の、送り手が書く部分(uv.lock の sha256 は組み立てが checkout から計算する)。"
  (#^ str repo)
  (#^ str path)
  (#^ str python)
  (setv #^ tuple groups #())
  (setv #^ tuple native #()))


(defrecord CheckoutState
  "checkout の読み。head = HEAD の commit・url = remote の URL・dirty = commit していない変更がある・
   on-remote = HEAD が remote の branch のどれかに含まれる。"
  (#^ str head)
  (#^ str url)
  (#^ bool dirty)
  (#^ bool on-remote))


;; --- effect ------------------------------------------------------------------------------

(defclass [(dataclass :frozen True)] ReadCheckout [EffectBase]
  "checkout を読む。答え = CheckoutState。"
  (#^ str path)
  (#^ str remote))


(defclass [(dataclass :frozen True)] SenderSourceRoot [EffectBase]
  "送り手自身が動いている source(このパッケージ)の checkout の根。答え = 絶対 path か None(checkout の外 — 例: wheel で入れた)。")


;; --- 組み立て -----------------------------------------------------------------------------

(defk checked-repo [checkout]
  {:pre [(: checkout LocalCheckout)] :post [(: % RepoCheckout)]}
  "checkout 1 つを宣言の repo にする。worker が取れない commit(汚れたツリー・push していない)はここで断る。"
  (<- seen CheckoutState (ReadCheckout checkout.path checkout.remote))
  (when seen.dirty
    (raise (RuntimeEnvInvalid InvalidKind.DIRTY-TREE
                              (.format "{}({})に commit していない変更がある(送れるのは commit した物だけ)"
                                       checkout.name checkout.path))))
  (when (not seen.on-remote)
    (raise (RuntimeEnvInvalid InvalidKind.COMMIT-NOT-ON-REMOTE
                              (.format "{} の commit {} が remote {} の branch に無い(push していない)"
                                       checkout.name seen.head checkout.remote))))
  (RepoCheckout :name checkout.name :url seen.url :commit seen.head))


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


;; --- handler(実 I/O) ------------------------------------------------------------------

(defk git-output [path args]
  {:pre [(: path str) (: args tuple)] :post [(: % str)]}
  "checkout の中で git を 1 回呼んで標準出力を返す(送り手の手元を読むため)。"
  (val done (subprocess.run ["git" "-C" path #* args] :capture-output True :text True :check True))
  (.strip done.stdout))


(defhandler local-checkouts
  ;; 送り手の手元の checkout を git で読む。remote の branch の知識は手元の追跡の ref(最後の fetch)による。
  (ReadCheckout [path remote]
    (<- head str (git-output path #("rev-parse" "HEAD")))
    (<- url str (git-output path #("remote" "get-url" remote)))
    (<- status str (git-output path #("status" "--porcelain" "--untracked-files=no")))
    (<- containing str (git-output path #("branch" "-r" "--contains" head "--list" (.format "{}/*" remote))))
    (resume (CheckoutState :head head :url url :dirty (bool status) :on-remote (bool containing))))
  (SenderSourceRoot []
    (val here (. (Path __file__) (resolve) parent))
    (val done (subprocess.run ["git" "-C" (str here) "rev-parse" "--show-toplevel"] :capture-output True :text True))
    (resume (if (= done.returncode 0) (.strip done.stdout) None)))
  (FileSha256 [path]
    (val p (Path path))
    (resume (if (.is-file p) (.hexdigest (hashlib.sha256 (.read-bytes p))) None))))
