"""Tests for the HTTP transport.

Every test here runs offline and without real sleeping. `request_json` accepts
`urlopen`, `sleep` and `rng` precisely so the retry logic can be driven through
its failure paths deterministically; these tests are what that seam is for.
"""

import dataclasses
import email.message
import io
import json
import urllib.error

import pytest

from jira_cli.errors import HttpError, RetryLimitExceeded
from jira_cli.http import (
    DEFAULT_TIMEOUT,
    RetryPolicy,
    is_retryable_status,
    parse_retry_after,
    request_json,
)

URL = "https://example.test/rest/api/3/myself"

# Deterministic stand-ins for random.random(): the top and bottom of its range.
FULL_JITTER = lambda: 1.0  # noqa: E731 - draw the largest delay the ceiling allows
NO_JITTER = lambda: 0.0  # noqa: E731 - draw the smallest


def make_http_error(status, body=b"", headers=None):
    """Build a real urllib HTTPError the way urllib itself would."""
    hdrs = email.message.Message()
    for name, value in (headers or {}).items():
        hdrs[name] = value
    return urllib.error.HTTPError(URL, status, "synthetic", hdrs, io.BytesIO(body))


class FakeResponse:
    """The context-manager response object that urlopen hands back."""

    def __init__(self, body):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class ExplodingStream(io.BytesIO):
    """A response stream that fails partway through, like a reset connection."""

    def read(self, *_):
        raise OSError("connection reset while reading the error body")


class FakeUrlopen:
    """Replays a scripted list of outcomes, one per call, recording each call.

    An outcome is either bytes (a successful body) or an exception instance
    (raised). Running past the end of the script is a test bug, not a
    behaviour under test, so it fails loudly.
    """

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def __call__(self, request, timeout=None):
        self.calls.append((request, timeout))
        if not self.outcomes:
            raise AssertionError("urlopen called more times than the test scripted")
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return FakeResponse(outcome)


class RecordingSleep:
    """Captures the delays that would have been slept, instantly."""

    def __init__(self):
        self.delays = []

    def __call__(self, seconds):
        self.delays.append(seconds)


def body_of(payload):
    return json.dumps(payload).encode("utf-8")


# --------------------------------------------------------------------------
# is_retryable_status
# --------------------------------------------------------------------------


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504, 599])
def test_retryable_statuses_are_worth_another_attempt(status):
    assert is_retryable_status(status) is True


@pytest.mark.parametrize("status", [400, 401, 403, 404, 409, 422, 418])
def test_client_errors_other_than_429_are_not_retried(status):
    # Resending a malformed or unauthorized request reproduces the same
    # failure; retrying only wastes the server's time and ours.
    assert is_retryable_status(status) is False


@pytest.mark.parametrize("status", [200, 201, 204, 301, 304])
def test_non_error_statuses_are_not_retryable(status):
    assert is_retryable_status(status) is False


def test_600_is_outside_the_5xx_range():
    # The check is an inclusive range, not "anything >= 500".
    assert is_retryable_status(600) is False


# --------------------------------------------------------------------------
# parse_retry_after
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [("5", 5.0), (" 5 ", 5.0), ("0", 0.0), ("2.5", 2.5), ("120", 120.0)],
)
def test_retry_after_reads_delta_seconds(raw, expected):
    assert parse_retry_after(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "not-a-number",
        "-1",
        "Wed, 21 Oct 2015 07:28:00 GMT",  # the HTTP-date form we deliberately skip
    ],
)
def test_unusable_retry_after_falls_back_to_our_own_backoff(raw):
    assert parse_retry_after(raw) is None


def test_zero_retry_after_is_a_value_not_an_absence():
    # 0 is falsy but meaningful: "retry immediately". It must survive as 0.0,
    # not collapse into None and silently become an exponential-backoff wait.
    assert parse_retry_after("0") == 0.0


# --------------------------------------------------------------------------
# RetryPolicy.delay_for
# --------------------------------------------------------------------------


def test_ceiling_doubles_each_attempt():
    policy = RetryPolicy(base_delay=0.5, max_delay=20.0)
    ceilings = [policy.delay_for(n, rng=FULL_JITTER) for n in (1, 2, 3, 4, 5)]
    assert ceilings == [0.5, 1.0, 2.0, 4.0, 8.0]


def test_jitter_can_draw_anywhere_below_the_ceiling():
    policy = RetryPolicy(base_delay=0.5)
    assert policy.delay_for(3, rng=NO_JITTER) == 0.0
    assert policy.delay_for(3, rng=lambda: 0.25) == 0.5
    assert policy.delay_for(3, rng=FULL_JITTER) == 2.0


