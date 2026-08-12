"""Tests for the Jira layer.

`search` takes a `request` callable so these run without a network. What is
under test is the URL we build, the paging loop, and how tolerantly we read a
response - not urllib, which `test_http` already covers.
"""

from urllib.parse import parse_qs, urlparse

import pytest

from jira_cli.client import (
    FIELDS,
    MAX_PAGE_SIZE,
    SEARCH_PATH,
    Issue,
    project_jql,
    search,
    search_project,
)
from jira_cli.config import Config
from jira_cli.errors import JiraCliError

CLOUD_ID = "00000000-1111-2222-3333-444444444444"


def make_config(**overrides):
    env = {
        "JIRA_SITE": "https://acme.atlassian.net",
        "JIRA_EMAIL": "dev@acme.test",
        "JIRA_API_TOKEN": "tok",
    }
    env.update(overrides)
    return Config.from_env(env)


class FakeRequest:
    """Replays scripted response payloads, recording every call."""

    def __init__(self, *pages):
        self.pages = list(pages)
        self.calls = []

    def __call__(self, url, headers=None):
        self.calls.append((url, headers))
        if not self.pages:
            raise AssertionError("more requests than the test scripted")
        return self.pages.pop(0)

    def query(self, index=0):
        """The parsed query string of the nth request."""
        return parse_qs(urlparse(self.calls[index][0]).query)


def api_issue(key="ABC-1", **fields):
    """One entry as it appears in a search response's `issues` list."""
    return {"key": key, "fields": fields}


def page(keys, token=None):
    payload = {"issues": [api_issue(key) for key in keys]}
    if token:
        payload["nextPageToken"] = token
    return payload


# --------------------------------------------------------------------------
# Issue.from_api
# --------------------------------------------------------------------------


def test_a_complete_issue_is_flattened():
    issue = Issue.from_api(
        api_issue(
            "ABC-7",
            summary="Fix login redirect loop",
            status={"name": "In Progress"},
            assignee={"displayName": "Dana Reed"},
            priority={"name": "High"},
            updated="2026-08-11T09:15:00.000-0700",
        )
    )
    assert issue.key == "ABC-7"
    assert issue.summary == "Fix login redirect loop"
    assert issue.status == "In Progress"
    assert issue.assignee == "Dana Reed"
    assert issue.priority == "High"
    assert issue.updated == "2026-08-11T09:15:00.000-0700"


@pytest.mark.parametrize("field", ["status", "assignee", "priority"])
def test_an_explicit_null_becomes_an_empty_string(field):
    # Jira sends null rather than omitting the key - an unassigned issue has
    # "assignee": null. A null sails past a dict.get default and would raise
    # on the next lookup, so this is the case that actually bites.
    issue = Issue.from_api(api_issue("ABC-1", **{field: None}))
    assert getattr(issue, field) == ""


@pytest.mark.parametrize("field", ["status", "assignee", "priority"])
def test_a_missing_nested_field_becomes_an_empty_string(field):
    issue = Issue.from_api(api_issue("ABC-1"))
    assert getattr(issue, field) == ""


def test_a_present_but_empty_nested_object_is_survivable():
    issue = Issue.from_api(api_issue("ABC-1", assignee={}))
    assert issue.assignee == ""


def test_an_issue_with_no_fields_at_all_still_parses():
    # Ask for fields the instance does not have and this is what comes back.
    issue = Issue.from_api({"key": "ABC-1"})
    assert issue == Issue(key="ABC-1")


def test_a_null_fields_object_is_survivable():
    assert Issue.from_api({"key": "ABC-1", "fields": None}).summary == ""


def test_a_missing_key_becomes_an_empty_string_rather_than_raising():
    assert Issue.from_api({}).key == ""


def test_summary_whitespace_is_trimmed():
    assert Issue.from_api(api_issue("ABC-1", summary="  padded  ")).summary == "padded"


def test_unknown_fields_are_ignored():
    # Jira adds fields over time; that must not break parsing.
    issue = Issue.from_api(api_issue("ABC-1", summary="s", customfield_10042={"x": 1}))
    assert issue.summary == "s"


def test_issues_compare_by_value():
    assert Issue.from_api(api_issue("ABC-1")) == Issue.from_api(api_issue("ABC-1"))


# --------------------------------------------------------------------------
# project_jql
# --------------------------------------------------------------------------


def test_jql_orders_by_most_recently_updated():
    assert project_jql("ABC") == "project = ABC ORDER BY updated DESC"


