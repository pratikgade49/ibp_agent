"""Create a short-lived CF interpolation file from the private root .env."""
import json
import os
import sys
import tempfile
from pathlib import Path

from dotenv import dotenv_values

from auth.store import validate_user


def main():
    root = Path(__file__).resolve().parent.parent
    values = dotenv_values(root / '.env')
    username = values.get('AUTH_ADMIN_USERNAME') or os.environ.get('AUTH_ADMIN_USERNAME', '')
    password = values.get('AUTH_ADMIN_PASSWORD') or os.environ.get('AUTH_ADMIN_PASSWORD', '')
    if password.startswith('REPLACE_'):
        raise ValueError('Replace the example administrator password with a unique password.')
    validate_user(username, password, 'admin')
    fd, path = tempfile.mkstemp(prefix='cf-auth-vars-', suffix='.json')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump({'auth-admin-username': username, 'auth-admin-password': password}, handle)
        print(path)
    except BaseException:
        os.unlink(path)
        raise


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError) as exc:
        print(f'Invalid initial admin configuration: {exc}', file=sys.stderr)
        raise SystemExit(1)
