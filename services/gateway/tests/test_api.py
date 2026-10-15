from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.gateway.api import create_api_router
from services.gateway.db import session_dependency


class EmptySession:
    async def scalar(self, _query):
        return None


async def empty_session_dependency():
    yield EmptySession()


def test_local_test_email_reaches_login_handler_without_query_request():
    app = FastAPI()
    app.include_router(create_api_router())
    app.dependency_overrides[session_dependency] = empty_session_dependency

    response = TestClient(app).post(
        "/api/auth/login",
        json={"email": "admin@example.test", "password": "not-the-password"},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Email or password is incorrect"
