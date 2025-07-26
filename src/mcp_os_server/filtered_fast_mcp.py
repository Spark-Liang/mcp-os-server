"""Filtered FastMCP implementation for environment variable based filtering."""

import os
from typing import List
from urllib.parse import urlparse

from mcp.server.fastmcp import FastMCP
from mcp.types import Resource as MCPResource
from mcp.types import ResourceTemplate as MCPResourceTemplate
from mcp.types import Tool as MCPTool


def should_include_tool(tool_name: str) -> bool:
    """Check if a tool should be included based on environment variables."""
    disable_tools = os.getenv("DISABLE_TOOLS", "")
    enable_tools_only = os.getenv("ENABLE_TOOLS_ONLY", "")

    # Parse environment variables
    disabled_tools = [tool.strip() for tool in disable_tools.split(",") if tool.strip()]
    enabled_tools_only = [
        tool.strip() for tool in enable_tools_only.split(",") if tool.strip()
    ]

    # If ENABLE_TOOLS_ONLY is set, only include tools in that list
    if enabled_tools_only:
        return tool_name in enabled_tools_only

    # If DISABLE_TOOLS is set, exclude tools in that list
    if disabled_tools:
        return tool_name not in disabled_tools

    # Default: include all tools
    return True


def should_include_resource(resource_name: str) -> bool:
    """Check if a resource should be included based on environment variables."""
    disable_resources = os.getenv("DISABLE_RESOURCES", "")
    enable_resources_only = os.getenv("ENABLE_RESOURCES_ONLY", "")

    # Parse environment variables
    disabled_resources = [
        res.strip() for res in disable_resources.split(",") if res.strip()
    ]
    enabled_resources_only = [
        res.strip() for res in enable_resources_only.split(",") if res.strip()
    ]

    # If ENABLE_RESOURCES_ONLY is set, only include resources in that list
    if enabled_resources_only:
        return resource_name in enabled_resources_only

    # If DISABLE_RESOURCES is set, exclude resources in that list
    if disabled_resources:
        return resource_name not in disabled_resources

    # Default: include all resources
    return True


class FilteredFastMCP(FastMCP):
    """FastMCP subclass that supports filtering tools and resources via environment variables."""

    def _extract_resource_type(self, uri: str) -> str:
        """Extract resource type from URI using urllib.parse."""
        try:
            parsed = urlparse(uri)
            return parsed.scheme if parsed.scheme else "unknown"
        except Exception:
            # Fallback for invalid URIs
            return "unknown"

    async def list_tools(self) -> List[MCPTool]:
        """List tools filtered by environment variables."""
        tools = await super().list_tools()
        return [tool for tool in tools if should_include_tool(tool.name)]

    async def list_resources(self) -> List[MCPResource]:
        """List resources filtered by environment variables."""
        resources = await super().list_resources()
        filtered_resources = []
        for resource in resources:
            resource_type = self._extract_resource_type(str(resource.uri))
            if should_include_resource(resource_type):
                filtered_resources.append(resource)
        return filtered_resources

    async def list_resource_templates(self) -> List[MCPResourceTemplate]:
        """List resource templates filtered by environment variables."""
        templates = await super().list_resource_templates()
        filtered_templates = []
        for template in templates:
            resource_type = self._extract_resource_type(template.uriTemplate)
            if should_include_resource(resource_type):
                filtered_templates.append(template)
        return filtered_templates
