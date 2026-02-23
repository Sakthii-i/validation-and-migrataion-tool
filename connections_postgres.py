import os
import psycopg2

def get_postgres_conn():
    return psycopg2.connect(
        host=os.getenv("sakthi.postgres.database.azure.com"),
        database=os.getenv("postgres"),
        user=os.getenv("sakthi"),
        password=os.getenv("Petchi@2811"),
        port=5432,
        sslmode="require"
    )

