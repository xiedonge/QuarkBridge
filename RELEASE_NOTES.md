Windows、Apple Silicon Mac、Intel Mac 自带运行环境包，无需安装 Python 或 pip。

- M 系列 Mac mini 选择 macOS-arm64；Intel Mac 选择 macOS-x64。
- Mac 解压后将 QuarkBridge.app 拖入“应用程序”并打开。尚未 Apple 开发者签名与公证，首次可能需在系统设置中允许打开。
- Windows 解压完整文件夹，双击 QuarkBridge.exe，保留 _internal 文件夹。
- 首次在设置中填写自己的登录 Cookie，默认输出到用户下载目录下的 QuarkDownloads。

包含任务队列、断点续传、查重、视频扩展名处理、完成文件集中保存。Mac 登录凭据使用钥匙串，Windows 使用 DPAPI。

发布流水线分别在三个系统构建，测试内嵌环境的 Tk 窗口、状态读写、文件锁和 HTTPS。真实夸克账号下载与用户电脑上的首次钥匙串授权不属于 CI 测试范围。
