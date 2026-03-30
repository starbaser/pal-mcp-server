# PALTree Tools — Persistent Knowledge Repository

**A layered, tree-structured knowledge store — initialize, build, query, and discover**

The PALTree tools give you a persistent, queryable knowledge repository that lives across conversations. Rather than repeatedly re-explaining a project to each tool call, you initialize a named tree once, build it up in layers, and query it as many times as needed. Context accumulates in layers; queries branch into independent threads that can themselves be continued or layered further.

Four tools form the system: `palinit` creates named trees, `palstore` builds and extends them, `palquery` interrogates them (forking or continuing depending on target), and `pallist` discovers existing trees registered to a project directory.

## Thinking Mode

**Fixed at `max` for all PALTree tools.** Thinking mode is hard-coded and cannot be overridden. Every init, store, query, and list operation runs at maximum reasoning depth — the tree's integrity depends on precise synthesis and citation, not speed.

---

## The tree_path System

Every PALNode in the tree has a human-readable path that encodes its lineage. The `tree_path` returned by each tool call is this path, not a UUID.

### Path Segments

| Segment | Meaning | Example |
|---------|---------|---------|
| `<name>` | Root tree created by `palinit` | `myproject` |
| `.<name>.L<n>` | Layer `n` appended by `palstore` | `myproject.L1`, `myproject.L2` |
| `.<name>.Q<n>` | Query fork `n` branched from a store node | `myproject.L2.Q0` |
| `.<name>.Q<n>.<m>` | Follow-up `m` continued from a query node | `myproject.L2.Q0.1` |
| `.<name>.Q<n>.L<m>` | Layer `m` appended to a query node | `myproject.L2.Q0.L1` |

### Threading Model

The PALNode type at the target `tree_path` determines what `palstore` and `palquery` do:

| Operation | On store node | On query node |
|-----------|--------------|---------------|
| `palstore` | Append layer (same thread) | Append layer (same thread) |
| `palquery` | Fork to new thread | Continue same thread |

- **Append**: The new PALNode joins the same conversation thread as the target. Full prior history is visible to the model.
- **Fork**: A new independent thread branches from the target checkpoint. The parent thread is never modified.
- **Continue**: The query extends the existing query thread. The model sees the full prior query exchange.

### Complete Path Example

```
palinit(directory="/path", store_name="myproject")
  → "myproject"

palstore(tree_path="myproject", prompt="...")
  → "myproject.L1"

palstore(tree_path="myproject.L1", prompt="...")
  → "myproject.L2"

palquery(tree_path="myproject.L2", prompt="...")
  → "myproject.L2.Q0"         (fork: new thread branched from L2)

palquery(tree_path="myproject.L2.Q0", prompt="...")
  → "myproject.L2.Q0.1"       (continue: extends the Q0 thread)

palquery(tree_path="myproject.L2", prompt="...")
  → "myproject.L2.Q1"         (fork: second independent branch from L2)

palstore(tree_path="myproject.L2.Q0.1", prompt="...")
  → "myproject.L2.Q0.L1"      (layer appended to query thread Q0.1)
```

The full tree at this point:

```
myproject
└── L1
    └── L2
        ├── Q0
        │   └── Q0.1
        │       └── Q0.L1
        └── Q1
```

---

## palinit — Create a Named Tree

`palinit` creates a new named root tree and registers it with a project directory. It does not contact any model — it is a pure registry operation. The resulting `tree_path` is the tree name you provide.

### Parameters

| Parameter | Required | Description |
|-----------|----------|-------------|
| `store_name` | Yes | Name for the new tree. Used directly as the `tree_path` root segment |
| `directory` | Yes | Absolute path to the project directory to associate with this tree |

### Behavior

`palinit` validates that `store_name` is unique within the directory's registry, creates the root entry, and returns the `tree_path`. No model call is made. The tree is empty until the first `palstore` call.

Always check `pallist` before calling `palinit` — if a tree already exists for your project, initialize from that instead.

### Response

The new `tree_path` (equal to `store_name`) and a confirmation that the tree was registered.

### Example

```
palinit(
  store_name="pal-mcp",
  directory="/home/user/dev/opt/pal-mcp-server"
)
→ tree_path: "pal-mcp"
```

---

## palstore — Build and Extend the Tree

`palstore` is the write path. Every call appends a new layer to an existing PALNode. The `tree_path` parameter is always required — use `palinit` to create the root first.

### Parameters

