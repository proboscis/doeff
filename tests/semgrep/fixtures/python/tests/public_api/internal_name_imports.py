"""Sample for doeff-public-api-no-subpackage-imports.

Only lines marked `# fires` are hit. Rule wording = imports from a subpackage below are allowed; names at the top level come from the top level.
Forms that do not fire = every imported name is an internal name starting with `_` (a name not exported at top level -- a sentinel, etc.).
Forms that fire = a form containing even one public name, and `import doeff.<subpackage>`.
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

from doeff.mcp import McpToolDef  # fires
from doeff.mcp import _MISSING, McpToolDef  # fires
from doeff.mcp import McpToolDef, _MISSING  # fires
from doeff.mcp import McpToolDef as tool_def  # fires
from doeff.mcp import (  # fires
    _MISSING,
    McpToolDef,
)
from doeff.mcp import (  # fires
    McpToolDef,
    _MISSING,
)
from doeff.mcp.schema import McpToolDef  # fires
from doeff.mcp import *  # fires
import doeff.mcp  # fires
import doeff.mcp as mcp  # fires
from doeff.mcp import (  # fires
    _MISSING,
    # ##########################################################################################
    McpToolDef,
)
