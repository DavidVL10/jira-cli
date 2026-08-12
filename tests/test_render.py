"""Tests for terminal rendering.

`render_issues` takes a width so these are not at the mercy of whatever
terminal happens to run them.
"""

import pytest

from jira_cli.client import Issue
from jira_cli.render import (
    ELLIPSIS,
    MIN_SUMMARY_WIDTH,
    SUMMARY_HEADING,
    render_issues,
)

WIDE = 200


def issue(key="ABC-1", summary="Fix login redirect loop", status="In Progress",
          assignee="Dana Reed", priority="High", updated="2026-08-11T09:15:00.000-0700"):
    return Issue(
        key=key,
        summary=summary,
        status=status,
        assignee=assignee,
        priority=priority,
        updated=updated,
    )


def lines(issues, width=WIDE):
    return render_issues(issues, width=width).split("\n")


def column_start(header, heading):
    """Where a column begins, from the header row."""
    return header.index(heading)


# --------------------------------------------------------------------------
# Shape of the output
# --------------------------------------------------------------------------


def test_no_issues_says_so_rather_than_printing_an_empty_table():
    assert render_issues([], width=WIDE) == "No issues found."


def test_every_issue_gets_a_row_under_one_header():
    assert len(lines([issue("ABC-1"), issue("ABC-2"), issue("ABC-3")])) == 4


def test_the_header_names_every_column():
    header = lines([issue()])[0]
    for heading in ("KEY", "STATUS", "ASSIGNEE", "UPDATED", SUMMARY_HEADING):
        assert heading in header


def test_output_has_no_trailing_newline():
    # print() adds one; a second would leave a blank line behind every table.
    assert not render_issues([issue()], width=WIDE).endswith("\n")


def test_no_line_carries_trailing_whitespace():
    # Invisible until it reaches a diff or a copy-paste.
    rendered = lines([issue(summary=""), issue(summary="short")])
    assert all(line == line.rstrip() for line in rendered)


def test_the_issue_data_actually_appears():
    row = lines([issue()])[1]
    assert "ABC-1" in row
    assert "In Progress" in row
    assert "Dana Reed" in row
    assert "Fix login redirect loop" in row


# --------------------------------------------------------------------------
# Alignment
# --------------------------------------------------------------------------


@pytest.mark.parametrize("heading", ["STATUS", "ASSIGNEE", "UPDATED"])
def test_columns_line_up_across_rows_of_different_widths(heading):
    rendered = lines([issue(key="A-1"), issue(key="LONGPROJ-12345"), issue(key="BB-2")])
    header, rows = rendered[0], rendered[1:]
    start = column_start(header, heading)
    for row in rows:
        assert row[start] != " ", "column {} is misaligned in {!r}".format(heading, row)


def test_a_column_is_as_wide_as_its_widest_value():
    # KEY holds "ABC-1" and the heading is shorter, so STATUS starts after it.
    header = lines([issue(key="ABC-1")])[0]
    assert column_start(header, "STATUS") == len("ABC-1") + 2


def test_a_short_column_is_still_at_least_as_wide_as_its_heading():
    header = lines([issue(key="A-1")])[0]
    assert column_start(header, "STATUS") == len("KEY") + 2


def test_an_overlong_value_is_capped_so_it_cannot_starve_the_summary():
    name = "Bartholomew Fitzgerald-Montgomery III"
    row = lines([issue(assignee=name)])[1]
    assert name not in row
    assert ELLIPSIS in row


# --------------------------------------------------------------------------
# Individual cells
# --------------------------------------------------------------------------


def test_an_unassigned_issue_says_so():
    # An empty column reads as a rendering bug; "unassigned" reads as a fact.
    assert "unassigned" in lines([issue(assignee="")])[1]


def test_timestamps_are_cut_to_the_date():
    row = lines([issue(updated="2026-08-11T09:15:00.000-0700")])[1]
    assert "2026-08-11" in row
    assert "09:15" not in row


@pytest.mark.parametrize("odd", ["", "not-a-date", "2026"])
def test_an_unexpected_timestamp_is_passed_through_untouched(odd):
    # Better to show something strange than to crash or invent a date.
    rendered = render_issues([issue(updated=odd)], width=WIDE)
    assert odd in rendered or odd == ""


def test_an_overlong_timestamp_is_truncated_like_any_other_cell():
    # _short_date declines to reinterpret it, then the column cap applies.
    row = lines([issue(updated="20260811T091500")])[1]
    assert "20260811" in row
    assert ELLIPSIS in row


def test_summary_whitespace_is_collapsed_onto_one_line():
    # A newline in a summary would otherwise break the table apart.
    rendered = render_issues([issue(summary="first\nsecond\t\tthird")], width=WIDE)
    assert len(rendered.split("\n")) == 2
    assert "first second third" in rendered


# --------------------------------------------------------------------------
# Width and truncation
# --------------------------------------------------------------------------


def test_a_summary_that_fits_is_left_alone():
    summary = "Fix login redirect loop"
    assert summary in lines([issue(summary=summary)], width=WIDE)[1]


def test_a_summary_that_does_not_fit_is_truncated_and_marked():
    long_summary = "Add pagination to the issue list endpoint " * 5
    row = lines([issue(summary=long_summary)], width=80)[1]
    assert row.endswith(ELLIPSIS)
    assert len(row) == 80


@pytest.mark.parametrize("width", [80, 120, 200])
def test_the_table_respects_the_width_it_is_given(width):
    # Any width leaving room for a readable summary; see the floor case below.
    long_summary = "x" * 500
    rendered = lines([issue(summary=long_summary), issue(key="B-2")], width=width)
    assert all(len(line) <= width for line in rendered)


def test_a_cramped_width_overruns_rather_than_shrinking_the_summary():
    # Deliberate: past a point, narrowing the summary further only produces a
    # column too short to read. Overrunning and letting the terminal wrap is
    # the better failure.
    rendered = lines([issue(summary="x" * 500)], width=40)
    assert any(len(line) > 40 for line in rendered)


def test_truncation_marks_the_cut_rather_than_silently_dropping_text():
    row = lines([issue(summary="y" * 500)], width=80)[1]
    assert ELLIPSIS in row
    assert "yyy" in row  # some of it survives


def test_a_narrow_terminal_keeps_the_summary_readable():
    # Below this the summary is useless, so the line is allowed to overrun
    # rather than shrink to nothing.
    row = lines([issue(summary="z" * 200)], width=20)[1]
    assert row.count("z") >= MIN_SUMMARY_WIDTH - len(ELLIPSIS)


def test_width_defaults_to_the_terminal(monkeypatch):
    import shutil

    monkeypatch.setattr(
        shutil, "get_terminal_size", lambda fallback=None: __import__("os").terminal_size((70, 24))
    )
    rendered = render_issues([issue(summary="q" * 300)])
    assert all(len(line) <= 70 for line in rendered.split("\n"))


def test_rendering_is_stable_for_a_single_issue():
    # The narrowest interesting case: every column sized by one row.
    rendered = render_issues([issue(key="AB-1", status="Done", assignee="Kim", summary="Ship it")], width=WIDE)
    header, row = rendered.split("\n")
    assert header.split() == ["KEY", "STATUS", "ASSIGNEE", "UPDATED", "SUMMARY"]
    assert row.split() == ["AB-1", "Done", "Kim", "2026-08-11", "Ship", "it"]
