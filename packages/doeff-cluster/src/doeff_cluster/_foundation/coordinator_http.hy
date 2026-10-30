;;; coordinator へ話す口の定数(返事と接続の上限・読みを送り直す期限と間・先頭の宛先を試し直す間)と、書きの送り手の既定の名。
;;; 送り方そのもの(宛先の順・切り替え・送り直し)は shared/protocol/coordinator_route.hy の宛先の部品(汎用の HttpRequest の上)— 前は
;;; この module が httpx の client を持つ口を持っていた(#2427 で退役)。下の実測と直しの理由は宛先の部品の振る舞いの出自。
;;;
;;; 2026-09-23 の実測(newmac の `turn-runner` が `GET /board` の `ConnectTimeout` で 5 回落ちた件):
;;;
;;; - 落ちた時刻 5 回とも、atlas の tailscaled が newmac への経路を IPv4(LAN)から newmac の IPv6 の番地へ切り替えた
;;;   時刻と 1 秒以内で重なった。21:29:46〜57 の回を atlas の tailscale0 で記録すると、newmac の SYN は届き、atlas は
;;;   SYN-ACK をすぐ返しているのに newmac に届かず、newmac は 6〜13 秒 SYN を送り直し続けた(同じ時間の LAN 経由の
;;;   接続は失われなかった)。途絶は数秒〜十数秒で、tailscaled が IPv4 へ戻すと直る。tailnet の経路の揺れは worker の外。
;;; - worker の側の弱さは 2 つ: coordinator が要求ごとに接続を閉じていた(HTTP/1.0)ので client は要求のたびに
;;;   TCP の接続を張り直し(newmac 1 台で毎秒 3 本前後)、途絶の間の新しい接続は必ず失敗した。そして共有の保存の読みは
;;;   1 回の失敗で例外を業務の Program へ投げ、service の process ごと落ちた。
;;; - 直し: 接続を使い回す(coordinator の側を HTTP/1.1 にした — `coordinator.hy`)。接続の段の上限を短くし、その段の
;;;   失敗だけは書きでも送り直す(要求がまだ相手に届いていない。httpx の transport の retries は ConnectError と
;;;   ConnectTimeout だけを送り直す)。何度送っても同じ意味の読みは、切れ方を問わず期限まで送り直す(宛先の部品の resent-request)。
;;;   自己停止(20 秒)と移し替え(45 秒)の時間は cluster_model の ClusterTiming。読みを送り直す期限 IDEMPOTENT-DEADLINE-SECONDS は
;;;   その ClusterTiming から導くので shared/core/resend.hy(層 foundation は intent を読まない・#2566)。
(require doeff-hy.macros [val])
(val MODULE-TAGS {:context "doeff-cluster" :role "foundation"})

;; 接続の段の上限(秒)。tailnet の上の往復は数 ms なので、2 秒待って届かない SYN は待つより送り直す方が早い。
(setv CONNECT-SECONDS 2.0)

;; 送り直しの間(秒)。本番の宛先の部品(resent-request)と手元の sim-cluster の宿(local.hy — sim の時計で眠る)が同じ値を使う。
(val RESEND-PAUSE-SECONDS 0.5)

;; 先の宛先(LAN)から後ろ(tailnet)へ回った後、先の宛先を試し直すまでの秒。
(setv PREFERRED-RECHECK-SECONDS 60.0)


(defn #^ str default-actor []
  "送り手の既定: worker の子 process なら「job 名@worker 名/pid」、それ以外は「pid@機体」。ASCII に限る(HTTP の header)。"
  (import os socket)
  (setv job (os.environ.get "DOEFF_WORKER_JOB") worker (os.environ.get "DOEFF_WORKER_NAME"))
  (setv actor (if (and job worker)
                  (.format "{}@{}/{}" job worker (os.getpid))
                  (.format "{}@{}" (os.getpid) (socket.gethostname))))
  (.decode (.encode actor "ascii" "replace") "ascii"))
