;;; Specimen (a file with deliberate violations): reads the worker's silence anchors (the last-ok pair, the start-of-send pair) outside the silence measure silent-ms, or writes only one of them.
;;; The rule doeff-cluster-worker-silence-anchors-are-read-by-the-one-measure fires on lines 9, 13, 17, 20, 23, 26, 29, 32, 35, 38, 41 and does not fire on lines 44-55.
;;;
(import doeff_cluster._worker.core.heartbeat_rules [silent-ms])
(import doeff_cluster._worker.core.worker_due [fence-due])


(defn wall-only-silence [state now-ms]
  (- now-ms state.last-ok-ms))

(defn carried-out-under-another-name [state now-ms]
  ;; Carrying it out under another name hides the subtraction after that from the rule.
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

(defn anchor-as-the-now-reading [state now-ms]
  (silent-ms now-ms state.last-ok-mono-ms state.last-ok-ms state.last-ok-mono-ms))

(defn pair-shifted-forward-from-itself [state grace]
  (setv state.last-ok-ms (+ state.last-ok-ms grace) state.last-ok-mono-ms (+ state.last-ok-mono-ms grace)))


(defn measured-in-the-one-place [state beating now-ms now-mono-ms]
  ;; Does not fire: pass the anchor pair as the 3rd and 4th arguments of silent-ms (a monotonic clock reading may be an expression).
  #((silent-ms now-ms now-mono-ms state.last-ok-ms state.last-ok-mono-ms)
    (silent-ms now-ms (monotonic-ms-of now-mono-ms) beating.started-at beating.started-mono-ms)))

(defn written-as-a-pair [state beating truth now-ms mono]
  ;; Does not fire: write the pair as two adjacent groups of one setv, build a record with a key pair, a question about existence.
  (setv state.last-ok-ms now-ms state.last-ok-mono-ms (monotonic-ms-of mono))
  (setv beating.started-at now-ms
        beating.started-mono-ms (monotonic-ms-of mono))
  (when (is-not beating.started-at None)
    (replace truth :last-ok-ms now-ms :last-ok-mono-ms (monotonic-ms-of mono))))
