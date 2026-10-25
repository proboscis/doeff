# Compared with DI: the page's logging, once as one handler around the running example and once as one wrapper
# class per client. Prints both logs and counts the pieces each way for N kinds of calls and M concerns.
from doeff import Pass, do, handler, run

from ai_effects import Chat, Draw, canned_chat, canned_images, illustrated_summary

log = []


@do
def log_calls(effect: Chat | Draw, k):  # one handler for both services
    log.append(f"{type(effect).__name__.lower()} {effect.model}")
    return (yield Pass(effect, k))


program = handler(log_calls)(illustrated_summary("tides"))
print("effects:", run(handler(canned_chat)(handler(canned_images)(program))), log)


class CannedChat:
    def chat(self, model, prompt):
        return f"summary by {model}"


class CannedImages:
    def draw(self, model, prompt):
        return f"image by {model}"


di_log = []


class LoggedChat:
    def __init__(self, inner):
        self.inner = inner

    def chat(self, model, prompt):
        di_log.append(f"chat {model}")
        return self.inner.chat(model, prompt)


class LoggedImages:
    def __init__(self, inner):
        self.inner = inner

    def draw(self, model, prompt):
        di_log.append(f"draw {model}")
        return self.inner.draw(model, prompt)


llm, images = LoggedChat(CannedChat()), LoggedImages(CannedImages())
summary = llm.chat("gpt-4o", "Summarize tides")
picture = images.draw("imagen-3", summary)
print("di:", f"{summary} | {picture}", di_log)
for n, m in [(2, 1), (2, 3), (3, 3)]:
    print(f"{n} kinds of calls x {m} concerns: DI {n * m} wrapper classes, effects {n} effects + {m} handlers")
