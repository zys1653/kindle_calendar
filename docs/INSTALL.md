# 安装、配置、恢复与调试

## 1. 设备与准备

目标设备：已越狱 Kindle Oasis 1（Whisky / Duet），Python 3.9.8，Pillow 9.0.0.dev0，requests 2.26.0，FBInk 位于 `/usr/bin/fbink`。

原生 framebuffer 为 1072×1448、8 bpp，stride 由 ioctl 读取；默认软件旋转为 1448×1072。`virtual_size=1088,6144` 不是显示尺寸。当前设备档案只接受原生 rotation 0，先在 Kindle 正常竖屏主页启动 KUAL，然后将按钮宽边放到下方。

准备一个支持中文的 TrueType 字体，例如有合法授权的 Noto Sans SC 静态 TTF，放入 `assets/fonts/regular.ttf`。保留字体许可文件；本项目不附带字体，也不依赖 MRInstaller 字体。自定义路径可写入 `local.json` 的 `font`。

首次测试前保留可用的 USBNetwork/SSH 恢复通道，确认知道 Kindle 电源键强制重启的方法。先退出 KOReader 或其他独占屏幕插件。运行期间不要启用 USB 存储模式、覆盖安装或删除插件文件；先退出再传文件。

## 2. 安装文件

解压 `TodoClock-0.1.2.zip`，将其中 `todoclock` 文件夹复制到 Kindle 的 `extensions`，使路径为：

```text
/mnt/us/extensions/todoclock/config.xml
/mnt/us/extensions/todoclock/menu.json
/mnt/us/extensions/todoclock/run.py
```

不是再多套一层同名目录。保留脚本 LF 换行；通过 `sh` 运行脚本，无需给用户分区修改挂载方式。

## 3. 和风天气

复制 `config/secrets.example.json` 为 `config/secrets.json`，在电脑编辑：

```json
{
  "qweather": {
    "api_host": "你的专属主机.qweatherapi.com",
    "api_key": "你的API_KEY"
  }
}
```

`api_host` 使用和风控制台分配的主机名，可以带 `https://`，不要附加接口路径、端口或 query。首版仅接受 `*.qweatherapi.com` 专属主机，防止错误配置向第三方发送 Key。请求通过 `X-QW-Api-Key` 认证，HTTPS 证书校验始终开启，禁止自动重定向。

实现的接口：

```text
GET /weather/v1/current/{latitude}/{longitude}
GET /weather/v1/daily/{latitude}/{longitude}?days=3
GET /weather/v1/hourly/{latitude}/{longitude}?hours=24
```

传入 `lang=zh`、`localTime=true`。默认只更新当前选择地点，每次完整同步三个请求；手动更新也消耗请求量。具体权限、额度及费用以你的和风账户为准，不自动购买套餐。

地点可在设置切换。普通配置支持覆盖经纬度，示例：

```json
{
  "locations": {
    "nankai": {"name": "天津南开", "latitude": 39.14, "longitude": 117.15},
    "langfang": {"name": "河北廊坊", "latitude": 39.54, "longitude": 116.68}
  }
}
```

坐标是地区代表点，非你的精确住址。两位小数与 v1 接口精度要求一致。

修改配置后打开“设置 → 更多 → 重新加载 → 测试连接”。网络任务未结束时暂不重载。失败会显示认证、权限、额度、限流或网络问题；重试有退避，不会连续点击就无限发送请求。

天气页“数据”可查看三个接口各自获取时间、错误及归因信息。服务端没有给出观测/更新时间时显示“未提供”，本地获取时间不冒充气象观测时间。小时预报只展示实际返回的时段；首次运行不补造已过去小时。