| Parameter | Required | Description |
|-----------|----------|-------------|
| `prompt` | Yes | Content or context to store in this layer |
| `tree_path` | Yes | The PALNode to extend. Must already exist (created by `palinit` or returned by a prior tool call) |
| `context_label` | No | Human-readable label for this layer (e.g., `"auth subsystem"`, `"migration plan"`) |
| `absolute_file_paths` | No | Files to include as additional context in this layer |
| `media` | No | Images to include in this layer (absolute paths) |
| `model` | No | Model to use (default: server default) |
| `temperature` | No | Response temperature, 0–1 (default: analytical) |

### Behavior

`palstore` loads the full prior history of the target PALNode's thread and presents it to the model alongside the new layer prompt. The model synthesizes the new content against everything it already knows, updating its index of entities, decisions, relationships, and open questions.

The returned `tree_path` is the new layer path (e.g., `"pal-mcp.L1"`). Pass this as `tree_path` to the next `palstore` call to continue appending to the same thread.

Querying or layering on any PALNode in the same thread gives the model the same accumulated history — the path encodes lineage, not a separate history.

### What to Include in the First Layer

The richer the first layer, the more powerful the tree becomes:

- Project state, goals, and direction
- Architectural overview and key decisions already made
- Relevant files via `absolute_file_paths`
- Assumptions, constraints, and open questions
- Domain context, specs, or documentation that shapes the work

### Response

The model returns a synthesis of the stored layer: key entities, concepts, files, and decisions it has indexed, plus what topics are now queryable.

### Example

```
First layer — seed the tree:
  palstore(
    tree_path="pal-mcp",
    prompt="PAL MCP server: MCP protocol server connecting Claude/Gemini/Codex to external AI
            models. Provider registry pattern. Conversation memory: stateless MCP → stateful
            via continuation IDs. Current work: tree-based context path redesign.",
    context_label="project overview",
    absolute_file_paths=["/home/user/dev/opt/pal-mcp-server/server.py",
                         "/home/user/dev/opt/pal-mcp-server/utils/conversation_memory.py"]
  )
  → tree_path: "pal-mcp.L1"

Second layer — deepen:
  palstore(
    tree_path="pal-mcp.L1",
    prompt="PALTree redesign: tree_path is now a human-readable path. palinit creates
            roots. palquery forks on store nodes, continues on query nodes. palfork removed.",
    context_label="PALTree path redesign"
  )
  → tree_path: "pal-mcp.L2"
```

---

## palquery — Query the Tree

`palquery` interrogates an existing PALNode. Its behavior depends on the target PALNode type:

- **On a store node** (`entry_type="store"`): creates a new independent fork thread. Returns a `.Q<n>` path.
- **On a query node** (`entry_type="query"`): continues the existing query thread. Returns a `.Q<n>.<m>` path.

This means querying is naturally exploratory. Multiple forks from the same store PALNode are independent. Follow-up questions on a query PALNode share that query's thread and see the full prior exchange.

### Parameters

| Parameter | Required | Description |
|-----------|----------|-------------|
| `prompt` | Yes | Question or query to run against the PALTree |
| `tree_path` | Yes | The PALNode to query |
| `model` | No | Model to use (default: server default) |
| `temperature` | No | Response temperature, 0–1 (default: analytical) |

### Behavior

`palquery` loads the full history of the target PALNode's thread. The model answers strictly from stored material, citing layers by label or path. If the query cannot be answered from what is stored, the model says so explicitly rather than speculating.

**Fork path** (target is a store node): A new conversation thread is created branching from the target's checkpoint. The parent thread is never modified. The response includes the new `.Q<n>` path as the returned `tree_path`.

**Continue path** (target is a query node): The query is appended to the existing query thread. The model sees the full prior query exchange. The response includes the new `.Q<n>.<m>` path.

### Response

The model's answer with layer citations in the form `"From [label / path]: ..."`. The returned `tree_path` is the new PALNode path.

### Examples

```
Fork from a store node:
  palquery(
    tree_path="pal-mcp.L2",
    prompt="How does tree_path map to the conversation thread system?"
  )
  → tree_path: "pal-mcp.L2.Q0"
  → "From [PALTree path redesign / pal-mcp.L2]: The tree_path is now a
     human-readable path. palinit creates the root; each palstore appends
     .L<n> to the current path..."

Follow-up on the query thread:
  palquery(
    tree_path="pal-mcp.L2.Q0",
    prompt="What determines whether palquery forks or continues?"
  )
  → tree_path: "pal-mcp.L2.Q0.1"
  → "From [PALTree path redesign / pal-mcp.L2]: The target PALNode type
     determines behavior. Store nodes fork; query nodes continue..."

Second independent fork from the same store node:
  palquery(
    tree_path="pal-mcp.L2",
    prompt="Which files were modified in the PALTree path redesign?"
  )
  → tree_path: "pal-mcp.L2.Q1"
```

