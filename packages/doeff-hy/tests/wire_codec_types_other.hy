;;; 自前の書き手の JSON Schema の検(test_wire_codec_write.py)に使う、wire_codec_types と同じ名の違う型。

(require doeff-hy.record [defwire])
(import dataclasses [dataclass])


(defwire Note {:names :camel} (#^ int count))
