;;; 待ちの子(#3646)の小さな判断 — どの job が待ちの子から分かれるか・分かれ元の root のキー・準備完了の印の読み・起動で読み込む module の
;;; 名・待ちの子の置き場(socket・印・exit の file)と起動の argv。型は worker/intent/worker_model。
;;;
;;; 条の守りの置き場(architecture.hy の worker の :invariants・判じる関数は worker/core/invariants):
;;;   WC1(古い root から分かれない)= task の分かれ元のキーを決めるのは warm-key-of の 1 か所(task 自身の env のキー)。
;;;   WC3(分かれる前に VM を起こさない)= 準備済みに数える印の形を判じるのは warm-mark-clean? の 1 か所。
;;;
;;; 常駐の job(service — card ki-de44efdb9aa4): WorkerPolicy.warm-services が真の時、実行環境の service も待ちの子から分けて起こし、
;;; 待ちの子が読み込んだ module の頁を共有する。分けるのは service-warm-keys の root(service が warm-services-min 本以上か、分かれる task も
;;; 在る root — 待ちの子そのものの頁が service の得より大きい root に待ちの子を起こさない)の service だけ。道は warm-start の 1 か所で決める:
;;; 待ちの子の準備を待つ(WAIT)・分ける(FORK)・入れ物 shim(SHIM — 印で service を受けると名乗らない古い版の待ちの子と、断りの上限に
;;; 届いた待ちの子の root。service は task と違い env-failed で終えない — 動き続ける job を待ちの子の都合で止めない)。
(require doeff-hy.macros [defk val var <-])
(require doeff-hy.record [defrecord defenum])
(val MODULE-TAGS {:context "worker" :role "judgment"})
(import collections [Counter])
(import json)
(import dataclasses [dataclass])  ; defrecord の展開が名指す
(import enum [StrEnum])  ; defenum の展開が名指す
(import doeff_cluster._shared.intent.job_model [JobSpec])
(import doeff_cluster._shared.intent.runtime_env_model [RuntimeEnv EnvFailure EnvFailureKind])
(import doeff_cluster._shared.core.runtime_env_rules [runtime-env-of-json])
(import doeff_cluster._worker.intent.worker_model [WorldView WarmChildMark WarmMarkUnreadable WarmChildView WarmLaunch WorkerPolicy])
(import doeff_cluster._worker.core.worker_rules [code-key])
(import doeff_cluster._worker.core.launch [outlives-worker])
(import doeff_cluster._shared.core.runtime_env [project-dir])


(defn #^ bool forks-from-warm-child [#^ JobSpec spec]  ; defk にできない: 純粋な判断の start-on-ready-tree(Program の外の関数)が呼ぶ
  "待ちの子から分かれて走る job = 実行環境を宣言した task(once)。service は今までどおり入れ物 shim で起こす(種類で道が決まる)。"
  (and spec.once (is-not spec.runtime-env None)))


(defn #^ frozenset service-warm-keys [#^ tuple desired #^ WorkerPolicy policy]  ; defk にできない: 純粋な判断の phase-of・ready-tree-step の呼び手(Program の外の関数)が呼ぶ
  "service を待ちの子から分ける root のキー(頭の註): warm-services が真の時、実行環境の service が warm-services-min 本以上ある root と、
   分かれる task も宣言に在る root。偽なら空。"
  (if (not policy.warm-services)
      (frozenset)
      (do
        (setv tasks (frozenset (gfor spec desired :if (forks-from-warm-child spec) (warm-key-of spec)))
              counts (Counter (gfor spec desired :if (and (is-not spec.runtime-env None) (not spec.once)) (warm-key-of spec))))
        (frozenset (gfor #(key n) (.items counts) :if (or (>= n policy.warm-services-min) (in key tasks)) key)))))


(defn #^ bool fork-candidate [#^ JobSpec spec #^ WorkerPolicy policy #^ frozenset warm-keys]  ; defk にできない: 純粋な判断の warm-start・ready-followups が呼ぶ
  "待ちの子から分けて起こしうる job か: 分かれる task(forks-from-warm-child)と、warm-keys(service-warm-keys)の root の実行環境の
   service(warm-services が真の時)。"
  (or (forks-from-warm-child spec)
      (and policy.warm-services (is-not spec.runtime-env None) (not spec.once) (in (warm-key-of spec) warm-keys))))


;; 宣言の job を起こす道(頭の註): SHIM = 入れ物 shim・WAIT = 待ちの子の準備を待つ・FORK = 待ちの子から分ける。
(defenum WarmStart SHIM WAIT FORK)


(defn #^ WarmStart warm-start [#^ JobSpec spec #^ WorldView world #^ WorkerPolicy policy #^ frozenset warm-keys]  ; defk にできない: 純粋な判断の phase-of(Program の外の関数)が呼ぶ
  "宣言の job を起こす道を 1 か所で決めるため(頭の註): 分けうる job でなければ SHIM。task は待ちの子が準備済みなら FORK・それまで WAIT
   (断りの上限は phase-of が env-failed で終える)。service は待ちの子が断りの上限に届いていれば SHIM・準備済みなら印が service を受けると
   名乗る時だけ FORK(名乗らない版は SHIM)・それまで WAIT。"
  (if (not (fork-candidate spec policy warm-keys))
      WarmStart.SHIM
      (do
        (setv view (warm-child-of world (warm-key-of spec)))
        (cond
          (forks-from-warm-child spec) (if (warm-child-ready view) WarmStart.FORK WarmStart.WAIT)
          (warm-child-refused view policy) WarmStart.SHIM
          ;; worker より長く生きる service(outlives-worker)は、頼みの orphan-seconds を受けると名乗る版の待ちの子にだけ頼む。
          (warm-child-ready view) (if (and view.mark.services (or view.mark.orphans (not (outlives-worker spec))))
                                      WarmStart.FORK
                                      WarmStart.SHIM)
          True WarmStart.WAIT))))


(defn #^ str warm-key-of [#^ JobSpec spec]  ; defk にできない: 純粋な判断の start-on-ready-tree(Program の外の関数)が呼ぶ
  "task の分かれ元の待ちの子の root のキー = その task 自身の env の root のキー(条 WC1 — 定義点はここ 1 つ)。"
  (code-key spec))


(defn #^ bool warm-mark-clean [#^ (| WarmChildMark WarmMarkUnreadable) mark]  ; defk にできない: 純粋な判断の warm-child-ready(Program の外の関数)が呼ぶ
  "準備完了の印が「分かれる前に thread も VM も無い」形か(条 WC3 — 判じるのはここ 1 か所): 読めた印で、threads が 1・vm-live が全部 0。"
  (and (isinstance mark WarmChildMark) (= mark.threads 1) (all (gfor n mark.vm-live (= n 0)))))


(defn #^ (| WarmChildView None) warm-child-of [#^ WorldView world #^ str key]  ; defk にできない: 純粋な判断の phase-of が呼ぶ
  "root のキーの待ちの子の観測(起こしていなければ None)。"
  (next (gfor view world.warm-children :if (= view.key key) view) None))


(defn #^ bool warm-child-ready [#^ (| WarmChildView None) view]  ; defk にできない: 純粋な判断の start-on-ready-tree が呼ぶ
  "分けてよい待ちの子か: 走っていて(終わりを観測していない・止め始めていない)、準備完了の印が分かれる前の形。"
  (and (is-not view None) (is view.exit-code None) (is view.stop None)
       (is-not view.mark None) (warm-mark-clean view.mark)))


(defn #^ bool ended-before-ready [#^ WarmChildView view]  ; defk にできない: 純粋な判断の refusals-after・warm-child-refused(Program の外の関数)が呼ぶ
  "待ちの子が起動を断ったか: worker が止めていないのに、分かれる前の形の準備完了の印を書く前に終わった(import の失敗など)。"
  (and (is-not view.exit-code None) (is view.stop None)
       (not (and (is-not view.mark None) (warm-mark-clean view.mark)))))


(defn #^ bool ended-refusing [#^ WarmChildView view]  ; defk にできない: 純粋な判断の refusals-after・warm-child-refused(Program の外の関数)が呼ぶ
  "待ちの子が断って終わったか(断りの数えの 1 か所): 起動を断った(ended-before-ready)か、task を分けるのを断ったので worker が止め、
   終わりを観測した(fork-refused — card ki-e1612a12d0c3)。"
  (or (ended-before-ready view) (and (is-not view.exit-code None) view.fork-refused)))


(defn #^ int refusals-after [#^ (| WarmChildView None) previous]  ; defk にできない: 待ちの子の宿(warm_host・sim の宿)の StartWarmChild の答え手が呼ぶ
  "root の待ちの子を起こし直す時に、新しい観測が持つ「続けて断った回数」を数えるため(数えの定義点はここ 1 つ — 本番の宿も
   sim の宿も呼ぶ): 前の子が断って終わっていれば(ended-refusing)1 つ増やし、準備済みになった子・断りの外で worker が止めた子・初めての
   子の後は 0。"
  (if (and (is-not previous None) (ended-refusing previous)) (+ previous.refusals 1) 0))


(defn #^ bool warm-child-refused [#^ (| WarmChildView None) view #^ WorkerPolicy policy]  ; defk にできない: 純粋な判断の warm-child-step・phase-of が呼ぶ
  "待ちの子が断りの上限に達したか: 今の子も断って終わり(起動を断った・分けるのを断った — ended-refusing)、続けた回数が
   WorkerPolicy.warm-refusal-limit に届いた。届いたら起こし直さず、その root から分かれる task を env-failed で終える(断りが決定的なら
   起こし直しても同じで、上限が無いと task は PREPARING のまま worker の枠を取り続けた — 2026-10-10)。"
  (and (is-not view None) (ended-refusing view) (>= (+ view.refusals 1) policy.warm-refusal-limit)))


(defn #^ EnvFailure warm-refusal-failure [#^ WarmChildView view]  ; defk にできない: 純粋な判断の statuses(Program の外の関数)が呼ぶ
  "上限に達した待ちの子の root から分かれる task の準備の失敗(coordinator がやり直さずに env-failed で終える形 — 種類は「子の約束の版の外」
   env-incompatible・訳は断りの 1 行)。"
  (EnvFailure :kind EnvFailureKind.ENV-INCOMPATIBLE :detail view.detail :retryable False))


(defk mark-refusal [mark]
  {:pre [(: mark (| WarmChildMark WarmMarkUnreadable))] :post [(: % str)] :tags {:context "worker" :role "judgment"}}
  "分かれる前の形でない印・読めない印の待ちの子を止める訳(観測の detail に残る)を綴るため。"
  (match mark
    (WarmMarkUnreadable) (.format "準備完了の印が読めない: {}" mark.detail)
    (WarmChildMark) (.format "準備完了の印が分かれる前の形でない(threads={} vmLive={})" mark.threads (list mark.vm-live))))


(defk warm-launch [key root specs warm [services (frozenset)]]
  {:pre [(: key str) (: root str) (: specs tuple) (: warm tuple) (: services frozenset)] :post [(: % WarmLaunch)] :tags {:context "worker" :role "judgment"}}
  "要る root の待ちの子の起こし方を宣言から決めるため: uv の --project は venv を持つ project の dir の定義点(runtime_env.project-dir —
   同じ root の宣言は project が同じなので最初の宣言を読む)、起動で読み込む module の名は、その root の task の宣言と温める表の行の bytecodeEntries を
   合わせた物(名の順・重なりは 1 つ — 宣言の読みは定義点 runtime-env-of-json)。名は root のキーに入らないので、同じ root の宣言どうしで
   違えば合わせた物を読む(読まなかった module は分かれた task が自分で読む — 遅くなるだけで落ちない)。services = service を分ける root の
   キー(service-warm-keys — 在れば、その root の service の宣言の bytecodeEntries も合わせる・card ki-de44efdb9aa4)。"
  (val texts (+ (tuple (gfor spec specs :if (and (= (warm-key-of spec) key)
                                                  (or (forks-from-warm-child spec)
                                                      (and (in key services) (is-not spec.runtime-env None) (not spec.once))))
                             spec.runtime-env))
                (tuple (gfor w warm :if (= w.key key) w.runtime-env))))
  (var names (frozenset))
  (for [text texts]
    (<- env RuntimeEnv (runtime-env-of-json (json.loads text)))
    (:= names (| names (frozenset env.bytecode-entries))))
  (<- declared RuntimeEnv (runtime-env-of-json (json.loads (get texts 0))))
  (<- project str (project-dir declared root))
  (WarmLaunch :root root :project project :preload (tuple (sorted names))))


;; --- 置き場と起動の命令(宿 = worker/protocol/warm_host と process_host が読む)--------------------------------------------

(defrecord WarmPlace
  "待ちの子 1 つの置き場(root の外 — root の中身と完成の印を汚さない): dir = <待ちの子の根>/<root のキー>・socket = 頼みを受ける unix socket・
   ready = 準備完了の印の file。"
  {:tags {:context "worker" :role "type"}}
  (#^ str dir)
  (#^ str socket)
  (#^ str ready))


(defrecord WarmChildFlags
  "待ちの子を起こす StartProcess の flag 3 つ: process-group = 専用の process group・hold-stdin = 標準入力の pipe を worker が握る(worker が
   消えると EOF で待ちの子も終わる)・reap-group = 終わりを回収する時に group の残りを止める。argv の頭は uv(uv run は python の子を
   自分の group に置く)なので、uv だけが外から KILL されても、worker が終わりを観測して回収する時に group に残った待ちの子が止まる
   (2026-10-05 cc2-w50 の問い・検 test_warm_child_uv_reap)。"
  {:tags {:context "worker" :role "type"}}
  (#^ bool process-group)
  (#^ bool hold-stdin)
  (#^ bool reap-group))

;; 待ちの子の起こし方の flag の定義点(宿 warm_host と検 test_warm_child_uv_reap が読む)。
(val WARM-CHILD-FLAGS (WarmChildFlags :process-group True :hold-stdin True :reap-group True))


(defk warm-dir-of [state]
  {:pre [(: state str)] :post [(: % str)] :tags {:context "worker" :role "judgment"}}
  "待ちの子の置き場の根(<state-dir>/warm)を、起こす宿・分ける宿・掃除の係が同じ綴りで作るため。"
  (+ state "/warm"))


(defk warm-place [warm-dir key]
  {:pre [(: warm-dir str) (: key str)] :post [(: % WarmPlace)] :tags {:context "worker" :role "judgment"}}
  "root のキーの待ちの子の置き場を導くため — 起こす宿(--socket・--ready)と task を分ける宿(ForkFromWarm の socket)が同じ関数で導くので、
   task は自分の root のキーの socket へだけ頼む(条 WC1 の宿の側の半分)。socket の path は unix socket の上限 107 byte の内に収まる
   (<state-dir>/warm/env-<24 桁>/sock)。"
  (val dir (+ warm-dir "/" key))
  (WarmPlace :dir dir :socket (+ dir "/sock") :ready (+ dir "/ready.json")))


(defk warm-child-argv [uv launch place]
  {:pre [(: uv str) (: launch WarmLaunch) (: place WarmPlace)] :post [(: % (get tuple #(str ...)))] :tags {:context "worker" :role "judgment"}}
  "待ちの子を root の venv で起こす命令を、task の子と同じ uv run の形(--no-sync --frozen --project)で組むため(入口 =
   worker/entry/warm_child — 起動で読む module は --preload を名の順に並べる)。入口の名は区画を _worker へ隠す前の path のまま
   (card ki-3ff93cd3499e — 待ちの子は root の版の doeff で起き、_worker の無い古い版の root でも解ける名が要る。新しい版の root では
   移し替えの module worker.py が _worker の module を返す。段 3 で移し替えの module を消す前に root の版の下限と合わせて替える)。"
  (+ #(uv "run" "--no-sync" "--frozen" "--project" launch.project "python" "-m" "doeff_cluster.worker.entry.warm_child"
       "--root" launch.root "--socket" place.socket "--ready" place.ready)
     (tuple (gfor name launch.preload part #("--preload" name) part))))
