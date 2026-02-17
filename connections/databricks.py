from databricks import sql

def connect_databricks(server, http_path, token):
    return sql.connect(
        server_hostname=server,
        http_path=http_path,
        access_token=token
    )

