# slog: a structured entry (a message and keyword data), a separate effect from Tell. slog_handler prints each
# entry on stderr; Listen(types=(SlogEffect,)) captures the entries as values. Backs the slog scene.
from doeff_core_effects import Listen, SlogEffect, Tell, slog
from doeff_core_effects.handlers import listen_handler, slog_handler, state, writer

from doeff import do, run


@do
def structured_logging():
    yield slog("User logged in", level="info", user_id=12345, ip="192.168.1.1")
    yield slog("High memory usage", level="warn", memory_mb=512, threshold_mb=400)
    return "logged"


print(run(slog_handler(structured_logging())), flush=True)


@do
def import_rows(row_ids):
    yield Tell("import started")
    for row_id in row_ids:
        yield slog("row imported", row_id=row_id)
    return len(row_ids)


@do
def audited_import():
    count, entries = yield Listen(import_rows([7, 8]), types=(SlogEffect,))
    return count, [(entry.msg, entry.kwargs) for entry in entries]


print(run(slog_handler(state()(writer(listen_handler(audited_import()))))))
