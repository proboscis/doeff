# production.py
import atexit
import sqlite3

from doeff import Resume, handler, run, with_handlers

from infra import User
from welcome import LoadUser, SendMail, welcome

db = sqlite3.connect(":memory:")
atexit.register(db.close)
db.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, email TEXT)")
db.execute("INSERT INTO users VALUES (1, 'Grace', 'grace@example.com')")


@handler
def db_load_user(effect: LoadUser, k):
    name, email = db.execute(
        "SELECT name, email FROM users WHERE id = ?", (effect.user_id,)
    ).fetchone()
    return (yield Resume(k, User(name, email)))


@handler
def smtp_send_mail(effect: SendMail, k):
    print(f"SMTP -> {effect.to}: {effect.body}")  # stand-in for smtplib
    return (yield Resume(k, None))


if __name__ == "__main__":
    print(run(with_handlers([db_load_user, smtp_send_mail], welcome(1))))
