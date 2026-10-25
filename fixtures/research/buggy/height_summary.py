import csv

with open('plots.csv', newline='', encoding='utf-8') as handle:
    rows = list(csv.DictReader(handle))
values = [float(row['height_m']) for row in rows]
print('n=', len(values))
print('mean_height_m=', sum(values) / len(values))
