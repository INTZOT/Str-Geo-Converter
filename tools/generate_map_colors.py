#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成项目内置的 data/map_colors.json（方块地图色表）。

用法:
    python tools/generate_map_colors.py --from-ref <mapcolors.html> [-o data/map_colors.json]
    python tools/generate_map_colors.py --from-wiki <map_item_format.html> [-o data/map_colors.json]
    python tools/generate_map_colors.py --from-blocks <vanilla blocks.json> [-o data/map_colors.json]

数据来源（三选一）:
    1. 基岩版地图基色表（推荐，逐方块 ID 855 条，含 per-color 变体）:
       https://comeixalpha.github.io/ref/mapcolors/  （Colorify Docs）
    2. Minecraft Wiki "Map item format" 页面（Java 源，64 基础色）:
       https://minecraft.wiki/w/Map_item_format
    3. 基岩版 vanilla 行为包的 blocks.json（每个方块条目含 map_color 字段）。

生成的表包含:
    - colors:            方块 ID -> 十六进制颜色（基表）
    - state_overrides:   state 覆盖表（如羊毛/陶瓦/玻璃的 ``color`` state、
                        木头的 ``wood_type`` state），因为基岩版把同色系方块
                        归为单个 ID + state，blocks.json 本身无法区分。
    - block_overrides:   特定方块的 state 覆盖（如陶瓦的 16 色与羊毛不同）。

