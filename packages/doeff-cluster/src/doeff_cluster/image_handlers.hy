;;; image_model の effect の handler。registry-http = registry の HTTP API v2 を読む(本番の image の置き場 zeus:5000 等・読むだけ)・
;;; image-memory = テストの dict。HTTP の client はこの module の中に閉じる。
(require doeff-hy.macros [defhandler])
(import json)
(import httpx)
(import .image_model [ReadImageLabels ImageUnavailable])

(setv MANIFEST-TYPES (.join ", " ["application/vnd.oci.image.index.v1+json"
                                  "application/vnd.docker.distribution.manifest.list.v2+json"
                                  "application/vnd.oci.image.manifest.v1+json"
                                  "application/vnd.docker.distribution.manifest.v2+json"]))
(setv INDEX-TYPES #{"application/vnd.oci.image.index.v1+json" "application/vnd.docker.distribution.manifest.list.v2+json"})


(defn #^ tuple split-image [#^ str image]
  "「host:port/名:tag」→ #(host 名 tag)。host の無い名・tag の無い名は断る(本番の manifest は両方を書く)。"
  (setv #(host _ rest) (.partition image "/"))
  (when (or (not rest) (not (or (in ":" host) (in "." host))))
    (raise (ImageUnavailable (+ "registry の host の無い image は読まない: " image))))
  (setv #(name _ tag) (.rpartition rest ":"))
  (when (or (not name) (not tag) (in "/" tag))
    (raise (ImageUnavailable (+ "tag の無い image は読まない: " image))))
  #(host name tag))


(defn #^ dict pick-platform [#^ dict index #^ str os #^ str arch]
  (for [m (.get index "manifests" [])]
    (setv p (.get m "platform" {}))
    (when (and (= (.get p "os") os) (= (.get p "architecture") arch)) (return m)))
  (raise (ImageUnavailable (.format "index に {}/{} の manifest が無い" os arch))))


(defclass RegistryClient []
  (defn __init__ [self [timeout 5.0] [transport None] [os "linux"] [arch "amd64"]]
    (setv self.os os self.arch arch
          self.client (httpx.Client :timeout timeout :trust-env False #** (if transport {"transport" transport} {}))))

  (defn get [self #^ str url #** kwargs]
    (try
      (setv response (.get self.client url #** kwargs))
      (except [error httpx.HTTPError]
        (raise (ImageUnavailable (.format "registry に届かない: {}: {}" (. (type error) __name__) error)))))
    (when (>= response.status-code 300)
      (raise (ImageUnavailable (.format "registry が {} を返した: {} {}" response.status-code url (cut response.text 0 200)))))
    response)

  (defn #^ dict labels [self #^ str image]
    (setv #(host name tag) (split-image image) base (.format "http://{}/v2/{}" host name))
    (setv manifest (.json (.get self (.format "{}/manifests/{}" base tag) :headers {"Accept" MANIFEST-TYPES})))
    (when (in (.get manifest "mediaType") INDEX-TYPES)
      (setv chosen (pick-platform manifest self.os self.arch))
      (setv manifest (.json (.get self (.format "{}/manifests/{}" base (get chosen "digest")) :headers {"Accept" MANIFEST-TYPES}))))
    (setv digest (.get (.get manifest "config" {}) "digest"))
    (when (not digest) (raise (ImageUnavailable (+ "manifest に config が無い: " image))))
    (setv config (.json (.get self (.format "{}/blobs/{}" base digest))))
    (or (.get (.get config "config" {}) "Labels") {})))


(defhandler registry-http [#^ RegistryClient client]
  (ReadImageLabels [image] (resume (.labels client image))))


(defhandler image-memory [#^ dict images]
  ;; テスト: image → Labels の dict(無い image は ImageUnavailable)。
  (ReadImageLabels [image]
    (when (not-in image images) (raise (ImageUnavailable (+ "テストの registry に無い: " image))))
    (resume (get images image))))
