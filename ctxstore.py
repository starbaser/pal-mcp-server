# PAL Context Store REPL — paste into a Python REPL
# Full filesystem access, no LLM calls

import sys, os, json, tempfile, re
from datetime import datetime, timezone
from typing import Literal
from pydantic import BaseModel

# --- Storage config ---
PAL_STORAGE_DIR = os.environ.get("PAL_STORAGE_DIR") or os.path.join(
    os.environ.get("CLAUDE_CONFIG_DIR", os.path.expanduser("~/.claude")), "pal"
)
CTX_DIR = os.path.join(PAL_STORAGE_DIR, "context")
INDEX_PATH = os.path.join(CTX_DIR, "store-index.json")
ARMED_PATH = os.path.join(CTX_DIR, "armed.json")

# --- Data models ---
class StoreNode(BaseModel):
    entry_type: Literal["store", "query", "tool", "fork"]
    label: str | None = None
    timestamp: str
    model: str | None = None
    files: list[str] = []
    prompt: str = ""
    response: str = ""
    content: str = ""
    tool_name: str | None = None
    children: dict[str, "StoreNode"] = {}

class StoreRoot(BaseModel):
    store_id: str
    directory: str
    label: str | None = None
    created_at: str
    children: dict[str, StoreNode] = {}

# --- Atomic write ---
def _atomic_write(path, data):
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    payload = data.model_dump() if isinstance(data, BaseModel) else data
    with tempfile.NamedTemporaryFile(mode="w", dir=d, delete=False, suffix=".tmp") as tmp:
        json.dump(payload, tmp, indent=2)
        tmp_path = tmp.name
    os.rename(tmp_path, path)

# --- Path helpers ---
def encode_directory(p):
    return p.replace("/", "-").replace(".", "-")

def get_store_dir(directory):
    return os.path.join(CTX_DIR, encode_directory(directory))

def get_store_path(directory, store_id):
    return os.path.join(get_store_dir(directory), f"{store_id}.json")

# --- Index ---
def load_index():
    if not os.path.exists(INDEX_PATH):
        os.makedirs(CTX_DIR, exist_ok=True)
        return {"directories": {}, "stores": {}}
    try:
        with open(INDEX_PATH) as f:
            data = json.load(f)
        data.setdefault("directories", {})
        data.setdefault("stores", {})
        return data
    except (json.JSONDecodeError, OSError):
        return {"directories": {}, "stores": {}}

def save_index(data):
    _atomic_write(INDEX_PATH, data)

def update_index(directory, store_id):
    index = load_index()
    index["directories"][directory] = encode_directory(directory)
    index["stores"][store_id] = encode_directory(directory)
    save_index(index)

# --- Store I/O ---
def load_store(directory, store_id):
    path = get_store_path(directory, store_id)
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return StoreRoot.model_validate(json.load(f))

def save_store(store):
    _atomic_write(get_store_path(store.directory, store.store_id), store)

# --- Parse / resolve ---
def parse_store_path(store_id):
    if "." not in store_id:
        return store_id, []
    index = load_index()
    parts = store_id.split(".")
    for i in range(len(parts), 0, -1):
        candidate = ".".join(parts[:i])
        if candidate in index["stores"]:
            return candidate, parts[i:]
    return parts[0], parts[1:]

def resolve_store_location(store_id):
    root, _ = parse_store_path(store_id)
    index = load_index()
    encoded = index["stores"].get(root)
    if encoded:
        directory = next((d for d, e in index["directories"].items() if e == encoded), None)
        if directory and os.path.exists(get_store_path(directory, root)):
            return directory, root
    return None

# --- Tree navigation ---
def resolve_node(store, store_id):
    _, segments = parse_store_path(store_id)
    if not segments:
        return None
    current = store.children
    node = None
    for seg in segments:
        node = current.get(seg)
        if node is None:
            return None
        current = node.children
    return node

def walk_ancestry(store, store_id):
    _, segments = parse_store_path(store_id)
    if not segments:
        return []
    ancestry = []
    current = store.children
    for seg in segments:
        node = current.get(seg)
        if node is None:
            break
        ancestry.append(node)
        current = node.children
    return ancestry

def get_next_key(store, parent_path, prefix):
    if parent_path == store.store_id:
        siblings = store.children
    else:
        parent = resolve_node(store, parent_path)
        siblings = parent.children if parent else {}
    if prefix == "":
        nums = [int(k) for k in siblings if re.fullmatch(r"\d+", k)]
        return str(max(nums) + 1) if nums else "1"
    if prefix in ("L", "Q", "F"):
        start = 1 if prefix == "L" else 0
        pat = re.compile(rf"^{re.escape(prefix)}(\d+)$")
        indices = [int(m.group(1)) for k in siblings if (m := pat.match(k))]
        return f"{prefix}{max(indices) + 1}" if indices else f"{prefix}{start}"
    raise ValueError(f"Unsupported prefix: {prefix!r}")

def add_child(store, parent_path, child_key, node):
    if parent_path == store.store_id:
        store.children[child_key] = node
    else:
        parent = resolve_node(store, parent_path)
        if parent is None:
            raise KeyError(f"parent not found: {parent_path}")
        parent.children[child_key] = node
    return f"{parent_path}.{child_key}"

# --- Listing ---
def list_stores(directory=None):
    if directory is not None:
        folder = get_store_dir(directory)
        if not os.path.isdir(folder):
            return []
        roots = []
        for e in os.scandir(folder):
            if e.name.endswith(".json"):
                s = load_store(directory, e.name[:-5])
                if s:
                    roots.append(s)
        return roots
    results = []
    if not os.path.isdir(CTX_DIR):
        return results
    for de in os.scandir(CTX_DIR):
        if not de.is_dir():
            continue
        for fe in os.scandir(de.path):
            if not fe.name.endswith(".json"):
                continue
            try:
                with open(fe.path) as f:
                    results.append(StoreRoot.model_validate(json.load(f)))
            except Exception:
                continue
    return results

