# auto_test

## 配置文件

仅保留以下配置入口：
- 解析配置：`config/parser/targets.json`
- 解析策略：`config/parser/profiles.json`
- 用例生成配置：`config/casegen/targets.json`

### 解析配置（`config/parser/targets.json`）
核心字段：
- `type_files`: 类型定义输入（文件/目录/glob）
- `interface_files`: 接口定义输入（文件/目录/glob）
- `target_interfaces`: 待解析接口列表
- `output.dir`: 解析结果目录
- `output.filename_format`: 解析结果文件名模板
- `output.mode`: `full` 或 `simple`
- `output.scope`: `all` 或 `selected`
- `variation`: 变化变量筛选规则
- `includes`: 通常为 `profiles.json`

### 策略配置（`config/parser/profiles.json`）
核心字段：
- `basic_type_profiles`
- `variable_profiles`
- `interface_profiles`

常用子字段：
- `seed_pool`
- `illegal_values`
- `boundary_values`
- `value_range`

### 用例生成配置（`config/casegen/targets.json`）
核心字段：
- `includes`: 引入解析配置和策略配置
- `case_generation.dir`: 用例输出目录
- `case_generation.filename_format`: 用例文件名模板
- `case_generation.variable_scope`: `selected` 或 `all`
- `case_generation.mode`: `full` 或 `simple`
- `case_generation.extra_variables`: 全局额外变量（接口外参数）
- `case_generation.interface_extra_variables`: 接口级额外变量
- `case_generation.constraint_groups`: 多参数组合约束组（可选）
- `case_generation.case_count`: 正整数或 `all`
- `case_generation.random_seed`: 随机种子

`constraint_groups` 结构：
- `name`: 约束组名称
- `variables`: 该组包含的变量路径列表
- `combinations`: 允许的组合列表（每项是 `{变量路径: 值}`）

示例（把两个参数打包为有限合法组合）：

```json
"constraint_groups": [
  {
    "name": "base_and_struct0",
    "variables": [
      "align_scan_base.a",
      "align_scan_periodic.other_struct[0].some_field"
    ],
    "combinations": [
      {
        "align_scan_base.a": "2",
        "align_scan_periodic.other_struct[0].some_field": "0"
      },
      {
        "align_scan_base.a": "6",
        "align_scan_periodic.other_struct[0].some_field": "1"
      }
    ]
  }
]
```

说明：
- 该组中的变量不会再做彼此笛卡尔积，只使用 `combinations` 里定义的组合。
- 未在任何组中的变量，仍按原本候选值自由组合。

额外变量配置（接口外参数）：
- `extra_variables`: 全局生效
- `interface_extra_variables.<接口名>`: 只对指定接口生效

每个额外变量支持两种候选值来源：
1. 自定义：`candidates` 或 `seed_pool`
2. 文件规则推导：设置 `basic_type/source_type`（可选 `from_profile: true`），从 `profiles.json` 对应规则生成候选值

额外变量类型字段建议：
- `type_name`: 推荐，填写 C 声明类型名（支持 typedef，自动归约到基础类型）
- `source_type`: 可选，直接指定源类型名（会尝试归约）
- `basic_type`: 可选，手动指定基础类型（不做归约）

示例：

```json
"extra_variables": [
  {
    "name": "manual_mode",
    "candidates": ["0", "1", "2"]
  },
  {
    "name": "manual_level",
    "type_name": "SMEE_INT32",
    "from_profile": true
  }
],
"interface_extra_variables": {
  "QA4A_request_align_periodic": [
    {
      "name": "manual_tag",
      "seed_pool": ["\"A\"", "\"B\""]
    }
  ]
}
```

## 命令与选项

在 `auto_test` 根目录执行。

### 1) 解析命令

```bash
python src/interface_parser/parse_interface.py --config config/parser/targets.json
```

选项：
- `--config <path>`: 必填，配置文件路径
- `--output <path>`: 覆盖输出路径
- `--mode <full|simple>`: 覆盖 `output.mode`
- `--scope <all|selected>`: 覆盖 `output.scope`
- `--simple`: 强制 `simple`（优先级最高）

优先级：
- `mode`: `--simple` > `--mode` > `output.mode`
- `scope`: `--scope` > `output.scope`

示例：

```bash
python src/interface_parser/parse_interface.py --config config/parser/targets.json --mode full --scope selected
python src/interface_parser/parse_interface.py --config config/parser/targets.json --simple
```

### 2) 测试用例生成命令

```bash
python src/casegen/generate_test_cases.py --config config/casegen/targets.json
```

选项：
- `--config <path>`: 必填，配置文件路径
- `--output <path>`: 覆盖输出路径
- `--scope <all|selected>`: 覆盖 `case_generation.variable_scope`
- `--mode <full|simple>`: 覆盖 `case_generation.mode`
- `--case-count <N|all>`: 覆盖 `case_generation.case_count`
- `--seed <int>`: 覆盖 `case_generation.random_seed`
- `--simple`: 强制简单报告（仅 `id + inputs`）

优先级：
- `mode`: `--simple` > `--mode` > `case_generation.mode`

生成策略：
- `--case-count all`: 全组合
- `--case-count N`: 当 `N` 小于全组合数时，无放回随机采样
- 输出模式：
  - `full`: 保留统计、变量候选、`combination_index` 等完整信息
  - `simple`: 仅保留测试编号和变量赋值（`id` + `inputs`）

示例：

```bash
python src/casegen/generate_test_cases.py --config config/casegen/targets.json --case-count 200
python src/casegen/generate_test_cases.py --config config/casegen/targets.json --scope all --case-count all
python src/casegen/generate_test_cases.py --config config/casegen/targets.json --simple
```
