# 检查更新

在“设置 → 检查更新”查看当前和最新版本，点击“下载更新”，校验完成后可“安装并重启”。也可从设置窗口的“Python 与 MLIP”页进入。更新直接读取公开仓库，无需 GitHub 账号或令牌。

安装前会正常询问未保存的结构，并备份旧程序。Python、MLIP 模型、结构数据及个人设置继续使用原来的文件；备份和下载保存在 `work/updates/`。开发目录提供检查和下载，自动安装用于包含 manifest.json 的 Windows 轻量版目录。

## 维护者公开仓库

打开 [VBuild 仓库设置](https://github.com/Lin-Yueyang/VBuild/settings)，在 General 页底部找到 Danger Zone → Change repository visibility → Change visibility，选择 Public，按页面提示确认并点击 Make this repository public。仓库地址继续使用原地址。

GitHub 官方说明：[更改仓库可见性](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/managing-repository-settings/setting-repository-visibility)。

## 发布更新

将准备好的 GitHub 文件夹提交、推送即可。软件读取主分支 main 的 manifest.json，再下载对应提交，版本检查与下载始终对应同一份代码；无需额外创建 Release。

首次需要手动更新到包含此入口的版本，以后即可在软件内操作。
