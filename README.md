暂只支持 **Windows**（x64）；Python 需自行安装，兼容 **64 位 CPython 3.11–3.13**，推荐 **3.12**。

# VBuild

以 VASP 为中心的轻量结构建模软件，支持原子编辑、切面扩胞、轨迹查看、电荷与磁矩显示，以及可选的 MLIP 结构优化和预平衡。

## 安装与启动

1. 在 GitHub 点击 **Code → Download ZIP**，解压完整文件夹。
2. 双击 **VBuild.exe**，选择已安装的 **python.exe**，点击“检测并使用”。
3. 首次缺少建模依赖时，确认安装；完成后进入软件。
4. 将结构文件拖入窗口，或通过“文件 → 打开”导入。

基础依赖包括 PySide6、PyVista/VTK、NumPy、ASE、SciPy、spglib 和 Matplotlib。首次安装需要联网，之后可直接启动。需要更换 Python 时，在“设置 → Python 与 MLIP”选择路径并检测，重启后使用。

## 可选功能

- **MLIP**：在“设置 → Python 与 MLIP”勾选模型，点击“下载”。需要 NVIDIA GPU 时勾选 GPU 选项；使用 CPU 时无需 GPU 环境。软件不会安装显卡驱动。
- **SMILES**：在“添加分子 → 以 SMILES 添加分子”中预览；缺少 RDKit 时可确认后一键安装。
- **Materials Project**：在构建结构窗口输入自己的 API key 并保存，即可搜索和下载结构。

## 更新软件

可通过“设置 → 检查更新”查看最新版本，下载后点击“安装并重启”，无需登录 GitHub。也可关闭软件，将新版 ZIP 中的程序文件复制到原目录并替换同名文件，再启动。原有 Python、模型和用户设置可继续使用。

## 使用说明

- [按钮与功能](docs/buttons.md)
- [版本记录](docs/updates.md)
- [检查更新](docs/update.md)
