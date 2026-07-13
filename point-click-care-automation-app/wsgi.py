"""WSGI entry point for production hosting.

Run behind gunicorn + nginx, e.g.:
    gunicorn -w 3 -b 127.0.0.1:5000 wsgi:app

The database is initialised on import so the first request already has the
schema + default admin in place.
"""
from backend import database
from backend.server import app

database.init_db()

# When running behind a TLS-terminating reverse proxy (nginx), trust the
# X-Forwarded-* headers it sets so Flask sees the real scheme (https), host and
# client IP. Without this, url_for() and "Secure" cookies would think the
# request arrived over plain http. Only the immediate proxy (1 hop) is trusted.
from werkzeug.middleware.proxy_fix import ProxyFix  # noqa: E402

app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

if __name__ == "__main__":
    app.run()
