import asyncio
import structlog
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from starlette.responses import JSONResponse

from app.config import get_settings
from app.database import init_db, async_session

structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.dev.ConsoleRenderer() if get_settings().is_dev else structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
)

logger = structlog.get_logger()

# Global services container
_services: dict = {}


def get_app_services() -> dict:
    return _services


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logger.info("app.starting", env=settings.ENVIRONMENT)

    # Initialize database
    await init_db()

    # Create admin user
    from app.api.auth import ensure_admin_exists
    async with async_session() as session:
        await ensure_admin_exists(session)

    # Initialize services
    from app.services.waha import WhatsAppClient
    from app.services.groq_llm import GroqLLMClient
    from app.services.groq_whisper import GroqWhisperClient
    from app.services.humanizer import Humanizer
    from app.services.funnel import FunnelEngine
    from app.services.tabulation import TabulationService

    waha = WhatsAppClient(settings)
    groq_llm = GroqLLMClient(settings)
    groq_whisper = GroqWhisperClient(settings)
    humanizer = Humanizer(settings, waha)
    funnel = FunnelEngine(settings, waha, groq_llm, groq_whisper, humanizer)
    tabulation = TabulationService(settings, groq_llm)

    _services.update({
        "waha": waha,
        "groq_llm": groq_llm,
        "groq_whisper": groq_whisper,
        "humanizer": humanizer,
        "funnel": funnel,
        "tabulation": tabulation,
    })

    # Sync: delete orphaned WAHA sessions not in ZapFlow DB
    await _cleanup_orphaned_sessions(waha)

    # Start background tasks
    tab_task = asyncio.create_task(tabulation.run_periodic())
    sync_task = asyncio.create_task(_periodic_status_sync())

    logger.info("app.started", url=settings.APP_BASE_URL)

    yield

    # Shutdown
    tabulation.stop()
    tab_task.cancel()
    sync_task.cancel()
    logger.info("app.stopped")


# Rate limiter
limiter = Limiter(key_func=get_remote_address)


async def _cleanup_orphaned_sessions(waha):
    """Delete WAHA sessions that don't exist in ZapFlow DB.
    Retries connection to WAHA since it may start after the app."""
    for attempt in range(10):
        try:
            waha_sessions = await waha.list_sessions()
            break
        except Exception:
            if attempt < 9:
                await asyncio.sleep(3)
            else:
                logger.warning("startup.sync_skipped", reason="WAHA not reachable after retries")
                return

    try:
        async with async_session() as session:
            from sqlmodel import select
            from app.models.instance import Instance
            result = await session.execute(select(Instance))
            db_instances = result.scalars().all()
            db_names = {inst.name for inst in db_instances}

        # 1. Update statuses of existing instances based on WAHA state
        waha_by_name = {ws.get("name"): ws for ws in waha_sessions if ws.get("name")}
        
        status_map = {
            "WORKING": "connected",
            "SCAN_QR_CODE": "connecting",
            "STARTING": "connecting",
            "STOPPED": "disconnected",
            "FAILED": "disconnected",
        }

        async with async_session() as session:
            for inst in db_instances:
                waha_info = waha_by_name.get(inst.name)
                if not waha_info:
                    new_status = "disconnected"
                else:
                    waha_status = waha_info.get("status", "")
                    new_status = status_map.get(waha_status, "disconnected")
                
                if inst.status != new_status:
                    logger.info("startup.status_sync", name=inst.name, old=inst.status, new=new_status)
                    inst.status = new_status
                    # If it's connected but we don't have the phone, try to get it
                    if new_status == "connected" and not inst.phone_number:
                        me = waha_info.get("me") or {}
                        me_id = me.get("id", "")
                        if me_id:
                            inst.phone_number = me_id.split("@")[0]
            await session.commit()

        # 2. Delete orphaned WAHA sessions not in ZapFlow DB
        for ws in waha_sessions:
            ws_name = ws.get("name", "")
            if ws_name and ws_name not in db_names:
                try:
                    await waha.delete_session(ws_name)
                    logger.info("startup.orphan_deleted", name=ws_name)
                except Exception as e:
                    logger.warning("startup.orphan_delete_failed", name=ws_name, error=str(e))
    except Exception as e:
        logger.warning("startup.sync_failed", error=str(e))


async def _periodic_status_sync():
    """Periodically sync instance status with WAHA."""
    from app.main import get_app_services
    while True:
        try:
            await asyncio.sleep(60) # Sync every 60 seconds
            services = get_app_services()
            if "waha" in services:
                await _cleanup_orphaned_sessions(services["waha"])
        except Exception as e:
            logger.error("periodic_sync.error", error=str(e))



app = FastAPI(
    title="ZapFlow",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs" if get_settings().is_dev else None,
    redoc_url=None,
)

app.state.limiter = limiter


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(status_code=429, content={"detail": "Muitas requisições. Aguarde."})


# CORS
settings = get_settings()
origins = ["*"] if settings.is_dev else [settings.APP_BASE_URL]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Security headers middleware
@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if not get_settings().is_dev:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://cdn.tailwindcss.com https://unpkg.com https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; "
            "connect-src 'self'; "
            "font-src 'self'; "
            "frame-ancestors 'none'"
        )
    return response


# Health check
@app.get("/health")
async def health():
    return {"status": "ok", "version": "0.1.0"}


# Mount static files
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Register API routers
from app.api.auth import router as auth_router
from app.api.webhooks import router as webhooks_router
from app.api.instances import router as instances_router
from app.api.campaigns import router as campaigns_router
from app.api.leads import router as leads_router
from app.api.dashboard import router as dashboard_router

app.include_router(auth_router)
app.include_router(webhooks_router)
app.include_router(instances_router)
app.include_router(campaigns_router)
app.include_router(leads_router)
app.include_router(dashboard_router)

# Register web (HTML) router
from app.web.pages import router as web_router
app.include_router(web_router)
