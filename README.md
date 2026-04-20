# auto_test

用于自动化测试前置解析：解析 C 接口参数，递归展开结构体字段，输出变量基础类型与值域/边界占位。

## 代码结构

核心代码：
- `src/parse_interface.py`: 入口与结果组装
- `src/c_parser.py`: C 声明解析与结构体递归展开
- `src/type_specs.py`: 基本类型父类/子类策略
- `src/configuration.py`: 配置加载与输入文件解析（文件/目录/通配符）

核心配置：
- `config/targets.json`: 任务入口配置（输入文件、接口、输出）
- `config/profiles.json`: 类型策略配置（全局 + 接口级）

## 快速开始

在 `auto_test` 目录执行：

```bash
python src/parse_interface.py --config config/targets.json
```

可选覆盖输出路径：

```bash
python src/parse_interface.py --config config/targets.json --output output/custom.json
```

可选简易输出（仅变量名 + 基础类型）：

```bash
python src/parse_interface.py --config config/targets.json --simple
```

## 执行命令与选项

基础命令：

```bash
python src/parse_interface.py --config config/targets.json
```

命令行选项：
- `-h`, `--help`: 查看帮助信息。
- `--config <path>`: 配置文件路径（必填）。
- `--output <path>`: 覆盖输出文件路径；不传则按 `config.output.*` 生成。
- `--mode <full|simple>`: 覆盖 `output.mode`。
- `--scope <all|selected>`: 覆盖 `output.scope`。
- `--simple`: 强制简易输出模式；会覆盖 `output.mode=full`。

常用组合：

```bash
# 1) 使用配置默认输出（默认 full + all）
python src/parse_interface.py --config config/targets.json

# 2) 指定输出文件名
python src/parse_interface.py --config config/targets.json --output output/custom.json

# 3) 强制 simple（不改配置文件）
python src/parse_interface.py --config config/targets.json --simple

# 4) 命令行指定 full/simple
python src/parse_interface.py --config config/targets.json --mode simple

# 5) 命令行指定 all/selected
python src/parse_interface.py --config config/targets.json --scope selected

# 6) 同时指定范围与模式
python src/parse_interface.py --config config/targets.json --mode full --scope selected
```

说明：
- 输出范围由配置 `output.scope` 控制（`all` / `selected`）。
- 输出详细程度由 `output.mode`、`--mode` 或 `--simple` 控制。
- 优先级：`--simple` > `--mode` > `output.mode`；`--scope` > `output.scope`。

## 输入文件配置

支持 4 种输入键（满足其一即可）：
- `type_files`: 类型定义来源（`struct/enum/typedef`）
- `interface_files`: 接口声明来源（函数原型/定义）
- `source_files`: 同时作为 `type + interface` 来源
- `header_files`: 旧字段，向后兼容

每个字段都支持：
- 单个文件：`data/api/QA_tc.h`
- 目录：`data/common`
- 通配符：`data/api/**/*.h`

`config/targets.json` 示例：

```json
{
  "type_files": [
    "data/common",
    "data/api/QA_tc.h"
  ],
  "interface_files": [
    "data/api/**/*.h"
  ],
  "target_interfaces": [
    "QA4A_request_align_periodic"
  ],
  "output": {
    "dir": "output",
    "filename_format": "parse_result_{interface}_{datetime}.json",
    "mode": "full",
    "scope": "all"
  },
  "includes": "config/profiles.json"
}
```

`output.mode` 可选：
- `full`：完整输出（默认）
- `simple`：简易输出（`name`、`basic_type`，以及 `variation_target` 标记）

`output.scope` 可选：
- `all`：输出全部变量（默认）
- `selected`：仅输出 `variation_target=true` 的变量

说明：
- 每次只生成一个输出报告。
- 报告细粒度由 `output.mode` 控制，报告范围由 `output.scope` 控制。

## 变化变量筛选配置（可选）

如果实际测试只希望变化一部分变量，可在 `targets.json` 增加 `variation`：

