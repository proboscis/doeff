;;; macOS(Darwin)の volume の空きの読み — CoreFoundation の CFURLCopyResourcePropertyForKey で、volume の資源の key
;;; kCFURLVolumeAvailableCapacityForOpportunisticUsageKey(先回りの・無くてもよい物の置き場の空き)を読む(card ki-4ee952807ce0)。
;;;
;;; statvfs の空き(shutil.disk_usage)は、macOS が要る時に自動で空ける領域(APFS の local snapshot が持つ消した中身・消してよい cache)
;;; を使えない空きとして引く。Apple の文書の使い分けは、利用者が頼んだ物・動くのに要る物の置き場 = ImportantUsage、先回りの・無くてもよい
;;; 物の置き場 = Opportunistic。空きを読む手(doeff-cluster の worker の env の準備の断り・heartbeat の作業 disk の状態・掃除の判じ)は
;;; 消しても組み直せる物の置き場なので Opportunistic を読む — 2 つのうち小さい方で、macOS が空ける領域を全部は数えない(10-17 17:48 の
;;; Mac: statvfs 45.2 GB・Opportunistic 75.5 GB・ImportantUsage 92.7 GB)。Time Machine の保存先に届かない間は local snapshot が手元の
;;; 唯一の backup なので、多く数える ImportantUsage は使わない。
;;;
;;;   darwin-volume-free  在る path の volume の Opportunistic の空き(byte)。volume が key に答えない(値が無い)・CoreFoundation が断った
;;;                       = None。CoreFoundation を直に呼ぶのはこの module だけで、macOS の上でだけ呼ばれる(機体の選びと床は os_file.hy)。
(require doeff-hy.macros [defk <- val])
(val MODULE-TAGS {:context "file" :role "foundation"})
(import ctypes)
(import os)

(val CORE-FOUNDATION-PATH "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
;; 読む資源の key(CFURL.h の CFStringRef の定数の名 — 頭の註)。
(val VOLUME-FREE-KEY "kCFURLVolumeAvailableCapacityForOpportunisticUsageKey")
;; CFNumberGetValue の型の印 kCFNumberSInt64Type(CFNumber.h)。
(val CF-NUMBER-SINT64 4)
;; 呼ぶ 4 つの関数の型(CFURL.h・CFNumber.h・CFBase.h)。Boolean は unsigned char。
(val URL-OF-PATH (ctypes.CFUNCTYPE ctypes.c-void-p ctypes.c-void-p ctypes.c-char-p ctypes.c-long ctypes.c-ubyte))
(val COPY-PROPERTY (ctypes.CFUNCTYPE ctypes.c-ubyte ctypes.c-void-p ctypes.c-void-p (ctypes.POINTER ctypes.c-void-p) ctypes.c-void-p))
(val NUMBER-VALUE (ctypes.CFUNCTYPE ctypes.c-ubyte ctypes.c-void-p ctypes.c-int (ctypes.POINTER ctypes.c-int64)))
(val RELEASE (ctypes.CFUNCTYPE None ctypes.c-void-p))


(defk number-value [library number]
  {:pre [(: library ctypes.CDLL) (: number int)] :post [(: % (| int None))] :tags {:context "file" :role "foundation"}}
  "CFNumber を 64 bit の整数で読んで手放すため(読めない・負 = None)。"
  (val value (ctypes.c-int64))
  (try
    (if ((NUMBER-VALUE #("CFNumberGetValue" library)) number CF-NUMBER-SINT64 (ctypes.byref value))
        (if (< value.value 0) None value.value)
        None)
    (finally ((RELEASE #("CFRelease" library)) number))))


(defk volume-property [library url]
  {:pre [(: library ctypes.CDLL) (: url int)] :post [(: % (| int None))] :tags {:context "file" :role "foundation"}}
  "CFURL の volume の空きの key を読むため(断った・値が無い = None)。"
  (val key (. (.in-dll ctypes.c-void-p library VOLUME-FREE-KEY) value))
  (val number (ctypes.c-void-p))
  (val answered ((COPY-PROPERTY #("CFURLCopyResourcePropertyForKey" library)) url key (ctypes.byref number) None))
  (if (and answered number.value)
      (do (<- free (| int None) (number-value library number.value))
          free)
      None))


(defk darwin-volume-free [path]
  {:pre [(: path str)] :post [(: % (| int None))] :tags {:context "file" :role "foundation"}}
  "在る path の volume の Opportunistic の空き(byte)を読むため(頭の註)。答えが無い・CoreFoundation が断った = None。"
  (val library (ctypes.CDLL CORE-FOUNDATION-PATH))
  (val raw (os.fsencode path))
  (val url ((URL-OF-PATH #("CFURLCreateFromFileSystemRepresentation" library)) None raw (len raw) (os.path.isdir path)))
  (if (not url)
      None
      (try
        (<- free (| int None) (volume-property library url))
        free
        (finally ((RELEASE #("CFRelease" library)) url)))))
