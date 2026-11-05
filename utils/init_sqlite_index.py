"""Rebuild the SQLite question index from existing .tex files."""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils.index_rebuild import rebuild_index

if __name__ == "__main__":
    print(f"Rebuilt SQLite index: {rebuild_index()} records")
