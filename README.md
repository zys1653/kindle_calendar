# TodoClock

Kindle Oasis 1（第八代）KUAL 信息屏：顶部状态栏、左侧导航、Microsoft To Do、整月日历、和风天气、倒计时/秒表和设备设置。

**版本 0.1.2：已完成电脑仿真与自动测试，尚未在你的 Kindle 上验证。** 不会自动连接设备。首次安装先诊断、校准，再启用完整接管。

## 电脑快速预览

在项目根目录执行（Python 3.9+，带 Tkinter）：

```powershell
python -m pip install -r requirements-dev.txt
python todoclock/run.py simulate
```

仿真使用隔离假数据，鼠标操作屏幕、左右键切列表/月份；工具栏可模拟断网、封皮插拔、电量/充电和日期推进。临时数据在 `.scratch/`，正常退出清理，不访问微软或和风服务。

```powershell
python todoclock/run.py preview
```

生成 `preview/` 中五个页面、四种方向的 PNG。电脑使用系统字体，Kindle 需自行放入支持中文的 `todoclock/assets/fonts/regular.ttf`。安装包不附带字体文件。

## 安装与配置

详见 [安装、配置、恢复与调试](docs/INSTALL.md)。

- 安装目录：`/mnt/us/extensions/todoclock/`。
- 复制 `config/secrets.example.json` 为 `config/secrets.json`，手动填写和风 API Host、API Key。
- 复制 `config/local.example.json` 为 `config/local.json`，填写微软 Client ID。
- 正式使用前通过 KUAL `Diagnostics` 和 `Touch calibration`，再设置 `device.verified=true`。
- 默认每 30 分钟更新、前光关闭、15 分钟彻底刷新；左侧五页均可触控。

## 实现边界

- Python 3.9、Pillow、requests、FBInk；不使用浏览器，不需要常开电脑。
- 待办仅查看与完成；“重要”“计划内”为本地聚合，不显示“我的一天”。断网勾选持久排队，远端冲突不会静默覆盖。
- 和风使用 **weather v1**，API Key 放请求头。地点预置天津南开和河北廊坊。
- 原生系统仅做运行期间的屏保控制、Wi-Fi/前光和 `awesome` 进程暂停/恢复；不写 rootfs，不停 `powerd`，不改开机启动。
- 不做深度休眠/RTC 唤醒、系统校时、天气预警、农历、新增/编辑待办或声音提醒。

参阅 [架构与扩展](docs/ARCHITECTURE.md)、[测试结果与真机验收](docs/TESTING.md)。

## 开发与打包

```powershell
python -m unittest discover -s tests -v
python tools/check.py
python tools/ui_smoke.py
python todoclock/run.py preview
python tools/package.py
```

产物：`dist/TodoClock-0.1.2.zip`、对应 SHA256 文件。打包脚本仅包含明确列出的代码、示例配置和文档，不包含本地配置、令牌、待办、日志或设备恢复记录。
