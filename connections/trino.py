import os
import socket


def connect_trino(
    host=None,
    port=None,
    user=None,
    catalog=None,
    schema=None,
    http_scheme=None,
    password=None,
):
    try:
        import trino
        from trino.auth import BasicAuthentication
    except Exception as exc:
        raise RuntimeError("Trino connector is not installed. Run pip install -r requirements.txt.") from exc

    host = (host or os.getenv("TRINO_HOST") or "localhost").strip()
    if host.lower() == "trino":
        try:
            socket.gethostbyname(host)
        except OSError:
            host = "localhost"
    port = int(port or os.getenv("TRINO_PORT") or 8080)
    user = (user or os.getenv("TRINO_USER") or "admin").strip()
    catalog = (catalog or os.getenv("TRINO_CATALOG") or "").strip() or None
    schema = (schema or os.getenv("TRINO_SCHEMA") or "").strip() or None
    http_scheme = (http_scheme or os.getenv("TRINO_HTTP_SCHEME") or "http").strip()
    password = (password or os.getenv("TRINO_PASSWORD") or "").strip()

    kwargs = {
        "host": host,
        "port": port,
        "user": user,
        "http_scheme": http_scheme,
    }
    if catalog:
        kwargs["catalog"] = catalog
    if schema:
        kwargs["schema"] = schema
    if password:
        kwargs["auth"] = BasicAuthentication(user, password)
        kwargs["http_scheme"] = "https"

    return trino.dbapi.connect(**kwargs)
