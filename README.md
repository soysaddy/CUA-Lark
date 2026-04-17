# CUA-Lark

基于 macOS Accessibility、窗口截图和视觉决策的飞书桌面端自动化项目。

当前实际入口是视觉 Agent：

- 任务规划：`agent/planner.py`
- 感知融合：`agent/perception_fusion.py`
- 主执行循环：`agent/vision_loop.py`
- 动作执行：`execution/`
- 护栏与恢复：`agent/guardrail.py`、`execution/recovery.py`

## 当前能力

- 自然语言下发飞书桌面端任务
- 结合截图、AX 信息和视觉模型决定下一步 GUI 操作
- 环境检查、窗口标准化、AX 树导出
- Gradio Demo
- 单独验证 `gpt-4o` 图片识别

当前更适合这类任务：

- 打开消息、文档、日历等常见模块
- 简单导航和输入类操作
- 基于当前飞书界面的短流程任务
- 遇到需要人工确认的步骤时中止并交还给用户

当前 planner 更偏“策略化规划”，不是固定脚本模板：

- plan 顶层可包含 `preferred_path`、`fallback_path`、`expected_transition`
- step 里会尽量保留结构化目标，而不是把 verifier 规则写死到规划里
- 消息场景通常表达为：
  - 首选路径：列表直达
  - 备选路径：搜索定位
  - 期望迁移：`im_main -> im_chat(target)`

## 环境要求

- macOS
- Python 3.10+
- 已安装并登录飞书桌面客户端
- 终端具备以下系统权限：
  - 辅助功能
  - 屏幕录制

建议使用虚拟环境：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 依赖

当前关键依赖：

- `openai==1.40.0`
- `httpx<0.28`
- `pyautogui`
- `Pillow`
- `pyobjc-framework-ApplicationServices`
- `pyobjc-framework-Cocoa`
- `pyobjc-framework-Quartz`
- `gradio`

如果你已经装过依赖，建议确认 `httpx` 没有被升级到 `0.28+`：

```bash
pip install --upgrade "openai==1.40.0" "httpx<0.28"
```

## 模型配置

项目通过 `OpenAI` 兼容接口调用模型，支持官方接口和兼容 OpenAI 的中转站。

推荐通过环境变量配置：

```bash
export OPENAI_API_KEY="你的 key"
export OPENAI_BASE_URL="https://你的中转站/v1"
export OPENAI_MODEL="gpt-4o"
export OPENAI_PLANNER_MODEL="gpt-4o"
```

说明：

- `OPENAI_BASE_URL` 可留空；留空时走官方默认地址
- 如果用中转站，通常需要带 `/v1`
- 当前图片理解和视觉决策依赖多模态模型，推荐 `gpt-4o`

项目的实际默认值定义在 [config.py](/Users/saddy/Documents/技术与开发/CUA-Lark/config.py)。

## 首次运行前

1. 打开飞书并确保已登录。
2. 给当前终端开启 `辅助功能`。
3. 给当前终端开启 `屏幕录制`。
4. 如果系统弹出 `自动化` 授权，允许终端控制 `System Events` 和飞书。

如果这些权限不完整，常见现象包括：

- `osascript 不允许辅助访问`
- 窗口标准化失败
- 读不到 AX 元素
- 无法执行键盘和鼠标操作

## 运行方式

### 环境检查

```bash
python3 main.py --check
```

会检查：

- 辅助功能权限
- 屏幕录制权限
- Pillow 依赖
- 飞书是否运行
- 飞书窗口是否可标准化
- `OPENAI_API_KEY` 是否存在

### 打印飞书 AX 树

```bash
python3 main.py --dump-ax
```

用于查看当前飞书页面的辅助功能结构。

### 执行单个任务

```bash
python3 main.py --task "打开云文档模块"
python3 main.py --task "给张三发消息：明天下午3点开会"
```

### 单次交互输入

```bash
python3 main.py
```

程序会提示输入一条任务，然后执行一次。

### 连续交互模式

```bash
python3 main.py --interactive
```

会进入循环输入模式，输入 `quit` / `exit` / `q` 退出。

### 启动 Gradio Demo

```bash
python3 demo.py
```

默认会启动本地页面，并创建一个临时 `gradio.live` 分享地址。

当前返回结果 JSON 除了成功与否，还会包含：

- `handoff_required`
- `handoff_reason`

当模型判断当前步骤必须由用户处理时，会返回人工接管状态，而不是继续盲目操作。

## 图片识别单测

验证当前模型和中转站是否支持图片输入，可以单独跑：

```bash
python3 test_vision.py ./1.png
```

也可以自定义提示词：

```bash
python3 test_vision.py ./1.png --prompt "请识别这个飞书界面里当前在哪个模块，并指出可点击的入口。"
```

这一步不会驱动飞书，只会测试：

- `OPENAI_API_KEY`
- `OPENAI_BASE_URL`
- 当前模型是否支持多模态图片输入

## 运行产物

每次运行会在 `runs/<时间戳>/` 下生成结果：

- `step_01.png`、`step_02.png`：每一步截图
- `step_01_som.png`：带标注的 SoM 图
- `trace.json`：完整执行轨迹

CLI 结束时会输出执行状态；详细轨迹可以直接查看 `runs/`。

`trace.json` 里当前会额外记录：

- 每一步的 `verification`
- 如触发恢复，会记录 `verification.recovery`
- 如进入人工接管，会记录 `handoff_required` 和 `handoff_reason`
- 每一步的 `verification.perception_diag`，用于区分：
  - `screenshot_size`
  - `raw_size`
  - `resized_size`
  - `window_bounds`
  - `capture_source`
  - `size_assessment`

