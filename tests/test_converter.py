# -*- coding: utf-8 -*-
"""mc_geo_converter 的自检测试（无需 pytest，python -m unittest 即可运行）。"""

import json
import os
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib
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
        self.assertEqual(len(cubes), 1)  # 相邻同种方块默认合并
        self.assertEqual(cubes[0]["origin"], [0, 0, 0])
        self.assertEqual(cubes[0]["size"], [4, 2, 2])
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
        cubes = geo["minecraft:geometry"][0]["bones"][0]["cubes"]
        self.assertEqual(len(cubes), 1)  # 3 个相邻方块合并为 1 个
        self.assertEqual(cubes[0]["size"], [6, 2, 2])

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


class MergeVoxelTests(unittest.TestCase):
    """相邻同种方块贪心合并为长方体。"""

    def test_solid_cube_merges_to_single_cube(self):
        stone = m.BlockRef("minecraft:stone")
        data = m.StructureData(
            size=(2, 2, 2),
            primary={(x, y, z): 0 for x in range(2) for y in range(2) for z in range(2)},
            secondary={},
            palette=[stone],
        )
        geo = m.structure_to_geometry(data, source_stem="cube")
        cubes = geo["minecraft:geometry"][0]["bones"][0]["cubes"]
        self.assertEqual(len(cubes), 1)
        self.assertEqual(cubes[0]["origin"], [0, 0, 0])
        self.assertEqual(cubes[0]["size"], [2, 2, 2])

    def test_l_shape_merges_to_two_cubes(self):
        stone = m.BlockRef("minecraft:stone")
        cells = {(x, 0, 0): 0 for x in range(3)}
        cells.update({(0, 1, 0): 0, (0, 2, 0): 0})
        data = m.StructureData(size=(3, 3, 1), primary=cells, secondary={}, palette=[stone])
        geo = m.structure_to_geometry(data, source_stem="l")
        cubes = geo["minecraft:geometry"][0]["bones"][0]["cubes"]
        self.assertEqual(len(cubes), 2)
        sizes = sorted(tuple(cube["size"]) for cube in cubes)
        self.assertEqual(sizes, [(1, 3, 1), (2, 1, 1)])

    def test_merge_can_be_disabled(self):
        stone = m.BlockRef("minecraft:stone")
        data = m.StructureData(
            size=(2, 1, 1),
            primary={(0, 0, 0): 0, (1, 0, 0): 0},
            secondary={},
            palette=[stone],
        )
        geo = m.structure_to_geometry(data, source_stem="x", merge=False)
        cubes = geo["minecraft:geometry"][0]["bones"][0]["cubes"]
        self.assertEqual(len(cubes), 2)
        for cube in cubes:
            self.assertEqual(cube["size"], [1, 1, 1])

    def test_merged_geometry_roundtrips_losslessly(self):
        stone = m.BlockRef("minecraft:stone")
        cells = {(x, y, z): 0 for x in range(3) for y in range(2) for z in range(2)}
        data = m.StructureData(size=(3, 2, 2), primary=cells, secondary={}, palette=[stone])
        geo = m.structure_to_geometry(data, source_stem="big")
        geometry = geo["minecraft:geometry"][0]
        self.assertEqual(len(geometry["bones"][0]["cubes"]), 1)
        back = m.geometry_to_structure(geometry, m.parse_block_ref("minecraft:stone"))
        self.assertEqual(non_air_occupancy(back), non_air_occupancy(data))


