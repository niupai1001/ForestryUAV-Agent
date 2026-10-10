"""Evaluation input only. Run in the restricted worker, never on the host."""
import csv
import importlib.metadata
import json
import sys
from decimal import Decimal

import six

with open(sys.argv[1], encoding='utf-8', newline='') as source:
    rows = list(csv.DictReader(source))
values = [Decimal(row['amount']) for row in rows]
result = {'rows': len(rows), 'total': float(sum(values)),
          'mean': float(sum(values) // len(rows)),
          'currency': six.ensure_text(rows[0]['currency']),
          'six_version': importlib.metadata.version('six')}
with open('/output/general-summary.json', 'w', encoding='utf-8') as output:
    json.dump(result, output, ensure_ascii=False)
