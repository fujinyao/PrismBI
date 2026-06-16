from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, List, Optional

import duckdb

from db import connection_lock, get_connection, rows_to_dicts
from services.ask_service import _language_name

LOGGER = logging.getLogger(__name__)

_RECOMMENDATION_LLM_PROMPT = """You are a data analyst helping users discover insights from datasets.
Based on the data model and its columns below, generate {max_questions} diverse, business-relevant questions in {language}.

Return a JSON object with key "recommendations" containing an array of objects. Each object must have:
- "question": string, the question text
- "category": one of trend, ranking, comparison, distribution, aggregation, anomaly, contribution, correlation, drilldown, relationship
- "confidence": number between 0.0 and 1.0
- "model_names": optional array of strings

Output only JSON, no other text.

Requirements:
- Questions must use actual model and column names from the data model.
- Vary analysis depth from simple summaries to deep insights.
- Do not generate simple counting questions (e.g. "how many records").
- Prioritize insight questions: trends, anomalies, correlations, contributions.
- All generated questions must be answerable with the given data model.

Model:
{models_summary}"""

_VALID_RECOMMENDATION_CATEGORIES = {
    "trend", "ranking", "comparison", "distribution", "aggregation",
    "anomaly", "contribution", "correlation", "drilldown", "relationship",
}
_RECOMMENDATION_LIST_KEYS = ("recommendations", "items", "questions", "results", "data")

QUESTION_TEMPLATES: dict[str, dict[str, list[str]]] = {
    "en": {
        "trend": [
            "How has {metric} changed over {time_dim} for {model}?",
            "What is the {metric} trend for {model} by {time_dim}?",
        ],
        "ranking": [
            "Which {n} {model} have the highest {metric}?",
            "What are the top and bottom {model} by {metric}?",
        ],
        "comparison": [
            "How does {metric} vary across {dimension} for {model}?",
            "Compare {metric} across {dimension} groups in {model}",
        ],
        "distribution": [
            "What is the distribution of {dimension} in {model}?",
            "What proportion does each {dimension} category represent in {model}?",
        ],
        "aggregation": [
            "What is the average {metric} for {model}?",
            "How many {model} are in the dataset?",
        ],
        "anomaly": [
            "Are there any unusual spikes or drops in {metric} for {model}?",
            "What are the key outliers in {model} based on {metric}?",
        ],
        "contribution": [
            "What is each {dimension}'s contribution to total {metric} in {model}?",
            "Which {dimension} contributes the most to {metric} in {model}?",
        ],
        "correlation": [
            "Is there a correlation between {metric_a} and {metric_b} in {model}?",
        ],
        "drilldown": [
            "Which {model} have {metric} above average?",
            "Break down {model} by {dimension} for detail",
        ],
        "relationship": [
            "How do {src_model} and {tgt_model} relate to each other?",
            "What are the common patterns between {src_model} and {tgt_model}?",
        ],
    },
    "zh": {
        "trend": [
            "{model}的{metric}随{time_dim}的变化趋势是什么？",
            "{model}近期{metric}是上升还是下降？",
        ],
        "ranking": [
            "{metric}排名前{n}的{model}是哪些？",
            "按{metric}排序，{model}中哪些表现最好和最差？",
        ],
        "comparison": [
            "{model}在不同{dimension}下的{metric}差异如何？",
            "比较{model}中各{dimension}分组的{metric}表现",
        ],
        "distribution": [
            "{model}中各{dimension}的占比分布如何？",
            "{model}中{dimension}各分类的比例是多少？",
        ],
        "aggregation": [
            "{model}的平均{metric}是多少？",
            "{model}总共有多少条记录？",
        ],
        "anomaly": [
            "{model}的{metric}是否有异常的峰值或低谷？",
            "{model}中基于{metric}有哪些异常值？",
        ],
        "contribution": [
            "{model}中各{dimension}对总{metric}的贡献分别是多少？",
            "哪个{dimension}对{model}的{metric}贡献最大？",
        ],
        "correlation": [
            "{model}中{metric_a}和{metric_b}之间是否存在相关性？",
        ],
        "drilldown": [
            "哪些{model}的{metric}高于平均水平？",
            "按{dimension}下钻{model}的详细数据",
        ],
        "relationship": [
            "{src_model}和{tgt_model}之间存在怎样的关联？",
            "{src_model}和{tgt_model}有哪些共同特征？",
        ],
    },
}


def _get_templates(lang: str) -> dict[str, list[str]]:
    normalized = (lang or "en").strip().lower().replace("-", "_")
    if normalized.startswith("zh"):
        return QUESTION_TEMPLATES["zh"]
    return QUESTION_TEMPLATES["en"]


def _classify_column(col: dict) -> str:
    ctype = (col.get("type") or "").upper()
    if ctype in {"INTEGER", "BIGINT", "SMALLINT", "TINYINT", "INT", "FLOAT", "DOUBLE", "DECIMAL", "NUMERIC", "REAL"}:
        return "numeric"
    if ctype in {"DATE", "TIMESTAMP", "DATETIME"}:
        return "temporal"
    if ctype in {"VARCHAR", "TEXT", "CHAR", "STRING"}:
        return "dimension"
    if ctype in {"BOOLEAN", "BOOL"}:
        return "boolean"
    return "dimension"


