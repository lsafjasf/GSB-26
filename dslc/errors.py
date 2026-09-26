"""Error types carrying source positions."""


class DSLError(Exception):
    """A single compile error with an optional line:col position."""

    def __init__(self, message, line=None, col=None):
        self.message = message
        self.line = line
        self.col = col
        super().__init__(str(self))

    def __str__(self):
        if self.line is not None:
            return "{}:{}: error: {}".format(self.line, self.col, self.message)
        return "error: {}".format(self.message)


class DSLErrorList(Exception):
    """All static-validation errors collected in one pass."""

    def __init__(self, errors):
        self.errors = list(errors)
        super().__init__(str(self))

    def __str__(self):
        return "\n".join(str(e) for e in self.errors)
