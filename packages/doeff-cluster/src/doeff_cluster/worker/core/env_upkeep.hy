;;; 実行環境の root の手入れの純粋な判断(2026-09-26・設計 worker-runtime-env.md 節 3.2 の期限・節 3.6 の掃除と disk の条件)。
;;;
;;; worker の root の言い換え(worker/protocol/env_store の env-host)が観測を集めて、ここで決め、I/O(dir の削除・準備の process の停止)を汎用の効果で出す。
;;;   sweep-candidates 掃除で消してよい root の列(古い順 — 選びと掃除の頭の行が読む)
;;;   sweep-choice     roots の合計が上限を越えた時に消す root の列(固定・project ごとの新しい 2 つ・worker が作っていない dir は消さない)
;;;   sweep-wanted     掃除の係が拍を求めているか(heartbeat の観測 EnvDisk に載せる — worker の判断はこれで SweepEnvs を撃つ)
;;;   sweep-due        新しい掃除(数え)を始める時か(まだ数えていない・完成した root の集合が変わった・上限を越えたままで固定が変わったか
;;;                    前の掃除の終わりから SWEEP-EVERY-MS — #3715・#3732)
;;;   prepare-overdue  準備の期限: 先読みも job の準備も、停滞(進みの印が動かない長さ)だけで止める(合計の時間では止めない — #3515)
;;;   env-capacity     heartbeat で名乗る disk の条件(共有の disk の空きが最低を割っていれば exhausted)
;;;
;;; 掃除の下限は 2 つの絶対の量(#3732 — 以前の「volume の 15% と準備を始める空きの大きい方」の割合は外した。root の置き場は他の物と
;;; 共有の disk に在り、root の外の物で空きが割合の下限を常に割ると、組むたびに固定されていない root を消し続けた — 戻し先の版の root も):
;;;   roots の合計の上限(EnvSettings.roots-cap-bytes) — これを越えた時だけ固定されていない root を消す
;;;   共有の disk の空きの最低(EnvSettings.min-free-bytes) — 割った時は root を消さずに準備を disk-full で断る(env_prepare の stage-disk)
(require doeff-hy.macros [defk <- val var])
(val MODULE-TAGS {:context "worker" :role "program"})
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])

