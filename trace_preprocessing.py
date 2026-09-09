#!/usr/bin/env python3
"""
Check if SQL preprocessing is causing the type conversion.
"""
from migration.ast_transformer import SQLPreprocessor

sql = 'SELECT CAST(col AS INT) AS x'
print(f"Input: {sql}\n")

# Step 1: extract_comments
sql_no_comments, comment_map = SQLPreprocessor.extract_comments(sql)
print(f"After extract_comments: {sql_no_comments}")

# Step 2: convert_dbt_partition_config
sql2 = SQLPreprocessor.convert_dbt_partition_config(sql_no_comments)
print(f"After convert_dbt_partition_config: {sql2}")

# Step 3: clean_sql
normalized = SQLPreprocessor.clean_sql(sql2)
print(f"After clean_sql: {normalized}")

# Step 4: remove_comment_placeholders
llm_context = SQLPreprocessor.remove_comment_placeholders(normalized)
print(f"After remove_comment_placeholders: {llm_context}")
