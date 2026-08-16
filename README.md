# Str-Geo-Converter

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Minecraft **基岩版（Bedrock Edition）** `.geo.json` 几何模型文件与 `.mcstructure` 结构方块文件的双向转换工具。

> 注意：这里的 `.geo.json` 指 Minecraft 资源包里的 **几何模型 JSON**（Blockbench 导出的模型格式），不是地图领域的 GeoJSON。

---

## 目录结构

```
Str-Geo-Converter/
├─ mc_geo_converter.py        # 核心转换脚本（命令行工具）
├─ mc_geo_converter_gui.py    # 可选图形界面（tkinter）
├─ requirements.txt           # 依赖：nbtlib
├─ CHANGELOG.md               # 版本历史
├─ docs/ARCHITECTURE.md       # 内部架构与数据流说明
├─ LICENSE                    # MIT 许可证
├─ data/map_colors.json       # 方块地图色表（--map-color-texture 用）
├─ tools/generate_map_colors.py  # 从 vanilla blocks.json 重新生成颜色表
├─ examples/
│  ├─ house.geo.json          # 示例几何模型（小房子）
│  └─ house.mcstructure       # 由 house.geo.json 生成的结构文件
├─ samples/                   # 游戏真实导出的 .mcstructure 样例
├─ tests/test_converter.py    # 自动化测试
├─ structure2geo.bat          # Windows 快捷方式：结构 -> 几何
├─ geo2structure.bat          # Windows 快捷方式：几何 -> 结构
└─ start_gui.bat              # Windows 快捷方式：启动图形界面
```

---

## 安装

需要 Python 3.9+。

```bat
cd /d D:\Develop\Str-Geo-Converter
pip install -r requirements.txt
```