def _generate_mdl_candidates(project_id: int, max_results: int = 20, language: str = "en") -> list[dict]:
    from services.ask_service import _models_for_project, _relations_for_project
    models = _models_for_project(project_id)
    relations = _relations_for_project(project_id)
    templates = _get_templates(language)
    candidates = []
    seen_questions = set()
    model_type_count: dict[str, int] = {}

    def _add(q: str, category: str, qtype: str, model_names: list[str], confidence: float, extra_meta: dict | None = None) -> None:
        if q in seen_questions:
            return
        key = f"{model_names[0]}:{qtype}" if model_names else qtype
        model_type_count[key] = model_type_count.get(key, 0) + 1
        if model_type_count[key] > 1:
            return
        seen_questions.add(q)
        meta: dict = {"model_names": model_names, "question_type": qtype}
        if extra_meta:
            meta.update(extra_meta)
        candidates.append({
            "title": q,
            "category": category,
            "scope": "project",
            "source_type": "schema",
            "confidence": confidence,
            "metadata": meta,
        })

    for model in models:
        if model.get("_type") == "calculated_field":
            continue
        mname = model.get("display_name") or model.get("name", "")
        columns = model.get("columns", [])
        if not columns:
            continue

        numeric_cols = [c for c in columns if _classify_column(c) == "numeric" and not c.get("is_primary_key")]
        temporal_cols = [c for c in columns if _classify_column(c) == "temporal"]
        dimension_cols = [c for c in columns if _classify_column(c) == "dimension" and not c.get("is_primary_key")]

        for metric_col in numeric_cols[:2]:
            metric = metric_col.get("display_name") or metric_col.get("name", "")
            if not metric:
                continue
            for n in [5]:
                for tmpl in templates.get("ranking", []):
                    _add(tmpl.format(n=n, model=mname, metric=metric), "ranking", "ranking", [mname], 0.6)
            for tmpl in templates.get("aggregation", []):
                _add(tmpl.format(model=mname, metric=metric), "aggregation", "aggregation", [mname], 0.55)

        for dim_col in dimension_cols[:1]:
            dim = dim_col.get("display_name") or dim_col.get("name", "")
            if not dim:
                continue
            for tmpl in templates.get("distribution", []):
                _add(tmpl.format(model=mname, dimension=dim), "distribution", "distribution", [mname], 0.50)
            for metric_col in numeric_cols[:1]:
                metric = metric_col.get("display_name") or metric_col.get("name", "")
                if not metric:
                    continue
                for tmpl in templates.get("comparison", []):
                    _add(tmpl.format(model=mname, metric=metric, dimension=dim), "comparison", "comparison", [mname], 0.50)
                for tmpl in templates.get("contribution", []):
                    _add(tmpl.format(model=mname, metric=metric, dimension=dim), "contribution", "contribution", [mname], 0.48)

        for time_col in temporal_cols[:1]:
            time_dim = time_col.get("display_name") or time_col.get("name", "")
            if not time_dim:
                continue
            for metric_col in numeric_cols[:1]:
                metric = metric_col.get("display_name") or metric_col.get("name", "")
                if not metric:
                    continue
                for tmpl in templates.get("trend", []):
                    _add(tmpl.format(model=mname, metric=metric, time_dim=time_dim), "trend", "trend", [mname], 0.55)
                for tmpl in templates.get("anomaly", []):
                    _add(tmpl.format(model=mname, metric=metric), "anomaly", "anomaly", [mname], 0.45)

        if len(numeric_cols) >= 2:
            ma = numeric_cols[0].get("display_name") or numeric_cols[0].get("name", "")
            mb = numeric_cols[1].get("display_name") or numeric_cols[1].get("name", "")
            for tmpl in templates.get("correlation", []):
                _add(tmpl.format(model=mname, metric_a=ma, metric_b=mb), "correlation", "correlation", [mname], 0.40)

        for dim_col in dimension_cols[:1]:
            dim = dim_col.get("display_name") or dim_col.get("name", "")
            if not dim:
                continue
            for metric_col in numeric_cols[:1]:
                metric = metric_col.get("display_name") or metric_col.get("name", "")
                if not metric:
                    continue
                for tmpl in templates.get("drilldown", []):
                    _add(tmpl.format(model=mname, metric=metric, dimension=dim), "drilldown", "drilldown", [mname], 0.42)

    if relations:
        for rel in relations[:3]:
            src = rel.get("source_model", "")
            tgt = rel.get("target_model", "")
            if src and tgt:
                for tmpl in templates.get("relationship", []):
                    _add(tmpl.format(src_model=src, tgt_model=tgt), "relationship", "relationship", [src, tgt], 0.40)

    candidates.sort(key=lambda c: c.get("confidence", 0), reverse=True)
    return candidates[:max_results]


def _generate_session_followups(project_id: int, context: Optional[str], max_results: int = 5, language: str = "en") -> list[dict]:
    if not context or len(context.strip()) < 2:
        return []

    from services.ask_service import _models_for_project
    models = _models_for_project(project_id)
    model_names = [m.get("display_name") or m.get("name", "") for m in models if m.get("_type") != "calculated_field"]
    context_lower = context.lower()
    templates = _get_templates(language)

    follow_ups = []
    seen_questions = set()

    mentioned_models = [mn for mn in model_names if mn.lower() in context_lower]
    if mentioned_models:
        mentioned_model = mentioned_models[0]
        model_data = next((m for m in models if (m.get("display_name") or m.get("name", "")) == mentioned_model), None)
        if model_data:
            columns = model_data.get("columns", [])
            numeric_cols = [c for c in columns if _classify_column(c) == "numeric" and not c.get("is_primary_key")]
            dimension_cols = [c for c in columns if _classify_column(c) == "dimension" and not c.get("is_primary_key")]

            for dim_col in dimension_cols[:2]:
                dim = dim_col.get("display_name") or dim_col.get("name", "")
                if not dim:
                    continue
                metric = (numeric_cols[0].get("display_name") or numeric_cols[0].get("name", "")) if numeric_cols else ""
                if not metric:
                    continue
                for tmpl in templates.get("comparison", []):
                    q = tmpl.format(model=mentioned_model, metric=metric, dimension=dim)
                    if q not in seen_questions:
                        seen_questions.add(q)
                        follow_ups.append({
                            "title": q,
                            "category": "comparison",
                            "scope": "project",
                            "source_type": "session",
                            "confidence": 0.65,
                            "metadata": {"model_names": [mentioned_model], "question_type": "follow_up", "context": context[:200]},
                        })

            for metric_col in numeric_cols[:1]:
                metric = metric_col.get("display_name") or metric_col.get("name", "")
                for tmpl in templates.get("aggregation", []):
                    q = tmpl.format(model=mentioned_model, metric=metric)
                    if q not in seen_questions:
                        seen_questions.add(q)
                        follow_ups.append({
                            "title": q,
                            "category": "aggregation",
                            "scope": "project",
                            "source_type": "session",
                            "confidence": 0.6,
                            "metadata": {"model_names": [mentioned_model], "question_type": "follow_up", "context": context[:200]},
                        })

            for other in model_names:
                if other != mentioned_model and other.lower() not in context_lower:
                    for tmpl in templates.get("relationship", [])[:1]:
                        q = tmpl.format(src_model=mentioned_model, tgt_model=other)
                        if q not in seen_questions:
                            seen_questions.add(q)
                            follow_ups.append({
                                "title": q,
                                "category": "relationship",
                                "scope": "project",
                                "source_type": "session",
                                "confidence": 0.5,
                                "metadata": {"model_names": [mentioned_model, other], "question_type": "follow_up", "context": context[:200]},
                            })
                    break

    follow_ups.sort(key=lambda c: c.get("confidence", 0), reverse=True)
    return follow_ups[:max_results]


def _get_hot_catalog_questions(project_id: int, max_results: int = 10) -> list[dict]:
    try:
        with connection_lock():
            con = get_connection()
            rows = con.execute(
                "SELECT id, question, sql_text, frequency, metadata FROM metadata.question_sql_catalog "
                "WHERE project_id = ? AND verified = TRUE ORDER BY frequency DESC, last_used DESC LIMIT ?",
                [project_id, max_results],
            ).fetchall()
    except Exception:
        return []

    results = []
    for row in rows:
        meta = row[4]
        if isinstance(meta, (bytes, str)):
            try:
                meta = json.loads(meta) if isinstance(meta, str) else json.loads(meta.decode("utf-8"))
            except Exception:
                meta = {}
        elif meta is None:
            meta = {}

        results.append({
            "title": row[1],
            "category": "catalog",
            "scope": "project",
            "source_type": "project",
            "confidence": min(0.4 + (row[3] or 1) * 0.05, 0.9),
            "metadata": {"model_names": meta.get("model_names", []), "question_type": "catalog", "catalog_id": row[0], "sql": row[2]},
        })
    return results


