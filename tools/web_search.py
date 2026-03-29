from ddgs import DDGS
from bridge import bridge

@bridge.tool({
    "type": "function",
    "function": {
        "name": "web_search",
        "description": "Search the internet for technical information, code snippets, or documentation.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The search term."}
            },
            "required": ["query"]
        }
    }
})
def web_search(query):
    try:
        with DDGS() as ddgs:
            # Fetch the top 5 relevant results
            results = [r for r in ddgs.text(query, max_results=5)]
            formatted = "\n\n".join([f"Title: {r['title']}\nSnippet: {r['body']}" for r in results])
            return formatted if formatted else "No results found."
    except Exception as e:
        return f"Search Error: {str(e)}"