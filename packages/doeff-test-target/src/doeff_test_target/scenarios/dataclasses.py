from dataclasses import dataclass

from doeff import do

from ..core.alpha import helper_alpha


@dataclass
class DataclassHolder:
    program: object


def dataclass_program_runner():
    return DataclassHolder(program=helper_alpha())


@do
def dataclass_program():
    holder = dataclass_program_runner()
    value = yield holder.program
    return value
