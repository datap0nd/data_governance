"""Optional recording checks do not alter other sources' import contracts."""
from pathlib import Path

import pytest

from app import flow_worker


@pytest.mark.parametrize('fmt,content', [
    ('csv', b'Code\r\n'),
    ('csv', b'Code\r\nA\r\n'),
    ('html', b'<html><body><p>No results</p></body></html>'),
    ('txt', b'No results'),
])
def test_unchecked_recording_preserves_download_bytes(tmp_path, fmt, content):
    source = tmp_path / f'download.{fmt}'
    source.write_bytes(content)
    result = flow_worker._store_completed_download(source, tmp_path / f'output.{fmt}',
        file_format=fmt, recorded_output=True, require_normalized_csv=False)
    assert Path(result['file_path']).read_bytes() == content
    assert Path(result['file_path']).suffix == f'.{fmt}'
    assert result['row_count'] is None
    assert 'normalized_file_path' not in result


def test_recording_can_normalize_one_column_for_an_optional_check(tmp_path):
    source = tmp_path / 'download.csv'
    source.write_text('Code\nA\nB\nC\nD\n', encoding='utf-8')
    result = flow_worker._store_completed_download(source, tmp_path / 'output.csv',
        file_format='csv', csv_preamble='none', recorded_output=True)
    assert result['row_count'] == 4
    assert result['columns'] == ['Code']


def test_nonrecorded_download_keeps_existing_table_requirements(tmp_path):
    source = tmp_path / 'download.csv'
    source.write_text('Code\nA\n', encoding='utf-8')
    with pytest.raises(RuntimeError, match='usable delimited header'):
        flow_worker._store_completed_download(source, tmp_path / 'output.csv',
            file_format='csv', csv_preamble='none')


def test_unchecked_recording_still_rejects_a_sign_in_page(tmp_path):
    source = tmp_path / 'download.html'
    source.write_text('<html><body>Sign in to continue</body></html>', encoding='utf-8')
    with pytest.raises(RuntimeError, match='sign-in|expired-session'):
        flow_worker._store_completed_download(source, tmp_path / 'output.html',
            file_format='html', recorded_output=True, require_normalized_csv=False)
    assert not (tmp_path / 'output.html').exists()


def test_unchecked_recording_still_rejects_a_broken_excel_container(tmp_path):
    source = tmp_path / 'download.xlsx'
    source.write_bytes(b'PK\x03\x04broken workbook')
    with pytest.raises(RuntimeError):
        flow_worker._store_completed_download(source, tmp_path / 'output.xlsx',
            file_format='xlsx', recorded_output=True, require_normalized_csv=False)
    assert not (tmp_path / 'output.xlsx').exists()
