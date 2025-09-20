from doeff import Program

# Test case 1: Program with type parameter
my_program: Program[str] = Program.unit("hello")

# Test case 2: Program without type parameter
simple_program: Program = Program.unit(42)

# Test case 3: Program with complex type
complex_program: Program[list[int]] = Program.unit([1, 2, 3])

# Test case 4: Using string annotation (for forward references)
forward_ref: "Program[dict[str, int]]" = Program.unit({"a": 1})

# Test case 5: Not a Program (should not show gutter icon)
not_a_program: str = "test"

# Test interpreter functions
def interpreter(p: Program):
    """Generic interpreter that accepts any Program"""
    print(f"Running program: {p}")
    return p.run()

def str_interpreter(p: Program[str]) -> str:
    """Type-specific interpreter for Program[str]"""
    print(f"Running string program: {p}")
    result = p.run()
    return f"Result: {result}"

def int_interpreter(p: Program[int]) -> int:
    """Type-specific interpreter for Program[int]"""
    print(f"Running int program: {p}")
    return p.run()

# Test case 6: Inside a class (should not show gutter icon according to code)
# noqa: PINJ053 - Intentionally using a class to test gutter icon skipping behavior
class TestClass:  # noqa: PINJ053
    class_program: Program[str] = Program.unit("class")