def test_ceiling_is_capped_so_backoff_does_not_grow_without_bound():
    policy = RetryPolicy(base_delay=0.5, max_delay=20.0)
    # 2 ** 19 seconds without the cap.
    assert policy.delay_for(20, rng=FULL_JITTER) == 20.0


def test_default_rng_stays_within_the_ceiling():
    # Exercises the real random.random default rather than an injected one.
    policy = RetryPolicy(base_delay=0.5)
    for _ in range(100):
        assert 0.0 <= policy.delay_for(1) < 0.5


def test_server_instruction_beats_our_guess():
    policy = RetryPolicy(base_delay=0.5)
    # Without retry_after this attempt would wait at most 0.5s.
    assert policy.delay_for(1, retry_after=9.0, rng=FULL_JITTER) == 9.0


def test_retry_after_is_used_verbatim_without_jitter():
    policy = RetryPolicy()
    assert policy.delay_for(1, retry_after=3.0, rng=NO_JITTER) == 3.0


def test_absurd_retry_after_is_clamped():
    # A buggy or hostile header must not park the process for an hour.
    policy = RetryPolicy(max_delay=20.0)
    assert policy.delay_for(1, retry_after=3600.0, rng=FULL_JITTER) == 20.0


def test_retry_after_of_zero_means_no_wait():
    policy = RetryPolicy(base_delay=0.5)
    assert policy.delay_for(1, retry_after=0, rng=FULL_JITTER) == 0.0


def test_policy_is_immutable():
    # A policy read across a multi-second retry loop must not be retunable
    # mid-flight. setattr rather than plain assignment keeps type checkers from
    # flagging the deliberate violation.
    policy = RetryPolicy()
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(policy, "max_attempts", 99)


# --------------------------------------------------------------------------
# request_json - success path
# --------------------------------------------------------------------------


def test_successful_request_returns_decoded_json():
    urlopen = FakeUrlopen(body_of({"key": "ABC-1", "count": 2}))
    assert request_json(URL, urlopen=urlopen) == {"key": "ABC-1", "count": 2}


def test_success_on_first_attempt_never_sleeps():
    sleep = RecordingSleep()
    request_json(URL, urlopen=FakeUrlopen(body_of({})), sleep=sleep)
    assert sleep.delays == []


def test_every_request_carries_a_timeout():
    # The central promise of this module: a hung connection fails, not hangs.
    urlopen = FakeUrlopen(body_of({}))
    request_json(URL, urlopen=urlopen)
    _, timeout = urlopen.calls[0]
    assert timeout == DEFAULT_TIMEOUT


def test_caller_timeout_is_forwarded():
    urlopen = FakeUrlopen(body_of({}))
    request_json(URL, timeout=1.5, urlopen=urlopen)
    assert urlopen.calls[0][1] == 1.5


def test_headers_and_method_reach_the_request():
    urlopen = FakeUrlopen(body_of({}))
    request_json(URL, headers={"Authorization": "Basic abc"}, urlopen=urlopen)
    request = urlopen.calls[0][0]
    assert request.get_header("Authorization") == "Basic abc"
    assert request.get_method() == "GET"
    assert request.full_url == URL


def test_utf8_bodies_decode_correctly():
    urlopen = FakeUrlopen(body_of({"summary": "Fix café ordering ✓"}))
    assert request_json(URL, urlopen=urlopen)["summary"] == "Fix café ordering ✓"


# --------------------------------------------------------------------------
# request_json - failures we do not retry
# --------------------------------------------------------------------------


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_non_retryable_status_fails_immediately(status):
    sleep = RecordingSleep()
    urlopen = FakeUrlopen(make_http_error(status, b"nope"))

    with pytest.raises(HttpError) as caught:
        request_json(URL, urlopen=urlopen, sleep=sleep)

    assert caught.value.status == status
    assert caught.value.url == URL
    assert sleep.delays == []  # no waiting on a request that cannot succeed
    assert len(urlopen.calls) == 1  # and no second attempt


def test_error_body_is_carried_into_the_exception():
    urlopen = FakeUrlopen(make_http_error(404, b'{"errorMessages":["Issue not found"]}'))
    with pytest.raises(HttpError) as caught:
        request_json(URL, urlopen=urlopen)
    assert "Issue not found" in caught.value.body
    assert "Issue not found" in str(caught.value)


def test_unreadable_error_body_does_not_mask_the_real_error():
    # A body read can fail on its own - a connection reset partway through,
    # say. That must not replace the HTTP error with a confusing one.
    broken = urllib.error.HTTPError(
        URL, 403, "synthetic", email.message.Message(), ExplodingStream()
    )

    with pytest.raises(HttpError) as caught:
        request_json(URL, urlopen=FakeUrlopen(broken))

    assert caught.value.status == 403
    assert caught.value.body == ""


