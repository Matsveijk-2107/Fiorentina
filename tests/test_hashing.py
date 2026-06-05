"""Content hashing: stable, and sensitive to content changes."""

from __future__ import annotations

from football_pipeline.hashing import file_sha256


def test_same_content_same_hash(tmp_path):
    a = tmp_path / "a.json"
    b = tmp_path / "b.json"
    a.write_text('{"x": 1}', encoding="utf-8")
    b.write_text('{"x": 1}', encoding="utf-8")
    assert file_sha256(a) == file_sha256(b)


def test_different_content_different_hash(tmp_path):
    a = tmp_path / "a.json"
    a.write_text('{"x": 1}', encoding="utf-8")
    h1 = file_sha256(a)
    a.write_text('{"x": 2}', encoding="utf-8")
    assert file_sha256(a) != h1
