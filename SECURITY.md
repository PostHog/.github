# Security Policy

## Supported Versions

We always recommend using the latest version of PostHog to ensure you get all security updates.

## Reporting a Vulnerability

Please report security vulnerabilities to security-reports@posthog.com.

We currently operate a vulnerability disclosure program and reward valid, high quality reports with merch.

## Revoking a Leaked API Key or Token

If a PostHog credential is exposed, revoke it yourself through the public key-revocation API. Send an unauthenticated POST request with the leaked token. If the token matches a real credential, the endpoint revokes it immediately and emails the owner. This covers personal API keys, project secret API keys, and OAuth access and refresh tokens. A revoked OAuth access token also revokes the paired refresh token.

The endpoint checks only the region it runs on. Check both regions if you do not know which one issued the token:

- US: `https://app.posthog.com/api/revoke_leaked_key`
- EU: `https://eu.posthog.com/api/revoke_leaked_key`

```bash
curl -X POST https://app.posthog.com/api/revoke_leaked_key \
  -H 'Content-Type: application/json' \
  -d '{"token": "LEAKED_TOKEN"}'
```

A `"found": false` response means the token was not live in that region, not that it is safe everywhere.
