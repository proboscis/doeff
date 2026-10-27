;;; worker の Pod の job の label の判断(card ki-a66bb71f4152)— I/O は呼び手(worker/protocol/pod_labels)が行う。
;;;
;;; 常駐の job の置き先は coordinator の GET /state にしか出なかったので、k8s の Service は job の置き先を選べず、機体の label
;;; (doeff.dev/node)で 1 台を名指していた — job が別の機体へ移ると口が届かない。worker は coordinator の宣言がこの worker に置いた
;;; 常駐の job を、自分の Pod の label JOB-LABEL-PREFIX<job の名> = JOB-LABEL-VALUE に写し、宣言から外れた job の label を外す。
;;; Service は selector {app: doeff-worker, job.doeff.dev/<job の名>: placed} で、job の置かれた worker の Pod を選ぶ。
;;;   * label の鍵は job の名ごと(1 つの Pod が幾つの job を担っても、Service はそれぞれの job を名で選べる)。
;;;   * 前置き JOB-LABEL-PREFIX の label は worker が持ち主 — 宣言に無い job の label は外す(前の世代や手で付けた残りも外す)。
;;;   * job の名が label の名の字(英数字・-・_・.、頭と尻は英数字、63 字まで)に成らない job は label にせず、名を挙げて返す
;;;     (呼び手が log に 1 度出す)。
;;;   * task(once)は載せない — 口を持つのは常駐の job だけ。
;;; worker が死んだ Pod は readinessProbe(この Pod の worker が heartbeat を届けているか — deploy/boot.sh の役 ready)が落ちて
;;; NotReady になり、label が残っていても Service の振り先から外れる。
(require doeff-hy.macros [defk <- val var])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "worker" :role "judgment"})
(import dataclasses [dataclass])  ; defrecord の展開が使う
(import re)
(import doeff_cluster.shared.intent.job_model [JobSpec])

;; worker が持ち主の label の鍵の前置き(後ろに job の名)と、置かれた job の label の値。
(val JOB-LABEL-PREFIX "job.doeff.dev/")
(val JOB-LABEL-VALUE "placed")
;; k8s の label の名(鍵の / の後ろ)の字: 英数字・-・_・.、頭と尻は英数字、63 字まで。
(val LABEL-NAME (re.compile r"^[A-Za-z0-9]([-A-Za-z0-9_.]{0,61}[A-Za-z0-9])?$"))
;; 宣言が変わらなくても Pod の label を読み直して揃える間(手で外された・付けられた label を戻す)。
(val RESYNC-EVERY-MS (* 60 1000))
;; 揃えに失敗した後、次に試すまでの間(初め・上限 — 失敗が続くたびに倍にする)。API server に届かない間、拍ごとに要求の上限まで待たない。
(val RETRY-FIRST-MS (* 10 1000))
(val RETRY-LIMIT-MS (* 5 60 1000))


(defrecord JobLabels
  "宣言から求める Pod の job の label: wanted = 鍵 → 値(付けておく label)・unlabelled = label の名に成らない job の名(名の順)。
   wanted を dict にする理由: 鍵が job ごとに変わる k8s の label の写しで、そのまま merge patch の本文の labels の欄になる(欄の決まった値でない)。"
  (#^ dict wanted)
  (#^ tuple unlabelled))


(defk job-label-key [name]
  {:pre [(: name str)] :post [(: % (| str None))]}
  "job の名 name の label の鍵を決めるため(label の名の字に成らない名は None)。"
  (if (.match LABEL-NAME name) (+ JOB-LABEL-PREFIX name) None))


(defk job-labels-wanted [jobs]
  {:pre [(: jobs (get tuple #(JobSpec ...)))] :post [(: % JobLabels)]}
  "この worker に置かれた宣言の job の列(JobSpec — task も混ざる)から、Pod に付けておく job の label を決めるため。task(once)は
   載せない。label の名に成らない job は wanted に入れず unlabelled に挙げる。"
  (var wanted {})
  (var unlabelled #())
  (for [spec jobs]
    (when (not spec.once)
      (<- key (| str None) (job-label-key spec.name))
      (if (is key None)
          (:= unlabelled (+ unlabelled #(spec.name)))
          (:= wanted (| wanted {key JOB-LABEL-VALUE})))))
  (JobLabels :wanted wanted :unlabelled (tuple (sorted (set unlabelled)))))


(defk job-labels-patch [current wanted]
  {:pre [(: current dict) (: wanted dict)] :post [(: % dict)]}
  "Pod の今の label(current — 全部の label)を wanted に揃える merge patch の labels の欄を決めるため: wanted に在って無い・値の違う鍵は
   その値、前置き JOB-LABEL-PREFIX の鍵で wanted に無い物は None(merge patch で消す)。前置きの外の label には触らない。空 = 揃っている。"
  (val put (dfor #(key value) (.items wanted) :if (!= (.get current key) value) key value))
  (val dropped (dfor key current :if (and (.startswith key JOB-LABEL-PREFIX) (not-in key wanted)) key None))
  (dict (sorted (.items (| put dropped)))))


(defk labels-sync-due [wanted synced-wanted synced-ms retry-at-ms now-ms]
  {:pre [(: wanted dict) (: synced-wanted (| dict None)) (: synced-ms (| int None)) (: retry-at-ms (| int None)) (: now-ms int)]
   :post [(: % bool)]}
  "この拍で Pod の label を揃えるかを決めるため: synced-wanted = 前に揃えた時の wanted(None = まだ揃えていない)・synced-ms = その刻・
   retry-at-ms = 失敗の後に次に試す刻(None = 失敗していない)。失敗の後は次に試す刻まで待つ(wanted が変わっても)。そうでなければ、
   まだ揃えていない・wanted が変わった・RESYNC-EVERY-MS を過ぎた・壁の時計が戻った時に揃える。"
  (cond
    (is-not retry-at-ms None) (>= now-ms retry-at-ms)
    (or (is synced-wanted None) (is synced-ms None)) True
    (!= wanted synced-wanted) True
    True (or (< now-ms synced-ms) (>= (- now-ms synced-ms) RESYNC-EVERY-MS))))


(defk retry-after-ms [failures]
  {:pre [(: failures int)] :post [(: % int)]}
  "揃えに続けて failures 回(1 以上)失敗した後、次に試すまでの間(RETRY-FIRST-MS から倍にして RETRY-LIMIT-MS まで)。"
  (min RETRY-LIMIT-MS (* RETRY-FIRST-MS (** 2 (min 16 (max 0 (- failures 1)))))))


(defk labels-line [patch]
  {:pre [(: patch dict)] :post [(: % str)]}
  "Pod の job の label を揃えた移り(付けた・外した鍵)を worker の log の 1 行にするため。"
  (val added (lfor #(key value) (.items patch) :if (is-not value None) (cut key (len JOB-LABEL-PREFIX) None)))
  (val removed (lfor #(key value) (.items patch) :if (is value None) (cut key (len JOB-LABEL-PREFIX) None)))
  (.format "worker: Pod の job の label を揃えた — 付けた {}・外した {}"
           (or (.join "・" added) "無し") (or (.join "・" removed) "無し")))
