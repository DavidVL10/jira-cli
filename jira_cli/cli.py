"""Command-line entry point.

This layer does three things and nothing else: parse arguments, call into the
modules that do the work, and turn our own exceptions into a clean message and
an exit code. Anything that looks like Jira knowledge or formatting belongs in
`client` or `render`.
"""

import argparse
import os
import sys

from . import __version__
from .client import search_project
from .config import Config
from .errors import JiraCliError
from .render import render_issues

DEFAULT_LIMIT = 50


def positive_int(value):
    """An argparse type for counts that must be at least one."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError("{!r} is not a whole number".format(value))
    if number < 1:
        raise argparse.ArgumentTypeError("must be at least 1, got {}".format(number))
    return number


def build_parser():
    parser = argparse.ArgumentParser(
        prog="jira-cli",
        description="Work with Jira issues from the terminal.",
        epilog=(
            "Configuration is read from the environment: "
            "JIRA_SITE, JIRA_EMAIL and JIRA_API_TOKEN."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version="jira-cli {}".format(__version__),
    )

    subcommands = parser.add_subparsers(dest="command", required=True)

    issues = subcommands.add_parser("issues", help="List issues in a project")
    issues.add_argument(
        "--project",
        required=True,
        help="Project key, e.g. ABC",
    )
    issues.add_argument(
        "--limit",
        type=positive_int,
        default=DEFAULT_LIMIT,
        help="Maximum issues to list (default: {})".format(DEFAULT_LIMIT),
    )
    issues.set_defaults(func=cmd_issues)

    return parser


def cmd_issues(args):
    config = Config.from_env()
    issues = search_project(config, args.project, limit=args.limit)
    print(render_issues(issues))
    return 0


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        return args.func(args)

    except JiraCliError as exc:
        # Errors we raised deliberately already carry a message written for a
        # human. A traceback here would only bury it. Genuine bugs are not
        # caught and still surface in full.
        print("error: {}".format(exc), file=sys.stderr)
        return 1

    except KeyboardInterrupt:
        # Finish the line that ^C landed in the middle of.
        print(file=sys.stderr)
        return 130

    except BrokenPipeError:
        # `jira-cli issues --project ABC | head` closes our stdout early. Point
        # the fd at devnull so the interpreter's final flush has somewhere to
        # go, otherwise Python prints its own noise on the way out.
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
        return 141


if __name__ == "__main__":
    sys.exit(main())
