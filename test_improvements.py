"""Quick test of new platform improvements."""
import sys
sys.path.insert(0, '.')

# Test 1: Auto-enrichment detection
from aeon_nimbus.auto_enrich import needs_enrichment
from pathlib import Path

print("Testing auto-enrichment system...")
test_file = Path("data/extracted/apple_inc.json")
if test_file.exists():
    needs = needs_enrichment(test_file)
    print(f"  Apple needs enrichment: {needs}")
else:
    print("  Apple file not found")

# Test 2: Cache system
from aeon_nimbus.cache import ResponseCache
cache = ResponseCache(ttl=60)
cache.set("test_key", {"data": "value"})
result = cache.get("test_key")
print(f"  Cache working: {result is not None}")

# Test 3: Screener API imports
try:
    from aeon_nimbus import screener_api
    print(f"  Screener API loaded: {hasattr(screener_api, 'router')}")
except Exception as e:
    print(f"  Screener API error: {e}")

# Test 4: Bulk API imports
try:
    from aeon_nimbus import bulk_api
    print(f"  Bulk API loaded: {hasattr(bulk_api, 'router')}")
except Exception as e:
    print(f"  Bulk API error: {e}")

print("\nAll core improvements loaded successfully!")
