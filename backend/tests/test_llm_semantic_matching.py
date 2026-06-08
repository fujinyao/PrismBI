"""
测试 LLM 语义元数据匹配 + SQL 生成能力。

基于 Samples 项目的元数据（Olist 电商 + Employees HR），生成中文问题，
测试 token 重叠快速路径命中、LLM 回退语义命中、以及完整 SQL 生成链路。
"""

from __future__ import annotations

import json
import logging
import os
import sys

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
LOGGER = logging.getLogger("test_llm_semantic")

# ── ensure backend is importable ──
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services.ask_service import (
    _llm_semantic_matching,
    _models_for_project,
    _relations_for_project,
    _semantic_hits,
    _semantic_prompt,
    _tokenize,
    _generate_sql,
    _knowledge_context,
)
from services.llm_service import LLMService

PROJECT_ID = 1  # Samples


def banner(title: str) -> None:
    print(f"\n{'=' * 72}")
    print(f"  {title}")
    print(f"{'=' * 72}")


def print_metadata() -> None:
    """Print the full metadata catalog for reference."""
    models = _models_for_project(PROJECT_ID)
    relations = _relations_for_project(PROJECT_ID)
    print(f"Total models: {len(models)}, relations: {len(relations)}")
    for m in models:
        cols = ", ".join(f"{c['name']}({c['type']})" for c in m["columns"][:5])
        print(f"  [{m['_type']}] {m['name']} (display: {m['display_name']}) cols: {cols}")
    for r in relations:
        print(f"  rel: {r['source_model']}.{r['source_column']} -> {r['target_model']}.{r['target_column']} ({r['relation_type']})")


def run_token_overlap(question: str, expected_has_hits: bool) -> dict:
    hits = _semantic_hits(question, _models_for_project(PROJECT_ID), _relations_for_project(PROJECT_ID))
    tokens = _tokenize(question)
    status = "✓" if hits["has_hits"] == expected_has_hits else "✗"
    print(f"  {status} tokens={tokens} | has_hits={hits['has_hits']} (expected {expected_has_hits})")
    if hits.get("models"):
        for m in hits["models"]:
            matched = [c["name"] for c in m.get("matched_columns", m.get("columns", []))]
            print(f"      model={m['name']} matched_cols={matched[:5]}")
    return hits


def run_llm_matching(question: str) -> dict | None:
    result = _llm_semantic_matching(question, PROJECT_ID)
    if result and result.get("has_hits"):
        models = [m["name"] for m in result["models"]]
        cols = {}
        for m in result["models"]:
            cols[m["name"]] = [c["name"] for c in m.get("matched_columns", [])]
        print(f"  ✓ LLM matched models={models}")
        for k, v in cols.items():
            if v:
                print(f"      {k} columns={v}")
        return result
    else:
        print(f"  ✗ LLM returned no matches")
        return None


