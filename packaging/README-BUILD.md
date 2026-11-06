# FloatingWordBall Windows 打包说明

本目录提供可复现的 Windows 打包流程，产物不需要用户另装 Python。

## 前置条件

- Windows 10/11 x64
- Python 3.11（命令 `py -3.11` 可用）
- 首次构建可联网（用于安装 PyInstaller 和从 Tauri binary-releases 下载 NSIS 3.13）；离线构建请用 `-NsisExe` 指定本机 `makensis.exe`
- 安装器编译会自动在 `%PUBLIC%` 下创建 ASCII 临时目录，避免中文用户名路径触发 NSIS `Internal compiler error #12345`

## 一键构建

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1 -Version 1.0.0
```

脚本会：

1. 检查并安装 `requirements.txt` 与 `packaging\requirements-build.txt`；
2. 用 PyInstaller `--onedir --windowed` 构建 `packaging\dist\FloatingWordBall\`；
3. 打入 `vocab/`、`importance.json`、`LICENSES/`、`THIRD_PARTY_NOTICES.md`、`README.md`；
4. 生成便携版 ZIP（含 `portable.flag`，数据保存在 EXE 旁）和 NSIS 按用户安装包；
5. 输出 `packaging\artifacts\SHA256SUMS.txt`。

## 产物

- `packaging\artifacts\FloatingWordBall-Portable-1.0.0.zip`
- `packaging\artifacts\FloatingWordBall-Setup-1.0.0.exe`
- `packaging\artifacts\SHA256SUMS.txt`

`packaging\artifacts\`、`build\`、`dist\`、`stage\`、`tools\` 均不提交 Git；正式发布时上传到 GitHub Releases。

## 数据与权限

- 安装版：`%APPDATA%\FloatingWordBall`（配置、单词本、详情缓存、日志、可写词库副本）。
- 便携版：EXE 同级目录；目录必须可写。
- 源码运行：仍使用仓库目录，兼容现有开发数据。
- 旧数据：安装版首次启动会从旧 EXE 目录迁移 `config.json`、`wordbooks.json`、`details_cache.json`，不覆盖已有文件。

## 截图

真实 Qt 界面截图由同一程序离屏渲染：

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
py -3.11 -B packaging\capture_screenshots.py
```

输出到 `packaging\screenshots\`。

## 本机启动验证

```powershell
$exe = "packaging\dist\FloatingWordBall\FloatingWordBall.exe"
$p = Start-Process -FilePath $exe -PassThru
Start-Sleep -Seconds 5
if ($p.HasExited) { throw "FloatingWordBall exited early: $($p.ExitCode)" }
$p | Stop-Process
```

便携版验证：解压 ZIP 后运行 `FloatingWordBall.exe`，确认同目录出现 `config.json`、`FloatingWordBall.log` 和 `vocab\`。