;; 既定の値(設計 U10 — 実測で直す)。
(val WHEEL-UNUSED-SECONDS (* 7 24 3600)) ; どの root からも使われず 7 日経った native の wheel を消す
(val SWEEP-EVERY-MS 30000)              ; roots の合計が上限を越えている間の掃除の間隔(前の掃除の終わりから — 固定の集合が変わった時はすぐ)
;; project ごとに消さない root の数(最後に使った時刻の新しい順): 今の版と 1 つ前に動いていた版(戻し先 — #3732)。
(val KEEP-PER-PROJECT 2)


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
  "準備の期限の秒: stall-seconds = 準備の停滞(進みの印が動かない長さ — 先読みも job の準備も同じ)。既定は設計 U10 の 10 分。
   合計の時間の期限は持たない — 温い job の準備の期限 300 秒は、負荷の高い時の bytecode の処理ステージ(実測 267.9 秒)だけで
   使い切られ、答えを書き終えた直後の準備まで止めていた(#3515)。"
  (setv #^ float stall-seconds 600.0))


(defrecord RootsTally
  "掃除の数えの結び(#3732): ready = 数えを始めた拍の完成した root のキーの集合(heartbeat の観測の READY — 次の拍の集合と比べて、新しく
   完成した root か消えた root が在れば数え直す)・bytes = roots の合計(roots-bytes — hardlink を重ねて数える)。"
  (#^ (get frozenset str) ready)
  (#^ int bytes))


(defk recent-per-project [roots]
  {:pre [(: roots tuple)] :post [(: % frozenset)] :tags {:context "worker" :role "judgment"}}
  "project ごとに、最後に使った時刻の新しい KEEP-PER-PROJECT 個の root のキー(消さない)を返すため。worker が作った root だけを数える。
   今の版の root と、1 つ前に動いていた版の root(戻し先)を、宣言の履歴を持たずに置き場の durable な材料(完成マーカーの project・
   完成の時刻・.last-used)だけで守る(#3732 — worker の記憶は起き直しで読み直されないので、Pod を作り直した直後の戻しでも消えない)。"
  (val owned (tuple (gfor r roots :if r.owned r)))
  (val projects (frozenset (gfor r owned r.project)))
  (frozenset (gfor project projects
                   ;; 新しい順 = 最後に使った時刻の新しい順・同じなら完成の新しい順・同じなら名の順。
                   r (cut (sorted (gfor r owned :if (= r.project project) r) :key (fn [r] #((- r.last-used-ms) (- r.made-ms) r.key)))
                          0 KEEP-PER-PROJECT)
                   r.key)))


(defk roots-bytes [roots]
  {:pre [(: roots tuple)] :post [(: % int)] :tags {:context "worker" :role "judgment"}}
  "roots の合計(byte)を返すため — root ごとの大きさ(MeasureTree)を足す。root どうしは木と .pyc を hardlink で共有する(#3671・#3727)
   ので、共有の file は root ごとに重ねて数える(実の使用量より大きく出る = 早めに消す側)。重ねて数える訳: 選び(sweep-choice)が 1 つ
   消すごとに合計から引く量はその root の数えた大きさで、同じ物差しの上で引き算が正しい(inode を 1 度だけ数えると、消して空く量は root
   ごとの大きさより小さく、選びの見積もりが合わなくなる)。上限の値は重ねて数えた物差しで決める(boot.sh の WORKER_ENV_ROOTS_GIB の註)。"
  ;; worker が作っていない dir は数えない(消せない — root-infos も大きさを測らない)。
  (sum (gfor r roots :if r.owned r.bytes)))


(defk sweep-candidates [roots pinned]
  {:pre [(: roots tuple) (: pinned frozenset)] :post [(: % tuple)] :tags {:context "worker" :role "judgment"}}
  "掃除で消してよい root(RootInfo)を、最後に使った時刻の古い順に求めるため(掃除の選び sweep-choice と、掃除の頭の行の候補の数が同じ
   集合を読む — #3713)。消さない物: 固定(pinned)・project ごとの新しい 2 つ(recent-per-project)・worker が作っていない dir。"
  (<- keep frozenset (recent-per-project roots))
  (tuple (sorted (gfor r roots :if (and r.owned (not-in r.key pinned) (not-in r.key keep)) r)
                 :key (fn [r] #(r.last-used-ms r.key)))))


(defk sweep-choice [roots pinned cap]
  {:pre [(: roots tuple) (: pinned frozenset) (: cap int)] :post [(: % tuple)] :tags {:context "worker" :role "judgment"}}
  "roots の合計(roots-bytes)が上限 cap を越えた時に消す root のキーの列(消す順)を返すため。root の置き場を上限の内に保つ。
   消さない物: 固定(pinned — 走っている job・宣言の job・準備中・温める表)・project ごとの新しい 2 つ・worker が作っていない dir。
   残りを最後に使った時刻の古い順に、合計が上限の内へ戻るまで選ぶ。戻れなくても選べる物は全部選ぶ。共有の disk の空きは見ない
   (空きが最低を割った時は root を消さずに準備を断る — env_prepare の stage-disk・env-capacity)。"
  (<- total int (roots-bytes roots))
  (if (<= total cap)
      #()
      (do (<- candidates tuple (sweep-candidates roots pinned))
          (var chosen #())
          (var left total)
          (for [r candidates]
            (when (> left cap)
              (:= chosen (+ chosen #(r.key)))
              (:= left (- left r.bytes))))
          chosen)))


(defk sweep-wanted [running tally ready cap]
  {:pre [(: running bool) (: tally (| RootsTally None)) (: ready frozenset) (: cap int)] :post [(: % bool)]
   :tags {:context "worker" :role "judgment"}}
  "掃除の係が拍(SweepEnvs)を求めているかを判じるため(heartbeat の観測 EnvDisk の sweep-wanted — worker の判断は固定の集合が変わった時と
   これが真の時に撃つ): 掃除が走っている(running — 数えと消しは拍ごとに答えを読んで進む)・まだ数えていない(tally = None)・完成した
   root の集合 ready が数えた時と違う・数えた合計が上限 cap を越えている。"
  (match tally
    None True
    (RootsTally) (or running (!= tally.ready ready) (> tally.bytes cap))))


(defk sweep-due [tally ready cap changed now-ms swept-ms]
  {:pre [(: tally (| RootsTally None)) (: ready frozenset) (: cap int) (: changed bool) (: now-ms int) (: swept-ms int)] :post [(: % bool)]
   :tags {:context "worker" :role "judgment"}}
  "新しい掃除(数え)を始める時かを判じるため: まだ数えていない(tally = None)・完成した root の集合 ready が数えた時と違う(新しく完成した
   root か消えた root — すぐ数え直す)・数えた合計が上限 cap を越えたままで、固定の集合が変わったか前の掃除の終わり swept-ms から
   SWEEP-EVERY-MS が経った時。合計が上限の内で完成した root の集合が変わらない間は数えない(数えは root ごとに木を歩く重い仕事)。
   走っている掃除が在る間は呼び手が判じない(同時に 1 つ)。"
  (match tally
    None True
    (RootsTally) (or (!= tally.ready ready)
                     (and (> tally.bytes cap) (or changed (>= (- now-ms swept-ms) SWEEP-EVERY-MS))))))


(defk prepare-overdue [progressed now limits]
  {:pre [(: progressed float) (: now float) (: limits PrepareLimits)] :post [(: % bool)]}
  "準備を止める時か。止めた準備は prepare-timeout(一時)になる。
   先読みも job の準備も、最後の進み(progressed — 進みの印の時刻)から stall-seconds 進まない時だけ止める。進んでいる準備は、
   起こしてから長くても止めない。"
  (> (- now progressed) limits.stall-seconds))


(defk env-capacity [free min-free]
  {:pre [(: free int) (: min-free int)] :post [(: % str)]}
  "heartbeat で名乗る disk の条件。共有の disk の空きが最低 min-free を割っていれば exhausted(coordinator は準備済みでない env の task を
   置かない — 準備の process も同じ値で disk-full と断る)。"
  (if (< free min-free) "exhausted" "ok"))
