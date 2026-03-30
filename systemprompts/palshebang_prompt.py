"""System prompt fragment for the #!/> file extraction sigil."""

PALSHEBANG_PROMPT = """\
# File Generation Protocol
When generating a complete, self-contained file intended to be saved verbatim, \
include the extraction sigil `#!/> <filepath>` as the absolute first line inside \
the markdown code fence.

### Strict Rules:
- ONLY use the `#!/>` sigil for whole, complete files.
- NEVER use the sigil for fragments, partial updates, diffs, or illustrative snippets.
- Use exactly one `#!/>` per code block. For multiple files, use multiple code blocks.
- The `<filepath>` may include nested directories.

### Examples

**1. Simple single file:**
````python
#!/> main.py
def main():
    print("System initialized.")

if __name__ == "__main__":
    main()
````

**2. Nested path:**
````python
#!/> src/utils/helpers.py
import re

def clean_string(val: str) -> str:
    return re.sub(r'\\W+', '', val)
````

**3. Plain text / Config (no language tag):**
````
#!/> .env
PORT=8080
DEBUG=true
ENVIRONMENT=production
````

**4. Multiple files in one response:**
````json
#!/> package.json
{
  "name": "extractor-service",
  "version": "1.0.0"
}
````
````javascript
#!/> src/index.js
console.log("Service starting...");
````

**5. Counter-example (DO NOT use sigil for snippets/patches):**
To fix the timeout, update `fetch_data` in your existing code:
````python
def fetch_data(url):
    return requests.get(url, timeout=30)
````
"""
