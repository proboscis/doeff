;;; 検体(違反をわざと書いた file): worker の沈黙の起点(last-ok の対・送りの始めの対)を、沈黙の計り silent-ms の外で読む・片方だけ書く。
;;; doeff-cluster-worker-silence-anchors-are-read-by-the-one-measure が 9・13・17・20・23・26・29・32・35 行目で発火し、38〜49 行目では
;;; 発火しない。
(import doeff_cluster.worker.core.heartbeat_rules [silent-ms])
(import doeff_cluster.worker.core.worker_due [fence-due])


(defn wall-only-silence [state now-ms]
  (- now-ms state.last-ok-ms))

(defn carried-out-under-another-name [state now-ms]
  ;; 名に写して持ち出すと、その先の引き算は規則から見えない。
  (setv last state.last-ok-ms)
  (- now-ms last))

(defn raw-anchor-into-the-due [state now]
  (fence-due now state.last-ok-ms state.fence-ms state.keep-fence-ms))

(defn swapped-anchors [state now-ms now-mono-ms]
  (silent-ms now-ms now-mono-ms state.last-ok-mono-ms state.last-ok-ms))

(defn anchors-of-two-owners [state beating now-ms now-mono-ms]
  (silent-ms now-ms now-mono-ms state.last-ok-ms beating.started-mono-ms))

(defn wall-half-written-alone [state now-ms]
  (setv state.last-ok-ms now-ms))

(defn record-half-written-alone [truth now]
  (replace truth :last-ok-ms now))

(defn beat-gap-by-the-wall [beating now-ms]
  (- now-ms beating.started-at))

(defn attribute-form [state now-ms]
  (- now-ms (. state last-ok-ms)))


(defn measured-in-the-one-place [state beating now-ms now-mono-ms]
  ;; 発火しない: 起点の対を silent-ms の 3・4 番目に渡す(単調な時計の読みは式でもよい)。
  #((silent-ms now-ms now-mono-ms state.last-ok-ms state.last-ok-mono-ms)
    (silent-ms now-ms (monotonic-ms-of now-mono-ms) beating.started-at beating.started-mono-ms)))

(defn written-as-a-pair [state beating truth now-ms mono]
  ;; 発火しない: 対を 1 つの setv の隣り合う 2 組で書く・record は鍵の対で作る・在るかどうかの問い。
  (setv state.last-ok-ms now-ms state.last-ok-mono-ms (monotonic-ms-of mono))
  (setv beating.started-at now-ms
        beating.started-mono-ms (monotonic-ms-of mono))
  (when (is-not beating.started-at None)
    (replace truth :last-ok-ms now-ms :last-ok-mono-ms (monotonic-ms-of mono))))
