(require doeff-hy.macros [deftest defk <- with-handler])

;;; ResolveHost(名前引きだけ — 繋がない)の答え手 http-production-handler の検:
;;;   * 引けた名は、resolver の答えの番地を順のまま重ねずに HostResolved で答える
;;;   * 引けなかった名は値で答える(例外にしない): resolver の誤り(socket.gaierror)は NOT-RESOLVED・上限の秒の内に答えが無ければ TIMED-OUT
;;;   * 反例: 文に timed out を含む resolver の誤りは時間切れと読まない(kind は例外の class だけから決まる)
;;;   * 既定の resolver は走っている loop の getaddrinfo(HTTP の client の接続と同じ resolver)— localhost を引ける
;;;   * host の無い要求と上限の秒の無い要求は作る時に断る
;;; resolver は検ごとの代役(host → getaddrinfo の行の列を答える awaitable)で、本物の名前引きの外に出ない(localhost の 1 本を除く)。

(import asyncio)
(import socket)
(import doeff_core_effects [await-handler])
(import doeff_core_effects [HostNotResolved HostResolved ResolveFailureKind ResolveHost])
(import doeff_core_effects [http-production-handler])


(defn :async rows-of-two [host]
  "同じ番地を 2 度含む getaddrinfo の行(IPv4 2 つと IPv6 1 つ)を答える resolver の代役。"
  [#(socket.AF-INET socket.SOCK-STREAM 6 "" #("192.0.2.7" 0))
   #(socket.AF-INET socket.SOCK-STREAM 6 "" #("192.0.2.8" 0))
   #(socket.AF-INET socket.SOCK-STREAM 6 "" #("192.0.2.7" 0))
   #(socket.AF-INET6 socket.SOCK-STREAM 6 "" #("2001:db8::7" 0 0 0))])


(defn :async no-such-name [host]
  "resolver が名を知らないと答える代役(glibc の EAI_NONAME と同じ誤りの class)。"
  (raise (socket.gaierror socket.EAI-NONAME "Name or service not known")))


(defn :async failure-saying-timed-out [host]
  "文に timed out を含むが、resolver が答えた誤りの代役(時間切れの class ではない — 文を読むと取り違える反例)。"
  (raise (socket.gaierror socket.EAI-AGAIN "lookup timed out at the server")))


(defn :async never-answers [host]
  "答えを返さない resolver の代役(誰も満たさない future を待つ — 上限の秒で打ち切られるまで)。"
  (await (.create-future (asyncio.get-running-loop)))
  [])


(defk lookup-with [resolver host timeout-seconds]
  {:pre [(: resolver "host を引く resolver の代役(host → getaddrinfo の行の列の awaitable)") (: host str) (: timeout-seconds float)] :post [(: % (| HostResolved HostNotResolved))] :tags {:context "http" :role "foundation"}}
  "resolver の代役を渡した本番の答え手の下で host を 1 度引き、答えを読むため。"
  (<- answer (| HostResolved HostNotResolved)
      (with-handler [await-handler (http-production-handler :resolver resolver)]
        (ResolveHost :host host :timeout-seconds timeout-seconds)))
  answer)


(deftest test-a-resolved-name-answers-its-addresses-in-order-without-repeats
  (<- found (| HostResolved HostNotResolved) (lookup-with rows-of-two "notes.test" 5.0))
  (assert (= found (HostResolved :addresses #("192.0.2.7" "192.0.2.8" "2001:db8::7"))) found))


(deftest test-a-name-the-resolver-refuses-is-not-resolved
  (<- refused (| HostResolved HostNotResolved) (lookup-with no-such-name "notes.test" 5.0))
  (assert (= refused.kind ResolveFailureKind.NOT-RESOLVED) refused)
  (assert (.startswith refused.detail "gaierror: ") refused.detail))


(deftest test-the-kind-comes-from-the-error-class-not-its-text
  ;; 反例: resolver の誤りの文が timed out と言っても、class は resolver の誤り(gaierror)なので NOT-RESOLVED。
  (<- said (| HostResolved HostNotResolved) (lookup-with failure-saying-timed-out "notes.test" 5.0))
  (assert (= said.kind ResolveFailureKind.NOT-RESOLVED) said))


(deftest test-a-lookup-past-its-limit-is-timed-out
  (<- late (| HostResolved HostNotResolved) (lookup-with never-answers "notes.test" 0.05))
  (assert (= late.kind ResolveFailureKind.TIMED-OUT) late)
  (assert (in "notes.test" late.detail) late.detail))


(deftest test-the-default-resolver-is-the-loops-getaddrinfo
  ;; 既定の resolver(引数を渡さない答え手)は走っている loop の getaddrinfo — /etc/hosts の localhost を引ける。
  (<- local (| HostResolved HostNotResolved)
      (with-handler [await-handler (http-production-handler)]
        (ResolveHost :host "localhost" :timeout-seconds 5.0)))
  (assert (isinstance local HostResolved) local)
  (assert (& (set local.addresses) #{"127.0.0.1" "::1"}) local))


(deftest test-a-lookup-without-a-host-or-a-limit-is-refused-when-made
  (for [#(host seconds) [#("" 5.0) #("  " 5.0) #("notes.test" 0.0) #("notes.test" -1.0)]]
    (try
      (ResolveHost :host host :timeout-seconds seconds)
      (assert False #(host seconds))
      (except [ValueError]))))
