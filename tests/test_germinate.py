"""Unit tests for the germinate tool."""

import json
import os
from unittest.mock import MagicMock, patch

import pytest

from tools.germinate import (
    GerminateTool,
    LayerSpec,
    _merge_thin_layers,
    _select_key_files,
    scan_project_layers,
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


@pytest.fixture
def project_dir(tmp_path):
    """Creates a temp project with known directory structure."""
    p = tmp_path / "myproject"
    p.mkdir()
    return p


def _touch(path: str, content: str = "# placeholder\n") -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(content)


def _make_mock_provider(response_text: str = "Analysis result.") -> MagicMock:
    mock_response = MagicMock()
    mock_response.content = response_text
    mock_provider = MagicMock()
    mock_provider.generate_content.return_value = mock_response
    mock_provider.get_provider_type.return_value = MagicMock(value="google")
    return mock_provider


# ---------------------------------------------------------------------------
# TestLayerSpec
# ---------------------------------------------------------------------------


class TestLayerSpec:
    def test_basic_construction(self):
        spec = LayerSpec(name="Core", ring=0, description="Core layer (2 files)")
        assert spec.name == "Core"
        assert spec.ring == 0
        assert spec.description == "Core layer (2 files)"
        assert spec.files == []
        assert spec.key_files == []

    def test_construction_with_files(self):
        files = ["/project/models/user.py", "/project/models/product.py"]
        spec = LayerSpec(name="Core", ring=0, description="Core layer (2 files)", files=files, key_files=files[:1])
        assert len(spec.files) == 2
        assert len(spec.key_files) == 1

    def test_default_mutable_fields_are_independent(self):
        a = LayerSpec(name="A", ring=0, description="A")
        b = LayerSpec(name="B", ring=1, description="B")
        a.files.append("/a.py")
        assert b.files == []


# ---------------------------------------------------------------------------
# TestScanProjectLayers
# ---------------------------------------------------------------------------


class TestScanProjectLayers:
    def test_models_dir_classified_as_core_ring_0(self, project_dir):
        _touch(str(project_dir / "models" / "user.py"), "class User: pass\n" * 20)
        _touch(str(project_dir / "models" / "product.py"), "class Product: pass\n" * 20)
        _touch(str(project_dir / "models" / "order.py"), "class Order: pass\n" * 20)

        layers = scan_project_layers(str(project_dir))
        names = [ly.name for ly in layers]
        assert "Core" in names
        core = next(ly for ly in layers if ly.name == "Core")
        assert core.ring == 0
        assert len(core.files) == 3

    def test_utils_dir_classified_as_foundation_ring_1(self, project_dir):
        for i in range(3):
            _touch(str(project_dir / "utils" / f"helper{i}.py"), "def helper(): pass\n" * 10)

        layers = scan_project_layers(str(project_dir))
        names = [ly.name for ly in layers]
        assert "Foundation" in names
        foundation = next(ly for ly in layers if ly.name == "Foundation")
        assert foundation.ring == 1

    def test_services_dir_classified_as_business_logic_ring_2(self, project_dir):
        for i in range(3):
            _touch(str(project_dir / "services" / f"svc{i}.py"), "class Service: pass\n" * 10)

        layers = scan_project_layers(str(project_dir))
        names = [ly.name for ly in layers]
        assert "Business Logic" in names
        bl = next(ly for ly in layers if ly.name == "Business Logic")
        assert bl.ring == 2

    def test_api_dir_classified_as_interface_ring_3(self, project_dir):
        for i in range(3):
            _touch(str(project_dir / "api" / f"endpoint{i}.py"), "def endpoint(): pass\n" * 10)

        layers = scan_project_layers(str(project_dir))
        names = [ly.name for ly in layers]
        assert "Interface" in names
        iface = next(ly for ly in layers if ly.name == "Interface")
        assert iface.ring == 3

    def test_excluded_dirs_are_skipped(self, project_dir):
        # Files in excluded dirs should not appear in any layer
        for excluded in (".git", "__pycache__", "node_modules"):
            _touch(str(project_dir / excluded / "secret.py"), "x = 1\n")

        # Add a real source file so layers are not empty
        for i in range(3):
            _touch(str(project_dir / "models" / f"m{i}.py"), "class M: pass\n" * 10)

        layers = scan_project_layers(str(project_dir))
        all_files = [f for ly in layers for f in ly.files]
        for f in all_files:
            assert ".git" not in f
            assert "__pycache__" not in f
            assert "node_modules" not in f

    def test_test_directories_are_excluded(self, project_dir):
        for tdir in ("tests", "test", "testing"):
            _touch(str(project_dir / tdir / "test_foo.py"), "def test_foo(): pass\n")

        for i in range(3):
            _touch(str(project_dir / "models" / f"m{i}.py"), "class M: pass\n" * 10)

        layers = scan_project_layers(str(project_dir))
        all_files = [f for ly in layers for f in ly.files]
        for f in all_files:
            assert "/tests/" not in f
            assert "/test/" not in f
            assert "/testing/" not in f

    def test_non_source_files_excluded(self, project_dir):
        _touch(str(project_dir / "models" / "user.pyc"), "bytecode")
        _touch(str(project_dir / "models" / "logo.png"), "PNG binary")
        _touch(str(project_dir / "models" / "poetry.lock"), "lock file")
        # Real source file so the layer survives merging
        for i in range(3):
            _touch(str(project_dir / "models" / f"m{i}.py"), "class M: pass\n" * 10)

        layers = scan_project_layers(str(project_dir))
        all_files = [f for ly in layers for f in ly.files]
        for f in all_files:
            assert not f.endswith(".pyc")
            assert not f.endswith(".png")
            assert not f.endswith(".lock")

    def test_layers_sorted_by_ring(self, project_dir):
        for i in range(3):
            _touch(str(project_dir / "api" / f"ep{i}.py"), "x\n" * 10)
            _touch(str(project_dir / "models" / f"m{i}.py"), "x\n" * 10)
            _touch(str(project_dir / "utils" / f"u{i}.py"), "x\n" * 10)

        layers = scan_project_layers(str(project_dir))
        rings = [ly.ring for ly in layers]
        assert rings == sorted(rings)

    def test_multiple_dirs_same_ring_merged_or_classified(self, project_dir):
        # 'models' and 'schema' both map to ring 0 / Core; they may be merged or separate
        for i in range(3):
            _touch(str(project_dir / "models" / f"m{i}.py"), "x\n" * 10)
            _touch(str(project_dir / "schema" / f"s{i}.py"), "x\n" * 10)

        layers = scan_project_layers(str(project_dir))
        # Both sets of files should appear somewhere in the result
        all_files = [f for ly in layers for f in ly.files]
        assert any("models" in f for f in all_files)
        assert any("schema" in f for f in all_files)

    def test_empty_directory_returns_empty_list(self, project_dir):
        layers = scan_project_layers(str(project_dir))
        assert layers == []

    def test_file_level_fallback_classifies_by_name(self, project_dir):
        # Files with 'model' in stem at root level should go to Core via file pattern fallback
        _touch(str(project_dir / "user_model.py"), "class UserModel: pass\n" * 10)
        _touch(str(project_dir / "product_model.py"), "class ProductModel: pass\n" * 10)
        _touch(str(project_dir / "order_model.py"), "class OrderModel: pass\n" * 10)

        layers = scan_project_layers(str(project_dir))
        all_files = [f for ly in layers for f in ly.files]
        assert any("user_model.py" in f for f in all_files)


# ---------------------------------------------------------------------------
# TestMergeThinLayers
# ---------------------------------------------------------------------------


class TestMergeThinLayers:
    def _layer(self, name: str, ring: int, n_files: int) -> LayerSpec:
        files = [f"/project/{name}/f{i}.py" for i in range(n_files)]
        return LayerSpec(name=name, ring=ring, description=f"{name} ({n_files})", files=files, key_files=files)

    def test_thin_layer_merges_into_nearest_thick_neighbor(self):
        thin = self._layer("Thin", ring=1, n_files=1)
        thick = self._layer("Thick", ring=2, n_files=5)

        result = _merge_thin_layers([thin, thick])

        assert len(result) == 1
        assert result[0].name == "Thick"
        assert len(result[0].files) == 6

    def test_all_thick_layers_unchanged(self):
        a = self._layer("A", ring=0, n_files=4)
        b = self._layer("B", ring=2, n_files=5)

        result = _merge_thin_layers([a, b])
        assert len(result) == 2
        assert {r.name for r in result} == {"A", "B"}

    def test_single_layer_returned_unchanged(self):
        only = self._layer("Only", ring=0, n_files=1)
        result = _merge_thin_layers([only])
        assert result == [only]

    def test_empty_list_returned_unchanged(self):
        assert _merge_thin_layers([]) == []

    def test_thin_merges_into_nearest_by_ring(self):
        # thin at ring 3, thick at ring 0 and ring 4 — should merge into ring 4
        far = self._layer("Far", ring=0, n_files=5)
        near = self._layer("Near", ring=4, n_files=5)
        thin = self._layer("Thin", ring=3, n_files=2)

        result = _merge_thin_layers([far, thin, near])
        assert len(result) == 2
        names = {r.name for r in result}
        assert "Far" in names
        assert "Near" in names
        near_result = next(r for r in result if r.name == "Near")
        assert len(near_result.files) == 7  # 5 own + 2 absorbed

    def test_merged_files_are_sorted(self):
        thin = self._layer("Thin", ring=1, n_files=2)
        thick = self._layer("Thick", ring=2, n_files=3)

        result = _merge_thin_layers([thin, thick])
        assert result[0].files == sorted(result[0].files)

    def test_all_thin_no_thick_returns_all_layers(self):
        a = self._layer("A", ring=0, n_files=1)
        b = self._layer("B", ring=2, n_files=2)
        result = _merge_thin_layers([a, b])
        # No thick layers exist; all layers returned as-is
        assert len(result) == 2


# ---------------------------------------------------------------------------
# TestSelectKeyFiles
# ---------------------------------------------------------------------------


class TestSelectKeyFiles:
    def test_fewer_than_max_returns_all(self, tmp_path):
        files = []
        for i in range(4):
            f = tmp_path / f"file{i}.py"
            f.write_text("x = 1\n")
            files.append(str(f))

        result = _select_key_files(files, max_files=6)
        assert sorted(result) == sorted(files)

    def test_caps_at_max_files(self, tmp_path):
        files = []
        for i in range(10):
            f = tmp_path / f"file{i}.py"
            f.write_text("x = 1\n" * (i + 1))
            files.append(str(f))

        result = _select_key_files(files, max_files=4)
        assert len(result) == 4

    def test_larger_files_preferred(self, tmp_path):
        small = tmp_path / "small.py"
        small.write_text("x = 1\n")

        big = tmp_path / "big.py"
        big.write_text("x = 1\n" * 500)

        # Pad with neutral filler files to exceed max
        fillers = []
        for i in range(5):
            f = tmp_path / f"filler{i}.py"
            f.write_text("y = 2\n" * 5)
            fillers.append(str(f))

        all_files = [str(small), str(big)] + fillers
        result = _select_key_files(all_files, max_files=3)
        assert str(big) in result

    def test_recognized_names_boosted(self, tmp_path):
        tiny_model = tmp_path / "user_model.py"
        tiny_model.write_text("x = 1\n")

        # A large but unnamed file
        big_generic = tmp_path / "xyzzy.py"
        big_generic.write_text("x = 1\n" * 200)

        # More filler files
        fillers = []
        for i in range(5):
            f = tmp_path / f"filler{i}.py"
            f.write_text("y = 2\n" * 50)
            fillers.append(str(f))

        all_files = [str(tiny_model), str(big_generic)] + fillers
        result = _select_key_files(all_files, max_files=3)
        assert str(tiny_model) in result

    def test_init_penalized(self, tmp_path):
        init_file = tmp_path / "__init__.py"
        init_file.write_text("# init\n" * 100)

        real_file = tmp_path / "logic.py"
        real_file.write_text("x = 1\n")

        # Fill to exceed max
        fillers = []
        for i in range(5):
            f = tmp_path / f"filler{i}.py"
            f.write_text("y = 2\n" * 50)
            fillers.append(str(f))

        all_files = [str(init_file), str(real_file)] + fillers
        result = _select_key_files(all_files, max_files=3)
        # __init__.py should be pushed out in favour of larger/boosted files
        assert str(init_file) not in result


# ---------------------------------------------------------------------------
# TestGerminateTool — execute()
# ---------------------------------------------------------------------------


class TestGerminateTool:
    @pytest.fixture
    def tool(self):
        return GerminateTool()

    def _build_project(self, base: str) -> None:
        """Build a minimal project with three thick layers."""
        for i in range(3):
            _touch(os.path.join(base, "models", f"m{i}.py"), "class M: pass\n" * 20)
        for i in range(3):
            _touch(os.path.join(base, "utils", f"u{i}.py"), "def util(): pass\n" * 20)
        for i in range(3):
            _touch(os.path.join(base, "api", f"ep{i}.py"), "def endpoint(): pass\n" * 20)

    @pytest.mark.asyncio
    async def test_nonexistent_directory_returns_error(self, tool, ctx_env):
        result = await tool.execute({"directory": "/does/not/exist/at/all"})
        assert len(result) == 1
        data = json.loads(result[0].text)
        assert data["status"] == "error"

    @pytest.mark.asyncio
    async def test_dot_in_tree_name_returns_error(self, tool, ctx_env, tmp_path):
        project = tmp_path / "proj"
        project.mkdir()
        result = await tool.execute({"directory": str(project), "tree_name": "my.tree"})
        assert len(result) == 1
        data = json.loads(result[0].text)
        assert data["status"] == "error"
        assert "dots" in data["content"].lower() or "dot" in data["content"].lower()

    @pytest.mark.asyncio
    async def test_tree_already_exists_returns_error(self, tool, ctx_env, tmp_path):
        from utils.palstore import PalRoot, save_store, update_index

        project = tmp_path / "existing_proj"
        project.mkdir()
        self._build_project(str(project))

        # Pre-create a store with the same name
        store = PalRoot(tree_path="existing_proj", directory=str(project), created_at="2026-01-01T00:00:00Z")
        save_store(store)
        update_index(str(project), "existing_proj")

        result = await tool.execute({"directory": str(project), "tree_name": "existing_proj"})
        assert len(result) == 1
        data = json.loads(result[0].text)
        assert data["status"] == "error"
        assert "already exists" in data["content"].lower()

    @pytest.mark.asyncio
    async def test_full_flow_creates_tree_and_nodes(self, tool, ctx_env, tmp_path):
        project = tmp_path / "germtest"
        project.mkdir()
        self._build_project(str(project))

        mock_provider = _make_mock_provider("Layer analysis complete.")

        with patch.object(tool, "get_model_provider", return_value=mock_provider):
            result = await tool.execute(
                {"directory": str(project), "tree_name": "germtest", "model": "gemini-2.5-flash"}
            )

        assert len(result) == 1
        data = json.loads(result[0].text)
        assert data["status"] in ("success", "partial")
        assert "tree_path" in data["metadata"]
        assert data["metadata"]["tree_path"] == "germtest"

    @pytest.mark.asyncio
    async def test_response_contains_layer_count(self, tool, ctx_env, tmp_path):
        project = tmp_path / "layercount"
        project.mkdir()
        self._build_project(str(project))

        mock_provider = _make_mock_provider("Synthesis output.")

        with patch.object(tool, "get_model_provider", return_value=mock_provider):
            result = await tool.execute(
                {"directory": str(project), "tree_name": "layercount", "model": "gemini-2.5-flash"}
            )

        data = json.loads(result[0].text)
        assert "layers_completed" in data["metadata"]
        assert data["metadata"]["layers_completed"] > 0
        assert "layers_total" in data["metadata"]

    @pytest.mark.asyncio
    async def test_l1_node_created_in_store(self, tool, ctx_env, tmp_path):
        from utils.palstore import load_store

        project = tmp_path / "l1test"
        project.mkdir()
        self._build_project(str(project))

        mock_provider = _make_mock_provider("Analysis.")

        with patch.object(tool, "get_model_provider", return_value=mock_provider):
            await tool.execute({"directory": str(project), "tree_name": "l1test", "model": "gemini-2.5-flash"})

        store = load_store(str(project), "l1test")
        assert store is not None
        assert "0" in store.children

    @pytest.mark.asyncio
    async def test_query_nodes_nested_under_l1(self, tool, ctx_env, tmp_path):
        from utils.palstore import load_store

        project = tmp_path / "qnest"
        project.mkdir()
        self._build_project(str(project))

        mock_provider = _make_mock_provider("Q synthesis.")

        with patch.object(tool, "get_model_provider", return_value=mock_provider):
            await tool.execute({"directory": str(project), "tree_name": "qnest", "model": "gemini-2.5-flash"})

        store = load_store(str(project), "qnest")
        assert store is not None
        l1 = store.children["0"]
        # At least one numeric child node should be nested under the manifest node
        assert any(k.isdigit() for k in l1.children)

    @pytest.mark.asyncio
    async def test_query_nodes_nested_chain_not_siblings(self, tool, ctx_env, tmp_path):
        """Layers accumulate as nested Q-nodes, not siblings under L1."""
        from utils.palstore import load_store

        project = tmp_path / "nestchain"
        project.mkdir()
        self._build_project(str(project))

        mock_provider = _make_mock_provider("Nested Q.")

        with patch.object(tool, "get_model_provider", return_value=mock_provider):
            result = await tool.execute(
                {"directory": str(project), "tree_name": "nestchain", "model": "gemini-2.5-flash"}
            )

        data = json.loads(result[0].text)
        layers_completed = data["metadata"]["layers_completed"]

        if layers_completed >= 2:
            store = load_store(str(project), "nestchain")
            manifest = store.children["0"]
            # Only one numeric node should be a direct child of the manifest; the rest nest deeper
            numeric_keys_on_manifest = [k for k in manifest.children if k.isdigit()]
            assert len(numeric_keys_on_manifest) == 1, "Layers must nest, not accumulate as siblings under manifest"

    @pytest.mark.asyncio
    async def test_final_node_path_in_metadata(self, tool, ctx_env, tmp_path):
        project = tmp_path / "finpath"
        project.mkdir()
        self._build_project(str(project))

        mock_provider = _make_mock_provider("Synthesis.")

        with patch.object(tool, "get_model_provider", return_value=mock_provider):
            result = await tool.execute(
                {"directory": str(project), "tree_name": "finpath", "model": "gemini-2.5-flash"}
            )

        data = json.loads(result[0].text)
        assert "final_node" in data["metadata"]
        final = data["metadata"]["final_node"]
        # final_node must be a dot-path starting with the tree name
        assert final.startswith("finpath.")

    @pytest.mark.asyncio
    async def test_provider_called_twice_per_layer(self, tool, ctx_env, tmp_path):
        """Each layer triggers analyze + synthesize = 2 provider calls."""
        project = tmp_path / "callcount"
        project.mkdir()
        # Build exactly one thick layer (models only)
        for i in range(3):
            _touch(os.path.join(str(project), "models", f"m{i}.py"), "class M: pass\n" * 20)

        mock_provider = _make_mock_provider("Response.")

        with patch.object(tool, "get_model_provider", return_value=mock_provider):
            result = await tool.execute(
                {"directory": str(project), "tree_name": "callcount", "model": "gemini-2.5-flash"}
            )

        data = json.loads(result[0].text)
        # Each completed layer = 2 calls (analyze + synthesize)
        expected_calls = data["metadata"]["layers_completed"] * 2
        assert mock_provider.generate_content.call_count == expected_calls

    @pytest.mark.asyncio
    async def test_tree_name_defaults_to_directory_basename(self, tool, ctx_env, tmp_path):
        from utils.palstore import load_store

        project = tmp_path / "auto_named"
        project.mkdir()
        self._build_project(str(project))

        mock_provider = _make_mock_provider("Auto name.")

        with patch.object(tool, "get_model_provider", return_value=mock_provider):
            result = await tool.execute({"directory": str(project), "model": "gemini-2.5-flash"})

        data = json.loads(result[0].text)
        assert data["metadata"]["tree_path"] == "auto_named"
        store = load_store(str(project), "auto_named")
        assert store is not None