def _adjust_weights_from_scores(project_id: int) -> None:
    try:
        with connection_lock():
            con = get_connection()
            recent_scores = con.execute(
                "SELECT source_layer, AVG(score) as avg_score, COUNT(*) as cnt "
                "FROM metadata.recommendation_scores "
                "WHERE project_id = ? AND created_at >= CURRENT_TIMESTAMP - INTERVAL '7 days' "
                "GROUP BY source_layer",
                [project_id],
            ).fetchall()

            if not recent_scores:
                return

            current_weights = {}
            weight_rows = con.execute(
                "SELECT key, value FROM metadata.settings WHERE key LIKE 'recommender_weight_%'"
            ).fetchall()
            for wr in weight_rows:
                key = wr[0].replace("recommender_weight_", "")
                try:
                    current_weights[key] = float(json.loads(wr[1]) if isinstance(wr[1], str) else wr[1])
                except Exception:
                    current_weights[key] = 0.5

            defaults = {"schema": 0.5, "session": 0.3, "project": 0.2, "catalog": 0.4}
            for key, default_val in defaults.items():
                if key not in current_weights:
                    current_weights[key] = default_val

            for layer, avg_score, cnt in recent_scores:
                layer = layer or "schema"
                if layer not in current_weights:
                    current_weights[layer] = 0.5
                old_weight = current_weights[layer]

                if avg_score >= 4:
                    adjustment = 0.05
                elif avg_score >= 3:
                    adjustment = 0.02
                elif avg_score <= 2:
                    adjustment = -0.10
                else:
                    adjustment = -0.03

                new_weight = max(0.1, min(1.0, old_weight + adjustment * min(cnt / 5, 1.0)))
                current_weights[layer] = new_weight

                if abs(new_weight - old_weight) > 0.001:
                    history_id = con.execute(
                        "SELECT COALESCE(MAX(id), 0) + 1 FROM metadata.layer_weight_history"
                    ).fetchone()[0]
                    con.execute(
                        "INSERT INTO metadata.layer_weight_history (id, source_layer, previous_weight, new_weight, reason) VALUES (?, ?, ?, ?, ?)",
                        [history_id, layer, old_weight, new_weight, f"Auto-adjusted based on {cnt} scores (avg={avg_score:.1f})"],
                    )

            for key, value in current_weights.items():
                setting_key = f"recommender_weight_{key}"
                con.execute(
                    "INSERT OR REPLACE INTO metadata.settings (key, value) VALUES (?, ?::JSON)",
                    [setting_key, json.dumps(round(value, 3))],
                )
    except Exception as exc:
        LOGGER.warning("Weight auto-adjustment failed for project %s: %s", project_id, exc)


def _collaborative_filtering(project_id: int, question: str, max_results: int = 3) -> list[dict]:
    escaped_question = question.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    try:
        with connection_lock():
            con = get_connection()
            co_occurrence = con.execute(
                """SELECT r2.title, COUNT(*) as cnt
                FROM metadata.recommendation_feedback f1
                JOIN metadata.recommendations r1 ON r1.id = f1.recommendation_id AND r1.project_id = ?
                JOIN metadata.recommendation_feedback f2 ON f2.user_id = f1.user_id AND f2.action = 'accept'
                JOIN metadata.recommendations r2 ON r2.id = f2.recommendation_id AND r2.project_id = ? AND r2.id != r1.id
                WHERE f1.project_id = ? AND f1.action = 'accept'
                AND (r1.title ILIKE ? OR ? = '')
                GROUP BY r2.title
                ORDER BY cnt DESC
                LIMIT ?""",
                [project_id, project_id, project_id, f"%{escaped_question}%", question, max_results],
            ).fetchall()

            if not co_occurrence:
                similar_threads = con.execute(
                    """SELECT tr2.sql, tr2.question, COUNT(*) as cnt
                    FROM metadata.thread_responses tr1
                    JOIN metadata.threads t1 ON t1.id = tr1.thread_id AND t1.project_id = ?
                    JOIN metadata.threads t2 ON t2.project_id = ? AND t2.user_id = t1.user_id AND t2.id != t1.id
                    JOIN metadata.thread_responses tr2 ON tr2.thread_id = t2.id
                    WHERE t1.project_id = ?
                    AND (tr1.question ILIKE ? OR ? = '')
                    AND tr1.question IS NOT NULL AND tr2.question IS NOT NULL
                    AND tr2.question ILIKE '%' || SUBSTRING(tr1.question, 1, 20) || '%'
                    GROUP BY tr2.question, tr2.sql
                    ORDER BY cnt DESC
                    LIMIT ?""",
                    [project_id, project_id, project_id, f"%{escaped_question}%", question, max_results],
                ).fetchall()
                return [
                    {
                        "title": row[1] if row[1] else "Similar question",
                        "category": "correlation",
                        "scope": "project",
                        "source_type": "global",
                        "confidence": min(0.3 + (row[2] or 1) * 0.05, 0.7),
                        "metadata": {"question_type": "collaborative", "sql": row[0], "co_occurrence_count": row[2]},
                    }
                    for row in similar_threads
                ]
        return [
            {
                "title": row[0],
                "category": "correlation",
                "scope": "project",
                "source_type": "global",
                "confidence": min(0.3 + (row[1] or 1) * 0.08, 0.85),
                "metadata": {"question_type": "collaborative", "co_occurrence_count": row[1]},
            }
            for row in co_occurrence
        ]
    except Exception as exc:
        LOGGER.warning("Collaborative filtering failed for project %s: %s", project_id, exc)
        return []


def _preference_learning(user_id: int, project_id: int, max_results: int = 3) -> list[dict]:
    try:
        with connection_lock():
            con = get_connection()
            user_hints = con.execute(
                "SELECT hint_text, source_query, confidence FROM metadata.user_preference_hints WHERE user_id = ? ORDER BY confidence DESC LIMIT ?",
                [user_id, max_results * 2],
            ).fetchall()

            accepted_cats = con.execute(
                """SELECT r.category, r.source_type, COUNT(*) as cnt
                FROM metadata.recommendation_feedback f
                JOIN metadata.recommendations r ON r.id = f.recommendation_id AND r.project_id = ?
                WHERE f.user_id = ? AND f.action = 'accept'
                GROUP BY r.category, r.source_type
                ORDER BY cnt DESC""",
                [project_id, user_id],
            ).fetchall()

            recent_questions = con.execute(
                """SELECT question FROM metadata.thread_responses tr
                JOIN metadata.threads t ON t.id = tr.thread_id
                WHERE t.user_id = ? AND t.project_id = ? AND tr.question IS NOT NULL
                ORDER BY tr.created_at DESC NULLS LAST LIMIT 5""",
                [user_id, project_id],
            ).fetchall()

        preference_hints = []
        for hint_text, source_query, confidence in user_hints:
            preference_hints.append({
                "title": hint_text,
                "category": "follow_up",
                "scope": "project",
                "source_type": "preference",
                "confidence": min(float(confidence or 1.0) * 0.9, 0.95),
                "metadata": {"question_type": "preference_hint", "source_query": source_query},
            })

        if accepted_cats:
            top_cat = accepted_cats[0]
            preference_hints.append({
                "title": f"More {top_cat[0]} questions",
                "category": top_cat[0] if top_cat[0] else "aggregation",
                "scope": "project",
                "source_type": "preference",
                "confidence": min(0.4 + (top_cat[2] or 1) * 0.05, 0.75),
                "metadata": {"question_type": "preference_category", "preferred_category": top_cat[0], "preferred_source": top_cat[1]},
            })

        return preference_hints[:max_results]
    except Exception as exc:
        LOGGER.warning("Preference learning failed for user %s project %s: %s", user_id, project_id, exc)
        return []


