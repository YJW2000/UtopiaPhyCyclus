"""Legacy command alias: rebuild SQLite without creating a CSV snapshot."""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils.core_config import CHAPTERS_DIR, CSV_INDEX_PATH
from utils.index_rebuild import rebuild_index

if __name__ == "__main__":
    count = rebuild_index(CHAPTERS_DIR, CSV_INDEX_PATH)
    print(f"Rebuilt SQLite index: {count} records")
