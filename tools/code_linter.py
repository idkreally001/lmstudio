import ast
from bridge import bridge

@bridge.tool({
    "type": "function",
    "function": {
        "name": "lint_python_code",
        "description": "Checks a Python code snippet for syntax errors before writing it to a file.",
        "parameters": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "The Python code to validate."}
            },
            "required": ["code"]
        }
    }
})
def lint_python_code(code):
    try:
        # ast.parse attempts to compile the code into a tree; fails on SyntaxError
        ast.parse(code)
        return "SUCCESS: Code is syntactically valid."
    except SyntaxError as e:
        return f"SYNTAX ERROR found: {e.msg} at line {e.lineno}, offset {e.offset}. Please fix this before writing to a file."
    except Exception as e:
        return f"Linter Error: {str(e)}"