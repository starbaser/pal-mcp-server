Here is the complete, token-optimized system prompt fragment. It clearly defines the rules and provides tight, distinct few-shot examples that establish the boundaries between full files and snippets.

You can paste this directly into your `PALSHEBANG_PROMPT` string constant.

```markdown
# File Generation Protocol
When generating a complete, self-contained file intended to be saved verbatim, you must include the extraction sigil `#!/> <filepath>` as the absolute first line inside the markdown code fence. 

### Strict Rules:
- ONLY use the `#!/>` sigil for whole, complete files.
- NEVER use the sigil for fragments, partial updates, diffs, or illustrative snippets.
- Use exactly one `#!/>` per code block. For multiple files, use multiple code blocks.
- The `<filepath>` may include nested directories.

### Examples

**1. Simple single file:**
```python
#!/> main.py
def main():
    print("System initialized.")

if __name__ == "__main__":
    main()
```

**2. Nested path:**
```python
#!/> src/utils/helpers.py
import re

def clean_string(val: str) -> str:
    return re.sub(r'\W+', '', val)
```

**3. Plain text / Config (no language tag):**
```
#!/> .env
PORT=8080
DEBUG=true
ENVIRONMENT=production
```

**4. Multiple files in one response:**
Here is the configuration and the entry point:
```json
#!/> package.json
{
  "name": "extractor-service",
  "version": "1.0.0"
}
```
```javascript
#!/> src/index.js
console.log("Service starting...");
```

**5. Counter-example (Snippets/Partial Updates — DO NOT USE SIGIL):**
To fix the timeout issue, update the `fetch_data` function in your existing code:
```python
def fetch_data(url):
    # Increased timeout from 10 to 30
    return requests.get(url, timeout=30) 
```
```

***

### Why this design works:
* **Minimal Token Overhead:** The file contents are artificially short (1-3 lines), showing the parser the exact shape of the output without burning context window on long dummy code.
* **High Contrast:** Example 5 explicitly demonstrates the "absence" of the sigil, grounding the model's understanding of a *snippet* versus a *file*.
* **Format Reinforcement:** Every positive example shows the sigil on line 1, immediately followed by the filename and extension, with valid code immediately following on line 2.

Would you like me to write the Python regex extraction function that pairs perfectly with this prompt?
