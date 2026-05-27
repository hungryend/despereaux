from __future__ import annotations

import uuid_utils
from sqlalchemy.orm import DeclarativeBase


def new_id() -> str:
    return str(uuid_utils.uuid7())


class Base(DeclarativeBase):
    pass
