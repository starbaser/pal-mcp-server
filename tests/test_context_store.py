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
    get_last_layer_path,
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
        f0 = _make_node(entry_type="fork", prompt="", response="")
        td = _make_node(entry_type="tool", prompt="tdp", response="tdr")

        f0.children["thinkdeep"] = td
        q0.children["F0"] = f0
        l1.children["Q0"] = q0
        store.children["L1"] = l1

        # Index the store so parse_store_path can find the root
        update_index(store.directory, store.store_id)

        result = resolve_node(store, "mystore.L1.Q0.F0.thinkdeep")
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
        f0 = _make_node(entry_type="fork", prompt="", response="")
        td = _make_node(entry_type="tool", prompt="tdp", response="tdr")

        f0.children["thinkdeep"] = td
        q0.children["F0"] = f0
        l1.children["Q0"] = q0
        store.children["L1"] = l1

        update_index(store.directory, store.store_id)

        result = walk_ancestry(store, "mystore.L1.Q0.F0.thinkdeep")
        assert len(result) == 4
        assert result[0].prompt == "l1p"
        assert result[1].prompt == "q0p"
        assert result[2].entry_type == "fork"
        assert result[3].prompt == "tdp"

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

    def test_tool_name_prefix_raises_value_error(self, ctx_env):
        store = _make_root()
        with pytest.raises(ValueError, match="Unsupported prefix"):
            get_next_key(store, "mystore", "thinkdeep")

    def test_another_tool_name_raises_value_error(self, ctx_env):
        store = _make_root()
        with pytest.raises(ValueError, match="Unsupported prefix"):
            get_next_key(store, "mystore", "analyze")


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

    def test_with_tool_child_under_fork(self, ctx_env):
        update_index("/home/user/project", "mystore")
        root, segments = parse_store_path("mystore.Q0.F0.thinkdeep")
        assert root == "mystore"
        assert segments == ["Q0", "F0", "thinkdeep"]

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

    def test_build_context_uses_content_for_user_turn(self):
        from utils.context_builder import build_context_from_ancestry

        full_prompt = "user text\n\n=== CONTEXT FILES ===\nFILE BLOB CONTENT HERE\n=== END CONTEXT FILES ==="
        raw_response = "assistant replied"
        node = StoreNode(
            entry_type="store",
            timestamp="2026-01-01T00:00:00Z",
            prompt="user text",
            response=raw_response,
            content=f"{full_prompt}\n\n---\n\n{raw_response}",
        )
        result = build_context_from_ancestry([node])
        assert "FILE BLOB CONTENT HERE" in result
        assert raw_response in result

    def test_build_context_falls_back_to_prompt_when_no_content(self):
        from utils.context_builder import build_context_from_ancestry

        node = _make_node(entry_type="store", prompt="bare prompt", response="response")
        result = build_context_from_ancestry([node])
        assert "bare prompt" in result

    def test_build_context_split_uses_first_separator_only(self):
        from utils.context_builder import build_context_from_ancestry

        tricky_prompt = "before\n\n---\n\nstill part of prompt"
        response = "answer"
        node = StoreNode(
            entry_type="store",
            timestamp="2026-01-01T00:00:00Z",
            prompt="short",
            response=response,
            content=f"{tricky_prompt}\n\n---\n\n{response}",
        )
        result = build_context_from_ancestry([node])
        assert "before" in result
        assert "still part of prompt" not in result
        assert "answer" in result


# ---------------------------------------------------------------------------
# TestEncodeDirectoryEdgeCases
# ---------------------------------------------------------------------------


class TestEncodeDirectoryEdgeCases:
    def test_empty_string(self):
        assert encode_directory("") == ""

    def test_trailing_slash(self):
        assert encode_directory("/home/user/") == "-home-user-"

    def test_path_with_spaces(self):
        assert encode_directory("/home/user/my project") == "-home-user-my project"

    def test_multiple_consecutive_slashes(self):
        assert encode_directory("//home///user") == "--home---user"


# ---------------------------------------------------------------------------
# TestPathUtilities
# ---------------------------------------------------------------------------


class TestPathUtilities:
    def test_get_store_dir_returns_expected_path(self, ctx_env):
        from utils.context_store import get_store_dir

        result = get_store_dir("/home/user/project")
        assert result == os.path.join(ctx_env, "-home-user-project")

    def test_get_store_path_ends_with_json(self, ctx_env):
        from utils.context_store import get_store_path

        result = get_store_path("/home/user/project", "mystore")
        assert result.endswith("mystore.json")


