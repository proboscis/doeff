;;; 契約の筋書きが走らせる「負荷」(workload)の約束。
;;;
;;; 契約の検(test_contract.hy)は、どの handler の下でも同じ筋書きを撃つ。筋書きが走らせる中身は、image の中の実行 file
;;; `contract-workload` を命令に名指して選ぶ(命令の綴りがこの約束の全部):
;;;
;;;   contract-workload live                走り続ける(止められるまで)。止めの合図(SIGTERM)で直ちに終わる。
;;;   contract-workload exits <code> <秒>   <秒> の後に終了 code <code> で自分で終わる。
;;;   contract-workload stubborn            走り続ける。止めの合図を無視する(猶予が尽きて強制で止められるまで)。
;;;   contract-workload hog                 資源の上限(メモリ)を越えて使い、OOM で殺される。
;;;
;;; 本物の handler(docker・process・k3s)の解釈器は、この約束を守る実行 file の入った image を `Settings.image` に置き、同じ
;;; 筋書きを通す。fake の解釈器は、命令を読んで FakeBehavior を決める(fake-behavior-of)。image の名で「起動できない image」を
;;; 表す(Settings.missing-image)— 本物の解釈器は、存在しない image の名をここに置く。
(require doeff-hy.macros [defk <- val])
(import doeff_run [RunSpec ResourceLimits FakeBehavior])

(setv WORKLOAD "contract-workload")
;; 1 GiB 未満の上限で hog が OOM になる(本物の解釈器は上限を守る handler の下でだけ hog を走らせる)。
(setv HOG-MEMORY-BYTES (* 64 1024 1024))


(defn #^ RunSpec live-spec [#^ str image]
  (RunSpec :image image :command #(WORKLOAD "live")))

(defn #^ RunSpec exits-spec [#^ str image #^ int code #^ float seconds]
  (RunSpec :image image :command #(WORKLOAD "exits" (str code) (str seconds))))

(defn #^ RunSpec stubborn-spec [#^ str image]
  (RunSpec :image image :command #(WORKLOAD "stubborn")))

(defn #^ RunSpec hog-spec [#^ str image]
  (RunSpec :image image :command #(WORKLOAD "hog") :limits (ResourceLimits :memory-bytes HOG-MEMORY-BYTES)))


(defn #^ FakeBehavior fake-behavior-of [#^ RunSpec spec #^ str missing-image]
  "fake の世界が、RunSpec(命令の約束)から負荷の振る舞いを決める関数の本体。image が missing-image なら起動できない。"
  (when (= spec.image missing-image)
    (return (FakeBehavior :refuses-to-begin "image を取り寄せられない(fake)")))
  (val args spec.command)
  (when (or (< (len args) 2) (!= (get args 0) WORKLOAD))
    (raise (ValueError (.format "fake の世界は契約の負荷の命令だけを知る: {!r}" args))))
  (val kind (get args 1))
  (cond
    (= kind "live") (FakeBehavior)
    (= kind "exits") (FakeBehavior :runs-for (float (get args 3)) :exit-code (int (get args 2)))
    (= kind "stubborn") (FakeBehavior :stop-takes None)
    (= kind "hog") (FakeBehavior :runs-for 0.5 :out-of-memory True)
    True (raise (ValueError (.format "契約の負荷の種類を知らない: {!r}" kind)))))
