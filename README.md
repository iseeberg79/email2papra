# email2papra

A lightweight Postfix pipe transport script that forwards email attachments to [Papra's](https://github.com/papra-hq/papra) intake API.

**Use case:** Self-hosted alternative to [OwlRelay](https://owlrelay.email/) for feeding documents into Papra via email — no external service required.

## How it works

```
Email → Postfix (fetchmail or Sieve redirect)
      → Pipe transport
      → email2papra.py
      → HTTP POST → Papra intake API
```

The script reads a raw email from stdin, extracts attachments (falling back to the plain-text body if there are none), and posts them to Papra's `/api/intake-emails/ingest` endpoint with an HMAC-SHA256/Base64 signature matching Papra's `@owlrelay/webhook` verification.

## Requirements

- Python 3.6+ (stdlib only, no pip install needed)
- Postfix with pipe transport support
- Papra instance configured with `INTAKE_EMAILS_DRIVER=catch-all` (see step 1)

## Setup

### 1. Papra: docker-compose.yml

In your `docker-compose.yml` for Papra, comment out the OwlRelay lines and add the catch-all configuration below them:

```yaml
environment:
  - INTAKE_EMAILS_IS_ENABLED=true
  #      - INTAKE_EMAILS_DRIVER=owlrelay
  #      - INTAKE_EMAILS_WEBHOOK_SECRET=<old-owlrelay-secret>
  #      - OWLRELAY_API_KEY=<your-owlrelay-key>
  #      - OWLRELAY_WEBHOOK_URL=https://your-papra.example.com/api/intake-emails/ingest
  - INTAKE_EMAILS_DRIVER=catch-all
  - INTAKE_EMAILS_CATCH_ALL_DOMAIN=your-domain.example.com
  - INTAKE_EMAILS_WEBHOOK_SECRET=<your-generated-secret>
```

The `INTAKE_EMAILS_WEBHOOK_SECRET` must be at least 16 characters long — generate one with:

```bash
openssl rand -hex 32
```

This same value goes into `email2papra.conf` as `webhook_secret`.

Apply the changes:

```bash
docker compose up -d papra
```

### 2. Papra: configure intake email address

Open the Papra web UI and navigate to your organisation → **Intake Emails**.

**If you had an OwlRelay address before:**

1. Select the existing OwlRelay intake address and disable or delete it — it no longer receives mail now that the driver has changed

**Create the new catch-all address:**

2. Click **Create intake email** — Papra generates an address like `random-adjective-123@your-domain.example.com`
3. Copy that address and put it in `email2papra.conf` as `intake_address`
4. In the intake email settings, configure **Allowed senders** — add the email addresses permitted to submit documents (e.g. your own address, a shared mailbox). This is the equivalent of OwlRelay's allowed origins. Papra silently drops mail from unlisted senders.

> The generated address is the key that Papra uses to match incoming requests.
> All mail routed through this script is posted under that address regardless of
> the original `To:` header, so you can keep using a human-friendly address
> (e.g. `papra@your-domain.example.com`) for sending.

### 3. Install the script

```bash
cp email2papra.py /usr/local/bin/email2papra.py
chmod 755 /usr/local/bin/email2papra.py

cp email2papra.conf.example /etc/email2papra.conf
chmod 640 /etc/email2papra.conf
chown root:nobody /etc/email2papra.conf

touch /var/log/email2papra.log
chown nobody:nogroup /var/log/email2papra.log
```

Edit `/etc/email2papra.conf` with your values.

### 4. Postfix pipe transport

Add to `/etc/postfix/master.cf`:

```
papra_pipe  unix  -  n  n  -  1  pipe
  flags=Rq user=nobody argv=/usr/local/bin/email2papra.py
```

Add to `/etc/postfix/virtual` (or `.../virtual.db` via `postmap`):

```
papra@your-domain.example.com    papra-intake@localhost
```

Add to `/etc/postfix/papra_transport`:

```
papra-intake@localhost    papra_pipe:
```

```bash
postmap /etc/postfix/virtual
postmap /etc/postfix/papra_transport
```

Add `hash:/etc/postfix/papra_transport` to `transport_maps` in `main.cf` (or `main.cf.local` on UCS/Univention):

```
transport_maps = hash:/etc/postfix/transport, ldap:/etc/postfix/ldap.transport, hash:/etc/postfix/papra_transport
```

```bash
postfix check && systemctl reload postfix
```

### 5. Fetchmail (optional)

To pull from an external mailbox (e.g. public mail provider):

```
poll 'pop3.your-mailprovider.mail' with proto POP3 auth password
  user 'papra@your-domain.example.com' there
  with password 'SECRET'
  is 'papra@your-internal-domain.example.com' here
  options ssl fetchall
```

### 6. Sieve redirect (optional)

To forward matching emails from existing mailboxes:

```sieve
if anyof (
    header :contains "Subject" ["Rechnung", "Invoice", "Mahnung"],
    header :contains "Subject" ["Bestellung", "Order"]
) {
    redirect :copy "papra@your-internal-domain.example.com";
}
```

## Signature scheme

Papra's `@owlrelay/webhook` package verifies signatures using:

```
X-Signature: base64( HMAC-SHA256( rawMultipartBody, webhookSecret ) )
```

Note: **Base64**, not hex — this differs from many common webhook implementations.

## UCS / Univention notes

- `main.cf` changes belong in `/etc/postfix/main.cf.local` (applied via `ucr commit /etc/postfix/main.cf`)
- `master.cf` has no `.local` equivalent; append directly (survives normal updates)
- Fetchmail entries managed via LDAP can be added at the end of `/etc/fetchmailrc`

## License

MIT
