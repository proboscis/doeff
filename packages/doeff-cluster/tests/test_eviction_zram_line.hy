;; 機体の線の使いに zram の device の memory を足す — 模擬の検。
;;
;; 実例: node-h の RAM disk の zram(mem_limit なし)4〜5 個が、どの cgroup にも数えられない memory を 15〜22 GiB 持っていた。kubelet の式の
;; 機体の線(capacity − E − root の working set)はそれを数えず、worker は線の空き 24〜34 GiB を名乗り、MemAvailable は 2.4〜8.5 GiB で、
;; 置き先が task を置き続けて機体ごと止まった(11-16)。
;;   - 判断(worker/core/eviction_rules の zram-used-of): mm_stat の 3 番目の欄 mem_used_total。
;;   - 読み(worker/protocol/eviction_line の cgroup-line-read): /sys/block の zram<N> の和を root の working set に足す。zram でない device は
;;     読まない。block の dir を並べられない container は前の式のまま。並べた device の mm_stat が読めない・形が違う回は線を読めない訳。
(require doeff-hy.macros [deftest defk <- val])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import pytest)
(import doeff [with-handlers])
(import doeff_core_effects [state])
(import doeff_core_effects [MemoryFile MemoryFiles])
(import doeff_core_effects [memory-file-handler])
(import doeff_cluster._worker.core.memory_rules [MIB])
(import doeff_cluster._worker.core.eviction_rules [EvictionLine KubeletMemory LineKind zram-used-of])
(import doeff_cluster._worker.protocol.eviction_line [EvictionSettings cgroup-line-read])

(val GIB (* 1024 MIB))
;; node-h の Node の宣言(11-17 の Node の status): capacity 64195272Ki・allocatable 61049544Ki。E はこの検では 1 GiB と置く
;; (kubepods の memory.max = allocatable + 1 GiB)。
(val CAPACITY (* 64195272 1024))
(val ALLOCATABLE (* 61049544 1024))
(val HARD GIB)
(val NODE-H (KubeletMemory :capacity-bytes CAPACITY :allocatable-bytes ALLOCATABLE))
(val HOST "/host/cgroup")
(val PODS (+ HOST "/kubepods.slice"))
(val BLOCK "/sys/block")
;; node-h の 11-17 01:0x UTC の mm_stat(zram2 cargo の中間物・zram3 jobs・zram4 一時の dir・zram5 前の作業場・zram6 作業場 — 3 番目の欄の
;; 和 17,044,467,712 byte = 15.87 GiB)と、使われていない zram0。
(val MM-STATS {"zram0" "       0        0        0        0        0        0        0        0        0\n"
               "zram2" "479092736 97784213 101179392        0 12919447552      742 24280978      591  6162274\n"
               "zram3" "17791131648 3836423919 3928469504        0 8863956992     1061  5764662    53008 10065830\n"
               "zram4" "1056075776 89012990 93818880        0 1003675648     7265   584847       45  6768794\n"
               "zram5" "22258364416 4493912220 4611010560        0 19091591168    39526  4119953        0   858031\n"
               "zram6" "17621995520 8129462935 8309989376        0 11484680192    31922   306337    92819  1295862\n"})
(val ZRAM-USED (+ 101179392 3928469504 93818880 4611010560 8309989376))
;; Pod の working set 20 GiB・root の working set 32 GiB(anon 30 + file 4 − inactive_file 2)。
(val PODS-USED (* 20 GIB))
(val ROOT-SET (* 32 GIB))


