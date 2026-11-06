# Contributing to async-storage-kit

Thank you for contributing. Bug reports, documentation, tests, code reviews, and
implementation changes are welcome. Please follow the [Code of Conduct](CODE_OF_CONDUCT.md).
Report undisclosed vulnerabilities through [SECURITY.md](SECURITY.md).

## Contributor and maintainer responsibilities

Contributors propose changes through pull requests and use the commit conventions
below. **Contributing does not grant release or publishing permission.** Do not
bump the package version, create release tags, or upload distributions as part of
a normal contribution. Maintainers review and merge changes, select release
versions, approve release notes, and authorize publication.

The [maintainer release workflow](CODE_OF_CONDUCT.md#maintainer-release-workflow) documents version bumps,
changelog generation, release commits, tag pushes, and publishing. Those steps
are reserved for authorized release maintainers.

## Fork, clone, and create a branch

Fork [Lazarus-org/async-storage-kit](https://github.com/Lazarus-org/async-storage-kit),
clone your fork, and add the upstream remote. Replace `YOUR_USERNAME` with your
GitHub username:

```bash
git clone https://github.com/YOUR_USERNAME/async-storage-kit.git
cd async-storage-kit
git remote add upstream https://github.com/Lazarus-org/async-storage-kit.git
git switch -c fix/s3-listing
```

Keep each branch and pull request focused on one problem. Sync with upstream
before starting additional work:

```bash
git fetch upstream
git switch main
git merge --ff-only upstream/main
```

## Install dependencies and hooks

Use Python 3.11 or later and uv:

```bash
uv sync --locked
uv run --locked pre-commit install
```

The hook configuration installs both `pre-commit` and `commit-msg` hooks.
`pre-commit` checks formatting, lint, types, security, and tests; `commit-msg`
checks Conventional Commits through Commitizen. Fix hook failures and retry the
commit instead of bypassing the checks.

The default `dev` dependency group includes test and quality tools and the optional
S3 SDK. The core package has no mandatory runtime dependencies. Pip users can
install the contributor extras with:

```bash
python -m pip install -e '.[test,quality,s3]'
```

When changing dependencies, run `uv lock`, review the changes, and include both
`pyproject.toml` and `uv.lock` in the pull request.

## Run checks

```bash
uv run --locked ruff check src tests examples .github/scripts
uv run --locked ruff format --check src tests examples .github/scripts
uv run --locked mypy src/
uv run --locked bandit -c pyproject.toml -r src/async_storage_kit
uv run --locked pytest --cov=async_storage_kit --cov-branch -m "not live_s3"
uv run --locked pre-commit run --all-files
uv run --locked tox -e py311,py312,py313,py314,py315
```

CI requires **100% statement and branch coverage** after combining the
Python/platform matrix, including both filesystem drivers. Individual platform
runs require 85%, since Windows cannot execute POSIX directory-descriptor APIs.
No platform-specific code is excluded from measurement. Tox requires the selected
interpreters to be installed. CI also runs the live S3-compatible service contract.

To combine coverage data collected on different platforms, copy their uniquely
named `.coverage.*` files into one checkout and run:

```bash
uv run --locked coverage combine
uv run --locked coverage report --fail-under=100
```

For a focused test run:

```bash
uv run --locked pytest tests/test_registry.py
```

Add regression tests for fixes, especially collision handling, streaming,
cancellation, cleanup, path safety, and optional dependency isolation. Keep
filesystem/memory storage dependency-free and third-party imports lazy. Update
the API docs and examples when public behavior changes. Avoid unrelated rewrites
and new abstractions without a concrete need.

### Live S3 tests

Provision a disposable bucket and set `S3_TEST_ENDPOINT`, `S3_TEST_BUCKET`,
`S3_TEST_ACCESS_KEY`, and `S3_TEST_SECRET_KEY`, then run:

```bash
uv run --locked pytest -q -m live_s3
```

These tests write/delete objects and inspect multipart uploads. Do not use a
production bucket. A configured endpoint that cannot be reached fails the tests.
CI uses a pinned PGSTY SILO image, a MinIO fork; provider compatibility should
also be checked against the provider used by your application.

## Commit conventions

Use [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/):

```text
<type>(<optional scope>): <short description>

<optional explanation>

<optional footers>
```

| Type | Use |
| --- | --- |
| `feat` | A new public capability |
| `fix` | A bug or security fix |
| `docs` | Documentation changes |
| `test` | Test additions or corrections |
| `refactor` | Code restructuring without changing public behavior |
| `perf` | Performance improvements |
| `build` | Packaging, dependencies, or build tooling |
| `ci` | Continuous integration or release automation |
| `style` | Formatting without behavior changes |
| `chore` | Other maintenance |

Use a meaningful scope such as `filesystem`, `memory`, `s3`, `registry`, or
`fastapi` when it helps. Write a short, specific description:

```text
fix(s3): abort multipart uploads after cancellation
feat(registry): support named backend factories
docs: clarify filesystem permission handling
test(memory): cover concurrent collision policies
ci: pin the S3 integration image
```

Mark breaking changes with `!` after the type/scope and explain the migration in
a `BREAKING CHANGE:` footer:

```text
feat(storage)!: require explicit overwrite policy

BREAKING CHANGE: pass on_conflict="overwrite" when replacing an existing object.
```

Stage the intended changes and use Commitizen's interactive commit command:

```bash
git add src tests
uv run --locked cz commit
```

Adjust the staged paths to your change, including documentation or dependency
files when relevant. You can also write the message yourself; the installed
`commit-msg` hook checks it. To validate a proposed message without committing:

```bash
uv run --locked cz check --message "fix(s3): preserve conditional write conflicts"
```

Commit messages drive semantic version selection and generated release notes:
`fix` normally means a patch, `feat` a minor release, and a breaking change a
major release. Maintainers review the actual compatibility impact and final
version before a release.

## Changelog contributions

Describe user-visible behavior in your pull request and keep the commit message
accurate. A concise entry under `Unreleased` in [CHANGELOG.md](CHANGELOG.md) is
welcome when it helps explain the change. Maintainers generate and review release
sections with Commitizen; contributors should not generate a release, change
version files, or create release tags.

To preview generated notes without changing files:

```bash
uv run --locked cz changelog --dry-run
```

## Push your branch and open a pull request

```bash
git push -u origin fix/s3-listing
```

Open a pull request against upstream `main`. Explain the problem, resulting
behavior, tests run, related issues, and any compatibility or security impact.
Keep review feedback constructive and rerun affected checks after revisions.
Use a Conventional Commit-style pull request title so a squash merge preserves
the changelog/versioning information. Maintainers are responsible for the final
merge message and release decisions.

**Push contribution branches to your fork. Release tag pushes and package
publication are maintainer-only actions.**
