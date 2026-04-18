# CUA-Lark

基于 macOS 截图、Accessibility 和视觉大模型的飞书桌面端 GUI Agent。

当前主链路已经收敛为视觉优先闭环：

`observe -> think -> act -> verify`

其中：

- 视觉模型是主判断源
- planner 只提供高层目标和候选路径
- verifier 以视觉验证为主，AX/规则只做辅助证据
- guardrail / recovery 只做全局兜底

## 当前结构

- `agent/planner.py`
  高层任务规划器，只输出：
  - `goal`
  - `preferred_path`
  - `fallback_path`
  - `expected_transition`
- `agent/decision_engine.py`
  视觉决策器，负责 `think`
- `agent/vision_loop.py`
  主协调器，负责 `observe -> think -> act -> verify`
- `verification/transition_verifier.py`
  视觉优先验证器，负责动作后迁移判断和任务完成判断
- `agent/perception_fusion.py`
  截图、AX、SoM 融合
- `execution/action_executor.py`
  动作执行
- `agent/guardrail.py`
  全局安全兜底
- `execution/recovery.py`
  脱困和回安全状态
- `ui/quick_command_window.py`
  极简前台小窗口入口

## 当前能力

更适合这类飞书桌面端任务：

- 打开消息、日历、云文档等常见模块
- 打开某个群聊 / 会话
- 简单输入、发送、搜索类任务
- 以当前飞书界面为基础的短流程导航任务

当前不追求：

- 复杂多页面长流程自动化
- 浏览器/系统设置/飞书外部网站联动
- 完整控制台式调度界面

## 环境要求

- macOS
- Python 3.10+
- 已安装并登录飞书桌面客户端
- 当前终端具备：
  - 辅助功能权限
  - 屏幕录制权限

建议使用虚拟环境：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 依赖

关键依赖包括：

- `openai==1.40.0`
- `httpx<0.28`
- `pyautogui`
- `Pillow`
- `pyobjc-framework-ApplicationServices`
- `pyobjc-framework-Cocoa`
- `pyobjc-framework-Quartz`
- `gradio`

如果环境里 `httpx` 被升级到 `0.28+`，建议重新固定：

```bash
pip install --upgrade "openai==1.40.0" "httpx<0.28"
```

## 模型配置

项目通过 OpenAI 兼容接口调用模型，支持官方接口和中转站。

建议用环境变量配置：

```bash
export OPENAI_API_KEY="你的 key"
export OPENAI_BASE_URL="https://你的中转站/v1"
export OPENAI_MODEL="gpt-4o"
export OPENAI_PLANNER_MODEL="gpt-4o"
```

说明：

- `OPENAI_BASE_URL` 可为空；为空时走官方默认地址
- 如果使用中转站，通常需要带 `/v1`
- 视觉决策和验证依赖多模态模型，推荐 `gpt-4o`

默认值定义在 `config.py`。

## 首次运行前

1. 打开飞书并确保已登录
2. 给当前终端开启 `辅助功能`
3. 给当前终端开启 `屏幕录制`
4. 如系统弹出 `自动化` 授权，允许终端控制 `System Events` 和飞书

如果权限不完整，常见现象包括：

- `osascript 不允许辅助访问`
- 窗口标准化失败
- 读不到 AX 元素
- 键鼠操作发不到飞书前台

## 运行方式

### 环境检查

```bash
python3 main.py --check
```

检查项包括：

- 辅助功能权限
- 屏幕录制权限
- Pillow 依赖
- 飞书是否运行
- 窗口是否可标准化
- `OPENAI_API_KEY` 是否存在

### 打印飞书 AX 树

```bash
python3 main.py --dump-ax
```

### 执行单个任务

```bash
python3 main.py --task "打开日历页面"
python3 main.py --task "打开大群聊天"
python3 main.py --task "给张三发消息：明天下午3点开会"
```

### 默认启动方式

```bash
python3 main.py
```

默认会启动极简前台小窗口，不再走命令行单次输入。

### 显式启动极简前台小窗口

```bash
python3 main.py --ui
```

### 交互模式

```bash
python3 main.py --interactive
```

### 极简前台小窗口

```bash
python3 ui/quick_command_window.py
```

这个窗口：

- 始终置顶
- 只有任务输入框、发送按钮、一行状态文本
- 后台线程执行 agent，不会阻塞 UI
- `python3 main.py` 和 `python3 main.py --ui` 都会进入这个窗口

状态只有：

- `空闲`
- `执行中`
- `完成`
- `失败：xxx`
- `需要接管：xxx`

### 启动优先级

- `python3 main.py --task "..."`：直接执行单个任务，不弹窗口
- `python3 main.py --interactive`：进入命令行交互模式
- `python3 main.py --ui`：显式启动极简前台小窗口
- `python3 main.py`：默认启动极简前台小窗口

### Gradio Demo

```bash
python3 demo.py
```

当前 Demo 只返回结果 JSON，不展示中间步骤控制台。

## 图片识别单测

单独验证当前模型/中转站是否支持图片输入：

```bash
python3 test_vision.py ./1.png
```

也可以自定义提示词：

```bash
python3 test_vision.py ./1.png --prompt "请识别这个飞书界面里当前在哪个模块，并指出可点击的入口。"
```

## Planner

当前 planner 已降级为高层策略提供者，不再输出执行步骤。

plan 只包含：

- `goal`
- `preferred_path`
- `fallback_path`
- `expected_transition`
- `feasible`
- `confidence`
- `reasoning`
- `risk_notes`

`expected_transition` 使用统一结构：

```json
{
  "from": "current",
  "to": "<canonical_page_or_state>",
  "target_page": "<canonical_page_or_state>",
  "target_name": "<optional_target_name>",
  "text": "<human_readable_goal>"
}
```

