"""Naming is bounded, tool-free, asynchronous, and separate from conversation content."""

import asyncio
import json

import httpx
import pytest

from alcuin_api.config import Settings
from alcuin_api.thread_titles import ThreadTitleService, fallback_title


class NamingRepository:
    def __init__(self, text="Summarize research on reusable agents"):
        self.thread = {
            "id": "thread",
            "workspace_id": "ws",
            "agent_version_id": "version",
            "title": "Working session",
            "title_status": "pending",
        }
        self.text = text
        self.claim = None

    def get_thread(self, workspace_id, thread_id):
        return (
            dict(self.thread) if (workspace_id, thread_id) == ("ws", "thread") else None
        )

    def thread_title_candidates(self, workspace_id, thread_id):
        assert (workspace_id, thread_id) == ("ws", "thread")
        return [{"text": self.text}] if self.text else []

    def claim_thread_title(self, workspace_id, thread_id, expected_title, claim):
        if self.claim or expected_title != self.thread["title"]:
            return False
        self.claim = claim
        self.thread["title_status"] = "generating"
        return True

    def finish_thread_title(
        self, workspace_id, thread_id, expected_title, claim, title
    ):
        if self.claim != claim or self.thread["title"] != expected_title:
            return False
        self.thread.update(
            title=title or expected_title, title_status="ready" if title else "pending"
        )
        self.claim = None
        return True

    def get_agent_version(self, workspace_id, version_id):
        return {
            "definition": {
                "identity": {"name": "Agent"},
                "instructions": "Never send this Agent instruction for naming.",
                "model": {"provider": "deepseek", "model": "deepseek-v4-pro"},
                "tools": ["mutate.tool"],
            }
        }


def service(repository, handler=None, *, key=""):
    return ThreadTitleService(
        repository,
        Settings(_env_file=None, deepseek_api_key=key, openai_api_key=""),
        transport=httpx.MockTransport(handler) if handler else None,
    )


async def settle(namer):
    await asyncio.gather(*list(namer.workers))


async def test_no_input_remains_pending_then_first_input_gets_local_title():
    repo = NamingRepository("")
    namer = service(repo)
    assert namer.ensure("ws", "thread")["title_status"] == "pending"
    assert not namer.workers
    repo.text = "首次确认的任务目标"
    assert namer.ensure("ws", "thread")["title_status"] == "generating"
    await settle(namer)
    assert repo.thread["title"] == "首次确认的任务目标"
    assert repo.thread["title_status"] == "ready"


async def test_model_is_nonblocking_capability_free_and_single_claim():
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def handler(request):
        payload = json.loads(request.content)
        calls.append(payload)
        assert "tools" not in payload
        assert payload["max_tokens"] == 256
        assert (
            "Never send this Agent instruction" not in payload["messages"][0]["content"]
        )
        entered.set()
        await release.wait()
        events = [
            {
                "choices": [
                    {"delta": {"content": "Reusable agents"}, "finish_reason": None}
                ]
            },
            {"choices": [{"delta": {}, "finish_reason": "stop"}]},
        ]
        return httpx.Response(
            200, text="".join(f"data: {json.dumps(e)}\n\n" for e in events)
        )

    repo = NamingRepository()
    namer = service(repo, handler, key="test-key")
    assert namer.ensure("ws", "thread")["title_status"] == "generating"
    await entered.wait()
    assert namer.ensure("ws", "thread")["title_status"] == "generating"
    assert len(calls) == 1
    release.set()
    await settle(namer)
    assert repo.thread["title"] == "Reusable agents"


@pytest.mark.parametrize(
    "text",
    [
        "password: actual-password",
        "密码：私密",
        "Use secret://widget/password",
        "sk-abcdefghijk",
        "my exact-known-key is here",
    ],
)
async def test_sensitive_input_never_enters_model_or_fallback(text):
    repo = NamingRepository(text)
    namer = service(
        repo, lambda _: pytest.fail("Sensitive input was sent"), key="exact-known-key"
    )
    assert namer.ensure("ws", "thread")["title_status"] == "pending"
    assert not namer.workers
    assert repo.thread["title"] == "Working session"


async def test_timeout_falls_back_and_cancellation_allows_repair(monkeypatch):
    monkeypatch.setattr("alcuin_api.thread_titles.TITLE_TIMEOUT_SECONDS", 0.01)

    async def handler(_):
        await asyncio.sleep(10)

    repo = NamingRepository()
    namer = service(repo, handler, key="test-key")
    namer.ensure("ws", "thread")
    await settle(namer)
    assert repo.thread["title_status"] == "ready"
    assert repo.thread["title"] == repo.text

    repo = NamingRepository()
    namer = service(repo, handler, key="test-key")
    namer.ensure("ws", "thread")
    await asyncio.sleep(0)
    await namer.close()
    assert repo.thread["title_status"] == "pending"


async def test_custom_title_and_workspace_are_protected():
    repo = NamingRepository()
    repo.thread.update(title="用户自定义标题", title_status="ready")
    namer = service(repo)
    assert namer.ensure("ws", "thread")["title"] == "用户自定义标题"
    assert not namer.workers
    with pytest.raises(LookupError):
        namer.ensure("other", "thread")


def test_fallback_extracts_a_topic_and_preserves_english_words():
    assert (
        fallback_title(
            "请为一个可溯源交互式 Agent 系统准备一份简短的功能演示方案，分为两个步骤，最后输出 Markdown 文档。"
        )
        == "可溯源交互式 Agent 系统"
    )
    assert not fallback_title(
        "A conversation about a rather extensive Markdown document"
    ).endswith("Mark")
    assert (
        fallback_title("A conversation about a rather extensive Markdown document")
        == "A conversation about a rather extensive Markdown"
    )
