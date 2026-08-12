"""Turn issues into aligned terminal output.

Kept separate from `client` so the fetch path has no opinion about
presentation, and so this can be tested without a network.

The layout rule: the fixed columns size themselves to their content, and the
summary takes whatever width is left. Summaries are the one field with no
useful upper bound, so they are the right thing to truncate.
"""

import shutil

#: Fixed columns, in display order. The summary is appended last and flexes.
#: `max_width` keeps one 40-character display name from starving the summary.
COLUMNS = (
    ("KEY", "key", 12),
    ("STATUS", "status", 14),
    ("ASSIGNEE", "assignee", 18),
    ("UPDATED", "updated", 10),
)

SUMMARY_HEADING = "SUMMARY"

#: Below this the summary is unreadable, so we let the line overrun instead.
MIN_SUMMARY_WIDTH = 20

#: Two spaces between columns: enough to separate, cheaper than a box border.
GAP = "  "

ELLIPSIS = "…"

FALLBACK_WIDTH = 100


def render_issues(issues, width=None):
    """Return a table of issues as a string, without a trailing newline.

    `width` is injectable so tests are not at the mercy of the terminal that
    happens to be running them.
    """
    if not issues:
        return "No issues found."

    width = _resolve_width(width)
    rows = [_cells(issue) for issue in issues]

    fixed = [
        min(max_width, max(len(heading), *(len(row[name]) for row in rows)))
        for heading, name, max_width in COLUMNS
    ]

    # Everything not spoken for by the fixed columns and the gaps between them.
    used = sum(fixed) + len(GAP) * len(COLUMNS)
    summary_width = max(MIN_SUMMARY_WIDTH, width - used)

    lines = [_line([h for h, _, _ in COLUMNS], SUMMARY_HEADING, fixed, summary_width)]
    for row in rows:
        lines.append(
            _line(
                [row[name] for _, name, _ in COLUMNS],
                row["summary"],
                fixed,
                summary_width,
            )
        )
    return "\n".join(lines)


def _line(values, summary, fixed, summary_width):
    """One row: fixed columns padded to width, then the summary, truncated.

    The summary is not padded - trailing whitespace on every line is invisible
    until it lands in a diff or a copy-paste.
    """
    cells = [_fit(value, size) for value, size in zip(values, fixed)]
    cells.append(_truncate(summary, summary_width))
    return GAP.join(cells).rstrip()


def _cells(issue):
    """Flatten an issue into the strings the table displays."""
    return {
        "key": issue.key,
        "status": issue.status,
        "assignee": issue.assignee or "unassigned",
        "updated": _short_date(issue.updated),
        "summary": " ".join(issue.summary.split()),
    }


def _fit(value, size):
    """Truncate to `size` if too long, pad to `size` if too short."""
    return _truncate(value, size).ljust(size)


def _truncate(value, size):
    """Shorten to `size` characters, marking that something was cut."""
    if len(value) <= size:
        return value
    if size <= len(ELLIPSIS):
        return value[:size]
    return value[: size - len(ELLIPSIS)] + ELLIPSIS


def _short_date(timestamp):
    """Reduce a Jira timestamp to its date.

    Jira sends ISO-8601 with a compact offset ("2026-08-11T09:15:00.000-0700").
    Python before 3.11 cannot parse that offset form, and the clock time adds
    nothing to a list view, so we take the date prefix rather than pull in a
    parser. Anything unexpected is passed through untouched.
    """
    date = timestamp[:10]
    if len(date) == 10 and date[4] == "-" and date[7] == "-":
        return date
    return timestamp


def _resolve_width(width):
    """Terminal width, or a sane default when there is no terminal.

    get_terminal_size falls back to 80 columns when stdout is a pipe, which is
    exactly when truncating is least welcome - `jira-cli issues > file` should
    keep the summaries.
    """
    if width is not None:
        return width
    size = shutil.get_terminal_size(fallback=(FALLBACK_WIDTH, 24))
    return size.columns
