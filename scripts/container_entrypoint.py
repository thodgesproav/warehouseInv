"""Single-process container launcher; data and credentials live only in /data."""
import os
from pathlib import Path

os.umask(0o077)
for directory in ('/data', '/data/images', '/data/backups'):
    Path(directory).mkdir(parents=True, exist_ok=True)
if not os.access('/data', os.W_OK):
    raise SystemExit('/data must be writable by UID 10001. Use a named Docker volume or fix bind-mount ownership.')
command = ['uvicorn', 'app.main:app', '--host', '0.0.0.0', '--port', '8000', '--workers', '1']
certificate, key = os.getenv('TLS_CERTFILE'), os.getenv('TLS_KEYFILE')
if certificate or key:
    if not certificate or not key or not Path(certificate).is_file() or not Path(key).is_file():
        raise SystemExit('TLS_CERTFILE and TLS_KEYFILE must both point to readable certificate files')
    command += ['--ssl-certfile', certificate, '--ssl-keyfile', key]
os.execvp('uvicorn', command)
