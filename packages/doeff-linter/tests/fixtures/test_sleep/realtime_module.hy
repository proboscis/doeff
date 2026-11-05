;;; Fixture for DOEFF211 — a file that claims realtime at the module level (the shape of a test helper that waits, with a deadline, for a real external process).
;;; The top-level (val pytestmark ...) has realtime in its marker list, so no sleep inside the file fires.
;;; tests/semgrep/test_realtime_tests_registry.py reads this file as "one module-level claim". This file is not executed.
(require doeff-hy.macros [deftest defk <- val])
(import time)
(import pytest)

(val pytestmark [pytest.mark.slow pytest.mark.realtime])


(defk wait-until-the-child-ends [child deadline]
  {:pre [(: deadline float)] :post [(: % bool)] :tags {:context "sample" :role "program"}}
  "Wait, with a deadline, for a real child process to end."
  (while (and (is (.poll child) None) (< (time.monotonic) deadline))
    (time.sleep 0.05))
  (is-not (.poll child) None))


(deftest test-the-child-ends
  (<- ended (wait-until-the-child-ends (start-child) (+ (time.monotonic) 30)))
  (time.sleep 0.1)
  (assert ended))
