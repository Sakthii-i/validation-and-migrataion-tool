"""
Query Complexity Analyzer - Based on the Databricks Labs DQX Analyzer framework

Analyzes SQL queries and categorizes them into complexity levels:
- SIMPLE: Single-table operations, basic filters, projections
- MEDIUM: Joins, aggregations, CTEs, window functions, subqueries
- COMPLEX: Nested subqueries, union chains, correlated subqueries, vendor-specific features
"""

import re
from typing import Dict, List, Optional, Tuple
from enum import Enum


class ComplexityLevel(str, Enum):
    """Complexity levels based on DQX Analyzer framework"""
    SIMPLE = "SIMPLE"
    MEDIUM = "MEDIUM"
    COMPLEX = "COMPLEX"


class QueryComplexityAnalyzer:
    """
    Analyzes query complexity using static SQL parsing patterns.
    Does not require actual SQL execution - based on syntactic patterns.
    """

    # SQL keywords that indicate complexity
    MEDIUM_INDICATORS = {
        r'\bJOIN\b': 'join',
        r'\bGROUP\s+BY\b': 'aggregation',
        r'\bHAVING\b': 'aggregation_filter',
        r'\bWINDOW\b|\bOVER\b': 'window_function',
        r'\bCTE\b|\bWITH\b': 'cte',
        r'\bSUBQUERY\b|\bIN\s*\(\s*SELECT\b': 'subquery',
        r'\bUNION\b|\bINTERSECT\b|\bEXCEPT\b': 'set_operations',
        # "Cosmetic"/common clauses; these should contribute only slightly.
        r'\bDISTINCT\b': 'deduplication',
        r'\bORDER\s+BY\b': 'sorting',
        r'\bLIMIT\b|\bOFFSET\b': 'pagination',
        r'\bCASE\s+WHEN\b': 'conditional_logic',
    }

    COMPLEX_INDICATORS = {
        r'\bCORRELATED\b|EXISTS\s*\(\s*SELECT': 'correlated_subquery',
        r'(?:SELECT|FROM)\s*\([^)]*(?:SELECT|FROM)[^)]*\)\s*(?:SELECT|FROM)': 'nested_subquery_chain',
        r'\bUNION\s+ALL\s+SELECT.*UNION\s+ALL': 'union_chain',
        r'(?:LEFT|RIGHT|FULL|INNER|CROSS)\s+JOIN.*(?:LEFT|RIGHT|FULL|INNER|CROSS)\s+JOIN.*(?:LEFT|RIGHT|FULL|INNER|CROSS)\s+JOIN': 'multiple_joins_chain',
        r'\bRECURSIVE\b': 'recursive_cte',
        # Type casting alone is not usually "complex" (it is common in practice),
        # so we give it a low weight vs true structural patterns.
        r'(?:CAST|CONVERT|PARSE)\s*\(': 'type_casting',
        r'\bLATERAL\b': 'lateral_join',
        r'\bPIVOT\b|\bUNPIVOT\b': 'pivot_operations',
        r'\bGENERATE\b|\bEXPLODE\b': 'array_operations',
        r'\bSTRUCT<|ARRAY<|MAP<': 'complex_data_types',
        r'(?:JSON|XML)': 'semi_structured_data',
    }

    VENDOR_SPECIFIC_FUNCTIONS = {
        # BigQuery functions
        'ARRAY_AGG', 'STRUCT', 'FLATTEN', 'CROSS_APPLY', 'ARRAY_CONCAT',
        'GENERATE_ARRAY', 'GENERATE_DATE_ARRAY', 'GENERATE_TIMESTAMP_ARRAY',
        'JSON_EXTRACT', 'JSON_EXTRACT_ARRAY', 'JSON_EXTRACT_SCALAR',
        # Snowflake functions
        'ARRAY_AGG', 'OBJECT_AGG', 'LATERAL', 'FLATTEN',
        'TRY_CAST', 'TRY_TO_DATE', 'TRY_TO_TIMESTAMP', 'IFF', 'NVL',
        'QUALIFY', 'WITHIN_GROUP', 'DATEADD', 'DATEDIFF',
        # Common across platforms but need conversion awareness
        'WINDOW_FUNCTIONS',
    }

    def __init__(self):
        self.normalized_sql = ""
        self.metrics: Dict = {}

    # Weights are tuned to avoid misclassifying "simple" SQL that contains
    # common clauses like ORDER BY / LIMIT / DISTINCT / CAST.
    MEDIUM_WEIGHTS = {
        "join": 14,
        "aggregation": 10,
        "aggregation_filter": 8,
        "window_function": 14,
        "cte": 14,
        "subquery": 14,
        "set_operations": 12,
        # common clauses -> small impact
        "deduplication": 4,
        "sorting": 4,
        "pagination": 4,
        "conditional_logic": 5,
    }

    COMPLEX_WEIGHTS = {
        # true structural complexity -> high impact
        "correlated_subquery": 35,
        "nested_subquery_chain": 35,
        "union_chain": 35,
        "multiple_joins_chain": 35,
        "recursive_cte": 30,
        # "common but not necessarily hard" -> low impact
        "type_casting": 8,
        "lateral_join": 16,
        "pivot_operations": 18,
        "array_operations": 16,
        "complex_data_types": 18,
        "semi_structured_data": 14,
    }

    def analyze(self, sql: str) -> Dict:
        """
        Analyze query complexity and return detailed metrics.
        
        Returns:
            Dict containing:
                - complexity_level: SIMPLE | MEDIUM | COMPLEX
                - complexity_score: 0-100 (100 = most complex)
                - indicators: List of detected complexity patterns
                - tables_referenced: Number of tables
                - joins_count: Number of joins
                - subqueries_count: Number of subqueries
                - aggregations: Whether aggregations are present
                - window_functions: Whether window functions are present
                - cte_count: Number of CTEs
                - set_operations: Whether UNION/INTERSECT/EXCEPT present
                - estimated_conversion_risk: low | medium | high
                - conversion_risk_factors: List of risk factors
        """
        if not sql or not sql.strip():
            return self._empty_result()

        self.normalized_sql = self._normalize_sql(sql)
        self.metrics = self._extract_metrics()
        
        complexity_level, score = self._determine_complexity()
        risk_level, risk_factors = self._assess_conversion_risk()

        return {
            'complexity_level': complexity_level.value,
            'complexity_score': score,
            'indicators': self.metrics.get('indicators', []),
            'tables_referenced': self.metrics.get('tables_count', 0),
            'joins_count': self.metrics.get('joins_count', 0),
            'subqueries_count': self.metrics.get('subqueries_count', 0),
            'aggregations': self.metrics.get('has_aggregation', False),
            'window_functions': self.metrics.get('has_window_function', False),
            'cte_count': self.metrics.get('cte_count', 0),
            'set_operations': self.metrics.get('has_set_operation', False),
            'estimated_conversion_risk': risk_level,
            'conversion_risk_factors': risk_factors,
            'vendor_specific_functions': self.metrics.get('vendor_specific_functions', []),
        }

    def _normalize_sql(self, sql: str) -> str:
        """Normalize SQL for analysis (remove comments, extra whitespace)"""
        # Remove line comments
        sql = re.sub(r'--.*?$', '', sql, flags=re.MULTILINE)
        # Remove block comments
        sql = re.sub(r'/\*.*?\*/', '', sql, flags=re.DOTALL)
        # Normalize whitespace
        sql = ' '.join(sql.split())
        return sql.upper()

    def _extract_metrics(self) -> Dict:
        """Extract various metrics from normalized SQL"""
        metrics = {
            'indicators': [],
            'tables_count': self._count_tables(),
            'joins_count': self._count_joins(),
            'subqueries_count': self._count_subqueries(),
            'has_aggregation': self._has_aggregation(),
            'has_window_function': self._has_window_function(),
            'cte_count': self._count_ctes(),
            'has_set_operation': self._has_set_operation(),
            'vendor_specific_functions': self._find_vendor_functions(),
        }
        return metrics

    def _count_tables(self) -> int:
        """Estimate number of tables referenced"""
        # Look for FROM and JOIN keywords
        from_count = len(re.findall(r'\bFROM\b', self.normalized_sql))
        join_count = len(re.findall(r'(?:LEFT|RIGHT|FULL|INNER|CROSS)?\s*JOIN\b', self.normalized_sql))
        return from_count + join_count

    def _count_joins(self) -> int:
        """Count number of JOIN clauses"""
        return len(re.findall(r'(?:LEFT|RIGHT|FULL|INNER|CROSS)?\s*JOIN\b', self.normalized_sql))

    def _count_subqueries(self) -> int:
        """Count nested SELECT statements (subqueries)"""
        # Simple heuristic: count opening parentheses before SELECT
        subquery_pattern = r'\(\s*SELECT\b'
        return len(re.findall(subquery_pattern, self.normalized_sql))

    def _has_aggregation(self) -> bool:
        """Check for GROUP BY, HAVING, or aggregate functions"""
        agg_patterns = [
            r'\bGROUP\s+BY\b',
            r'\bHAVING\b',
            r'\b(COUNT|SUM|AVG|MIN|MAX|STDDEV|VARIANCE|COLLECT_LIST|COLLECT_SET)\s*\(',
        ]
        return any(re.search(pattern, self.normalized_sql) for pattern in agg_patterns)

    def _has_window_function(self) -> bool:
        """Check for OVER or WINDOW keywords"""
        return bool(re.search(r'\b(?:OVER|WINDOW)\b', self.normalized_sql))

    def _count_ctes(self) -> int:
        """Count WITH clauses (CTEs)"""
        # Count comma-separated CTEs
        with_match = re.search(r'\bWITH\b', self.normalized_sql)
        if not with_match:
            return 0
        # Count AS keywords after WITH as rough CTE count
        after_with = self.normalized_sql[with_match.start():]
        # Find the main SELECT after WITH
        main_select = re.search(r'SELECT\b', after_with)
        if not main_select:
            return 0
        cte_section = after_with[:main_select.start()]
        return cte_section.count(',') + 1

    def _has_set_operation(self) -> bool:
        """Check for UNION, INTERSECT, EXCEPT"""
        return bool(re.search(r'\b(?:UNION|INTERSECT|EXCEPT)\b', self.normalized_sql))

    def _find_vendor_functions(self) -> List[str]:
        """Find vendor-specific functions"""
        found = []
        for func in self.VENDOR_SPECIFIC_FUNCTIONS:
            if re.search(rf'\b{func}\b', self.normalized_sql):
                found.append(func)
        return found

    def _determine_complexity(self) -> Tuple[ComplexityLevel, int]:
        """Determine overall complexity level"""
        score = 0
        indicators = []

        # Check for complex indicators first (highest priority)
        for pattern, indicator_name in self.COMPLEX_INDICATORS.items():
            if re.search(pattern, self.normalized_sql):
                score += self.COMPLEX_WEIGHTS.get(indicator_name, 25)
                indicators.append(indicator_name)

        # Check for medium indicators
        for pattern, indicator_name in self.MEDIUM_INDICATORS.items():
            if re.search(pattern, self.normalized_sql):
                score += self.MEDIUM_WEIGHTS.get(indicator_name, 10)
                indicators.append(indicator_name)

        # Additional scoring based on metrics
        # Counts act as reinforcement, but keep them smaller than keyword hits
        # to reduce false "complex" classifications.
        score += self.metrics.get('joins_count', 0) * 3
        score += self.metrics.get('subqueries_count', 0) * 5
        score += self.metrics.get('cte_count', 0) * 4

        # Cap score at 100
        score = min(100, score)

        # Determine complexity level
        if score >= 55:
            level = ComplexityLevel.COMPLEX
        elif score >= 25:
            level = ComplexityLevel.MEDIUM
        else:
            level = ComplexityLevel.SIMPLE

        self.metrics['indicators'] = indicators
        self.metrics['score'] = score

        return level, score

    def _assess_conversion_risk(self) -> Tuple[str, List[str]]:
        """Assess conversion risk from source SQL to Databricks."""
        risk_factors = []
        risk_score = 0

        # Risk factors for BigQuery -> Databricks conversion
        if self.metrics.get('has_window_function'):
            risk_factors.append('Window functions may need syntax adjustment')
            risk_score += 5

        if self._count_joins() >= 4:
            risk_factors.append('Multiple joins may impact performance')
            risk_score += 8

        if self.metrics.get('cte_count', 0) > 5:
            risk_factors.append('Deep CTE chains may need optimization')
            risk_score += 10

        if self.metrics.get('subqueries_count', 0) > 3:
            risk_factors.append('Nested subqueries should be reviewed')
            risk_score += 12

        if self.metrics.get('vendor_specific_functions'):
            risk_factors.append(f'Vendor-specific functions: {", ".join(self.metrics["vendor_specific_functions"])}')
            risk_score += 15

        if re.search(r'\bARRAY<|STRUCT<|MAP<', self.normalized_sql):
            risk_factors.append('Complex nested data types require careful mapping')
            risk_score += 10

        if re.search(r'\bJSON_|XML', self.normalized_sql):
            risk_factors.append('Semi-structured data handling may differ')
            risk_score += 8

        # Determine risk level
        if risk_score >= 30:
            risk_level = 'high'
        elif risk_score >= 15:
            risk_level = 'medium'
        else:
            risk_level = 'low'

        return risk_level, risk_factors

    def _empty_result(self) -> Dict:
        """Return empty/default result"""
        return {
            'complexity_level': 'SIMPLE',
            'complexity_score': 0,
            'indicators': [],
            'tables_referenced': 0,
            'joins_count': 0,
            'subqueries_count': 0,
            'aggregations': False,
            'window_functions': False,
            'cte_count': 0,
            'set_operations': False,
            'estimated_conversion_risk': 'low',
            'conversion_risk_factors': [],
            'vendor_specific_functions': [],
        }
