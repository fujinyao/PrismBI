from __future__ import annotations

import json


def test_migrate_project_datasources_config_overrides_to_varchar(test_db):
    import db

    test_db.execute(
        """
        INSERT INTO metadata.projects (id, name, display_name, description, language, prompt)
        VALUES (1, 'migration-project', 'Migration Project', 'migration', 'EN', 'prompt')
        """
    )
    test_db.execute(
        """
        INSERT INTO metadata.datasources (id, name, type, properties_encrypted)
        VALUES (1, 'source', 'postgresql', '{}')
        """
    )
    test_db.execute(
        """
        INSERT INTO metadata.project_datasources (id, project_id, datasource_id, alias, config_overrides)
        VALUES (1, 1, 1, 'main', ?::JSON)
        """,
        ['{"limit": 10}'],
    )
    test_db.execute(
        """
        INSERT INTO metadata.model_datasource_mappings
        (id, project_id, model_name, project_datasource_id, table_catalog, table_schema)
        VALUES (1, 1, 'orders', 1, NULL, 'public')
        """
    )

    assert db._project_datasource_config_overrides_type(test_db) == "JSON"

    assert db._migrate_project_datasources_config_overrides_to_varchar(test_db) is True

    assert db._project_datasource_config_overrides_type(test_db) == "VARCHAR"
    stored = test_db.execute(
        "SELECT config_overrides FROM metadata.project_datasources WHERE id = 1"
    ).fetchone()[0]
    assert isinstance(stored, str)
    assert json.loads(stored) == {"limit": 10}

    mapping = test_db.execute(
        "SELECT project_datasource_id FROM metadata.model_datasource_mappings WHERE id = 1"
    ).fetchone()
    assert mapping == (1,)

    assert db._migrate_project_datasources_config_overrides_to_varchar(test_db) is False


def test_migrate_models_model_type_from_legacy_columns(test_db):
    import db

    test_db.execute(
        """
        INSERT INTO metadata.projects (id, name, display_name, description, language, prompt)
        VALUES (1, 'migration-project', 'Migration Project', 'migration', 'EN', 'prompt')
        """
    )
    test_db.execute("ALTER TABLE metadata.models ADD COLUMN source_type VARCHAR")
    test_db.execute(
        """
        INSERT INTO metadata.models
            (id, project_id, name, table_reference, model_type, source_type, column_defs)
        VALUES
            (1, 1, 'orders', 'orders', NULL, 'materialized view', '[]'::JSON),
            (2, 1, 'customers_view', 'customers_view', '', 'view', '[]'::JSON),
            (3, 1, 'events', 'events', NULL, NULL, '[]'::JSON),
            (4, 1, 'ext_data', 'ext_data', 'external_table', NULL, '[]'::JSON)
        """
    )

    assert db._migrate_models_model_type(test_db) is True

    rows = test_db.execute(
        "SELECT id, model_type FROM metadata.models ORDER BY id"
    ).fetchall()
    assert rows == [
        (1, "materialized_view"),
        (2, "view"),
        (3, "table"),
        (4, "other"),
    ]

    assert db._migrate_models_model_type(test_db) is False


def test_migrate_schema_adds_catalog_verified_column_for_upgrade_db(test_db):
    import db

    test_db.execute("DROP TABLE IF EXISTS metadata.question_sql_catalog")
    test_db.execute(
        """
        CREATE TABLE metadata.question_sql_catalog (
            id INTEGER PRIMARY KEY,
            project_id INTEGER NOT NULL REFERENCES metadata.projects(id),
            question TEXT NOT NULL,
            sql_text TEXT NOT NULL,
            frequency INTEGER DEFAULT 1,
            last_used TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            metadata JSON
        )
        """
    )

    assert db._metadata_column_exists(test_db, "question_sql_catalog", "verified") is False

    db._migrate_schema(test_db)

    assert db._metadata_column_exists(test_db, "question_sql_catalog", "verified") is True


def test_migrate_models_model_type_overrides_default_table_once(test_db):
    import db

    test_db.execute(
        """
        INSERT INTO metadata.projects (id, name, display_name, description, language, prompt)
        VALUES (1, 'migration-project', 'Migration Project', 'migration', 'EN', 'prompt')
        """
    )
    test_db.execute("ALTER TABLE metadata.models ADD COLUMN source_type VARCHAR")
    test_db.execute(
        """
        INSERT INTO metadata.models
            (id, project_id, name, table_reference, model_type, source_type, column_defs)
        VALUES
            (1, 1, 'legacy_view', 'legacy_view', 'table', 'view', '[]'::JSON)
        """
    )

    assert db._migrate_models_model_type(test_db) is True

    migrated = test_db.execute(
        "SELECT model_type FROM metadata.models WHERE id = 1"
    ).fetchone()[0]
    assert migrated == "view"

    marker = test_db.execute(
        "SELECT value FROM metadata.settings WHERE key = 'migration_models_model_type_legacy_backfill_done'"
    ).fetchone()
    assert marker is not None

    test_db.execute("UPDATE metadata.models SET model_type = 'table' WHERE id = 1")

    assert db._migrate_models_model_type(test_db) is False

    after_manual_override = test_db.execute(
        "SELECT model_type FROM metadata.models WHERE id = 1"
    ).fetchone()[0]
    assert after_manual_override == "table"