def _intent_trends(project_id: int, max_results: int = 5) -> list[dict]:
    try:
        with connection_lock():
            con = get_connection()
            trending = con.execute(
                """SELECT tr.question, COUNT(*) as cnt, MAX(tr.created_at) as last_at
                FROM metadata.thread_responses tr
                JOIN metadata.threads t ON t.id = tr.thread_id AND t.project_id = ?
                WHERE tr.question IS NOT NULL AND tr.created_at >= CURRENT_TIMESTAMP - INTERVAL '14 days'
                GROUP BY tr.question
                ORDER BY cnt DESC, last_at DESC
                LIMIT ?""",
                [project_id, max_results * 2],
            ).fetchall()

            catalog_trending = con.execute(
                """SELECT question, frequency FROM metadata.question_sql_catalog
                WHERE project_id = ? AND verified = TRUE
                AND last_used >= CURRENT_TIMESTAMP - INTERVAL '14 days'
                ORDER BY frequency DESC LIMIT ?""",
                [project_id, max_results],
            ).fetchall()

        seen_questions = set()
        results = []
        for question, cnt, last_at in trending:
            q = (question or "").strip()
            if not q or q.lower() in seen_questions or len(q) < 5:
                continue
            seen_questions.add(q.lower())
            results.append({
                "title": q,
                "category": "trending",
                "scope": "project",
                "source_type": "global",
                "confidence": min(0.3 + int(cnt or 1) * 0.08, 0.8),
                "metadata": {"question_type": "intent_trend", "frequency": cnt, "last_used": str(last_at) if last_at else None},
            })
            if len(results) >= max_results:
                break

        for question, frequency in catalog_trending:
            q = (question or "").strip()
            if not q or q.lower() in seen_questions:
                continue
            seen_questions.add(q.lower())
            results.append({
                "title": q,
                "category": "trending",
                "scope": "project",
                "source_type": "global",
                "confidence": min(0.25 + int(frequency or 1) * 0.03, 0.7),
                "metadata": {"question_type": "intent_trend_catalog", "frequency": frequency},
            })
            if len(results) >= max_results:
                break

        return results
    except Exception as exc:
        LOGGER.warning("Intent trends failed for project %s: %s", project_id, exc)
        return []


_ROUTE_AWARE_COMPLEX_TYPES = frozenset({
    "comparison", "correlation", "relationship", "drilldown", "collaborative",
})
_ROUTE_AWARE_SQL_TYPES = frozenset({
    "trend", "ranking", "comparison", "distribution", "aggregation", "anomaly",
    "contribution", "correlation", "drilldown", "relationship", "catalog",
})
_ROUTE_AWARE_GENERAL_MARKERS = (
    "explain", "why", "how", "what is", "meaning", "opinion", "建议",
    "解释", "原因", "为什么", "怎么", "如何", "概念", "含义",
)
_ROUTE_AWARE_MULTI_CLAUSE_MARKERS = (
    " and ", " by ", " across ", " while ", "同时", "以及", "并且", "而且", "另外", "然后",
)


def _route_signal_snapshot(project_id: Optional[int], max_events: int = 1200) -> dict[str, Any]:
    snapshot: dict[str, Any] = {
        "available": False,
        "project_id": int(project_id) if project_id is not None else None,
        "dominant_route_kind": "",
        "mixed_ratio": 0.0,
        "avg_metadata_clause_count": 0.0,
        "sql_success_rate": 0.0,
        "route_kind_counts": {},
    }
    try:
        with connection_lock():
            con = get_connection()
            if project_id is None:
                rows = con.execute(
                    "SELECT event_type, payload FROM metadata.sql_route_events ORDER BY created_at DESC LIMIT ?",
                    [max_events],
                ).fetchall()
            else:
                rows = con.execute(
                    "SELECT event_type, payload FROM metadata.sql_route_events WHERE project_id = ? ORDER BY created_at DESC LIMIT ?",
                    [project_id, max_events],
                ).fetchall()
    except Exception:
        return snapshot

    if not rows:
        return snapshot

    route_kind_counts: dict[str, int] = {}
    mixed_count = 0
    metadata_clause_total = 0
    question_route_total = 0
    sql_terminal_total = 0
    sql_terminal_success = 0

    for event_type, payload in rows:
        payload_obj: dict[str, Any] = {}
        if isinstance(payload, dict):
            payload_obj = payload
        elif isinstance(payload, bytes):
            try:
                payload_obj = json.loads(payload.decode("utf-8"))
            except Exception:
                payload_obj = {}
        elif isinstance(payload, str):
            try:
                payload_obj = json.loads(payload)
            except Exception:
                payload_obj = {}

        marker = str(event_type or "").strip()
        if marker == "question_route_decision":
            question_route_total += 1
            clause_payload = payload_obj.get("clause_routing") if isinstance(payload_obj.get("clause_routing"), dict) else {}
            mixed = bool(payload_obj.get("clause_mixed") if "clause_mixed" in payload_obj else clause_payload.get("mixed"))
            if mixed:
                mixed_count += 1
            metadata_clause_count = payload_obj.get("metadata_clause_count")
            if metadata_clause_count is None:
                metadata_clause_count = clause_payload.get("metadata_clause_count")
            try:
                metadata_clause_total += max(0, int(metadata_clause_count or 0))
            except Exception:
                pass
            continue

        if marker == "execution_route_decision":
            route_kind = str(payload_obj.get("route_kind") or "").strip().lower()
            if route_kind:
                route_kind_counts[route_kind] = int(route_kind_counts.get(route_kind) or 0) + 1
            continue

        if marker in {"ask_route_success", "ask_route_failure"}:
            if bool(payload_obj.get("has_sql")):
                sql_terminal_total += 1
                if marker == "ask_route_success":
                    sql_terminal_success += 1

    dominant_route_kind = ""
    if route_kind_counts:
        dominant_route_kind = max(route_kind_counts.items(), key=lambda item: item[1])[0]

    mixed_ratio = (mixed_count / question_route_total) if question_route_total else 0.0
    avg_metadata_clause_count = (metadata_clause_total / question_route_total) if question_route_total else 0.0
    sql_success_rate = (sql_terminal_success / sql_terminal_total) if sql_terminal_total else 0.0

    snapshot.update(
        {
            "available": bool(question_route_total or route_kind_counts or sql_terminal_total),
            "dominant_route_kind": dominant_route_kind,
            "mixed_ratio": round(mixed_ratio, 4),
            "avg_metadata_clause_count": round(avg_metadata_clause_count, 4),
            "sql_success_rate": round(sql_success_rate, 4),
            "route_kind_counts": route_kind_counts,
        }
    )
    return snapshot


def _candidate_question_type(candidate: dict[str, Any], metadata: dict[str, Any]) -> str:
    question_type = str(metadata.get("question_type") or "").strip().lower()
    if question_type:
        return question_type
    return str(candidate.get("category") or "").strip().lower() or "aggregation"


def _candidate_is_general_phrase(title: str) -> bool:
    lowered = str(title or "").strip().lower()
    if not lowered:
        return False
    return any(marker in lowered for marker in _ROUTE_AWARE_GENERAL_MARKERS)


def _candidate_is_multi_clause(title: str) -> bool:
    lowered = f" {str(title or '').strip().lower()} "
    if not lowered.strip():
        return False
    return any(marker in lowered for marker in _ROUTE_AWARE_MULTI_CLAUSE_MARKERS)


