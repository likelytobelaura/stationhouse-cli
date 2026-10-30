"""The plugin's PreToolUse hook: runs the Station House design gate on Bash calls that push or open a
PR (`git push`, `gh pr create`, `hf upload --create-pr`). Exit 2 blocks the call and tells Claude to
run the Experimentation Wizard first. Uses the CLI code shipped in this plugin (stdlib only) and the
contributor's `stationhouse login` session. Any failure lets the push through; the watcher still
notes an unreviewed experiment PR.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

try:
    from stationhouse_cli import gate
except Exception as e:  # noqa: BLE001
    print(f"Station House design gate unavailable: {e}", file=sys.stderr)
    sys.exit(0)

code, message = gate.hook(sys.stdin.read())
if message:
    print(message, file=sys.stderr)
sys.exit(code)
