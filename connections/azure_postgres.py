import psycopg2

def connect_azure_postgres():
    return psycopg2.connect(
        host="validation.postgres.database.azure.com",
        database="postgres",
        user="admin_post",
        password="Sherin_post",
        port=5432,
        sslmode="require"
    )
