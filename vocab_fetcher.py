#!/usr/bin/env python3
"""Download, normalize and initialize local vocabularies."""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Iterable
from urllib.error import URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_VOCAB_DIR = BASE_DIR / "vocab"
SOURCE_ROOT = "https://raw.githubusercontent.com/KyleBing/english-vocabulary/master/json/"
VOCABULARIES = {
    "CET4": "3-CET4-顺序.json",
    "CET6": "4-CET6-顺序.json",
    "Kaoyan": "5-考研-顺序.json",
    "TOEFL": "6-托福-顺序.json",
}
CORE_WORDS = [
    ("ability", "əˈbɪləti", "n. 能力；才能", "learning ability", "学习能力"),
    ("achieve", "əˈtʃiːv", "v. 实现；达到", "achieve a goal", "实现目标"),
    ("adapt", "əˈdæpt", "v. 适应；改编", "adapt to change", "适应变化"),
    ("ambition", "æmˈbɪʃən", "n. 雄心；抱负", "realize an ambition", "实现抱负"),
    ("analyze", "ˈænəlaɪz", "v. 分析", "analyze the data", "分析数据"),
    ("approach", "əˈproʊtʃ", "v. 接近；处理 n. 方法", "a practical approach", "实用的方法"),
    ("available", "əˈveɪləbəl", "adj. 可获得的；有空的", "available resources", "可用资源"),
    ("balance", "ˈbæləns", "n. 平衡 v. 使平衡", "keep a balance", "保持平衡"),
    ("benefit", "ˈbenɪfɪt", "n. 益处 v. 有益于", "benefit from experience", "从经验中受益"),
    ("challenge", "ˈtʃælɪndʒ", "n. 挑战 v. 向……挑战", "face a challenge", "面对挑战"),
    ("communicate", "kəˈmjuːnɪkeɪt", "v. 交流；传达", "communicate clearly", "清晰地沟通"),
    ("compare", "kəmˈper", "v. 比较；相比", "compare with others", "与他人比较"),
    ("confidence", "ˈkɑːnfɪdəns", "n. 信心；信任", "build confidence", "建立信心"),
    ("consider", "kənˈsɪdər", "v. 考虑；认为", "consider a possibility", "考虑一种可能性"),
    ("create", "kriˈeɪt", "v. 创造；引起", "create opportunities", "创造机会"),
    ("decision", "dɪˈsɪʒən", "n. 决定；抉择", "make a decision", "作出决定"),
    ("develop", "dɪˈveləp", "v. 发展；培养", "develop a habit", "培养习惯"),
    ("discover", "dɪˈskʌvər", "v. 发现；发觉", "discover a solution", "发现解决办法"),
    ("efficient", "ɪˈfɪʃənt", "adj. 高效的", "efficient learning", "高效学习"),
    ("effort", "ˈefərt", "n. 努力；尝试", "make an effort", "付出努力"),
    ("encourage", "ɪnˈkɜːrɪdʒ", "v. 鼓励；促进", "encourage creativity", "鼓励创新"),
    ("energy", "ˈenərdʒi", "n. 能量；精力", "save energy", "节约能源"),
    ("environment", "ɪnˈvaɪrənmənt", "n. 环境", "protect the environment", "保护环境"),
    ("essential", "ɪˈsenʃəl", "adj. 必要的；本质的", "essential skills", "必备技能"),
    ("evidence", "ˈevɪdəns", "n. 证据；根据", "strong evidence", "有力证据"),
    ("experience", "ɪkˈspɪriəns", "n. 经验；经历 v. 体验", "gain experience", "积累经验"),
    ("explore", "ɪkˈsplɔːr", "v. 探索；探究", "explore new ideas", "探索新想法"),
    ("focus", "ˈfoʊkəs", "n. 焦点 v. 集中", "focus on the task", "专注于任务"),
    ("goal", "ɡoʊl", "n. 目标；进球", "set a clear goal", "设定明确目标"),
    ("habit", "ˈhæbɪt", "n. 习惯", "form a good habit", "养成好习惯"),
    ("improve", "ɪmˈpruːv", "v. 改善；提高", "improve efficiency", "提高效率"),
    ("independent", "ˌɪndɪˈpendənt", "adj. 独立的；自主的", "independent thinking", "独立思考"),
    ("knowledge", "ˈnɑːlɪdʒ", "n. 知识；了解", "acquire knowledge", "获取知识"),
    ("manage", "ˈmænɪdʒ", "v. 管理；设法做到", "manage time", "管理时间"),
    ("measure", "ˈmeʒər", "v. 测量；衡量 n. 措施", "measure progress", "衡量进展"),
    ("method", "ˈmeθəd", "n. 方法；条理", "an effective method", "有效的方法"),
    ("motivation", "ˌmoʊtɪˈveɪʃən", "n. 动力；动机", "maintain motivation", "保持动力"),
    ("necessary", "ˈnesəseri", "adj. 必要的；必需的", "necessary conditions", "必要条件"),
    ("opportunity", "ˌɑːpərˈtuːnəti", "n. 机会；时机", "seize the opportunity", "抓住机会"),
    ("organize", "ˈɔːrɡənaɪz", "v. 组织；整理", "organize information", "整理信息"),
    ("patient", "ˈpeɪʃənt", "adj. 有耐心的 n. 病人", "be patient with yourself", "对自己有耐心"),
    ("progress", "ˈprɑːɡres", "n. 进步；进展", "make steady progress", "稳步进步"),
    ("purpose", "ˈpɜːrpəs", "n. 目的；用途", "serve a purpose", "发挥作用"),
    ("reduce", "rɪˈduːs", "v. 减少；降低", "reduce distractions", "减少干扰"),
    ("reflect", "rɪˈflekt", "v. 反思；反映", "reflect on experience", "反思经验"),
    ("require", "rɪˈkwaɪər", "v. 需要；要求", "require practice", "需要练习"),
    ("responsible", "rɪˈspɑːnsəbəl", "adj. 负责的；有责任的", "be responsible for", "对……负责"),
    ("solution", "səˈluːʃən", "n. 解决方案；答案", "find a solution", "找到解决方案"),
    ("strategy", "ˈstrætədʒi", "n. 策略；规划", "a learning strategy", "学习策略"),
    ("success", "səkˈses", "n. 成功；成就", "achieve success", "取得成功"),
]


