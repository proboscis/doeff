;;; Reading free space of a macOS (Darwin) volume -- reads the volume resource key
;;; kCFURLVolumeAvailableCapacityForOpportunisticUsageKey (free space of storage for opportunistic, nice-to-have data) with CoreFoundation's CFURLCopyResourcePropertyForKey.
;;;
;;; The free space of statvfs (shutil.disk_usage) counts as unusable the area that macOS frees automatically when needed (deleted content held by APFS local snapshots, purgeable caches).
;;; Apple's documentation distinguishes: storage for things the user asked for or that are needed to run = ImportantUsage; storage for opportunistic, nice-to-have
;;; data = Opportunistic. The readers of free space (refusal of worker env preparation in doeff-cluster, the work-disk state in the heartbeat, cleanup decisions) deal with
;;; storage that can be rebuilt after deletion, so they read Opportunistic -- the smaller of the two, which does not count all of the area macOS frees (Mac at 10-17 17:48:
;;; statvfs 45.2 GB, Opportunistic 75.5 GB, ImportantUsage 92.7 GB). While the Time Machine destination is unreachable, the local snapshot is the only backup at hand,
;;; so the more generous ImportantUsage is not used.
;;;
;;;   darwin-volume-free  free space (bytes) of Opportunistic for the volume of an existing path. None if the volume does not answer the key (no value) or CoreFoundation refuses.
;;;                       This module is the only one that calls CoreFoundation directly, and it is called only on macOS (machine selection and the floor are in os_file.hy).
(require doeff-hy.macros [defk <- val])
(val MODULE-TAGS {:context "file" :role "foundation"})
(import ctypes)
(import os)

(val CORE-FOUNDATION-PATH "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
;; The resource key to read (name of a CFStringRef constant in CFURL.h -- see header note).
(val VOLUME-FREE-KEY "kCFURLVolumeAvailableCapacityForOpportunisticUsageKey")
;; Type tag kCFNumberSInt64Type for CFNumberGetValue (CFNumber.h).
(val CF-NUMBER-SINT64 4)
;; Types of the 4 functions called (CFURL.h, CFNumber.h, CFBase.h). Boolean is unsigned char.
(val URL-OF-PATH (ctypes.CFUNCTYPE ctypes.c-void-p ctypes.c-void-p ctypes.c-char-p ctypes.c-long ctypes.c-ubyte))
(val COPY-PROPERTY (ctypes.CFUNCTYPE ctypes.c-ubyte ctypes.c-void-p ctypes.c-void-p (ctypes.POINTER ctypes.c-void-p) ctypes.c-void-p))
(val NUMBER-VALUE (ctypes.CFUNCTYPE ctypes.c-ubyte ctypes.c-void-p ctypes.c-int (ctypes.POINTER ctypes.c-int64)))
(val RELEASE (ctypes.CFUNCTYPE None ctypes.c-void-p))


(defk number-value [library number]
  {:pre [(: library ctypes.CDLL) (: number int)] :post [(: % (| int None))] :tags {:context "file" :role "foundation"}}
  "Read a CFNumber as a 64-bit integer and release it (unreadable or negative = None)."
  (val value (ctypes.c-int64))
  (try
    (if ((NUMBER-VALUE #("CFNumberGetValue" library)) number CF-NUMBER-SINT64 (ctypes.byref value))
        (if (< value.value 0) None value.value)
        None)
    (finally ((RELEASE #("CFRelease" library)) number))))


(defk volume-property [library url]
  {:pre [(: library ctypes.CDLL) (: url int)] :post [(: % (| int None))] :tags {:context "file" :role "foundation"}}
  "Read the free-space key of a CFURL volume (refused or no value = None)."
  (val key (. (.in-dll ctypes.c-void-p library VOLUME-FREE-KEY) value))
  (val number (ctypes.c-void-p))
  (val answered ((COPY-PROPERTY #("CFURLCopyResourcePropertyForKey" library)) url key (ctypes.byref number) None))
  (if (and answered number.value)
      (do (<- free (| int None) (number-value library number.value))
          free)
      None))


(defk darwin-volume-free [path]
  {:pre [(: path str)] :post [(: % (| int None))] :tags {:context "file" :role "foundation"}}
  "Read the Opportunistic free space (bytes) of the volume of an existing path (see header note). No answer or CoreFoundation refused = None."
  (val library (ctypes.CDLL CORE-FOUNDATION-PATH))
  (val raw (os.fsencode path))
  (val url ((URL-OF-PATH #("CFURLCreateFromFileSystemRepresentation" library)) None raw (len raw) (os.path.isdir path)))
  (if (not url)
      None
      (try
        (<- free (| int None) (volume-property library url))
        free
        (finally ((RELEASE #("CFRelease" library)) url)))))
