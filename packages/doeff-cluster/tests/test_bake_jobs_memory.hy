;; 焼きの並列数を memory の空きで絞る判断(worker/core/bake_plan の memory-room-of・bake-jobs-of・cgroup-levels-of)。
;; 失敗ケース(2026-11-02 18:56 JST の k3s-0): 並列数を CPU の数(2)だけで決め、memory 4 GiB の node の jobs の層(896 MiB のうち動く job が
;; 約 450 MiB)で焼く子を 2 本並べ、子が層の上限で殺されて準備が落ちた(測り: 子 1 本の山 198〜316 MiB・入口の山 191 MiB)。
;; 並列数は CPU の数を上限に、居る cgroup とその上の層の 上限 − 今の使い の最小から、子 1 本の見積もりで入る数へ絞る。
(require doeff-hy.macros [deftest val <-])
(import doeff_cluster._worker.core.bake_plan [memory-room-of bake-jobs-of cgroup-levels-of BAKE-CHILD-BYTES BAKE-FIXED-BYTES])

(val MIB (* 1024 1024))


(deftest test-the-room-is-the-smallest-headroom-of-the-limited-levels
  ;; 層ごとの 上限 − 今の使い の最小。上限の無い(max)・読めない層は数えない。どれも上限を持たなければ None。
  (<- room (| int None) (memory-room-of #(#("939524096\n" "471859200\n") #("3758096384" "1262575616") #("max" "5"))))
  (assert (= room (- 939524096 471859200)) room)
  (<- unlimited (| int None) (memory-room-of #(#("max\n" "100") #(None None))))
  (assert (is unlimited None))
  ;; 使いが上限を越えた読みは空き 0(負にしない)。
  (<- over (| int None) (memory-room-of #(#("100" "200"))))
  (assert (= over 0)))


(deftest test-the-jobs-shrink-to-what-the-room-holds
  ;; k3s-0 の jobs の層(896 MiB・動く job 約 450 MiB → 空き約 446 MiB)と準備の層(640 MiB・空)では 1 本 — CPU の 2 では並べない。
  (<- in-jobs int (bake-jobs-of 2 (* 446 MIB)))
  (assert (= in-jobs 1) in-jobs)
  (<- in-prepare int (bake-jobs-of 2 (* 640 MIB)))
  (assert (= in-prepare 1) in-prepare)
  ;; 1 本も入らない空きでも 1(道具の親が並べずに焼く)。上限の無い層は CPU の数のまま。
  (<- none-fit int (bake-jobs-of 2 (* 10 MIB)))
  (assert (= none-fit 1))
  (<- free int (bake-jobs-of 8 None))
  (assert (= free 8))
  ;; 大きい空きでは CPU の数(上限)を越えない。子 2 本ちょうど入る空きで 2。
  (<- big int (bake-jobs-of 4 (* 64 1024 MIB)))
  (assert (= big 4))
  (<- two int (bake-jobs-of 4 (+ BAKE-FIXED-BYTES (* 2 BAKE-CHILD-BYTES))))
  (assert (= two 2)))


(deftest test-the-levels-run-from-the-own-cgroup-up-to-the-root
  ;; /proc/self/cgroup の v2 の行から、居る cgroup とその上の層(近い順・根を含む)。Pod の container は名前空間の根が自分の cgroup。
  (<- pod tuple (cgroup-levels-of "0::/prepare\n" "/sys/fs/cgroup"))
  (assert (= pod #("/sys/fs/cgroup/prepare" "/sys/fs/cgroup")) pod)
  (<- deep tuple (cgroup-levels-of "0::/a/b\n" "/sys/fs/cgroup"))
  (assert (= deep #("/sys/fs/cgroup/a/b" "/sys/fs/cgroup/a" "/sys/fs/cgroup")) deep)
  (<- root tuple (cgroup-levels-of "0::/\n" "/sys/fs/cgroup"))
  (assert (= root #("/sys/fs/cgroup")) root)
  (<- unread tuple (cgroup-levels-of None "/sys/fs/cgroup"))
  (assert (= unread #("/sys/fs/cgroup")) unread))
