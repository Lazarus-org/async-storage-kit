# Security policy

## Supported versions

Security fixes target the latest supported stable release. Older versions may
not receive backported fixes; upgrade to a supported version when reporting an issue.

## Reporting a vulnerability

Do not open a public issue for an undisclosed vulnerability. Contact a maintainer
privately using [AUTHORS.md](AUTHORS.md):

- [Aryan Niknezhad](mailto:aryan513966@gmail.com)
- [Mehrshad Mirshekary](mailto:mehrshad_mirshekary@email.com)

Include the affected version, backend, OS, reproduction steps, and impact.
Redact credentials, presigned URLs, and personal data. Maintainers coordinate
private assessment, remediation, and disclosure with the reporter. If a listed
contact cannot be reached, try the other maintainer; do not disclose the issue
publicly while seeking a private reporting channel.

## Storage threat model

Authenticate and authorize access in your application. This package does not
provide quotas, malware scanning, content validation, or a permission system.

Keys reject absolute paths, traversal, control characters, and reserved staging
names. Protect filesystem roots and their parents from untrusted local users.
POSIX descriptor-relative operations reject symlinks but cannot defend against
all hostile parent-directory renames. The portable Windows fallback is not
race-resistant against hostile directory changes. `path()` returns a convenience
pathname rather than a secure descriptor. Configure restrictive file and
directory permissions for private data. Atomic publication does not guarantee
crash durability; temporary files can survive process termination or power loss.

Use TLS and least-privilege workload credentials for S3. Credentials use the AWS
SDK provider chain unless supplied explicitly. Public URLs require explicit
configuration; URL generation does not authorize access. Treat presigned URLs as
credentials and avoid logging them. Validate any custom endpoint you configure.

Conditional writes and multipart completion depend on provider support. A
concurrent rename conflict may raise rather than replay a stream. Delete is a
check-then-delete operation and may race with replacement. Provision buckets
explicitly and configure lifecycle cleanup for incomplete multipart uploads;
best-effort abort cannot handle every interruption. Repeated cancellation waits
for an ongoing abort attempt; network failures can still prevent cleanup.

See [the API reference](docs/API.md) and [release checklist](docs/RELEASE_READINESS.md).

Registry dotted imports and factories are trusted executable application configuration.
Do not derive backend paths or options directly from untrusted request data.
Filesystem names additionally reject Windows reserved devices, alternate streams,
trailing dots/spaces and forbidden characters on every host. POSIX descendants
may still be renamed by a hostile local actor; this is not a filesystem sandbox.
