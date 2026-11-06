# FloatingWordBall 下载官网

FloatingWordBall 的 GitHub Pages 下载与使用说明官网。纯静态站点，无第三方运行时依赖。

## 目录结构

```
website/
├── index.html               # 单页站点
├── build.py                 # 可复现生产构建（仅标准库）
├── assets/
│   ├── css/style.css
│   ├── js/main.js           # 胶囊演示交互
│   └── screenshots/         # 真实界面截图（由打包方提供）
└── dist/                    # 构建产物，不入库
```

## 本地预览与构建

```bash
# 构建到 website/dist
python3 website/build.py

# 本地起一个静态服务器预览
python3 -m http.server 8941 --directory website/dist
# 打开 http://127.0.0.1:8941/
```

## 截图

页面 `#guide` 区引用 `assets/screenshots/shot-1.png` ~ `shot-4.png`（对应 `packaging/screenshots/` 的展开态、收起态、单词本设置、我的单词本四张真实截图）。
截图由应用打包方导出到 `packaging/screenshots/` 后按顺序复制为本目录的 `shot-1..4.png`；缺失时构建会跳过该图并显示占位块，不影响构建。

## 下载链接

下载按钮指向正式 GitHub Releases 的 `v1.0.0` 资产：

- `releases/download/v1.0.0/FloatingWordBall-Setup-1.0.0.exe`
- `releases/download/v1.0.0/FloatingWordBall-Portable-1.0.0.zip`

资产命名与打包方约定一致，发布 `v1.0.0` 后即可直接下载。

## 部署

`.github/workflows/pages.yml` 在 `main` 分支且 `website/**` 变更时，运行 `build.py`，
上传 `website/dist` 并通过 GitHub Pages 发布。仓库 Settings → Pages 的 Source 需设为
「GitHub Actions」。
