import snowflake.connector

def connect_snowflake(account, user, password, warehouse, role=None, database=None, schema=None):
    kwargs = {
        "account": account,
        "user": user,
        "password": password,
        "warehouse": warehouse,
        "role": role,
    }
    if database:
        kwargs["database"] = database
    if schema:
        kwargs["schema"] = schema
    return snowflake.connector.connect(**kwargs)

