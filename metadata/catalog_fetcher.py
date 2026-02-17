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
