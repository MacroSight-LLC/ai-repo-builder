from __future__ import annotations

import datetime
import logging
import os
import sqlite3
import threading

from cuga.backend.memory.agentic_memory.schema import Namespace, Run
from cuga.backend.memory.agentic_memory.utils.exceptions import (
    NamespaceAlreadyExistsException,
    RunAlreadyExistsException,
)
from cuga.config import DBS_DIR

logger = logging.getLogger(__name__)


def adapt_datetime_epoch(time: datetime.datetime) -> int:
    """Adapt datetime.datetime to Unix timestamp."""
    return int(time.timestamp())


def convert_timestamp(time: bytes) -> datetime.datetime:
    """Convert Unix epoch timestamp to datetime.datetime object."""
    return datetime.datetime.fromtimestamp(int.from_bytes(time), datetime.UTC)


sqlite3.register_adapter(datetime.datetime, adapt_datetime_epoch)
sqlite3.register_converter("timestamp", convert_timestamp)


class SQLiteManager:
    """A database for any resources that can't be generalized across backends."""

    def __init__(self, db_path: str = os.path.join(DBS_DIR, "agentic.db")):
        self.db_path = db_path

    def _create_namespace_table(self):
        with self._lock:
            try:
                self.connection.execute("BEGIN")
                self.connection.execute("""
                    CREATE TABLE IF NOT EXISTS namespaces (
                        id           TEXT PRIMARY KEY,
                        created_at   TIMESTAMP NOT NULL,
                        user_id      TEXT,
                        agent_id     TEXT,
                        app_id       TEXT
                    )
                """)
                self.connection.execute("COMMIT")
            except Exception as e:
                self.connection.execute("ROLLBACK")
                logger.error(f"Failed to create namespaces table: {e}")
                raise

    def _create_run_table(self):
        with self._lock:
            try:
                self.connection.execute("BEGIN")
                self.connection.execute("""
                    CREATE TABLE IF NOT EXISTS runs (
                        namespace_id TEXT NOT NULL,
                        id           TEXT NOT NULL,
                        created_at   TIMESTAMP NOT NULL,
                        ended        BOOLEAN NOT NULL,

                        PRIMARY KEY (namespace_id, id),
                        FOREIGN KEY (namespace_id) REFERENCES namespaces (id)
                    )
                """)
                self.connection.execute("COMMIT")
            except Exception as e:
                self.connection.execute("ROLLBACK")
                logger.error(f"Failed to create runs table: {e}")
                raise

    def create_namespace(
        self,
        namespace_id: str,
        user_id: str | None = None,
        agent_id: str | None = None,
        app_id: str | None = None,
    ) -> Namespace:
        created_at = datetime.datetime.now(datetime.UTC)
        with self._lock:
            try:
                self.connection.execute("BEGIN")
                self.connection.execute(
                    """
                    INSERT INTO namespaces (
                        id, created_at, user_id, agent_id, app_id
                    )
                    VALUES (?, ?, ?, ?, ?)
                """,
                    (
                        namespace_id,
                        created_at,
                        user_id,
                        agent_id,
                        app_id,
                    ),
                )
                self.connection.execute("COMMIT")
            except sqlite3.IntegrityError as e:
                self.connection.execute("ROLLBACK")
                raise NamespaceAlreadyExistsException(
                    f'Namespace "{namespace_id}" already exists.'
                ) from e
            except Exception as e:
                self.connection.execute("ROLLBACK")
                logger.error(f"Failed to create namespace: {e}")
                raise
        return Namespace(
            id=namespace_id,
            created_at=created_at,
            user_id=user_id,
            agent_id=agent_id,
            app_id=app_id,
        )

    def create_run(
        self,
        namespace_id: str,
        run_id: str,
    ) -> Run:
        created_at = datetime.datetime.now(datetime.UTC)
        with self._lock:
            try:
                self.connection.execute("BEGIN")
                self.connection.execute(
                    """
                    INSERT INTO runs (
                        namespace_id, id, created_at, ended
                    )
                    VALUES (?, ?, ?, ?)
                """,
                    (namespace_id, run_id, created_at, False),
                )
                self.connection.execute("COMMIT")
            except sqlite3.IntegrityError as e:
                self.connection.execute("ROLLBACK")
                raise RunAlreadyExistsException(f'Run "{run_id}" already exists.') from e
            except Exception as e:
                self.connection.execute("ROLLBACK")
                logger.error(f"Failed to create run: {e}")
                raise
        return Run(id=run_id, created_at=created_at, steps=[], ended=False)

    def get_namespace(self, namespace_id: str) -> Namespace | None:
        with self._lock:
            cursor: sqlite3.Cursor = self.connection.cursor()
            cursor.row_factory = Namespace.row_factory
            cursor.execute(
                """
                    SELECT id, created_at, user_id, agent_id, app_id
                    FROM namespaces
                    WHERE id = ?
                """,
                (namespace_id,),
            )
            return cursor.fetchone()

    def get_run(self, namespace_id: str, run_id: str) -> Run | None:
        with self._lock:
            cursor: sqlite3.Cursor = self.connection.cursor()
            cursor.row_factory = Run.row_factory
            cursor.execute(
                """
                    SELECT namespace_id, id, created_at, ended
                    FROM runs
                    WHERE namespace_id = ? AND id = ?
                """,
                (
                    namespace_id,
                    run_id,
                ),
            )
            return cursor.fetchone()

    def list_runs(self, namespace_id: str, limit: int = 10) -> list[Run]:
        params = [namespace_id, limit]
        with self._lock:
            cursor: sqlite3.Cursor = self.connection.cursor()
            cursor.row_factory = Run.row_factory
            cursor.execute(
                """
                    SELECT namespace_id, id, created_at, ended
                    FROM runs
                    WHERE namespace_id = ?
                    ORDER BY created_at DESC
                    LIMIT ?
                """,
                params,
            )
            return cursor.fetchall()

    def search_namespaces(
        self,
        user_id: str | None = None,
        agent_id: str | None = None,
        app_id: str | None = None,
        limit: int = 10,
    ) -> list[Namespace]:
        query = {
            k: v
            for k, v in {"user_id": user_id, "agent_id": agent_id, "app_id": app_id}.items()
            if v is not None
        }
        sql = " AND ".join([f"{k} = ?" for k in query])
        params = list(query.values()) + [limit]
        with self._lock:
            cursor: sqlite3.Cursor = self.connection.cursor()
            cursor.row_factory = Namespace.row_factory
            if sql:
                cursor.execute(
                    f"""
                        SELECT id, created_at, user_id, agent_id, app_id
                        FROM namespaces
                        WHERE {sql}
                        LIMIT ?
                    """,
                    params,
                )
            else:
                cursor.execute(
                    """
                        SELECT id, created_at, user_id, agent_id, app_id
                        FROM namespaces
                        LIMIT ?
                    """,
                    [limit],
                )
            return cursor.fetchall()

    def end_run(self, namespace_id: str, run_id: str):
        with self._lock:
            try:
                self.connection.execute("BEGIN")
                self.connection.execute(
                    "UPDATE runs SET ended = ? WHERE namespace_id = ? AND id = ?",
                    (
                        True,
                        namespace_id,
                        run_id,
                    ),
                )
                self.connection.execute("COMMIT")
            except Exception as e:
                self.connection.execute("ROLLBACK")
                logger.error(f"Failed to end run {run_id}: {e}")
                raise

    def delete_namespace(self, namespace_id: str):
        with self._lock:
            try:
                self.connection.execute("BEGIN")
                self.connection.execute("DELETE FROM namespaces WHERE id = ?", (namespace_id,))
                self.connection.execute("COMMIT")
            except Exception as e:
                self.connection.execute("ROLLBACK")
                logger.error(f"Failed to delete namespace {namespace_id}: {e}")
                raise

    def delete_run(self, namespace_id: str, run_id: str):
        with self._lock:
            try:
                self.connection.execute("BEGIN")
                self.connection.execute(
                    "DELETE FROM runs WHERE namespace_id = ? AND id = ?",
                    (
                        namespace_id,
                        run_id,
                    ),
                )
                self.connection.execute("COMMIT")
            except Exception as e:
                self.connection.execute("ROLLBACK")
                logger.error(f"Failed to delete run {run_id}: {e}")
                raise

    def reset(self) -> None:
        """Drop and recreate every table."""
        with self._lock:
            try:
                self.connection.execute("BEGIN")
                self.connection.execute("DROP TABLE IF EXISTS namespaces")
                self.connection.execute("DROP TABLE IF EXISTS runs")
                self.connection.execute("COMMIT")
                self._create_namespace_table()
            except Exception as e:
                self.connection.execute("ROLLBACK")
                logger.error(f"Failed to reset tables: {e}")
                raise

    def close(self) -> None:
        if self.connection:
            self.connection.close()
            self.connection = None

    def __enter__(self) -> SQLiteManager:
        # Ensure parent directory exists
        try:
            os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        except Exception:
            pass
        self.connection: sqlite3.Connection = sqlite3.connect(self.db_path, check_same_thread=False)
        self._lock = threading.Lock()
        self._create_namespace_table()
        self._create_run_table()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def __del__(self):
        self.close()
