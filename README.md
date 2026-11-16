# Movie Data API

This FastAPI application accepts CSV uploads containing movie titles, release years, ratings, and genres. The `main.py` module exposes health, upload, retrieval, search, and processing endpoints, while `src/models.py` defines their request and response models. `src/database.py` creates the SQLite database and movie table, and `src/storage.py` opens a connection for each operation and uses parameterized SQL queries. Saving a collection uses one transaction, so a failed insert rolls back the delete and keeps the previous collection. The `data_pipeline` modules validate numeric fields and apply duplicate, missing-value, and outlier handling. `src/cache.py` uses `fakeredis` and JSON to cache `GET /movies` responses for 90 seconds, with a different key for each limit. Successful uploads and processing invalidate cached retrievals so the next request reads the updated SQLite data. The request middleware logs each request and adds a request ID to its response.

Start the API and run the test suite from this directory:

```bash
python -m uvicorn main:app --reload
python -m pytest tests -v
```

Example `POST /movies/process` request:

```json
{
  "validation": {
    "required_columns": ["title", "release_year", "rating", "genre"],
    "numeric_columns": ["release_year", "rating"]
  },
  "processing": {
    "remove_duplicates": true,
    "missing": {"enabled": true, "axis": "rows"},
    "outliers": {
      "enabled": true,
      "columns": ["rating"],
      "method": "iqr",
      "threshold": 1.5
    }
  }
}
```

For the provided 20-row sample, the expected response is `validation_rows_removed: 0` with a cleaning report showing 20 rows before processing, 16 afterward, and 4 removed.
