"""doeff-public-api-no-subpackage-imports の検体。

`# 鳴る` を付けた行だけが当たる。規則の言葉 = 下の package からの import は許す・top level に在る名は top level から。
鳴らない形 = 取り込む名が全部 `_` で始まる内部の名(top level に出ない名 — sentinel など)。
鳴る形 = 公開の名を 1 つでも含む形・`import doeff.<下の package>`。
"""

from doeff import McpParamSchema
from doeff.mcp import _MISSING
from doeff.mcp import _MISSING, _OTHER_MISSING
from doeff.mcp import _MISSING as missing_sentinel
from doeff.mcp import _MISSING, _OTHER_MISSING as other_sentinel  # noqa: PLC2701
from doeff.mcp import (
    _MISSING,
    _OTHER_MISSING as other_sentinel,
)
from doeff.mcp import (  # noqa: PLC2701
    _MISSING,  # sentinel
    _OTHER_MISSING,
)
from doeff.mcp.schema import _MISSING

from doeff.mcp import McpToolDef  # 鳴る
from doeff.mcp import _MISSING, McpToolDef  # 鳴る
from doeff.mcp import McpToolDef, _MISSING  # 鳴る
from doeff.mcp import McpToolDef as tool_def  # 鳴る
from doeff.mcp import (  # 鳴る
    _MISSING,
    McpToolDef,
)
from doeff.mcp import (  # 鳴る
    McpToolDef,
    _MISSING,
)
from doeff.mcp.schema import McpToolDef  # 鳴る
from doeff.mcp import *  # 鳴る
import doeff.mcp  # 鳴る
import doeff.mcp as mcp  # 鳴る
from doeff.mcp import (  # 鳴る
    _MISSING,
    # ##########################################################################################
    McpToolDef,
)
