"""Shared non-mutating import validation used by both preview and atomic import."""
import csv
import io
import json

# The API body is bounded to 8 MB; accept long JSON cells up to that same ceiling.
csv.field_size_limit(8_000_000)


class BatchValidationError(ValueError):
    def __init__(self, report):
        self.report = report
        first = report['errors'][0]
        super().__init__(f'Row {first["row"]}, {first["field"]}: {first["message"]}')


def validate_batch(validate_case, records=None, csv_text=None):
    if (records is None) == (csv_text is None):
        raise ValueError('Provide records or csv_text')
    csv_mode = csv_text is not None
    if csv_mode:
        if not isinstance(csv_text, str):
            raise ValueError('csv_text must be a string')
        reader = csv.DictReader(io.StringIO(csv_text.lstrip('\ufeff')), strict=True)
        if not reader.fieldnames or reader.fieldnames.count('input') != 1 or len(set(reader.fieldnames)) != len(reader.fieldnames):
            raise ValueError('CSV needs a unique input column and non-duplicate headers')
        try:
            records = []
            for row in reader:
                if len(records) >= 10000:
                    raise ValueError('Import expects 1..10000 records')
                records.append((row, reader.line_num))
        except csv.Error as exc:
            raise ValueError(f'Invalid CSV near line {reader.line_num}: {exc}') from None
    elif not isinstance(records, list):
        raise ValueError('records must be an array')
    else:
        records = [(row, None) for row in records]
    if not 1 <= len(records) <= 10000:
        raise ValueError('Import expects 1..10000 records')
    normalized, errors, invalid_count, error_count = [], [], 0, 0
    for index, (row, line) in enumerate(records, 1):
        current_errors = []
        if not isinstance(row, dict):
            current_errors.append(('record', 'Record must be an object'))
            value = {}
        elif csv_mode:
            value = {}
            if None in row or any(v is None for v in row.values()):
                current_errors.append(('record', 'CSV field count does not match headers'))
            for field in ('input', 'expected', 'metadata'):
                raw = row.get(field)
                if raw in (None, '') and field != 'input':
                    value[field] = {} if field == 'metadata' else None
                else:
                    try:
                        value[field] = json.loads(raw)
                    except (ValueError, TypeError):
                        current_errors.append((field, 'Cell must contain valid JSON'))
        else:
            value = {'input': row.get('input'), 'expected': row.get('expected', row.get('expected_output')),
                     'metadata': row.get('metadata', {})}
        if not current_errors:
            if not isinstance(value.get('metadata'), dict):
                current_errors.append(('metadata', 'metadata must be an object'))
            if not isinstance(value.get('input'), dict):
                current_errors.append(('input', 'input must be an object'))
        if not current_errors:
            try:
                validate_case(value['input'], value['metadata'])
                json.dumps(value, allow_nan=False)
            except (ValueError, TypeError) as exc:
                # These messages contain schema constraints, never the original cell contents.
                current_errors.append(('record', str(exc)))
        if current_errors:
            invalid_count += 1
            error_count += len(current_errors)
            for field, message in current_errors:
                if len(errors) < 100:
                    errors.append({'row': index, 'line': line, 'field': field, 'message': message})
        else:
            normalized.append(value)
    report = {'valid': invalid_count == 0, 'total': len(records), 'valid_count': len(normalized),
              'invalid_count': invalid_count, 'errors': errors, 'errors_truncated': error_count > len(errors),
              'preview': normalized[:5]}
    return normalized, report
