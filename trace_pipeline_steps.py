#!/usr/bin/env python3
"""
Trace INT -> BIGINT conversion step by step through the pipeline.
"""
import sqlglot
from migration.snowflake_rule_loader import SnowflakeRuleEngine
from migration.ast_transformer import ExpressionOptimizer

sql_input = 'SELECT CAST(col AS INT) AS x'
print(f"Input: {sql_input}\n")

# Step 1: sqlglot transpile
print("=" * 60)
print("STEP 1: sqlglot transpile (Snowflake -> Databricks)")
print("=" * 60)
tree = sqlglot.parse_one(sql_input, read='snowflake')
sqlglot_output = tree.sql(dialect='databricks', pretty=True)
print(f"Output: {sqlglot_output}\n")

# Step 2: apply_pre_ast_translation
print("=" * 60)
print("STEP 2: apply_pre_ast_translation")
print("=" * 60)
pre_ast_output = SnowflakeRuleEngine.apply_pre_ast_translation(sqlglot_output)
print(f"Output: {pre_ast_output}\n")

# Step 3: apply_rules
print("=" * 60)
print("STEP 3: apply_rules")
print("=" * 60)
sf_engine = SnowflakeRuleEngine([], [])
rules_output = sf_engine.apply_rules(pre_ast_output)
print(f"Output: {rules_output}\n")

# Step 4: apply_function_translation
print("=" * 60)
print("STEP 4: apply_function_translation")
print("=" * 60)
func_trans_output = sf_engine.apply_function_translation(rules_output)
print(f"Output: {func_trans_output}\n")

# Step 5: ExpressionOptimizer.optimize
print("=" * 60)
print("STEP 5: ExpressionOptimizer.optimize")
print("=" * 60)
optimized_output = ExpressionOptimizer.optimize(func_trans_output)
print(f"Output: {optimized_output}\n")
