;;; worker が job の process の木の外に起こす process(預かりの process)の effect と型。
;;;
;;; job の入れ物 shim(worker/entry/shim)は job の終わりに子孫を全部 KILL する(条 C4b)。job の終わりや版の入れ替え(handoff)を越えて
;;; 動き続けてほしい process(例: 会話の CLI 1 本を持つ cli-shim)は、job が自分で起こさず、worker に頼んで worker の子として起こして
;;; もらう。worker はその process を id と持ち主の job の名で覚え、終わりを拾い、持ち主が居なくなった時に止める。systemd が service の
;;; 起き直しの間も process を持ち続ける形・containerd-shim が containerd の入れ替えを越えて残る形と同じ向き。
;;;
;;;   (<- started (| HostedProcess HostedRefused) (LaunchHosted (HostedLaunch :argv #("cli-shim" …) :env #(…) :layer "clis" :owner "turn-host")))
;;;   (<- seen tuple (ListHosted))            ; この worker が覚えている預かりの process の全部
;;;   (<- stopped (| HostedProcess HostedRefused) (StopHosted started.id))
;;;
;;; 答え手: worker の中 = worker/protocol/hosted_host の hosted-host(本物の子を起こす)。job の中 = worker/entry/hosted_client の
;;; hosted-client(worker の口の unix socket へ 1 行の JSON で頼む — socket の path は worker が job の子へ環境変数で渡す)。
;;; 型は claude を知らない汎用の形(argv・env・cgroup の層・持ち主の job の名)。何を起こすかは頼み手の宣言が決める。
(require doeff-hy.macros [val])
(val MODULE-TAGS {:context "doeff-cluster" :role "intent"})
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])
(import doeff [EffectBase])
(import doeff_core_effects [EnvEntry])


(defrecord HostedLaunch
  "預かりの process 1 本の起こし方: argv = 命令・env = 環境変数の全部(worker の環境は継がない — 頼み手が渡した物と、worker が足す
   置き場の 2 つだけ)・layer = 置く cgroup の層の名(機体の配備の層の宣言の名 — 例 clis。None = worker の今の置き方のまま)・owner =
   持ち主の job の名(worker が持ち主の有無を判じる名 — 入れ替えの前と後で同じ名)。"
  {:check [(> (len argv) 0) (> (len owner) 0)]}
  (#^ (get tuple #(str ...)) argv)
  (#^ str owner)
  (setv #^ (get tuple #(EnvEntry ...)) env #())
  (setv #^ (| str None) layer None))


(defrecord HostedProcess
  "worker が覚えている預かりの process 1 本の今: id = worker が振った名(h- と 16 進 16 字)・pid・owner = 持ち主の job の名・layer = 置いた
   層の名(None = 置き分けていない)・place = worker が作った置き場の dir(process の cwd — process は環境変数 DOEFF_HOSTED_PLACE でも
   読む。socket や spool はここに置く)・started-ms = 起こした刻(epoch ms)・ended = 終わりを観測した・exit-code = 終わりの code
   (この worker の世代が起こした子だけが持つ — 起き直しで覚え直した process の終わりは code が分からず None)。"
  (#^ str id)
  (#^ int pid)
  (#^ str owner)
  (#^ (| str None) layer)
  (#^ str place)
  (#^ int started-ms)
  (setv #^ bool ended False)
  (setv #^ (| int None) exit-code None))


(defrecord HostedRefused
  "頼みを断った訳(起こせない・知らない id・知らない層・worker が口を持たない・口に届かない)。例外で落とさず値で返す(頼み手は訳を
   log に出して今の形 — job の木の中に起こす — へ戻れる)。"
  (#^ str detail))


(defclass [(dataclass :frozen True)] LaunchHosted [(get EffectBase (| HostedProcess HostedRefused))]
  "預かりの process を 1 本起こす(頭の註)。答え = 起こした process(HostedProcess)か断り(HostedRefused)。"
  (#^ HostedLaunch launch))


(defclass [(dataclass :frozen True)] StopHosted [(get EffectBase (| HostedProcess HostedRefused))]
  "預かりの process を止めて忘れる(TERM — 猶予の後も残れば KILL。置き場の dir も消す)。答え = 止めた後の姿(ended)か断り
   (知らない id)。"
  (#^ str id))


(defclass [(dataclass :frozen True)] ListHosted [(get EffectBase (| (get tuple #(HostedProcess ...)) HostedRefused))]
  "worker が覚えている預かりの process の全部(id の順・終わった物も止めるか持ち主が消えるまで残る)。断り(HostedRefused)は job の中の
   答え手が worker の口に届かなかった時だけ。")
