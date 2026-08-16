# 架构说明（ARCHITECTURE）

本文件面向想读懂、修改或扩展本项目的开发者，说明代码的组织方式、
核心数据模型与两条转换管线的数据流。使用层面的说明见根目录 `README.md`。

---

## 1. 总览

```
Str-Geo-Converter/
├─ mc_geo_converter.py        # 核心：数据模型 + 两条转换管线 + argparse CLI（约 1250 行）
├─ mc_geo_converter_gui.py    # 可选 tkinter 图形界面，薄封装，直接复用核心函数
├─ tests/test_converter.py    # unittest 自检测试（无 pytest 依赖）
├─ examples/                  # 示例几何模型与其生成的结构文件
├─ samples/                   # 游戏真实导出的 .mcstructure 样例
├─ data/map_colors.json       # 方块地图色表（map-color 贴图功能）
├─ tools/generate_map_colors.py  # 从 vanilla blocks.json 重新生成颜色表
└─ *.bat                      # Windows 快捷入口
```

设计原则：

- **单一数据模型**：两种方向的转换共享同一套数据类（`StructureData`、
  `BlockRef` / `BlockState`），保证"几何 ↔ 结构"往返无损；
- **纯函数管线**：`parse_*` / `*_to_*` / `build_*` 都是无副作用的纯函数，
  输入输出均为普通 dict / dataclass，便于单测与复用；
- **依赖最小化**：除 `nbtlib` 外只用标准库；`nbtlib` 仅承担小端 NBT 的
  读写（`parse_mcstructure` / `build_structure_nbt` 两个接触点）。

---

## 2. 核心数据模型（mc_geo_converter.py）

| 类型 | 作用 |
| --- | --- |
| `BlockState` | 单个方块状态，保留 `kind`（string/int/byte/short/long）与值，使 NBT 类型经骨骼名编码后仍可无损往返 |
| `BlockRef` | 不可变方块引用：`name`（带命名空间）+ 有序 `states` + `version`（palette 兼容版本号） |
| `BoneBlock` | `BlockRef` + 所在层 `layer`（0=主层，1=副层），是骨骼名解析的结果 |
| `StructureData` | 解析后的结构文件：`size`、`primary`/`secondary`（`pos -> palette 索引` 字典）、`palette`、`world_origin`、`format_version`、`entity_count`、`block_position_data_count`、`warnings` |
| `ConverterError` | 应向用户报告的业务错误（CLI 捕获后打印并返回非零退出码） |

方块索引按 Bedrock 的 **ZYX 展开顺序** 拍平：

```python
xyz_to_index(x, y, z, size)   # index = (sz*sy)*x + sz*y + z
index_to_xyz(index, size)     # 逆运算，越界抛 ConverterError
```

### 2.1 骨骼名编码协议（几何 ↔ 方块的双向桥）

几何 JSON 没有方块概念，转换通过**骨骼名**传递方块信息：

```
minecraft:stone                          # 纯方块 ID
minecraft:planks[wood_type="oak"]        # ID + states
minecraft:wooden_door[direction=0,open_bit=0b]
secondary:minecraft:water[liquid_depth=0]  # secondary: 前缀 = 副层（含水方块）
```

对应实现：

- `encode_block_ref(ref, layer)` → 骨骼名；
- `parse_bone_name(name)` → `BoneBlock`（无法识别时返回 `None`，如 `head`/`leg`）；
- `parse_block_ref(text)` → `BlockRef`（可解析普通方块缩写 `stone` → `minecraft:stone`）。

states 的 NBT 类型用后缀保留：字符串 `key="v"`、int `key=3`、byte `key=0b`、
short `key=3s`、long `key=3L`。这是"几何 → 结构 → 几何"往返无损的关键。

---

## 3. 管线一：`.mcstructure` → `.geo.json`（结构转几何）

```
parse_mcstructure(path)           # 读小端 NBT，展平为 StructureData
        │
_group_structure_blocks(data,...) # 按 (layer, BlockRef) 分组（骨骼的唯一定义来源）
        │
structure_to_geometry(data, ...)  # 分组 → 骨骼；每方块 → 1×1×1 cube（--scale 等比放大）
        │                         # 可选 texture（MapColorTexture）→ cube 写 per-face UV
        ▼
Bedrock geometry JSON dict        # bones/description/visible_bounds
```

要点：

- 主层每格方块坐标即为 cube 的 `origin`；同方块（含相同 states）合并为
  一个骨骼，`--include-air` 时才保留空气方块；
- `--scale N`：cube `size=[N,N,N]`，origin / pivot / visible_bounds 同步缩放
  （`_tidy_number` 负责清理浮点尾巴，如 `0.30000000000000004`）；
- `--include-secondary` 将副层方块写成 `secondary:` 前缀骨骼；
- `--include-origin` 把 `structure_world_origin` 写入 description 附加字段；
- 实体、方块实体数据无法表达，跳过并收集进 `warnings` 由 CLI 打印。

### 3.1 map-color 贴图（可选）

`to-geo --map-color-texture` 利用方块地图色（`data/map_colors.json`，源自 vanilla
行为包 `blocks.json` 的 `map_color` 字段）为模型自动生成纯色贴图：

```
_group_structure_blocks 的分组结果
        │
map_color_for(ref, table)         # state 覆盖（color/wood_type）> ID 基色 > 默认灰
        ▼
build_map_color_texture(groups)   # 每骨骼 3 个色块（亮/侧/暗）→ 2 的幂尺寸图集
        │
write_png(path, texture)          # 纯标准库 PNG 编码器（zlib+struct，无 Pillow）
        ▼
structure_to_geometry(texture=)   # cube 写 per-face UV；description 写 textures + 图集尺寸
```