def test_original_error_is_chained_for_debugging():
    original = make_http_error(401, b"bad token")
    with pytest.raises(HttpError) as caught:
        request_json(URL, urlopen=FakeUrlopen(original))
    assert caught.value.__cause__ is original


# --------------------------------------------------------------------------
# request_json - failures we do retry
# --------------------------------------------------------------------------


@pytest.mark.parametrize("status", [429, 500, 503])
def test_retryable_status_is_retried_then_succeeds(status):
    urlopen = FakeUrlopen(make_http_error(status), body_of({"ok": True}))
    sleep = RecordingSleep()

    assert request_json(URL, urlopen=urlopen, sleep=sleep, rng=FULL_JITTER) == {"ok": True}
    assert len(urlopen.calls) == 2
    assert sleep.delays == [0.5]


@pytest.mark.parametrize(
    "failure",
    [
        urllib.error.URLError("name resolution failed"),
        urllib.error.URLError(ConnectionRefusedError()),
        TimeoutError(),
    ],
)
def test_transport_failures_are_retried(failure):
    # No usable response at all: DNS, refused connection, or our own timeout.
    urlopen = FakeUrlopen(failure, body_of({"ok": True}))
    assert request_json(URL, urlopen=urlopen, sleep=RecordingSleep()) == {"ok": True}
    assert len(urlopen.calls) == 2


def test_delays_follow_exponential_backoff_across_attempts():
    policy = RetryPolicy(max_attempts=4, base_delay=0.5, max_delay=20.0)
    urlopen = FakeUrlopen(*[make_http_error(503) for _ in range(4)])
    sleep = RecordingSleep()

    with pytest.raises(RetryLimitExceeded):
        request_json(URL, urlopen=urlopen, policy=policy, sleep=sleep, rng=FULL_JITTER)

    assert sleep.delays == [0.5, 1.0, 2.0]


def test_no_pointless_sleep_after_the_final_attempt():
    policy = RetryPolicy(max_attempts=3)
    urlopen = FakeUrlopen(*[make_http_error(500) for _ in range(3)])
    sleep = RecordingSleep()

    with pytest.raises(RetryLimitExceeded):
        request_json(URL, urlopen=urlopen, policy=policy, sleep=sleep, rng=FULL_JITTER)

    assert len(urlopen.calls) == 3
    assert len(sleep.delays) == 2  # one fewer wait than attempts


def test_retry_after_header_overrides_computed_backoff():
    policy = RetryPolicy(max_attempts=2, base_delay=0.5)
    urlopen = FakeUrlopen(
        make_http_error(429, headers={"Retry-After": "7"}),
        body_of({"ok": True}),
    )
    sleep = RecordingSleep()

    request_json(URL, urlopen=urlopen, policy=policy, sleep=sleep, rng=FULL_JITTER)
    assert sleep.delays == [7.0]


def test_retry_after_does_not_leak_into_later_attempts():
    # Attempt 1 is told to wait 7s; attempt 2 gets no header and must fall
    # back to its own ceiling rather than reusing 7s.
    policy = RetryPolicy(max_attempts=3, base_delay=0.5)
    urlopen = FakeUrlopen(
        make_http_error(429, headers={"Retry-After": "7"}),
        make_http_error(429),
        body_of({"ok": True}),
    )
    sleep = RecordingSleep()

    request_json(URL, urlopen=urlopen, policy=policy, sleep=sleep, rng=FULL_JITTER)
    assert sleep.delays == [7.0, 1.0]


def test_giving_up_reports_the_attempt_count_and_last_failure():
    policy = RetryPolicy(max_attempts=3)
    urlopen = FakeUrlopen(*[make_http_error(503, b"overloaded") for _ in range(3)])

    with pytest.raises(RetryLimitExceeded) as caught:
        request_json(URL, urlopen=urlopen, policy=policy, sleep=RecordingSleep())

    assert caught.value.attempts == 3
    assert isinstance(caught.value.last_error, HttpError)
    assert caught.value.last_error.status == 503
    assert "3 attempt(s)" in str(caught.value)


def test_a_single_attempt_policy_never_sleeps():
    policy = RetryPolicy(max_attempts=1)
    sleep = RecordingSleep()

    with pytest.raises(RetryLimitExceeded):
        request_json(URL, urlopen=FakeUrlopen(make_http_error(503)), policy=policy, sleep=sleep)

    assert sleep.delays == []


def test_transport_failure_is_preserved_as_the_last_error():
    policy = RetryPolicy(max_attempts=2)
    failure = urllib.error.URLError("connection reset")
    urlopen = FakeUrlopen(failure, failure)

    with pytest.raises(RetryLimitExceeded) as caught:
        request_json(URL, urlopen=urlopen, policy=policy, sleep=RecordingSleep())

    assert caught.value.last_error is failure
