import psycopg2
from bridge import bridge
import os

# Database connection configuration
# In a production environment, these should be environment variables
DB_CONFIG = {
    "dbname": "ai_memory",
    "user": "admin",
    "password": "password",
    "host": "postgres_db", # Name of your postgres container or host
    "port": "5432"
}

def get_connection():
    return psycopg2.connect(**DB_CONFIG)

@bridge.tool({
    "type": "function",
    "function": {
        "name": "manage_memory",
        "description": "Save or retrieve important facts to persistent Postgres memory.",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["save", "recall"]},
                "key": {"type": "string", "description": "The topic or name of the fact."},
                "fact": {"type": "string", "description": "The information to save (only for 'save')."}
            },
            "required": ["action", "key"]
        }
    }
})
def manage_memory(action, key, fact=None):
    try:
        conn = get_connection()
        cur = conn.cursor()

        # Ensure table exists
        cur.execute("""
            CREATE TABLE IF NOT EXISTS knowledge_base (
                key TEXT PRIMARY KEY,
                fact TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        if action == "save":
            # UPSERT: Insert new fact or update if key exists
            cur.execute("""
                INSERT INTO knowledge_base (key, fact) 
                VALUES (%s, %s) 
                ON CONFLICT (key) DO UPDATE SET fact = EXCLUDED.fact, updated_at = CURRENT_TIMESTAMP
            """, (key, fact))
            conn.commit()
            result = f"Saved fact about '{key}' to Postgres memory."
        
        else: # recall
            cur.execute("SELECT fact FROM knowledge_base WHERE key = %s", (key,))
            row = cur.fetchone()
            result = row[0] if row else f"I have no memory of '{key}'."

        cur.close()
        conn.close()
        return result

    except Exception as e:
        return f"Database Error: {str(e)}"