# Context Store Tools — Persistent Knowledge Repository

**A layered, tree-structured knowledge store — initialize, build, query, and discover**

The context store tools give you a persistent, queryable knowledge repository that lives across conversations. Rather than repeatedly re-explaining a project to each tool call, you initialize a named store once, build it up in layers, and query it as many times as needed. Context accumulates in layers; queries branch into independent threads that can themselves be continued or layered further.

Four tools form the system: `palinit` creates named stores, `palstore` builds and extends them, `palquery` interrogates them (forking or continuing depending on target), and `pallist` discovers existing stores registered to a project directory.

## Thinking Mode

**Fixed at `max` for all context store tools.** Thinking mode is hard-coded and cannot be overridden. Every init, store, query, and list operation runs at maximum reasoning depth — the store's integrity depends on precise synthesis and citation, not speed.

---

## The store_id Path System

Every node in the store tree has a human-readable path that encodes its lineage. The `store_id` returned by each tool call is this path, not a UUID.

### Path Segments

| Segment | Meaning | Example |
|---------|---------|---------|
| `<name>` | Root store created by `palinit` | `myproject` |
| `.<name>.L<n>` | Layer `n` appended by `palstore` | `myproject.L1`, `myproject.L2` |
| `.<name>.Q<n>` | Query fork `n` branched from a store node | `myproject.L2.Q0` |
| `.<name>.Q<n>.<m>` | Follow-up `m` continued from a query node | `myproject.L2.Q0.1` |
| `.<name>.Q<n>.L<m>` | Layer `m` appended to a query node | `myproject.L2.Q0.L1` |

### Threading Model

The node type at the target `store_id` determines what `palstore` and `palquery` do:

| Operation | On store node | On query node |
|-----------|--------------|---------------|
| `palstore` | Append layer (same thread) | Append layer (same thread) |
| `palquery` | Fork to new thread | Continue same thread |

- **Append**: The new node joins the same conversation thread as the target. Full prior history is visible to the model.
- **Fork**: A new independent thread branches from the target checkpoint. The parent thread is never modified.
- **Continue**: The query extends the existing query thread. The model sees the full prior query exchange.

### Complete Path Example

```
palinit(directory="/path", store_name="myproject")
  → "myproject"

palstore(store_id="myproject", prompt="...")
  → "myproject.L1"

palstore(store_id="myproject.L1", prompt="...")
  → "myproject.L2"

palquery(store_id="myproject.L2", prompt="...")
  → "myproject.L2.Q0"         (fork: new thread branched from L2)

palquery(store_id="myproject.L2.Q0", prompt="...")
  → "myproject.L2.Q0.1"       (continue: extends the Q0 thread)

palquery(store_id="myproject.L2", prompt="...")
  → "myproject.L2.Q1"         (fork: second independent branch from L2)

palstore(store_id="myproject.L2.Q0.1", prompt="...")
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

## palinit — Create a Named Store

`palinit` creates a new named root store and registers it with a project directory. It does not contact any model — it is a pure registry operation. The resulting `store_id` is the store name you provide.

### Parameters

| Parameter | Required | Description |
|-----------|----------|-------------|
| `store_name` | Yes | Name for the new store. Used directly as the `store_id` root segment |
| `directory` | Yes | Absolute path to the project directory to associate with this store |

### Behavior

`palinit` validates that `store_name` is unique within the directory's registry, creates the root entry, and returns the `store_id`. No model call is made. The store is empty until the first `palstore` call.

Always check `pallist` before calling `palinit` — if a store already exists for your project, initialize from that instead.

### Response

The new `store_id` (equal to `store_name`) and a confirmation that the store was registered.

### Example

```
palinit(
  store_name="pal-mcp",
  directory="/home/user/dev/opt/pal-mcp-server"
)
→ store_id: "pal-mcp"
```

---

## palstore — Build and Extend the Store

`palstore` is the write path. Every call appends a new layer to an existing node. The `store_id` parameter is always required — use `palinit` to create the root first.

### Parameters

| Parameter | Required | Description |
|-----------|----------|-------------|
| `prompt` | Yes | Content or context to store in this layer |
| `store_id` | Yes | The node to extend. Must already exist (created by `palinit` or returned by a prior tool call) |
| `context_label` | No | Human-readable label for this layer (e.g., `"auth subsystem"`, `"migration plan"`) |
| `absolute_file_paths` | No | Files to include as additional context in this layer |
| `media` | No | Images to include in this layer (absolute paths) |
| `model` | No | Model to use (default: server default) |
| `temperature` | No | Response temperature, 0–1 (default: analytical) |

### Behavior

`palstore` loads the full prior history of the target node's thread and presents it to the model alongside the new layer prompt. The model synthesizes the new content against everything it already knows, updating its index of entities, decisions, relationships, and open questions.

The returned `store_id` is the new layer path (e.g., `"pal-mcp.L1"`). Pass this as `store_id` to the next `palstore` call to continue appending to the same thread.

Querying or layering on any node in the same thread gives the model the same accumulated history — the path encodes lineage, not a separate history.

### What to Include in the First Layer

The richer the first layer, the more powerful the store becomes:

- Project state, goals, and direction
- Architectural overview and key decisions already made
- Relevant files via `absolute_file_paths`
- Assumptions, constraints, and open questions
- Domain context, specs, or documentation that shapes the work

### Response

The model returns a synthesis of the stored layer: key entities, concepts, files, and decisions it has indexed, plus what topics are now queryable.

### Example

```
First layer — seed the store:
  palstore(
    store_id="pal-mcp",
    prompt="PAL MCP server: MCP protocol server connecting Claude/Gemini/Codex to external AI
            models. Provider registry pattern. Conversation memory: stateless MCP → stateful
            via continuation IDs. Current work: tree-based context path redesign.",
    context_label="project overview",
    absolute_file_paths=["/home/user/dev/opt/pal-mcp-server/server.py",
                         "/home/user/dev/opt/pal-mcp-server/utils/conversation_memory.py"]
  )
  → store_id: "pal-mcp.L1"

