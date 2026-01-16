from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, TypeVar

if TYPE_CHECKING:
    from doeff.program import Program
    from doeff.cesk.types import Store, Environment

T = TypeVar("T")


class Runtime(ABC):
    @abstractmethod
    def run(
        self,
        program: Program[T],
        env: Environment | dict | None = None,
        store: Store | None = None,
    ) -> T:
        pass


__all__ = ["Runtime"]
