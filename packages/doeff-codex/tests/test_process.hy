;; app-server の子 process の器の検 — 替え玉の CLI(stdin の 1 行をそのまま stdout へ返す python)で、器が行を運び、ターンをまたいで
;; 生き、stdin の EOF で降り、終わりの code と stderr の末尾を 1 度だけ知らせる事を確かめる。
(require doeff-hy.macros [val])
(val MODULE-TAGS {:context "codex-test" :role "foundation"})
(require doeff-hy.macros [deftest])
(import queue)
(import sys)
(import doeff_codex.process [CodexProcess])

;; 替え玉の CLI: stdin の行を 1 行ずつ stdout へ返し、stdin の EOF で code 0 で終わる。
(val ECHO "import sys\nfor line in sys.stdin:\n    sys.stdout.write(line)\n    sys.stdout.flush()\n")
;; 替え玉の CLI: stderr に 2 行書いて code 3 で終わる(stdout には何も出さない)。
(val FAIL "import sys\nsys.stderr.write('first\\n')\nsys.stderr.write('boom\\n')\nsys.exit(3)\n")
(val WAIT-SECONDS 15)


(deftest test-the-process-stays-up-between-exchanges-and-ends-on-eof
  (val got (queue.Queue))
  (val ended (queue.Queue))
  (val process (CodexProcess [sys.executable "-u" "-c" ECHO] "/" {"PATH" "/usr/bin:/bin"}
                             (fn [raw] (.put got raw))
                             (fn [code tail] (.put ended #(code tail)))
                             (fn [] None)))
  ;; 1 つ目のやりとり(ターン 1 つ分)の後も process は生きていて、2 つ目も同じ process が運ぶ。
  (.send process "{\"id\": 1}")
  (assert (= (.strip (.get got :timeout WAIT-SECONDS)) "{\"id\": 1}"))
  (assert (.alive process))
  (val pid process.pid)
  (.send process "{\"id\": 2}")
  (assert (= (.strip (.get got :timeout WAIT-SECONDS)) "{\"id\": 2}"))
  (assert (= process.pid pid))
  ;; 降ろす: stdin に EOF → 子が code 0 で終わり、終わりの知らせは 1 度だけ。
  (.retire process)
  (assert (= (.get ended :timeout WAIT-SECONDS) #(0 "")))
  (assert (.empty ended))
  (assert (not (.alive process))))


(deftest test-a-failing-process-reports-its-code-and-stderr-tail
  (val ended (queue.Queue))
  (val process (CodexProcess [sys.executable "-u" "-c" FAIL] "/" {"PATH" "/usr/bin:/bin"}
                             (fn [raw] None)
                             (fn [code tail] (.put ended #(code tail)))
                             (fn [] None)))
  (assert (= (.get ended :timeout WAIT-SECONDS) #(3 "first\nboom")))
  (assert (= (.exit-code process) 3)))
