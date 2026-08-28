"""Run the optional Operations Copilot example with its built-in adapter."""

from alcuin_api.config import Settings
from alcuin_api.main import create_app
from alcuin_api.store import Store
from alcuin_operations_copilot import operations_demo_adapter, seed_operations_demo


settings = Settings()
store = Store(settings.database_path)
seed_operations_demo(store)
app = create_app(
    settings,
    store=store,
    builtin_adapters={"operations-demo": operations_demo_adapter},
)
