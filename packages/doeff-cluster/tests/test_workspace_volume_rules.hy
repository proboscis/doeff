;;; 作業場所の volume の純粋な判断(doeff_cluster._shared.core.workspace_volume_rules)の検 — 持ち主の id の条件と名・mountinfo の読み・
;;; attach と detach の次の一手。
;;;
;;;   名                持ち主の id は英字・数字・-(頭と尻は英字か数字)・37 字まで。名は ws- + 小文字(40 字まで)。外れる id は断る(失敗ケース —
;;;                     縮めた名・重なった名・k8s の名に成らない名を作らない)
;;;   mountinfo         path の 8 進の escape を戻す・後の行ほど上・Longhorn の device の source だけを volume の mount と読む
;;;   attach の一手     VolumeAttachment がまだ無い → 待つ・ticket が無い → 置く・別の node → 断る(失敗ケース — 別の機体の ticket を
;;;                     書き換えない)・満たされて付いた → 付いた
;;;   detach の一手     別の node → 断る(失敗ケース)・この node → 外す・外れる途中 → 待つ・ほかの ticket が付けている → この答え手の分は外れた
;;;   冷やす            使っている印の ticket(Longhorn が冷やす手順と戻しのために置く ticket を除く)・snapshot と backup の名(63 字まで)・
;;;                     戻す元(完了した物の一番新しい物 — 途中・失敗・url の無い物は選ばない)・札の disk の残りの byte(Longhorn の置く線
;;;                     ごと — 負も足す)・冷やす順(2 時間を過ぎた物は全部・両方の線が需要の byte を覆うまで古い順に・冷やした volume が線ごとに
;;;                     空ける byte は名目 = spec.size・実 = actualSize・覆えなければ 2 時間を過ぎた物だけ)
(require doeff-hy.macros [deftest <- val])
(import doeff_cluster._shared.core.workspace_volume_rules [AttachStep AttachView BackupView backup-volume-name DetachStep DiskRoom MountEntry NAME-MAX-LENGTH
                                                         NAME-PREFIX CoolingPlan RoomBytes WarmVolume attach-step cooling-name cooling-order
                                                         detach-step growing-name mount-entries mounted-at newest-backup owner-refusal restore-node room-bytes
                                                         room-covers? ticket-id using-tickets volume-mount-points volume-name volume-of-entry])

(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})

;; 持ち主の id の例(頭 c- と英大文字・数字 26 字 — 本番の id と同じ長さ)。
(val OWNER "c-EXAMPLEOWNERID000000000001")
;; zeus の worker の container の mountinfo の形(/work は host の USB の SSD・係が付けた volume 2 つ・空白を含む path)。
(val MOUNTINFO (.join "\n" #("13419 13415 0:902 / / rw,relatime master:1 - overlay overlay rw,lowerdir=/l"
                             "13423 13415 8:17 /agent-worker /work rw,noatime master:790 - ext4 /dev/sdb1 rw"
                             "13500 13423 8:96 / /work/agent-workspaces/a-1 rw,relatime master:801 - ext4 /host/dev/longhorn/ws-c-1 rw"
                             "13501 13423 8:112 / /work/agent-workspaces/with\\040space rw,relatime master:802 - ext4 /host/dev/longhorn/ws-c-2 rw"
                             "13502 13423 0:55 / /work/agent-workspaces/a-1 rw,relatime - tmpfs tmpfs rw"
                             "not a mountinfo line")))


(deftest test-an-owner-id-names-its-volume-in-lower-case
  (<- refusal (owner-refusal OWNER))
  (assert (is refusal None) refusal)
  (<- name (volume-name OWNER))
  (assert (= name "ws-c-exampleownerid000000000001") name)
  (assert (<= (len name) NAME-MAX-LENGTH))
  (<- ticket (ticket-id name))
  (assert (= ticket "longhorn-api-ws-c-exampleownerid000000000001") ticket))


