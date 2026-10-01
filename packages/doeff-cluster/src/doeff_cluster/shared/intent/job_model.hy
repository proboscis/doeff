;;; job 1 本の宣言(JobSpec)と job の段階(JobPhase)— coordinator(割り当て・入れ替え・資源の勘定)・worker(起動・観測)・SDK の
;;; 模擬の環境が同じ型を読む共有の部品(worker_model から分けた・#2025 の 1 本目・#2021 の決め 1)。
;;; 指紋 spec-hash は doeff_cluster.shared.core.job_rules。worker だけが使う型(観測・記録・action)は doeff_cluster.worker_model。
(require doeff-hy.macros [val])
(val MODULE-TAGS {:context "doeff-cluster" :role "type"})
(import dataclasses [dataclass field])
(import enum [Enum])


(defclass [(dataclass :frozen True)] JobSpec []
  "job 1 本の宣言。entry は `hy -m` へ渡す module 名、revision は git の commit。once = 1 度だけ走らせる(task)。"
  (#^ str name)
  (#^ str entry)
  (#^ tuple args)
  (#^ str revision)
  (setv #^ bool once False)
  ;; 割り当ての世代(coordinator の Placement.generation)。process を起こした時の値を子 process へ渡し、readiness と計器の報告に
  ;; 載せる。比べない(compare=False): 世代だけが変わっても process を起こし直さない(起こし直すのは spec の中身が変わった時だけ)。
  (setv #^ (| int None) placement (field :default None :compare False))
  ;; 入れ替えの形(2026-09-24)。偽 = 旧を止めてから新を起こす(Recreate)。真 = 新を旧と並べて起こし、coordinator が新の process を
  ;; Ready と数えた(ready-instance がその世代の名になった)後に旧を止める(k8s の RollingUpdate の maxSurge 1・maxUnavailable 0)。
  ;; 名前付きの lease で書きを 1 つに絞る service だけが使う。どちらも比べない欄(値が変わっても process を起こし直さない)。
  (setv #^ bool handoff (field :default False :compare False))
  (setv #^ (| str None) ready-instance (field :default None :compare False))
  ;; 入れ替えの諦め(2026-09-26)。coordinator が「新の世代が期限の間 Ready にならなかった」と記録した handoff の job(heartbeat の返事の
  ;; handoffAbandoned)。worker は新の process を止めて起こし直さず、退いた旧を動かし続ける(worker_policy.plan-job)。宣言が変われば
  ;; coordinator が記録を捨てて偽に戻る。比べない欄。
  (setv #^ bool handoff-abandoned (field :default False :compare False))
  ;; 切り離した task(2026-09-25・once と組)。coordinator との連絡が途絶えても止めない(担い手の heartbeat が lease を延ばし、途絶が
  ;; lease より長ければ coordinator が lost にして、再接続の返事から外れた時に止める — worker_policy.kept-when-cut-off)。比べない欄。
  (setv #^ bool detached (field :default False :compare False))
  ;; 実行環境の宣言(runtime_env_model の RuntimeEnv の JSON を正規化した文字列・2026-09-26)。在れば worker は木を展開せずに env の
  ;; root を準備し(PrepareEnv)、root の venv で子を起こす。比べる欄。
  (setv #^ (| str None) runtime-env None)
  ;; env のキー(この worker の platform で宣言から計算した値・"env-" を付けない)。root の置き場の鍵と子の DOEFF_RUNTIME_ENV_KEY に使う。
  ;; worker の中だけで決まる値なので比べない欄 — 版(revision)は宣言のまま運び、coordinator が同じ宣言から計算する版と指紋(spec-hash)
  ;; に合わせる(版を env のキーに置き換えると、coordinator は「版が違う」で env の service を Ready と数えない)。
  (setv #^ (| str None) env-key (field :default None :compare False))
  ;; Program の job(2026-09-27・ADR-DOE-CLUSTER-001・改訂 1 の F): program = 詰めた Program の置き場のキー(sha256 — worker は
  ;; coordinator の /programs/<sha> から取って子へ file で渡す)。比べない欄 — 同じ Program でも詰めた中身は揺れるので、入れ替えの要否は
  ;; args に載る identity の指紋で決める(改訂 1 の A)。
  (setv #^ (| str None) program (field :default None :compare False))
  ;; 子の環境変数(宣言の :environ・名の順の #(名 値) の tuple — 改訂 1 の G)。比べる欄(変われば入れ替える・spec-hash に入る)。
  (setv #^ tuple environ #())

  (defn #^ None __post-init__ [self]
    (when (or (not self.name) (not self.entry) (not self.revision))
      (raise (ValueError "job には name・entry・revision が必要です")))))


(defclass JobPhase [Enum]
  (setv PREPARING "preparing"
        CODE-FAILED "code-failed"
        STARTING "starting"            ; コードは揃い、次の拍で起動する
        PROBING "probing"              ; コードは揃い、入口の検め(probe)が走っている・同じ木の検めの終わりを待っている
        BACKOFF "backoff"              ; 予期せず終了した後の再起動待ち
        RUNNING "running"
        STOPPING "stopping"
        STOP-UNCONFIRMED "stop-unconfirmed"  ; KILL の後も終了を確認できない。置き換えは起動しない
        PROBE-FAILED "probe-failed"    ; 木は揃ったが、実行環境で入口の module を読み込めない(起動しない)
        ENV-FAILED "env-failed"        ; 実行環境(runtime env)の root を準備できない(子 process を起こしていない)
        HANDOFF-ABANDONED "handoff-abandoned"  ; 入れ替えを諦めた(新は起こさない・退いた旧が動いている — 宣言が変わるまで)
        FINISHED "finished"            ; task が終わった(起動し直さない)
        STOPPED "stopped"))
