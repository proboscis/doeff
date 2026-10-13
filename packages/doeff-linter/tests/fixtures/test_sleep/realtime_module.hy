;;; DOEFF211 の検体 — module の段で realtime を名乗る file(本物の外の process を締め切りつきで待つ検の道具の形)。
;;; 最上位の (val pytestmark …) が印の list に realtime を持つので、file の中の眠りはどれも鳴らない。
;;; tests/semgrep/test_realtime_tests_registry.py は、この file を「module の段の名乗り 1 つ」と読む。この file は実行しない。
(require doeff-hy.macros [deftest defk <- val])
(import time)
(import pytest)

(val pytestmark [pytest.mark.slow pytest.mark.realtime])


(defk wait-until-the-child-ends [child deadline]
  {:pre [(: deadline float)] :post [(: % bool)] :tags {:context "sample" :role "program"}}
  "本物の子 process の終わりを締め切りつきで待つ。"
  (while (and (is (.poll child) None) (< (time.monotonic) deadline))
    (time.sleep 0.05))
  (is-not (.poll child) None))


(deftest test-the-child-ends
  (<- ended (wait-until-the-child-ends (start-child) (+ (time.monotonic) 30)))
  (time.sleep 0.1)
  (assert ended))
