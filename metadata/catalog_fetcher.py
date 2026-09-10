def get_catalogs(engine, conn):
    if engine == "BigQuery":
        # BigQuery does not support listing projects
        return [conn.project]

    elif engine == "Snowflake":
        cur = conn.cursor()
        cur.execute("SHOW DATABASES")
        return [row[1] for row in cur.fetchall()]

    elif engine == "Databricks":
        cur = conn.cursor()
        cur.execute("SHOW CATALOGS")
        return [row[0] for row in cur.fetchall()]

    elif engine == "Trino":
        cur = conn.cursor()
        cur.execute("SHOW CATALOGS")
        rows = [row[0] for row in cur.fetchall()]
        cur.close()
        return rows

    elif engine == "Redshift":
        cur = conn.cursor()
        cur.execute("SELECT datname FROM pg_database WHERE datistemplate = false ORDER BY datname")
        rows = [row[0] for row in cur.fetchall()]
        cur.close()
        return rows


def get_schemas(engine, conn, catalog):
    if engine == "BigQuery":
        return [d.dataset_id for d in conn.list_datasets(project=catalog)]

    elif engine == "Snowflake":
        cur = conn.cursor()
        cur.execute(f"SHOW SCHEMAS IN DATABASE {catalog}")
        return [row[1] for row in cur.fetchall()]

    elif engine == "Databricks":
        cur = conn.cursor()
        cur.execute(f"SHOW SCHEMAS IN {catalog}")
        return [row[0] for row in cur.fetchall()]

    elif engine == "Trino":
        cur = conn.cursor()
        cur.execute(f"SHOW SCHEMAS FROM {catalog}")
        rows = [row[0] for row in cur.fetchall()]
        cur.close()
        return rows

    elif engine == "Redshift":
        cur = conn.cursor()
        cur.execute("SELECT schema_name FROM information_schema.schemata WHERE schema_name NOT LIKE 'pg_%' AND schema_name NOT IN ('information_schema') ORDER BY schema_name")
        rows = [row[0] for row in cur.fetchall()]
        cur.close()
        return rows


def get_tables(engine, conn, catalog, schema):
    if engine == "BigQuery":
        dataset_ref = f"{catalog}.{schema}"
        return [t.table_id for t in conn.list_tables(dataset_ref)]

    elif engine == "Snowflake":
        cur = conn.cursor()
        cur.execute(f"SHOW TABLES IN {catalog}.{schema}")
        return [row[1] for row in cur.fetchall()]

    elif engine == "Databricks":
        cur = conn.cursor()
        cur.execute(f"SHOW TABLES IN {catalog}.{schema}")
        return [row[1] for row in cur.fetchall()]

    elif engine == "Trino":
        cur = conn.cursor()
        cur.execute(f"SHOW TABLES FROM {catalog}.{schema}")
        rows = [row[0] for row in cur.fetchall()]
        cur.close()
        return rows

    elif engine == "Redshift":
        cur = conn.cursor()
        cur.execute(f"SELECT table_name FROM information_schema.tables WHERE table_schema = %s ORDER BY table_name", (schema,))
        rows = [row[0] for row in cur.fetchall()]
        cur.close()
        return rows
