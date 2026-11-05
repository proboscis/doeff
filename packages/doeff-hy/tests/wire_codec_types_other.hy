;;; Types with the same names as wire_codec_types but different, used by the check of the self-written JSON Schema writer (test_wire_codec_write.py).

(require doeff-hy.record [defwire])
(import dataclasses [dataclass])


(defwire Note {:names :camel} (#^ int count))
