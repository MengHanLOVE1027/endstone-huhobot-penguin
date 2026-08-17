# HuHoBotPenguin — EndStone (Python)

QQ 开放平台官方机器人（WebSocket 接入）与 Minecraft 基岩版（BDS）服务器之间的聊天 / 命令桥接插件。由 Java Spigot 版 [HuHoBot/PenguinClient](https://github.com/HuHoBot/PenguinClient) 移植而来，功能平级。

## 工作原理

- **QQ → 游戏**：群内 @机器人 发出的消息（官方 `GROUP_AT_MESSAGE_CREATE` 事件）→ 命令分发 / 全量转发到游戏。
- **游戏 → QQ**：以 `chat-format.start-with`（默认 `#`）开头的游戏聊天 → 处理后发送到所有已配置的群。
- **命令执行**：通过 `dispatch_command` + `CommandSenderWrapper` 在主线程执行 BDS 控制台命令并捕获输出。

## 为什么零依赖

QQ 官方网关是 `wss://`（TLS WebSocket）。本插件用 Python 标准库 `socket` + `ssl` 自实现最小 RFC6455 WebSocket 客户端（`wsc.py`），**零第三方依赖**；REST 走标准库 `urllib.request`。因此插件可在任何 EndStone Python 运行时直接加载，无需 `pip install`。

## 部署

1. **构建 / 下载**：`python -m build --wheel`（或直接下载 Release 里的 `endstone_huhobot_penguin-*.whl`）。
2. **拷贝插件**：把 `.whl` 复制到服务器 `plugins/` 目录。
3. **QQ 开放平台配置**：
   - 机器人创建后，接入方式选择 **WebSocket**（事件订阅：群聊事件）。
   - 在“开发设置”里拿到 **AppID** 与 **AppSecret**。
   - 若开启了 IP 白名单，把 BDS 服务器的公网出口 IP 加入白名单（否则网关连接会被拒）。
4. **填配置**：编辑 `plugins/HuHoBot-Penguin/config.json`，填入 `bot.app-id`、`bot.secret`，并把目标群 OpenID 填进 `bot.groups`（为空 = 所有群都可触发）。
5. **重启服务器**，控制台应依次出现：
   - `HuHoBot Penguin 已加载`
   - `正在获取 access_token…`
   - `环境：正式，后端 …` → `QQ 机器人已连接（session_id=…）`

> 注意：本版本固定连接**正式环境**（api.bot.qq.com），机器人需**提审上线**后才会在正式网关收到群事件。

## 配置说明

配置文件位于 `plugins/HuHoBot-Penguin/config.json`（首次启动自动生成完整默认项）。

| 配置 | 默认 | 说明 |
|---|---|---|
| `bot.app-id` / `bot.secret` | 空 | QQ 开放平台凭据，必填 |
| `bot.name` | HuHoBot | 机器人显示名（“在线服务器”命令回复用） |
| `serverName` | 空 | 进服/退服通知前缀 `{server}`；留空回退 `bot.name` |
| `bot.groups` | `[]` | 允许的群 OpenID 列表；空 = 所有群 |
| `chat-format.from-game` | `[游戏] {name}: {message}` | 游戏 → 群的格式（`{name}` 玩家名） |
| `chat-format.from-group` | `[QQ] {name}: {message}` | 群 → 游戏的格式（非命令转发时用） |
| `chat-format.post-chat` | `true` | 群内非命令消息是否广播进游戏（需配合全量转发） |
| `chat-format.start-with` | `#` | 游戏聊天触发前缀；**留空 = 所有游戏聊天都转发** |
| `whitelist.add-command` / `del-command` | whitelist add/remove {name} | 白名单命令模板 |
| `filter-regex` | `[]` | 正则过滤列表（Python 正则语法，命中整词替换为 `*`） |
| `admin.mode` | `both` | 管理员判定：`qq`（仅群主/管理员）、`manual`（仅手动添加）、`both`（任一即可） |
| `admin.openids` | `[]` | 全局手动管理员 OpenID（不受群管理方式约束） |
| `features.full-amount` | `false` | 全量转发默认值（可用“全量”命令按群覆盖） |
| `features.markdown-query-online` | `true` | “查在线”用自定义 Markdown 卡片展示（`msg_type=2`）；解析失败/发送失败自动回退纯文本 |
| `features.markdown-whitelist` | `true` | “查白名单”用自定义 Markdown 卡片展示（解析 `allowlist list` 的 JSON 输出）；失败自动回退纯文本 |
| `motd.ip` | 空 | 服务器公网地址（IP 或域名），填写后“查在线”卡片顶部显示 MOTD 状态图（motd.minebbs.com 需能连通该地址）；留空不显示 |
| `motd.port` | `19132` | 服务器端口（BDS 默认 19132） |
| `join-leave.enabled` | `true` | 进服/退服通知开关 |
| `join-leave.join-format` / `leave-format` | `[{server}] 🟢/🔴…` | 进/退服群通知模板；`{server}`=`serverName`（回退 `bot.name`）、`{name}`=玩家名 |
| `audit.base-url` / `audit.api-key` / `audit.model` | 空 / gpt-4o-mini | OpenAI 兼容二次审核端点；配齐后命中本地敏感词才调用 |
| `custom-commands` | `[]` | 自定义命令，见下节 |
| `commands.<命令名>` | `true` | 单独开关某个内置命令 |
| `debug.log-events` | `false` | 输出网关事件/发消息调试日志 |

敏感词：代码内置默认词 + `plugins/HuHoBot-Penguin/sensitive-words/*.txt`（每行一词，`#` 开头为注释，UTF-8）。

> 注：与 Node 版不同，本版去掉了 `debug.probe`（TLS 出口探针是 Node 后端特有问题），改为 `debug.log-events` 控制事件调试日志。

## 控制台命令

服务器控制台（或游戏内 OP）输入：

| 命令 | 说明 |
|---|---|
| `huhobot reload` | 重新读取 `config.json` 并重启 QQ 机器人网关，**无需重启服务器** |
| `huhobot info` | 查看平台、插件版本与运行模式 |

> 说明：`reload` 会先停掉旧机器人连接再按新配置重建；若改的是 `bot.app-id` / `bot.secret` 等连接凭据，`reload` 同样生效。

## 内置命令（22 个）

群内 @机器人 + 命令即可。标注 ⭐ 需管理员（按 `admin.mode` 判定）。

| 命令 | 说明 |
|---|---|
| `查信息` | 无参：本群 OpenID / 本人 OpenID / 角色 / 认证状态；带参 ⭐：查看指定 OpenID 认证状态 |
| `发信息 <内容>` | 过滤后广播进游戏（`[QQ] …`） |
| `发消息 <内容>` | `发信息` 的同义词 |
| `查在线` | 执行 `list` 返回在线玩家（默认 Markdown 卡片展示，可配置关闭） |
| `在线服务器` | 返回机器人名 + 在线状态 |
| `motd <地址[:端口]>` | 查询任意 Minecraft 服务器状态（motd.minebbs.com，支持基岩/Java，在线时 Markdown 卡片 + 状态图展示） |
| `执行 <key>` | 执行自定义命令（仅 `permission: 0` 的命令） |
| `执行命令 <命令>` ⭐ | 以管理员身份直接在服务器控制台执行任意命令 |
| `管理员执行 <key>` ⭐ | 执行任意权限的自定义命令 |
| `查管理` | 列出本群手动管理员 |
| `加管理 <OpenID>` ⭐ | 添加本群手动管理员 |
| `删管理 <OpenID>` ⭐ | 移除本群手动管理员 |
| `管理方式 <QQ/手动/双重>` ⭐ | 设置本群管理员判定方式 |
| `添加白名单 <玩家名>` ⭐ | 执行 `whitelist.add-command` 模板 |
| `删除白名单 <玩家名>` ⭐ | 执行 `whitelist.del-command` 模板 |
| `查白名单` | 执行 `allowlist list` 返回白名单玩家（默认 Markdown 卡片展示；BDS 1.21+，旧版需在源码改回 `whitelist list`） |
| `绑定白名单 <玩家名>` | 自助：把本人 QQ 与该游戏名绑定并加入白名单（绑定记录存 `bindings`） |
| `解除绑定` | 自助：解除本人绑定并移出白名单 |
| `解绑白名单 <玩家名>` ⭐ | 管理员：按游戏名反查绑定并解除，同时移出白名单（用于成员退群后手动解绑） |
| `认证` | 无参：本人认证状态；带参 ⭐：认证指定 OpenID（取最后一个词） |
| `解除认证 [<OpenID>]` | 无参：解除本人；带参 ⭐：解除指定 OpenID |
| `全量 <开/关>` ⭐ | 设置本群全量转发开关 |

## 自定义命令

```json
"custom-commands": [
  { "key": "服务器状态", "command": "mem", "permission": 0 },
  { "key": "踢人", "command": "kick {1} 你已被管理员移除", "permission": 1 }
]
```

- `permission: 0`：普通成员用 `执行 <key>`；`permission > 0`：仅 `管理员执行 <key>`。
- 占位符：`{params}` 全部参数、`{group}` 群 OpenID、`{user}` 用户 OpenID、`{0}/{1}...` 第 N 个参数、`&0/&1...` 同义。
- 命令为 BDS 控制台命令字符串，支持空格与参数展开。

## 测试（黄金路径）

1. 重启后确认 控制台 `QQ 机器人已连接`。
2. 目标群内 @机器人 发送 `查信息` → 回复本群 OpenID、本人 OpenID。
3. `认证` → 回复本人认证状态；群主/管理员 `加管理 <OpenID>` → `plugins/HuHoBot-Penguin/command-state.json` 落盘。
4. `执行 list` → 回复在线玩家。
5. 游戏内发送 `#测试消息` → 群收到 `[游戏] 测试消息`；群内 `发信息 hello` → 游戏内广播 `[QQ] <OpenID>: hello`。
6. 群内 `全量 开` 后发送普通（非命令）@消息 → 游戏内出现 `[QQ] …` 广播。
7. 断网/重启网关 → 控制台应自动重连（Resume 或重新 Identify）。

## 故障排查

- **连接后立刻关闭 / 4014**：Identify 的 `intents` 只订阅了群聊事件；若机器人能力未开通群聊或未切 WebSocket，会在后台拒绝。检查平台“开发设置 → 接入方式 → WebSocket 与事件订阅”。
- **能连接但收不到任何群事件（最常见）**：机器人**未提审上线**，正式网关不会推送事件。① 在开放平台完成机器人提审上线；② 确认机器人已被群主“添加到群聊”；③ 群设置里“机器人主动在群聊内发言”已开启。三者缺一都收不到。
- **回复报错 11273 / 鉴权失败**：发消息 `Authorization` 头必须是 `QQBot <token>`（不是 `Bearer`）。代码已按要求实现。
- **连不上网关**：检查 IP 白名单与服务器 TLS 出口；开启 `debug.log-events` 看连接/握手日志。
- **日志没有 access_token 获取记录**：确认 `bot.app-id`/`bot.secret` 已填写且 `plugins/HuHoBot-Penguin/config.json` 是当前读取的那份。

## 已知限制

- 群消息事件有两类：默认仅推 @ 机器人的消息（`GROUP_AT_MESSAGE_CREATE`）；若群主在机器人资料卡开启了“获取群内全部消息”，则群里每条消息（含 @）都以 `GROUP_MESSAGE_CREATE` 全量事件推送，本插件两种都处理。仅收到 @ 事件时，“全量转发”只会转发 @ 且非命令的消息。
- 游戏 → 群方向不能改原消息，只做转发（与 Spigot 端一致）。
- 灵感移植自 Java 版，`motd.*`、`command-sender` 等死配置已丢弃。
- 跨插件桥接接口（LuckyClover 的 `ll.exports`）为 LeviLamina 专属机制，EndStone 版暂未提供等价的跨插件导出；若需要，可在本插件上暴露一个 Python 模块函数供其他 EndStone 插件 `import` 调用。

## 开发

```bash
# 语法检查
python -m compileall src/

# 构建 wheel
python -m build --wheel
# 产物：dist/endstone_huhobot_penguin-<version>-py3-none-any.whl
```

## License

[AGPL-3.0](LICENSE) © 2026 HuHoBot。本插件由 [HuHoBot/PenguinClient](https://github.com/HuHoBot/PenguinClient)（Java 版）移植而来。
