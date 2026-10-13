#!/usr/bin/env python3
"""IO-утилиты скила «парсинг-адресов»: prepare / save. Разбор сущностей делает агент."""

import argparse
import csv
import glob
import io
import json
import os
import sys
import tempfile

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

CITY_COLUMN_CANDIDATES = [
    "адрес", "address", "addr", "адрес_одной_строкой", "full_address", "адрес_full",
]

STREET_HINTS = (
    "улиц", "проспект", "шоссе", "линия", "набережная", "проезд", "площад",
    "дорога", "переулок", "тупик", "бульвар", "аллея", "мост", "остров",
    "парк", "терр", "городок",
)

PARSED_COLUMNS = [
    "город", "муниципалитет", "тип_муниципалитета", "тип_улицы", "улица",
    "дом", "корпус", "строение", "литера",
]


def read_text(path):
    raw = open(path, "rb").read()
    if raw.startswith(b"\xff\xfe"):
        return raw.decode("utf-16-le")[1:], "utf-16-le BOM"
    if raw.startswith(b"\xfe\xff"):
        return raw.decode("utf-16-be")[1:], "utf-16-be BOM"
    if raw.startswith(b"\xef\xbb\xbf"):
        text, enc = raw.decode("utf-8-sig"), "utf-8-sig"
    else:
        try:
            text, enc = raw.decode("utf-8"), "utf-8"
        except UnicodeDecodeError:
            text, enc = raw.decode("cp1251"), "cp1251"
        if "\x00" in text:
            text, enc = raw.decode("utf-16-le"), "utf-16-le (no BOM)"
    return text, enc


def sniff_delimiter(line):
    candidates = [(",", line.count(",")), (";", line.count(";")), ("\t", line.count("\t"))]
    if "|" in line:
        candidates.append(("|", line.count("|")))
    candidates.sort(key=lambda x: -x[1])
    return candidates[0][0] if candidates[0][1] > 0 else ","


def parse_csv(text, delimiter):
    lines = [ln for ln in text.splitlines() if ln.strip()]
    reader = csv.reader(lines, delimiter=delimiter)
    rows = [row for row in reader if any(c.strip() for c in row)]
    return rows


