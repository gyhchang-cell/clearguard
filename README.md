# ClearGuard

**空间有数，清理有据。** An offline, consent-first duplicate finder for macOS and Windows.

> 0.2.0 experimental preview / 实验预览版。先用可恢复的测试文件体验，再处理重要资料。

ClearGuard scans a folder you choose or discovers local disks with Whole-computer Scan, shows exact duplicate evidence and lets you review a concrete cleanup plan. Nothing is selected by default. Cleanup sends explicitly approved duplicates to the native Trash / Recycle Bin. It never empties it or falls back to permanent deletion.

## 中文说明

- **全机扫描**：点击新增按钮自动扫描已挂载的本地磁盘（含 USB），统一查找跨磁盘重复文件。macOS 避免重复扫描 APFS 内部卷；Windows 跳过网络驱动器。保护目录、无权限目录、网络/虚拟文件系统、离线或未挂载磁盘不在覆盖范围内；界面与导出报告列出范围、排除项和错误。
- **重复核验**：文件大小分组 → 完整 SHA-256 → 独立逐字节比较。名字相同不等于重复。
- **执行前复核**：检查文件身份、大小、修改/元数据时间、哈希与内容，保留副本也要重新核验；变化即停止。
- **明确同意**：手动选择 → 展示完整路径、保留副本、数量与大小 → 勾选确认 → 保存新的本地审计日志 → 执行。
- **至少留一份**：界面固定保留每组第一份，底层拒绝清空整组。第一份只按遍历顺序确定，不代表更重要。
- **保守排除**：系统目录、开发项目、应用包、已知云同步目录、云占位文件、符号链接、Windows 联接/重解析点、硬链接。
- **“没用”不凭猜测**：大于 100 MiB 或修改时间超过 180 天的文件仅列为查看建议，不提供一键删除；日期不能证明没人使用。
- **磁盘空间与 RAM 分开**：本项目分析文件占用的磁盘空间，不杀进程、不清运行内存。
- **离线与隐私**：运行时不联网、不上传路径、不遥测。导出的报告和审计日志包含完整本机路径，请勿直接上传到公开 issue。

### 快速运行

安装 Python 3.11+（带 Tk；推荐 python.org 安装包）。在项目目录执行：

```sh
python -m venv .venv
```

macOS:

```sh
.venv/bin/python -m pip install --cache-dir .cache/pip -e .
.venv/bin/python run.py
```

Windows PowerShell（无需修改执行策略）：

```powershell
.venv\Scripts\python.exe -m pip install --cache-dir .cache/pip -e .
.venv\Scripts\python.exe run.py
```

点击「全机扫描」即可开始自动发现并扫描本地磁盘，无需先选择文件夹；也可选择 Downloads、照片导出文件夹等个人目录进行局部扫描。双击或空格切换重复文件的选择，点击「检查所选并清理…」。取消或关闭确认窗口不会清理。扫描可以随时取消，未完成结果不能执行清理。

### 已知边界

1. **内容相同仍可能各有用途**，例如不同目录中的附件和备份；必须自己判断路径用途。程序不会承诺“绝不误删”。
2. 回收站内的文件仍占磁盘；本程序不会自动腾空回收站。逻辑大小不等于实际可回收大小，APFS 克隆、压缩、稀疏文件尤其如此。
3. 全机扫描会读取大量文件，耗时和内存取决于文件数量，可随时取消。它不绕过保护或权限，并非每个字节均覆盖；权限拒绝会显示问题，保护目录整棵跳过。开发项目根据常见根标记识别，不能识别所有工程。
4. 操作系统没有为跨平台 Trash 提供原子“验证并移动”接口。停止其他程序写入/同步所选路径；仍存在最后复核与移动之间的竞争窗口，不适合攻击者可修改的共享目录。
5. 已知云目录只按名称和系统占位标志保守排除；自定义同步位置无法全部识别，请自行避开。
6. 批量操作不是事务：中途失败停止后，已经移入回收站的文件不会自动恢复；查看日志与系统回收站。日志存在 `intent` 而无结果时，应人工核查该路径。
7. 不请求管理员权限、不强行绕过 macOS 隐私设置；外接盘不支持回收站时清理失败，不永久删除。
8. 当前 UI 为中文；macOS 构建未进行 Apple 签名/公证，Windows 构建未签名。请勿把预览版当作经过商业签名的正式安装器。

## Development / 开发

```sh
python -m unittest discover -s tests -v
python tests/gui_smoke.py
python -m pip install --cache-dir .cache/pip -e ".[build]"
python build.py
```

PyInstaller must run on each target OS. GitHub Actions tests and builds on macOS Apple Silicon (`macos-15`), Intel (`macos-15-intel`) and Windows x64 (`windows-latest`), then uploads native artifacts. The workflow is not evidence of successful validation until its run is green. Download the platform ZIP from Releases, extract it, and run `ClearGuard.app` or `ClearGuard/ClearGuard.exe`; do not move the executable away from its companion files.

All configurable caches, virtual environments, build products and disposable fixtures stay inside the project. Safety tests use a fake trash backend and disposable fixtures; they never touch personal files or the real Trash. The GUI smoke test opens the actual native Tk UI and confirms empty default selection, the review dialog, and the Whole-computer Scan button/worker flow using a mocked scan result, without scanning real disks or executing cleanup.

### Safety model

`core.scan` produces immutable file snapshots. `make_plan` accepts only duplicate paths from that scan and rejects selecting every member. `execute` requires a plan-bound explicit-consent token with a ten-minute expiry; this is an in-process guard, not authentication against hostile Python code. All selected files and keepers pass preflight before any move. An exclusive new JSONL audit file must be writable first; each move has a flushed and fsynced intent and result. Failure stops the remaining batch. Native Trash failure has no deletion fallback.

Scope for later releases: per-folder exclusions, configurable large/old thresholds, English UI, disk visualization, signed installers, accessibility testing and broader filesystem validation. Automated “unused file” removal is deliberately outside this preview's scope.

Contributions: see [CONTRIBUTING.md](CONTRIBUTING.md). Security: see [SECURITY.md](SECURITY.md).

## License

MIT. See [LICENSE](LICENSE).
