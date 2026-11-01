"""Single-owner browser sessions, separate from SDK/admin bearer credentials."""
import hashlib
import hmac
from http.cookies import SimpleCookie, CookieError
import secrets
import time
from urllib.parse import urlsplit

COOKIE_NAME = 'liteeval_session'
IDLE_SECONDS = 8 * 3600
MAX_SECONDS = 7 * 86400
PASSWORD_ITERATIONS = 600_000


class AuthError(Exception):
    def __init__(self, status, message):
        self.status = status
        super().__init__(message)


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def csrf_token(token):
    return hmac.new(token.encode(), b'liteeval-browser-csrf-v1', hashlib.sha256).hexdigest()


def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    value = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), PASSWORD_ITERATIONS)
    return f'pbkdf2_sha256${PASSWORD_ITERATIONS}${salt}${value.hex()}'


def verify_password(password, stored):
    _, iterations, salt, expected = stored.split('$')
    value = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), int(iterations))
    return hmac.compare_digest(value.hex(), expected)


def cookie_token(header):
    try:
        cookie = SimpleCookie()
        cookie.load(header or '')
        return cookie[COOKIE_NAME].value if COOKIE_NAME in cookie else None
    except CookieError:
        return None


def cookie_header(token, secure=False, clear=False):
    value = f'{COOKIE_NAME}={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={0 if clear else MAX_SECONDS}'
    return value + ('; Secure' if secure else '')


def normalize_origin(value):
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password:
            return None
        if parsed.path not in ('', '/') or parsed.query or parsed.fragment:
            return None
        return parsed.scheme, parsed.hostname.lower(), parsed.port or (443 if parsed.scheme == 'https' else 80)
    except ValueError:
        return None


def check_origin(headers, expected_origin):
    origin = normalize_origin(headers.get('Origin', ''))
    expected = normalize_origin(expected_origin)
    if not origin or not expected or origin != expected:
        raise AuthError(403, 'A matching Origin header is required')


class BrowserSessions:
    def __init__(self, store):
        self.store = store

    def configure_owner(self, username, password):
        if not isinstance(username, str) or not 1 <= len(username.strip()) <= 100:
            raise ValueError('Username must contain 1..100 characters')
        if not isinstance(password, str) or not 12 <= len(password) <= 1024:
            raise ValueError('Password must contain 12..1024 characters')
        encoded = password_hash(password)
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('INSERT INTO owner_account VALUES (1,?,?) ON CONFLICT(id) DO UPDATE SET username=excluded.username,password_hash=excluded.password_hash',
                       (username.strip(), encoded))
            db.execute('DELETE FROM browser_sessions')
            db.execute('DELETE FROM login_limits')

    def login(self, username, password, client):
        if not isinstance(username, str) or not isinstance(password, str) or len(username) > 100 or len(password) > 1024:
            raise AuthError(400, 'Invalid login fields')
        stamp = time.time()
        error = None
        token = None
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM login_limits WHERE window_start<?', (stamp - 600,))
            db.execute('DELETE FROM browser_sessions WHERE expires_at<=? OR last_seen<=?', (stamp, stamp - IDLE_SECONDS))
            owner = db.execute('SELECT * FROM owner_account WHERE id=1').fetchone()
            limit = db.execute('SELECT failures FROM login_limits WHERE client=?', (client,)).fetchone()
            if not owner:
                error = AuthError(503, 'Owner is not configured; run owner-init first')
            elif limit and limit[0] >= 5:
                error = AuthError(429, 'Too many failed logins; retry after the 10 minute window')
            else:
                valid_password = verify_password(password, owner['password_hash'])
                if not hmac.compare_digest(username.encode(), owner['username'].encode()) or not valid_password:
                    db.execute('INSERT INTO login_limits VALUES (?,1,?) ON CONFLICT(client) DO UPDATE SET failures=failures+1', (client, stamp))
                    error = AuthError(401, 'Invalid username or password')
                else:
                    db.execute('DELETE FROM login_limits WHERE client=?', (client,))
                    token = secrets.token_urlsafe(32)
                    db.execute('INSERT INTO browser_sessions VALUES (?,?,?,?,?)',
                               (token_hash(token), username, stamp, stamp, stamp + MAX_SECONDS))
        # Raise outside the transaction so failed-login counters are committed.
        if error:
            raise error
        return token

    def authenticate(self, token):
        if not token or len(token) > 200:
            raise AuthError(401, 'Browser session required')
        stamp = time.time()
        with self.store.connect() as db:
            row = db.execute('SELECT * FROM browser_sessions WHERE token_hash=? AND expires_at>? AND last_seen>?',
                             (token_hash(token), stamp, stamp - IDLE_SECONDS)).fetchone()
            if not row:
                raise AuthError(401, 'Browser session expired or revoked')
            if stamp - row['last_seen'] >= 60:
                db.execute('UPDATE browser_sessions SET last_seen=? WHERE token_hash=?', (stamp, token_hash(token)))
        return {'admin': True, 'project_id': None, 'username': row['username'], 'auth_type': 'session'}

    def revoke(self, token):
        with self.store.connect() as db:
            db.execute('DELETE FROM browser_sessions WHERE token_hash=?', (token_hash(token),))
