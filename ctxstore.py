# PAL Context Store — IPython interface
# %run ctxstore.py  or  exec(open("ctxstore.py").read())

import json
import os
import re
import tempfile
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel

# ── Storage config ──────────────────────────────────────────────────────────
PAL_STORAGE_DIR = os.environ.get("PAL_STORAGE_DIR") or os.path.join(
    os.environ.get("CLAUDE_CONFIG_DIR", os.path.expanduser("~/.claude")), "pal"
)
CTX_DIR = os.path.join(PAL_STORAGE_DIR, "context")
INDEX_PATH = os.path.join(CTX_DIR, "store-index.json")
ARMED_PATH = os.path.join(CTX_DIR, "armed.json")


# ── Data models ─────────────────────────────────────────────────────────────
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


# ── Internals ───────────────────────────────────────────────────────────────
def _atomic_write(path, data):
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    payload = data.model_dump() if isinstance(data, BaseModel) else data
    with tempfile.NamedTemporaryFile(mode="w", dir=d, delete=False, suffix=".tmp") as tmp:
        json.dump(payload, tmp, indent=2)
        tmp_path = tmp.name
    os.rename(tmp_path, path)


def _encode_directory(p):
    return p.replace("/", "-").replace(".", "-")


def _get_store_dir(directory):
    return os.path.join(CTX_DIR, _encode_directory(directory))


def _get_store_path(directory, store_id):
    return os.path.join(_get_store_dir(directory), f"{store_id}.json")


# ── Index ───────────────────────────────────────────────────────────────────
def _load_index():
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


def _save_index(data):
    _atomic_write(INDEX_PATH, data)


def _update_index(directory, store_id):
    index = _load_index()
    index["directories"][directory] = _encode_directory(directory)
    index["stores"][store_id] = _encode_directory(directory)
    _save_index(index)


# ── Store I/O ───────────────────────────────────────────────────────────────
def _load_store(directory, store_id):
    path = _get_store_path(directory, store_id)
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return StoreRoot.model_validate(json.load(f))


def _save_store(store):
    _atomic_write(_get_store_path(store.directory, store.store_id), store)


# ── Parse / resolve ─────────────────────────────────────────────────────────
def _parse_store_path(store_id):
    if "." not in store_id:
        return store_id, []
    index = _load_index()
    parts = store_id.split(".")
    for i in range(len(parts), 0, -1):
        candidate = ".".join(parts[:i])
        if candidate in index["stores"]:
            return candidate, parts[i:]
    return parts[0], parts[1:]


def _resolve_store_location(store_id):
    root, _ = _parse_store_path(store_id)
    index = _load_index()
    encoded = index["stores"].get(root)
    if encoded:
        directory = next((d for d, e in index["directories"].items() if e == encoded), None)
        if directory and os.path.exists(_get_store_path(directory, root)):
            return directory, root
    return None


# ── Tree navigation ─────────────────────────────────────────────────────────
def _resolve_node(store, store_id):
    _, segments = _parse_store_path(store_id)
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


def _walk_ancestry(store, store_id):
    _, segments = _parse_store_path(store_id)
    if not segments:
        return []
    ancestry = []
    current = store.children
    for seg in segments:
        node = current.get(seg)
        if node is None:
            break
        if seg[0] == "L" and seg[1:].isdigit():
            target_idx = int(seg[1:])
            preceding = [
                (int(k[1:]), v)
                for k, v in current.items()
                if k[0] == "L" and k[1:].isdigit() and int(k[1:]) < target_idx
            ]
            preceding.sort(key=lambda kv: kv[0])
            ancestry.extend(v for _, v in preceding)
        ancestry.append(node)
        current = node.children
    return ancestry


def _get_last_layer_path(store):
    count = sum(1 for k in store.children if k.startswith("L") and k[1:].isdigit())
    if not count:
        return None
    return f"{store.store_id}.L{count}"


def _get_next_key(store, parent_path, prefix):
    if parent_path == store.store_id:
        siblings = store.children
    else:
        parent = _resolve_node(store, parent_path)
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


def _add_child(store, parent_path, child_key, node):
    if parent_path == store.store_id:
        store.children[child_key] = node
    else:
        parent = _resolve_node(store, parent_path)
        if parent is None:
            raise KeyError(f"parent not found: {parent_path}")
        parent.children[child_key] = node
    return f"{parent_path}.{child_key}"


