# jira-cli

List Jira issues from the terminal. No runtime dependencies — the standard
library only.

```
$ jira-cli issues --project ACME --limit 5
KEY      STATUS       ASSIGNEE     UPDATED     SUMMARY
ACME-88  In Progress  Dana Reed    2026-08-11  Fix the login redirect loop
ACME-87  Done         Sam Okafor   2026-08-10  Cache the project metadata lookup
ACME-84  To Do        unassigned   2026-08-08  Paginate the issue list endpoint so large projects d…
ACME-81  In Progress  Priya Raman  2026-08-07  Audit accessibility of the settings screens
ACME-79  To Do        unassigned   2026-08-05  Add a retry budget to the webhook dispatcher
```

## Status

Listing issues works. Everything else in *Planned* below is not built yet.

- `issues --project KEY` — list a project's issues, most recently updated first

### Planned

- View a single issue with its description and comments
- Search with arbitrary JQL rather than one project at a time
- Create, update, and transition issues
- `--json` output for piping into other tools

## Install

Requires Python 3.9 or newer.

```bash
git clone https://github.com/DavidVL10/jira-cli.git
cd jira-cli
python3 -m venv .venv
.venv/bin/pip install -e .
```

That puts a `jira-cli` executable in `.venv/bin/`.

## Configuration

Settings come from the environment. Nothing is read from a file the repository
tracks, and the API token is never written to logs or error messages.

| Variable | Required | Description |
|---|---|---|
| `JIRA_SITE` | yes | Your Jira URL, e.g. `https://your-org.atlassian.net` |
| `JIRA_EMAIL` | yes | The email of the account that owns the token |
| `JIRA_API_TOKEN` | yes | Create one at [id.atlassian.com](https://id.atlassian.com/manage-profile/security/api-tokens) |
| `JIRA_CLOUD_ID` | only for scoped tokens | See below |

If any required variable is missing, the tool names all of them at once and
prints the `export` lines you need, rather than failing one at a time.

### Scoped tokens need a cloud ID

Atlassian issues two kinds of API token, and they use different URLs:

- A **classic** token is presented to your site directly, at
  `https://your-org.atlassian.net/rest/api/3/...`. Leave `JIRA_CLOUD_ID` unset.
- A token created **with scopes** is only accepted at the platform gateway, at
  `https://api.atlassian.com/ex/jira/<cloud id>/rest/api/3/...`. Set
  `JIRA_CLOUD_ID` and requests are routed there instead.

Symptoms of getting this wrong are a `401` from the site with a scoped token.
Find your cloud ID with:

```bash
curl -s https://your-org.atlassian.net/_edge/tenant_info
```

That endpoint needs no authentication; the cloud ID is an instance identifier,
not a secret. Listing issues needs the `read:jira-work` scope — a token without
it authenticates fine and then returns `403`.

### Keeping credentials out of your shell history

Put them in a file **outside the repository**, readable only by you:

```bash
umask 077
cat > ~/.jira-cli.env <<'ENV'
JIRA_SITE=https://your-org.atlassian.net
JIRA_EMAIL=you@example.com
JIRA_API_TOKEN=your-token
ENV
chmod 600 ~/.jira-cli.env
```

Load it before running:

```bash
set -a; . ~/.jira-cli.env; set +a
```

`set -a` exports each assignment so child processes inherit it — without it the
variables exist in your shell but not in the environment `jira-cli` reads.

## Usage

```bash
jira-cli issues --project ACME            # 50 most recently updated
jira-cli issues --project ACME --limit 200  # pages automatically
jira-cli --version
```

Exit codes: `0` success, `1` a configuration or API error, `2` bad arguments,
`130` interrupted.

## Development

```bash
.venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest
```

The tests never touch the network and never sleep. `request_json` takes its
`urlopen`, `sleep`, and `rng` as parameters, and `search` takes its `request`,
so retry backoff and pagination are driven directly.

### Layout

| Module | Responsibility |
|---|---|
| `config.py` | Credentials from the environment; URL construction |
| `http.py` | Timeouts, and retries for the failures worth retrying |
| `client.py` | The only module that knows Jira's URLs and JSON shapes |
| `render.py` | Aligned terminal output |
| `cli.py` | Argument parsing; turning errors into messages and exit codes |
| `errors.py` | Exception types |

Two conventions worth knowing before changing things:

- **Only `4xx` responses that can succeed later are retried** — `429` and `5xx`.
  A `401` or `404` fails immediately, because resending it changes nothing.
- **Retries use exponential backoff with full jitter**, so several clients that
  hit a rate limit together do not wake together and retry in lockstep.

## License

Not yet specified.
