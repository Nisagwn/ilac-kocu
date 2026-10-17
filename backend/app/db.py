from collections.abc import Generator, Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


engine = create_engine(get_settings().database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def middleware_session(app) -> Iterator[Session]:
    """Middleware'ler için oturum. Rotalar gibi `get_db` bağımlılık geçersiz kılmasına (testler) uyar:
    geçersiz kılma hazır bir Session döndürüyorsa o kullanılır ve kapatılmaz."""
    provider = getattr(app, "dependency_overrides", {}).get(get_db, get_db)
    made = provider()
    if isinstance(made, Session):
        yield made
        return
    db = next(made)
    try:
        yield db
    finally:
        made.close()
