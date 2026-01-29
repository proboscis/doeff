"""
Comprehensive test suite for doeff type detection.
Each section tests specific detection rules.
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Generic, TypeVar

from doeff import Effect, Program, do

# ===========================================================================
# SECTION 1: INTERPRETERS (Program[T] -> Any where Any != Program)
# ===========================================================================

# CORRECT: Marked interpreter (Program -> non-Program)
def exec_int(program: Program[int]) -> int:  # doeff: interpreter
    """✅ Should be found by find-interpreters"""
    return program.run()

# CORRECT: Interpreter with Any type
def exec_any(program: Program[Any]) -> Any:  # doeff: interpreter
    """✅ Should be found by find-interpreters"""
    return program.run()

# WRONG: Not marked (signature alone not enough for find-*)
def exec_unmarked(program: Program[str]) -> str:
    """❌ Should NOT be found by find-interpreters (no marker)"""
    return program.run()

# WRONG: Returns Program (this is a transform, not interpreter!)
def wrong_interpreter(program: Program[int]) -> Program[int]:  # doeff: interpreter
    """❌ Incorrectly marked - returns Program so it's a transform"""
    return program

# ===========================================================================
# SECTION 2: TRANSFORMS (Program[_] -> Program[_])
# ===========================================================================

# CORRECT: Marked transform
def map_transform(program: Program[int]) -> Program[str]:  # doeff: transform
    """✅ Should be found by find-transforms"""
    return program.map(str)

# CORRECT: @do with Program first param (becomes transform)
@do
def do_transform(program: Program[int]) -> str:  # doeff: transform
    """✅ @do with Program param -> Transform (returns Program[str])"""
    result = yield program
    return str(result)

# WRONG: Not marked
def unmarked_transform(program: Program[int]) -> Program[int]:
    """❌ Should NOT be found by find-transforms (no marker)"""
    return program

# WRONG: Doesn't return Program (this is an interpreter!)
def wrong_transform(program: Program[int]) -> int:  # doeff: transform
    """❌ Incorrectly marked - doesn't return Program"""
    return program.run()

# ===========================================================================
# SECTION 3: KLEISLI PROGRAMS (T -> Program[U] or @do functions)
# ===========================================================================

# CORRECT: @do function (automatic Kleisli)
@do
def fetch_by_id(user_id: str) -> User:  # doeff: kleisli
    """✅ KleisliProgram[str, User] via @do"""
    yield Tell(f"Fetching {user_id}")
    return User(user_id)

# CORRECT: @do with Any parameter
@do
def process_any(data: Any) -> Result:  # doeff: kleisli
    """✅ KleisliProgram[Any, Result] - matches ALL type filters"""
    yield Tell("Processing")
    return Result(data)

# CORRECT: Manually marked Kleisli
def manual_kleisli(value: int) -> Program[str]:  # doeff: kleisli
    """✅ Marked as kleisli"""
    return Program.of(str(value))

# SPECIAL: @do with Program param (NOT Kleisli, it's Transform!)
@do
def not_kleisli(program: Program[int]) -> str:
    """❌ NOT Kleisli - @do with Program param makes it Transform"""
    result = yield program
    return str(result)

# Type filtering test cases
@do
def kleisli_str(name: str) -> int:  # doeff: kleisli
    """For testing: find-kleisli --type-arg str should match"""
    return len(name)

@do
def unmarked_kleisli(name: str) -> int:
    """❌ Missing marker - should not be discoverable"""
    return len(name) + 1

@do
def kleisli_int(count: int) -> str:  # doeff: kleisli
    """For testing: find-kleisli --type-arg str should NOT match"""
    return str(count)

@do
def kleisli_optional(value: str | None) -> int:  # doeff: kleisli
    """For testing: Optional parameters"""
    return len(value or "")

@do
def kleisli_with_default(name: str, retries: int = 1) -> int:  # doeff: kleisli
    """Additional optional parameters should be allowed"""
    yield Tell(f"Retry {name}")
    return len(name) + retries

@do
def kleisli_multi_required(primary: str, secondary: int) -> int:  # doeff: kleisli
    """Multiple required parameters should disqualify Program[T] filters"""
    yield Tell("Multiple args")
    return len(primary) + secondary

@do
def kp_aggregate_segmentations(
    image: Img,
    extractors: Iterable[tuple[int, Extractor]],
) -> EffectGenerator[Mask1]:  # doeff: kleisli
    """Aggregate multiple segmentations into a multi-class mask using parallel execution."""
    yield Tell("Starting aggregation of segmentations")
    extractor_items = list(extractors)
    return Mask1(len(extractor_items))

# ===========================================================================
# SECTION 4: INTERCEPTORS (Effect -> Effect | Program)
# ===========================================================================

# CORRECT: Marked interceptor
def log_effect_interceptor(effect: LogEffect) -> LogEffect:  # doeff: interceptor
    """✅ Should be found by find-interceptors"""
    return LogEffect(f"[INTERCEPTED] {effect.message}")

# CORRECT: @do with Effect first param
@do
def do_effect_interceptor(effect: Effect) -> str:  # doeff: interceptor
    """✅ @do with Effect param -> Interceptor"""
    yield effect
    return "done"

# CORRECT: Generic Effect type
def generic_interceptor(effect: Effect) -> Effect:  # doeff: interceptor
    """✅ Generic Effect interceptor"""
    return effect

# WRONG: Not marked
def unmarked_interceptor(effect: Effect) -> Effect:
    """❌ Should NOT be found by find-interceptors (no marker)"""
    return effect

# ===========================================================================
# SECTION 5: EDGE CASES & SPECIAL SCENARIOS
# ===========================================================================

# Multiple markers (should appear in multiple find-* results)
def hybrid_function(x: Any) -> Program[Any]:  # doeff: kleisli transform
    """Has both kleisli and transform markers"""
    return Program.of(x)

# @do with Effect param but NOT marked as interceptor
@do
def unlabeled_do_effect(effect: Effect) -> str:
    """@do with Effect but no interceptor marker - categorized but not found by find-interceptors"""
    yield effect
    return "result"

# Function with no doeff relevance
def regular_function(x: int) -> str:
    """Regular function - should not appear in any find-* results"""
    return str(x)

# Class methods (should also be detected)
class Controller:
    def run_program(self, program: Program[int]) -> int:  # doeff: interpreter
        """Class method interpreter"""
        return program.run()

    @do
    def fetch_data(self, key: str) -> Data:
        """Class method Kleisli"""
        yield Tell(f"Fetching {key}")
        return Data(key)

# ===========================================================================
# HELPER CLASSES (for type annotations)
# ===========================================================================

class User:
    def __init__(self, id: str): self.id = id

class Result:
    def __init__(self, data: Any): self.data = data

class Data:
    def __init__(self, key: str): self.key = key

class LogEffect(Effect):
    def __init__(self, message: str): self.message = message

def Log(msg: str): return LogEffect(msg)

T = TypeVar("T")

class EffectGenerator(Generic[T]):
    def __init__(self, value: T): self.value = value

class Img: ...

class Extractor:
    def __init__(self, name: str): self.name = name

class Mask1:
    def __init__(self, classes: int): self.classes = classes
