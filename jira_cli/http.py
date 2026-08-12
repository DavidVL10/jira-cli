"""HTTP transport: timeouts on every request, retries only where they are safe.

The rules this module enforces:

* Every request has a timeout. A hung connection must fail, not hang forever.
* 429 and 5xx are retried, because they mean "try again later".
* Any other 4xx is not retried, because the request itself is wrong and
  repeating it will fail the same way while wasting the server's time.
* Waits double each attempt and carry jitter, so a fleet of clients that all
  hit a rate limit at once does not retry in lockstep.
* Attempts are capped, so a persistent outage fails instead of looping.
"""

import json
import random
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

from .errors import HttpError, RetryLimitExceeded

#: Seconds to wait for the connection and for each read before giving up.
DEFAULT_TIMEOUT = 30.0


def is_retryable_status(status):
    """Is this status worth trying again?

    429 means we are rate limited: the request was fine, we just sent it too
    soon. 5xx means the server failed, which is often transient. Every other
    4xx (401 bad credentials, 403 forbidden, 404 missing) is our mistake and
    will fail identically no matter how many times we resend it.
    """
    return status == 429 or 500 <= status <= 599


@dataclass(frozen=True)
class RetryPolicy:
    """How hard to try, and how long to wait between attempts."""

    max_attempts: int = 4
    base_delay: float = 0.5
    max_delay: float = 20.0

    def delay_for(self, attempt, retry_after=None, rng=None):
        """Seconds to sleep after a failed attempt (1-based).

        Exponential backoff with full jitter: the ceiling doubles each attempt
        and the actual wait is drawn uniformly from zero to that ceiling.
        Without the jitter, clients that failed together would wake together
        and hammer the server in synchronised waves.
        """
        rng = random.random if rng is None else rng

        if retry_after is not None:
            # The server told us exactly how long to wait; that beats guessing.
            return min(retry_after, self.max_delay)

        ceiling = min(self.max_delay, self.base_delay * (2 ** (attempt - 1)))
        return rng() * ceiling


def parse_retry_after(value):
    """Read a Retry-After header expressed in seconds, or None if unusable."""
    if not value:
        return None
    try:
        seconds = float(value.strip())
    except (TypeError, ValueError):
        # The header also permits an HTTP date. We do not parse that form and
        # fall back to our own backoff rather than guessing wrong.
        return None
    return seconds if seconds >= 0 else None


def request_json(
    url,
    headers=None,
    timeout=DEFAULT_TIMEOUT,
    policy=None,
    sleep=time.sleep,
    rng=None,
    urlopen=None,
):
    """GET `url` and return the decoded JSON body.

    `sleep`, `rng` and `urlopen` are injectable so tests can run the retry
    logic without real waiting, real randomness, or a real network.

    Raises HttpError for a status we will not retry, and RetryLimitExceeded
    once a retryable failure has used up every attempt.
    """
    policy = RetryPolicy() if policy is None else policy
    urlopen = urllib.request.urlopen if urlopen is None else urlopen

    last_error = None

    for attempt in range(1, policy.max_attempts + 1):
        retry_after = None

        try:
            request = urllib.request.Request(url, headers=headers or {}, method="GET")
            # timeout applies to the connection and to each socket read, so a
            # server that accepts the connection then stalls still fails.
            with urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))

        except urllib.error.HTTPError as exc:
            # The server answered with an error status.
            body = _safe_read(exc)
            if not is_retryable_status(exc.code):
                raise HttpError(exc.code, url, body) from exc
            last_error = HttpError(exc.code, url, body)
            retry_after = parse_retry_after(exc.headers.get("Retry-After"))

        except (urllib.error.URLError, socket.timeout, TimeoutError) as exc:
            # No usable response at all: DNS failure, refused connection,
            # dropped socket, or our timeout firing. All worth another try.
            last_error = exc

        if attempt == policy.max_attempts:
            break

        sleep(policy.delay_for(attempt, retry_after=retry_after, rng=rng))

    raise RetryLimitExceeded(policy.max_attempts, last_error)


def _safe_read(exc):
    """Read an error response body without letting that read raise."""
    try:
        return exc.read().decode("utf-8", errors="replace")
    except Exception:
        return ""
