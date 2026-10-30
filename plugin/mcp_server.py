"""The plugin's MCP server: `stationhouse mcp` from the CLI code shipped in this plugin, so it works
without a separate install (plugin/mcp.json runs it with `uv run --with mcp`). Signs in with the
contributor's `stationhouse login` session.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from stationhouse_cli import mcp_server  # noqa: E402

sys.exit(mcp_server.main())
