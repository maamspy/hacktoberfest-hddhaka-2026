"""`fieldkit discover` — probe the environment, then let Laya sort the damage.

Runs real checks (DNS, TLS reach, proxy and key env vars) and classifies
every failure with a four-option `choice` question. Four options with
short, distinct descriptions is exactly the shape Laya handles well;
this is the highest-accuracy primitive in the tool.
"""

from __future__ import annotations

import os
import socket
import ssl
from dataclasses import dataclass, field
from urllib.parse import urlparse

CATEGORIES = {
    "blocked": "firewall, network egress blocked, DNS does not resolve, TLS interception",
    "auth": "missing, malformed or rejected API key, expired credentials, 401 or 403",
    "quota": "rate limit hit, spending cap reached, 429, out of credit",
    "other": "anything else: timeouts, 5xx, malformed requests, client-side bugs",
}


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    # `fallback` is the deterministic guess. `category` is what Laya says
    # and it is what gets reported. Keeping them separate matters: the
    # whole point of the feature is that Laya sorts the errors, so
    # Laya's answer wins whenever it has one, and the deterministic
    # guess is only used when Laya is unavailable.
    fallback: str | None = None
    category: str | None = None
    confidence: float = 0.0

    def as_line(self) -> str:
        mark = "ok  " if self.ok else "FAIL"
        tail = "" if self.ok else f"  [{self.category} p={self.confidence:.2f}]"
        return f"  {mark} {self.name}: {self.detail}{tail}"


def _check_dns(host: str) -> Check:
    if not host:
        return Check("dns", False, "no host in endpoint URL", "other")
    try:
        socket.getaddrinfo(host, None)
        return Check("dns", True, f"{host} resolves")
    except socket.gaierror as exc:
        return Check("dns", False, f"{host} does not resolve: {exc}", "blocked")
    except Exception as exc:
        return Check("dns", False, f"{host} lookup failed: {exc}", "other")


def _check_tls(url: str, timeout: float = 6.0) -> Check:
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.hostname:
        return Check("tls", False, f"cannot parse endpoint URL {url!r}", "other")

    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((parsed.hostname, port), timeout=timeout):
            pass
    except (socket.timeout, TimeoutError):
        return Check("tls", False, f"connection to {parsed.hostname}:{port} timed out", "blocked")
    except OSError as exc:
        return Check("tls", False, f"cannot reach {parsed.hostname}:{port}: {exc}", "blocked")

    if parsed.scheme != "https":
        return Check("tls", True, f"{parsed.hostname}:{port} reachable (plaintext)")

    context = ssl.create_default_context()
    try:
        with socket.create_connection((parsed.hostname, port), timeout=timeout) as sock:
            with context.wrap_socket(sock, server_hostname=parsed.hostname):
                pass
    except ssl.SSLCertVerificationError as exc:
        return Check("tls", False, f"TLS rejected for {parsed.hostname}: {exc}", "blocked")
    except ssl.SSLError as exc:
        return Check("tls", False, f"TLS handshake failed for {parsed.hostname}: {exc}", "blocked")
    except OSError as exc:
        return Check("tls", False, f"TLS probe failed for {parsed.hostname}: {exc}", "blocked")

    return Check("tls", True, f"{parsed.hostname}:{port} TLS handshake ok")


def _check_proxy() -> Check:
    proxies = {
        name: os.environ[name]
        for name in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY")
        if os.environ.get(name)
    }
    for name in os.environ:
        if name.lower() in ("https_proxy", "http_proxy", "all_proxy") and name not in proxies:
            proxies[name] = os.environ[name]

    if proxies:
        listed = ", ".join(f"{k}={v}" for k, v in sorted(proxies.items()))
        return Check("proxy", True, f"proxy set: {listed}")
    return Check("proxy", True, "no proxy configured")


def _check_key(endpoint: str) -> Check:
    for name in ("GOOGLE_AI_STUDIO_API_KEY", "GEMINI_API_KEY"):
        if os.environ.get(name):
            return Check("key", True, f"{name} is set")
    return Check("key", False, "no Google AI Studio key in the environment", "auth")


def run_checks(endpoint: str, key_required: bool = True) -> list[Check]:
    checks = [_check_dns(urlparse(endpoint).hostname or "")]
    checks.append(_check_tls(endpoint))
    checks.append(_check_proxy())
    if key_required:
        checks.append(_check_key(endpoint))
    return checks


def classify(failures: list[Check], decide_fn) -> None:
    """Let Laya sort every failed check, in place.

    One batched question over the joined failure text keeps this to a
    single forward pass regardless of how many checks failed. Laya's
    answer replaces the deterministic fallback, which is the whole point
    of the feature.
    """
    pending = [check for check in failures if not check.ok]
    if not pending:
        return

    state = "\n".join(f"- {check.name}: {check.detail}" for check in pending)
    verdict = decide_fn(
        key="error_class",
        state=state,
        pairs=CATEGORIES,
        instructions=(
            "Classify this environment failure report. Pick the single category "
            "that best describes why the environment is not usable."
        ),
    )

    for check in pending:
        check.category = verdict.answer or check.fallback or "other"
        check.confidence = verdict.confidence


def apply_fallbacks(failures: list[Check]) -> None:
    """Used when Laya is unavailable: report the deterministic guess."""
    for check in failures:
        if check.ok:
            continue
        check.category = check.fallback or "other"
        check.confidence = 0.0



def summarise(checks: list[Check]) -> tuple[int, int]:
    passed = sum(1 for check in checks if check.ok)
    return passed, len(checks) - passed
