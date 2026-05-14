#!/usr/bin/env python3
"""Postfix pipe transport: forwards email attachments to Papra's intake API."""

import base64
import configparser
import email
import hashlib
import hmac
import json
import logging
import os
import sys
import urllib.error
import urllib.request
import uuid

CONFIG_PATH = "/etc/email2papra.conf"
LOG_PATH = "/var/log/email2papra.log"

EX_OK = 0
EX_TEMPFAIL = 75

logging.basicConfig(
    filename=LOG_PATH,
    level=logging.INFO,
    format="%(asctime)s [email2papra] %(levelname)s %(message)s",
)
log = logging.getLogger("email2papra")


def load_config():
    cfg = configparser.ConfigParser()
    cfg.read(CONFIG_PATH)
    return cfg["papra"]


def build_multipart(boundary, email_json, attachments):
    """Build raw multipart/form-data bytes."""
    parts = []
    sep = b"--" + boundary

    parts.append(
        sep + b"\r\n"
        + b'Content-Disposition: form-data; name="email"\r\n\r\n'
        + email_json.encode()
        + b"\r\n"
    )

    for filename, mime_type, data in attachments:
        safe_name = filename.encode("utf-8", errors="replace")
        parts.append(
            sep + b"\r\n"
            + b'Content-Disposition: form-data; name="attachments[]"; filename="'
            + safe_name + b'"\r\n'
            + b"Content-Type: " + mime_type.encode() + b"\r\n\r\n"
            + data
            + b"\r\n"
        )

    return b"".join(parts) + sep + b"--\r\n"


def sign_body(body: bytes, secret: str) -> str:
    """HMAC-SHA256, Base64-encoded — matches @owlrelay/webhook verifySignature."""
    return base64.b64encode(
        hmac.new(secret.encode(), body, hashlib.sha256).digest()
    ).decode()


def extract_attachments(msg):
    """Return list of (filename, mime_type, bytes). Falls back to text body."""
    attachments = []
    for part in msg.walk():
        ct = part.get_content_type()
        disp = part.get("Content-Disposition", "")

        if ct == "application/pkcs7-signature" or ct == "application/x-pkcs7-signature":
            log.info("Überspringe S/MIME-Signatur")
            continue

        if "attachment" in disp or part.get_filename():
            filename = part.get_filename() or f"attachment.{part.get_content_subtype()}"
            payload = part.get_payload(decode=True)
            if payload:
                attachments.append((filename, ct, payload))

    if not attachments:
        # Fall back to plain text body
        body_part = msg.get_body(preferencelist=("plain",))
        if body_part:
            text = body_part.get_payload(decode=True) or b""
            subject = msg.get("Subject", "email")
            attachments.append((f"{subject[:40]}.txt", "text/plain", text))

    return attachments


def send_to_papra(cfg, from_addr, to_addr, attachments):
    webhook_url = cfg["webhook_url"]
    secret = cfg["webhook_secret"]
    intake_address = cfg.get("intake_address", to_addr)
    timeout = int(cfg.get("timeout", 30))

    email_json = json.dumps({
        "from": {"address": from_addr},
        "to": [{"address": intake_address}],
        "originalTo": [],
    })

    boundary = b"PapraBoundary" + uuid.uuid4().hex[:16].encode()
    body = build_multipart(boundary, email_json, attachments)
    signature = sign_body(body, secret)

    req = urllib.request.Request(
        webhook_url,
        data=body,
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary.decode()}",
            "X-Signature": signature,
            "User-Agent": "email2papra/1.0",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            log.info("Papra OK: HTTP %d", resp.status)
            return EX_OK
    except urllib.error.HTTPError as exc:
        body_bytes = exc.read()
        log.error("HTTP-Fehler %d — %s", exc.code, body_bytes)
        return EX_OK  # 4xx = permanent error, don't retry
    except (urllib.error.URLError, OSError) as exc:
        log.error("Netzwerkfehler — %s", exc)
        return EX_TEMPFAIL


def main():
    try:
        cfg = load_config()
    except KeyError:
        log.error("Konfigurationsabschnitt [papra] fehlt in %s", CONFIG_PATH)
        sys.exit(EX_TEMPFAIL)

    raw = sys.stdin.buffer.read()
    msg = email.message_from_bytes(raw)

    from_addr = email.utils.parseaddr(msg.get("From", ""))[1]
    to_header = msg.get("To", msg.get("Delivered-To", ""))
    to_addr = email.utils.parseaddr(to_header)[1]
    subject = msg.get("Subject", "(kein Betreff)")

    log.info(
        "Von: %s | An: %s → %s | Betreff: %s",
        from_addr,
        to_addr,
        cfg.get("intake_address", to_addr),
        subject,
    )

    attachments = extract_attachments(msg)
    if not attachments:
        log.warning("Keine Anhänge und kein Text-Body — Mail wird ignoriert")
        sys.exit(EX_OK)

    for filename, mime_type, data in attachments:
        log.info("Anhang: %s (%s, %d Bytes)", filename, mime_type, len(data))

    sys.exit(send_to_papra(cfg, from_addr, to_addr, attachments))


if __name__ == "__main__":
    main()
