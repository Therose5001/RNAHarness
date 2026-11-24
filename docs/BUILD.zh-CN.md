# 从仓库构建 RNAHarness

本指南构建 Python 工具、Node 网关、科研 MCP 与共享 skill。微信和 QQ 通过 OpenClaw 的腾讯官方渠道插件连接到同一个 RNAHarness 科研 MCP。科研 MCP 支持标识符、人类实体、PMID 索引查询和固定发卡对照。演示数据和作业保存在 Git 忽略的 runs 目录。

## 版本与接入范围

| 组件 | 版本与范围 |
| --- | --- |
| 构建环境 | Python 3.12、uv、Node.js 24.16 及以上的 24.x |
| 微信 ClawBot | 腾讯插件 @tencent-weixin/openclaw-weixin 2.4.10，通过手机微信扫码授权。iOS 以 8.0.70 或更新版本为接入起点，Android 以客户端是否显示并能授权 ClawBot 为准 |
| Windows 微信 | 开发机 EVO-X3 实际运行的是 **4.1.7.59**。这是已有桌面环境的记录。仓库中的 ClawBot 渠道使用手机授权 |
| QQ | 腾讯插件 @tencent-connect/openclaw-qqbot 2.0.4，通过 QQ 开放平台 Bot 连接。默认配置只接受明确列出的私聊 OpenID |
| OpenClaw | 2026.11.7 |

[腾讯微信插件](https://github.com/Tencent/openclaw-weixin) · [手机微信版本说明](https://developer.cloud.tencent.com/article/2648243) · [腾讯 QQ Bot 插件](https://github.com/tencent-connect/openclaw-qqbot)

## 1. 从干净仓库构建

在 PowerShell 中执行。跨平台使用时保留相同的 Python、Node 和插件版本。

~~~powershell
git clone https://github.com/RomanCohort/RNAHarness.git
Set-Location RNAHarness
uv sync --python 3.12 --extra research
npm ci --prefix gateway
npm run build --prefix gateway

$rnhStack = uv run --no-sync python scripts/research_stack.py | ConvertFrom-Json
$env:OPENCLAW_CONFIG_PATH = $rnhStack.config
$env:OPENCLAW_STATE_DIR = $rnhStack.state
~~~

初始化建立独立的 OpenClaw 配置、演示实体与文献索引，并复制共享 skill。重复运行保留已有数据库和配置。发卡对照在本机用 ViennaRNA 2.7.2 计算，在 jobs 中留下带参数和版本的作业回执。需要扩充数据时，可用 [实体加载脚本](../scripts/entity_build.py) 和 [文献加载脚本](../scripts/evidence_ingest.py) 构建自己的 SQLite，再在独立配置中设置两项数据库路径。

Bioregistry 查询使用管理员批准的快照。需要这项能力时，在本机提交并审批：

~~~powershell
uv run --no-sync python -m rnaharness.tool_imports --db $rnhStack.registry request https://github.com/biopragmatics/bioregistry
uv run --no-sync python -m rnaharness.tool_imports --db $rnhStack.registry review
~~~

## 2. 接入微信和 QQ

保留上方配置路径。在合适的 Node 版本下安装固定版本的 OpenClaw 和渠道插件。用独立配置运行时，微信和 QQ 的会话在账户、渠道和发送人维度隔离，模型可调用的工具来自科研 MCP。

~~~powershell
npm install -g openclaw@2026.11.7
openclaw plugins install npm:@tencent-weixin/openclaw-weixin@2.4.10 --pin
openclaw plugins install npm:@tencent-connect/openclaw-qqbot@2.0.4 --pin
openclaw configure --section model
~~~

微信使用手机在微信客户端扫码并同意授权。新私聊用户由管理员按渠道配对。QQ 使用自己的开放平台机器人。通过本地私密环境配置 QQBOT_APP_ID 和 QQBOT_CLIENT_SECRET，把已授权用户的大写 OpenID 写入本机配置。命令中的 OpenID 参数是替换示例。

~~~powershell
uv run --no-sync python scripts/research_stack.py --enable-wechat --qq-openid 'REPLACE_WITH_AUTHORIZED_QQ_OPENID'
openclaw channels login --channel openclaw-weixin
openclaw config validate
openclaw mcp doctor rnaharness-research --probe
openclaw gateway
~~~

有首次微信配对请求时，在另一个指向同一配置的终端运行 openclaw pairing list openclaw-weixin，核对并批准该用户的配对码。可用 openclaw channels status --probe 检查两条渠道。授权后在各自客户端发一条测试问题，例如「用 RNAHarness 比较 55 °C 下 WT 与 LR 发卡，并给出两个作业 ID」。

仓库构建与 MCP 测试可在没有微信、QQ 或模型账户时完成。扫码、开放平台凭据和平台侧收发需要实际授权账号。[OpenClaw 微信配置](https://docs.openclaw.ai/zh-CN/channels/wechat) · [QQ Bot 配置](https://docs.openclaw.ai/zh-CN/channels/qqbot) · [MCP 配置](https://docs.openclaw.ai/tools/mcp)

## 3. 可选的 Claude Code 和 Codex 入口

两个客户端复用 [同一份科研 skill](../skills/rnaharness-research/SKILL.md) 和科研 MCP。可将初始化生成的 SQLite 路径和本机发卡后端环境传给任一客户端，或用管理员提供的授权连接接入团队服务。[客户端配置说明](../README.md#claude-code-and-codex-research-skill)
