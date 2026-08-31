import os
import ssl
import urllib.request

secure = bool(os.getenv('TLS_CERTFILE'))
# Loopback-only probe: accept the deployment's own certificate, including private CAs.
context = ssl._create_unverified_context() if secure else None
urllib.request.urlopen(('https' if secure else 'http') + '://127.0.0.1:8000/healthz', timeout=3, context=context).close()
