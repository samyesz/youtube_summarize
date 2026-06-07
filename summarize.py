#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
YouTube 视频总结工具 (Groq API, 免费)

用法:
    python summarize.py <youtube_url>
    python summarize.py <youtube_url> --lang zh              # 仅中文总结
    python summarize.py <youtube_url> --lang en              # 仅英文总结
    python summarize.py <youtube_url> --lang both            # 中英对照总结 (默认)
    python summarize.py <youtube_url> --strategy bullet      # 直接分点（快）
    python summarize.py <youtube_url> --strategy cod         # Chain-of-Density / 密度链 迭代精炼（默认，质量更高）
    python summarize.py <youtube_url> --strategy outline     # 分章节大纲 + 分层抽象（长视频自动 Map-Reduce 分块）
    python summarize.py <youtube_url> --no-transcript        # 不附字幕原文
    python summarize.py <youtube_url> --model llama-3.1-8b-instant  # 换模型
    python summarize.py <youtube_url> -o out.txt             # 同时保存到文件

需要 Groq API key (完全免费):
    1. 打开 https://console.groq.com/keys 登录后点 "Create API Key"
    2. 设置环境变量 (一次性永久生效):
         setx GROQ_API_KEY "你的key"
       然后重开终端。

默认模型: llama-3.3-70b-versatile (免费, 每天 1000 次)
"""

import argparse
import os
import re
import sys
import glob
import tempfile

# Windows 默认终端是 cp1252/gbk, 强制 UTF-8 输出
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass

DEFAULT_MODEL = "llama-3.3-70b-versatile"
DEFAULT_STRATEGY = "cod"

PROMPTS = {
    "both": (
        "请对以下视频字幕进行分点总结，规则如下：\n"
        "1. 第一条：用一句完整、流畅的话概括视频主题，不要出现'因此'、'由于'等不必要的连接词\n"
        "2. 后续每条只描述一个独立的知识点或论点，不得将多个概念合并到同一条\n"
        "3. 每个要点言简意赅，一句话说清楚\n"
        "4. 视频涉及几个知识点就写几条，不增不减\n"
        "5. 先输出【中文总结】，再输出【English Summary】，两部分内容一一对应\n"
        "6. 每条以 • 开头"
    ),
    "zh": (
        "请对以下视频字幕进行分点总结，规则如下：\n"
        "1. 第一条：用一句完整、流畅的话概括视频主题，不要出现'因此'、'由于'等不必要的连接词\n"
        "2. 后续每条只描述一个独立的知识点或论点，不得将多个概念合并到同一条\n"
        "3. 每个要点言简意赅，一句话说清楚\n"
        "4. 视频涉及几个知识点就写几条，不增不减\n"
        "5. 每条以 • 开头"
    ),
    "en": (
        "Summarize the video subtitles below using bullet points. Rules:\n"
        "1. First bullet: one clean, complete sentence summarizing the video topic\n"
        "2. Each subsequent bullet covers exactly ONE distinct concept or argument — never merge multiple ideas into one bullet\n"
        "3. Keep each bullet concise — one sentence\n"
        "4. Number of bullets matches the number of actual key points in the video\n"
        "5. Each bullet starts with •"
    ),
}

# ── 分章节大纲 + 分层抽象 的最终格式化提示词 ──────────────────────
# 在已得到「分章节要点」之后，整理成「一句话总览 → 核心要点 → 分章节详述」
# 三层结构，让读者按需选择阅读粒度。
OUTLINE_PROMPTS = {
    "both": (
        "请基于以下【分章节要点】整理成结构化总结。\n"
        "先输出【中文总结】，再输出【English Summary】，两部分内容一一对应。\n"
        "每部分内部都按如下三层结构输出：\n"
        "1. **一句话总览（TL;DR）**：用一句完整、流畅的话概括整个视频\n"
        "2. **核心要点**：3-6 条以 • 开头的要点，覆盖最重要的结论\n"
        "3. **分章节详述**：按视频推进顺序划分若干章节，每章一个 ### 小标题，"
        "标题下用 • 列出该章要点\n"
        "要求：章节顺序符合视频逻辑，内容不重复、不遗漏关键信息，每条要点言简意赅。"
    ),
    "zh": (
        "请基于以下【分章节要点】整理成结构化中文总结，按如下三层结构输出：\n"
        "1. **一句话总览（TL;DR）**：用一句完整、流畅的话概括整个视频\n"
        "2. **核心要点**：3-6 条以 • 开头的要点，覆盖最重要的结论\n"
        "3. **分章节详述**：按视频推进顺序划分若干章节，每章一个 ### 小标题，"
        "标题下用 • 列出该章要点\n"
        "要求：章节顺序符合视频逻辑，内容不重复、不遗漏关键信息，每条要点言简意赅。"
    ),
    "en": (
        "Based on the [chapter notes] below, produce a structured summary with this three-layer format:\n"
        "1. **TL;DR**: one clean, complete sentence summarizing the whole video\n"
        "2. **Key points**: 3-6 bullets (start each with •) covering the most important takeaways\n"
        "3. **Chapter-by-chapter**: split into chapters following the video's flow; "
        "each chapter gets a ### heading with • bullets underneath\n"
        "Requirements: chapters follow the video's logic, no duplication, no missing key info, each bullet concise."
    ),
}

LANG_NAMES = {
    "zh": "中文",
    "zh-Hans": "中文（简体）",
    "zh-Hant": "中文（繁体）",
    "en": "English",
}


def get_client():
    from groq import Groq

    key = os.environ.get("GROQ_API_KEY")
    if not key:
        sys.exit(
            "错误: 未找到 GROQ_API_KEY。\n"
            "请到 https://console.groq.com/keys 免费申请，然后运行:\n"
            '    setx GROQ_API_KEY "你的key"\n'
            "再重开终端。"
        )
    return Groq(api_key=key)


def chat(client, model, system, user):
    """调用 Groq，返回文本。"""
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    return resp.choices[0].message.content


def fetch_all_transcripts(url):
    """
    用 yt-dlp 抓取字幕，返回:
      transcripts: dict  { lang_key: text }
      title:       str
      orig_lang:   str   视频原始语言代码 (可能为 None)
    """
    import yt_dlp

    tmp = tempfile.mkdtemp(prefix="ytsum_")
    outtmpl = os.path.join(tmp, "sub")
    opts = {
        "skip_download": True,
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": ["zh-Hans", "zh-Hant", "zh", "en"],
        "subtitlesformat": "vtt",
        "outtmpl": outtmpl,
        "quiet": True,
        "no_warnings": True,
        "ignoreerrors": True,
    }
    title = ""
    orig_lang = None
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        if info is None:
            return {}, "", None
        title = info.get("title", "")
        orig_lang = info.get("language") or info.get("original_language")

    vtts = glob.glob(os.path.join(tmp, "*.vtt"))
    transcripts = {}
    for vtt_path in vtts:
        fname = os.path.basename(vtt_path)
        m = re.match(r"sub\.(.+)\.vtt$", fname)
        if not m:
            continue
        lang_code = m.group(1)
        if lang_code.startswith("zh"):
            key = "zh"
        elif lang_code.startswith("en"):
            key = "en"
        else:
            key = lang_code
        if key not in transcripts:
            text = parse_vtt(vtt_path)
            if text:
                transcripts[key] = text

    return transcripts, title, orig_lang


def parse_vtt(path):
    """把 VTT 字幕解析成去重后的纯文本。"""
    with open(path, encoding="utf-8") as f:
        raw = f.read()
    lines = []
    seen = set()
    for line in raw.splitlines():
        line = line.strip()
        if not line or line == "WEBVTT":
            continue
        if "-->" in line:
            continue
        if re.match(r"^\d+$", line):
            continue
        if line.startswith(("Kind:", "Language:", "NOTE")):
            continue
        line = re.sub(r"<[^>]+>", "", line).strip()
        if not line:
            continue
        if line not in seen:
            seen.add(line)
            lines.append(line)
    return "\n".join(lines)


def summarize_bullet(client, model, transcript, title, lang):
    """直接分点总结（单次调用，速度快）。"""
    system = "你是一个专业的视频内容总结助手。"
    user = PROMPTS[lang] + f"\n\n视频标题: {title}\n\n字幕内容:\n{transcript}"
    return chat(client, model, system, user)


def summarize_cod(client, model, transcript, title, lang, rounds=3):
    """Chain-of-Density 迭代精炼总结（Adams et al., 2023）。

    流程：
      Round 0  — 生成初步粗略总结
      Round 1-N — 每轮识别遗漏实体并融入，使摘要信息密度递增
      最终      — 将最密版本格式化为分点 + 中英对照
    """
    # 初始总结语言：优先英文（信息量更完整），仅中文视频用中文
    init_lang = "zh" if lang == "zh" else "en"
    init_lang_label = "中文" if init_lang == "zh" else "English"

    # ── Round 0: 初始粗略总结 ────────────────────────────────
    system = "你是专业视频内容分析师，擅长精炼摘要。"
    user = (
        f"请为以下视频字幕生成一段 4-6 句话的初步总结（用{init_lang_label}）。"
        f"只写摘要本身，不要加前缀或解释。\n\n"
        f"视频标题: {title}\n\n字幕内容:\n{transcript}"
    )
    summary = chat(client, model, system, user)
    print(f"  CoD Round 0 完成", file=sys.stderr)

    # ── Round 1-N: 密度迭代 ──────────────────────────────────
    for i in range(1, rounds + 1):
        user = (
            f"原始字幕：\n{transcript}\n\n"
            f"上一轮总结：\n{summary}\n\n"
            "请完成以下两步：\n"
            "1. 从字幕中找出 1-3 个【上轮总结未提及、但对理解视频内容重要】的实体或概念，"
            "以「缺失实体：A, B, C」格式列出。\n"
            "2. 将这些实体自然融入总结，重写为信息更密集的新版本。\n"
            "   要求：\n"
            "   - 句子数不超过上一轮\n"
            "   - 每句承载更多信息，不得出现空泛描述\n"
            "   - 只输出「缺失实体」行和新总结，不要其他说明"
        )
        raw = chat(client, model, system, user)
        # 去掉"缺失实体"行，只保留新总结
        lines = raw.splitlines()
        summary_lines = [l for l in lines if not l.strip().startswith(("缺失实体", "「缺失实体"))]
        summary = "\n".join(summary_lines).strip()
        print(f"  CoD Round {i} 完成", file=sys.stderr)

    # ── 最终格式化：套用 bullet prompt 转成分点 + 中英对照 ────
    print(f"  CoD 格式化输出...", file=sys.stderr)
    system_fmt = "你是一个专业的视频内容总结助手。"
    user_fmt = (
        PROMPTS[lang]
        + f"\n\n以下是一段经过多轮精炼的高密度摘要，请直接将其转化为规定格式，"
        f"不要丢失任何信息实体：\n\n{summary}"
    )
    return chat(client, model, system_fmt, user_fmt)


def chunk_transcript(text, max_chars=8000, overlap_lines=2):
    """把长字幕按行切分成带少量重叠的块，避免割裂语义。

    返回 list[str]；若文本不超长则返回单元素列表。
    """
    lines = text.splitlines()
    chunks = []
    cur = []
    cur_len = 0
    for line in lines:
        # 当前块已满且非空 → 收口，并保留尾部几行作为重叠上下文
        if cur_len + len(line) + 1 > max_chars and cur:
            chunks.append("\n".join(cur))
            cur = cur[-overlap_lines:] if overlap_lines else []
            cur_len = sum(len(l) + 1 for l in cur)
        cur.append(line)
        cur_len += len(line) + 1
    if cur:
        chunks.append("\n".join(cur))
    return chunks or [text]


def summarize_outline(client, model, transcript, title, lang, max_chars=8000):
    """分章节大纲 + 分层抽象 总结（长视频先 Map-Reduce 分块）。

    流程：
      Map    — 把超长字幕切块，逐块提炼该段的主题/章节要点
      Reduce — 把各块要点汇总，交由模型整合成全局章节大纲并去重
      分层抽象 — 输出「一句话总览(TL;DR) → 核心要点 → 分章节详述」三层结构
    短视频（单块）跳过 Map-Reduce，直接进入分层抽象。
    """
    work_lang = "zh" if lang == "zh" else "en"
    lang_label = "中文" if work_lang == "zh" else "English"

    chunks = chunk_transcript(transcript, max_chars=max_chars)

    # ── Map: 逐块提炼章节要点 ────────────────────────────────
    if len(chunks) == 1:
        chapter_notes = transcript
    else:
        print(f"  Outline: 长字幕分为 {len(chunks)} 块，开始 Map-Reduce", file=sys.stderr)
        system = "你是专业视频内容分析师，擅长从字幕片段中提炼结构化要点。"
        partials = []
        for i, chunk in enumerate(chunks, 1):
            user = (
                f"以下是视频字幕的第 {i}/{len(chunks)} 段。"
                f"请用{lang_label}提炼这一段涉及的主题/章节及其要点，"
                f"以「主题 — 要点1；要点2；…」的形式分行列出，"
                f"只输出要点，不要额外解释。\n\n{chunk}"
            )
            partials.append(chat(client, model, system, user))
            print(f"  Outline Map {i}/{len(chunks)} 完成", file=sys.stderr)

        # ── Reduce: 汇总各块要点，整合成全局章节大纲 ──────────
        print("  Outline Reduce 整合章节大纲...", file=sys.stderr)
        merged = "\n\n".join(f"[第{i}段]\n{p}" for i, p in enumerate(partials, 1))
        user = (
            f"以下是同一视频按时间顺序分段提炼出的要点（用{lang_label}）。"
            f"请把它们合并、去重，整合成一份覆盖全片、逻辑连贯的【分章节要点】，"
            f"按视频推进顺序组织，相邻重复的主题合并为一个章节，"
            f"以「章节 — 要点1；要点2；…」分行列出，只输出整合结果。\n\n{merged}"
        )
        chapter_notes = chat(client, model, system, user)

    # ── 分层抽象：套用 outline 提示词输出三层结构 ──────────────
    print("  Outline 分层抽象输出...", file=sys.stderr)
    system_fmt = "你是一个专业的视频内容总结助手。"
    user_fmt = (
        OUTLINE_PROMPTS[lang]
        + f"\n\n视频标题: {title}\n\n分章节要点:\n{chapter_notes}"
    )
    return chat(client, model, system_fmt, user_fmt)


# ── 策略注册表 ────────────────────────────────────────────────
# 新增策略：实现同签名函数 fn(client, model, transcript, title, lang) -> str
# 然后在此处注册即可。
SUMMARIZE_STRATEGIES = {
    "bullet":  summarize_bullet,
    "cod":     summarize_cod,
    "outline": summarize_outline,
}


def translate_to_chinese(client, model, text):
    """把字幕文本翻译成中文。"""
    system = "你是一个专业翻译，擅长将视频字幕准确翻译成中文。"
    user = (
        "请将以下视频字幕逐句翻译成中文，保持原文分行格式，不要添加任何解释或额外内容：\n\n"
        + text
    )
    return chat(client, model, system, user)


def sanitize_filename(name, max_len=80):
    """把视频标题转成合法的文件名。"""
    name = re.sub(r"#\w+", "", name)              # 去掉 #hashtag
    name = re.sub(r'[\\/:*?"<>|]', "_", name)    # Windows 非法字符
    name = re.sub(r"\s+", " ", name).strip()      # 多余空白
    name = name[:max_len].rstrip(". ")            # 截断并去掉末尾点/空格
    return name or "untitled"


def build_markdown(title, url, summary, transcripts, orig_lang, ai_translated, lang, no_transcript, strategy=None):
    """把总结和字幕拼成 Markdown 格式。"""
    lines = []

    # 标题
    lines += [f"# {title or 'YouTube 视频总结'}", ""]
    lines += [f"> 来源：{url}"]
    if strategy:
        strategy_labels = {
            "bullet":  "Bullet（直接分点）",
            "cod":     "Chain-of-Density / 密度链 CoD（迭代精炼）",
            "outline": "分章节大纲 + 分层抽象（长视频 Map-Reduce）",
        }
        lines += [f"> 总结策略：{strategy_labels.get(strategy, strategy)}"]
    lines += ["", "---", ""]

    # 总结部分：把 【中文总结】/【English Summary】 转成 ## 标题
    summary_md = summary
    summary_md = re.sub(r"【中文总结】\s*", "## 中文总结\n\n", summary_md)
    summary_md = re.sub(r"【English Summary】\s*", "## English Summary\n\n", summary_md)
    lines += [summary_md.strip(), ""]

    # 字幕部分
    if not no_transcript and transcripts:
        lines += ["---", "", "## 完整字幕原文", ""]

        def label(lang_key):
            if orig_lang and orig_lang.lower().startswith(lang_key):
                return "（原文）"
            if lang_key in ai_translated:
                return "（AI翻译）"
            return "（自动翻译）"

        all_keys = list(transcripts.keys())
        ordered = []
        if "zh" in transcripts:
            ordered.append("zh")
        for k in ["en"] + [k for k in all_keys if k not in ("zh", "en")]:
            if k in transcripts:
                ordered.append(k)

        for key in ordered:
            display = LANG_NAMES.get(key, key)
            lines += [f"### {display} 字幕{label(key)}", ""]
            # 每行加换行，保留分行结构
            for text_line in transcripts[key].splitlines():
                lines.append(text_line)
            lines += [""]

    return "\n".join(lines)


def format_transcript_block(transcripts, orig_lang, ai_translated=None):
    """把字幕格式化成带语言标注的文本块。"""
    if not transcripts:
        return ""
    if ai_translated is None:
        ai_translated = set()

    def label(lang_key):
        if orig_lang and orig_lang.lower().startswith(lang_key):
            return "（原文）"
        if lang_key in ai_translated:
            return "（AI翻译）"
        return "（自动翻译）"

    # 中文在前，其他在后
    all_keys = list(transcripts.keys())
    ordered = []
    if "zh" in transcripts:
        ordered.append("zh")
    for k in ["en"] + [k for k in all_keys if k not in ("zh", "en")]:
        if k in transcripts:
            ordered.append(k)

    blocks = []
    for key in ordered:
        display = LANG_NAMES.get(key, key)
        header = f"【{display} 字幕{label(key)}】"
        blocks.append(f"{header}\n{transcripts[key]}")

    return "\n\n".join(blocks)


def main():
    ap = argparse.ArgumentParser(description="YouTube 视频总结 (Groq, 免费)")
    ap.add_argument("url", help="YouTube 视频链接")
    ap.add_argument(
        "--lang", choices=["both", "zh", "en"], default="both",
        help="总结输出语言 (默认 both 中英对照)",
    )
    ap.add_argument(
        "--model", default=DEFAULT_MODEL,
        help=f"Groq 模型 (默认 {DEFAULT_MODEL})。可选: llama-3.1-8b-instant, mixtral-8x7b-32768",
    )
    ap.add_argument(
        "--strategy", choices=list(SUMMARIZE_STRATEGIES.keys()), default=DEFAULT_STRATEGY,
        help="总结策略：bullet=直接分点（快）, cod=Chain-of-Density/密度链迭代精炼（默认，质量更高）, "
             "outline=分章节大纲+分层抽象（长视频自动 Map-Reduce 分块）",
    )
    ap.add_argument(
        "--no-transcript", action="store_true",
        help="不附字幕原文，只输出总结",
    )
    ap.add_argument("-o", "--output", help="把完整输出保存到指定文件")
    args = ap.parse_args()

    client = get_client()
    model = args.model
    ai_translated = set()

    # ── 第一步: 抓字幕 ────────────────────────────────────────
    print("正在抓取字幕...", file=sys.stderr)
    try:
        transcripts, title, orig_lang = fetch_all_transcripts(args.url)
    except Exception as e:
        sys.exit(f"字幕抓取失败: {e}")

    best = transcripts.get("en") or transcripts.get("zh") or next(iter(transcripts.values()), "")
    if not best:
        sys.exit("该视频没有可用字幕。可考虑安装 ffmpeg + Whisper 做语音转录。")

    strategy_labels = {
        "bullet":  "Bullet 分点",
        "cod":     "Chain-of-Density / 密度链",
        "outline": "分章节大纲 + 分层抽象",
    }
    print(f"  抓到字幕 ({len(best)} 字)，正在总结（策略: {strategy_labels.get(args.strategy, args.strategy)}）...", file=sys.stderr)

    # ── 第二步: 总结 ──────────────────────────────────────────
    strategy_fn = SUMMARIZE_STRATEGIES[args.strategy]
    summary = strategy_fn(client, model, best, title, args.lang)

    # ── 第三步: 若没有中文字幕，翻译一份 ────────────────────────
    if not args.no_transcript and "zh" not in transcripts:
        print("正在将字幕翻译成中文...", file=sys.stderr)
        try:
            transcripts["zh"] = translate_to_chinese(client, model, best)
            ai_translated.add("zh")
        except Exception as e:
            print(f"  中文翻译失败: {e}", file=sys.stderr)

    # ── 组装 Markdown 输出 ────────────────────────────────────
    md_content = build_markdown(
        title, args.url, summary,
        transcripts if not args.no_transcript else {},
        orig_lang, ai_translated, args.lang, args.no_transcript,
        strategy=args.strategy,
    )

    # 终端预览（纯文本，去掉 # 等符号让阅读更舒适）
    print("\n" + md_content)

    # ── 保存到 output 文件夹 ──────────────────────────────────
    script_dir = os.path.dirname(os.path.abspath(__file__))
    output_dir = os.path.join(script_dir, "output")
    os.makedirs(output_dir, exist_ok=True)

    safe_title = sanitize_filename(title or "untitled")
    md_path = os.path.join(output_dir, f"{safe_title}.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md_content)
    print(f"\n已保存到: {md_path}", file=sys.stderr)

    # -o 额外保存到指定路径
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(md_content)
        print(f"同时保存到: {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
