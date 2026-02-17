import snowflake.connector

def connect_snowflake(account, user, password, warehouse, role):
    return snowflake.connector.connect(
        account=account,
        user=user,
        password=password,
        warehouse=warehouse,
        role=role
    )