依赖仅有一个纯 Python 库 [`nbtlib`](https://pypi.org/project/nbtlib/)，用于读写小端（little-endian）NBT。

---

## 快速使用

### 1. `.mcstructure` → `.geo.json`（结构转几何）

```bat
python mc_geo_converter.py to-geo 输入.mcstructure -o 输出.geo.json
```

等价写法（按扩展名自动识别方向）：

```bat
python mc_geo_converter.py 输入.mcstructure 输出.geo.json
```

### 2. `.geo.json` → `.mcstructure`（几何转结构）

```bat
python mc_geo_converter.py to-structure 输入.geo.json -o 输出.mcstructure
```

等价写法：

```bat
python mc_geo_converter.py 输入.geo.json 输出.mcstructure
```

不写 `-o` 时，输出文件默认与输入文件同名、仅改扩展名。

### 3. 试试示例

```bat
python mc_geo_converter.py examples/house.geo.json -o out.mcstructure
python mc_geo_converter.py out.mcstructure -o roundtrip.geo.json
```

### 4. 图形界面（可选）

```bat
python mc_geo_converter_gui.py
```

或直接双击 `start_gui.bat`。界面提供文件选择、副层/世界原点选项、默认方块与取整方式设置。

**转换产物指定路径**：两个转换区的“输出路径”均可手动填写完整文件路径，也可点击：

- **选文件...**：弹出另存为对话框，选择输出文件名；
- **选目录...**：选择目标目录，转换产物会以“输入文件名 + 对应扩展名”自动命名后保存到该目录。

输出路径留空时，默认保存到输入文件所在目录。

**体素等比缩放**：

- 结构 → 几何区的“等比缩放”输入框可设置缩放比例（默认 1）。例如填 `2` 时，每个方块转换为 `size=[2,2,2]` 的 cube，坐标和 pivot 同步放大；填 `0.5` 时缩小。
- 几何 → 结构区的“体素尺寸”输入框是它的逆运算：由 `--scale 2` 生成的几何，在此处填 `2` 即可精确还原为原始方块结构。

---

## 转换规则

### `.mcstructure` → `.geo.json`

| 结构中的内容 | 几何文件中的表示 |
| --- | --- |
| 主层中的每个非空气方块 | 默认合并为 cube：相邻同种方块贪心合并成一个大长方体（如 `2×2×2` 实心 → 1 个 `size=[2,2,2]` 的 cube），大幅减少 cube 数量与 Blockbench 渲染压力；`--no-merge-voxels` 可关闭（每个方块独立 1×1×1 cube）；`--scale N` 时整体等比缩放 |
| 同一种方块（含相同 states） | 合并为一个骨骼（bone），骨骼名编码方块 ID 与 states |
| 空气方块 | 默认跳过（`--include-air` 可保留） |
| 副层（例如含水方块） | 默认跳过，`--include-secondary` 时转换为骨骼名带 `secondary:` 前缀的 cube |
| 实体、箱子内容等方块实体数据 | 无法用几何表达，转换时跳过并在命令行提示 |
| `structure_world_origin` | 默认不写入；`--include-origin` 时写入 description 附加字段 |

> 合并只发生在 6 邻接的同种方块之间，覆盖区域与原结构完全一致，
> 转回 `.mcstructure` 时大 cube 会无损展开为原方块（对 `--voxel-size` 还原同样成立）。

**等比缩放示例**：`--scale 2` 后，结构内 `(x,y,z)` 处方块变为 `origin=[2x,2y,2z]`、`size=[2,2,2]` 的 cube；`--scale 0.5` 则变为 `size=[0.5,0.5,0.5]`。骨骼 pivot 和 `visible_bounds` 也会同步缩放。

骨骼名编码格式：

```
minecraft:stone
minecraft:planks[wood_type="oak"]
minecraft:wooden_door[direction=0,open_bit=0b]
secondary:minecraft:water[liquid_depth=0]
```

方块 states 的 NBT 类型通过后缀保留：

| 类型 | 写法 | 例子 |
| --- | --- | --- |
| string | `key="value"` | `color="red"` |
| int | `key=3` | `direction=0` |
| byte | `key=0b` | `open_bit=0b` |
| short | `key=3s` | — |
| long | `key=3L` | — |

### `.geo.json` → `.mcstructure`

- 每个骨骼对应一种方块，骨骼名按上表解析。无法识别成方块 ID 的骨骼（如 `head`、`leg`）使用 `--block` 指定的默认方块（默认 `minecraft:stone`）。
- 每个 cube 体素化为整数方块网格：
  - `size=[2,1,3]` 会展开成 6 个方块；
  - 默认 `--snap floor`：坐标向下取整，覆盖 `[origin, origin+size)` 内的整格；
  - `--snap round`：坐标和尺寸四舍五入。
  - cube 的 `inflate` 会参与计算，`rotation`/`mirror` 会被忽略（命令行会有提示）。
- 所有方块整体平移，使结构最小角落在 `(0,0,0)`。
- 空位在主层填入 `minecraft:air`，副层填入 `-1`（结构空位）。
- 含 `secondary:` 前缀的骨骼写入副层，可用于还原含水方块。
- 一个文件含多个几何对象时，用 `--geometry <标识符或序号>` 选择。

---

## 命令行选项

### `to-geo`（结构 → 几何）

| 选项 | 默认值 | 说明 |
| --- | --- | --- |
| `-o, --output` | 与输入同名 | 输出 `.geo.json` 路径 |
| `--identifier` | `geometry.<文件名>` | 几何标识符，如 `geometry.house` |
| `--format-version` | `1.16.0` | 几何 JSON 的 format_version |
| `--texture-width` | `16` | `description.texture_width` |
| `--texture-height` | `16` | `description.texture_height` |
| `--scale` | `1` | 等比缩放：体素 cube 尺寸变为 `[N,N,N]`，坐标、pivot 与边界同步缩放 |
| `--include-air` | 关 | 把空气方块也转换成 cube（不推荐） |
| `--include-secondary` | 关 | 转换副层方块 |
| `--include-origin` | 关 | 把结构世界原点写入 description 附加字段 |
| `--map-color-texture` | 关 | 按方块地图色自动生成 `.png` 色块贴图并写入 per-face UV（见下文） |
| `--map-colors` | 内置表 | 自定义 map 颜色表 JSON（格式见 `data/map_colors.json`） |
| `--texture-size` | `16` | 贴图单个色块边长（须为 2 的幂） |
| `--no-texture-shade` | 关 | 关闭 Minecraft 风面着色（顶亮/侧中/底暗） |
| `--no-merge-voxels` | 关 | 关闭相邻同种方块的合并（默认开启） |

### `to-structure`（几何 → 结构）

| 选项 | 默认值 | 说明 |
| --- | --- | --- |
| `-o, --output` | 与输入同名 | 输出 `.mcstructure` 路径 |
| `--geometry` | 单个几何时自动 | 选择哪个几何对象（标识符或 1 起始序号） |
| `--block` | `minecraft:stone` | 骨骼名无法识别时的默认方块 |
| `--block-version` | `17959425` | palette 方块兼容版本号 |
| `--world-origin` | `0,0,0` | `structure_world_origin`，如 `100,64,-100` |
| `--snap` | `floor` | 非整数坐标体素化：`floor` 或 `round` |
| `--voxel-size` | `1` | 几何中一个方块对应的长度单位；若几何由 `to-geo --scale N` 生成，填 `N` 可精确还原 |

---

## 自动生成 map-color 贴图

Minecraft 中每个方块在地图上都有对应的显示色（基岩版数据源为 vanilla 行为包
`blocks.json` 的 `map_color` 字段）。利用这一点，`to-geo` 可以为模型**自动生成
一套纯色贴图**：每个骨骼对应一个色块，图集按 2 的幂尺寸排布，每个 cube 的面
UV 指向自己骨骼的色块区域，模型立刻可见、可区分。

```bat
python mc_geo_converter.py to-geo 输入.mcstructure -o 输出.geo.json --map-color-texture
```

会同时生成 `输出.png`（图集）与 `输出.geo.json`（`description.textures` 引用同名
贴图 + per-face UV），Blockbench 打开即可看到带色模型。

- 颜色查表优先级：方块级 state 覆盖（如陶瓦 16 色）> `state_overrides`（如 `color="red"` 羊毛 → 红色、`wood_type`）> 方块 ID 基色 > **变体材质回退**（台阶/楼梯/墙/栅栏/门/告示牌等剥掉变体后缀后按材质归类，如 `spruce_stairs` → 云杉木色、`iron_door` → 铁色）> 默认灰色（`--map-colors` 可提供自定义表，例如行为包方块）；
- 默认开启 Minecraft 风**面着色**：顶面亮、侧面中、底面暗（`--no-texture-shade` 关闭）；
- 内置颜色表以[基岩版地图基色表](https://comeixalpha.github.io/ref/mapcolors/)（770 个方块 ID）为底，经 Minecraft Wiki 交叉校验修正，可用 `tools/generate_map_colors.py --from-ref/--from-wiki/--from-blocks` 重新生成；
- 局限性：地图色是去饱和扁平色，适合预览/占位/骨架，不能替代真实纹理；
  草、短草、蕨、珊瑚、树叶、藤蔓、水在 Bedrock 地图上按生物群系染色，
  表中统一采用**平原群系**（默认）色：grass `#7cbd6b`、foliage `#77ab2f`、water `#3f76e4`；
  自定义方块需自行提供颜色表。

---

## 限制与说明

- `.mcstructure` 的实体列表与方块实体数据（箱子、告示牌内容等）无法映射到几何 JSON，反向转换也无法凭空恢复，会丢失。
- 几何中的旋转/镜像不参与体素化。
- 生成的 `.mcstructure` 为未压缩小端 NBT，与游戏导出的文件格式一致，可直接放入行为包 `structures/` 目录，或通过结构方块/`/structure` 命令加载。
- 超过游戏结构方块上限 `64×384×64` 时会给出警告但不会阻止生成；外部生成的结构通常仍能加载。

---

## 测试

```bat
python -m unittest discover -s tests -v
```

测试覆盖：方块索引顺序（Bedrock ZYX 顺序）、骨骼名编码往返、真实 `.mcstructure` 样例往返、副层（含水方块）、负坐标归一化、CLI 双向转换等。

---

## 格式参考

- Bedrock Wiki: [.mcstructure](https://wiki.bedrock.dev/nbt/mcstructure.html)
- tryashtar: [Bedrock mcstructure file format](https://gist.github.com/tryashtar/87ad9654305e5df686acab05cc4b6205)
- Bedrock Wiki: [Block Modeling / Geometry](https://wiki.bedrock.dev/visuals/bedrock-modeling)

`samples/` 目录中的真实结构文件来自 [phoenixr-codes/mcstructure](https://github.com/phoenixr-codes/mcstructure)（MIT License，见 `SAMPLES-LICENSE.txt`）。

---

## 许可证

本项目以 [MIT 许可证](LICENSE) 开源（Copyright © 2026 INTZOT）。
`samples/` 目录中第三方样例的许可见 `SAMPLES-LICENSE.txt`。
