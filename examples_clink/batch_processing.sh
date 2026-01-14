#!/usr/bin/env bash
# Batch processing examples

# Create sample batch file
cat > examples_clink/prompts.jsonl << 'EOF'
{"prompt": "Explain Python list comprehensions", "files": []}
{"prompt": "Show me 3 examples of list comprehensions", "files": []}
{"prompt": "What are the performance implications?", "files": []}
EOF

echo "Created examples_clink/prompts.jsonl"

# Execute batch (independent prompts)
echo -e "\nExecuting batch (independent prompts)..."
uv run clink_invoke.py claude-zai --batch examples_clink/prompts.jsonl --role agency

# Chain batch (each continues from previous)
echo -e "\nExecuting batch (chained conversation)..."
uv run clink_invoke.py claude-zai --batch examples_clink/prompts.jsonl --chain

# Batch with custom output file
echo -e "\nExecuting batch with custom output..."
uv run clink_invoke.py claude-zai \
  --batch examples_clink/prompts.jsonl \
  --output-file examples_clink/results.json

echo -e "\nResults saved to examples_clink/results.json"

# Create batch with file references
cat > examples_clink/code_review.jsonl << 'EOF'
{"prompt": "Review this module", "files": ["clink/agents/base.py"]}
{"prompt": "Find potential bugs", "files": ["clink/agents/claude.py"]}
{"prompt": "Suggest improvements", "files": ["clink/registry.py"]}
EOF

echo -e "\nCreated examples_clink/code_review.jsonl"

# Execute code review batch
echo -e "\nExecuting code review batch..."
uv run clink_invoke.py claude-zai \
  --batch examples_clink/code_review.jsonl \
  --role agency \
  --chain
