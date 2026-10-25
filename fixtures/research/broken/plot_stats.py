import csv, json

with open('data/plots.csv', newline='', encoding='utf-8') as handle:
    rows = list(csv.DictReader(handle))
values = [float(row['dbh_cm']) for row in rows]
summary = {'n': len(values), 'mean_dbh_cm': sum(values) / len(values)}
with open('out/summary.json', 'w', encoding='utf-8') as handle:
    json.dump(summary, handle, ensure_ascii=False)
print(summary)
