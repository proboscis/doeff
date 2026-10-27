# 付録 A の走りの script

design.md の付録 A の結果を出した script。doeff の checkout で `.hy` の file に写し、`uv run --project <doeff の checkout> --frozen --no-sync hy <file>` で走らせる(2026-10-27・pydantic 2.13.4・pydantic_core 2.46.4)。repo の検査が拾わないよう、code の file にせずここに置く。

## probe1_scalars.hy

```hy
(require doeff-hy.record [defrecord defwire defenum])
(import enum [StrEnum])
(import dataclasses [dataclass])
(import typing [Literal Annotated])
(import json)
(import pydantic [ValidationError])
(import doeff_hy.json_value [OpaqueJson])
(import doeff_hy.frozen [FrozenMap])

(defenum Color RED (BLUE "blue-x"))
(defwire Inner {:names :camel} (#^ int n-val))
(defwire Opt {:names :camel} (#^ int a) (setv #^ (| str None) b None) (setv #^ int c 3))
(defwire IgnoreT {:names :camel :unknown :ignore} (#^ int a))
(defrecord Plain (#^ int a))
(defwire T {:names :camel}
  (#^ str s) (#^ int i) (#^ float f) (#^ bool b) (#^ (| int None) oi)
  (#^ (get tuple #(int ...)) tv) (#^ (get tuple #(int str)) tf) (#^ (get list int) li)
  (#^ (get dict #(str int)) d) (#^ Color e) (#^ (get Literal #("x" "y")) lit)
  (#^ Inner inner) (#^ OpaqueJson oj) (#^ (| int str) u) (#^ (get frozenset str) fs) (#^ object ob) (#^ dict rawd))
(setv good {"s" "a" "i" 1 "f" 1.5 "b" True "oi" None "tv" [1 2] "tf" [1 "a"] "li" [1] "d" {"k" 1} "e" "red" "lit" "x"
            "inner" {"nVal" 1} "oj" {"z" [1]} "u" 1 "fs" ["a"] "ob" {"q" 1} "rawd" {"q" [1]}})
(defn try-it [label k v]
  (setv x (dict good))
  (if (= v "DEL") (del (get x k)) (setv (get x k) v))
  (try
    (setv r (.validate-json T.__doeff_wire__.adapter (json.dumps x)))
    (print label "OK" (repr (getattr r (hy.mangle k) None)))
    (except [e ValidationError]
      (print label "ERR" (lfor p (.errors e :include-url False) #((get p "loc") (get p "type") (get p "msg")))))))
(try-it "good" "s" "a")
(try-it "str<-int" "s" 1)
(try-it "int<-str" "i" "1")
(try-it "int<-bool" "i" True)
(try-it "int<-float1.0" "i" 1.0)
(try-it "int<-float1.5" "i" 1.5)
(try-it "float<-int" "f" 1)
(try-it "float<-str" "f" "1")
(try-it "float<-bool" "f" True)
(try-it "bool<-int" "b" 1)
(try-it "bool<-str" "b" "true")
(try-it "opt<-missing" "oi" "DEL")
(try-it "tv<-obj" "tv" {"a" 1})
(try-it "tv<-str" "tv" "ab")
(try-it "tf<-short" "tf" [1])
(try-it "tf<-long" "tf" [1 "a" 2])
(try-it "li<-tuple" "li" [1 "x"])
(try-it "d<-badval" "d" {"k" "1"})
(try-it "e<-name" "e" "RED")
(try-it "e<-bluex" "e" "blue-x")
(try-it "e<-bad" "e" "zz")
(try-it "lit<-bad" "lit" "z")
(try-it "inner<-pyname" "inner" {"n_val" 1})
(try-it "inner<-extra" "inner" {"nVal" 1 "zz" 2})
(try-it "oj<-scalar" "oj" 5)
(try-it "u<-float" "u" 1.5)
(try-it "u<-bool" "u" True)
(try-it "fs<-list" "fs" ["a" "a"])
(try-it "ob<-any" "ob" [1])
(try-it "rawd<-list" "rawd" [1])
(try-it "missing-s" "s" "DEL")
(try-it "s<-null" "s" None)
(print "ignore" (.validate-json IgnoreT.__doeff_wire__.adapter "{\"a\":1,\"zz\":2}"))
(try (.validate-json T.__doeff_wire__.adapter "not json") (except [e ValidationError] (print "notjson" (.errors e :include-url False))))
(try (.validate-json T.__doeff_wire__.adapter "[1]") (except [e ValidationError] (print "toplist" (lfor p (.errors e :include-url False) #((get p "loc") (get p "type"))))))
(try (.validate-json Opt.__doeff_wire__.adapter "{\"a\":1,\"b\":null}") (except [e ValidationError] (print "optnull" e)) (else (print "opt b null OK")))
(print "dump" (.dump-python Opt.__doeff_wire__.adapter (Opt :a 1) :mode "json" :by-alias True :exclude-defaults True))
(print "dump2" (.dump-python Opt.__doeff_wire__.adapter (Opt :a 1 :b "x" :c 3) :mode "json" :by-alias True :exclude-defaults True))
(print "f-big" (.validate-json T.__doeff_wire__.adapter (json.dumps (| good {"f" 1e400}))))
(try (print "i-1e3" (.validate-json T.__doeff_wire__.adapter (.replace (json.dumps good) "\"i\": 1" "\"i\": 1e3")))(except [e ValidationError] (print "i-1e3 ERR" (lfor p (.errors e :include-url False) (get p "type")))))
(try (print "f-nan" (. (.validate-json T.__doeff_wire__.adapter (.replace (json.dumps good) "\"f\": 1.5" "\"f\": NaN")) f)) (except [e ValidationError] (print "f-nan ERR" (lfor p (.errors e :include-url False) (get p "type")))))
(try (print "dup-key" (. (.validate-json T.__doeff_wire__.adapter (.replace (json.dumps good) "\"i\": 1" "\"i\": 1, \"i\": 2")) i)) (except [e ValidationError] (print "dup ERR" e)))
(print "multi-err" (try (.validate-json T.__doeff_wire__.adapter "{\"s\":1,\"i\":\"x\"}") (except [e ValidationError] (len (.errors e)))))
(print "schema" (json.dumps (.json-schema Opt.__doeff_wire__.adapter :by-alias True)))
```

