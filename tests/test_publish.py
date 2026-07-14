import json

from prelude_data.publish import atomic_write, build_meta, dumps, write_feed


class TestAtomicWrite:
    def test_writes_and_replaces(self, tmp_path):
        target = tmp_path / "a.json"
        atomic_write(target, '{"v": 1}\n')
        atomic_write(target, '{"v": 2}\n')
        assert target.read_text() == '{"v": 2}\n'

    def test_no_tmp_droppings_left_behind(self, tmp_path):
        atomic_write(tmp_path / "a.json", "x")
        assert [p.name for p in tmp_path.iterdir()] == ["a.json"]

    def test_creates_parent_dirs(self, tmp_path):
        atomic_write(tmp_path / "deep" / "dir" / "a.json", "x")
        assert (tmp_path / "deep" / "dir" / "a.json").exists()


class TestMeta:
    def docs(self):
        return {
            "companies.json": {
                "as_of": "2026-07-14",
                "generated_at": "2026-07-14T02:00:00+00:00",
                "companies": [{"id": "a"}, {"id": "b"}],
            }
        }

    def test_meta_counts_and_hashes(self):
        docs = self.docs()
        rendered = {k: dumps(v) for k, v in docs.items()}
        meta = build_meta(docs, rendered)
        entry = meta["files"]["companies.json"]
        assert entry["record_count"] == 2
        assert entry["as_of"] == "2026-07-14"
        assert len(entry["sha256"]) == 64
        assert entry["bytes"] == len(rendered["companies.json"].encode())

    def test_meta_carries_compliance_line(self):
        meta = build_meta({}, {})
        assert "No recommendations" in meta["compliance"]


class TestWriteFeed:
    def test_writes_all_products_plus_meta_and_index(self, tmp_path):
        docs = {
            "companies.json": {"as_of": "2026-07-14", "companies": []},
            "pipeline.json": {"as_of": "2026-07-14", "filings": []},
        }
        feed_dir = tmp_path / "feed" / "v1"
        written = write_feed(docs, feed_dir=feed_dir)
        names = sorted(p.name for p in written)
        assert names == ["companies.json", "feed_meta.json", "index.json", "pipeline.json"]
        meta = json.loads((feed_dir / "feed_meta.json").read_text())
        assert set(meta["files"]) == {"companies.json", "pipeline.json"}
        index = json.loads((tmp_path / "feed" / "index.json").read_text())
        assert index["latest_version"] == "v1"

    def test_output_is_stable_json(self, tmp_path):
        docs = {"companies.json": {"as_of": "x", "companies": [{"name": "Zed"}]}}
        write_feed(docs, feed_dir=tmp_path)
        text = (tmp_path / "companies.json").read_text()
        assert json.loads(text)["companies"][0]["name"] == "Zed"
        assert text.endswith("\n")