def test_project_keys_are_upper_cased():
    assert project_jql("abc") == "project = ABC ORDER BY updated DESC"


def test_surrounding_whitespace_is_ignored():
    assert project_jql("  abc\n") == "project = ABC ORDER BY updated DESC"


def test_order_can_be_overridden():
    assert project_jql("ABC", order_by="created ASC").endswith("ORDER BY created ASC")


@pytest.mark.parametrize("key", ["ABC1", "A", "with_underscore", "A1_B2"])
def test_legitimate_keys_are_accepted(key):
    assert project_jql(key).startswith("project = ")


@pytest.mark.parametrize(
    "key",
    [
        "",
        "   ",
        "1ABC",  # must start with a letter
        "AB-C",  # hyphen is an issue key, not a project key
        "AB C",
        "AB;DROP",
        'ABC" OR project = "SECRET',  # the injection this guard exists for
        "ABC OR 1=1",
        "*",
    ],
)
def test_anything_that_is_not_a_bare_project_key_is_rejected(key):
    with pytest.raises(JiraCliError):
        project_jql(key)


def test_the_rejection_names_the_offending_key():
    with pytest.raises(JiraCliError) as caught:
        project_jql("AB C")
    assert "AB C" in str(caught.value)


# --------------------------------------------------------------------------
# search - the request we build
# --------------------------------------------------------------------------


def test_search_hits_the_search_endpoint():
    request = FakeRequest(page(["ABC-1"]))
    search(make_config(), "project = ABC", request=request)
    assert urlparse(request.calls[0][0]).path == SEARCH_PATH


def test_search_sends_the_jql_and_the_fields_we_render():
    request = FakeRequest(page(["ABC-1"]))
    search(make_config(), "project = ABC", request=request)
    query = request.query()
    assert query["jql"] == ["project = ABC"]
    assert query["fields"] == [",".join(FIELDS)]


def test_search_authenticates_every_request():
    config = make_config()
    request = FakeRequest(page(["ABC-1"]))
    search(config, "project = ABC", request=request)
    _, headers = request.calls[0]
    assert headers["Authorization"] == config.auth_header()
    assert headers["Accept"] == "application/json"


def test_a_scoped_token_sends_requests_to_the_gateway():
    config = make_config(JIRA_CLOUD_ID=CLOUD_ID)
    request = FakeRequest(page(["ABC-1"]))
    search(config, "project = ABC", request=request)
    url = request.calls[0][0]
    assert url.startswith("https://api.atlassian.com/ex/jira/" + CLOUD_ID + SEARCH_PATH)


def test_the_page_size_asked_for_is_the_limit_when_it_is_small():
    request = FakeRequest(page(["ABC-1"]))
    search(make_config(), "project = ABC", limit=5, request=request)
    assert request.query()["maxResults"] == ["5"]


def test_the_page_size_is_capped_at_what_jira_will_return():
    request = FakeRequest(page(["ABC-%d" % n for n in range(MAX_PAGE_SIZE)]))
    search(make_config(), "project = ABC", limit=1000, request=request)
    assert request.query()["maxResults"] == [str(MAX_PAGE_SIZE)]


def test_the_first_request_carries_no_page_token():
    request = FakeRequest(page(["ABC-1"]))
    search(make_config(), "project = ABC", request=request)
    assert "nextPageToken" not in request.query()


# --------------------------------------------------------------------------
# search - reading the response
# --------------------------------------------------------------------------


def test_results_come_back_as_issues():
    request = FakeRequest(page(["ABC-1", "ABC-2"]))
    issues = search(make_config(), "project = ABC", request=request)
    assert [issue.key for issue in issues] == ["ABC-1", "ABC-2"]
    assert all(isinstance(issue, Issue) for issue in issues)


def test_no_matches_is_an_empty_list_not_an_error():
    request = FakeRequest({"issues": []})
    assert search(make_config(), "project = ABC", request=request) == []


def test_a_response_without_an_issues_key_yields_nothing():
    # Defensive: a shape we did not expect must not raise a KeyError.
    request = FakeRequest({})
    assert search(make_config(), "project = ABC", request=request) == []


def test_a_null_issues_list_yields_nothing():
    request = FakeRequest({"issues": None})
    assert search(make_config(), "project = ABC", request=request) == []


# --------------------------------------------------------------------------
# search - paging
# --------------------------------------------------------------------------


def test_a_single_page_makes_a_single_request():
    request = FakeRequest(page(["ABC-1"]))
    search(make_config(), "project = ABC", limit=50, request=request)
    assert len(request.calls) == 1


