;; app-server の子 process の器の検 — 替え玉の CLI(stdin の 1 行をそのまま stdout へ返す python)で、器が行を運び、ターンをまたいで
;; 生き、stdin の EOF で降り、終わりの code と stderr の末尾を 1 度だけ知らせる事を確かめる。
(require doeff-hy.macros [val])
(val MODULE-TAGS {:context "codex-test" :role "foundation"})
(require doeff-hy.macros [deftest])
(import queue)
(import sys)
(import doeff_codex._process [CodexProcess CutLine CUT-HEAD-BYTES LINE-CAP-BYTES])

;; 替え玉の CLI: stdin の行を 1 行ずつ stdout へ返し、stdin の EOF で code 0 で終わる。
(val ECHO "import sys\nfor line in sys.stdin:\n    sys.stdout.write(line)\n    sys.stdout.flush()\n")
;; 検の 1 行の上限(bytes — 改行を含む)と、替え玉の CLI: 上限を超える行(A が LONG-CHARS 字)・上限ちょうどの行(B — 改行を足して
;; 上限の bytes)・短い行を順に書いて code 0 で終わる。
(val SMALL-CAP (* 1024 1024))
(val LONG-CHARS 3000000)
(val LINES (+ "import sys\n"
              (.format "sys.stdout.write('A' * {} + '\\n')\n" LONG-CHARS)
              (.format "sys.stdout.write('B' * {} + '\\n')\n" (- SMALL-CAP 1))
              "sys.stdout.write('{\"id\": 8}\\n')\nsys.stdout.flush()\n"))
;; 替え玉の CLI: stderr に 2 行書いて code 3 で終わる(stdout には何も出さない)。
(val FAIL "import sys\nsys.stderr.write('first\\n')\nsys.stderr.write('boom\\n')\nsys.exit(3)\n")
;; 子の python の env(PYTHONDONTWRITEBYTECODE は子へ明示する — 渡す env の写像は呼び手の環境変数を継がないので、作り立ての venv では子が
;; site の起動で読む module の .pyc を checkout へ書いた・日次 2026-10-11 の doeff の後片付けの検)。
(val CHILD-ENV {"PATH" "/usr/bin:/bin" "PYTHONDONTWRITEBYTECODE" "1"})
(val WAIT-SECONDS 15)


(deftest test-the-process-stays-up-between-exchanges-and-ends-on-eof
  (val got (queue.Queue))
  (val ended (queue.Queue))
  (val process (CodexProcess [sys.executable "-u" "-c" ECHO] "/" CHILD-ENV
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
  (val process (CodexProcess [sys.executable "-u" "-c" FAIL] "/" CHILD-ENV
                             (fn [raw] None)
                             (fn [code tail] (.put ended #(code tail)))
                             (fn [] None)))
  (assert (= (.get ended :timeout WAIT-SECONDS) #(3 "first\nboom")))
  (assert (= (.exit-code process) 3)))


(deftest test-a-line-over-the-cap-is-passed-as-its-head-and-size-and-the-rest-is-dropped
  ;; 上限を超える行は、頭(CUT-HEAD-BYTES)と行の大きさの CutLine で渡り、残りは読み捨てる。上限ちょうどの行と、その後の行は丸ごと
  ;; 渡る(捨てた行の残りが次の行に混ざらない)。捨てた行の数を process が数える。
  (val got (queue.Queue))
  (val ended (queue.Queue))
  (val process (CodexProcess [sys.executable "-u" "-c" LINES] "/" CHILD-ENV
                             (fn [raw] (.put got raw))
                             (fn [code tail] (.put ended #(code tail)))
                             (fn [] None)
                             :line-cap SMALL-CAP))
  (val cut (.get got :timeout WAIT-SECONDS))
  (assert (isinstance cut CutLine) (type cut))
  (assert (= cut.size (+ LONG-CHARS 1)))
  (assert (= cut.head (* "A" CUT-HEAD-BYTES)))
  (assert (= (.get got :timeout WAIT-SECONDS) (+ (* "B" (- SMALL-CAP 1)) "\n")))
  (assert (= (.get got :timeout WAIT-SECONDS) "{\"id\": 8}\n"))
  (assert (= (.get ended :timeout WAIT-SECONDS) #(0 "")))
  (assert (.empty got))
  (assert (= process.cut-lines 1)))


(deftest test-the-line-cap-starts-at-64-mib
  ;; 上限の初めの値(card ki-83f24000d86c: thread/resume の答え 1 行が数百 MB になる — 上限までの行は丸ごと読む)。
  (assert (= LINE-CAP-BYTES (* 64 1024 1024))))