说明：

- `to` / `target_page` 使用系统内部 canonical page/state id
- planner 只做少量高层结构化，不承担完成判断，不决定具体点击哪个控件

## 视觉主循环

主循环位于 `agent/vision_loop.py`。

实际流程是：

1. `observe`
   - 获取当前截图
   - 视需要融合 AX / SoM
2. `think`
   - 由 `agent/decision_engine.py` 调视觉模型判断当前状态和下一步动作
3. `act`
   - 由 `execution/action_executor.py` 执行动作
4. `verify`
   - 由 `verification/transition_verifier.py` 统一做动作后迁移判断和任务完成判断

现在主循环不再依赖：

- plan steps
- 固定 step type 驱动
- scattered completion patch
- 到处散落的 done 判定

## Verifier

当前 verifier 是视觉优先验证器。

核心职责只有两个：

- 动作后是否发生预期状态迁移
- 当前任务是否已完成

统一输出字段包括：

- `state`
- `transition`
- `step_completed`
- `task_completed`
- `confidence`
- `evidence`
- `next_step_hint`

原则：

- 视觉结果是主事实来源
- AX / `expected_transition` / 规则只作为辅助证据
- 弱辅助证据不能推翻高置信视觉事实

## Guardrail

`agent/guardrail.py` 现在只做全局安全兜底，不再做页面语义判断。

主要消费结构化字段：

- `step_num`
- `decision.confidence`
- `decision.current_page`
- `decision.action`
- `verify_result.task_completed`
- `verify_result.step_completed`
- `verify_result.progress_made`
- `verify_result.next_step_hint`
- `verify_result.post_action_state`

主要处理：

- 总步数过高
- 连续 low confidence
- 连续无 `progress_made`
- 连续 retry 且动作/状态不变
- 连续 unknown/other 页面
- 重复动作过多

注意：

- 如果 `verify_result.task_completed = true`，guardrail 完全放行
- “重复动作过多”现在优先 `REPLAN`
- `RECOVER` 只用于明显异常状态，例如：
  - 连续未知页面
  - 失焦
  - 弹窗/遮挡
  - 空白/异常窗口

## Recovery

`execution/recovery.py` 只负责：

- 脱困
- 回到安全状态

不负责：

- step 完成判断
- task 完成判断
- verifier 级别页面语义解释

返回状态只有：

- `recovered`
- `retryable`
- `need_replan`
- `handoff`

## Perception

`agent/perception_fusion.py` 现在是简单的最小复用模型，不做复杂缓存。

### 当前模式

- `observe_light()`
  - screenshot only
  - 不跑 AX
  - 不跑 SoM
- `observe_structured()`
  - screenshot + AX
  - 不跑 SoM
- `observe_annotated()`
  - screenshot + AX + SoM

默认 `perceive()` 现在是轻量模式：

- `with_som=False`
- `with_ax=False`

### 字段语义

`FusedPerception` 里保留这些审计字段：

- `capture_source`
  - `fresh`：当前 capture 第一次被融合
  - `reused`：同一 capture 对象再次复用
- `capture_duration_ms`
  - 截图本身耗时
- `perception_duration_ms`
  - 基于已有 capture 做 AX / SoM / 坐标系构建的耗时
- `ax_enabled`
- `som_enabled`

### 缓存范围

当前缓存只在“同一 capture 对象”内生效：

- `_capture_key = id(screen_data)`
- 不是内容级缓存
- 不是跨截图缓存
- 不是跨步骤历史缓存

## 窗口与截图

窗口管理在 `utils/window_manager.py`：

- 识别 `Lark / 飞书 / Feishu`
- 启动时尝试激活并标准化窗口
- 默认目标窗口模式是 `centered_ratio`

截图逻辑在 `perception/screen_capturer.py`：

- macOS `screencapture`
- 优先窗口级截图
- 带 Retina 修正

注意：

- 截图不等于可交互
- 鼠标键盘动作仍建议飞书处于前台
- 执行期间不要继续手动操作鼠标键盘

## 运行产物

每次运行会在 `runs/<时间戳>/` 下生成：

- `step_01.png`、`step_02.png`：每步截图
- `step_01_som.png`：如有 SoM 标注则保存
- `trace.json`：完整执行轨迹

`trace.json` 当前包含：

- `plan`
- 每一步的：
  - `observation`
  - `thinking`
  - `action_decided`
  - `action_executed`
  - `verification`
  - `duration`
- 顶层：
  - `success`
  - `error`
  - `handoff_required`
  - `handoff_reason`
  - `vision_calls`
  - `total_tokens`

## 测试与校验

语法检查：

```bash
PYTHONPYCACHEPREFIX=/tmp/cua-lark-pyc python3 -m py_compile $(rg --files -g '*.py')
```

## 目录说明

- `main.py`：CLI 入口
- `demo.py`：Gradio Demo
- `ui/quick_command_window.py`：极简前台小窗口
- `agent/`：planner、decision engine、vision loop、guardrail、perception fusion、state schema
- `perception/`：截图、AX、SoM、视觉模型接口
- `execution/`：动作执行、恢复
- `verification/`：视觉优先验证器
- `utils/`：窗口管理、坐标、OpenAI 客户端等
- `runs/`：运行产物

## 当前限制

- 仅支持 macOS
- 强依赖飞书桌面端当前 UI
- 视觉链路依赖远程模型，速度明显慢于固定脚本
- planner 已经收敛为高层策略，但高层文本结构化仍有少量关键词规则
- verifier 虽然已改成视觉优先，但任务稳定性仍取决于模型对当前飞书截图的判断质量
- 当前环境下如果缺少 `Pillow` / `pyautogui` / macOS 权限，主链路无法正常运行
- 仍更适合短流程任务，不适合复杂长链事务
