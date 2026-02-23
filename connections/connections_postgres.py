import psycopg2

def connect_postgres(host, db, user, password, port=5432):
    return psycopg2.connect(
        host=host,
        database=db,
        user=user,
        password=password,
        port=port
    )
