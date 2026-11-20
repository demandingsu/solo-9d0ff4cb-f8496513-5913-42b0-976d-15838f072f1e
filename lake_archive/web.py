"""Flask application: upload, browse, correct, download."""

from __future__ import annotations

import os

from flask import Flask, Response, abort, redirect, request, url_for

from . import archive as store
from . import views
from .csvio import CsvFormatError

DB_PATH = os.environ.get("LAKE_DB", os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "archive.sqlite3"))
MAX_UPLOAD = 32 * 1024 * 1024
MAX_NAME = 120


def create_app(db_path: str | None = None) -> Flask:
    app = Flask(__name__)
    app.config["LAKE_DB"] = db_path or DB_PATH
    app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD
    store.init_db(app.config["LAKE_DB"])

    def db() -> str:
        return app.config["LAKE_DB"]

    def index_response(notice=None, kind="ok", status=200):
        return views.index_page(store.list_archives(db()), notice, kind), status

    @app.get("/")
    def index():
        return index_response(request.args.get("notice"), request.args.get("kind", "ok"))

    @app.post("/archives")
    def upload():
        name = (request.form.get("name") or "").strip()
        upload_file = request.files.get("file")
        if not name or len(name) > MAX_NAME:
            return index_response("请填写档案名称（不超过 %d 字）。" % MAX_NAME, "bad", 400)
        if upload_file is None or not upload_file.filename:
            return index_response("请选择一个 CSV 文件。", "bad", 400)
        try:
            archive_id = store.create_archive(
                db(), name, upload_file.filename, upload_file.read())
        except CsvFormatError as exc:
            return index_response("CSV 无法导入：%s" % exc, "bad", 400)
        except store.ArchiveError as exc:
            return index_response(exc.message, "bad", exc.status)
        return redirect(url_for("show_archive", archive_id=archive_id,
                                notice="已导入档案。"))

    @app.get("/archives/<int:archive_id>")
    def show_archive(archive_id: int):
        try:
            summary = store.get_archive(db(), archive_id)
            frames = store.list_frames(db(), archive_id)
        except store.ArchiveError as exc:
            abort(exc.status, exc.message)
        return views.archive_page(summary, frames,
                                  request.args.get("notice"),
                                  request.args.get("kind", "ok"))

    def frame_response(archive_id: int, seq: int, notice=None, kind="ok", status=200):
        try:
            summary = store.get_archive(db(), archive_id)
            frame, base, referrers = store.get_frame_bundle(db(), archive_id, seq)
        except store.ArchiveError as exc:
            abort(exc.status, exc.message)
        return views.frame_page(summary, frame, base, referrers, notice, kind), status

    @app.get("/archives/<int:archive_id>/frames/<int:seq>")
    def show_frame(archive_id: int, seq: int):
        return frame_response(archive_id, seq, request.args.get("notice"),
                              request.args.get("kind", "ok"))

    @app.get("/archives/<int:archive_id>/lookup")
    def lookup(archive_id: int):
        seq = (request.args.get("seq") or "").strip()
        try:
            seq_num = int(seq, 10)
        except ValueError:
            seq_num = None
        if seq_num is None:
            return views.error_page(400, "请输入整数帧序号。"), 400
        try:
            store.get_frame(db(), archive_id, seq_num)
        except store.ArchiveError as exc:
            if exc.status != 404:
                abort(exc.status, exc.message)
            try:
                summary = store.get_archive(db(), archive_id)
                frames = store.list_frames(db(), archive_id)
            except store.ArchiveError as inner:
                abort(inner.status, inner.message)
            return views.archive_page(summary, frames, missing_seq=seq_num), 404
        return redirect(url_for("show_frame", archive_id=archive_id, seq=seq_num))

    @app.post("/archives/<int:archive_id>/frames/<int:seq>/correct")
    def correct(archive_id: int, seq: int):
        try:
            seen = int((request.form.get("seen_restored_mc") or "").strip(), 10)
            new = int((request.form.get("new_restored_mc") or "").strip(), 10)
        except ValueError:
            return frame_response(archive_id, seq,
                                  "更正后的温度必须是整数毫摄氏度。", "bad", 400)
        try:
            store.correct_frame(db(), archive_id, seq,
                                seen_restored_mc=seen, new_restored_mc=new)
        except store.ConflictError as exc:
            return frame_response(archive_id, seq, exc.message, "bad", 409)
        except store.ArchiveError as exc:
            abort(exc.status, exc.message)
        return redirect(url_for("show_frame", archive_id=archive_id, seq=seq,
                                notice="更正已提交。", kind="ok"))

    @app.get("/archives/<int:archive_id>/download.csv")
    def download(archive_id: int):
        try:
            summary = store.get_archive(db(), archive_id)
            payload = store.export_csv(db(), archive_id)
        except store.ArchiveError as exc:
            abort(exc.status, exc.message)
        safe_source = "".join(
            ch for ch in summary.source_filename if ch not in '"\\\r\n')
        response = Response(payload, mimetype="text/csv; charset=utf-8")
        response.headers["Content-Disposition"] = (
            'attachment; filename="archive-%d-%s"' % (archive_id, safe_source))
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/healthz")
    def healthz():
        return "ok", 200, {"Content-Type": "text/plain; charset=utf-8"}

    @app.errorhandler(400)
    @app.errorhandler(404)
    @app.errorhandler(409)
    def on_error(err):
        return views.error_page(err.code, getattr(err, "description", "请求无法完成。")), err.code

    @app.errorhandler(413)
    def too_large(err):
        return views.error_page(413, "上传文件超过大小上限。"), 413

    return app


def main() -> None:
    app = create_app()
    host = os.environ.get("LAKE_HOST", "127.0.0.1")
    port = int(os.environ.get("LAKE_PORT", "8000"))
    # One worker process with threads: SQLite writes are serialized by
    # BEGIN IMMEDIATE, so a threaded server keeps the check-and-write atomic
    # without cross-process lock contention.
    from waitress import serve
    serve(app, host=host, port=port, threads=8, ident="lake-archive")


if __name__ == "__main__":
    main()
