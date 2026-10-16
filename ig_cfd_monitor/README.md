# Harbour · IG CFD 只读监控

运行在自己的 Mac 上，通过 IG REST + Lightstreamer 读取账户，通过 Tailscale Funnel 分享只读网页。代码放 GitHub，API 密钥、登录凭据、资金和历史数据库只存本机。此目录与仓库原有的美股看板独立。

## 连接账户

1. 在 My IG → Settings → API keys 生成并启用 API Key。Live 与 Demo 的密钥和账户不能混用。官方指南：https://labs.ig.com/gettingstarted
2. 双击 `启动监控平台.command`，或创建 `.venv`、安装 `requirements.txt` 后运行 `.venv/bin/python manage.py install`。
3. 在 **这台 Mac** 打开 http://127.0.0.1:8787/，填 Live API Key、IG 登录用户名、登录密码。Account ID 可留空；多个 HKD CFD 账户时，配置页显示候选编号，再明确选择。历史起始日填开户日或更早。
4. 保存后自动连接；页面显示结果或经过处理的 IG 错误码。密码错误、密钥无效、待同意协议等错误不会不断重复登录；修正后重新保存。不要将任何密钥发到聊天、GitHub 或网址参数里。
5. 查看 http://127.0.0.1:8786/ 。首次接入必须核对 IG 平台中的权益、现金、保证金、仓位和最近几笔交易。代码和合成测试通过，不等于真实账号已验证。

只选择 `ENABLED`、`CFD`、`HKD` 账户。“个人账户”不能确定法律实体；实际开户实体和保证金平仓规则仍须以用户协议为准。API 本身可能带有交易权限，本项目通过出站端点白名单和公开接口限制实现应用只读，不声称 API Key 是券商层面的只读密钥。

## 分享链接

Mac 上的服务绑定 loopback。只把 **8786** 交给 Funnel：

```sh
/Applications/Tailscale.app/Contents/MacOS/Tailscale funnel --bg --https=8443 --yes http://127.0.0.1:8786
```

分享命令返回的 HTTPS 网址。查看者不需要加入 tailnet；所有拿到链接的人可以查看完整余额、持仓和逐笔记录。`noindex` 只是搜索引擎提示，不是访问控制。**不要发布 8787 配置端口、state 目录或文件服务。**

旧看板的 HTTPS 443 / 私人 Serve 可以继续保留。仅关闭本项目入口：

```sh
/Applications/Tailscale.app/Contents/MacOS/Tailscale funnel --https=8443 off
```

## 后台运行与本机存储

`manage.py install` 将运行代码复制到 `~/Library/Application Support/HarbourCFD/`，安装当前用户 LaunchAgent `com.harbour.ig-monitor`。后台监督公开读取服务、独立的本机配置服务；登录后自动启动，异常退出后重启。后续安装更新代码时保留运行目录中的账户数据。安装器会按 requirements.txt 核对运行目录中的依赖。

凭据实际位于该运行目录的 `state/credentials.json`，权限 0600，目录权限 0700；文件只允许当前用户读取，但不是加密保险库。SQLite 为 `state/monitor.sqlite3`，按环境和 Account ID 分区。勿把运行目录、备份或截图上传仓库。公开 API 仅返回明确列举的账户/交易字段，不包含登录响应、令牌、用户名、密码或密钥。

服务运行时使用 `caffeinate -i -s` 避免空闲睡眠。关机、合盖睡眠、退出用户会话、断网仍会中断采集，Tailscale 不代替云服务器。

```sh
.venv/bin/python manage.py status
.venv/bin/python manage.py stop
```

## 数据及刷新口径

