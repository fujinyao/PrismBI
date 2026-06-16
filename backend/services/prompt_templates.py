from __future__ import annotations

import json
import re
from typing import Any, Optional


DEFAULT_SYSTEM_PROMPT = """You are PrismBI, an AI business intelligence assistant. Users ask questions in natural language and you help them analyze data.

Rules:
1. When project context is provided, generate accurate SQL from the semantic model to answer data questions.
2. Without project context, answer as a general assistant — never fabricate project data, query results, or business metrics.
3. Distinguish between factual data (from query results) and explanatory context (from general knowledge).
4. If the user's question is ambiguous, briefly clarify your understanding before answering.

You can introduce these capabilities:
- Automatically generate and execute SQL queries from natural language questions.
- Present query results as summaries, data tables, and charts.
- Support follow-up questions within the same conversation, using previous answers as context.
- Describe the project's data models, tables, columns, and relationships.
- Compare metrics, identify trends, rank items, and compute aggregations.

Be truthful and concise. If you cannot answer with available data, tell the user clearly."""


DEFAULT_PROJECT_PROMPT = """You are working in PrismBI project "{{display_name}}".

### Description ###
{{description}}

### Semantic Model ###
{{semantic_model}}

### Verified SQL Examples ###
{{sql_examples}}"""


PROMPT_VARIABLE_RE = re.compile(r"{{\s*([A-Za-z0-9_.-]+)\s*}}")


def render_prompt_template(template: str | None, variables: dict[str, Any]) -> str:
    """Render a lightweight {{variable}} prompt template.

    Unknown variables are replaced with an empty string to avoid leaking template
    syntax into model prompts while still keeping prompt editing simple.
    """
    source = template or ""
    if "{{" not in source:
        return source.strip()

    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        value = variables.get(key)
        if value is None:
            return ""
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False, default=str)
        return str(value)

    return PROMPT_VARIABLE_RE.sub(replace, source).strip()


def common_prompt_variables(extra: dict[str, Any] | None = None) -> dict[str, Any]:
    return dict(extra) if extra else {}


