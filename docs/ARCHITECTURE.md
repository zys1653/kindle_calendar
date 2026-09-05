# 架构与扩展

## 数据流

```text
KUAL → launch → /tmp 独立 guardian → Kindle 主循环
触摸/按键 → Controller.action → 状态变化 → render → Pillow 图像/点击区域
后台串行任务 → Microsoft / QWeather → 原子 JSON 缓存 → Controller → 页面
应用退出或心跳超时 → guardian → 校验身份、释放输入、恢复原状态
```

`render.py` 不做网络/设备 I/O。`controller.py` 管理状态、任务与计时；网络线程仅更新服务缓存，主线程获取完成通知后读取快照。日期变化和任务完成使页面失效，可见计时器每秒失效。无内容变化不调用 FBInk。

## 主要接口

| 模块 | 接口 | 约定 |
|---|---|---|
| 页面 | `render(controller, font_path) -> (Image, hits)` | L 模式图像；hits 为 `(矩形, action 元组)` |
| 输入 | `Inputs.poll(rotation)` | tap、key、exit；仅完整点击帧产生 tap |
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