和风当前支持 API Key，但公告从 2027-01-01 起限制此认证方式的每日请求量；未来可替换认证模块为 JWT，无需改变页面。参考：[API Host](https://dev.qweather.com/docs/configuration/api-host/)、[认证](https://dev.qweather.com/docs/configuration/authentication/)、[天气 v1](https://dev.qweather.com/docs/api/weather/)。

## 4. 微软个人账户登录

1. 使用微软 Entra 应用注册入口创建自己的应用。若个人账号没有可管理的目录，先按微软门户要求建立可注册应用的目录/账户环境；这属于账户侧前置条件，插件不能代为授予目录权限。
2. 支持账户类型选择包含个人 Microsoft 账户的选项；不要仅允许单一组织租户。
3. 在身份验证/高级设置中允许公共客户端流程（设备代码流程）。不创建客户端密钥。
4. 添加 Microsoft Graph **委托权限** `Tasks.ReadWrite`。程序还请求 `offline_access`，用于刷新令牌。
5. 复制应用程序（客户端）ID。将 `local.example.json` 复制为 `local.json` 并填写：

```json
{
  "microsoft": {"client_id": "你的应用程序客户端ID"},
  "device": {"verified": false},
  "font": "assets/fonts/regular.ttf"
}
```

6. 插件设置页选择“登录”，用手机或电脑打开屏幕显示的网址、输入设备码并授权。验证码有时限，程序按微软返回间隔轮询。

首次同步可能需要等待多个列表分页请求。已有令牌会自动刷新；登录失效时按提示处理。首版换账户/重新登录前需注销旧会话，注销会清除本机缓存和未提交操作，界面有确认提示。先处理或记录尚未同步的任务，不要依靠注销重试网络错误。

离线点击复选框后显示待同步；成功提交后从未完成视图移除。任务远端变化、删除或无权限时转为冲突，查看任务详情或“设置 → 同步队列”。可清除冲突操作，再刷新远端数据后重新确认。清除本地队列不会改变云端任务。

Microsoft Graph 并不总是提供可用 ETag：返回 ETag 时发送 `If-Match`；否则以提交前读取的 `lastModifiedDateTime` 比较做尽力冲突检查，不能保证消除读取与提交之间的极小竞态。重复任务由 Graph 完成语义处理，不在本机创建下一次任务。

参考：[设备代码流程](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-device-code)、[To Do 任务资源](https://learn.microsoft.com/en-us/graph/api/resources/todotask?view=graph-rest-1.0)。

## 5. 真机分步启用

### 只读诊断

在 KUAL 选择 `TodoClock → Diagnostics (read only)`，或通过已有 SSH：

```sh
python3 /mnt/us/extensions/todoclock/run.py diagnose
```

结果保存 `state/diagnostics.json`。检查 framebuffer、FBInk 图片/刷新能力、触摸/翻页/电源设备、前光及屏保/Wi-Fi 属性。诊断不修改设备设置，不采集序列号、SSID、令牌或任务正文。`hardware_verified=false` 是刻意保留的事实标记，不是诊断程序故障。

### 单帧显示

```sh
python3 /mnt/us/extensions/todoclock/run.py once
```

该命令仅显示一次，不禁止屏保、不独占输入、不联网同步。原生界面可能重绘覆盖；可返回主页恢复画面。

### 触摸校准

KUAL 选择 `Touch calibration`。它会临时控制屏保、关闭前光、暂停原生窗口管理器并抓取输入，独立看护进程同时运行。按顺序点击四个十字：前三点拟合变换，第四点检查误差。120 秒超时退出，电源键或双翻页键长按也能退出。成功后写 `state/calibration.json`，退出后恢复原系统状态。

确认诊断、单帧、校准及退出恢复有效后，在 `local.json` 设置：

```json
{"device": {"verified": true}}
```

这是本地启用开关，不意味着开发者已验证了你的真机。然后 KUAL 选择 `Start`。重复启动不会创建第二个接管进程。若方向相反，用设置中的旋转按钮修正；15 秒内未确认自动回退。

前光默认关闭；如果实测为 0 仍发光，请停止接管并反馈诊断，不自行写未知 sysfs 节点。Wi-Fi 只打开/关闭已有配置，不录入密码。退出恢复进入前的前光和 Wi-Fi 状态，插件内调整不会永久改变原生偏好。

## 6. 退出与补救

正常退出路径：设置退出、短按电源键、双翻页键长按 3 秒、KUAL Stop / Restore。

通过 SSH 恢复：

```sh
sh /mnt/us/extensions/todoclock/bin/stop.sh
```

看护进程位于 `/tmp/todoclock-runtime/`，应用心跳超出 45 秒时终止所属应用并重放恢复记录。正常退出先尝试 SIGTERM，5 秒仍未结束才 SIGKILL；恢复对象必须匹配 PID、启动时刻和进程名，不使用 `killall`。

如插件代码损坏而设备仍可 SSH，在确认应用已停止后可运行已经复制到内存目录的独立恢复脚本：

```sh
python3 /tmp/todoclock-runtime/guardian.py restore /tmp/todoclock-runtime
```

不要在应用仍运行时直接调用这个底层命令。优先使用 `stop.sh`，它也会处理看护进程死亡后残留的应用。

`/tmp/todoclock-runtime/result.json` 中 `restored=true` 表示恢复命令成功完成；`false` 或缺失时检查 `journal.json` 与 `state/app.log`。不要在恢复成功前删除恢复记录。整个系统无响应时使用设备电源键强制重启；由于没有改动开机项，插件不会随重启自动运行。软件恢复机制不能保证修复硬件或固件故障。

## 7. 日志、卸载与更新

- `state/app.log` 与 `.1`–`.4`：总量约 5 MB，默认 INFO；设置可临时开启 DEBUG 10 分钟。
- “日志”按页显示全部保留内容，不记录凭据、请求头、任务正文或原始网络异常。
- `state/token.json`、`config/secrets.json` 含凭据；`state/todo.json`、`outbox.json` 含个人任务数据。不要把整个 state 目录上传作诊断。
- 用户分区可能是 FAT，不能将 chmod 当作可靠加密。需要撤销访问时在微软/和风账户侧撤销凭据。
- 更新：先停止并确认恢复，备份配置和 state，再替换代码；不要用安装包覆盖自己的字体/配置。
- 卸载：先停止，确认恢复后删除 `extensions/todoclock`；无系统文件或开机项需要回滚。

## 8. 可配置项

优先级：`default.json` → `local.json` → `state/preferences.json`（屏幕设置保存值）。要让手工配置覆盖已保存的屏幕偏好，退出插件后删除或修改 preferences 中对应项。凭据独立从 `secrets.json` 加载。

| 项目 | 默认 | 允许值 |
|---|---|---|
| rotation | 90 | 0 / 90 / 180 / 270 |
| todo_minutes | 30 | 0（手动）/ 15 / 30 / 60 / 120 |
| weather_minutes | 30 | 0（手动）/ 15 / 30 / 60 / 120 |
| full_refresh_minutes | 15 | 5 / 15 / 30 / 60 |
| location | nankai | locations 中的键 |
| device.verified | false | true / false |
| device.suppress_processes | awesome | 仅允许 awesome；空列表用于诊断实验，可能被原生界面覆盖 |

时区固定中国标准时间，时间来自 Kindle 系统，不自动改系统时钟。秒级计时使用单调时钟；每 15 秒保存一次运行快照，异常重启可能丢失最近最多约 15 秒进度。正常退出保存最新值，重启恢复为暂停。


## 从 0.1.0 升级到 0.1.1

先从设置退出插件，或使用 KUAL 的 Stop/restore。将新版 ZIP 中的 todoclock 文件夹合并覆盖到原目录；保留 config/local.json、config/secrets.json、state 以及自行添加的字体，不要先删除旧目录。安装包不包含这些个人文件。重新启动，无须重新登录或校准。

0.1.1 将翻页箭头改为直接绘制；原竖屏顶端按键（按钮朝下横屏时的右键）执行下一项，另一键上一项。触摸按下时反白，松开后执行；快速点击也保留至少约 180 毫秒的可见反馈。FBInk 必须支持 --wait；等待完成可避免按下与松开两帧被合并，实际延迟取决于墨水屏刷新速度。

电源状态每秒读取本机 sysfs，变化才局部刷新；Wi-Fi/亮度的较慢查询在后台进行。读取间隔不等于驱动报告的延迟。如果封皮节点存在却没有可靠 present 信号，显示“封皮未知”，不把残留 capacity 当成仍在位。需要进一步适配时，分别在封皮装上和拔下后运行诊断，提供 state/diagnostics.json 中 battery 与 power_sources 两段。

天气失败时，点击设置中的天气测试，等待请求结束，提供最新 job_failed weather 行及屏幕错误文字。例如 kind=HTTP_ERROR stage=current status=403 表示实时接口拒绝访问；DNS_ERROR 表示域名解析失败；TLS_ERROR 表示证书或握手失败；READ_TIMEOUT 表示读取超时；SCHEMA_ERROR 表示响应缺少预期字段。HTTP 401 检查 Key，403 检查该项目权限，402 检查额度，429 等待限流重试。TLS 失败先检查 Kindle 日期时间和已有证书配置，不要关闭 HTTPS 证书验证。失败后的重试仍遵守退避时间（通常至少 60 秒），未到时间时不会再次发送请求。

无需提供 API Key、secrets.json 或令牌文件。新版只是补齐诊断，原有单条 ServiceError 日志不足以保证连接故障已解决。


## 0.1.2 和风 Host 校验修复

旧版错误地只允许单层子域名，导致控制台分配的 account.re.qweatherapi.com 一类地址在请求前报 CONFIG_ERROR。新版允许 qweatherapi.com 下的合法多层子域名，同时继续拒绝第三方域名、端口、路径和非 HTTPS 地址。请完整复制控制台 Host，保留地区段，不要手动删除 .re 等中间部分。

来源：https://dev.qweather.com/docs/configuration/api-host/

退出插件后合并覆盖新版安装包，保留 config/local.json、config/secrets.json、state 和字体，再启动测试天气。此修复已验证本地校验；真实服务认证和网络连接仍需真机测试。