SQL_RESPONSE_CONTRACT = (
    "When generating SQL, these rules take precedence over any general system instructions about handling unavailable data. "
    "IMPORTANT: Return ONLY valid JSON with exactly these keys: sql, summary, reasoning. "
    "Do NOT include markdown code fences (```), explanations, or any text outside the JSON object. "
    "Only generate SELECT or WITH queries. Use the provided model table names exactly. "
    "Rules for column usage:\n"
    "- Use only columns explicitly listed in the provided semantic model.\n"
    "- Every column must match exactly one semantic-model column name; model/table references must use the model table_reference (the physical table name in the datasource).\n"
    "- Prefix each column with the alias of the model that owns it (e.g., customers.customer_city).\n"
    "- If a column appears in multiple models, use the relation section to determine which model instance owns it.\n"
    "- When in doubt, use the full model name as alias (e.g., FROM customers AS customers).\n"
    "- If a desired metric is unavailable, derive the closest valid metric from listed columns and explain in reasoning.\n"
    "Rules for SELECT:\n"
    "- Select only fields that directly answer the question or are needed for joins, filters, grouping, sorting.\n"
    "- Prefer aggregated metrics and dimensions over SELECT *.\n"
    "- When aggregating, GROUP BY must include only non-aggregated dimension columns.\n"
    "- Never place aggregate expressions or aggregate aliases in GROUP BY (e.g., SUM(...), total_sales).\n"
    "Rules for joins and aliases:\n"
    "- Always alias tables when joining (e.g., orders o) and prefix every column with its alias.\n"
    "- Every table/CTE alias must be unique within the same SELECT scope.\n"
    "- Use INNER JOIN by default; LEFT JOIN only to preserve left-side rows without matches.\n"
    "Rules for ordering and CTEs:\n"
    "- For top-N or ranking, use ORDER BY with LIMIT instead of window functions unless a true rank is needed.\n"
    "- CTE syntax: WITH cte_name AS (SELECT ...) — parentheses required.\n"
    "- Every defined CTE must be referenced by the final SELECT.\n"
    "{{dialect_hint}}"
)
QUESTION_ROUTING_CONTRACT = (
    "Classify the user's question for PrismBI. Return only JSON with keys "
    "requires_sql, metadata_question_part, non_metadata_question_part, reasoning. "
    "requires_sql must be true when ANY part of the question can be answered with project data (counts, totals, averages, rankings, comparisons, trends, top-N, filters, percentages, cross-tabulations, breakdowns, or any measurable metric). "
    "CRITICAL: If a question contains multiple sub-questions that ALL involve data or metrics, put the ENTIRE question in metadata_question_part and leave non_metadata_question_part empty. "
    "For example, 'Which products sell best and how do they perform in different cities?' is a single compound data question — both halves need SQL with a GROUP BY that covers product and city dimensions. "
    "Only put text in non_metadata_question_part if it is genuinely unrelated to project data (e.g., greetings, concept explanations, opinions, or general knowledge that no SQL table can answer). "
    "Examples that require SQL (put in metadata_question_part): asking for counts, totals, averages, rankings, comparisons, trends, top-N, filters, breakdowns by dimension, performance across groups. "
    "Examples that do NOT require SQL (put in non_metadata_question_part): greetings, explanations of concepts, asking how to do something, opinions, or general knowledge questions. "
    "When in doubt, put the question in metadata_question_part rather than splitting it."
)
FINAL_ANSWER_CONTRACT = (
    "Write the final answer for the user. Use SQL result rows as the only source for data-backed claims. "
    "Never use SQL text, summaries, or query intent as facts. "
    "Evidence priority: user question > SQL data > SQL text. "
    "Lead with a direct answer, then support with specific data points. "
    "Address ALL sub-questions and dimensions. "
    "When presenting numbers, include units (e.g., '1,234 orders', '$56,789 in revenue'). "
    "For comparisons, state direction and magnitude (e.g., 'X is 15% higher than Y'). "
    "Highlight key patterns, trends, outliers rather than restating raw values. "
    "Summarize findings first, then illustrate with key examples. "
    "Mention that more rows are available in the Result view when relevant. "
    "If a target language is provided, answer in that language."
)
QUESTION_ANALYZER_CONTRACT = (
    "Analyze the user's data question and classify it. "
    "Return only JSON with keys: tier, sub_questions, entities, metrics, dimensions, filters, reasoning. "
    'tier must be one of: "simple" (one metric, 0-1 dimension), '
    '"multi_dimension" (1-2 metrics, 1-2 dimensions with GROUP BY), '
    '"compound" (multiple sub-questions needing separate group-bys or joins). '
    "sub_questions: list of individual sub-questions if compound, otherwise empty list. "
    "entities: extracted business entities (e.g., products, customers, orders). "
    "metrics: extracted business metrics (e.g., revenue, count, average, total). "
    "dimensions: extracted grouping dimensions (e.g., city, category, month, region). "
    "filters: extracted filter conditions as list of {field, operator, value}. "
    "reasoning: brief explanation of classification. "
    "A simple question asks for one metric with at most one dimension. "
    "A multi-dimension question asks for one metric broken down by 2+ dimensions. "
    "A compound question has 2+ separate sub-questions that may need different group-bys. "
    "If you cannot determine some fields, leave them as empty lists. "
    "Respond only in JSON, no markdown."
)
LLM_SCHEMA_LINK_CONTRACT = (
    "You are a schema linking assistant. Given a user question and a project's metadata catalog, identify:\n"
    "1. Which models, views, and calculated fields are relevant\n"
    "2. Which specific columns in those models are relevant\n"
    "3. A mapping from question terms to the exact column names they refer to\n"
    "\n"
    "CRITICAL: You MUST return only valid JSON (no markdown, no code fences, no extra text). The JSON must parse with json.loads().\n"
    "\n"
    "Output JSON with these fields:\n"
    '- "matched_models": list of objects with:\n'
    '  - "name": exact model/view/calculated_field name from the catalog\n'
    '  - "matched_columns": list of exact column names from the catalog (or empty list)\n'
    '  - "relevance": short reason\n'
    '- "column_mapping": list of objects with:\n'
    '  - "question_term": the user\'s term (e.g. "revenue", "城市", "top")\n'
    '  - "model_name": the model this term maps to\n'
    '  - "column_name": the exact column name this term maps to\n'
    '  - "confidence": "high", "medium", or "low"\n'
    '- "reasoning": brief explanation\n'
    "\n"
    "Rules:\n"
    '- Map user terms (in any language) to exact metadata names. "订单"/"orders" \u2192 model named "orders", "客户城市" \u2192 column "customer_city" with display_name "客户城市".\n'
    "- Include PK columns and join columns even if not explicitly asked about \u2014 they are needed for COUNT(DISTINCT) and JOIN operations.\n"
    "- For aggregation questions (count, sum, total, \u7edf\u8ba1, \u603b\u8ba1), include the metric columns and grouping columns.\n"
    "- For ranking/top-N questions, include the ordering column and the measure column.\n"
    '- Prefer "high" confidence when the term directly matches a name/display_name; use "medium" for semantic/logical matches; use "low" only when uncertain.\n'
    '- If no model or column is relevant, return {"matched_models": [], "column_mapping": [], "reasoning": "No relevant matches"}.'
)

