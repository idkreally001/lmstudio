import json
from bridge import bridge

@bridge.tool({
    "type": "function",
    "function": {
        "name": "list_available_tools",
        "description": "Returns a list of all currently registered tool names and their descriptions. Use this if you are unsure of a tool name.",
        "parameters": {"type": "object", "properties": {}}
    }
})
def list_available_tools():
    tools = []
    for schema in bridge.schemas:
        name = schema["function"]["name"]
        desc = schema["function"]["description"]
        tools.append(f"- {name}: {desc}")
    return json.dumps({"status": "ok", "output": "Available Tools:\n" + "\n".join(tools)})