#!/bin/bash
# Basic usage examples for clink_invoke.py

echo "=== Clink Invoke Basic Usage ==="
echo ""

# Example 1: Generate shell command (default)
echo "1. Generate shell command (output only):"
echo "   $ uv run clink_invoke.py glmaude 'What is 2+2?'"
echo ""
uv run clink_invoke.py glmaude "What is 2+2?" | head -5
echo "   [... command continues ...]"
echo ""

# Example 2: Eval the command in your shell
echo "2. Eval command in your shell (runs in YOUR environment):"
echo "   $ eval \"\$(uv run clink_invoke.py glmaude 'What is 2+2?')\""
echo ""
echo "   Running..."
eval "$(uv run clink_invoke.py glmaude 'What is 2+2?')" 2>&1 | head -10
echo ""

# Example 3: With files
echo "3. With files:"
echo "   $ eval \"\$(uv run clink_invoke.py glmaude 'Summarize this file' --files clink_invoke.py)\""
echo "   [Would include file contents in prompt]"
echo ""

# Example 4: With role
echo "4. With custom role:"
echo "   $ eval \"\$(uv run clink_invoke.py glmaude 'Review code' --role agency --files *.py)\""
echo "   [Uses agency role prompt]"
echo ""

# Example 5: Preview without executing
echo "5. Preview mode (see command without running):"
echo "   $ uv run clink_invoke.py glmaude 'test' --preview"
echo ""
uv run clink_invoke.py glmaude "test" --preview 2>&1 | tail -5
echo ""

# Example 6: Verify environment
echo "6. Verify it uses YOUR environment:"
echo "   $ export MY_VAR='from_my_shell'"
export MY_VAR="from_my_shell"
echo "   $ eval \"\$(uv run clink_invoke.py glmaude 'test')\""
echo "   [Command runs with MY_VAR in environment, YOUR PATH, YOUR python venv if any]"
echo ""

echo "✓ Basic usage examples complete!"
echo ""
echo "Key points:"
echo "- Default: Outputs shell command (use with eval)"
echo "- Runs in YOUR shell environment, not Python's"
echo "- Uses YOUR PATH, YOUR env vars, YOUR venv"
echo "- Add --execute to run as subprocess (not recommended)"