Second layer — deepen:
  palstore(
    store_id="pal-mcp.L1",
    prompt="Context store redesign: store_id is now a human-readable path. palinit creates
            roots. palquery forks on store nodes, continues on query nodes. palfork removed.",
    context_label="context path redesign"
  )
  → store_id: "pal-mcp.L2"
```

---

## palquery — Query the Store

`palquery` interrogates an existing node. Its behavior depends on the target node type:

- **On a store node** (`entry_type="store"`): creates a new independent fork thread. Returns a `.Q<n>` path.
- **On a query node** (`entry_type="query"`): continues the existing query thread. Returns a `.Q<n>.<m>` path.

This means querying is naturally exploratory. Multiple forks from the same store node are independent. Follow-up questions on a query node share that query's thread and see the full prior exchange.

### Parameters

| Parameter | Required | Description |
|-----------|----------|-------------|
| `prompt` | Yes | Question or query to run against the context store |
| `store_id` | Yes | The node to query |
| `model` | No | Model to use (default: server default) |
| `temperature` | No | Response temperature, 0–1 (default: analytical) |

### Behavior

`palquery` loads the full history of the target node's thread. The model answers strictly from stored material, citing layers by label or path. If the query cannot be answered from what is stored, the model says so explicitly rather than speculating.

**Fork path** (target is a store node): A new conversation thread is created branching from the target's checkpoint. The parent thread is never modified. The response includes the new `.Q<n>` path as the returned `store_id`.

**Continue path** (target is a query node): The query is appended to the existing query thread. The model sees the full prior query exchange. The response includes the new `.Q<n>.<m>` path.

### Response

The model's answer with layer citations in the form `"From [label / path]: ..."`. The returned `store_id` is the new node path.

### Examples

```
Fork from a store node:
  palquery(
    store_id="pal-mcp.L2",
    prompt="How does store_id map to the conversation thread system?"
  )
  → store_id: "pal-mcp.L2.Q0"
  → "From [context path redesign / pal-mcp.L2]: The store_id is now a
     human-readable path. palinit creates the root; each palstore appends
     .L<n> to the current path..."

Follow-up on the query thread:
  palquery(
    store_id="pal-mcp.L2.Q0",
    prompt="What determines whether palquery forks or continues?"
  )
  → store_id: "pal-mcp.L2.Q0.1"
  → "From [context path redesign / pal-mcp.L2]: The target node type
     determines behavior. Store nodes fork; query nodes continue..."

Second independent fork from the same store node:
  palquery(
    store_id="pal-mcp.L2",
    prompt="Which files were modified in the context path redesign?"
  )
  → store_id: "pal-mcp.L2.Q1"
