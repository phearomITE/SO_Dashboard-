# Email sign-in

All valid email providers can receive a code. One outgoing SMTP account is required; recipient providers do not need separate setup.

Set these in .env (never commit credentials):

```
SMTP_HOST=your-mail-provider-host
SMTP_PORT=587
SMTP_SECURITY=starttls
SMTP_FROM=SO Alert Dashboard <your-sender@example.com>
SMTP_USERNAME=your-sender@example.com
SMTP_PASSWORD=your-app-password-or-SMTP-password
DASHBOARD_ADMIN_EMAILS=owner@example.com,another-admin@example.com
DASHBOARD_COOKIE_SECURE=true
```

For implicit TLS use SMTP_SECURITY=ssl and SMTP_PORT=465. Follow the sender provider's supported SMTP authentication policy. App passwords or an SMTP relay account may be needed; OAuth-only mailboxes need a compatible SMTP relay. Sender address must be authorized by that provider. Never use ordinary unencrypted SMTP.

All verified emails receive view/export access to dashboard data. Only addresses in DASHBOARD_ADMIN_EMAILS can change master data, AL, settings, generate/modify GP reports or run Hermes processing. No email-domain restriction is imposed. Set the admin addresses before activation. Access roles are checked on every request.

Codes expire after 10 minutes, allow five attempts, and can be used only once. Requests have a 60-second resend cooldown plus per-address/hour and socket-peer limits. Redis must be available; sign-in fails closed if it is unavailable. Only keyed code digests are stored, and codes are never returned by the API or logged. Access sessions last eight hours. A secure HttpOnly remembered-device cookie restores access automatically for 90 days on the same browser, including after Redis restart/eviction. PostgreSQL dashboard_users_v1 stores verified emails and timestamps; dashboard_sessions_v1 stores token hashes, device descriptions, expiry and revocation. Logout revokes the remembered device and clears cookies. New browsers, cleared cookies, revocation or 90-day expiry require a new verification code. Existing still-valid verified email sessions are upgraded on the next /auth/me request. Shared-secret sign-in and old shared-secret sessions are disabled.

Dashboard APIs and report images require authentication. Read-only images use an HttpOnly SameSite=Strict cookie. Mutations additionally require the bearer session token, preventing cookie-only cross-site changes. Keep DASHBOARD_COOKIE_SECURE=true on the public HTTPS URL. Local HTTP image testing needs false locally; do not use false for public deployment.

After configuring the sender, activate from this project directory:

```
docker compose up -d --build dashboard
```

Test a real inbox, enter the code, check view/export access, then test an admin address and logout. No real verification email is sent by automated tests.
