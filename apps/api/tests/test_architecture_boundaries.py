from __future__ import annotations

import ast
import re
from pathlib import Path

from alcuin_core.contracts import AgentDefinition, EventType


REPOSITORY_ROOT = Path(__file__).parents[3]


def imported_roots(source_root: Path) -> set[str]:
    roots: set[str] = set()
    for source_file in source_root.rglob("*.py"):
        tree = ast.parse(source_file.read_text(), filename=str(source_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                roots.add(node.module.split(".")[0])
    return roots


def test_core_has_no_application_runtime_or_domain_dependencies() -> None:
    roots = imported_roots(
        REPOSITORY_ROOT / "packages/python/alcuin-core/src/alcuin_core"
    )

    assert roots.isdisjoint(
        {
            "alcuin_api",
            "alcuin_operations_copilot",
            "fastapi",
            "langgraph",
            "qdrant_client",
        }
    )


def test_domain_extension_depends_inward_on_core_only() -> None:
    roots = imported_roots(
        REPOSITORY_ROOT
        / "extensions/operations-copilot/src/alcuin_operations_copilot"
    )

    assert "alcuin_core" in roots
    assert roots.isdisjoint({"alcuin_api", "fastapi", "langgraph"})


def test_storage_package_depends_inward_and_services_use_ports() -> None:
    storage_source = (
        REPOSITORY_ROOT / "packages/python/alcuin-storage/src/alcuin_storage"
    )
    storage_roots = imported_roots(storage_source)
    assert "alcuin_core" in storage_roots
    assert storage_roots.isdisjoint(
        {
            "alcuin_api",
            "alcuin_operations_copilot",
            "fastapi",
            "langgraph",
            "qdrant_client",
        }
    )
    assert not (storage_source / "sqlite.py").exists()
    assert all(
        "sqlite3" not in source.read_text()
        for source in storage_source.rglob("*.py")
    )

    for service in ("runtime.py", "extension_tools.py"):
        source = (REPOSITORY_ROOT / "apps/api/src/alcuin_api" / service).read_text()
        assert "alcuin_storage" in source
        assert "sqlite3" not in source

    knowledge_source = (
        REPOSITORY_ROOT
        / "packages/python/alcuin-knowledge/src/alcuin_knowledge"
    )
    knowledge_roots = imported_roots(knowledge_source)
    assert {"alcuin_core", "alcuin_storage", "qdrant_client"}.issubset(
        knowledge_roots
    )
    assert knowledge_roots.isdisjoint(
        {"alcuin_api", "alcuin_operations_copilot", "fastapi", "langgraph"}
    )

    web_search_source = (
        REPOSITORY_ROOT
        / "packages/python/alcuin-web-search/src/alcuin_web_search"
    )
    web_search_roots = imported_roots(web_search_source)
    assert {"alcuin_core", "httpx"}.issubset(web_search_roots)
    assert web_search_roots.isdisjoint(
        {
            "alcuin_api",
            "alcuin_operations_copilot",
            "fastapi",
            "langgraph",
            "qdrant_client",
        }
    )

    api_composition = (
        REPOSITORY_ROOT / "apps/api/src/alcuin_api/main.py"
    ).read_text()
    assert "sqlite3" not in api_composition
    api_config = (
        REPOSITORY_ROOT / "apps/api/src/alcuin_api/config.py"
    ).read_text()
    assert "database_path" not in api_config


def test_python_and_typescript_share_platform_contract_vocabulary() -> None:
    source = (
        REPOSITORY_ROOT / "packages/contracts/src/platform.ts"
    ).read_text()
    schema_version = re.search(r'AGENT_SCHEMA_VERSION = "([^"]+)"', source)
    event_block = re.search(
        r"EXECUTION_EVENT_TYPES = \[(.*?)\] as const",
        source,
        flags=re.DOTALL,
    )

    assert schema_version is not None
    assert event_block is not None
    assert schema_version.group(1) == AgentDefinition.model_fields[
        "schema_version"
    ].default
    assert re.findall(r'"([a-z.]+)"', event_block.group(1)) == [
        event.value for event in EventType
    ]