## 窗口与截图说明

当前窗口相关逻辑在 [utils/window_manager.py](/Users/saddy/Documents/技术与开发/CUA-Lark/utils/window_manager.py)：

- 会识别 `Lark / 飞书 / Feishu`
- 启动时尝试激活并标准化飞书窗口
- 默认目标窗口模式是 `centered_ratio`
- 标准化成功判定同时检查 `x / y / width / height`

当前截图逻辑在 [perception/screen_capturer.py](/Users/saddy/Documents/技术与开发/CUA-Lark/perception/screen_capturer.py)：

- 全屏截图使用 macOS `screencapture`
- 飞书窗口截图优先使用窗口级截图 `screencapture -l <window_id>`
- 截图后会做 Retina 尺寸修正

注意：

- 截图不等于可交互；即使能截到飞书，执行点击和热键时仍建议让飞书处于前台
- 任务执行期间不要继续操作鼠标键盘
- 恢复动作执行前会先尝试把飞书切回前台；如果失败，会停止自动恢复并转入人工接管

感知融合在 [agent/perception_fusion.py](/Users/saddy/Documents/技术与开发/CUA-Lark/agent/perception_fusion.py)：

- `capture_screen()` 只负责拿 `screen_data + bounds`
- `perceive_from_capture()` 负责基于已有截图做 AX / SoM / 坐标系构建
- 同一份 `screen_data` 会尽量复用，避免同一步重复 enrich
- `annotated_b64` 改为惰性生成，只有确实要发 SoM 标注图给模型时才编码
- `FusedPerception` 里当前会保留最小审计字段：
  - `capture_source: fresh / reused`
  - `ax_enabled`
  - `som_enabled`

## 已知行为

- 当前主流程是通用视觉 Agent，不是“固定脚本点击器”，简单任务也可能走多轮截图、规划、验证和恢复
- planner 当前更接近“候选路径 + 期望状态迁移”，执行层会结合当前状态决定是否走列表、导航或搜索 fallback
- `RecoveryManager` 已改为状态驱动恢复，不再只是固定热键脚本
- recovery 的目标是回到飞书的“已知安全状态”，当前安全页包括：`im_main`、`im_chat`、`calendar`、`docs`、`search`
- recovery 每执行一步动作后都会重新做状态检查，状态至少包含：
  - 当前页面
  - 页面置信度：`confirmed` / `inferred` / `unknown`
  - 是否前台
  - 是否存在弹窗
- recovery 的结果当前分为：
  - `recovered`
  - `retryable`
  - `need_replan`
  - `handoff`
- 只有页面状态被本轮真实观测为 `confirmed` 时，才会直接判定为 `recovered`
- 如果视觉验证不能确认“步骤已完成”，可能出现重复截图、重规划或恢复
- 如果出现登录、验证码、系统权限、人工确认等步骤，模型可以返回 `pause_for_user`，主循环会停止并要求人工接管
- 对“截图过小 / 看不清 / 低置信度”这类系统性感知失败，主循环现在不会立即 handoff，而是优先：
  - 重拍 / 重感知一次
  - 尝试消费 planner 的搜索 fallback
  - 连续失败后才 handoff
- 这类最终 handoff 文案已改成内部原因，例如：
  - `当前窗口尺寸异常，已重试和 fallback 仍无法确认目标`
  - `已重试和 fallback 仍无法确认目标，当前更适合由用户接管`
- `demo.py` 返回的是结果 JSON，不展示中间每一步的可视化过程

动作后验证当前由 [verification/transition_verifier.py](/Users/saddy/Documents/技术与开发/CUA-Lark/verification/transition_verifier.py) 统一输出：

- `status: confirmed / inferred / unknown / failed`
- `transition`
- `next_step_hint: retry / wait / reobserve / replan / handoff`
- `template`
- `target_name`

当前已落地的模板包括：

- `navigation_to_page`
- `list_item_to_detail`
- `search_result_open`
- `search_overlay`
- `dialog_visibility`
- `input_edit`

## 测试与校验

语法检查：

```bash
PYTHONPYCACHEPREFIX=/tmp/cua-lark-pyc python3 -m py_compile $(rg --files -g '*.py')
```

## 目录说明

- `main.py`：CLI 入口
- `demo.py`：Gradio Demo
- `config.py`：全局配置
- `agent/`：当前主执行链，包含规划、感知融合、护栏和视觉循环
- `perception/`：AX、截图、SoM、视觉识别
- `execution/`：动作执行、输入、恢复
- `verification/`：状态迁移验证
- `utils/`：窗口管理、OpenAI 客户端、坐标工具
- `runs/`：运行产物

## 当前限制

- 仅支持 macOS
- 强依赖飞书桌面端当前 UI 结构
- 视觉链路依赖远程模型，请求耗时会明显高于固定脚本
- planner 虽然已改成更通用的策略表达，但执行层对 `preferred_path / fallback_path` 的消费仍以消息场景为主
- 中转站如果对 `json_object`、图片消息格式或多模态支持不完整，会导致视觉决策失败
- 文档、日历等任务仍偏 MVP，稳定性依赖本机界面和账号状态
- recovery 虽然已改为状态驱动，但页面识别仍依赖 AX 和视觉结果，复杂弹窗或异常页面仍可能进入 `need_replan` 或 `handoff`
