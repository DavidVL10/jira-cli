"""Exception types raised by jira-cli."""


class JiraCliError(Exception):
    """Base class for every error this tool raises deliberately.

    Catching this in the CLI layer lets us print a clean message instead of a
    traceback, while genuine bugs still surface as tracebacks.
    """


class ConfigError(JiraCliError):
    """Configuration is missing or malformed, usually an unset environment variable."""


class HttpError(JiraCliError):
    """The server returned a status we are not going to retry."""

    def __init__(self, status, url, body=""):
        self.status = status
        self.url = url
        self.body = body
        super().__init__(self._message())

    def _message(self):
        snippet = " ".join(self.body.split())
        if len(snippet) > 300:
            snippet = snippet[:300] + "..."
        base = "HTTP {} from {}".format(self.status, self.url)
        return "{}: {}".format(base, snippet) if snippet else base


class RetryLimitExceeded(JiraCliError):
    """Every attempt failed with a retryable condition, and we ran out of attempts."""

    def __init__(self, attempts, last_error):
        self.attempts = attempts
        self.last_error = last_error
        super().__init__(
            "gave up after {} attempt(s); last failure: {}".format(attempts, last_error)
        )
