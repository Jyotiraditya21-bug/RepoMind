import sys
import os

# Add the backend directory to system path so backend imports (like analyzer, agent) resolve correctly when run from the root
sys.path.append(os.path.join(os.path.dirname(__file__), "backend"))

from backend.main import app
