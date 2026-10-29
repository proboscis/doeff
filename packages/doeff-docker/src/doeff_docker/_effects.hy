;;; Docker effects for doeff.
;;;
;;; Dockerfile instruction effects (From, Run, Copy, etc.) follow the Writer pattern:
;;; a handler collects them during Program execution and produces a Dockerfile string.
;;;
;;; Docker operation effects handle image building, running, and pushing.

(import dataclasses [dataclass])
(import pathlib [Path])
(import typing [Any])
(import doeff [EffectBase])


;; ===========================================================================
;; Dockerfile instruction effects — Writer pattern
;; ===========================================================================

(defclass [(dataclass :frozen True :kw-only True)] From [EffectBase]
  "FROM instruction."
  #^ str image)

(defclass [(dataclass :frozen True :kw-only True)] Run [EffectBase]
  "RUN instruction."
  #^ str command)

(defclass [(dataclass :frozen True :kw-only True)] Copy [EffectBase]
  "COPY instruction. src must be within build context."
  #^ str src
  #^ str dst)

(defclass [(dataclass :frozen True :kw-only True)] Workdir [EffectBase]
  "WORKDIR instruction."
  #^ str path)

(defclass [(dataclass :frozen True :kw-only True)] SetEnv [EffectBase]
  "ENV instruction."
  #^ str key
  #^ str value)

(defclass [(dataclass :frozen True :kw-only True)] Expose [EffectBase]
  "EXPOSE instruction."
  #^ int port)


;; ===========================================================================
;; Docker operations
;; ===========================================================================

(defclass [(dataclass :frozen True :kw-only True)] DockerBuild [EffectBase]
  "Build a Docker image from a Dockerfile string."
  #^ str dockerfile
  #^ str tag
  #^ Path context-path
  #^ str host
  (setv host "localhost"))

(defclass [(dataclass :frozen True :kw-only True)] DockerRun [EffectBase]
  "Run a program inside a Docker container via cloudpickle."
  #^ str image
  #^ Any program
  #^ str host
  (setv host "localhost")
  #^ bool gpu
  (setv gpu False)
  #^ tuple mounts
  (setv mounts #())
  #^ tuple env-vars
  (setv env-vars #()))

(defclass [(dataclass :frozen True :kw-only True)] ImagePush [EffectBase]
  "Push a Docker image to a registry."
  #^ str local-tag
  #^ str remote-tag)


;; ===========================================================================
;; Shell substrate — boundary effect for subprocess execution
;; ===========================================================================

(defclass [(dataclass :frozen True :kw-only True)] ShellRunResult []
  "Structured result from a ShellRun effect."
  #^ int returncode
  #^ bytes stdout
  #^ bytes stderr)

(defclass [(dataclass :frozen True :kw-only True)] ShellRun [EffectBase]
  "Run a subprocess as a list of string args.  A production shell-run-handler
   calls subprocess.run — that IS the boundary.  Handler clauses yield this
   effect instead of calling subprocess directly."
  #^ tuple args
  #^ (| bytes None) stdin-data
  (setv stdin-data None))