# ---------------------------------------------------------------------------
# TestStoreLifecycleEdgeCases
# ---------------------------------------------------------------------------


class TestStoreLifecycleEdgeCases:
    def test_load_store_with_list_json_returns_none(self, ctx_env):
        from utils import context_store

        store = _make_root()
        path = context_store.get_store_path(store.directory, store.store_id)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            import json

            json.dump([1, 2, 3], f)

        result = load_store(store.directory, store.store_id)
        assert result is None

    def test_load_store_with_missing_required_fields_returns_none(self, ctx_env):
        from utils import context_store

        store = _make_root()
        path = context_store.get_store_path(store.directory, store.store_id)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            import json

            json.dump({"unrelated_field": "value"}, f)

        result = load_store(store.directory, store.store_id)
        assert result is None

    def test_save_store_creates_nested_directories(self, ctx_env):
        from utils import context_store

        store = _make_root(directory="/some/deeply/nested/path")
        save_store(store)

        path = context_store.get_store_path(store.directory, store.store_id)
        assert os.path.exists(path)


# ---------------------------------------------------------------------------
# TestRenameStore
# ---------------------------------------------------------------------------


class TestRenameStore:
    def test_rename_happy_path(self, ctx_env):
        from utils.context_store import get_store_path, load_index, rename_store

        store = _make_root()
        save_store(store)
        update_index(store.directory, store.store_id)

        rename_store(store.directory, "mystore", "renamed-store")

        assert os.path.exists(get_store_path(store.directory, "renamed-store"))
        assert not os.path.exists(get_store_path(store.directory, "mystore"))

        loaded = load_store(store.directory, "renamed-store")
        assert loaded is not None
        assert loaded.store_id == "renamed-store"

        index = load_index()
        assert "renamed-store" in index["stores"]
        assert "mystore" not in index["stores"]

    def test_rename_nonexistent_raises_key_error(self, ctx_env):
        from utils.context_store import rename_store

        with pytest.raises(KeyError, match="Store not found"):
            rename_store("/home/user/project", "ghost-store", "newname")

    def test_rename_new_id_with_dots_raises_value_error(self, ctx_env):
        from utils.context_store import rename_store

        store = _make_root()
        save_store(store)

        with pytest.raises(ValueError, match="dots"):
            rename_store(store.directory, "mystore", "new.name")

    def test_rename_updates_armed_state(self, ctx_env):
        from utils.context_store import rename_store

        store = _make_root()
        save_store(store)
        update_index(store.directory, store.store_id)
        arm_store(store.directory, "mystore")

        rename_store(store.directory, "mystore", "renamed-store")

        assert get_armed_store(store.directory) == "renamed-store"

    def test_rename_when_store_not_armed_is_noop_for_armed_state(self, ctx_env):
        from utils.context_store import rename_store

        store = _make_root()
        save_store(store)
        update_index(store.directory, store.store_id)
        arm_store("/home/OTHER/project", "other-store")

        rename_store(store.directory, "mystore", "renamed-store")

        assert get_armed_store("/home/OTHER/project") == "other-store"
        assert get_armed_store(store.directory) is None

    def test_rename_preserves_children(self, ctx_env):
        from utils.context_store import rename_store

        store = _make_root()
        child = _make_node(entry_type="query", prompt="child-q", response="child-r")
        store.children["L1"] = child
        save_store(store)
        update_index(store.directory, store.store_id)

        rename_store(store.directory, "mystore", "renamed-store")

        loaded = load_store(store.directory, "renamed-store")
        assert loaded is not None
        assert "L1" in loaded.children
        assert loaded.children["L1"].prompt == "child-q"


# ---------------------------------------------------------------------------
# TestResolveNodeEdgeCases
# ---------------------------------------------------------------------------


class TestResolveNodeEdgeCases:
    def test_partial_valid_path_then_invalid_segment_returns_none(self, ctx_env):
        store = _make_root()
        child = _make_node(entry_type="query", prompt="p", response="r")
        store.children["L1"] = child
        update_index(store.directory, store.store_id)

        result = resolve_node(store, "mystore.L1.Q99")
        assert result is None

    def test_empty_children_at_intermediate_node_returns_none(self, ctx_env):
        store = _make_root()
        child = _make_node(entry_type="query", prompt="p", response="r")
        store.children["L1"] = child
        update_index(store.directory, store.store_id)

        result = resolve_node(store, "mystore.L1.Q0")
        assert result is None


# ---------------------------------------------------------------------------
# TestAddChildEdgeCases
# ---------------------------------------------------------------------------


