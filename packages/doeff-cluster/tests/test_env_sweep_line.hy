;; 実行環境の root の掃除(env_store の env-host の SweepEnvs)が、掃除の頭で 1 行を出す形の検(#3713): 共有の disk の空き(free-bytes)・
;; roots の合計(roots-bytes)・その上限(cap-bytes — #3732)・固定の数(pinned)・消してよい root の数(candidates)・選んだ数(chosen)。
;; 何も選ばなかった回も 1 行出す。
;; 反例 = 行を出さない掃除(直す前の形)は、下の行の列の断言が赤。
;; 掃除の数えと消しはループの外の task で走る(#3715)ので、筋書きは掃除の終わりの行(SWEEP-DONE-LOG)が出るまで SweepEnvs を撃ち続ける。
;; 土台: file system と子 process は本物(検の tmp の dir の中だけ)— uv の代わりに何もしない `true` を渡す(prune は起きてすぐ終わる)。
(require doeff-hy.macros [deftest defk defhandler defeffect <- val var])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import dataclasses [dataclass])  ; defrecord の展開が名指す
(import json)
(import os)
(import pathlib [Path])
(import doeff [Program with-handlers])
(import doeff_core_effects.effects [SlogEffect])
(import doeff_core_effects.handlers [state])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_core_effects.os_file [os-file-handler])
(import doeff_core_effects.os_process [subprocess-handler])
(import doeff_time [Delay sync-time-handler])
(import doeff_cluster.shared.intent.env_marker_model [ENV-MARKER])
(import doeff_cluster.worker.intent.worker_model [SweepEnvs])
(import doeff_cluster.worker.protocol.code_store [PREPARE-TOOL])
(import doeff_cluster.worker.protocol.env_store [EnvSettings SWEEP-LOG SWEEP-DONE-LOG env-host])

;; 同じ project の完成した root 3 つ(いちばん古い方は消してよい・新しい 2 つは今の版と戻し先の版なので残す — #3732)。
(val OLD-NAME "0123456789abcdef01234567")
(val MID-NAME "456789abcdef0123456789ab")
(val NEW-NAME "89abcdef0123456789abcdef")
(val MARKER {"env" {"project" {"repo" "r" "path" "p"} "repos" [{"name" "r" "url" "https://example.invalid/r"}]}})
;; roots の合計の上限を 0 に置いて、必ず掃除させる。
(val CAP 0)
;; 掃除の終わりを待つ SweepEnvs の回数の上限と、回の間の秒(本物の時計 — 合わせて 10 秒)。
(val SWEEP-ROUNDS 1000)
(val SWEEP-PAUSE-SECONDS 0.01)


(defrecord SweepLine
  "掃除の頭の行 1 つの欄。"
  (#^ int free-bytes)
  (#^ int roots-bytes)
  (#^ int cap-bytes)
  (#^ int pinned)
  (#^ int candidates)
  (#^ int chosen))


(defrecord NotedSweeps
  "ここまでに出た掃除の行: lines = 掃除の選びの行(出た順)・done = 掃除の終わりの行の数。"
  (#^ (get tuple #(SweepLine ...)) lines)
  (#^ int done))


(defeffect NotedSweepLines
  "ここまでに出た掃除の行を読む — 検だけの問い(sweep-lines-noted が答える)。"
  {:fields [] :answer NotedSweeps :tags {:context "doeff-cluster-test" :role "intent"}})


(defhandler sweep-lines-noted
  "外の世界の log の代役: 掃除の選びの行(SWEEP-LOG)の欄を出た順に覚え、終わりの行(SWEEP-DONE-LOG)を数え、他の行は受け流す。覚えた物は
   NotedSweepLines で読む。"
  {:tags {:context "doeff-cluster-test" :role "foundation"}}
  (session var lines #())
  (session var done 0)
  (SlogEffect []
    (when (= effect.msg SWEEP-DONE-LOG)
      (:= done (+ done 1)))
    (when (= effect.msg SWEEP-LOG)
      (val fields effect.kwargs)
      (:= lines (+ lines #((SweepLine :free-bytes (get fields "free_bytes") :roots-bytes (get fields "roots_bytes")
                                      :cap-bytes (get fields "cap_bytes")
                                      :pinned (get fields "pinned") :candidates (get fields "candidates") :chosen (get fields "chosen"))))))
    (resume None))
  (NotedSweepLines []
    (resume (NotedSweeps :lines lines :done done))))


(defk three-roots [tmp]
  {:pre [(: tmp Path)] :post [(: % EnvSettings)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "root の置き場に同じ project の完成した root を 3 つ(完成マーカーの時刻を古い方から 2 時間前・1 時間前・今に — .last-used が無いので
   最後に使った時刻 = 完成の時刻)置き、必ず掃除させる設定を返すため。"
  (val roots (/ tmp "state" "roots"))
  (for [name #(OLD-NAME MID-NAME NEW-NAME)]
    (.mkdir (/ roots name) :parents True)
    (.write-text (/ roots name ENV-MARKER) (json.dumps MARKER)))
  (val now (. (.stat (/ roots NEW-NAME ENV-MARKER)) st-mtime))
  (for [#(name hours) #(#(OLD-NAME 2) #(MID-NAME 1))]
    (val at (- now (* 3600 hours)))
    (os.utime (/ roots name ENV-MARKER) #(at at))
    (os.utime (/ roots name) #(at at)))
  (EnvSettings :state (str (/ tmp "state")) :uv-cache (str (/ tmp "state" "uv-cache")) :hy-command "hy" :platform "test" :code-prepare PREPARE-TOOL :uv "true" :roots-cap-bytes CAP))


(defk sweep-once [pinned]
  {:pre [(: pinned frozenset)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "固定の集合 pinned で掃除を 1 回終わりまで進め(終わりの行が出るまで SweepEnvs を撃つ)、出た掃除の選びの行を返すため。"
  (for [_ (range SWEEP-ROUNDS)]
    (<- (SweepEnvs pinned))
    (<- seen NotedSweeps (NotedSweepLines))
    (when (> seen.done 0)
      (break))
    (<- (Delay SWEEP-PAUSE-SECONDS)))
  (<- noted NotedSweeps (NotedSweepLines))
  (assert (= noted.done 1) noted)
  noted.lines)


(defk on-envs [settings program]
  {:pre [(: settings EnvSettings) (: program Program)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "筋書きの Program を env-host と本物の答え手の下で回すため(掃除の頭の行は env-host の外側の sweep-lines-noted が受ける)。"
  (<- answer tuple (scheduled (with-handlers [(state) (sync-time-handler) sweep-lines-noted os-file-handler subprocess-handler
                                              (env-host settings)]
                                             program)))
  answer)


(deftest test-the-sweep-names-free-roots-cap-pinned-candidates-and-chosen-in-one-line [tmp-path]
  ;; 固定の無い掃除: 消してよいのはいちばん古い root 1 つ(新しい 2 つは今の版と戻し先の版)で、上限 0 に届かないので選べる物を全部選ぶ。
  (<- settings EnvSettings (three-roots tmp-path))
  (<- lines tuple (on-envs settings (sweep-once (frozenset))))
  (assert (= (len lines) 1) lines)
  (val line (get lines 0))
  (assert (= #(line.cap-bytes line.pinned line.candidates line.chosen) #(CAP 0 1 1)) line)
  (assert (> line.roots-bytes CAP) line)
  (assert (> line.free-bytes 0) line)
  (assert (not (.exists (/ tmp-path "state" "roots" OLD-NAME))) "選んだ root は消す"))


(deftest test-a-sweep-that-chooses-nothing-still-has-its-line [tmp-path]
  ;; 古い root を固定すると、消してよい root は無い — 何も選ばない回も 1 行出す(固定の数つき)。
  (<- settings EnvSettings (three-roots tmp-path))
  (<- lines tuple (on-envs settings (sweep-once (frozenset #((+ "env-" OLD-NAME))))))
  (assert (= (tuple (gfor line lines #(line.cap-bytes line.pinned line.candidates line.chosen))) #(#(CAP 1 0 0))) lines)
  (assert (.exists (/ tmp-path "state" "roots" OLD-NAME)) "固定の root は消さない"))
