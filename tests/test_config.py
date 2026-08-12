"""Tests for credential loading.

`from_env` takes an `environ` mapping so these tests never read or mutate the
real process environment.
"""

import base64
import dataclasses

import pytest

from jira_cli.config import EMAIL_VAR, SITE_VAR, TOKEN_VAR, Config
from jira_cli.errors import ConfigError

TOKEN = "s3cret-token-value"


def env(site="https://acme.atlassian.net", email="dev@acme.test", token=TOKEN):
    """A complete environment, with individual entries overridable."""
    return {SITE_VAR: site, EMAIL_VAR: email, TOKEN_VAR: token}


# --------------------------------------------------------------------------
# from_env - the happy path
# --------------------------------------------------------------------------


def test_reads_all_three_settings():
    config = Config.from_env(env())
    assert config.site == "https://acme.atlassian.net"
    assert config.email == "dev@acme.test"
    assert config.token == TOKEN


def test_trailing_slash_is_stripped_so_url_joining_stays_predictable():
    config = Config.from_env(env(site="https://acme.atlassian.net/"))
    assert config.site == "https://acme.atlassian.net"


def test_repeated_trailing_slashes_are_stripped():
    config = Config.from_env(env(site="https://acme.atlassian.net///"))
    assert config.site == "https://acme.atlassian.net"


def test_surrounding_whitespace_is_trimmed_from_every_value():
    # Copy-pasting a token out of a browser routinely picks up a newline.
    config = Config.from_env(env(site="  https://acme.atlassian.net  ", email=" dev@acme.test\n", token="  tok\n"))
    assert config.site == "https://acme.atlassian.net"
    assert config.email == "dev@acme.test"
    assert config.token == "tok"


def test_http_sites_are_accepted():
    # Useful against a local proxy or test server.
    assert Config.from_env(env(site="http://localhost:8080")).site == "http://localhost:8080"


def test_from_env_defaults_to_the_real_environment(monkeypatch):
    monkeypatch.setenv(SITE_VAR, "https://real.atlassian.net")
    monkeypatch.setenv(EMAIL_VAR, "real@acme.test")
    monkeypatch.setenv(TOKEN_VAR, "real-token")
    assert Config.from_env().site == "https://real.atlassian.net"


# --------------------------------------------------------------------------
# from_env - missing or malformed settings
# --------------------------------------------------------------------------


def test_all_missing_variables_are_reported_at_once():
    # One error listing three problems beats three runs fixing one each.
    with pytest.raises(ConfigError) as caught:
        Config.from_env({})

    message = str(caught.value)
    assert SITE_VAR in message
    assert EMAIL_VAR in message
    assert TOKEN_VAR in message


@pytest.mark.parametrize("missing", [SITE_VAR, EMAIL_VAR, TOKEN_VAR])
def test_a_single_missing_variable_is_named(missing):
    broken = env()
    del broken[missing]

    with pytest.raises(ConfigError) as caught:
        Config.from_env(broken)

    message = str(caught.value)
    assert missing in message
    present = {SITE_VAR, EMAIL_VAR, TOKEN_VAR} - {missing}
    header = message.splitlines()[0]
    assert not any(name in header for name in present)


@pytest.mark.parametrize("empty", ["", "   "])
def test_an_empty_variable_counts_as_missing(empty):
    # An exported-but-blank variable is a configuration mistake, not a value.
    with pytest.raises(ConfigError):
        Config.from_env(env(token=empty))


def test_the_error_explains_how_to_fix_it():
    with pytest.raises(ConfigError) as caught:
        Config.from_env({})
    message = str(caught.value)
    assert "export" in message
    assert "api-tokens" in message  # where to create a token


@pytest.mark.parametrize("bad_site", ["acme.atlassian.net", "ftp://acme.test", "//acme.test", "www.acme.test"])
def test_a_site_without_a_usable_scheme_is_rejected(bad_site):
    with pytest.raises(ConfigError) as caught:
        Config.from_env(env(site=bad_site))
    assert SITE_VAR in str(caught.value)


# --------------------------------------------------------------------------
# auth_header
# --------------------------------------------------------------------------


def test_auth_header_is_basic_email_colon_token():
    config = Config.from_env(env(email="dev@acme.test", token="abc123"))
    expected = base64.b64encode(b"dev@acme.test:abc123").decode("ascii")
    assert config.auth_header() == "Basic " + expected


def test_auth_header_round_trips_back_to_the_credentials():
    config = Config.from_env(env())
    decoded = base64.b64decode(config.auth_header().split(" ", 1)[1]).decode("utf-8")
    assert decoded == "dev@acme.test:{}".format(TOKEN)


def test_auth_header_handles_non_ascii_credentials():
    # b64encode would raise on a str; the implementation encodes UTF-8 first.
    config = Config.from_env(env(email="José@acme.test"))
    assert config.auth_header().startswith("Basic ")


# --------------------------------------------------------------------------
# url
# --------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/rest/api/3/myself", "rest/api/3/myself"])
def test_url_joins_with_exactly_one_slash(path):
    config = Config.from_env(env())
    assert config.url(path) == "https://acme.atlassian.net/rest/api/3/myself"


def test_url_preserves_query_strings():
    config = Config.from_env(env())
    assert config.url("/rest/api/3/search?jql=x").endswith("/search?jql=x")


# --------------------------------------------------------------------------
# The token must not leak
# --------------------------------------------------------------------------


def test_repr_redacts_the_token():
    config = Config.from_env(env())
    assert TOKEN not in repr(config)
    assert "<redacted>" in repr(config)


def test_str_redacts_the_token():
    # Plain interpolation into a log line goes through __str__, not __repr__.
    config = Config.from_env(env())
    assert TOKEN not in str(config)
    assert TOKEN not in "config was {}".format(config)


def test_token_does_not_leak_through_a_containing_collection():
    # Collections render their members with repr().
    config = Config.from_env(env())
    assert TOKEN not in repr([config])
    assert TOKEN not in repr({"config": config})


def test_site_and_email_stay_visible_for_debugging():
    config = Config.from_env(env())
    assert "acme.atlassian.net" in repr(config)
    assert "dev@acme.test" in repr(config)


def test_the_token_is_still_reachable_when_actually_needed():
    config = Config.from_env(env())
    assert config.token == TOKEN


# --------------------------------------------------------------------------
# Immutability
# --------------------------------------------------------------------------


def test_config_is_frozen():
    config = Config.from_env(env())
    with pytest.raises(dataclasses.FrozenInstanceError):
        config.site = "https://evil.test"


def test_config_compares_by_value():
    assert Config.from_env(env()) == Config.from_env(env())
    assert Config.from_env(env()) != Config.from_env(env(email="other@acme.test"))
