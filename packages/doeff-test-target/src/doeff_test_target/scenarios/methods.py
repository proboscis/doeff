from doeff import do

from ..core.alpha import helper_alpha
from ..core.beta import helper_beta


class MethodPrograms:
    @do
    def instance_method(self):
        value = yield helper_alpha()
        return value

    @classmethod
    @do
    def class_method(cls):
        value = yield helper_beta()
        return value


@do
def run_instance_method():
    result = yield MethodPrograms.instance_method(MethodPrograms())
    return result


@do
def run_class_method():
    result = yield MethodPrograms.class_method()
    return result
