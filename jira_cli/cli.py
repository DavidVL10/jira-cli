"""Command-line entry point."""

import argparse
import sys

from . import __version__


def build_parser():
    parser = argparse.ArgumentParser(
        prog="jira-cli",
        description="Work with Jira issues from the terminal.",
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
    issues.set_defaults(func=cmd_issues)

    return parser


def cmd_issues(args):
    # Not implemented yet: this proves the command wiring works end to end.
    print("would list issues for project {}".format(args.project))
    return 0


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
