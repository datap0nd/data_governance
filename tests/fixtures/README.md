# Excel compatibility fixtures

The binary workbook fixtures are base64-encoded so they remain portable through
text-only patches and code review.

- `minimal.xls.b64` is a two-column BIFF8 workbook generated for this suite.
- `minimal.xlsb.b64` is pandas' BSD-3-Clause `test3.xlsb` interoperability
  fixture, reduced here only by base64 encoding (the workbook bytes are
  unchanged):
  <https://github.com/pandas-dev/pandas/blob/main/pandas/tests/io/data/excel/test3.xlsb>
