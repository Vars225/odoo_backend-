import traceback
import sys

try:
    from main import app
    with open("test_result.txt", "w") as f:
        f.write(f"SUCCESS - {len(app.routes)} routes\n")
    print("SUCCESS")
except Exception as e:
    with open("test_result.txt", "w") as f:
        f.write(traceback.format_exc())
    print("FAILED - see test_result.txt")