(deftest test-ids-outside-the-name-rule-are-refused-before-any-volume
  ;; 37 字は通り、38 字は断る(名が 40 字を越えると Longhorn が縮めて別の名にする)。
  (val longest (* "a" (- NAME-MAX-LENGTH (len NAME-PREFIX))))
  (<- fits (owner-refusal longest))
  (assert (is fits None) fits)
  (<- too-long (owner-refusal (+ longest "b")))
  (assert (and (is-not too-long None) (in "38 字" too-long)) too-long)
  (for [bad #("" "c_1" "c-1/../x" "c 1" "-c1" "c1-" "Ⅽ-1" "c.1")]
    (<- refusal (owner-refusal bad))
    (assert (is-not refusal None) bad)))


(deftest test-mountinfo-lines-are-read-with-their-escapes
  (<- entries (mount-entries MOUNTINFO))
  (assert (= (len entries) 5) entries)
  (assert (= (get entries 3) (MountEntry :mount-point "/work/agent-workspaces/with space" :source "/host/dev/longhorn/ws-c-2"
                                         :fs-type "ext4"))
          (get entries 3))
  ;; 同じ path に重ねて付いた物は、後の行(上)を読む。
  (<- top (mounted-at entries "/work/agent-workspaces/a-1"))
  (assert (= top.source "tmpfs") top)
  (<- nothing (mounted-at entries "/work/agent-workspaces/a-3"))
  (assert (is nothing None) nothing))


(deftest test-only-a-longhorn-device-counts-as-a-volume-mount
  (<- entries (mount-entries MOUNTINFO))
  (<- points (volume-mount-points entries "ws-c-1"))
  (assert (= points #("/work/agent-workspaces/a-1")) points)
  (<- spaced (volume-mount-points entries "ws-c-2"))
  (assert (= spaced #("/work/agent-workspaces/with space")) spaced)
  (<- none (volume-mount-points entries "ws-c-3"))
  (assert (= none #()) none)
  (<- tmpfs (volume-of-entry (MountEntry :mount-point "/work/x" :source "tmpfs" :fs-type "tmpfs")))
  (assert (is tmpfs None) tmpfs)
  ;; dir の名が longhorn でない device(host の disk)は volume と読まない。
  (<- disk (volume-of-entry (MountEntry :mount-point "/work" :source "/dev/ws-c-1" :fs-type "ext4")))
  (assert (is disk None) disk))


(deftest test-attach-puts-waits-and-refuses-another-nodes-ticket
  (val base (AttachView :attachment True :ticket-node None :satisfied False :message "" :state "detached" :current-node ""
                        :other-tickets #()))
  (val cases #(#((AttachView :attachment False :ticket-node None :satisfied False :message "" :state "creating" :current-node ""
                             :other-tickets #())
                 AttachStep.WAIT)
               #(base AttachStep.PUT)
               #((AttachView :attachment True :ticket-node "zeus" :satisfied False :message "" :state "attaching" :current-node "zeus"
                             :other-tickets #())
                 AttachStep.WAIT)
               #((AttachView :attachment True :ticket-node "zeus" :satisfied True :message "" :state "attached" :current-node "zeus"
                             :other-tickets #())
                 AttachStep.READY)
               ;; 満たされても、volume がまだ別の node に付いている間は待つ。
               #((AttachView :attachment True :ticket-node "zeus" :satisfied True :message "" :state "attached" :current-node "atlas"
                             :other-tickets #())
                 AttachStep.WAIT)
               #((AttachView :attachment True :ticket-node "atlas" :satisfied True :message "" :state "attached" :current-node "atlas"
                             :other-tickets #())
                 AttachStep.ELSEWHERE)))
  (for [#(view wanted) cases]
    (<- step (attach-step view "zeus"))
    (assert (= step wanted) #(view step))))


(deftest test-detach-removes-waits-and-refuses-another-nodes-ticket
  (val cases #(#((AttachView :attachment True :ticket-node "zeus" :satisfied True :message "" :state "attached" :current-node "zeus"
                             :other-tickets #())
                 DetachStep.REMOVE)
               #((AttachView :attachment True :ticket-node None :satisfied False :message "" :state "detaching" :current-node "zeus"
                             :other-tickets #())
                 DetachStep.WAIT)
               #((AttachView :attachment True :ticket-node None :satisfied False :message "" :state "detached" :current-node ""
                             :other-tickets #())
                 DetachStep.GONE)
               ;; backup の ticket が付けている間は Longhorn が付けたまま置く — この答え手の分は外れた。
               #((AttachView :attachment True :ticket-node None :satisfied False :message "" :state "attached" :current-node "zeus"
                             :other-tickets #("volume-backup-controller-ws-c-1"))
                 DetachStep.GONE)
               #((AttachView :attachment True :ticket-node "atlas" :satisfied True :message "" :state "attached" :current-node "atlas"
                             :other-tickets #())
                 DetachStep.ELSEWHERE)))
  (for [#(view wanted) cases]
    (<- step (detach-step view "zeus"))
    (assert (= step wanted) #(view step))))


(deftest test-only-tickets-outside-the-cooling-mark-a-volume-in-use
  (<- users (using-tickets #("snapshot-controller-ws-c-1-cool-5" "csi-abc" "backup-controller-ws-c-1-cool-5" "longhorn-api-ws-c-1")))
  (assert (= users #("csi-abc" "longhorn-api-ws-c-1")) users)
  (<- none (using-tickets #("backup-controller-x" "snapshot-controller-y")))
  (assert (= none #()) none))


(deftest test-the-cooling-and-growing-names-fit-a-kubernetes-name
  (val longest (+ NAME-PREFIX (* "a" (- NAME-MAX-LENGTH (len NAME-PREFIX)))))
  (<- name (cooling-name longest 1793643381))
  (assert (= name (+ longest "-cool-1793643381")) name)
  (assert (<= (len name) 63) (len name))
  (<- grow (growing-name longest 1793643381))
  (assert (= grow (+ longest "-grow-1793643381")) grow)
  (assert (<= (len grow) 63) (len grow)))


(deftest test-the-newest-completed-backup-is-restored-from
  (val old (BackupView :name "b-old" :state "Completed" :created-at "2026-11-01T10:00:00Z" :url "s3://t/?backup=b-old&volume=v"))
  (val new (BackupView :name "b-new" :state "Completed" :created-at "2026-11-02T10:00:00Z" :url "s3://t/?backup=b-new&volume=v"))
  (val going (BackupView :name "b-going" :state "InProgress" :created-at "" :url ""))
  (val broken (BackupView :name "b-broken" :state "Error" :created-at "2026-11-03T10:00:00Z" :url ""))
  (<- chosen (newest-backup #(old going new broken)))
  (assert (= chosen new) chosen)
  ;; 失敗ケース: 完了した物が無ければ選ばない(呼び手は空で作らずに断る)。
  (<- nothing (newest-backup #(going broken)))
  (assert (is nothing None) nothing)
  (<- empty (newest-backup #()))
  (assert (is empty None) empty))


(deftest test-a-bundle-is-named-as-longhorn-names-it
  ;; 本番の Longhorn v1.13 が作った束の名(2026-11-04 17:2x UTC の kubectl の読み)と、全体の同期が落ちている間に手で作った束の名(17:42Z —
  ;; Longhorn が受け、1 本だけ同期した)。同じ名で作れば、後から来る Longhorn の同期の作りは AlreadyExists で退く。
  (<- seen (backup-volume-name "ws-a-01f8jkd6e7yaggsssqtrvtxtsw" "garage-workspace"))
  (assert (= seen "ws-a-01f8jkd6e7yaggsssqtrvtxtsw-513858ba") seen)
  (<- trial (backup-volume-name "bvtrial-baef-2" "garage-workspace"))
  (assert (= trial "bvtrial-baef-2-f38f5748") trial)
  ;; 先が違えば名も違う(同じ volume を 2 つの先に置いても重ならない)。
  (<- other (backup-volume-name "ws-a-01f8jkd6e7yaggsssqtrvtxtsw" "garage-other"))
  (assert (and (!= other seen) (.startswith other "ws-a-01f8jkd6e7yaggsssqtrvtxtsw-") (= (len other) (len seen))) other))


;; byte の単位(2 GiB = 作る時の大きさ・10 GiB と 16 GiB = 本番で戻された volume の大きさ・1 MB = 本番の外れた 2 GiB の volume の
;; 多くの実の使用)。
(val GIB 1073741824)
(val MB 1000000)


(deftest test-the-room-is-counted-in-bytes-on-each-longhorn-line
  ;; zeus の USB の SSD の image(2026-11-03 の読み): 105 GB・予約 0・置いた 0・空き 105 GB。名目の予約の線は 200% で 210 GB、実の空きの
  ;; 線は最小の空き 0% で 105 GB・25% で 105 − 26.3 GB。
  (val maximum 105089261568)
  (val empty (DiskRoom :maximum maximum :reserved 0 :scheduled 0 :available maximum))
  (<- unlined (room-bytes #(empty) 200 0 {}))
  (assert (= unlined (RoomBytes :nominal (* 2 maximum) :actual maximum)) unlined)
  (<- lined (room-bytes #(empty) 200 25 {}))
  (assert (= lined (RoomBytes :nominal (* 2 maximum) :actual (- maximum (// maximum 4)))) lined)
  ;; 置いた分を引き、2 つの disk は足す。線を越えて置かれた disk は負のまま足す(空けても線の下は使えない)。
  (<- two (room-bytes #((DiskRoom :maximum (* 10 GIB) :reserved 0 :scheduled (* 3 GIB) :available (* 100 GIB))
                        (DiskRoom :maximum (* 10 GIB) :reserved (* 5 GIB) :scheduled (* 20 GIB) :available (* 100 GIB)))
                      200 25 {}))
  (assert (= two.nominal (+ (* 17 GIB) (* -10 GIB))) two)
  ;; 2026-11-03 16:2x JST の zeus-usb-workspace(過剰割り当て 2000% の繋ぎの下): 実の空き 62.7 GB は最小の空き 25%(26.3 GB)の線まで
  ;; 36.4 GB。宣言の 200% では、今の予約 848 GB が上限 210 GB を越えて負。
  (val now (DiskRoom :maximum maximum :reserved 0 :scheduled 848256040960 :available 62704844800))
  (<- declared (room-bytes #(now) 200 25 {}))
  (assert (= declared (RoomBytes :nominal (- (* 2 maximum) 848256040960) :actual (- 62704844800 (// maximum 4)))) declared)
  (assert (< declared.nominal 0) declared))


(deftest test-a-disk-floor-raises-only-the-actual-line-of-its-disk
  ;; 2026-11-05 21:5x JST の zeus-usb-workspace: 容量 231.9 GB・実の空き 111.3 GB。disk-share/shares.yaml が USB の SSD の分け前から導いた
  ;; 床 93.7 GB は % の線(25% = 58.0 GB)より高いので、実の空きの線の残りは 111.3 − 93.7 GB。名目の線と、床の名の無い disk は変わらない。
  (val maximum 231917150208)
  (val available 111256465408)
  (val floor 93665506817)
  (val usb (DiskRoom :maximum maximum :reserved 0 :scheduled 691489734656 :available available :disk "zeus-usb-workspace"))
  (val other (DiskRoom :maximum maximum :reserved 0 :scheduled 691489734656 :available available :disk "other-disk"))
  (<- floored (room-bytes #(usb) 300 25 {"zeus-usb-workspace" floor}))
  (<- plain (room-bytes #(usb) 300 25 {}))
  (assert (= floored (RoomBytes :nominal plain.nominal :actual (- available floor))) floored)
  (assert (= plain.actual (- available (// maximum 4))) plain)
  (<- unnamed (room-bytes #(other) 300 25 {"zeus-usb-workspace" floor}))
  (assert (= unnamed plain) unnamed)
  ;; 床が % の量より低い時は % の量を残す(床は線を下げない)。
  (<- low (room-bytes #(usb) 300 25 {"zeus-usb-workspace" GIB}))
  (assert (= low plain) low)
  ;; 戻す node も同じ床で選ぶ — 床の上に 10 GB しか無い disk は 20 GB を覆わない。
  (val tight (DiskRoom :node "zeus" :maximum 100 :reserved 0 :scheduled 0 :available 100 :disk "d"))
  (<- covered (restore-node #(tight) 100 0 {"d" 90} 10))
  (assert (= covered "zeus") covered)
  (<- short (restore-node #(tight) 100 0 {"d" 90} 20))
  (assert (= short "") short))


(deftest test-a-demand-is-covered-only-when-both-lines-hold-it
  (for [#(room covered) #(#((RoomBytes :nominal (* 10 GIB) :actual (* 10 GIB)) True)
                          #((RoomBytes :nominal (* 8 GIB) :actual (* 100 GIB)) False)
                          #((RoomBytes :nominal (* 100 GIB) :actual (* 8 GIB)) False)
                          #((RoomBytes :nominal (* -2 GIB) :actual (* 100 GIB)) False))]
    (<- found bool (room-covers? room (* 10 GIB)))
    (assert (= found covered) #(room found))))


;; 刻 10 時間に、使い終わりが 0・7・9・9.5 時間の外れた volume 4 つ(どれも名目 2 GiB)。実の使用は ws-a が 1 MB・ws-b が 3 GiB・ws-c が
;; 1 MB・ws-d が 5 GiB。
(val HOUR 3600.0)
(val NOW (* 10 HOUR))
(val WARM #((WarmVolume :name "ws-d" :used-at (* 9.5 HOUR) :size (* 2 GIB) :actual (* 5 GIB))
            (WarmVolume :name "ws-a" :used-at 0.0 :size (* 2 GIB) :actual MB)
            (WarmVolume :name "ws-c" :used-at (* 9 HOUR) :size (* 2 GIB) :actual MB)
            (WarmVolume :name "ws-b" :used-at (* 7 HOUR) :size (* 2 GIB) :actual (* 3 GIB))))
(val AMPLE (* 1000 GIB))


(deftest test-the-cooling-order-takes-the-stale-then-the-oldest-until-the-demand-is-covered
  ;; 空きが需要を覆っていれば、2 時間を過ぎた物(ws-a・ws-b)だけを古い順に。
  (<- stale (cooling-order WARM NOW (* 2 HOUR) (RoomBytes :nominal AMPLE :actual AMPLE) (* 16 GIB)))
  (assert (= stale (CoolingPlan :names #("ws-a" "ws-b") :covered True)) stale)
  ;; 名目の残り 4 GiB に ws-a・ws-b の 4 GiB を足して 8 GiB < 16 GiB → ws-c と ws-d も足して 12 GiB でもまだ届かない → 覆えないので
  ;; 2 時間を過ぎた物だけ(温かい物を冷やしても入らない)。
  (<- short (cooling-order WARM NOW (* 2 HOUR) (RoomBytes :nominal (* 4 GIB) :actual AMPLE) (* 16 GIB)))
  (assert (= short (CoolingPlan :names #("ws-a" "ws-b") :covered False)) short)
  ;; 名目の残り 8 GiB + 4 GiB = 12 GiB < 16 GiB → ws-c(14 GiB)→ ws-d(16 GiB)で届く。
  (<- reach (cooling-order WARM NOW (* 2 HOUR) (RoomBytes :nominal (* 8 GIB) :actual AMPLE) (* 16 GIB)))
  (assert (= reach (CoolingPlan :names #("ws-a" "ws-b" "ws-c" "ws-d") :covered True)) reach)
  ;; 何も外れていなければ何も冷やさない(足りなければ覆えない)。
  (<- none (cooling-order #() NOW (* 2 HOUR) (RoomBytes :nominal 0 :actual 0) (* 16 GIB)))
  (assert (= none (CoolingPlan :names #() :covered False)) none))


(deftest test-four-free-two-gib-slots-do-not-cover-a-ten-gib-restore
  ;; 2026-11-04 08:05 JST の形: 名目の空きは 2 GiB の 4 本ぶん(8 GiB — 前の数えでは空けておく 4 本に届いて 1 本も冷やさなかった)。
  ;; 10 GiB の戻しの需要には、使い終わりの古い 2 GiB の volume を 1 本冷やして 10 GiB にする(温かく置く時間を待たない)。
  (<- plan (cooling-order WARM NOW (float "inf") (RoomBytes :nominal (* 8 GIB) :actual AMPLE) (* 10 GIB)))
  (assert (= plan (CoolingPlan :names #("ws-a") :covered True)) plan)
  ;; 16 GiB の戻しには 4 本(8 + 8 = 16 GiB)。
  (<- larger (cooling-order WARM NOW (float "inf") (RoomBytes :nominal (* 8 GIB) :actual AMPLE) (* 16 GIB)))
  (assert (= larger (CoolingPlan :names #("ws-a" "ws-b" "ws-c" "ws-d") :covered True)) larger))


(deftest test-a-one-mb-volume-does-not-cover-the-real-space-line
  ;; 実の空きの線の上が 8 GiB で 10 GiB の需要(名目は十分)。一番古い ws-a は名目 2 GiB でも実の使用は 1 MB — 冷やしても実の空きは
  ;; ほぼ空かないので覆わず、次の古い ws-b(実の使用 3 GiB)へ進む。
  (<- plan (cooling-order WARM NOW (float "inf") (RoomBytes :nominal AMPLE :actual (* 8 GIB)) (* 10 GIB)))
  (assert (= plan (CoolingPlan :names #("ws-a" "ws-b") :covered True)) plan)
  ;; 実の使用が 1 MB の物しか無ければ、全部冷やしても覆えない — 何も冷やさない。
  (val tiny (tuple (gfor one WARM (WarmVolume :name one.name :used-at one.used-at :size one.size :actual MB))))
  (<- none (cooling-order tiny NOW (float "inf") (RoomBytes :nominal AMPLE :actual (* 8 GIB)) (* 10 GIB)))
  (assert (= none (CoolingPlan :names #() :covered False)) none))


(deftest test-the-restore-ticket-of-longhorn-is-not-a-user
  ;; Longhorn が戻しのために置く ticket は、失敗した戻しに残っても使っている印でない。ほかの持ち主の ticket は使っている印のまま。
  (<- users (using-tickets #("volume-restore-controller-ws-c-1" "csi-abc")))
  (assert (= users #("csi-abc")) users))


(deftest test-a-restore-runs-on-the-node-of-the-disk-that-can-hold-it
  ;; 札の disk の在る node(1 枚ならその node)。複数なら、需要を両方の線で覆う disk のうち名目の残りが一番大きい disk の node(同じなら名の順)。
  ;; 覆う disk が無ければ空(spec.nodeID を書かない — Longhorn が選ぶ)。
  (val small (DiskRoom :node "alpha" :maximum 100 :reserved 0 :scheduled 80 :available 100))
  (val large (DiskRoom :node "beta" :maximum 100 :reserved 0 :scheduled 20 :available 100))
  (val same (DiskRoom :node "gamma" :maximum 100 :reserved 0 :scheduled 20 :available 100))
  (val dry (DiskRoom :node "delta" :maximum 100 :reserved 0 :scheduled 0 :available 5))
  (<- only (restore-node #(small) 100 0 {} 10))
  (assert (= only "alpha") only)
  (<- larger (restore-node #(small large) 100 0 {} 10))
  (assert (= larger "beta") larger)
  (<- tied (restore-node #(same large) 100 0 {} 10))
  (assert (= tied "beta") tied)
  (<- real (restore-node #(dry small) 100 0 {} 10))
  (assert (= real "alpha") real)
  (<- none (restore-node #(small dry) 100 0 {} 50))
  (assert (= none "") none)
  (<- empty (restore-node #() 100 0 {} 10))
  (assert (= empty "") empty))
