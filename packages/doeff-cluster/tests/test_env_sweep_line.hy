;; 実行環境の root の掃除(env_store の env-host の SweepEnvs)が、掃除の頭で 1 行を出す形の検(#3713): 空き(free-bytes)・下限(floor-bytes)・
;; 固定の数(pinned)・消してよい root の数(candidates)・選んだ数(chosen)。何も選ばなかった回も 1 行出す。
;; 反例 = 行を出さない掃除(直す前の形)は、下の行の列の断言が赤。
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
(import doeff_core_effects.os_file [os-file-handler])
(import doeff_core_effects.os_process [subprocess-handler])
(import doeff_time [sync-time-handler])
(import doeff_cluster.shared.intent.env_marker_model [ENV-MARKER])
(import doeff_cluster.worker.intent.worker_model [SweepEnvs])
(import doeff_cluster.worker.protocol.code_store [PREPARE-TOOL])
(import doeff_cluster.worker.protocol.env_store [EnvSettings SWEEP-LOG env-host])

;; 同じ project の完成した root 2 つ(古い方は消してよい・新しい方は project の最新なので残す)。
(val OLD-NAME "0123456789abcdef01234567")
(val NEW-NAME "89abcdef0123456789abcdef")
(val MARKER {"env" {"project" {"repo" "r" "path" "p"} "repos" [{"name" "r" "url" "https://example.invalid/r"}]}})
;; 下限を disk の大きさより上に置いて、必ず掃除させる。
(val FLOOR (** 10 18))


(defrecord SweepLine
  "掃除の頭の行 1 つの欄。"
  (#^ int free-bytes)
  (#^ int floor-bytes)
  (#^ int pinned)
  (#^ int candidates)
  (#^ int chosen))


(defeffect NotedSweepLines
  "ここまでに出た掃除の頭の行を読む — 検だけの問い(sweep-lines-noted が答える)。"
  {:fields [] :answer tuple :tags {:context "doeff-cluster-test" :role "intent"}})


(defhandler sweep-lines-noted
  "外の世界の log の代役: 掃除の頭の行(SWEEP-LOG)の欄を出た順に覚え、他の行は受け流す。覚えた列は NotedSweepLines で読む。"
  {:tags {:context "doeff-cluster-test" :role "foundation"}}
  (session var lines #())
  (SlogEffect []
    (when (= effect.msg SWEEP-LOG)
      (val fields effect.kwargs)
      (:= lines (+ lines #((SweepLine :free-bytes (get fields "free_bytes") :floor-bytes (get fields "floor_bytes")
                                      :pinned (get fields "pinned") :candidates (get fields "candidates") :chosen (get fields "chosen"))))))
    (resume None))
  (NotedSweepLines []
    (resume lines)))


(defk two-roots [tmp]
  {:pre [(: tmp Path)] :post [(: % EnvSettings)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "root の置き場に同じ project の完成した root を 2 つ(古い方の完成マーカーを 1 時間前に)置き、必ず掃除させる設定を返すため。"
  (val roots (/ tmp "state" "roots"))
  (for [name #(OLD-NAME NEW-NAME)]
    (.mkdir (/ roots name) :parents True)
    (.write-text (/ roots name ENV-MARKER) (json.dumps MARKER)))
  (val old-marker (/ roots OLD-NAME ENV-MARKER))
  (val hour-ago (- (. (.stat old-marker) st-mtime) 3600))
  (os.utime old-marker #(hour-ago hour-ago))
  (os.utime (/ roots OLD-NAME) #(hour-ago hour-ago))
  (EnvSettings :state (str (/ tmp "state")) :hy-command "hy" :platform "test" :code-prepare PREPARE-TOOL :uv "true" :sweep-floor-bytes FLOOR))


(defk sweep-once [pinned]
  {:pre [(: pinned frozenset)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "固定の集合 pinned で掃除を 1 回させ、出た掃除の頭の行を返すため。"
  (<- (SweepEnvs pinned))
  (<- lines tuple (NotedSweepLines))
  lines)


(defk on-envs [settings program]
  {:pre [(: settings EnvSettings) (: program Program)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "筋書きの Program を env-host と本物の答え手の下で回すため(掃除の頭の行は env-host の外側の sweep-lines-noted が受ける)。"
  (<- answer tuple (with-handlers [(state) (sync-time-handler) sweep-lines-noted os-file-handler subprocess-handler (env-host settings)]
                                  program))
  answer)


(deftest test-the-sweep-names-free-floor-pinned-candidates-and-chosen-in-one-line [tmp-path]
  ;; 固定の無い掃除: 消してよいのは古い root 1 つ(新しい方は project の最新)で、下限に届かないので選べる物を全部選ぶ。
  (<- settings EnvSettings (two-roots tmp-path))
  (<- lines tuple (on-envs settings (sweep-once (frozenset))))
  (assert (= (len lines) 1) lines)
  (val line (get lines 0))
  (assert (= #(line.floor-bytes line.pinned line.candidates line.chosen) #(FLOOR 0 1 1)) line)
  (assert (< 0 line.free-bytes FLOOR) line)
  (assert (not (.exists (/ tmp-path "state" "roots" OLD-NAME))) "選んだ root は消す"))


(deftest test-a-sweep-that-chooses-nothing-still-has-its-line [tmp-path]
  ;; 古い root を固定すると、消してよい root は無い — 何も選ばない回も 1 行出す(固定の数つき)。
  (<- settings EnvSettings (two-roots tmp-path))
  (<- lines tuple (on-envs settings (sweep-once (frozenset #((+ "env-" OLD-NAME))))))
  (assert (= (tuple (gfor line lines #(line.floor-bytes line.pinned line.candidates line.chosen))) #(#(FLOOR 1 0 0))) lines)
  (assert (.exists (/ tmp-path "state" "roots" OLD-NAME)) "固定の root は消さない"))
