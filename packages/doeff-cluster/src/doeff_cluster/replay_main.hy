;;; 再生の子 process の入口(記録係の便 = 段 4 で書き直す)。
;;;
;;; 旧い形(記録の header の factory・config から Program を作り直し、入口が再生の handler を足す)は、job API の変更
;;; (ADR-DOE-CLUSTER-001 R1・R5 — 記録と再生は Program の中の境目の handler で、入口は handler を足さない)で成り立たなくなった。
;;; 新しい形(記録の header が運ぶ詰めた Program を解き、再生の mode の環境で走らせるだけ)は段 4 で置く。それまでは理由つきで止まる。
(import sys)


(defn main []  ; defk にできない: process の入口
  "旧い再生の入口は受け付けない(理由を出して止まる)。"
  (print "replay_main: 旧い再生の入口(header の factory・--config)は受け付けない — 段 4 の新しい入口(記録の header の Program を再生の mode で走らせる)を使う"
         :file sys.stderr :flush True)
  (sys.exit 2))


(when (= __name__ "__main__")
  (main))