```json
"variation": {
  "mode": "mark",
  "variables": [
    "align_scan_base.a",
    "align_scan_periodic.other_struct[*].some_field"
  ],
  "interfaces": {
    "QA4A_request_align_periodic": [
      "align_scan_periodic.b"
    ]
  }
}
```

字段说明：
- `mode`:
  - `mark`: 输出所有变量，并用 `variation_target` 标记是否需要变化
  - `only`: 仅输出需要变化的变量
- `variables`: 全局变量路径匹配规则（支持 `*` 通配）
- `interfaces`: 接口级附加匹配规则，key 为接口名

数组变量匹配建议：
- `align_scan_periodic.other_struct[*].some_field` 可匹配
  `align_scan_periodic.other_struct[0].some_field`、`[1]`、`[2]`...

## 策略配置

`config/profiles.json` 示例（支持类型级 + 变量级）：

```json
{
  "basic_type_profiles": {
    "int": {
      "seed_pool": ["-1", "0", "1", "10"],
      "illegal_values": ["2147483648", "-2147483649"]
    }
  },
  "variable_profiles": {
    "align_scan_periodic_id": {
      "value_range": { "min": 10, "max": 20 },
      "seed_pool": ["10", "15", "20"],
      "illegal_values": ["9", "21"]
    }
  },
  "interface_profiles": {
    "QA4A_request_align_periodic": {
      "basic_type_profiles": {
        "int": {
          "seed_pool": ["2", "6", "7"]
        }
      },
      "variable_profiles": {
        "align_scan_periodic.other_struct[*].some_field": {
          "value_range": { "min": 0, "max": 3 },
          "seed_pool": ["0", "1", "3"],
          "illegal_values": ["-1", "4"]
        }
      }
    }
  }
}
```

可配置字段（类型 profile 与变量 profile 通用）：
- `seed_pool`: 合法值种子池（可选，覆盖默认）
- `illegal_values`: 非法值候选（可选，覆盖默认）
- `boundary_values`: 边界值结构（可选，按 key 深度合并默认）
- `value_range`: 值域覆盖（可选，建议提供 `min/max`，可选 `count`）

兼容说明：`legal_values` 仍支持，但推荐统一使用 `seed_pool`。

变量级路径支持：
- 精确路径：`align_scan_base.a`
- 通配路径：`align_scan_periodic.other_struct[*].some_field`
- 全局默认：`*`

字符串变量特殊规则：
- `char[]` / `signed char[]` / `unsigned char[]`（含 typedef 别名）按 `string` 处理。
- 这类字段不会按数组下标展开，保留为单个变量路径。
- 例如 `S_CHAR some_string[50];` 输出为
  `align_scan_periodic.other_struct[0].some_string`（而不是 `...some_string[0..49]`）。

字符串自定义示例：

```json
{
  "variable_profiles": {
    "align_scan_periodic.other_struct[*].some_string": {
      "seed_pool": ["\"\"", "\"OK\"", "\"A123\""],
      "illegal_values": ["NULL"],
      "boundary_values": {
        "max_len": "\"12345678901234567890\""
      }
    }
  }
}
```

## 覆盖优先级

最终策略按以下顺序合并，后者覆盖前者：
1. `basic_type_profiles["*"]`
2. `basic_type_profiles[basic_type]`
3. `basic_type_profiles[source_type]`
4. `interface_profiles[interface].basic_type_profiles["*"]`
5. `interface_profiles[interface].basic_type_profiles[basic_type]`
6. `interface_profiles[interface].basic_type_profiles[source_type]`
7. `variable_profiles[* / pattern / exact]`
8. `interface_profiles[interface].variable_profiles[* / pattern / exact]`

等价理解：`接口变量级 > 全局变量级 > 接口类型级 > 全局类型级 > 默认级(*)`。

