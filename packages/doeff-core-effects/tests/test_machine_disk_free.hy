;;; Tests for the per-machine choice of reader and the floor for free-space reads (ReadDiskFree, ReadDiskUsage -- os_file.hy).
;;;
;;;   - Choice of re-reader: macOS (Darwin) = darwin-volume-free (CoreFoundation's Opportunistic free space), Linux = None (statvfs free space as is).
;;;   - Floor: when the re-read has no answer, or its answer is smaller than the statvfs free space, the statvfs free space stays -- a re-read never measures smaller than now.
;;;   - Tests that call the real CoreFoundation run only on macOS (the Linux tests check the choice and the floor).
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
  "To answer, in place of the re-reader, a free space 9 larger than the statvfs free space (the floor) (the answer that counts the space macOS can free)."
  9)


(defk answers-two [probe]
  {:pre [(: probe str)] :post [(: % int)] :tags {:context "file-test" :role "program"}}
  "To answer, in place of the re-reader, a free space 2 smaller than the floor."
  2)


(defk answers-nothing [probe]
  {:pre [(: probe str)] :post [(: % None)] :tags {:context "file-test" :role "program"}}
  "To return, in place of the re-reader, no answer (a volume that does not answer the key, or a CoreFoundation refusal)."
  None)


(deftest test-the-free-reader-follows-the-machine
  ;; macOS re-reads with CoreFoundation's free space; Linux and unknown machines do not re-read (statvfs free space as is).
  (<- mac (free-reader-for "Darwin"))
  (<- linux (free-reader-for "Linux"))
  (<- other (free-reader-for "Windows"))
  (assert (is mac darwin-volume-free) mac)
  (assert (is linux None) linux)
  (assert (is other None) other))


(deftest test-the-reread-keeps-the-statvfs-floor
  ;; Floor 5 (statvfs free space): no re-reader = 5, large answer 9 = 9, small answer 2 = 5, no answer = 5.
  (<- unread int (reread-free "/v" 5 None))
  (<- larger int (reread-free "/v" 5 answers-nine))
  (<- smaller int (reread-free "/v" 5 answers-two))
  (<- missing int (reread-free "/v" 5 answers-nothing))
  (assert (= #(unread larger smaller missing) #(5 9 5 5)) #(unread larger smaller missing)))


(deftest test-linux-reads-the-statvfs-free-as-before [tmp-path]
  {:skip-if (!= (platform.system) "Linux") :skip-reason "the statvfs-as-is read is a test for Linux machines"}
  ;; Linux real answer: a nonexistent path is measured at its existing parent, and the free space is the same file-system value as the statvfs free space (the total is the statvfs total itself).
  (<- usage DiskUsage (with_handlers [os-file-handler] (ReadDiskUsage (str (tmp-path.joinpath "missing" "deeper")))))
  (val statvfs (shutil.disk-usage tmp-path))
  (assert (= usage.total statvfs.total) #(usage statvfs))
  (assert (<= 0 usage.free usage.total) usage))


(deftest test-the-mac-reads-the-opportunistic-free [tmp-path]
  {:skip-if (!= (platform.system) "Darwin") :skip-reason "the CoreFoundation free-space key can be read only on macOS"}
  ;; macOS real answer: CoreFoundation answers the Opportunistic free space as a positive number smaller than the total, and the free space of ReadDiskFree and ReadDiskUsage is also within the total.
  (val statvfs (shutil.disk-usage tmp-path))
  (<- answered (darwin-volume-free (str tmp-path)))
  (<- free int (with_handlers [os-file-handler] (ReadDiskFree (str tmp-path))))
  (<- usage DiskUsage (with_handlers [os-file-handler] (ReadDiskUsage (str tmp-path))))
  (assert (and (isinstance answered int) (< 0 answered statvfs.total)) #(answered statvfs))
  (assert (< 0 free statvfs.total) #(free statvfs))
  (assert (< 0 usage.free usage.total) usage))
