# The traceback of a caught failure stays on the exception inside Err: the frames down to where it was raised.
import traceback

from doeff import Err, Try, do, run
from doeff_core_effects import try_handler


@do
def load(path):
    raise FileNotFoundError(path)


@do
def boom():
    return (yield load("config.toml"))


@do
def app():
    return (yield Try(boom()))


result = run(try_handler(app()))
print("result:", repr(result), "is Err:", isinstance(result, Err))
print("captured_traceback:", result.captured_traceback)
print("frames:", [frame.name for frame in traceback.extract_tb(result.error.__traceback__)])
