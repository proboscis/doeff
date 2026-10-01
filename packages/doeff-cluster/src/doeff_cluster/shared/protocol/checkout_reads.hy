;;; 送り手の手元の checkout の読み(ReadCheckout・CheckoutRoot・SenderSourceRoot・FileSha256)を、doeff の汎用の子 process の effect
;;; (RunProcess — git を起こす)と file の effect(StatPath・ReadBytes)へ訳す handler(runtime_env から分けた・agora-redesign #2110)。
;;; I/O を持たない(sha256 は計算だけ)。訳し方・本物と模擬の組は doeff_cluster.shared.core.runtime_env の頭の註。
;;; 訳し方(git は `git -C <path> …` の 1 回ずつ・0 でない終わりは読めない checkout として RuntimeError — 前の本物の check=True と同じ):
;;;   ReadCheckout      rev-parse HEAD → remote get-url <remote> → status --porcelain --untracked-files=no(空でなければ dirty)→
;;;                     branch -r --contains <head> --list <remote>/*(空でなければ on-remote — 知識は手元の追跡の ref・最後の fetch による)
;;;   CheckoutRoot      <path> で rev-parse --show-toplevel。0 でなければ None(checkout の外)
;;;   SenderSourceRoot  CheckoutRoot と同じ問いを SENDER-SOURCE-DIR(この module の dir)で
;;;   FileSha256        StatPath が file なら ReadBytes の sha256・file でなければ None
(require doeff-hy.macros [defk defhandler <- val])
(val MODULE-TAGS {:context "doeff-cluster" :role "protocol"})
(import hashlib)
(import os)
(import doeff_core_effects.process_effects [ProcessOutcome RunProcess])
(import doeff_core_effects.file_effects [PathKind PathStat FileFailed StatPath ReadBytes])
(import doeff_cluster.shared.intent.checkout_model [CheckoutState ReadCheckout CheckoutRoot SenderSourceRoot])
(import doeff_cluster.env_prepare [FileSha256])

;; 送り手自身が動いている source の dir(この module の置き場)。SenderSourceRoot はここで git に checkout の根を聞く — 模擬の git の台本も
;; この値で「送り手の source がどの checkout に在るか」を書く。
(val SENDER-SOURCE-DIR (os.path.dirname (os.path.abspath __file__)))


;; --- 翻訳の handler(汎用の子 process と file の effect へ訳す) ------------------------------------------

(defk git-output [path args]
  {:pre [(: path str) (: args tuple)] :post [(: % str)]}
  "checkout の中で git を 1 回走らせて標準出力を読むため(0 でない終わり・起こせない git は読めない checkout — 例外)。"
  (<- outcome ProcessOutcome (RunProcess :argv (+ #("git" "-C" path) args)))
  (when (!= outcome.exit-code 0)
    (raise (RuntimeError (.format "git -C {} {} が exit {}: {}{}" path (.join " " args) outcome.exit-code
                                  (.strip outcome.stderr) outcome.start-error))))
  (.strip outcome.stdout))


(defk checkout-state-at [path remote]
  {:pre [(: path str) (: remote str)] :post [(: % CheckoutState)]}
  "checkout 1 つの読み(HEAD・remote の URL・汚れ・remote の branch に在るか)を git の 4 問から作るため(頭の註の訳し方)。"
  (<- head str (git-output path #("rev-parse" "HEAD")))
  (<- url str (git-output path #("remote" "get-url" remote)))
  (<- status str (git-output path #("status" "--porcelain" "--untracked-files=no")))
  (<- containing str (git-output path #("branch" "-r" "--contains" head "--list" (.format "{}/*" remote))))
  (CheckoutState :head head :url url :dirty (bool status) :on-remote (bool containing)))


(defk checkout-root [path]
  {:pre [(: path str)] :post [(: % (| str None))]}
  "path を含む checkout の根を git に聞くため(git の外・git を起こせない時は None — 宣言の commit と比べられない)。"
  (<- outcome ProcessOutcome (RunProcess :argv #("git" "-C" path "rev-parse" "--show-toplevel")))
  (if (= outcome.exit-code 0) (.strip outcome.stdout) None))


(defk file-sha256 [path]
  {:pre [(: path str)] :post [(: % (| str None))]}
  "path の file の中身の sha256 を読むため(file でなければ None・在る file を読めなければ例外 — 前の本物の read_bytes と同じ)。"
  (<- stat (StatPath path))
  (if (and (isinstance stat PathStat) (= stat.kind PathKind.FILE))
      (do (<- content (| bytes FileFailed) (ReadBytes path))
          (when (isinstance content FileFailed)
            (raise (RuntimeError (.format "{} を読めない: {}" content.path content.detail))))
          (.hexdigest (hashlib.sha256 content)))
      None))


(defhandler checkout-reads
  ;; 送り手の手元の checkout の読みを、汎用の子 process(git)と file の effect へ訳す(頭の註)。本物と模擬で同じ 1 つ。
  (ReadCheckout [path remote]
    (<- state CheckoutState (checkout-state-at path remote))
    (resume state))
  (CheckoutRoot [path]
    (<- root (| str None) (checkout-root path))
    (resume root))
  (SenderSourceRoot []
    (<- root (| str None) (checkout-root SENDER-SOURCE-DIR))
    (resume root))
  (FileSha256 [path]
    (<- digest (| str None) (file-sha256 path))
    (resume digest)))