def test_paging_follows_the_token_until_the_results_run_out():
    request = FakeRequest(
        page(["ABC-1", "ABC-2"], token="page2"),
        page(["ABC-3"]),
    )
    issues = search(make_config(), "project = ABC", limit=50, request=request)
    assert [issue.key for issue in issues] == ["ABC-1", "ABC-2", "ABC-3"]
    assert len(request.calls) == 2


def test_the_token_from_one_page_is_sent_with_the_next():
    request = FakeRequest(page(["ABC-1"], token="opaque-token"), page(["ABC-2"]))
    search(make_config(), "project = ABC", limit=50, request=request)
    assert request.query(1)["nextPageToken"] == ["opaque-token"]


def test_later_pages_ask_only_for_what_is_still_missing():
    request = FakeRequest(page(["ABC-1", "ABC-2"], token="t"), page(["ABC-3"]))
    search(make_config(), "project = ABC", limit=5, request=request)
    assert request.query(0)["maxResults"] == ["5"]
    assert request.query(1)["maxResults"] == ["3"]  # 5 asked for, 2 already held


def test_paging_stops_once_the_limit_is_reached():
    # A full page plus a token, but the caller only wanted 2.
    request = FakeRequest(page(["ABC-1", "ABC-2"], token="t"))
    issues = search(make_config(), "project = ABC", limit=2, request=request)
    assert len(issues) == 2
    assert len(request.calls) == 1  # no request we would throw away


def test_an_over_full_page_is_truncated_to_the_limit():
    # Jira is free to return more than we asked for; the caller's limit wins.
    request = FakeRequest(page(["ABC-1", "ABC-2", "ABC-3"]))
    issues = search(make_config(), "project = ABC", limit=2, request=request)
    assert [issue.key for issue in issues] == ["ABC-1", "ABC-2"]


def test_is_last_ends_paging_even_when_a_token_is_present():
    # The server telling us this is the end beats inferring it. Observed
    # behaviour is that Jira omits the token on a final page, so this is
    # belt-and-braces: if a token ever does accompany a last page, isLast
    # stops us from spending a request to discover there is nothing left.
    request = FakeRequest({"issues": [api_issue("ABC-1")], "nextPageToken": "t", "isLast": True})
    issues = search(make_config(), "project = ABC", limit=50, request=request)
    assert [issue.key for issue in issues] == ["ABC-1"]
    assert len(request.calls) == 1


def test_is_last_false_keeps_paging():
    request = FakeRequest(
        {"issues": [api_issue("ABC-1")], "nextPageToken": "t", "isLast": False},
        {"issues": [api_issue("ABC-2")], "isLast": True},
    )
    issues = search(make_config(), "project = ABC", limit=50, request=request)
    assert [issue.key for issue in issues] == ["ABC-1", "ABC-2"]
    assert len(request.calls) == 2


def test_paging_still_works_without_is_last():
    # Older responses omit it; the token remains a sufficient signal.
    request = FakeRequest(page(["ABC-1"], token="t"), page(["ABC-2"]))
    issues = search(make_config(), "project = ABC", limit=50, request=request)
    assert [issue.key for issue in issues] == ["ABC-1", "ABC-2"]


def test_an_empty_page_ends_paging_even_with_a_token():
    # Otherwise a server that always returns a token would loop forever.
    request = FakeRequest({"issues": [], "nextPageToken": "never-ends"})
    assert search(make_config(), "project = ABC", limit=50, request=request) == []
    assert len(request.calls) == 1


@pytest.mark.parametrize("limit", [0, -1])
def test_a_pointless_limit_makes_no_request_at_all(limit):
    request = FakeRequest()
    assert search(make_config(), "project = ABC", limit=limit, request=request) == []
    assert request.calls == []


# --------------------------------------------------------------------------
# search_project
# --------------------------------------------------------------------------


def test_search_project_searches_that_project():
    request = FakeRequest(page(["ABC-1"]))
    search_project(make_config(), "abc", request=request)
    assert request.query()["jql"] == ["project = ABC ORDER BY updated DESC"]


def test_search_project_passes_the_limit_through():
    request = FakeRequest(page(["ABC-1"]))
    search_project(make_config(), "ABC", limit=7, request=request)
    assert request.query()["maxResults"] == ["7"]


def test_an_invalid_project_fails_before_anything_is_sent():
    request = FakeRequest()
    with pytest.raises(JiraCliError):
        search_project(make_config(), "AB C", request=request)
    assert request.calls == []