class TestAddChildEdgeCases:
    def test_overwrite_existing_child_key_silently(self, ctx_env):
        store = _make_root()
        original = _make_node(entry_type="store", prompt="original", response="old")
        replacement = _make_node(entry_type="query", prompt="new prompt", response="new response")

        add_child(store, "mystore", "L1", original)
        add_child(store, "mystore", "L1", replacement)

        assert store.children["L1"].prompt == "new prompt"
        assert store.children["L1"].entry_type == "query"

    def test_deeply_nested_add_three_levels(self, ctx_env):
        store = _make_root()

        n1 = _make_node(entry_type="store", prompt="l1", response="r1")
        add_child(store, "mystore", "L1", n1)
        update_index(store.directory, store.store_id)

        n2 = _make_node(entry_type="query", prompt="q0", response="rq0")
        path2 = add_child(store, "mystore.L1", "Q0", n2)
        assert path2 == "mystore.L1.Q0"

        n3 = _make_node(entry_type="tool", prompt="tool-p", response="tool-r")
        path3 = add_child(store, "mystore.L1.Q0", "thinkdeep", n3)
        assert path3 == "mystore.L1.Q0.thinkdeep"

        assert store.children["L1"].children["Q0"].children["thinkdeep"].prompt == "tool-p"


# ---------------------------------------------------------------------------
# TestWalkAncestryEdgeCases
# ---------------------------------------------------------------------------


class TestWalkAncestryEdgeCases:
    def test_partial_walk_first_valid_second_missing(self, ctx_env):
        store = _make_root()
        child = _make_node(entry_type="store", prompt="p1", response="r1")
        store.children["L1"] = child
        update_index(store.directory, store.store_id)

        result = walk_ancestry(store, "mystore.L1.Q0")
        assert len(result) == 1
        assert result[0].prompt == "p1"

    def test_walk_through_mixed_entry_types(self, ctx_env):
        store = _make_root()
        n_store = _make_node(entry_type="store", prompt="store-p", response="store-r")
        n_fork = _make_node(entry_type="fork", prompt="", response="")
        n_query = _make_node(entry_type="query", prompt="query-p", response="query-r")
        n_tool = _make_node(entry_type="tool", prompt="tool-p", response="tool-r")

        n_query.children["thinkdeep"] = n_tool
        n_fork.children["Q0"] = n_query
        n_store.children["F0"] = n_fork
        store.children["L1"] = n_store
        update_index(store.directory, store.store_id)

        result = walk_ancestry(store, "mystore.L1.F0.Q0.thinkdeep")
        assert len(result) == 4
        assert result[0].entry_type == "store"
        assert result[1].entry_type == "fork"
        assert result[2].entry_type == "query"
        assert result[3].entry_type == "tool"


# ---------------------------------------------------------------------------
# TestGetNextKeyEdgeCases
# ---------------------------------------------------------------------------


class TestGetNextKeyEdgeCases:
    def test_gap_in_numbering_returns_max_plus_one(self, ctx_env):
        store = _make_root()
        store.children["L1"] = _make_node()
        store.children["L3"] = _make_node()

        result = get_next_key(store, "mystore", "L")
        assert result == "L4"

    def test_nonexistent_parent_path_treats_as_empty_L(self, ctx_env):
        store = _make_root()
        result = get_next_key(store, "mystore.NONEXISTENT", "L")
        assert result == "L1"

    def test_nonexistent_parent_path_treats_as_empty_Q(self, ctx_env):
        store = _make_root()
        result = get_next_key(store, "mystore.NONEXISTENT", "Q")
        assert result == "Q0"

    def test_mixed_children_types_each_prefix_independent(self, ctx_env):
        store = _make_root()
        store.children["L1"] = _make_node()
        store.children["L2"] = _make_node()
        store.children["Q0"] = _make_node()
        store.children["Q1"] = _make_node()
        store.children["F0"] = _make_node()

        assert get_next_key(store, "mystore", "L") == "L3"
        assert get_next_key(store, "mystore", "Q") == "Q2"
        assert get_next_key(store, "mystore", "F") == "F1"


# ---------------------------------------------------------------------------
# TestParseStorePathEdgeCases
# ---------------------------------------------------------------------------


class TestParseStorePathEdgeCases:
    def test_store_id_with_hyphen(self, ctx_env):
        update_index("/home/user/project", "my-store")
        root, segments = parse_store_path("my-store.L1.Q0")
        assert root == "my-store"
        assert segments == ["L1", "Q0"]

    def test_empty_string(self, ctx_env):
        root, segments = parse_store_path("")
        assert root == ""
        assert segments == []


