from migration.translator_service import TranslatorService
from migration.rule_engine import RuleEngine
import sys

# Patch apply_function_translation to log  
from migration.snowflake_rule_loader import SnowflakeRuleEngine
orig_sf_func = SnowflakeRuleEngine.apply_function_translation
orig_bq_func = RuleEngine.apply_function_translation

count = [0]

def new_sf_func(self, sql):
    result = orig_sf_func(self, sql)
    count[0] += 1
    if "INT" in sql.upper() or "INT" in result.upper():
        print(f"CALL {count[0]}: [SF.func_trans] IN={sql} OUT={result}", file=sys.stderr)
    return result

def new_bq_func(self, sql):
    result = orig_bq_func(self, sql)
    count[0] += 1
    if "INT" in sql.upper() or "INT" in result.upper():
        print(f"CALL {count[0]}: [BQ.func_trans] IN={sql} OUT={result}", file=sys.stderr)
    return result

SnowflakeRuleEngine.apply_function_translation = new_sf_func
RuleEngine.apply_function_translation = new_bq_func

service = TranslatorService()
sql = "SELECT col::INT AS x"
result = service.run_pipeline(sql, "gpt-5-nano", provider="OpenAI", api_key="", use_llm=False, source_engine="snowflake")[0]
print(result.strip())
