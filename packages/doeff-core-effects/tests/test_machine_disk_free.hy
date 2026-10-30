;;; 空きの読み(ReadDiskFree・ReadDiskUsage — os_file.hy)の機体ごとの読み手の選びと床の検(card ki-4ee952807ce0)。
;;;
;;;   - 読み直し手の選び: macOS(Darwin)= darwin-volume-free(CoreFoundation の Opportunistic の空き)・Linux = None(statvfs の空きのまま)。
;;;   - 床: 読み直しの答えが無い時と statvfs の空きより小さい答えは statvfs の空きのまま — 読み直しで今より小さく測らない。
;;;   - 本物の CoreFoundation を呼ぶ検は macOS の上でだけ走る(Linux の検は選びと床を確かめる)。
(require doeff-hy.macros [defk deftest <- val])
(import platform)
(import shutil)
(import doeff [with_handlers])
(import doeff_core_effects [ReadDiskFree ReadDiskUsage DiskUsage])
(import doeff_core_effects [os-file-handler]
        doeff_core_effects._os_file [free-reader-for reread-free])
(import doeff_core_effects._darwin_volume [darwin-volume-free])


(defk answers-nine [probe]
  {:pre [(: probe str)] :post [(: % int)] :tags {:context "file-test" :role "program"}}
  "読み直し手の代わりに、statvfs の空き(床)より大きい空き 9 を答えるため(macOS が空ける領域を数えた答え)。"
  9)


(defk answers-two [probe]
  {:pre [(: probe str)] :post [(: % int)] :tags {:context "file-test" :role "program"}}
  "読み直し手の代わりに、床より小さい空き 2 を答えるため。"
  2)


(defk answers-nothing [probe]
  {:pre [(: probe str)] :post [(: % None)] :tags {:context "file-test" :role "program"}}
  "読み直し手の代わりに、答えが無い(key に答えない volume・CoreFoundation の断り)を返すため。"
  None)


(deftest test-the-free-reader-follows-the-machine
  ;; macOS は CoreFoundation の空きで読み直し、Linux と知らない機体は読み直さない(statvfs の空きのまま)。
  (<- mac (free-reader-for "Darwin"))
  (<- linux (free-reader-for "Linux"))
  (<- other (free-reader-for "Windows"))
  (assert (is mac darwin-volume-free) mac)
  (assert (is linux None) linux)
  (assert (is other None) other))


(deftest test-the-reread-keeps-the-statvfs-floor
  ;; 床 5(statvfs の空き): 読み直し手が無い = 5・大きい答え 9 = 9・小さい答え 2 = 5・答えが無い = 5。
  (<- unread int (reread-free "/v" 5 None))
  (<- larger int (reread-free "/v" 5 answers-nine))
  (<- smaller int (reread-free "/v" 5 answers-two))
  (<- missing int (reread-free "/v" 5 answers-nothing))
  (assert (= #(unread larger smaller missing) #(5 9 5 5)) #(unread larger smaller missing)))


(deftest test-linux-reads-the-statvfs-free-as-before [tmp-path]
  {:skip-if (!= (platform.system) "Linux") :skip-reason "statvfs のままの読みは Linux の機体の検"}
  ;; Linux の本物の答え: 無い path は在る親で測り、空きは statvfs の空きと同じ file system の値(総量は statvfs の総量そのもの)。
  (<- usage DiskUsage (with_handlers [os-file-handler] (ReadDiskUsage (str (tmp-path.joinpath "missing" "deeper")))))
  (val statvfs (shutil.disk-usage tmp-path))
  (assert (= usage.total statvfs.total) #(usage statvfs))
  (assert (<= 0 usage.free usage.total) usage))


(deftest test-the-mac-reads-the-opportunistic-free [tmp-path]
  {:skip-if (!= (platform.system) "Darwin") :skip-reason "CoreFoundation の空きの key は macOS の上でだけ読める"}
  ;; macOS の本物の答え: CoreFoundation が Opportunistic の空きを総量より小さい正の数で答え、ReadDiskFree・ReadDiskUsage の空きも総量の内。
  (val statvfs (shutil.disk-usage tmp-path))
  (<- answered (darwin-volume-free (str tmp-path)))
  (<- free int (with_handlers [os-file-handler] (ReadDiskFree (str tmp-path))))
  (<- usage DiskUsage (with_handlers [os-file-handler] (ReadDiskUsage (str tmp-path))))
  (assert (and (isinstance answered int) (< 0 answered statvfs.total)) #(answered statvfs))
  (assert (< 0 free statvfs.total) #(free statvfs))
  (assert (< 0 usage.free usage.total) usage))
