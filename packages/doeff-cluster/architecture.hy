;;; doeff-cluster の service と業務の不変条件の宣言(doeff-linter が実行せずに読む — doeff は monorepo
;;; なので、本番の code を持つ package の根に置き、linter の走査の根をこの package にする)。
;;;
;;; 読むのは この dir で linter を走らせた時だけ(設定は同じ dir の pyproject.toml の [tool.doeff-linter] — linter は今の dir から上へ設定を
;;; 探すので、doeff の根で走る hook と make lint-doeff は根の pyproject.toml を読み、この宣言を読まない)。
;;; この package は層の dir(<root>/<service>/entry/ など)を持たず src/doeff_cluster/ に平たく置くので、code の在りかは :entry-modules で
;;; 宣言する(DOEFF163 がそれで「code を持つ service」を判じる — doeff 042c1fa83)。層の置き場の規則 DOEFF114・115 は pyproject.toml で
;;; 対象外(分けるかは別に決める)。
;;;
;;; 条と確かめる検:
;;;   C1 acknowledged-writes-survive(doeff_cluster.coordinator_invariants:acknowledged-writes-survive)— 返事を返した盤の行は、coordinator が
;;;   止まり置き場から作り直された後も残る。確かめるのは tests/test_local.hy の
;;;   test-a-stopped-coordinator-is-recreated-from-its-store-after-the-downtime(止める前の行と作り直した後の行を判断に渡す)。
;;;   壊した置き場の反例を deftest で結ぶ形(DOEFF167)は別に足す。
;;;   W1 handoff-keeps-a-ready-writer(doeff_cluster.worker_invariants:handoff-keeps-a-ready-writer)— 入れ替え(handoff)を宣言した Service
;;;   は、入れ替えの間も Ready の書き手が途切れない(旧は新が Ready になった後にだけ止める)。確かめるのは tests/test_local.hy の
;;;   test-redeclaring-a-handoff-service-stops-the-old-process-only-after-the-new-one-is-ready(世代ごとの最初の Ready と終わりを判断に渡す)。
;;;   失敗ケースは同じ file の test-a-counterexample-worker-that-stops-the-old-process-on-retire-breaks-w1(sim の宿の RetireJob の handler を
;;;   「外すと同時に旧を止める」形に壊した worker — SimWorker の retire-stops — で、同じ筋書きに W1 の空白が出る)。
;;;   R1 prune-keeps-runs-whole(doeff_cluster.record_store_invariants:prune-keeps-runs-whole)— record-store の保持は run を丸ごと消すか
;;;   丸ごと残し、run の途中だけを残さない(再生は run の始まりから走らせる)。確かめるのは tests/test_record_files_contract.hy の
;;;   test-prune-removes-or-keeps-each-run-whole(区切りを複数持つ run の消す前と後の text を判断に渡す — 本物と memory の file system の両方)。
;;;   失敗ケースは同じ file の test-a-counterexample-remove-that-leaves-part-of-a-run-breaks-r1(保持が使う file system の RemoveTree の答え手を
;;;   「頭の区切りだけを消す」形に壊すと、R1 が 2 つの run を名指す)。

(defarchitecture doeff-cluster
  :root "doeff_cluster"
  :layers [(layer foundation :summary "coordinator・worker・土台の handler(src/doeff_cluster/ に平たく置く — 層の置き場へ分けるかは別に決める)")]
  :foundation foundation)

(defservice coordinator "worker へ job を割り当てる coordinator(資源と盤の置き場・調停のループ)"
  {:entry-modules ["doeff_cluster.coordinator"]
   :invariants ["doeff_cluster.coordinator_invariants:acknowledged-writes-survive"]})

;; worker の条は W1(入れ替えの間も書き手が居続ける)。消す順などの条は後から足す。:entry-modules は層に分ける前の今の入口
;; (doeff_cluster.main)。層に分けた後は :entry-modules を外し、entry 層の dir の定義で「code を持つ service」を数える形に移る。
(defservice worker "coordinator から job と task を受けて子 process として走らせる worker"
  {:entry-modules ["doeff_cluster.main"]
   :invariants ["doeff_cluster.worker_invariants:handoff-keeps-a-ready-writer"]})

;; record-store の条は R1(保持は run を丸ごと)。「追記して fsync してから返事」は file system の性質で、memory の置き場では確かめられない
;; ので条にしていない。:entry-modules は層に分ける前の今の入口(doeff_cluster.record_store_main)。層に分けた後は :entry-modules を外し、
;; entry 層の dir の定義で数える形に移る。
(defservice record-store "effect の記録の置き場(run ごと・区切りごとの file に追記し、読み・一覧・圧縮・保持を答える)"
  {:entry-modules ["doeff_cluster.record_store_main"]
   :invariants ["doeff_cluster.record_store_invariants:prune-keeps-runs-whole"]})