def list_all_directories():
    index = load_index()
    return sorted(index["directories"].keys())

# --- Armed state ---
def load_armed():
    if not os.path.exists(ARMED_PATH):
        return {}
    try:
        with open(ARMED_PATH) as f:
            return json.load(f)
    except Exception:
        return {}

def arm_store(directory, store_id):
    armed = load_armed()
    armed[directory] = store_id
    _atomic_write(ARMED_PATH, armed)

def disarm_store(directory):
    armed = load_armed()
    armed.pop(directory, None)
    _atomic_write(ARMED_PATH, armed)

# --- Context reconstruction (no LLM) ---
def build_context_from_ancestry(ancestors, include_files=True):
    nodes = [n for n in ancestors if n.entry_type != "fork" and n.prompt and n.response]
    if not nodes:
        return ""
    parts = ["=== CONVERSATION HISTORY ===", ""]
    for i, node in enumerate(nodes, 1):
        user_turn = node.content.split("\n\n---\n\n", 1)[0] if node.content else node.prompt
        parts += [f"--- Turn {i} (user) ---", user_turn, "", f"--- Turn {i} (assistant) ---", node.response, ""]
    if include_files:
        files = []
        for n in nodes:
            for f in (n.files or []):
                if f not in files:
                    files.append(f)
        if files:
            parts.append("=== FILES REFERENCED IN THIS CONVERSATION ===")
            parts += [f"  {f}" for f in files]
            parts.append("")
    parts.append("=== END CONVERSATION HISTORY ===")
    return "\n".join(parts)

# --- Convenience: create a new store ---
def create_store(store_id, directory, label=None):
    if "." in store_id:
        raise ValueError("Store names cannot contain dots")
    store = StoreRoot(
        store_id=store_id,
        directory=directory,
        label=label,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    save_store(store)
    update_index(directory, store_id)
    return store

# --- Convenience: add a layer ---
def add_layer(store_id, prompt, response, files=None, label=None, model=None):
    loc = resolve_store_location(store_id)
    if loc is None:
        raise KeyError(f"Store not found: {store_id}")
    directory, root_id = loc
    store = load_store(directory, root_id)
    now = datetime.now(timezone.utc).isoformat()
    file_blobs = ""
    if files:
        parts = ["=== CONTEXT FILES ==="]
        for fp in files:
            try:
                with open(fp) as f:
                    content = f.read()
                parts.append(f"--- BEGIN FILE: {fp} ---")
                parts.append(content)
                parts.append(f"--- END FILE: {fp} ---")
            except OSError as e:
                parts.append(f"--- BEGIN FILE: {fp} (ERROR: {e}) ---")
                parts.append(f"--- END FILE: {fp} ---")
        file_blobs = "\n".join(parts) + "\n\n"
    full_content = f"{file_blobs}{prompt}\n\n---\n\n{response}"
    node = StoreNode(
        entry_type="store", label=label, timestamp=now, model=model,
        files=files or [], prompt=prompt, response=response, content=full_content,
    )
    key = get_next_key(store, store_id, "L")
    full_path = add_child(store, store_id, key, node)
    save_store(store)
    return full_path

# --- Convenience: add a fork ---
def add_fork(store_id, label=None):
    loc = resolve_store_location(store_id)
    if loc is None:
        raise KeyError(f"Store not found: {store_id}")
    directory, root_id = loc
    store = load_store(directory, root_id)
    node = StoreNode(
        entry_type="fork", label=label,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )
    key = get_next_key(store, store_id, "F")
    full_path = add_child(store, store_id, key, node)
    save_store(store)
    return full_path

# --- Pretty print tree ---
def print_tree(store_id=None, directory=None):
    if store_id:
        loc = resolve_store_location(store_id)
        if not loc:
            print(f"Store not found: {store_id}")
            return
        directory, root_id = loc
        stores = [load_store(directory, root_id)]
    else:
        stores = list_stores(directory)
    for store in stores:
        if store is None:
            continue
        print(f"■ {store.store_id}  ({store.directory})")
        if store.label:
            print(f"  label: {store.label}")
        print(f"  created: {store.created_at}")
        _print_children(store.children, indent=2)
        print()

def _print_children(children, indent):
    for key, node in children.items():
        typ = {"store": "L", "query": "Q", "fork": "F", "tool": "T"}.get(node.entry_type, "?")
        lbl = f' "{node.label}"' if node.label else ""
        mdl = f" [{node.model}]" if node.model else ""
        nf = f" ({len(node.files)} files)" if node.files else ""
        plen = len(node.prompt)
        rlen = len(node.response)
        sz = f" p:{plen} r:{rlen}" if plen or rlen else ""
        print(f"{' ' * indent}├─ {key} ({typ}){lbl}{mdl}{nf}{sz}")
        if node.children:
            _print_children(node.children, indent + 4)

# --- Ready ---
print(f"CTX_DIR: {CTX_DIR}")
print(f"Stores: {len(list_stores())}")
print(f"Directories: {list_all_directories()}")
print()
print("Functions: create_store, load_store, save_store, add_layer, add_fork,")
print("  resolve_store_location, resolve_node, walk_ancestry, build_context_from_ancestry,")
print("  list_stores, list_all_directories, print_tree, arm_store, disarm_store, load_armed")
