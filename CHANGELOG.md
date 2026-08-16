# 更新日志（Changelog）

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

版本号与 `mc_geo_converter.py` 中的 `__version__` 保持一致。

## [Unreleased]

- 项目以 [MIT 许可证](LICENSE) 开源（Copyright © 2026 INTZOT）。
- `to-geo` 新增 `--map-color-texture`：按方块地图色自动生成色块贴图图集
  （`.png` + per-face UV），内置 `data/map_colors.json` 颜色表，支持 Minecraft 风面着色；
  GUI 同步增加「生成 map-color 贴图」开关。
- 颜色表以[基岩版地图基色表](https://comeixalpha.github.io/ref/mapcolors/)（770 个方块 ID）
  为底重新生成，经 Minecraft Wiki 交叉校验：
  - 新增方块级覆盖（`block_overrides`）：陶瓦 16 色与羊毛不同（如红陶瓦 `#8e3c2e`）；
  - 修正紫系为基岩版值 `#995acd`，采纳基岩版专属差异（水 `#1e5af5`、草方块 `#92bc58`、
    sculk `#0d1217`、紫水晶 `#995acd`、床 `#993333` 等）；
  - 拒绝参考表错误条目（珊瑚误为草绿、玻璃板/红石灯透明等），保留 wiki 交叉验证值；
  - 生成器新增 `--from-ref` 模式。
- 经 [mcpixelart.com](https://mcpixelart.com/pixelart) 纹理数据第三源校验：
  - 基表扩充至 816 项（补入 1.21.x 新方块：pale_oak 家族、树脂、creaking_heart、
    铜系 waxed 变体、湿海绵等 46 项）；
  - 三源裁决修正 10 项：紫系改回 `#7f3fb2`、草方块 `#7fb238`、sculk 系 `#191919`、
    磁石 `#a7a7a7`、标靶 `#fffcf5`、去皮金合欢木 `#d87f33`；
  - pale_oak 木板色定为 `#fffcf5`。

## [1.1.0] - 2026-08-17

首次纳入 Git 版本控制（更早的版本历史未保留，此为当前功能基线）。

### 转换能力

- `.mcstructure` → `.geo.json`：结构转几何（`to-geo`）
  - 主层每个非空气方块 → `1×1×1` cube，同方块（含相同 states）合并为一个骨骼；
  - 骨骼名编码方块 ID 与 states（NBT 类型经后缀保留，可无损往返）；
  - 支持 `--scale` 等比缩放、`--include-air` / `--include-secondary` / `--include-origin`。
- `.geo.json` → `.mcstructure`：几何转结构（`to-structure`）
  - 骨骼 → 方块类型，cube 体素化为整数网格（`--snap floor|round`，支持 `inflate`）；
  - 结构整体平移至最小角落在 `(0,0,0)`，空位填 `minecraft:air` / `-1`；
  - `secondary:` 前缀骨骼写入副层（还原含水方块）；`--voxel-size` 与 `--scale` 互为逆运算。
- 命令行与 tkinter 图形界面（`start_gui.bat`）双入口；按扩展名自动识别转换方向。

### 工程

- 依赖仅 `nbtlib`（纯 Python，读写小端 NBT），Python 3.9+；
- `tests/test_converter.py` 自检测试（`python -m unittest discover -s tests -v`）；
- `samples/` 真实游戏样例（MIT，见 `SAMPLES-LICENSE.txt`）。

### 已知限制

- 实体与方块实体数据（箱子、告示牌等）无法映射到几何 JSON，转换时丢弃；
- 几何的 `rotation` / `mirror` 不参与体素化；
- 结构大小超过游戏上限 `64×384×64` 时仅警告、不阻止生成。