# ── Listing ─────────────────────────────────────────────────────────────────
def _list_stores(directory=None):
    if directory is not None:
        folder = _get_store_dir(directory)
        if not os.path.isdir(folder):
            return []
        roots = []
        for e in os.scandir(folder):
            if e.name.endswith(".json"):
                s = _load_store(directory, e.name[:-5])
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


def _list_all_directories():
    index = _load_index()
    return sorted(index["directories"].keys())


# ── Armed state ─────────────────────────────────────────────────────────────
def _load_armed():
    if not os.path.exists(ARMED_PATH):
        return {}
    try:
        with open(ARMED_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


# ── Context reconstruction ──────────────────────────────────────────────────
def _build_context(ancestors, include_files=True):
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
            for f in n.files or []:
                if f not in files:
                    files.append(f)
        if files:
            parts.append("=== FILES REFERENCED IN THIS CONVERSATION ===")
            parts += [f"  {f}" for f in files]
            parts.append("")
    parts.append("=== END CONVERSATION HISTORY ===")
    return "\n".join(parts)


# ── Tree rendering ──────────────────────────────────────────────────────────
_TYPE_GLYPHS = {
    "store": "\033[34mL\033[0m",
    "query": "\033[33mQ\033[0m",
    "fork": "\033[35mF\033[0m",
    "tool": "\033[36mT\033[0m",
}
_DIM = "\033[2m"
_BOLD = "\033[1m"
_RST = "\033[0m"
_CYAN = "\033[36m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"


def _render_children(children, indent=2):
    lines = []
    keys = list(children.keys())
    for i, key in enumerate(keys):
        node = children[key]
        last = i == len(keys) - 1
        branch = "└─" if last else "├─"
        glyph = _TYPE_GLYPHS.get(node.entry_type, "?")
        lbl = f' {_DIM}"{node.label}"{_RST}' if node.label else ""
        mdl = f" {_CYAN}[{node.model}]{_RST}" if node.model else ""
        nf = f" {_GREEN}({len(node.files)} files){_RST}" if node.files else ""
        plen, rlen = len(node.prompt), len(node.response)
        sz = f" {_DIM}p:{plen} r:{rlen}{_RST}" if plen or rlen else ""
        lines.append(f"{' ' * indent}{branch} {key} ({glyph}){lbl}{mdl}{nf}{sz}")
        if node.children:
            lines += _render_children(node.children, indent + 4)
    return lines


def _render_store(store):
    armed = _load_armed()
    armed_marker = f" {_YELLOW}[armed]{_RST}" if armed.get(store.directory) == store.store_id else ""
    lines = [
        f"{_BOLD}■ {store.store_id}{_RST}  {_DIM}{store.directory}{_RST}{armed_marker}",
    ]
    if store.label:
        lines.append(f"  {_DIM}label: {store.label}{_RST}")
    lines.append(f"  {_DIM}created: {store.created_at}{_RST}")
    lines += _render_children(store.children)
    return "\n".join(lines)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Public API — tab-completable namespace
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class Ctx:
    """PAL context store interface. Use `ctx.<tab>` to explore."""

    # ── read ────────────────────────────────────────────────────────────────

    def stores(self, directory=None):
        """List all stores, optionally filtered by directory."""
        return _list_stores(directory)

    def directories(self):
        """List all directories with stores."""
        return _list_all_directories()

    def load(self, store_id):
        """Load a store by ID. Returns (StoreRoot, directory) or raises."""
        loc = _resolve_store_location(store_id)
        if loc is None:
            raise KeyError(f"Store not found: {store_id}")
        directory, root_id = loc
        store = _load_store(directory, root_id)
        if store is None:
            raise KeyError(f"Store file missing: {root_id}")
        return store

    def node(self, store_id):
        """Resolve a dotted path to its StoreNode (None for root)."""
        store = self.load(store_id)
        return _resolve_node(store, store_id)

    def ancestry(self, store_id):
        """Walk ancestor nodes from root to store_id."""
        store = self.load(store_id)
        return _walk_ancestry(store, store_id)

    def history(self, store_id, include_files=True):
        """Reconstruct conversation history string from ancestry."""
        return _build_context(self.ancestry(store_id), include_files)

    def read(self, store_id):
        """Read a node's prompt and response."""
        node = self.node(store_id)
        if node is None:
            store = self.load(store_id)
            print(f"Root: {store.store_id}  dir: {store.directory}  created: {store.created_at}")
            print(f"Children: {list(store.children.keys())}")
            return
        print(f"type: {node.entry_type}  label: {node.label}  model: {node.model}")
        print(f"timestamp: {node.timestamp}")
        if node.files:
            print(f"files: {node.files}")
        if node.prompt:
            print(f"\n{'─' * 40} prompt {'─' * 40}")
            print(node.prompt[:2000] + ("..." if len(node.prompt) > 2000 else ""))
        if node.response:
            print(f"\n{'─' * 40} response {'─' * 39}")
            print(node.response[:2000] + ("..." if len(node.response) > 2000 else ""))
        if node.children:
            print(f"\nchildren: {list(node.children.keys())}")

    def last(self, store_id):
        """Get the dotted path to the last layer of a store."""
        store = self.load(store_id)
        return _get_last_layer_path(store)

    def armed(self):
        """Show armed stores."""
        return _load_armed()

    # ── write ───────────────────────────────────────────────────────────────

    def create(self, store_id, directory, label=None):
        """Create a new empty store."""
        if "." in store_id:
            raise ValueError("Store names cannot contain dots")
        store = StoreRoot(
            store_id=store_id,
            directory=directory,
            label=label,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        _save_store(store)
        _update_index(directory, store_id)
        return store

    def layer(self, store_id, prompt, response, files=None, label=None, model=None):
        """Append a layer node. Reads files from disk into the content blob."""
        store = self.load(store_id)
        now = datetime.now(timezone.utc).isoformat()
        file_blobs = ""
        if files:
            parts = ["=== CONTEXT FILES ==="]
            for fp in files:
                try:
                    with open(fp) as f:
                        content = f.read()
                    parts += [f"--- BEGIN FILE: {fp} ---", content, f"--- END FILE: {fp} ---"]
                except OSError as e:
                    parts += [f"--- BEGIN FILE: {fp} (ERROR: {e}) ---", f"--- END FILE: {fp} ---"]
            file_blobs = "\n".join(parts) + "\n\n"
        full_content = f"{file_blobs}{prompt}\n\n---\n\n{response}"
        node = StoreNode(
            entry_type="store",
            label=label,
            timestamp=now,
            model=model,
            files=files or [],
            prompt=prompt,
            response=response,
            content=full_content,
        )
        key = _get_next_key(store, store_id, "L")
        full_path = _add_child(store, store_id, key, node)
        _save_store(store)
        return full_path

    def fork(self, store_id, label=None):
        """Create a fork branch point."""
        store = self.load(store_id)
        node = StoreNode(
            entry_type="fork",
            label=label,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        key = _get_next_key(store, store_id, "F")
        full_path = _add_child(store, store_id, key, node)
        _save_store(store)
        return full_path

    def arm(self, directory, store_id):
        """Arm a store for auto-revival."""
        armed = _load_armed()
        armed[directory] = store_id
        _atomic_write(ARMED_PATH, armed)

    def disarm(self, directory):
        """Remove armed state for a directory."""
        armed = _load_armed()
        armed.pop(directory, None)
        _atomic_write(ARMED_PATH, armed)

    def save(self, store):
        """Persist a modified StoreRoot to disk."""
        _save_store(store)

    # ── display ─────────────────────────────────────────────────────────────

    def tree(self, store_id=None, directory=None):
        """Pretty-print store tree(s)."""
        if store_id:
            store = self.load(store_id)
            print(_render_store(store))
        else:
            for store in _list_stores(directory):
                print(_render_store(store))
                print()

    def __repr__(self):
        n = len(_list_stores())
        dirs = _list_all_directories()
        return f"Ctx({n} stores, {len(dirs)} dirs)"


ctx = Ctx()

# ── IPython startup ─────────────────────────────────────────────────────────
print(f"{_BOLD}PAL Context Store{_RST}  {_DIM}{CTX_DIR}{_RST}")
print(f"{repr(ctx)}\n")
print("Usage: ctx.<tab>")
print("  .stores() .directories() .tree() .load(id) .node(id) .read(id)")
print("  .ancestry(id) .history(id) .last(id) .armed()")
print("  .create(id, dir) .layer(id, prompt, resp) .fork(id) .arm(dir, id) .disarm(dir) .save(store)")
