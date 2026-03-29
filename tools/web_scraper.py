import subprocess
import base64
from bridge import bridge

CONTAINER_NAME = "ai_sandbox"

@bridge.tool({
    "type": "function",
    "function": {
        "name": "scrape_url",
        "description": "Fetch and extract the full text content from a given URL using BeautifulSoup inside the sandbox.",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "The URL to scrape content from."}
            },
            "required": ["url"]
        }
    }
})
def scrape_url(url):
    # Python script to run inside the sandbox
    python_code = f"""
import requests
from bs4 import BeautifulSoup

try:
    headers = {{'User-Agent': 'Mozilla/5.0'}}
    response = requests.get("{url}", headers=headers, timeout=15)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, 'html.parser')
    
    # Remove non-content elements
    for element in soup(["script", "style", "nav", "footer", "header"]):
        element.decompose()
        
    text = soup.get_text(separator=' ')
    # Clean up whitespace
    lines = (line.strip() for line in text.splitlines())
    chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
    text = '\\n'.join(chunk for chunk in chunks if chunk)
    
    print(text[:8000]) # Truncate to avoid context overflow
except Exception as e:
    print(f"Scraping Error: {{str(e)}}")
"""
    encoded_code = base64.b64encode(python_code.encode()).decode()
    cmd = f"echo '{encoded_code}' | base64 -d | python3"
    
    try:
        process = subprocess.run(
            ["docker", "exec", CONTAINER_NAME, "bash", "-c", cmd],
            capture_output=True, text=True, encoding="utf-8"
        )
        return process.stdout if process.returncode == 0 else process.stderr
    except Exception as e:
        return f"Scraper Tool Error: {str(e)}"