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

#: Optional. Set it when the token carries scopes: those must be presented to
#: the platform gateway rather than to the site, and the gateway identifies the
#: instance by cloud id rather than by hostname. An unscoped token works
#: against the site directly and needs none of this.
CLOUD_ID_VAR = "JIRA_CLOUD_ID"

#: Where scoped tokens are accepted.
GATEWAY = "https://api.atlassian.com"


@dataclass(frozen=True)
class Config:
    """Everything needed to talk to a Jira site."""

    site: str
    email: str
    token: str
    cloud_id: str = ""

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
            cloud_id=(env.get(CLOUD_ID_VAR) or "").strip(),
        )

    def auth_header(self):
        """Return the Basic auth header value for these credentials.

        Jira Cloud authenticates with HTTP Basic auth where the username is the
        account email and the password is the API token.
        """
        raw = "{}:{}".format(self.email, self.token).encode("utf-8")
        return "Basic " + base64.b64encode(raw).decode("ascii")

    def url(self, path):
        """Build the full URL for an API path.

        A scoped token is only accepted at the gateway, addressed by cloud id:

            https://api.atlassian.com/ex/jira/<cloud id>/rest/api/3/...

        An unscoped token goes to the site itself:

            https://your-org.atlassian.net/rest/api/3/...

        The path after /rest/api/3 is identical either way, which is why this
        is the only method that has to know the difference.
        """
        root = self.api_root()
        return "{}/{}".format(root, path.lstrip("/"))

    def api_root(self):
        """The base every API path hangs off, gateway or site."""
        if self.cloud_id:
            return "{}/ex/jira/{}".format(GATEWAY, self.cloud_id)
        return self.site

    def __repr__(self):
        # Never let the token reach a log line, a traceback, or a debugger dump.
        # The cloud id is an instance identifier, not a credential, and is worth
        # showing: a wrong one is otherwise invisible.
        return "Config(site={!r}, email={!r}, cloud_id={!r}, token=<redacted>)".format(
            self.site, self.email, self.cloud_id
        )

    __str__ = __repr__


def _normalize_site(value):
    """Validate the site URL and strip the trailing slash."""
    site = value.strip().rstrip("/")
    if not site.startswith(("http://", "https://")):
        raise ConfigError(
            "{} must start with https://, got {!r}".format(SITE_VAR, value)
        )
    return site