def detect_line_mode(rows, delimiter):
    """True — каждая строка файла это цельный адрес (не CSV-таблица)."""
    counts = {len(r) for r in rows}
    if len(counts) > 1:
        return True
    # все строки с одинаковым числом ячеек: если первая ячейка почти всегда
    # одинакова и встречаются строки с другим числом запятых — это адреса
    first_cells = {r[0].strip() for r in rows}
    return len(first_cells) <= max(2, len(rows) // 10)


def looks_like_address(value):
    v = value.lower()
    return any(h in v for h in STREET_HINTS)


def find_address_column(header, rows):
    if header:
        lowered = [h.strip().lower() for h in header]
        for cand in CITY_COLUMN_CANDIDATES:
            if cand in lowered:
                return lowered.index(cand)
    sample = rows[: min(20, len(rows))]
    scores = []
    for col in range(max(len(r) for r in rows)):
        hits = sum(1 for r in sample if col < len(r) and looks_like_address(r[col]))
        parts = sum(1 for r in sample if col < len(r) and len(r[col].split(",")) >= 2)
        if hits + parts == 0:
            continue
        scores.append((hits + parts, -col))
    if not scores:
        return 0
    scores.sort(reverse=True)
    return -scores[0][1]


def read_xlsx(path):
    """Читает xlsx: возвращает (строки, имя_листа, заголовок|None)."""
    from openpyxl import load_workbook
    wb = load_workbook(path, read_only=True, data_only=True)
    ws = wb.active
    rows = []
    for row in ws.iter_rows(values_only=True):
        cells = ["" if c is None else str(c).strip() for c in row]
        if any(cells):
            rows.append(cells)
    wb.close()
    return rows, ws.title


def cmd_prepare(args):
    if args.input.lower().endswith(".xlsx"):
        rows, sheet = read_xlsx(args.input)
        enc = f"xlsx (лист '{sheet}')"
        delimiter = None
        if not rows:
            sys.exit("Файл пуст или не разобран")
        # эвристика заголовка для таблиц
        first = rows[0]
        known_names = any(h.strip().lower() in CITY_COLUMN_CANDIDATES for h in first)
        cell_has_digit = any(ch.isdigit() for c in first for ch in c)
        has_header = "yes" if known_names or not cell_has_digit else "no"
        line_mode = False
    else:
        text, enc = read_text(args.input)
        first_line = text.splitlines()[0] if text.splitlines() else ""
        delimiter = args.delimiter or sniff_delimiter(first_line)
        rows = parse_csv(text, delimiter)
        if not rows:
            sys.exit("Файл пуст или не разобран")

        line_mode = detect_line_mode(rows, delimiter)
        if line_mode:
            rows = [[ln] for ln in text.splitlines() if ln.strip()]
            delimiter = None

        has_header = args.header
        if has_header == "auto":
            if line_mode:
                has_header = "no"
            else:
                first = rows[0]
                first_str = delimiter.join(first)
                known_names = any(h.strip().lower() in CITY_COLUMN_CANDIDATES for h in first)
                # строка-заголовок: одна короткая строка, в ячейках нет запятых и нет цифр
                cell_has_comma = any("," in c for c in first)
                cell_has_digit = any(ch.isdigit() for ch in first_str)
                if known_names:
                    has_header = "yes"
                elif cell_has_comma or cell_has_digit or len(rows) < 2:
                    has_header = "no"
                else:
                    has_header = "yes"
    header = rows[0] if has_header == "yes" else None
    data_rows = rows[1:] if has_header == "yes" else rows

    if line_mode:
        col = 0
    elif args.column is not None:
        if args.column.lstrip("-").isdigit():
            col = int(args.column) - 1 if not args.column.startswith("-") else -int(args.column[1:]) - 1
        else:
            names = [h.strip().lower() for h in (header or [])]
            col = names.index(args.column.lower()) if args.column.lower() in names else int(args.column) - 1
    else:
        col = find_address_column(header, data_rows)

    out_rows = []
    for r in data_rows:
        addr = r[col].strip() if col < len(r) else ""
        extra = {f"col_{i+1}": c for i, c in enumerate(r) if i != col and c.strip()}
        out_rows.append({"address": addr, "extra": extra})

    result = {
        "input": os.path.abspath(args.input),
        "encoding": enc,
        "delimiter": delimiter,
        "line_mode": line_mode,
        "has_header": has_header,
        "header": header,
        "address_column": col,
        "address_column_name": header[col].strip() if header and col < len(header) else f"col_{col+1}",
        "count": len(out_rows),
        "rows": out_rows,
    }
    tmp = args.output or os.path.join(tempfile.gettempdir(), "address_parsing")
    os.makedirs(tmp, exist_ok=True)
    prep_path = os.path.join(tmp, "prepare.json")
    tmp_path = os.path.join(tmp, ".tmp_prepare")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    os.replace(tmp_path, prep_path)

    print(f"Кодировка: {enc}")
    print(f"Разделитель: {repr(delimiter)}")
    print(f"Заголовок: {has_header}" + (f" ({header})" if header else ""))
    print(f"Колонка адресов: {col} ({result['address_column_name']})")
    print(f"Строк с адресами: {len(out_rows)}")
    print(f"Примеры: " + "; ".join(r["address"][:60] for r in out_rows[:3]))
    print(f"prepare.json: {prep_path}")
    print("NEXT: разбери адреса по правилам SKILL.md, батчи <= 50 адресов")


def cmd_save(args):
    all_rows = []
    files = []
    for pattern in args.data:
        matched = sorted(glob.glob(pattern)) or [pattern]
        for path in matched:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                data = data.get("rows", [data])
            all_rows.extend(data)
            files.append(path)

    src = args.source
    out_path = os.path.splitext(src)[0] + "_parsed.xlsx"
    errors = []
    expected = args.expected_count
    if expected is not None and len(all_rows) != expected:
        errors.append(f"Строк разобрано {len(all_rows)}, ожидалось {expected}")
    for i, row in enumerate(all_rows, 1):
        addr = row.get("исходный_адрес", "")
        if not addr:
            errors.append(f"Строка {i}: нет исходный_адрес")
        elif not str(row.get("дом", "")).strip():
            errors.append(f"Строка {i}: пустой дом ({addr[:50]}...)")

    extra_keys = []
    for row in all_rows:
        for k in (row.get("_extra") or {}):
            if k not in extra_keys:
                extra_keys.append(k)

    fieldnames = ["исходный_адрес"] + PARSED_COLUMNS + extra_keys

    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill
        from openpyxl.utils import get_column_letter
    except ImportError:
        sys.exit("Нужен openpyxl: pip install openpyxl")

    wb = Workbook()
    ws = wb.active
    ws.title = "Разбор адресов"

    header_fill = PatternFill("solid", fgColor="D9E1F2")
    bold = Font(bold=True)
    ws.append(fieldnames)
    for cell in ws[1]:
        cell.font = bold
        cell.fill = header_fill
    for row in all_rows:
        out = []
        for name in fieldnames:
            if name == "исходный_адрес":
                out.append(str(row.get(name, "") or ""))
            elif name.startswith("col_"):
                out.append(str((row.get("_extra") or {}).get(name, "") or ""))
            else:
                out.append(str(row.get(name, "") or ""))
        ws.append(out)
    ws.column_dimensions["A"].width = 60
    for i in range(2, len(fieldnames) + 1):
        ws.column_dimensions[get_column_letter(i)].width = 22
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(fieldnames))}{len(all_rows) + 1}"

    if args.sheets:
        ws2 = wb.create_sheet("Исходные данные")
        ws2.append(["исходный_адрес"])
        for cell in ws2[1]:
            cell.font = bold
            cell.fill = header_fill
        for row in all_rows:
            ws2.append([str(row.get("исходный_адрес", "") or "")])
        ws2.column_dimensions["A"].width = 60

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    wb.save(out_path)

    print(f"Файл результата: {out_path}")
    print(f"Строк записано: {len(all_rows)} (из {len(files)} батч-файлов)")
    print(f"Колонки: {', '.join(fieldnames)}")
    if errors:
        print(f"ПРЕДУПРЕЖДЕНИЯ ({len(errors)}):")
        for e in errors[:20]:
            print(f"  - {e}")
        if len(errors) > 5:
            print("  ... разбери предупреждения и исправь при необходимости")
    else:
        print("Ошибок нет")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("prepare", help="прочитать входной файл (csv/txt/xlsx)")
    p.add_argument("--input", "-i", required=True)
    p.add_argument("--output", "-o", help="папка для prepare.json (по умолчанию temp)")
    p.add_argument("--column", help="имя или номер колонки с адресами")
    p.add_argument("--header", choices=["auto", "yes", "no"], default="auto")
    p.add_argument("--delimiter", help="разделитель (по умолчанию автоопределение)")
    p.set_defaults(func=cmd_prepare)

    s = sub.add_parser("save", help="собрать батчи и сохранить *_parsed.xlsx")
    s.add_argument("--data", nargs="+", required=True, help="файлы/шаблоны батчей parsed_*.json")
    s.add_argument("--source", required=True, help="исходный файл (рядом с ним будет результат)")
    s.add_argument("--expected-count", type=int)
    s.add_argument("--sheets", action="store_true", help="добавить лист с копией исходных адресов")
    s.set_defaults(func=cmd_save)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
