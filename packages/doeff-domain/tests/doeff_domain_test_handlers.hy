(require doeff-hy.handle [defhandler])

(import doeff_vm [EffectBase])


(defclass AlphaEffect [EffectBase])
(defclass BetaEffect [EffectBase])
(defclass GammaEffect [EffectBase])
(defclass FallbackEffect [EffectBase])


(defhandler direct-handler
  (lazy token "ready")
  (AlphaEffect []
    (resume token))
  (BetaEffect []
    :when True
    (resume None)))


(defhandler configured-handler [prefix]
  (GammaEffect []
    (resume prefix)))


(defhandler fallback-handler
  (FallbackEffect []
    (resume None)))
