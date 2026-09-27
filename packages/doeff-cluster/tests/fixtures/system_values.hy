;;; declare の CLI の反例の見本: 系の関数ではなく、System の値そのものを module の大域に置いた形(旧い宣言の形 — declare が断る)。
(require doeff-hy.macros [val])
(import tests.fixtures.services [lab])
(import tests.fixtures.envs [plain-foundation])

(val LAB-VALUE (lab plain-foundation))
