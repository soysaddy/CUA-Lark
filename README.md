# CUA-Lark

基于视觉模型的飞书桌面端自动化 Agent。

主流程：

`observe -> think -> act -> verify`

## 功能概览

- 打开飞书常见模块，如消息、日历、云文档
- 打开群聊或联系人会话
- 简单搜索、输入、发送类任务
- 以当前飞书界面为基础的短流程桌面操作

## 项目架构

主链路：

1. `observe`
   - 获取飞书窗口截图
   - 按需融合 AX / SoM 信息
2. `think`
   - 视觉模型判断当前状态并决定下一步动作
3. `act`
   - 执行点击、输入、滚动、快捷键等动作
4. `verify`
   - 视觉优先验证动作后状态迁移和任务完成情况

运行时逻辑：

- 启动后先生成高层计划，只提供目标和候选路径
- 每一轮主循环都重新截图并重新判断当前界面，不依赖固定脚本步骤
- 如果视觉模型判断任务已经完成，会直接收口，不继续多余点击
- 如果动作后没有确认完成，会进入下一轮重新观察，而不是盲目重复旧动作

核心模块：

- `agent/planner.py`
  - 高层任务规划，只输出 `goal / preferred_path / fallback_path / expected_transition`
- `agent/decision_engine.py`
  - 视觉决策器，负责下一步动作选择
- `agent/vision_loop.py`
  - 主循环协调器
- `verification/transition_verifier.py`
  - 动作后验证器
- `agent/perception_fusion.py`
  - 截图、AX、SoM、坐标系统融合
- `execution/action_executor.py`
  - 动作执行
- `agent/guardrail.py`
  - 全局安全兜底
- `execution/recovery.py`
  - 脱困和回安全状态
- `ui/quick_command_window.py`
  - 极简前台输入窗口

## 环境要求

- macOS
- Python 3.10+
- 飞书桌面端
- macOS 权限：
  - 屏幕录制权限
  - 辅助功能权限

## 安装

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 模型配置

通过环境变量配置：

```bash
export OPENAI_API_KEY="你的 key"
export OPENAI_BASE_URL="https://你的中转站/v1"
export OPENAI_MODEL="gpt-4o"
export OPENAI_PLANNER_MODEL="gpt-4o"
```

说明：

- `OPENAI_BASE_URL` 可留空，留空时使用默认地址
- 推荐使用支持图片输入的多模态模型

## 运行方式

### 默认启动极简窗口

```bash
python3 main.py
```

### 显式启动极简窗口

```bash
python3 main.py --ui
```

### 直接执行单个任务

```bash
python3 main.py --task "打开日历页面"
```

### 命令行交互模式

```bash
python3 main.py --interactive
```

### 单独启动极简窗口

```bash
python3 ui/quick_command_window.py
```

### Gradio Demo

```bash
python3 demo.py
```

## 极简窗口

窗口只包含：

- 任务输入框
- 发送按钮
- 一行状态文本

状态文本只显示：

- 空闲
- 执行中
- 完成
- 失败：xxx
- 需要接管：xxx

## 感知模式

`agent/perception_fusion.py` 提供三种模式：

- `observe_light()`
  - 只截图
- `observe_structured()`
  - 截图 + AX
- `observe_annotated()`
  - 截图 + AX + SoM

主循环默认优先使用轻量或结构化感知，只有需要更强定位和标注时才使用带 SoM 的感知结果。

## 动作执行逻辑

常见动作类型包括：

- `click`
- `double_click`
- `right_click`
- `type`
- `hotkey`
- `scroll`
- `wait`

点击类动作采用候选点顺序执行：

- 视觉模型会给出主点击点，必要时给出最多 3 个候选点
- 执行层按顺序尝试候选点
- 每次点击后立即验证是否产生有效变化
- 候选点全部失败后，本轮点击止损，不会无限重试

非点击动作按单步执行：

- 输入：执行一次后进入验证
- 快捷键：执行一次后进入验证
- 滚动：执行一次后进入验证

如果点击动作没有模型坐标，执行层才会尝试一次 AX / SoM 辅助补点；不会进行多轮复杂补救。

## 验证与兜底

验证阶段主要判断两件事：

- 当前动作后是否发生了预期状态迁移
- 当前任务是否已经完成

如果验证明确认为任务已完成，主循环会直接结束。

如果连续多轮没有有效进展，才会进入兜底逻辑：

- `guardrail`
  - 处理明显死循环、连续未知页面、重复动作过多、超步数
- `recovery`
  - 只负责脱困和回到安全状态

正常情况下，`guardrail` 和 `recovery` 不参与页面语义判断。

## 运行产物

每次运行会在 `runs/<时间戳>/` 下生成：

- 步骤截图
- 可选 SoM 标注图
- `trace.json`

`trace.json` 会在每个 step 完成后增量更新。

其中常见内容包括：

- 当前任务 plan
- 每一步的观察、思考、动作、验证结果
- 点击候选点尝试记录
- 当前是否完成、是否需要接管、错误原因

## 校验

```bash
python3 -m py_compile $(rg --files -g '*.py')
```

## 当前限制

- 仅支持 macOS
- 仅面向飞书桌面端
- 更适合短流程任务
- 视觉链路依赖远程模型，速度慢于固定脚本
- 执行时应避免手动干预飞书窗口
