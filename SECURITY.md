# Security Policy

## Supported versions

Noval is pre-1.0. Security fixes target the latest `0.10.x` release and `main`.
Older minor versions may receive guidance but are not guaranteed patches.

## Reporting a vulnerability

Do not report vulnerabilities, credentials, private Session content, or exploit
details in a public Issue.

Use GitHub's private security-advisory flow:

https://github.com/kestiny18/Noval/security/advisories/new

Include:

- affected version or commit;
- the violated boundary (permission, confinement, process isolation, redaction,
  Provider replay state, Session integrity, or another seam);
- a minimal sanitized reproduction;
- realistic impact and required preconditions;
- any proposed mitigation.

Do not include real credentials, customer data, proprietary repositories, or raw
chain-of-thought. Synthetic canaries and minimal fixtures are preferred.

## Response expectations

The maintainer will acknowledge a valid private report when it is reviewed,
coordinate a fix and release when appropriate, and credit the reporter unless
anonymity is requested. Noval does not currently promise a formal response-time
SLA.

## Security model boundary

Noval reduces risk at the model-to-world execution boundary; it is not a
complete host security product. In particular:

- `FULL_ACCESS` disables permission prompts but not confinement or sandboxing;
- `NoSandbox` explicitly means hard process isolation is unavailable;
- persistent Session content is plaintext;
- a project Hook or Skill is executable project content and should be reviewed;
- host authentication, multi-tenant isolation, and secret storage remain host
  responsibilities.