class MapColorTextureTests(unittest.TestCase):
    """map-color 贴图：颜色查表、图集规划、PNG 写出与 CLI 端到端。"""

    def _small_table(self):
        return {
            "colors": {
                "minecraft:stone": "#7f7f7f",
                "minecraft:dirt": "#866043",
                "minecraft:water": "#3f76e4",
            },
            "state_overrides": {"color": {"red": "#a12722", "blue": "#35399d"}},
        }

    def test_color_lookup_base_override_and_fallback(self):
        table = self._small_table()
        rgb, found = m.map_color_for(m.BlockRef("minecraft:stone"), table)
        self.assertTrue(found)
        self.assertEqual(rgb, (0x7F, 0x7F, 0x7F))
        rgb, found = m.map_color_for(
            m.BlockRef("minecraft:wool", (("color", m.BlockState("string", "red")),)),
            table,
        )
        self.assertTrue(found)
        self.assertEqual(rgb, (0xA1, 0x27, 0x22))
        rgb, found = m.map_color_for(m.BlockRef("minecraft:unknown_block"), table)
        self.assertFalse(found)
        self.assertEqual(rgb, m.DEFAULT_MAP_COLOR)

    def test_atlas_layout_and_uv_bounds(self):
        table = self._small_table()
        groups = [
            ((0, m.BlockRef("minecraft:stone")), [(0, 0, 0)]),
            ((0, m.BlockRef("minecraft:dirt")), [(1, 0, 0)]),
        ]
        texture = m.build_map_color_texture(groups, table, "house", tile_size=16, shade=True)
        self.assertEqual(len(texture.tiles), 6)
        # 图集两维都必须是 2 的幂，且面积足够容纳全部色块
        self.assertEqual(texture.atlas_width & (texture.atlas_width - 1), 0)
        self.assertEqual(texture.atlas_height & (texture.atlas_height - 1), 0)
        self.assertGreaterEqual(
            texture.atlas_width * texture.atlas_height,
            len(texture.tiles) * texture.tile_size * texture.tile_size,
        )
        self.assertGreaterEqual(texture.atlas_width, 32)
        faces = texture.bone_faces["minecraft:stone"]
        self.assertEqual(set(faces), set(m.UV_FACE_ORDER))
        for face, (u, v) in faces.items():
            self.assertLessEqual(u + texture.tile_size, texture.atlas_width)
            self.assertLessEqual(v + texture.tile_size, texture.atlas_height)
        tile_by_uv = {(u, v): rgb for u, v, rgb in texture.tiles}
        up_rgb = tile_by_uv[faces["up"]]
        down_rgb = tile_by_uv[faces["down"]]
        self.assertGreater(sum(up_rgb), sum(down_rgb))

    def test_flat_no_shade_single_tile_per_bone(self):
        table = self._small_table()
        groups = [((0, m.BlockRef("minecraft:stone")), [(0, 0, 0)])]
        texture = m.build_map_color_texture(groups, table, "flat", tile_size=16, shade=False)
        self.assertEqual(len(texture.tiles), 1)
        for face, (u, v) in texture.bone_faces["minecraft:stone"].items():
            self.assertEqual((u, v), (0, 0))

    def test_png_writer_produces_valid_png(self):
        table = self._small_table()
        groups = [((0, m.BlockRef("minecraft:stone")), [(0, 0, 0)])]
        texture = m.build_map_color_texture(groups, table, "stone", tile_size=8, shade=True)
        with tempfile.TemporaryDirectory() as tmp:
            png_path = os.path.join(tmp, "stone.png")
            m.write_png(png_path, texture)
            with open(png_path, "rb") as fileobj:
                raw = fileobj.read()
        self.assertTrue(raw.startswith(b"\x89PNG\r\n\x1a\n"))
        width, height = struct.unpack(">II", raw[16:24])
        self.assertEqual((width, height), (texture.atlas_width, texture.atlas_height))
        idat = raw.find(b"IDAT")
        length = struct.unpack(">I", raw[idat - 4 : idat])[0]
        pixels = zlib.decompress(raw[idat + 4 : idat + 4 + length])
        # first scanline: filter byte 0, then RGB of the bright tile at (0,0)
        self.assertEqual(pixels[0], 0)
        self.assertEqual(tuple(pixels[1:4]), (0x7F, 0x7F, 0x7F))

    def test_invalid_tile_size_rejected(self):
        groups = [((0, m.BlockRef("minecraft:stone")), [(0, 0, 0)])]
        with self.assertRaises(m.ConverterError):
            m.build_map_color_texture(groups, self._small_table(), "bad", tile_size=12)

    def test_block_level_color_override(self):
        table = {
            "colors": {},
            "state_overrides": {"color": {"red": "#993333"}},
            "block_overrides": {
                "minecraft:stained_hardened_clay": {"color": {"red": "#8e3c2e"}}
            },
        }
        rgb, found = m.map_color_for(
            m.BlockRef(
                "minecraft:stained_hardened_clay",
                (("color", m.BlockState("string", "red")),),
            ),
            table,
        )
        self.assertTrue(found)
        self.assertEqual(rgb, (0x8E, 0x3C, 0x2E))
        # 羊毛等仍走通用覆盖
        rgb, found = m.map_color_for(
            m.BlockRef("minecraft:wool", (("color", m.BlockState("string", "red")),)),
            table,
        )
        self.assertTrue(found)
        self.assertEqual(rgb, (0x99, 0x33, 0x33))

    def test_variant_material_fallback(self):
        table = self._small_table()
        table["colors"]["minecraft:sandstone"] = "#f7e9a3"
        table["colors"]["minecraft:mossy_stonebrick"] = "#707070"
        table["colors"]["minecraft:iron_block"] = "#a7a7a7"
        table["state_overrides"]["wood_type"] = {"spruce": "#815631"}
        cases = [
            ("minecraft:spruce_stairs", (0x81, 0x56, 0x31)),
            ("minecraft:spruce_slab", (0x81, 0x56, 0x31)),
            ("minecraft:sandstone_slab", (0xF7, 0xE9, 0xA3)),
            ("minecraft:sandstone_wall", (0xF7, 0xE9, 0xA3)),
            ("minecraft:mossy_stonebrick_wall", (0x70, 0x70, 0x70)),
            ("minecraft:iron_door", (0xA7, 0xA7, 0xA7)),
            ("minecraft:iron_trapdoor", (0xA7, 0xA7, 0xA7)),
            ("minecraft:wooden_pressure_plate", (0x8F, 0x77, 0x48)),
            ("minecraft:unknown_stuff", None),
        ]
        for name, expected in cases:
            rgb, found = m.map_color_for(m.BlockRef(name), table)
            if expected is None:
                self.assertFalse(found)
            else:
                self.assertEqual(rgb, expected)

    def test_cli_map_color_texture_end_to_end(self):
        sample = ROOT / "samples" / "dirt_house.mcstructure"
        if not sample.exists():
            self.skipTest("sample not present")
        with tempfile.TemporaryDirectory() as tmp:
            geo_path = os.path.join(tmp, "dirt_house.geo.json")
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "mc_geo_converter.py"),
                    "to-geo",
                    str(sample),
                    "-o",
                    geo_path,
                    "--map-color-texture",
                ],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            png_path = geo_path[: -len(".geo.json")] + ".png"
            self.assertTrue(os.path.exists(png_path))
            geometry = m.load_geometry(geo_path)[0]
            description = geometry["description"]
            self.assertEqual(description["textures"], ["dirt_house"])
            with open(png_path, "rb") as fileobj:
                png = fileobj.read()
            png_width, png_height = struct.unpack(">II", png[16:24])
            self.assertEqual(description["texture_width"], png_width)
            self.assertEqual(description["texture_height"], png_height)
            for bone in geometry["bones"]:
                for cube in bone["cubes"]:
                    uv = cube["uv"]
                    self.assertIsInstance(uv, dict)
                    self.assertEqual(set(uv), set(m.UV_FACE_ORDER))
            # 带贴图的几何仍可无损转回结构
            original = m.parse_mcstructure(str(sample))
            back = m.geometry_to_structure(
                geometry,
                m.parse_block_ref("minecraft:stone"),
                block_version=original.palette[0].version,
            )
            self.assertEqual(non_air_occupancy(back), non_air_occupancy(original))


if __name__ == "__main__":
    unittest.main(verbosity=2)
