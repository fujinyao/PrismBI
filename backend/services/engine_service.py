from __future__ import annotations

from typing import Any, AsyncIterable, List, Optional


class EngineService:
    def __init__(self, endpoint: str = "http://localhost:8000"):
        self.endpoint = endpoint

    def query(self, sql: str, project_id: int, limit: Optional[int] = None) -> dict:
        raise NotImplementedError

    def dry_plan(self, sql: str, project_id: int) -> dict:
        raise NotImplementedError

    async def ask(
        self,
        question: str,
        thread_id: Optional[int] = None,
        previous_questions: Optional[List[str]] = None,
    ) -> dict:
        raise NotImplementedError

    async def ask_stream(
        self,
        question: str,
        thread_id: Optional[int] = None,
        previous_questions: Optional[List[str]] = None,
    ) -> AsyncIterable[dict]:
        raise NotImplementedError
        yield  # pragma: no cover

    def generate_chart_spec(self, sql: str, question: Optional[str] = None) -> dict:
        raise NotImplementedError

    def list_data_source_tables(self, datasource_id: int) -> List[str]:
        raise NotImplementedError
