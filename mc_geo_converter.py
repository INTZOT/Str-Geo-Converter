#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mc_geo_converter.py — Minecraft Bedrock `.geo.json` <-> `.mcstructure` 转换工具

A bidirectional converter between:
  * Bedrock geometry model files (``.geo.json``) used by resource packs, and
  * Bedrock structure files (``.mcstructure``) saved by structure blocks.

The converter is intentionally data-driven and transparent:

  mcstructure -> geo.json
      Each non-air block in the primary block layer becomes a 1x1x1 cube.
      Cubes are grouped into bones whose name encodes the block identifier
      and its block states, e.g. ``minecraft:log[axis=y]``.  The secondary
      block layer (waterlogged blocks, etc.) is optionally converted using a
      ``secondary:`` bone-name prefix.

  geo.json -> mcstructure
      Each bone becomes a block type.  The bone name may be a plain block id
      (``minecraft:stone``) or a block id with states
      (``minecraft:planks[wood_type=oak]``).  Cubes are voxelised onto the
      integer block grid, the structure is shifted so its minimum corner is
      at (0, 0, 0), and empty cells are filled with air in the primary layer
      and structure voids (-1) in the secondary layer.

Only the Python standard library and ``nbtlib`` are required.
"""

from __future__ import annotations

import argparse
import io
import json
import math
import os
import re
import sys
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

# Make Chinese CLI output readable on Windows consoles / pipes (avoids GBK mojibake).
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except OSError:  # pragma: no cover - stream may not support reconfigure
            pass

try:
    import nbtlib
except ImportError:  # pragma: no cover - only hit when dependency is missing
    print(
        "缺少依赖 nbtlib。请先运行:  pip install -r requirements.txt",
        file=sys.stderr,
    )
    raise SystemExit(2)

__version__ = "1.1.0"

DEFAULT_BLOCK_VERSION = 17959425  # compatibility version used by recent Bedrock builds
DEFAULT_FALLBACK_BLOCK = "minecraft:stone"
DEFAULT_GEOMETRY_FORMAT_VERSION = "1.16.0"
MAX_STRUCTURE_SIZE = (64, 384, 64)  # soft warning limit only; files may still load
MAX_CUBE_CELLS = 5_000_000  # safety valve against accidental gigantic cube expansion

_AXIS_NAMES = ("x", "y", "z")
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_.-]+:[A-Za-z0-9_./-]+$")
_UNNAMESPACED_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_STATE_KEY_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_INT_RE = re.compile(r"^[+-]?\d+$")


class ConverterError(Exception):
    """A conversion problem that should be reported to the user."""


# ---------------------------------------------------------------------------
# Block references (block id + states) and bone-name encoding
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BlockState:
    """One block state, remembering its NBT type so it survives a round-trip."""

    kind: str  # "string" | "int" | "byte" | "short" | "long"
    value: Any


@dataclass(frozen=True)
class BlockRef:
    """An immutable block reference: identifier plus ordered states."""

    name: str
    states: Tuple[Tuple[str, BlockState], ...] = ()
    version: int = DEFAULT_BLOCK_VERSION


@dataclass(frozen=True)
class BoneBlock:
    """A block reference together with the structure block layer it belongs to."""

    ref: BlockRef
    layer: int = 0  # 0 = primary, 1 = secondary


def normalise_block_name(name: str) -> str:
    """Return ``name`` unchanged, adding the ``minecraft:`` namespace if missing."""
    name = name.strip()
    if ":" not in name:
        return "minecraft:" + name
    return name


def _escape_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _unescape_string(value: str) -> str:
    out: List[str] = []
    escape = False
    for ch in value:
        if escape:
            out.append(ch)
            escape = False
        elif ch == "\\":
            escape = True
        else:
            out.append(ch)
    if escape:
        out.append("\\")
    return "".join(out)


def _state_kind_to_tag_class(kind: str):
    if kind == "string":
        return nbtlib.String
    if kind == "int":
        return nbtlib.Int
    if kind == "byte":
        return nbtlib.Byte
    if kind == "short":
        return nbtlib.Short
    if kind == "long":
        return nbtlib.Long
    raise ConverterError(f"不支持的方块状态类型: {kind}")


def tag_to_state(tag: Any) -> BlockState:
    """Convert an nbtlib tag into a :class:`BlockState`."""
    if isinstance(tag, nbtlib.String):
        return BlockState("string", str(tag))
    if isinstance(tag, nbtlib.Int):
        return BlockState("int", int(tag))
    if isinstance(tag, nbtlib.Byte):
        return BlockState("byte", int(tag))
    if isinstance(tag, nbtlib.Short):
        return BlockState("short", int(tag))
    if isinstance(tag, nbtlib.Long):
        return BlockState("long", int(tag))
    raise ConverterError(f"不支持的方块状态 NBT 类型: {type(tag).__name__}")


def state_to_tag(state: BlockState) -> Any:
    return _state_kind_to_tag_class(state.kind)(state.value)


def parse_state_value(raw: str) -> Optional[BlockState]:
    """Parse one ``key=value`` token used in bone names."""
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == '"' and raw[-1] == '"':
        return BlockState("string", _unescape_string(raw[1:-1]))

    for suffix, kind in (("b", "byte"), ("s", "short"), ("L", "long")):
        if raw.endswith(suffix) and _INT_RE.match(raw[:-1]):
            return BlockState(kind, int(raw[:-1]))

    if _INT_RE.match(raw):
        return BlockState("int", int(raw))

    lowered = raw.lower()
    if lowered in ("true", "false"):
        return BlockState("byte", 1 if lowered == "true" else 0)

    if raw:
        return BlockState("string", raw)
    return None


def _split_state_list(text: str) -> List[str]:
    """Split ``key=value,key=value`` on commas outside of double quotes."""
    parts: List[str] = []
    current: List[str] = []
    in_string = False
    escape = False
    for ch in text:
        if escape:
            current.append(ch)
            escape = False
        elif ch == "\\" and in_string:
            current.append(ch)
            escape = True
        elif ch == '"':
            current.append(ch)
            in_string = not in_string
        elif ch == "," and not in_string:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
    if in_string:
        return []
    parts.append("".join(current))
    return parts


def parse_block_ref(text: str, auto_namespace: bool = True) -> Optional[BlockRef]:
    """
    Parse ``minecraft:planks[wood_type=oak,open_bit=0b]`` into a BlockRef.

    When ``auto_namespace`` is true (the default), ``stone`` is accepted as a
    shorthand for ``minecraft:stone``.  Bone-name parsing disables this
    shorthand so that ordinary bone names such as ``leg`` do not silently
    become block ids.

    Returns ``None`` when the text is not a recognisable block reference.
    """
    text = text.strip()
    states: List[Tuple[str, BlockState]] = []

    bracket = text.find("[")
    if bracket != -1:
        if not text.endswith("]"):
            return None
        identifier = text[:bracket].strip()
        inside = text[bracket + 1 : -1].strip()
        if inside:
            for token in _split_state_list(inside):
                token = token.strip()
                if "=" not in token:
                    return None
                key, value = token.split("=", 1)
                key = key.strip()
                if not _STATE_KEY_RE.match(key):
                    return None
                state = parse_state_value(value)
                if state is None:
                    return None
                states.append((key, state))
    else:
        identifier = text

    if not identifier:
        return None
    if ":" not in identifier:
        if not auto_namespace:
            return None
        identifier = "minecraft:" + identifier
    if not _IDENTIFIER_RE.match(identifier):
        return None
    return BlockRef(identifier, tuple(states))


def parse_bone_name(bone_name: str) -> Optional[BoneBlock]:
    """Parse a geometry bone name produced by (or compatible with) this tool."""
    text = bone_name.strip()
    layer = 0
    if text.startswith("secondary:"):
        layer = 1
        text = text[len("secondary:") :].strip()
    ref = parse_block_ref(text, auto_namespace=False)
    if ref is None:
        return None
    return BoneBlock(ref, layer)


def encode_block_ref(ref: BlockRef, layer: int = 0) -> str:
    """Encode a block reference as a geometry bone name."""
    parts: List[str] = []
    for key, state in ref.states:
        if state.kind == "string":
            parts.append(f'{key}="{_escape_string(str(state.value))}"')
        elif state.kind == "int":
            parts.append(f"{key}={int(state.value)}")
        elif state.kind == "byte":
            parts.append(f"{key}={int(state.value)}b")
        elif state.kind == "short":
            parts.append(f"{key}={int(state.value)}s")
        elif state.kind == "long":
            parts.append(f"{key}={int(state.value)}L")
        else:  # pragma: no cover - defensive
            raise ConverterError(f"不支持的方块状态类型: {state.kind}")

    name = ref.name
    if parts:
        name += "[" + ",".join(parts) + "]"
    if layer == 1:
        name = "secondary:" + name
    return name


# ---------------------------------------------------------------------------
# .mcstructure reading / writing
# ---------------------------------------------------------------------------

@dataclass
class StructureData:
    """A parsed .mcstructure file, reduced to the data relevant for geometry."""

    size: Tuple[int, int, int]
    primary: Dict[Tuple[int, int, int], int]      # pos -> palette index
    secondary: Dict[Tuple[int, int, int], int]    # pos -> palette index
    palette: List[BlockRef]
    world_origin: Tuple[int, int, int] = (0, 0, 0)
    format_version: int = 1
    entity_count: int = 0
    block_position_data_count: int = 0
    warnings: List[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.warnings is None:
            self.warnings = []


def xyz_to_index(x: int, y: int, z: int, size: Tuple[int, int, int]) -> int:
    """Flat block index in Bedrock ZYX order."""
    sx, sy, sz = size
    return (sz * sy) * x + sz * y + z


def index_to_xyz(index: int, size: Tuple[int, int, int]) -> Tuple[int, int, int]:
    """Inverse of :func:`xyz_to_index`."""
    sx, sy, sz = size
    if index < 0 or index >= sx * sy * sz:
        raise ConverterError(f"方块索引 {index} 超出结构范围 {size}")
    x, remainder = divmod(index, sz * sy)
    y, z = divmod(remainder, sz)
    return x, y, z


def _as_int_list(tag: Any, field_name: str, length: Optional[int] = None) -> List[int]:
    try:
        values = [int(item) for item in tag]
    except (TypeError, ValueError):
        raise ConverterError(f"NBT 字段 '{field_name}' 不是整数列表")
    if length is not None and len(values) != length:
        raise ConverterError(f"NBT 字段 '{field_name}' 应有 {length} 个元素，实际 {len(values)} 个")
    return values


def _tag_dict(tag: Any) -> Dict[str, Any]:
    if not hasattr(tag, "items"):
        return {}
    return {str(key): value for key, value in tag.items()}


def parse_mcstructure(path: str) -> StructureData:
    """Read an uncompressed little-endian Bedrock .mcstructure file."""
    with open(path, "rb") as fileobj:
        data = fileobj.read()
    if not data:
        raise ConverterError(f"结构文件为空: {path}")
    try:
        root = nbtlib.File.parse(io.BytesIO(data), byteorder="little")
    except Exception as exc:
        raise ConverterError(f"无法解析结构文件 {path}（应为小端 NBT）: {exc}")

    warnings: List[str] = []
    try:
        format_version = int(root["format_version"])
    except (KeyError, TypeError, ValueError):
        raise ConverterError("结构 NBT 缺少有效的 'format_version' 字段")

    try:
        size_list = _as_int_list(root["size"], "size", length=3)
    except (KeyError, TypeError):
        raise ConverterError("结构 NBT 缺少有效的 'size' 字段")
    if any(v <= 0 for v in size_list):
        raise ConverterError(f"结构尺寸无效: {size_list}")
    size = (size_list[0], size_list[1], size_list[2])
    total = size[0] * size[1] * size[2]

    try:
        structure = root["structure"]
        indices_tag = structure["block_indices"]
        palette_tag = structure["palette"]["default"]["block_palette"]
    except (KeyError, TypeError):
        raise ConverterError("结构 NBT 缺少 'structure/block_indices' 或 'palette/default/block_palette'")

    if not isinstance(indices_tag, list) or len(indices_tag) != 2:
        raise ConverterError("'block_indices' 必须恰好包含两个子列表（主层与副层）")

    primary_list: List[int] = []
    secondary_list: List[int] = []
    for layer_index, layer_tag in enumerate(indices_tag):
        if not isinstance(layer_tag, list):
            raise ConverterError(f"'block_indices[{layer_index}]' 不是列表")
        try:
            values = [int(item) for item in layer_tag]
        except (TypeError, ValueError):
            raise ConverterError(f"'block_indices[{layer_index}]' 包含非整数元素")
        if len(values) != total:
            warnings.append(
                f"'block_indices[{layer_index}]' 长度 {len(values)} 与 size 乘积 {total} 不一致；"
                "将按 size 截断/填充"
            )
            values = (values + [-1] * total)[:total]
        if layer_index == 0:
            primary_list = values
        else:
            secondary_list = values

    palette: List[BlockRef] = []
    if not isinstance(palette_tag, list):
        raise ConverterError("'block_palette' 不是列表")
    for entry_index, entry in enumerate(palette_tag):
        try:
            name = str(entry["name"])
            states_tag = entry["states"]
            version = int(entry["version"])
        except (KeyError, TypeError, ValueError):
            raise ConverterError(f"palette 第 {entry_index} 项缺少 'name'/'states'/'version'")
        states_dict = _tag_dict(states_tag)
        states: List[Tuple[str, BlockState]] = []
        for key, value in states_dict.items():
            states.append((key, tag_to_state(value)))
        palette.append(BlockRef(normalise_block_name(name), tuple(states), version))

    def _collect(layer_values: List[int]) -> Dict[Tuple[int, int, int], int]:
        result: Dict[Tuple[int, int, int], int] = {}
        for flat_index, palette_index in enumerate(layer_values):
            if palette_index < 0:
                continue  # structure void
            if palette_index >= len(palette):
                warnings.append(
                    f"方块索引 {palette_index} 超出 palette 范围（共 {len(palette)} 项），已忽略"
                )
                continue
            result[index_to_xyz(flat_index, size)] = palette_index
        return result

    primary = _collect(primary_list)
    secondary = _collect(secondary_list)

    origin_list = _as_int_list(root.get("structure_world_origin", [0, 0, 0]), "structure_world_origin")
    world_origin = (origin_list[0], origin_list[1], origin_list[2]) if len(origin_list) >= 3 else (0, 0, 0)

    entity_count = 0
    block_position_data_count = 0
    try:
        entity_count = len(structure.get("entities", []))
    except TypeError:
        warnings.append("'structure/entities' 不是列表，已忽略")
    try:
        block_position_data_count = len(_tag_dict(structure["palette"]["default"].get("block_position_data", {})))
    except (KeyError, TypeError):
        pass

    return StructureData(
        size=size,
        primary=primary,
        secondary=secondary,
        palette=palette,
        world_origin=world_origin,
        format_version=format_version,
        entity_count=entity_count,
        block_position_data_count=block_position_data_count,
        warnings=warnings,
    )


def _palette_entry_to_nbt(ref: BlockRef) -> nbtlib.Compound:
    states = nbtlib.Compound()
    for key, state in ref.states:
        states[key] = state_to_tag(state)
    return nbtlib.Compound(
        {
            "name": nbtlib.String(ref.name),
            "states": states,
            "version": nbtlib.Int(ref.version),
        }
    )


def build_structure_nbt(
    size: Tuple[int, int, int],
    primary: Iterable[int],
    secondary: Iterable[int],
    palette: Sequence[BlockRef],
    world_origin: Tuple[int, int, int] = (0, 0, 0),
    format_version: int = 1,
) -> nbtlib.File:
    """Build the NBT root object for a Bedrock .mcstructure file."""
    palette_entries = nbtlib.List[nbtlib.Compound]([_palette_entry_to_nbt(ref) for ref in palette])
    default_palette = nbtlib.Compound(
        {
            "block_palette": palette_entries,
            "block_position_data": nbtlib.Compound(),
        }
    )
    structure = nbtlib.Compound(
        {
            "block_indices": nbtlib.List[nbtlib.List[nbtlib.Int]](
                [
                    nbtlib.List[nbtlib.Int](list(primary)),
                    nbtlib.List[nbtlib.Int](list(secondary)),
                ]
            ),
            "entities": nbtlib.List[nbtlib.Compound]([]),
            "palette": nbtlib.Compound({"default": default_palette}),
        }
    )
    return nbtlib.File(
        {
            "format_version": nbtlib.Int(format_version),
            "size": nbtlib.List[nbtlib.Int](list(size)),
            "structure": structure,
            "structure_world_origin": nbtlib.List[nbtlib.Int](list(world_origin)),
        },
        root_name="",
    )


def write_mcstructure(
    path: str,
    size: Tuple[int, int, int],
    primary: Iterable[int],
    secondary: Iterable[int],
    palette: Sequence[BlockRef],
    world_origin: Tuple[int, int, int] = (0, 0, 0),
    format_version: int = 1,
) -> None:
    """Write a little-endian, uncompressed Bedrock .mcstructure file."""
    root = build_structure_nbt(size, primary, secondary, palette, world_origin, format_version)
    with open(path, "wb") as fileobj:
        root.write(fileobj, byteorder="little")


# ---------------------------------------------------------------------------
# .geo.json reading
# ---------------------------------------------------------------------------

def load_geometry(path: str) -> List[Dict[str, Any]]:
    """Load every geometry object contained in a Bedrock .geo.json file."""
    with open(path, "r", encoding="utf-8-sig") as fileobj:
        data = json.load(fileobj)
    if not isinstance(data, dict):
        raise ConverterError(f"几何 JSON 根节点必须是对象: {path}")

    if "minecraft:geometry" in data:
        geometries = data["minecraft:geometry"]
    elif "bones" in data or "description" in data:
        geometries = [data]
    else:
        raise ConverterError(
            f"找不到 'minecraft:geometry' 数组，文件也不是单个几何对象: {path}"
        )

    if isinstance(geometries, dict):
        geometries = [geometries]
    if not isinstance(geometries, list):
        raise ConverterError("'minecraft:geometry' 必须是数组")

    result: List[Dict[str, Any]] = []
    for index, geometry in enumerate(geometries):
        if not isinstance(geometry, dict):
            raise ConverterError(f"minecraft:geometry[{index}] 不是对象")
        description = geometry.get("description")
        if not isinstance(description, dict):
            description = {}
        bones = geometry.get("bones")
        if bones is None:
            bones = []
        if not isinstance(bones, list):
            raise ConverterError(f"几何 {description.get('identifier', index)!r} 的 'bones' 必须是数组")
        result.append(
            {
                "description": description,
                "bones": bones,
                "_index": index,
            }
        )
    if not result:
        raise ConverterError(f"文件中没有任何几何对象: {path}")
    return result


def select_geometry(
    geometries: List[Dict[str, Any]], selector: Optional[str] = None
) -> Dict[str, Any]:
    """Select one geometry by ``--geometry`` (identifier or 1-based index)."""
    if len(geometries) == 1 and selector is None:
        return geometries[0]

    if selector is None:
        identifiers = [
            geometry["description"].get("identifier", f"#{(i + 1)}")
            for i, geometry in enumerate(geometries)
        ]
        raise ConverterError(
            "该文件包含多个几何对象，请用 --geometry 指定。可选：\n  "
            + "\n  ".join(f"#{i + 1}  {identifier}" for i, identifier in enumerate(identifiers))
        )

    if selector.isdigit():
        index = int(selector)
        if 1 <= index <= len(geometries):
            return geometries[index - 1]
        raise ConverterError(f"--geometry 索引超出范围: {selector}")

    matches = [
        geometry
        for geometry in geometries
        if selector.lower() in str(geometry["description"].get("identifier", "")).lower()
    ]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        identifiers = [
            str(geometry["description"].get("identifier", f"#{(i + 1)}"))
            for i, geometry in enumerate(geometries)
        ]
        raise ConverterError(
            f"找不到标识符包含 {selector!r} 的几何对象。可选："
            + ", ".join(identifiers)
        )
    raise ConverterError(f"--geometry {selector!r} 匹配到多个几何对象，请提供更精确的标识符")


# ---------------------------------------------------------------------------
# mcstructure -> geo.json
# ---------------------------------------------------------------------------

def _sanitise_identifier(identifier: str, source_stem: str) -> str:
    """Return a safe Bedrock geometry identifier."""
    identifier = identifier.strip()
    if identifier:
        candidate = identifier
        if ":" not in candidate and not candidate.startswith("geometry."):
            candidate = "geometry." + candidate
    else:
        stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", source_stem).strip("._")
        if not stem or stem[0].isdigit():
            stem = "structure_" + stem
        candidate = "geometry." + stem.lower()

    if re.match(r"^[A-Za-z0-9_.-]+:[A-Za-z0-9_.-]+$", candidate):
        return candidate

    sanitised = re.sub(r"[^A-Za-z0-9_.-]+", "_", candidate.replace(":", "_"))
    if sanitised[0].isdigit():
        sanitised = "geometry_" + sanitised
    return sanitised


def _tidy_number(value: float) -> Any:
    """Return ``int(value)`` when integral, keeping JSON output clean."""
    if float(value).is_integer():
        return int(value)
    return value


def _validate_scale(scale: Any) -> float:
    """Validate a uniform scale factor: finite and greater than zero."""
    try:
        value = float(scale)
    except (TypeError, ValueError):
        raise ConverterError(f"缩放比例必须是数字: {scale!r}")
    if not math.isfinite(value) or value <= 0:
        raise ConverterError(f"缩放比例必须大于 0 且为有限数字: {scale!r}")
    return value


def structure_to_geometry(
    data: StructureData,
    identifier: str = "",
    source_stem: str = "",
    format_version: str = DEFAULT_GEOMETRY_FORMAT_VERSION,
    texture_width: int = 16,
    texture_height: int = 16,
    include_air: bool = False,
    include_secondary: bool = False,
    include_origin: bool = False,
    scale: Any = 1.0,
) -> Dict[str, Any]:
    """
    Convert parsed structure data into a Bedrock geometry JSON object.

    ``scale`` uniformly scales the generated voxel cubes: with scale ``s`` a
    block at ``(x, y, z)`` becomes a cube with ``origin=[x*s, y*s, z*s]`` and
    ``size=[s, s, s]`` (``s=1`` by default).  Pivot and visible bounds are
    scaled as well.
    """
    scale = _validate_scale(scale)

    def scaled(value: float) -> Any:
        return _tidy_number(value * scale)

    sx, sy, sz = data.size
    groups: Dict[Tuple[int, BlockRef], List[Tuple[int, int, int]]] = {}

    layers = [(0, data.primary)]
    if include_secondary:
        layers.append((1, data.secondary))
    elif data.secondary:
        data.warnings.append(
            f"副层中有 {len(data.secondary)} 个方块未转换（如需包含请使用 --include-secondary）"
        )

    for layer_index, layer_cells in layers:
        for position, palette_index in layer_cells.items():
            ref = data.palette[palette_index]
            if ref.name == "minecraft:air" and not include_air:
                continue
            groups.setdefault((layer_index, ref), []).append(position)

    bones: List[Dict[str, Any]] = []
    ordered_groups = sorted(groups.items(), key=lambda item: (item[0][0], encode_block_ref(item[0][1])))
    for (layer_index, ref), positions in ordered_groups:
        positions = sorted(positions, key=lambda p: (p[0], p[1], p[2]))
        min_x = min(p[0] for p in positions)
        min_y = min(p[1] for p in positions)
        min_z = min(p[2] for p in positions)
        max_x = max(p[0] for p in positions)
        max_y = max(p[1] for p in positions)
        max_z = max(p[2] for p in positions)
        bones.append(
            {
                "name": encode_block_ref(ref, layer_index),
                "pivot": [
                    scaled((min_x + max_x) / 2.0),
                    scaled((min_y + max_y) / 2.0),
                    scaled((min_z + max_z) / 2.0),
                ],
                "cubes": [
                    {
                        "origin": [scaled(x), scaled(y), scaled(z)],
                        "size": [scaled(1), scaled(1), scaled(1)],
                        "uv": [0, 0],
                    }
                    for x, y, z in positions
                ],
            }
        )

    description: Dict[str, Any] = {
        "identifier": _sanitise_identifier(identifier, source_stem),
        "texture_width": texture_width,
        "texture_height": texture_height,
        "visible_bounds_width": scaled(max(sx, sz)),
        "visible_bounds_height": scaled(sy),
        "visible_bounds_offset": [0, 0, 0],
    }
    if include_origin:
        description["structure_world_origin"] = list(data.world_origin)
        description["structure_size"] = list(data.size)

    return {
        "format_version": format_version,
        "minecraft:geometry": [{"description": description, "bones": bones}],
    }


# ---------------------------------------------------------------------------
# geo.json -> mcstructure
# ---------------------------------------------------------------------------

def _parse_float_list(value: Any, what: str) -> List[float]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ConverterError(f"{what} 必须是包含 3 个数字的数组")
    try:
        return [float(item) for item in value]
    except (TypeError, ValueError):
        raise ConverterError(f"{what} 必须是包含 3 个数字的数组")


def _cube_cells(
    origin: Sequence[float],
    size: Sequence[float],
    snap: str,
    voxel_size: float = 1.0,
) -> List[Tuple[int, int, int]]:
    """
    Voxelise one cube onto the integer block grid.

    ``voxel_size`` is the geometry length that corresponds to one structure
    block.  ``voxel_size=2`` therefore turns a cube of ``size=[2,2,2]`` at an
    aligned ``origin`` back into a single block cell.
    """
    ranges: List[range] = []
    epsilon = 1e-9
    for o, s in zip(origin, size):
        if s <= 0:
            raise ConverterError(f"cube 的 size 必须大于 0: origin={origin}, size={size}")
        o = o / voxel_size
        s = s / voxel_size
        if snap == "round":
            start = math.floor(o + 0.5)
            count = max(1, math.floor(s + 0.5))
            ranges.append(range(start, start + count))
        elif snap == "floor":
            start = math.floor(o + epsilon)
            end = math.floor(o + s + epsilon)
            if end <= start:
                end = start + 1  # extremely thin slice still occupies one cell
            ranges.append(range(start, end))
        else:
            raise ConverterError(f"未知的 --snap 模式: {snap!r}（可选 floor 或 round）")

    cell_count = len(ranges[0]) * len(ranges[1]) * len(ranges[2])
    if cell_count > MAX_CUBE_CELLS:
        raise ConverterError(
            f"cube 体素化后需要 {cell_count} 个格子，超过安全上限 {MAX_CUBE_CELLS}；"
            "请检查 origin/size 是否误填了过大的数值"
        )

    cells: List[Tuple[int, int, int]] = []
    for x in ranges[0]:
        for y in ranges[1]:
            for z in ranges[2]:
                cells.append((x, y, z))
    return cells


def _apply_inflate(
    origin: List[float], size: List[float], inflate: Any
) -> Tuple[List[float], List[float]]:
    if inflate is None or inflate is False:
        return origin, size
    if isinstance(inflate, (int, float)):
        delta = [float(inflate)] * 3
    elif isinstance(inflate, (list, tuple)) and len(inflate) == 3:
        delta = [float(item) for item in inflate]
    else:
        raise ConverterError(f"cube 的 inflate 字段格式无效: {inflate!r}")
    return (
        [origin[0] - delta[0], origin[1] - delta[1], origin[2] - delta[2]],
        [size[0] + 2 * delta[0], size[1] + 2 * delta[1], size[2] + 2 * delta[2]],
    )


def _is_identity_rotation(value: Any) -> bool:
    if value in (None, False):
        return True
    if isinstance(value, (list, tuple)):
        return all(float(item) == 0.0 for item in value)
    return False


def geometry_to_structure(
    geometry: Dict[str, Any],
    fallback_ref: BlockRef,
    block_version: int = DEFAULT_BLOCK_VERSION,
    world_origin: Tuple[int, int, int] = (0, 0, 0),
    snap: str = "floor",
    voxel_size: Any = 1.0,
) -> StructureData:
    """
    Convert one geometry object into structure data.

    ``voxel_size`` is the geometry length corresponding to one structure
    block.  It is the inverse of the ``to-geo --scale`` factor: geometry
    created with ``--scale 2`` converts back cleanly with ``voxel_size=2``.

    The returned :class:`StructureData` has all cube positions shifted so the
    structure's minimum corner is at (0, 0, 0).
    """
    voxel_size = _validate_scale(voxel_size)
    identifier = geometry["description"].get("identifier", "<unnamed>")
    primary: Dict[Tuple[int, int, int], BlockRef] = {}
    secondary: Dict[Tuple[int, int, int], BlockRef] = {}
    skipped_bones = 0
    rotation_warnings = 0
    overwritten_cells = 0

    for bone_index, bone in enumerate(geometry["bones"]):
        if not isinstance(bone, dict):
            raise ConverterError(f"几何 {identifier!r} 的 bones[{bone_index}] 不是对象")

        bone_name = str(bone.get("name") or "")
        parsed = parse_bone_name(bone_name)
        if parsed is None:
            ref = fallback_ref
            layer = 0
            skipped_bones += 1
        else:
            ref = parsed.ref
            layer = parsed.layer
            ref = BlockRef(ref.name, ref.states, block_version)

        cubes = bone.get("cubes")
        if cubes is None:
            continue
        if not isinstance(cubes, list):
            raise ConverterError(f"骨骼 {bone_name!r} 的 'cubes' 必须是数组")

        for cube_index, cube in enumerate(cubes):
            if not isinstance(cube, dict):
                raise ConverterError(f"骨骼 {bone_name!r} 的 cubes[{cube_index}] 不是对象")
            try:
                origin = _parse_float_list(cube.get("origin"), f"骨骼 {bone_name!r} 的 cube 缺少有效 origin")
                size = _parse_float_list(cube.get("size"), f"骨骼 {bone_name!r} 的 cube 缺少有效 size")
                origin, size = _apply_inflate(origin, size, cube.get("inflate"))
                cells = _cube_cells(origin, size, snap, voxel_size)
            except ConverterError as exc:
                raise ConverterError(f"{identifier} -> {bone_name} cube#{cube_index}: {exc}")

            if not _is_identity_rotation(cube.get("rotation")):
                rotation_warnings += 1
            if cube.get("mirror") not in (None, False):
                rotation_warnings += 1

            target = secondary if layer == 1 else primary
            for cell in cells:
                previous = target.get(cell)
                if previous is not None and previous != ref:
                    overwritten_cells += 1
                target[cell] = ref

    all_positions = list(primary.keys()) + list(secondary.keys())
    if not all_positions:
        min_corner = (0, 0, 0)
        max_corner = (0, 0, 0)
    else:
        min_corner = tuple(min(p[axis] for p in all_positions) for axis in range(3))
        max_corner = tuple(max(p[axis] for p in all_positions) for axis in range(3))
    size = tuple(max_corner[axis] - min_corner[axis] + 1 for axis in range(3))

    shifted_primary = {
        (x - min_corner[0], y - min_corner[1], z - min_corner[2]): ref
        for (x, y, z), ref in primary.items()
    }
    shifted_secondary = {
        (x - min_corner[0], y - min_corner[1], z - min_corner[2]): ref
        for (x, y, z), ref in secondary.items()
    }

    # Palette: air is always index 0, then blocks in first-use order.
    air_ref = BlockRef("minecraft:air", (), block_version)
    palette_map: Dict[BlockRef, int] = {air_ref: 0}
    palette: List[BlockRef] = [air_ref]
    for ref in sorted(shifted_primary.values(), key=encode_block_ref):
        if ref not in palette_map:
            palette_map[ref] = len(palette)
            palette.append(ref)
    for ref in sorted(shifted_secondary.values(), key=encode_block_ref):
        if ref not in palette_map:
            palette_map[ref] = len(palette)
            palette.append(ref)

    total = size[0] * size[1] * size[2]
    primary_indices = [0] * total
    secondary_indices = [-1] * total
    for position, ref in shifted_primary.items():
        primary_indices[xyz_to_index(*position, size)] = palette_map[ref]
    for position, ref in shifted_secondary.items():
        secondary_indices[xyz_to_index(*position, size)] = palette_map[ref]

    warnings: List[str] = []
    if skipped_bones:
        warnings.append(
            f"{skipped_bones} 个骨骼的名称不是方块 ID（已使用默认方块 {fallback_ref.name}）"
        )
    if overwritten_cells:
        warnings.append(f"{overwritten_cells} 个格子被多个 cube 覆盖，后者覆盖前者")
    if rotation_warnings:
        warnings.append(
            f"{rotation_warnings} 个 cube 带有 rotation/mirror；转换忽略旋转并按未旋转位置处理"
        )
    if any(size[axis] > MAX_STRUCTURE_SIZE[axis] for axis in range(3)):
        warnings.append(
            f"结构尺寸 {size} 超过游戏结构方块上限 {MAX_STRUCTURE_SIZE}（外部生成的文件通常仍可加载）"
        )

    return StructureData(
        size=size,
        primary={position: palette_map[ref] for position, ref in shifted_primary.items()},
        secondary={position: palette_map[ref] for position, ref in shifted_secondary.items()},
        palette=palette,
        world_origin=world_origin,
        format_version=1,
        warnings=warnings,
    )


def write_structure_from_geometry(
    geometry: Dict[str, Any],
    output_path: str,
    fallback_ref: BlockRef,
    block_version: int = DEFAULT_BLOCK_VERSION,
    world_origin: Tuple[int, int, int] = (0, 0, 0),
    snap: str = "floor",
    voxel_size: Any = 1.0,
) -> StructureData:
    data = geometry_to_structure(
        geometry,
        fallback_ref=fallback_ref,
        block_version=block_version,
        world_origin=world_origin,
        snap=snap,
        voxel_size=voxel_size,
    )
    total = data.size[0] * data.size[1] * data.size[2]
    primary_indices = [0] * total  # unfilled primary cells are air (palette index 0)
    secondary_indices = [-1] * total  # unfilled secondary cells are structure voids
    for position, palette_index in data.primary.items():
        primary_indices[xyz_to_index(*position, data.size)] = palette_index
    for position, palette_index in data.secondary.items():
        secondary_indices[xyz_to_index(*position, data.size)] = palette_index
    write_mcstructure(
        output_path,
        data.size,
        primary_indices,
        secondary_indices,
        data.palette,
        data.world_origin,
        data.format_version,
    )
    return data


# ---------------------------------------------------------------------------
# Command line interface
# ---------------------------------------------------------------------------

def _parse_origin(text: str) -> Tuple[int, int, int]:
    parts = [item.strip() for item in text.split(",")]
    if len(parts) != 3:
        raise argparse.ArgumentTypeError("格式应为 x,y,z，例如 100,64,-100")
    try:
        return tuple(int(item) for item in parts)  # type: ignore[return-value]
    except ValueError:
        raise argparse.ArgumentTypeError("坐标必须是整数")


def _positive_scale(text: str) -> float:
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError("缩放比例必须是数字，例如 1、2、0.5")
    if not math.isfinite(value) or value <= 0:
        raise argparse.ArgumentTypeError("缩放比例必须大于 0 且为有限数字")
    return value


def _default_output(input_path: str, direction: str) -> str:
    if direction == "to-geo":
        return os.path.splitext(input_path)[0] + ".geo.json"
    if input_path.lower().endswith(".geo.json"):
        return input_path[: -len(".geo.json")] + ".mcstructure"
    return os.path.splitext(input_path)[0] + ".mcstructure"


def _print_warnings(warnings: Sequence[str]) -> None:
    for warning in warnings:
        print(f"  警告: {warning}")


def run_to_geo(args: argparse.Namespace) -> int:
    input_path = args.input
    output_path = args.output or _default_output(input_path, "to-geo")
    if os.path.abspath(input_path) == os.path.abspath(output_path):
        raise ConverterError("输出文件不能覆盖输入文件")

    data = parse_mcstructure(input_path)
    solid_primary = sum(
        1
        for palette_index in data.primary.values()
        if data.palette[palette_index].name != "minecraft:air"
    )
    print(f"已读取: {input_path}")
    print(f"  结构尺寸: {data.size}  世界原点: {data.world_origin}")
    print(
        f"  主层格数: {len(data.primary)}（非空气: {solid_primary}）  "
        f"副层格数: {len(data.secondary)}  palette: {len(data.palette)} 项"
    )
    if data.entity_count:
        print(f"  注意: 结构包含 {data.entity_count} 个实体，几何 JSON 无法表达，已跳过")
    if data.block_position_data_count:
        print(
            f"  注意: 结构包含 {data.block_position_data_count} 项方块实体数据（箱子内容等），"
            "几何 JSON 无法表达，已跳过"
        )
    _print_warnings(data.warnings)

    stem = os.path.splitext(os.path.basename(input_path))[0]
    geometry = structure_to_geometry(
        data,
        identifier=args.identifier,
        source_stem=stem,
        format_version=args.format_version,
        texture_width=args.texture_width,
        texture_height=args.texture_height,
        include_air=args.include_air,
        include_secondary=args.include_secondary,
        include_origin=args.include_origin,
        scale=args.scale,
    )
    with open(output_path, "w", encoding="utf-8") as fileobj:
        json.dump(geometry, fileobj, ensure_ascii=False, indent=2)
        fileobj.write("\n")
    print(f"已写出: {output_path}")
    print(
        f"  几何数量: 1  骨骼数量: {len(geometry['minecraft:geometry'][0]['bones'])}  "
        f"体素大小: {_tidy_number(args.scale)}"
    )
    return 0


def run_to_structure(args: argparse.Namespace) -> int:
    input_path = args.input
    output_path = args.output or _default_output(input_path, "to-structure")
    if os.path.abspath(input_path) == os.path.abspath(output_path):
        raise ConverterError("输出文件不能覆盖输入文件")

    fallback_ref = parse_block_ref(args.block)
    if fallback_ref is None:
        raise ConverterError(f"无效的默认方块 ID: {args.block!r}")

    geometries = load_geometry(input_path)
    geometry = select_geometry(geometries, args.geometry)
    identifier = geometry["description"].get("identifier", "<unnamed>")

    print(f"已读取: {input_path}")
    print(f"  几何标识符: {identifier}  (共 {len(geometries)} 个几何对象)")

    data = write_structure_from_geometry(
        geometry,
        output_path,
        fallback_ref=fallback_ref,
        block_version=args.block_version,
        world_origin=args.world_origin,
        snap=args.snap,
        voxel_size=args.voxel_size,
    )
    print(f"已写出: {output_path}")
    print(f"  结构尺寸: {data.size}  世界原点: {data.world_origin}")
    print(f"  主层方块: {len(data.primary)}  副层方块: {len(data.secondary)}")
    print(f"  palette: {[ref.name for ref in data.palette]}")
    _print_warnings(data.warnings)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mc_geo_converter.py",
        description="Minecraft Bedrock .geo.json 与 .mcstructure 双向转换工具",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", metavar="命令")

    to_geo = subparsers.add_parser(
        "to-geo",
        aliases=["mc2geo"],
        help=".mcstructure -> .geo.json",
        description="把 .mcstructure 方块结构转换为 .geo.json 几何模型",
    )
    to_geo.add_argument("input", help="输入 .mcstructure 文件")
    to_geo.add_argument("-o", "--output", help="输出 .geo.json 文件（默认与输入同名）")
    to_geo.add_argument("--identifier", default="", help="几何标识符，默认 geometry.<文件名>")
    to_geo.add_argument("--format-version", default=DEFAULT_GEOMETRY_FORMAT_VERSION, help="几何 format_version（默认 1.16.0）")
    to_geo.add_argument("--texture-width", type=int, default=16, help="description.texture_width（默认 16）")
    to_geo.add_argument("--texture-height", type=int, default=16, help="description.texture_height（默认 16）")
    to_geo.add_argument("--include-air", action="store_true", help="把空气方块也转换为 cube（不推荐）")
    to_geo.add_argument("--include-secondary", action="store_true", help="转换副层（例如含水方块），骨骼名加 secondary: 前缀")
    to_geo.add_argument("--include-origin", action="store_true", help="把结构世界原点写入 description 附加字段")
    to_geo.add_argument(
        "--scale",
        type=_positive_scale,
        default=1.0,
        metavar="N",
        help="等比缩放生成的几何体：体素 cube 尺寸变为 [N,N,N]，坐标与边界同步缩放（默认 1）",
    )
    to_geo.set_defaults(func=run_to_geo)

    to_structure = subparsers.add_parser(
        "to-structure",
        aliases=["geo2mc"],
        help=".geo.json -> .mcstructure",
        description="把 .geo.json 几何模型转换为 .mcstructure 方块结构",
    )
    to_structure.add_argument("input", help="输入 .geo.json 文件")
    to_structure.add_argument("-o", "--output", help="输出 .mcstructure 文件（默认与输入同名）")
    to_structure.add_argument(
        "--geometry",
        help="当文件包含多个几何对象时，按标识符或序号（1 起）选择",
    )
    to_structure.add_argument(
        "--block",
        default=DEFAULT_FALLBACK_BLOCK,
        help="无法从骨骼名识别方块 ID 时使用的默认方块（默认 minecraft:stone）",
    )
    to_structure.add_argument("--block-version", type=int, default=DEFAULT_BLOCK_VERSION, help="palette 兼容版本号（默认 17959425）")
    to_structure.add_argument("--world-origin", type=_parse_origin, default=(0, 0, 0), help="structure_world_origin，格式 x,y,z")
    to_structure.add_argument(
        "--snap",
        choices=("floor", "round"),
        default="floor",
        help="非整数 cube 坐标的体素化方式：floor=下取整（默认），round=四舍五入",
    )
    to_structure.add_argument(
        "--voxel-size",
        type=_positive_scale,
        default=1.0,
        metavar="N",
        help="几何中一个方块对应多少长度单位；若几何由 to-geo --scale N 生成，填 N 可精确还原（默认 1）",
    )
    to_structure.set_defaults(func=run_to_structure)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()

    # Convenience form:  python mc_geo_converter.py INPUT [OUTPUT] [options]
    if argv and argv[0] not in ("to-geo", "mc2geo", "to-structure", "geo2mc", "-h", "--help", "--version"):
        source = argv[0]
        lowered = source.lower()
        if lowered.endswith(".mcstructure"):
            command = "to-geo"
        elif lowered.endswith(".geo.json") or lowered.endswith(".json"):
            command = "to-structure"
        else:
            parser.error("无法从扩展名判断转换方向。请使用 to-geo 或 to-structure 子命令")
        if len(argv) >= 2 and not argv[1].startswith("-"):
            argv = [command, source, "-o", argv[1]] + argv[2:]
        else:
            argv = [command, source] + argv[1:]

    args = parser.parse_args(argv)
    if getattr(args, "command", None) is None:
        parser.print_help()
        return 0
    try:
        return args.func(args)
    except ConverterError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1
    except (OSError, json.JSONDecodeError) as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
