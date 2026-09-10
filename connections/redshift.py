import os


def connect_redshift(host=None, port=None, database=None, user=None, password=None, schema=None):
    try:
        import psycopg2
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("psycopg2 is not installed. Run pip install -r requirements.txt.") from exc

    host = (host or os.getenv("REDSHIFT_HOST") or "").strip()
    port = int(port or os.getenv("REDSHIFT_PORT") or 5439)
    database = (database or os.getenv("REDSHIFT_DATABASE") or "").strip()
    user = (user or os.getenv("REDSHIFT_USER") or "").strip()
    password = (password or os.getenv("REDSHIFT_PASSWORD") or "").strip()
    schema = (schema or os.getenv("REDSHIFT_SCHEMA") or "").strip()

    if not (host and database and user and password):
        return None

    conn_kwargs = {
        "host": host,
        "port": port,
        "dbname": database,
        "user": user,
        "password": password,
    }
    if schema:
        conn_kwargs["options"] = f"-c search_path={schema}"

    return psycopg2.connect(**conn_kwargs)
