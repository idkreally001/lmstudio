import importlib
import pkgutil
from bridge import bridge

def auto_register_tools():
    """Auto‑discover all modules in the `tools` package that define a tool via the `@bridge.tool` decorator.
    Each module is imported, which triggers the decorator and registers the function in the bridge registry.
    """
    package = __name__
    for _, module_name, is_pkg in pkgutil.iter_modules(__path__):
        if not is_pkg:
            importlib.import_module(f"{package}.{module_name}")

# Execute registration on import
auto_register_tools()
