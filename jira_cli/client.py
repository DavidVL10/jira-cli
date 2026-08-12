"""The Jira layer: turn API responses into plain objects the rest of us can use.

This is the only module that knows Jira's URL shapes and its deeply nested
JSON. Everything above it works with `Issue`, so a change to Jira's response
format is a change to one file.

Nothing here retries or sleeps; that belongs to `http`. Nothing here formats
for a terminal; that belongs to `render`.
"""

import re
from dataclasses import dataclass
from urllib.parse import urlencode

from .errors import JiraCliError
from .http import request_json

#: Search endpoint. Jira Cloud replaced the older /rest/api/3/search with this
#: token-paginated one; the GET form takes everything as query parameters.
SEARCH_PATH = "/rest/api/3/search/jql"

#: The fields we ask for. Requesting an explicit list rather than everything
#: keeps responses small - a full issue payload is enormous, and we render
#: five columns.
FIELDS = ("summary", "status", "assignee", "priority", "updated")

#: Jira caps a page at 100 regardless of what we ask for.
MAX_PAGE_SIZE = 100

#: Project keys are uppercase alphanumeric starting with a letter. We validate
#: rather than escape: a key is the one part of the JQL we interpolate, and
#: rejecting anything unexpected is simpler to get right than quoting it.
PROJECT_KEY = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class Issue:
    """One issue, flattened to the handful of fields we display.

    Every field is a string. Jira omits or nulls most of them freely - an
    unassigned issue has no assignee, a project without priorities has no
    priority - so absence is normal and becomes an empty string here rather
    than a None that every caller would have to guard.
    """

    key: str
    summary: str = ""
    status: str = ""
    assignee: str = ""
    priority: str = ""
    updated: str = ""

    @classmethod
    def from_api(cls, payload):
        """Build an Issue from one entry of a search response's `issues` list."""
        fields = payload.get("fields") or {}
        return cls(
            key=payload.get("key") or "",
            summary=(fields.get("summary") or "").strip(),
            status=_nested(fields, "status", "name"),
            assignee=_nested(fields, "assignee", "displayName"),
            priority=_nested(fields, "priority", "name"),
            updated=fields.get("updated") or "",
        )


def _nested(fields, outer, inner):
    """Read fields[outer][inner], tolerating either level being absent or null.

    `fields.get(outer) or {}` rather than `fields.get(outer, {})`: Jira sends
    an explicit null for an unassigned issue, and a null would sail past the
    default and raise on the next lookup.
    """
    value = fields.get(outer) or {}
    return value.get(inner) or ""


def project_jql(project, order_by="updated DESC"):
    """Build the JQL for "issues in this project, newest first".

    Raises JiraCliError for a key that is not a bare project key, so a stray
    quote or JQL fragment cannot reshape the query.
    """
    key = project.strip()
    if not PROJECT_KEY.match(key):
        raise JiraCliError(
            "invalid project key {!r}: expected letters, digits and underscores, "
            "starting with a letter (for example ABC)".format(project)
        )
    return "project = {} ORDER BY {}".format(key.upper(), order_by)


def search(config, jql, limit=50, request=request_json):
    """Run a JQL search and return up to `limit` issues.

    Follows `nextPageToken` until the results run out or `limit` is reached,
    so a caller asking for 250 gets 250 rather than the first page.

    `request` is injectable so tests can exercise paging without a network.
    """
    if limit <= 0:
        return []

    headers = {
        "Authorization": config.auth_header(),
        "Accept": "application/json",
    }

    issues = []
    token = None

    while len(issues) < limit:
        params = {
            "jql": jql,
            "fields": ",".join(FIELDS),
            "maxResults": min(limit - len(issues), MAX_PAGE_SIZE),
        }
        if token:
            params["nextPageToken"] = token

        url = "{}?{}".format(config.url(SEARCH_PATH), urlencode(params))
        payload = request(url, headers=headers)

        page = payload.get("issues") or []
        issues.extend(Issue.from_api(item) for item in page)

        token = payload.get("nextPageToken")
        # An empty page with a token would loop forever; treat it as the end.
        if not token or not page:
            break

    return issues[:limit]


def search_project(config, project, limit=50, request=request_json):
    """Convenience wrapper: every issue in a project, most recently updated first."""
    return search(config, project_jql(project), limit=limit, request=request)
