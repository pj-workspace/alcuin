"""Worker ownership and shutdown ordering without a database or provider."""
import asyncio

import pytest

from alcuin_api.run_control import RunControls


class FakeStore:
    def __init__(self):
        self.run = {"id": "run", "status": "queued"}
        self.events = []
        self.closed = False
        self.reads_after_close = 0

    def get_run(self, workspace_id, run_id):
        assert (workspace_id, run_id) == ("workspace", "run")
        if self.closed:
            self.reads_after_close += 1
            raise RuntimeError("Repository was already closed")
        return dict(self.run)

    def request_run_cancel(self, workspace_id, run_id, *, tool_in_flight=False):
        run = self.get_run(workspace_id, run_id)
        if run["status"] not in {"cancel_requested", "cancelled", "completed", "failed"}:
            self.run["status"] = "cancel_requested"
            self.events.append(("run.cancel_requested", {"tool_in_flight": tool_in_flight}))
        return dict(self.run)

    def finish(self, workspace_id, run_id, mutation_started):
        if self.get_run(workspace_id, run_id)["status"] == "cancel_requested":
            self.run["status"] = "cancelled"
            self.events.append(("run.cancelled", {"external_actions_may_have_completed": mutation_started}))


def controls_fixture():
    store = FakeStore()
    return store, RunControls(store, store.finish)


async def test_cancel_before_first_instruction_never_calls_work_and_finalizes_once():
    store, controls = controls_fixture()
    called = []

    async def work():
        called.append(True)

    task = controls.schedule("workspace", "run", work)
    controls.cancel("workspace", "run")
    controls.cancel("workspace", "run")
    await asyncio.gather(task, return_exceptions=True)
    assert called == []
    assert task.cancelled()
    assert store.run["status"] == "cancelled"
    assert [kind for kind, _ in store.events] == ["run.cancel_requested", "run.cancelled"]
    assert controls.executions == {}


async def test_running_provider_wait_is_interrupted_without_continuing_work():
    store, controls = controls_fixture()
    entered, exited = asyncio.Event(), asyncio.Event()
    continued = []

    async def work():
        store.run["status"] = "running"
        store.events.append(("message.delta", {"delta": "Partial answer"}))
        entered.set()
        try:
            await asyncio.Event().wait()
            continued.append(True)
        finally:
            exited.set()

    task = controls.schedule("workspace", "run", work)
    await asyncio.wait_for(entered.wait(), 1)
    controls.cancel("workspace", "run")
    await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 1)
    assert exited.is_set()
    assert continued == []
    assert store.run["status"] == "cancelled"
    assert store.events == [
        ("message.delta", {"delta": "Partial answer"}),
        ("run.cancel_requested", {"tool_in_flight": False}),
        ("run.cancelled", {"external_actions_may_have_completed": False}),
    ]
    assert controls.executions == {}


async def test_dispatched_mutation_is_not_interrupted_and_receipt_precedes_stop():
    store, controls = controls_fixture()
    entered, release = asyncio.Event(), asyncio.Event()
    continued = []

    async def work():
        store.run["status"] = "running"
        controls.before_tool("workspace", "run", mutating=True)
        entered.set()
        await release.wait()
        store.events.append(("tool.completed", {"status": "succeeded"}))
        controls.after_tool("workspace", "run")
        continued.append(True)

    task = controls.schedule("workspace", "run", work)
    try:
        await asyncio.wait_for(entered.wait(), 1)
        controls.cancel("workspace", "run")
        controls.cancel("workspace", "run")
        assert task.cancelling() == 0
        assert not task.done()
        assert store.run["status"] == "cancel_requested"
        assert store.events == [("run.cancel_requested", {"tool_in_flight": True})]
    finally:
        release.set()
        await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 1)
    assert continued == []
    assert store.run["status"] == "cancelled"
    assert store.events == [
        ("run.cancel_requested", {"tool_in_flight": True}),
        ("tool.completed", {"status": "succeeded"}),
        ("run.cancelled", {"external_actions_may_have_completed": True}),
    ]
    assert controls.executions == {}


@pytest.mark.parametrize("phase", ["queued", "provider", "mutation"])
async def test_shutdown_cancel_and_drain_settles_callbacks_before_repository_close(phase):
    store, controls = controls_fixture()
    entered = asyncio.Event()
    callback_errors = []
    loop = asyncio.get_running_loop()
    previous_handler = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: callback_errors.append(context))

    async def work():
        store.run["status"] = "running"
        if phase == "mutation":
            controls.before_tool("workspace", "run", mutating=True)
        entered.set()
        await asyncio.Event().wait()

    try:
        task = controls.schedule("workspace", "run", work)
        if phase != "queued":
            await asyncio.wait_for(entered.wait(), 1)
        # Same ordering as application shutdown: cancel handles, drain tasks
        # and registered completion callbacks, then close the repository.
        task.cancel()
        outcomes = await asyncio.gather(task, return_exceptions=True)
        assert isinstance(outcomes[0], asyncio.CancelledError)
        assert controls.executions == {}
        store.closed = True
        await asyncio.sleep(0)
        assert store.reads_after_close == 0
        assert callback_errors == []
        # Shutdown is not a user decision and must not fabricate a stop receipt.
        assert store.events == []
        assert store.run["status"] == ("queued" if phase == "queued" else "running")
    finally:
        loop.set_exception_handler(previous_handler)
