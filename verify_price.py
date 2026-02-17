import sys
import os
from pathlib import Path

# Add project root to path
sys.path.append(str(Path.cwd()))

try:
    from bot import config
    from bot import handlers
except ImportError as e:
    print(f"ImportError: {e}")
    sys.exit(1)

def verify():
    print("--- Verifying Price Update ---")
    
    # Check Config
    expected_price = 20
    if config.STARS_PER_50_PAGES != expected_price:
        print(f"❌ Config Mismatch: Expected {expected_price}, got {config.STARS_PER_50_PAGES}")
        return False
    print(f"✅ Config STARS_PER_50_PAGES = {config.STARS_PER_50_PAGES}")

    # Check Calculation
    cost_for_50 = handlers._calc_cost(50)
    if cost_for_50 != 20:
        print(f"❌ Calculation Logic Fail: 50 pages should cost 20, got {cost_for_50}")
        return False
    print(f"✅ Calculation Logic: 50 pages = {cost_for_50} stars")

    # Check UI Text
    expected_text_fragment = "~25 стр" # 10 stars buys 25 pages now
    pkg_10 = handlers.STAR_PACKAGES[0] # (10, "label")
    if expected_text_fragment not in pkg_10[1]:
        print(f"❌ UI Text Fail: Expected '{expected_text_fragment}' in '{pkg_10[1]}'")
        return False
    print(f"✅ UI Text Updated: {pkg_10[1]}")

    print("\n🎉 Verification Passed!")
    return True

if __name__ == "__main__":
    if not verify():
        sys.exit(1)
