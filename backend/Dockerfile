FROM python:3.12-slim

ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ARG PIP_TRUSTED_HOST=pypi.tuna.tsinghua.edu.cn
ARG PIP_DEFAULT_TIMEOUT=120
ARG PIP_RETRIES=8

ENV PIP_INDEX_URL=${PIP_INDEX_URL}
ENV PIP_TRUSTED_HOST=${PIP_TRUSTED_HOST}
ENV PIP_DEFAULT_TIMEOUT=${PIP_DEFAULT_TIMEOUT}
ENV PIP_RETRIES=${PIP_RETRIES}

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        curl \
        unixodbc \
        unixodbc-dev \
        g++ && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml ./

RUN pip install --no-cache-dir \
        "fastapi>=0.115.0" \
        "uvicorn[standard]>=0.32.0" \
        "duckdb>=1.1.0" \
        "python-jose[cryptography]>=3.3.0" \
        "bcrypt>=4.2.0" \
        "websockets>=14.0" \
        "cryptography>=44.0.0" \
        "lancedb>=0.12.0" \
        "sqlglot>=26.0.0" \
        "pyarrow>=19.0.0" \
        "httpx>=0.28.0" \
        "pydantic>=2.10.0" \
        "pydantic-settings>=2.7.0" \
        "python-multipart>=0.0.18" \
        "orjson>=3.10.0" \
        "sse-starlette>=2.2.0" \
        "wren-engine>=0.5.0" \
        "psycopg[binary]>=3.2.0" \
        "psycopg2-binary>=2.9.10" \
        "mysql-connector-python>=9.0.0" \
        "PyMySQL>=1.1.1" \
        "clickhouse-connect>=0.8.10" \
        "pymssql>=2.3.1" \
        "pyodbc>=5.1.0" \
        "trino>=0.330.0" \
        "PyAthena>=3.10.0" \
        "google-cloud-bigquery>=3.32.0" \
        "oracledb>=2.5.0" \
        "snowflake-connector-python>=3.17.0" \
        "databricks-sql-connector>=4.1.3"

COPY . .

ENV HOSTNAME="0.0.0.0"
ENV PRISMBI_DB_PATH=/data/prismbi.duckdb
ENV PRISMBI_DATA_DIR=/data
ENV PRISMBI_ENCRYPTION_KEY_FILE=/data/master.key
ENV PRISMBI_BOOTSTRAP_ADMIN_PASSWORD_FILE=/data/bootstrap-admin-password

RUN mkdir -p /data

EXPOSE 8400

HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=5 \
    CMD curl -f http://localhost:8400/health || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8400", "--log-level", "info"]
