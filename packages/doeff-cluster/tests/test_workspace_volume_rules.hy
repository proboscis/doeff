;;; 作業場所の volume の純粋な判断(doeff_cluster._shared.core.workspace_volume_rules)の検 — 持ち主の id の条件と名・mountinfo の読み・
;;; attach と detach の次の一手。
;;;
;;;   名                持ち主の id は英字・数字・-(頭と尻は英字か数字)・37 字まで。名は ws- + 小文字(40 字まで)。外れる id は断る(失敗ケース —
;;;                     縮めた名・重なった名・k8s の名に成らない名を作らない)
;;;   mountinfo         path の 8 進の escape を戻す・後の行ほど上・Longhorn の device の source だけを volume の mount と読む
;;;   attach の一手     VolumeAttachment がまだ無い → 待つ・ticket が無い → 置く・別の node → 断る(失敗ケース — 別の機体の ticket を
;;;                     書き換えない)・満たされて付いた → 付いた
;;;   detach の一手     別の node → 断る(失敗ケース)・この node → 外す・外れる途中 → 待つ・ほかの ticket が付けている → この答え手の分は外れた
(require doeff-hy.macros [deftest <- val])
(import doeff_cluster._shared.core.workspace_volume_rules [AttachStep AttachView DetachStep MountEntry NAME-MAX-LENGTH NAME-PREFIX
                                                         attach-step detach-step mount-entries mounted-at owner-refusal ticket-id
                                                         volume-mount-points volume-name volume-of-entry])

(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})

;; 持ち主の id の形(Crockford の base32 の ULID・頭 c-)。
(val OWNER "[id]")
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
  (assert (= name "ws-c-k973zx4fcyma7pz498c0464b7g") name)
  (assert (<= (len name) NAME-MAX-LENGTH))
  (<- ticket (ticket-id name))
  (assert (= ticket "longhorn-api-ws-c-k973zx4fcyma7pz498c0464b7g") ticket))


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
