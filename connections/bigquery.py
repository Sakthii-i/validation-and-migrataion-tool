from google.cloud import bigquery
from google.oauth2 import service_account

def connect_bigquery(project_id, key_path, location):
    credentials = service_account.Credentials.from_service_account_file(key_path)
    client = bigquery.Client(
        project=project_id,
        credentials=credentials,
        location=location
    )
    return client

