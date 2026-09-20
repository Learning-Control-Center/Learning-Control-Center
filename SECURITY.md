# Security policy

## Reporting a vulnerability

Please report suspected vulnerabilities privately by email to
[contact@waqsea.com](mailto:contact@waqsea.com). Do not open a public issue containing exploit
details, credentials, personal data, or information that could put deployed instances at risk.

Include the affected version or commit, deployment context, reproduction steps, observed impact,
and any suggested mitigation that is safe to share. Encrypt sensitive supporting material before
sending it and arrange the transfer method by email first.

No response or remediation SLA is promised. Reports will be assessed according to severity,
reproducibility, affected versions, and available maintainer capacity.

## Supported versions

Security fixes are prepared for the current published release line. Operators should use deliberate
tagged releases, review release notes, keep Ubuntu and Caddy patched, and follow the controlled
update procedure in [`docs/UPDATES.md`](docs/UPDATES.md).

## Deployment responsibility

Learning Control Center is self-hosted. Operators are responsible for TLS and DNS operation,
firewall policy, host updates, restrictive configuration and database permissions, backup custody,
and removing the one-time bootstrap secret after initialization. The production runbook is
[`docs/PRODUCTION_OPERATIONS.md`](docs/PRODUCTION_OPERATIONS.md).
