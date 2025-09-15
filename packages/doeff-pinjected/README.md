# doeff-pinjected

Pinjected integration for doeff effects system.

This package provides a bridge between doeff's free monad implementation
and the pinjected dependency injection framework.

## Features

- Convert doeff Programs to pinjected IProxy objects
- Seamless integration with pinjected's AsyncResolver
- Support for dependency injection via Dep effect

## Installation

```bash
pip install doeff-pinjected
```

## Usage

```python
from doeff import do, Dep
from doeff_pinjected import program_to_injected

@do
def my_program():
    service = yield Dep("service")
    result = yield service.process()
    return result

# Convert to pinjected
injected_func = program_to_injected(my_program)
```