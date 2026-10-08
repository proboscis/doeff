"""盤の lease の行を読む使い手が import する 2 つの宣言の失敗ケース — coordinator の断り・不達の例外(shared/protocol/coordinator_route)と、
期限の切れていない持ち手の判断 live-holders(shared/core/lease_rules)。

使い手の repo の lease の読み(盤の行を ReadShared で読み、live-holders で持ち手を数え、盤に届かない・断られた例外 RouteUnreachable・
RouteRefused を except で受ける)に、strict の型検査が書き手に直せない赤を 6 件出した:
- coordinator_route.hy に型の宣言(.pyi)が無く、RouteRefused・RouteUnreachable が Unknown(except で受けた名と `(str e)` も Unknown)。
- lease_rules.pyi の live-holders の引数の行と答えが要素の型の無い dict で、名が partially unknown。
→ coordinator_route.pyi を doeff_hy.static_stub で作り、live-holders の引数と答えの要素の型を lease_rules.hy の注記に書いて作り直す。
宣言と実装の一致は tests/test_generated_stubs.py の一致の検が見る。
"""

import shutil
from pathlib import Path

import pytest

from doeff_hy.static_stub import strict_errors

needs_pyright = pytest.mark.skipif(shutil.which("pyright") is None, reason="pyright が無い")

ROUTE_ERRORS = """\
(require doeff-hy.macros [defk var])
(import doeff_cluster.shared.protocol.coordinator_route [RouteRefused RouteUnreachable])

(defk probe-refuse [status body]
  {:pre [(: status int) (: body str)] :post [(: % str)] :tags {:context "probe" :role "protocol"}}
  "盤が断った形の例外を作って投げ、届かない・断られたの 2 つを except で受けて文にする(使い手の lease の読みと検の盤の代役と同じ形)。"
  (var note "")
  (try
    (raise (RouteRefused status body))
    (except [e #(RouteUnreachable RouteRefused)]
      (:= note (str e))))
  note)

(defk probe-refused-status [e]
  {:pre [(: e RouteRefused)] :post [(: % int)] :tags {:context "probe" :role "protocol"}}
  "断りの status と本文の欄を読む。"
  (+ e.status (len e.body)))

(defk probe-unreachable-url [e]
  {:pre [(: e RouteUnreachable)] :post [(: % str)] :tags {:context "probe" :role "protocol"}}
  "届かなかった失敗の値の欄を読む。"
  e.failed.url)
"""

LIVE_HOLDERS = """\
(require doeff-hy.macros [defk])
(import doeff_cluster.shared.core.lease_rules [live-holders])

(defk probe-live-tokens [rows key now-ms]
  {:pre [(: rows (get dict #(str (get dict #(str object))))) (: key str) (: now-ms int)] :post [(: % (get list str))]
   :tags {:context "probe" :role "judgment"}}
  "盤の読みの答えの行(鍵 → 行の値)を渡し、期限の切れていない持ち手の token を読む(答えの要素の型が読める)。"
  (lfor #(token expires) (.items (live-holders (.get rows key) now-ms)) :if (> expires now-ms) token))

(defk probe-first-expiry [row now-ms]
  {:pre [(: row (get dict #(str int))) (: now-ms int)] :post [(: % int)] :tags {:context "probe" :role "judgment"}}
  "値の型がより細かい行も渡せ、持ち手の期限が int として読める。"
  (min (.values (live-holders row now-ms)) :default now-ms))
"""


@needs_pyright
def test_users_catching_the_route_errors_get_no_unknown_types(tmp_path: Path) -> None:
    errors = strict_errors(tmp_path, ROUTE_ERRORS)
    assert errors == (), errors


@needs_pyright
def test_users_reading_live_holders_get_typed_elements(tmp_path: Path) -> None:
    errors = strict_errors(tmp_path, LIVE_HOLDERS)
    assert errors == (), errors
