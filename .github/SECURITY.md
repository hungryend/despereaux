# Security Policy

## Supported versions

despereaux ships as a rolling Docker image. Security fixes land on `main` and are
published in the next `ghcr.io/hungryend/despereaux:latest` build; tagged releases
receive fixes on a best-effort basis.

| Version | Supported |
| --- | --- |
| `main` / `:latest` | ✅ |
| older tags | ⚠️ best-effort |

## Reporting a vulnerability

**Please do not open a public issue for security vulnerabilities.**

Report privately through GitHub's **[Report a vulnerability](https://github.com/hungryend/despereaux/security/advisories/new)**
button (the repository's **Security → Advisories** tab). This opens a private
advisory visible only to you and the maintainers.

Please include:

- a description of the issue and its impact,
- steps to reproduce or a proof of concept,
- the affected version / commit, and
- any suggested remediation, if you have one.

## What to expect

- **Acknowledgement** within a few days.
- An initial severity assessment and triage.
- **Coordinated disclosure:** we'll agree on a fix and a disclosure timeline, and
  credit you in the published advisory unless you'd prefer to remain anonymous.

## Scope

**In scope:** the despereaux server (FastAPI backend, in-browser reader, and the
Docker image) in this repository.

**Out of scope:** vulnerabilities in third-party dependencies (please report those
upstream — Dependabot tracks them here), and issues that require privileged access
to a deployment you don't control.
