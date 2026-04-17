import sys
from pathlib import Path

# Allow `import ibkr_bot.x` when running pytest from the package dir.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
