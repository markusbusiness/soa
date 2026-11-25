import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from producer.config import load_settings
from producer.generator import EventGenerator
from producer.models import MovieEventIn, PublishedEvent
from producer.publisher import EventValidationError, KafkaPublisher, PublishError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def error(status_code: int, code: str, details) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": code, "details": details})


def create_app() -> FastAPI:
    settings = load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        publisher = await asyncio.to_thread(KafkaPublisher, settings.publisher)
        generator = EventGenerator(settings.generator, publisher)
        app.state.publisher = publisher
        app.state.generator = generator
        if settings.generator.enabled:
            generator.start()
        yield
        await generator.stop()
        await asyncio.to_thread(publisher.close)

    app = FastAPI(title="Movie Events Producer", lifespan=lifespan)

    @app.exception_handler(RequestValidationError)
    async def on_validation_error(_: Request, exc: RequestValidationError):
        details = [{"loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors()]
        return error(status.HTTP_422_UNPROCESSABLE_CONTENT, "VALIDATION_ERROR", details)

    @app.post("/events", status_code=status.HTTP_202_ACCEPTED, response_model=PublishedEvent)
    async def publish_event(event: MovieEventIn, request: Request):
        publisher: KafkaPublisher = request.app.state.publisher
        try:
            delivery = await publisher.publish(event.to_record())
        except EventValidationError as exc:
            return error(status.HTTP_422_UNPROCESSABLE_CONTENT, "SCHEMA_VALIDATION_ERROR", str(exc))
        except PublishError as exc:
            return error(status.HTTP_503_SERVICE_UNAVAILABLE, "PUBLISH_FAILED", str(exc))
        return PublishedEvent(event_id=delivery.event_id, partition=delivery.partition, offset=delivery.offset)

    @app.get("/generator")
    async def generator_status(request: Request):
        generator: EventGenerator = request.app.state.generator
        state = generator.state
        return {"running": generator.running, "published": generator.published, "failed": generator.failed,
                "launch": state.launch if state else None,
                "published_until": state.published_until if state else None}

    @app.post("/generator/start")
    async def generator_start(request: Request):
        request.app.state.generator.start()
        return await generator_status(request)

    @app.post("/generator/stop")
    async def generator_stop(request: Request):
        await request.app.state.generator.stop()
        return await generator_status(request)

    @app.get("/health")
    async def health(request: Request):
        publisher: KafkaPublisher = request.app.state.publisher
        if not await asyncio.to_thread(publisher.is_ready):
            return error(status.HTTP_503_SERVICE_UNAVAILABLE, "KAFKA_UNAVAILABLE", "topic metadata unavailable")
        return {"status": "ok", "schema_id": publisher.schema_id, "schema_version": publisher.schema_version}

    return app
