;;; 実行環境の root の手入れの純粋な判断(2026-09-26・設計 worker-runtime-env.md 節 3.2 の期限・節 3.6 の掃除と disk の条件)。
;;;
;;; worker の root の言い換え(worker/protocol/env_store の env-host)が観測を集めて、ここで決め、I/O(dir の削除・準備の process の停止)を汎用の効果で出す。
;;;   sweep-candidates 掃除で消してよい root の列(古い順 — 選びと掃除の頭の行が読む)
;;;   sweep-choice     roots の合計が上限を越えた時か、共有の disk の空きが最低を割った時に消す root の列(固定・project ごとの新しい 2 つ・
;;;                    worker が作っていない dir は消さない)
;;;   sweep-wanted     掃除の係が拍を求めているか(heartbeat の観測 EnvDisk に載せる — worker の判断はこれで SweepEnvs を撃つ)
;;;   sweep-due        新しい掃除(数え)を始める時か(まだ数えていない・完成した root の集合が変わった・空きが最低を割っているかが数えた時と
;;;                    違う・空きが割ったままで固定が変わった・上限を越えたままで固定が変わったか前の掃除の終わりから SWEEP-EVERY-MS —
;;;                    #3715・#3732・#4051)
;;;   prepare-overdue  準備の期限: 先読みも job の準備も、停滞(進みの印が動かない長さ)だけで止める(合計の時間では止めない — #3515)
;;;   env-capacity     heartbeat で名乗る disk の条件(共有の disk の空きが最低を割っていれば exhausted・最低 + NEAR-MARGIN-BYTES を割っていれば near)
;;;   warm-refusal     先の組み(温める表の行)を始める前に断るか(no-disk-room・over-roots-cap・no-memory-room — #3748)。見積もりは
;;;                    root-estimate(disk)と build-memory-estimate(memory の山)・memory の読みは memory-use-of
;;;
;;; 掃除の下限は 2 つの絶対の量(#3732 — 以前の「volume の 15% と準備を始める空きの大きい方」の割合は外した。root の置き場は他の物と
;;; 共有の disk に在り、root の外の物で空きが割合の下限を常に割ると、組むたびに固定されていない root を消し続けた — 戻し先の版の root も):
;;;   roots の合計の上限(EnvSettings.roots-cap-bytes) — 越えた時は固定されていない root を古い順に、合計が上限の内へ戻るまで消す
;;;   共有の disk の空きの最低(EnvSettings.min-free-bytes) — 割った時も同じ候補を古い順に、空きが最低へ戻るまで消す(#4051)。候補を
;;;                    全部消しても戻らない時は、今までどおり準備を disk-full で断る(env_prepare の stage-disk)
;;; 空きで消しても戻し先は消えない: 候補は project ごとの新しい 2 つ(今の版と戻し先 — #3732 の KEEP-PER-PROJECT)の外だけ。#3732 で
;;; 空きの割合で消すのを外した訳(戻し先の版の root まで消し続けた)は、新しい 2 つを守る作りが入った今は当たらない。空きで起こす掃除は時刻で
;;; 繰り返さない: 数えの結びに空きの状態を持ち、状態が変わった時・割ったまま固定が変わった時・完成した root の集合が変わった時だけ数え直す
;;; (#4051 — 本番の実例: 空き 24.96 GiB < 最低 25 GiB・roots の合計 0.86 GB ≤ 上限 20 GiB・候補 2 で、毎回 chosen=0 のまま準備を
;;; 断り続けた)。
(require doeff-hy.macros [defk <- val var])
(val MODULE-TAGS {:context "worker" :role "program"})
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])
(import doeff_cluster.shared.intent.runtime_env_model [EnvFailure EnvFailureKind])

;; 既定の値(設計 U10 — 実測で直す)。
(val WHEEL-UNUSED-SECONDS (* 7 24 3600)) ; どの root からも使われず 7 日経った native の wheel を消す
;; 7 日使われない bytecode の保存先の entry を消す(native の wheel と同じ 7 日の作法 — entry は使うたびに時刻を進める・#3858)。
(val CODE-STORE-UNUSED-SECONDS WHEEL-UNUSED-SECONDS)
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
  (#^ bool owned)
  ;; 組みの山の memory(byte — 完成マーカーの buildMemoryBytes。測れなかった組み・欄の無い前の印は None・#3748)。
  (setv #^ (| int None) build-memory-bytes None))


(defrecord PrepareLimits
  "準備の期限の秒: stall-seconds = 準備の停滞(進みの印が動かない長さ — 先読みも job の準備も同じ)。既定は設計 U10 の 10 分。
   合計の時間の期限は持たない — 温い job の準備の期限 300 秒は、負荷の高い時の bytecode の処理ステージ(実測 267.9 秒)だけで
   使い切られ、答えを書き終えた直後の準備まで止めていた(#3515)。"
  (setv #^ float stall-seconds 600.0))


(defrecord RootsTally
  "掃除の数えの結び(#3732): ready = 数えを始めた拍の完成した root のキーの集合(heartbeat の観測の READY — 次の拍の集合と比べて、新しく
   完成した root か消えた root が在れば数え直す)・bytes = roots の合計(roots-bytes — hardlink を重ねて数える)・below-min-free = 数えの
   答えを受けた時に共有の disk の空きが最低を割っていたか(今の空きの状態と違えば数え直す — #4051)。"
  (#^ (get frozenset str) ready)
  (#^ int bytes)
  (#^ bool below-min-free))


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


(defk sweep-choice [roots pinned cap free min-free]
  {:pre [(: roots tuple) (: pinned frozenset) (: cap int) (: free int) (: min-free int)] :post [(: % tuple)]
   :tags {:context "worker" :role "judgment"}}
  "roots の合計(roots-bytes)が上限 cap を越えた時か、共有の disk の空き free が最低 min-free を割った時に消す root のキーの列(消す順)を
   返すため。root の dir の合計を上限の内に、共有の disk の空きを最低の上に保つ(#4051)。
   消さない物: 固定(pinned — 走っている job・宣言の job・準備中・温める表)・project ごとの新しい 2 つ・worker が作っていない dir。
   残りを最後に使った時刻の古い順に、合計が上限の内へ戻り、かつ空きが最低へ戻るまで選ぶ(空きの見積もりは消す root の数えた大きさを
   足す — hardlink を重ねて数えるので実際に空く量より大きく、足りなければ消した後の数え直しで残りを選ぶ)。戻れなくても選べる物は
   全部選ぶ(候補を全部消しても空きが戻らない時は、準備が disk-full で断る — env_prepare の stage-disk・env-capacity)。"
  (<- total int (roots-bytes roots))
  (if (and (<= total cap) (>= free min-free))
      #()
      (do (<- candidates tuple (sweep-candidates roots pinned))
          (var chosen #())
          (var left total)
          (var room free)
          (for [r candidates]
            (when (or (> left cap) (< room min-free))
              (:= chosen (+ chosen #(r.key)))
              (:= left (- left r.bytes))
              (:= room (+ room r.bytes))))
          chosen)))


(defk sweep-wanted [running tally ready cap low]
  {:pre [(: running bool) (: tally (| RootsTally None)) (: ready frozenset) (: cap int) (: low bool)] :post [(: % bool)]
   :tags {:context "worker" :role "judgment"}}
  "掃除の係が拍(SweepEnvs)を求めているかを判じるため(heartbeat の観測 EnvDisk の sweep-wanted — worker の判断は固定の集合が変わった時と
   これが真の時に撃つ): 掃除が走っている(running — 数えと消しは拍ごとに答えを読んで進む)・まだ数えていない(tally = None)・完成した
   root の集合 ready が数えた時と違う・数えた合計が上限 cap を越えている・今の空きが最低を割っているか(low)が数えた時と違う(#4051 —
   割ったままで数え済みなら求めない。割ったまま固定が変われば判断の側が固定の変わりで SweepEnvs を出す)。"
  (match tally
    None True
    (RootsTally) (or running (!= tally.ready ready) (> tally.bytes cap) (!= low tally.below-min-free))))


(defk sweep-due [tally ready cap low changed now-ms swept-ms]
  {:pre [(: tally (| RootsTally None)) (: ready frozenset) (: cap int) (: low bool) (: changed bool) (: now-ms int) (: swept-ms int)]
   :post [(: % bool)] :tags {:context "worker" :role "judgment"}}
  "新しい掃除(数え)を始める時かを判じるため: まだ数えていない(tally = None)・完成した root の集合 ready が数えた時と違う(新しく完成した
   root か消えた root — すぐ数え直す)・今の空きが最低を割っているか(low)が数えた時と違う・空きが最低を割ったままで固定の集合が
   変わった(止まった job の root が候補になる — #4051)・数えた合計が上限 cap を越えたままで、固定の集合が変わったか前の掃除の終わり
   swept-ms から SWEEP-EVERY-MS が経った時。空きが割ったままの間は時刻では数え直さない(候補が増えない限り、数え直しても選べる物は
   同じ — #4051)。合計が上限の内・空きが最低の上で完成した root の集合が変わらない間は数えない(数えは root ごとに木を歩く重い仕事)。
   走っている掃除が在る間は呼び手が判じない(同時に 1 つ)。"
  (match tally
    None True
    (RootsTally) (or (!= tally.ready ready)
                     (!= low tally.below-min-free)
                     (and low changed)
                     (and (> tally.bytes cap) (or changed (>= (- now-ms swept-ms) SWEEP-EVERY-MS))))))


(defk prepare-overdue [progressed now limits]
  {:pre [(: progressed float) (: now float) (: limits PrepareLimits)] :post [(: % bool)]}
  "準備を止める時か。止めた準備は prepare-timeout(一時)になる。
   先読みも job の準備も、最後の進み(progressed — 進みの印の時刻)から stall-seconds 進まない時だけ止める。進んでいる準備は、
   起こしてから長くても止めない。"
  (> (- now progressed) limits.stall-seconds))


;; 空きの予告の幅(byte)— 空きが最低 + この幅を割ったら near を名乗る。2026-10-10 に空きが 1 時間に約 5 GB 減って最低を切り、切ってから
;; 分かった(準備が全部止まった後)。20 GB は約 4 時間の手前。
(val NEAR-MARGIN-BYTES (* 20 (** 10 9)))


(defk env-capacity [free min-free]
  {:pre [(: free int) (: min-free int)] :post [(: % str)]}
  "heartbeat で名乗る disk の条件。共有の disk の空きが最低 min-free を割っていれば exhausted(coordinator は準備済みでない env の task を
   置かない — 準備の process も同じ値で disk-full と断る)。最低 + NEAR-MARGIN-BYTES を割っていれば near(置き方は ok と同じ — 最低を
   切る前に読み手が予告するための語)。最低 0(下限を置かない worker)は ok。"
  (cond
    (< free min-free) "exhausted"
    (and (> min-free 0) (< free (+ min-free NEAR-MARGIN-BYTES))) "near"
    True "ok"))


;; --- 先の組みを始める前の断り(#3748・#3671 の子)---------------------------------------------------------------------
;; 実例(2026-10-06 05:24〜05:36・screen-worker): 先の組み(POST /warm → 温める表の行 → PrepareEnv :warm)が空きや memory の足りない台でも
;; 始まり、同じ Longhorn の PVC へ小さい file を大量に書いて空きが掃除の下限を割り、worker のループが約 11 分止まった。先の組みは回の前の
;; 用意なので、足りない台では始めずに断り、断りの種類を AwaitWarm の WarmFailed で頼み手へ返す。宣言された job の準備は断らない
;; (今までどおり disk-full の判じだけ — 呼び手 env_store の env-host が warm の頼みにだけこの判じを当てる)。

;; 組む root 1 つの disk の見積もりの既定(byte)— 同じ project の完成した root を数えていない時。専用の台の root は 1 つ 0.19〜0.25 GB
;; (du -sbl — hardlink を重ねて数える roots-bytes と同じ物差し・#3732 の実測)なので、その上の端を丸めた 256 MiB。大きく置くと、上限の小さい台
;; (WORKER_ENV_ROOTS_GIB=1)で新しい project の先の組みが一度も通らない(job の準備は断らないので、そこで組めば次から実測が入る)。
(val DEFAULT-ROOT-BYTES (* 256 1024 1024))
;; 組みの山の memory の見積もりの既定(byte)— 同じ project の前の組みの実測(完成マーカーの buildMemoryBytes)が無い時。実測がまだ 1 つも
;; 無いので、本番の 2Gi の container の入口の線(× 0.75 = 1.5GiB)の 3 分の 1 の 512 MiB に置く(初めの組みを 1 本は通し、実測で置き換える)。
(val DEFAULT-BUILD-MEMORY-BYTES (* 512 1024 1024))
;; memory の入口の線(memory.max に掛ける割合)。c2-w28 の外の見張りは 2Gi の 1.6GiB(0.8)か oom_kill で組みを取り消すので、入口はそれより下の
;; 0.75 に置く — 0.9 だと入口を通った組みを見張りが途中で取り消す形が残る(#3748 の c2-w36 の決め)。
(val MEMORY-ROOM-RATIO 0.75)


(defrecord MemoryUse
  "worker の container の cgroup(v2)の memory の読み: current = memory.current・limit = memory.max(byte)。"
  {:tags {:context "worker" :role "type"}}
  (#^ int current)
  (#^ int limit))


(defrecord MemoryUnread
  "memory を測れなかった(cgroup v1・file が無い・上限が max = 無い・数でない)。reason = 訳(worker の log の 1 行と、先の組みを断らずに始めた
   印の訳)。"
  {:tags {:context "worker" :role "type"}}
  (#^ str reason))


(defrecord WarmRoom
  "先の組みを始める前の読み 1 つ: free = 共有の disk の空き・min-free = 空きの最低・root-bytes = 組む root 1 つの見積もり・roots = roots の
   合計(最後の数え — まだ数えていなければ None = 判じない)・reclaimable = 掃除で空けられる分(固定でない root の大きさの和)・cap = roots の
   合計の上限・memory = memory の読み・peak = 組みの山の memory の見積もり。量は全部 byte。"
  {:tags {:context "worker" :role "type"}}
  (#^ int free)
  (#^ int min-free)
  (#^ int root-bytes)
  (#^ (| int None) roots)
  (#^ int reclaimable)
  (#^ int cap)
  (#^ (| MemoryUse MemoryUnread) memory)
  (#^ int peak))


(defk memory-use-of [current limit]
  {:pre [(: current (| str None)) (: limit (| str None))] :post [(: % (| MemoryUse MemoryUnread))]
   :tags {:context "worker" :role "judgment"}}
  "cgroup v2 の memory.current と memory.max の中身(読めなければ None)を memory の読みにするため。読めない・上限が max(無い)・数でない時は
   MemoryUnread(訳つき — 呼び手は断らずに組み、測っていない印を立てる)。"
  (val now (if (is current None) "" (.strip current)))
  (val cap (if (is limit None) "" (.strip limit)))
  (match #(current limit)
    #(None _) (MemoryUnread :reason "memory.current が読めない(cgroup v1 か file が無い)")
    #(_ None) (MemoryUnread :reason "memory.max が読めない(cgroup v1 か file が無い)")
    _ :if (= cap "max") (MemoryUnread :reason "memory.max が max(container の memory の上限が無い)")
    _ :if (not (and (.isdigit now) (.isdigit cap))) (MemoryUnread :reason (.format "memory.current {!r} か memory.max {!r} が数でない" now cap))
    _ (MemoryUse :current (int now) :limit (int cap))))


(defk latest-of-project [roots project]
  {:pre [(: roots tuple) (: project str)] :post [(: % tuple)] :tags {:context "worker" :role "judgment"}}
  "同じ project の worker が作った root を、完成の新しい順(同じなら名の順)に並べるため(見積もりの元)。"
  (tuple (sorted (gfor r roots :if (and r.owned (= r.project project)) r) :key (fn [r] #((- r.made-ms) r.key)))))


(defk root-estimate [roots project]
  {:pre [(: roots tuple) (: project str)] :post [(: % int)] :tags {:context "worker" :role "judgment"}}
  "組む root 1 つの disk の見積もり(byte)を返すため: 同じ project の最後に組んだ root の大きさ(掃除の数え — hardlink を重ねて数える
   roots-bytes と同じ物差し)。同じ project の root を数えていなければ DEFAULT-ROOT-BYTES。"
  (<- latest tuple (latest-of-project roots project))
  (if latest (. (get latest 0) bytes) DEFAULT-ROOT-BYTES))


(defk build-memory-estimate [roots project]
  {:pre [(: roots tuple) (: project str)] :post [(: % int)] :tags {:context "worker" :role "judgment"}}
  "組みの山の memory の見積もり(byte)を返すため: 同じ project の root のうち、組みの山を測れた最も新しい物の実測(完成マーカーの
   buildMemoryBytes)。無ければ DEFAULT-BUILD-MEMORY-BYTES。"
  (<- latest tuple (latest-of-project roots project))
  (next (gfor r latest :if (is-not r.build-memory-bytes None) r.build-memory-bytes) DEFAULT-BUILD-MEMORY-BYTES))


(defk reclaimable-bytes [roots pinned]
  {:pre [(: roots tuple) (: pinned frozenset)] :post [(: % int)] :tags {:context "worker" :role "judgment"}}
  "掃除で空けられる分(byte)を返すため = 掃除の候補(sweep-candidates — 固定・project ごとの新しい 2 つ・worker が作っていない dir を除く)の
   大きさの和。"
  (<- candidates tuple (sweep-candidates roots pinned))
  (sum (gfor r candidates r.bytes)))


(defk warm-refusal [room]
  {:pre [(: room WarmRoom)] :post [(: % (| EnvFailure None))] :tags {:context "worker" :role "judgment"}}
  "先の組みを始めずに断るかを判じるため(判じるのはここ 1 か所)。断る時は種類つきの失敗(恒久 — AwaitWarm が即座に WarmFailed で返す)、
   始めてよければ None。順は disk の空き → roots の上限 → memory:
     no-disk-room    空き < 空きの最低 + root 1 つの見積もり
     over-roots-cap  roots の合計 + root 1 つの見積もり − 掃除で空けられる分 > 上限(まだ数えていなければ判じない)
     no-memory-room  memory.current + 組みの山の見積もり > memory.max × MEMORY-ROOM-RATIO(memory を測れなければ判じない — 呼び手が
                     測っていない印を立てる)"
  (val need-free (+ room.min-free room.root-bytes))
  (match room
    _ :if (< room.free need-free)
      (EnvFailure :kind EnvFailureKind.NO-DISK-ROOM :retryable False
                  :detail (.format "先の組みを断った: 共有の disk の空き {} byte < 空きの最低 {} byte + root 1 つの見積もり {} byte"
                                   room.free room.min-free room.root-bytes))
    (WarmRoom :roots total :root-bytes extra :reclaimable freed :cap cap)
      :if (and (is-not total None) (> (- (+ total extra) freed) cap))
      (EnvFailure :kind EnvFailureKind.OVER-ROOTS-CAP :retryable False
                  :detail (.format "先の組みを断った: roots の合計 {} byte + root 1 つの見積もり {} byte − 掃除で空けられる {} byte > 上限 {} byte"
                                   total extra freed cap))
    (WarmRoom :memory (MemoryUse :current current :limit limit) :peak peak) :if (> (+ current peak) (* limit MEMORY-ROOM-RATIO))
      (EnvFailure :kind EnvFailureKind.NO-MEMORY-ROOM :retryable False
                  :detail (.format "先の組みを断った: memory.current {} byte + 組みの山の見積もり {} byte > memory.max {} byte × {}"
                                   current peak limit MEMORY-ROOM-RATIO))
    _ None))
