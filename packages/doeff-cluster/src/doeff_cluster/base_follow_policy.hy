;;; Service の土台の commit(spec.base)を、配備の流れが決めた Deployment の版へ追わせる純粋な判断(2026-09-24)。I/O はしない。
;;;
;;; 版の正本は配備の流れ(image を焼いて manifest の image を上げ、kubectl apply する)が決めた **Deployment の pod template の image**
;;; ただ 1 つ。Deployment の台数が 0 でも apply は template を書き換えるので、template の image が「いま本番の版と決まった物」を表す。
;;; coordinator はそれを観測し(ReadDeployment の images)、image の LABEL(ClusterNaming の revision-label・焼く時に刻む 40 桁の commit)
;;; を読み(ReadImageLabels)、Service の spec.base をその commit へ進める(送り手 base-follow・出来事の記録に前後の値)。
;;; level-triggered: 毎回「観測した版 ≠ 宣言の base」だけから決め、何度当てても同じ。
;;; spec.base はこの係だけが書く写しで、版を決める第 2 の正本ではない(宣言の書き換えが base を書かなければ今の値を保つ —
;;; resource_policy.update-resource)。base が変わると worker は Service の入れ替え(handoff)で新しい process を並べて起こし、Ready の後に旧を止める。
(import dataclasses [replace])
(import re)
(import .cluster_model [ClusterState ClusterNaming])

(setv BASE-FOLLOW-ACTOR "base-follow")
(setv OBSERVE-EVERY-MS 10000)        ; 追う相手の Deployment を読む間(Rollout の台数の持ち主の見張りと同じ)
(setv OBSERVATION-STALE-MS 60000)    ; これより古い観測では追わない(k8s に届かない間は動かさない)
(setv IMAGE-RETRY-MS 60000)          ; LABEL を読めなかった image を読み直すまで
(setv FULL-SHA (re.compile "^[0-9a-f]{40}$"))


(defn #^ str deployment-key [#^ dict target]
  (+ (get target "namespace") "/" (get target "name")))


(defn #^ list followed-deployments [#^ ClusterState state]
  "base を追う Service が名指す Deployment の「ns/名」。"
  (list (dict.fromkeys (gfor j state.jobs :if j.base-from (deployment-key j.base-from)))))


(defn #^ list due-deployments [#^ ClusterState state #^ int now]
  "読むべき追う相手(最後の観測から OBSERVE-EVERY-MS 過ぎた物)。"
  (lfor key (followed-deployments state)
        :if (>= (- now (.get (.get state.deployments key {}) "at" 0)) OBSERVE-EVERY-MS)
        key))


(defn #^ dict source-image [#^ ClusterState state #^ dict target #^ int now]
  "追う相手の Deployment の image。答え = {\"image\" …} か {\"reason\" …}(観測が無い・古い・届かない・container が決まらない)。"
  (setv obs (.get state.deployments (deployment-key target)))
  (cond
    (is obs None) {"reason" "まだ観測していない"}
    (in "error" obs) {"reason" (+ "k8s の読みが失敗: " (str (get obs "error")))}
    (> (- now (.get obs "at" 0)) OBSERVATION-STALE-MS) {"reason" "観測が古い"}
    True
      (do (setv images (or (.get obs "images") {}) container (.get target "container"))
          (cond
            container (if (in container images) {"image" (get images container)}
                          {"reason" (.format "container {} が無い(在るのは {})" container (sorted images))})
            (= (len images) 1) {"image" (next (iter (.values images)))}
            True {"reason" (.format "container が 1 つでない({})— baseFrom に container を書く" (sorted images))}))))


(defn #^ list images-to-resolve [#^ ClusterState state #^ int now]
  "LABEL を読むべき image(まだ読んでいない・読めなかった物を IMAGE-RETRY-MS の後に)。"
  (setv out [])
  (for [job state.jobs]
    (when job.base-from
      (setv image (.get (source-image state job.base-from now) "image"))
      (when image
        (setv known (.get state.images image))
        (when (and (not-in image out)
                   (or (is known None)
                       (and (in "error" known) (>= (- now (.get known "at" 0)) IMAGE-RETRY-MS))))
          (.append out image)))))
  out)


(defn #^ dict image-entry [#^ dict labels #^ int now #^ ClusterNaming [naming (ClusterNaming)]]
  "LABEL の dict → cache の 1 行 {\"revision\" <version-labels の鍵>… \"at\"}。版が 40 桁の commit でなければ error。"
  (setv revision (.get labels naming.revision-label))
  (if (and (isinstance revision str) (FULL-SHA.match revision))
      (| {"revision" revision} (dfor #(key label) naming.version-labels key (.get labels label)) {"at" now})
      {"error" (.format "LABEL {} が 40 桁の commit でない: {!r}" naming.revision-label revision) "at" now}))


(defn #^ dict base-observation [#^ ClusterState state job #^ int now]
  "Service 1 つの追随の観測(資源の status の base)。{\"image\" \"revision\" \"reason\"}。時刻は入れない(変わった時だけ版が進む)。"
  (setv src (source-image state job.base-from now))
  (if (not-in "image" src)
      {"image" None "revision" None "reason" (get src "reason")}
      (do (setv image (get src "image") known (.get state.images image))
          (cond
            (is known None) {"image" image "revision" None "reason" "LABEL をまだ読んでいない"}
            (in "error" known) {"image" image "revision" None "reason" (get known "error")}
            ;; 版と一緒に写した LABEL(ClusterNaming の version-labels の鍵)も並べる。
            True (| (dfor #(k v) (.items known) :if (not-in k #("revision" "at")) k v)
                    {"image" image "revision" (get known "revision") "reason" None})))))


(defn #^ ClusterState follow-bases [#^ ClusterState state #^ int now]
  "base を追う Service の spec.base を、観測した本番の版へ進める。観測が揃わない Service は触らない(今の base のまま動かし続ける)。
   版の組(2026-09-25): overlay の無い Service は spec.revision も同じ commit へ同じ置き換えで進める(業務コード・定義・実行環境が
   1 つの commit — 片方だけ進んだ状態を作らない)。overlay を明示した Service は base だけを進める(定義の版は overlay のまま)。"
  (setv changed False jobs [])
  (for [job state.jobs]
    (setv next-job job)
    (when job.base-from
      (setv revision (get (base-observation state job now) "revision"))
      (when (and revision (or (!= revision job.spec.base) (and (not job.overlay) (!= revision job.spec.revision))))
        (setv next-job (replace job :spec (if job.overlay
                                          (replace job.spec :base revision)
                                          (replace job.spec :base revision :revision revision)))
              changed True)))
    (.append jobs next-job))
  (if changed (replace state :jobs (tuple jobs)) state))
