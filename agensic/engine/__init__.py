from .context import RequestContext, Settings, SystemInventory

__all__ = [
    "RequestContext",
    "Settings",
    "SuggestionEngine",
    "SystemInventory",
]


def __getattr__(name: str):
    if name == "SuggestionEngine":
        from .suggestion_engine import SuggestionEngine

        return SuggestionEngine
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
