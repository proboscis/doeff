# plain_app.py
import infra


def welcome(user_id: int) -> str:
    user = infra.load_user(user_id)
    body = f"Welcome, {user.name}!"
    infra.send_mail(user.email, body)
    return body
