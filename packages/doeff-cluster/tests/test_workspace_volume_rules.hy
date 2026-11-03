;;; 作業場所の volume の純粋な判断(doeff_cluster._shared.core.workspace_volume_rules)の検 — 持ち主の id の条件と名・mountinfo の読み・
;;; attach と detach の次の一手。
;;;
;;;   名                持ち主の id は英字・数字・-(頭と尻は英字か数字)・37 字まで。名は ws- + 小文字(40 字まで)。外れる id は断る(失敗ケース —
;;;                     縮めた名・重なった名・k8s の名に成らない名を作らない)
;;;   mountinfo         path の 8 進の escape を戻す・後の行ほど上・Longhorn の device の source だけを volume の mount と読む
;;;   attach の一手     VolumeAttachment がまだ無い → 待つ・ticket が無い → 置く・別の node → 断る(失敗ケース — 別の機体の ticket を
;;;                     書き換えない)・満たされて付いた → 付いた
;;;   detach の一手     別の node → 断る(失敗ケース)・この node → 外す・外れる途中 → 待つ・ほかの ticket が付けている → この答え手の分は外れた
;;;   冷やす            使っている印の ticket(冷やす手順の snapshot と backup の ticket を除く)・snapshot と backup の名(63 字まで)・
;;;                     戻す元(完了した物の一番新しい物 — 途中・失敗・url の無い物は選ばない)・札の disk の空きの本数(Longhorn の置く線・
;;;                     負は 0)・冷やす順(2 時間を過ぎた物は全部・足りない空きの分を古い順に・空きが足りていれば足さない)
(require doeff-hy.macros [deftest <- val])
(import doeff_cluster._shared.core.workspace_volume_rules [AttachStep AttachView BackupView DetachStep DiskRoom MountEntry NAME-MAX-LENGTH
                                                         NAME-PREFIX WarmVolume attach-step cooling-name cooling-order detach-step
                                                         free-slots mount-entries mounted-at newest-backup owner-refusal ticket-id
                                                         using-tickets volume-mount-points volume-name volume-of-entry])

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


(deftest test-the-cooling-name-fits-a-kubernetes-name
  (val longest (+ NAME-PREFIX (* "a" (- NAME-MAX-LENGTH (len NAME-PREFIX)))))
  (<- name (cooling-name longest 1793643381))
  (assert (= name (+ longest "-cool-1793643381")) name)
  (assert (<= (len name) 63) (len name)))


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


(deftest test-free-slots-follow-the-longhorn-scheduling-line
  (val size 10737418240)
  ;; zeus の USB の SSD の image(2026-11-03 の読み): 105 GB・予約 0・置いた 0・空き 105 GB。名目の予約の線は 200% で 19 本、実の空きの
  ;; 線は 105 GB / 10 GiB = 9 本(最小の空き 0%)・(105 − 26.3) GB / 10 GiB = 7 本(25%)— 少ない方が効く。空きが十分なら名目の 19 本。
  (val empty (DiskRoom :maximum 105089261568 :reserved 0 :scheduled 0 :available 105089261568))
  (<- unlined (free-slots #(empty) size 200 0))
  (assert (= unlined 9) unlined)
  (<- lined (free-slots #(empty) size 200 25))
  (assert (= lined 7) lined)
  (<- wide (free-slots #((DiskRoom :maximum 105089261568 :reserved 0 :scheduled 0 :available (* 100 size))) size 200 25))
  (assert (= wide 19) wide)
  ;; 置いた分を引く。2 つの disk は足す。名目で溢れた disk は 0(負にしない)。
  (<- two (free-slots #((DiskRoom :maximum (* 10 size) :reserved 0 :scheduled (* 3 size) :available (* 100 size))
                        (DiskRoom :maximum (* 10 size) :reserved (* 5 size) :scheduled (* 20 size) :available (* 100 size)))
                      size 200 25))
  (assert (= two 17) two))


(deftest test-free-slots-stop-at-the-minimal-available-line
  ;; 2026-11-03 16:2x JST の zeus-usb-workspace(名目 10 GiB の volume 79 本・過剰割り当てを 2000% に上げた繋ぎの下): 2 GiB の volume を
  ;; 名目の予約の線では 583 本置けるが、実の空きは 62.7 GB で最小の空き 25%(26.3 GB)の線まで 36.4 GB — 名目まで書いて 16 本。
  ;; 宣言の 200% では、今の予約 848 GB が上限 210 GB を越えているので 0 本。
  (val gib 1073741824)
  (val now (DiskRoom :maximum 105089261568 :reserved 0 :scheduled 848256040960 :available 62704844800))
  (<- reserve (free-slots #((DiskRoom :maximum now.maximum :reserved 0 :scheduled now.scheduled :available (* 10000 gib))) (* 2 gib) 2000 25))
  (assert (= reserve 583) reserve)
  (<- both (free-slots #(now) (* 2 gib) 2000 25))
  (assert (= both 16) both)
  (<- declared (free-slots #(now) (* 2 gib) 200 25))
  (assert (= declared 0) declared)
  ;; 線を割った disk(空きが最小の空きより少ない)は 0(負にしない)。
  (<- under (free-slots #((DiskRoom :maximum (* 100 gib) :reserved 0 :scheduled 0 :available (* 20 gib))) gib 200 25))
  (assert (= under 0) under)
  ;; 線の上がちょうど 1 本ぶんなら 1 本。
  (<- one (free-slots #((DiskRoom :maximum (* 100 gib) :reserved 0 :scheduled 0 :available (* 26 gib))) gib 200 25))
  (assert (= one 1) one))


(deftest test-the-cooling-order-takes-the-stale-then-the-oldest
  (val hour 3600.0)
  (val warm #((WarmVolume :name "ws-d" :used-at (* 9.5 hour)) (WarmVolume :name "ws-a" :used-at 0.0)
              (WarmVolume :name "ws-c" :used-at (* 9 hour)) (WarmVolume :name "ws-b" :used-at (* 7 hour))))
  (val now (* 10 hour))
  ;; 空きが足りていれば、2 時間を過ぎた物だけを古い順に。
  (<- stale (cooling-order warm now (* 2 hour) 20 8))
  (assert (= stale #("ws-a" "ws-b")) stale)
  ;; 空き 5 と冷やす 2 で 7 < 8 → もう 1 本(残りの一番古い ws-c)。
  (<- short (cooling-order warm now (* 2 hour) 5 8))
  (assert (= short #("ws-a" "ws-b" "ws-c")) short)
  ;; 空きが 0 なら、外れた物は全部(足りない数より少ない)。
  (<- all (cooling-order warm now (* 2 hour) 0 8))
  (assert (= all #("ws-a" "ws-b" "ws-c" "ws-d")) all)
  ;; 何も外れていなければ何も冷やさない。
  (<- none (cooling-order #() now (* 2 hour) 0 8))
  (assert (= none #()) none))