颜色查表优先级：方块级 `block_overrides`（如陶瓦 16 色 `stained_hardened_clay`）
> `state_overrides`（如羊毛 `color="red"` → 红色、木头 `wood_type`）
> 方块 ID 基色 > 默认灰 `DEFAULT_MAP_COLOR`。内置覆盖表（`DYE_COLORS` /
`WOOD_COLORS`）是兜底，表内同名字段优先；自定义表经 `--map-colors FILE` 传入，
格式与 `data/map_colors.json` 一致。内置表以
[基岩版地图基色表](https://comeixalpha.github.io/ref/mapcolors/) 为底生成
（`tools/generate_map_colors.py --from-ref`，另支持 `--from-wiki` / `--from-blocks`）。

## 4. 管线二：`.geo.json` → `.mcstructure`（几何转结构）

```
load_geometry(path)               # 读几何 JSON，可能含多个 geometry 对象
        │
select_geometry(geometries, id)   # --geometry 按标识符或 1 起始序号选择
        ▼
geometry_to_structure(geo, ...)   # 骨骼 → BlockRef（fallback 默认方块）
        │                         # 每个 cube → _cube_cells 体素化为整格
        │                         # 平移至最小角落在 (0,0,0)，空位填 air / -1
        ▼
build_structure_nbt(data)         # 组装 palette + 按 ZYX 顺序写层数据
        │
write_mcstructure(path, nbt)      # 未压缩小端 NBT 落盘
```

要点：

- `parse_bone_name` 识别成功的骨骼 → 对应方块；失败（如 `head`）→
  `--block` 默认方块（默认 `minecraft:stone`）并计入跳过警告；
- 体素化：`_cube_cells` 计算 `[origin, origin+size)` 覆盖的整格（默认
  `--snap floor`，`--snap round` 则四舍五入）；`inflate` 参与计算，
  `rotation`/`mirror` 忽略并警告；`MAX_CUBE_CELLS = 5_000_000` 是防止
  意外超大 cube 撑爆内存的安全阀；
- `--voxel-size N` 是 `--scale N` 的逆运算，可精确还原 `to-geo --scale 2`
  生成的几何（每个 cube 视作 `N×N×N` 方块块）；
- 空位：主层填 `minecraft:air`，副层填 `-1`（结构空位）；
- `MAX_STRUCTURE_SIZE = (64, 384, 64)` 超出游戏上限时仅警告。

---

## 5. CLI 与 GUI 层

### CLI（mc_geo_converter.py）

- `build_parser()` 定义两个子命令 `to-geo` / `to-structure` 与全部选项；
- 未写子命令时按**输入/输出扩展名自动推断方向**（`.mcstructure` → `to-geo`）；
- `_default_output()` 实现"不写 `-o` 则同名换扩展名"的默认行为；
- `run_to_geo` / `run_to_structure` 捕获 `ConverterError`，打印警告列表
  （`_print_warnings`），错误时返回非零退出码；
- 模块顶部对 `sys.stdout/stderr` 做 UTF-8 `reconfigure`，避免 Windows
  控制台 GBK 乱码。

### GUI（mc_geo_converter_gui.py）

- `ConverterApp`：两个转换区 + 日志区；文件选择、输出路径（选文件/选目录）、
  缩放与体素尺寸、默认方块、取整方式等控件一一对应 CLI 选项；
- 转换在后台线程执行（`_run_job` → `_poll_queue` 队列轮询刷新日志），
  避免阻塞 tkinter 主循环；
- 不复制业务逻辑：`_job_structure_to_geo` / `_job_geo_to_structure`
  只是把控件值整理成 `run_to_geo` / `run_to_structure` 的参数。

---

## 6. 测试（tests/test_converter.py）

纯 `unittest`，无 pytest 依赖，运行：

```bat
python -m unittest discover -s tests -v
```

覆盖重点：

- 骨骼名编码 / 解析的往返（全部 NBT 类型、`secondary:` 前缀、非法引用拒绝）；
- Bedrock ZYX 方块索引顺序；
- 真实 `.mcstructure` 样例（`samples/`）往返：结构 → 几何 → 结构后逐格比对；
- 副层（含水方块）、负坐标归一化、scale/voxel-size 互逆；
- CLI 双向转换与默认输出命名；
- tkinter 可用时对 GUI 模块做导入级冒烟测试（`HAVE_TKINTER` 守卫）。

新增转换逻辑时，建议同时补一组"往返"用例：转换两次后结果应逐字节/逐格一致。

---

## 7. 常见改动路径

| 想做什么 | 改哪里 |
| --- | --- |
| 新增 CLI 选项 | `build_parser()` + 对应 `run_to_*` 透传 |
| 改变方块分组/骨骼命名规则 | `encode_block_ref` / `parse_bone_name` + 2.1 节协议 |
| 改变体素化策略 | `_cube_cells` / `_apply_inflate` / `geometry_to_structure` |
| 新增输出格式 | 新增 `parse_*` + `*_to_*` + `run_to_*` 三个环节 |
| GUI 加控件 | `ConverterApp._build_*` 系列 + 对应 `_job_*` |

## 8. 参考资料

- Bedrock Wiki: [.mcstructure](https://wiki.bedrock.dev/nbt/mcstructure.html)
- tryashtar: [Bedrock mcstructure file format](https://gist.github.com/tryashtar/87ad9654305e5df686acab05cc4b6205)
- Bedrock Wiki: [Block Modeling / Geometry](https://wiki.bedrock.dev/visuals/bedrock-modeling)
