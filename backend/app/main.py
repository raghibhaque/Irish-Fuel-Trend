from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.db import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="Irish Fuel Trend",
    description="Petrol/diesel price trend predictor for Ireland.",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "service": "irish-fuel-trend", "version": "0.1.0"}


@app.get("/")
def root() -> dict:
    return {"message": "Irish Fuel Trend API. See /docs for endpoints."}
