"""Minimal HTML views (stdlib string formatting, escaped output)."""

from __future__ import annotations

from html import escape

from .archive import ArchiveSummary, FrameView


def _mc(value: int) -> str:
    return "%d m°C (%.3f °C)" % (value, value / 1000.0)


STYLE = """
:root{color-scheme:light dark;--fg:#1c1c1e;--bg:#fafafa;--muted:#6b6b70;--line:#d8d8dc;
--accent:#0b62c4;--bad:#b3261e;--ok:#1e6b34;--warn:#8a5a00}
@media (prefers-color-scheme:dark){:root{--fg:#eaeaea;--bg:#17171a;--muted:#a0a0a8;
--line:#3a3a40;--accent:#6aa9ff;--bad:#ff8a80;--ok:#7fce93;--warn:#e0b050}}
*{box-sizing:border-box}
body{font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;color:var(--fg);
background:var(--bg);margin:0;padding:24px}
main{max-width:960px;margin:0 auto}
h1{font-size:1.4rem;margin:0 0 4px}
h2{font-size:1.1rem;margin:24px 0 8px}
a{color:var(--accent);text-decoration:none} a:hover{text-decoration:underline}
.muted{color:var(--muted)} .small{font-size:.86rem}
table{border-collapse:collapse;width:100%;margin:8px 0}
th,td{border-bottom:1px solid var(--line);padding:6px 8px;text-align:left;
vertical-align:top}
th{font-weight:600;color:var(--muted);font-size:.8rem;text-transform:uppercase;
letter-spacing:.03em}
tr.stale td{background:rgba(179,38,30,.08)}
code,.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
form.card{border:1px solid var(--line);border-radius:10px;padding:16px;margin:12px 0;
background:color-mix(in srgb,var(--bg) 92%, var(--fg) 8%)}
label{display:block;font-weight:600;margin:10px 0 4px}
input[type=text],input[type=number],input[type=file],select{width:100%;padding:8px;
border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--fg);
font:inherit}
button{margin-top:14px;padding:9px 16px;border-radius:7px;border:0;
background:var(--accent);color:#fff;font:inherit;font-weight:600;cursor:pointer}
button:disabled{opacity:.5;cursor:not-allowed}
.notice{border-radius:8px;padding:10px 14px;margin:12px 0;border:1px solid}
.notice.ok{border-color:var(--ok);color:var(--ok)}
.notice.bad{border-color:var(--bad);color:var(--bad)}
.notice.warn{border-color:var(--warn);color:var(--warn)}
.kv{display:grid;grid-template-columns:max-content 1fr;gap:4px 16px;margin:8px 0}
.kv dt{color:var(--muted)} .kv dd{margin:0}
nav{margin-bottom:18px}
"""


def _page(title: str, body: str) -> str:
    return (
        "<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        "<title>%s</title><style>%s</style></head><body><main>%s</main></body></html>"
        % (escape(title), STYLE, body)
    )


def index_page(archives: list[ArchiveSummary], notice: str | None = None,
               notice_kind: str = "ok") -> str:
    parts = ["<h1>湖泊测温档案</h1>"]
    parts.append("<p class=\"muted\">上传记录仪 CSV，按帧序号查看还原温度并提交更正。</p>")
    if notice:
        parts.append("<div class=\"notice %s\">%s</div>" % (escape(notice_kind), escape(notice)))

    parts.append("<h2>导入记录仪 CSV</h2>")
    parts.append(
        "<form class=\"card\" method=\"post\" action=\"/archives\" enctype=\"multipart/form-data\">"
        "<label for=\"name\">档案名称</label>"
        "<input id=\"name\" name=\"name\" type=\"text\" required maxlength=\"120\""
        " placeholder=\"例如：北湖 2024 年 1 月\">"
        "<label for=\"file\">CSV 文件</label>"
        "<input id=\"file\" name=\"file\" type=\"file\" accept=\".csv,text/csv\" required>"
        "<p class=\"muted small\">列：<code>seq</code>、<code>sampled_at_utc</code>、"
        "<code>value_millidegrees_c</code>（整数毫摄氏度）、<code>kind</code>"
        "（absolute/delta）、<code>base_seq</code>（delta 时引用较早帧）。</p>"
        "<button type=\"submit\">上传并归档</button></form>"
    )

    parts.append("<h2>现有档案</h2>")
    if not archives:
        parts.append("<p class=\"muted\">还没有档案。</p>")
    else:
        rows = []
        for a in archives:
            rows.append(
                "<tr><td><a href=\"/archives/%d\">%s</a></td><td>%d</td><td class=\"mono small\">%s</td>"
                "<td class=\"small muted\">%s</td></tr>"
                % (a.id, escape(a.name), a.frame_count, escape(a.source_filename),
                   escape(a.created_at))
            )
        parts.append(
            "<table><thead><tr><th>档案</th><th>帧数</th><th>源文件</th><th>导入时间</th></tr>"
            "</thead><tbody>%s</tbody></table>" % "".join(rows)
        )
    return _page("湖泊测温档案", "".join(parts))


def _fmt_opt(value) -> str:
    return "—" if value is None else str(value)


