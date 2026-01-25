 # test_picklist_update.py
import asyncio
import sys
import logging

logging.basicConfig(level=logging.INFO)

from workflows.save_template_workflow import ensure_picklist_value

async def test_picklist_update():
    print("\n=== Testing Picklist Update ===\n")

    result = await ensure_picklist_value(
        object_name="Campaign",
        field_name="Email_template__c",
        value="9"
    )

    print("\n=== RESULT ===")
    print("✅ PASSED" if result else "❌ FAILED")
    return result

if __name__ == "__main__":
    ok = asyncio.run(test_picklist_update())
    sys.exit(0 if ok else 1)
