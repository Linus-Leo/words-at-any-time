#!/usr/bin/env python3
"""FloatingWordBall 官网的可复现生产构建。

只依赖 Python 标准库：
  1. 校验 index.html 引用的本地静态资源是否齐全；
  2. 把源码复制到 website/dist/，并在 dist 内对 CSS 做保守压缩；
  3. 生成 dist/build-manifest.json 记录产物大小。

用法（仓库根目录）：
    python3 website/build.py
GitHub Actions 随后上传 website/dist 交给 GitHub Pages。
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DIST = HERE / "dist"

# 复制进 dist 的源码条目（相对 website/）
COPY_ITEMS = ["index.html", "assets/css", "assets/js", "assets/screenshots"]
# 允许缺失的可选资源（尚未提供时不算失败）
OPTIONAL_PREFIXES = ("assets/screenshots/",)


def strip_css_comments_and_squash(css: str) -> str:
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    css = re.sub(r"\s+", " ", css)
    css = re.sub(r"\s*([{};,>])\s*", r"\1", css)
    css = css.replace(";}", "}").strip()
    return css


def collect_referenced_assets(index_html: str) -> list[str]:
    refs: list[str] = []
    for m in re.finditer(r'(?:src|href)\s*=\s*"([^"]+)"', index_html):
        url = m.group(1).strip()
        if not url:
            continue
        if url.startswith(("http://", "https://", "//", "#", "data:", "mailto:")):
            continue
        refs.append(url.split("?")[0].split("#")[0])
    return sorted(set(refs))


def main() -> int:
    if not (HERE / "index.html").is_file():
        print("ERROR: 找不到 website/index.html", file=sys.stderr)
        return 1

    # 1) 资源引用校验（针对源目录）
    index_html = (HERE / "index.html").read_text(encoding="utf-8")
    referenced = collect_referenced_assets(index_html)
    missing: list[str] = []
    optional_missing: list[str] = []
    for rel in referenced:
        target = HERE / rel
        if not target.exists():
            if rel.startswith(OPTIONAL_PREFIXES):
                optional_missing.append(rel)
            else:
                missing.append(rel)
    if missing:
        print("ERROR: index.html 引用了不存在的本地资源：", file=sys.stderr)
        for rel in missing:
            print("  - " + rel, file=sys.stderr)
        return 1

    # 2) 组装 dist
    if DIST.exists():
        shutil.rmtree(DIST)
    DIST.mkdir(parents=True, exist_ok=True)

    for item in COPY_ITEMS:
        src = HERE / item
        if not src.exists():
            continue
        dst = DIST / item
        if src.is_dir():
            shutil.copytree(src, dst)
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)

    # 3) 在 dist 内保守压缩 CSS（保持同名路径，引用不变）
    for css in (DIST / "assets" / "css").glob("*.css"):
        original = css.read_text(encoding="utf-8")
        css.write_text(strip_css_comments_and_squash(original), encoding="utf-8")

    # 3.4) 截图存在性：缺失的截图不发起请求，直接渲染占位，避免 404 噪音
    shots_dir = DIST / "assets" / "screenshots"

    def _shot_repl(m):
        fallback, num = m.group(1), m.group(2)
        if (shots_dir / ("shot-%s.png" % num)).exists():
            return m.group(0)
        return '<div class="shot-frame is-empty" data-fallback="%s">\n            </div>' % fallback

    idx_text = (DIST / "index.html").read_text(encoding="utf-8")
    idx_text = re.sub(
        r'<div class="shot-frame" data-fallback="([^"]*)">\s*'
        r'<img src="assets/screenshots/shot-(\d)\.png"[^>]*>\s*</div>',
        _shot_repl,
        idx_text,
    )
    (DIST / "index.html").write_text(idx_text, encoding="utf-8")

    # 3.5) 资源指纹，避免浏览器缓存旧样式/脚本
    def file_hash(paths):
        h = hashlib.sha256()
        for p in sorted(paths):
            h.update(p.read_bytes())
        return h.hexdigest()[:10]

    css_hash = file_hash((DIST / "assets" / "css").glob("*.css"))
    js_hash = file_hash((DIST / "assets" / "js").glob("*.js"))
    idx_path = DIST / "index.html"
    idx = idx_path.read_text(encoding="utf-8")
    idx = idx.replace("assets/css/style.css", "assets/css/style.css?v=" + css_hash)
    idx = idx.replace("assets/js/main.js", "assets/js/main.js?v=" + js_hash)
    idx_path.write_text(idx, encoding="utf-8")

    # 4) 产物清单
    files = sorted(p for p in DIST.rglob("*") if p.is_file())
    manifest = {
        "generator": "website/build.py",
        "files": [
            {
                "path": p.relative_to(DIST).as_posix(),
                "bytes": p.stat().st_size,
            }
            for p in files
        ],
    }
    total = sum(f["bytes"] for f in manifest["files"])
    manifest["total_bytes"] = total
    (DIST / "build-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    print("FloatingWordBall 官网构建完成 -> " + str(DIST.relative_to(HERE.parent)))
    for f in manifest["files"]:
        print(f"  {f['bytes']:>8}  {f['path']}")
    print(f"  {'-' * 8}")
    print(f"  {total:>8}  合计（{len(files)} 个文件）")
    if optional_missing:
        print("提示：以下可选截图尚未提供，将回退为占位：" + ", ".join(optional_missing))
    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
