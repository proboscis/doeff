;;; sim-cluster(doeff_cluster.local)の検(test_local.hy・test_remote.hy)の service と task の Program の見本。
;;;
;;; どの Program も土台(tests.fixtures.envs の sim-foundation — scheduler と時計を含まない sim の土台)で本体を包む。service どうしは
;;; 盤(ReadShared / WriteShared)でだけつながり、宿の契約の environ は Ask で読む。
(require doeff-hy.macros [defk deff defsystem defhandler defeffect do! <- val var])
(import collections.abc [Callable])
(import doeff [with-handlers DoExpr EffectBase Program])
(import doeff_core_effects.effects [Ask])
(import doeff_core_effects.handlers [reader])
(import doeff_core_effects.scheduler [Spawn Wait])
(import doeff_time [Delay])
(import doeff_cluster.clock [now-epoch-ms])
(import doeff_cluster.local [ProcessesOf])
(import doeff_cluster.metrics_model [ReportMetrics])
(import doeff_cluster.readiness_model [ReportReady])
(import doeff_cluster.remote_model [RemoteJob])
(import doeff_cluster.shared_model [ReadShared WriteShared])
(import doeff_cluster.detached_model [SubmitDetached AwaitDetached DetachedSucceeded])
(import doeff_cluster.host_contract [HOST-CONTRACT])
(import doeff_cluster.job_context [RunContext])

(val NET (frozenset ["cluster-net"]))


;; --- 業務の effect と、service ごとに違う答えを持つ handler ---------------------------------------------------

(defeffect Flavor
  "見本の業務の effect: この service の味(答え = 文字列)。答えは service ごとに並べる handler が持つ。"
  {:answer str
   :tags {:context "doeff-cluster-test" :role "intent"}})


