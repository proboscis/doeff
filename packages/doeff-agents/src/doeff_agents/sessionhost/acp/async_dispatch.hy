;;; 同期I/O handlerをAwaitへ翻訳する汎用境界。業務Programと再開は同じVMで動く。
(require doeff-hy.macros [defhandler <-])
(import asyncio)
(import collections.abc [Callable])
(import doeff_vm [EffectBase Resume Pass])
(import doeff_core_effects.effects [Await])

(defhandler async-dispatch [#^ Callable dispatcher #^ str owned-module-prefix]
  (EffectBase []
    :when (.startswith (. (type effect) __module__) owned-module-prefix)
    ;; pool threadで作るResume/Passは値のみ。continuationを実行するのはVM threadだけ。
    (<- result (| Resume Pass) (Await (asyncio.to-thread dispatcher effect k)))
    (if (isinstance result Resume)
      (resume result.value)
      (do
        (assert (isinstance result Pass))
        (reperform effect)))))
