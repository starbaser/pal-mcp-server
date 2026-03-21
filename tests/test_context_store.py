"""
Unit tests for utils/context_store.py and utils/context_builder.py.
"""

import os

import pytest

from utils.context_store import (
    StoreNode,
    StoreRoot,
    add_child,
    arm_store,
    disarm_store,
    encode_directory,
    get_armed_store,
    get_next_key,
    list_armed_stores,
    list_stores,
    load_store,
    parse_store_path,
    rebuild_index,
    resolve_node,
    resolve_store_location,
    save_store,
    update_index,
    walk_ancestry,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def ctx_env(tmp_path, monkeypatch):
    from utils import context_store

    ctx_dir = str(tmp_path / "context")
    monkeypatch.setattr(context_store, "_CTX_DIR", ctx_dir)
    monkeypatch.setattr(context_store, "_INDEX_PATH", os.path.join(ctx_dir, "store-index.json"))
    monkeypatch.setattr(context_store, "_ARMED_PATH", os.path.join(ctx_dir, "armed.json"))
    return ctx_dir


def _make_node(entry_type="store", prompt="", response="", files=None, label=None) -> StoreNode:
    return StoreNode(
        entry_type=entry_type,
        label=label,
        timestamp="2026-01-01T00:00:00Z",
        prompt=prompt,
        response=response,
        files=files or [],
    )


def _make_root(store_id="mystore", directory="/home/user/project") -> StoreRoot:
    return StoreRoot(
        store_id=store_id,
        directory=directory,
        created_at="2026-01-01T00:00:00Z",
    )


# ---------------------------------------------------------------------------
# TestEncodeDirectory
# ---------------------------------------------------------------------------


class TestEncodeDirectory:
    def test_basic_path(self):
        assert encode_directory("/home/user/project") == "-home-user-project"

    def test_dotfiles(self):
        assert encode_directory("/home/user/.config") == "-home-user--config"

    def test_root_path(self):
        assert encode_directory("/") == "-"

    def test_nested_path_with_dots(self):
        assert encode_directory("/home/user/my.project") == "-home-user-my-project"


# ---------------------------------------------------------------------------
# TestStoreLifecycle
# ---------------------------------------------------------------------------


class TestStoreLifecycle:
    def test_save_and_load_roundtrip(self, ctx_env):
        store = _make_root()
        save_store(store)
        loaded = load_store(store.directory, store.store_id)
        assert loaded is not None
        assert loaded.store_id == store.store_id
        assert loaded.directory == store.directory
        assert loaded.created_at == store.created_at

    def test_load_nonexistent_returns_none(self, ctx_env):
        result = load_store("/home/nobody/project", "ghost-store")
        assert result is None

    def test_atomic_write_does_not_corrupt_on_overwrite(self, ctx_env):
        store = _make_root()
        save_store(store)

        # Add a child and overwrite
        child = _make_node(entry_type="query", prompt="hello", response="world")
        store.children["L1"] = child
        save_store(store)

        loaded = load_store(store.directory, store.store_id)
        assert loaded is not None
        assert "L1" in loaded.children
        assert loaded.children["L1"].prompt == "hello"

    def test_load_corrupt_json_returns_none(self, ctx_env):
        from utils import context_store

        store = _make_root()
        path = context_store.get_store_path(store.directory, store.store_id)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write("{not valid json")

        result = load_store(store.directory, store.store_id)
        assert result is None


# ---------------------------------------------------------------------------
# TestResolveNode
# ---------------------------------------------------------------------------


class TestResolveNode:
    def test_resolve_direct_child(self, ctx_env):
        store = _make_root()
        child = _make_node(entry_type="query", prompt="q", response="r")
        store.children["L1"] = child

        result = resolve_node(store, "mystore.L1")
        assert result is not None
        assert result.prompt == "q"

    def test_resolve_deeper_path(self, ctx_env):
        store = _make_root()
        l1 = _make_node(entry_type="query", prompt="l1p", response="l1r")
        q0 = _make_node(entry_type="query", prompt="q0p", response="q0r")
        td0 = _make_node(entry_type="tool", prompt="tdp", response="tdr")

        q0.children["thinkdeep0"] = td0
        l1.children["Q0"] = q0
        store.children["L1"] = l1

        # Index the store so parse_store_path can find the root
        update_index(store.directory, store.store_id)

        result = resolve_node(store, "mystore.L1.Q0.thinkdeep0")
        assert result is not None
        assert result.prompt == "tdp"

    def test_resolve_root_only_returns_none(self, ctx_env):
        store = _make_root()
        result = resolve_node(store, "mystore")
        assert result is None

    def test_resolve_nonexistent_path_returns_none(self, ctx_env):
        store = _make_root()
        result = resolve_node(store, "mystore.Z99")
        assert result is None


# ---------------------------------------------------------------------------
# TestAddChild
# ---------------------------------------------------------------------------


class TestAddChild:
    def test_add_child_to_root(self, ctx_env):
        store = _make_root()
        child = _make_node(entry_type="query", prompt="q", response="r")

        full_path = add_child(store, "mystore", "L1", child)
        assert full_path == "mystore.L1"
        assert "L1" in store.children

    def test_add_child_to_nested_node(self, ctx_env):
        store = _make_root()
        l1 = _make_node(entry_type="query", prompt="p", response="r")
        store.children["L1"] = l1
        update_index(store.directory, store.store_id)

        grandchild = _make_node(entry_type="tool", prompt="gp", response="gr")
        full_path = add_child(store, "mystore.L1", "Q0", grandchild)
        assert full_path == "mystore.L1.Q0"
        assert "Q0" in store.children["L1"].children

    def test_add_child_to_nonexistent_parent_raises(self, ctx_env):
        store = _make_root()
        child = _make_node()

        with pytest.raises(KeyError, match="parent path not found"):
            add_child(store, "mystore.Z99", "L1", child)


# ---------------------------------------------------------------------------
# TestWalkAncestry
# ---------------------------------------------------------------------------


class TestWalkAncestry:
    def test_walk_to_direct_child(self, ctx_env):
        store = _make_root()
        child = _make_node(entry_type="query", prompt="p", response="r")
        store.children["L1"] = child

        result = walk_ancestry(store, "mystore.L1")
        assert len(result) == 1
        assert result[0].prompt == "p"

    def test_walk_to_deeper_path(self, ctx_env):
        store = _make_root()
        l1 = _make_node(entry_type="query", prompt="l1p", response="l1r")
        q0 = _make_node(entry_type="query", prompt="q0p", response="q0r")
        td0 = _make_node(entry_type="tool", prompt="tdp", response="tdr")

        q0.children["thinkdeep0"] = td0
        l1.children["Q0"] = q0
        store.children["L1"] = l1

        update_index(store.directory, store.store_id)

        result = walk_ancestry(store, "mystore.L1.Q0.thinkdeep0")
        assert len(result) == 3
        assert result[0].prompt == "l1p"
        assert result[1].prompt == "q0p"
        assert result[2].prompt == "tdp"

    def test_walk_to_root_returns_empty(self, ctx_env):
        store = _make_root()
        result = walk_ancestry(store, "mystore")
        assert result == []

    def test_fork_nodes_included_in_ancestry(self, ctx_env):
        store = _make_root()
        fork = _make_node(entry_type="fork", prompt="", response="")
        child = _make_node(entry_type="query", prompt="cp", response="cr")

        fork.children["L1"] = child
        store.children["F0"] = fork

        update_index(store.directory, store.store_id)

        result = walk_ancestry(store, "mystore.F0.L1")
        assert len(result) == 2
        assert result[0].entry_type == "fork"
        assert result[1].prompt == "cp"


# ---------------------------------------------------------------------------
# TestGetNextKey
# ---------------------------------------------------------------------------


class TestGetNextKey:
    def test_L_prefix_empty_store(self, ctx_env):
        store = _make_root()
        assert get_next_key(store, "mystore", "L") == "L1"

    def test_L_prefix_existing_children(self, ctx_env):
        store = _make_root()
        store.children["L1"] = _make_node()
        store.children["L2"] = _make_node()
        assert get_next_key(store, "mystore", "L") == "L3"

    def test_Q_prefix_empty_store(self, ctx_env):
        store = _make_root()
        assert get_next_key(store, "mystore", "Q") == "Q0"

    def test_Q_prefix_existing_children(self, ctx_env):
        store = _make_root()
        store.children["Q0"] = _make_node()
        assert get_next_key(store, "mystore", "Q") == "Q1"

    def test_F_prefix_empty_store(self, ctx_env):
        store = _make_root()
        assert get_next_key(store, "mystore", "F") == "F0"

    def test_F_prefix_existing_children(self, ctx_env):
        store = _make_root()
        store.children["F0"] = _make_node()
        assert get_next_key(store, "mystore", "F") == "F1"

    def test_empty_prefix_followup_empty(self, ctx_env):
        store = _make_root()
        assert get_next_key(store, "mystore", "") == "1"

    def test_empty_prefix_followup_existing(self, ctx_env):
        store = _make_root()
        store.children["1"] = _make_node()
        store.children["2"] = _make_node()
        assert get_next_key(store, "mystore", "") == "3"

    def test_tool_prefix_empty(self, ctx_env):
        store = _make_root()
        assert get_next_key(store, "mystore", "thinkdeep") == "thinkdeep0"

    def test_tool_prefix_existing(self, ctx_env):
        store = _make_root()
        store.children["thinkdeep0"] = _make_node()
        assert get_next_key(store, "mystore", "thinkdeep") == "thinkdeep1"

    def test_tool_prefix_nested_node(self, ctx_env):
        store = _make_root()
        l1 = _make_node(entry_type="query", prompt="p", response="r")
        store.children["L1"] = l1
        update_index(store.directory, store.store_id)

        assert get_next_key(store, "mystore.L1", "analyze") == "analyze0"


# ---------------------------------------------------------------------------
# TestParseStorePath
# ---------------------------------------------------------------------------


class TestParseStorePath:
    def test_simple_no_dot(self, ctx_env):
        root, segments = parse_store_path("mystore")
        assert root == "mystore"
        assert segments == []

    def test_with_segments(self, ctx_env):
        update_index("/home/user/project", "mystore")
        root, segments = parse_store_path("mystore.L1.Q0")
        assert root == "mystore"
        assert segments == ["L1", "Q0"]

    def test_with_tool_index(self, ctx_env):
        update_index("/home/user/project", "mystore")
        root, segments = parse_store_path("mystore.Q0.thinkdeep0")
        assert root == "mystore"
        assert segments == ["Q0", "thinkdeep0"]

    def test_fallback_to_first_segment_when_not_indexed(self, ctx_env):
        # No index entry for "unknown-store"
        root, segments = parse_store_path("unknown-store.L1.Q0")
        assert root == "unknown-store"
        assert segments == ["L1", "Q0"]


# ---------------------------------------------------------------------------
# TestIndexOperations
# ---------------------------------------------------------------------------


class TestIndexOperations:
    def test_update_and_load_index_roundtrip(self, ctx_env):
        update_index("/home/user/project", "mystore")
        from utils.context_store import load_index

        index = load_index()
        assert "mystore" in index["stores"]
        assert "/home/user/project" in index["directories"]

    def test_resolve_store_location_finds_indexed_store(self, ctx_env):
        store = _make_root()
        save_store(store)
        update_index(store.directory, store.store_id)

        result = resolve_store_location("mystore")
        assert result is not None
        directory, root_id = result
        assert root_id == "mystore"
        assert directory == store.directory

    def test_resolve_store_location_returns_none_for_unknown(self, ctx_env):
        result = resolve_store_location("totally-unknown-store")
        assert result is None

    def test_rebuild_index_scans_directories(self, ctx_env):
        store_a = _make_root(store_id="store-a", directory="/home/user/proj-a")
        store_b = _make_root(store_id="store-b", directory="/home/user/proj-b")
        save_store(store_a)
        save_store(store_b)

        index = rebuild_index()

        assert "store-a" in index["stores"]
        assert "store-b" in index["stores"]
        assert "/home/user/proj-a" in index["directories"]
        assert "/home/user/proj-b" in index["directories"]

    def test_resolve_store_location_falls_back_to_scan(self, ctx_env):
        # Save store but do NOT update index — force a scan fallback
        store = _make_root()
        save_store(store)

        result = resolve_store_location("mystore")
        assert result is not None
        _, root_id = result
        assert root_id == "mystore"


# ---------------------------------------------------------------------------
# TestArmedOperations
# ---------------------------------------------------------------------------


class TestArmedOperations:
    def test_arm_and_get_roundtrip(self, ctx_env):
        arm_store("/home/user/project", "mystore")
        result = get_armed_store("/home/user/project")
        assert result == "mystore"

    def test_disarm_removes_armed_state(self, ctx_env):
        arm_store("/home/user/project", "mystore")
        disarm_store("/home/user/project")
        result = get_armed_store("/home/user/project")
        assert result is None

    def test_list_armed_stores_returns_all(self, ctx_env):
        arm_store("/home/user/proj-a", "store-a")
        arm_store("/home/user/proj-b", "store-b")

        all_armed = list_armed_stores()
        assert all_armed["/home/user/proj-a"] == "store-a"
        assert all_armed["/home/user/proj-b"] == "store-b"

    def test_disarm_nonexistent_is_noop(self, ctx_env):
        # Should not raise
        disarm_store("/home/user/nonexistent")
        assert get_armed_store("/home/user/nonexistent") is None

    def test_get_armed_returns_none_when_empty(self, ctx_env):
        result = get_armed_store("/home/user/project")
        assert result is None


# ---------------------------------------------------------------------------
# TestListStores
# ---------------------------------------------------------------------------


class TestListStores:
    def test_list_stores_with_directory_filter(self, ctx_env):
        store_a = _make_root(store_id="store-a", directory="/home/user/proj-a")
        store_b = _make_root(store_id="store-b", directory="/home/user/proj-b")
        save_store(store_a)
        save_store(store_b)

        results = list_stores(directory="/home/user/proj-a")
        assert len(results) == 1
        assert results[0].store_id == "store-a"

    def test_list_stores_without_filter_returns_all(self, ctx_env):
        store_a = _make_root(store_id="store-a", directory="/home/user/proj-a")
        store_b = _make_root(store_id="store-b", directory="/home/user/proj-b")
        save_store(store_a)
        save_store(store_b)

        results = list_stores()
        ids = {s.store_id for s in results}
        assert "store-a" in ids
        assert "store-b" in ids

    def test_list_stores_empty_for_nonexistent_directory(self, ctx_env):
        results = list_stores(directory="/home/nobody/nowhere")
        assert results == []

    def test_list_stores_filters_corrupt_files(self, ctx_env):
        from utils import context_store

        store = _make_root()
        save_store(store)

        # Drop a corrupt JSON file in the same directory
        folder = context_store.get_store_dir(store.directory)
        corrupt_path = os.path.join(folder, "corrupt.json")
        with open(corrupt_path, "w") as f:
            f.write("{bad json")

        results = list_stores(directory=store.directory)
        assert len(results) == 1
        assert results[0].store_id == "mystore"


# ---------------------------------------------------------------------------
# TestContextBuilder
# ---------------------------------------------------------------------------


class TestContextBuilder:
    def test_build_context_from_ancestry_with_content_nodes(self):
        from utils.context_builder import build_context_from_ancestry

        nodes = [
            _make_node(entry_type="query", prompt="first question", response="first answer"),
            _make_node(entry_type="tool", prompt="second question", response="second answer"),
        ]

        result = build_context_from_ancestry(nodes)
        assert "=== CONVERSATION HISTORY ===" in result
        assert "first question" in result
        assert "first answer" in result
        assert "second question" in result
        assert "second answer" in result
        assert "Turn 1" in result
        assert "Turn 2" in result

    def test_build_context_skips_fork_nodes(self):
        from utils.context_builder import build_context_from_ancestry

        fork = _make_node(entry_type="fork", prompt="", response="")
        content = _make_node(entry_type="query", prompt="real question", response="real answer")
        nodes = [fork, content]

        result = build_context_from_ancestry(nodes)
        assert "real question" in result
        assert "Turn 1" in result
        # Only one turn since fork is skipped
        assert "Turn 2" not in result

    def test_build_context_returns_empty_for_empty_list(self):
        from utils.context_builder import build_context_from_ancestry

        result = build_context_from_ancestry([])
        assert result == ""

    def test_build_context_returns_empty_when_all_forks(self):
        from utils.context_builder import build_context_from_ancestry

        nodes = [
            _make_node(entry_type="fork", prompt="", response=""),
            _make_node(entry_type="fork", prompt="", response=""),
        ]

        result = build_context_from_ancestry(nodes)
        assert result == ""

    def test_build_context_includes_file_listing(self):
        from utils.context_builder import build_context_from_ancestry

        node = _make_node(
            entry_type="query",
            prompt="with files",
            response="got it",
            files=["/home/user/foo.py", "/home/user/bar.py"],
        )

        result = build_context_from_ancestry([node], include_files=True)
        assert "=== FILES REFERENCED IN THIS CONVERSATION ===" in result
        assert "/home/user/foo.py" in result
        assert "/home/user/bar.py" in result

    def test_build_context_omits_file_listing_when_disabled(self):
        from utils.context_builder import build_context_from_ancestry

        node = _make_node(
            entry_type="query",
            prompt="with files",
            response="got it",
            files=["/home/user/foo.py"],
        )

        result = build_context_from_ancestry([node], include_files=False)
        assert "FILES REFERENCED" not in result

    def test_build_context_deduplicates_files_across_nodes(self):
        from utils.context_builder import build_context_from_ancestry

        node_a = _make_node(entry_type="query", prompt="q1", response="r1", files=["/shared.py", "/a.py"])
        node_b = _make_node(entry_type="query", prompt="q2", response="r2", files=["/shared.py", "/b.py"])

        result = build_context_from_ancestry([node_a, node_b], include_files=True)
        # /shared.py must appear exactly once
        assert result.count("/shared.py") == 1

    def test_build_context_ends_with_end_marker(self):
        from utils.context_builder import build_context_from_ancestry

        node = _make_node(entry_type="query", prompt="q", response="r")
        result = build_context_from_ancestry([node])
        assert result.strip().endswith("=== END CONVERSATION HISTORY ===")