(defk node-h-files [mm-stats]
  {:pre [(: mm-stats dict)] :post [(: % MemoryFiles)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "模擬の node-h の kubepods と root の cgroup の file と、block の device の dir(mm-stats = device の名 → mm_stat の中身。zram でない loop0 も
   並べる)を組むため。"
  (val names (+ (tuple (sorted mm-stats)) #("loop0")))
  (MemoryFiles :dirs (+ #("/host" HOST PODS "/sys" BLOCK) (tuple (gfor name names (+ BLOCK "/" name))))
               :files (+ #((MemoryFile :path (+ PODS "/memory.max") :content (.encode (str (+ ALLOCATABLE HARD))))
                           (MemoryFile :path (+ PODS "/memory.current") :content (.encode (str PODS-USED)))
                           (MemoryFile :path (+ PODS "/memory.stat") :content b"anon 1\ninactive_file 0\n")
                           (MemoryFile :path (+ HOST "/memory.stat")
                                       :content (.encode (.format "anon {}\nfile {}\ninactive_file {}\n" (* 30 GIB) (* 4 GIB) (* 2 GIB))))
                           (MemoryFile :path (+ BLOCK "/loop0/size") :content b"0\n"))
                         (tuple (gfor [name text] (.items mm-stats) (MemoryFile :path (+ BLOCK "/" name "/mm_stat") :content (.encode text)))))))


(defk line-read-on [files]
  {:pre [(: files MemoryFiles)] :post [(: % (| EvictionLine None str))] :tags {:context "doeff-cluster-test" :role "program"}}
  "本物の線の読みを模擬の node-h の file の上で 1 回回すため。"
  (val settings (EvictionSettings :api "https://api.test" :token-file "/sa/token" :node "node-h" :pods-dir PODS :host-root HOST
                                  :jobs-dir None :jobs-cap-bytes None :block-dir BLOCK))
  (<- line (| EvictionLine None str) (with-handlers [(state) (memory-file-handler files)] (cgroup-line-read settings NODE-H)))
  line)


(deftest test-the-zram-used-is-the-third-field-of-mm-stat
  (<- used int (zram-used-of (get MM-STATS "zram6")))
  (assert (= used 8309989376))
  (with [(pytest.raises ValueError)] (<- (zram-used-of "17621995520 8129462935\n")))
  (with [(pytest.raises ValueError)] (<- (zram-used-of "1 2 x 0\n"))))


(deftest test-the-node-line-counts-the-zram-devices-the-root-cgroup-does-not
  ;; 前の式: 機体の線の空き = capacity − E − root の working set = 28.22 GiB で、allocatable の線(58.22 − 20 = 38.22 GiB)より近い。
  ;; zram の 15.87 GiB を足すと 12.35 GiB — 同じ刻の MemAvailable(13.24 GiB)との差は 1 GiB の内。
  (<- files MemoryFiles (node-h-files MM-STATS))
  (<- line (| EvictionLine None str) (line-read-on files))
  (assert (= #(line.kind line.size-bytes line.used-bytes) #(LineKind.NODE (- CAPACITY HARD) (+ ROOT-SET ZRAM-USED))) line)
  (assert (= line.room-bytes (- CAPACITY HARD ROOT-SET ZRAM-USED)) line)
  ;; zram の無い機体は前の式のまま。
  (<- bare MemoryFiles (node-h-files {}))
  (<- before (| EvictionLine None str) (line-read-on bare))
  (assert (= #(before.kind before.room-bytes) #(LineKind.NODE (- CAPACITY HARD ROOT-SET))) before))


(deftest test-a-zram-device-whose-mm-stat-is-unreadable-leaves-the-line-unread
  (<- files MemoryFiles (node-h-files {"zram2" "479092736 97784213\n"}))
  (<- line (| EvictionLine None str) (line-read-on files))
  (assert (isinstance line str) line)
  (assert (in "mem_used_total" line) line))


(deftest test-a-container-without-the-block-dir-reads-the-line-as-before
  (<- files MemoryFiles (node-h-files MM-STATS))
  (val hidden (MemoryFiles :dirs (tuple (gfor d files.dirs :if (not (.startswith d BLOCK)) d))
                           :files (tuple (gfor f files.files :if (not (.startswith f.path BLOCK)) f))))
  (<- line (| EvictionLine None str) (line-read-on hidden))
  (assert (= line.room-bytes (- CAPACITY HARD ROOT-SET)) line))
