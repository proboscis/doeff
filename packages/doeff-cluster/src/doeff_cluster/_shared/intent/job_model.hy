;;; job 1 本の宣言(JobSpec)と job の段階(JobPhase)— coordinator(割り当て・入れ替え・資源の勘定)・worker(起動・観測)・SDK の
;;; 模擬の環境が同じ型を読む共有の部品(worker_model から分けた・#2025 の 1 本目・#2021 の決め 1)。
;;; 指紋 spec-hash は doeff_cluster._shared.core.job_rules。worker だけが使う型(観測・記録・action)は doeff_cluster._worker.intent.worker_model。
(require doeff-hy.macros [val])
(val MODULE-TAGS {:context "doeff-cluster" :role "type"})
(import dataclasses [dataclass field])
(import enum [Enum])
(import functools [cached-property])
(import hashlib)
(import json)


;; 子の入口(job_entry・warm_child)の在る package の置き場の列(新しい置き場を先に)。子は job の root の版の
;; doeff で起きるので、root へ送る名は root ごとに、その root の中で解ける置き場から選ぶ(worker の準備の確かめが root の答えを完成の印の
;; entryPlace へ書く — worker/core/env_prepare の stage-probe)。区画 worker を _worker へ隠す前(c04f425ed より前)の doeff の root は
;; 2 つ目の置き場だけを持つ。
(val CHILD-ENTRY-PLACES #("doeff_cluster._worker.entry" "doeff_cluster.worker.entry"))
;; 完成の印に置き場の欄の無い root の置き場: 欄を足す前の worker が準備した root で、その頃の doeff はどれも 2 つ目の置き場の名を解く
;; (_worker の在る版も、移し替えの module worker.py が 2 つ目の名を _worker の module に解く)。
(val LEGACY-ENTRY-PLACE "doeff_cluster.worker.entry")


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
  ;; 退いた process を同時に残す数の上限 R(宣言の readiness の retiredLimit・handoff の job だけ)。None = worker の既定
  ;; (WorkerPolicy.retired-limit)。R に達している間の宣言し直しは、退いた process を止めず今の process も退かせずに待つ
  ;; (worker_policy.handoff-actions・#4072 の D-3 の改め)。比べない欄(値が変わっても process を起こし直さない)。
  (setv #^ (| int None) retired-limit (field :default None :compare False))
  ;; 退きの保ちを持つ退いた process の世代の名(handoff の job だけ — readiness_model の RETIREMENT-HOLD)。coordinator が
  ;; 今の世代の worker への返事に、期限の内の保ちの持ち手を名の順で載せる。worker は列に在る退いた process を寿命の上限 retired-ms で
  ;; 止めない(worker_policy.retired-actions — 保ちが無くなった拍から今どおり)。空 = 誰も持たない。比べない欄。
  (setv #^ (get tuple #(str ...)) retired-held (field :default #() :compare False))
  ;; 世代をまたぐ入れ替えの印(handoff の job だけ・AL-D2)。同じ名の worker の新しい世代が同じ job の process を Ready にした後、
  ;; coordinator が退いた世代の worker への返事(superseded-reply)に、その Ready の process の世代の名を載せる。退いた世代の worker は
  ;; 今の process を止めずに退かせ(RetireJob — 走っている仕事を終えて自分で終わる・寿命の上限は retired-ms)、名が空いても起こし直さない
  ;; (worker_policy.plan-job)。退いた世代の Pod の止まりが始まった後(AL-D2k の leaving)は、今の世代の process が Ready と数えられて
  ;; いなくても今の世代の名(generation:<boot>)を載せる(AL-D2n — 印が無いと、退かせた process の終わりの後に新しい試行を起こす)。
  ;; worker は値を読まず、在るかだけを読む。None = 今どおり。比べない欄(値が変わっても process を起こし直さない)。
  (setv #^ (| str None) superseded-by (field :default None :compare False))
  ;; 前の世代が動かしている印(recreate の job だけ・AL-D2f)。同じ名の worker の前の世代がまだ走っている間に起きた新しい世代への返事で、
  ;; 前の世代がこの job を動かしている間、coordinator が付ける。worker は準備と入口の検めだけをして起こさず、揃ったら状態の行を
  ;; elder-runs にする(coordinator はそれを見て前の世代から外し、前の世代が止め終えたら印を外す)。偽 = 今どおり。比べない欄。
  (setv #^ bool elder-runs (field :default False :compare False))
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
  ;; 無い」と判じた入れ替えでない service の job に付け、印を渡した担い手からは、担い手が印を持たないと知らせるか、Worker が消されるか、
  ;; 担い手の沈黙が約束の期限(ClusterTiming.kept-reassign-after-ms)を越えるまで他へ移さない(cluster_policy の keep-marks)。worker は印の在る job を coordinator との途絶(fence)でも止めない — 長い方の柵
  ;; ClusterTiming.keep-fence-ms(240 秒)を越えるまで(worker_policy.kept-when-cut-off?)。欄の無い返事(古い coordinator)は偽 = 今までどおり fence で止める。比べない欄(印だけが変わっても
  ;; process を起こし直さない)。位置の引数で作る呼び手を崩さないよう最後に置く。
  (setv #^ bool keep-when-cut-off (field :default False :compare False))
  ;; 版を据え置く印(#3684)。worker の返事の読み(worker/protocol/declared.declared-job-specs)が、heartbeat の返事の draining(この worker が
  ;; drain 中)を返事の job の全部に写す。worker は印の在る job の spec が変わっても、止めていない process をそのまま動かす(worker_policy.plan-job
  ;; — drain の間に宣言し直された新しい版を、移される worker の上で準備も起動もしない)。宣言から消えた job は今までどおり止め、drain が解けて
  ;; 印が偽に戻った拍から普通の入れ替えへ進む。coordinator は持たない(返事の JSON にも保存にも載らない — worker の中だけの欄)。比べない欄
  ;; (印だけが変わっても process を起こし直さない・指紋 spec-hash に入らない)。位置の引数で作る呼び手を崩さないよう最後に置く。
  (setv #^ bool hold-version (field :default False :compare False))
  ;; 子の入口が比べる送り手の版(名の順の #(名 版) の tuple — #3762)。Program の job は必ず持つ: service = Service の宣言の行の
  ;; run.versions(coordinator が spec-of-declaration で読み、heartbeat の返事の job の行に載せる)・task = task の行の versions(task を
  ;; 作った時の送り手の版)。coordinator の Program の行の版は、同じ sha の Program を後から別の版の送り手が置くと上書きされるので使わない
  ;; (2026-10-06 の t661・2026-10-10 の zeus の merge-queue)。worker は Program の cache の file を版ごとに分けて
  ;; 置く(worker/core/launch.spec-program-file)— 版が替われば別の file なので取り直す。None = Program を持たない job。
  ;; 比べない欄(service の版の替わりは identity の指紋で args に載る・指紋 spec-hash に入らない)。位置の引数で作る呼び手を崩さないよう最後に置く。
  (setv #^ (| (get tuple #((get tuple #(str str)) ...)) None) versions (field :default None :compare False))
  ;; 中身の鍵。宣言の行の content — 送り手が「この job の process が読む code の全部(どの repo の木の、どの file の
  ;; 中身か・依存の lock)」から計った鍵。在れば指紋 spec-hash と比べ(==)は版(revision)と実行環境の repo の commit を材料に入れず、
  ;; この鍵で置き換える — 読む code の変わらない commit で宣言し直されても、process を起こし直さず root も用意しない(2026-10-27 01:16〜01:50
  ;; JST に 5 commit で全部の service が 10 回作り直された)。None = 今どおり版と commit で比べる(鍵を渡さない送り手・古い送り手)。
  ;; 欄そのものは比べない(比べは指紋 fingerprint が持つ — 下の __eq__)。位置の引数で作る呼び手を崩さないよう最後に置く。
  (setv #^ (| str None) content (field :default None :compare False))

  (defn #^ None __post-init__ [self]
    (when (or (not self.name) (not self.entry) (not self.revision))
      (raise (ValueError "job には name・entry・revision が必要です")))
    ;; Program の cache の file は版ごと(launch.spec-program-name)— 版の無い Program の job は、どの版の file を取るかを決められない。
    (when (and (is-not self.program None) (is self.versions None))
      (raise (ValueError (.format "Program の job {} には送り手の版 versions が必要です" self.name)))))

  (defn [cached-property] #^ str fingerprint [self]  ; defk にできない: 値の属性(Program の外の純粋な判断 probe-of・statuses が読む)
    "指紋 spec-hash の計算の本体(公開の入口は doeff_cluster._shared.core.job_rules.spec-hash)。値ごとに最初に読まれた時に 1 度だけ
     計り、その値の中に覚える(覚えの寿命 = この値。module の大域には持たない)。dataclasses.replace で作り直した値は別の object なので
     計り直す — 欄が変われば指紋も変わる。材料の欄(name・entry・args・revision・once・runtime-env・environ・content)は str・bool と str の
     tuple・str の組の tuple だけで、値を作った後に中身が書き換わらないので、覚えた指紋は古くならない。
     中身の鍵 content が在れば、版 revision と実行環境の repo の commit を材料から外し、鍵を入れる(実行環境の
     ほかの欄〔lock の sha256・Python・import の根・native の wheel〕はそのまま材料)。
     入口の名は置き場を LEGACY-ENTRY-PLACE に替えた綴りで材料に入れる(送る名を _worker の置き場へ替えても
     process の形は変わらないので、置き場の改名で全部の service を作り直さない。前の綴りの指紋とも同じ値になる)。"
    (setv entry (.entry-at self LEGACY-ENTRY-PLACE))
    (setv materials
      (if (is self.content None)
          (+ [self.name entry (list self.args) self.revision self.once]
             (if self.runtime-env [self.runtime-env] []))
          (+ ["content" self.name entry (list self.args) self.once self.content]
             (if self.runtime-env [(env-without-commits self.runtime-env)] []))))
    (cut (.hexdigest (hashlib.sha256 (.encode (json.dumps (+ materials (if self.environ [(lfor #(k v) self.environ [k v])] []))
                                                          :ensure-ascii False :separators #("," ":"))
                                              "utf-8")))
         0 16))

  ;; 比べは指紋 1 つ。前は dataclass の作る比べ(比べる欄 = 指紋の材料の欄)で、指紋と同じ意味だった。中身の鍵の
  ;; 在る値は版が違っても同じ process の形なので、欄ごとの比べでは表せない — worker の「宣言が変わったか」(plan-job の (= want process.spec))と
  ;; 指紋を 1 つの定義にする。
  (defn #^ bool __eq__ [self #^ object other]  ; defk にできない: 値の比べの口
    (and (isinstance other JobSpec) (= self.fingerprint other.fingerprint)))

  (defn #^ int __hash__ [self]  ; defk にできない: 値の hash の口(比べと揃える)
    (hash self.fingerprint))

  (defn #^ str entry-at [self #^ (| str None) place]  ; defk にできない: 値の読み口(指紋 fingerprint と worker の起こし方が読む)
    "入口の名 entry(置き場の列 CHILD-ENTRY-PLACES のどれかの下の module)を、置き場 place の下の同じ module の名で返す(place = None・
     列の外の名は entry のまま)。worker が root へ送る名(子の起動・待ちの子から分ける task・入口の確かめ)と指紋の材料が、同じ読みで
     置き場を替える。"
    (for [known CHILD-ENTRY-PLACES]
      (when (and (is-not place None) (.startswith self.entry (+ known ".")))
        (return (+ place (cut self.entry (len known) None)))))
    self.entry))


(defn #^ str env-without-commits [#^ str runtime-env]
  "実行環境の宣言の JSON(正規化した文字列)から repo の commit を外した正規の文字列 — 中身の鍵の在る job の指紋の材料(commit の違いは
   鍵が運ぶ)。"
  (setv declared (json.loads runtime-env))
  (json.dumps (| declared {"repos" (lfor r (.get declared "repos" []) (dfor #(k v) (.items r) :if (!= k "commit") k v))})
              :sort-keys True :ensure-ascii False :separators #("," ":")))


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
        ELDER-RUNS "elder-runs"        ; コードと入口の検めは揃ったが、同じ名の前の世代がこの job を動かしている(印 elder-runs — 起こさない)
        MEMORY-KILLED "memory-killed"  ; 常駐の job が機体の memory の見張りに続けて止められた(起こし直さない — 宣言が変わるまで)
        FINISHED "finished"            ; task が終わった(起動し直さない)
        STOPPED "stopped"))