# ---------------------------------------------------------------------------
# TestResolveStoreLocationEdgeCases
# ---------------------------------------------------------------------------


class TestResolveStoreLocationEdgeCases:
    def test_dotted_path_resolves_to_root(self, ctx_env):
        store = _make_root()
        save_store(store)
        update_index(store.directory, store.store_id)

        result = resolve_store_location("mystore.L1.Q0")
        assert result is not None
        directory, root_id = result
        assert root_id == "mystore"
        assert directory == store.directory

    def test_stale_index_entry_falls_back_to_scan(self, ctx_env):
        import os

        from utils.context_store import get_store_path

        store = _make_root()
        save_store(store)
        update_index(store.directory, store.store_id)

        os.remove(get_store_path(store.directory, store.store_id))

        result = resolve_store_location("mystore")
        assert result is None

    def test_unknown_store_with_empty_context_dir_returns_none(self, ctx_env):
        result = resolve_store_location("completely-unknown-store-xyz")
        assert result is None


# ---------------------------------------------------------------------------
# TestArmedOperationsEdgeCases
# ---------------------------------------------------------------------------


class TestArmedOperationsEdgeCases:
    def test_arm_same_directory_twice_second_wins(self, ctx_env):
        arm_store("/home/user/project", "store-a")
        arm_store("/home/user/project", "store-b")

        result = get_armed_store("/home/user/project")
        assert result == "store-b"

    def test_list_armed_stores_when_empty_returns_empty_dict(self, ctx_env):
        result = list_armed_stores()
        assert result == {}


# ---------------------------------------------------------------------------
# TestListStoresEdgeCases
# ---------------------------------------------------------------------------


class TestListStoresEdgeCases:
    def test_multiple_stores_in_same_directory(self, ctx_env):
        store_a = _make_root(store_id="store-a")
        store_b = _make_root(store_id="store-b")
        save_store(store_a)
        save_store(store_b)

        results = list_stores(directory="/home/user/project")
        ids = {s.store_id for s in results}
        assert "store-a" in ids
        assert "store-b" in ids
        assert len(results) == 2

    def test_directory_with_no_json_files_returns_empty(self, ctx_env):
        from utils.context_store import get_store_dir

        folder = get_store_dir("/home/user/project")
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "not-a-store.txt"), "w") as f:
            f.write("irrelevant content")

        results = list_stores(directory="/home/user/project")
        assert results == []


# ---------------------------------------------------------------------------
# TestListAllDirectories
# ---------------------------------------------------------------------------


class TestListAllDirectories:
    def test_returns_indexed_directories(self, ctx_env):
        from utils.context_store import list_all_directories

        store = _make_root(store_id="store-a", directory="/home/user/proj-a")
        save_store(store)
        update_index(store.directory, store.store_id)

        result = list_all_directories()
        assert "/home/user/proj-a" in result

    def test_returns_unindexed_directories_via_scan(self, ctx_env):
        from utils.context_store import list_all_directories

        store = _make_root(store_id="store-b", directory="/home/user/proj-b")
        save_store(store)

        result = list_all_directories()
        assert "/home/user/proj-b" in result

    def test_empty_context_directory_returns_empty(self, ctx_env):
        from utils.context_store import list_all_directories

        result = list_all_directories()
        assert result == []


# ---------------------------------------------------------------------------
# TestHydrateThreadContext
# ---------------------------------------------------------------------------