def archive_page(archive: ArchiveSummary, frames: list[FrameView],
                 notice: str | None = None, notice_kind: str = "ok",
                 missing_seq: int | None = None) -> str:
    parts = [
        "<nav><a href=\"/\">← 全部档案</a></nav>",
        "<h1>%s</h1>" % escape(archive.name),
        "<div class=\"kv\"><dt>源文件</dt><dd class=\"mono\">%s</dd>"
        "<dt>帧数</dt><dd>%d</dd>"
        "<dt>导入时间</dt><dd class=\"mono\">%s</dd></div>"
        % (escape(archive.source_filename), archive.frame_count, escape(archive.created_at)),
        "<p><a href=\"/archives/%d/download.csv\">下载 CSV（整份同一已提交状态）</a></p>"
        % archive.id,
    ]
    if notice:
        parts.append("<div class=\"notice %s\">%s</div>" % (escape(notice_kind), escape(notice)))
    if missing_seq is not None:
        parts.append("<div class=\"notice bad\">本档案没有序号为 %d 的帧。</div>"
                     % missing_seq)
    parts.append("<p class=\"muted small\">点击帧序号进入单帧页面查看还原温度并提交更正。"
                 "绝对帧的存储值等于还原温度；相对帧的存储值是相对更早基准帧的差值。"
                 "更正基准帧不会改动其他帧的还原温度和基准引用。</p>")
    parts.append(
        "<form class=\"card\" method=\"get\" action=\"/archives/%d/lookup\">"
        "<label for=\"seq\">按帧序号查还原温度</label>"
        "<input id=\"seq\" name=\"seq\" type=\"number\" min=\"0\" step=\"1\" required"
        " placeholder=\"输入帧序号\">"
        "<button type=\"submit\">查看该帧</button></form>" % archive.id
    )

    trs = []
    for f in frames:
        base_cell = _fmt_opt(f.base_seq)
        if f.base_seq is not None:
            base_cell = "<a href=\"/archives/%d/frames/%d\">%d</a>" % (
                archive.id, f.base_seq, f.base_seq)
        trs.append(
            "<tr><td><a href=\"/archives/%d/frames/%d\">%d</a></td>"
            "<td class=\"mono\">%s</td><td class=\"mono\">%s</td>"
            "<td>%s</td><td class=\"mono\">%s</td></tr>"
            % (archive.id, f.seq, f.seq, escape(f.sampled_at),
               escape("%s %d" % (f.stored_kind, f.stored_mc)), base_cell,
               escape(_mc(f.restored_mc)))
        )
    parts.append(
        "<table><thead><tr><th>序号</th><th>UTC 采样时间</th><th>存储形式与值</th>"
        "<th>基准帧</th><th>还原温度</th></tr></thead><tbody>%s</tbody></table>"
        % "".join(trs)
    )
    return _page(archive.name, "".join(parts))


def frame_page(archive: ArchiveSummary, frame: FrameView, base: FrameView | None,
               referrers: list[FrameView],
               notice: str | None = None, notice_kind: str = "ok") -> str:
    parts = [
        "<nav><a href=\"/\">全部档案</a> · <a href=\"/archives/%d\">%s</a></nav>"
        % (archive.id, escape(archive.name)),
        "<h1>帧 %d</h1>" % frame.seq,
        "<div class=\"kv\"><dt>UTC 采样时间</dt><dd class=\"mono\">%s</dd>"
        "<dt>还原温度</dt><dd class=\"mono\"><strong>%s</strong></dd>"
        "<dt>存储形式</dt><dd class=\"mono\">%s %d</dd>"
        "<dt>基准帧</dt><dd>%s</dd></div>"
        % (
            escape(frame.sampled_at), escape(_mc(frame.restored_mc)),
            escape(frame.stored_kind), frame.stored_mc,
            ("<a href=\"/archives/%d/frames/%d\">帧 %d</a>（%s）"
             % (archive.id, base.seq, base.seq, escape(_mc(base.restored_mc))))
            if base is not None else "—",
        ),
    ]
    if notice:
        parts.append("<div class=\"notice %s\">%s</div>" % (escape(notice_kind), escape(notice)))
    if frame.base_seq is not None and base is not None:
        parts.append(
            "<div class=\"notice warn\">本帧存储的是相对帧 %d 的差值。更正帧 %d 的还原温度后，"
            "本帧的还原温度与基准引用保持原样，只有存储差值随基准变化。</div>"
            % (base.seq, base.seq)
        )
    if referrers:
        parts.append("<div class=\"notice warn\">以下帧以本帧为基准：%s。更正本帧不会改动"
                     "它们的还原温度和基准引用；只有它们的存储差值随本帧变化，下载时按新差值"
                     "导出。</div>" % escape("、".join(str(r.seq) for r in referrers)))

    parts.append("<h2>提交更正</h2>")
    parts.append(
        "<form class=\"card\" method=\"post\" action=\"/archives/%d/frames/%d/correct\">"
        "<input type=\"hidden\" name=\"seen_restored_mc\" value=\"%d\">"
        "<label for=\"new_restored_mc\">更正后的还原温度（整数毫摄氏度）</label>"
        "<input id=\"new_restored_mc\" name=\"new_restored_mc\" type=\"number\" step=\"1\""
        " required value=\"%d\">"
        "<p class=\"muted small\">提交前请核对：本页显示的温度是 <span class=\"mono\">%s</span>。"
        "若在提交前该帧的还原温度已被他人更正，提交会被拒绝并保留现档。</p>"
        "<button type=\"submit\">提交更正</button></form>"
        % (archive.id, frame.seq, frame.restored_mc, frame.restored_mc,
           escape(_mc(frame.restored_mc)))
    )
    return _page("帧 %d" % frame.seq, "".join(parts))


def error_page(status: int, message: str) -> str:
    return _page("错误 %d" % status, "<h1>%d</h1><div class=\"notice bad\">%s</div>"
                 "<p><a href=\"/\">← 返回全部档案</a></p>"
                 % (status, escape(message)))