def _route_aware_confidence(
    candidate: dict[str, Any],
    metadata: dict[str, Any],
    route_snapshot: dict[str, Any],
) -> tuple[float, float]:
    base_conf = max(0.0, min(1.0, float(candidate.get("confidence") or 0.5)))
    if not route_snapshot.get("available"):
        return base_conf, 0.0

    source_type = str(candidate.get("source_type") or "").strip().lower()
    question_type = _candidate_question_type(candidate, metadata)
    title = str(candidate.get("title") or "")

    dominant_route_kind = str(route_snapshot.get("dominant_route_kind") or "")
    mixed_ratio = float(route_snapshot.get("mixed_ratio") or 0.0)
    avg_metadata_clause_count = float(route_snapshot.get("avg_metadata_clause_count") or 0.0)
    sql_success_rate = float(route_snapshot.get("sql_success_rate") or 0.0)

    sql_like = question_type in _ROUTE_AWARE_SQL_TYPES or source_type in {"schema", "catalog", "llm", "project"}
    complex_sql = question_type in _ROUTE_AWARE_COMPLEX_TYPES
    general_phrase = _candidate_is_general_phrase(title)
    multi_clause = _candidate_is_multi_clause(title)

    adjustment = 0.0

    if mixed_ratio >= 0.2:
        if general_phrase:
            adjustment -= 0.12
        elif sql_like:
            adjustment += 0.05

    if dominant_route_kind == "cross_source":
        if complex_sql or multi_clause:
            adjustment -= 0.08
        elif sql_like:
            adjustment += 0.02
    elif dominant_route_kind in {"single_duckdb", "single_external"}:
        if sql_like:
            adjustment += 0.03

    if sql_success_rate > 0:
        if sql_success_rate < 0.55 and (complex_sql or multi_clause):
            adjustment -= 0.08
        elif sql_success_rate >= 0.75 and sql_like:
            adjustment += 0.04

    if avg_metadata_clause_count >= 1.3 and multi_clause and not general_phrase:
        adjustment += 0.03

    if source_type == "catalog":
        adjustment += 0.03

    adjusted = max(0.0, min(1.0, base_conf + adjustment))
    return adjusted, adjustment


_RECOMMENDER_SETTING_KEYS = frozenset({
    "recommender_weights", "recommender_enabled", "recommender_collaborative_weight",
    "recommender_content_weight", "recommender_popularity_weight", "recommender_freshness_weight",
    "recommender_min_confidence", "recommender_max_results", "recommender_decay_days",
})


