;;; 待ちの子(#3646)の小さな判断 — どの job が待ちの子から分かれるか・分かれ元の root のキー・準備完了の印の読み・起動で読み込む module の
;;; 名・待ちの子の置き場(socket・印・exit の file)と起動の argv。型は worker/intent/worker_model。
;;;
;;; 条の守りの置き場(architecture.hy の worker の :invariants・判じる関数は worker/core/invariants):
;;;   WC1(古い root から分かれない)= task の分かれ元のキーを決めるのは warm-key-of の 1 か所(task 自身の env のキー)。
;;;   WC3(分かれる前に VM を起こさない)= 準備済みに数える印の形を判じるのは warm-mark-clean? の 1 か所。
;;;
;;; 常駐の job(service): WorkerPolicy.warm-services が真の時、実行環境の service も待ちの子から分けて起こし、
;;; 待ちの子が読み込んだ module の頁を共有する。分けるのは service-warm-keys の root(service が warm-services-min 本以上か、分かれる task も
;;; 在る root — 待ちの子そのものの頁が service の得より大きい root に待ちの子を起こさない)の service だけ。道は warm-start の 1 か所で決める:
;;; 待ちの子の準備を待つ(WAIT)・分ける(FORK)・入れ物 shim(SHIM — 印で service を受けると名乗らない古い版の待ちの子と、断りの上限に
;;; 届いた待ちの子の root。service は task と違い env-failed で終えない — 動き続ける job を待ちの子の都合で止めない)。
(require doeff-hy.macros [defk val var <-])
(require doeff-hy.record [defrecord defenum])
(val MODULE-TAGS {:context "worker" :role "judgment"})
(import collections [Counter])
(import hashlib)
(import json)
(import dataclasses [dataclass])  ; defrecord の展開が名指す
(import enum [StrEnum])  ; defenum の展開が名指す
(import doeff_cluster._shared.intent.job_model [JobSpec CHILD-ENTRY-PLACES])
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
  "宣言の job を起こす道を 1 か所で決めるため(頭の註): 分けうる job でなければ SHIM。task は待ちの子が準備済みなら FORK・待ちの子が
   jobs の層に入らない(warm-child-roomless)か、層の OOM kill で落ちて断りの上限に届いた(warm-child-killed-out)root では SHIM(冷えた道 —
   入らない待ちの子を待たない)・それまで WAIT(ほかの断りの上限は phase-of が env-failed で終える)。service は待ちの子が断りの上限に届いて
   いるか jobs の層に入らなければ SHIM・準備済みなら印が service を受けると名乗る時だけ FORK(名乗らない版は SHIM)・それまで WAIT。"
  (if (not (fork-candidate spec policy warm-keys))
      WarmStart.SHIM
      (do
        (setv key (warm-key-of spec)
              view (warm-child-of world key))
        (cond
          (forks-from-warm-child spec) (cond (warm-child-ready view) WarmStart.FORK
                                             (or (warm-child-roomless key view world policy) (warm-child-killed-out view policy))
                                               WarmStart.SHIM
                                             True WarmStart.WAIT)
          (warm-child-refused view policy) WarmStart.SHIM
          (warm-child-roomless key view world policy) WarmStart.SHIM
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


(defn #^ bool warm-child-places [#^ WorldView world #^ str key]  ; defk にできない: 純粋な判断の ready-tree-step が StartJob の欄に組む
  "root のキーの待ちの子が、分ける頼みの cgroup(cgroupProcs)を受ける版か: 準備完了の印が読めて serviceForks を名乗る(同じ版から頼みの
   cgroup を受ける — 印の無い版は知らない欄を断る)。宿は真の時だけ、分けた task を job の名の子へ移す(worker/core/job_cgroup)。"
  (setv view (warm-child-of world key))
  (and (is-not view None) (isinstance view.mark WarmChildMark) view.mark.services))


(defn #^ bool warm-child-ready [#^ (| WarmChildView None) view]  ; defk にできない: 純粋な判断の start-on-ready-tree が呼ぶ
  "分けてよい待ちの子か: 走っていて(終わりを観測していない・止め始めていない)、準備完了の印が分かれる前の形。"
  (and (is-not view None) (is view.exit-code None) (is view.stop None)
       (is-not view.mark None) (warm-mark-clean view.mark)))


(defn #^ bool ended-before-ready [#^ WarmChildView view]  ; defk にできない: 純粋な判断の refusals-after・warm-child-refused(Program の外の関数)が呼ぶ
  "待ちの子が起動を断ったか: worker が止めていないのに、分かれる前の形の準備完了の印を書く前に終わった(import の失敗など)。"
  (and (is-not view.exit-code None) (is view.stop None)
       (not (and (is-not view.mark None) (warm-mark-clean view.mark)))))


;; SIGKILL で終わった子の終わりの code: 子 process の答えは負の signal の番号(-9)。uv が python の子の signal の終わりを 128 + 番号の
;; 終了 code で返す版も同じに読む(待ちの子の pid は uv)。
(val KILLED-CODES (frozenset #(-9 137)))


(defn #^ bool ended-killed [#^ WarmChildView view]  ; defk にできない: 純粋な判断の ended-refusing・warm-child-killed-out(Program の外の関数)が呼ぶ
  "待ちの子が、worker が止めていないのに SIGKILL で終わったか(jobs の層の memory の上限に当たった kernel の OOM kill — 準備完了の後の子
   も。2026-11-06 の k3s-0〜2 で、入らない待ちの子が準備完了の後に落ち、30 秒ごとに起こし直された)。"
  (and (in view.exit-code KILLED-CODES) (is view.stop None) (not view.fork-refused)))


(defn #^ bool ended-refusing [#^ WarmChildView view]  ; defk にできない: 純粋な判断の refusals-after・warm-child-refused(Program の外の関数)が呼ぶ
  "待ちの子が断って終わったか(断りの数えの 1 か所): 起動を断った(ended-before-ready)か、task を分けるのを断ったので worker が止め、
   終わりを観測した(fork-refused)か、worker が止めていないのに SIGKILL で終わった(ended-killed — 起こし直しと上限は起動の断りと同じ道)。"
  (or (ended-before-ready view) (and (is-not view.exit-code None) view.fork-refused) (ended-killed view)))


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


(defn #^ bool warm-child-killed-out [#^ (| WarmChildView None) view #^ WorkerPolicy policy]  ; defk にできない: 純粋な判断の warm-start・phase-of が呼ぶ
  "待ちの子が断りの上限に、SIGKILL の終わり(ended-killed — jobs の層の OOM kill)で届いたか。起こし直さない事は他の断りと同じだが、
   root の中身の断りではないので、その root から分かれる task を env-failed で終えず、入れ物 shim の冷えた道で起こす(warm-start)。"
  (and (warm-child-refused view policy) (ended-killed view)))


(defn #^ int warm-child-size [#^ (| WarmChildView None) view #^ WorkerPolicy policy]  ; defk にできない: 純粋な判断の warm-room-of・warm-child-roomless が呼ぶ
  "root の待ちの子 1 本の大きさ(byte): その root で準備完了の時に測った RSS(WarmChildView.size-bytes)、まだ測っていなければ
   WorkerPolicy.warm-child-size-bytes の見積もり。"
  (if (and (is-not view None) (is-not view.size-bytes None)) view.size-bytes policy.warm-child-size-bytes))


(defn #^ (| int None) warm-room-of [#^ str key #^ WorldView world #^ WorkerPolicy policy]  ; defk にできない: 純粋な判断の warm-child-roomless が呼ぶ
  "root のキー key の待ちの子に使える jobs の層の空き(byte — None = 空きで縛らない・WorldView.jobs-room): 見張りの読んだ空きから、
   ほかの root の起こし中の待ちの子(走っていて、止めておらず、まだ準備済みでない — 見張りの読みにまだ全部は載っていない)の大きさを
   引く。準備済みの子は層の使いに載っているので引かない。"
  (if (is world.jobs-room None)
      None
      (- world.jobs-room
         (sum (gfor view world.warm-children
                    :if (and (!= view.key key) (is view.exit-code None) (is view.stop None) (not (warm-child-ready view)))
                    (warm-child-size view policy))))))


(defn #^ bool warm-child-roomless [#^ str key #^ (| WarmChildView None) view #^ WorldView world #^ WorkerPolicy policy #^ int [taken 0]]  ; defk にできない: 純粋な判断の warm-start・phase-of が呼ぶ
  "root のキー key の待ちの子を起こすなら jobs の層に入らないか(起こす前の判じの 1 か所 — 判断 warm-child-step と道の warm-start が読む):
   今の子が走っておらず(起こしていない・終わった)、その root の待ちの子 1 本の大きさ(warm-child-size)が使える空き(warm-room-of から、
   同じ拍に先に起こした待ちの子の大きさ taken を引いた値)を越える。入らない待ちの子を起こすと、層の上限に当たって kernel の OOM kill が
   層でいちばん大きい process = その待ちの子を落とし、起こし直しの輪になる(2026-11-06 の k3s-0〜2)。空きで縛らない層(None)では偽。"
  (setv room (warm-room-of key world policy))
  (and (is-not room None)
       (or (is view None) (is-not view.exit-code None))
       (> (warm-child-size view policy) (- room taken))))


(defk vm-rss-bytes [text]
  {:pre [(: text str)] :post [(: % (| int None))] :tags {:context "worker" :role "judgment"}}
  "process の status の中身(/proc/<pid>/status)から、待ちの子 1 本の大きさにする RSS(VmRSS の行・kB)を byte で読むため(宿 warm_host が
   準備完了の時に読む)。行が無い・数でなければ None。"
  (var found None)
  (for [line (.splitlines text)]
    (val parts (.split line))
    (when (and (= (len parts) 3) (= (get parts 0) "VmRSS:") (.isdigit (get parts 1)) (= (get parts 2) "kB"))
      (:= found (* (int (get parts 1)) 1024))))
  found)


(defk room-shortage [key view world policy]
  {:pre [(: key str) (: view (| WarmChildView None)) (: world WorldView) (: policy WorkerPolicy)] :post [(: % str)]
   :tags {:context "worker" :role "judgment"}}
  "jobs の層に入らない待ちの子の訳(状態の行の理由 — 起こさない待ちの子の root の task)を綴るため。"
  (.format "no-jobs-room: jobs の層の空き {} MiB に待ちの子 {} MiB が入らない — 待ちの子を起こさず冷えた道で起こす"
           (// (or (warm-room-of key world policy) 0) (* 1024 1024)) (// (warm-child-size view policy) (* 1024 1024))))


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
   キー(service-warm-keys — 在れば、その root の service の宣言の bytecodeEntries も合わせる)。"
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
  "待ちの子 1 つの置き場(root の外 — root の中身と完成の印を汚さない): dir = <待ちの子の根>/<root のキー>・socket-dir = socket だけを置く
   持ち主だけの dir(短い根の下 — warm-socket-of)・socket = 頼みを受ける unix socket・ready = 準備完了の印の file。"
  {:tags {:context "worker" :role "type"}}
  (#^ str dir)
  (#^ str socket-dir)
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


(defclass WarmSocketTooLong [ValueError]  ; class にする理由: 上限を越える socket の path を名指して断る例外の型(検が pytest.raises で名指す — 欄も状態も足さない)
  "待ちの子の socket の path が unix socket の上限(SOCKET-PATH-LIMIT)を越える。args = path と長さを書いた文。")


;; unix socket の path の上限(byte・終わりの NUL を除く): sun_path は macOS が 104 byte・Linux が 108 byte(どちらも NUL 込み)。
;; 狭い macOS の側に合わせる。
(val SOCKET-PATH-LIMIT 103)
;; 待ちの子の socket を置く短い根。状態の dir(<state-dir>/warm/<root のキー>)は機体と世代で長さが変わり、Mac の世代の dir では
;; socket の path が 118 byte になって bind が断った(2026-11-07 の Mac の worker・AF_UNIX path too long)。そこで socket だけを長さの
;; 決まった根の下に置く。/tmp は macOS(/private/tmp)と Linux の container のどちらにも在り、sticky なので他人が dir を消せない
;; (他人が先に作った dir は、待ちの子が持ち主と mode を確かめて起動を断る — warm_child)。
(val WARM-SOCKET-ROOT "/tmp")
;; socket の dir の名に入れる、置き場の dir の sha256 の頭の桁数(96 bit — 同じ機体の世代と root の置き場が重ならない)。
(val WARM-SOCKET-DIGITS 24)


(defk warm-socket-dir-of [root dir]
  {:pre [(: root str) (: dir str)] :post [(: % str)] :tags {:context "worker" :role "judgment"}}
  "待ちの子の置き場の dir から、socket だけを置く持ち主だけの dir(<root>/doeff-warm-<dir の sha256 の頭 24 桁>)を導くため。名の長さは
   dir の長さに依らない。"
  (.format "{}/doeff-warm-{}" root (cut (.hexdigest (hashlib.sha256 (.encode dir "utf-8"))) 0 WARM-SOCKET-DIGITS)))


(defk warm-socket-of [root dir]
  {:pre [(: root str) (: dir str)] :post [(: % str)] :tags {:context "worker" :role "judgment"}}
  "待ちの子の置き場の dir から、頼みを受ける socket の path(<warm-socket-dir-of>/sock)を導くため。上限を越える組み立て(長すぎる根)は
   WarmSocketTooLong で名指して断る — 黙って待ちの子の bind の断りまで運ばない。"
  (<- socket-dir str (warm-socket-dir-of root dir))
  (val path (+ socket-dir "/sock"))
  (val size (len (.encode path "utf-8")))
  (when (> size SOCKET-PATH-LIMIT)
    (raise (WarmSocketTooLong (.format "待ちの子の socket の path が {} byte で、unix socket の上限 {} byte を越える: {}" size SOCKET-PATH-LIMIT path))))
  path)


(defk warm-place [warm-dir key]
  {:pre [(: warm-dir str) (: key str)] :post [(: % WarmPlace)] :tags {:context "worker" :role "judgment"}}
  "root のキーの待ちの子の置き場を導くため — 起こす宿(--socket・--ready)と task を分ける宿(ForkFromWarm の socket)と忘れる時の掃除が
   同じ関数で導くので、task は自分の root のキーの socket へだけ頼む(条 WC1 の宿の側の半分)。socket は状態の dir の下でなく短い根の下
   (warm-socket-of — 状態の dir の長さに依らず unix socket の上限の内)。"
  (val dir (+ warm-dir "/" key))
  (<- socket-dir str (warm-socket-dir-of WARM-SOCKET-ROOT dir))
  (<- socket str (warm-socket-of WARM-SOCKET-ROOT dir))
  (WarmPlace :dir dir :socket-dir socket-dir :socket socket :ready (+ dir "/ready.json")))


(defk warm-child-argv [uv launch place entry-place]
  {:pre [(: uv str) (: launch WarmLaunch) (: place WarmPlace) (: entry-place (| str None))] :post [(: % (get tuple #(str ...)))]
   :tags {:context "worker" :role "judgment"}}
  "待ちの子を root の venv で起こす命令を、task の子と同じ uv run の形(--no-sync --frozen --project)で組むため(入口 =
   _worker/entry/warm_child — 起動で読む module は --preload を名の順に並べる)。待ちの子は root の版の doeff で起きるので、入口の名は
   root の完成の印の置き場 entry-place の下の warm_child(_worker の無い古い版の root では区画を隠す前の置き場。
   None = 印の読めない root で、job_model の CHILD-ENTRY-PLACES の先頭の置き場)。"
  (+ #(uv "run" "--no-sync" "--frozen" "--project" launch.project "python" "-m"
       (+ (if (is entry-place None) (get CHILD-ENTRY-PLACES 0) entry-place) ".warm_child")
       "--root" launch.root "--socket" place.socket "--ready" place.ready)
     (tuple (gfor name launch.preload part #("--preload" name) part))))
