import csv
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def read_csv_rows(csv_path: Path) -> list[list[str]]:
    """Read a CSV file and return all rows as lists of strings.

    Uses pywin32 (Excel COM) on Windows for reliable encoding handling.
    Falls back to raw byte decoding on non-Windows (dev/Docker).
    """
    # Try pywin32 first
    try:
        import win32com.client
        excel = None
        try:
            excel = win32com.client.Dispatch("Excel.Application")
            excel.Visible = False
            excel.DisplayAlerts = False
            wb = excel.Workbooks.Open(str(csv_path.resolve()))
            ws = wb.Sheets(1)
            used = ws.UsedRange
            rows = []
            for r in range(1, used.Rows.Count + 1):
                row = []
                for c in range(1, used.Columns.Count + 1):
                    val = ws.Cells(r, c).Value
                    row.append(str(val).strip() if val is not None else "")
                rows.append(row)
            wb.Close(False)
            logger.info("Read %s via pywin32 Excel COM (%d rows)", csv_path.name, len(rows))
            return rows
        except Exception as e:
            logger.warning("pywin32 Excel COM failed for %s: %s — using fallback", csv_path.name, e)
        finally:
            if excel:
                try:
                    excel.Quit()
                except Exception:
                    pass
    except ImportError:
        pass

    # Fallback: read raw bytes and detect encoding
    raw = csv_path.read_bytes()
    for enc in ("utf-8-sig", "utf-16", "cp1252", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except (UnicodeDecodeError, UnicodeError):
            continue
    else:
        logger.warning("Could not decode %s with any known encoding", csv_path)
        return []
    logger.info("Read %s via fallback decoder (encoding=%s)", csv_path.name, enc)
    return list(csv.reader(text.splitlines()))
