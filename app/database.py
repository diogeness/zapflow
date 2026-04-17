from sqlmodel import SQLModel, create_engine, text
from sqlalchemy import event
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from app.config import get_settings
import structlog

settings = get_settings()
logger = structlog.get_logger()

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.is_dev,
    connect_args={"check_same_thread": False} if "sqlite" in settings.DATABASE_URL else {},
)

@event.listens_for(engine.sync_engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    if "sqlite" in settings.DATABASE_URL:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()

async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_session():
    async with async_session() as session:
        yield session


async def _run_migrations(conn):
    """Run lightweight schema migrations for SQLite (ALTER TABLE ADD COLUMN)."""
    migrations = [
        ("campaigns", "context_file", "TEXT"),
        ("campaigns", "ignore_groups", "BOOLEAN DEFAULT 1"),
        ("campaigns", "ignore_contacts", "BOOLEAN DEFAULT 0"),
        ("campaigns", "ai_enabled", "BOOLEAN DEFAULT 1"),
        ("campaigns", "max_ai_interactions", "INTEGER DEFAULT 0"),
        ("instances", "saved_contacts", "TEXT"),
        ("leads", "ai_interactions_count", "INTEGER DEFAULT 0"),
    ]
    for table, column, col_type in migrations:
        try:
            await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"))
            logger.info("db.migration_applied", table=table, column=column)
        except Exception:
            pass  # Column already exists

    # Index on evolution_msg_id for dedup
    try:
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_messages_evolution_msg_id ON messages(evolution_msg_id)"
        ))
    except Exception:
        pass

    # Migrate media_url → media_urls on campaign_steps
    try:
        await conn.execute(text("ALTER TABLE campaign_steps ADD COLUMN media_urls TEXT"))
        logger.info("db.migration_applied", table="campaign_steps", column="media_urls")
    except Exception:
        pass  # Column already exists

    # Copy data from old media_url column to new media_urls (as JSON array)
    try:
        import json
        result = await conn.execute(text(
            "SELECT id, media_url FROM campaign_steps WHERE media_url IS NOT NULL AND (media_urls IS NULL OR media_urls = '')"
        ))
        rows = result.fetchall()
        for row in rows:
            step_id, old_url = row[0], row[1]
            if old_url:
                import os
                name = os.path.basename(old_url)
                new_val = json.dumps([{"path": old_url, "name": name}])
                await conn.execute(
                    text("UPDATE campaign_steps SET media_urls = :val WHERE id = :sid"),
                    {"val": new_val, "sid": step_id}
                )
        if rows:
            logger.info("db.media_url_migrated", count=len(rows))
    except Exception as e:
        logger.warning("db.media_url_migration_error", error=str(e))


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
        await _run_migrations(conn)
