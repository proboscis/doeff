;;; 送り手の手元の checkout の読みの型と effect(runtime_env から分けた・#2110)。
;;; 組み立ての Program は doeff_cluster._shared.core.runtime_env、汎用の子 process と file の effect へ訳す handler は
;;; doeff_cluster._shared.protocol.checkout_reads。宣言そのものの型(RuntimeEnv)は doeff_cluster._shared.intent.runtime_env_model。
(require doeff-hy.macros [defeffect val])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "doeff-cluster" :role "intent"})
(import dataclasses [dataclass])


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

(defeffect ReadCheckout
  "checkout を読む。答え = CheckoutState。"
  {:fields [(: path str) (: remote str)]
   :answer CheckoutState
   :tags {:context "runtime-env" :role "intent"}})


(defeffect CheckoutRoot
  "path(dir)を含む git の checkout の根。答え = 絶対 path か None(checkout の外 — git を起こせない時も)。"
  {:fields [(: path str)]
   :answer (| str None)
   :tags {:context "runtime-env" :role "intent"}})

(defeffect SenderSourceRoot
  "送り手自身が動いている source(このパッケージ)の checkout の根。答え = 絶対 path か None(checkout の外 — 例: wheel で入れた)。"
  {:answer (| str None)
   :tags {:context "runtime-env" :role "intent"}})


;; --- git を起こさずに読む checkout の file(覚えた答えを使うかの鍵 — protocol/checkout_memo.hy) -----------------------

(defrecord CheckoutFiles
  "git を起こさずに checkout の file から読んだ姿(答えるのは foundation/checkout_files.hy の checkout-files-reader)。root = 作業木の根・
   git-dir = この作業木の git の dir(覚えた答えの置き場)・head = HEAD の commit(HEAD と ref の file から解いた)・index-digest = index の
   file の末尾の checksum(index の中身が同じなら同じ)・config-digest = remote の URL を決める設定の file の中身の sha256・settled = index の
   全部の項の stat が作業木の file と同じで、作業木を見ないと汚れを判じられない項(racy な項・stage の番号のある項・gitlink・skip-worktree
   など)が無い(git status が作業木の file を読み直さずに済む形)。"
  (#^ str root)
  (#^ str git-dir)
  (#^ str head)
  (#^ str index-digest)
  (#^ str config-digest)
  (#^ bool settled))


(defeffect ReadCheckoutFiles
  "path の checkout の file を git を起こさずに読む。答え = CheckoutFiles か None(file から読めない形 — reftable・index の版 4・split index・
   sparse index・生まれていない branch など。読み手は git の読みへ倒す)。"
  {:fields [(: path str)]
   :answer (| CheckoutFiles None)
   :tags {:context "runtime-env" :role "intent"}})


(defeffect FindCheckoutRoot
  "path(dir)から上へ .git を探し、git を起こさずに checkout の根を読む。答え = 絶対 path(symlink を解いた — git rev-parse --show-toplevel と
   同じ)か None(見つからない・.git の dir の中 — 読み手は git の読みへ倒す)。"
  {:fields [(: path str)]
   :answer (| str None)
   :tags {:context "runtime-env" :role "intent"}})