```

---

## pallist — Discover Registered Stores

`pallist` reads the per-directory registry and returns all known stores, optionally filtered by project directory. It requires no model and makes no external API calls — it is a pure registry lookup.

Use `pallist` at the start of a session to find whether a store already exists for your current project before calling `palinit`.

### Parameters

| Parameter | Required | Description |
|-----------|----------|-------------|
| `directory` | No | Absolute path to filter stores by project directory. Omit to list all known stores |

### Behavior

`pallist` reads the registry and returns all matching entries in a tree view organized by root store name. Each entry shows its path, label, layer count, creation timestamp, and node type.

### Response

A tree-formatted list of store entries grouped by root name:

```
Found 2 root store(s) for /home/user/dev/opt/pal-mcp-server:

pal-mcp
├── [root]    created: 2026-03-17T10:00:00Z
├── L1        label: project overview      model: gemini-2.5-pro
├── L2        label: context path redesign  model: gemini-2.5-pro
│   ├── Q0    query fork
│   │   └── Q0.1  follow-up
│   └── Q1    query fork
└── ...
```

### Example

```
pallist(directory="/home/user/dev/opt/pal-mcp-server")
→ Returns all nodes registered under that project path.

pallist()
→ Returns all nodes across all projects.
```

---

## Workflow Examples

### Initialize and Build a Store

```
Start of project — initialize the store:
  palinit(
    store_name="pal-mcp",
    directory="/path/to/project"
  )
  → store_id: "pal-mcp"

Seed the first layer:
  palstore(
    store_id="pal-mcp",
    prompt="[Project overview, goals, architecture, open questions]",
    context_label="project overview",
    absolute_file_paths=["/path/to/project/server.py", ...]
  )
  → store_id: "pal-mcp.L1"

As work progresses — deepen the store:
  palstore(
    store_id="pal-mcp.L1",
    prompt="[Completed refactor details, new design decisions]",
    context_label="refactor complete"
  )
  → store_id: "pal-mcp.L2"
```

### Query and Follow Up

```
Fork a query thread from the latest layer:
  palquery(
    store_id="pal-mcp.L2",
    prompt="What was the root cause of the token overflow bug?"
  )
  → store_id: "pal-mcp.L2.Q0"
  → Cited answer; new fork thread created; parent L2 unchanged.

Ask a follow-up on the same query thread:
  palquery(
    store_id="pal-mcp.L2.Q0",
    prompt="Which files were affected by that fix?"
  )
  → store_id: "pal-mcp.L2.Q0.1"
  → Continues Q0's thread; model sees the prior exchange.
```

### Layer on a Query Thread

```
Build out a query thread into a longer investigation:
  palstore(
    store_id="pal-mcp.L2.Q0.1",
    prompt="[Detailed findings from the investigation, conclusions]",
    context_label="overflow investigation complete"
  )
  → store_id: "pal-mcp.L2.Q0.L1"
  → Appends a persistent layer to the query thread.
```

### Session Resume

```
Beginning a new session — check what already exists:
  pallist(directory="/path/to/project")
  → Found: "pal-mcp" with layers L1, L2 and query forks L2.Q0, L2.Q1

Resume at the latest layer:
  palstore(
    store_id="pal-mcp.L2",
    prompt="[New session context, picking up from last handoff]",
    context_label="session 4 pickup"
  )
  → store_id: "pal-mcp.L3"
```

---

## Registry

The registry is a flat, path-keyed index persisted to `$PAL_STORAGE_DIR/context/registry.json`. Every node created by any tool call has an entry. The registry survives server restarts.

Each entry records:

| Field | Description |
|-------|-------------|
| `store_id` | Full human-readable path for this node (e.g., `"pal-mcp.L2.Q0"`) |
| `root_name` | Root store name (e.g., `"pal-mcp"`) |
| `directory` | Absolute project path this node is registered under |
| `entry_type` | Node type: `"root"`, `"store"`, or `"query"` |
| `label` | Optional human-readable label for this layer |
| `model` | Model used when this node was created |
| `thread_id` | Internal conversation thread UUID backing this node |
| `parent_store_id` | Path of the parent node this was branched or appended from |
| `created_at` | UTC timestamp of node creation |

The registry is written atomically (temp file + rename) to prevent corruption on concurrent access. `pallist` reads directly from this index without touching conversation memory.
