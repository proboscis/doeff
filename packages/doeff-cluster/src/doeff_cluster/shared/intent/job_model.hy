;;; job 1 本の宣言(JobSpec)と job の段階(JobPhase)— coordinator(割り当て・入れ替え・資源の勘定)・worker(起動・観測)・SDK の
;;; 模擬の環境が同じ型を読む共有の部品(worker_model から分けた・#2025 の 1 本目・#2021 の決め 1)。
;;; 指紋 spec-hash は doeff_cluster.shared.core.job_rules。worker だけが使う型(観測・記録・action)は doeff_cluster.worker.intent.worker_model。
(require doeff-hy.macros [val])
(val MODULE-TAGS {:context "doeff-cluster" :role "type"})
(import dataclasses [dataclass field])
(import enum [Enum])
(import functools [cached-property])
(import hashlib)
(import json)


(defclass [(dataclass :frozen True)] JobSpec []
  "job 1 本の宣言。entry は `hy -m` へ渡す module 名、revision は git の commit。once = 1 度だけ走らせる(task)。"
  (#^ str name)
  (#^ str entry)
  (#^ (get tuple #(str ...)) args)
  (#^ str revision)
  (setv #^ bool once False)
  ;; 割り当ての世代(coordinator の Placement.generation)。process を起こした時の値を子 process へ渡し、readiness と計器の報告に
  ;; 載せる。比べない(compare=False): 世代だけが変わっても process を起こし直さない(起こし直すのは spec の中身が変わった時だけ)。
  (setv #^ (| int None) placement (field :default None :compare False))
  ;; 入れ替えの形(2026-09-24)。偽 = 旧を動かしたまま新しい版の準備と入口の検めを済ませ、旧を止めてから新を起動する(Recreate —
  ;; 2026-10-08・worker_policy.replace-step)。真 = 新を旧と並べて起こし、coordinator が新の process を
  ;; Ready と数えた(ready-instance がその世代の名になった)後に旧を止める(k8s の RollingUpdate の maxSurge 1・maxUnavailable 0)。
  ;; 名前付きの lease で書きを 1 つに絞る service だけが使う。どちらも比べない欄(値が変わっても process を起こし直さない)。
  (setv #^ bool handoff (field :default False :compare False))
  (setv #^ (| str None) ready-instance (field :default None :compare False))
  ;; 入れ替えの諦め(2026-09-26)。coordinator が「新の世代が期限の間 Ready にならなかった」と記録した handoff の job(heartbeat の返事の
  ;; handoffAbandoned)。worker は新の process を止めて起こし直さず、退いた旧を動かし続ける(worker_policy.plan-job)。宣言が変われば
  ;; coordinator が記録を捨てて偽に戻る。比べない欄。
  (setv #^ bool handoff-abandoned (field :default False :compare False))
  ;; 入れ替えで退いた process の寿命の上限(ms — #4072 の D-2・宣言の readiness の retiredSeconds・handoff の job だけ)。None = 今どおり
  ;; 新の世代が Ready と数えられた時に退いた旧を止める。値が在れば新の Ready で止めず、旧が自分で終わるか、退いてからこの長さを越えた時に
  ;; 止める(worker_policy.retired-actions)。比べない欄(値が変わっても process を起こし直さない)。
  (setv #^ (| int None) retired-ms (field :default None :compare False))
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
  (setv #^ (get tuple #((get tuple #(str str)) ...)) environ #())
  ;; 途絶しても動かし続けてよい印(#2804 — heartbeat の返事の job の行の keepWhenCutOff)。coordinator が「他に置ける worker が
  ;; 無い」と判じた入れ替えでない service の job に付け、印を渡した担い手からは、担い手が印を持たないと知らせるか Worker が消されるまで
  ;; 他へ移さない(cluster_policy の keep-marks)。worker は印の在る job を coordinator との途絶(fence)でも止めない — 長い方の柵
  ;; ClusterTiming.keep-fence-ms(240 秒)を越えるまで(worker_policy.kept-when-cut-off?)。欄の無い返事(古い coordinator)は偽 = 今までどおり fence で止める。比べない欄(印だけが変わっても
  ;; process を起こし直さない)。位置の引数で作る呼び手を崩さないよう最後に置く。
  (setv #^ bool keep-when-cut-off (field :default False :compare False))
  ;; 版を据え置く印(#3684)。worker の返事の読み(worker/protocol/declared.declared-job-specs)が、heartbeat の返事の draining(この worker が
  ;; drain 中)を返事の job の全部に写す。worker は印の在る job の spec が変わっても、止めていない process をそのまま動かす(worker_policy.plan-job
  ;; — drain の間に宣言し直された新しい版を、移される worker の上で準備も起動もしない)。宣言から消えた job は今までどおり止め、drain が解けて
  ;; 印が偽に戻った拍から普通の入れ替えへ進む。coordinator は持たない(返事の JSON にも保存にも載らない — worker の中だけの欄)。比べない欄
  ;; (印だけが変わっても process を起こし直さない・指紋 spec-hash に入らない)。位置の引数で作る呼び手を崩さないよう最後に置く。
  (setv #^ bool hold-version (field :default False :compare False))
  ;; 子の入口が比べる送り手の版(#3762)。task = task の行の versions(task を作った時の送り手の版 — 名の順の #(名 版) の tuple)・
  ;; None = coordinator の Program の行の版(service — /programs/<sha> の答えの versions)。同じ sha の Program を後から別の版の送り手が
  ;; 置くと Program の行の版は上書きされるので、待っている task の版は task の行から読む(2026-10-06 の t661)。worker は task の
  ;; Program の cache の file を版ごとに分けて置く(worker/core/launch.spec-program-file)。coordinator は持たない(worker の中だけの欄)。
  ;; 比べない欄(task は 1 度だけ走る・指紋 spec-hash に入らない)。位置の引数で作る呼び手を崩さないよう最後に置く。
  (setv #^ (| (get tuple #((get tuple #(str str)) ...)) None) versions (field :default None :compare False))

  (defn #^ None __post-init__ [self]
    (when (or (not self.name) (not self.entry) (not self.revision))
      (raise (ValueError "job には name・entry・revision が必要です"))))

  (defn [cached-property] #^ str fingerprint [self]  ; defk にできない: 値の属性(Program の外の純粋な判断 probe-of・statuses が読む)
    "指紋 spec-hash の計算の本体(公開の入口は doeff_cluster.shared.core.job_rules.spec-hash)。値ごとに最初に読まれた時に 1 度だけ
     計り、その値の中に覚える(覚えの寿命 = この値。module の大域には持たない)。dataclasses.replace で作り直した値は別の object なので
     計り直す — 欄が変われば指紋も変わる。材料の欄(name・entry・args・revision・once・runtime-env・environ)は str・bool と str の
     tuple・str の組の tuple だけで、値を作った後に中身が書き換わらないので、覚えた指紋は古くならない。"
    (cut (.hexdigest (hashlib.sha256 (.encode (json.dumps (+ [self.name self.entry (list self.args) self.revision self.once]
                                                             (if self.runtime-env [self.runtime-env] [])
                                                             (if self.environ [(lfor #(k v) self.environ [k v])] []))
                                                          :ensure-ascii False :separators #("," ":"))
                                              "utf-8")))
         0 16)))


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
