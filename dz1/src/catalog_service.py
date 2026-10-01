from fastapi import FastAPI


app = FastAPI(title="Catalog Service")


@app.get("/health")
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "service": "catalog-service",
    }

