# 架构与扩展

## 数据流

```text
KUAL → launch → /tmp 独立 guardian → Kindle 主循环
触摸/按键 → Controller.action → 状态变化 → render → Pillow 图像/点击区域 → 串行显示线程 → FBInk
后台串行任务 → Microsoft / QWeather → 原子 JSON 缓存 → Controller → 页面
应用退出或心跳超时 → guardian → 校验身份、释放输入、恢复原状态
```

`render.py` 不做网络/设备 I/O。`controller.py` 管理状态、任务与计时；网络线程仅更新服务缓存，主线程获取完成通知后读取快照。日期变化和任务完成使页面失效，可见计时器每秒失效。无内容变化不调用 FBInk。

## 主要接口

| 模块 | 接口 | 约定 |
|---|---|---|
| 页面 | `render(controller, font_path) -> (Image, hits)` | L 模式图像；hits 为 `(矩形, action 元组)` |
| 输入 | `Inputs.poll(rotation)` | press、tap、cancel、key、exit；仅完整点击帧产生 tap |
| 显示 | `Kindle.show(image, force=False)` | 软件旋转后比较原生方向脏矩形，GC16；周期性全刷 |
| 设备 | `status / set_wifi / set_light` | Kindle/SimDevice 两种实现 |
| 天气 | `cached(place) / sync(place)` | 按经纬度隔离缓存，规范化数据与 API 无关 |
| 待办 | `sync / enqueue / flush / begin_login / poll_login` | 单一账户，本地 outbox 持久保存 |
| 持久化 | `Store.read / write / update` | 进程内锁、fsync、原子替换，坏 JSON 不静默清空 |

增加页面：在 PAGES 注册入口，实现渲染分支、action 和必要状态。增加天气服务：实现 cached/sync，输出与 `weather.view` 相同的页面模型；不将 API 原始响应传入页面。若以后服务结构明显不同，应将缓存模型规范化进一步移至 provider 内。

## 坐标与硬件边界

原生始终 1072×1448；软件旋转按 0/90/180/270 度处理。`logical_to_native` / `native_to_logical` 互逆。触摸先用三点仿射校准映射到原生坐标，再变换到逻辑方向。

输入读取原生 ABI `input_event`，兼容 ARMv7 的 16 字节事件和 64 位主机的 24 字节事件。实现单触点点击、MT slot 0，忽略其他触点；SYN_DROPPED 丢弃手势。没有滑动、缩放或双击动作。翻页键的实际方向需真机核对。

启动探测 framebuffer 的 ioctl 信息，不从 virtual_size 推导显示大小。暂停 `awesome` 只影响当前运行进程；恢复记录写入后才发送信号。看护脚本独立且只依赖标准库，恢复时仍可在项目模块加载失败的情况下运行。