生成的 JSON 与 mc_geo_converter.py 中 map_color_for() 的读取逻辑对应。
"""

import argparse
import datetime
import html
import json
import os
import re
import sys
from typing import Dict, List, Optional, Sequence, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import mc_geo_converter as m  # noqa: E402  (复用 DYE_COLORS / WOOD_COLORS)

_HEX_RE = re.compile(r"^#?[0-9a-fA-F]{6}$")

# Java(wiki) 名称 -> 基岩版方块 ID（常见重命名；未列出的按同名映射）
_JAVA_TO_BEDROCK = {
    "grass_block": "grass_block",
    "dirt_path": "grass_path",
    "snow_block": "snow",
    "stone_bricks": "stonebrick",
    "mossy_stone_bricks": "mossy_stonebrick",
    "cracked_stone_bricks": "cracked_stonebrick",
    "chiseled_stone_bricks": "chiseled_stonebrick",
    "nether_bricks": "nether_brick",
    "red_nether_bricks": "red_nether_brick",
    "end_stone_bricks": "end_bricks",
    "bricks": "brick_block",
    "brick": "brick_block",
    "terracotta": "hardened_clay",
    "stained_hardened_clay": "stained_hardened_clay",
    "concrete_powder": "concrete_powder",
    "lily_pad": "waterlily",
    "sea_lantern": "seaLantern",
    "sugar_cane": "reeds",
    "sugar_cane_block": "reeds",
    "hay_bale": "hay_block",
    "mob_spawner": "mob_spawner",
    "note_block": "noteblock",
    "magma_block": "magma",
    "slime_block": "slime",
    "oak_leaves": "leaves",
    "oak_sapling": "sapling",
    "oak_log": "log",
    "oak_wood": "wood",
    "oak_planks": "planks",
    "oak_fence": "fence",
    "oak_fence_gate": "fence_gate",
    "stripped_oak_log": "stripped_oak_log",
    "stripped_oak_wood": "stripped_oak_wood",
    "dandelion": "yellow_flower",
    "poppy": "red_flower",
    "blue_orchid": "red_flower",
    "allium": "red_flower",
    "azure_bluet": "red_flower",
    "red_tulip": "red_flower",
    "orange_tulip": "red_flower",
    "white_tulip": "red_flower",
    "pink_tulip": "red_flower",
    "oxeye_daisy": "red_flower",
    "cornflower": "red_flower",
    "lily_of_the_valley": "red_flower",
    "wither_rose": "wither_rose",
    "dead_bush": "deadbush",
    "vine": "vine",
    "glow_lichen": "glow_lichen",
    "iron_bars": "iron_bars",
    "iron_chain": "chain",
    "end_stone": "end_stone",
    "nether_quartz_ore": "quartz_ore",
    "quartz_block": "quartz_block",
    "smooth_quartz": "quartz_block",
    "chiseled_quartz_block": "chiseled_quartz_block",
    "quartz_pillar": "quartz_pillar",
    "purpur_block": "purpur_block",
    "purpur_pillar": "purpur_pillar",
    "prismarine_bricks": "prismarine_bricks",
    "dark_prismarine": "dark_prismarine",
    "sea_pickle": "sea_pickle",
    "turtle_egg": "turtle_egg",
    "dried_kelp_block": "dried_kelp_block",
    "scaffolding": "scaffolding",
    "red_sand": "red_sand",
    "red_sandstone": "red_sandstone",
    "smooth_red_sandstone": "red_sandstone",
    "chiseled_red_sandstone": "chiseled_red_sandstone",
    "cut_red_sandstone": "cut_red_sandstone",
    "smooth_sandstone": "sandstone",
    "chiseled_sandstone": "chiseled_sandstone",
    "cut_sandstone": "cut_sandstone",
    "cobblestone": "cobblestone",
    "mossy_cobblestone": "mossy_cobblestone",
    "netherrack": "netherrack",
    "crying_obsidian": "crying_obsidian",
    "soul_sand": "soul_sand",
    "soul_soil": "soul_soil",
    "basalt": "basalt",
    "polished_basalt": "polished_basalt",
    "smooth_basalt": "smooth_basalt",
    "blackstone": "blackstone",
    "polished_blackstone": "polished_blackstone",
    "polished_blackstone_bricks": "polished_blackstone_bricks",
    "chiseled_polished_blackstone": "chiseled_polished_blackstone",
    "gilded_blackstone": "gilded_blackstone",
    "gold_block": "gold_block",
    "iron_block": "iron_block",
    "diamond_block": "diamond_block",
    "emerald_block": "emerald_block",
    "redstone_block": "redstone_block",
    "lapis_block": "lapis_block",
    "lapis_lazuli_block": "lapis_block",
    "coal_block": "coal_block",
    "netherite_block": "netherite_block",
    "copper_block": "copper_block",
    "exposed_copper": "exposed_copper",
    "weathered_copper": "weathered_copper",
    "oxidized_copper": "oxidized_copper",
    "cut_copper": "cut_copper",
    "raw_iron_block": "raw_iron_block",
    "raw_gold_block": "raw_gold_block",
    "raw_copper_block": "raw_copper_block",
    "amethyst_block": "amethyst_block",
    "budding_amethyst": "budding_amethyst",
    "calcite": "calcite",
    "tuff": "tuff",
    "deepslate": "deepslate",
    "cobbled_deepslate": "cobbled_deepslate",
    "polished_deepslate": "polished_deepslate",
    "deepslate_bricks": "deepslate_bricks",
    "deepslate_tiles": "deepslate_tiles",
    "chiseled_deepslate": "chiseled_deepslate",
    "reinforced_deepslate": "reinforced_deepslate",
    "dripstone_block": "dripstone_block",
    "pointed_dripstone": "pointed_dripstone",
    "mud": "mud",
    "packed_mud": "packed_mud",
    "mud_bricks": "mud_bricks",
    "mangrove_roots": "mangrove_roots",
    "muddy_mangrove_roots": "muddy_mangrove_roots",
    "sculk": "sculk",
    "sculk_vein": "sculk_vein",
    "sculk_catalyst": "sculk_catalyst",
    "sculk_sensor": "sculk_sensor",
    "sculk_shrieker": "sculk_shrieker",
    "glowstone": "glowstone",
    "shroomlight": "shroomlight",
    "ochre_froglight": "ochre_froglight",
    "verdant_froglight": "verdant_froglight",
    "pearlescent_froglight": "pearlescent_froglight",
    "target": "target",
    "lodestone": "lodestone",
    "respawn_anchor": "respawn_anchor",
    "beehive": "beehive",
    "bee_nest": "bee_nest",
    "honey_block": "honey_block",
    "honeycomb_block": "honeycomb_block",
    "enchanting_table": "enchanting_table",
    "brewing_stand": "brewing_stand",
    "cauldron": "cauldron",
    "blast_furnace": "blast_furnace",
    "smoker": "smoker",
    "cartography_table": "cartography_table",
    "fletching_table": "fletching_table",
    "smithing_table": "smithing_table",
    "loom": "loom",
    "lectern": "lectern",
    "grindstone": "grindstone",
    "stonecutter": "stonecutter",
    "composter": "composter",
    "bell": "bell",
    "lantern": "lantern",
    "soul_lantern": "soul_lantern",
    "campfire": "campfire",
    "soul_campfire": "soul_campfire",
    "ender_chest": "ender_chest",
    "trapped_chest": "trapped_chest",
    "crafting_table": "crafting_table",
    "furnace": "furnace",
    "jukebox": "jukebox",
    "barrel": "barrel",
    "bookshelf": "bookshelf",
    "anvil": "anvil",
    "hopper": "hopper",
    "beacon": "beacon",
    "conduit": "conduit",
    "end_portal_frame": "end_portal_frame",
    "dragon_egg": "dragon_egg",
    "piston": "piston",
    "sticky_piston": "sticky_piston",
    "observer": "observer",
    "dispenser": "dispenser",
    "dropper": "dropper",
    "redstone_lamp": "redstone_lamp",
    "redstone_wire": "redstone_wire",
    "lever": "lever",
    "tripwire_hook": "tripwire_hook",
    "sponge": "sponge",
    "wet_sponge": "wet_sponge",
    "cactus": "cactus",
    "bamboo": "bamboo",
    "kelp": "kelp",
    "tnt": "tnt",
    "torch": "torch",
    "soul_torch": "soul_torch",
    "jack_o_lantern": "lit_pumpkin",
    "carved_pumpkin": "carved_pumpkin",
    "pumpkin": "pumpkin",
    "melon": "melon_block",
    "mycelium": "mycelium",
    "podzol": "podzol",
    "farmland": "farmland",
    "clay": "clay",
    "packed_ice": "packed_ice",
    "blue_ice": "blue_ice",
    "frosted_ice": "frosted_ice",
    "snow": "snow",
    "snow_block": "snow",
    "ice": "ice",
    "water": "water",
    "lava": "lava",
    "moss_block": "moss_block",
    "moss_carpet": "moss_carpet",
    "cobweb": "cobweb",
    "bedrock": "bedrock",
    "obsidian": "obsidian",
    "barrier": "barrier",
    "light": "light_block",
    "structure_block": "structure_block",
    "structure_void": "structure_void",
    "jigsaw": "jigsaw",
    "command_block": "command_block",
    "chain_command_block": "chain_command_block",
    "repeating_command_block": "repeating_command_block",
    "bone_block": "bone_block",
    "coal_ore": "coal_ore",
    "iron_ore": "iron_ore",
    "copper_ore": "copper_ore",
    "gold_ore": "gold_ore",
    "redstone_ore": "redstone_ore",
    "emerald_ore": "emerald_ore",
    "lapis_ore": "lapis_ore",
    "diamond_ore": "diamond_ore",
    "deepslate_coal_ore": "deepslate_coal_ore",
    "deepslate_iron_ore": "deepslate_iron_ore",
    "deepslate_copper_ore": "deepslate_copper_ore",
    "deepslate_gold_ore": "deepslate_gold_ore",
    "deepslate_redstone_ore": "deepslate_redstone_ore",
    "deepslate_emerald_ore": "deepslate_emerald_ore",
    "deepslate_lapis_ore": "deepslate_lapis_ore",
    "deepslate_diamond_ore": "deepslate_diamond_ore",
    "nether_gold_ore": "nether_gold_ore",
    "nether_quartz_ore": "quartz_ore",
    "ancient_debris": "ancient_debris",
    "crimson_stem": "crimson_stem",
    "warped_stem": "warped_stem",
    "stripped_crimson_stem": "stripped_crimson_stem",
    "stripped_warped_stem": "stripped_warped_stem",
    "nether_wart_block": "nether_wart_block",
    "warped_wart_block": "warped_wart_block",
    "waxed_copper": "waxed_copper",
    "waxed_cut_copper": "waxed_cut_copper",
    "waxed_exposed_copper": "waxed_exposed_copper",
    "waxed_weathered_copper": "waxed_weathered_copper",
    "waxed_oxidized_copper": "waxed_oxidized_copper",
    "glow_item_frame": "frame",
    "item_frame": "frame",
    "flower_pot": "flower_pot",
    "ladder": "ladder",
    "rail": "rail",
    "powered_rail": "golden_rail",
    "detector_rail": "detector_rail",
    "activator_rail": "activator_rail",
}

# 仅这些"木材家族"方块的 wood_type state 才应用木材色（树叶等有独立的叶绿色）
_WOOD_FAMILY = {
    "minecraft:planks",
    "minecraft:log",
    "minecraft:wood",
    "minecraft:fence",
    "minecraft:fence_gate",
    "minecraft:stripped_oak_log",
    "minecraft:stripped_spruce_log",
    "minecraft:stripped_birch_log",
    "minecraft:stripped_jungle_log",
    "minecraft:stripped_acacia_log",
    "minecraft:stripped_dark_oak_log",
    "minecraft:stripped_mangrove_log",
    "minecraft:stripped_cherry_log",
    "minecraft:stripped_pale_oak_log",
    "minecraft:stripped_crimson_stem",
    "minecraft:stripped_warped_stem",
}


def rgb_to_hex(rgb: Tuple[int, int, int]) -> str:
    return "#%02x%02x%02x" % tuple(rgb)


def _parse_blocks_json(path: str) -> Tuple[Dict[str, str], List[str]]:
    """blocks.json 模式：提取 {方块ID: #rrggbb} 与缺失 map_color 的条目清单。"""
    with open(path, encoding="utf-8") as fileobj:
        data = json.load(fileobj)
    colors: Dict[str, str] = {}
    skipped: List[str] = []
    for key, entry in data.items():
        if not isinstance(entry, dict):
            skipped.append(key)
            continue
        name = key if ":" in key else "minecraft:" + key
        map_color = entry.get("map_color")
        if isinstance(map_color, str) and _HEX_RE.match(map_color.strip()):
            colors[name] = "#" + map_color.strip().lstrip("#").lower()
        else:
            skipped.append(key)
    return colors, skipped


def _parse_wiki_html(path: str) -> List[Tuple[str, str, List[str]]]:
    """wiki 模式：解析 'Map item format' 页面的 Base colors 表格。

    返回 [(ID名称, "R, G, B", [方块词条...]), ...]，词条已展开
    （'stone (slab, stairs)' -> 'stone slab', 'stone stairs'）。
    """
    with open(path, encoding="utf-8") as fileobj:
        raw = fileobj.read()
    start = raw.find('id="Base_colors"')
    end = raw.find('id="Map_colors"')
    if start < 0 or end < 0:
        raise ValueError("HTML 中未找到 Base colors 表格（请使用 minecraft.wiki/w/Map_item_format 页面）")
    segment = raw[start:end]
    rows: List[Tuple[str, str, List[str]]] = []
    for row in re.findall(r"<tr>(.*?)</tr>", segment, re.S):
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
        if len(cells) < 4:
            continue
        id_text = html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", cells[0]))).strip()
        rgb_text = html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", cells[2]))).strip()
        blocks_text = html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", cells[3]))).strip()
        rows.append((id_text, rgb_text, _split_blocks_text(blocks_text)))
    return rows


def _split_blocks_text(text: str) -> List[str]:
    """把方块清单文本展开为词条列表。

    - 按逗号（忽略括号深度 >0 的逗号）切分；
    - 'X (a, b)' -> 'X a', 'X b'，且当 X 不是木材/染料前缀时同时保留 X 本身；
    - 说明性括号（all/including/head/foot 等）只保留 X；
    - 丢弃 'JE'/'BE only'/'[Java Edition only]' 等注记。
    """
    raw_tokens: List[str] = []
    buffer, depth = "", 0
    for ch in text:
        if ch == "(":
            depth += 1
            buffer += ch
        elif ch == ")":
            depth = max(0, depth - 1)
            buffer += ch
        elif ch == "," and depth == 0:
            raw_tokens.append(buffer.strip())
            buffer = ""
        else:
            buffer += ch
    if buffer.strip():
        raw_tokens.append(buffer.strip())

    out: List[str] = []
    for token in raw_tokens:
        token = token.replace("\u200c", "").strip()
        token = re.sub(r"\[[^\]]*\]", "", token).strip()
        token = re.sub(r"\s+except\s+.*$", "", token).strip()
        if not token:
            continue
        if "edition" in token.lower() or re.fullmatch(
            r"(je|be|java edition|bedrock edition)( only)?", token, re.I
        ):
            continue
        if "(" not in token:
            out.append(token)
            continue
        head, _, rest = token.partition("(")
        rest = rest.rsplit(")", 1)[0]
        head = head.strip()
        rest_flat = re.sub(r"\([^)]*\)", "", rest)
        if re.search(r"\b(all|every|including|head|foot)\b", rest_flat, re.I):
            if head:
                out.append(head)
            continue
        # 非说明性分组：'stone (slab, stairs)' / 'birch (planks, log ...)'
        head_words = re.split(r"[ \-]+", head.lower())
        head_is_prefix = (
            "_".join(head_words) in _WOOD_WORDS
            or "_".join(head_words) in _DYEWORDS
        )
        if head and not head_is_prefix:
            out.append(head)
        for item in [part.strip() for part in rest.split(",") if part.strip()]:
            item = re.sub(r"\([^)]*\)", "", item).strip()
            if item:
                out.append((head + " " + item).strip())
    return out


_DYEWORDS = {
    "white", "orange", "magenta", "light_blue", "yellow", "lime", "pink",
    "gray", "light_gray", "cyan", "purple", "blue", "brown", "green", "red",
    "black",
}


def _dye_name(parts: Sequence[str]) -> Optional[str]:
    """'red wool' -> 'red'；'light gray wool' -> 'silver'（基岩版 color state 值）。"""
    color_words = {
        "white": "white", "orange": "orange", "magenta": "magenta",
        "light_blue": "light_blue", "yellow": "yellow", "lime": "lime",
        "pink": "pink", "gray": "gray", "light_gray": "silver",
        "cyan": "cyan", "purple": "purple", "blue": "blue", "brown": "brown",
        "green": "green", "red": "red", "black": "black",
    }
    return color_words.get("_".join(parts[:-1]))


_WOOD_WORDS = {
    "oak", "spruce", "birch", "jungle", "acacia", "dark_oak",
    "mangrove", "cherry", "pale_oak", "crimson", "warped",
}

# 颜色家族：基岩版中这些方块以单个 ID + color state 存在
_COLOR_FAMILY_ITEMS = {
    "wool", "carpet", "candle", "bed", "concrete", "concrete_powder",
    "stained_glass", "stained_glass_pane", "shulker_box",
}
_COLOR_FAMILY_BASE = {
    "wool": "minecraft:wool",
    "carpet": "minecraft:carpet",
    "candle": "minecraft:candle",
    "bed": "minecraft:bed",
    "concrete": "minecraft:concrete",
    "concrete_powder": "minecraft:concrete_powder",
    "stained_glass": "minecraft:stained_glass",
    "stained_glass_pane": "minecraft:stained_glass_pane",
    "shulker_box": "minecraft:shulker_box",
}


def _bedrock_id(parts: Sequence[str]) -> Optional[str]:
    """把 wiki 方块名（词元列表）映射为基岩版 ID；无法映射返回 None。"""
    if not parts:
        return None
    if parts[:2] == ["block", "of"]:
        parts = ["_".join(parts[2:]) + "_block"]  # 'block of iron' -> 'iron_block'
    joined = "_".join(parts)
    if not joined or joined.startswith("infested_") or parts[-1] in ("head", "foot"):
        return None
    if joined.startswith("waterlogged"):
        return None
    # 颜色家族（由 state_overrides 处理，这里不落基表）
    if parts[-1] in _COLOR_FAMILY_ITEMS:
        return None
    if parts[-1] == "glazed_terracotta":
        return None if len(parts) == 1 else "minecraft:" + joined
    if parts[-1] == "terracotta" and len(parts) > 1:
        return None  # stained_hardened_clay 由 color state 处理
    if parts[-1] == "leaves" and len(parts) > 1 and parts[0] != "oak":
        return None  # 只有橡树树叶作为 minecraft:leaves 基色
    # 木材家族（wood_type 覆盖表处理；基表只保留 oak 兜底）
    if parts[-1] in ("planks", "log", "wood", "fence", "fence_gate") and len(parts) > 1:
        return None if parts[0] != "oak" else "minecraft:" + parts[-1]
    return "minecraft:" + _JAVA_TO_BEDROCK.get(joined, joined)


# --- 基岩版地图基色表（comeixalpha / Colorify Docs） -------------------------

_DYE_ORDER = [
    "white", "orange", "magenta", "light_blue", "yellow", "lime", "pink",
    "gray", "light_gray", "cyan", "purple", "blue", "brown", "green", "red",
    "black",
]
# ref 表格命名 -> 基岩版 color state 值
_DYE_STATE_NAMES = {"light_gray": "silver"}

# ref 表中缺失/错误的值（wiki 交叉验证后的人工修正）
_REF_FIXES = {
    "minecraft:redstone_lamp": "#9f5224",
    "minecraft:pointed_dripstone": "#4c3223",
    "minecraft:pink_petals": "#007c00",
}

# 生物群系染色方块（Bedrock 地图按 tint 渲染，见 wiki Tints 章节）：
# 统一以平原群系为准。平原 = 默认 tint（minecraft-data tints.json 中 plains 的
# color=0），即 Java DefaultBiomeColors 经典值：
#   grass #7cbd6b / foliage #77ab2f / water #3f76e4
# 覆盖的方块：草方块、短草、蕨、珊瑚与珊瑚扇（grass tint）、树叶、藤蔓
# （foliage tint）、水（water tint）。珊瑚因此与草同色（Bedrock 地图语义）。
_PLAINS_TINT_FIXES = {
    "minecraft:grass_block": "#7cbd6b",
    "minecraft:short_grass": "#7cbd6b",
    "minecraft:fern": "#7cbd6b",
    "minecraft:tube_coral": "#7cbd6b",
    "minecraft:brain_coral": "#7cbd6b",
    "minecraft:bubble_coral": "#7cbd6b",
    "minecraft:fire_coral": "#7cbd6b",
    "minecraft:horn_coral": "#7cbd6b",
    "minecraft:leaves": "#77ab2f",
    "minecraft:vine": "#77ab2f",
    "minecraft:water": "#3f76e4",
}

# 三源裁决修正（mcpixelart 纹理最亮色 + wiki 地图色 2:1 反对 ref 的条目）
# 注：grass_block 不在此列——Bedrock 地图上草方块按生物群系染色（grass tint），
# ref 的 #92bc58 即 Bedrock 默认草地色，语义上优于 Java 固定色 #7fb238。
_PIXELART_FIXES = {
    "minecraft:sculk": "#191919",
    "minecraft:sculk_vein": "#191919",
    "minecraft:sculk_catalyst": "#191919",
    "minecraft:sculk_sensor": "#191919",
    "minecraft:sculk_shrieker": "#191919",
    "minecraft:calibrated_sculk_sensor": "#191919",
    "minecraft:lodestone": "#a7a7a7",
    "minecraft:target": "#fffcf5",
    "minecraft:stripped_acacia_wood": "#d87f33",
}

# ref 缺失、由 mcpixelart 补充的 Bedrock 方块 ID（值取纹理最亮色档）
_PIXELART_ADDITIONS = {
    # 树苗：非 tint 方块，用 wiki PLANT 行值
    "minecraft:sapling": "#007c00",
    "minecraft:copper_block": "#d87f33",
    "minecraft:chiseled_quartz_block": "#fffcf5",
    "minecraft:chiseled_red_sandstone": "#d87f33",
    "minecraft:chiseled_resin_bricks": "#9f5224",
    "minecraft:chiseled_sandstone": "#f7e9a3",
    "minecraft:chiseled_stone_bricks": "#707070",
    "minecraft:coarse_dirt": "#976d4d",
    "minecraft:copper_ore": "#707070",
    "minecraft:cracked_stone_bricks": "#707070",
    "minecraft:creaking_heart": "#d87f33",
    "minecraft:cut_red_sandstone": "#d87f33",
    "minecraft:dark_prismarine": "#5cdbd5",
    "minecraft:deepslate_copper_ore": "#646464",
    "minecraft:light_gray_glazed_terracotta": "#999999",
    "minecraft:magma_block": "#700200",
    "minecraft:mushroom_stem": "#c7c7c7",
    "minecraft:note_block": "#8f7748",
    "minecraft:pale_moss_block": "#999999",
    "minecraft:prismarine_bricks": "#5cdbd5",
    "minecraft:purpur_pillar": "#b24cd8",
    "minecraft:quartz_pillar": "#fffcf5",
    "minecraft:raw_copper_block": "#d87f33",
    "minecraft:red_sand": "#d87f33",
    "minecraft:resin_block": "#9f5224",
    "minecraft:resin_bricks": "#9f5224",
    "minecraft:rooted_dirt": "#976d4d",
    "minecraft:stripped_pale_oak_log": "#fffcf5",
    "minecraft:stripped_pale_oak_wood": "#fffcf5",
    "minecraft:waxed_chiseled_copper": "#d87f33",
    "minecraft:waxed_copper_block": "#d87f33",
    "minecraft:waxed_copper_bulb": "#d87f33",
    "minecraft:waxed_copper_grate": "#d87f33",
    "minecraft:waxed_cut_copper": "#d87f33",
    "minecraft:waxed_exposed_copper": "#876b62",
    "minecraft:waxed_exposed_copper_bulb": "#876b62",
    "minecraft:waxed_exposed_cut_copper": "#876b62",
    "minecraft:waxed_oxidized_chiseled_copper": "#167e86",
    "minecraft:waxed_oxidized_copper": "#167e86",
    "minecraft:waxed_oxidized_copper_bulb": "#167e86",
    "minecraft:waxed_oxidized_copper_grate": "#167e86",
    "minecraft:waxed_oxidized_cut_copper": "#167e86",
    "minecraft:waxed_weathered_chiseled_copper": "#3a8e8c",
    "minecraft:waxed_weathered_copper": "#3a8e8c",
    "minecraft:waxed_weathered_copper_bulb": "#3a8e8c",
    "minecraft:waxed_weathered_copper_grate": "#3a8e8c",
    "minecraft:waxed_weathered_cut_copper": "#3a8e8c",
    "minecraft:wet_sponge": "#e5e533",
}

# ref 命名 -> 基岩版实际方块 ID（camelCase 等差异）
_REF_ID_RENAMES = {
    "minecraft:sea_lantern": "minecraft:seaLantern",
}


def _parse_ref_html(path: str) -> Dict[str, str]:
    """解析基岩版地图基色表页面，返回 {方块ID: #rrggbb}。"""
    with open(path, encoding="utf-8") as fileobj:
        raw = fileobj.read()
    tables = re.findall(r"<table.*?</table>", raw, re.S)
    if not tables:
        raise ValueError("HTML 中未找到颜色表格（请使用 comeixalpha.github.io/ref/mapcolors/ 页面）")
    ref: Dict[str, str] = {}
    for row in re.findall(r"<tr>(.*?)</tr>", tables[0], re.S):
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
        if len(cells) < 2:
            continue
        bid = html.unescape(re.sub(r"<[^>]+>", "", cells[0])).strip()
        hexv = html.unescape(re.sub(r"<[^>]+>", "", cells[1])).strip().lower()
        if bid.startswith("minecraft:") and _HEX_RE.match(hexv):
            ref[bid] = "#" + hexv.lstrip("#")
    if not ref:
        raise ValueError("未能从表格解析到任何颜色")
    return ref


def build_table_from_ref(ref: Dict[str, str]) -> Dict[str, object]:
    """以基岩版地图基色表为底，合并人工修正与 state 覆盖。"""
    colors = {bid: hexv for bid, hexv in ref.items() if hexv != "#000000"}
    for old_id, new_id in _REF_ID_RENAMES.items():
        if old_id in colors:
            colors[new_id] = colors.pop(old_id)
    colors.update(_REF_FIXES)
    colors.update(_PIXELART_FIXES)
    colors.update(_PIXELART_ADDITIONS)
    colors.update(_PLAINS_TINT_FIXES)
    dye_overrides: Dict[str, str] = {}
    for name in _DYE_ORDER:
        value = ref.get(f"minecraft:{name}_wool")
        if value:
            dye_overrides[_DYE_STATE_NAMES.get(name, name)] = value
    # 三源裁决：purple 系以 wiki/mcpixelart 为准（#7f3fb2），ref 的 #995acd 不采纳
    dye_overrides["purple"] = "#7f3fb2"
    wood_overrides: Dict[str, str] = {}
    for name in sorted(_WOOD_WORDS):
        value = ref.get(f"minecraft:{name}_planks")
        if value:
            wood_overrides[name] = value
    # ref 表较旧，pale_oak 由 mcpixelart 数据补充
    wood_overrides.setdefault("pale_oak", "#fffcf5")
    terracotta: Dict[str, str] = {}
    for name in _DYE_ORDER:
        value = ref.get(f"minecraft:{name}_terracotta")
        if value:
            terracotta[_DYE_STATE_NAMES.get(name, name)] = value
    return {
        "colors": dict(sorted(colors.items())),
        "state_overrides": {
            "color": dict(sorted(dye_overrides.items())),
            "wood_type": dict(sorted(wood_overrides.items())),
        },
        "block_overrides": {
            "minecraft:stained_hardened_clay": {"color": dict(sorted(terracotta.items()))}
        },
    }


def build_table_from_wiki(rows: Sequence[Tuple[str, str, List[str]]]) -> Dict[str, object]:
    colors: Dict[str, str] = {}
    dye_from_wool: Dict[str, str] = {}
    dye_other: Dict[str, str] = {}
    wood_overrides: Dict[str, str] = {}
    for id_text, rgb_text, names in rows:
        if not rgb_text or "transparent" in rgb_text.lower():
            continue
        try:
            rgb = tuple(int(item.strip()) for item in rgb_text.split(","))
            if len(rgb) != 3:
                continue
        except ValueError:
            continue
        hex_value = rgb_to_hex(rgb)
        for token in names:
            parts = [p for p in re.split(r"[ \-]+", token.lower()) if p]
            if not parts:
                continue
            joined = "_".join(parts)
            # 木材家族：只取 'X planks' 定义木材色（log 等会落在别的颜色行）
            if parts[-1] == "planks" and "_".join(parts[:-1]) in _WOOD_WORDS:
                wood = "_".join(parts[:-1])
                wood_overrides.setdefault(wood, hex_value)
                if wood == "oak":
                    colors.setdefault("minecraft:planks", hex_value)
                continue
            # 颜色家族：'red wool' / 'white stained glass' / 'orange concrete' ...
            family = next(
                (item for item in _COLOR_FAMILY_ITEMS if joined.endswith("_" + item)),
                None,
            )
            if family is not None:
                dye = _dye_name(parts)
                if dye:
                    if family == "wool":
                        dye_from_wool.setdefault(dye, hex_value)
                    else:
                        dye_other.setdefault(dye, hex_value)
                    if dye == "white":
                        base_id = _COLOR_FAMILY_BASE.get(family)
                        if base_id:
                            colors.setdefault(base_id, hex_value)
                continue
            bedrock = _bedrock_id(parts)
            if bedrock and bedrock not in colors:
                colors[bedrock] = hex_value
    # 染料色：羊毛定义优先，其余（蜡烛等）补缺
    dye_overrides = {**dye_other, **dye_from_wool}
    return {
        "colors": dict(sorted(colors.items())),
        "state_overrides": {
            "color": dict(sorted(dye_overrides.items())),
            "wood_type": dict(sorted(wood_overrides.items())),
        },
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--from-ref", metavar="HTML", help="基岩版地图基色表（comeixalpha.github.io/ref/mapcolors/）HTML 路径")
    source.add_argument("--from-wiki", metavar="HTML", help="Minecraft Wiki Map item format 页面 HTML 路径")
    source.add_argument("--from-blocks", metavar="JSON", help="vanilla 行为包 blocks.json 路径")
    parser.add_argument(
        "-o", "--output",
        default=os.path.join(ROOT, "data", "map_colors.json"),
        help="输出路径（默认 data/map_colors.json）",
    )
    args = parser.parse_args(argv)

    if args.from_ref:
        ref = _parse_ref_html(args.from_ref)
        table = build_table_from_ref(ref)
        table["meta"] = {
            "source": "comeixalpha.github.io/ref/mapcolors/ + mcpixelart.com + Minecraft Wiki 三源交叉",
            "input_file": os.path.basename(args.from_ref),
            "generated": datetime.date.today().isoformat(),
            "block_count": len(table["colors"]),
        }
        skip_info = None
    elif args.from_blocks:
        colors, skipped = _parse_blocks_json(args.from_blocks)
        if not colors:
            print(f"错误: 未从 {args.from_blocks} 中解析到任何 map_color", file=sys.stderr)
            return 2
        table: Dict[str, object] = {
            "meta": {
                "source": "vanilla behavior pack blocks.json (map_color)",
                "input_file": os.path.basename(args.from_blocks),
                "generated": datetime.date.today().isoformat(),
                "block_count": len(colors),
            },
            "colors": dict(sorted(colors.items())),
            "state_overrides": {
                "color": {name: rgb_to_hex(rgb) for name, rgb in sorted(m.DYE_COLORS.items())},
                "wood_type": {name: rgb_to_hex(rgb) for name, rgb in sorted(m.WOOD_COLORS.items())},
            },
        }
        skip_info = len(skipped)
    else:
        rows = _parse_wiki_html(args.from_wiki)
        table = build_table_from_wiki(rows)
        table["meta"] = {
            "source": "minecraft.wiki Map item format (Base colors)",
            "input_file": os.path.basename(args.from_wiki),
            "generated": datetime.date.today().isoformat(),
            "rows": len(rows),
            "block_count": len(table["colors"]),
        }
        skip_info = None

    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as fileobj:
        json.dump(table, fileobj, ensure_ascii=False, indent=2)
        fileobj.write("\n")

    overrides = table["state_overrides"]
    block_overrides = table.get("block_overrides")
    print(f"已写出: {args.output}")
    print(f"  方块颜色: {len(table['colors'])} 项")
    print(f"  state 覆盖: color={len(overrides['color'])}  wood_type={len(overrides['wood_type'])}")
    if block_overrides:
        print(
            "  block 覆盖: "
            + ", ".join(f"{bid}({len(bucket['color'])}色)" for bid, bucket in block_overrides.items())
        )
    if skip_info is not None:
        print(f"  （跳过无 map_color 的条目: {skip_info}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
