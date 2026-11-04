"""Questions by disposition, and changing a question's disposition, which retires its binding."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from personal_os_backend.config import StoreBase
from personal_os_backend.jobs_changes import record_change
from personal_os_backend.jobs_patterns import (
    LIVE_STATUSES,
    PATTERN_COLUMNS,
    PATTERN_FROM,
    Pattern,
    _record_pattern,
)
from personal_os_backend.jobs_questions import (
    QUESTION_COLUMNS,
    Disposition,
    Question,
    QuestionUpdate,
)


class QuestionInUse(Exception):
    """A live pattern still answers the question, so deleting it would drop the pattern."""


class QuestionsStore(Protocol):
    async def list_questions(
        self, user_id: UUID, disposition: Disposition | None
    ) -> list[Question]: ...

    async def update_question(
        self, user_id: UUID, question_id: UUID, item: QuestionUpdate
    ) -> Question | None: ...

    async def delete_question(self, user_id: UUID, question_id: UUID) -> bool: ...


class QuestionsMixin(StoreBase):
    async def list_questions(
        self, user_id: UUID, disposition: Disposition | None
    ) -> list[Question]:
        async with (
            self._pool.connection(timeout=self._timeout) as connection,
            connection.transaction(),
        ):
            await connection.execute("set local role personal_os_api")
            cursor = await connection.execute(
                f"select {QUESTION_COLUMNS} from app.jobs_questions where user_id = %s "
                "and (%s::text is null or disposition = %s) order by last_seen_at desc",
                (user_id, disposition, disposition),
            )
            return [Question.model_validate(row) for row in await cursor.fetchall()]

    async def update_question(
        self, user_id: UUID, question_id: UUID, item: QuestionUpdate
    ) -> Question | None:
        """Disposition a question; leaving `pattern` retires the binding that answered it."""
        async with (
            self._pool.connection(timeout=self._timeout) as connection,
            connection.transaction(),
        ):
            await connection.execute("set local role personal_os_api")
            cursor = await connection.execute(
                f"select {PATTERN_COLUMNS} {PATTERN_FROM} where pattern.user_id = %s "
                "and pattern.question_id = %s and pattern.kind = 'binding' "
                "and pattern.status = 'active' for update of pattern",
                (user_id, question_id),
            )
            retired = [Pattern.model_validate(row) for row in await cursor.fetchall()]
            await connection.execute(
                """
                update app.jobs_patterns set status = 'retired'
                where user_id = %s and question_id = %s and kind = 'binding' and status = 'active'
                """,
                (user_id, question_id),
            )
            for old in retired:
                await _record_pattern(
                    connection, user_id, "retire", old,
                    old.model_copy(update={"status": "retired"}), origin="inbox",
                )
            cursor = await connection.execute(
                "select disposition from app.jobs_questions where id = %s and user_id = %s "
                "for update",
                (question_id, user_id),
            )
            before = await cursor.fetchone()
            cursor = await connection.execute(
                f"""
                update app.jobs_questions set disposition = %s, pattern_id = null
                where id = %s and user_id = %s
                returning {QUESTION_COLUMNS}
                """,
                (item.disposition, question_id, user_id),
            )
            row = await cursor.fetchone()
            if row is not None and before is not None and before["disposition"] != item.disposition:
                await record_change(
                    connection, user_id, subject="question", subject_id=str(question_id),
                    action="update", before={"disposition": before["disposition"]},
                    after={"disposition": item.disposition}, origin=item.origin,
                )
        return Question.model_validate(row) if row else None

    async def delete_question(self, user_id: UUID, question_id: UUID) -> bool:
        """Delete a question nothing live answers. Its recorded answers stay, unlinked.

        The row comes back if a run meets the label again. Raises `QuestionInUse`
        while a live pattern answers it; False when it is not this user's.
        """
        async with (
            self._pool.connection(timeout=self._timeout) as connection,
            connection.transaction(),
        ):
            await connection.execute("set local role personal_os_api")
            cursor = await connection.execute(
                "select id from app.jobs_patterns where user_id = %s and question_id = %s "
                "and status = any(%s) limit 1",
                (user_id, question_id, list(LIVE_STATUSES)),
            )
            if await cursor.fetchone() is not None:
                raise QuestionInUse
            cursor = await connection.execute(
                "delete from app.jobs_questions where id = %s and user_id = %s "
                "returning sample_label, disposition",
                (question_id, user_id),
            )
            row = await cursor.fetchone()
            if row is None:
                return False
            await record_change(
                connection, user_id, subject="question", subject_id=str(question_id),
                action="delete", origin="you", after=None,
                before={"sample_label": row["sample_label"], "disposition": row["disposition"]},
            )
        return True