def run_full_pipeline(question: str, label: str = "") -> None:
    tag = f" [{label}]" if label else ""
    print(f"\n  ── Question: {question}{tag}")

    # 1. Token overlap
    tokens = _tokenize(question)
    hits = _semantic_hits(question, _models_for_project(PROJECT_ID), _relations_for_project(PROJECT_ID))

    if hits["has_hits"]:
        print(f"     Phase 1 (token overlap): ✓ hit, models={[m['name'] for m in hits['models']]}")
        prompt_text, tables, final_hits = _semantic_prompt(PROJECT_ID, question, require_hits=True)
    else:
        print(f"     Phase 1 (token overlap): ✗ no hit → trying LLM fallback")
        prompt_text, tables, final_hits = _semantic_prompt(PROJECT_ID, question, require_hits=True)

    if not final_hits.get("has_hits"):
        print(f"     ✗ No metadata matched (token overlap + LLM fallback both failed)")
        return

    print(f"     Phase 2: retrieved tables={tables}")

    # 3. Knowledge context
    knowledge_text, knowledge_hits = _knowledge_context(PROJECT_ID, question)
    if knowledge_hits["instructions"] or knowledge_hits["sql_pairs"]:
        print(f"     Knowledge: instructions={len(knowledge_hits['instructions'])}, sql_pairs={len(knowledge_hits['sql_pairs'])}")

    # 4. SQL generation
    llm = LLMService()
    if not llm.is_configured():
        print(f"     ⚠ LLM not configured — skipping SQL generation")
        return

    sql_result = _generate_sql(
        question=question,
        project_id=PROJECT_ID,
        semantic_context=prompt_text,
        retrieved_tables=tables,
        semantic_hits=final_hits,
        knowledge_context=knowledge_text,
        language="zh",
    )
    sql = sql_result.get("sql")
    if sql and sql.strip():
        print(f"     ✓ Generated SQL ({len(sql)} chars):")
        for line in sql.split("\n"):
            print(f"       {line}")
        print(f"     Summary: {sql_result.get('summary', '')[:120]}")
    else:
        print(f"     ✗ SQL generation failed: {sql_result.get('summary', 'no summary')}")


def main():
    llm = LLMService()
    if not llm.is_configured():
        print("⚠ LLM is not configured. Set LLM_PROVIDER_TYPE and related env vars then re-run.")
        print("  The token-overlap tests will still run but SQL generation will be skipped.\n")

    # ── Metadata overview ──
    banner("Samples 项目元数据概览")
    print_metadata()

    # ── Token overlap 快速路径 ──
    banner("Phase 1: Token Overlap 快速路径")

    print("\n-- 英文关键词匹配英文模型名")
    run_token_overlap("show me top revenue by city", True)

    print("\n-- 纯中文问 + 英文元数据（重叠不命中 → 走 LLM 回退）")
    run_token_overlap("订单客户城市", False)

    print("\n-- 完全无关问题")
    run_token_overlap("今天天气怎么样", False)

    # ── LLM 语义匹配 ──
    banner("Phase 2: LLM 语义匹配回退")

    print("\n-- 中文问题 → 英文电商元数据: 按城市看销售额排行")
    run_llm_matching("按城市看销售额排行")

    print("\n-- 中文问题 → 英文电商元数据: 每个产品类别的总销售额")
    run_llm_matching("每个产品类别的总销售额")

    print("\n-- 中文问题 → 英文电商元数据: 评价最差的订单")
    run_llm_matching("评价最差的订单")

    print("\n-- 中文问题 → 英文 HR 元数据: 各部门平均薪资")
    run_llm_matching("各部门平均薪资")

    print("\n-- 中文问题 → 英文 HR 元数据: 入职最久的员工")
    run_llm_matching("入职最久的员工")

    print("\n-- 混合中英文: 销量最高的产品 top 10")
    run_llm_matching("销量最高的产品 top 10")

    print("\n-- 模糊商业术语: GMV 趋势按月份")
    run_llm_matching("GMV 趋势按月份")

    print("\n-- 跨域查询: 高薪资员工所在城市的订单表现")
    run_llm_matching("高薪资员工所在城市的订单表现")

    # ── 完整端到端 SQL 生成 ──
    if llm.is_configured():
        banner("Phase 3: 端到端 SQL 生成")

        questions = [
            ("按城市看销售额排行", "电商-城市销售额"),
            ("每个产品类别总销售额", "电商-产品类目"),
            ("评价最差的10个订单", "电商-差评订单"),
            ("各部门平均薪资排名", "HR-部门薪资"),
            ("入职最久的5名员工", "HR-资深员工"),
        ]
        for q, label in questions:
            run_full_pipeline(q, label)
    else:
        banner("Phase 3: SQL 生成 (跳过)")
        print("  设置 LLM 配置后重试即可执行 SQL 生成测试。")

    print(f"\n{'=' * 72}")
    print("  测试完成")
    print(f"{'=' * 72}")


if __name__ == "__main__":
    main()
