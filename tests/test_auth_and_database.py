from app.auth import hash_password, verify_password
from app.database import db_session, rows, utcnow


def test_password_hashing():
    hashed = hash_password("correct horse battery staple")
    assert "correct horse" not in hashed
    assert verify_password("correct horse battery staple", hashed)
    assert not verify_password("wrong", hashed)


def test_roles_requests_and_transactions_persist(provider):
    with db_session() as db:
        db.execute("INSERT INTO users(username,display_name,password_hash,role,created_at) VALUES(?,?,?,?,?)", ("worker", "Worker", "hash", "standard", utcnow()))
        user_id = db.execute("SELECT id FROM users WHERE username='worker'").fetchone()["id"]
        db.execute("INSERT INTO item_requests(item_requested,manufacturer_model,quantity,notes,requested_by,requested_by_name,status,created_at,updated_at) VALUES(?,?,?,?,?,?, 'new',?,?)", ("Cable", "", 2, "Meeting room", user_id, "Worker", utcnow(), utcnow()))
    request = rows("SELECT * FROM item_requests WHERE requested_by=?", (user_id,))[0]
    assert request["quantity"] == 2
    assert request["status"] == "new"

