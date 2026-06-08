from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


class TestRegister:
    def test_register_disabled_by_default(self, test_app: TestClient, sample_user: dict):
        response = test_app.post("/api/auth/register", json=sample_user)
        assert response.status_code == 403
        assert response.json()["detail"] == "Self registration is disabled"

    def test_register_success_when_enabled(self, test_app: TestClient, sample_user: dict, monkeypatch):
        monkeypatch.setenv("PRISMBI_ENABLE_REGISTRATION", "true")
        response = test_app.post("/api/auth/register", json=sample_user)
        assert response.status_code == 200
        data = response.json()
        assert data["data"]["user"]["username"] == sample_user["username"]
        assert data["data"]["user"].get("permissions", []) == []


class TestLogin:
    def test_login_success(self, test_app: TestClient, seed_user: dict):
        response = test_app.post(
            "/api/auth/login",
            json={"username": "testuser", "password": "password123"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["data"]["token"]
        assert data["data"]["user"]["username"] == "testuser"
        assert data["data"]["is_first_login"] is True

    def test_login_wrong_password(self, test_app: TestClient, seed_user: dict):
        response = test_app.post(
            "/api/auth/login",
            json={"username": "testuser", "password": "wrongpassword"},
        )
        assert response.status_code == 401
        assert response.json()["detail"] == "Invalid username or password"


class TestMe:
    def test_me_authenticated(self, test_app: TestClient, auth_headers: dict):
        response = test_app.get("/api/auth/me", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["data"]["username"] == "testuser"
        assert any(role["name"] == "super_admin" for role in data["data"]["roles"])

    def test_me_unauthenticated(self, test_app: TestClient):
        response = test_app.get("/api/auth/me")
        assert response.status_code == 401
        assert response.json()["detail"] == "Missing authorization header"


class TestRefresh:
    def test_refresh_token(self, test_app: TestClient, auth_headers: dict):
        response = test_app.post("/api/auth/refresh", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["data"]["token"]

    def test_refresh_without_auth(self, test_app: TestClient):
        response = test_app.post("/api/auth/refresh")
        assert response.status_code == 401


class TestApiTokens:
    def test_api_token_authenticates_and_updates_usage(self, test_app: TestClient, auth_headers: dict):
        import db

        created = test_app.post(
            "/api/profile/tokens",
            headers=auth_headers,
            json={"name": "automation", "scope": ["settings:read"]},
        )
        assert created.status_code == 200

        token = created.json()["data"]["token"]
        response = test_app.get("/api/settings", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 200
        assert response.json()["data"]["settings"]["app_name"] == "PrismBI"

        last_used = db.get_connection().execute(
            "SELECT last_used_at FROM metadata.api_tokens WHERE id = ?",
            [created.json()["data"]["id"]],
        ).fetchone()[0]
        assert last_used is not None

    def test_api_token_scope_is_enforced(self, test_app: TestClient, auth_headers: dict):
        created = test_app.post(
            "/api/profile/tokens",
            headers=auth_headers,
            json={"name": "limited", "scope": ["settings:read"]},
        )
        token = created.json()["data"]["token"]

        response = test_app.post(
            "/api/projects",
            headers={"Authorization": f"Bearer {token}"},
            json={"name": "blocked"},
        )
        assert response.status_code == 403


class TestSSOService:
    def test_validate_redirect_uri_valid(self):
        from services.sso_service import _validate_redirect_uri
        result = _validate_redirect_uri("https://app.example.com/callback")
        assert result == "https://app.example.com/callback"

    def test_validate_redirect_uri_rejects_relative(self):
        from services.sso_service import _validate_redirect_uri
        import pytest
        with pytest.raises(ValueError, match="absolute URL"):
            _validate_redirect_uri("/callback")

    def test_validate_redirect_uri_rejects_javascript(self):
        from services.sso_service import _validate_redirect_uri
        import pytest
        with pytest.raises(ValueError):
            _validate_redirect_uri("javascript:alert(1)")

    def test_validate_redirect_uri_allows_whitelisted_origin(self):
        from services.sso_service import _validate_redirect_uri
        config = {"allowed_redirect_origins": ["https://app.example.com"]}
        result = _validate_redirect_uri("https://app.example.com/auth/callback", config)
        assert result == "https://app.example.com/auth/callback"

    def test_validate_redirect_uri_rejects_non_whitelisted_origin(self):
        from services.sso_service import _validate_redirect_uri
        import pytest
        config = {"allowed_redirect_origins": ["https://app.example.com"]}
        with pytest.raises(ValueError, match="not in the allowed list"):
            _validate_redirect_uri("https://evil.example.com/callback", config)

    def test_validate_redirect_uri_no_whitelist_allows_any_https(self):
        from services.sso_service import _validate_redirect_uri
        result = _validate_redirect_uri("https://any.example.com/path", {})
        assert result == "https://any.example.com/path"

    def test_validate_redirect_uri_handles_non_string_allowed_origins(self):
        from services.sso_service import _validate_redirect_uri

        config = {"allowed_redirect_origins": [None, 123, "https://app.example.com"]}
        result = _validate_redirect_uri("https://app.example.com/auth/callback", config)
        assert result == "https://app.example.com/auth/callback"

    def test_map_claims_to_roles_default(self):
        from services.sso_service import map_claims_to_roles
        roles = map_claims_to_roles({"sub": "123"}, None)
        assert roles == ["viewer"]

    def test_map_claims_to_roles_string_match(self):
        from services.sso_service import map_claims_to_roles
        roles = map_claims_to_roles({"role": "admin"}, {"role": "admin"})
        assert "admin" in roles

    def test_map_claims_to_roles_list(self):
        from services.sso_service import map_claims_to_roles
        roles = map_claims_to_roles({"roles": ["admin", "editor"]}, {"roles": "admin"})
        assert "admin" in roles
        assert "editor" in roles

    def test_sso_login_or_create_new_user(self, test_db):
        from services.sso_service import sso_login_or_create
        claims = {"sub": "google-12345", "email": "newuser@example.com", "name": "New User"}
        result = sso_login_or_create(claims, ["viewer"])
        assert result["is_new"] is True
        assert result["username"] == "sso_google-12345"
        row = test_db.execute("SELECT email, display_name FROM metadata.users WHERE id = ?", [result["user_id"]]).fetchone()
        assert row[0] == "newuser@example.com"
        assert row[1] == "New User"

    def test_sso_login_or_create_existing_user(self, test_db, seed_user):
        from services.sso_service import sso_login_or_create
        claims = {"sub": "sso-existing", "email": "updated@example.com", "name": "Updated"}
        result1 = sso_login_or_create(claims, ["viewer"])
        assert result1["is_new"] is True
        result2 = sso_login_or_create(claims, ["viewer"])
        assert result2["is_new"] is False
        assert result2["user_id"] == result1["user_id"]

    def test_sso_login_or_create_email_collision(self, test_db, seed_user):
        from services.sso_service import sso_login_or_create
        import pytest
        claims = {"sub": "new-sso-user", "email": "testuser@example.com", "name": "Collision"}
        with pytest.raises(ValueError, match="already exists"):
            sso_login_or_create(claims, ["viewer"])

    def test_sso_login_or_create_inactive_user(self, test_db):
        from db import connection_lock, get_connection
        from services.sso_service import sso_login_or_create
        import pytest
        with connection_lock():
            con = get_connection()
            uid = con.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM metadata.users").fetchone()[0]
            con.execute("INSERT INTO metadata.users (id, username, display_name, email, password_hash, status) VALUES (?, ?, ?, ?, '', 'INACTIVE')", [uid, "sso_inactive", "Inactive", "inactive@example.com"])
        claims = {"sub": "inactive", "email": "inactive@example.com", "name": "Inactive"}
        with pytest.raises(ValueError, match="inactive"):
            sso_login_or_create(claims, ["viewer"])

    def test_sso_login_or_create_missing_sub(self, test_db):
        from services.sso_service import sso_login_or_create
        import pytest
        with pytest.raises(ValueError, match="sub"):
            sso_login_or_create({"email": "no@sub.com"}, ["viewer"])

    def test_sso_login_endpoint_disabled(self, test_app: TestClient):
        response = test_app.get("/api/auth/sso/login", follow_redirects=False)
        assert response.status_code == 400

    def test_sso_callback_invalid_state(self, test_app: TestClient):
        response = test_app.get("/api/auth/sso/callback?code=fake&state=invalid_state")
        assert response.status_code == 400

    def test_sso_token_endpoint_disabled(self, test_app: TestClient):
        response = test_app.post("/api/auth/sso/token", json={})
        assert response.status_code == 400

    def test_sso_login_accepts_non_string_sso_config_values(self, test_app: TestClient, monkeypatch):
        from routers import auth as auth_router

        monkeypatch.setattr(
            auth_router,
            "_get_sso_config",
            lambda: {
                "enabled": True,
                "issuer_url": 12345,
                "client_id": 67890,
                "client_secret": 111,
            },
        )
        monkeypatch.setattr(auth_router, "generate_state", lambda: "state")
        monkeypatch.setattr(auth_router, "store_state", lambda _state: "nonce")
        monkeypatch.setattr(auth_router, "_validate_redirect_uri", lambda redirect_uri, config=None: redirect_uri)
        monkeypatch.setattr(
            auth_router,
            "get_authorization_url",
            lambda issuer_url, client_id, redirect_uri, state, nonce=None: f"https://example.com/auth?issuer={issuer_url}&client_id={client_id}",
        )

        response = test_app.get("/api/auth/sso/login", follow_redirects=False)

        assert response.status_code == 302
        assert "issuer=12345" in response.headers.get("location", "")

    def test_generate_state_unique(self):
        from services.sso_service import generate_state
        states = {generate_state() for _ in range(20)}
        assert len(states) == 20

    def test_store_and_consume_state(self, test_db):
        from services.sso_service import generate_state, store_state, consume_state
        state = generate_state()
        store_state(state)
        nonce = consume_state(state)
        assert nonce is not None
        assert isinstance(nonce, str)
        nonce2 = consume_state(state)
        assert nonce2 is None

    def test_consume_state_unknown_state(self, test_db):
        from services.sso_service import consume_state
        result = consume_state("nonexistent_state_12345")
        assert result is None
