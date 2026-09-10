from migration.snowflake_rule_loader import SnowflakeRuleEngine

# Create a snowflake engine with empty lists
engine = SnowflakeRuleEngine([], [])

# Count how many pattern_rules it has
print(f'Number of pattern rules: {len(engine.pattern_rules)}')

# Look for any rules that might convert INT or DOUBLE
for i, rule in enumerate(engine.pattern_rules):
    pattern_str = str(rule['pattern'])
    replacement_str = str(rule['replacement'])
    if 'INT' in pattern_str or 'INT' in replacement_str or 'DOUBLE' in pattern_str or 'DOUBLE' in replacement_str or 'FLOAT' in pattern_str or 'FLOAT' in replacement_str:
        print(f'\nRule {i}:')
        print(f'  Pattern: {pattern_str[:100]}')
        print(f'  Replacement: {replacement_str[:100]}')
