from migration.translator_service import TranslatorService

service = TranslatorService()

test_cases = [
    ('bigquery', 'SELECT CAST(col AS INT64) AS x'),  
    ('bigquery', 'SELECT CAST(col AS FLOAT64) AS x'),
    ('snowflake', 'SELECT CAST(col AS INT) AS x'),
    ('snowflake', 'SELECT CAST(col AS DOUBLE) AS x'),
    ('snowflake', 'SELECT col::INT AS x'),
    ('snowflake', 'SELECT col::DOUBLE AS x'),
    ('trino', 'SELECT CAST(col AS INTEGER) AS x'),
    ('trino', 'SELECT CAST(col AS DOUBLE) AS x'),
]

print("="*70)
print("TYPE CONVERSION TEST")
print("="*70)

for source_engine, sql in test_cases:
    result = service.run_pipeline(
        sql, 
        'gpt-5-nano', 
        provider='OpenAI', 
        api_key='', 
        use_llm=False, 
        source_engine=source_engine
    )[0].strip()
    
    print(f"\nSource: {source_engine}")
    print(f"Input:  {sql}")
    print(f"Output: {result}")
