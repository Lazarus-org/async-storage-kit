# Maintainer release checklist

Only authorized release maintainers approve versions, push release tags, and
publish async-storage-kit. Contributors follow [CONTRIBUTING.md](../CONTRIBUTING.md).
See [maintainer release workflow](../CODE_OF_CONDUCT.md#maintainer-release-workflow) for the Commitizen and tag-push commands.

## Source and release notes

- [ ] Review and merge the changes intended for the release.
- [ ] Select the semantic version from the actual compatibility impact.
- [ ] Preview the Commitizen bump and generated changelog.
- [ ] Update `pyproject.toml`, `uv.lock`, and the runtime `__version__` together.
- [ ] Review `CHANGELOG.md`, public docs, examples, and upgrade instructions.
- [ ] Check package metadata, project links, license, and maintainer contacts.

## Validation

- [ ] Verify `uv sync --locked` and the offline lock check.
- [ ] Run lint, formatting, mypy, Bandit, hooks, and the offline tests.
- [ ] Pass the CI coverage gate: 100% statements and branches across platforms.
- [ ] Require the configured Python/platform CI matrix to pass.
- [ ] Run the real disposable S3-compatible service contract and any additional
      provider-specific validation required by the release's compatibility claims.
- [ ] Build the wheel and source archive from the reviewed source.
- [ ] Pass strict Twine metadata/description validation.
- [ ] Verify isolated pip/uv installation without runtime dependencies, each
      optional extras configuration, extracted-source tests, and matching rebuilds.
- [ ] Inspect archives for the typing marker, license, documentation, and absence
      of credentials, local artifacts, and historical engineering reports.

## Approval, tag, and publication

- [ ] Obtain release approval from the designated maintainers.
- [ ] Confirm the release commit is in default-branch history and the working
      tree is clean before creating the matching `v<version>` tag.
- [ ] Configure the protected `pypi` environment: required reviewers, no self-review,
      no protection bypass, and appropriate release-tag restrictions.
- [ ] Verify the Trusted Publisher owner, repository, workflow, and environment.
- [ ] Restrict release-tag creation/push and publishing configuration to maintainers.
- [ ] Push only the approved release tag and inspect the triggered workflow.
- [ ] Require all validation jobs to pass before the protected publish job runs.
- [ ] Verify that the publisher uses the exact validated artifact ID.
- [ ] Check installation and public metadata of the exact published version.

Publishing is disabled by default. Setting `PYPI_PUBLISH_ENABLED=true` is an
administrator action and does not replace maintainer approval. Never reuse a
published version or move a published release tag.
