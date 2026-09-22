"""Durable user clarification storage, mixed into the scoped SQL repository."""
import json
import uuid

from alcuin_core.contracts import utc_now
from alcuin_core.human_input import HumanAnswer, HumanQuestion
from .errors import RepositoryConflict


class HumanInputStorage:
    def suspend_run_for_input(self, workspace_id: str, run_id: str, question: dict, continuation: dict) -> dict:
        public = HumanQuestion.model_validate({key: question[key] for key in ("question", "options")}).model_dump()
        public["call_id"] = str(question["call_id"])
        self._validate_no_raw_secrets(public)
        self._validate_no_raw_secrets(continuation)
        if len(self._json(continuation).encode()) > 2_000_000:
            raise ValueError("Interaction continuation exceeds the safe size limit")
        input_id, created_at = f"inp_{uuid.uuid4().hex[:20]}", utc_now()
        with self.lock, self.connection:
            run = self._one("SELECT * FROM runs WHERE workspace_id = ? AND id = ? FOR UPDATE", (workspace_id, run_id))
            if not run or run["status"] != "running":
                raise RepositoryConflict("Run is not available for a question")
            if self._one("SELECT 1 FROM task_run_links WHERE workspace_id = ? AND run_id = ?", (workspace_id, run_id)):
                raise RepositoryConflict("Task questions require a Task suspension boundary")
            self.connection.execute(
                """INSERT INTO run_inputs
                (id, workspace_id, run_id, status, question_json, continuation_json, created_at)
                VALUES (?, ?, ?, 'pending', ?, ?, ?)""",
                (input_id, workspace_id, run_id, self._json(public), self._json(continuation), created_at),
            )
            self.connection.execute("UPDATE runs SET status = 'waiting_for_input' WHERE workspace_id = ? AND id = ?", (workspace_id, run_id))
            return self._append_event_locked(workspace_id, run_id, "input.required", {**public, "input_id": input_id})

    def answer_run_input(self, workspace_id: str, run_id: str, input_id: str, answer: dict) -> dict | None:
        answer = HumanAnswer.model_validate(answer).model_dump()
        self._validate_no_raw_secrets(answer)
        answered_at = utc_now()
        with self.lock, self.connection:
            run = self._one("SELECT * FROM runs WHERE workspace_id = ? AND id = ? FOR UPDATE", (workspace_id, run_id))
            row = self._one("SELECT * FROM run_inputs WHERE workspace_id = ? AND run_id = ? AND id = ? FOR UPDATE", (workspace_id, run_id, input_id))
            if not run or not row:
                return None
            if row["status"] != "pending" or run["status"] != "waiting_for_input":
                raise RepositoryConflict("This question is no longer waiting for an answer")
            status = "skipped" if answer["skip"] else "answered"
            question = json.loads(row["question_json"])
            continuation = json.loads(row["continuation_json"])
            self.connection.execute(
                """UPDATE run_inputs SET status = ?, answer_json = ?, answered_at = ?, continuation_json = NULL
                WHERE workspace_id = ? AND id = ?""",
                (status, self._json(answer), answered_at, workspace_id, input_id),
            )
            # Atomic claim. A restart now fails this Run instead of replaying
            # already completed tools or reusing the same answer twice.
            self.connection.execute("UPDATE runs SET status = 'queued' WHERE workspace_id = ? AND id = ?", (workspace_id, run_id))
            self._append_event_locked(workspace_id, run_id, "input.answered", {
                "input_id": input_id, "call_id": question["call_id"],
                "question": question["question"], **answer, "answered_at": answered_at,
            })
            self._append_event_locked(workspace_id, run_id, "tool.completed", {
                "tool": "human.ask", "call_id": question["call_id"], "status": "succeeded",
                "result_summary": "User chose to continue without answering" if answer["skip"] else "User answered the question",
                "duration_ms": 0,
            })
        return {"id": input_id, "run_id": run_id, "workspace_id": workspace_id, "status": status,
                "question": question, "answer": answer, "continuation": continuation}

    def list_thread_input_events(self, workspace_id: str, thread_id: str, run_ids: list[str]) -> list[dict]:
        ids = list(dict.fromkeys(run_ids))
        if len(ids) > 50:
            raise ValueError("At most 50 Runs may be projected per Thread page")
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        rows = self._all(
            f"""SELECT e.* FROM events e JOIN runs r ON r.workspace_id = e.workspace_id AND r.id = e.run_id
            WHERE e.workspace_id = ? AND r.thread_id = ? AND e.run_id IN ({placeholders})
              AND e.type IN ('input.required', 'input.answered') ORDER BY e.run_id, e.sequence LIMIT 400""",
            (workspace_id, thread_id, *ids),
        )
        return [self._citation_event_projection(row) for row in rows]

    def list_thread_input_answers(self, workspace_id: str, thread_id: str) -> list[dict]:
        rows = self._all(
            """SELECT i.id, i.run_id, i.question_json, i.answer_json FROM run_inputs i
            JOIN runs r ON r.id = i.run_id AND r.workspace_id = i.workspace_id
            WHERE i.workspace_id = ? AND r.thread_id = ? AND i.status IN ('answered', 'skipped')
            ORDER BY i.created_at DESC LIMIT 200""", (workspace_id, thread_id),
        )
        return [{"id": row["id"], "run_id": row["run_id"], "question": json.loads(row["question_json"]), "answer": json.loads(row["answer_json"])} for row in reversed(rows)]
