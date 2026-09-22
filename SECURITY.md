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

Security fixes are prepared for the current published code line. Normal installation and update
resolve GitHub public `main` to one exact commit SHA, then request the pinned bootstrap/source
archive over HTTPS from that SHA before host mutation. GitHub is the chosen source-host trust
boundary; this does not claim provider-independent cryptographic authenticity. Private Forgejo
is development infrastructure, not a normal production source. Tags and
Releases remain optional immutable snapshots for pinned deployments and review. Operators should
deliberately choose when to invoke the controlled update procedure in
[`docs/UPDATES.md`](docs/UPDATES.md), review relevant changes, and keep Ubuntu and Caddy patched;
LCC does not update itself in the background. Ubuntu packages use the host's configured APT
authentication, candidate selection, and pinning policy; LCC never bypasses package authentication
or edits repository/key definitions to install itself. Production secrets are root-controlled, the
application service runs unprivileged and binds only to loopback, and public traffic needs HTTPS
through managed Caddy or an explicitly configured same-host reverse proxy.

## Deployment responsibility

Learning Control Center is self-hosted. Operators are responsible for TLS and DNS operation,
firewall policy, host updates, restrictive configuration and database permissions, backup custody,
and removing the one-time bootstrap secret after initialization. The production runbook is
[`docs/PRODUCTION_OPERATIONS.md`](docs/PRODUCTION_OPERATIONS.md).
