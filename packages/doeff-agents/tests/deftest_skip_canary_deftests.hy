;;; deftest の skip が pytest に届いていることの番犬(挙動の検)。
;;;
;;; 必ず真になる skip 条件を書き、本体は必ず失敗させる:
;;;   - マークが pytest に届いている ⇒ 本体は走らない ⇒ skipped(緑)
;;;   - マークが公開時に落ちている   ⇒ 本体が走る     ⇒ failed(赤)
;;; `-rs` の出力を読まなくても、どの起動の仕方でも成立する。
;;; ADR-DOE-HY-002 law deftest-params-are-honored(params_silently_dropped == 0)。

(require doeff-hy.macros [deftest <-])
(import doeff [Pure])


(deftest test-declared-skip-is-honored
  {:skip-if True
   :skip-reason "この skip が pytest に届いていれば本体は走らない"}
  (<- _ (Pure None))
  (assert False
          (+ "書いた skip が pytest に届いていない — 公開する側が deftest を包み直して "
             "pytestmark を落としている(ADR-DOE-HY-002 params_silently_dropped == 0)")))


(deftest test-unskipped-companion-actually-runs
  {:skip-if False
   :skip-reason "走るべき対照 — これが skip されたら番犬自体が空振りしている"}
  (<- v (Pure 1))
  (assert (= v 1)))