class RecommendationService:
    def __init__(self, db: duckdb.DuckDBPyConnection):
        self.db = db

    def get_recommendations(
        self,
        project_id: int,
        context: Optional[str] = None,
        max_results: int = 5,
        types: Optional[str] = None,
        user_id: int = 0,
        language: str = "en",
    ) -> List[dict]:
        type_filters = {part.strip() for part in (types or "").split(",") if part.strip()} or None
        all_candidates = []

        schema_candidates = _generate_mdl_candidates(project_id, max_results=max_results * 2, language=language)
        if not type_filters or type_filters & {"schema", "trend", "ranking", "comparison", "distribution", "aggregation", "anomaly", "contribution", "correlation", "drilldown", "relationship"}:
            all_candidates.extend(schema_candidates)

        catalog_candidates = _get_hot_catalog_questions(project_id, max_results=max_results)
        if not type_filters or type_filters & {"catalog", "project"}:
            all_candidates.extend(catalog_candidates)

        session_candidates = _generate_session_followups(project_id, context, max_results=max_results, language=language)
        if not type_filters or type_filters & {"session", "follow_up", "comparison", "aggregation", "relationship", "drilldown"}:
            all_candidates.extend(session_candidates)

        if not type_filters or type_filters & {"global", "correlation"}:
            collaborative_candidates = _collaborative_filtering(project_id, context or "", max_results=max_results)
            all_candidates.extend(collaborative_candidates)

        if not type_filters or type_filters & {"preference", "follow_up", "aggregation"}:
            preference_candidates = _preference_learning(user_id, project_id, max_results=max_results)
            all_candidates.extend(preference_candidates)

        if not type_filters or type_filters & {"global", "trending"}:
            trend_candidates = _intent_trends(project_id, max_results=max_results)
            all_candidates.extend(trend_candidates)

        if not type_filters or type_filters & {
            "llm", "trend", "ranking", "comparison", "distribution", "aggregation",
            "anomaly", "contribution", "correlation", "drilldown", "relationship",
        }:
            llm_candidates = generate_llm_recommendations(project_id, language=language, max_questions=max_results)
            all_candidates.extend(llm_candidates)

        try:
            with connection_lock():
                existing_rows = get_connection().execute(
                    "SELECT title, status FROM metadata.recommendations WHERE project_id = ? AND status IN ('active', 'accepted')",
                    [project_id],
                ).fetchall()
            existing_titles = {(r[0] or "").lower().strip() for r in existing_rows}
        except Exception:
            existing_titles = set()

        try:
            with connection_lock():
                dismissed_rows = get_connection().execute(
                    "SELECT title FROM metadata.recommendations WHERE project_id = ? AND status = 'dismissed'",
                    [project_id],
                ).fetchall()
            dismissed_titles = {(r[0] or "").lower().strip() for r in dismissed_rows}
        except Exception:
            dismissed_titles = set()

        filtered = []
        seen_titles = set()
        route_snapshot = _route_signal_snapshot(project_id)
        for c in all_candidates:
            title_lower = (c.get("title") or "").lower().strip()
            if title_lower in dismissed_titles:
                continue
            if title_lower in seen_titles:
                continue
            seen_titles.add(title_lower)
            is_existing = title_lower in existing_titles
            metadata_obj = c.get("metadata", {})
            if isinstance(metadata_obj, str):
                try:
                    metadata_obj = json.loads(metadata_obj)
                except Exception:
                    metadata_obj = {}
            if not isinstance(metadata_obj, dict):
                metadata_obj = {}
            adjusted_confidence, route_adjustment = _route_aware_confidence(c, metadata_obj, route_snapshot)
            metadata_obj = dict(metadata_obj)
            if abs(route_adjustment) > 0.0001:
                metadata_obj["route_adjustment"] = round(route_adjustment, 4)
            filtered.append({
                "title": c["title"],
                "description": c.get("description"),
                "category": c.get("category", "aggregation"),
                "scope": c.get("scope", "project"),
                "source_type": c.get("source_type", "schema"),
                "confidence": adjusted_confidence,
                "status": "active",
                "metadata": json.dumps(metadata_obj),
                "is_existing": is_existing,
            })

        filtered.sort(key=lambda c: (0 if not c["is_existing"] else 1, -c.get("confidence", 0)))

        diversified = []
        by_type: dict[str, list[dict]] = {}
        for c in filtered:
            qtype = c.get("metadata", {})
            if isinstance(qtype, str):
                try:
                    qtype = json.loads(qtype)
                except Exception:
                    qtype = {}
            qt = qtype.get("question_type", "") if isinstance(qtype, dict) else ""
            by_type.setdefault(qt, []).append(c)

        type_order = sorted(by_type.keys(), key=lambda t: -by_type[t][0].get("confidence", 0))
        idx = 0
        while len(diversified) < len(filtered):
            added = False
            for qt in type_order:
                bucket = by_type[qt]
                if idx < len(bucket):
                    candidate = bucket[idx]
                    diversified.append(candidate)
                    added = True
            if not added:
                break
            idx += 1

        if not diversified:
            diversified = filtered

        return diversified[:max_results]

    def get_onboarding(self, project_id: int, max_results: int = 5, language: str = "en") -> List[dict]:
        candidates = _generate_mdl_candidates(project_id, max_results=max_results * 2, language=language)
        results = []
        seen_titles = set()
        for idx, c in enumerate(candidates):
            title = c["title"]
            if title in seen_titles:
                continue
            seen_titles.add(title)
            meta = c.get("metadata", {})
            if isinstance(meta, str):
                try:
                    meta = json.loads(meta)
                except Exception:
                    meta = {}
            results.append({
                "id": -(idx + 1),
                "project_id": project_id,
                "title": title,
                "description": c.get("description"),
                "category": c.get("category", "aggregation"),
                "scope": c.get("scope", "project"),
                "source_type": c.get("source_type", "schema"),
                "source_id": None,
                "confidence": c.get("confidence", 0.5),
                "status": "active",
                "metadata": meta,
                "created_at": None,
                "updated_at": None,
            })
        return results[:max_results]

    def list_catalog(self, project_id: int, search: Optional[str] = None, sort: str = "frequency") -> List[dict]:
        order = "frequency DESC, last_used DESC" if sort == "frequency" else "last_used DESC"
        conditions = ["project_id = ?"]
        params: list = [project_id]
        if search:
            conditions.append("(question LIKE ? OR sql_text LIKE ?) ESCAPE '\\' ")
            escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            params.extend([f"%{escaped}%", f"%{escaped}%"])
        with connection_lock():
            rows = self.db.execute(
                f"SELECT id, project_id, question, sql_text, frequency, last_used, metadata, verified FROM metadata.question_sql_catalog WHERE {' AND '.join(conditions)} ORDER BY {order}",
                params,
            ).fetchall()
        return [
            {
                "id": r[0], "project_id": r[1], "question": r[2], "sql": r[3], "sql_text": r[3],
                "frequency": r[4], "last_used": str(r[5]) if r[5] else None,
                "metadata": r[6] if isinstance(r[6], dict) else (json.loads(r[6]) if isinstance(r[6], str) else {}),
                "verified": bool(r[7]),
            }
            for r in rows
        ]

    def create_catalog_entry(
        self,
        project_id: int,
        question: str,
        sql: str,
        metadata: Optional[dict] = None,
        verified: bool = False,
    ) -> int:
        with connection_lock():
            existing = self.db.execute(
                "SELECT id, frequency FROM metadata.question_sql_catalog WHERE project_id = ? AND question = ?",
                [project_id, question],
            ).fetchone()
            if existing:
                new_freq = (existing[1] or 0) + 1
                self.db.execute(
                    "UPDATE metadata.question_sql_catalog SET frequency = ?, last_used = CURRENT_TIMESTAMP WHERE id = ?",
                    [new_freq, existing[0]],
                )
                return existing[0]
            self.db.execute("INSERT INTO metadata.id_sequences VALUES (?, COALESCE((SELECT MAX(id) FROM metadata.question_sql_catalog), 0)) ON CONFLICT DO NOTHING", ["metadata.question_sql_catalog"])
            existing_seq = self.db.execute("SELECT next_id FROM metadata.id_sequences WHERE table_name = 'metadata.question_sql_catalog'").fetchone()
            if existing_seq and existing_seq[0] <= 1:
                max_existing = self.db.execute("SELECT COALESCE(MAX(id), 0) FROM metadata.question_sql_catalog").fetchone()[0]
                if max_existing > 0:
                    self.db.execute("UPDATE metadata.id_sequences SET next_id = ? WHERE table_name = 'metadata.question_sql_catalog'", [max_existing])
            entry_id = self.db.execute("UPDATE metadata.id_sequences SET next_id = next_id + 1 WHERE table_name = ? RETURNING next_id", ["metadata.question_sql_catalog"]).fetchone()[0]
            self.db.execute(
                "INSERT INTO metadata.question_sql_catalog (id, project_id, question, sql_text, metadata, verified) VALUES (?, ?, ?, ?, ?::JSON, ?)",
                [entry_id, project_id, question, sql, json.dumps(metadata or {}), bool(verified)],
            )
            return entry_id

    def update_catalog_entry(self, entry_id: int, data: dict) -> bool:
        sets = []
        params = []
        for key in ("question", "sql_text", "metadata", "verified"):
            if key in data:
                if key == "metadata":
                    sets.append(f"{key} = ?::JSON")
                    params.append(json.dumps(data[key]))
                elif key == "verified":
                    sets.append(f"{key} = ?")
                    params.append(bool(data[key]))
                else:
                    sets.append(f"{key} = ?")
                    params.append(data[key])
        if not sets:
            return False
        params.append(entry_id)
        with connection_lock():
            self.db.execute(
                f"UPDATE metadata.question_sql_catalog SET {', '.join(sets)} WHERE id = ?",
                params,
            )
        return True

    def delete_catalog_entry(self, entry_id: int) -> bool:
        with connection_lock():
            self.db.execute(
                "DELETE FROM metadata.question_sql_catalog WHERE id = ?", [entry_id],
            )
        return True

    def list_hints(self, user_id: int) -> List[dict]:
        with connection_lock():
            result = self.db.execute(
                "SELECT * FROM metadata.user_preference_hints WHERE user_id = ?",
                [user_id],
            )
            rows = result.fetchall()
        return rows_to_dicts(rows, result.description)

    def create_hint(self, user_id: int, hint_text: str, source_query: Optional[str] = None) -> int:
        with connection_lock():
            self.db.execute("INSERT INTO metadata.id_sequences VALUES (?, COALESCE((SELECT MAX(id) FROM metadata.user_preference_hints), 0)) ON CONFLICT DO NOTHING", ["metadata.user_preference_hints"])
            existing_seq = self.db.execute("SELECT next_id FROM metadata.id_sequences WHERE table_name = 'metadata.user_preference_hints'").fetchone()
            if existing_seq and existing_seq[0] <= 1:
                max_existing = self.db.execute("SELECT COALESCE(MAX(id), 0) FROM metadata.user_preference_hints").fetchone()[0]
                if max_existing > 0:
                    self.db.execute("UPDATE metadata.id_sequences SET next_id = ? WHERE table_name = 'metadata.user_preference_hints'", [max_existing])
            hint_id = self.db.execute("UPDATE metadata.id_sequences SET next_id = next_id + 1 WHERE table_name = ? RETURNING next_id", ["metadata.user_preference_hints"]).fetchone()[0]
            self.db.execute(
                "INSERT INTO metadata.user_preference_hints (id, user_id, hint_text, source_query, confidence) VALUES (?, ?, ?, ?, ?)",
                [hint_id, user_id, hint_text, source_query, 1.0],
            )
            return hint_id

    def delete_hint(self, hint_id: int) -> bool:
        with connection_lock():
            self.db.execute("DELETE FROM metadata.user_preference_hints WHERE id = ?", [hint_id])
        return True

    def get_statistics(self) -> dict:
        with connection_lock():
            total_catalogs = self.db.execute("SELECT COUNT(*) FROM metadata.question_sql_catalog").fetchone()[0]
            total_hints = self.db.execute("SELECT COUNT(*) FROM metadata.user_preference_hints").fetchone()[0]
            top_queries = self.db.execute(
                "SELECT question, sql_text, frequency, last_used FROM metadata.question_sql_catalog ORDER BY frequency DESC, last_used DESC LIMIT 10"
            ).fetchall()
            scores = self.db.execute(
                "SELECT source_layer, score, COUNT(*) FROM metadata.recommendation_scores GROUP BY source_layer, score ORDER BY source_layer, score"
            ).fetchall()
        layer_performance: dict[str, dict[str, float]] = {}
        score_distribution: dict[str, int] = {}
        for layer, score, count in scores:
            key = str(layer or "unknown")
            stats = layer_performance.setdefault(key, {"count": 0, "avg_score": 0})
            stats["avg_score"] = ((stats["avg_score"] * stats["count"]) + (float(score) * int(count))) / (stats["count"] + int(count))
            stats["count"] += int(count)
            score_distribution[str(score)] = score_distribution.get(str(score), 0) + int(count)
        return {
            "total_catalogs": total_catalogs,
            "total_hints": total_hints,
            "top_queries": top_queries,
            "layer_performance": layer_performance,
            "score_distribution": score_distribution,
        }

    def get_weight_history(self) -> List[dict]:
        with connection_lock():
            result = self.db.execute(
                "SELECT * FROM metadata.layer_weight_history ORDER BY created_at DESC LIMIT 100"
            )
            rows = result.fetchall()
        return rows_to_dicts(rows, result.description)

    def get_low_score_alerts(self) -> List[dict]:
        with connection_lock():
            rows = self.db.execute(
                "SELECT source_layer, score, created_at FROM metadata.recommendation_scores WHERE score <= 2 ORDER BY created_at DESC LIMIT 50"
            ).fetchall()
        return [{"source_layer": r[0], "consecutive_low": 1, "last_score": r[1], "timestamp": str(r[2]) if r[2] else None} for r in rows]

    def update_settings(self, settings: dict) -> None:
        with connection_lock():
            for key, value in settings.items():
                if key not in _RECOMMENDER_SETTING_KEYS:
                    continue
                self.db.execute(
                    "UPDATE metadata.settings SET value = ?::JSON, updated_at = CURRENT_TIMESTAMP WHERE key = ?",
                    [value, key],
                )


