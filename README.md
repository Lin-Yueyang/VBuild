# VBuild · Windows 轻量版

以 VASP 为中心的结构建模工具：静态编辑、切面扩胞、轨迹查看，以及可选的 MLIP 受力、结构优化与预平衡。

当前版本 **0.3.34**。支持改胞与双结构匹配、方向轴拖动、沿斜晶胞 a/b/c 的标尺/网格/平面。平面位置在基础面板中直接调整，显示细节收在高级参数；见 [轴与网格](docs/grid.md)。

轻量版使用你自己安装的 Python。此目录不包含 Python、GUI 依赖、CUDA 或 MLIP 模型，首次使用按需安装。

## 开始使用

1. 在 GitHub 点击 **Code → Download ZIP**，解压到自己可写的文件夹；请保留完整目录。
2. 双击 **VBuild.exe**，选择 **Python 3.11–3.13，64 位常规 CPython**，推荐 **3.12**。没有 Python 时需先自行安装。
3. 点击“检测并使用”。缺少建模依赖时会列出清单，点击确认后安装到刚选的 Python；完成后自动启动。不新建环境。
4. 拖入 POSCAR/CONTCAR，或通过“文件”菜单打开。可以用 `examples/POSCAR` 测试；它是演示用水分子。

首次安装需要网络和下载时间。基础依赖包括 NumPy、ASE、SciPy、PySide6、PyVista、PyVistaQt、VTK、spglib、Matplotlib，其中 Qt/VTK 体积较大。基础建模不安装 MLIP 或 CUDA。

Python 3.12 已在开发环境和独立测试环境验证；3.11、3.13 属于支持范围，仍需不同电脑实测。

## 可选模型

打开 **设置 → Python 与 MLIP**，勾选需要的模型，点击“下载”。需要 NVIDIA GPU 时再勾选 GPU 选项；先测试 CPU 也可以。

- 可选 NEP89、MACE small-0b2、MACE MP-0、MACE MH-1、DPA4C OMol25 Neo；MH-1 使用 omat_pbe。
- 下载会保存模型，并为所选 Python 安装对应计算库；模型文件放在 `MLIP/`。
- GPU 选项按模型准备计算后端，不安装显卡驱动。普通建模无需这些组件。
- 下载完成后，在受力/优化/预平衡面板选择设备、加载模型。
- 已安装库需要变更版本时，先列出旧版→新版，确认后安装；缺失库随下载操作补齐。
- DPA4C Neo 使用 ASE 纯 PyTorch float32，可复用兼容的 Torch/e3nn；配置会备份 DeePMD 可选编译扩展，已有扩展时先提示确认。电荷默认中性，多重度按电子奇偶给出初值，可手动修改。

Materials Project 功能需要自己的 API key，可在构建结构窗口输入保存；普通本地建模不需要 key。SMILES 构建需要另装可选 RDKit，其他常用小分子按钮不依赖它。

## 常用操作

| 操作 | 默认方式 |
|---|---|
| 旋转 / 选择 / 编辑 | R / S / E |
| 平移 / 缩放 | 鼠标右键拖动 / 滚轮 |
| 选择相连分子 | Shift + 单击 |
| 复制 / 粘贴 | Ctrl+C / Ctrl+V |
| 删除 / 撤销 / 重做 | Del / Ctrl+Z / Ctrl+Y |
| 固定 / 解除固定 | Ctrl+F |
| 切换模型显示 | 空格 |
| 设置 | Ctrl+,，可修改快捷键 |

支持 POSCAR/CONTCAR、XYZ、XDATCAR、CIF、LAMMPS data/dump，以及 vasprun.xml、OUTCAR、extxyz、CHGCAR/ELFCAR 等数据导入。VASP 单帧导出保留固定标记，可选坐标类型和重新赋温；轨迹导出支持间隔抽帧。切面默认 (111)，普通 bulk 默认使用常规晶胞基准。

## 更新版本

关闭软件，将新版 ZIP 解压后的程序内容复制到原 VBuild 目录，选择替换同名文件，再启动即可。发布内容不包含个人 work、data、模型或 Python 环境，原有设置、结构和模型保留；不用重新安装现有依赖。使用 GitHub Desktop 克隆的用户，也可关闭软件后 Fetch origin → Pull origin，再启动。新增计算依赖在设置中按需安装。

## 配置与反馈

- Python 路径、窗口习惯、样式和日志保存在 `work/`，不要把它整体当缓存删除。
- 默认结构下载位置为 `data/`，模型为 `MLIP/`；这些运行数据均不进入 Git。
- 换 Python：按住 Shift 双击 VBuild.exe，或在设置中选择并重启。
- 启动/安装失败时保留实际错误，日志在 `work/log/`。反馈时提供 Windows/Python 版本、操作过程与相关错误；提交 GitHub Issue 前去掉个人信息和 API key。
- 本版用于异机安装与功能测试。没有内置 Python，也没有自动安装 Python 的功能。

## 源码与启动器

运行源码在 `src/vbuild/`，`pyproject.toml` 记录版本及依赖范围。也可用自己选择的 Python 执行 `python -B run.py`；不必先把 VBuild 安装成 Python 包。

启动器源码为 `tools/launch.cs`。需要重新编译时，在 Windows PowerShell 5.1 执行 `powershell -NoProfile -ExecutionPolicy Bypass -File tools/launch.ps1 -Output VBuild-new.exe`。它只编译小型启动器，不打包解释器或库；已有输出不会覆盖。

项目使用 PySide6、PyVista/VTK、ASE、spglib 等组件；模型与第三方库的使用条件以各自上游说明为准。
