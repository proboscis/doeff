;;; 実行環境の root の手入れの純粋な判断(2026-09-26・設計 worker-runtime-env.md 節 3.2 の期限・節 3.6 の掃除と disk の条件)。
;;;
;;; worker の EnvStore(handlers.hy)が観測を集めて、ここで決め、実 I/O(dir の削除・準備の process の停止)を行う。
;;;   sweep-choice     空きが下限を切った時に消す root の列(固定・project ごとの最新・worker が作っていない dir は消さない)
;;;   prepare-overdue  準備の期限: 先読みは停滞(処理ステージが進まない)だけ・job の準備は冷たい / 温いで別の期限
;;;   env-capacity     heartbeat で名乗る disk の条件(準備を始める空きが無ければ exhausted)
(require doeff-hy.macros [defk <- val var])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])

;; 既定の値(設計 U10 — 実測で直す)。
(val SWEEP-FLOOR-RATIO 0.15)            ; 空きの下限 = volume の 15%
(val WHEEL-UNUSED-SECONDS (* 7 24 3600)) ; どの root からも使われず 7 日経った native の wheel を消す


(defrecord RootInfo
  "掃除の候補の root 1 つ。key = root の dir 名・project = 同じ project の組の名(完成マーカーの宣言の project の repo の url と path)・
   made-ms = 完成マーカーを置いた時刻・last-used-ms = 最後に job を起こした時刻(.last-used)・bytes = 大きさ・
   owned = worker が作った root か(キーの形の名で完成マーカーを持つ dir だけ — それ以外の dir は消さない)。"
  (#^ str key)
  (#^ str project)
  (#^ int made-ms)
  (#^ int last-used-ms)
  (#^ int bytes)
  (#^ bool owned))


(defrecord PrepareLimits
  "準備の期限の秒: cold-seconds = 冷たい job の準備(root も引き継げる root も無い)・warm-seconds = 温い job の準備・
   stall-seconds = 先読みの停滞(処理ステージが進まない長さ)。既定は設計 U10(30 分 / 5 分 / 10 分)。"
  (setv #^ float cold-seconds 1800.0)
  (setv #^ float warm-seconds 300.0)
  (setv #^ float stall-seconds 600.0))


(defk latest-per-project [roots]
  {:pre [(: roots tuple)] :post [(: % frozenset)]}
  "project ごとに最後に完成した root のキー(bytecode の引き継ぎ元なので消さない)。worker が作った root だけを数える。"
  (var latest {})
  (for [r roots]
    (when r.owned
      (val seen (.get latest r.project))
      (when (or (is seen None) (> r.made-ms seen.made-ms))
        (setv (get latest r.project) r))))
  (frozenset (gfor r (.values latest) r.key)))


(defk sweep-choice [roots pinned free floor]
  {:pre [(: roots tuple) (: pinned frozenset) (: free int) (: floor int)] :post [(: % tuple)]}
  "空き free が下限 floor を切った時に消す root のキーの列(消す順)。disk を空けて次の準備を通すため。
   消さない物: 固定(pinned — 走っている job・準備中・温める表)・project ごとの最新・worker が作っていない dir。
   残りを最後に使った時刻の古い順に、空きが下限を越えるまで選ぶ。越えられなくても選べる物は全部選ぶ。"
  (if (>= free floor)
      #()
      (do (<- keep frozenset (latest-per-project roots))
          (val candidates (sorted (gfor r roots :if (and r.owned (not-in r.key pinned) (not-in r.key keep)) r)
                                  :key (fn [r] #(r.last-used-ms r.key))))
          (var chosen [])
          (var gained free)
          (for [r candidates]
            (when (< gained floor)
              (.append chosen r.key)
              (:= gained (+ gained r.bytes))))
          (tuple chosen))))


(defk prepare-overdue [warm cold started progressed now limits]
  {:pre [(: warm bool) (: cold bool) (: started float) (: progressed float) (: now float) (: limits PrepareLimits)]
   :post [(: % bool)]}
  "準備を止める時か。止めた準備は prepare-timeout(一時)になる。
   先読み(warm)は task を待たせないので期限を掛けず、処理ステージが stall-seconds 進まない(progressed から)時だけ止める。
   job の準備は、冷たい(cold)なら cold-seconds・温いなら warm-seconds を started から数える。"
  (cond
    warm (> (- now progressed) limits.stall-seconds)
    cold (> (- now started) limits.cold-seconds)
    True (> (- now started) limits.warm-seconds)))


(defk env-capacity [free min-free]
  {:pre [(: free int) (: min-free int)] :post [(: % str)]}
  "heartbeat で名乗る disk の条件。準備を始める空きの下限を切っていれば exhausted(coordinator は準備済みでない env の task を置かない)。"
  (if (< free min-free) "exhausted" "ok"))
