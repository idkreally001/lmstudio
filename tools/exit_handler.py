from bridge import bridge

@bridge.tool({
    "type": "function",
    "function": {
        "name": "terminate_research",
        "description": "Formally ends the autonomous session when all goals are met or no further progress can be made.",
        "parameters": {
            "type": "object",
            "properties": {
                "reason": {"type": "string", "description": "A brief summary of why the research is ending."},
                "final_discovery": {"type": "string", "description": "The most important finding from the session."}
            },
            "required": ["reason"]
        }
    }
})
def terminate_research(reason, final_discovery="None"):
    # This string acts as a 'Flag' for the bridge.py loop
    return f"TERMINATE_SIGNAL: {reason} | Discovery: {final_discovery}"