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

(defarchitecture doeff-cluster
  :root "doeff_cluster"
  :layers [(layer foundation :summary "coordinator・worker・土台の handler(src/doeff_cluster/ に平たく置く — 層の置き場へ分けるかは別に決める)")]
  :foundation foundation)

(defservice coordinator "worker へ job を割り当てる coordinator(資源と盤の置き場・調停のループ)"
  {:entry-modules ["doeff_cluster.coordinator"]
   :invariants ["doeff_cluster.coordinator_invariants:acknowledged-writes-survive"]})

;; worker の条(入れ替えの間も書き手が居続ける・消す順など)はまだ無い。:entry-modules(doeff_cluster.main)は最初の条と同じ変更で足す — 足せば DOEFF163 が
;; 条の欠けを数え始める(条の無いまま足すと critical の欠けを置くだけになる)。
(defservice worker "coordinator から job と task を受けて子 process として走らせる worker" {})
