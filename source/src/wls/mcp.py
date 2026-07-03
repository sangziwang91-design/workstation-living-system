from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class MCPTool:
    name: str
    description: str
    input_schema: dict[str, Any]
    server_name: str


class MCPTrustGate:
    """Discovery and trust management for MCP servers and tools."""

    def __init__(self):
        self._allowed_servers: set[str] = set()
        self._allowed_tools: set[str] = set()

    def allow_server(self, server_name: str) -> None:
        self._allowed_servers.add(server_name)

    def allow_tool(self, tool_name: str) -> None:
        self._allowed_tools.add(tool_name)

    def is_allowed(self, server_name: str, tool_name: str | None = None) -> bool:
        if server_name not in self._allowed_servers:
            return False
        if tool_name is not None and tool_name not in self._allowed_tools:
            return False
        return True

    def list_tools(self, server_name: str) -> list[MCPTool]:
        if server_name not in self._allowed_servers:
            raise PermissionError(f"MCP server not trusted: {server_name}")
            
        result = subprocess.run(
            ["manus-mcp-cli", "tool", "list", "--server", server_name],
            check=True,
            capture_output=True,
            text=True
        )
        
        # Parse the output of manus-mcp-cli (assuming it returns JSON or structured text)
        # For this foundation, we provide a placeholder parser
        try:
            data = json.loads(result.stdout)
            return [
                MCPTool(
                    name=item["name"],
                    description=item["description"],
                    input_schema=item["input_schema"],
                    server_name=server_name
                )
                for item in data
            ]
        except (json.JSONDecodeError, KeyError):
            # Fallback for non-JSON output
            return []

    def call_tool(self, server_name: str, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if not self.is_allowed(server_name, tool_name):
            raise PermissionError(f"MCP tool call blocked by trust gate: {server_name}/{tool_name}")
            
        result = subprocess.run(
            ["manus-mcp-cli", "tool", "call", tool_name, "--server", server_name, "--input", json.dumps(arguments)],
            check=True,
            capture_output=True,
            text=True
        )
        
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            return {"raw_output": result.stdout}

