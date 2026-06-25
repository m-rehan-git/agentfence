# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 0.3.x   | :white_check_mark: |

## Reporting a Vulnerability

If you discover a security vulnerability in Sentinel, please report it responsibly:

1. **Email**: Send details to security@sentinel.dev
2. **Do NOT** open a public GitHub issue for security vulnerabilities
3. Include:
   - Description of the vulnerability
   - Steps to reproduce
   - Potential impact
   - Suggested fix (if any)

We aim to acknowledge reports within 48 hours and provide a fix timeline within 7 days.

## Security Considerations

Sentinel is a security tool. We take the security of this tool seriously:

- All security-relevant events are logged to a tamper-evident audit chain
- The gateway should be deployed with API key authentication enabled
- Never expose the gateway directly to the public internet without proper auth
- Keep your `AF_GATEWAY_API_KEY` secret and rotate it regularly
- Agent API keys are stored as SHA-256 hashes — the raw key is only shown at creation time