class TestHydrateThreadContext:
    def test_ancestor_with_content_used_as_user_turn(self, ctx_env):
        from utils.context_builder import hydrate_thread_context

        store = _make_root()
        node = StoreNode(
            entry_type="store",
            timestamp="2026-01-01T00:00:00Z",
            prompt="short prompt",
            response="the response",
            content="FULL CONTENT BLOB",
        )
        store.children["L1"] = node
        update_index(store.directory, store.store_id)

        thread = hydrate_thread_context(store, "mystore.L1")
        assert len(thread.turns) == 1
        assert thread.turns[0].role == "user"
        assert thread.turns[0].content == "FULL CONTENT BLOB"

    def test_ancestor_without_content_yields_separate_turns(self, ctx_env):
        from utils.context_builder import hydrate_thread_context

        store = _make_root()
        node = _make_node(entry_type="query", prompt="the question", response="the answer")
        store.children["L1"] = node
        update_index(store.directory, store.store_id)

        thread = hydrate_thread_context(store, "mystore.L1")
        assert len(thread.turns) == 2
        assert thread.turns[0].role == "user"
        assert thread.turns[0].content == "the question"
        assert thread.turns[1].role == "assistant"
        assert thread.turns[1].content == "the answer"

    def test_fork_nodes_are_skipped(self, ctx_env):
        from utils.context_builder import hydrate_thread_context

        store = _make_root()
        n_store = _make_node(entry_type="store", prompt="q1", response="r1")
        n_fork = _make_node(entry_type="fork", prompt="", response="")
        n_query = _make_node(entry_type="query", prompt="q2", response="r2")

        n_fork.children["Q0"] = n_query
        n_store.children["F0"] = n_fork
        store.children["L1"] = n_store
        update_index(store.directory, store.store_id)

        thread = hydrate_thread_context(store, "mystore.L1.F0.Q0")
        roles = [t.role for t in thread.turns]
        assert "fork" not in [t.content for t in thread.turns]
        assert roles == ["user", "assistant", "user", "assistant"]

    def test_root_path_yields_empty_thread(self, ctx_env):
        from utils.context_builder import hydrate_thread_context

        store = _make_root()
        update_index(store.directory, store.store_id)

        thread = hydrate_thread_context(store, "mystore")
        assert len(thread.turns) == 0

    def test_runtime_error_when_get_thread_returns_none(self, ctx_env, monkeypatch):
        import utils.conversation_memory as cm
        from utils.context_builder import hydrate_thread_context

        monkeypatch.setattr(cm, "get_thread", lambda thread_id: None)

        store = _make_root()
        update_index(store.directory, store.store_id)

        with pytest.raises(RuntimeError, match="Failed to retrieve hydrated thread"):
            hydrate_thread_context(store, "mystore")


# ---------------------------------------------------------------------------
# TestBuildContextAdditional
# ---------------------------------------------------------------------------


class TestBuildContextAdditional:
    def test_node_with_prompt_but_empty_response_filtered_out(self):
        from utils.context_builder import build_context_from_ancestry

        node = _make_node(entry_type="store", prompt="question only", response="")
        result = build_context_from_ancestry([node])
        assert result == ""

    def test_node_with_empty_prompt_but_response_filtered_out(self):
        from utils.context_builder import build_context_from_ancestry

        node = _make_node(entry_type="query", prompt="", response="answer only")
        result = build_context_from_ancestry([node])
        assert result == ""

    def test_five_node_chain_turns_numbered_one_through_five(self):
        from utils.context_builder import build_context_from_ancestry

        nodes = [
            _make_node(entry_type="store", prompt=f"q{i}", response=f"r{i}") for i in range(1, 6)
        ]
        result = build_context_from_ancestry(nodes)
        for i in range(1, 6):
            assert f"Turn {i}" in result

    def test_multi_node_chain_ordering_preserved(self):
        from utils.context_builder import build_context_from_ancestry

        nodes = [
            _make_node(entry_type="store", prompt="first", response="f-resp"),
            _make_node(entry_type="query", prompt="second", response="s-resp"),
            _make_node(entry_type="tool", prompt="third", response="t-resp"),
        ]
        result = build_context_from_ancestry(nodes)
        assert result.index("first") < result.index("second") < result.index("third")
        assert "Turn 1" in result
        assert "Turn 3" in result


# ---------------------------------------------------------------------------
# TestGetLastLayerPath
# ---------------------------------------------------------------------------


class TestGetLastLayerPath:
    def test_empty_store_returns_none(self, ctx_env):
        store = _make_root()
        assert get_last_layer_path(store) is None

    def test_single_layer(self, ctx_env):
        store = _make_root()
        store.children["L1"] = _make_node()
        assert get_last_layer_path(store) == "mystore.L1"

    def test_multiple_layers(self, ctx_env):
        store = _make_root()
        store.children["L1"] = _make_node()
        store.children["L2"] = _make_node()
        store.children["L3"] = _make_node()
        assert get_last_layer_path(store) == "mystore.L3"

    def test_ignores_non_l_children(self, ctx_env):
        store = _make_root()
        store.children["L1"] = _make_node()
        store.children["L2"] = _make_node()
        store.children["F0"] = _make_node(entry_type="fork")
        assert get_last_layer_path(store) == "mystore.L2"

    def test_gap_in_numbering(self, ctx_env):
        store = _make_root()
        store.children["L1"] = _make_node()
        store.children["L5"] = _make_node()
        # count is 2, so returns L2 — assumes contiguous numbering
        assert get_last_layer_path(store) == "mystore.L2"