## probe2_nested_record_and_rare.hy

```hy
(require doeff-hy.record [defrecord defwire defenum])
(import enum [StrEnum])
(import dataclasses [dataclass])
(import typing [Literal Annotated])
(import datetime [datetime])
(import json)
(import pydantic [ValidationError Field])
(defrecord Plain (#^ int a-b) (setv #^ int c 0))
(defwire Outer {:names :camel} (#^ Plain p))
(defwire Rare {:names :camel} (#^ datetime dt) (#^ bytes by) (#^ (get Literal #(1 2)) li) (#^ (get Annotated #(int (Field :ge 0))) ge)
  (setv #^ (get tuple #(str ...)) xs #()))
(defn show [T text]
  (try (print "OK" (.validate-json T.__doeff_wire__.adapter text))
       (except [e ValidationError] (print "ERR" (lfor p (.errors e :include-url False) #((get p "loc") (get p "type") (get p "msg")))))
       (except [e Exception] (print "EXC" (type e) e))))
(show Outer "{\"p\":{\"aB\":1}}")
(show Outer "{\"p\":{\"a_b\":1}}")
(show Outer "{\"p\":{\"a_b\":1,\"zz\":1}}")
(show Outer "{\"p\":{\"a_b\":\"1\"}}")
(try (print "dump-outer" (.dump-python Outer.__doeff_wire__.adapter (Outer :p (Plain :a-b 1)) :mode "json" :by-alias True :exclude-defaults True)) (except [e Exception] (print "dump EXC" e)))
(show Rare "{\"dt\":\"2026-10-27T01:02:03Z\",\"by\":\"abc\",\"li\":1,\"ge\":0}")
(show Rare "{\"dt\":1700000000,\"by\":\"abc\",\"li\":true,\"ge\":-1}")
(show Rare "{\"dt\":\"2026-10-27\",\"by\":\"abc\",\"li\":3,\"ge\":0,\"xs\":[\"a\"]}")
(print "dump-rare" (.dump-python Rare.__doeff_wire__.adapter (Rare :dt (datetime 2026 1 1) :by b"ab" :li 1 :ge 0 :xs #()) :mode "json" :by-alias True :exclude-defaults True))
(print "schema-rare" (json.dumps (.json-schema Rare.__doeff_wire__.adapter :by-alias True)))
```

## probe3_union_pick.hy

```hy
(require doeff-hy.record [defrecord defwire])
(import dataclasses [dataclass])
(import json)
(import pydantic [ValidationError])
(defwire A {:names :camel :unknown :ignore} (#^ int x))
(defwire B {:names :camel :unknown :ignore} (#^ int x) (setv #^ int y 0))
(defwire C {:names :camel} (#^ int x) (setv #^ int y 0))
(defwire D {:names :camel} (#^ int x))
(defwire U1 {:names :camel} (#^ (| A B) v))
(defwire U2 {:names :camel} (#^ (| D C) v))
(defwire U3 {:names :camel} (#^ (| float int) n) (#^ (| int float) m))
(defwire U4 {:names :camel} (#^ (| D int None) v))
(defn show [T text]
  (try (print "OK" (.validate-json T.__doeff_wire__.adapter text))
       (except [e ValidationError] (print "ERR" (lfor p (.errors e :include-url False) #((get p "loc") (get p "type")))))))
(show U1 "{\"v\":{\"x\":1}}")
(show U1 "{\"v\":{\"x\":1,\"y\":2}}")
(show U2 "{\"v\":{\"x\":1}}")
(show U2 "{\"v\":{\"x\":1,\"y\":2}}")
(show U3 "{\"n\":1,\"m\":1}")
(show U3 "{\"n\":1.0,\"m\":1.0}")
(show U4 "{\"v\":{\"x\":\"s\"}}")
(show U4 "{\"v\":\"s\"}")
```

