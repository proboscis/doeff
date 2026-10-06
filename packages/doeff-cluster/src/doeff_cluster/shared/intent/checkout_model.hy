;;; 送り手の手元の checkout の読みの型と effect(runtime_env から分けた・#2110)。
;;; 組み立ての Program は doeff_cluster.shared.core.runtime_env、汎用の子 process と file の effect へ訳す handler は
;;; doeff_cluster.shared.protocol.checkout_reads。宣言そのものの型(RuntimeEnv)は doeff_cluster.shared.intent.runtime_env_model。
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
  "宣言の project(主の project と足しの project — 同じ型)の、送り手が書く部分(uv.lock の sha256 は組み立てが checkout から
   project ごとに同じ手順で計算する)。足しの project は native を持てない(宣言の型 RuntimeEnv が断る)。"
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
