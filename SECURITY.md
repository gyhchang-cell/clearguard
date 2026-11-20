# Security and data safety

This preview handles destructive user intent conservatively but is not a backup tool. Use important files only with independent backups. Never run elevated. Never operate in a directory controlled by an untrusted writer.

For a report, reproduce with synthetic files and redact absolute paths, usernames, file content and credentials. Do not upload personal scan reports or audit logs. Public issues should contain only sanitized information. If a vulnerability would enable data loss, do not publish exploit details before coordinating a fix with the maintainer via an available private GitHub security reporting channel.

Security-sensitive changes must retain all consent, keeper, preflight and native Trash guards. Tests must operate only on project-contained disposable fixtures; use a fake trash adapter and never empty the system Trash.

Known limitation: path revalidation cannot eliminate filesystem race conditions between checking and calling platform Trash APIs. A matching digest does not establish that an extra copy is semantically unnecessary.
