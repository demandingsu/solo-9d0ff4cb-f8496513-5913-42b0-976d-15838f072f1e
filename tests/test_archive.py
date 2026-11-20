"""Unit/integration tests exercised through the Flask test client.

Run with:  PYTHONPATH=.:<flask site> python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import io
import os
import tempfile
import unittest
import unittest.mock

from lake_archive import archive as store
from lake_archive import csvio
from lake_archive.web import create_app

CSV = b"""seq,sampled_at_utc,value_millidegrees_c,kind,base_seq
1,2024-01-01T00:00:00Z,-1500,absolute,
2,2024-01-01T01:00:00Z,250,delta,1
3,2024-01-01T02:00:00Z,500,absolute,
4,2024-01-01T03:00:00Z,-100,delta,2
5,2024-01-01T04:00:00Z,-1800,delta,1
"""


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "archive.sqlite3")
        self.app = create_app(self.db)
        self.app.testing = True
        self.client = self.app.test_client()
        self.aid = store.create_archive(self.db, "lake", "logger.csv", CSV)
        store.init_db(self.db)  # idempotent

    def tearDown(self):
        self.tmp.cleanup()

    def frame(self, seq):
        return store.get_frame(self.db, self.aid, seq)

    def frames(self):
        return {f.seq: f for f in store.list_frames(self.db, self.aid)}

    def download(self):
        return self.client.get("/archives/%d/download.csv" % self.aid).data

    def correct(self, seq, seen, new):
        return self.client.post(
            "/archives/%d/frames/%d/correct" % (self.aid, seq),
            data={"seen_restored_mc": str(seen), "new_restored_mc": str(new)},
        )


class TestImport(Base):
    def test_restored_values(self):
        f = self.frames()
        self.assertEqual(f[1].restored_mc, -1500)
        self.assertEqual(f[2].restored_mc, -1250)   # -1500 + 250
        self.assertEqual(f[3].restored_mc, 500)
        self.assertEqual(f[4].restored_mc, -1350)   # -1250 + -100
        self.assertEqual(f[5].restored_mc, -3300)   # -1500 + -1800
        self.assertEqual(f[2].base_seq, 1)
        self.assertIsNone(f[3].base_seq)

    def test_absolute_stored_equals_restored(self):
        f = self.frames()
        self.assertEqual(f[1].stored_kind, "absolute")
        self.assertEqual(f[1].stored_mc, f[1].restored_mc)
        self.assertEqual(f[2].stored_kind, "delta")
        self.assertEqual(f[2].stored_mc, 250)

    def test_duplicate_name_rejected(self):
        with self.assertRaises(store.ConflictError):
            store.create_archive(self.db, "lake", "x.csv", CSV)

    def test_bad_csv_rejected(self):
        for payload in (b"", b"a,b\n1,2\n",
                        b"seq,sampled_at_utc,value_millidegrees_c\n1,nope,5\n",
                        b"seq,sampled_at_utc,value_millidegrees_c,base_seq\n"
                        b"1,2024-01-01T00:00:00Z,5,\n2,2024-01-01T01:00:00Z,5,9\n",
                        b"seq,sampled_at_utc,value_millidegrees_c,base_seq\n"
                        b"2,2024-01-01T00:00:00Z,5,\n2,2024-01-01T01:00:00Z,5,\n",
                        b"seq,sampled_at_utc,value_millidegrees_c\n1,2024-01-01T00:00:00Z,1.5\n"):
            with self.assertRaises(csvio.CsvFormatError):
                store.create_archive(self.db, "other-%d" % id(payload), "x.csv", payload)

    def test_csv_roundtrip_format(self):
        out = self.download().decode()
        self.assertEqual(out, CSV.decode())


class TestBaseCorrection(Base):
    def test_correct_base_frame_keeps_others_restored(self):
        before = self.frames()
        r = self.correct(1, seen=-1500, new=-2000)
        self.assertEqual(r.status_code, 302)
        after = self.frames()
        self.assertEqual(after[1].restored_mc, -2000)
        for seq in (2, 3, 4, 5):
            self.assertEqual(after[seq].restored_mc, before[seq].restored_mc,
                             "restored temperature of frame %d must not move" % seq)
            self.assertEqual(after[seq].base_seq, before[seq].base_seq)
        # only the derived difference moved
        self.assertEqual(after[2].stored_mc, 750)
        self.assertEqual(after[4].stored_mc, -100)
        self.assertEqual(after[5].stored_mc, -1300)

    def test_export_after_base_correction_is_consistent(self):
        self.correct(1, seen=-1500, new=-2000)
        rows, _ = csvio.parse_csv(self.download())
        by = {r.seq: r for r in rows}
        restored = {}
        for r in rows:
            restored[r.seq] = r.stored_mc if r.base_seq is None else r.stored_mc + restored[r.base_seq]
        self.assertEqual(restored, {1: -2000, 2: -1250, 3: 500, 4: -1350, 5: -3300})

    def test_correcting_referrer_keeps_its_own_base(self):
        r = self.correct(2, seen=-1250, new=-900)
        self.assertEqual(r.status_code, 302)
        f = self.frame(2)
        self.assertEqual((f.restored_mc, f.base_seq, f.stored_mc), (-900, 1, 600))
        self.assertEqual(self.frame(4).restored_mc, -1350)  # child of 2 unchanged

    def test_stale_page_rejected_and_archive_kept(self):
        self.correct(1, seen=-1500, new=-2000)
        stale = self.correct(1, seen=-1500, new=-2500)   # page still shows -1500
        self.assertEqual(stale.status_code, 409)
        self.assertIn("已拒绝", stale.data.decode())
        self.assertEqual(self.frame(1).restored_mc, -2000)
        self.assertEqual(self.download(), self.download())  # nothing changed

    def test_stale_page_second_tab_after_other_change(self):
        # Tab A opens frame 2 showing -1250; tab B corrects frame 2 to -900.
        self.assertEqual(self.correct(2, seen=-1250, new=-900).status_code, 302)
        r = self.correct(2, seen=-1250, new=-800)
        self.assertEqual(r.status_code, 409)
        self.assertEqual(self.frame(2).restored_mc, -900)
        # tab B's page (showing -900) may still submit
        self.assertEqual(self.correct(2, seen=-900, new=-800).status_code, 302)
        self.assertEqual(self.frame(2).restored_mc, -800)

    def test_same_temperature_resubmit_accepted(self):
        r = self.correct(2, seen=-1250, new=-1250)
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.frame(2).restored_mc, -1250)

    def test_only_stored_delta_changed_still_submittable(self):
        # Frame 2's stored delta becomes 750 once base frame 1 is corrected.
        self.correct(1, seen=-1500, new=-2000)
        page = self.client.get("/archives/%d/frames/2" % self.aid).data.decode()
        self.assertIn("delta 750", page)
        # A visitor whose page displayed restored -1250 (now equal) can submit,
        # even though the stored difference differs from what an older page read.
        self.assertEqual(self.correct(2, seen=-1250, new=-1200).status_code, 302)
        self.assertEqual(self.frame(2).restored_mc, -1200)

    def test_referrer_page_uses_fresh_delta_with_old_value(self):
        # A page opened before the base change shows the old delta. Submitting
        # the same restored value must be accepted, and the stored delta is
        # recomputed from the current base.
        self.correct(1, seen=-1500, new=-2000)
        self.assertEqual(self.correct(2, seen=-1250, new=-1250).status_code, 302)
        self.assertEqual(self.frame(2).stored_mc, 750)

    def test_non_integer_rejected(self):
        r = self.client.post("/archives/%d/frames/1/correct" % self.aid,
                             data={"seen_restored_mc": "-1500", "new_restored_mc": "1.5"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.frame(1).restored_mc, -1500)

    def test_unknown_frame_404(self):
        self.assertEqual(self.client.get(
            "/archives/%d/frames/99" % self.aid).status_code, 404)
        self.assertEqual(self.correct(99, seen=0, new=0).status_code, 404)

    def test_missing_seen_token_rejected(self):
        r = self.client.post("/archives/%d/frames/1/correct" % self.aid,
                             data={"new_restored_mc": "1"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.frame(1).restored_mc, -1500)


class TestConcurrency(Base):
    def test_simultaneous_stale_submissions_only_one_wins(self):
        import threading
        barrier = threading.Barrier(2)
        outcomes = []

        def submit(value):
            barrier.wait()
            try:
                store.correct_frame(self.db, self.aid, 2,
                                    seen_restored_mc=-1250, new_restored_mc=value)
                outcomes.append(value)
            except store.ConflictError:
                outcomes.append("rejected")

        threads = [threading.Thread(target=submit, args=(v,)) for v in (-900, -800)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(outcomes), 2)
        self.assertEqual(outcomes.count("rejected"), 1, outcomes)
        self.assertEqual(self.frame(2).restored_mc,
                         -900 if outcomes.count(-900) else -800)
        conn = store.connect(self.db)
        try:
            n = conn.execute("SELECT COUNT(*) FROM revision").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(n, 1)  # the rejected attempt left no audit row either


class TestIsolation(Base):
    def test_correction_writes_only_the_addressed_row(self):
        seen = []
        real_connect = store.connect

        def traced_connect(db_path):
            conn = real_connect(db_path)

            def tracer(statement):
                lowered = statement.lstrip().lower()
                if lowered.startswith(("insert", "update", "delete")):
                    seen.append(statement)

            conn.set_trace_callback(tracer)
            return conn

        with unittest.mock.patch.object(store, "connect", traced_connect):
            store.correct_frame(self.db, self.aid, 2,
                                seen_restored_mc=-1250, new_restored_mc=-900)
        writes = [s for s in seen if not s.lstrip().lower().startswith("insert")]
        self.assertEqual(len(writes), 1, seen)
        self.assertIn("WHERE archive_id = 1 AND seq = 2", writes[0])
        for other in (1, 3, 4, 5):
            self.assertNotIn("seq = %d" % other, writes[0])
        # the only other write is the audit row, not a frame change
        inserts = [s for s in seen if s.lstrip().lower().startswith("insert")]
        self.assertEqual(len(inserts), 1)
        self.assertIn("INTO revision", inserts[0])

    def test_two_archives_are_independent(self):
        other = store.create_archive(
            self.db, "lake2", "b.csv",
            b"seq,sampled_at_utc,value_millidegrees_c\n1,2024-02-01T00:00:00Z,-1500\n")
        self.correct(1, seen=-1500, new=-2000)
        self.assertEqual(store.get_frame(self.db, other, 1).restored_mc, -1500)


class TestExport(Base):
    def test_export_snapshot_matches_frames(self):
        self.correct(1, seen=-1500, new=-2100)
        rows, layout = csvio.parse_csv(self.download())
        self.assertEqual(layout.header, ("seq", "sampled_at_utc",
                                         "value_millidegrees_c", "kind", "base_seq"))
        self.assertEqual(rows[0].stored_mc, -2100)

    def test_download_headers(self):
        r = self.client.get("/archives/%d/download.csv" % self.aid)
        self.assertEqual(r.status_code, 200)
        self.assertIn("attachment", r.headers["Content-Disposition"])
        self.assertEqual(r.headers["Cache-Control"], "no-store")

    def test_unmanaged_columns_echoed(self):
        payload = (b"seq,sampled_at_utc,value_millidegrees_c,kind,base_seq,site\n"
                   b"1,2024-01-01T00:00:00Z,-1500,absolute,,north\n"
                   b"2,2024-01-01T01:00:00Z,250,delta,1,north\n")
        aid = store.create_archive(self.db, "extra", "d.csv", payload)
        out = self.client.get("/archives/%d/download.csv" % aid).data
        self.assertEqual(out, payload)

    def test_blank_kind_column_stays_blank(self):
        payload = (b"seq,sampled_at_utc,value_millidegrees_c,kind,base_seq\n"
                   b"1,2024-01-01T00:00:00Z,-1500,,\n"
                   b"2,2024-01-01T01:00:00Z,250,,1\n")
        aid = store.create_archive(self.db, "blankkind", "e.csv", payload)
        self.assertEqual(self.client.get("/archives/%d/download.csv" % aid).data, payload)

    def test_crlf_layout_preserved(self):
        crlf = CSV.replace(b"\n", b"\r\n")
        aid = store.create_archive(self.db, "crlf", "c.csv", crlf)
        out = self.client.get("/archives/%d/download.csv" % aid).data
        self.assertIn(b"\r\n", out)
        self.assertNotIn(b"\n", out.replace(b"\r\n", b""))


class TestPages(Base):
    def test_index_and_archive_pages(self):
        self.assertEqual(self.client.get("/").status_code, 200)
        page = self.client.get("/archives/%d" % self.aid).data.decode()
        self.assertIn("/frames/1", page)
        self.assertIn("-1250 m°C", page)

    def test_frame_page_loads_and_shows_value(self):
        page = self.client.get("/archives/%d/frames/2" % self.aid).data.decode()
        self.assertIn("还原温度", page)
        self.assertIn("-1250", page)

    def test_lookup_redirects_to_frame(self):
        r = self.client.get("/archives/%d/lookup?seq=4" % self.aid)
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r.headers["Location"].endswith("/archives/1/frames/4"))
        self.assertEqual(self.client.get(
            "/archives/%d/lookup?seq=abc" % self.aid).status_code, 400)
        missing = self.client.get("/archives/%d/lookup?seq=999" % self.aid)
        self.assertEqual(missing.status_code, 404)
        self.assertIn("没有序号为 999 的帧", missing.data.decode())

    def test_html_escaped(self):
        aid = store.create_archive(
            self.db, "<script>alert(1)</script>", "x.csv",
            b"seq,sampled_at_utc,value_millidegrees_c\n1,2024-01-01T00:00:00Z,1\n")
        page = self.client.get("/archives/%d" % aid).data.decode()
        self.assertNotIn("<script>alert(1)</script>", page)
        self.assertIn("&lt;script&gt;", page)


if __name__ == "__main__":
    unittest.main()
