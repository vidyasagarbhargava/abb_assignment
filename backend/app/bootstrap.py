"""Composition root: the only place concrete adapters are wired together."""

from dataclasses import dataclass

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings, get_settings
from app.db import make_engine, make_session_factory
from app.runtime.base import RuntimeRegistry
from app.runtime.simulated import SimulatedRuntime


@dataclass
class Container:
    settings: Settings
    engine: Engine
    session_factory: sessionmaker[Session]
    runtimes: RuntimeRegistry


def build_container(settings: Settings | None = None, runtimes: RuntimeRegistry | None = None) -> Container:
    settings = settings or get_settings()
    engine = make_engine(settings.database_url)
    return Container(
        settings=settings,
        engine=engine,
        session_factory=make_session_factory(engine),
        runtimes=runtimes or RuntimeRegistry([SimulatedRuntime()]),
    )
