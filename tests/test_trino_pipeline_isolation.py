from validation_tool.migration.translator_service import TranslatorService
from migration.rule_engine import RuleEngine


class DummyEngine:
    def __init__(self, name):
        self.name = name
        self.calls = []

    def apply_pre_ast_translation(self, sql):
        self.calls.append(("pre_ast", sql))
        return sql

    def apply_rules(self, sql):
        self.calls.append(("rules", sql))
        return sql

    def apply_function_translation(self, sql):
        self.calls.append(("function_translation", sql))
        return sql


def test_trino_uses_trino_engine_only():
    service = TranslatorService()
    generic = DummyEngine("generic")
    trino = DummyEngine("trino")
    snowflake = DummyEngine("snowflake")

    service._components = {
        "rule_engine": generic,
        "snowflake_rule_engine": snowflake,
        "trino_rule_engine": trino,
    }

    selected = service._get_rule_engine_for_source("trino")

    assert selected is trino
    assert selected.name == "trino"
    assert generic.name == "generic"


def test_safe_cast_and_float64_rewrite_to_databricks_types():
    engine = RuleEngine([], [])

    result = engine.apply_rules("SAFE_CAST(col AS FLOAT64)")
    assert result == "TRY_CAST(col AS DOUBLE)"

    result = engine.apply_rules("SELECT SAFE_CAST(col AS FLOAT64) AS x FROM t")
    assert "TRY_CAST(col AS DOUBLE)" in result
    assert "SAFE_CAST" not in result.upper()
    assert "FLOAT64" not in result.upper()


def test_bigquery_syntax_is_rewritten_even_when_engine_is_trino_or_snowflake():
    for engine_name in ("trino", "snowflake"):
        translated = TranslatorService().run_pipeline(
            "SELECT SAFE_CAST(col AS FLOAT64) AS val",
            "gpt-5-nano",
            provider="OpenAI",
            api_key="",
            use_llm=False,
            source_engine=engine_name,
        )[0]
        assert "TRY_CAST" in translated.upper()
        assert "SAFE_CAST" not in translated.upper()
        assert "FLOAT64" not in translated.upper()
