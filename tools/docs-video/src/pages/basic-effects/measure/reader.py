# Reader: Ask reads the env the reader handler holds; a missing key raises MissingEnvKeyError; Local runs a
# sub-program over an overridden env and then the env is back. Backs the Reader and Local scenes of the video.
from doeff_core_effects import Ask, Local, ReadWriterLog, Tell, local_handler, reader, state, writer

from doeff import do, run

ENV = {"database_url": "postgresql://localhost/mydb", "timeout": 30}


@do
def connect():
    url = yield Ask("database_url")
    timeout = yield Ask("timeout")
    return f"{url} ({timeout} s)"


@do
def connect_typo():
    return (yield Ask("timout"))


print("connect:", run(reader(env=ENV)(connect())))
try:
    run(reader(env=ENV)(connect_typo()))
except KeyError as error:
    print(f"{type(error).__name__}: {error}")


@do
def fetch_data():
    url = yield Ask("api_url")
    yield Tell(f"Fetching from {url}")
    return f"data from {url}"


@do
def with_custom_config():
    url1 = yield Ask("api_url")
    yield Tell(f"Default URL: {url1}")
    result = yield Local({"api_url": "https://staging.example.com"}, fetch_data())
    url2 = yield Ask("api_url")
    yield Tell(f"Back to: {url2}")
    return result, (yield ReadWriterLog())


prog = local_handler(with_custom_config())
prog = reader(env={"api_url": "https://api.example.com"})(prog)
result, log = run(state()(writer(prog)))
print("local result:", result)
for line in log:
    print("log:", line)