---

## pallist — Discover Registered Trees

`pallist` reads the per-directory registry and returns all known trees, optionally filtered by project directory. It requires no model and makes no external API calls — it is a pure registry lookup.

Use `pallist` at the start of a session to find whether a tree already exists for your current project before calling `palinit`.

### Parameters

| Parameter | Required | Description |
|-----------|----------|-------------|
| `directory` | No | Absolute path to filter trees by project directory. Omit to list all known trees |

### Behavior

`pallist` reads the registry and returns all matching entries in a tree view organized by root tree name. Each entry shows its path, label, layer count, creation timestamp, and PALNode type.

### Response

A tree-formatted list of PALNode entries grouped by root name:

```
Found 2 root tree(s) for /home/user/dev/opt/pal-mcp-server:

pal-mcp
├── [root]    created: 2026-03-17T10:00:00Z
├── L1        label: project overview      model: gemini-2.5-pro
├── L2        label: PALTree path redesign  model: gemini-2.5-pro
│   ├── Q0    query fork
│   │   └── Q0.1  follow-up
│   └── Q1    query fork
└── ...
```

### Example

```
pallist(directory="/home/user/dev/opt/pal-mcp-server")
→ Returns all PALNodes registered under that project path.

pallist()
→ Returns all PALNodes across all projects.
```

---

## Workflow Examples

### Initialize and Build a Tree

```
Start of project — initialize the tree:
  palinit(
    store_name="pal-mcp",
    directory="/path/to/project"
  )
  → tree_path: "pal-mcp"

Seed the first layer:
  palstore(
    tree_path="pal-mcp",
    prompt="[Project overview, goals, architecture, open questions]",
    context_label="project overview",
    absolute_file_paths=["/path/to/project/server.py", ...]
  )
  → tree_path: "pal-mcp.L1"

As work progresses — deepen the tree:
  palstore(
    tree_path="pal-mcp.L1",
    prompt="[Completed refactor details, new design decisions]",
    context_label="refactor complete"
  )
  → tree_path: "pal-mcp.L2"
```

### Query and Follow Up

```
Fork a query thread from the latest layer:
  palquery(
    tree_path="pal-mcp.L2",
    prompt="What was the root cause of the token overflow bug?"
  )
  → tree_path: "pal-mcp.L2.Q0"
  → Cited answer; new fork thread created; parent L2 unchanged.

Ask a follow-up on the same query thread:
  palquery(
    tree_path="pal-mcp.L2.Q0",
    prompt="Which files were affected by that fix?"
  )
  → tree_path: "pal-mcp.L2.Q0.1"
  → Continues Q0's thread; model sees the prior exchange.
```

### Layer on a Query Thread

```
Build out a query thread into a longer investigation:
  palstore(
    tree_path="pal-mcp.L2.Q0.1",
    prompt="[Detailed findings from the investigation, conclusions]",
    context_label="overflow investigation complete"
  )
  → tree_path: "pal-mcp.L2.Q0.L1"
  → Appends a persistent layer to the query thread.
```

### Session Resume

```
Beginning a new session — check what already exists:
  pallist(directory="/path/to/project")
  → Found: "pal-mcp" with layers L1, L2 and query forks L2.Q0, L2.Q1

Resume at the latest layer:
  palstore(
    tree_path="pal-mcp.L2",
    prompt="[New session context, picking up from last handoff]",
    context_label="session 4 pickup"
  )
  → tree_path: "pal-mcp.L3"
```

---

## Registry

The registry is a flat, path-keyed index persisted to `$PAL_STORAGE_DIR/context/registry.json`. Every PALNode created by any tool call has an entry. The registry survives server restarts.

Each entry records:

| Field | Description |
|-------|-------------|
| `tree_path` | Full human-readable path for this PALNode (e.g., `"pal-mcp.L2.Q0"`) |
| `root_name` | Root tree name (e.g., `"pal-mcp"`) |
| `directory` | Absolute project path this PALNode is registered under |
| `entry_type` | PALNode type: `"root"`, `"store"`, or `"query"` |
| `label` | Optional human-readable label for this layer |
| `model` | Model used when this PALNode was created |
| `thread_id` | Internal conversation thread UUID backing this PALNode |
| `parent_tree_path` | Path of the parent PALNode this was branched or appended from |
| `created_at` | UTC timestamp of PALNode creation |

The registry is written atomically (temp file + rename) to prevent corruption on concurrent access. `pallist` reads directly from this index without touching conversation memory.