SAMPLE_DATASET_QUESTIONS: dict[str, dict[str, list[dict]]] = {
    "en": {
        "hr": [
            {"question": "What is the average salary for each position?", "label": "Aggregation"},
            {"question": "Compare the average salary across different departments", "label": "Comparison"},
            {"question": "Which 5 employees have the highest salaries?", "label": "Ranking"},
            {"question": "How has the average salary changed over hire date?", "label": "Trend"},
            {"question": "What is the gender distribution across departments?", "label": "Distribution"},
            {"question": "Are there any unusual patterns in salary distribution?", "label": "Anomaly"},
            {"question": "What is each department's contribution to total payroll?", "label": "Contribution"},
            {"question": "Is there a correlation between hire date and salary?", "label": "Correlation"},
            {"question": "How do employees and departments relate?", "label": "Relationship"},
        ],
        "music": [
            {"question": "What are the top 5 selling albums?", "label": "Ranking"},
            {"question": "What is the total revenue generated from each genre?", "label": "Aggregation"},
            {"question": "Which customers purchased tracks from albums in each genre?", "label": "Relationship"},
            {"question": "Are there any unusual spikes in track purchases?", "label": "Anomaly"},
            {"question": "Is there a correlation between album price and purchase count?", "label": "Correlation"},
            {"question": "How does revenue vary across genres?", "label": "Comparison"},
            {"question": "What is each genre's contribution to total sales?", "label": "Contribution"},
        ],
        "ecommerce": [
            {"question": "Which are the top 3 cities with the highest number of orders?", "label": "Ranking"},
            {"question": "What is the distribution of order statuses?", "label": "Distribution"},
            {"question": "How does the average review score differ between payment types?", "label": "Comparison"},
            {"question": "What is each product category's contribution to total revenue?", "label": "Contribution"},
            {"question": "Are there any unusual patterns in order delivery times?", "label": "Anomaly"},
            {"question": "How has the order volume trended over time?", "label": "Trend"},
            {"question": "What is the average payment value per order?", "label": "Aggregation"},
        ],
        "nba": [
            {"question": "What is the average points scored per game for each team?", "label": "Aggregation"},
            {"question": "How do turnover rates compare between high-scoring and low-scoring teams?", "label": "Comparison"},
            {"question": "Which teams had the highest average points per game this season?", "label": "Ranking"},
            {"question": "How has the average game score trended over the season?", "label": "Trend"},
            {"question": "Is there a correlation between three-point attempts and win rate?", "label": "Correlation"},
            {"question": "What is each team's contribution to total points scored?", "label": "Contribution"},
            {"question": "Are there any anomalies in team performance metrics?", "label": "Anomaly"},
        ],
    },
    "zh": {
        "hr": [
            {"question": "各职位的平均工资是多少？", "label": "汇总"},
            {"question": "不同部门的平均工资对比如何？", "label": "对比"},
            {"question": "薪资排名前5的员工是谁？", "label": "排名"},
            {"question": "员工入职时间与薪资的趋势是什么？", "label": "趋势"},
            {"question": "各部门的性别分布如何？", "label": "分布"},
            {"question": "薪资分布是否存在异常模式？", "label": "异常"},
            {"question": "各部门对总薪资的贡献分别是多少？", "label": "贡献度"},
            {"question": "入职时间与薪资之间是否有关联？", "label": "关联"},
            {"question": "员工和部门之间存在怎样的关联？", "label": "关联分析"},
        ],
        "music": [
            {"question": "销量排名前5的专辑是哪些？", "label": "排名"},
            {"question": "各音乐类型的总收入是多少？", "label": "汇总"},
            {"question": "各类型专辑的购买客户有哪些？", "label": "关联分析"},
            {"question": "曲目购买量是否有异常高峰？", "label": "异常"},
            {"question": "专辑价格与购买次数之间是否有关联？", "label": "关联"},
            {"question": "不同音乐类型的收入差异如何？", "label": "对比"},
            {"question": "各类型对总销售额的贡献分别是多少？", "label": "贡献度"},
        ],
        "ecommerce": [
            {"question": "订单量排名前3的城市是哪些？", "label": "排名"},
            {"question": "各订单状态的分布如何？", "label": "分布"},
            {"question": "不同支付方式的平均评分差异如何？", "label": "对比"},
            {"question": "各产品品类对总收入的贡献分别是多少？", "label": "贡献度"},
            {"question": "订单配送时间是否有异常模式？", "label": "异常"},
            {"question": "订单量随时间有何趋势变化？", "label": "趋势"},
            {"question": "每笔订单的平均支付金额是多少？", "label": "汇总"},
        ],
        "nba": [
            {"question": "各队每场平均得分是多少？", "label": "汇总"},
            {"question": "高分队和低分队的失误率对比如何？", "label": "对比"},
            {"question": "本赛季场均得分最高的球队有哪些？", "label": "排名"},
            {"question": "赛季期间场均比分有何趋势变化？", "label": "趋势"},
            {"question": "三分球出手数和胜率之间是否有关联？", "label": "关联"},
            {"question": "各队对总得分的贡献分别是多少？", "label": "贡献度"},
            {"question": "球队表现指标是否有异常？", "label": "异常"},
        ],
    },
}


