# -*- coding: utf-8 -*-
"""mc_geo_converter 的自检测试（无需 pytest，python -m unittest 即可运行）。"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import mc_geo_converter as m  # noqa: E402

try:
    import tkinter  # noqa: F401

    HAVE_TKINTER = True
except ImportError:  # pragma: no cover - depends on the Python install
    HAVE_TKINTER = False

if HAVE_TKINTER:
    import mc_geo_converter_gui as gui  # noqa: E402


def block_occupancy(data: m.StructureData, layer: int = 0):
    """Return {(x,y,z): (block_name, sorted_states)} for one block layer."""
    cells = data.primary if layer == 0 else data.secondary
    result = {}
    for position, palette_index in cells.items():
        ref = data.palette[palette_index]
        result[position] = (
            ref.name,
            tuple((key, state.kind, state.value) for key, state in ref.states),
        )
    return result


def non_air_occupancy(data: m.StructureData):
    return {
        position: value
        for position, value in block_occupancy(data, 0).items()
        if value[0] != "minecraft:air"
    }


class BoneNameTests(unittest.TestCase):
    def test_state_roundtrip_all_types(self):
        ref = m.BlockRef(
            "minecraft:door",
            (
                ("direction", m.BlockState("int", 3)),
                ("open_bit", m.BlockState("byte", 1)),
                ("text", m.BlockState("string", 'say "hi", please')),
                ("short_val", m.BlockState("short", -7)),
                ("long_val", m.BlockState("long", 9000000000)),
            ),
        )
        encoded = m.encode_block_ref(ref)
        parsed = m.parse_bone_name(encoded)
        self.assertEqual(parsed.ref, ref)
        self.assertEqual(parsed.layer, 0)

    def test_secondary_prefix(self):
        parsed = m.parse_bone_name("secondary:minecraft:water[liquid_depth=0]")
        self.assertEqual(parsed.layer, 1)
        self.assertEqual(parsed.ref.name, "minecraft:water")
        self.assertEqual(parsed.ref.states, (("liquid_depth", m.BlockState("int", 0)),))

    def test_cli_shorthand_only_for_block_option(self):
        self.assertEqual(m.parse_block_ref("stone").name, "minecraft:stone")
        # Ordinary entity bone names must not silently become block ids.
        self.assertIsNone(m.parse_bone_name("leg"))
        self.assertIsNone(m.parse_bone_name("body"))
        self.assertIsNotNone(m.parse_bone_name("minecraft:stone"))

    def test_invalid_reference_rejected(self):
        self.assertIsNone(m.parse_block_ref("minecraft:stone[axis"))
        self.assertIsNone(m.parse_bone_name("minecraft:stone[axis=y"))


class IndexingTests(unittest.TestCase):
    def test_wiki_example_2x3x4(self):
        # Bedrock index order is ZYX: i = SZ*SY*X + SZ*Y + Z.
        size = (2, 3, 4)
        order = []
        for x in range(size[0]):
            for y in range(size[1]):
                for z in range(size[2]):
                    order.append((x, y, z))
        for index, expected in enumerate(order):
            self.assertEqual(m.index_to_xyz(index, size), expected)
            self.assertEqual(m.xyz_to_index(*expected, size), index)


class GeometryFromStructureTests(unittest.TestCase):
    def test_basic_conversion(self):
        stone = m.BlockRef("minecraft:stone", (), 17959425)
        planks = m.BlockRef("minecraft:planks", (("wood_type", m.BlockState("string", "oak")),))
        air = m.BlockRef("minecraft:air", ())
        data = m.StructureData(
            size=(2, 1, 2),
            primary={(0, 0, 0): 1, (1, 0, 1): 2, (1, 0, 0): 0},
            secondary={},
            palette=[air, stone, planks],
        )
        geo = m.structure_to_geometry(data, source_stem="my house")
        self.assertEqual(geo["format_version"], m.DEFAULT_GEOMETRY_FORMAT_VERSION)
        geometry = geo["minecraft:geometry"][0]
        self.assertEqual(geometry["description"]["identifier"], "geometry.my_house")
        self.assertEqual(len(geometry["bones"]), 2)
        names = [bone["name"] for bone in geometry["bones"]]
        self.assertIn("minecraft:stone", names)
        self.assertIn('minecraft:planks[wood_type="oak"]', names)
        for bone in geometry["bones"]:
            for cube in bone["cubes"]:
                self.assertEqual(cube["size"], [1, 1, 1])
                self.assertEqual(len(cube["origin"]), 3)
        self.assertFalse(any('"air"' in name for name in names))

    def test_secondary_warning_when_omitted(self):
        data = m.StructureData(
            size=(1, 1, 1),
            primary={},
            secondary={(0, 0, 0): 1},
            palette=[m.BlockRef("minecraft:air"), m.BlockRef("minecraft:water")],
        )
        m.structure_to_geometry(data, source_stem="x")
        self.assertTrue(any("--include-secondary" in warning for warning in data.warnings))

    def test_uniform_scale_doubles_voxels(self):
        data = m.StructureData(
            size=(2, 1, 1),
            primary={(0, 0, 0): 0, (1, 0, 0): 0},
            secondary={},
            palette=[m.BlockRef("minecraft:stone")],
        )
        geo = m.structure_to_geometry(data, source_stem="scaled", scale=2)
        description = geo["minecraft:geometry"][0]["description"]
        self.assertEqual(description["visible_bounds_width"], 4)
        self.assertEqual(description["visible_bounds_height"], 2)
        cubes = geo["minecraft:geometry"][0]["bones"][0]["cubes"]
        self.assertEqual(cubes[0]["origin"], [0, 0, 0])
        self.assertEqual(cubes[1]["origin"], [2, 0, 0])
        for cube in cubes:
            self.assertEqual(cube["size"], [2, 2, 2])
        self.assertEqual(geo["minecraft:geometry"][0]["bones"][0]["pivot"], [1.0, 0.0, 0.0])

    def test_uniform_scale_fractional(self):
        data = m.StructureData(
            size=(1, 1, 1),
            primary={(0, 0, 0): 0},
            secondary={},
            palette=[m.BlockRef("minecraft:stone")],
        )
        geo = m.structure_to_geometry(data, source_stem="half", scale=0.5)
        cube = geo["minecraft:geometry"][0]["bones"][0]["cubes"][0]
        self.assertEqual(cube["origin"], [0.0, 0.0, 0.0])
        self.assertEqual(cube["size"], [0.5, 0.5, 0.5])
        self.assertEqual(geo["minecraft:geometry"][0]["description"]["visible_bounds_width"], 0.5)

    def test_invalid_scale_rejected(self):
        data = m.StructureData(
            size=(1, 1, 1),
            primary={},
            secondary={},
            palette=[m.BlockRef("minecraft:air")],
        )
        for invalid in (0, -1, float("inf"), float("nan"), "abc"):
            with self.assertRaises(m.ConverterError, msg=repr(invalid)):
                m.structure_to_geometry(data, source_stem="bad", scale=invalid)


class StructureFromGeometryTests(unittest.TestCase):
    def test_voxelise_and_normalise(self):
        geometry = {
            "description": {"identifier": "geometry.test"},
            "bones": [
                {
                    "name": "minecraft:stone",
                    "cubes": [{"origin": [-2, -1, -0.5], "size": [2, 1, 1]}],
                },
                {
                    "name": 'minecraft:planks[wood_type="oak"]',
                    "cubes": [{"origin": [0.2, 0, 0], "size": [1, 1, 1]}],
                },
            ],
        }
        fallback = m.parse_block_ref("minecraft:stone")
        data = m.geometry_to_structure(geometry, fallback)
        self.assertEqual(data.size, (3, 2, 2))
        self.assertEqual(non_air_occupancy(data), {
            (0, 0, 0): ("minecraft:stone", ()),
            (1, 0, 0): ("minecraft:stone", ()),
            (2, 1, 1): ("minecraft:planks", (("wood_type", "string", "oak"),)),
        })

    def test_empty_geometry_makes_air_cell(self):
        data = m.geometry_to_structure(
            {"description": {}, "bones": []}, m.parse_block_ref("minecraft:stone")
        )
        self.assertEqual(data.size, (1, 1, 1))
        self.assertEqual(data.palette[0].name, "minecraft:air")

    def test_non_block_bone_uses_fallback(self):
        geometry = {
            "description": {},
            "bones": [{"name": "leg", "cubes": [{"origin": [0, 0, 0], "size": [1, 1, 1]}]}],
        }
        data = m.geometry_to_structure(geometry, m.parse_block_ref("minecraft:diamond_block"))
        self.assertEqual(data.palette[1].name, "minecraft:diamond_block")
        self.assertTrue(data.warnings and "1 个骨骼" in data.warnings[0])

    def test_snap_round(self):
        geometry = {
            "description": {},
            "bones": [{"name": "minecraft:stone", "cubes": [{"origin": [0.5, 0.5, 0.5], "size": [1, 1, 1]}]}],
        }
        data = m.geometry_to_structure(geometry, m.parse_block_ref("minecraft:stone"), snap="round")
        self.assertIn((0, 0, 0), data.primary)

    def test_invalid_cube_reports_context(self):
        geometry = {
            "description": {"identifier": "geometry.bad"},
            "bones": [{"name": "minecraft:stone", "cubes": [{"origin": [0, 0, 0], "size": [0, 1, 1]}]}],
        }
        with self.assertRaises(m.ConverterError) as caught:
            m.geometry_to_structure(geometry, m.parse_block_ref("minecraft:stone"))
        self.assertIn("geometry.bad", str(caught.exception))
        self.assertIn("size", str(caught.exception))


class FileRoundTripTests(unittest.TestCase):
    def _write(self, obj, path):
        with open(path, "w", encoding="utf-8") as fileobj:
            json.dump(obj, fileobj, ensure_ascii=False)
            fileobj.write("\n")

    def test_scaled_geometry_roundtrip_with_voxel_size(self):
        original = m.StructureData(
            size=(3, 1, 1),
            primary={(0, 0, 0): 0, (1, 0, 0): 0, (2, 0, 0): 0},
            secondary={},
            palette=[m.BlockRef("minecraft:stone")],
        )
        geo = m.structure_to_geometry(original, source_stem="scaled", scale=2)
        self.assertEqual(geo["minecraft:geometry"][0]["bones"][0]["cubes"][0]["size"], [2, 2, 2])

        geometry = geo["minecraft:geometry"][0]
        back = m.geometry_to_structure(
            geometry, m.parse_block_ref("minecraft:stone"), voxel_size=2
        )
        self.assertEqual(back.size, original.size)
        self.assertEqual(non_air_occupancy(back), non_air_occupancy(original))

    def test_generated_mcstructure_is_little_endian_nbt(self):
        geometry = {
            "description": {"identifier": "geometry.small"},
            "bones": [{"name": "minecraft:stone", "cubes": [{"origin": [0, 0, 0], "size": [2, 1, 2]}]}],
        }
        with tempfile.TemporaryDirectory() as tmp:
            out_path = os.path.join(tmp, "small.mcstructure")
            data = m.write_structure_from_geometry(
                geometry, out_path, m.parse_block_ref("minecraft:stone")
            )
            with open(out_path, "rb") as fileobj:
                raw = fileobj.read()
            self.assertEqual(raw[:3], b"\x0a\x00\x00")
            back = m.parse_mcstructure(out_path)
            self.assertEqual(back.size, data.size)
            self.assertEqual(non_air_occupancy(back), non_air_occupancy(data))

    def test_real_sample_roundtrip(self):
        sample = ROOT / "samples" / "dirt_house.mcstructure"
        if not sample.exists():
            self.skipTest("sample not present")
        original = m.parse_mcstructure(str(sample))
        geo = m.structure_to_geometry(
            original, source_stem="dirt_house", include_secondary=True
        )
        with tempfile.TemporaryDirectory() as tmp:
            geo_path = os.path.join(tmp, "dirt_house.geo.json")
            self._write(geo, geo_path)
            geometry = m.select_geometry(m.load_geometry(geo_path))
            back = m.geometry_to_structure(
                geometry, m.parse_block_ref("minecraft:stone"),
                block_version=original.palette[0].version,
            )
            self.assertEqual(back.size, original.size)
            self.assertEqual(non_air_occupancy(back), non_air_occupancy(original))
            self.assertEqual(block_occupancy(back, 1), block_occupancy(original, 1))

    def test_waterlogged_sample_secondary_roundtrip(self):
        sample = ROOT / "samples" / "waterlogged.mcstructure"
        if not sample.exists():
            self.skipTest("sample not present")
        original = m.parse_mcstructure(str(sample))
        geo = m.structure_to_geometry(original, source_stem="wl", include_secondary=True)
        with tempfile.TemporaryDirectory() as tmp:
            geo_path = os.path.join(tmp, "wl.geo.json")
            self._write(geo, geo_path)
            back = m.geometry_to_structure(
                m.select_geometry(m.load_geometry(geo_path)),
                m.parse_block_ref("minecraft:stone"),
                block_version=original.palette[0].version,
            )
            self.assertEqual(block_occupancy(back, 0), block_occupancy(original, 0))
            self.assertEqual(block_occupancy(back, 1), block_occupancy(original, 1))

    def test_cli_auto_bidirectional(self):
        with tempfile.TemporaryDirectory() as tmp:
            geo_input = os.path.join(tmp, "in.geo.json")
            self._write(
                {
                    "format_version": "1.16.0",
                    "minecraft:geometry": [
                        {
                            "description": {"identifier": "geometry.cli"},
                            "bones": [
                                {
                                    "name": "minecraft:stone",
                                    "cubes": [{"origin": [0, 0, 0], "size": [1, 2, 1]}],
                                }
                            ],
                        }
                    ],
                },
                geo_input,
            )
            mc_path = os.path.join(tmp, "out.mcstructure")
            result = subprocess.run(
                [sys.executable, str(ROOT / "mc_geo_converter.py"), geo_input, "-o", mc_path],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(os.path.exists(mc_path))

            geo_back_path = os.path.join(tmp, "back.geo.json")
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "mc_geo_converter.py"),
                    "to-geo",
                    mc_path,
                    "-o",
                    geo_back_path,
                    "--identifier",
                    "geometry.cli",
                ],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            geometry = m.load_geometry(geo_back_path)[0]
            self.assertEqual(geometry["description"]["identifier"], "geometry.cli")

    def test_multi_geometry_selection_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "multi.geo.json")
            self._write(
                {
                    "minecraft:geometry": [
                        {"description": {"identifier": "geometry.a"}, "bones": []},
                        {"description": {"identifier": "geometry.b"}, "bones": []},
                    ]
                },
                path,
            )
            with self.assertRaises(m.ConverterError):
                m.select_geometry(m.load_geometry(path))
            self.assertEqual(
                m.select_geometry(m.load_geometry(path), "b")["description"]["identifier"],
                "geometry.b",
            )


@unittest.skipUnless(HAVE_TKINTER, "当前 Python 环境没有 tkinter")
class GuiOutputPathTests(unittest.TestCase):
    """GUI 输出路径（文件路径 / 指定目录）解析逻辑。"""

    def test_auto_output_name(self):
        self.assertEqual(
            gui.ConverterApp._auto_output_path("C:/x/house.geo.json", ".mcstructure"),
            "C:/x/house.mcstructure",
        )
        self.assertEqual(
            gui.ConverterApp._auto_output_path("C:/x/house.mcstructure", ".geo.json"),
            "C:/x/house.geo.json",
        )
        self.assertEqual(
            gui.ConverterApp._auto_output_path("C:/x/model.json", ".mcstructure"),
            "C:/x/model.mcstructure",
        )

    def test_resolve_output_paths(self):
        source = "C:/x/house.geo.json"
        with tempfile.TemporaryDirectory() as directory:
            result = gui.ConverterApp._resolve_output(source, directory, ".mcstructure")
            self.assertEqual(result, os.path.join(directory, "house.mcstructure"))

        self.assertEqual(
            gui.ConverterApp._resolve_output(source, "", ".mcstructure"),
            "C:/x/house.mcstructure",
        )
        self.assertEqual(
            gui.ConverterApp._resolve_output(source, "D:/out/custom.mcstructure", ".mcstructure"),
            "D:/out/custom.mcstructure",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
