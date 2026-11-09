"""Finding a `pytest.raises` block that makes more than one call, so that nothing but
the call under test can raise the exception a test expects."""

import textwrap

import check_raises


def _found(source: str) -> list[int]:
    return list(check_raises.blocks_with_more_than_one_call(textwrap.dedent(source)))


class TestBlocks:
    def test_one_call_is_allowed(self):
        assert not _found("""
            with pytest.raises(ValueError):
                parse(text)
        """)

    def test_a_call_in_the_arguments_of_the_call_is_found(self):
        assert _found("""
            with pytest.raises(ValueError):
                parse(read(path))
        """) == [2]

    def test_a_second_statement_is_found(self):
        assert _found("""
            def test_refused():
                with pytest.raises(ValueError):
                    text = read(path)
                    parse(text)
        """) == [3]

    def test_an_awaited_call_is_one_call(self):
        assert not _found("""
            async def test_refused():
                with pytest.raises(ValueError):
                    await records.patch(database, user_id)
        """)

    def test_calls_in_what_is_expected_are_not_counted(self):
        assert not _found("""
            with pytest.raises(ValueError, match=re.escape(str(guide))):
                parse(text)
        """)

    def test_other_with_blocks_are_left_alone(self):
        assert not _found("""
            with open(path) as file:
                parse(file.read())
        """)


class TestMain:
    def test_a_folder_with_such_a_block_fails_naming_the_file_and_line(
        self, tmp_path, capsys
    ):
        (tmp_path / "test_one.py").write_text(
            "with pytest.raises(ValueError):\n    parse(read(path))\n"
        )

        failed = check_raises.main([str(tmp_path)])

        assert failed == 1
        assert f"{tmp_path / 'test_one.py'}:1:" in capsys.readouterr().err

    def test_a_folder_without_one_passes(self, tmp_path):
        (tmp_path / "test_one.py").write_text(
            "with pytest.raises(ValueError):\n    parse(text)\n"
        )

        assert check_raises.main([str(tmp_path)]) == 0