(defhandler flavor [#^ str taste]
  {:tags {:context "doeff-cluster-test" :role "protocol"}}
  ;; 引数に残す理由: 同じ effect の型に service ごとに別の答えを持たせる見本(別スコープの検)。
  (Flavor []
    (resume taste)))


;; --- 詰められるが解けない値(Program を解けない宿の枝の見本)------------------------------------------------------

(deff refuse-to-load []  ; defk にできない: cloudpickle が詰めた値を解く時に呼ぶ素の関数(Program の外)
  {:pre [] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "詰めた値を解く時に必ず断るため(実行先で Program を解けない — 版の違い・消えた module の代役)。"
  (raise (RuntimeError "解けない値(検の見本)")))


(defclass Unloadable []  ; class にする理由: cloudpickle の詰め方(__reduce__ の約束)が class を要求する — 欄も状態も持たない
  "詰めることはできるが、解く時に refuse-to-load を呼んで断る値。"
  (defn __reduce__ [self]
    #(refuse-to-load #())))


(defk holding-unloadable [foundation value]
  {:pre [(: foundation Callable) (: value Unloadable)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "service の反例: 解けない値を引数に持つ(詰めて置けるが、実行先で解けない)。"
  (<- n int (foundation (beacon-loop "unloadable/beat" 1.0)))
  n)


;; --- service の本体 ------------------------------------------------------------------------------------

(defk beacon-loop [key every]
  {:pre [(: key str) (: every float)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "program"}}
  "拍ごとに、宣言の environ の STEP と拍の数を盤の key に書き、準備できたと報告し、計器(拍の数)を報告する(止められるまで続ける)。"
  (<- step str (Ask "STEP"))
  (var n 0)
  (while True
    (:= n (+ n 1))
    (<- written bool (WriteShared key {"step" step "n" n}))
    (<- (ReportReady written "書けた"))
    (<- (ReportMetrics {"counters" {"beats" (float n)}}))
    (<- (Delay every)))
  n)


(defk copy-loop [source target every]
  {:pre [(: source str) (: target str) (: every float)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "program"}}
  "拍ごとに盤の source の行を読み、在れば target へ写す(service どうしは盤でだけつながる)。"
  (var copies 0)
  (while True
    (<- rows dict (ReadShared source))
    (when (in source rows)
      (<- (WriteShared target (get rows source)))
      (:= copies (+ copies 1)))
    (<- (ReportReady True "読んだ"))
    (<- (Delay every)))
  copies)


(defk flavor-loop [key every]
  {:pre [(: key str) (: every float)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "program"}}
  "拍ごとに Flavor の答えを盤の key に書く(答えるのは本体を包んだ側が並べた handler)。"
  (var n 0)
  (while True
    (<- taste str (Flavor))
    (<- (WriteShared key taste))
    (<- (ReportReady True taste))
    (:= n (+ n 1))
    (<- (Delay every)))
  n)


(defk passable-body [key]
  {:pre [(: key str)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "program"}}
  "sim の外側が答える物(scheduler の Spawn / Wait・時計の Delay / GetTime)だけを使う本体: 子の task を起こして待ち、経った時間を
   盤の key に書いてから、準備できたと報告し続ける。"
  (<- started int (now-epoch-ms))
  (<- child (Spawn (do! (<- (Delay 0.5)) 21)))
  (<- half int (Wait child))
  (<- ended int (now-epoch-ms))
  (<- (WriteShared key {"answer" (* 2 half) "elapsedMs" (- ended started)}))
  (while True
    (<- (ReportReady True "通った"))
    (<- (Delay 1.0)))
  0)


(defk peeking-body []
  {:pre [] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "program"}}
  "反例: sim の世界だけが答える検の effect(ProcessesOf)を service の中から出す — 本番の子には答える物が無いので、柵で落ちる。"
  (<- seen tuple (ProcessesOf "peeker"))
  (len seen))


(defk delegate-body [n key]
  {:pre [(: n int) (: key str)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "program"}}
  "RemoteJob で task を 2 つ出す: 自分の reader を持つ add-task(答え = 100 + n)と、reader を持たない orphan-task(呼び手の reader が
   届かなければ失敗する)。呼び手は base = 1 の reader の下で出す。結果を盤の key に書いてから、準備できたと報告し続ける。"
  (<- sum int (with-handlers [(reader {"base" 1})] (RemoteJob (add-task sim-task-foundation n) :needs NET :name "add")))
  (var orphan "")
  (try
    (<- got int (with-handlers [(reader {"base" 1})] (RemoteJob (orphan-task sim-task-foundation) :needs NET :name "orphan")))
    (:= orphan (+ "答えた " (str got)))
    (except [error Exception]
      (:= orphan (+ "失敗 " (. (type error) __name__) ": " (str error)))))
  (<- (WriteShared key {"sum" sum "orphan" orphan}))
  (while True
    (<- (ReportReady True "出した"))
    (<- (Delay 1.0)))
  0)


(defk child-writer [prefix instance]
  {:pre [(: prefix str) (: instance str)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "program"}}
  "process の中で Spawn した task の見本: 盤の <prefix><世代の名> に拍の数を書き続ける(process と一緒に止まるかを盤で見るため)。"
  (var n 0)
  (while True
    (:= n (+ n 1))
    (<- (WriteShared (+ prefix instance) {"n" n}))
    (<- (Delay 1.0)))
  n)

(defk spawning-body [prefix beats]
  {:pre [(: prefix str) (: beats int)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "program"}}
  "子の task(child-writer)を起こして盤に書き続けさせ、自分は準備できたと報告し続ける。beats > 0 なら beats 拍の後に値で抜ける
   (process が終わる)。子は自分の世代の名(宿の契約の run-context)を鍵に書く。"
  (<- ctx RunContext (Ask HOST-CONTRACT.run-context-key))
  (<- (Spawn (child-writer prefix ctx.instance)))
  (var n 0)
  (while (or (<= beats 0) (< n beats))
    (<- (ReportReady True "子が書いている"))
    (<- (Delay 1.0))
    (:= n (+ n 1)))
  n)

(defk pulse-body []
  {:pre [] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "program"}}
  "準備できたと報告し続けるだけ(coordinator に届かなくても落ちない — 報告は観測)。"
  (var n 0)
  (while True
    (<- (ReportReady True "動いている"))
    (<- (Delay 1.0))
    (:= n (+ n 1)))
  n)

(defk detaching-body [n key]
  {:pre [(: n int) (: key str)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "program"}}
  "切り離した task を 1 本出して待ち(自分の reader を持つ add-task — 答え = 100 + n)、答えを盤の key に書いてから、準備できたと報告
   し続ける(sim の宿が SubmitDetached・AwaitDetached に本番と同じ要求で答えるかを見るため)。"
  (<- submitted (SubmitDetached (add-task sim-task-foundation n) :key "svc-detached" :needs NET :name "add"))
  (<- outcome (AwaitDetached submitted.key))
  (<- (WriteShared key {"created" submitted.created
                        "value" (if (isinstance outcome DetachedSucceeded) outcome.value None)
                        "outcome" (. (type outcome) __name__)}))
  (while True
    (<- (ReportReady True "出した"))
    (<- (Delay 1.0)))
  0)


;; --- task の Program ----------------------------------------------------------------------------------

(defk sim-task-foundation [body]
  {:pre [(: body (| Program EffectBase))] :post [(: % "body の答え")] :needs #{"cluster-net"}
   :tags {:context "doeff-cluster-test" :role "foundation"}}
  "task の sim の土台: 何も並べない(scheduler と時計は sim の外側が答える)。"
  (<- answer body)
  answer)


(defk add-task [foundation n]
  {:pre [(: foundation Callable) (: n int)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "task: 自分で並べた reader(base = 100)の答えに n を足す。"
  (<- total int (foundation (with-handlers [(reader {"base" 100})] (do! (<- base int (Ask "base")) (+ base n)))))
  total)


(defk orphan-task [foundation]
  {:pre [(: foundation Callable)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "task の反例: reader を並べずに Ask \"base\" を出す — 別の process なので呼び手の reader は届かず、答えが無い。"
  (<- base int (foundation (Ask "base")))
  base)


;; --- service の Program(土台で本体を包む)--------------------------------------------------------------------

(defk beacon-program [foundation key every]
  {:pre [(: foundation Callable) (: key str) (: every float)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "service: beacon-loop を土台で包む。"
  (<- n int (foundation (beacon-loop key every)))
  n)


(defk copy-program [foundation source target]
  {:pre [(: foundation Callable) (: source str) (: target str)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "service: copy-loop を土台で包む。"
  (<- n int (foundation (copy-loop source target 1.0)))
  n)


(defk scoped-program [foundation taste key]
  {:pre [(: foundation Callable) (: taste str) (: key str)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "service: 自分の Flavor の handler(答え = taste)を本体の中で作って並べる。"
  (<- n int (foundation (with-handlers [(flavor taste)] (flavor-loop key 1.0))))
  n)


(defk unscoped-program [foundation key]
  {:pre [(: foundation Callable) (: key str)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "service の反例: Flavor の handler を並べない(他の service の handler が混ざらなければ答えが無い)。"
  (<- n int (foundation (flavor-loop key 1.0)))
  n)


(defk passable-program [foundation key]
  {:pre [(: foundation Callable) (: key str)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "service: passable-body を土台で包む。"
  (<- n int (foundation (passable-body key)))
  n)


(defk peeking-program [foundation]
  {:pre [(: foundation Callable)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "service の反例: peeking-body を土台で包む。"
  (<- n int (foundation (peeking-body)))
  n)


(defk delegate-program [foundation n key]
  {:pre [(: foundation Callable) (: n int) (: key str)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "service: delegate-body を土台で包む。"
  (<- r int (foundation (delegate-body n key)))
  r)


(defk spawning-program [foundation prefix beats]
  {:pre [(: foundation Callable) (: prefix str) (: beats int)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "service: spawning-body を土台で包む。"
  (<- n int (foundation (spawning-body prefix beats)))
  n)

(defk pulse-program [foundation]
  {:pre [(: foundation Callable)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "service: pulse-body を土台で包む。"
  (<- n int (foundation (pulse-body)))
  n)

(defk detaching-program [foundation n key]
  {:pre [(: foundation Callable) (: n int) (: key str)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "service: detaching-body を土台で包む。"
  (<- r int (foundation (detaching-body n key)))
  r)

;; --- 系 ------------------------------------------------------------------------------------------------

(defsystem beacons [foundation]
  "見本の系: 拍ごとに盤へ書く service 1 つ(版 1 — STEP 1)"
  (beacon (beacon-program foundation "beacon/a" 1.0) :needs #{"cluster-net"} :readiness {"windowSeconds" 5}
          :environ {"STEP" "1"}))


(defsystem beacons-v2 [foundation]
  "beacons の版 2(environ の STEP を変えた — 入れ替わる)"
  (beacon (beacon-program foundation "beacon/a" 1.0) :needs #{"cluster-net"} :readiness {"windowSeconds" 5}
          :environ {"STEP" "2"}))


(defsystem handoff-beacons [foundation]
  "見本の系: handoff で入れ替える beacon(版 1)"
  (beacon (beacon-program foundation "beacon/h" 1.0) :needs #{"cluster-net"} :readiness {"windowSeconds" 5}
          :update "handoff" :environ {"STEP" "1"}))


(defsystem handoff-beacons-v2 [foundation]
  "handoff-beacons の版 2(本体の引数 every を変えた — 入れ替わる)"
  (beacon (beacon-program foundation "beacon/h" 2.0) :needs #{"cluster-net"} :readiness {"windowSeconds" 5}
          :update "handoff" :environ {"STEP" "1"}))


(defsystem relay [foundation]
  "見本の系: 盤に書く beacon と、それを読んで写す copier"
  (beacon (beacon-program foundation "relay/source" 1.0) :needs #{"cluster-net"} :environ {"STEP" "9"})
  (copier (copy-program foundation "relay/source" "relay/copy") :needs #{"cluster-net"}))


(defsystem flavors [foundation]
  "見本の系: 同じ Flavor に別の答えを持つ 2 つの service と、答えを持たない 1 つ"
  (sweet (scoped-program foundation "sweet" "flavor/sweet") :needs #{"cluster-net"})
  (sour (scoped-program foundation "sour" "flavor/sour") :needs #{"cluster-net"})
  (plain (unscoped-program foundation "flavor/plain") :needs #{"cluster-net"}))


(defsystem fenced [foundation]
  "見本の系: 柵を通る物だけを使う service と、sim の世界の effect を覗く service"
  (passer (passable-program foundation "fence/passer") :needs #{"cluster-net"})
  (peeker (peeking-program foundation) :needs #{"cluster-net"}))


(defsystem delegating [foundation]
  "見本の系: RemoteJob で task を出す service 1 つ"
  (delegator (delegate-program foundation 3 "remote/result") :needs #{"cluster-net"}))


(defsystem gpu-only [foundation]
  "見本の系: どの worker も提供しない能力を要る service"
  (trainer (beacon-program foundation "gpu/beat" 1.0) :needs #{"gpu"} :environ {"STEP" "1"}))

(defsystem spawners [foundation]
  "見本の系: 子の task に盤へ書き続けさせる service 1 つ(止まらない)"
  (spawner (spawning-program foundation "spawn/" 0) :needs #{"cluster-net"}))

(defsystem quitters [foundation]
  "見本の系: 子の task に盤へ書かせ、自分は 3 拍で値を返して抜ける service 1 つ"
  (quitter (spawning-program foundation "quit/" 3) :needs #{"cluster-net"}))

(defsystem pulses [foundation]
  "見本の系: 準備できたと報告し続けるだけの service 1 つ"
  (pulse (pulse-program foundation) :needs #{"cluster-net"} :readiness {"windowSeconds" 5}))

(defsystem detaching [foundation]
  "見本の系: 切り離した task を出して待つ service 1 つ"
  (detacher (detaching-program foundation 3 "detached/result") :needs #{"cluster-net"}))
