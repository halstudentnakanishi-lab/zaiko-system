"""Run only against an explicitly selected disposable local MariaDB server."""
import importlib.util
import os
from pathlib import Path
import uuid

import pymysql
import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "Portfolio_2026"
pytestmark = pytest.mark.skipif(
    not os.getenv("ZAIKO_TEST_DB_PORT"), reason="Set ZAIKO_TEST_DB_PORT for a disposable local MariaDB"
)


def execute_script(conn, sql):
    with conn.cursor() as cursor:
        cursor.execute(sql)
        while cursor.nextset():
            pass
    conn.commit()


@pytest.fixture(params=["fresh", "legacy_migration"])
def api(request, monkeypatch):
    options = dict(host="127.0.0.1", port=int(os.environ["ZAIKO_TEST_DB_PORT"]),
                   user="root", password=os.getenv("ZAIKO_TEST_DB_PASSWORD", ""),
                   charset="utf8mb4", client_flag=pymysql.constants.CLIENT.MULTI_STATEMENTS)
    name = "zaiko_test_" + uuid.uuid4().hex
    admin = pymysql.connect(**options)
    with admin.cursor() as cur:
        cur.execute(f"CREATE DATABASE `{name}` CHARACTER SET utf8mb4")
    conn = pymysql.connect(**options, database=name)
    try:
        sql = (APP / "inventory_db.sql").read_text(encoding="utf-8")
        if request.param == "legacy_migration":
            sql = "\n".join(line for line in sql.splitlines() if not any(
                f"`{field}`" in line for field in ["reason_code", "before_quantity", "after_quantity"]))
        execute_script(conn, sql)
        if request.param == "legacy_migration":
            migration = (APP / "migrations/001_transaction_details.sql").read_text()
            execute_script(conn, migration)
            execute_script(conn, migration)  # Reapplying must preserve existing data.
        monkeypatch.chdir(APP)
        spec = importlib.util.spec_from_file_location("inventory_test_app", APP / "main.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        monkeypatch.setattr(module, "get_conn", lambda: pymysql.connect(
            **options, database=name, cursorclass=pymysql.cursors.DictCursor))
        module.app.dependency_overrides[module.get_current_user] = lambda: {"user_id": 1, "role": "admin"}
        with TestClient(module.app) as client:
            yield client, module
    finally:
        conn.close()
        with admin.cursor() as cur:
            cur.execute(f"DROP DATABASE `{name}`")
        admin.close()


def test_stock_history_and_analysis(api):
    client, _ = api
    assert client.get("/stocks").json()[0]["quantity"] == 39
    old = client.get("/stocks/transactions")
    assert old.status_code == 200
    assert len(old.json()) == 7
    assert all(row["before_quantity"] is None for row in old.json())
    for kind, qty, before, after in [("IN", 5, 39, 44), ("OUT", 2, 44, 42)]:
        response = client.post("/stocks/transactions", json={
            "product_id": 1, "change_type": kind, "quantity": qty,
            "reason_code": "SALE" if kind == "OUT" else "PURCHASE", "note": "integration test"})
        assert response.status_code == 200, response.text
        assert response.json()["before_quantity"] == before
        assert response.json()["after_quantity"] == after
    assert client.get("/stocks").json()[0]["quantity"] == 42
    rows = client.get("/stocks/transactions").json()
    new = [row for row in rows if row["note"] == "integration test"]
    assert len(new) == 2
    assert {(r["before_quantity"], r["after_quantity"]) for r in new} == {(39, 44), (44, 42)}
    analysis = client.get("/ai/analysis/stock_trend?days=1&reason=SALE")
    assert analysis.status_code == 200, analysis.text
    assert analysis.json()[0]["total_out_in_window"] == 2


def test_invalid_transactions_preserve_data(api):
    client, _ = api
    for kind, qty in [("OUT", 1000), ("IN", 0), ("INVALID", 1)]:
        assert client.post("/stocks/transactions", json={
            "product_id": 1, "change_type": kind, "quantity": qty}).status_code == 400
    assert client.get("/stocks").json()[0]["quantity"] == 39
    assert len(client.get("/stocks/transactions").json()) == 7


def test_user_permissions_and_fields(api):
    client, module = api
    response = client.get("/users")
    assert response.status_code == 200
    assert len(response.json()) == 2
    assert "role" in response.json()[0]
    module.app.dependency_overrides[module.get_current_user] = lambda: {"user_id": 3, "role": "staff"}
    assert client.get("/users").status_code == 403
