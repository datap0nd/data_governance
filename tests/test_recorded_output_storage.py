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


@pytest.mark.parametrize('suffix,expected', [('.dat', '.dat'), ('.download', '.bin'), ('', '.bin')])
def test_unchecked_recording_preserves_opaque_binary_with_its_actual_suffix(tmp_path, suffix, expected):
    import hashlib
    content = b'Protected export\x00\x01\x02\x03' + bytes(range(256))
    source = tmp_path / ('browser-file' + suffix)
    source.write_bytes(content)
    result = flow_worker._store_completed_download(source, tmp_path / 'configured.xlsx',
        file_format='xlsx', recorded_output=True, require_normalized_csv=False)
    output = Path(result['file_path'])
    assert output.read_bytes() == content
    assert output.suffix == expected
    assert result['detected_format'] == 'binary'
    assert result['checksum'] == hashlib.sha256(content).hexdigest()
    assert result['row_count'] is None and 'normalized_file_path' not in result


def test_unchecked_recording_rejects_opaque_bytes_mislabeled_as_xlsx(tmp_path):
    source = tmp_path / 'browser-file.xlsx'
    source.write_bytes(b'Protected export\x00\x01\x02\x03' + bytes(range(256)))
    with pytest.raises(RuntimeError, match='complete XLSX ZIP container'):
        flow_worker._store_completed_download(
            source, tmp_path / 'configured.xlsx', file_format='xlsx',
            recorded_output=True, require_normalized_csv=False,
        )


def test_unchecked_recording_preserves_pdf_but_processing_still_rejects_binary(tmp_path):
    source = tmp_path / 'download.pdf'
    source.write_bytes(b'%PDF-1.7\nFictional report\x00')
    result = flow_worker._store_completed_download(source, tmp_path / 'configured.xlsx',
        file_format='xlsx', recorded_output=True, require_normalized_csv=False)
    assert Path(result['file_path']).suffix == '.pdf'
    assert Path(result['file_path']).read_bytes() == source.read_bytes()
    source.write_bytes(b'Protected\x00\x01\x02report')
    with pytest.raises(RuntimeError, match='looks like binary'):
        flow_worker._store_completed_download(source, tmp_path / 'processed.csv', recorded_output=True)
    assert not (tmp_path / 'processed.csv').exists()


@pytest.mark.parametrize('encoding', ['utf-16-le', 'utf-16-be'])
def test_bomless_utf16_export_is_recognized_and_normalized_for_sql(tmp_path, encoding):
    source = tmp_path / 'download.csv'
    content = 'Region,Units\r\nMENA,7\r\nPortugal,8\r\n'.encode(encoding)
    source.write_bytes(content)
    assert flow_worker._detect_download_format(source) == 'csv'
    result = flow_worker._store_completed_download(source, tmp_path / 'normalized.csv', recorded_output=True)
    assert result['source_encoding'] == encoding
    assert result['row_count'] == 2
    assert Path(result['file_path']).read_text(encoding='utf-8-sig') == 'Region,Units\nMENA,7\nPortugal,8\n'


@pytest.mark.parametrize('encoding', ['utf-8', 'utf-16-le', 'utf-16-be'])
def test_excel_named_text_export_is_normalized_for_recorded_sql(tmp_path, encoding):
    source = tmp_path / 'MTracker_subs.xlsx'
    source.write_bytes('Region\tUnits\r\nMENA\t7\r\nPortugal\t8\r\n'.encode(encoding))

    assert flow_worker._detect_download_format(source) == 'csv'
    result = flow_worker._store_completed_download(
        source, tmp_path / 'configured.xlsx', file_format='xlsx',
        recorded_output=True,
    )

    output = Path(result['file_path'])
    assert output.suffix == '.csv'
    assert result['detected_format'] == 'csv'
    assert result['row_count'] == 2
    assert output.read_text(encoding='utf-8-sig') == 'Region,Units\nMENA,7\nPortugal,8\n'


def test_recorded_excel_output_accepts_html_table_for_sql_without_invented_asap_type(tmp_path):
    source = tmp_path / 'MTracker_subs.xlsx'
    source.write_text(
        '<html><body><table><tr><th>Region</th><th>Units</th></tr>'
        '<tr><td>MENA</td><td>7</td></tr></table></body></html>',
        encoding='utf-8',
    )

    result = flow_worker._store_completed_download(
        source, tmp_path / 'configured.xlsx', file_format='xlsx',
        recorded_output=True,
    )

    output = Path(result['file_path'])
    assert output.suffix == '.csv'
    assert result['row_count'] == 1
    assert result['columns'] == ['Region', 'Units']
    assert Path(result['original_file_path']).suffix == '.xls'


def test_utf16_detection_handles_prefix_ending_between_surrogates(tmp_path):
    source = tmp_path / 'download.csv'
    prefix = 'Name,Units\n' + 'A' * (2047 - len('Name,Units\n'))
    source.write_bytes((prefix + '\U0001f600,7\n').encode('utf-16-le'))
    assert flow_worker._detect_download_format(source) == 'csv'
    assert flow_worker._decode_downloaded_text(source.read_bytes(), source_label='fixture')[1] == 'utf-16-le'
