# Code of Conduct for Contributing to async-storage-kit

We welcome contributions to `async-storage-kit`. Be respectful, keep changes
focused, and follow the steps below. See [CONTRIBUTING.md](CONTRIBUTING.md) for
the full guide.

## Community expectations

- Treat everyone with respect and give constructive feedback.
- Respect privacy, authorship, and other people's time.
- Do not harass, discriminate, threaten, or make personal attacks.
- Never share credentials, private configuration, or personal information.

Report conduct concerns privately to [Aryan Niknezhad](mailto:aryan513966@gmail.com)
or [Mehrshad Mirshekary](mailto:mehrshad_mirshekary@email.com). If a concern involves
one maintainer, contact the other. Maintainers may request corrections, remove
harmful content, or restrict participation. Report vulnerabilities through
[SECURITY.md](SECURITY.md).

## Contribution workflow

### 1. Fork and clone

Fork the repository, then replace `YOUR_USERNAME` with your GitHub username:

```bash
git clone https://github.com/YOUR_USERNAME/async-storage-kit.git
cd async-storage-kit
git remote add upstream https://github.com/Lazarus-org/async-storage-kit.git
```

### 2. Create a branch

```bash
git switch -c feature/your-feature-name
```

Keep each branch focused on one feature, fix, or documentation change.

### 3. Install dependencies and hooks

Use Python 3.11 or newer and uv:

```bash
uv sync --locked
uv run --locked pre-commit install
```

This installs the locked test/quality tools and both `pre-commit` and `commit-msg`
hooks. No manual environment activation is needed.

### 4. Write code and tests

Add regression tests for changed behavior and run the offline suite:

```bash
uv run --locked pytest --cov=async_storage_kit --cov-branch -m "not live_s3"
```

CI requires **100% statement and branch coverage** across the platform matrix.
For a focused run:

```bash
uv run --locked pytest tests/test_registry.py
```

Keep filesystem/memory storage dependency-free and optional SDK imports lazy.
Use a disposable endpoint for live S3 tests; see [CONTRIBUTING.md](CONTRIBUTING.md#live-s3-tests).

### 5. Run quality checks

```bash
uv run --locked ruff check src tests examples .github/scripts
uv run --locked ruff format --check src tests examples .github/scripts
uv run --locked mypy src/
uv run --locked bandit -c pyproject.toml -r src/async_storage_kit
uv run --locked pre-commit run --all-files
```

Review and stage automatic fixes before retrying a commit. To test installed
Python versions through Tox:

```bash
uv run --locked tox -e py311,py312,py313,py314,py315
```

### 6. Commit your changes

Stage only the files belonging to your change, then use Commitizen:

```bash
git add src tests
uv run --locked cz commit
```

Include documentation and dependency files when relevant. Use `Closes #123` or
`Refs #123` only when referring to a real issue.

### 7. Push and open a pull request

```bash
git push -u origin feature/your-feature-name
```

Open a pull request against upstream `main`. Explain the problem, the change,
and the checks you ran. Respond to review and rerun affected checks.

**Normal contributions do not bump the package version, create release tags,
or publish packages. The following steps are for authorized maintainers only.**

## Maintainer release workflow

### 8. Prepare the version and changelog

For the first `1.0.0` release, keep that version and finalize its changelog entry.
For later releases, preview the proposed bump, then update the release files:

```bash
uv run --locked cz bump --dry-run --changelog
uv run --locked cz bump --version-files-only --changelog
uv lock --check --offline
```

Commitizen updates `pyproject.toml`, the package version in `uv.lock`, and
`src/async_storage_kit/__init__.py`. `--version-files-only` leaves the commit
and tag for the reviewed release steps.

The bump command already generates the changelog. To generate notes separately:

```bash
uv run --locked cz changelog --dry-run
uv run --locked cz changelog --incremental
```

Review the generated version, release notes, and any manual entries. Commit the
release preparation and merge it through the normal review process:

```bash
git add pyproject.toml uv.lock src/async_storage_kit/__init__.py CHANGELOG.md
uv run --locked cz commit
```

### 9. Build and validate

From the reviewed release source, build into an empty output directory:

```bash
uv sync --locked
uv build --no-sources --out-dir dist/release
uv run --locked twine check --strict dist/release/*
uv run --locked python .github/scripts/validate_distribution.py dist/release --test-sdist --test-extras
```

Require the hosted compatibility, quality, and live S3 checks to pass. Use the
[release checklist](docs/RELEASE_READINESS.md) before tagging.

### 10. Create and push the release tag

After the release preparation is merged, update `main` and tag the reviewed
commit. These commands illustrate `1.0.0`; use the selected version for later releases:

```bash
git switch main
git pull --ff-only origin main
git tag -a v1.0.0 -m "Release 1.0.0"
git push origin v1.0.0
```

The tag must match the package version. Its push triggers release validation and,
when publishing is enabled, protected PyPI Trusted Publishing. **Only authorized
release maintainers may push release tags.** Do not move a published tag.

### 11. Publish and verify

The normal publishing path is the release workflow. Maintainers configure its
Trusted Publisher, protected `pypi` environment, and `PYPI_PUBLISH_ENABLED` variable.
The variable does not replace maintainer approval.

For an authorized manual upload, choose the intended index and upload the
validated artifacts:

```bash
# TestPyPI
uv publish --publish-url https://test.pypi.org/legacy/ dist/release/*

# PyPI
uv publish --publish-url https://upload.pypi.org/legacy/ dist/release/*
```

Keep automatic publishing disabled when choosing a manual publishing path.
TestPyPI and PyPI use separate credentials. Keep tokens out of source, logs, and
command-line arguments. Verify installation of the exact uploaded version.
Use the [maintainer checklist](docs/RELEASE_READINESS.md) to review release approvals and publishing checks.

## Commitizen message rule

Use Conventional Commits:

```text
<type>[optional scope]: <short description>

[optional body]

[optional footer(s)]
```

Use a specific, imperative description. Examples:

| Change | Commit message |
| --- | --- |
| Add a feature | `feat(storage): add streaming downloads` |
| Fix a bug | `fix(s3): abort multipart uploads after cancellation` |
| Add tests | `test(filesystem): cover concurrent file creation` |
| Update documentation | `docs: explain storage collision policies` |
| Refactor code | `refactor(registry): simplify backend construction` |
| Improve performance | `perf(memory): reduce copies during chunk iteration` |
| Update dependencies | `chore(deps): update locked quality tools` |
| Change packaging | `build: include the public typing marker` |
| Update CI | `ci: pin the S3 integration image` |
| Prepare a release (maintainers) | `chore(release): prepare version 1.0.0` |

For a breaking change, use `!` and explain the migration in a footer:

```text
feat(storage)!: change the default collision policy

BREAKING CHANGE: pass on_conflict="rename" to retain the previous behavior.
```

Creating a commit does not itself push a tag or publish a package.
