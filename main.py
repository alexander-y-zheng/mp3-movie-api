import io
import logging
from pathlib import Path

import pandas as pd
from fastapi import FastAPI, File, HTTPException, Query, UploadFile

from data_pipeline import create_cleaning_report, process_data, validate_dataframe
from src import (
    HealthResponse,
    MovieCache,
    MovieDataResponse,
    MovieProcessingReport,
    MovieStorage,
    MovieUploadInfo,
    ProcessConfig,
    log_requests,
    setup_logging,
)
from src.storage import MOVIE_COLUMNS

setup_logging(verbose=True)
logger = logging.getLogger(__name__)

app = FastAPI(title="Movie Data API", version="1.0.0")
app.middleware("http")(log_requests)
storage = MovieStorage()
cache = MovieCache()


@app.get("/health", response_model=HealthResponse)
def health_check():
    """Return the application status and version."""
    return HealthResponse(status="ok", version="1.0.0")


@app.post("/upload", response_model=MovieUploadInfo)
async def upload_movies(file: UploadFile = File(...)):
    """Validate and store a CSV movie collection."""
    filename = file.filename or ""
    if Path(filename).suffix.lower() != ".csv":
        extension = Path(filename).suffix.lower()
        logger.warning("Unsupported file format: %s", extension)
        raise HTTPException(status_code=400, detail="Only CSV files are supported.")

    contents = await file.read()
    try:
        df = pd.read_csv(io.BytesIO(contents))
    except (pd.errors.EmptyDataError, pd.errors.ParserError, UnicodeDecodeError) as error:
        logger.warning("Failed to read CSV file %s: %s", filename, error)
        raise HTTPException(
            status_code=400,
            detail=f"Failed to read CSV file: {error}",
        )

    if len(df.columns) != len(MOVIE_COLUMNS) or set(df.columns) != set(MOVIE_COLUMNS):
        logger.warning("Invalid movie columns in upload: %s", list(df.columns))
        raise HTTPException(
            status_code=400,
            detail=f"CSV must contain exactly these columns: {', '.join(MOVIE_COLUMNS)}",
        )
    if df.empty:
        logger.warning("Movie upload contains no data rows: %s", filename)
        raise HTTPException(status_code=400, detail="CSV must contain at least one data row.")

    df = df[MOVIE_COLUMNS]
    storage.save(df)
    cache.invalidate()
    logger.info("Uploaded movies: filename=%s, rows=%s", filename, len(df))

    return MovieUploadInfo(
        filename=filename,
        rows=len(df),
        columns=len(df.columns),
        column_names=list(df.columns),
    )


@app.get("/movies", response_model=MovieDataResponse)
def get_movies(limit: int = Query(default=100, ge=1)):
    """Return movie rows, using the cache when available."""
    cached_response = cache.get(limit)
    if cached_response is not None:
        logger.info("Retrieved movies from cache: returned_rows=%s", len(cached_response["data"]))
        return cached_response

    response = {
        "data": storage.select_columns(MOVIE_COLUMNS, limit),
        "total_rows": storage.count_movies(),
    }
    cache.set(limit, response)
    logger.info("Retrieved movies: returned_rows=%s", len(response["data"]))
    return response


@app.get("/movies/search", response_model=list[dict])
def query_movies(
    columns: list[str] = Query(default=["title"]),
    min_rating: float = Query(default=0, ge=0, le=10),
    limit: int = Query(default=100, ge=1),
):
    """Search movies by minimum rating and selected columns."""
    try:
        results = storage.query_movies(columns, min_rating, limit)
    except ValueError as error:
        logger.warning("Invalid movie search: %s", error)
        raise HTTPException(status_code=400, detail=str(error))

    logger.info(
        "Queried movies: columns=%s, min_rating=%s, returned_rows=%s",
        columns,
        min_rating,
        len(results),
    )
    return results


@app.post("/movies/process", response_model=MovieProcessingReport)
def process_movies(config: ProcessConfig):
    """Validate and process the stored movie collection."""
    df = storage.get()
    if df.empty:
        logger.warning("Cannot process an empty movie collection")
        raise HTTPException(status_code=400, detail="No movies are available to process.")

    config_data = config.model_dump()
    numeric_columns = config_data["validation"]["numeric_columns"]
    missing_numeric_columns = [
        column for column in numeric_columns if column not in df.columns
    ]
    if missing_numeric_columns:
        missing = ", ".join(missing_numeric_columns)
        logger.warning("Movie collection is missing configured numeric columns: %s", missing)
        raise HTTPException(
            status_code=400,
            detail=f"Missing numeric columns: {missing}",
        )

    try:
        validated_df = validate_dataframe(
            df,
            required_columns=config_data["validation"]["required_columns"],
            numeric_columns=numeric_columns,
        )
        validation_rows_removed = len(df) - len(validated_df)
        validated_copy = validated_df.copy()
        processed_df = process_data(validated_df, config_data)
        cleaning_report = create_cleaning_report(validated_copy, processed_df)
    except ValueError as error:
        logger.warning("Failed to process movies: %s", error)
        raise HTTPException(status_code=400, detail=str(error))

    storage.save(processed_df)
    cache.invalidate()
    logger.info(
        "Processed movies: validation_removed=%s, rows_before=%s, rows_after=%s",
        validation_rows_removed,
        cleaning_report["rows_before"],
        cleaning_report["rows_after"],
    )
    return {
        "validation_rows_removed": validation_rows_removed,
        "cleaning_report": cleaning_report,
    }