关键硬件依据来自 [FBInk](https://github.com/NiLuJe/FBInk) 的 CLI 文档及 [KOReader Kindle 设备适配](https://github.com/koreader/koreader/blob/master/frontend/device/kindle/device.lua)。仅借鉴接口资料与设备路径，没有复制整个参考项目或引入其运行依赖。

## 网络、失败与扩展限制

每个请求带连接/读取超时；重定向关闭，Graph nextLink 仅接受官方 Graph v1.0 主机路径。429 遵守 Retry-After，其他失败指数退避。设置“手动同步”停止周期拉取，但已有待提交完成操作仍在联网后自动提交。

天气归因信息保留，缺失值显示未知。实时、每日、小时接口分别保存成功时间与错误。真实 Key 的访问权限和账户额度只能由用户联网验证。

Graph 完成操作先 GET 检查状态与修改时间，再 PATCH；可用时使用 ETag 条件提交。没有 ETag 时仅提供尽力冲突检测。发生删除、移动、权限变化不猜测目标，不重复创建任务。

登录暂时不支持多账户或免注销账户切换；计时暂时不支持跨重启继续运行；设备暂时仅针对已提供参数的 KOA1，其他固件需要先探测和验证。


## 0.1.3 输入与显示分离

Feedback.event 在有效松开时直接返回 action；80 毫秒视觉状态不锁住输入。主循环使用最近完成显示的页面上下文与命中区；新页面显示完成前不把旧页面触摸映射到新页面。任务点击携带 list_id/task_id，原有整数索引仅供内部兼容调用。

Display 独占 Kindle.show，提交时复制图像、配置和命中区。条件变量保护一帧正在执行、一帧可替换的待显示任务，待显示任务的 force 标志按 OR 保留。GC16 与 --wait 在显示线程运行；只有成功完成的画面更新命中快照。异常回报主循环后退出恢复。退出取消待显示帧并等待当前有超时保护的命令结束，避免恢复画面后旧写入覆盖屏幕。

power.sample 提供单次读取结果和 cover_evidence；Kindle.power_status 在每秒采样时用 CoverTracker 解释连续无数据。后台 Wi-Fi 查询不能改变计数或覆盖当前电量。live_gauge 是依据 KOA1 实测推断在位，不代表所有固件都提供明确硬件连接信号。诊断只输出允许的字段值及读取分类。

天气每页容量统一为 layout.HOURS_PER_PAGE=4。icons 是原创 Pillow 线条绘图；天气代码映射参考 https://dev.qweather.com/docs/api/weather/weather-conditions/ ，未知代码保留天气文字并使用中性图标。页脚来源入口打开本地数据详情，不启动浏览器。


## 0.1.4 顶部状态与验收职责

statusbar 提供纯展示逻辑：本体充电文案去重、两项同步是否完整且新鲜。Controller 缓存天气三个接口最早的成功时间，renderer 不读取文件；页码按钮和实体键共享 settings_page 状态。full_refresh 仅请求现有显示线程全刷，沿用有界队列与 force 保留机制。

开发检查保留确定性单元测试、静态检查和打包校验。自 0.1.4 起仿真及视觉验收由用户负责，simulate/preview/Tk 工具继续保留，修改项目后不自动执行。


## 0.1.5 邮箱

`mail.py` 封装 Graph 列表、正文与已读队列；`mail_controller.py` 接入现有串行 worker，`mail_render.py` 只读取主线程快照。`mail_minutes` 是独立配置，缺省 15；第三页设置管理授权与周期。原顶部同步指标仍只有待办和天气。

Mail 接口：`ready / cached(folder) / fetch(folder, more=False) / sync / body(id, fetch=False) / enqueue / flush / cancel / clear`。分类使用固定文件夹名称与 Outlook inferenceClassification；HTTP 请求有超时、关闭重定向、响应大小上限，分页 URL 验证沿用 Graph 官方主机限制。正文是规范化纯文字，渲染器不执行网络或设备 I/O。

邮箱缓存以账户 ID 摘要命名，列表按 30 项写入不可变批次后原子提交索引；进程锁保护读取和批次回收。正文每封独立文件，LRU 索引限制为 100 封，避免每次读取整个正文库。已读队列只存稳定邮件 ID 和安全状态，PATCH 确认后更新缓存；403/404/409/412 转终止失败，其余保留待提交并退避。

授权补充使用设备码流程，请求 Tasks.ReadWrite、Mail.ReadWrite、User.Read、offline_access。旧账户 ID 来自已有已核对记录、旧令牌访问 /me 的结果，或直接从微软获取的旧 JWT 的 oid 身份提示；身份不明时停止升级并说明重新登录步骤。JWT 身份提示不用于批准访问。新令牌以 /me 校验身份和 scope 后一次写入，取消与写入共用锁。注销等待活动任务结束，再清除对应缓存。

文件夹和正文点击携带稳定 ID；分页完成通知同时携带文件夹与视图代次，过期结果不会跳转当前页面。正文异步结果只进入仍在查看的邮件。墨水屏显示线程、设备探测和恢复策略保持原边界。


## 0.1.6 统一账户与同步摘要

Microsoft.begin_login/poll_login 统一请求待办、邮件和账户读取权限；begin_mail_login 保留为旧调用别名。旧 scope 缺失的令牌按 Tasks.ReadWrite/offline_access 刷新，补充授权通过同一登录入口显式完成。登录成功后 Controller 提交一次待办与邮箱初始同步，随后各自遵守设置周期。

Mail.summary 返回账户隔离的 {synced, error}，独立于各文件夹缓存。完整同步开始前标为未完成，六分类全部成功后原子写入时间并清空错误；失败或中断保留前次完整时间。旧缓存不会通过分页时间推断完整成功。

Controller.mail_snapshot 向渲染器提供 mail_summary/mail_synced，queue_controller 合并两种队列的展示和计数，保持持久化文件独立。同步总状态包含 todo/weather/mail 与 flush/mail_flush，排除 mail_more/mail_body。设置和队列状态全部在主线程更新；渲染器仍不读取缓存。

设置两页，周期与退出集中第一页，账户与合并队列在第二页。队列弹窗每页对应一项稳定 ID 操作，异步完成后刷新计数与当前项；清除失败不影响正常待提交项。取消最后一项时移除已无重试对象的队列提交错误。