def _build_models_summary(project_id: int, max_models: int = 10) -> str:
    from services.ask_service import _models_for_project

    models = _models_for_project(project_id)[:max_models]
    lines = []
    for m in models:
        if m.get("_type") == "calculated_field":
            continue
        name = m.get("display_name") or m.get("name", "")
        cols = m.get("columns", [])
        if not cols:
            continue
        col_descs = []
        for c in cols[:15]:
            cname = c.get("display_name") or c.get("name", "")
            ctype = (c.get("type") or "").upper()
            pk = " (PK)" if c.get("is_primary_key") else ""
            col_descs.append(f"{cname}: {ctype}{pk}")
        lines.append(f"- {name}: {', '.join(col_descs)}")
    return "\n".join(lines)


def _strip_markdown_json_wrappers(content: str) -> str:
    text = str(content or "").strip()
    if not text:
        return ""
    fenced = re.findall(r"```(?:json)?\s*([\s\S]*?)```", text, flags=re.IGNORECASE)
    if fenced:
        text = fenced[0].strip()
    lowered = text.lower()
    if lowered.startswith("json\n"):
        text = text[5:].strip()
    elif lowered == "json":
        text = ""
    return text.strip()


def _extract_first_balanced_json_fragment(text: str) -> str | None:
    if not text:
        return None
    start_candidates = [idx for idx in (text.find("{"), text.find("[")) if idx >= 0]
    if not start_candidates:
        return None
    start = min(start_candidates)
    stack: list[str] = []
    in_string = False
    escaped = False
    for idx in range(start, len(text)):
        ch = text[idx]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            continue
        if ch in "[{":
            stack.append(ch)
            continue
        if ch == "]":
            if not stack or stack[-1] != "[":
                return None
            stack.pop()
            if not stack:
                return text[start: idx + 1]
            continue
        if ch == "}":
            if not stack or stack[-1] != "{":
                return None
            stack.pop()
            if not stack:
                return text[start: idx + 1]
    return None


def _extract_recommendation_items(payload: Any) -> tuple[list[Any], bool]:
    if isinstance(payload, list):
        return payload, True
    if isinstance(payload, dict):
        for key in _RECOMMENDATION_LIST_KEYS:
            value = payload.get(key)
            if isinstance(value, list):
                return value, True
    return [], False


def _parse_recommendation_items(content: str) -> tuple[list[dict[str, Any]] | None, str | None]:
    text = _strip_markdown_json_wrappers(content)
    if not text:
        return [], None
    parsed: Any
    try:
        parsed = json.loads(text)
    except Exception as exc:
        fragment = _extract_first_balanced_json_fragment(text)
        if not fragment:
            return None, str(exc)
        try:
            parsed = json.loads(fragment)
        except Exception as fragment_exc:
            return None, str(fragment_exc)

    items, has_list_payload = _extract_recommendation_items(parsed)
    if not has_list_payload and parsed not in ({}, [], None):
        return None, "Missing recommendations array in JSON payload"
    normalized_items = [item for item in items if isinstance(item, dict)]
    return normalized_items, None


def _repair_recommendation_items_with_llm(
    llm: Any,
    raw_content: str,
    *,
    max_questions: int,
) -> tuple[list[dict[str, Any]] | None, str | None]:
    repair_messages = [
        {
            "role": "system",
            "content": "You are responsible for fixing malformed recommendation data. Return strict JSON only.",
        },
        {
            "role": "user",
            "content": (
                "Convert the text below into a valid JSON object with key \"recommendations\" and an array value (max {max_questions} items).\n"
                "Each item must contain:\n"
                "- question (string)\n"
                "- category (trend|ranking|comparison|distribution|aggregation|anomaly|contribution|correlation|drilldown|relationship)\n"
                "- confidence (0.0-1.0)\n"
                "- model_names (optional array of strings)\n"
                "\n"
                "If recovery is not possible, return {{\"recommendations\": []}}.\n"
                "\n"
                "Malformed text:\n{raw}"
            ).format(max_questions=max_questions, raw=str(raw_content or "")[:12000]),
        },
    ]
    repaired = llm.chat(repair_messages, response_format="json")
    repaired_content = str((repaired or {}).get("content") or "")
    return _parse_recommendation_items(repaired_content)


def generate_llm_recommendations(project_id: int, language: str = "en", max_questions: int = 8) -> list[dict]:
    try:
        from services.llm_service import LLMService
        llm = LLMService()
        if not llm.is_configured():
            LOGGER.info("LLM not configured, skipping LLM recommendation generation")
            return []
    except Exception:
        LOGGER.warning("Failed to initialize LLMService, skipping LLM recommendations")
        return []

    models_summary = _build_models_summary(project_id)
    if not models_summary.strip():
        return []

    lang_name = _language_name(language) or "English"
    prompt_template = _RECOMMENDATION_LLM_PROMPT
    prompt = prompt_template.format(max_questions=max_questions, language=lang_name, models_summary=models_summary)

    try:
        response = llm.chat([
            {"role": "system", "content": "You are a data analyst helping users discover insights from datasets. Return only valid JSON."},
            {"role": "user", "content": prompt},
        ], response_format="json")
        content = str(response.get("content", "") or "")
        if not content:
            return []

        results, parse_error = _parse_recommendation_items(content)
        if parse_error:
            LOGGER.warning(
                "LLM recommendation payload parse failed for project %s: %s; trying one repair retry",
                project_id,
                parse_error,
            )
            repaired_results, repair_error = _repair_recommendation_items_with_llm(
                llm,
                content,
                max_questions=max_questions,
            )
            if repair_error:
                raise ValueError(f"initial parse error: {parse_error}; repair parse error: {repair_error}")
            results = repaired_results
        if results is None:
            return []

        candidates = []
        for item in results[:max_questions]:
            if not isinstance(item, dict) or "question" not in item:
                continue
            question = str(item.get("question") or "").strip()
            if not question:
                continue
            cat = item.get("category", "aggregation")
            if cat not in _VALID_RECOMMENDATION_CATEGORIES:
                cat = "aggregation"
            confidence_raw = item.get("confidence", 0.7)
            try:
                confidence = float(confidence_raw)
            except Exception:
                confidence = 0.7
            confidence = max(0.0, min(confidence, 1.0))
            model_names_raw = item.get("model_names", [])
            if isinstance(model_names_raw, list):
                model_names = [str(name).strip() for name in model_names_raw if str(name).strip()]
            elif model_names_raw:
                model_names = [str(model_names_raw).strip()]
            else:
                model_names = []
            candidates.append({
                "title": question,
                "category": cat,
                "scope": "project",
                "source_type": "llm",
                "confidence": confidence,
                "metadata": {"model_names": model_names, "question_type": cat, "generated_by": "llm"},
            })
        return candidates
    except Exception as exc:
        LOGGER.warning("LLM recommendation generation failed for project %s: %s", project_id, exc)
        return []


def get_sample_dataset_questions(sample_dataset: str, language: str = "en") -> list[dict]:
    normalized = (sample_dataset or "").strip().lower()
    lang_key = "zh" if (language or "en").strip().lower().replace("-", "_").startswith("zh") else "en"
    dataset_questions = SAMPLE_DATASET_QUESTIONS.get(lang_key, SAMPLE_DATASET_QUESTIONS.get("en", {}))
    dataset_key = normalized if normalized in dataset_questions else None
    if not dataset_key:
        for key in dataset_questions:
            if normalized == key:
                dataset_key = key
                break
    if not dataset_key:
        return []
    return dataset_questions[dataset_key]
