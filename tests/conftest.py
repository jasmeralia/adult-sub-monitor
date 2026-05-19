from pathlib import Path
from tempfile import NamedTemporaryFile
from unittest.mock import AsyncMock

import pytest

from adult_sub_monitor.db import Database


@pytest.fixture
def tmp_db_path() -> Path:
    with NamedTemporaryFile(delete=False) as tmp_file:
        path = Path(tmp_file.name)

    try:
        yield path
    finally:
        path.unlink(missing_ok=True)
        path.with_name(f"{path.name}-shm").unlink(missing_ok=True)
        path.with_name(f"{path.name}-wal").unlink(missing_ok=True)


@pytest.fixture
def db() -> Database:
    return Database(Path(":memory:"))


@pytest.fixture
def mock_page() -> AsyncMock:
    methods = [
        "goto",
        "fill",
        "click",
        "wait_for_selector",
        "wait_for_load_state",
        "wait_for_navigation",
        "inner_text",
        "get_attribute",
        "locator",
        "evaluate",
        "count",
    ]
    locator_methods = [*methods, "first", "nth"]

    locator = AsyncMock()
    for method in locator_methods:
        setattr(locator, method, AsyncMock())

    page = AsyncMock()
    for method in methods:
        setattr(page, method, AsyncMock())
    page.locator.return_value = locator

    return page
