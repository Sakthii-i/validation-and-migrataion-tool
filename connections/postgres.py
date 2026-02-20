import psycopg2

# =============================
# HARDCODED POSTGRES CONNECTION (NeonDB)
# =============================
POSTGRES_CONFIG = {
    "host": "validation.postgres.database.azure.com",
    "port": 5432,
    "db": "postgres",  # Replace with your database name
    "user": "admin_post",  # Replace with your username
    "password": "Sherin_post",  # Replace with your password
    "sslmode": "require",  
}
