import sys
from pathlib import Path

# Make the project root importable without installing anything.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