def fallback_words() -> list[dict[str, Any]]:
    return [
        {"word": word, "phonetic": f"/{phonetic}/", "translation": translation,
         "phrases": [{"phrase": phrase, "translation": meaning}]}
        for word, phonetic, translation, phrase, meaning in CORE_WORDS
    ]


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def normalize_entry(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    word = _text(item.get("word") or item.get("headWord"))
    if not word:
        return None
    raw_translations = item.get("translations", item.get("translation", ""))
    translations = []
    if isinstance(raw_translations, str):
        translations = [raw_translations.strip()]
    elif isinstance(raw_translations, list):
        for entry in raw_translations:
            if isinstance(entry, str):
                translations.append(entry.strip())
            elif isinstance(entry, dict):
                meaning = _text(entry.get("translation") or entry.get("tranCn") or entry.get("definition"))
                kind = _text(entry.get("type") or entry.get("pos"))
                if meaning:
                    translations.append(f"{kind} {meaning}" if kind else meaning)
    translation = "\n".join(part for part in translations if part) or "释义暂缺"
    phonetic = _text(item.get("phonetic") or item.get("usphone") or item.get("ukphone")
                     or item.get("usPhone") or item.get("ukPhone"))
    if phonetic and not (phonetic.startswith("/") and phonetic.endswith("/")):
        phonetic = f"/{phonetic}/"
    phrases = []
    raw_phrases = item.get("phrases", [])
    if isinstance(raw_phrases, list):
        for entry in raw_phrases[:6]:
            if isinstance(entry, str):
                phrase, meaning = entry.strip(), ""
            elif isinstance(entry, dict):
                phrase = _text(entry.get("phrase") or entry.get("pContent"))
                meaning = _text(entry.get("translation") or entry.get("pCn"))
            else:
                continue
            if phrase:
                phrases.append({"phrase": phrase, "translation": meaning})
    return {"word": word, "phonetic": phonetic, "translation": translation, "phrases": phrases}


def normalize_vocab(payload: Any) -> list[dict[str, Any]]:
    entries = payload.get("words", []) if isinstance(payload, dict) else payload
    if not isinstance(entries, list):
        raise ValueError("词库顶层必须是数组，或包含 words 数组")
    result, seen = [], set()
    for item in entries:
        entry = normalize_entry(item)
        if entry and entry["word"].casefold() not in seen:
            result.append(entry)
            seen.add(entry["word"].casefold())
    if not result:
        raise ValueError("词库中没有有效词条")
    return result


def write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=1)
            stream.write("\n")
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def load_vocab(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig") as stream:
        return normalize_vocab(json.load(stream))


def _valid_vocab(path: Path) -> bool:
    try:
        return bool(load_vocab(path))
    except (OSError, ValueError, json.JSONDecodeError):
        return False


def ensure_local_vocab(vocab_dir: Path = DEFAULT_VOCAB_DIR) -> list[str]:
    """Synchronously create missing vocabularies without network access."""
    created = []
    for name in VOCABULARIES:
        path = vocab_dir / f"{name}.json"
        if not _valid_vocab(path):
            write_json_atomic(path, fallback_words())
            created.append(name)
    return created


def download_vocab(name: str, timeout: float = 20.0) -> list[dict[str, Any]]:
    url = SOURCE_ROOT + quote(VOCABULARIES[name])
    last_error = None
    for attempt in range(2):
        try:
            request = Request(url, headers={"User-Agent": "FloatingWordBall/1.0"})
            with urlopen(request, timeout=timeout) as response:
                payload = json.load(response)
            return normalize_vocab(payload)
        except (OSError, URLError, ValueError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt == 0:
                time.sleep(0.6)
    raise RuntimeError(f"{name} 下载或清洗失败：{last_error}")


def fetch_vocabularies(vocab_dir: Path = DEFAULT_VOCAB_DIR, names: Iterable[str] | None = None,
                       force: bool = False, offline: bool = False) -> dict[str, str]:
    selected = list(names or VOCABULARIES)
    unknown = set(selected) - set(VOCABULARIES)
    if unknown:
        raise ValueError(f"未知词库：{', '.join(sorted(unknown))}")
    statuses = {}
    for name in selected:
        path = vocab_dir / f"{name}.json"
        if not force and _valid_vocab(path):
            statuses[name] = "已存在，跳过"
            continue
        if offline:
            write_json_atomic(path, fallback_words())
            statuses[name] = f"已生成 {len(CORE_WORDS)} 词离线词库"
            continue
        try:
            words = download_vocab(name)
            write_json_atomic(path, words)
            statuses[name] = f"已下载 {len(words)} 个词条"
        except RuntimeError as exc:
            if _valid_vocab(path):
                statuses[name] = f"网络失败，保留现有词库（{exc}）"
            else:
                write_json_atomic(path, fallback_words())
                statuses[name] = f"网络失败，已使用 {len(CORE_WORDS)} 词离线兜底（{exc}）"
    return statuses


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="获取并清洗 english-vocabulary 词库")
    parser.add_argument("--vocab", nargs="+", choices=list(VOCABULARIES), help="只处理指定词库")
    parser.add_argument("--offline", action="store_true", help="不联网，生成内置高频词库")
    parser.add_argument("--force", action="store_true", help="覆盖已存在的有效词库")
    parser.add_argument("--dir", type=Path, default=DEFAULT_VOCAB_DIR, help="词库保存目录")
    args = parser.parse_args(argv)
    try:
        statuses = fetch_vocabularies(args.dir, args.vocab, args.force, args.offline)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"初始化失败：{exc}\n")
    for name, status in statuses.items():
        print(f"[{name}] {status}")
    print(f"词库目录：{args.dir.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
