"""Tests for middleware server endpoints."""
import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient


@pytest.fixture
def mock_state():
    """Create a mock AppState with all fields populated."""
    from arena_kicad_library_sync.server.middleware_server import AppState

    state = AppState()
    state.db = MagicMock()
    state.arena_api = MagicMock()
    state.sync_engine = MagicMock()
    state.config = MagicMock()
    return state


@pytest.fixture
def mock_app(mock_state):
    """Create app with mocked dependencies by patching _state."""
    with patch(
        "arena_kicad_library_sync.server.middleware_server._state", mock_state
    ):
        # Import the module-level app after patching _state so the /health
        # endpoint reads the patched object.
        from arena_kicad_library_sync.server.middleware_server import app

        # We also need to ensure the lifespan does not run (it calls
        # _init_from_env which would fail).  TestClient context-manages the
        # lifespan by default; override it to be a no-op.
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def _noop_lifespan(_app):
            yield

        original_lifespan = app.router.lifespan_context
        app.router.lifespan_context = _noop_lifespan
        try:
            yield TestClient(app)
        finally:
            app.router.lifespan_context = original_lifespan


class TestHealthCheck:
    def test_health_endpoint(self, mock_app, mock_state):
        """GET /health returns status."""
        with patch(
            "arena_kicad_library_sync.server.middleware_server._state",
            mock_state,
        ):
            resp = mock_app.get("/health")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "healthy"
            assert data["database"] is True
            assert data["arena_connected"] is True
            assert data["sync_engine_ready"] is True

    def test_health_endpoint_no_db(self, mock_state):
        """GET /health reflects missing DB."""
        mock_state.db = None
        mock_state.arena_api = None
        mock_state.sync_engine = None

        with patch(
            "arena_kicad_library_sync.server.middleware_server._state",
            mock_state,
        ):
            from arena_kicad_library_sync.server.middleware_server import app
            from contextlib import asynccontextmanager

            @asynccontextmanager
            async def _noop_lifespan(_app):
                yield

            original = app.router.lifespan_context
            app.router.lifespan_context = _noop_lifespan
            try:
                client = TestClient(app)
                resp = client.get("/health")
                assert resp.status_code == 200
                data = resp.json()
                assert data["database"] is False
                assert data["arena_connected"] is False
                assert data["sync_engine_ready"] is False
            finally:
                app.router.lifespan_context = original


class TestApiKeyAuth:
    def test_api_key_required(self, mock_state):
        """Sync endpoints require API key when configured."""
        mock_state.config = MagicMock()

        with patch(
            "arena_kicad_library_sync.server.middleware_server._state",
            mock_state,
        ), patch.dict("os.environ", {"SERVER_API_KEY": "secret-key-123"}):
            from arena_kicad_library_sync.server.middleware_server import app
            from contextlib import asynccontextmanager

            @asynccontextmanager
            async def _noop_lifespan(_app):
                yield

            original = app.router.lifespan_context
            app.router.lifespan_context = _noop_lifespan
            try:
                client = TestClient(app)
                # Attempt to hit a sync endpoint without an API key
                resp = client.get("/v1/sync/status")
                assert resp.status_code == 401
                assert "Invalid API key" in resp.json()["detail"]
            finally:
                app.router.lifespan_context = original

    def test_api_key_success(self, mock_state):
        """Valid API key allows access."""
        mock_state.config = MagicMock()
        mock_state.db.get_last_sync_time.return_value = None
        mock_state.db.get_part_count.return_value = 42
        mock_state.db.get_dirty_count.return_value = 0

        with patch(
            "arena_kicad_library_sync.server.middleware_server._state",
            mock_state,
        ), patch.dict("os.environ", {"SERVER_API_KEY": "secret-key-123"}):
            from arena_kicad_library_sync.server.middleware_server import app
            from contextlib import asynccontextmanager

            @asynccontextmanager
            async def _noop_lifespan(_app):
                yield

            original = app.router.lifespan_context
            app.router.lifespan_context = _noop_lifespan
            try:
                client = TestClient(app)
                resp = client.get(
                    "/v1/sync/status",
                    headers={"X-API-Key": "secret-key-123"},
                )
                assert resp.status_code == 200
                data = resp.json()
                assert data["total_parts"] == 42
                assert data["dirty_parts"] == 0
            finally:
                app.router.lifespan_context = original
