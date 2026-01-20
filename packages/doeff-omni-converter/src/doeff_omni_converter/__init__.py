"""
doeff-omni-converter: Kleisli-based auto-conversion with doeff effects.

This package integrates doeff's effect system with omni-converter's
auto-conversion library, enabling effectful conversion rules while
preserving A* solver compatibility.

Key Features:
- Effectful Conversions: Rules can do IO, logging, config lookup
- Observable Solver: Can log/trace the A* search process
- Deferred Execution: Conversion plan is a Program, user controls when/how to run
- Type-Safe Formats: Beartype catches typos early, IDE support
- Composable: Conversions compose naturally with other doeff effects
- Testable: Mock conversions via handler replacement

Example:
    >>> from doeff import do, run_with_env
    >>> from doeff_omni_converter import AutoData, F, KleisliRuleBook, RULEBOOK_KEY
    >>>
    >>> @do
    >>> def image_pipeline():
    >>>     img = AutoData(path, F.path)
    >>>
    >>>     # Effectful conversion - handler resolves path via rulebook
    >>>     tensor = yield img.to(F.torch("float32", "CHW", "RGB", (0, 1)))
    >>>
    >>>     return tensor.value
    >>>
    >>> # Run with rulebook in environment
    >>> result = run_with_env(
    >>>     image_pipeline().intercept(convert_handler_interceptor),
    >>>     env={RULEBOOK_KEY: my_rulebook}
    >>> )
"""

__version__ = "0.1.0"

# Types exports
# Effects exports
from doeff_omni_converter.effects import (
    ConvertEffect,
    convert,
)

# Handlers exports
from doeff_omni_converter.handlers import (
    RULEBOOK_KEY,
    convert_handler_interceptor,
    handle_convert,
)

# Rules exports
from doeff_omni_converter.rule import (
    AutoData,
    KleisliConverter,
    KleisliEdge,
    KleisliRuleBook,
    Rule,
)

# Solver exports
from doeff_omni_converter.solver import (
    can_convert,
    estimate_cost,
    solve,
    solve_lazy,
)
from doeff_omni_converter.types import (
    Arrangement,
    Backend,
    ColorSpace,
    DType,
    F,
    Format,
    ImageFormat,
)

__all__ = [
    # Handlers
    "RULEBOOK_KEY",
    "Arrangement",
    "AutoData",
    # Types
    "Backend",
    "ColorSpace",
    # Effects
    "ConvertEffect",
    "DType",
    "F",
    "Format",
    "ImageFormat",
    # Rules
    "KleisliConverter",
    "KleisliEdge",
    "KleisliRuleBook",
    "Rule",
    # Version
    "__version__",
    "can_convert",
    "convert",
    "convert_handler_interceptor",
    "estimate_cost",
    "handle_convert",
    # Solver
    "solve",
    "solve_lazy",
]
