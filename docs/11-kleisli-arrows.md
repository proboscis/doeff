# Kleisli Arrows

KleisliProgram enables elegant composition and automatic Program unwrapping in doeff.

## Table of Contents

- [What is KleisliProgram?](#what-is-kleisliprogram)
- [Automatic Program Unwrapping](#automatic-program-unwrapping)
- [Composition](#composition)
- [Partial Application](#partial-application)

## What is KleisliProgram?

`KleisliProgram[P, T]` is a callable that:
1. Takes parameters `P`
2. Returns `Program[T]`
3. Automatically unwraps `Program` arguments

The `@do` decorator converts functions to `KleisliProgram`.

### Basic Structure

```python
@do
def my_func(x: int, y: str) -> EffectGenerator[bool]:
    yield Log(f"x={x}, y={y}")
    return x > 0 and y != ""

# Type: KleisliProgram[(x: int, y: str), bool]
# Which means: Callable[[int, str], Program[bool]]
```

## Automatic Program Unwrapping

KleisliProgram automatically unwraps `Program` arguments:

### Example: Composing Programs

```python
@do
def add(x: int, y: int):
    yield Log(f"Adding {x} + {y}")
    return x + y

@do
def multiply(x: int, y: int):
    return x * y

@do
def complex_calculation():
    # These are Program[int]
    a = add(5, 3)      # Program[int] with value 8
    b = multiply(2, 4) # Program[int] with value 8
    
    # Automatic unwrapping: add/multiply receive unwrapped values
    result = yield add(a, b)  # add receives (8, 8)
    
    return result  # 16
```

### How It Works

When you call a KleisliProgram with `Program` arguments:

```python
@do
def process(x: int, y: int):
    return x + y

# Create Program arguments
prog_x = Program.pure(5)
prog_y = Program.pure(10)

# Call with Programs
result = process(prog_x, prog_y)
# Internally:
# 1. Unwraps prog_x to get 5
# 2. Unwraps prog_y to get 10
# 3. Calls the original function with (5, 10)
# 4. Returns Program[int]
```

### Opt-Out of Unwrapping

Annotate parameters as `Program[T]` to prevent unwrapping:

```python
@do
def manual_control(x: int, y: Program[int]):
    # x is unwrapped automatically
    # y is NOT unwrapped - you receive the Program
    
    yield Log(f"x = {x}")
    actual_y = yield y  # Manual unwrap
    yield Log(f"y = {actual_y}")
    
    return x + actual_y

prog_y = Program.pure(10)
result = manual_control(5, prog_y)  # x unwrapped, y passed as Program
```

## Composition

KleisliProgram supports functional composition.

### and_then_k / >> operator

Chain computations with `and_then_k` (or `>>`):

```python
@do
def fetch_user(user_id: int):
    yield Log(f"Fetching user {user_id}")
    return {"id": user_id, "name": f"User{user_id}"}

@do
def fetch_posts(user: dict):
    yield Log(f"Fetching posts for {user['name']}")
    return [{"id": 1, "title": "Post 1"}, {"id": 2, "title": "Post 2"}]

# Compose using >>
fetch_user_posts = fetch_user.and_then_k(lambda user: fetch_posts(user))
# Or: fetch_user_posts = fetch_user >> fetch_posts

runtime = create_runtime()
result = await runtime.run(fetch_user_posts(123))
# Result: [{"id": 1, "title": "Post 1"}, {"id": 2, "title": "Post 2"}]
```

### Pipeline Pattern

```python
@do
def load_data(filename: str):
    yield Log(f"Loading {filename}")
    return {"data": [1, 2, 3, 4, 5]}

@do
def validate_data(data: dict):
    yield Log("Validating data")
    if not data["data"]:
        yield Fail(ValueError("Empty data"))
    return data

@do
def process_data(data: dict):
    yield Log("Processing data")
    return {"result": sum(data["data"])}

# Build pipeline
pipeline = (
    load_data
    >> (lambda d: validate_data(d))
    >> (lambda d: process_data(d))
)

runtime = create_runtime()
result = await runtime.run(pipeline("data.json"))
# Result: {"result": 15}
```

### fmap

Map a pure function over the result:

```python
@do
def get_user():
    return {"id": 1, "name": "Alice", "age": 30}

# Extract just the name
get_name = get_user.fmap(lambda user: user["name"])

runtime = create_runtime()
result = await runtime.run(get_name())
# Result: "Alice"
```

### Combining fmap and and_then_k

```python
@do
def fetch_number():
    return 42

# Transform and chain
pipeline = (
    fetch_number
    .fmap(lambda x: x * 2)  # 84
    .and_then_k(lambda x: Program.pure(x + 10))  # 94
)

runtime = create_runtime()
result = await runtime.run(pipeline())
# Result: 94
```

## Partial Application

Apply some arguments, creating a new KleisliProgram.

### Basic Partial

```python
@do
def greet(greeting: str, name: str):
    yield Log(f"{greeting}, {name}!")
    return f"{greeting}, {name}!"

# Partially apply greeting
say_hello = greet.partial("Hello")

runtime = create_runtime()
result = await runtime.run(say_hello("Alice"))
# Result: "Hello, Alice!"
```

### Partial with Programs

```python
@do
def add_three(x: int, y: int, z: int):
    return x + y + z

# Partially apply first argument
add_to_5 = add_three.partial(5)

prog_y = Program.pure(3)
result = add_to_5(prog_y, 2)  # add_three(5, 3, 2)
# Result: Program[int] with value 10
```

### Currying Pattern

```python
@do
def multiply(x: int, y: int):
    return x * y

# Create specialized multipliers
double = multiply.partial(2)
triple = multiply.partial(3)

@do
def use_multipliers():
    a = yield double(5)  # 10
    b = yield triple(5)  # 15
    return a + b  # 25
```

## Advanced Patterns

### Method Decoration

KleisliProgram works as a method decorator:

```python
class UserService:
    @do
    def get_user(self, user_id: int):
        yield Log(f"Fetching user {user_id}")
        return {"id": user_id, "name": f"User{user_id}"}
    
    @do
    def update_user(self, user_id: int, data: dict):
        user = yield self.get_user(user_id)
        updated = {**user, **data}
        yield Put(f"user_{user_id}", updated)
        return updated

service = UserService()
runtime = create_runtime()
result = await runtime.run(service.get_user(123))
```

### Higher-Order Functions

```python
@do
def apply_twice(f: KleisliProgram, x):
    """Apply a KleisliProgram twice"""
    result1 = yield f(x)
    result2 = yield f(result1)
    return result2

@do
def increment(x: int):
    return x + 1

@do
def example():
    result = yield apply_twice(increment, 5)
    return result  # 7 (increment applied twice)
```

### Factory Pattern

```python
def create_processor(config: dict) -> KleisliProgram:
    """Factory that creates a configured KleisliProgram"""
    
    @do
    def process(data: list):
        yield Log(f"Processing with config: {config}")
        
        if config.get("filter"):
            data = [x for x in data if x > 0]
        
        if config.get("double"):
            data = [x * 2 for x in data]
        
        return data
    
    return process

# Create specialized processors
positive_doubler = create_processor({"filter": True, "double": True})
simple_filter = create_processor({"filter": True})

runtime = create_runtime()
result = await runtime.run(positive_doubler([1, -2, 3, -4, 5]))
# Result: [2, 6, 10]
```

## Best Practices

### Use Type Annotations

```python
# Good: clear types
@do
def process(x: int, y: str) -> EffectGenerator[bool]:
    ...

# Less clear: no types
@do
def process(x, y):
    ...
```

### Opt-In to Manual Program Handling

```python
# When you need control over Program unwrapping
@do
def conditional_execute(condition: bool, action: Program[int]):
    if condition:
        result = yield action
        return result
    else:
        return 0
```

### Compose Small Functions

```python
# Good: small, composable functions
@do
def validate(data):
    ...

@do
def transform(data):
    ...

@do
def save(data):
    ...

pipeline = validate >> transform >> save

# Less good: one large function
@do
def validate_transform_and_save(data):
    # Everything in one place...
```

## Summary

| Feature | Usage |
|---------|-------|
| Auto-unwrap | Programs automatically unwrapped as arguments |
| `and_then_k` / `>>` | Chain KleisliPrograms |
| `fmap` | Map pure functions over results |
| `partial` | Partial application |
| Type annotation opt-out | `Program[T]` parameters not unwrapped |

**Key Points:**
- `@do` creates KleisliProgram
- Automatic Program unwrapping enables natural composition
- Use `>>` for pipelines
- Use `partial` for currying
- Annotate as `Program[T]` to prevent unwrapping

## Next Steps

- **[Patterns](12-patterns.md)** - Composition patterns and best practices
- **[Core Concepts](02-core-concepts.md)** - Deep dive into Program and Effect
- **[API Reference](13-api-reference.md)** - Complete API documentation