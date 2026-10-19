"""Optional localization metadata, preserving plain English strings for older clients."""

class Message(str):
    def __new__(cls, code: str, text: str, **args):
        value = super().__new__(cls, text)
        value.code, value.args = code, args
        return value


def descriptor(text):
    return {"code": text.code, "args": text.args} if isinstance(text, Message) else None
