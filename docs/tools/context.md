# Context Silo Tools - Persistent Knowledge Repository

**A malleable KV cache for accumulated context — store, query, fork, and discover**

The context silo tools give you a persistent, queryable knowledge repository that lives across conversations. Rather than repeatedly re-explaining a project to each tool call, you build a silo once and query it as many times as needed. Context accumulates in layers, each call deepening the repository's knowledge of your project without ever disturbing what came before.

Four tools form the system: `ctxstore` builds and extends the silo, `ctxquery` interrogates it without mutation, `ctxfork` branches it into an independent exploration thread, and `ctxlist` discovers existing stores registered to a project directory.

## Thinking Mode

**Fixed at `max` for all context silo tools.** Thinking mode is hard-coded and cannot be overridden. Every store, query, and fork operation runs at maximum reasoning depth — the silo's integrity depends on precise synthesis and citation, not speed.

## The Context Silo Concept

A context silo is a thread-backed knowledge store. Under the hood it maps directly to a PAL continuation thread — the `store_id` returned by `ctxstore` is a continuation ID that the conversation memory system uses to reconstruct the full accumulated history on every subsequent call.

What makes silos distinct from ordinary continuation threads is their layered accumulation model. Each `ctxstore` call adds a new layer to the silo. The AI model receives the full history of prior layers and synthesizes the new submission against them, tracking entities, decisions, relationships, and open questions. The silo grows richer with each layer without requiring the caller to re-supply previous content.

The silo's AI model operates under strict integrity constraints: it cites sources by layer, surfaces ambiguity rather than resolving it arbitrarily, and refuses to hallucinate beyond stored material. Querying a silo is a precision operation — you get exactly what was stored, attributed to the layer it came from.

```
First ctxstore call
  │  directory="/path/to/project"
  │  prompt="Project overview, goals, architecture..."
  ▼
  ┌─────────────────────────┐
  │  Silo created           │
  │  store_id: <uuid>       │◀── Register in per-directory registry
  └────────────┬────────────┘
               │
  Subsequent ctxstore calls (store_id=<uuid>)
               │  prompt="Authentication subsystem design..."
               ▼
  ┌─────────────────────────┐
  │  Layer 2 added          │
  │  Synthesized against    │
  │  prior layers           │
  └────────────┬────────────┘
               │
      ┌────────┴────────┐
      ▼                 ▼
  ctxquery          ctxfork
  (read-only)       (new branch)
```

## The store_id System

Every silo has a `store_id` — a UUID that identifies both the silo and its underlying conversation thread. This ID is returned on the first `ctxstore` call and must be passed back to all subsequent calls that interact with the same silo.

The `store_id` is also the key into the per-directory registry. When you call `ctxlist`, you are reading from this registry filtered by project directory. The registry persists to disk at `$PAL_STORAGE_DIR/context/stores.json` and survives server restarts.

## How the Tools Work Together

The four tools compose naturally into a workflow:

1. `ctxstore` — creates the silo and accumulates layers over time
2. `ctxquery` — reads from the silo without advancing its state
3. `ctxfork` — branches the silo at its current checkpoint for independent exploration
4. `ctxlist` — discovers existing silos registered to a project directory

A typical session: use `ctxlist` to find whether a silo already exists for your project. If none exists, use `ctxstore` to initialize one with a comprehensive first layer. Add more layers as the session progresses. Use `ctxquery` at any point to retrieve specific information without affecting what future stores will see. Use `ctxfork` when you want to explore an alternative path without modifying the canonical silo.

---

## ctxstore — Build and Extend the Silo

`ctxstore` is the write path. Every call adds a new layer to the silo. The first call creates the silo and registers it with the project directory; subsequent calls deepen it.

### Parameters

| Parameter | Required | Description |
|-----------|----------|-------------|
| `prompt` | Yes | Content or context to store in this layer |
| `store_id` | No | The silo to extend. Omit on the first call; required on all subsequent calls |
| `directory` | Conditional | Absolute path to the project directory. Required on the first call (when `store_id` is absent); ignored thereafter |
| `context_label` | No | Human-readable label for this layer (e.g., `"auth subsystem"`, `"migration plan"`) |
| `absolute_file_paths` | No | Files to include as additional context in this layer |
| `media` | No | Images to include in this layer (absolute paths) |
| `model` | No | Model to use (default: server default) |
| `temperature` | No | Response temperature, 0–1 (default: analytical) |

