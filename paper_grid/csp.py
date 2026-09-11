"""CSP for trusted dashboard templates, without authorizing arbitrary inline code."""
import base64
import hashlib
import re
import secrets

NONCE_BYTES = 32
INLINE_TAG = re.compile(rb'<(script|style)(\s[^>]*|)>', re.IGNORECASE)
INLINE_BLOCK = re.compile(rb'<(script|style)(?:\s[^>]*|)>(.*?)</\1\s*>', re.IGNORECASE | re.DOTALL)
POLICY_SUFFIX = ("connect-src 'self'; img-src 'self' data:; object-src 'none'; "
                 "frame-ancestors 'none'; base-uri 'none'; form-action 'none'; "
                 "script-src-attr 'none'; style-src-attr 'none'")


def policy(scripts=(), styles=()):
    script_sources = ' '.join(scripts) or "'none'"
    style_sources = ' '.join(styles) or "'none'"
    return (f"default-src 'self'; script-src {script_sources}; "
            f"style-src {style_sources}; {POLICY_SUFFIX}")


def nonce_document(content, *, scripts=False):
    """Only pass reviewed dashboard templates with scripts=True, never audit data."""
    nonce = secrets.token_urlsafe(NONCE_BYTES)
    source = f"'nonce-{nonce}'"

    def add_nonce(match):
        if match.group(1).lower() == b'script' and not scripts:
            return match.group(0)
        return match.group(0)[:-1] + f' nonce="{nonce}">'.encode()

    document = INLINE_TAG.sub(add_nonce, content)
    return document, policy((source,) if scripts else (), (source,))


def static_policy(content, *, scripts=True):
    """Hash exact inline bytes; static hosts cannot issue fresh response nonces."""
    sources = {'script': set(), 'style': set()}
    for match in INLINE_BLOCK.finditer(content):
        digest = base64.b64encode(hashlib.sha256(match.group(2)).digest()).decode()
        sources[match.group(1).decode().lower()].add(f"'sha256-{digest}'")
    script_sources = tuple(sorted(sources['script'])) if scripts else ()
    return policy(script_sources, tuple(sorted(sources['style'])))
