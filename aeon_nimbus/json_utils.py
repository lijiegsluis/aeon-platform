"""Utility to clean NaN/Inf values from JSON serialization."""
import math
from typing import Any

def clean_for_json(obj: Any) -> Any:
    """Recursively replace NaN and Inf with None for JSON serialization."""
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    elif isinstance(obj, dict):
        return {k: clean_for_json(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [clean_for_json(v) for v in obj]
    elif isinstance(obj, tuple):
        return tuple(clean_for_json(v) for v in obj)
    return obj
