
from typing import Generic, TypeVar

T = TypeVar("T")

@dataclass
class DoeffIterator(Generic[T],Protocol):
    def has_next(self)->Program[bool]:
        pass
    def next(self)->Program[T]:
        pass

"""
TODO 
"""