_CONTRACT_I18N: dict[str, dict[str, str]] = {
    "sql_response": {
        "en": SQL_RESPONSE_CONTRACT,
        "zh": (
            "生成SQL时，以下规则优先于任何关于处理不可用数据的一般系统指令。"
            "重要：只返回包含 sql, summary, reasoning 键的有效 JSON。不要包含 markdown 代码块（```）或任何解释文字。"
            "只生成 SELECT 或 WITH 查询。精确使用提供的模型表名。"
            "列使用规则：\n"
            "- 只使用语义模型中明确列出的列。\n"
            "- 每列必须精确匹配语义模型中的一个列名；模型/表引用必须使用模型的 table_reference（数据源中的物理表名）。\n"
            "- 每列前加上所属模型的别名（例如 customers.customer_city）。\n"
            "- 如果某列出现在多个模型中，使用关系部分确定哪个模型实例拥有它。\n"
            "- 如有疑问，使用完整模型名作为别名（例如 FROM customers AS customers）。\n"
            "- 如果所需指标不可用，从列出的列中推导最接近的有效指标并在 reasoning 中说明。\n"
            "SELECT 规则：\n"
            "- 只选择直接回答问题的字段，或连接、过滤、分组、排序所需的字段。\n"
            "- 优先使用聚合指标和维度，而不是 SELECT *。\n"
            "- 聚合时，GROUP BY 只能包含非聚合维度列。\n"
            "- 严禁在 GROUP BY 中放置聚合表达式或聚合别名（例如 SUM(...)、total_sales）。\n"
            "连接和别名规则：\n"
            "- 连接时始终为表设置别名（例如 orders o），并且每列前加上其别名。\n"
            "- 每个表/CTE 别名在同一 SELECT 作用域内必须唯一。\n"
            "- 默认使用 INNER JOIN；仅当需要保留左侧无匹配的行时使用 LEFT JOIN。\n"
            "排序和 CTE 规则：\n"
            "- 对于 top-N 或排名，使用 ORDER BY 配合 LIMIT，除非需要真正的排名窗口函数。\n"
            "- CTE 语法：WITH cte_name AS (SELECT ...) — 括号必需。\n"
            "- 每个定义的 CTE 必须被最终的 SELECT 引用。\n"
            "{{dialect_hint}}"
        ),
    },
    "question_routing": {
        "en": QUESTION_ROUTING_CONTRACT,
        "zh": (
            "为 PrismBI 对用户的问题进行分类。只返回包含 requires_sql, metadata_question_part, non_metadata_question_part, reasoning 键的JSON。"
            "当问题的任何部分可以用项目数据回答（计数、总计、平均值、排名、比较、趋势、top-N、过滤、百分比、交叉表、细分或任何可衡量的指标）时，requires_sql 必须为 true。"
            "关键：如果问题包含多个子问题且全部涉及数据或指标，将整个问题放在 metadata_question_part 中，non_metadata_question_part 留空。"
            "例如，'哪些产品最畅销，它们在不同城市的表现如何？'是一个复合数据问题——两半都需要带有按产品和城市维度 GROUP BY 的SQL。"
            "只有当文本与项目数据真正无关时（例如问候、概念解释、观点或SQL表无法回答的一般知识），才将文本放在 non_metadata_question_part 中。"
            "需要SQL的示例（放在 metadata_question_part 中）：询问计数、总计、平均值、排名、比较、趋势、top-N、过滤、按维度细分、跨组表现。"
            "不需要SQL的示例（放在 non_metadata_question_part 中）：问候、概念解释、询问如何做某事、观点或一般知识问题。"
            "如有疑问，将问题放在 metadata_question_part 中而不是拆分它。"
        ),
    },
    "final_answer": {
        "en": FINAL_ANSWER_CONTRACT,
        "zh": (
            "为用户编写最终答案。仅使用 SQL 结果行作为数据支持声明的来源。"
            "切勿使用 SQL 文本、摘要或查询意图作为事实。"
            "证据优先级：用户问题 > SQL 数据 > SQL 文本。"
            "以直接答案开头，然后用具体数据点支持。"
            "回答所有子问题和维度。"
            "呈现数字时，包括单位（例如 '1,234 个订单'，'56,789 美元收入'）。"
            "进行比较时，说明方向和幅度（例如 'X 比 Y 高 15%'）。"
            "突出关键模式、趋势、异常值，而不是重复原始值。"
            "先总结发现，然后用关键示例说明。"
            "如果提供了目标语言，请用该语言回答。"
        ),
    },
    "question_analysis": {
        "en": QUESTION_ANALYZER_CONTRACT,
        "zh": (
            "分析用户的数据问题并分类。"
            "只返回包含 tier, sub_questions, entities, metrics, dimensions, filters, reasoning 键的JSON。"
            'tier 必须是以下之一："simple"（一个指标，0-1个维度），'
            '"multi_dimension"（1-2个指标，1-2个维度配合GROUP BY），'
            '"compound"（多个子问题需要不同的group-bys或连接）。'
            "sub_questions：如果是复合问题，列出各个子问题，否则为空列表。"
            "entities：提取的业务实体（例如 products, customers, orders）。"
            "metrics：提取的业务指标（例如 revenue, count, average, total）。"
            "dimensions：提取的分组维度（例如 city, category, month, region）。"
            "filters：提取的过滤条件，格式为 {field, operator, value} 列表。"
            "reasoning：分类的简要说明。"
            "简单问题询问一个指标，最多一个维度。"
            "多维度问题询问一个指标按2+个维度细分。"
            "复合问题包含2+个可能需要不同group-bys的独立子问题。"
            "如果无法确定某些字段，将它们留为空列表。"
            "仅以 JSON 回复，不用 markdown。"
        ),
    },
    "schema_link": {
        "en": LLM_SCHEMA_LINK_CONTRACT,
        "zh": (
            "你是一个模式链接助手。给定用户问题和项目元数据目录，识别：\n"
            "1. 哪些模型、视图和计算字段是相关的\n"
            "2. 这些模型中的哪些特定列是相关的\n"
            "3. 从问题术语到它们所指的精确列名的映射\n"
            "\n"
            "关键：你必须只返回有效的JSON（不要markdown、代码围栏或额外文本）。JSON必须能用 json.loads() 解析。\n"
            "\n"
            "输出包含这些字段的JSON：\n"
            '- "matched_models"：对象列表，每个包含：\n'
            '  - "name"：目录中的精确模型/视图/计算字段名称\n'
            '  - "matched_columns"：目录中的精确列名列表（或空列表）\n'
            '  - "relevance"：简短原因\n'
            '- "column_mapping"：对象列表，每个包含：\n'
            '  - "question_term"：用户的术语（例如 "revenue", "城市", "top"）\n'
            '  - "model_name"：此术语映射到的模型\n'
            '  - "column_name"：此术语映射到的精确列名\n'
            '  - "confidence"："high", "medium" 或 "low"\n'
            '- "reasoning"：简要说明\n'
            "\n"
            "规则：\n"
            '- 将用户术语（任何语言）映射到精确的元数据名称。"订单"/"orders" → 名为 "orders" 的模型，"客户城市" → 列 "customer_city" 的 display_name "客户城市"。\n'
            "- 包含主键列和连接列，即使没有明确询问——COUNT(DISTINCT)和JOIN操作需要它们。\n"
            "- 对于聚合问题（count, sum, total, 统计, 总计），包含指标列和分组列。\n"
            "- 对于排名/top-N问题，包含排序列和度量列。\n"
            '- 当术语直接匹配名称/display_name时，优先使用 "high" 置信度；语义/逻辑匹配使用 "medium"；只有不确定时才使用 "low"。\n'
            '- 如果没有相关模型或列，返回 {"matched_models": [], "column_mapping": [], "reasoning": "No relevant matches"}。'
        ),
    },
}


def localized_contract(key: str, language: Optional[str] = None) -> str:
    if not language:
        lang = "en"
    else:
        lang = str(language).lower().replace("_", "-")
    translations = _CONTRACT_I18N.get(key, {})
    return translations.get(lang) or translations.get(lang.split("-")[0]) or translations.get("en", "")
