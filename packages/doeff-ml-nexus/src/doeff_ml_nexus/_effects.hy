;;; ML-nexus specific effects.
;;; Docker effects (From, Run, Copy, DockerBuild, DockerRun, etc.) are in doeff-docker.

(import dataclasses [dataclass])
(import pathlib [Path])
(import typing [Any])
(import doeff [EffectBase])


;; ===========================================================================
;; Resolve — file/resource location resolution
;; ===========================================================================

(defclass [(dataclass :frozen True :kw-only True)] Resolve [EffectBase]
  "Resolve a target to a requested kind.
   Handler performs any side-effects needed (download, rsync, etc)."
  #^ Any target
  #^ type kind)


;; ===========================================================================
;; File transfer effects
;; ===========================================================================

(defclass [(dataclass :frozen True :kw-only True)] RsyncTo [EffectBase]
  "Rsync files to a destination."
  #^ Path src
  #^ str host
  #^ str dst-path
  #^ tuple excludes
  (setv excludes #())
  #^ tuple includes
  (setv includes #()))


;; ===========================================================================
;; Remote file operations
;; ===========================================================================

(defclass [(dataclass :frozen True :kw-only True)] WriteFile [EffectBase]
  "Write content to a file on a host."
  #^ str host
  #^ str path
  #^ str content)
