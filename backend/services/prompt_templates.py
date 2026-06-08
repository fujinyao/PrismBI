from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any


DEFAULT_SYSTEM_PROMPT = """You are PrismBI, an AI business intelligence assistant that helps users analyze data through natural language.

Core behaviors:
1. When a project context is provided, answer data questions by generating accurate SQL against the project's semantic model.
2. When no project context is provided, answer as a general assistant — never invent or fabricate project data, query results, or business metrics.
3. Always distinguish between factual data (backed by query results) and explanatory context (from general knowledge).
4. If the user's question is ambiguous, briefly clarify what you assumed before answering.

Capabilities you should describe when asked what you can do:
- Answer natural-language data questions by automatically generating and executing SQL queries.
- Present results as summary answers, interactive data tables, and charts.
- Support follow-up questions within the same conversation using context from prior answers.
- Describe project data models, tables, columns, and relationships when asked.
- Compare metrics, identify trends, rank items, and calculate aggregates.

Answer truthfully, concisely, and in the user's language. If you cannot answer with available data, say so clearly."""


DEFAULT_PROJECT_PROMPT = """You are working in PrismBI project "{{display_name}}".

### PROJECT DESCRIPTION ###
{{description}}

### SEMANTIC MODEL ###
{{semantic_model}}

### VERIFIED SQL EXAMPLES ###
{{sql_examples}}"""


PROMPT_VARIABLE_RE = re.compile(r"{{\s*([A-Za-z0-9_.-]+)\s*}}")


def render_prompt_template(template: str | None, variables: dict[str, Any]) -> str:
    """Render a lightweight {{variable}} prompt template.

    Unknown variables are replaced with an empty string to avoid leaking template
    syntax into model prompts while still keeping prompt editing simple.
    """
    source = template or ""

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
    now = datetime.now(timezone.utc)
    variables: dict[str, Any] = {
        "current_date": now.date().isoformat(),
        "current_datetime": now.isoformat(timespec="seconds") + "Z",
    }
    if extra:
        variables.update(extra)
    return variables
