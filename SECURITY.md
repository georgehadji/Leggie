# Security Policy

## Reporting a Vulnerability

Please report security vulnerabilities by emailing the maintainer(s) at a
private address (maintainers will publish a public channel once the project
has a stable maintainership).

**Do not open a public issue for a security vulnerability.** To report:

- Email: `security@leggie.dev` (placeholder — replace with the actual address)
- Include: a description of the vulnerability, the affected version, steps to
  reproduce, and its potential impact.

You should receive an acknowledgement within 48 hours. Maintainers will work
with you to confirm the issue and disclose it responsibly.

## Supported Versions

| Version | Supported          |
|---------|--------------------|
| 0.x     | Active development (best-effort) |
| < 0.1   | Not supported       |

## Security Posture

- **Hermetic test suite** — the test suite makes zero outbound connections
  (enforced by a socket guard), so credentials are never leaked during tests.
- **No prompt-injection blind spots** — `PromptHardeningDecorator` quarantines
  untrusted document text before it reaches the LLM.
- **Input caps** — `BoundedIngestor` prevents oversized / decompression-bomb
  documents from exhausting the host.
- **Cost ceiling** — the `$5/run` budget is fully enforced by reserve→settle.

## Data handling

See [DATA_HANDLING.md](docs/DATA_HANDLING.md) for how bill text is handled and
which providers receive it via OpenRouter.
