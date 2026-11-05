# gh-accounts

A small GitHub CLI extension for viewing and switching accounts already authenticated with GitHub CLI.

It delegates account discovery and switching to `gh auth status` and `gh auth switch`. It never retrieves, stores, or prints token values. It does not log in or log out accounts.

## Requirements

- GitHub CLI (`gh`) with support for `gh auth status --json hosts`
- Python 3.10 or newer

## Commands

```text
gh accounts [list] [--hostname HOST]
gh accounts current [--hostname HOST]
gh accounts switch [LOGIN] [--hostname HOST]
```

With no subcommand, `gh accounts` lists accounts. `switch` opens a numbered selector when run in a terminal without a `LOGIN`; otherwise, pass the login. If the same login exists on multiple hosts, pass `--hostname`.

The extension refuses to switch while a GitHub token environment override applies, since those variables take precedence over stored `gh` credentials. It checks only whether an override variable is set, reports variable names, and never displays their values.

GitHub CLI switching changes the account used by `gh` for that host. Git over HTTPS follows it when the repository has been configured with `gh auth setup-git`. SSH key selection and Git commit author settings are managed separately.

## Use from this repository

This extension is nested in the zTTato monorepo, so it is not independently installable from the repository root. It can be run directly:

```sh
python3 plugins/gh-accounts/gh-accounts list
python3 plugins/gh-accounts/gh-accounts switch octocat
```

To install it as `gh accounts`, copy this directory to a standalone directory named `gh-accounts`, then initialize and install that local extension:

```sh
cp -R plugins/gh-accounts /tmp/gh-accounts
cd /tmp/gh-accounts
git init -b main
gh extension install .
gh accounts
```

For a shared installation, publish this source as its own GitHub repository named `gh-accounts`, then install it with `gh extension install OWNER/gh-accounts`. It has not been published.

## Security boundary

GitHub CLI owns credential storage and API requests. This extension calls only `gh auth status --json hosts` and, when a switch is requested, `gh auth switch --hostname HOST --user LOGIN`. It does not access GitHub auth files or call `gh auth token`.
