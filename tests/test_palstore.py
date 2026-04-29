"""
Unit tests for utils/palstore.py and utils/palstore_builder.py.
"""

import os

import pytest

from utils.palstore import (
    PalNode,
    PalRoot,
    TraversalLog,
    add_palnode,
    arm_tree,
    collect_palnode_files,
    collect_traversal,
    copy_palnode,
    detach_palnode,
    disarm_tree,
    encode_directory,
    find_palnode_ancestor,
    fold_palnode_range,
    get_armed_tree,
    get_next_key,
    iter_ancestry,
    iter_dfs,
    iter_range,
    list_armed_trees,
    list_trees,
    load_tree,
    move_palnode,
    parse_tree_path,
    rebuild_index,
    resolve_node,
    resolve_tree_location,
    save_tree,
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
    monkeypatch.setattr(palstore, "_INDEX_PATH", os.path.join(ctx_dir, "tree-index.json"))
    monkeypatch.setattr(palstore, "_ARMED_PATH", os.path.join(ctx_dir, "armed.json"))
    return ctx_dir


def _make_node(
    content="",
    *,
    files=None,
    label=None,
    timestamp="",
    model=None,
    tool_name=None,
    metadata=None,
    input=None,
    output=None,
) -> PalNode:
    return PalNode(
        label=label,
        timestamp=timestamp or "2026-01-01T00:00:00Z",
        model=model,
        tool_name=tool_name,
        files=files or [],
        input=input if input is not None else content,
        output=output if output is not None else content,
        metadata=metadata or {},
    )


# Default directory/name used throughout tests
_DEFAULT_DIR = "/home/user/project"
_DEFAULT_NAME = "mystore"
_DEFAULT_CANONICAL = f"{_DEFAULT_DIR}:{_DEFAULT_NAME}"


def _make_root(tree_path=_DEFAULT_NAME, directory=_DEFAULT_DIR) -> PalRoot:
    canonical = f"{directory}:{tree_path}"
    return PalRoot(
        tree_path=canonical,
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
        save_tree(store)
        loaded = load_tree(store.tree_path)
        assert loaded is not None
        assert loaded.tree_path == store.tree_path
        assert loaded.directory == store.directory
        assert loaded.created_at == store.created_at

    def test_load_nonexistent_returns_none(self, ctx_env):
        result = load_tree("/home/nobody/project:ghost-store")
        assert result is None

    def test_atomic_write_does_not_corrupt_on_overwrite(self, ctx_env):
        store = _make_root()
        save_tree(store)

        # Add a child and overwrite
        child = _make_node(input="hello", output="world")
        store.children["L1"] = child
        save_tree(store)

        loaded = load_tree(store.tree_path)
        assert loaded is not None
        assert "L1" in loaded.children
        assert loaded.children["L1"].input == "hello"

    def test_load_corrupt_json_returns_none(self, ctx_env):
        from utils import palstore

        store = _make_root()
        path = palstore.get_tree_file_path(store.tree_path)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write("{not valid json")

        result = load_tree(store.tree_path)
        assert result is None


# ---------------------------------------------------------------------------
# TestResolveNode
# ---------------------------------------------------------------------------


class TestResolveNode:
    def test_resolve_direct_child(self, ctx_env):
        store = _make_root()
        child = _make_node(input="q", output="r")
        store.children["L1"] = child

        result = resolve_node(store, "L1")
        assert result is not None
        assert result.input == "q"

    def test_resolve_deeper_path(self, ctx_env):
        store = _make_root()
        l1 = _make_node(input="l1p", output="l1r")
        q0 = _make_node(input="q0p", output="q0r")
        f0 = _make_node(input="", output="")
        td = _make_node(input="tdp", output="tdr")

        f0.children["thinkdeep"] = td
        q0.children["F0"] = f0
        l1.children["Q0"] = q0
        store.children["L1"] = l1

        result = resolve_node(store, "L1.Q0.F0.thinkdeep")
        assert result is not None
        assert result.input == "tdp"

    def test_resolve_root_only_returns_none(self, ctx_env):
        store = _make_root()
        result = resolve_node(store, "")
        assert result is None

    def test_resolve_nonexistent_path_returns_none(self, ctx_env):
        store = _make_root()
        result = resolve_node(store, "Z99")
        assert result is None


# ---------------------------------------------------------------------------
# TestAddChild
# ---------------------------------------------------------------------------


class TestAddChild:
    def test_add_palnode_to_root(self, ctx_env):
        store = _make_root()
        child = _make_node(input="q", output="r")

        full_path = add_palnode(store, "", "L1", child)
        assert full_path == f"{_DEFAULT_CANONICAL}.L1"
        assert "L1" in store.children

    def test_add_palnode_to_nested_node(self, ctx_env):
        store = _make_root()
        l1 = _make_node(input="p", output="r")
        store.children["L1"] = l1

        grandchild = _make_node(input="gp", output="gr")
        full_path = add_palnode(store, "L1", "Q0", grandchild)
        assert full_path == f"{_DEFAULT_CANONICAL}.L1.Q0"
        assert "Q0" in store.children["L1"].children

    def test_add_palnode_to_nonexistent_parent_raises(self, ctx_env):
        store = _make_root()
        child = _make_node()

        with pytest.raises(KeyError, match="parent path not found"):
            add_palnode(store, "Z99", "L1", child)


# ---------------------------------------------------------------------------
# TestWalkAncestry
# ---------------------------------------------------------------------------


class TestWalkAncestry:
    def test_walk_to_direct_child(self, ctx_env):
        store = _make_root()
        child = _make_node(input="p", output="r")
        store.children["L1"] = child

        result = walk_palnode_ancestry(store, "L1")
        assert len(result) == 1
        assert result[0].input == "p"

    def test_walk_to_deeper_path(self, ctx_env):
        store = _make_root()
        l1 = _make_node(input="l1p", output="l1r")
        q0 = _make_node(input="q0p", output="q0r")
        f0 = _make_node(input="", output="")
        td = _make_node(input="tdp", output="tdr")

        f0.children["thinkdeep"] = td
        q0.children["F0"] = f0
        l1.children["Q0"] = q0
        store.children["L1"] = l1

        result = walk_palnode_ancestry(store, "L1.Q0.F0.thinkdeep")
        assert len(result) == 4
        assert result[0].input == "l1p"
        assert result[1].input == "q0p"
        assert result[2].input == ""
        assert result[3].input == "tdp"

    def test_walk_to_root_returns_empty(self, ctx_env):
        store = _make_root()
        result = walk_palnode_ancestry(store, "")
        assert result == []

    def test_fork_nodes_included_in_ancestry(self, ctx_env):
        store = _make_root()
        fork = _make_node(input="", output="")
        child = _make_node(input="cp", output="cr")

        fork.children["L1"] = child
        store.children["F0"] = fork

        result = walk_palnode_ancestry(store, "F0.L1")
        assert len(result) == 2
        assert result[0].input == ""
        assert result[1].input == "cp"

    def test_cumulative_layers_at_root(self, ctx_env):
        """Pure parent-chain: siblings are NOT accumulated. L2 is a direct child
        of root, so walk returns only [L2]."""
        store = _make_root()
        store.children["L0"] = _make_node(input="p0", output="r0")
        store.children["L1"] = _make_node(input="p1", output="r1")
        store.children["L2"] = _make_node(input="p2", output="r2")

        result = walk_palnode_ancestry(store, "L2")
        assert len(result) == 1
        assert result[0].input == "p2"

    def test_cumulative_layers_with_fork_descendant(self, ctx_env):
        """Pure parent-chain: walk of L2.F0 returns [L2, F0] only."""
        store = _make_root()
        store.children["L0"] = _make_node(input="p0", output="r0")
        store.children["L1"] = _make_node(input="p1", output="r1")
        l2 = _make_node(input="p2", output="r2")
        fork = _make_node(input="", output="")
        l2.children["F0"] = fork
        store.children["L2"] = l2

        result = walk_palnode_ancestry(store, "L2.F0")
        assert len(result) == 2
        assert result[0].input == "p2"
        assert result[1].input == ""

    def test_cumulative_layers_nested_under_fork(self, ctx_env):
        """Pure parent-chain: walk of F0.L1 returns [F0, L1] only."""
        store = _make_root()
        fork = _make_node(input="", output="")
        fork.children["L0"] = _make_node(input="fp0", output="fr0")
        fork.children["L1"] = _make_node(input="fp1", output="fr1")
        store.children["F0"] = fork

        result = walk_palnode_ancestry(store, "F0.L1")
        assert len(result) == 2
        assert result[0].input == ""
        assert result[1].input == "fp1"


# ---------------------------------------------------------------------------
# TestGetNextKey
# ---------------------------------------------------------------------------


class TestGetNextKey:
    def test_l_prefix_first_child(self, ctx_env):
        store = _make_root()
        assert get_next_key(store, "", "L") == "L0"

    def test_l_prefix_existing_children(self, ctx_env):
        store = _make_root()
        store.children["L0"] = _make_node()
        store.children["L1"] = _make_node()
        assert get_next_key(store, "", "L") == "L2"

    def test_q_prefix_empty_store(self, ctx_env):
        store = _make_root()
        assert get_next_key(store, "", "Q") == "Q0"

    def test_q_prefix_existing_children(self, ctx_env):
        store = _make_root()
        store.children["Q0"] = _make_node()
        assert get_next_key(store, "", "Q") == "Q1"

    def test_f_prefix_empty_store(self, ctx_env):
        store = _make_root()
        assert get_next_key(store, "", "F") == "F0"

    def test_f_prefix_existing_children(self, ctx_env):
        store = _make_root()
        store.children["F0"] = _make_node()
        assert get_next_key(store, "", "F") == "F1"

    def test_numeric_prefix_first_child(self, ctx_env):
        store = _make_root()
        assert get_next_key(store, "", "") == "0"

    def test_empty_prefix_followup_existing(self, ctx_env):
        store = _make_root()
        store.children["0"] = _make_node()
        store.children["1"] = _make_node()
        assert get_next_key(store, "", "") == "2"

    def test_arbitrary_prefix(self, ctx_env):
        store = _make_root()
        assert get_next_key(store, "", "custom") == "custom0"

    def test_arbitrary_prefix_existing(self, ctx_env):
        store = _make_root()
        store.children["thinkdeep0"] = _make_node()
        store.children["thinkdeep1"] = _make_node()
        assert get_next_key(store, "", "thinkdeep") == "thinkdeep2"


# ---------------------------------------------------------------------------
# TestParseStorePath
# ---------------------------------------------------------------------------


class TestParseStorePath:
    def test_canonical_no_segments(self, ctx_env):
        root, segments = parse_tree_path("/home/user/project:mystore")
        assert root == "/home/user/project:mystore"
        assert segments == []

    def test_canonical_with_segments(self, ctx_env):
        root, segments = parse_tree_path("/home/user/project:mystore.L1.Q0")
        assert root == "/home/user/project:mystore"
        assert segments == ["L1", "Q0"]

    def test_canonical_with_tool_child_under_fork(self, ctx_env):
        root, segments = parse_tree_path("/home/user/project:mystore.Q0.F0.thinkdeep")
        assert root == "/home/user/project:mystore"
        assert segments == ["Q0", "F0", "thinkdeep"]

    def test_shorthand_with_segments(self, ctx_env):
        update_index(_DEFAULT_CANONICAL)
        root, segments = parse_tree_path("mystore.L1.Q0")
        assert root == _DEFAULT_CANONICAL
        assert segments == ["L1", "Q0"]

    def test_shorthand_no_segments(self, ctx_env):
        update_index(_DEFAULT_CANONICAL)
        root, segments = parse_tree_path("mystore")
        assert root == _DEFAULT_CANONICAL
        assert segments == []

    def test_empty_string(self, ctx_env):
        root, segments = parse_tree_path("")
        assert root == ""
        assert segments == []


# ---------------------------------------------------------------------------
# TestIndexOperations
# ---------------------------------------------------------------------------


class TestIndexOperations:
    def test_update_and_load_index_roundtrip(self, ctx_env):
        update_index(_DEFAULT_CANONICAL)
        from utils.palstore import load_index

        index = load_index()
        assert _DEFAULT_CANONICAL in index["trees"]
        assert _DEFAULT_DIR in index["directories"]

    def test_resolve_store_location_finds_indexed_store(self, ctx_env):
        store = _make_root()
        save_tree(store)
        update_index(store.tree_path)

        result = resolve_tree_location("mystore")
        assert result is not None
        directory, root_id = result
        assert root_id == "mystore"
        assert directory == store.directory

    def test_resolve_store_location_returns_none_for_unknown(self, ctx_env):
        result = resolve_tree_location("totally-unknown-store")
        assert result is None

    def test_rebuild_index_scans_directories(self, ctx_env):
        store_a = _make_root(tree_path="store-a", directory="/home/user/proj-a")
        store_b = _make_root(tree_path="store-b", directory="/home/user/proj-b")
        save_tree(store_a)
        save_tree(store_b)

        index = rebuild_index()

        assert "/home/user/proj-a:store-a" in index["trees"]
        assert "/home/user/proj-b:store-b" in index["trees"]
        assert "/home/user/proj-a" in index["directories"]
        assert "/home/user/proj-b" in index["directories"]

    def test_resolve_store_location_falls_back_to_scan(self, ctx_env):
        # Save store but do NOT update index — force a scan fallback
        store = _make_root()
        save_tree(store)

        result = resolve_tree_location("mystore")
        assert result is not None
        _, root_id = result
        assert root_id == "mystore"


# ---------------------------------------------------------------------------
# TestArmedOperations
# ---------------------------------------------------------------------------


class TestArmedOperations:
    def test_arm_and_get_roundtrip(self, ctx_env):
        arm_tree("/home/user/project", "mystore")
        result = get_armed_tree("/home/user/project")
        assert result == "mystore"

    def test_disarm_removes_armed_state(self, ctx_env):
        arm_tree("/home/user/project", "mystore")
        disarm_tree("/home/user/project")
        result = get_armed_tree("/home/user/project")
        assert result is None

    def test_list_armed_stores_returns_all(self, ctx_env):
        arm_tree("/home/user/proj-a", "store-a")
        arm_tree("/home/user/proj-b", "store-b")

        all_armed = list_armed_trees()
        assert all_armed["/home/user/proj-a"] == "store-a"
        assert all_armed["/home/user/proj-b"] == "store-b"

    def test_disarm_nonexistent_is_noop(self, ctx_env):
        # Should not raise
        disarm_tree("/home/user/nonexistent")
        assert get_armed_tree("/home/user/nonexistent") is None

    def test_get_armed_returns_none_when_empty(self, ctx_env):
        result = get_armed_tree("/home/user/project")
        assert result is None


# ---------------------------------------------------------------------------
# TestListStores
# ---------------------------------------------------------------------------


class TestListStores:
    def test_list_stores_with_directory_filter(self, ctx_env):
        store_a = _make_root(tree_path="store-a", directory="/home/user/proj-a")
        store_b = _make_root(tree_path="store-b", directory="/home/user/proj-b")
        save_tree(store_a)
        save_tree(store_b)

        results = list_trees(directory="/home/user/proj-a")
        assert len(results) == 1
        assert results[0].tree_name == "store-a"

    def test_list_stores_without_filter_returns_all(self, ctx_env):
        store_a = _make_root(tree_path="store-a", directory="/home/user/proj-a")
        store_b = _make_root(tree_path="store-b", directory="/home/user/proj-b")
        save_tree(store_a)
        save_tree(store_b)

        results = list_trees()
        names = {s.tree_name for s in results}
        assert "store-a" in names
        assert "store-b" in names

    def test_list_stores_empty_for_nonexistent_directory(self, ctx_env):
        results = list_trees(directory="/home/nobody/nowhere")
        assert results == []

    def test_list_stores_filters_corrupt_files(self, ctx_env):
        from utils import palstore

        store = _make_root()
        save_tree(store)

        # Drop a corrupt JSON file in the same directory
        folder = palstore.get_tree_dir(store.directory)
        corrupt_path = os.path.join(folder, "corrupt.json")
        with open(corrupt_path, "w") as f:
            f.write("{bad json")

        results = list_trees(directory=store.directory)
        assert len(results) == 1
        assert results[0].tree_name == "mystore"


# ---------------------------------------------------------------------------
# TestContextBuilder
# ---------------------------------------------------------------------------


class TestContextBuilder:
    def test_build_context_from_ancestry_with_content_nodes(self):
        from utils.palstore_builder import build_context_from_ancestry

        nodes = [
            _make_node(input="first question", output="first answer"),
            _make_node(input="second question", output="second answer"),
        ]

        result = build_context_from_ancestry(nodes)
        assert "=== CONVERSATION HISTORY ===" in result
        assert "first question" in result
        assert "first answer" in result
        assert "second question" in result
        assert "second answer" in result
        assert "Turn 1" in result
        assert "Turn 2" in result

    def test_build_context_skips_empty_nodes(self):
        from utils.palstore_builder import build_context_from_ancestry

        empty = _make_node(input="", output="")
        content = _make_node(input="real question", output="real answer")
        nodes = [empty, content]

        result = build_context_from_ancestry(nodes)
        assert "real question" in result
        assert "Turn 1" in result
        # Only one turn since empty node is skipped
        assert "Turn 2" not in result

    def test_build_context_returns_empty_for_empty_list(self):
        from utils.palstore_builder import build_context_from_ancestry

        result = build_context_from_ancestry([])
        assert result == ""

    def test_build_context_returns_empty_when_all_empty(self):
        from utils.palstore_builder import build_context_from_ancestry

        nodes = [
            _make_node(input="", output=""),
            _make_node(input="", output=""),
        ]

        result = build_context_from_ancestry(nodes)
        assert result == ""

    def test_build_context_includes_file_listing(self):
        from utils.palstore_builder import build_context_from_ancestry

        node = _make_node(
            input="with files",
            output="got it",
            files=["/home/user/foo.py", "/home/user/bar.py"],
        )

        result = build_context_from_ancestry([node], include_files=True)
        assert "=== FILES REFERENCED IN THIS CONVERSATION ===" in result
        assert "/home/user/foo.py" in result
        assert "/home/user/bar.py" in result

    def test_build_context_omits_file_listing_when_disabled(self):
        from utils.palstore_builder import build_context_from_ancestry

        node = _make_node(
            input="with files",
            output="got it",
            files=["/home/user/foo.py"],
        )

        result = build_context_from_ancestry([node], include_files=False)
        assert "FILES REFERENCED" not in result

    def test_build_context_deduplicates_files_across_nodes(self):
        from utils.palstore_builder import build_context_from_ancestry

        node_a = _make_node(input="q1", output="r1", files=["/shared.py", "/a.py"])
        node_b = _make_node(input="q2", output="r2", files=["/shared.py", "/b.py"])

        result = build_context_from_ancestry([node_a, node_b], include_files=True)
        # /shared.py must appear exactly once
        assert result.count("/shared.py") == 1

    def test_file_blob_stripped_from_cumulative_ancestry(self):
        """File content embedded in CONTEXT FILES blocks must be stripped from
        reconstructed conversation history — file paths are listed separately."""
        from utils.file_diff import decide_file_representation
        from utils.palstore_builder import build_context_from_ancestry

        file_body = "class Foo:\n    pass\n"
        mtime = "2026-01-01 00:00:00 UTC"

        # L1: first occurrence — full file embed
        l1_file_block = decide_file_representation("/app.py", file_body, None, None, mtime)
        l1_prompt = f"=== CONTEXT LAYER SUBMISSION ===\n\nsetup\n\n=== CONTEXT FILES ===\n{l1_file_block}\n=== END CONTEXT FILES ==="
        l1 = PalNode(
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
            timestamp="2026-01-02T00:00:00Z",
            files=["/app.py"],
            prompt="update",
            response="ok",
            content=f"{l2_prompt}\n\n---\n\nok",
        )

        history = build_context_from_ancestry([l1, l2])
        assert "class Foo:" not in history, "file content should be stripped from conversation history"
        assert "setup" in history
        assert "update" in history

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
            timestamp="2026-01-01T00:00:00Z",
            files=["/a.py"],
            prompt="p",
            response="r",
            content=_blob("/a.py", "v1"),
        )
        n2 = PalNode(
            timestamp="2026-01-02T00:00:00Z",
            files=["/a.py", "/b.py"],
            prompt="p",
            response="r",
            content=_blob("/a.py", "v2") + _blob("/b.py", "b_content"),
        )
        n3 = PalNode(
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
            tree_path="/tmp/test:regression",
            created_at="2026-01-01T00:00:00Z",
            children={
                "L1": PalNode(
                    timestamp="2026-01-01T00:00:00Z",
                    files=["/app.py"],
                    prompt="initial",
                    response="stored L1",
                    content=_make_layer("initial", {"/app.py": file_body}, "stored L1"),
                ),
                "L2": PalNode(
                    timestamp="2026-01-02T00:00:00Z",
                    files=["/app.py"],
                    prompt="update",
                    response="stored L2",
                    content=_make_layer("update", {"/app.py": file_body}, "stored L2"),
                ),
                "L3": PalNode(
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

        # File content is stripped from conversation history
        assert "def hello():" not in history, "file content should be stripped from conversation history"
        # All responses should still be present
        assert "stored L1" in history
        assert "stored L2" in history
        assert "stored L3" in history

    def test_build_context_ends_with_end_marker(self):
        from utils.palstore_builder import build_context_from_ancestry

        node = _make_node(input="q", output="r")
        result = build_context_from_ancestry([node])
        assert result.strip().endswith("=== END CONVERSATION HISTORY ===")

    def test_build_context_strips_context_files_from_user_turn(self):
        from utils.palstore_builder import build_context_from_ancestry

        full_prompt = "user text\n\n=== CONTEXT FILES ===\nFILE BLOB CONTENT HERE\n=== END CONTEXT FILES ==="
        raw_response = "assistant replied"
        node = PalNode(
            timestamp="2026-01-01T00:00:00Z",
            input=f"{full_prompt}\n\n---\n\n{raw_response}",
            output=raw_response,
        )
        result = build_context_from_ancestry([node])
        assert "FILE BLOB CONTENT HERE" not in result
        assert "=== CONTEXT FILES ===" not in result
        assert "user text" in result
        assert raw_response in result

    def test_build_context_falls_back_to_prompt_when_no_content(self):
        from utils.palstore_builder import build_context_from_ancestry

        node = _make_node(input="bare prompt", output="response")
        result = build_context_from_ancestry([node])
        assert "bare prompt" in result

    def test_build_context_input_used_verbatim_as_user_turn(self):
        from utils.palstore_builder import build_context_from_ancestry

        user_turn = "before\n\nstill part of prompt"
        response = "answer"
        node = PalNode(
            timestamp="2026-01-01T00:00:00Z",
            input=user_turn,
            output=response,
        )
        result = build_context_from_ancestry([node])
        assert "before" in result
        assert "still part of prompt" in result
        assert "answer" in result


# ---------------------------------------------------------------------------
# TestMaterializeStagingDir
# ---------------------------------------------------------------------------


class TestMaterializeStagingDir:
    def test_extracts_file_from_blob_to_staging_dir(self, tmp_path):
        from utils.palstore_builder import _materialize_staging_dir

        file_content = "class Widget:\n    pass"
        blob = (
            "=== CONTEXT LAYER SUBMISSION ===\n\nsetup"
            "\n\n=== CONTEXT FILES ===\n"
            f"--- BEGIN FILE: /src/widget.py (Last modified: 2026-01-01 00:00:00 UTC) ---\n"
            f"{file_content}\n"
            f"--- END FILE: /src/widget.py ---\n"
            "\n=== END CONTEXT FILES ==="
        )
        node = PalNode(
            timestamp="2026-01-01T00:00:00Z",
            files=["/src/widget.py"],
            input=blob,
            output="ok",
        )

        staging_dir = _materialize_staging_dir([node])
        assert staging_dir is not None

        import os
        import shutil

        try:
            staged_file = os.path.join(staging_dir, "T0", "widget.py")
            assert os.path.isfile(staged_file)
            with open(staged_file) as f:
                assert f.read() == file_content
        finally:
            shutil.rmtree(staging_dir, ignore_errors=True)

    def test_returns_none_when_no_files(self):
        from utils.palstore_builder import _materialize_staging_dir

        node = PalNode(
            timestamp="2026-01-01T00:00:00Z",
            input="just text",
            output="reply",
        )
        assert _materialize_staging_dir([node]) is None

    def test_disk_fallback_when_no_blob_match(self, tmp_path):
        from utils.palstore_builder import _materialize_staging_dir

        disk_file = tmp_path / "real.py"
        disk_file.write_text("on disk content")

        node = PalNode(
            timestamp="2026-01-01T00:00:00Z",
            files=[str(disk_file)],
            input="no context files section here",
            output="reply",
        )

        staging_dir = _materialize_staging_dir([node])
        assert staging_dir is not None

        import os
        import shutil

        try:
            staged = os.path.join(staging_dir, "T0", "real.py")
            assert os.path.isfile(staged)
            with open(staged) as f:
                assert f.read() == "on disk content"
        finally:
            shutil.rmtree(staging_dir, ignore_errors=True)


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
        from utils.palstore import get_tree_dir

        result = get_tree_dir("/home/user/project")
        assert result == os.path.join(ctx_env, "-home-user-project")

    def test_get_store_path_ends_with_json(self, ctx_env):
        from utils.palstore import get_tree_file_path

        result = get_tree_file_path("/home/user/project:mystore")
        assert result.endswith("mystore.json")


# ---------------------------------------------------------------------------
# TestStoreLifecycleEdgeCases
# ---------------------------------------------------------------------------


class TestStoreLifecycleEdgeCases:
    def test_load_store_with_list_json_returns_none(self, ctx_env):
        from utils import palstore

        store = _make_root()
        path = palstore.get_tree_file_path(store.tree_path)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            import json

            json.dump([1, 2, 3], f)

        result = load_tree(store.tree_path)
        assert result is None

    def test_load_store_with_missing_required_fields_returns_none(self, ctx_env):
        from utils import palstore

        store = _make_root()
        path = palstore.get_tree_file_path(store.tree_path)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            import json

            json.dump({"unrelated_field": "value"}, f)

        result = load_tree(store.tree_path)
        assert result is None

    def test_save_store_creates_nested_directories(self, ctx_env):
        from utils import palstore

        store = _make_root(directory="/some/deeply/nested/path")
        save_tree(store)

        path = palstore.get_tree_file_path(store.tree_path)
        assert os.path.exists(path)


# ---------------------------------------------------------------------------
# TestRenameStore
# ---------------------------------------------------------------------------


class TestRenameStore:
    def test_rename_happy_path(self, ctx_env):
        from utils.palstore import get_tree_file_path, load_index, rename_tree

        store = _make_root()
        save_tree(store)
        update_index(store.tree_path)

        new_canonical = f"{store.directory}:renamed-store"
        rename_tree(store.tree_path, "renamed-store")

        assert os.path.exists(get_tree_file_path(new_canonical))
        assert not os.path.exists(get_tree_file_path(store.tree_path))

        loaded = load_tree(new_canonical)
        assert loaded is not None
        assert loaded.tree_name == "renamed-store"

        index = load_index()
        assert new_canonical in index["trees"]
        assert store.tree_path not in index["trees"]

    def test_rename_nonexistent_raises_key_error(self, ctx_env):
        from utils.palstore import rename_tree

        with pytest.raises(KeyError, match="PALTree not found"):
            rename_tree("/home/user/project:ghost-store", "newname")

    def test_rename_new_id_with_dots_raises_value_error(self, ctx_env):
        from utils.palstore import rename_tree

        store = _make_root()
        save_tree(store)

        with pytest.raises(ValueError, match="dots"):
            rename_tree(store.tree_path, "new.name")

    def test_rename_updates_armed_state(self, ctx_env):
        from utils.palstore import rename_tree

        store = _make_root()
        save_tree(store)
        update_index(store.tree_path)
        arm_tree(store.directory, store.tree_path)

        new_canonical = f"{store.directory}:renamed-store"
        rename_tree(store.tree_path, "renamed-store")

        assert get_armed_tree(store.directory) == new_canonical

    def test_rename_when_store_not_armed_is_noop_for_armed_state(self, ctx_env):
        from utils.palstore import rename_tree

        store = _make_root()
        save_tree(store)
        update_index(store.tree_path)
        arm_tree("/home/OTHER/project", "other-store")

        rename_tree(store.tree_path, "renamed-store")

        assert get_armed_tree("/home/OTHER/project") == "other-store"
        assert get_armed_tree(store.directory) is None

    def test_rename_preserves_children(self, ctx_env):
        from utils.palstore import rename_tree

        store = _make_root()
        child = _make_node(input="child-q", output="child-r")
        store.children["L1"] = child
        save_tree(store)
        update_index(store.tree_path)

        new_canonical = f"{store.directory}:renamed-store"
        rename_tree(store.tree_path, "renamed-store")

        loaded = load_tree(new_canonical)
        assert loaded is not None
        assert "L1" in loaded.children
        assert loaded.children["L1"].input == "child-q"


# ---------------------------------------------------------------------------
# TestResolveNodeEdgeCases
# ---------------------------------------------------------------------------


class TestResolveNodeEdgeCases:
    def test_partial_valid_path_then_invalid_segment_returns_none(self, ctx_env):
        store = _make_root()
        child = _make_node(input="p", output="r")
        store.children["L1"] = child

        result = resolve_node(store, "L1.Q99")
        assert result is None

    def test_empty_children_at_intermediate_node_returns_none(self, ctx_env):
        store = _make_root()
        child = _make_node(input="p", output="r")
        store.children["L1"] = child

        result = resolve_node(store, "L1.Q0")
        assert result is None


# ---------------------------------------------------------------------------
# TestAddChildEdgeCases
# ---------------------------------------------------------------------------


class TestAddChildEdgeCases:
    def test_overwrite_existing_child_key_silently(self, ctx_env):
        store = _make_root()
        original = _make_node(input="original", output="old")
        replacement = _make_node(input="new prompt", output="new response")

        add_palnode(store, "", "L1", original)
        add_palnode(store, "", "L1", replacement)

        assert store.children["L1"].input == "new prompt"

    def test_deeply_nested_add_three_levels(self, ctx_env):
        store = _make_root()

        n1 = _make_node(input="l1", output="r1")
        add_palnode(store, "", "L1", n1)

        n2 = _make_node(input="", output="")
        path2 = add_palnode(store, "L1", "F0", n2)
        assert path2 == f"{_DEFAULT_CANONICAL}.L1.F0"

        n3 = _make_node(input="tool-p", output="tool-r")
        path3 = add_palnode(store, "L1.F0", "Q0", n3)
        assert path3 == f"{_DEFAULT_CANONICAL}.L1.F0.Q0"

        assert store.children["L1"].children["F0"].children["Q0"].input == "tool-p"

    def test_grammar_enforced(self, ctx_env):
        """PALTree grammar is enforced: only L at root, no L→L, C/N are leaves."""
        store = _make_root()

        # L at root: allowed
        add_palnode(store, "", "L0", _make_node())
        assert "L0" in store.children

        # Q at root: rejected
        with pytest.raises(ValueError, match="grammar violation"):
            add_palnode(store, "", "Q0", _make_node())

        # Numeric at root: rejected
        with pytest.raises(ValueError, match="grammar violation"):
            add_palnode(store, "", "0", _make_node())

        # F under L: allowed
        add_palnode(store, "L0", "F0", _make_node())

        # L under L: rejected
        with pytest.raises(ValueError, match="grammar violation"):
            add_palnode(store, "L0", "L1", _make_node())


# ---------------------------------------------------------------------------
# TestWalkAncestryEdgeCases
# ---------------------------------------------------------------------------


class TestWalkAncestryEdgeCases:
    def test_partial_walk_first_valid_second_missing(self, ctx_env):
        store = _make_root()
        child = _make_node(input="p1", output="r1")
        store.children["L1"] = child

        result = walk_palnode_ancestry(store, "L1.Q0")
        assert len(result) == 1
        assert result[0].input == "p1"

    def test_walk_through_mixed_entry_types(self, ctx_env):
        store = _make_root()
        n_store = _make_node(input="store-p", output="store-r")
        n_fork = _make_node(input="", output="")
        n_query = _make_node(input="query-p", output="query-r")
        n_tool = _make_node(input="tool-p", output="tool-r")

        n_query.children["thinkdeep"] = n_tool
        n_fork.children["Q0"] = n_query
        n_store.children["F0"] = n_fork
        store.children["L1"] = n_store

        result = walk_palnode_ancestry(store, "L1.F0.Q0.thinkdeep")
        assert len(result) == 4
        assert result[0].input == "store-p"
        assert result[1].input == ""
        assert result[2].input == "query-p"
        assert result[3].input == "tool-p"


# ---------------------------------------------------------------------------
# TestGetNextKeyEdgeCases
# ---------------------------------------------------------------------------


class TestGetNextKeyEdgeCases:
    def test_gap_in_numbering_returns_max_plus_one(self, ctx_env):
        store = _make_root()
        store.children["L0"] = _make_node()
        store.children["L2"] = _make_node()

        result = get_next_key(store, "", "L")
        assert result == "L3"

    def test_nonexistent_parent_path_treats_as_empty_L(self, ctx_env):
        store = _make_root()
        result = get_next_key(store, "NONEXISTENT", "L")
        assert result == "L0"

    def test_nonexistent_parent_path_treats_as_empty_Q(self, ctx_env):
        store = _make_root()
        result = get_next_key(store, "NONEXISTENT", "Q")
        assert result == "Q0"

    def test_mixed_children_types_each_prefix_independent(self, ctx_env):
        store = _make_root()
        store.children["L0"] = _make_node()
        store.children["L1"] = _make_node()
        store.children["Q0"] = _make_node()
        store.children["Q1"] = _make_node()
        store.children["F0"] = _make_node()

        assert get_next_key(store, "", "L") == "L2"
        assert get_next_key(store, "", "Q") == "Q2"
        assert get_next_key(store, "", "F") == "F1"


# ---------------------------------------------------------------------------
# TestParseStorePathEdgeCases
# ---------------------------------------------------------------------------


class TestParseStorePathEdgeCases:
    def test_store_id_with_hyphen(self, ctx_env):
        update_index("/home/user/project:my-store")
        root, segments = parse_tree_path("my-store.L1.Q0")
        assert root == "/home/user/project:my-store"
        assert segments == ["L1", "Q0"]

    def test_empty_string(self, ctx_env):
        root, segments = parse_tree_path("")
        assert root == ""
        assert segments == []

    def test_canonical_path_no_segments(self, ctx_env):
        root, segments = parse_tree_path("/home/user/project:mystore")
        assert root == "/home/user/project:mystore"
        assert segments == []

    def test_canonical_path_with_segments(self, ctx_env):
        root, segments = parse_tree_path("/home/user/project:mystore.L1.Q0")
        assert root == "/home/user/project:mystore"
        assert segments == ["L1", "Q0"]


# ---------------------------------------------------------------------------
# TestResolveStoreLocationEdgeCases
# ---------------------------------------------------------------------------


class TestResolveStoreLocationEdgeCases:
    def test_dotted_path_resolves_to_root(self, ctx_env):
        store = _make_root()
        save_tree(store)
        update_index(store.tree_path)

        result = resolve_tree_location("mystore")
        assert result is not None
        directory, root_id = result
        assert root_id == "mystore"
        assert directory == store.directory

    def test_stale_index_entry_still_resolves_name(self, ctx_env):
        """resolve_tree_location with a shorthand resolves via the index regardless
        of file existence. The caller is responsible for checking load_tree()."""
        import os

        from utils.palstore import get_tree_file_path

        store = _make_root()
        save_tree(store)
        update_index(store.tree_path)

        os.remove(get_tree_file_path(store.tree_path))

        result = resolve_tree_location("mystore")
        assert result is not None
        directory, root_id = result
        assert root_id == "mystore"
        assert directory == store.directory

    def test_unknown_store_with_empty_context_dir_returns_none(self, ctx_env):
        result = resolve_tree_location("completely-unknown-store-xyz")
        assert result is None


# ---------------------------------------------------------------------------
# TestArmedOperationsEdgeCases
# ---------------------------------------------------------------------------


class TestArmedOperationsEdgeCases:
    def test_arm_same_directory_twice_second_wins(self, ctx_env):
        arm_tree("/home/user/project", "store-a")
        arm_tree("/home/user/project", "store-b")

        result = get_armed_tree("/home/user/project")
        assert result == "store-b"

    def test_list_armed_stores_when_empty_returns_empty_dict(self, ctx_env):
        result = list_armed_trees()
        assert result == {}


# ---------------------------------------------------------------------------
# TestListStoresEdgeCases
# ---------------------------------------------------------------------------


class TestListStoresEdgeCases:
    def test_multiple_stores_in_same_directory(self, ctx_env):
        store_a = _make_root(tree_path="store-a")
        store_b = _make_root(tree_path="store-b")
        save_tree(store_a)
        save_tree(store_b)

        results = list_trees(directory="/home/user/project")
        names = {s.tree_name for s in results}
        assert "store-a" in names
        assert "store-b" in names
        assert len(results) == 2

    def test_directory_with_no_json_files_returns_empty(self, ctx_env):
        from utils.palstore import get_tree_dir

        folder = get_tree_dir("/home/user/project")
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "not-a-store.txt"), "w") as f:
            f.write("irrelevant content")

        results = list_trees(directory="/home/user/project")
        assert results == []


# ---------------------------------------------------------------------------
# TestListAllDirectories
# ---------------------------------------------------------------------------


class TestListAllDirectories:
    def test_returns_indexed_directories(self, ctx_env):
        from utils.palstore import list_all_directories

        store = _make_root(tree_path="store-a", directory="/home/user/proj-a")
        save_tree(store)
        update_index(store.tree_path)

        result = list_all_directories()
        assert "/home/user/proj-a" in result

    def test_returns_unindexed_directories_via_scan(self, ctx_env):
        from utils.palstore import list_all_directories

        store = _make_root(tree_path="store-b", directory="/home/user/proj-b")
        save_tree(store)

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
            timestamp="2026-01-01T00:00:00Z",
            input="FULL CONTENT BLOB",
        )
        store.children["L1"] = node

        thread = hydrate_thread_context(store, "L1")
        assert len(thread.turns) == 1
        assert thread.turns[0].role == "user"
        assert thread.turns[0].content == "FULL CONTENT BLOB"

    def test_ancestor_without_content_yields_separate_turns(self, ctx_env):
        from utils.palstore_builder import hydrate_thread_context

        store = _make_root()
        node = _make_node(input="the question", output="the answer")
        store.children["L1"] = node

        thread = hydrate_thread_context(store, "L1")
        assert len(thread.turns) == 2
        assert thread.turns[0].role == "user"
        assert thread.turns[0].content == "the question"
        assert thread.turns[1].role == "assistant"
        assert thread.turns[1].content == "the answer"

    def test_empty_nodes_are_skipped(self, ctx_env):
        """Nodes with empty input/output produce no turns."""
        from utils.palstore_builder import hydrate_thread_context

        store = _make_root()
        n_content = _make_node(input="q1", output="r1")
        n_empty = _make_node(input="", output="")
        n_query = _make_node(input="q2", output="r2")

        n_empty.children["Q0"] = n_query
        n_content.children["F0"] = n_empty
        store.children["L1"] = n_content

        thread = hydrate_thread_context(store, "L1.F0.Q0")
        roles = [t.role for t in thread.turns]
        assert roles == ["user", "assistant", "user", "assistant"]

    def test_root_path_yields_empty_thread(self, ctx_env):
        from utils.palstore_builder import hydrate_thread_context

        store = _make_root()

        thread = hydrate_thread_context(store, "")
        assert len(thread.turns) == 0

    def test_runtime_error_when_get_thread_returns_none(self, ctx_env, monkeypatch):
        import utils.conversation_memory as cm
        from utils.palstore_builder import hydrate_thread_context

        monkeypatch.setattr(cm, "get_thread", lambda thread_id: None)

        store = _make_root()

        with pytest.raises(RuntimeError, match="Failed to retrieve hydrated thread"):
            hydrate_thread_context(store, "")


# ---------------------------------------------------------------------------
# TestBuildContextAdditional
# ---------------------------------------------------------------------------


class TestBuildContextAdditional:
    def test_node_with_prompt_but_empty_response_filtered_out(self):
        from utils.palstore_builder import build_context_from_ancestry

        node = _make_node(input="question only", output="")
        result = build_context_from_ancestry([node])
        assert result == ""

    def test_node_with_empty_prompt_but_response_filtered_out(self):
        from utils.palstore_builder import build_context_from_ancestry

        node = _make_node(input="", output="answer only")
        result = build_context_from_ancestry([node])
        assert result == ""

    def test_five_node_chain_turns_numbered_one_through_five(self):
        from utils.palstore_builder import build_context_from_ancestry

        nodes = [_make_node(input=f"q{i}", output=f"r{i}") for i in range(1, 6)]
        result = build_context_from_ancestry(nodes)
        for i in range(1, 6):
            assert f"Turn {i}" in result

    def test_multi_node_chain_ordering_preserved(self):
        from utils.palstore_builder import build_context_from_ancestry

        nodes = [
            _make_node(input="first", output="f-resp"),
            _make_node(input="second", output="s-resp"),
            _make_node(input="third", output="t-resp"),
        ]
        result = build_context_from_ancestry(nodes)
        assert result.index("first") < result.index("second") < result.index("third")
        assert "Turn 1" in result
        assert "Turn 3" in result


# ---------------------------------------------------------------------------
# TestReferenceTree
# ---------------------------------------------------------------------------


@pytest.fixture
def ref_tree(ctx_env):
    """Build the reference tree using add_palnode for every node."""
    store = _make_root()
    update_index(store.tree_path)

    # Root level: L1, L2, L3, L4
    add_palnode(store, "", "L1", _make_node(input="p1", output="r1"))
    add_palnode(store, "", "L2", _make_node(input="p2", output="r2"))
    add_palnode(store, "", "L3", _make_node(input="p3", output="r3"))
    add_palnode(store, "", "L4", _make_node(input="", output=""))

    # L1 subtree
    add_palnode(store, "L1", "Q0", _make_node(input="what is X?", output="answer"))
    add_palnode(store, "L1", "F0", _make_node(input="", output=""))

    # L1.Q0 subtree — Q→Q nesting is intentional (follow-up queries)
    add_palnode(store, "L1.Q0", "Q0", _make_node(input="sub-question", output="sub-answer"))
    add_palnode(store, "L1.Q0", "C0", _make_node(input="follow-up 1", output="fu-r1"))
    add_palnode(store, "L1.Q0", "C1", _make_node(input="follow-up 2", output="fu-r2"))
    add_palnode(store, "L1.Q0", "F0", _make_node(input="", output=""))
    add_palnode(store, "L1.Q0.F0", "C0", _make_node(input="td", output="td-r"))

    # L1.F0 subtree
    add_palnode(store, "L1.F0", "L1", _make_node(input="branch1", output="br1"))
    add_palnode(store, "L1.F0", "L2", _make_node(input="branch2", output="br2"))
    add_palnode(store, "L1.F0", "F0", _make_node(input="", output=""))
    add_palnode(store, "L1.F0.L1", "Q0", _make_node(input="branch query", output="bq-r"))
    add_palnode(store, "L1.F0.F0", "L1", _make_node(input="deep branch", output="db-r"))

    # L2 subtree
    add_palnode(store, "L2", "Q0", _make_node(input="query on L2", output="ql2-r"))
    add_palnode(store, "L2", "F0", _make_node(input="", output=""))
    add_palnode(store, "L2.F0", "Q1", _make_node(input="analyze", output="ana-r"))
    add_palnode(store, "L2.F0.Q1", "F0", _make_node(input="", output=""))
    add_palnode(store, "L2.F0.Q1.F0", "C0", _make_node(input="chat", output="chat-r"))

    # L4.F0 subtree (root-level fork)
    add_palnode(store, "L4", "F0", _make_node(input="", output=""))
    add_palnode(store, "L4.F0", "L1", _make_node(input="root fork layer", output="rfl-r"))
    add_palnode(store, "L4.F0", "Q0", _make_node(input="root fork query", output="rfq-r"))
    add_palnode(store, "L4.F0", "C0", _make_node(input="root fork td", output="rftd-r"))

    return store


# ---------------------------------------------------------------------------
# TestWalkAncestryComplex
# ---------------------------------------------------------------------------


class TestWalkAncestryComplex:
    @pytest.mark.parametrize(
        "path,expected_count",
        [
            # Pure parent-chain: count = number of path segments
            ("L1", 1),
            ("L2", 1),
            ("L3", 1),
            ("L1.Q0", 2),
            ("L1.Q0.Q0", 3),
            ("L1.Q0.C0", 3),
            ("L1.Q0.C1", 3),
            ("L1.Q0.F0", 3),
            ("L1.Q0.F0.C0", 4),
            ("L1.F0", 2),
            ("L1.F0.L1", 3),
            ("L1.F0.L2", 3),
            ("L1.F0.L1.Q0", 4),
            ("L1.F0.F0", 3),
            ("L1.F0.F0.L1", 4),
            ("L2.Q0", 2),
            ("L2.F0", 2),
            ("L2.F0.Q1", 3),
            ("L2.F0.Q1.F0", 4),
            ("L2.F0.Q1.F0.C0", 5),
            ("L4", 1),
            ("L4.F0", 2),
            ("L4.F0.L1", 3),
            ("L4.F0.Q0", 3),
            ("L4.F0.C0", 3),
        ],
    )
    def test_walk_path(self, ref_tree, path, expected_count):
        result = walk_palnode_ancestry(ref_tree, path)
        assert len(result) == expected_count, f"path={path}: expected {expected_count} nodes, got {len(result)}"


# ---------------------------------------------------------------------------
# TestWalkRangeComplex
# ---------------------------------------------------------------------------


class TestWalkRangeComplex:
    @pytest.mark.parametrize(
        "start,end,expected_count",
        [
            # Range is a slice of the direct ancestry chain from start to end
            ("L1", "L1.Q0.F0.C0", 4),
            ("L1.Q0", "L1.Q0.C1", 2),
            ("L1.Q0", "L1.Q0.F0.C0", 3),
            ("L1.F0", "L1.F0.L2", 2),
            ("L1.F0", "L1.F0.F0.L1", 3),
            ("L2", "L2.F0.Q1.F0.C0", 5),
            ("L4.F0", "L4.F0.C0", 2),
            ("L1", "L1.Q0", 2),
            ("L2.F0", "L2.F0.Q1.F0", 3),
        ],
    )
    def test_valid_range(self, ref_tree, start, end, expected_count):
        result = walk_palnode_range(ref_tree, start, end)
        assert len(result) == expected_count, f"start={start}, end={end}: expected {expected_count}, got {len(result)}"

    @pytest.mark.parametrize(
        "start,end",
        [
            # start is NOT an ancestor of end (siblings or unrelated paths)
            ("L2", "L1"),
            ("L1.Q0", "L2.Q0"),
            ("L1.F0.L1", "L2"),
            ("L4.F0", "L1"),
            ("NONEXIST", "L1"),
            ("L1", "NONEXIST"),
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
    add_palnode(store, "", "L1", _make_node(input="p1", output="r1"))
    add_palnode(store, "", "L2", _make_node(input="p2", output="r2"))
    add_palnode(store, "", "L3", _make_node(input="p3", output="r3"))
    add_palnode(store, "", "L4", _make_node(input="", output=""))
    add_palnode(store, "L1", "F0", _make_node(input="", output=""))
    add_palnode(store, "L1.F0", "L1", _make_node(input="branch1", output="br1"))
    add_palnode(store, "L1.F0", "L2", _make_node(input="branch2", output="br2"))
    add_palnode(store, "L2", "F0", _make_node(input="", output=""))
    add_palnode(store, "L2.F0", "Q1", _make_node(input="analyze", output="ana-r"))
    add_palnode(store, "L2.F0.Q1", "F0", _make_node(input="", output=""))
    add_palnode(store, "L2.F0.Q1.F0", "C0", _make_node(input="chat", output="chat-r"))


def _assert_insertion_order_queries(store):
    # L3 is a direct child of root — pure parent-chain returns 1 node
    r1 = walk_palnode_ancestry(store, "L3")
    assert len(r1) == 1
    assert r1[0].input == "p3"

    # L1.F0.L2 — 3 segments deep
    r2 = walk_palnode_ancestry(store, "L1.F0.L2")
    assert len(r2) == 3

    # L2.F0.Q1.F0.C0 — 5 segments deep
    r3 = walk_palnode_ancestry(store, "L2.F0.Q1.F0.C0")
    assert len(r3) == 5


class TestInsertionOrder:
    def test_forward_order(self, ctx_env):
        store = _make_root()
        update_index(store.tree_path)
        _build_minimal_ref_tree(store)
        _assert_insertion_order_queries(store)

    def test_reverse_order(self, ctx_env):
        store = _make_root()
        update_index(store.tree_path)

        # Build bottom-up, right-to-left where possible
        add_palnode(store, "", "L3", _make_node(input="p3", output="r3"))
        add_palnode(store, "", "L2", _make_node(input="p2", output="r2"))
        add_palnode(store, "", "L1", _make_node(input="p1", output="r1"))
        add_palnode(store, "", "L4", _make_node(input="", output=""))

        add_palnode(store, "L2", "F0", _make_node(input="", output=""))
        add_palnode(store, "L2.F0", "Q1", _make_node(input="analyze", output="ana-r"))
        add_palnode(store, "L2.F0.Q1", "F0", _make_node(input="", output=""))
        add_palnode(store, "L2.F0.Q1.F0", "C0", _make_node(input="chat", output="chat-r"))

        add_palnode(store, "L1", "F0", _make_node(input="", output=""))
        add_palnode(store, "L1.F0", "L2", _make_node(input="branch2", output="br2"))
        add_palnode(store, "L1.F0", "L1", _make_node(input="branch1", output="br1"))

        _assert_insertion_order_queries(store)

    def test_interleaved_order(self, ctx_env):
        store = _make_root()
        update_index(store.tree_path)

        add_palnode(store, "", "L1", _make_node(input="p1", output="r1"))
        add_palnode(store, "", "L2", _make_node(input="p2", output="r2"))
        add_palnode(store, "L1", "F0", _make_node(input="", output=""))
        add_palnode(store, "", "L3", _make_node(input="p3", output="r3"))
        add_palnode(store, "L2", "F0", _make_node(input="", output=""))
        add_palnode(store, "L1.F0", "L1", _make_node(input="branch1", output="br1"))
        add_palnode(store, "L2.F0", "Q1", _make_node(input="analyze", output="ana-r"))
        add_palnode(store, "L1.F0", "L2", _make_node(input="branch2", output="br2"))
        add_palnode(store, "L2.F0.Q1", "F0", _make_node(input="", output=""))
        add_palnode(store, "", "L4", _make_node(input="", output=""))
        add_palnode(store, "L2.F0.Q1.F0", "C0", _make_node(input="chat", output="chat-r"))

        _assert_insertion_order_queries(store)


# ---------------------------------------------------------------------------
# TestDetachNode
# ---------------------------------------------------------------------------


class TestDetachNode:
    def test_detach_leaf(self, ctx_env):
        store = _make_root()
        update_index(store.tree_path)
        add_palnode(store, "", "L1", _make_node(input="hello", output="world"))

        node = detach_palnode(store, "L1")

        assert "L1" not in store.children
        assert node.input == "hello"

    def test_detach_with_subtree(self, ctx_env):
        store = _make_root()
        update_index(store.tree_path)
        add_palnode(store, "", "L1", _make_node(input="parent", output="pr"))
        add_palnode(store, "L1", "Q0", _make_node(input="child", output="cr"))

        node = detach_palnode(store, "L1")

        assert "L1" not in store.children
        assert "Q0" in node.children
        assert node.children["Q0"].input == "child"

    def test_detach_nonexistent_raises(self, ctx_env):
        store = _make_root()
        update_index(store.tree_path)

        with pytest.raises(KeyError):
            detach_palnode(store, "L99")

    def test_detach_root_raises(self, ctx_env):
        store = _make_root()
        update_index(store.tree_path)

        with pytest.raises(ValueError):
            detach_palnode(store, "")

    def test_siblings_not_renumbered(self, ctx_env):
        store = _make_root()
        update_index(store.tree_path)
        add_palnode(store, "", "L1", _make_node(input="one", output="r1"))
        add_palnode(store, "", "L2", _make_node(input="two", output="r2"))
        add_palnode(store, "", "L3", _make_node(input="three", output="r3"))

        detach_palnode(store, "L2")

        assert "L1" in store.children
        assert "L2" not in store.children
        assert "L3" in store.children
        assert store.children["L1"].input == "one"
        assert store.children["L3"].input == "three"


# ---------------------------------------------------------------------------
# TestMoveNode
# ---------------------------------------------------------------------------


class TestMoveNode:
    def test_move_between_parents(self, ctx_env):
        store = _make_root()
        update_index(store.tree_path)
        add_palnode(store, "", "L1", _make_node(input="", output=""))
        add_palnode(store, "L1", "F0", _make_node(input="", output=""))
        add_palnode(store, "L1", "F1", _make_node(input="", output=""))
        add_palnode(store, "L1.F0", "L1", _make_node(input="moveme", output="mv-r"))

        new_path = move_palnode(store, "L1.F0.L1", "L1.F1", "L1")

        assert new_path == f"{_DEFAULT_CANONICAL}.L1.F1.L1"
        l1 = store.children["L1"]
        assert "L1" not in l1.children["F0"].children
        assert "L1" in l1.children["F1"].children
        assert l1.children["F1"].children["L1"].input == "moveme"

    def test_move_preserves_subtree(self, ctx_env):
        store = _make_root()
        update_index(store.tree_path)
        add_palnode(store, "", "L1", _make_node(input="", output=""))
        add_palnode(store, "L1", "F0", _make_node(input="", output=""))
        add_palnode(store, "L1", "F1", _make_node(input="", output=""))
        add_palnode(store, "L1.F0", "L1", _make_node(input="parent", output="pr"))
        add_palnode(store, "L1.F0.L1", "Q0", _make_node(input="child", output="cr"))

        move_palnode(store, "L1.F0.L1", "L1.F1", "L1")

        moved = store.children["L1"].children["F1"].children["L1"]
        assert "Q0" in moved.children
        assert moved.children["Q0"].input == "child"

    def test_move_to_nonexistent_dest_rolls_back(self, ctx_env):
        """Moving to a non-existent parent path raises KeyError and rolls back."""
        store = _make_root()
        update_index(store.tree_path)
        add_palnode(store, "", "L1", _make_node(input="original", output="r"))

        with pytest.raises((ValueError, KeyError)):
            move_palnode(store, "L1", "NONEXISTENT", "X0")

        # Rollback: original node still in place
        assert "L1" in store.children
        assert store.children["L1"].input == "original"

    def test_move_returns_new_path(self, ctx_env):
        store = _make_root()
        update_index(store.tree_path)
        add_palnode(store, "", "L1", _make_node(input="", output=""))
        add_palnode(store, "L1", "F0", _make_node(input="", output=""))
        add_palnode(store, "L1.F0", "L1", _make_node(input="x", output="xr"))
        add_palnode(store, "", "L2", _make_node(input="", output=""))

        result = move_palnode(store, "L1.F0.L1", "", "L3")

        assert result == f"{_DEFAULT_CANONICAL}.L3"


# ---------------------------------------------------------------------------
# TestCopyNode
# ---------------------------------------------------------------------------


class TestCopyNode:
    def test_copy_creates_independent_clone(self, ctx_env):
        store = _make_root()
        update_index(store.tree_path)
        add_palnode(store, "", "L1", _make_node(input="orig", output="or"))
        add_palnode(store, "L1", "Q0", _make_node(input="orig-child", output="oc"))
        add_palnode(store, "", "L2", _make_node(input="", output=""))
        add_palnode(store, "L2", "F0", _make_node(input="", output=""))

        copy_palnode(store, "L1", "L2.F0", "L1")

        # Mutate copy; original must be unchanged
        store.children["L2"].children["F0"].children["L1"].input = "modified"
        assert store.children["L1"].input == "orig"

    def test_copy_preserves_content(self, ctx_env):
        store = _make_root()
        update_index(store.tree_path)
        add_palnode(
            store,
            "",
            "L1",
            _make_node(input="the prompt", output="the response", files=["a.py", "b.py"]),
        )
        add_palnode(store, "", "L2", _make_node(input="", output=""))
        add_palnode(store, "L2", "F0", _make_node(input="", output=""))

        copy_palnode(store, "L1", "L2.F0", "L1")
        copy_node_obj = store.children["L2"].children["F0"].children["L1"]

        assert copy_node_obj.input == "the prompt"
        assert copy_node_obj.output == "the response"
        assert copy_node_obj.files == ["a.py", "b.py"]

    def test_copy_source_not_found_raises(self, ctx_env):
        store = _make_root()
        update_index(store.tree_path)
        add_palnode(store, "", "L1", _make_node(input="x", output="xr"))

        with pytest.raises(KeyError):
            copy_palnode(store, "NONEXIST", "", "L2")

        # Original must still be present
        assert "L1" in store.children


# ---------------------------------------------------------------------------
# TestFoldRange
# ---------------------------------------------------------------------------


class TestFoldRange:
    def test_fold_chain(self, ctx_env):
        from utils.palstore_builder import build_context_from_ancestry

        store = _make_root()
        update_index(store.tree_path)
        n1 = _make_node(input="q1", output="r1")
        n2 = _make_node(input="q2", output="r2")
        n3 = _make_node(input="q3", output="r3")
        store.children["L1"] = n1
        n1.children["Q0"] = n2
        n2.children["1"] = n3

        result, _ = fold_palnode_range(store, "L1", "L1.Q0.1")

        expected_content = build_context_from_ancestry([n1, n2, n3])
        assert result.input == expected_content

    def test_fold_unions_files(self, ctx_env):
        store = _make_root()
        update_index(store.tree_path)
        n1 = _make_node(input="p1", output="r1", files=["a.py", "b.py"])
        n2 = _make_node(input="p2", output="r2", files=["b.py", "c.py"])
        store.children["L1"] = n1
        n1.children["Q0"] = n2

        result, _ = fold_palnode_range(store, "L1", "L1.Q0")

        assert result.files == ["a.py", "b.py", "c.py"]

    def test_fold_includes_all_range_files(self, ctx_env):
        """Files from all nodes in the range are included in the folded node."""
        store = _make_root()
        update_index(store.tree_path)
        n1 = _make_node(input="q1", output="r1", files=["f1.py"])
        n2 = _make_node(input="", output="")
        n3 = _make_node(input="q3", output="r3", files=["f3.py"])
        store.children["L1"] = n1
        n1.children["F0"] = n2
        n2.children["L1"] = n3

        result, _ = fold_palnode_range(store, "L1", "L1.F0.L1")

        assert "f1.py" in result.files
        assert "f3.py" in result.files

    def test_fold_returns_palnode(self, ctx_env):
        store = _make_root()
        update_index(store.tree_path)
        n1 = _make_node(input="p1", output="r1")
        store.children["L1"] = n1

        result, _ = fold_palnode_range(store, "L1", "L1")

        assert isinstance(result, PalNode)
        assert "L1" in store.children  # original unchanged


# ---------------------------------------------------------------------------
# TestFindAncestor
# ---------------------------------------------------------------------------


class TestFindAncestor:
    def test_find_ancestor_by_key_pattern(self, ctx_env):
        """find_palnode_ancestor works with any callable predicate."""
        store = _make_root()
        update_index(store.tree_path)
        add_palnode(store, "", "L1", _make_node(input="l1", output="r"))
        add_palnode(store, "L1", "Q0", _make_node(input="q", output="r"))
        add_palnode(store, "L1.Q0", "F0", _make_node(input="", output=""))
        add_palnode(store, "L1.Q0.F0", "C0", _make_node(input="td", output="r"))

        # Find the ancestor whose key starts with "L"
        result = find_palnode_ancestor(
            store,
            "L1.Q0.F0.C0",
            lambda key, node: key.startswith("L"),
        )

        assert result is not None
        path, node = result
        assert path == f"{_DEFAULT_CANONICAL}.L1"
        assert node.input == "l1"

    def test_find_nearest_fork_key(self, ctx_env):
        store = _make_root()
        update_index(store.tree_path)
        add_palnode(store, "", "L1", _make_node(input="", output=""))
        add_palnode(store, "L1", "F0", _make_node(input="", output=""))
        add_palnode(store, "L1.F0", "L1", _make_node(input="l1", output="r"))
        add_palnode(store, "L1.F0.L1", "F0", _make_node(input="", output=""))
        add_palnode(store, "L1.F0.L1.F0", "C0", _make_node(input="a", output="r"))

        result = find_palnode_ancestor(
            store,
            "L1.F0.L1.F0.C0",
            lambda key, node: key.startswith("F"),
        )

        assert result is not None
        path, node = result
        # Deepest fork ancestor is L1.F0.L1.F0
        assert path == f"{_DEFAULT_CANONICAL}.L1.F0.L1.F0"

    def test_no_match_returns_none(self, ctx_env):
        store = _make_root()
        update_index(store.tree_path)
        add_palnode(store, "", "L1", _make_node(input="l1", output="r"))
        add_palnode(store, "L1", "Q0", _make_node(input="q", output="r"))

        result = find_palnode_ancestor(
            store,
            "L1.Q0",
            lambda key, node: key.startswith("F"),
        )

        assert result is None

    def test_returns_deepest_match(self, ctx_env):
        store = _make_root()
        update_index(store.tree_path)
        add_palnode(store, "", "L1", _make_node(input="l1", output="r"))
        add_palnode(store, "L1", "F0", _make_node(input="", output=""))
        add_palnode(store, "L1.F0", "L1", _make_node(input="l2", output="r"))
        add_palnode(store, "L1.F0.L1", "Q0", _make_node(input="q", output="r"))

        result = find_palnode_ancestor(
            store,
            "L1.F0.L1.Q0",
            lambda key, node: key.startswith("L"),
        )

        assert result is not None
        path, _ = result
        # Both L1 ancestors exist; deepest is L1.F0.L1
        assert path == f"{_DEFAULT_CANONICAL}.L1.F0.L1"


# ---------------------------------------------------------------------------
# TestCollectSubtreeFiles
# ---------------------------------------------------------------------------


class TestCollectSubtreeFiles:
    def test_collects_from_node_and_children(self):
        node = _make_node(files=["a.py"])
        child = _make_node(files=["b.py"])
        node.children["Q0"] = child

        result = collect_palnode_files(node)

        assert result == ["a.py", "b.py"]

    def test_deduplicates(self):
        node = _make_node(files=["a.py", "b.py"])
        child = _make_node(files=["b.py", "c.py"])
        node.children["Q0"] = child

        result = collect_palnode_files(node)

        assert result == ["a.py", "b.py", "c.py"]
        assert len(result) == 3

    def test_empty_subtree(self):
        node = _make_node(files=[])

        result = collect_palnode_files(node)

        assert result == []

    def test_deep_nesting(self):
        root = _make_node(files=["root.py"])
        level1 = _make_node(files=["l1.py"])
        level2 = _make_node(files=["l2.py"])
        level3 = _make_node(files=["l3.py"])

        root.children["Q0"] = level1
        level1.children["1"] = level2
        level2.children["2"] = level3

        result = collect_palnode_files(root)

        assert result == ["root.py", "l1.py", "l2.py", "l3.py"]


# ---------------------------------------------------------------------------
# TestTraversalLog
# ---------------------------------------------------------------------------


class TestTraversalLog:
    def test_empty_log(self):
        tlog = TraversalLog(traversal_type="test")
        assert tlog.nodes_visited == []
        assert tlog.total_content_chars == 0
        assert tlog.total_tokens == 0
        d = tlog.to_dict()
        assert d["traversal_type"] == "test"
        assert d["traversal_order"] == []
        assert d["node_count"] == 0

    def test_record_accumulates(self):
        tlog = TraversalLog(traversal_type="ancestry")
        n1 = _make_node(input="hello", output="world")
        n2 = _make_node(input="foo", output="bar")
        tlog.record("/home/user/proj:mystore.L0", n1)
        tlog.record("/home/user/proj:mystore.L1", n2)

        assert tlog.nodes_visited == ["/home/user/proj:mystore.L0", "/home/user/proj:mystore.L1"]
        assert tlog.total_content_chars == len("helloworld") + len("foobar")
        assert tlog.total_tokens > 0
        d = tlog.to_dict()
        assert d["node_count"] == 2
        assert d["traversal_order"] == ["mystore:L0-L1"]

    def test_record_empty_node(self):
        tlog = TraversalLog(traversal_type="dfs")
        n = _make_node(input="", output="")
        tlog.record("/home/user/proj:mystore.L0", n)
        assert tlog.nodes_visited == ["/home/user/proj:mystore.L0"]
        assert tlog.total_content_chars == 0
        assert tlog.total_tokens == 0
        assert tlog.to_dict()["traversal_order"] == ["mystore:L0"]

    def test_compact_traversal_order_multi_tree(self):
        tlog = TraversalLog(traversal_type="dfs")
        n = _make_node(input="x", output="y")
        tlog.record("/dir:tree-a.L1", n)
        tlog.record("/dir:tree-a.L2", n)
        tlog.record("/dir:tree-a.L2.Q0", n)
        tlog.record("/other:tree-b.L1", n)
        tlog.record("/other:tree-b.L1.F0", n)
        order = tlog.to_dict()["traversal_order"]
        assert order == ["tree-a:L1-L2-L2.Q0", "tree-b:L1-L1.F0"]


# ---------------------------------------------------------------------------
# TestIterAncestry
# ---------------------------------------------------------------------------


class TestIterAncestry:
    def test_yields_ancestry_chain(self):
        root = _make_root()
        root.children["L0"] = _make_node(input="a", output="A")
        root.children["L0"].children["Q0"] = _make_node(input="b", output="B")
        root.children["L0"].children["Q0"].children["Q0"] = _make_node(input="c", output="C")

        pairs = list(iter_ancestry(root, "L0.Q0.Q0"))

        assert len(pairs) == 3
        assert [p for p, _ in pairs] == [
            f"{_DEFAULT_CANONICAL}.L0",
            f"{_DEFAULT_CANONICAL}.L0.Q0",
            f"{_DEFAULT_CANONICAL}.L0.Q0.Q0",
        ]
        assert pairs[0][1].input == "a"
        assert pairs[2][1].input == "c"

    def test_root_yields_nothing(self):
        root = _make_root()
        assert list(iter_ancestry(root, "")) == []

    def test_missing_node_stops(self):
        root = _make_root()
        root.children["L0"] = _make_node(input="a")
        pairs = list(iter_ancestry(root, "L0.Q0"))
        assert len(pairs) == 1
        assert pairs[0][0] == f"{_DEFAULT_CANONICAL}.L0"


# ---------------------------------------------------------------------------
# TestIterDfs
# ---------------------------------------------------------------------------


class TestIterDfs:
    def test_flat_children(self):
        n0 = _make_node(input="zero")
        n1 = _make_node(input="one")
        children = {"L0": n0, "L1": n1}

        pairs = list(iter_dfs("root", children))
        assert [p for p, _ in pairs] == ["root.L0", "root.L1"]

    def test_nested_dfs_order(self):
        root_children = {}
        l0 = _make_node(input="l0")
        l0.children["Q0"] = _make_node(input="q0")
        l0.children["Q1"] = _make_node(input="q1")
        l1 = _make_node(input="l1")
        root_children["L0"] = l0
        root_children["L1"] = l1

        paths = [p for p, _ in iter_dfs("r", root_children)]
        assert paths == ["r.L0", "r.L0.Q0", "r.L0.Q1", "r.L1"]

    def test_empty_children(self):
        assert list(iter_dfs("r", {})) == []

    def test_natural_sort_order(self):
        children = {f"L{i}": _make_node(input=str(i)) for i in [10, 2, 1, 0]}
        paths = [p for p, _ in iter_dfs("r", children)]
        assert paths == ["r.L0", "r.L1", "r.L2", "r.L10"]


# ---------------------------------------------------------------------------
# TestIterRange
# ---------------------------------------------------------------------------


class TestIterRange:
    def test_range_slice(self):
        root = _make_root()
        root.children["L0"] = _make_node(input="a")
        root.children["L0"].children["Q0"] = _make_node(input="b")
        root.children["L0"].children["Q0"].children["Q0"] = _make_node(input="c")

        pairs = list(iter_range(root, "L0.Q0", "L0.Q0.Q0"))
        assert len(pairs) == 2
        assert [p for p, _ in pairs] == [
            f"{_DEFAULT_CANONICAL}.L0.Q0",
            f"{_DEFAULT_CANONICAL}.L0.Q0.Q0",
        ]

    def test_single_node_range(self):
        root = _make_root()
        root.children["L0"] = _make_node(input="a")
        pairs = list(iter_range(root, "L0", "L0"))
        assert len(pairs) == 1

    def test_invalid_range_raises(self):
        root = _make_root()
        root.children["L0"] = _make_node(input="a")
        root.children["L1"] = _make_node(input="b")
        with pytest.raises(ValueError, match="not an ancestor"):
            list(iter_range(root, "L1", "L0"))


# ---------------------------------------------------------------------------
# TestCollectTraversal
# ---------------------------------------------------------------------------


class TestCollectTraversal:
    def test_collects_nodes_and_log(self):
        root = _make_root()
        root.children["L0"] = _make_node(input="hello", output="world")
        root.children["L0"].children["Q0"] = _make_node(input="foo", output="bar")

        nodes, tlog = collect_traversal(iter_ancestry(root, "L0.Q0"), "ancestry")

        assert len(nodes) == 2
        assert nodes[0].input == "hello"
        assert nodes[1].input == "foo"
        assert tlog.traversal_type == "ancestry"
        assert tlog.nodes_visited == [
            f"{_DEFAULT_CANONICAL}.L0",
            f"{_DEFAULT_CANONICAL}.L0.Q0",
        ]
        assert tlog.total_content_chars == len("helloworld") + len("foobar")
        assert tlog.total_tokens > 0

    def test_empty_generator(self):
        root = _make_root()
        nodes, tlog = collect_traversal(iter_ancestry(root, ""), "ancestry")
        assert nodes == []
        assert tlog.nodes_visited == []
        assert tlog.total_tokens == 0

    def test_dfs_collect(self):
        children = {"L0": _make_node(input="a"), "L1": _make_node(input="b")}
        nodes, tlog = collect_traversal(iter_dfs("r", children), "dfs")
        assert len(nodes) == 2
        assert tlog.traversal_type == "dfs"
        assert len(tlog.nodes_visited) == 2
