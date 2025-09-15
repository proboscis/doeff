from typing import TypeVar, Generator

from pinjected import AsyncResolver, Injected, IProxy

from doeff.core import Program, RunResult

T = TypeVar("T")

def program_to_injected(prog: Program[T]) -> Injected[T]: ...
def program_to_iproxy(prog: Program[T]) -> IProxy[T]: ...
def program_to_injected_result(prog: Program[T]) -> Injected[RunResult[T]]: ...
def program_to_iproxy_result(prog: Program[T]) -> IProxy[RunResult[T]]: ...

# Additional symbols:
def _create_dep_aware_generator(
    prog: Program, resolver: AsyncResolver
) -> Generator: ...
