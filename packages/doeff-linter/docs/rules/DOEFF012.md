# DOEFF012: No Append Loop Pattern

## Summary

Detects the anti-pattern of initializing an empty list followed by a for loop that appends to it. This pattern obscures the data transformation pipeline and makes code harder to understand at a glance.

## Rationale

The "initialize empty list + append in loop" pattern forces readers to:
1. Read through the entire loop body to understand what transformation is happening
2. Track mutable state across potentially many lines
3. Mentally reconstruct the data pipeline

By extracting processing logic into named functions and using list comprehensions, you create:
1. Self-documenting code with clear intent
2. A visible data pipeline where transformations are obvious
3. Easier testing of individual processing steps
4. Better composability

## Examples

### Bad

```python
# Pattern 1: Simple append loop
data = []
for item in items:
    data.append(process(item))

# Pattern 2: Long processing in loop body  
results = []
for item in processing_target:
    step1 = do_something(item)
    step2 = transform(step1)
    validated = validate(step2)
    if validated:
        final = finalize(validated)
        results.append(final)

# Pattern 3: Chained pipelines (detected twice)
first_stage = []
for item in raw_data:
    first_stage.append(parse(item))

second_stage = []
for item in first_stage:
    second_stage.append(transform(item))
```

### Good

```python
# Pattern 1: Simple list comprehension
data = [process(item) for item in items]

# Pattern 2: Extract processing into named function
def process_one_item(item):
    step1 = do_something(item)
    step2 = transform(step1)
    validated = validate(step2)
    if validated:
        return finalize(validated)
    return None

results = [r for r in (process_one_item(x) for x in processing_target) if r is not None]

# Or with filter:
results = list(filter(None, map(process_one_item, processing_target)))

# Pattern 3: Clear pipeline with named functions
def parse_item(item):
    return parse(item)

def transform_item(item):
    return transform(item)

first_stage = [parse_item(item) for item in raw_data]
second_stage = [transform_item(item) for item in first_stage]

# Or even more concise:
first_stage = list(map(parse, raw_data))
second_stage = list(map(transform, first_stage))
```

## Automatic Exceptions

### Visualization Code

This rule automatically allows append loops when the list is used in visualization library calls. The following libraries are recognized:

- matplotlib
- seaborn  
- plotly
- bokeh
- altair
- pygal
- vispy
- mayavi

**Example (no violation):**

```python
import matplotlib.pyplot as plt

x_values = []
y_values = []
for point in data:
    x_values.append(point.x)
    y_values.append(point.y)
plt.plot(x_values, y_values)  # Lists are used in plt.plot, so no violation
```

For this exception to apply:
1. A visualization library must be imported
2. The list variable must actually be used in a visualization function call

If you import matplotlib but don't use the list in a visualization call, the rule will still flag the violation.

## When This Rule Doesn't Apply

This rule may produce false positives in cases where list mutation is **intentionally required**:

### 1. Queue/Stack Operations (BFS, DFS, etc.)

```python
queue = []  # noqa: DOEFF012
for node in initial_nodes:
    queue.append(node)

while queue:
    current = queue.pop(0)  # or queue.pop() for stack
    for neighbor in get_neighbors(current):
        queue.append(neighbor)
```

### 2. Dynamic/Iterative Algorithms

```python
# Fibonacci, dynamic programming, etc.
results = []  # noqa: DOEFF012
for i in range(n):
    if i < 2:
        results.append(1)
    else:
        results.append(results[-1] + results[-2])  # depends on previous results
```

### 3. Complex Conditional Logic with Side Effects

```python
data = []
for item in items:  # noqa: DOEFF012
    if complex_condition(item, external_state):
        data.append(transform(item))
        if should_stop(data):
            break
```

### 4. Collecting Results from Generators with State

```python
collected = []
for chunk in stream_processor():  # noqa: DOEFF012
    if chunk.is_valid:
        collected.append(chunk)
        update_progress(len(collected))
```

## How to Suppress

Add `# noqa: DOEFF012` to the **for-loop line** (not the list initialization):

```python
data = []
for item in items:  # noqa: DOEFF012
    data.append(process(item))
```

Or suppress for the entire file at the top:

```python
# noqa: DOEFF012
```

## Configuration

This rule has no configuration options.

## See Also

- [Python List Comprehensions](https://docs.python.org/3/tutorial/datastructures.html#list-comprehensions)
- [Functional Programming HOWTO](https://docs.python.org/3/howto/functional.html)