### Behavior

On the first call, `ctxstore` requires `directory` to associate the silo with a project. It creates a new conversation thread, stores the layer, and registers the resulting `store_id` in the per-directory registry. The response includes the `store_id` — save it.

On subsequent calls, `ctxstore` loads the full prior history through the conversation memory system and presents it to the model alongside the new layer. The model synthesizes the new content against what it already knows, updating its index of queryable topics. The registry entry's `turn_count` is incremented with each layer.

### What to Include in the First Layer

The richer the first layer, the more powerful the silo becomes. The model will guide you if the initial submission is sparse, but for best results include:

- Project state, goals, and direction
- Architectural overview and key decisions already made
- Relevant files via `absolute_file_paths`
- Assumptions, constraints, and open questions
- Domain context, specs, or documentation that shapes the work

### Response

The model returns a synthesis of the stored layer: key entities, concepts, files, and decisions it has indexed, plus what topics are now queryable. On the first call, the response also guides you on what additional context would strengthen the silo.

The response includes a `continuation_id` equal to the `store_id`. Use this as the `store_id` for all future calls to this silo.

### Example

```
First call — create the silo:
  ctxstore(
    prompt="PAL MCP server: FastAPI-based MCP protocol server connecting Claude/Gemini/Codex
            to external AI models. Provider registry pattern. Conversation memory is stateless
            MCP → stateful via continuation IDs. Current work: adding context silo tools.",
    directory="/home/user/dev/opt/pal-mcp-server",
    context_label="project overview"
  )
  → store_id: "a1b2c3d4-..."

Second call — add a layer:
  ctxstore(
    prompt="The four new tools: CtxStoreTool, CtxQueryTool, CtxForkTool, CtxListTool.
            All inherit from ContextBaseTool except CtxListTool which extends BaseTool directly.
            Registry persists to PAL_STORAGE_DIR/context/stores.json.",
    store_id="a1b2c3d4-...",
    context_label="context silo architecture"
  )
```

---

## ctxquery — Ephemeral Read-Only Query

`ctxquery` interrogates an existing silo without writing to it. The silo checkpoint is preserved exactly as it was before the query — no new layer is added, the `turn_count` does not increment, and the assistant turn is not recorded in the conversation thread.

This is the tool to reach for when you need a precise answer sourced from the silo but do not want that question and its answer to become part of the silo's permanent record. Run as many queries as needed; the silo remains unchanged.

### Parameters

| Parameter | Required | Description |
|-----------|----------|-------------|
| `prompt` | Yes | Question or query to run against the context silo |
| `store_id` | Yes | The silo to query |
| `model` | No | Model to use (default: server default) |
| `temperature` | No | Response temperature, 0–1 (default: analytical) |

### Behavior

`ctxquery` loads the full silo history as read-only context. The model answers strictly from stored material, citing layers by label or turn number. If the query cannot be answered from what is stored, the model says so explicitly rather than speculating from training knowledge.

The response carries back the same `store_id` as a `continuation_id` in the response envelope — confirming the silo checkpoint is unchanged and available for further queries or future store operations.

### Response

The model's answer, with layer citations in the form `"From [label / turn N]: ..."`. The response envelope includes a note confirming the silo checkpoint was preserved.

### Example

```
ctxquery(
  prompt="What is the current architecture for how store_id maps to the conversation thread system?",
  store_id="a1b2c3d4-..."
)
→ "From [context silo architecture / turn 2]: The store_id is a continuation ID
   in the conversation memory system. CtxStoreTool maps store_id to continuation_id
   before passing arguments to the parent SimpleTool.execute()..."
```

---

## ctxfork — Branch the Silo

`ctxfork` creates a new independent silo branched from an existing one at its current checkpoint. The parent silo is not modified. The fork starts with the full history of the parent as its inherited context, then adds the fork prompt as its first new turn. From that point the fork evolves independently.

