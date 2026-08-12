"""Credentials and connection settings, read from the environment.

The API token is never read from a file the repository tracks and never written
to logs or error messages. It lives in the environment and nowhere else.
"""

import base64
import os
from dataclasses import dataclass

from .errors import ConfigError

SITE_VAR = "JIRA_SITE"
EMAIL_VAR = "JIRA_EMAIL"
TOKEN_VAR = "JIRA_API_TOKEN"


@dataclass(frozen=True)
class Config:
    """Everything needed to talk to a Jira site."""

    site: str
    email: str
    token: str

    @classmethod
    def from_env(cls, environ=None):
        """Build a Config from environment variables.

        Passing `environ` explicitly keeps tests from touching the real
        environment. Raises ConfigError listing every missing variable at once,
        so the user fixes them in one pass rather than one error at a time.
        """
        env = os.environ if environ is None else environ

        # Strip before testing: a variable holding only whitespace is a
        # mistake, not a value, and must fail here with a clear message rather
        # than later as a puzzling 401 from the server.
        missing = [
            name
            for name in (SITE_VAR, EMAIL_VAR, TOKEN_VAR)
            if not (env.get(name) or "").strip()
        ]
        if missing:
            raise ConfigError(
                "missing required environment variable(s): {}\n"
                "Set them in your shell, for example:\n"
                '  export {}="https://your-org.atlassian.net"\n'
                '  export {}="you@example.com"\n'
                '  export {}="your-api-token"\n'
                "Create a token at "
                "https://id.atlassian.com/manage-profile/security/api-tokens".format(
                    ", ".join(missing), SITE_VAR, EMAIL_VAR, TOKEN_VAR
                )
            )

        return cls(
            site=_normalize_site(env[SITE_VAR]),
            email=env[EMAIL_VAR].strip(),
            token=env[TOKEN_VAR].strip(),
        )

    def auth_header(self):
        """Return the Basic auth header value for these credentials.

        Jira Cloud authenticates with HTTP Basic auth where the username is the
        account email and the password is the API token.
        """
        raw = "{}:{}".format(self.email, self.token).encode("utf-8")
        return "Basic " + base64.b64encode(raw).decode("ascii")

    def url(self, path):
        """Join an API path onto the site root."""
        return "{}/{}".format(self.site, path.lstrip("/"))

    def __repr__(self):
        # Never let the token reach a log line, a traceback, or a debugger dump.
        return "Config(site={!r}, email={!r}, token=<redacted>)".format(self.site, self.email)

    __str__ = __repr__


def _normalize_site(value):
    """Validate the site URL and strip the trailing slash."""
    site = value.strip().rstrip("/")
    if not site.startswith(("http://", "https://")):
        raise ConfigError(
            "{} must start with https://, got {!r}".format(SITE_VAR, value)
        )
    return site
