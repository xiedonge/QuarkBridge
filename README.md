# QuarkBridge

夸克分享链接下载工具，提供自带运行环境的 Windows 程序及 Mac 应用。

## 直接使用

前往 [Releases](https://github.com/xiedonge/QuarkBridge/releases/latest)，不要下载源码包代替应用。

- **M 系列 Mac mini / MacBook**：下载 `QuarkBridge-macOS-arm64.zip`，解压后把 `QuarkBridge.app` 拖到“应用程序”，双击运行。
- **Intel Mac**：下载 `QuarkBridge-macOS-x64.zip`，操作相同。
- **Windows 64 位**：下载 `QuarkBridge-Windows-x64.zip`，解压整个文件夹，双击 `QuarkBridge.exe`；保留旁边的 `_internal` 文件夹。

应用包含 Python、Tk 界面、证书及相关组件，不需要手动安装 Python、pip 或依赖。Mac 目前使用临时代码签名，尚无 Apple 开发者签名和公证；首次如被系统拦截，请确认下载来源后在“系统设置 → 隐私与安全性”中允许打开。不要关闭系统整体安全保护。

默认保存到用户“下载/QuarkDownloads”，可改为 F 盘、外置硬盘或其他目录。首次在设置中点击“登录夸克 / 更换账号”，在内置官方页面扫码或登录；账号验证通过后自动获取 Cookie，然后“添加文件 → 解析分享 → 加入队列”。

## 功能

- 下载中随时追加任务；队列保存、暂停、重开续传。
- 分享文案和提取码识别，目录查重，进度及预计完成时间。
- 多连接分段下载和连接复用；速度受夸克、网络、账号及资源状态影响。
- 完成的视频集中到下载根目录，保留原扩展名，同名保护。
- 已收齐但接口摘要不一致时，保留“校验待确认”标记；不是校验通过的声明。
- Windows 使用 DPAPI，Mac 使用原生钥匙串保存凭据。无需在线解析站，不上传 Cookie 到第三方。

Windows 便携版任务保存在程序目录 `data`；Mac 保存在 `~/Library/Application Support/QuarkBridge/data`。升级保留相应目录。Windows 的加密登录不能直接导入 Mac，需要重新登录；跨系统未完成断点迁移尚未提供自动流程。

## 构建与验证

`windows/` 和 `macos/` 是平台源码，`packaging/` 是入口与构建检查，`.github/workflows/build.yml` 在 Windows、Apple Silicon Mac 和 Intel Mac 上分别构建自带环境的包。

打包后使用内嵌运行环境验证 Tk 窗口初始化、文件锁、状态读写和 HTTPS 证书；Mac 钥匙串适配的逻辑测试使用替身，首次系统授权仍需用户完成。CI 不使用真实夸克账号或下载用户视频。

本项目没有转存、上传、删除云端文件的调用。请仅下载有权访问的内容。接口可随服务端变化，不能保证所有分享和账号均可取链。

协议实现参考 [PowerList 夸克分享驱动](https://github.com/power721/PowerList/tree/main/drivers/quark_uc_share)。交互设计参考 Gopeed、Motrix 和 aria2；此次发布没有将它们的下载器作为运行依赖。

## 内置登录测试版（v0.12.0-rc.1）

从对应测试 Release 下载完整 Windows 或 Apple Silicon 包。软件内置 Qt WebEngine，不需要安装 Python、浏览器驱动或 WebView2。

1. 暂停下载队列，打开“设置”，点击“登录夸克 / 更换账号”。
2. 在官方 pan.quark.cn 页面完成扫码或登录；应用只读取可用于官方接口的 Cookie，不读取密码输入框。
3. 账号接口验证通过后，登录窗口关闭，返回下载器。原任务可继续或重试。

登录窗口采用无持久化浏览器配置，Cookie/缓存只保留在内存中。程序间通过仅监听本机的随机认证通道交接凭据，不写明文中转文件、不经过第三方网站。关闭登录窗口或验证失败时保留旧登录。勾选“记住登录”时保存到原 DPAPI / macOS 钥匙串；不勾选仅在本次进程中使用。加密保存失败会明确提示，不回退到明文。

网站验证码、手机确认和登录风控必须由用户完成；本功能不绕过验证。现有 Cookie 手动输入作为备用。服务端返回续期 Cookie 时仍会自动更新，但不能保证登录永不过期。

升级先退出程序并备份应用。Mac 仅替换 .app，保留 Application Support/QuarkBridge；Windows 保留程序目录的 data 文件夹。钥匙串首次授权需要在 Mac 上确认，即使拒绝授权也会单独恢复原任务配置。

CI 验证真实内嵌浏览器渲染、HttpOnly Cookie 读取与本机进程交接，以及队列保留/取消逻辑。测试不使用用户真实账号；官方网页扫码登录、风控和真实下载还需现场验收。此前 23018/LookupError 不因新增登录入口就视为已解决。
