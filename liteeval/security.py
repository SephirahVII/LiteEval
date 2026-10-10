"""Project API keys and env-referenced secrets; no credentials in configuration JSON."""
import hashlib
import hmac
import os
import re
import secrets
from urllib.parse import urlsplit

SENSITIVE = re.compile(r'^(authorization|api[_-]?key|access[_-]?token|secret|password|cookie)$', re.I)

def redact(value):
    if isinstance(value, dict):
        return {k: '[REDACTED]' if SENSITIVE.match(str(k)) else redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value,str):
        for name,secret in os.environ.items():
            if len(secret)>8 and re.search(r'(TOKEN|SECRET|PASSWORD|API_KEY)',name):value=value.replace(secret,'[REDACTED]')
    return value

def validate_no_secrets(value):
    if isinstance(value, dict):
        for k, v in value.items():
            if k=='headers_env':
                if not isinstance(v,dict) or not all(isinstance(ref,str) and re.fullmatch(r'[A-Z][A-Z0-9_]{0,100}',ref) for ref in v.values()):raise ValueError('headers_env values must be env references')
                continue
            if SENSITIVE.match(k):
                raise ValueError('Use environment variable references instead of literal secrets')
            validate_no_secrets(v)
    elif isinstance(value, list):
        for v in value:
            validate_no_secrets(v)

def secret_from_env(name):
    if not isinstance(name, str) or not re.fullmatch(r'[A-Z][A-Z0-9_]{0,100}', name):
        raise ValueError('Invalid secret env reference')
    value = os.getenv(name)
    if not value:
        raise ValueError(f'Required environment variable {name} is unset')
    return value

def checked_url(url):
    parts = urlsplit(url)
    if parts.scheme not in {'http', 'https'} or not parts.hostname or parts.username or parts.password:
        raise ValueError('Endpoint must be HTTP(S) without embedded credentials')
    if parts.fragment:
        raise ValueError('Endpoint fragments are not allowed')
    return url

def new_key():
    token = 'le_' + secrets.token_urlsafe(32)
    return token, hashlib.sha256(token.encode()).hexdigest()

def safe_error(exc):
    text = f'{type(exc).__name__}: {exc}'
    # Only redact configured credential variables, not arbitrary process environment content.
    for name, value in os.environ.items():
        if value and len(value) > 8 and re.search(r'(TOKEN|SECRET|PASSWORD|API_KEY)', name):
            text = text.replace(value, '[REDACTED]')
    return text[:2000]