Use `ctxfork` when you want to explore an alternative approach, test a hypothesis, or start a specialized sub-thread without polluting the canonical silo. The parent remains available and unchanged; the fork gets its own `store_id` and its own registry entry that records its lineage via `parent_store_id`.

### Parameters

| Parameter | Required | Description |
|-----------|----------|-------------|
| `prompt` | Yes | Query or instruction to start the fork with |
| `store_id` | Yes | The parent silo to fork from |
| `context_label` | No | Label for the forked silo |
| `absolute_file_paths` | No | Additional files to include in the fork's first turn |
| `media` | No | Images to include in the fork's first turn |
| `model` | No | Model to use (default: server default) |
| `temperature` | No | Response temperature, 0–1 (default: analytical) |

### Behavior

`ctxfork` creates a new conversation thread with the parent store's thread set as its `parent_thread_id`. The conversation memory system traverses this chain when reconstructing context, so the fork model sees the complete inherited history before responding to the fork prompt.

The fork is immediately registered in the per-directory registry under the same directory as the parent, with `parent_store_id` set. The response envelope includes both the new `store_id` (the fork's ID) and the `parent_store_id` for reference.

After forking, you can call `ctxstore` with the new `store_id` to continue building the fork independently, or `ctxquery` it, or fork it again.

### The Fork Chain

Forks can be nested. A fork of a fork is registered with its immediate parent's store_id. The conversation memory system walks the full `parent_thread_id` chain to reconstruct the complete inherited context at any depth.

```
Original silo
  store_id: A
       │
       ├── ctxfork ──▶ Fork 1
       │               store_id: B
       │               parent_store_id: A
       │                    │
       │                    └── ctxfork ──▶ Fork 1.1
       │                                    store_id: C
       │                                    parent_store_id: B
       │
       └── ctxfork ──▶ Fork 2
                        store_id: D
                        parent_store_id: A
```

### Response

The model's response to the fork prompt, answered in the context of the full inherited history. The response envelope identifies both the new fork's `store_id` and the parent's `store_id`, and includes a note confirming the parent silo is preserved.

### Example

```
ctxfork(
  prompt="Explore an alternative where ctxfork instead records its response into the parent thread
          as an ephemeral branch marker, rather than creating a fully independent thread.",
  store_id="a1b2c3d4-...",
  context_label="fork: ephemeral branch design"
)
→ continuation_id: "e5f6g7h8-..."  (new fork store_id)
→ parent_store_id: "a1b2c3d4-..."  (parent preserved)

Continue building the fork independently:
  ctxstore(
    prompt="Trade-offs of the ephemeral branch marker approach: simpler registry,
            but concurrent fork queries would interfere with each other...",
    store_id="e5f6g7h8-..."
  )
```

---

## ctxlist — Discover Registered Stores

`ctxlist` reads the per-directory registry and returns all known stores, optionally filtered by project directory. It requires no model and makes no external API calls — it is a pure registry lookup.

Use `ctxlist` at the start of a session to find whether a silo already exists for your current project before creating a new one.

### Parameters

| Parameter | Required | Description |
|-----------|----------|-------------|
| `directory` | No | Absolute path to filter stores by project directory. Omit to list all known stores |

### Behavior

`ctxlist` reads `$PAL_STORAGE_DIR/context/stores.json` and returns all matching entries. Each entry includes the `store_id`, label, model used, turn count, creation timestamp, and `parent_store_id` (for forks).

### Response

A formatted list of store entries:

```
Found 3 store(s):

- store_id: a1b2c3d4-...
  label: project overview
  model: gemini-2.5-pro
  turns: 4
  created: 2026-03-17T10:00:00Z

- store_id: e5f6g7h8-...
  label: fork: ephemeral branch design
  model: gemini-2.5-pro
  turns: 1
  created: 2026-03-17T11:30:00Z
  parent: a1b2c3d4-...
```

### Example

```
ctxlist(directory="/home/user/dev/opt/pal-mcp-server")
→ Returns all stores registered under that project path.

ctxlist()
→ Returns all stores across all projects.
```

---

## Workflow Examples

### Building Up Context Layers Across a Project

```
Session start — initialize the silo:
  ctxstore(
    prompt="[Project overview, goals, architecture, open questions]",
    directory="/path/to/project",
    context_label="project overview"
  )
  → store_id: "abc..."

As work progresses — deepen the silo:
  ctxstore(
    prompt="[Completed refactor details, new design decisions]",
    store_id="abc...",
    context_label="refactor complete",
    absolute_file_paths=["/path/to/project/utils/conversation_memory.py"]
  )

  ctxstore(
    prompt="[Bug investigation findings, root cause, fix applied]",
    store_id="abc...",
    context_label="bug fix: token overflow"
  )
```

### Querying Without Advancing State

```
Between work sessions — retrieve specific facts without touching the silo:
  ctxquery(
    prompt="What was the root cause of the token overflow bug?",
    store_id="abc..."
  )
  → Cited answer from stored layers, silo unchanged.

  ctxquery(
    prompt="Which files were modified in the refactor?",
    store_id="abc..."
  )
  → Cited answer from stored layers, silo still unchanged.
```

### Forking to Explore Alternatives

```
Exploring a design alternative without committing to it:
  ctxfork(
    prompt="Design the token budgeting system using a sliding window instead
            of the current newest-first prioritization approach.",
    store_id="abc...",
    context_label="alt: sliding window budget"
  )
  → new store_id: "def..."

Build out the alternative independently:
  ctxstore(
    prompt="[Sliding window trade-offs, implementation sketch]",
    store_id="def..."
  )

Parent silo "abc..." remains at its prior checkpoint — unaffected.
```

### Discovering Stores at Session Start

```
Beginning a new session — check what already exists:
  ctxlist(directory="/path/to/project")
  → Found 2 store(s): "abc..." (main silo, 6 turns) and "def..." (fork, 2 turns)

Resume the main silo:
  ctxstore(
    prompt="[New session context, picking up from last handoff]",
    store_id="abc...",
    context_label="session 4 pickup"
  )
```

---

## Ephemeral vs Persistent Operations

The distinction between ephemeral and persistent operations is the most important behavioral fact about this tool family.

**Persistent operations** (write to the silo):
- `ctxstore` — adds a layer, records both user and assistant turns in the conversation thread, increments `turn_count`
- `ctxfork` — creates a new thread and writes the fork's first response into it (the parent thread is never touched)

**Ephemeral operations** (read without mutation):
- `ctxquery` — loads the full silo history, answers the query, discards the interaction; the conversation thread is not modified, `turn_count` does not change, the response is not stored

This means you can run unlimited `ctxquery` calls against a silo checkpoint and it will look identical to `ctxlist` every time — same `turn_count`, same content. The silo advances only when you call `ctxstore`.

---

## Per-Directory Registry

Every silo is registered in a per-directory index that maps project paths to store entries. This registry is what `ctxlist` reads. It persists to `$PAL_STORAGE_DIR/context/stores.json` and survives server restarts.

Registry entries record:
- `store_id` — the continuation thread UUID
- `label` — optional human-readable name
- `model` — the model used when the store was created
- `turn_count` — number of `ctxstore` calls that have written to this silo (does not include `ctxquery` calls)
- `created_at` — UTC timestamp of silo creation
- `parent_store_id` — present only on forked silos; points to the parent silo's store_id

The registry is written atomically (temp file + rename) to prevent corruption on concurrent access.

---

## When to Use Context Silos vs Other Tools

- **Use context silos** for: Accumulating project knowledge across sessions, creating a queryable knowledge base, exploring design alternatives without polluting the main context, sharing a stable context snapshot across multiple parallel agents
- **Use `chat`** for: Open-ended collaborative discussions where you want the conversation to evolve freely in both directions
- **Use `analyze`** for: Investigating code structure without needing to persist findings for later querying
- **Use `thinkdeep`** for: Deep reasoning on a specific question using your current session's context rather than a persistent silo
- **Use continuation threads directly** for: Sequential multi-turn conversations with a model where accumulation and citation are not required
