from conftest import ADMIN


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["database"] == "up"


def test_seed_data(client):
    books = client.get("/api/books").json()
    assert len(books) >= 5


def test_create_get_delete_book(client):
    r = client.post("/api/books", json={"title": "Respaldos 101", "author": "Equipo BD2", "year": 2026})
    assert r.status_code == 201
    book_id = r.json()["id"]
    assert client.get(f"/api/books/{book_id}").json()["title"] == "Respaldos 101"
    assert client.delete(f"/api/books/{book_id}").status_code == 401  # requiere PIN
    assert client.delete(f"/api/books/{book_id}", headers=ADMIN).status_code == 204
    assert client.get(f"/api/books/{book_id}").status_code == 404


def test_rejects_invalid_book(client):
    assert client.post("/api/books", json={"title": "", "author": "x"}).status_code == 422


def test_dashboard_renders(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "PG Backup Guardian" in r.text