## probe4_error_order.hy

```hy
(require doeff-hy.record [defrecord defwire])
(import dataclasses [dataclass])
(import pydantic [ValidationError])
(defwire K {:names :camel :check [(> n 0)]} (#^ int n) (#^ (| int None) oi) (#^ (| int str None) u) (#^ str s))
(defwire N {:names :camel} (#^ K k))
(defn show [T text]
  (try (print "OK" (.validate-json T.__doeff_wire__.adapter text))
       (except [e ValidationError] (print "ERR" (lfor p (.errors e :include-url False) #((.join "." (map str (get p "loc"))) (.format "{} [{}]" (get p "msg") (get p "type"))))))))
(show K "{\"n\":0,\"oi\":null,\"u\":null,\"s\":\"a\"}")
(show K "{\"zz\":1,\"s\":1,\"oi\":\"x\",\"u\":true}")
(show N "{\"k\":{\"n\":0,\"oi\":null,\"u\":null,\"s\":\"a\"}}")
(show N "{\"k\":[]}")
(show N "{\"k\":null}")
```

## probe5_catch_all_arm.hy

```hy
(require doeff-hy.record [defrecord defwire])
(import dataclasses [dataclass])
(import doeff_hy.json_value [OpaqueJson])
(import pydantic [ValidationError])
(defwire D {:names :camel} (#^ int x))
(defwire P {:names :camel} (#^ (| OpaqueJson str) a) (#^ (| str OpaqueJson) b) (#^ (| object int) c) (#^ (| OpaqueJson D) d) (#^ (| str None OpaqueJson) e))
(print (.validate-json P.__doeff_wire__.adapter "{\"a\":\"s\",\"b\":\"s\",\"c\":1,\"d\":{\"x\":1},\"e\":null}"))
(print (.validate-json P.__doeff_wire__.adapter "{\"a\":1,\"b\":2,\"c\":\"q\",\"d\":{\"x\":\"no\"},\"e\":3}"))
(print (.dump-python P.__doeff_wire__.adapter (P :a "s" :b (OpaqueJson.of 2) :c 1 :d (D :x 1) :e (OpaqueJson.of [1])) :mode "json" :by-alias True))
```

## probe6_arm_labels.hy

```hy
(require doeff-hy.record [defrecord defwire defenum])
(import dataclasses [dataclass])
(import enum [StrEnum])
(import typing [Literal])
(import pydantic [ValidationError])
(defenum Color RED)
(defwire D {:names :camel} (#^ int x))
(defwire P {:names :camel} (#^ (| int float bool Color (get Literal #("x")) (get tuple #(int ...)) (get tuple #(int str)) (get list str) (get dict #(str int)) (get frozenset str) D) v))
(try (.validate-json P.__doeff_wire__.adapter "{\"v\":\"zz\"}") (except [e ValidationError] (for [p (.errors e :include-url False)] (print (get p "loc") (get p "type") (get p "msg")))))
(try (.validate-json P.__doeff_wire__.adapter "{\"v\":null}") (except [e ValidationError] (print (len (.errors e)))))
```

## probe7_top_level_and_enum.hy

```hy
(require doeff-hy.record [defrecord defwire defenum])
(import dataclasses [dataclass])
(import enum [StrEnum])
(import typing [Literal])
(import pydantic [ValidationError])
(defenum E3 A B C)
(defwire D {:names :camel} (#^ int x))
(defwire T {:names :camel} (#^ E3 e) (#^ (get Literal #("p" "q" "r")) l) (#^ (get tuple #(D ...)) ds) (#^ (get dict #(str D)) dm) (setv #^ float f 1.0))
(defn show [text]
  (try (print "OK" (.validate-json T.__doeff_wire__.adapter text))
       (except [e ValidationError] (for [p (.errors e :include-url False)] (print (repr (.join "." (map str (get p "loc")))) (get p "msg") (get p "type"))))))
(show "[1]")
(show "null")
(show "{\"e\":\"z\",\"l\":\"z\",\"ds\":[{\"x\":1},{}],\"dm\":{\"k\":{\"x\":\"1\"}}}")
(print (.dump-python T.__doeff_wire__.adapter (T :e E3.A :l "p" :ds #((D :x 1)) :dm {"k" (D :x 2)} :f (float "inf")) :mode "json" :by-alias True :exclude-defaults True))
```

## float_spelling.py

```python
import json
from pydantic import TypeAdapter
ta = TypeAdapter(list[float])
xs = [0.1, 1.0, 1e-4, 1e-5, 1e-7, 1.5e-7, 1e15, 1e16, 1.2345e16, 1e17, 123456789.123, 1e22, 1e300, 5e-324, float('inf'), float('nan')]
p = ta.dump_json(xs).decode()
j = json.dumps(xs, separators=(',', ':'))
print(p); print(j)
print(TypeAdapter(dict[str,str]).dump_json({"a":"é <\x7f"}).decode(), json.dumps({"a":"é <\x7f"}, ensure_ascii=False, separators=(',', ':')))
```
