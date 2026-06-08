# PrismBI Build Guide

## 架构

PrismBI Tauri 桌面 App 是一个**纯前端壳子**，连接外部后端：

```
┌──────────────────────────────┐
│  PrismBI App (Tauri Shell)  │
│  ┌─────────────────────────┐│
│  │  WebView2 / WebKitGTK  ││
│  │  → http://localhost:5173││  ← 前端地址可修改
│  └─────────────────────────┘│
│  系统托盘菜单:               │
│  - Show PrismBI             │
│  - Frontend URL...          │  ← 修改前端地址
│  - Quit                     │
└──────────────────────────────┘
         │HTTP
┌─────────▼──────────────────┐
│  Python 后端 (独立运行)     │
│  - FastAPI API              │
│  - Next.js 前端             │
│  - DuckDB 数据库            │
└────────────────────────────┘
```

### 修改前端地址

三种方式（优先级从高到低）：

1. **托盘菜单** — 右键托盘图标 → "Frontend URL..." → 输入新地址
2. **环境变量** — `PRISMBI_FRONTEND_URL=http://192.168.1.100:5173`
3. **配置文件** — 编辑：
   - Linux: `~/.config/ai.prism.bi/frontend_url.txt`
   - macOS: `~/Library/Application Support/ai.prism.bi/frontend_url.txt`
   - Windows: `%APPDATA%\ai.prism.bi\frontend_url.txt`

---

## 编译: Tauri 桌面 App

### Linux 系统依赖 (openSUSE)

```bash
sudo zypper install libwebkit2gtk-4_1-devel libgtk-3-devel \
  libappindicator3-devel librsvg-devel patchelf
```

### 编译

```bash
# macOS/Linux
./scripts/build-desktop.sh

# Windows
scripts\build-desktop.bat
```

### 输出

| 平台 | 位置 |
|------|------|
| Linux .deb | `src-tauri/target/release/bundle/deb/PrismBI_4.0.0_amd64.deb` |
| Linux .rpm | `src-tauri/target/release/bundle/rpm/PrismBI-4.0.0-1.x86_64.rpm` |
| macOS .dmg | `src-tauri/target/release/bundle/dmg/PrismBI_4.0.0_x64.dmg` |
| Windows .exe | `src-tauri/target/release/bundle/nsis/PrismBI_4.0.0_x64-setup.exe` |

---

## 编译: Legacy App (Win7+ pywebview)

```bash
# macOS/Linux
./scripts/build-legacy-server.sh

# Windows
scripts\build-legacy-server.bat
```

输出: `dist/prismbi-legacy/`

---

## 对比

| | Tauri 桌面版 | Legacy 版 (pywebview) |
|---|---|---|
| Win7 | ✗ | ✓ |
| Win10+ | ✓ | ✓ |
| 体积 | ~2MB (仅壳子) | ~200MB (含后端) |
| 后端 | 外部独立运行 | 打包在一起 |
| 修改前端地址 | 托盘菜单/环境变量/配置文件 | 命令行参数 `--port` |

## 版本

**4.0.0**