- 行情：使用当前 IG `PRICE:{accountId}:{epic}` / `Pricing` adapter，持仓自动加入，登录时读取 IG 内前五个自选列表；最多 38 个合约，持仓优先。未订阅数量单独显示。尚未确认的关键指数不自行选定。
- 推送使用 `ACCOUNT`、`TRADE` 和 `PRICE`。后台至网页 SSE 最高每 250 毫秒推送一次；这是展示更新上限，不是 IG 到屏幕的延迟保证。页面断流会标为中断并尝试重新连接。
- 账户、持仓和挂单：约每 20 秒 REST 对账；交易推送触发较快对账（最短 5 秒间隔）。REST 串行限速，不随访问人数放大 IG 调用。SDK 自动重连，令牌失效重新认证，限流退避。
- 账户/报价超过 30 秒或时间戳缺失会标注。休市和延迟报价仍保留原状态，不标为可用实时行情。
- 账户权益采用 IG 现金余额 + 浮动盈亏，推送明确提供 EQUITY 时采用该字段；资金、保证金不互相替代。逐仓 HKD 盈亏在点值/汇率未核验前不估算。
- 权益曲线只记录接入后的真实快照；快照间隔通常 30–40 秒，超过两分钟的缺口断开绘制，不生成合成历史。
- 历史以 28 天窗口、分页读取，保存交易流水、账户活动和推送事件；相同原始记录哈希去重。不同来源描述同一笔交易时保留来源，不能将总条数当成交笔数。网页每页 50 条，CSV 导出全部已读取记录。
- 启动时重新覆盖用户指定的历史范围，运行期间每五分钟回查最近两天。覆盖状态只说明 API 请求区间，不等于月结单已核对、开户以来绝对无缺失。API 错误、缺少分页元数据会显示未完成。
- SQLite 实际使用 events、snapshots、metadata 三张表，由 storage.py 创建。工作目录中旧 schema.sql 只是原型设计参考，不用于生产迁移。

## 风险提醒

本项目约定初始投入 HKD 30,000，累计亏损 60% / HKD 18,000 为网页提醒线，不自动下单或平仓。没有后续净入金时对应权益 HKD 12,000。

首次保存账户配置的时点作为出入金基准：基准之后通过 IG 明确的 DEPOSIT / WITHDRAWAL 查询过滤核对净入金，避免把提款算作亏损或把追加资金算作收益。该口径假设用户确认的 30,000 已代表基准之前投入的本金；若此前有其他出入金，需要先修正本金口径。资金记录每五分钟核对，未完成核对或币种/金额无法解释时风险显示待核验。

`经济损益 = 当前权益 - 基准后的净入金 - 30,000`。数据陈旧、连接异常、出入金未验证时不显示风险正常。权益不高于已用保证金时单独显示压力提醒；这只是本项目的监控阈值，IG 实际强平规则尚未核验。

目前只有网页内提醒，没有短信、微信或手机后台通知。

## 新闻覆盖

每小时检查美联储、欧洲央行、美国能源信息署官方 RSS，展示真实标题、原文链接、发布时间、来源可用状态和宏观背景。来源失败会显示失败状态；未自动产出具体持仓影响研判。个股新闻、财报/宏观日历和针对关注品种的完整每日简报仍待确定关注清单后扩展。

## 验证与 GitHub

```sh
.venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
# Optional browser QA: install Playwright and Chromium, set executable/module paths when required.
node tests/live-browser.mjs
node tests/live-populated.mjs
python3 export_source.py
```

`tests/live-browser.mjs` 是未配置账户环境的 UI/HTTP 验证，应只对没有真实账户配置的测试实例运行。源码由 `source_manifest.json` 白名单导出，不递归扫描运行目录。原型 app.js、fixtures.js、qa 截图及全部 state/.venv 文件不发布，公开服务器也不提供原型示例数据。

官方参考：

- https://labs.ig.com/rest-trading-api-guide.html
- https://labs.ig.com/streaming-api-reference.html
- https://labs.ig.com/reference/history-transactions.html
- https://lightstreamer.com/share/ig/
- https://tailscale.com/docs/features/tailscale-funnel