示例（basic_type 覆盖）：
- 全局 `basic_type_profiles.int.seed_pool = ["-1","0","1"]`
- 接口 `QA4A_request_align_periodic` 下 `basic_type_profiles.int.seed_pool = ["2","6","7"]`
- 则该接口里的 `int` 最终取 `["2","6","7"]`，其他接口仍使用全局配置。

示例（source_type 覆盖）：

```json
{
  "basic_type_profiles": {
    "enum(int)": {
      "illegal_values": ["-1"]
    },
    "SS800_CHUCK_ID_ENUM": {
      "illegal_values": ["SS800_CHUCK_ID_MAX"]
    }
  }
}
```

对于 `SS800_CHUCK_ID_ENUM`：最终 `illegal_values` 以 `source_type` 级配置为准（`SS800_CHUCK_ID_MAX`）。

示例（变量级覆盖）：
- 全局 `basic_type_profiles.int.seed_pool = ["-1","0","1"]`
- 接口 `QA4A_request_align_periodic` 下 `basic_type_profiles.int.seed_pool = ["2","6","7"]`
- 接口下 `variable_profiles.align_scan_base.a.seed_pool = ["100","200"]`
- 则 `align_scan_base.a` 最终使用 `["100","200"]`，同接口内其他 `int` 仍使用 `["2","6","7"]`。

## 枚举自动规则

当变量类型是 `typedef enum ... TYPE_NAME;` 时：
- 自动解析枚举成员和成员值（支持显式赋值和顺序自增）
- 自动写入 `value_domain.value_range`：`min/max/count`
- 自动给出合法值与非法值

默认约定：若最后一个成员名满足 `_MAX` / `_ENUM_MAX` / `_NUM`，则视作哨兵值，不放入合法值集合。

## 输出结构说明

输出文件位于 `output/`，每个接口项关键字段如下：
- `interface`: 接口名
- `expanded_variables[]`: 展开后的变量列表
- `expanded_variables[].name`: 变量路径（含结构体与数组路径）
- `expanded_variables[].basic_type`: 基础类型
- `expanded_variables[].variation_target`: 是否属于“需要变化”的变量
- `expanded_variables[].value_domain.source`: 候选值来源（默认/配置/枚举定义）
- `expanded_variables[].value_domain.candidates`: 合法值候选
- `expanded_variables[].value_domain.invalid_candidates`: 非法值候选
- `expanded_variables[].value_domain.value_range`: 当前生效值域（枚举自动推导或配置覆盖）
- `expanded_variables[].boundary_values`: 当前边界值（含配置覆盖结果）
- `summary.output_mode`: 当前输出模式（`full` / `simple`）
- `summary.output_scope`: 当前输出范围（`all` / `selected`）
- `summary.report_scope`: 报告语义范围（`all_variables` / `variation_selected_only`）
- `summary.variation_target_count`: 需要变化的变量总数
- `interface_results[].variation`: 当前接口的变化变量筛选配置快照

说明：默认输出是 `full + all`；`simple` 模式下，`expanded_variables[]` 包含 `name`、`basic_type` 与 `variation_target`。

## data 目录示例

- `data/common/QA_common_types.h`: 公共类型定义
- `data/api/QA_tc.h`: 接口声明
- `data/QA_tc.h`: 兼容入口（仅 `#include "api/QA_tc.h"`）

## 常见问题

`Interface not found in configured interface files`：
- 检查 `target_interfaces` 名称是否与头文件/源码一致。
- 检查 `interface_files` 是否包含该函数所在文件。

`No parseable files matched pattern`：
- 检查通配符路径是否正确（相对 `auto_test` 根目录）。
- Windows 下建议优先使用 `data/**/xxx.h` 形式。

类型未展开或显示为未解析：
- 检查对应 `struct/typedef/enum` 是否包含在 `type_files` 中。

## 兼容说明

- `includes` 支持字符串、数组、旧字典写法。
- 函数参数提取支持声明结尾 `;` 与定义起始 `{`。
- 旧配置 `output.selected_report.enabled=true` 仍可兼容：会自动映射为单报告 `output.scope=selected`。
