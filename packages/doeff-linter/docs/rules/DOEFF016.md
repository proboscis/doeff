# DOEFF016: No Relative Imports

## Summary

Relative imports (`from .module import ...`) are forbidden. Use absolute imports instead for better code maintainability and refactoring support.

## Why This Matters

Relative imports create several problems in a codebase:

1. **Refactoring Difficulty**: Moving files or renaming packages breaks relative imports
2. **Readability**: It's harder to understand where imports come from without tracing the file structure
3. **Consistency**: Mixing relative and absolute imports creates inconsistency
4. **Tooling Support**: Many static analysis tools work better with absolute imports

### The Problem with Relative Imports

```python
# ❌ Bad: Relative imports
from .utils import helper
from ..services import api
from ...core.models import User
```

These imports:
- Break when the file is moved to a different location
- Require mental tracking of the current file's position in the package hierarchy
- Make code harder to copy between projects

## What This Rule Detects

### Single-Level Relative Imports

```python
# ❌ Flagged
from . import utils
from .module import something
```

### Multi-Level Relative Imports

```python
# ❌ Flagged
from .. import parent_module
from ..sibling import helper
from ...grandparent.module import thing
```

## Recommended Fixes

### Convert to Absolute Imports

```python
# ✅ Good: Absolute imports
from mypackage.utils import helper
from mypackage.services import api
from mypackage.core.models import User
```

### Standard Library Example

```python
# ❌ Bad
from .os_utils import get_path

# ✅ Good
from myproject.utils.os_utils import get_path
```

### Deep Package Example

```python
# ❌ Bad: Hard to understand the actual source
from ...data.processing.transformers import DataTransformer

# ✅ Good: Clear and explicit
from myproject.data.processing.transformers import DataTransformer
```

## Allowed Patterns

The rule does NOT flag these patterns:

```python
# ✅ Absolute imports are allowed
from package.module import something
from os import path
import sys

# ✅ Standard library absolute imports
from collections import defaultdict
from typing import List, Optional

# ✅ Third-party absolute imports  
from pydantic import BaseModel
import numpy as np
```

## Error Message Format

```
DOEFF016: Relative import detected: 'from .{module} import ...'

Problem: Relative imports make code harder to move and refactor.

Fix: Use absolute import instead:
  from <package>.<module> import ...
```

## Severity

**Error** - Relative imports should be converted to absolute imports for maintainability.

## Configuration

This rule is enabled by default. To disable:

```toml
# pyproject.toml
[tool.doeff-linter]
disabled_rules = ["DOEFF016"]
```

## Migration Strategy

When converting a codebase from relative to absolute imports:

1. **Identify package root**: Determine the top-level package name
2. **Search and replace**: Use IDE refactoring tools to convert imports
3. **Verify imports**: Run tests and linting to ensure all imports resolve correctly
4. **Update __init__.py**: Ensure `__init__.py` files properly expose submodules if needed

### Example Migration

Before:
```python
# mypackage/services/user_service.py
from ..models import User
from .base_service import BaseService
```

After:
```python
# mypackage/services/user_service.py
from mypackage.models import User
from mypackage.services.base_service import BaseService
```

## Related Rules

- DOEFF004: No os.environ Access (configuration should be explicit)

## See Also

- [PEP 328 – Imports: Multi-Line and Absolute/Relative](https://peps.python.org/pep-0328/)
- [Google Python Style Guide - Imports](https://google.github.io/styleguide/pyguide.html#s2.2-imports)

