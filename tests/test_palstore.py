"""
Unit tests for utils/palstore.py and utils/palstore_builder.py.
"""

import os

import pytest

from utils.palstore import (
    PalNode,
    PalRoot,
    add_palnode,
    arm_store,
    collect_palnode_files,
    copy_palnode,
    detach_palnode,
    disarm_store,
    encode_directory,
    find_palnode_ancestor,
    fold_palnode_range,
    get_armed_store,
    get_last_layer_path,
    get_next_key,
    is_fork_ancestor,
    is_l_ancestor,
    list_armed_stores,
    list_stores,
    load_store,
    move_palnode,
    parse_store_path,
    rebuild_index,
    resolve_layer_insertion_point,
    resolve_palnode,
    resolve_root_alias,
    resolve_store_location,
    save_store,
    update_index,
    walk_palnode_ancestry,
    walk_palnode_range,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def ctx_env(tmp_path, monkeypatch):
    from utils import palstore

    ctx_dir = str(tmp_path / "context")
    monkeypatch.setattr(palstore, "_CTX_DIR", ctx_dir)
    monkeypatch.setattr(palstore, "_INDEX_PATH", os.path.join(ctx_dir, "store-index.json"))
    monkeypatch.setattr(palstore, "_ARMED_PATH", os.path.join(ctx_dir, "armed.json"))
    return ctx_dir


def _make_node(entry_type="store", prompt="", response="", files=None, label=None) -> PalNode:
    return PalNode(
        entry_type=entry_type,
        label=label,
        timestamp="2026-01-01T00:00:00Z",
        prompt=prompt,
        response=response,
        files=files or [],
    )


def _make_root(store_id="mystore", directory="/home/user/project") -> PalRoot:
    return PalRoot(
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
        from utils import palstore

        store = _make_root()
        path = palstore.get_store_path(store.directory, store.store_id)
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

        result = resolve_palnode(store, "mystore.L1")
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

        result = resolve_palnode(store, "mystore.L1.Q0.F0.thinkdeep")
        assert result is not None
        assert result.prompt == "tdp"

    def test_resolve_root_only_returns_none(self, ctx_env):
        store = _make_root()
        result = resolve_palnode(store, "mystore")
        assert result is None

    def test_resolve_nonexistent_path_returns_none(self, ctx_env):
        store = _make_root()
        result = resolve_palnode(store, "mystore.Z99")
        assert result is None


# ---------------------------------------------------------------------------
# TestAddChild
# ---------------------------------------------------------------------------


class TestAddChild:
    def test_add_palnode_to_root(self, ctx_env):
        store = _make_root()
        child = _make_node(entry_type="query", prompt="q", response="r")

        full_path = add_palnode(store, "mystore", "L1", child)
        assert full_path == "mystore.L1"
        assert "L1" in store.children

    def test_add_palnode_to_nested_node(self, ctx_env):
        store = _make_root()
        l1 = _make_node(entry_type="query", prompt="p", response="r")
        store.children["L1"] = l1
        update_index(store.directory, store.store_id)

        grandchild = _make_node(entry_type="tool", prompt="gp", response="gr")
        full_path = add_palnode(store, "mystore.L1", "Q0", grandchild)
        assert full_path == "mystore.L1.Q0"
        assert "Q0" in store.children["L1"].children

    def test_add_palnode_to_nonexistent_parent_raises(self, ctx_env):
        store = _make_root()
        child = _make_node()

        with pytest.raises(KeyError, match="parent path not found"):
            add_palnode(store, "mystore.Z99", "L1", child)


# ---------------------------------------------------------------------------
# TestWalkAncestry
# ---------------------------------------------------------------------------


class TestWalkAncestry:
    def test_walk_to_direct_child(self, ctx_env):
        store = _make_root()
        child = _make_node(entry_type="query", prompt="p", response="r")
        store.children["L1"] = child

        result = walk_palnode_ancestry(store, "mystore.L1")
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

        result = walk_palnode_ancestry(store, "mystore.L1.Q0.F0.thinkdeep")
        assert len(result) == 4
        assert result[0].prompt == "l1p"
        assert result[1].prompt == "q0p"
        assert result[2].entry_type == "fork"
        assert result[3].prompt == "tdp"

    def test_walk_to_root_returns_empty(self, ctx_env):
        store = _make_root()
        result = walk_palnode_ancestry(store, "mystore")
        assert result == []

    def test_fork_nodes_included_in_ancestry(self, ctx_env):
        store = _make_root()
        fork = _make_node(entry_type="fork", prompt="", response="")
        child = _make_node(entry_type="query", prompt="cp", response="cr")

        fork.children["L1"] = child
        store.children["F0"] = fork

        update_index(store.directory, store.store_id)

        result = walk_palnode_ancestry(store, "mystore.F0.L1")
        assert len(result) == 2
        assert result[0].entry_type == "fork"
        assert result[1].prompt == "cp"

    def test_cumulative_layers_at_root(self, ctx_env):
        store = _make_root()
        store.children["L0"] = _make_node(entry_type="store", prompt="p0", response="r0")
        store.children["L1"] = _make_node(entry_type="store", prompt="p1", response="r1")
        store.children["L2"] = _make_node(entry_type="store", prompt="p2", response="r2")

        update_index(store.directory, store.store_id)

        result = walk_palnode_ancestry(store, "mystore.L2")
        assert len(result) == 3
        assert result[0].prompt == "p0"
        assert result[1].prompt == "p1"
        assert result[2].prompt == "p2"

    def test_cumulative_layers_with_fork_descendant(self, ctx_env):
        store = _make_root()
        store.children["L0"] = _make_node(entry_type="store", prompt="p0", response="r0")
        store.children["L1"] = _make_node(entry_type="store", prompt="p1", response="r1")
        l2 = _make_node(entry_type="store", prompt="p2", response="r2")
        fork = _make_node(entry_type="fork", prompt="", response="")
        l2.children["F0"] = fork
        store.children["L2"] = l2

        update_index(store.directory, store.store_id)

        result = walk_palnode_ancestry(store, "mystore.L2.F0")
        assert len(result) == 4
        assert result[0].prompt == "p0"
        assert result[1].prompt == "p1"
        assert result[2].prompt == "p2"
        assert result[3].entry_type == "fork"

    def test_cumulative_layers_nested_under_fork(self, ctx_env):
        store = _make_root()
        fork = _make_node(entry_type="fork", prompt="", response="")
        fork.children["L0"] = _make_node(entry_type="store", prompt="fp0", response="fr0")
        fork.children["L1"] = _make_node(entry_type="store", prompt="fp1", response="fr1")
        store.children["F0"] = fork

        update_index(store.directory, store.store_id)

        result = walk_palnode_ancestry(store, "mystore.F0.L1")
        assert len(result) == 3
        assert result[0].entry_type == "fork"
        assert result[1].prompt == "fp0"
        assert result[2].prompt == "fp1"


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
        from utils.palstore import load_index

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
        from utils import palstore

        store = _make_root()
        save_store(store)

        # Drop a corrupt JSON file in the same directory
        folder = palstore.get_store_dir(store.directory)
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
        from utils.palstore_builder import build_context_from_ancestry

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
        from utils.palstore_builder import build_context_from_ancestry

        fork = _make_node(entry_type="fork", prompt="", response="")
        content = _make_node(entry_type="query", prompt="real question", response="real answer")
        nodes = [fork, content]

        result = build_context_from_ancestry(nodes)
        assert "real question" in result
        assert "Turn 1" in result
        # Only one turn since fork is skipped
        assert "Turn 2" not in result

    def test_build_context_returns_empty_for_empty_list(self):
        from utils.palstore_builder import build_context_from_ancestry

        result = build_context_from_ancestry([])
        assert result == ""

    def test_build_context_returns_empty_when_all_forks(self):
        from utils.palstore_builder import build_context_from_ancestry

        nodes = [
            _make_node(entry_type="fork", prompt="", response=""),
            _make_node(entry_type="fork", prompt="", response=""),
        ]

        result = build_context_from_ancestry(nodes)
        assert result == ""

    def test_build_context_includes_file_listing(self):
        from utils.palstore_builder import build_context_from_ancestry

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
        from utils.palstore_builder import build_context_from_ancestry

        node = _make_node(
            entry_type="query",
            prompt="with files",
            response="got it",
            files=["/home/user/foo.py"],
        )

        result = build_context_from_ancestry([node], include_files=False)
        assert "FILES REFERENCED" not in result

    def test_build_context_deduplicates_files_across_nodes(self):
        from utils.palstore_builder import build_context_from_ancestry

        node_a = _make_node(entry_type="query", prompt="q1", response="r1", files=["/shared.py", "/a.py"])
        node_b = _make_node(entry_type="query", prompt="q2", response="r2", files=["/shared.py", "/b.py"])

        result = build_context_from_ancestry([node_a, node_b], include_files=True)
        # /shared.py must appear exactly once
        assert result.count("/shared.py") == 1

    def test_file_blob_not_duplicated_in_cumulative_ancestry(self):
        """Regression: when layers share a file via content blobs, the same
        file body must NOT appear multiple times in the reconstructed history.
        This is the core invariant that diff-based dedup protects."""
        from utils.file_diff import decide_file_representation
        from utils.palstore_builder import build_context_from_ancestry

        file_body = "class Foo:\n    pass\n"
        mtime = "2026-01-01 00:00:00 UTC"

        # L1: first occurrence — full file embed
        l1_file_block = decide_file_representation("/app.py", file_body, None, None, mtime)
        l1_prompt = f"=== CONTEXT LAYER SUBMISSION ===\n\nsetup\n\n=== CONTEXT FILES ===\n{l1_file_block}\n=== END CONTEXT FILES ==="
        l1 = PalNode(
            entry_type="store",
            timestamp="2026-01-01T00:00:00Z",
            files=["/app.py"],
            prompt="setup",
            response="stored",
            content=f"{l1_prompt}\n\n---\n\nstored",
        )

        # L2: same file unchanged — should be omitted
        l2_file_block = decide_file_representation("/app.py", file_body, file_body, "L1", mtime)
        assert l2_file_block == "", "unchanged file should produce empty representation"
        l2_prompt = "=== CONTEXT LAYER SUBMISSION ===\n\nupdate"
        l2 = PalNode(
            entry_type="store",
            timestamp="2026-01-02T00:00:00Z",
            files=["/app.py"],
            prompt="update",
            response="ok",
            content=f"{l2_prompt}\n\n---\n\nok",
        )

        history = build_context_from_ancestry([l1, l2])
        assert history.count("class Foo:") == 1, f"file body appeared {history.count('class Foo:')} times, expected 1"

    def test_small_file_change_produces_diff_not_full_duplicate(self):
        """When a file has a small addition, the second layer should contain
        a DIFF marker, not a second full copy of the file."""
        from utils.file_diff import decide_file_representation

        old = "line1\nline2\nline3\nline4\nline5\nline6\nline7\nline8\nline9\nline10\n"
        new = old + "added_line\n"

        rep = decide_file_representation("/big.py", new, old, "L1", "2026-01-01 00:00:00 UTC")
        assert "BEGIN DIFF:" in rep, "small addition should produce a DIFF block"
        assert "BEGIN FILE:" not in rep, "small addition should NOT produce a full FILE block"
        assert "+added_line" in rep

    def test_large_file_change_embeds_full_file(self):
        """When >=50% of a file changes by tokens, the full file should be
        re-embedded rather than a diff."""
        from utils.file_diff import decide_file_representation

        old = "x\n"
        new = "a\nb\nc\nd\ne\nf\ng\nh\ni\nj\n"

        rep = decide_file_representation("/big.py", new, old, "L1", "2026-01-01 00:00:00 UTC")
        assert "BEGIN FILE:" in rep, "major rewrite should produce a full FILE block"
        assert "BEGIN DIFF:" not in rep

    def test_ancestry_file_state_tracks_across_layers(self):
        """build_file_state_from_ancestry should return the latest version
        of each file across the ancestor chain."""
        from utils.file_diff import build_file_state_from_ancestry

        def _blob(path, content, resp="r"):
            prompt = (
                f"=== CONTEXT LAYER SUBMISSION ===\n\ntext"
                f"\n\n=== CONTEXT FILES ===\n"
                f"\n--- BEGIN FILE: {path} (Last modified: 2026-01-01 00:00:00 UTC) ---\n"
                f"{content}\n"
                f"--- END FILE: {path} ---\n"
                f"\n=== END CONTEXT FILES ==="
            )
            return f"{prompt}\n\n---\n\n{resp}"

        n1 = PalNode(
            entry_type="store",
            timestamp="2026-01-01T00:00:00Z",
            files=["/a.py"],
            prompt="p",
            response="r",
            content=_blob("/a.py", "v1"),
        )
        n2 = PalNode(
            entry_type="store",
            timestamp="2026-01-02T00:00:00Z",
            files=["/a.py", "/b.py"],
            prompt="p",
            response="r",
            content=_blob("/a.py", "v2") + _blob("/b.py", "b_content"),
        )
        n3 = PalNode(
            entry_type="store",
            timestamp="2026-01-03T00:00:00Z",
            files=["/b.py"],
            prompt="p",
            response="r",
            content=_blob("/b.py", "b_v2"),
        )

        state = build_file_state_from_ancestry([n1, n2, n3])
        assert state["/a.py"] == "v2", "should have latest version from n2"
        assert state["/b.py"] == "b_v2", "should have latest version from n3"

    def test_migrated_store_round_trips_through_context_builder(self):
        """After migration replaces duplicates with diffs, the context
        builder should still reconstruct valid conversation history without
        duplicate file content."""
        from scripts.migrate_to_diff_stores import migrate_store
        from utils.palstore_builder import build_context_from_ancestry

        file_body = "def hello():\n    print('hi')\n"

        def _make_layer(prompt_text, files_dict, response):
            parts = []
            for path, content in files_dict.items():
                parts.append(
                    f"\n--- BEGIN FILE: {path} (Last modified: 2026-01-01 00:00:00 UTC) ---\n"
                    f"{content}\n"
                    f"--- END FILE: {path} ---\n"
                )
            file_section = f"\n\n=== CONTEXT FILES ===\n{''.join(parts)}\n=== END CONTEXT FILES ===" if parts else ""
            full_prompt = f"=== CONTEXT LAYER SUBMISSION ===\n\n{prompt_text}{file_section}"
            return f"{full_prompt}\n\n---\n\n{response}"

        store = PalRoot(
            store_id="regression",
            directory="/tmp/test",
            created_at="2026-01-01T00:00:00Z",
            children={
                "L1": PalNode(
                    entry_type="store",
                    timestamp="2026-01-01T00:00:00Z",
                    files=["/app.py"],
                    prompt="initial",
                    response="stored L1",
                    content=_make_layer("initial", {"/app.py": file_body}, "stored L1"),
                ),
                "L2": PalNode(
                    entry_type="store",
                    timestamp="2026-01-02T00:00:00Z",
                    files=["/app.py"],
                    prompt="update",
                    response="stored L2",
                    content=_make_layer("update", {"/app.py": file_body}, "stored L2"),
                ),
                "L3": PalNode(
                    entry_type="store",
                    timestamp="2026-01-03T00:00:00Z",
                    files=["/app.py"],
                    prompt="final",
                    response="stored L3",
                    content=_make_layer("final", {"/app.py": file_body}, "stored L3"),
                ),
            },
        )

        savings, modified = migrate_store(store, dry_run=False)
        assert savings > 0, "migration should yield token savings"
        assert modified == 2, "L2 and L3 should be modified"

        # Rebuild context from all 3 layers
        ancestors = [store.children["L1"], store.children["L2"], store.children["L3"]]
        history = build_context_from_ancestry(ancestors)

        # The file body should appear exactly once (in L1's turn)
        assert (
            history.count("def hello():") == 1
        ), f"file body appeared {history.count('def hello():')} times after migration, expected 1"
        # All responses should still be present
        assert "stored L1" in history
        assert "stored L2" in history
        assert "stored L3" in history

    def test_build_context_ends_with_end_marker(self):
        from utils.palstore_builder import build_context_from_ancestry

        node = _make_node(entry_type="query", prompt="q", response="r")
        result = build_context_from_ancestry([node])
        assert result.strip().endswith("=== END CONVERSATION HISTORY ===")

    def test_build_context_uses_content_for_user_turn(self):
        from utils.palstore_builder import build_context_from_ancestry

        full_prompt = "user text\n\n=== CONTEXT FILES ===\nFILE BLOB CONTENT HERE\n=== END CONTEXT FILES ==="
        raw_response = "assistant replied"
        node = PalNode(
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
        from utils.palstore_builder import build_context_from_ancestry

        node = _make_node(entry_type="store", prompt="bare prompt", response="response")
        result = build_context_from_ancestry([node])
        assert "bare prompt" in result

    def test_build_context_split_uses_first_separator_only(self):
        from utils.palstore_builder import build_context_from_ancestry

        tricky_prompt = "before\n\n---\n\nstill part of prompt"
        response = "answer"
        node = PalNode(
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
        from utils.palstore import get_store_dir

        result = get_store_dir("/home/user/project")
        assert result == os.path.join(ctx_env, "-home-user-project")

    def test_get_store_path_ends_with_json(self, ctx_env):
        from utils.palstore import get_store_path

        result = get_store_path("/home/user/project", "mystore")
        assert result.endswith("mystore.json")


# ---------------------------------------------------------------------------
# TestStoreLifecycleEdgeCases
# ---------------------------------------------------------------------------


class TestStoreLifecycleEdgeCases:
    def test_load_store_with_list_json_returns_none(self, ctx_env):
        from utils import palstore

        store = _make_root()
        path = palstore.get_store_path(store.directory, store.store_id)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            import json

            json.dump([1, 2, 3], f)

        result = load_store(store.directory, store.store_id)
        assert result is None

    def test_load_store_with_missing_required_fields_returns_none(self, ctx_env):
        from utils import palstore

        store = _make_root()
        path = palstore.get_store_path(store.directory, store.store_id)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            import json

            json.dump({"unrelated_field": "value"}, f)

        result = load_store(store.directory, store.store_id)
        assert result is None

    def test_save_store_creates_nested_directories(self, ctx_env):
        from utils import palstore

        store = _make_root(directory="/some/deeply/nested/path")
        save_store(store)

        path = palstore.get_store_path(store.directory, store.store_id)
        assert os.path.exists(path)


# ---------------------------------------------------------------------------
# TestRenameStore
# ---------------------------------------------------------------------------


class TestRenameStore:
    def test_rename_happy_path(self, ctx_env):
        from utils.palstore import get_store_path, load_index, rename_store

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
        from utils.palstore import rename_store

        with pytest.raises(KeyError, match="Store not found"):
            rename_store("/home/user/project", "ghost-store", "newname")

    def test_rename_new_id_with_dots_raises_value_error(self, ctx_env):
        from utils.palstore import rename_store

        store = _make_root()
        save_store(store)

        with pytest.raises(ValueError, match="dots"):
            rename_store(store.directory, "mystore", "new.name")

    def test_rename_updates_armed_state(self, ctx_env):
        from utils.palstore import rename_store

        store = _make_root()
        save_store(store)
        update_index(store.directory, store.store_id)
        arm_store(store.directory, "mystore")

        rename_store(store.directory, "mystore", "renamed-store")

        assert get_armed_store(store.directory) == "renamed-store"

    def test_rename_when_store_not_armed_is_noop_for_armed_state(self, ctx_env):
        from utils.palstore import rename_store

        store = _make_root()
        save_store(store)
        update_index(store.directory, store.store_id)
        arm_store("/home/OTHER/project", "other-store")

        rename_store(store.directory, "mystore", "renamed-store")

        assert get_armed_store("/home/OTHER/project") == "other-store"
        assert get_armed_store(store.directory) is None

    def test_rename_preserves_children(self, ctx_env):
        from utils.palstore import rename_store

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

        result = resolve_palnode(store, "mystore.L1.Q99")
        assert result is None

    def test_empty_children_at_intermediate_node_returns_none(self, ctx_env):
        store = _make_root()
        child = _make_node(entry_type="query", prompt="p", response="r")
        store.children["L1"] = child
        update_index(store.directory, store.store_id)

        result = resolve_palnode(store, "mystore.L1.Q0")
        assert result is None


# ---------------------------------------------------------------------------
# TestAddChildEdgeCases
# ---------------------------------------------------------------------------


class TestAddChildEdgeCases:
    def test_overwrite_existing_child_key_silently(self, ctx_env):
        store = _make_root()
        original = _make_node(entry_type="store", prompt="original", response="old")
        replacement = _make_node(entry_type="query", prompt="new prompt", response="new response")

        add_palnode(store, "mystore", "L1", original)
        add_palnode(store, "mystore", "L1", replacement)

        assert store.children["L1"].prompt == "new prompt"
        assert store.children["L1"].entry_type == "query"

    def test_deeply_nested_add_three_levels(self, ctx_env):
        store = _make_root()

        n1 = _make_node(entry_type="store", prompt="l1", response="r1")
        add_palnode(store, "mystore", "L1", n1)
        update_index(store.directory, store.store_id)

        n2 = _make_node(entry_type="fork", prompt="", response="")
        path2 = add_palnode(store, "mystore.L1", "F0", n2)
        assert path2 == "mystore.L1.F0"

        n3 = _make_node(entry_type="tool", prompt="tool-p", response="tool-r")
        path3 = add_palnode(store, "mystore.L1.F0", "thinkdeep", n3)
        assert path3 == "mystore.L1.F0.thinkdeep"

        assert store.children["L1"].children["F0"].children["thinkdeep"].prompt == "tool-p"


# ---------------------------------------------------------------------------
# TestWalkAncestryEdgeCases
# ---------------------------------------------------------------------------


class TestWalkAncestryEdgeCases:
    def test_partial_walk_first_valid_second_missing(self, ctx_env):
        store = _make_root()
        child = _make_node(entry_type="store", prompt="p1", response="r1")
        store.children["L1"] = child
        update_index(store.directory, store.store_id)

        result = walk_palnode_ancestry(store, "mystore.L1.Q0")
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

        result = walk_palnode_ancestry(store, "mystore.L1.F0.Q0.thinkdeep")
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

        from utils.palstore import get_store_path

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
        from utils.palstore import get_store_dir

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
        from utils.palstore import list_all_directories

        store = _make_root(store_id="store-a", directory="/home/user/proj-a")
        save_store(store)
        update_index(store.directory, store.store_id)

        result = list_all_directories()
        assert "/home/user/proj-a" in result

    def test_returns_unindexed_directories_via_scan(self, ctx_env):
        from utils.palstore import list_all_directories

        store = _make_root(store_id="store-b", directory="/home/user/proj-b")
        save_store(store)

        result = list_all_directories()
        assert "/home/user/proj-b" in result

    def test_empty_context_directory_returns_empty(self, ctx_env):
        from utils.palstore import list_all_directories

        result = list_all_directories()
        assert result == []


# ---------------------------------------------------------------------------
# TestHydrateThreadContext
# ---------------------------------------------------------------------------


class TestHydrateThreadContext:
    def test_ancestor_with_content_used_as_user_turn(self, ctx_env):
        from utils.palstore_builder import hydrate_thread_context

        store = _make_root()
        node = PalNode(
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
        from utils.palstore_builder import hydrate_thread_context

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
        from utils.palstore_builder import hydrate_thread_context

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
        from utils.palstore_builder import hydrate_thread_context

        store = _make_root()
        update_index(store.directory, store.store_id)

        thread = hydrate_thread_context(store, "mystore")
        assert len(thread.turns) == 0

    def test_runtime_error_when_get_thread_returns_none(self, ctx_env, monkeypatch):
        import utils.conversation_memory as cm
        from utils.palstore_builder import hydrate_thread_context

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
        from utils.palstore_builder import build_context_from_ancestry

        node = _make_node(entry_type="store", prompt="question only", response="")
        result = build_context_from_ancestry([node])
        assert result == ""

    def test_node_with_empty_prompt_but_response_filtered_out(self):
        from utils.palstore_builder import build_context_from_ancestry

        node = _make_node(entry_type="query", prompt="", response="answer only")
        result = build_context_from_ancestry([node])
        assert result == ""

    def test_five_node_chain_turns_numbered_one_through_five(self):
        from utils.palstore_builder import build_context_from_ancestry

        nodes = [_make_node(entry_type="store", prompt=f"q{i}", response=f"r{i}") for i in range(1, 6)]
        result = build_context_from_ancestry(nodes)
        for i in range(1, 6):
            assert f"Turn {i}" in result

    def test_multi_node_chain_ordering_preserved(self):
        from utils.palstore_builder import build_context_from_ancestry

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
        # Returns the highest-indexed L-node
        assert get_last_layer_path(store) == "mystore.L5"


# ---------------------------------------------------------------------------
# TestResolveRootAlias
# ---------------------------------------------------------------------------


class TestResolveRootAlias:
    def test_non_root_passes_through(self, ctx_env):
        store = _make_root()
        store.children["L1"] = _make_node()
        assert resolve_root_alias(store, "mystore.L1") == "mystore.L1"

    def test_root_resolves_to_last_layer(self, ctx_env):
        store = _make_root()
        store.children["L1"] = _make_node()
        store.children["L2"] = _make_node()
        store.children["L3"] = _make_node()
        assert resolve_root_alias(store, "mystore") == "mystore.L3"

    def test_root_with_no_layers_returns_passthrough(self, ctx_env):
        store = _make_root()
        assert resolve_root_alias(store, "mystore") == "mystore"


# ---------------------------------------------------------------------------
# TestNodeRulesMatrix
# ---------------------------------------------------------------------------


def _build_parent_scaffold(store, parent_type):
    """Return the parent_path string for add_palnode tests given parent_type."""
    if parent_type == "root":
        return store.store_id
    if parent_type == "store":
        store.children["L1"] = _make_node(entry_type="store")
        return "mystore.L1"
    if parent_type == "query":
        store.children["L1"] = _make_node(entry_type="store")
        store.children["L1"].children["Q0"] = _make_node(entry_type="query")
        return "mystore.L1.Q0"
    if parent_type == "fork":
        store.children["F0"] = _make_node(entry_type="fork")
        return "mystore.F0"
    if parent_type == "tool":
        store.children["F0"] = _make_node(entry_type="fork")
        store.children["F0"].children["thinkdeep"] = _make_node(entry_type="tool")
        return "mystore.F0.thinkdeep"
    raise ValueError(f"Unknown parent_type: {parent_type}")


class TestNodeRulesMatrix:
    @pytest.mark.parametrize(
        "parent_type,child_key,child_entry_type,description",
        [
            ("root", "L1", "store", "L at root"),
            ("root", "F0", "fork", "F at root"),
            ("store", "Q0", "query", "Q at store"),
            ("store", "F0", "fork", "F at store"),
            ("query", "Q0", "query", "Q at query"),
            ("query", "1", "query", "numeric at query"),
            ("query", "F0", "fork", "F at query"),
            ("fork", "L1", "store", "L at fork"),
            ("fork", "Q0", "query", "Q at fork"),
            ("fork", "F0", "fork", "F at fork"),
            ("fork", "thinkdeep", "tool", "tool at fork"),
            ("tool", "F0", "fork", "F at tool"),
            ("tool", "1", "query", "numeric at tool"),
        ],
    )
    def test_valid_child(self, ctx_env, parent_type, child_key, child_entry_type, description):
        store = _make_root()
        parent_path = _build_parent_scaffold(store, parent_type)
        child = _make_node(entry_type=child_entry_type)
        result = add_palnode(store, parent_path, child_key, child)
        assert result == f"{parent_path}.{child_key}"

    @pytest.mark.parametrize(
        "parent_type,child_key,child_entry_type,description",
        [
            ("root", "Q0", "query", "Q at root"),
            ("root", "1", "query", "numeric at root"),
            ("root", "thinkdeep", "tool", "tool at root"),
            ("store", "L1", "store", "L at store"),
            ("store", "1", "query", "numeric at store"),
            ("store", "thinkdeep", "tool", "tool at store"),
            ("query", "L1", "store", "L at query"),
            ("query", "thinkdeep", "tool", "tool at query"),
            ("tool", "L1", "store", "L at tool"),
            ("tool", "Q0", "query", "Q at tool"),
            ("tool", "thinkdeep", "tool", "tool at tool"),
            ("fork", "1", "query", "numeric at fork"),
        ],
    )
    def test_invalid_child(self, ctx_env, parent_type, child_key, child_entry_type, description):
        store = _make_root()
        parent_path = _build_parent_scaffold(store, parent_type)
        child = _make_node(entry_type=child_entry_type)
        with pytest.raises(ValueError):
            add_palnode(store, parent_path, child_key, child)


# ---------------------------------------------------------------------------
# TestReferenceTree
# ---------------------------------------------------------------------------


@pytest.fixture
def ref_tree(ctx_env):
    """Build the reference tree using add_palnode for every node."""
    store = _make_root()
    update_index(store.directory, store.store_id)

    # Root level: L1, L2, L3, F0
    add_palnode(store, "mystore", "L1", _make_node(entry_type="store", prompt="p1"))
    add_palnode(store, "mystore", "L2", _make_node(entry_type="store", prompt="p2"))
    add_palnode(store, "mystore", "L3", _make_node(entry_type="store", prompt="p3"))
    add_palnode(store, "mystore", "F0", _make_node(entry_type="fork"))

    # L1 subtree
    add_palnode(store, "mystore.L1", "Q0", _make_node(entry_type="query", prompt="what is X?"))
    add_palnode(store, "mystore.L1", "F0", _make_node(entry_type="fork"))

    # L1.Q0 subtree
    add_palnode(store, "mystore.L1.Q0", "Q0", _make_node(entry_type="query", prompt="sub-question"))
    add_palnode(store, "mystore.L1.Q0", "1", _make_node(entry_type="query", prompt="follow-up 1"))
    add_palnode(store, "mystore.L1.Q0", "2", _make_node(entry_type="query", prompt="follow-up 2"))
    add_palnode(store, "mystore.L1.Q0", "F0", _make_node(entry_type="fork"))
    add_palnode(store, "mystore.L1.Q0.F0", "thinkdeep", _make_node(entry_type="tool", prompt="td"))

    # L1.F0 subtree
    add_palnode(store, "mystore.L1.F0", "L1", _make_node(entry_type="store", prompt="branch1"))
    add_palnode(store, "mystore.L1.F0", "L2", _make_node(entry_type="store", prompt="branch2"))
    add_palnode(store, "mystore.L1.F0", "F0", _make_node(entry_type="fork"))
    add_palnode(store, "mystore.L1.F0.L1", "Q0", _make_node(entry_type="query", prompt="branch query"))
    add_palnode(store, "mystore.L1.F0.F0", "L1", _make_node(entry_type="store", prompt="deep branch"))

    # L2 subtree
    add_palnode(store, "mystore.L2", "Q0", _make_node(entry_type="query", prompt="query on L2"))
    add_palnode(store, "mystore.L2", "F0", _make_node(entry_type="fork"))
    add_palnode(store, "mystore.L2.F0", "analyze", _make_node(entry_type="tool", prompt="analyze"))
    add_palnode(store, "mystore.L2.F0.analyze", "F0", _make_node(entry_type="fork"))
    add_palnode(store, "mystore.L2.F0.analyze.F0", "chat", _make_node(entry_type="tool", prompt="chat"))

    # Root F0 subtree
    add_palnode(store, "mystore.F0", "L1", _make_node(entry_type="store", prompt="root fork layer"))
    add_palnode(store, "mystore.F0", "Q0", _make_node(entry_type="query", prompt="root fork query"))
    add_palnode(store, "mystore.F0", "thinkdeep", _make_node(entry_type="tool", prompt="root fork td"))

    return store


# ---------------------------------------------------------------------------
# TestWalkAncestryComplex
# ---------------------------------------------------------------------------


class TestWalkAncestryComplex:
    @pytest.mark.parametrize(
        "path,expected_count,expected_types",
        [
            ("mystore.L1", 1, ["store"]),
            ("mystore.L2", 2, ["store", "store"]),
            ("mystore.L3", 3, ["store", "store", "store"]),
            ("mystore.L1.Q0", 2, ["store", "query"]),
            ("mystore.L1.Q0.Q0", 3, ["store", "query", "query"]),
            ("mystore.L1.Q0.1", 3, ["store", "query", "query"]),
            ("mystore.L1.Q0.2", 3, ["store", "query", "query"]),
            ("mystore.L1.Q0.F0", 3, ["store", "query", "fork"]),
            ("mystore.L1.Q0.F0.thinkdeep", 4, ["store", "query", "fork", "tool"]),
            ("mystore.L1.F0", 2, ["store", "fork"]),
            ("mystore.L1.F0.L1", 3, ["store", "fork", "store"]),
            ("mystore.L1.F0.L2", 4, ["store", "fork", "store", "store"]),
            ("mystore.L1.F0.L1.Q0", 4, ["store", "fork", "store", "query"]),
            ("mystore.L1.F0.F0", 3, ["store", "fork", "fork"]),
            ("mystore.L1.F0.F0.L1", 4, ["store", "fork", "fork", "store"]),
            ("mystore.L2.Q0", 3, ["store", "store", "query"]),
            ("mystore.L2.F0", 3, ["store", "store", "fork"]),
            ("mystore.L2.F0.analyze", 4, ["store", "store", "fork", "tool"]),
            ("mystore.L2.F0.analyze.F0", 5, ["store", "store", "fork", "tool", "fork"]),
            ("mystore.L2.F0.analyze.F0.chat", 6, ["store", "store", "fork", "tool", "fork", "tool"]),
            ("mystore.F0", 1, ["fork"]),
            ("mystore.F0.L1", 2, ["fork", "store"]),
            ("mystore.F0.Q0", 2, ["fork", "query"]),
            ("mystore.F0.thinkdeep", 2, ["fork", "tool"]),
        ],
    )
    def test_walk_path(self, ref_tree, path, expected_count, expected_types):
        result = walk_palnode_ancestry(ref_tree, path)
        assert len(result) == expected_count, f"path={path}: expected {expected_count} nodes, got {len(result)}"
        assert [n.entry_type for n in result] == expected_types, f"path={path}: type mismatch"


# ---------------------------------------------------------------------------
# TestWalkRangeComplex
# ---------------------------------------------------------------------------


class TestWalkRangeComplex:
    @pytest.mark.parametrize(
        "start,end,expected_count,expected_types",
        [
            ("mystore.L1", "mystore.L3", 3, ["store", "store", "store"]),
            ("mystore.L1", "mystore.L1.Q0.F0.thinkdeep", 4, ["store", "query", "fork", "tool"]),
            ("mystore.L1.Q0", "mystore.L1.Q0.2", 2, ["query", "query"]),
            ("mystore.L1.Q0", "mystore.L1.Q0.F0.thinkdeep", 3, ["query", "fork", "tool"]),
            ("mystore.L1.F0", "mystore.L1.F0.L2", 3, ["fork", "store", "store"]),
            ("mystore.L1.F0", "mystore.L1.F0.F0.L1", 3, ["fork", "fork", "store"]),
            ("mystore.L2", "mystore.L2.F0.analyze.F0.chat", 5, ["store", "fork", "tool", "fork", "tool"]),
            ("mystore.F0", "mystore.F0.thinkdeep", 2, ["fork", "tool"]),
            ("mystore.L1", "mystore.L2.Q0", 3, ["store", "store", "query"]),
        ],
    )
    def test_valid_range(self, ref_tree, start, end, expected_count, expected_types):
        result = walk_palnode_range(ref_tree, start, end)
        assert len(result) == expected_count, f"start={start}, end={end}: expected {expected_count}, got {len(result)}"
        assert [n.entry_type for n in result] == expected_types, f"start={start}, end={end}: type mismatch"

    @pytest.mark.parametrize(
        "start,end",
        [
            ("mystore.L2", "mystore.L1"),
            ("mystore.L1.Q0", "mystore.L2.Q0"),
            ("mystore.L1.F0.L1", "mystore.L2"),
            ("mystore.F0", "mystore.L1"),
            ("mystore.NONEXIST", "mystore.L1"),
            ("mystore.L1", "mystore.NONEXIST"),
        ],
    )
    def test_invalid_range(self, ref_tree, start, end):
        with pytest.raises(ValueError):
            walk_palnode_range(ref_tree, start, end)


# ---------------------------------------------------------------------------
# TestInsertionOrder
# ---------------------------------------------------------------------------


def _build_minimal_ref_tree(store):
    """Build a subset of the reference tree used for insertion-order checks."""
    add_palnode(store, "mystore", "L1", _make_node(entry_type="store", prompt="p1"))
    add_palnode(store, "mystore", "L2", _make_node(entry_type="store", prompt="p2"))
    add_palnode(store, "mystore", "L3", _make_node(entry_type="store", prompt="p3"))
    add_palnode(store, "mystore", "F0", _make_node(entry_type="fork"))
    add_palnode(store, "mystore.L1", "F0", _make_node(entry_type="fork"))
    add_palnode(store, "mystore.L1.F0", "L1", _make_node(entry_type="store", prompt="branch1"))
    add_palnode(store, "mystore.L1.F0", "L2", _make_node(entry_type="store", prompt="branch2"))
    add_palnode(store, "mystore.L2", "F0", _make_node(entry_type="fork"))
    add_palnode(store, "mystore.L2.F0", "analyze", _make_node(entry_type="tool", prompt="analyze"))
    add_palnode(store, "mystore.L2.F0.analyze", "F0", _make_node(entry_type="fork"))
    add_palnode(store, "mystore.L2.F0.analyze.F0", "chat", _make_node(entry_type="tool", prompt="chat"))


def _assert_insertion_order_queries(store):
    r1 = walk_palnode_ancestry(store, "mystore.L3")
    assert len(r1) == 3
    assert [n.prompt for n in r1] == ["p1", "p2", "p3"]

    r2 = walk_palnode_ancestry(store, "mystore.L1.F0.L2")
    assert len(r2) == 4
    assert [n.entry_type for n in r2] == ["store", "fork", "store", "store"]

    r3 = walk_palnode_ancestry(store, "mystore.L2.F0.analyze.F0.chat")
    assert len(r3) == 6


class TestInsertionOrder:
    def test_forward_order(self, ctx_env):
        store = _make_root()
        update_index(store.directory, store.store_id)
        _build_minimal_ref_tree(store)
        _assert_insertion_order_queries(store)

    def test_reverse_order(self, ctx_env):
        store = _make_root()
        update_index(store.directory, store.store_id)

        # Build bottom-up, right-to-left where possible using direct assignment
        # for nodes that must exist before their parents can be referenced.
        # Nodes at same level can be added in any order; only parent→child ordering matters.
        add_palnode(store, "mystore", "L3", _make_node(entry_type="store", prompt="p3"))
        add_palnode(store, "mystore", "L2", _make_node(entry_type="store", prompt="p2"))
        add_palnode(store, "mystore", "L1", _make_node(entry_type="store", prompt="p1"))
        add_palnode(store, "mystore", "F0", _make_node(entry_type="fork"))

        add_palnode(store, "mystore.L2", "F0", _make_node(entry_type="fork"))
        add_palnode(store, "mystore.L2.F0", "analyze", _make_node(entry_type="tool", prompt="analyze"))
        add_palnode(store, "mystore.L2.F0.analyze", "F0", _make_node(entry_type="fork"))
        add_palnode(store, "mystore.L2.F0.analyze.F0", "chat", _make_node(entry_type="tool", prompt="chat"))

        add_palnode(store, "mystore.L1", "F0", _make_node(entry_type="fork"))
        add_palnode(store, "mystore.L1.F0", "L2", _make_node(entry_type="store", prompt="branch2"))
        add_palnode(store, "mystore.L1.F0", "L1", _make_node(entry_type="store", prompt="branch1"))

        _assert_insertion_order_queries(store)

    def test_interleaved_order(self, ctx_env):
        store = _make_root()
        update_index(store.directory, store.store_id)

        add_palnode(store, "mystore", "L1", _make_node(entry_type="store", prompt="p1"))
        add_palnode(store, "mystore", "L2", _make_node(entry_type="store", prompt="p2"))
        add_palnode(store, "mystore.L1", "F0", _make_node(entry_type="fork"))
        add_palnode(store, "mystore", "L3", _make_node(entry_type="store", prompt="p3"))
        add_palnode(store, "mystore.L2", "F0", _make_node(entry_type="fork"))
        add_palnode(store, "mystore.L1.F0", "L1", _make_node(entry_type="store", prompt="branch1"))
        add_palnode(store, "mystore.L2.F0", "analyze", _make_node(entry_type="tool", prompt="analyze"))
        add_palnode(store, "mystore.L1.F0", "L2", _make_node(entry_type="store", prompt="branch2"))
        add_palnode(store, "mystore.L2.F0.analyze", "F0", _make_node(entry_type="fork"))
        add_palnode(store, "mystore", "F0", _make_node(entry_type="fork"))
        add_palnode(store, "mystore.L2.F0.analyze.F0", "chat", _make_node(entry_type="tool", prompt="chat"))

        _assert_insertion_order_queries(store)


# ---------------------------------------------------------------------------
# TestResolveLayerInsertionPoint
# ---------------------------------------------------------------------------


class TestResolveLayerInsertionPoint:
    @pytest.mark.parametrize(
        "store_id,expected_parent",
        [
            ("mystore", "mystore"),
            ("mystore.L7", "mystore"),
            ("mystore.L2", "mystore"),
            ("mystore.F0", "mystore.F0"),
            ("mystore.F0.L3", "mystore.F0"),
            ("mystore.L1.F0.L2", "mystore.L1.F0"),
            ("mystore.L1.F0.F0.L1", "mystore.L1.F0.F0"),
            ("mystore.L2.Q0", "mystore.L2.Q0"),
            ("mystore.L2.F0.analyze", "mystore.L2.F0.analyze"),
        ],
    )
    def test_insertion_point(self, ctx_env, store_id, expected_parent):
        store = _make_root()
        result = resolve_layer_insertion_point(store, store_id)
        assert result == expected_parent


# ---------------------------------------------------------------------------
# TestPalStoreSiblingBug
# ---------------------------------------------------------------------------


class TestPalStoreSiblingBug:
    def test_l7_sibling_at_root(self, ctx_env):
        store = _make_root()
        update_index(store.directory, store.store_id)
        for i in range(1, 8):
            add_palnode(store, "mystore", f"L{i}", _make_node(entry_type="store", prompt=f"p{i}"))

        parent = resolve_layer_insertion_point(store, "mystore.L7")
        assert parent == "mystore"

        next_key = get_next_key(store, parent, "L")
        assert next_key == "L8"

    def test_fork_l3_sibling_within_fork(self, ctx_env):
        store = _make_root()
        update_index(store.directory, store.store_id)
        add_palnode(store, "mystore", "F0", _make_node(entry_type="fork"))
        for i in range(1, 4):
            add_palnode(store, "mystore.F0", f"L{i}", _make_node(entry_type="store", prompt=f"fp{i}"))

        parent = resolve_layer_insertion_point(store, "mystore.F0.L3")
        assert parent == "mystore.F0"

        next_key = get_next_key(store, parent, "L")
        assert next_key == "L4"


# ---------------------------------------------------------------------------
# TestDetachNode
# ---------------------------------------------------------------------------


class TestDetachNode:
    def test_detach_leaf(self, ctx_env):
        store = _make_root()
        update_index(store.directory, store.store_id)
        add_palnode(store, "mystore", "L1", _make_node(entry_type="store", prompt="hello"))

        node = detach_palnode(store, "mystore.L1")

        assert "L1" not in store.children
        assert node.prompt == "hello"

    def test_detach_with_subtree(self, ctx_env):
        store = _make_root()
        update_index(store.directory, store.store_id)
        add_palnode(store, "mystore", "L1", _make_node(entry_type="store", prompt="parent"))
        add_palnode(store, "mystore.L1", "Q0", _make_node(entry_type="query", prompt="child"))

        node = detach_palnode(store, "mystore.L1")

        assert "L1" not in store.children
        assert "Q0" in node.children
        assert node.children["Q0"].prompt == "child"

    def test_detach_nonexistent_raises(self, ctx_env):
        store = _make_root()
        update_index(store.directory, store.store_id)

        with pytest.raises(KeyError):
            detach_palnode(store, "mystore.L99")

    def test_detach_root_raises(self, ctx_env):
        store = _make_root()
        update_index(store.directory, store.store_id)

        with pytest.raises(ValueError):
            detach_palnode(store, "mystore")

    def test_siblings_not_renumbered(self, ctx_env):
        store = _make_root()
        update_index(store.directory, store.store_id)
        add_palnode(store, "mystore", "L1", _make_node(entry_type="store", prompt="one"))
        add_palnode(store, "mystore", "L2", _make_node(entry_type="store", prompt="two"))
        add_palnode(store, "mystore", "L3", _make_node(entry_type="store", prompt="three"))

        detach_palnode(store, "mystore.L2")

        assert "L1" in store.children
        assert "L2" not in store.children
        assert "L3" in store.children
        assert store.children["L1"].prompt == "one"
        assert store.children["L3"].prompt == "three"


# ---------------------------------------------------------------------------
# TestMoveNode
# ---------------------------------------------------------------------------


class TestMoveNode:
    def test_move_between_parents(self, ctx_env):
        store = _make_root()
        update_index(store.directory, store.store_id)
        add_palnode(store, "mystore", "F0", _make_node(entry_type="fork"))
        add_palnode(store, "mystore", "F1", _make_node(entry_type="fork"))
        add_palnode(store, "mystore.F0", "L1", _make_node(entry_type="store", prompt="moveme"))

        new_path = move_palnode(store, "mystore.F0.L1", "mystore.F1", "L1")

        assert new_path == "mystore.F1.L1"
        assert "L1" not in store.children["F0"].children
        assert "L1" in store.children["F1"].children
        assert store.children["F1"].children["L1"].prompt == "moveme"

    def test_move_preserves_subtree(self, ctx_env):
        store = _make_root()
        update_index(store.directory, store.store_id)
        add_palnode(store, "mystore", "F0", _make_node(entry_type="fork"))
        add_palnode(store, "mystore", "F1", _make_node(entry_type="fork"))
        add_palnode(store, "mystore.F0", "L1", _make_node(entry_type="store", prompt="parent"))
        add_palnode(store, "mystore.F0.L1", "Q0", _make_node(entry_type="query", prompt="child"))

        move_palnode(store, "mystore.F0.L1", "mystore.F1", "L1")

        moved = store.children["F1"].children["L1"]
        assert "Q0" in moved.children
        assert moved.children["Q0"].prompt == "child"

    def test_move_invalid_dest_rollback(self, ctx_env):
        store = _make_root()
        update_index(store.directory, store.store_id)
        add_palnode(store, "mystore", "L1", _make_node(entry_type="store", prompt="original"))

        with pytest.raises((ValueError, KeyError)):
            # Q0 cannot be a child of root — invalid dest
            move_palnode(store, "mystore.L1", "mystore", "Q0")

        assert "L1" in store.children
        assert store.children["L1"].prompt == "original"

    def test_move_returns_new_path(self, ctx_env):
        store = _make_root()
        update_index(store.directory, store.store_id)
        add_palnode(store, "mystore", "F0", _make_node(entry_type="fork"))
        add_palnode(store, "mystore.F0", "L1", _make_node(entry_type="store", prompt="x"))

        result = move_palnode(store, "mystore.F0.L1", "mystore", "L1")

        assert result == "mystore.L1"


# ---------------------------------------------------------------------------
# TestCopyNode
# ---------------------------------------------------------------------------


class TestCopyNode:
    def test_copy_creates_independent_clone(self, ctx_env):
        store = _make_root()
        update_index(store.directory, store.store_id)
        add_palnode(store, "mystore", "L1", _make_node(entry_type="store", prompt="orig"))
        add_palnode(store, "mystore.L1", "Q0", _make_node(entry_type="query", prompt="orig-child"))
        add_palnode(store, "mystore", "F0", _make_node(entry_type="fork"))

        copy_palnode(store, "mystore.L1", "mystore.F0", "L1")

        # Mutate copy; original must be unchanged
        store.children["F0"].children["L1"].prompt = "modified"
        assert store.children["L1"].prompt == "orig"

    def test_copy_preserves_content(self, ctx_env):
        store = _make_root()
        update_index(store.directory, store.store_id)
        add_palnode(
            store,
            "mystore",
            "L1",
            _make_node(entry_type="store", prompt="the prompt", response="the response", files=["a.py", "b.py"]),
        )
        add_palnode(store, "mystore", "F0", _make_node(entry_type="fork"))

        copy_palnode(store, "mystore.L1", "mystore.F0", "L1")
        copy_node_obj = store.children["F0"].children["L1"]

        assert copy_node_obj.prompt == "the prompt"
        assert copy_node_obj.response == "the response"
        assert copy_node_obj.files == ["a.py", "b.py"]

    def test_copy_to_invalid_dest_raises(self, ctx_env):
        store = _make_root()
        update_index(store.directory, store.store_id)
        add_palnode(store, "mystore", "L1", _make_node(entry_type="store", prompt="x"))

        with pytest.raises(ValueError):
            # Q0 is not valid at root
            copy_palnode(store, "mystore.L1", "mystore", "Q0")

        # Original must still be present
        assert "L1" in store.children


# ---------------------------------------------------------------------------
# TestFoldRange
# ---------------------------------------------------------------------------


class TestFoldRange:
    def test_fold_chain(self, ctx_env):
        from utils.palstore_builder import build_context_from_ancestry

        store = _make_root()
        update_index(store.directory, store.store_id)
        n1 = _make_node(entry_type="store", prompt="q1", response="r1")
        n2 = _make_node(entry_type="query", prompt="q2", response="r2")
        n3 = _make_node(entry_type="tool", prompt="q3", response="r3")
        store.children["L1"] = n1
        n1.children["Q0"] = n2
        n2.children["1"] = n3

        result = fold_palnode_range(store, "mystore.L1", "mystore.L1.Q0.1")

        expected_content = build_context_from_ancestry([n1, n2, n3])
        assert result.content == expected_content

    def test_fold_skips_forks(self, ctx_env):
        store = _make_root()
        update_index(store.directory, store.store_id)
        n1 = _make_node(entry_type="store", prompt="q1", response="r1", files=["f1.py"])
        fork = _make_node(entry_type="fork")
        n3 = _make_node(entry_type="store", prompt="q3", response="r3", files=["f3.py"])
        store.children["L1"] = n1
        n1.children["F0"] = fork
        fork.children["L1"] = n3

        result = fold_palnode_range(store, "mystore.L1", "mystore.L1.F0.L1")

        # Fork's files (empty) contribute nothing; fork-type skipped
        assert "f1.py" in result.files
        assert "f3.py" in result.files

    def test_fold_unions_files(self, ctx_env):
        store = _make_root()
        update_index(store.directory, store.store_id)
        n1 = _make_node(entry_type="store", prompt="p1", response="r1", files=["a.py", "b.py"])
        n2 = _make_node(entry_type="query", prompt="p2", response="r2", files=["b.py", "c.py"])
        store.children["L1"] = n1
        n1.children["Q0"] = n2

        result = fold_palnode_range(store, "mystore.L1", "mystore.L1.Q0")

        assert result.files == ["a.py", "b.py", "c.py"]

    def test_fold_returns_store_node(self, ctx_env):
        store = _make_root()
        update_index(store.directory, store.store_id)
        n1 = _make_node(entry_type="store", prompt="p1", response="r1")
        store.children["L1"] = n1

        result = fold_palnode_range(store, "mystore.L1", "mystore.L1")

        assert isinstance(result, PalNode)
        assert result.entry_type == "store"
        assert "L1" in store.children  # original unchanged


# ---------------------------------------------------------------------------
# TestFindAncestor
# ---------------------------------------------------------------------------


class TestFindAncestor:
    def test_find_nearest_l_ancestor(self, ctx_env):
        store = _make_root()
        update_index(store.directory, store.store_id)
        add_palnode(store, "mystore", "L1", _make_node(entry_type="store"))
        add_palnode(store, "mystore.L1", "Q0", _make_node(entry_type="query"))
        add_palnode(store, "mystore.L1.Q0", "F0", _make_node(entry_type="fork"))
        add_palnode(store, "mystore.L1.Q0.F0", "thinkdeep", _make_node(entry_type="tool"))

        result = find_palnode_ancestor(store, "mystore.L1.Q0.F0.thinkdeep", is_l_ancestor)

        assert result is not None
        path, node = result
        assert path == "mystore.L1"
        assert node.entry_type == "store"

    def test_find_nearest_fork(self, ctx_env):
        store = _make_root()
        update_index(store.directory, store.store_id)
        add_palnode(store, "mystore", "F0", _make_node(entry_type="fork"))
        add_palnode(store, "mystore.F0", "L1", _make_node(entry_type="store"))
        add_palnode(store, "mystore.F0.L1", "F0", _make_node(entry_type="fork"))
        add_palnode(store, "mystore.F0.L1.F0", "analyze", _make_node(entry_type="tool"))

        result = find_palnode_ancestor(store, "mystore.F0.L1.F0.analyze", is_fork_ancestor)

        assert result is not None
        path, node = result
        # Deepest fork ancestor is mystore.F0.L1.F0
        assert path == "mystore.F0.L1.F0"
        assert node.entry_type == "fork"

    def test_no_match_returns_none(self, ctx_env):
        store = _make_root()
        update_index(store.directory, store.store_id)
        add_palnode(store, "mystore", "L1", _make_node(entry_type="store"))
        add_palnode(store, "mystore.L1", "Q0", _make_node(entry_type="query"))

        result = find_palnode_ancestor(store, "mystore.L1.Q0", is_fork_ancestor)

        assert result is None

    def test_returns_deepest_match(self, ctx_env):
        store = _make_root()
        update_index(store.directory, store.store_id)
        add_palnode(store, "mystore", "L1", _make_node(entry_type="store"))
        add_palnode(store, "mystore.L1", "F0", _make_node(entry_type="fork"))
        add_palnode(store, "mystore.L1.F0", "L1", _make_node(entry_type="store"))
        add_palnode(store, "mystore.L1.F0.L1", "Q0", _make_node(entry_type="query"))

        result = find_palnode_ancestor(store, "mystore.L1.F0.L1.Q0", is_l_ancestor)

        assert result is not None
        path, _ = result
        # Both L1 ancestors exist; deepest is mystore.L1.F0.L1
        assert path == "mystore.L1.F0.L1"


# ---------------------------------------------------------------------------
# TestCollectSubtreeFiles
# ---------------------------------------------------------------------------


class TestCollectSubtreeFiles:
    def test_collects_from_node_and_children(self):
        node = _make_node(entry_type="store", files=["a.py"])
        child = _make_node(entry_type="query", files=["b.py"])
        node.children["Q0"] = child

        result = collect_palnode_files(node)

        assert result == ["a.py", "b.py"]

    def test_deduplicates(self):
        node = _make_node(entry_type="store", files=["a.py", "b.py"])
        child = _make_node(entry_type="query", files=["b.py", "c.py"])
        node.children["Q0"] = child

        result = collect_palnode_files(node)

        assert result == ["a.py", "b.py", "c.py"]
        assert len(result) == 3

    def test_empty_subtree(self):
        node = _make_node(entry_type="store", files=[])

        result = collect_palnode_files(node)

        assert result == []

    def test_deep_nesting(self):
        root = _make_node(entry_type="store", files=["root.py"])
        level1 = _make_node(entry_type="query", files=["l1.py"])
        level2 = _make_node(entry_type="query", files=["l2.py"])
        level3 = _make_node(entry_type="tool", files=["l3.py"])

        root.children["Q0"] = level1
        level1.children["1"] = level2
        level2.children["2"] = level3

        result = collect_palnode_files(root)

        assert result == ["root.py", "l1.py", "l2.py", "l3.py"]
