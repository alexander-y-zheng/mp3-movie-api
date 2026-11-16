import pytest

import main
from src import MovieStorage


def test_movies_persist(client, test_storage, uploaded_movies):
    persisted_storage = MovieStorage(test_storage.db_path)
    movies = persisted_storage.get()
    response = client.get("/movies", params={"limit": 100})

    assert response.status_code == 200
    body = response.json()
    assert len(movies) == 20
    assert list(movies.columns) == ["title", "release_year", "rating", "genre"]
    assert movies["title"].tolist() == [
        "Movie A", "Movie B", "Movie B", "Movie C", "Movie D",
        "Movie D", "Movie E", "Movie F", "Movie G", "Movie H",
        "Movie I", "Movie J", "Movie K", "Movie L", "Movie M",
        "Movie N", "Movie O", "Movie P", "Movie Q", "Movie R",
    ]
    assert persisted_storage.count_movies() == 20
    assert [row["id"] for row in persisted_storage.select_columns(["title"], 100)] == list(
        range(1, 21)
    )
    assert [row["id"] for row in body["data"]] == list(range(1, 21))


def test_cache_and_processing(client, uploaded_movies, processing_body):
    response_100 = client.get("/movies", params={"limit": 100})
    response_1 = client.get("/movies", params={"limit": 1})

    assert response_100.status_code == 200
    assert response_1.status_code == 200
    assert main.cache.get(100) == response_100.json()
    assert main.cache.get(1) == response_1.json()

    repeated_response = client.get("/movies", params={"limit": 100})
    assert repeated_response.json() == response_100.json()

    process_response = client.post("/movies/process", json=processing_body)
    assert process_response.status_code == 200
    assert process_response.json() == {
        "validation_rows_removed": 0,
        "cleaning_report": {
            "rows_before": 20,
            "rows_after": 16,
            "rows_removed": 4,
            "columns_before": 4,
            "columns_after": 4,
            "columns_removed": 0,
        },
    }
    assert main.cache.get(100) is None
    assert main.cache.get(1) is None

    processed_response = client.get("/movies", params={"limit": 100})
    body = processed_response.json()
    assert processed_response.status_code == 200
    assert body["total_rows"] == 16
    assert [movie["title"] for movie in body["data"]] == [
        f"Movie {letter}" for letter in "ABCDEFGHIJKLMNOP"
    ]
    assert [movie["id"] for movie in body["data"]] == list(range(1, 17))
    assert main.cache.get(100) == body


def test_cache_hit_skips_database(client, uploaded_movies, monkeypatch):
    first_response = client.get("/movies", params={"limit": 100})
    assert first_response.status_code == 200
    first_body = first_response.json()

    def fail_if_called(*args, **kwargs):
        pytest.fail("A cache hit should not query SQLite.")

    monkeypatch.setattr(main.storage, "select_columns", fail_if_called)
    monkeypatch.setattr(main.storage, "count_movies", fail_if_called)

    cached_response = client.get("/movies", params={"limit": 100})
    assert cached_response.status_code == 200
    assert cached_response.json() == first_body


def test_movie_search_uses_sql_filters_and_sorts(client, uploaded_movies):
    response = client.get(
        "/movies/search",
        params={"min_rating": 8.5, "limit": 2},
    )

    assert response.status_code == 200
    assert response.json() == [
        {"id": 4, "title": "Movie C"},
        {"id": 2, "title": "Movie B"},
    ]

    response = client.get(
        "/movies/search",
        params=[
            ("columns", "title"),
            ("columns", "rating"),
            ("min_rating", "8.5"),
            ("limit", "2"),
        ],
    )
    assert response.status_code == 200
    assert response.json() == [
        {"id": 4, "title": "Movie C", "rating": 9.0},
        {"id": 2, "title": "Movie B", "rating": 8.5},
    ]

    response = client.get("/movies/search", params={"columns": "unknown"})
    assert response.status_code == 400


def test_upload_replaces_rows_and_invalidates_cache(client, uploaded_movies):
    cached_response = client.get("/movies", params={"limit": 100})
    assert cached_response.status_code == 200
    assert main.cache.get(100) is not None

    replacement_csv = b"title,release_year,rating,genre\nReplacement,2025,8.1,\n"
    response = client.post(
        "/upload",
        files={"file": ("replacement.csv", replacement_csv, "text/csv")},
    )

    assert response.status_code == 200
    assert main.cache.get(100) is None

    response = client.get("/movies", params={"limit": 100})
    assert response.status_code == 200
    assert response.json() == {
        "data": [
            {
                "id": 1,
                "title": "Replacement",
                "release_year": 2025,
                "rating": 8.1,
                "genre": None,
            }
        ],
        "total_rows": 1,
    }
